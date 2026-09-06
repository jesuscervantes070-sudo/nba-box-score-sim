"""
The playable, text-based front end for the sim. Run this file directly:

    python3 main.py

Top-level menu: simulate a single game, or simulate a full season and
view standings.

This file only prints/reads text -- all the actual simulation logic
lives in game_engine.py, and loading real team data lives in loader.py.
Keeping this file "dumb" (just I/O) means the simulation itself stays
fully testable on its own, without needing a keyboard in the loop.
"""
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from loader import load_teams, load_team_abbreviations, load_roster_membership, load_league_pace_variation, DEFAULT_SEASON, load_schedule, available_seasons
from models import Player, Team
from game_engine import simulate_game, compute_league_averages, GameResult, LeagueAverages
from data_source import fetch_real_standings
from season import simulate_season
from playoffs import run_playoffs, compute_series_player_averages
from transactions import summarize_moves
import db

# Plain ASCII only for every divider/border in this file, on purpose --
# no fancy unicode box-drawing characters, just characters already on a
# standard keyboard, so the output looks right in any terminal.
LINE_WIDTH = 96
DIVIDER = "=" * LINE_WIDTH
SECTION = "-" * LINE_WIDTH

# A plain-text marker appended to a followed team's row wherever
# standings are printed -- kept even now that color exists below,
# since color alone isn't readable to someone piping output to a file
# or using a screen reader.
YOUR_TEAM_MARKER = "  <-- YOUR TEAM"

# The real 2025-26 NBA trade deadline (Thursday, Feb 5, 2026, 3pm ET --
# https://www.hoopsrumors.com/2025/08/2026-nba-trade-deadline-set-for-february-5.html).
# Only used as a jump target in the game-by-game replay below ("catch me
# up to the deadline"), same spirit as playoffs.py's hardcoded
# TEAM_DIVISIONS -- a fixed real-world fact, not worth a whole fetch/
# cache file for. Matches ScheduledGame.date's ISO format so it can be
# compared against a stored game's date directly, as a plain string.


# Real trade deadlines, by season. Only seasons whose real deadline is
# actually known go in here -- the replay's jump-to-deadline command is
# simply unavailable for the rest, rather than jumping to a made-up date.
#
# Deriving it from the cached data was tried and does NOT work: the
# roster data records WHEN a player first appeared for a team, not WHY,
# so the last such move is in mid-APRIL every season -- buyout signings
# and 10-day contracts, not trades. Nothing in this project's data
# distinguishes a deadline-day trade from an April signing, and
# inventing thirty deadline dates would be thirty chances to be quietly
# wrong about a real-world fact.
TRADE_DEADLINE_BY_SEASON = {
    "2025-26": "2026-02-05",   # the real 2025-26 deadline, Thu Feb 5 2026
}


def trade_deadline_for(season: str) -> Optional[str]:
    """The real trade deadline for `season`, or None if it isn't known
    -- see TRADE_DEADLINE_BY_SEASON."""
    return TRADE_DEADLINE_BY_SEASON.get(season)


# =====================================================================
# COLOR (optional -- everything below degrades to plain text)
# =====================================================================
# ANSI escape codes, only used when it's actually safe to: a real
# terminal (not a pipe/file, where the raw escape bytes would just show
# up as garbage text), and the user hasn't opted out via the NO_COLOR
# convention (https://no-color.org). Every color-producing function
# below falls back to returning its input unchanged otherwise, so
# nothing else in this file has to know or care whether color is on.
_COLOR_ENABLED = sys.stdout.isatty() and "NO_COLOR" not in os.environ

_ANSI_CODES = {"bold": "\033[1m", "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m", "red": "\033[31m"}
_ANSI_RESET = "\033[0m"


def _style(text: str, *names: str) -> str:
    """Wraps `text` in the named ANSI codes (e.g. _style(s, "bold", "cyan")).
    IMPORTANT: only call this on a string that's already been padded/
    centered to its final width -- the escape codes are invisible bytes
    that would otherwise get counted by str.center()/f-string width
    specifiers and throw off alignment."""
    if not _COLOR_ENABLED:
        return text
    return f"{''.join(_ANSI_CODES[n] for n in names)}{text}{_ANSI_RESET}"


# Matches the "<winner> def. <loser>, <W>-<L>" shape every playoff
# series/game result line ends in (see playoffs.py's _series_line and
# print_playoffs's Finals line) -- used to highlight the winner's name
# and the score without needing playoffs.py to build the string with
# color baked in (it stays pure text, testable without a terminal at all).
_DEF_LINE_RE = re.compile(r"^(.*?)([^:]+?) def\. ([^,]+),\s*(\d+)-(\d+)$")


def _colorize_series_line(line: str) -> str:
    """
    Bolds+greens the winner's name, AND colors the series score itself
    by how close it actually was -- green for a lopsided 4-0/4-1,
    yellow for a normal 4-2, red for a real 4-3 nailbiter. Plain text
    alone made every series read the same at a glance; this is meant
    to let the eye jump straight to the close ones. Falls back to the
    line unchanged if it doesn't match that shape, or color is off --
    always safe to call on any playoff log line.
    """
    if not _COLOR_ENABLED:
        return line
    match = _DEF_LINE_RE.match(line)
    if not match:
        return line
    prefix, winner, loser, win_ct, lose_ct = match.groups()
    margin = int(win_ct) - int(lose_ct)
    margin_color = "green" if margin >= 3 else "yellow" if margin == 2 else "red"
    return (f"{prefix}{_style(winner, 'bold', 'green')} def. {loser}, "
            f"{_style(f'{win_ct}-{lose_ct}', 'bold', margin_color)}")


def _colorize_play_in_line(line: str) -> str:
    """
    Bolds+greens the winner's name in a play-in log line. Play-in
    lines use a different shape than a full series' "X def. Y, N-M"
    line ("X wins, becomes the N seed" / "X wins, advances; Y
    eliminated") -- _colorize_series_line's regex doesn't match that
    shape at all, so play-in results were never getting colored. The
    winner's name is always the text right after the LAST "-> " (Games
    1-2) or "): " (Game 3, which has no "->") in the line, immediately
    before " wins" -- checked in that order since a line can contain
    both separators (e.g. "Game 1 (7 vs 8): ... -> ... wins", where the
    "): " is just part of the game-number prefix, not the real split
    point).
    """
    if not _COLOR_ENABLED or " wins" not in line:
        return line
    before, _, after = line.partition(" wins")
    for sep in (" -> ", "): "):
        if sep in before:
            prefix, _, winner = before.rpartition(sep)
            return f"{prefix}{sep}{_style(winner, 'bold', 'green')} wins{after}"
    return line


def _highlight_team(line: str, team_name: Optional[str]) -> str:
    """Bolds+cyans every occurrence of the followed team's name in a
    plain playoff line -- the same "your team" cyan already used on
    its standings row, extended to the playoffs. No-op if there's no
    followed team, color is off, or the team isn't even mentioned in
    this particular line. Safe to call after _colorize_series_line
    (see _format_playoff_line) -- .replace() matches the plain team
    name text even when it's already sitting inside that function's
    own color codes, since those codes wrap around the name rather
    than sit inside it."""
    if not team_name or not _COLOR_ENABLED or team_name not in line:
        return line
    return line.replace(team_name, _style(team_name, "bold", "cyan"))


def _format_playoff_line(line: str, highlight: Optional[str]) -> str:
    """Series-score coloring, then the followed team's name in cyan --
    in that order, since re-wrapping an already-colored winner name in
    cyan is harmless (the last color code wins), but doing it the
    other way around would put _colorize_series_line's reset code in
    the middle of _highlight_team's span instead of at its end."""
    return _highlight_team(_colorize_series_line(line), highlight)


def _format_play_in_line(line: str, highlight: Optional[str]) -> str:
    """Same idea as _format_playoff_line, for a play-in log line's
    different shape -- see _colorize_play_in_line."""
    return _highlight_team(_colorize_play_in_line(line), highlight)


def _prompt(text: str) -> str:
    """
    A drop-in replacement for input() that fixes a real bug found by
    testing: typing fast, ahead of the program actually reaching its
    next prompt, let a leftover keystroke silently leak into a LATER,
    unrelated prompt (e.g. a '2' meant for one question showing up
    glued onto the next question's answer -- "Simulate the full season
    now? (y/n): 2 y"). Flushing any not-yet-read input right before
    reading means only what's typed AFTER a prompt actually appears
    gets counted as the answer to it.

    Falls back to plain input() wherever termios isn't available or
    doesn't apply (Windows, or stdin isn't a real terminal at all --
    e.g. this file being tested by piping canned answers in) --
    flushing only matters for someone typing live at a real keyboard.
    """
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass
    return input(text)


def _confirm(question: str, default: bool = True) -> bool:
    """
    A y/n prompt where a bare Enter takes the DEFAULT answer instead of
    silently meaning "no" -- found by testing (reported directly):
    every (y/n) prompt in this file required literally typing "y", so
    an accidental bare Enter -- easy to do after several prompts in a
    row -- silently took the "no" branch with no confirmation of what
    just happened, which read as "it skipped the playoffs" rather than
    "I didn't type y". `question` should NOT include the "(y/n)" part
    -- this adds it, capitalized on whichever side is the default
    (Y/n or y/N), the common CLI convention for showing what Enter does.
    """
    suffix = "(Y/n)" if default else "(y/N)"
    answer = _prompt(f"{question} {suffix}: ").strip().lower()
    if answer == "":
        return default
    return answer.startswith("y")


# =====================================================================
# TEAM SELECTION
# =====================================================================

def print_welcome() -> None:
    print(DIVIDER)
    print(_style("NBA BOX SCORE SIM".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    print()


def print_team_list(team_names: List[str]) -> None:
    """Print the numbered team list ONCE. Both team-selection prompts
    reference these same numbers instead of re-printing the whole list
    a second time -- the user only has to read it once."""
    for i, name in enumerate(team_names, start=1):
        print(f"  {i:>2}. {name}")
    print()


def select_team_number(team_names: List[str], prompt_label: str, exclude_name: str = None) -> Optional[str]:
    """
    Read the user's choice by number, against the list already printed
    by print_team_list. Loops until it gets a valid number, and (if
    `exclude_name` is set -- used for picking the OPPONENT) refuses to
    let the user pick the same team twice.

    Returns None if the user types 'b'/'back' -- there was previously
    no way to escape team selection once inside it (found by testing:
    typing anything other than a valid number, including an attempt to
    back out, just got rejected and re-prompted forever). Callers
    should treat a None return as "give up and return to the menu."
    """
    print(f"{prompt_label} (or 'b' to go back)")
    while True:
        choice = _prompt("> ").strip()

        if choice.lower() in ("b", "back"):
            return None

        if not choice.isdigit():
            print("Please enter a number from the list above, or 'b' to go back.")
            continue

        index = int(choice)
        if index < 1 or index > len(team_names):
            print(f"Please enter a number between 1 and {len(team_names)}, or 'b' to go back.")
            continue

        chosen_name = team_names[index - 1]
        if chosen_name == exclude_name:
            print("That's already your team -- pick a different opponent.")
            continue

        return chosen_name


# =====================================================================
# BOX SCORE DISPLAY (single game)
# =====================================================================

def _team_totals_row(players) -> Player:
    """
    Build a fake 'player' representing the TEAM's totals, by summing the
    real simulated player rows -- reusing the Player class for this is
    intentional: it means the team-total row gets PTS/FG%/3P%/FT%/DREB
    computed correctly for free, using the exact same math as any real
    player, instead of writing separate team-total formulas that could
    drift out of sync with the individual rows they're supposed to match.
    """
    return Player(
        name="TOTAL",
        team="",
        min=sum(p.min for p in players),
        fgm=sum(p.fgm for p in players), fga=sum(p.fga for p in players),
        fg3m=sum(p.fg3m for p in players), fg3a=sum(p.fg3a for p in players),
        ftm=sum(p.ftm for p in players), fta=sum(p.fta for p in players),
        reb=sum(p.reb for p in players), oreb=sum(p.oreb for p in players),
        ast=sum(p.ast for p in players), stl=sum(p.stl for p in players),
        blk=sum(p.blk for p in players), tov=sum(p.tov for p in players),
        pf=sum(p.pf for p in players),
    )


# Column widths used by BOTH the header row and every player row, so
# they can never quietly drift out of alignment with each other -- one
# shared source of truth for "how wide is each column" instead of two
# separate hand-typed format strings that could disagree.
#
# PLAYER is 25 wide specifically because the longest real name in the
# whole league (checked directly against the data) is 24 characters
# ("Nickeil Alexander-Walker", "Yanic Konan Niederhäuser") -- a shorter
# column was silently overflowing and throwing off every column after
# it on that player's row.
BOX_SCORE_COLUMNS = [
    ("PLAYER", "<", 25),
    ("MIN", ">", 4),
    ("PTS", ">", 4),
    ("REB", ">", 4),
    ("OREB", ">", 4),
    ("AST", ">", 4),
    ("STL", ">", 3),
    ("BLK", ">", 3),
    ("TOV", ">", 3),
    ("PF", ">", 3),
    ("FG", ">", 7),
    ("FG%", ">", 6),
    ("3PT", ">", 7),
    ("3P%", ">", 6),
    ("FT", ">", 7),
    ("FT%", ">", 6),
]


def _box_score_header_row() -> str:
    return "  ".join(f"{label:{align}{width}}" for label, align, width in BOX_SCORE_COLUMNS)


def _pct_str(pct: float) -> str:
    """0.417 -> ' 41.7%' -- an actual percentage, not a raw decimal."""
    return f"{pct * 100:.1f}%"


def _format_player_row(p: Player) -> str:
    """One formatted box-score line for a single player (or the TOTAL
    row, since that's also just a Player -- see _team_totals_row)."""
    if p.min == 0:
        # A real box score doesn't print stats for someone who didn't
        # play -- it just marks them DNP. Uses BOX_SCORE_COLUMNS[0]'s
        # width directly (rather than a second hardcoded number) so
        # this can never quietly drift out of alignment with the
        # header again.
        name_width = BOX_SCORE_COLUMNS[0][2]
        return f"{p.name:<{name_width}}  DNP"

    values = [
        p.name, f"{p.min:.0f}", f"{p.pts:.0f}",
        f"{p.reb:.0f}", f"{p.oreb:.0f}", f"{p.ast:.0f}",
        f"{p.stl:.0f}", f"{p.blk:.0f}", f"{p.tov:.0f}", f"{p.pf:.0f}",
        f"{p.fgm:.0f}-{p.fga:.0f}", _pct_str(p.fg_pct),
        f"{p.fg3m:.0f}-{p.fg3a:.0f}", _pct_str(p.fg3_pct),
        f"{p.ftm:.0f}-{p.fta:.0f}", _pct_str(p.ft_pct),
    ]
    return "  ".join(f"{v:{align}{width}}" for v, (_, align, width) in zip(values, BOX_SCORE_COLUMNS))


def _print_team_box_score(team_name: str, players, score: float, highlight: Optional[str] = None) -> None:
    header = f"{team_name} ({score:.0f})"
    # Same "your team" bold-cyan already used on its standings row, moves
    # row, and playoff bracket label -- box scores were the one place
    # this was missing (reported directly): single-game mode (option 1)
    # never had a followed team to highlight, so this was never built
    # here at all until the replay feature started calling it FROM a
    # followed-team context.
    print(_style(header, "bold", "cyan") if team_name == highlight else header)
    print(SECTION)
    print(_box_score_header_row())
    print(SECTION)

    # Highest scorers show up first -- DNPs (0 minutes) sink to the
    # bottom automatically since they always score 0.
    for p in sorted(players, key=lambda player: -player.pts):
        print(_format_player_row(p))

    print(SECTION)
    print(_format_player_row(_team_totals_row(players)))
    print()


def print_box_score(result: GameResult, highlight: Optional[str] = None) -> None:
    print()
    print(DIVIDER)
    final_line = (f"FINAL{_ot_suffix(result.overtime_periods)}: {result.home_team} {result.home_score:.0f} - "
                  f"{result.away_score:.0f} {result.away_team}")
    # Highlight AFTER centering, not before -- _style's own docstring
    # warns that its invisible color codes would throw off .center()'s
    # width math if they were already in the string being centered.
    print(_highlight_team(final_line.center(LINE_WIDTH), highlight))
    print(DIVIDER)
    print()
    _print_team_box_score(result.home_team, result.home_players, result.home_score, highlight)
    # A terminal always jumps to show whatever was JUST printed -- there's
    # no way for a plain print()-based script to keep it scrolled to the
    # top instead. Pausing here breaks one huge wall of text into two
    # smaller, readable chunks, so the jump after each one is much less
    # jarring, and there's time to actually read the first team's box
    # score before the second one pushes it further up.
    _prompt("Press Enter to see the other team's box score...")
    print()
    _print_team_box_score(result.away_team, result.away_players, result.away_score, highlight)


# =====================================================================
# GAME-BY-GAME REPLAY (season + playoff series)
# =====================================================================
# IMPORTANT: this is a REPLAY, not a live simulation. By the time any of
# this runs, the whole season (season.py) or the whole series
# (playoffs.simulate_series) is already fully simulated and stored/held
# in memory -- same as it always was. All this section does is walk
# already-decided results back out at a controlled pace instead of
# dumping them all at once. That means stopping early (typing 'e') never
# leaves anything half-simulated -- standings/seeding are exactly as
# correct as if this whole section didn't exist.

def _parse_replay_command(raw: str) -> Tuple[str, int]:
    """
    Parses one line typed at a replay prompt into (action, count).
    Blank = the next single game; a plain number = that many games in a
    row before pausing again; 'b'/'t'/'e' are the box-score/jump-to-
    trade-deadline/stop-here commands (see run_team_game_log_replay and
    _replay_playoff_series). 'deadline' only makes sense for a whole
    season, not a single playoff series -- the playoff replay treats it
    as invalid, same as any other unrecognized input.
    """
    raw = raw.strip().lower()
    if raw == "":
        return ("next", 1)
    if raw.isdigit():
        return ("next", max(1, int(raw)))
    if raw in ("b", "box"):
        return ("box", 0)
    if raw in ("t", "deadline"):
        return ("deadline", 0)
    if raw in ("e", "end"):
        return ("end", 0)
    return ("invalid", 0)


def _ot_suffix(overtime_periods: int) -> str:
    """'' for a game decided in regulation, '/OT' for one overtime
    period, '/2OT'/'/3OT'/... for more -- the real broadcast convention
    for marking a final score, used both on a score line and on a full
    box score's FINAL line (see print_box_score). See game_engine.
    GameResult.overtime_periods -- a game literally can't end tied
    anymore (a real NBA rule this sim now models, see game_engine.py's
    OVERTIME_MINUTES), so this is purely informational, not something
    any score/average is computed from."""
    if not overtime_periods:
        return ""
    return "/OT" if overtime_periods == 1 else f"/{overtime_periods}OT"


def _format_score_line(label: str, opponent: str, my_score: float, opp_score: float, is_home: bool,
                        overtime_periods: int = 0, record: Optional[str] = None) -> str:
    """
    One score-line of a replay -- e.g. '2025-11-04 vs Miami Heat   W 108-102 (OT)  14-3'.
    Colored green/red by win/loss, not by accuracy -- the one place in
    this file color means "who won" rather than "how close to real,"
    since a replayed game has no "real" number to compare against.

    `record` (optional) is the followed team's running tally THROUGH
    this game -- "14-3" for the season replay, or the series score so
    far for a playoff series -- so watching game by game actually shows
    the standings/series updating as you go, not just isolated scores
    (reported directly: this was the one thing missing).
    """
    vs_at = "vs" if is_home else "@ "
    won = my_score > opp_score
    result = _style("W", "bold", "green") if won else _style("L", "bold", "red")
    ot = f" ({_ot_suffix(overtime_periods).lstrip('/')})" if overtime_periods else ""
    record_str = f"  {record}" if record else ""
    return f"  {label:<11}{vs_at} {opponent:<26} {result} {my_score:.0f}-{opp_score:.0f}{ot}{record_str}"


def run_team_game_log_replay(conn, team_name: str, season: str, highlight: Optional[str] = None) -> None:
    """
    Paces through one team's stored season, one game at a time, instead
    of only ever seeing it summarized in the standings. Score line only
    by default (a full box score for all 82 games at once would be
    unreadable) -- 'b' pulls up the full box score for whichever game
    was JUST shown, reusing print_box_score() unchanged (see
    db.get_game_box_score's docstring), so a replayed box score looks
    identical to a freshly-simulated one.

    Commands at each pause: Enter (next game), a number (that many games
    in a row), 'b' (box score of the last game shown), 't' (fast-forward
    -- still showing every score line along the way -- through every
    game up to the real trade deadline), 'e' (fast-forward the SAME way
    through every remaining game of the season, landing you at the
    standings right after -- not a silent skip; every score line still
    prints on the way there).

    Each score line also carries the team's RUNNING record through that
    game (e.g. "14-3") -- games always reveal in real chronological
    order here (no jumping backward), so it's a plain running win/loss
    tally, not a re-query of the standings table.
    """
    log = db.get_team_game_log(conn, season, team_name)
    if not log:
        print(f"No simulated games stored for {team_name}.")
        return

    header = f"-- {team_name.upper()}: {season} GAME BY GAME --"
    print()
    print(_style(header, "bold", "cyan") if team_name == highlight else _style(header, "bold"))
    print(SECTION)

    pos = 0  # index of the next not-yet-shown game
    wins = losses = 0
    last_shown_id: Optional[str] = None
    while pos < len(log):
        raw = _prompt(
            f"[{pos}/{len(log)} shown] Enter=next, N=skip N, b=box score, "
            f"t=jump to trade deadline, e=show the rest + standings: "
        )
        action, count = _parse_replay_command(raw)

        if action == "invalid":
            print("Please enter a blank line, a number, 'b', 't', or 'e'.")
            continue
        if action == "end":
            # 'e' still shows every remaining score line (not a silent
            # bail-out) -- reported directly: skipping straight to the
            # standings with no scores in between read as a bug, not a
            # shortcut. Falls through to the normal print block below,
            # sized to whatever's left, then the while loop ends on its
            # own once pos reaches len(log).
            count = len(log) - pos
        if action == "box":
            if last_shown_id is None:
                print("No game shown yet -- press Enter first to see one.")
                continue
            print_box_score(db.get_game_box_score(conn, last_shown_id), highlight)
            continue
        if action == "deadline":
            deadline = trade_deadline_for(season)
            if deadline is None:
                # An older season whose real deadline isn't recorded --
                # say so plainly rather than jumping to a date invented
                # for it. See TRADE_DEADLINE_BY_SEASON.
                print(f"The real trade deadline for {season} isn't recorded, so there's "
                      f"nothing to jump to -- Enter for the next game, or a number to skip ahead.")
                continue
            # If the very next not-yet-shown game is already ON/AFTER the
            # deadline, there's nothing left to fast-forward THROUGH --
            # say so and re-prompt, rather than silently falling through
            # to the code below and showing 1 game, which looked exactly
            # like pressing Enter with no explanation (reported directly).
            if log[pos]["date"] >= deadline:
                print("Already past the trade deadline -- Enter for the next game, "
                      "or a number to skip ahead.")
                continue
            end_pos = pos
            while end_pos < len(log) and log[end_pos]["date"] < deadline:
                end_pos += 1
            count = end_pos - pos

        shown = log[pos: pos + count]
        for game in shown:
            if game["my_score"] > game["opp_score"]:
                wins += 1
            else:
                losses += 1
            print(_format_score_line(game["date"], game["opponent"], game["my_score"], game["opp_score"],
                                      game["is_home"], game["overtime_periods"], record=f"{wins}-{losses}"))
        if shown:
            last_shown_id = shown[-1]["game_id"]
        pos += len(shown)

    print(SECTION)
    print()


def _run_game_log_browser(conn, team_names: List[str], season: str, highlight: Optional[str] = None) -> None:
    """
    Lets the user watch another team's season game-by-game too, one team
    at a time -- same "pick a number or press Enter to finish" pattern
    as the moves/injuries/season-averages browsers. No 'a' for "all 30
    teams" here, unlike those -- replaying every team's full season game
    by game at once isn't something anyone actually wants.
    """
    while True:
        print_team_list(team_names)
        choice = _prompt(
            "Watch another team's season game-by-game? Enter a number, "
            "or press Enter to finish: "
        ).strip()
        if choice == "":
            return
        if choice.isdigit() and 1 <= int(choice) <= len(team_names):
            run_team_game_log_replay(conn, team_names[int(choice) - 1], season, highlight=highlight)
            continue
        print("Please enter a number from the list, or press Enter to finish.")


def _replay_playoff_series(series: dict, matchup_label: str, final_line: str, highlight: Optional[str]) -> None:
    """
    Paces through one already-decided playoff series game by game,
    instead of only ever printing the final 'X def. Y, 4-2' line. Only
    ever called for a series the followed team actually played in (see
    _print_conference_bracket), so every game in `series["game_log"]`
    has `highlight` as either the home or away team the whole way
    through -- no need for a "not your series" fallback branch.

    Reads game_log straight out of memory (playoffs.py never writes to
    season.db -- see that module's docstring), not the database -- so
    'b' hands print_box_score() a GameResult it already has, unlike the
    season replay above, which has to rebuild one from storage.

    Each score line carries the SERIES record so far (e.g. "2-1"), same
    idea as the season replay's running win/loss tally above. 'e' shows
    every remaining game of the series (not a silent skip) before
    landing on the final series line -- same reasoning as 'e' in
    run_team_game_log_replay above.
    """
    print()
    print(_style(f"  {matchup_label}", "bold"))
    game_log = series["game_log"]

    pos = 0
    wins = losses = 0
    last_shown = None
    while pos < len(game_log):
        raw = _prompt(f"  Game {pos + 1}/{len(game_log)}: Enter=next, N=skip N, b=box score, "
                       f"e=show the rest: ")
        action, count = _parse_replay_command(raw)

        if action in ("invalid", "deadline"):
            print("  Please enter a blank line, a number, 'b', or 'e'.")
            continue
        if action == "end":
            count = len(game_log) - pos
        if action == "box":
            if last_shown is None:
                print("  No game shown yet -- press Enter first to see one.")
                continue
            print_box_score(last_shown, highlight)
            continue

        shown = game_log[pos: pos + count]
        for i, result in enumerate(shown, start=pos + 1):
            is_home = result.home_team == highlight
            opp = result.away_team if is_home else result.home_team
            my_score = result.home_score if is_home else result.away_score
            opp_score = result.away_score if is_home else result.home_score
            if my_score > opp_score:
                wins += 1
            else:
                losses += 1
            print(_format_score_line(f"Game {i}", opp, my_score, opp_score, is_home,
                                      result.overtime_periods, record=f"{wins}-{losses}"))
        if shown:
            last_shown = shown[-1]
        pos += len(shown)

    print()
    print(_format_playoff_line(final_line, highlight))


# =====================================================================
# SINGLE GAME FLOW
# =====================================================================

def run_single_game_flow(teams: Dict[str, Team], team_names: List[str], league_avg: LeagueAverages) -> None:
    """The original pick-two-teams-and-simulate loop. Returns to the
    caller (the top-level menu) once the user says they're done,
    rather than ending the whole program."""
    while True:
        # Printed at the start of every round (including replays) --
        # by the time a box score has scrolled by, the list is long
        # gone off-screen, so it needs to come back for the next pick.
        print_team_list(team_names)

        my_team_name = select_team_number(team_names, "Select YOUR team:")
        if my_team_name is None:
            return
        print(f"-> {my_team_name}\n")

        opponent_team_name = select_team_number(team_names, "Select the OPPONENT team:", exclude_name=my_team_name)
        if opponent_team_name is None:
            return
        print(f"-> {opponent_team_name}\n")

        print(f"Simulating: {my_team_name} vs. {opponent_team_name} ...")
        result = simulate_game(teams[my_team_name], teams[opponent_team_name], league_avg)
        print_box_score(result)

        again = _confirm("Play again?")
        print()
        if not again:
            return


# =====================================================================
# STANDINGS DISPLAY
# =====================================================================

def _standings_row(rank: int, row: dict, highlight: str) -> str:
    is_mine = row["team"] == highlight
    marker = YOUR_TEAM_MARKER if is_mine else ""
    line = f"{rank:>3}. {row['team']:<28}{row['W']:>5}{row['L']:>5}{marker}"
    # Colored on top of (not instead of) the marker text above -- see
    # YOUR_TEAM_MARKER's comment on why the text stays either way.
    return _style(line, "bold", "cyan") if is_mine else line


def print_standings(standings: List[dict], highlight: str = None) -> None:
    print()
    print(DIVIDER)
    print(_style("STANDINGS".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    print(f"{'#':>3}  {'TEAM':<28}{'W':>5}{'L':>5}")
    print(SECTION)
    for i, row in enumerate(standings, start=1):
        print(_standings_row(i, row, highlight))
    print()


def print_standings_by_conference(standings: List[dict], teams: Dict[str, Team], highlight: str = None) -> None:
    print()
    print(DIVIDER)
    print(_style("STANDINGS BY CONFERENCE".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    for conference in ("East", "West"):
        conf_rows = [row for row in standings if teams[row["team"]].conference == conference]
        print()
        print(_style(f"-- {conference} --", "bold"))
        print(f"{'#':>3}  {'TEAM':<28}{'W':>5}{'L':>5}")
        print(SECTION)
        for i, row in enumerate(conf_rows, start=1):
            print(_standings_row(i, row, highlight))
    print()


def print_standings_comparison(standings: List[dict], real_standings: Dict[str, int], highlight: str = None) -> None:
    """
    Simulated standings side by side with the REAL final standings --
    the original point of this whole project: checking how close a
    simulated season lands to what actually happened.
    """
    print()
    print(DIVIDER)
    print(_style("SIMULATED VS. REAL STANDINGS".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    print(f"{'TEAM':<28}{'REAL W':>8}{'SIM W':>8}{'DIFF':>7}")
    print(SECTION)

    rows = sorted(standings, key=lambda r: -real_standings.get(r["team"], 0))
    diffs = []
    for row in rows:
        real_w = real_standings.get(row["team"])
        sim_w = row["W"]
        is_mine = row["team"] == highlight
        marker = YOUR_TEAM_MARKER if is_mine else ""

        if real_w is None:
            line = f"{row['team']:<28}{'?':>8}{sim_w:>8}{marker}"
            print(_style(line, "bold", "cyan") if is_mine else line)
            continue

        diff = sim_w - real_w
        diffs.append(abs(diff))

        if is_mine:
            # Same whole-line highlight as every other standings view --
            # kept simple rather than also color-coding the diff below,
            # since nesting two colors in one line fights itself (the
            # inner reset code kills the outer color partway through).
            print(_style(f"{row['team']:<28}{real_w:>8}{sim_w:>8}{diff:>+7}{marker}", "bold", "cyan"))
            continue

        # Colored by DISTANCE from 0, not by sign -- a +1 isn't "better"
        # than a -1, so direction was never the meaningful part here,
        # only how far off the sim landed.
        accuracy_color = "green" if abs(diff) <= 3 else "yellow" if abs(diff) <= 7 else "red"
        diff_str = _style(f"{diff:>+7}", accuracy_color)
        print(f"{row['team']:<28}{real_w:>8}{sim_w:>8}{diff_str}{marker}")

    print(SECTION)
    if diffs:
        print(f"Mean absolute error: {sum(diffs) / len(diffs):.1f} games across {len(diffs)} teams")
    print()


# =====================================================================
# PLAYOFFS DISPLAY
# =====================================================================

# Bracket diagram layout. A leaf/round-node label is always "(N) ABC*"
# (a 1-digit seed + 3-letter code + a 1-char "is this the followed
# team" marker slot) -- fixed width, so every column below can be a
# plain constant instead of measured from real text. Colored text is
# NOT used inside the diagram itself (see _render_conference_tree's
# docstring) -- the marker is a single plain ASCII character instead,
# which is exactly as safe as any other character in a fixed grid.
_BRACKET_LEAF_WIDTH = 8
_BRACKET_GAP = 2
_BRACKET_MARKER = "*"


def _bracket_place_text(grid: dict, row: int, col: int, text: str,
                         highlight_spans: List[Tuple[int, int, int]], is_highlighted: bool) -> None:
    for i, ch in enumerate(text):
        grid.setdefault(row, {})[col + i] = ch
    if is_highlighted:
        # Record WHERE this label landed (row, start col, length) --
        # color gets applied later, once the grid is flattened into
        # plain strings, by slicing at this exact position. Never by
        # splicing color codes into the character grid itself (see
        # _render_conference_tree's docstring on why that would corrupt
        # the diagram's column math).
        highlight_spans.append((row, col, len(text)))


def _bracket_draw_connector(grid: dict, top_row: int, bot_row: int, conn_col: int, label_col: int, label: str,
                             highlight_spans: List[Tuple[int, int, int]], is_highlighted: bool) -> int:
    """
    Draws one bracket "elbow": a dash+plus at each of the two child
    rows, a vertical bar filling every row strictly between them, and
    -- at their exact midpoint row -- the branch into `label`. Returns
    that midpoint row, since it's the row the NEXT round's connector
    needs to treat this result as a single node at.
    """
    mid_row = (top_row + bot_row) // 2
    grid.setdefault(top_row, {})[conn_col - 1] = '-'
    grid.setdefault(top_row, {})[conn_col] = '+'
    grid.setdefault(bot_row, {})[conn_col - 1] = '-'
    grid.setdefault(bot_row, {})[conn_col] = '+'
    for row in range(top_row + 1, bot_row):
        grid.setdefault(row, {})[conn_col] = '+' if row == mid_row else '|'
    for col in range(conn_col + 1, label_col):
        grid.setdefault(mid_row, {})[col] = '-'
    _bracket_place_text(grid, mid_row, label_col, label, highlight_spans, is_highlighted)
    return mid_row


def _render_conference_tree(tree: dict, abbrev: Dict[str, str], highlight: Optional[str] = None) -> List[str]:
    """
    Draws the actual bracket shape (seed 1-8, not the play-in) as ASCII
    art: leaves -> Round 1 winners -> Semifinal winners -> conference
    champion, connected the way a real bracket sheet is. The layout is
    column math built on every label being exactly _BRACKET_LEAF_WIDTH
    wide, so the grid itself stays plain text -- splicing invisible
    escape bytes directly into a label would make it silently overrun
    into its neighboring column instead of raising an error, corrupting
    the diagram. The followed team still gets the plain "*" marker (a
    real character is exactly as safe as any other in a fixed grid) --
    PLUS actual color now, applied afterward: _bracket_place_text
    records WHERE each highlighted label landed as the grid is built,
    and only once the grid is flattened into plain row strings (below)
    does color get sliced in at those exact positions -- never through
    the character grid itself.

    Relies entirely on `tree`'s leaf order (see playoffs.py's
    run_conference_bracket) to know who plays whom -- this function
    itself has no idea what a "matchup" is, it just connects adjacent
    pairs, twice, recursively.
    """
    LEAF_COL = 0
    conn1_col = LEAF_COL + _BRACKET_LEAF_WIDTH + 2
    r2_label_col = conn1_col + 1 + _BRACKET_GAP
    conn2_col = r2_label_col + _BRACKET_LEAF_WIDTH + 2
    r3_label_col = conn2_col + 1 + _BRACKET_GAP
    conn3_col = r3_label_col + _BRACKET_LEAF_WIDTH + 2
    champ_col = conn3_col + 1 + _BRACKET_GAP

    def seed_label(seed: int, team: str) -> str:
        code = abbrev.get(team, team[:3].upper())
        marker = _BRACKET_MARKER if team == highlight else " "
        return f"({seed}) {code}{marker}"

    grid: dict = {}
    highlight_spans: List[Tuple[int, int, int]] = []
    leaf_rows = [i * 2 for i in range(8)]
    for row, (seed, team) in zip(leaf_rows, tree["leaves"]):
        _bracket_place_text(grid, row, LEAF_COL, seed_label(seed, team), highlight_spans, team == highlight)

    round2_rows = []
    for i, result in enumerate(tree["round1"]):
        top, bot = leaf_rows[2 * i], leaf_rows[2 * i + 1]
        label = seed_label(result["winner_seed"], result["winner"])
        round2_rows.append(_bracket_draw_connector(grid, top, bot, conn1_col, r2_label_col, label,
                                                     highlight_spans, result["winner"] == highlight))

    round3_rows = []
    for i, result in enumerate(tree["round2"]):
        top, bot = round2_rows[2 * i], round2_rows[2 * i + 1]
        label = seed_label(result["winner_seed"], result["winner"])
        round3_rows.append(_bracket_draw_connector(grid, top, bot, conn2_col, r3_label_col, label,
                                                     highlight_spans, result["winner"] == highlight))

    champion = tree["round3"]["winner"]
    champ_is_mine = champion == highlight
    champ_label = champion + (f" {_BRACKET_MARKER}" if champ_is_mine else "")
    _bracket_draw_connector(grid, round3_rows[0], round3_rows[1], conn3_col, champ_col, champ_label,
                             highlight_spans, champ_is_mine)

    max_row = max(grid.keys())
    max_col = max(col for row in grid.values() for col in row)
    rows = []
    for row in range(max_row + 1):
        line = "".join(grid.get(row, {}).get(col, " ") for col in range(max_col + 1))
        # Rightmost span first -- inserting invisible escape bytes for
        # one span never shifts the column positions of an earlier,
        # not-yet-processed span further left on the same row.
        for _, col, length in sorted((s for s in highlight_spans if s[0] == row), key=lambda s: -s[1]):
            line = line[:col] + _style(line[col:col + length], "bold", "cyan") + line[col + length:]
        rows.append(line.rstrip())
    return rows


def _series_for_line(line: str, series_list: List[dict]) -> Optional[dict]:
    """
    Finds which simulate_series() result produced one 'X def. Y, N-M'
    line, by matching the winner+loser names _DEF_LINE_RE already knows
    how to pull out of it. Needed because a round's raw series results
    (tree["round1"]/["round2"]/["round3"], which carry the full game_log
    a replay needs) aren't in the same order as that round's PRE-
    FORMATTED text lines (round_logs) -- see run_conference_bracket's r1
    vs. tree["round1"] ordering -- so matching by list position would
    quietly pair a line with the wrong series.
    """
    match = _DEF_LINE_RE.match(line)
    if not match:
        return None
    # _DEF_LINE_RE's winner group includes the space right after the
    # colon (harmless where it's normally used -- _colorize_series_line
    # just re-embeds it unchanged -- but it means an exact-equality
    # compare against a clean team name needs a .strip() first).
    _, winner, loser, _, _ = match.groups()
    winner, loser = winner.strip(), loser.strip()
    for series in series_list:
        if {series["winner"], series["loser"]} == {winner, loser}:
            return series
    return None


def _print_conference_bracket(conf_result: dict, abbrev: Dict[str, str], highlight: Optional[str] = None) -> None:
    """One conference's play-in, then each round in turn -- paused
    BETWEEN every stage (not just between conferences, see
    print_playoffs's docstring) so the postseason unfolds round by
    round: play-in, then Round 1, then Round 2, etc. The bracket
    diagram prints as a recap at the very end, once every round's
    winner is actually known -- it can't be drawn any earlier than
    that (its connector lines ARE those winners), so showing it
    upfront would either be blank or spoil rounds not revealed yet.

    Whichever series the followed team is actually playing in gets
    replayed game by game (_replay_playoff_series) instead of just
    printing its final score line -- every OTHER series in the same
    round still prints instantly, same "scoped to your team" rule as
    the regular-season replay above."""
    # No header at all for a season that had no play-in (every season
    # before 2019-20 -- see playoffs.playoff_format). Printing an empty
    # "Play-In Tournament" heading implied a round that never existed.
    if conf_result["play_in_log"]:
        print()
        print(_style(f"-- {conf_result['conference']} Play-In Tournament --", "bold"))
        for line in conf_result["play_in_log"]:
            print(_format_play_in_line(line, highlight))

    tree = conf_result["tree"]
    # Lined up with round_logs in [round1, round2, round3] order --
    # that outer ordering IS reliable (see run_conference_bracket), only
    # the series WITHIN a round need _series_for_line's name matching.
    round_series = [tree["round1"], tree["round2"], [tree["round3"]]]

    for round_lines, series_list in zip(conf_result["round_logs"], round_series):
        _prompt("Press Enter for the next round...")
        print()
        header, *series_lines = round_lines
        print(_style(header, "bold"))
        for line in series_lines:
            series = _series_for_line(line, series_list)
            if series and highlight in (series["winner"], series["loser"]):
                matchup_label, _, _ = line.partition(":")
                _replay_playoff_series(series, matchup_label.strip(), line, highlight)
            else:
                print(_format_playoff_line(line, highlight))

    _prompt("Press Enter to see the bracket recap...")
    print()
    for line in _render_conference_tree(conf_result["tree"], abbrev, highlight):
        print(line)

    print()
    print(_style(
        f"{conf_result['conference'].upper()} CHAMPION: "
        f"{conf_result['champion']} (#{conf_result['champion_seed']} seed)",
        "bold", "yellow",
    ))


def print_finals_averages(finals: dict, highlight: Optional[str] = None) -> None:
    """
    Per-player averages for the NBA Finals series only -- not the
    whole playoff run, per what was actually asked for, and not
    written to season.db (see playoffs.py's docstring). Winner's
    roster first. This is deliberately just the numbers -- picking an
    actual Finals MVP from them is a later step, not this one.
    """
    averages = compute_series_player_averages(finals["game_log"])
    print()
    print(_style("-- NBA FINALS -- PLAYER AVERAGES --", "bold"))
    for team_name in (finals["winner"], finals["loser"]):
        team_avgs = sorted(
            (a for a in averages.values() if a["team"] == team_name),
            key=lambda a: -a["pts"],
        )
        print()
        print(f"  {_style(team_name, 'bold', 'cyan') if team_name == highlight else team_name}")
        print(f"  {'PLAYER':<25}{'GP':>4}{'PTS':>7}{'REB':>7}{'AST':>7}{'FG%':>8}")
        for a in team_avgs:
            print(
                f"  {a['player']:<25}{a['games_played']:>4}{a['pts']:>7.1f}"
                f"{a['reb']:>7.1f}{a['ast']:>7.1f}{a['fg_pct'] * 100:>7.1f}%"
            )


def print_playoffs(result: dict, abbrev: Dict[str, str], highlight: Optional[str] = None) -> None:
    """
    Prints the play-in log, bracket diagram, every round's detail, and
    the Finals (plus Finals player averages) for a
    playoffs.run_playoffs() result. Pure display -- playoffs.py already
    decided every outcome, this function just reads it back out.

    `highlight` (the followed team, if any) is threaded all the way
    through -- the play-in log, the bracket diagram, every round's
    detail lines, the Finals, and the Finals averages all mark it,
    same "your team" cyan already used on its standings row.

    Paced with the same "Press Enter to continue" pattern already used
    for a single game's box score (see print_box_score) -- printing the
    whole postseason in one unbroken burst made it hard to actually
    read: the East bracket scrolled the West bracket and the Finals
    straight off screen before there was time to look at any of it.
    Paced by ROUND now, not just by conference (see
    _print_conference_bracket) -- play-in, then each round in turn,
    each its own pause, matching how the playoffs actually unfold.
    """
    print()
    print(DIVIDER)
    print(_style("PLAYOFFS".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)

    _print_conference_bracket(result["east"], abbrev, highlight)
    _prompt("Press Enter to see the West bracket...")

    _print_conference_bracket(result["west"], abbrev, highlight)
    _prompt("Press Enter to see the NBA Finals...")

    finals = result["finals"]
    print()
    print(SECTION)
    print(_style("-- NBA FINALS --", "bold"))
    print(_highlight_team(f"  {result['east']['champion']} vs {result['west']['champion']}", highlight))
    final_line = (
        f"  {finals['winner']} def. {finals['loser']}, "
        f"{finals['wins'][finals['winner']]}-{finals['wins'][finals['loser']]}"
    )
    # Same "your team's series gets replayed game by game" rule as every
    # conference round (see _print_conference_bracket) -- the Finals is
    # just as much "the playoffs" as any earlier round, so it shouldn't
    # be the one series that's always instant regardless of who's in it.
    if highlight in (finals["winner"], finals["loser"]):
        _replay_playoff_series(finals, f"{result['east']['champion']} vs {result['west']['champion']}",
                                final_line, highlight)
    else:
        print(_format_playoff_line(final_line, highlight))
    print(SECTION)
    print()
    print(_style(f"NBA CHAMPION: {result['champion']}".center(LINE_WIDTH), "bold", "yellow"))
    print_finals_averages(finals, highlight)
    print()
    print(DIVIDER)
    print()


# =====================================================================
# SEASON AVERAGES DISPLAY
# =====================================================================

def _injury_row(row: dict, highlight: Optional[str] = None) -> str:
    is_mine = row["team"] == highlight
    marker = YOUR_TEAM_MARKER if is_mine else ""
    # PLAYER field is 25 wide, not 22 -- the longest real names this
    # season (e.g. "Kentavious Caldwell-Pope", "Nickeil Alexander-
    # Walker") are 24 characters, which a 22-wide field doesn't
    # truncate, it just runs the TEAM column (and everything after it)
    # straight into the name with no gap and no longer under its
    # header. 25 leaves a 1-character buffer even for the longest name.
    line = (f"{row['player']:<25}{row['team']:<26}{row['start_date']:<12}"
            f"{row['end_date']:<12}{row['games_missed']:>4}{marker}")
    return _style(line, "bold", "cyan") if is_mine else line


def print_injuries(injuries: List[dict], title: str = "SIMULATED INJURIES",
                    highlight: Optional[str] = None, limit: Optional[int] = 25) -> None:
    """
    Prints simulated injuries, longest (most impactful) first -- see
    injuries.py for how a real player's real absence pattern this
    season gets reused, at randomized points, in a simulated season.
    `limit` caps how many print at once by default: a full season has
    well over a thousand short (2-3 game) absences league-wide, fine to
    have stored, not useful to dump all at once. `title` lets the same
    printer serve both the "healed before playoffs" and "still out
    entering the playoffs" views with their own header.
    """
    print()
    print(DIVIDER)
    print(_style(title.center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    if not injuries:
        print("None.")
        print()
        return

    print(f"{'PLAYER':<25}{'TEAM':<26}{'START':<12}{'END':<12}{'GAMES':>4}")
    print(SECTION)
    shown = injuries[:limit] if limit else injuries
    for row in shown:
        print(_injury_row(row, highlight))
    if limit and len(injuries) > limit:
        print(f"... and {len(injuries) - limit} more, mostly short (2-3 game) absences "
              f"-- filter by team below to see a team's full list.")
    print()


def print_team_moves(moves: List[dict], highlight: Optional[str] = None) -> None:
    """
    Prints every real in-season trade this season (see
    transactions.summarize_moves) as "Player: Team A -> Team B" --
    just the real WHERE, not the WHY (no trade-details data is fetched
    anywhere in this project, only real game-log evidence of who
    suited up for whom).
    """
    print()
    print(DIVIDER)
    print(_style("TEAM MOVES THIS SEASON".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    if not moves:
        print("No real in-season trades this season.")
        print()
        return

    for m in moves:
        is_mine = highlight in m["teams"]
        marker = YOUR_TEAM_MARKER if is_mine else ""
        # 25 wide, not 22 -- see _injury_row's comment: the longest real
        # names this season are 24 characters, which ran straight into
        # the " -> " chain with no gap at a 22-wide field.
        line = f"{m['player']:<25}{' -> '.join(m['teams'])}{marker}"
        print(_style(line, "bold", "cyan") if is_mine else line)
    print()


def _run_moves_browser(all_moves: List[dict], team_names: List[str], highlight: Optional[str] = None) -> None:
    """
    Lets the user look up another team's real in-season moves beyond
    the auto-printed "your team only" list above.
    """
    while True:
        print_team_list(team_names)
        choice = _prompt(
            "View another team's moves? Enter a number, 'a' for the full league list, "
            "or press Enter to finish: "
        ).strip().lower()

        if choice == "":
            return
        if choice == "a":
            print_team_moves(all_moves, highlight=highlight)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(team_names):
            chosen_name = team_names[int(choice) - 1]
            team_moves = [m for m in all_moves if chosen_name in m["teams"]]
            print_team_moves(team_moves, highlight=highlight)
            continue
        print("Please enter a number from the list, 'a' for the full list, or press Enter to finish.")


def _run_injuries_browser(conn, team_names: List[str], season: str, highlight: Optional[str] = None) -> None:
    """
    Lets the user look up another team's injuries beyond the auto-
    printed "your team only" lists above -- same single team-pick/'a'/
    Enter pattern as the moves and season-averages browsers below, not
    a separate healed-vs-still-out sub-menu (that extra step was
    confusing -- unclear what info it was even offering). Whichever
    team gets picked, both the healed and still-out sections print for
    it, same as the auto-printed view above.
    """
    all_injuries = db.get_injuries(conn, season)

    def _show(subset: List[dict], limit: Optional[int]) -> None:
        healed = [row for row in subset if not row["still_out_at_season_end"]]
        still_out = [row for row in subset if row["still_out_at_season_end"]]
        print_injuries(healed, title="INJURIES DURING THE SEASON (HEALED BEFORE PLAYOFFS)",
                        highlight=highlight, limit=limit)
        print_injuries(still_out, title="STILL OUT ENTERING THE PLAYOFFS", highlight=highlight, limit=limit)

    while True:
        print_team_list(team_names)
        choice = _prompt(
            "View another team's injuries? Enter a number, 'a' for the full league list, "
            "or press Enter to finish: "
        ).strip().lower()

        if choice == "":
            return
        if choice == "a":
            _show(all_injuries, limit=25)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(team_names):
            chosen_name = team_names[int(choice) - 1]
            team_injuries = [row for row in all_injuries if row["team"] == chosen_name]
            _show(team_injuries, limit=None)
            continue
        print("Please enter a number from the list, 'a' for the full list, or press Enter to finish.")


def _accuracy_color(real: float, sim: float) -> str:
    """
    Green/yellow/red by how far sim landed from real, as a % of real --
    same "distance, not direction" spirit as the standings comparison's
    inline accuracy coloring, just scaled RELATIVELY instead of by a
    fixed games-off count, so one threshold works across stats of very
    different sizes (PTS in the 20s, AST often under 5, FG% as a %).
    """
    if real == 0:
        return "green" if sim == 0 else "yellow"
    pct_off = abs(sim - real) / real
    return "green" if pct_off <= 0.10 else "yellow" if pct_off <= 0.25 else "red"


def _consistency_color(rating: int) -> str:
    """
    Colour for the CONS column. NOT the same meaning as _accuracy_color
    above (which is "how close did the sim land") -- this one is "is
    this player steady or erratic," a fact about the player himself.
    Green = reliable, red = wildly streaky, matching how the numbers
    read rather than how accurate anything is.
    """
    return "green" if rating >= 67 else "yellow" if rating >= 34 else "red"


def print_team_season_averages(conn, team: Team, season: str) -> None:
    """
    For one team's roster: real per-game averages vs. simulated season
    averages (from the games just simulated and stored) -- the original
    point of this whole project, finally visible in the game itself
    rather than only in a test script.

    Real and sim are on their OWN row per player (a "real" row directly
    above a "sim" row for the same stat columns) instead of interleaved
    side by side on one line -- that packed 8 numbers into a single row
    per player and was hard to read at a glance. Stacked wasn't quite
    enough on its own though, so each SIM number is also colored by how
    close it landed to the real number right above it (green/yellow/red
    -- same "how far off" idea as the standings comparison table's
    accuracy_color), which does double duty: separates the two rows at
    a glance AND shows how good that particular match actually was.
    """
    print()
    print(DIVIDER)
    print(f"{team.name.upper()} -- REAL VS. SIMULATED SEASON AVERAGES".center(LINE_WIDTH))
    print(DIVIDER)
    print(f"{'PLAYER':<25}{'GP':>4}{'CONS':>6}  {'PTS':>8}  {'REB':>8}  {'AST':>8}  {'FG%':>8}")
    print(SECTION)

    for player in sorted(team.players, key=lambda p: -p.pts):
        avg = db.get_player_season_averages(conn, player.name, season)
        # Bold, not colored -- color on this table already means
        # something specific (accuracy, below), so the name just needs
        # to read as "the anchor this block starts at," not another
        # color signal competing with that one.
        name_s = _style(f"{player.name:<25}", "bold")
        if not avg:
            print(f"{name_s}{'--':>4}  (no simulated games played)")
            continue
        # CONS = this player's real scoring consistency, 1-99 (see
        # models.Player.consistency_rating). It sits on the NAME row
        # rather than the real/sim rows on purpose: unlike everything
        # else in this table it isn't a real-vs-simulated comparison,
        # it's one fact about the player that the simulation below is
        # driven BY. "--" means genuinely not rated (too few games, or
        # too few points for the rating to mean anything), never zero.
        rating = player.consistency_rating
        if rating is None:
            # Left uncoloured on purpose -- "--" is the absence of a
            # rating, not a bad one, and colouring it would read as a
            # judgement about the player.
            rating_s = f"{'--':>6}"
        else:
            rating_s = _style(f"{rating:>6}", _consistency_color(rating))
        print(f"{name_s}{avg['games_played']:>4}{rating_s}")
        print(f"{'  real':<25}{'':>4}{'':>6}  {player.pts:>8.1f}  {player.reb:>8.1f}  "
              f"{player.ast:>8.1f}  {player.fg_pct * 100:>7.1f}%")

        pts_s = _style(f"{avg['pts']:>8.1f}", _accuracy_color(player.pts, avg['pts']))
        reb_s = _style(f"{avg['reb']:>8.1f}", _accuracy_color(player.reb, avg['reb']))
        ast_s = _style(f"{avg['ast']:>8.1f}", _accuracy_color(player.ast, avg['ast']))
        fg_s = _style(f"{avg['fg_pct'] * 100:>7.1f}%",
                      _accuracy_color(player.fg_pct * 100, avg['fg_pct'] * 100))
        print(f"{'  sim':<25}{'':>4}{'':>6}  {pts_s}  {reb_s}  {ast_s}  {fg_s}")
    print("  CONS = scoring consistency, 1-99: how steady this player's real scoring was")
    print("  game to game. 99 = steadier than 99% of real NBA scorers, 1 = the most erratic.")
    print()


# =====================================================================
# SEASON FLOW
# =====================================================================

def run_season_flow(teams: Dict[str, Team], team_names: List[str], league_avg: LeagueAverages,
                     abbrev: Dict[str, str], season: str = "2025-26") -> None:
    """
    Picks the followed team FIRST (so it's known before anything else
    runs, and can be highlighted everywhere below), then simulates the
    full real season (overwriting any previously simulated one -- see
    season.py's simulate_season for why re-running isn't additive),
    then shows -- all automatically, no "do you want to see this?
    (y/n)" gates in front of them (removed per feedback: those gates
    were in front of exactly the numbers this whole project exists to
    produce, not optional side content) -- standings, the real-vs-
    simulated comparison, and (scoped to just the followed team, each
    with an opt-in browser to look up another team) that team's real
    in-season moves, injuries, and simulated season averages. Playoffs
    (optional) run LAST, after all of that -- "who's actually
    available going in" naturally comes before the playoffs happen,
    not after.
    """
    print()
    print_team_list(team_names)
    my_team_name = select_team_number(team_names, "Select YOUR team (highlighted throughout):")
    if my_team_name is None:
        return
    print(f"-> {my_team_name}\n")

    if not _confirm("Simulate the full season now?"):
        return

    # verbose=False -- see simulate_season's docstring: printing "N games
    # simulated in X.XXs" right before asking "want to watch it game by
    # game?" undercut that question (reported directly).
    simulate_season(season=season, fresh=True, verbose=False)

    conn = db.init_db()

    # Optional, BEFORE standings -- watching the season happen game by
    # game naturally comes before seeing how it all turned out, not
    # after. Gated by a confirm (unlike the auto-printed views below)
    # since pacing through 82 games is a real time commitment, not a
    # quick list -- see run_team_game_log_replay's docstring; the
    # season itself is already fully simulated and stored either way,
    # so skipping this changes nothing about the numbers that follow.
    if _confirm(f"Watch {my_team_name}'s season game by game before seeing the standings?"):
        run_team_game_log_replay(conn, my_team_name, season, highlight=my_team_name)
        _run_game_log_browser(conn, team_names, season, highlight=my_team_name)

    standings = db.get_standings(conn, season)

    view = _prompt("View standings by conference, or overall? (c/o): ").strip().lower()
    if view == "c":
        print_standings_by_conference(standings, teams, highlight=my_team_name)
    else:
        print_standings(standings, highlight=my_team_name)

    real_standings = fetch_real_standings(season)
    print_standings_comparison(standings, real_standings, highlight=my_team_name)

    # Moves, injuries, and season averages -- all scoped to YOUR team
    # by default (the league-wide dumps were too much at once), each
    # with an opt-in browser to look up another team if you want one.
    # All shown BEFORE playoffs (not buried at the end) -- this is the
    # "who's actually available going in" picture, so it reads
    # naturally right before the playoffs happen, not after.
    membership = load_roster_membership()
    all_moves = summarize_moves(membership)
    my_moves = [m for m in all_moves if my_team_name in m["teams"]]
    print_team_moves(my_moves, highlight=my_team_name)
    _run_moves_browser(all_moves, team_names, highlight=my_team_name)

    all_injuries = db.get_injuries(conn, season)
    healed = [row for row in all_injuries if not row["still_out_at_season_end"]]
    still_out = [row for row in all_injuries if row["still_out_at_season_end"]]
    print_injuries([r for r in healed if r["team"] == my_team_name],
                    title="INJURIES DURING THE SEASON (HEALED BEFORE PLAYOFFS)", highlight=my_team_name)
    print_injuries([r for r in still_out if r["team"] == my_team_name],
                    title="STILL OUT ENTERING THE PLAYOFFS", highlight=my_team_name)
    _run_injuries_browser(conn, team_names, season, highlight=my_team_name)

    print_team_season_averages(conn, teams[my_team_name], season)
    _run_season_averages_browser(conn, teams, team_names, season)

    if _confirm("Simulate the playoffs too?"):
        playoff_result = run_playoffs(conn, season, teams, standings, league_avg)
        print_playoffs(playoff_result, abbrev, highlight=my_team_name)


def _run_season_averages_browser(conn, teams: Dict[str, Team], team_names: List[str], season: str) -> None:
    """
    Lets the user look up any OTHER team's simulated season averages,
    one at a time, several in a row, or all of them at once -- rather
    than being limited to just the team they followed.
    """
    while True:
        print_team_list(team_names)
        choice = _prompt(
            "View another team's season averages? Enter a number, 'a' for all, "
            "or press Enter to finish: "
        ).strip().lower()

        if choice == "":
            return

        if choice == "a":
            for name in team_names:
                print_team_season_averages(conn, teams[name], season)
            return

        if choice.isdigit() and 1 <= int(choice) <= len(team_names):
            chosen_name = team_names[int(choice) - 1]
            print_team_season_averages(conn, teams[chosen_name], season)
            continue

        print("Please enter a number from the list, 'a' for all, or press Enter to finish.")


# =====================================================================
# MAIN ENTRY POINT
# =====================================================================

def select_season(seasons: List[str]) -> Optional[str]:
    """
    Pick which real season to play. Enter alone takes the newest one,
    since that's what almost everyone wants and what this project did
    before any other season was playable.
    """
    print(DIVIDER)
    print(_style("PICK A SEASON".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    print(f"{len(seasons)} real seasons are available, {seasons[-1]} through {seasons[0]}.")
    print("Every one uses that season's real rosters, schedule, injuries, trades")
    print("and playoff rules (no play-in before 2019-20; a best-of-5 first round")
    print("before 2003).")
    print()
    # Four per line keeps all 30 on a compact block rather than a
    # 30-line list nobody wants to scroll.
    for i in range(0, len(seasons), 6):
        print("   " + "   ".join(f"{seasons[j]}" for j in range(i, min(i + 6, len(seasons)))))
    print()
    while True:
        choice = _prompt(f"Type a season (e.g. {seasons[0]}), or press Enter for {seasons[0]}: ").strip()
        if choice == "":
            return seasons[0]
        if choice in seasons:
            return choice
        print(f"'{choice}' isn't available -- type one exactly as listed above.")


def main() -> None:
    print_welcome()
    # ONE season for the whole run, chosen here and threaded everywhere
    # below -- it used to be defaulted independently in two places
    # (load_teams' own default and run_season_flow's), which is exactly
    # the sort of duplicated default that drifts apart.
    seasons = available_seasons()
    if not seasons:
        print("No season data is cached yet. Run `python data_source.py` first.")
        return
    season = select_season(seasons) if len(seasons) > 1 else seasons[0]
    print(f"-> {season}\n")
    teams = load_teams(season)
    team_names = sorted(teams.keys())  # alphabetical, so it's easy to scan

    # Real, league-wide baselines (what's an average defense, an
    # average steal/block rate) -- computed ONCE here, not per game.
    # See season.py -- the season's own real pace swing, not a constant.
    league_avg = compute_league_averages(teams, load_league_pace_variation(season))
    # Real 3-letter team codes, only used for the compact playoff
    # bracket diagram -- also loaded once, same reasoning as above.
    abbrev = load_team_abbreviations()

    while True:
        print("What would you like to do?")
        print("  1. Simulate a single game")
        print("  2. Simulate a full season and view standings")
        print("  3. Quit")
        choice = _prompt("> ").strip()
        print()

        if choice == "1":
            run_single_game_flow(teams, team_names, league_avg)
        elif choice == "2":
            run_season_flow(teams, team_names, league_avg, abbrev, season)
        elif choice == "3":
            print("Thanks for playing!")
            break
        else:
            print("Please enter 1, 2, or 3.")
        print()


if __name__ == "__main__":
    main()
