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
import dataclasses
import os
import re
import textwrap
import sys
from typing import Dict, List, Optional, Tuple

from loader import load_teams, load_team_abbreviations, load_roster_membership, load_league_pace_variation, DEFAULT_SEASON, load_schedule, available_seasons, load_team_coaches, load_real_best_record
from models import Player, Team
from game_engine import simulate_game, compute_league_averages, GameResult, LeagueAverages
from data_source import fetch_real_standings
from season import simulate_season
from playoffs import run_playoffs, compute_series_player_averages
from offseason import diff_seasons, franchise_map, team_changes
from transactions import summarize_moves
from awards import (
    simulated_mvp_candidates, simulated_roy_candidates, simulated_dpoy_candidates, simulated_mip_candidates,
    simulated_coy_candidates, mip_features_from_simulated, coy_features_from_simulated,
)
import db

# Plain ASCII only for every divider/border in this file, on purpose --
# no fancy unicode box-drawing characters, just characters already on a
# standard keyboard, so the output looks right in any terminal.
LINE_WIDTH = 96
# Colored just below, once _style exists -- see that assignment.
_DIVIDER_RAW = "=" * LINE_WIDTH
_SECTION_RAW = "-" * LINE_WIDTH

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


# The real NBA trade deadline for every season this project can play.
#
# Same "stable real-world fact, not worth fetch infrastructure" reasoning
# as playoffs.TEAM_DIVISIONS' fallback and HISTORICAL_TEAM_NAMES -- but
# unlike those, these had to be looked up rather than derived, because
# nothing in the cached data distinguishes a deadline trade from an
# April buyout signing (the roster data records WHEN a player first
# appeared for a team, never WHY).
#
# Every one of these was checked two ways before being trusted:
#   1. All 30 are Thursdays, which every real NBA deadline is.
#   2. Against this project's own real roster data -- the last busy
#      trade day of each season lands 1 to 15 days AFTER the date below
#      (median 5), exactly the lag expected from a traded player taking
#      a few days to debut for his new team. None land before it.
# The two seasons that looked odd on check 2 (1999-00 and 2006-07)
# simply never had a single day with four arrivals; both show real
# arrivals 2-3 days after their deadline when checked directly.
#
# The three outliers are real: March 1999 and March 2012 were lockout
# seasons that started late, and March 2021 was the COVID-shortened
# season. The deadline also moved from "the Thursday after the All-Star
# Game" to before the All-Star break starting in 2018, which is why the
# dates jump from late February to early February there.
TRADE_DEADLINE_BY_SEASON = {
    "1996-97": "1997-02-20", "1997-98": "1998-02-19", "1998-99": "1999-03-11",
    "1999-00": "2000-02-24", "2000-01": "2001-02-22", "2001-02": "2002-02-21",
    "2002-03": "2003-02-20", "2003-04": "2004-02-19", "2004-05": "2005-02-24",
    "2005-06": "2006-02-23", "2006-07": "2007-02-22", "2007-08": "2008-02-21",
    "2008-09": "2009-02-19", "2009-10": "2010-02-18", "2010-11": "2011-02-24",
    "2011-12": "2012-03-15", "2012-13": "2013-02-21", "2013-14": "2014-02-20",
    "2014-15": "2015-02-19", "2015-16": "2016-02-18", "2016-17": "2017-02-23",
    "2017-18": "2018-02-08", "2018-19": "2019-02-07", "2019-20": "2020-02-06",
    "2020-21": "2021-03-25", "2021-22": "2022-02-10", "2022-23": "2023-02-09",
    "2023-24": "2024-02-08", "2024-25": "2025-02-06", "2025-26": "2026-02-05",
}


def trade_deadline_for(season: str) -> Optional[str]:
    """The real trade deadline for `season` -- see
    TRADE_DEADLINE_BY_SEASON, which covers all 30 playable seasons."""
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

_ANSI_CODES = {
    "bold": "\033[1m", "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m", "red": "\033[31m",
    # 256-color code, not the basic 8-color "magenta" (\033[35m) --
    # that one reads as bright pink/fuchsia on most terminals, not the
    # softer purple asked for. 135 is a medium purple that holds up on
    # both light and dark backgrounds.
    "purple": "\033[38;5;135m",
}
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


# Colored once here, used everywhere as plain module constants -- every
# call site just does print(DIVIDER)/print(SECTION) unchanged, so this
# one place is the only thing that had to change to color literally
# every screen's rules/borders at once. DIVIDER (bold cyan) marks a
# screen's own boundary; SECTION (plain cyan) is the lighter rule used
# for a sub-block within one screen -- distinct weights so the two
# don't read as the same line at a glance.
DIVIDER = _style(_DIVIDER_RAW, "bold", "cyan")
SECTION = _style(_SECTION_RAW, "cyan")


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


def _team_record_str(standings: List[dict], team: str) -> str:
    """"58-24" for a team in `standings`, "" if it isn't there (a coach
    or player whose team didn't make this season's standings list for
    some reason -- shouldn't normally happen, but a blank is safer
    than a crash in a display-only helper)."""
    row = next((r for r in standings if r["team"] == team), None)
    return f"{row['W']}-{row['L']}" if row else ""


def _rank_number(i: int) -> str:
    """
    A medal-style rank prefix for a top-5 awards list -- "1." bold
    yellow (gold), "2."/"3." bold, "4."/"5." plain. Only the number
    itself is wrapped (not the whole line), so this composes safely
    with _highlight_team afterward -- that only replaces the team-name
    substring elsewhere in the line, so there's no nested-color-reset
    conflict between the two.
    """
    if i == 1:
        return _style(f"{i}.", "bold", "yellow")
    if i in (2, 3):
        return _style(f"{i}.", "bold")
    return f"{i}."


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

# The title screen's banner, drawn as 5x7 pixel-block letters -- the
# closest a plain terminal can get to "retro/pixel" without leaving the
# terminal entirely (a real pixel FONT would mean a pygame/web window
# instead, which is a much bigger rewrite -- see the UI mockup doc).
# Kept as a plain dict of glyphs rather than a font file so it prints
# with nothing but characters already on a keyboard, matching every
# other divider/border in this file.
_BANNER_GLYPHS = {
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    " ": ["00000"] * 7,
}

# GOATSIM is a placeholder name -- swap this one constant once a real
# name is picked, nothing else about the banner needs to change.
BANNER_TEXT = "GOATSIM"


def _render_banner(text: str) -> List[str]:
    """Turns `text` into 7 lines of pixel-block art, one glyph per
    letter with a one-column gap between them. Any character missing
    from _BANNER_GLYPHS (punctuation, digits) just prints blank."""
    rows = [""] * 7
    for ch in text.upper():
        glyph = _BANNER_GLYPHS.get(ch, _BANNER_GLYPHS[" "])
        for i in range(7):
            rows[i] += "".join("##" if bit == "1" else "  " for bit in glyph[i]) + "  "
    return [row.rstrip() for row in rows]


def print_title() -> None:
    """
    Screen 1 -- the title screen. Pure presentation, no state: prints
    the banner and waits for Enter, same "press enter" pattern as every
    other pause in this file.

    Deliberately has NO quote line. An earlier draft put a fabricated
    quote here ("Michael Jordan averaged 29.6 a night...") in quotation
    marks as if he'd said it -- putting words in a real person's mouth,
    even harmless ones, was the wrong way to fill the whitespace. Left
    blank rather than replaced with something similarly gimmicky.
    """
    print(DIVIDER)
    print()
    for line in _render_banner(BANNER_TEXT):
        print(_style(line.center(LINE_WIDTH).rstrip(), "bold", "purple"))
    print()
    print("1996-97 through 2025-26".center(LINE_WIDTH))
    print()
    print("[ press ENTER to begin ]".center(LINE_WIDTH))
    print(DIVIDER)
    _prompt("")


# The four top-level game modes (screen 2). Only History Sim is
# playable right now -- the other three are shown anyway rather than
# hidden, because seeing the roadmap is half the point of a mode-select
# screen. `playable=False` modes still get their own box; picking one
# just explains it isn't ready yet and returns to this same menu.
GAME_MODES = [
    {
        "name": "History Sim",
        "description": "Replay real NBA history. Every real roster, schedule, injury and "
                        "trade is kept -- only the results are simulated, so the standings, "
                        "the champion and the awards go their own way from night one.",
        "playable": True,
    },
    {
        "name": "Game Sim",
        "description": "One exhibition game between any two teams from any season -- no "
                        "season structure, just a single simulated matchup.",
        "playable": True,
    },
    {
        "name": "Legacy Sim",
        "description": "Pick one player from any season and live his career -- his "
                        "minutes, his teams, his numbers when it's over.",
        "playable": False,
    },
    {
        "name": "????",
        "description": "Not decided yet.",
        "playable": False,
    },
]


def select_game_mode() -> Optional[str]:
    """
    Screen 2 -- pick a game mode. Loops until the user picks the one
    playable mode or quits; picking an unbuilt one prints a short
    explanation and re-shows the same menu rather than doing nothing.

    Returns the chosen mode's name, or None if the user quit here.
    """
    while True:
        print()
        print(DIVIDER)
        print(_style("GAME MODES".center(LINE_WIDTH), "bold", "cyan"))
        print(DIVIDER)
        for i, mode in enumerate(GAME_MODES, start=1):
            tag = "" if mode["playable"] else "  (not built yet)"
            print()
            print(f"  {i}  {_style(mode['name'], 'bold')}{tag}")
            for line in textwrap.wrap(mode["description"], LINE_WIDTH - 6):
                print(f"     {line}")
        print()
        print(DIVIDER)
        choice = _prompt(f"Choose a mode 1-{len(GAME_MODES)} (or 'q' to quit): ").strip().lower()

        if choice in ("q", "quit"):
            return None
        if not (choice.isdigit() and 1 <= int(choice) <= len(GAME_MODES)):
            print(f"Please enter a number from 1 to {len(GAME_MODES)}, or 'q' to quit.")
            continue

        mode = GAME_MODES[int(choice) - 1]
        if mode["playable"]:
            return mode["name"]
        # Named dynamically from GAME_MODES, not hardcoded to "History
        # Sim" -- that hardcoded version silently went stale and lied
        # the moment Game Sim became playable too (reported directly).
        playable = [m["name"] for m in GAME_MODES if m["playable"]]
        print(f"\n{mode['name']} isn't built yet -- {' and '.join(playable)} "
              f"{'is' if len(playable) == 1 else 'are'} the only mode{'' if len(playable) == 1 else 's'} "
              f"you can play right now.\n")


def print_team_list(team_names: List[str]) -> None:
    """Print the numbered team list ONCE. Both team-selection prompts
    reference these same numbers instead of re-printing the whole list
    a second time -- the user only has to read it once."""
    for i, name in enumerate(team_names, start=1):
        print(f"  {i:>2}. {name}")
    print()


def print_team_list_with_best_player(teams: Dict[str, Team], team_names: List[str]) -> None:
    """
    Same numbered list, plus each team's real leading scorer that
    season (screen 4's "BEST PLAYER" column) -- the one piece of team
    identity that's already loaded and needs no network call, unlike a
    real prior-season win-loss record (held off for now; see the note
    where History Sim's team-select screen calls this).

    Widths are sized to the real longest name in the data (22-char
    team name, 24-char player name in 2025-26), not eyeballed -- same
    rule this project applies to every other column.
    """
    for i, name in enumerate(team_names, start=1):
        players = teams[name].players
        if players:
            # By POINTS -- back from a MINUTES-based pick, which
            # surfaced a defensive anchor (Draymond Green, minutes
            # leader on a below-average-scoring roster) as a team's
            # "best player" (reported directly). "Best player" reads
            # as "the star," which means the scorer.
            best = max(players, key=lambda p: p.pts)
            best_label = f"{best.name:<25} {best.pts:>4.1f} ppg"
        else:
            best_label = ""
        print(f"  {i:>2}. {name:<24} {best_label}")
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
    # Both teams print straight through. There used to be a "Press Enter
    # to see the other team's box score" pause here, on the theory that
    # it split one wall of text into two readable chunks -- but it put a
    # keypress in the MIDDLE of a single box score, so you could not just
    # read the game and move on. Same objection, and the same fix, as the
    # "do you want to see this?" gates removed from the season flow: a
    # gate in front of the numbers this project exists to produce.
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
    win_color = "green" if won else "red"
    result = _style("W" if won else "L", "bold", win_color)
    # The score itself now carries the same win/loss color as the
    # letter, not just the letter alone -- per the user, more of the
    # line should actually differentiate at a glance (a wall of white
    # "106-96" text next to a colored "W" undersold which number won).
    score = _style(f"{my_score:.0f}-{opp_score:.0f}", win_color)
    ot = f" ({_ot_suffix(overtime_periods).lstrip('/')})" if overtime_periods else ""
    record_str = f"  {record}" if record else ""
    return f"  {label:<11}{vs_at} {opponent:<26} {result} {score}{ot}{record_str}"


def run_team_game_log_replay(conn, team_name: str, season: str, highlight: Optional[str] = None) -> None:
    """
    Paces through one team's stored season, one game at a time, instead
    of only ever seeing it summarized in the standings. Score line only
    by default (a full box score for all 82 games at once would be
    unreadable) -- 'b' pulls up the full box score for whichever game
    was JUST shown, reusing print_box_score() unchanged (see
    db.get_game_box_score's docstring), so a replayed box score looks
    identical to a freshly-simulated one.

    Before every pause, the NEXT not-yet-shown game is previewed --
    its date, opponent, home/away, and the record going in -- so the
    prompt is never just a bare question with nothing to base it on.
    'b' is only offered once a game has actually been shown (nothing
    to look up before then). No "skip N games" here -- per the user,
    it didn't earn its place next to just Enter/t/e; a run of games
    only ever gets skipped through as a real jump (trade deadline, or
    the end of the season), not an arbitrary count.

    Commands at each pause: Enter (sim the next game), 't' (fast-
    forward -- still showing every score line along the way -- through
    every game up to the real trade deadline), 'e' (the SAME way
    through every remaining game of the season -- not a silent skip;
    every score line still prints on the way there).

    Each score line also carries the team's RUNNING record through that
    game (e.g. "14-3") -- games always reveal in real chronological
    order here (no jumping backward), so it's a plain running win/loss
    tally, not a re-query of the standings table.

    A blank line separates every round's output from the next -- once
    'e'/'t' has just printed a long run of scores, going straight into
    the next prompt with no breathing room made a long session hard to
    scan back through (reported directly).
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
        upcoming = log[pos]
        vs_at = "vs" if upcoming["is_home"] else "@ "
        print()
        print(f"  {_style('NEXT', 'bold', 'yellow')}   Game {pos + 1} of {len(log)}   {upcoming['date']}   "
              f"{vs_at} {upcoming['opponent']:<26} ({wins}-{losses})")

        options = "Enter=sim this game, t=sim to the trade deadline, e=sim to the end of the season"
        if last_shown_id is not None:
            options += ", b=box score of the last game"
        raw = _prompt(f"  {options}: ").strip().lower()

        action = {"": "next", "b": "box", "box": "box", "t": "deadline", "deadline": "deadline",
                  "e": "end", "end": "end"}.get(raw)
        if action is None:
            print("  Please enter a blank line, 't', 'e', or 'b'.")
            continue
        if action == "box":
            if last_shown_id is None:
                print("  No game shown yet -- press Enter first to see one.")
                continue
            print_box_score(db.get_game_box_score(conn, last_shown_id), highlight)
            continue
        if action == "end":
            # 'e' still shows every remaining score line (not a silent
            # bail-out) -- reported directly: skipping straight to the
            # end with no scores in between read as a bug, not a
            # shortcut. Falls through to the normal print block below,
            # sized to whatever's left, then the while loop ends on its
            # own once pos reaches len(log).
            count = len(log) - pos
        elif action == "deadline":
            deadline = trade_deadline_for(season)
            if deadline is None:
                # An older season whose real deadline isn't recorded --
                # say so plainly rather than jumping to a date invented
                # for it. See TRADE_DEADLINE_BY_SEASON.
                print(f"  The real trade deadline for {season} isn't recorded, so there's "
                      f"nothing to jump to -- Enter for the next game.")
                continue
            # If the very next not-yet-shown game is already ON/AFTER the
            # deadline, there's nothing left to fast-forward THROUGH --
            # say so and re-prompt, rather than silently falling through
            # to the code below and showing 1 game, which looked exactly
            # like pressing Enter with no explanation (reported directly).
            if log[pos]["date"] >= deadline:
                print("  Already past the trade deadline -- Enter for the next game.")
                continue
            end_pos = pos
            while end_pos < len(log) and log[end_pos]["date"] < deadline:
                end_pos += 1
            count = end_pos - pos
        else:  # "next" -- exactly one game
            count = 1

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

    # One more chance at the box score for the LAST game (often the
    # one that actually decided something -- a clinching win) before
    # moving on -- the while loop above ends the instant the season is
    # fully shown, which used to skip straight past it with no way
    # back (reported directly).
    while True:
        raw = _prompt("  b=box score of the last game, or press Enter to continue: ").strip().lower()
        if raw in ("b", "box"):
            print_box_score(db.get_game_box_score(conn, last_shown_id), highlight)
            continue
        if raw == "":
            break
        print("  Please enter 'b' or press Enter to continue.")

    print(SECTION)
    print()


def _run_game_log_browser(conn, team_names: List[str], season: str, highlight: Optional[str] = None) -> None:
    """
    Lets the user watch another team's season game-by-game too, one team
    at a time -- same "pick a number or press Enter to finish" pattern
    as the moves/injuries/season-averages browsers. No 'a' for "all 30
    teams" here, unlike those -- replaying every team's full season game
    by game at once isn't something anyone actually wants.

    The 30-team list is NOT printed up front -- 't' shows it on demand.
    Most passes through a browser like this are a single Enter to move
    on, so dumping 30 lines every time was printing a lot for no reason.
    """
    while True:
        choice = _prompt(
            "Watch another team's season game-by-game? Enter a number, "
            "'t' to see the team list, or press Enter to finish: "
        ).strip().lower()
        if choice == "":
            return
        if choice == "t":
            print_team_list(team_names)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(team_names):
            run_team_game_log_replay(conn, team_names[int(choice) - 1], season, highlight=highlight)
            continue
        print("Please enter a number from the list, 't' to see it, or press Enter to finish.")


def _replay_play_in(games: List[dict], highlight: Optional[str]) -> None:
    """
    Paces through the play-in's real games one at a time -- Enter to
    sim each, 'e' to sim the rest, 'b' for the last one's box score --
    available whether or not the followed team is actually IN the
    play-in (per the user: it used to just print already-decided
    results with no way to watch it or see a box score at all,
    regardless of who was playing).

    Each `games` entry is {"label", "home", "away", "result"} --
    playoffs.run_play_in now keeps the full GameResult per game (it
    used to throw it away the instant it read off the winner, which is
    why this couldn't exist before).
    """
    pos = 0
    last_shown = None
    while pos < len(games):
        upcoming = games[pos]
        print()
        print(f"  {_style('NEXT', 'bold', 'yellow')}   {upcoming['label']}   "
              f"{upcoming['home']} vs {upcoming['away']}")
        options = "Enter=sim this game, e=sim the rest"
        if last_shown is not None:
            options += ", b=box score of the last game"
        raw = _prompt(f"  {options}: ").strip().lower()

        action = {"": "next", "b": "box", "box": "box", "e": "end", "end": "end"}.get(raw)
        if action is None:
            print("  Please enter a blank line, 'e', or 'b'.")
            continue
        if action == "box":
            if last_shown is None:
                print("  No game shown yet -- press Enter first to see one.")
                continue
            print_box_score(last_shown, highlight)
            continue

        count = len(games) - pos if action == "end" else 1
        shown = games[pos: pos + count]
        for g in shown:
            result = g["result"]
            winner = g["home"] if result.home_score > result.away_score else g["away"]
            score = f"{result.home_score:.0f}-{result.away_score:.0f}"
            print(_highlight_team(
                f"  {g['label']} ({g['home']} vs {g['away']}): {winner} wins, {score}", highlight))
        if shown:
            last_shown = shown[-1]["result"]
        pos += len(shown)

    # Same "one more chance at the last game's box score" fix as the
    # season/series replays -- the game that just decided a seed is
    # exactly the one someone wants to look back at.
    while True:
        raw = _prompt("  b=box score of the last game, or press Enter to continue: ").strip().lower()
        if raw in ("b", "box"):
            print_box_score(last_shown, highlight)
            continue
        if raw == "":
            break
        print("  Please enter 'b' or press Enter to continue.")
    print()


def _replay_playoff_series(series: dict, matchup_label: str, final_line: str, highlight: Optional[str]) -> None:
    """
    Paces through one already-decided playoff series game by game,
    instead of only ever printing the final 'X def. Y, 4-2' line.

    Works two ways: the followed team's own series (called from
    _print_conference_bracket -- every game already has `highlight` as
    one of the two sides), AND, per the user, any OTHER series someone
    wants to watch (_run_playoff_series_browser's 'g' command) -- when
    `highlight` isn't actually playing in this series, the HOME team is
    just reported from throughout, same as a plain exhibition game with
    no followed side.

    No "skip N games" here -- per the user, same reasoning as dropping
    it from the regular-season replay (run_team_game_log_replay): it
    never earns its place next to Enter/e for something at most 7
    games long. Before every pause, the NEXT game is previewed (Game N
    of M, opponent, series record going in), same as the season replay.

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
    # Which side to report every game FROM, held constant for the
    # whole series -- the followed team if it's actually playing in
    # this series, otherwise the series WINNER (an arbitrary but fixed
    # choice; either team would do, since the exact same games are
    # being shown either way -- what matters is picking ONE and
    # sticking to it). Comparing against home_team fresh each game
    # without a fixed side was a real bug: home court alternates within
    # a series, so "always treat today's home team as me" silently
    # flipped whose win/loss was being tallied from game to game,
    # producing a running record that couldn't even reach the real
    # final score (caught by testing: a 4-2 series showing as 3-3).
    report_team = highlight if highlight in (series["winner"], series["loser"]) else series["winner"]
    # The MAXIMUM the series could go (best-of-7 -> 7, or best-of-5 for
    # a pre-2003 first round -- see playoffs.simulate_series), NOT
    # len(game_log) -- game_log only holds the games actually played,
    # so a series that ended 4-1 has exactly 5 entries in it, and
    # showing "Game 1 of 5" before game 1 even airs spoils that the
    # series won't go past 5 (reported directly). Falls back to
    # len(game_log) only if an older in-memory series somehow lacks
    # "best_of" (shouldn't happen -- simulate_series always sets it).
    max_games = series.get("best_of", len(game_log))

    pos = 0
    wins = losses = 0
    last_shown = None
    while pos < len(game_log):
        upcoming = game_log[pos]
        upcoming_is_home = upcoming.home_team == report_team
        upcoming_opp = upcoming.away_team if upcoming_is_home else upcoming.home_team
        vs_at = "vs" if upcoming_is_home else "@ "
        print()
        print(f"  {_style('NEXT', 'bold', 'yellow')}   Game {pos + 1} of {max_games}   "
              f"{vs_at} {upcoming_opp:<26} ({wins}-{losses})")

        options = "Enter=sim this game, e=sim to the end of the series"
        if last_shown is not None:
            options += ", b=box score of the last game"
        raw = _prompt(f"  {options}: ").strip().lower()

        action = {"": "next", "b": "box", "box": "box", "e": "end", "end": "end"}.get(raw)
        if action is None:
            print("  Please enter a blank line, 'e', or 'b'.")
            continue
        if action == "box":
            if last_shown is None:
                print("  No game shown yet -- press Enter first to see one.")
                continue
            print_box_score(last_shown, highlight)
            continue

        count = len(game_log) - pos if action == "end" else 1

        shown = game_log[pos: pos + count]
        for i, result in enumerate(shown, start=pos + 1):
            is_home = result.home_team == report_team
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

    # Same "one more chance at the last game's box score" fix as
    # run_team_game_log_replay -- the clinching game of a series is
    # exactly the one someone wants to look back at, and the while
    # loop above used to end the instant it was shown, skipping
    # straight to the final series line with no way back.
    while True:
        raw = _prompt("  b=box score of the last game, or press Enter to continue: ").strip().lower()
        if raw in ("b", "box"):
            print_box_score(last_shown, highlight)
            continue
        if raw == "":
            break
        print("  Please enter 'b' or press Enter to continue.")

    print()
    print(_format_playoff_line(final_line, highlight))


# =====================================================================
# SINGLE GAME FLOW
# =====================================================================

# Every real per-team stat LeagueAverages carries (2PT%/3PT%, TOV,
# STL, BLK, minutes, possessions) is keyed by team NAME, for one
# season. A cross-era Game Sim breaks that: the same franchise name
# (e.g. "Los Angeles Lakers") can legitimately appear on BOTH sides of
# the matchup from two different seasons, and every value in these
# dicts a game actually uses is looked up by .get(team.name) -- see
# game_engine.py's two_pt_offense_factor/possession_factor_against/
# pace_factor_for/team_availability/shorthanded_factor, which all
# treat a missing name as "exactly league-average" rather than
# guessing, but say nothing about a NAME COLLISION overwriting one
# team's real number with the other's. `_CROSS_ERA_FIELDS` lists every
# per-team-NAME dict field on LeagueAverages that this has to protect.
_CROSS_ERA_FIELDS = ("team_2pt_pct", "team_3pt_pct", "team_tov",
                     "team_stl", "team_blk", "team_min", "team_poss")


def _build_matchup(team_a: Team, avg_a: LeagueAverages, season_a: str,
                    team_b: Team, avg_b: LeagueAverages, season_b: str) -> Tuple[Team, Team, LeagueAverages]:
    """
    Prepares two teams (and one LeagueAverages) for a Game Sim matchup,
    which may cross two different real seasons.

    Same season: nothing to do, teams and league_avg pass through as
    they always have.

    Different seasons: two real problems get fixed, not just papered
    over -- see _CROSS_ERA_FIELDS above for the name-collision one.
    Fixed by cloning each team with its season appended to its name
    ("Los Angeles Lakers (1996-97)") whenever the two seasons differ,
    and building a LeagueAverages whose per-team dicts hold ONLY these
    two tagged entries, each pulled from its OWN season's real numbers
    -- so a shared franchise name can never overwrite the other team's
    real value. The season tag also disambiguates the box score itself,
    which would otherwise print the same team name twice.

    The second problem is that league-WIDE baselines (pace variation,
    steal/block rates, shooting splits) really do drift by era --
    documented elsewhere in this project (pace variation alone is 6.8%
    in 1996-97 vs 5.3% in 2025-26). Per the user: a cross-era game
    BLENDS both eras rather than picking one to govern, so every
    scalar league-wide field is a plain average of the two seasons'
    real numbers. (Which fields deserve more than a flat average is
    open -- flagged for discussion, not decided here.)
    """
    if season_a == season_b:
        return team_a, team_b, avg_a

    name_a, name_b = f"{team_a.name} ({season_a})", f"{team_b.name} ({season_b})"
    tagged_a = dataclasses.replace(team_a, name=name_a)
    tagged_b = dataclasses.replace(team_b, name=name_b)

    # Scalar league-wide fields on LeagueAverages -- every one that
    # isn't a per-team-NAME dict (those are handled separately below).
    # Averaged as a plain (a + b) / 2, not weighted -- "a mix of both",
    # per the user, with no reason yet to weight one era over the other.
    scalar_fields = [f.name for f in dataclasses.fields(LeagueAverages)
                     if f.name not in _CROSS_ERA_FIELDS]
    blended_scalars = {name: (getattr(avg_a, name) + getattr(avg_b, name)) / 2
                       for name in scalar_fields}

    def _tagged_dict(field: str, avg: LeagueAverages, real_name: str, tagged_name: str) -> Dict[str, float]:
        value = getattr(avg, field).get(real_name)
        return {tagged_name: value} if value is not None else {}

    blended_dicts = {}
    for field in _CROSS_ERA_FIELDS:
        merged = _tagged_dict(field, avg_a, team_a.name, name_a)
        merged.update(_tagged_dict(field, avg_b, team_b.name, name_b))
        blended_dicts[field] = merged

    blended_avg = dataclasses.replace(avg_a, **blended_scalars, **blended_dicts)
    return tagged_a, tagged_b, blended_avg


def run_single_game_flow(team_a: Team, team_b: Team, league_avg: LeagueAverages) -> Optional[str]:
    """
    Simulates one game between two ALREADY-CHOSEN teams (possibly from
    two different real seasons -- see _build_matchup) and loops on:

      r  retry -- resim this EXACT same matchup again (new random
         result, same two teams; nothing about who's playing changes)
      n  new matchup -- return "new" so the caller (_run_game_sim) goes
         back to picking teams (and seasons) from scratch
      Enter -- quit back to the mode menu

    Returns "new" to ask the caller to restart matchup selection, or
    None to stop entirely.
    """
    while True:
        print(f"Simulating: {team_a.name} vs. {team_b.name} ...")
        result = simulate_game(team_a, team_b, league_avg)
        print_box_score(result)

        choice = _prompt("Play again? [r]etry this matchup, [n]ew matchup, "
                          "or Enter to quit: ").strip().lower()
        print()
        if choice == "r":
            continue
        if choice == "n":
            return "new"
        return None


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


def print_season_mvp(conn, season: str, standings: List[dict], highlight: Optional[str] = None) -> None:
    """
    This SIMULATED season's MVP -- awards.mvp_score applied to this
    run's own simulated box scores (PIE/TS%/USG% computed straight from
    the stored player_game_stats rows, see db.get_simulated_advanced_stats),
    same formula backtested against real MVP winners at a 90% holdout
    hit-rate (see awards.py / backtest_mvp.py). Top 5 shown for
    context, not just the winner -- a close #1/#2 reads very
    differently from a runaway pick.
    """
    ranked = simulated_mvp_candidates(conn, season, standings)
    if not ranked:
        return
    team_of = {name: stats["team"] for name, stats in db.get_simulated_advanced_stats(conn, season).items()}

    print()
    print(_style("-- SIMULATED MVP --", "bold"))
    for i, (name, score) in enumerate(ranked[:5], start=1):
        team = team_of.get(name, "")
        marker = YOUR_TEAM_MARKER if team == highlight else ""
        # Real per-game stats, not the internal formula score -- a
        # bare "11.23" means nothing to a reader; PPG/REB/AST/TS% is
        # what actually explains why he's ranked here.
        avg = db.get_player_season_averages(conn, name, season)
        record = _team_record_str(standings, team)
        stat_line = (f"{avg['pts']:.1f} ppg, {avg['reb']:.1f} reb, {avg['ast']:.1f} ast, "
                     f"{avg['fg_pct']:.1%} FG" if avg else "")
        team_label = f"{team}, {record}" if record else team
        print(_highlight_team(f"  {_rank_number(i)} {name} ({team_label}) -- {stat_line}{marker}", highlight))
    print()


def print_season_roy(conn, season: str, standings: List[dict], highlight: Optional[str] = None) -> None:
    """
    This SIMULATED season's Rookie of the Year -- literally the MVP
    formula (see print_season_mvp) restricted to real rookies (real
    debut season + age, see awards.roy_features_from_simulated), fed
    this run's own simulated box scores. Same top-5-for-context idea,
    same real-stats-not-a-formula-score display as MVP.
    """
    ranked = simulated_roy_candidates(conn, season, standings)
    if not ranked:
        return
    team_of = {name: stats["team"] for name, stats in db.get_simulated_advanced_stats(conn, season).items()}

    print()
    print(_style("-- SIMULATED ROOKIE OF THE YEAR --", "bold"))
    for i, (name, score) in enumerate(ranked[:5], start=1):
        team = team_of.get(name, "")
        marker = YOUR_TEAM_MARKER if team == highlight else ""
        avg = db.get_player_season_averages(conn, name, season)
        record = _team_record_str(standings, team)
        stat_line = (f"{avg['pts']:.1f} ppg, {avg['reb']:.1f} reb, {avg['ast']:.1f} ast, "
                     f"{avg['fg_pct']:.1%} FG" if avg else "")
        team_label = f"{team}, {record}" if record else team
        print(_highlight_team(f"  {_rank_number(i)} {name} ({team_label}) -- {stat_line}{marker}", highlight))
    print()


def print_season_dpoy(conn, season: str, standings: List[dict], highlight: Optional[str] = None) -> None:
    """
    This SIMULATED season's Defensive Player of the Year -- real STL/
    BLK/REB plus each player's TEAM's simulated defensive strength (see
    awards.dpoy_features_from_simulated), all from this run's own
    simulated box scores. Backtested lower than MVP/ROY (50% holdout
    hit-rate vs 90%/60%) -- box-score stats genuinely can't see a lot
    of real defensive value, see awards.DPOY_WEIGHTS's comment (which
    also covers a real bug found and fixed here: an earlier, higher-
    scoring weight set let a pure offensive player who gambles for
    steals beat his own team's real defensive anchor). Real rim
    DETERRENCE (awards.dpoy_features_for_season's rim_deterrence) is
    what closed some of that gap on the REAL-season backtest -- but
    it's real player-TRACKING data (camera-based, only exists from
    2013-14 on) this project's box-score-only sim has no way to
    produce, so it can't flow into a simulated season's own numbers at
    all, only the real-data comparison.
    """
    ranked = simulated_dpoy_candidates(conn, season)
    if not ranked:
        return
    team_of = {name: stats["team"] for name, stats in db.get_simulated_advanced_stats(conn, season).items()}

    print()
    print(_style("-- SIMULATED DEFENSIVE PLAYER OF THE YEAR --", "bold"))
    for i, (name, score) in enumerate(ranked[:5], start=1):
        team = team_of.get(name, "")
        marker = YOUR_TEAM_MARKER if team == highlight else ""
        # Defensive stats, not the formula score -- BLK/STL/REB is what
        # a defensive case is actually made of.
        avg = db.get_player_season_averages(conn, name, season)
        record = _team_record_str(standings, team)
        stat_line = f"{avg['blk']:.1f} blk, {avg['stl']:.1f} stl, {avg['reb']:.1f} reb" if avg else ""
        team_label = f"{team}, {record}" if record else team
        print(_highlight_team(f"  {_rank_number(i)} {name} ({team_label}) -- {stat_line}{marker}", highlight))
    print()


def print_season_mip(conn, season: str, standings: List[dict], highlight: Optional[str] = None) -> None:
    """
    This SIMULATED season's Most Improved Player -- real scoring
    increase over the player's REAL previous season (see
    awards.mip_features_from_simulated for why the "before" side has to
    be real, not simulated: season.db is fully wiped every time a new
    season is simulated, so there is never a previous SIMULATED season
    sitting around to diff against). Backtested at MIP's own honest
    ceiling (40% holdout hit-rate) -- MIP voting is famously narrative-
    driven, see awards.MIP_WEIGHTS's comment. Skipped entirely for this
    project's earliest cached season (1996-97) -- no real prior season
    exists to compare against at all.
    """
    ranked = simulated_mip_candidates(conn, season)
    if not ranked:
        return
    team_of = {name: stats["team"] for name, stats in db.get_simulated_advanced_stats(conn, season).items()}
    # Raw features (not just the ranked score) carry pts_delta -- the
    # actual "X ppg -> Y ppg" jump the formula scored, not just its
    # internal number.
    delta_of = {f["name"]: f["pts_delta"] for f in mip_features_from_simulated(conn, season)}

    print()
    print(_style("-- SIMULATED MOST IMPROVED PLAYER --", "bold"))
    for i, (name, score) in enumerate(ranked[:5], start=1):
        team = team_of.get(name, "")
        marker = YOUR_TEAM_MARKER if team == highlight else ""
        avg = db.get_player_season_averages(conn, name, season)
        record = _team_record_str(standings, team)
        delta = delta_of.get(name)
        if avg and delta is not None:
            prev_pts = avg["pts"] - delta
            stat_line = f"{prev_pts:.1f} -> {avg['pts']:.1f} ppg ({delta:+.1f})"
        else:
            stat_line = ""
        team_label = f"{team}, {record}" if record else team
        print(_highlight_team(f"  {_rank_number(i)} {name} ({team_label}) -- {stat_line}{marker}", highlight))
    print()


def print_season_coy(conn, season: str, standings: List[dict], highlight: Optional[str] = None) -> None:
    """
    This SIMULATED season's Coach of the Year -- real head coach per
    team (awards.load_team_coaches, riding along free on the same real
    roster fetch every season already needed), scored on the team's
    real win% THIS season blended with its improvement over last
    season (see awards.COY_WEIGHTS -- real COY winners are
    overwhelmingly top-3-record teams, not just the biggest jumpers;
    an earlier version scoring improvement ALONE held a 67%-holdout
    formula down to ~20%). Real previous-season win% (see
    awards.coy_features_from_simulated -- same "before has to be real"
    reasoning as MIP).

    Ground truth for this ONE award (awards.REAL_COY_WINNERS) could NOT
    be verified against a live API like MVP/ROY/DPOY/MIP were --
    nba_api has no coach-award endpoint at all. Cross-checked against a
    real, current web source instead (see that table's own comment),
    which is real verification, just a step down from the other three.
    Real head-coach DATA itself also has genuine gaps in older seasons
    (patchy, not a clean before/after floor -- confirmed directly: some
    1990s/2000s seasons are missing over half the league's coaches, and
    coverage only becomes consistently complete from 2004-05 on), so a
    season this project can't even name a real winner's coach for isn't
    a formula failure, it's a data gap.
    """
    ranked = simulated_coy_candidates(conn, season, standings)
    if not ranked:
        return
    team_of = {coach: team for team, coach in load_team_coaches(season).items() if coach}
    # Real team record, not the formula score -- a coach has no box
    # score of his own; his "stats" are his team's record and how much
    # it improved. win_pct_delta * this season's games converts the
    # formula's real underlying fraction back into an actual win count.
    delta_of = {f["name"]: f["win_pct_delta"] for f in coy_features_from_simulated(conn, season, standings)}
    games_of = {row["team"]: row["W"] + row["L"] for row in standings}

    print()
    print(_style("-- SIMULATED COACH OF THE YEAR --", "bold"))
    for i, (name, score) in enumerate(ranked[:5], start=1):
        team = team_of.get(name, "")
        marker = YOUR_TEAM_MARKER if team == highlight else ""
        record = _team_record_str(standings, team)
        delta = delta_of.get(name)
        win_delta = f"{delta * games_of.get(team, 0):+.0f} wins from last season" if delta is not None else ""
        team_label = f"{team}, {record}" if record else team
        print(_highlight_team(f"  {_rank_number(i)} {name} ({team_label}) -- {win_delta}{marker}", highlight))
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


def _ask_and_show_series(matchup_label: str, series: dict, final_line: str, highlight: Optional[str]) -> None:
    """
    For a series the followed team ISN'T playing in (every other
    series in a round, and the Finals whenever it's not asked for
    unconditionally): asks BEFORE anything is revealed whether to
    watch it game by game (box scores included) or just see the final
    result.

    This replaces printing the result INSTANTLY and only THEN offering
    game-by-game detail -- that order already spoiled the outcome
    before the offer to see more even appeared (reported directly:
    "you have to give them the option to, not sim and then ask").
    """
    watch = _prompt(
        f"  {matchup_label}: 'g' to watch game by game (box scores), "
        f"or press Enter to just see the result: "
    ).strip().lower()
    if watch in ("g", "game", "gamebygame"):
        _replay_playoff_series(series, matchup_label, final_line, highlight)
    else:
        print(_format_playoff_line(final_line, highlight))


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
    replayed game by game (_replay_playoff_series) automatically.
    Every OTHER series in the round is asked about FIRST, before
    anything is revealed (_ask_and_show_series) -- watch it game by
    game, or just see the result -- rather than printing the result
    instantly and only THEN offering more detail, which already
    spoiled the outcome before that offer even appeared (reported
    directly).

    The play-in works the same way: asked about up front, whether or
    not the followed team is actually in it (per the user -- it used
    to just print, with no way to watch it or see a box score at all,
    regardless of who was in it)."""
    # No header at all for a season that had no play-in (every season
    # before 2019-20 -- see playoffs.playoff_format). Printing an empty
    # "Play-In Tournament" heading implied a round that never existed.
    if conf_result["play_in_log"]:
        print()
        print(_style(f"-- {conf_result['conference']} Play-In Tournament --", "bold"))
        play_in_games = conf_result.get("play_in_games") or []
        watch = play_in_games and _prompt(
            "'g' to watch the play-in game by game (box scores included), "
            "or press Enter to just see the results: "
        ).strip().lower() in ("g", "game", "gamebygame")
        if watch:
            _replay_play_in(play_in_games, highlight)
        else:
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
            matchup_label, _, _ = line.partition(":")
            if series and highlight in (series["winner"], series["loser"]):
                _replay_playoff_series(series, matchup_label.strip(), line, highlight)
            elif series:
                _ask_and_show_series(matchup_label.strip(), series, line, highlight)
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
    _print_series_mvp(conf_result["tree"]["round3"], "Conference Finals", highlight)


def _series_mvp(series: dict) -> Tuple[str, dict]:
    """
    Picks a series MVP from the WINNING team's players -- a real
    basketball rule (an MVP from the team that lost the series doesn't
    happen), using a plain "game score"-style standout number (points,
    plus rebounds/assists/steals/blocks weighted by their approximate
    value, minus turnovers) over that series' own averages
    (playoffs.compute_series_player_averages).

    Deliberately NOT one of this project's backtested real-season award
    formulas (MVP/ROY/DPOY/MIP/COY) -- there's no real "who actually won
    Finals MVP" ground truth fetched to score a formula against here,
    just a defensible tiebreaker among a series' best players on the
    team that won it. Returns (player name, their series averages dict).
    """
    averages = compute_series_player_averages(series["game_log"])
    winners = [a for a in averages.values() if a["team"] == series["winner"]]
    best = max(winners, key=lambda a: a["pts"] + a["reb"] + 1.5 * a["ast"]
               + 2 * a["stl"] + 2 * a["blk"] - a["tov"])
    return best["player"], best


def _print_series_mvp(series: dict, label: str, highlight: Optional[str] = None) -> None:
    """One line naming a series' MVP with his series averages -- see
    _series_mvp for the (non-backtested) formula behind the pick."""
    name, avg = _series_mvp(series)
    marker = YOUR_TEAM_MARKER if avg["team"] == highlight else ""
    print(_highlight_team(
        f"  {label} MVP: {_style(name, 'bold')} ({avg['team']}) -- "
        f"{avg['pts']:.1f} ppg, {avg['reb']:.1f} reb, {avg['ast']:.1f} ast{marker}",
        highlight,
    ))


def print_series_player_averages(series: dict, label: str, highlight: Optional[str] = None) -> None:
    """
    Per-player averages for one playoff SERIES only (its game_log) --
    generalized out of what used to be print_finals_averages's whole
    body, so the same "just the numbers" view works for any round, not
    only the Finals. `label` names the round and matchup for the
    header. Still not written to season.db (see playoffs.py's
    docstring) -- playoffs.py never stores anything, this is the only
    record of what happened in a series.
    """
    averages = compute_series_player_averages(series["game_log"])
    print()
    print(_style(f"-- {label} -- PLAYER AVERAGES --", "bold"))
    for team_name in (series["winner"], series["loser"]):
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


def print_finals_averages(finals: dict, highlight: Optional[str] = None) -> None:
    """
    Per-player averages for the NBA Finals series only -- not the
    whole playoff run, per what was actually asked for, and not
    written to season.db (see playoffs.py's docstring). Winner's
    roster first. This is deliberately just the numbers -- picking an
    actual Finals MVP from them is a later step, not this one.
    """
    print_series_player_averages(finals, "NBA FINALS", highlight)


def _all_playoff_series(result: dict) -> List[Tuple[str, dict]]:
    """
    Every best-of SERIES actually played this postseason -- east/west
    First Round (4 each), Conference Semifinals (2 each), Conference
    Finals (1 each), and the Finals -- paired with a label naming the
    round and matchup, for the browser below. Deliberately excludes the
    play-in: those are single elimination games (see run_play_in) with
    no game_log/series structure to build averages from, not a series
    at all.
    """
    series_list = []
    round_names = ["First Round", "Conference Semifinals", "Conference Finals"]
    for conf_result in (result["east"], result["west"]):
        conf = conf_result["conference"]
        tree = conf_result["tree"]
        # Same [round1, round2, [round3]] grouping _print_conference_bracket
        # uses -- round3 (conf finals) is a single series, not a list.
        round_series = [tree["round1"], tree["round2"], [tree["round3"]]]
        for round_name, group in zip(round_names, round_series):
            for series in group:
                series_list.append((f"{conf} {round_name}: {series['winner']} vs {series['loser']}", series))
    finals = result["finals"]
    series_list.append((f"NBA Finals: {finals['winner']} vs {finals['loser']}", finals))
    return series_list


def _run_playoff_series_browser(result: dict, highlight: Optional[str] = None) -> None:
    """
    Lets the user pull up player averages -- or, per the user, a full
    game-by-game replay with box scores -- for any OTHER playoff
    series, not just the followed team's own (which already gets
    replayed automatically in _print_conference_bracket). A plain
    number shows averages (the original scope here); 'g' plus a number
    replays that series game by game instead, reusing
    _replay_playoff_series exactly as the followed team's own series
    does -- it already handles a series `highlight` isn't actually
    playing in (reports from the home team's side instead).
    """
    series_list = _all_playoff_series(result)
    while True:
        for i, (label, _) in enumerate(series_list, 1):
            print(f"  {i}. {label}")
        choice = _prompt(
            "View player averages for another playoff series (enter a number), "
            "'g' + a number to watch it game by game, or press Enter to finish: "
        ).strip().lower()

        if choice == "":
            return

        replay = choice.startswith("g")
        number = choice[1:] if replay else choice
        if number.isdigit() and 1 <= int(number) <= len(series_list):
            label, series = series_list[int(number) - 1]
            if replay:
                final_line = (f"  {label}: {series['winner']} def. {series['loser']}, "
                              f"{series['wins'][series['winner']]}-{series['wins'][series['loser']]}")
                _replay_playoff_series(series, label, final_line, highlight)
            else:
                print_series_player_averages(series, label, highlight)
            continue

        print("Please enter a number, 'g' + a number, or press Enter to finish.")


def print_playoffs(result: dict, abbrev: Dict[str, str], highlight: Optional[str] = None) -> None:
    """
    Prints the play-in log, bracket diagram, every round's detail, and
    the Finals (plus Finals player averages) for a
    playoffs.run_playoffs() result. Ends with an opt-in browser
    (_run_playoff_series_browser) for player averages from any OTHER
    series played that postseason -- previously only the Finals had
    averages available at all. Pure display -- playoffs.py already
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
    finals_label = f"{result['east']['champion']} vs {result['west']['champion']}"
    final_line = (
        f"  {finals['winner']} def. {finals['loser']}, "
        f"{finals['wins'][finals['winner']]}-{finals['wins'][finals['loser']]}"
    )
    # Deliberately NOT auto-replayed game by game even when the
    # followed team is playing in it, unlike every earlier round (see
    # _print_conference_bracket) -- per the user, the Finals screen was
    # too much all at once (champion banner, MVP, averages, AND a full
    # forced game-by-game replay). Asked first instead (_ask_and_show_
    # series, same as every other non-followed series), same shape
    # either way, whether or not it's your team in it.
    print()
    print(SECTION)
    print(_style("-- NBA FINALS --", "bold"))
    print(_highlight_team(f"  {finals_label}", highlight))
    _ask_and_show_series(finals_label, finals, final_line, highlight)
    print(SECTION)
    print()
    print(_style(f"NBA CHAMPION: {result['champion']}".center(LINE_WIDTH), "bold", "yellow"))
    _print_series_mvp(finals, "Finals", highlight)
    print_finals_averages(finals, highlight)
    _run_playoff_series_browser(result, highlight)
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


def print_team_moves(moves: List[dict], highlight: Optional[str] = None,
                      team_scope: Optional[str] = None) -> None:
    """
    Prints every real in-season trade this season (see
    transactions.summarize_moves) as "Player: Team A -> Team B" --
    just the real WHERE, not the WHY (no trade-details data is fetched
    anywhere in this project, only real game-log evidence of who
    suited up for whom).

    `team_scope` names the team when `moves` has already been filtered
    to one. Without it an empty list printed "No real in-season trades
    this season," which claimed the whole LEAGUE stood pat when really
    it was just your team -- reported directly, and wrong often enough
    to matter: in 2025-26, 28 of the 30 teams made an in-season move,
    so the other two saw a league-wide claim that was false.
    """
    print()
    print(DIVIDER)
    title = f"{team_scope.upper()} -- MOVES THIS SEASON" if team_scope else "TEAM MOVES THIS SEASON"
    print(_style(title.center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    if not moves:
        if team_scope:
            print(f"{team_scope} made no in-season trades this season.")
        else:
            print("No real in-season trades anywhere in the league this season.")
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

    The 30-team list is NOT printed up front -- 't' shows it on demand,
    same as every other browser here. Most passes through this are a
    single Enter to move on, so the old unconditional print was a lot
    of output for no reason.
    """
    while True:
        choice = _prompt(
            "View another team's moves? Enter a number, 't' for the team list, "
            "'a' for the full league list, or press Enter to finish: "
        ).strip().lower()

        if choice == "":
            return
        if choice == "t":
            print_team_list(team_names)
            continue
        if choice == "a":
            print_team_moves(all_moves, highlight=highlight)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(team_names):
            chosen_name = team_names[int(choice) - 1]
            team_moves = [m for m in all_moves if chosen_name in m["teams"]]
            print_team_moves(team_moves, highlight=highlight, team_scope=chosen_name)
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
        choice = _prompt(
            "View another team's injuries? Enter a number, 't' for the team list, "
            "'a' for the full league list, or press Enter to finish: "
        ).strip().lower()

        if choice == "":
            return
        if choice == "t":
            print_team_list(team_names)
            continue
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
    runs, and can be highlighted everywhere below), simulates the full
    real season (overwriting any previously simulated one -- see
    season.py's simulate_season for why re-running isn't additive),
    then paces through it game by game (screen 5) -- Enter for the
    next game, 't' to jump to the real trade deadline, 'e' to jump
    straight to the end of the season. This is mandatory, not a "want
    to watch? y/n" gate: nothing below appears until this loop
    actually reaches the last game (see run_team_game_log_replay).

    Once there, in order (matching the UI mockup's screen 6/7 split):
    (1) real in-season moves and injuries, scoped to just the followed
    team with an opt-in browser to look up another team, printed
    automatically -- roster CONTEXT for the season just watched, not
    optional side content; (2) "end of season stats" -- the followed
    team's real-vs-simulated season averages, also automatic; (3) a
    prompt for the season awards; (4) LAST, one single prompt for
    standings and the playoffs together -- per the user, that's one
    moment ("how did it turn out, and who won it"), not two separate
    things to confirm one at a time.
    """
    print()
    print_team_list_with_best_player(teams, team_names)
    my_team_name = select_team_number(team_names, "Select YOUR team (highlighted throughout):")
    if my_team_name is None:
        return
    print(f"-> {my_team_name}\n")

    if not _confirm("Simulate the full season now?"):
        return

    # verbose=False -- see simulate_season's docstring: printing "N games
    # simulated in X.XXs" right before the game-by-game pacing below
    # would give away the ending before you've watched a single game.
    simulate_season(season=season, fresh=True, verbose=False)

    conn = db.init_db()

    # Mandatory, BEFORE anything below -- see the docstring above. The
    # season itself is already fully simulated and stored the instant
    # simulate_season returns; this loop only controls the PACE at
    # which it's revealed to you. Pressing 'e' immediately here plays
    # out exactly like watching all 82 games one by one -- same
    # result, no shortcut in what gets computed.
    run_team_game_log_replay(conn, my_team_name, season, highlight=my_team_name)
    _run_game_log_browser(conn, team_names, season, highlight=my_team_name)

    standings = db.get_standings(conn, season)

    # Moves and injuries -- all scoped to YOUR team by default (the
    # league-wide dumps were too much at once), each with an opt-in
    # browser to look up another team if you want one. Printed BEFORE
    # the "end of season" stats/awards/playoffs sequence below, since
    # they're roster CONTEXT for the season just watched, not part of
    # that sequence itself.
    # For the season actually being played -- this defaulted to
    # DEFAULT_SEASON, so picking any other season still showed 2025-26's
    # trades. Every other view here was already season-scoped; this one
    # was missed when the season picker landed.
    membership = load_roster_membership(season)
    all_moves = summarize_moves(membership)
    my_moves = [m for m in all_moves if my_team_name in m["teams"]]
    print_team_moves(my_moves, highlight=my_team_name, team_scope=my_team_name)
    _run_moves_browser(all_moves, team_names, highlight=my_team_name)

    all_injuries = db.get_injuries(conn, season)
    healed = [row for row in all_injuries if not row["still_out_at_season_end"]]
    still_out = [row for row in all_injuries if row["still_out_at_season_end"]]
    print_injuries([r for r in healed if r["team"] == my_team_name],
                    title="INJURIES DURING THE SEASON (HEALED BEFORE PLAYOFFS)", highlight=my_team_name)
    print_injuries([r for r in still_out if r["team"] == my_team_name],
                    title="STILL OUT ENTERING THE PLAYOFFS", highlight=my_team_name)
    _run_injuries_browser(conn, team_names, season, highlight=my_team_name)

    # "END OF SEASON STATS" (screen 6): the followed team's real-vs-
    # simulated season averages, printed automatically -- no gate in
    # front of it, same as moves/injuries above; this is the number
    # this whole project exists to produce.
    print_team_season_averages(conn, teams[my_team_name], season)
    _run_season_averages_browser(conn, teams, team_names, season)

    # THEN awards, behind their own prompt -- per the user: stats,
    # then a prompt for awards, then a prompt for the playoffs. Three
    # separate beats, not one auto-printed wall of everything. Default
    # Enter=yes here (the normal _confirm convention) -- a real PROMPT
    # existing at all was the actual fix (it used to auto-print with no
    # prompt whatsoever); per the user, Enter should still be the fast
    # "yes" path, with 'n' as the explicit opt-out, not the reverse.
    if _confirm("See the season awards?"):
        print_season_mvp(conn, season, standings, highlight=my_team_name)
        print_season_roy(conn, season, standings, highlight=my_team_name)
        print_season_dpoy(conn, season, standings, highlight=my_team_name)
        print_season_mip(conn, season, standings, highlight=my_team_name)
        print_season_coy(conn, season, standings, highlight=my_team_name)

    # LAST: standings and the playoffs together, one prompt -- "how did
    # the season finish, and who won it" is one moment, not a standings
    # confirm followed by a separate, easy-to-miss playoffs confirm.
    if _confirm("See the final standings and the playoffs?"):
        view = _prompt("View standings by conference, or overall? (c/o): ").strip().lower()
        if view == "c":
            print_standings_by_conference(standings, teams, highlight=my_team_name)
        else:
            print_standings(standings, highlight=my_team_name)

        real_standings = fetch_real_standings(season)
        print_standings_comparison(standings, real_standings, highlight=my_team_name)

        playoff_result = run_playoffs(conn, season, teams, standings, league_avg)
        print_playoffs(playoff_result, abbrev, highlight=my_team_name)


def _run_season_averages_browser(conn, teams: Dict[str, Team], team_names: List[str], season: str) -> None:
    """
    Lets the user look up any OTHER team's simulated season averages,
    one at a time, several in a row, or all of them at once -- rather
    than being limited to just the team they followed.
    """
    while True:
        choice = _prompt(
            "View another team's season averages? Enter a number, 't' for the team list, "
            "'a' for all, or press Enter to finish: "
        ).strip().lower()

        if choice == "":
            return
        if choice == "t":
            print_team_list(team_names)
            continue

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

# Seasons that were not a normal 82 games, and why. Verified against the
# cached real schedules rather than from memory -- the game counts below
# are what this project actually simulates. Shown when one is picked, so
# a 50-game season doesn't just look like broken data.
SEASON_NOTES = {
    "1998-99": ("Lockout season -- 50 games per team instead of 82. "
                "The season didn't tip off until February 1999."),
    "2011-12": ("Lockout season -- 66 games per team instead of 82. "
                "The season started on Christmas Day 2011."),
    "2012-13": ("81 games for two teams: a real cancelled game "
                "(Boston at Indiana) after the Sandy Hook shooting was "
                "never made up."),
    "2019-20": ("COVID season -- suspended in March 2020, then finished "
                "in the Orlando bubble. Teams played 64 to 75 games, and "
                "only 22 teams were invited back, so the schedule is "
                "genuinely uneven. It also used a one-off play-in: the "
                "9th seed only got a shot if it finished within 4 games "
                "of the 8th."),
    "2020-21": ("COVID-shortened season -- 72 games per team, starting "
                "in December 2020. The trade deadline was in March."),
}


def print_season_note(season: str) -> None:
    """Explain a season that wasn't 82 games, so its schedule and win
    totals don't read as a bug. Silent for every normal season."""
    note = SEASON_NOTES.get(season)
    if not note:
        return
    # Wrapped to the table width so a long note doesn't run off the
    # screen -- these are two or three sentences, not a label.
    print(_style(f"  Note on {season}:", "bold"))
    for line in textwrap.wrap(note, LINE_WIDTH - 4):
        print(f"    {line}")
    print()


def select_season(seasons: List[str]) -> Optional[str]:
    """
    Pick which real season's ROSTERS to pull from, BY NUMBER -- typing
    "1996-97" exactly, hyphen and all, was needlessly fiddly (reported
    directly). 1 is the newest season, counting back through history.

    Enter alone takes the newest, since that's what almost everyone
    wants. The season string itself is still accepted, so anyone who
    already knows what they want can just type it.

    Game Sim's ONLY caller now (History Sim moved to the richer
    select_season_range screen) -- so the blurb only mentions real
    rosters, not schedule/injuries/trades/playoff rules, none of which
    apply to a single exhibition game (reported directly: that context
    didn't make sense here).
    """
    print(DIVIDER)
    print(_style("PICK A SEASON".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    print(f"{len(seasons)} real seasons are available, {seasons[-1]} through {seasons[0]}.")
    print("Every one pulls that season's real rosters.")
    print()
    # Numbered, five per line -- keeps all 30 in a compact block rather
    # than a 30-line list, while still giving every one a number to type.
    # The width is sized to the longest real entry ("30. 1996-97"), not
    # eyeballed.
    for i in range(0, len(seasons), 5):
        row = seasons[i:i + 5]
        print("   " + "   ".join(f"{i + j + 1:>2}. {s}" for j, s in enumerate(row)))
    print()
    while True:
        choice = _prompt(f"Season number 1-{len(seasons)} "
                          f"(or press Enter for {seasons[0]}): ").strip()
        if choice == "":
            return seasons[0]
        if choice.isdigit() and 1 <= int(choice) <= len(seasons):
            return seasons[int(choice) - 1]
        if choice in seasons:
            return choice  # typed the season itself -- still fine
        print(f"Please enter a number from 1 to {len(seasons)}.")


# =====================================================================
# MULTI-SEASON RUN (year by year through real NBA history)
# =====================================================================

# A summer has ~90 league-wide arrivals and almost all of them are
# fringe, so the report needs a floor. It's on POINTS now rather than
# minutes: a move is news because of who can score, and a 10-minute
# floor was letting through end-of-bench defenders while ranking a
# genuine sixth man below them. 5.0 ppg is roughly "played a real role
# somewhere" -- low enough that a rotation piece still shows up.
MIN_OFFSEASON_PPG = 5.0
# How many names per category. Six keeps the whole screen readable, and
# they're sorted by scoring so the ones that get cut are the ones that
# matter least (offseason.diff_seasons does the sorting).
OFFSEASON_ROWS = 6


def _print_team_offseason(changes: dict, team_name: str) -> None:
    """One team's summer: who came in, who left, ranked by real scoring.

    Split out from the report below so the "look up another team"
    browser can reuse it unchanged -- same auto-print-yours/opt-in-for-
    anyone-else pattern as moves, injuries and season averages.
    """
    print(_style(f"  {team_name}", "bold"))

    def _show(rows, label, direction=None):
        # `direction` says which end of a move to name: "from" for
        # players arriving, "to" for players leaving. Getting this wrong
        # printed departures as "(from Atlanta Hawks)" -- naming the
        # team they just left rather than where they went.
        real = [r for r in rows if r["pts"] >= MIN_OFFSEASON_PPG][:OFFSEASON_ROWS]
        if not real:
            return
        print(f"    {label}")
        for r in real:
            where = f" ({direction} {r[direction]})" if direction and direction in r else ""
            print(f"      {r['player']:<26} {r['pts']:>4.1f} ppg{where}")
    _show(changes["gained"], "Signed/traded in:", "from")

    # Real draft year (see offseason._arrival_kind) splits these two --
    # a rookie and a veteran arriving from overseas used to be lumped
    # together as "new to the league" because the roster data alone
    # can't tell them apart. Drafted gets its own block (not the shared
    # _show helper) for two reasons: it says so explicitly when EMPTY
    # (a quiet draft is still worth a line, not a silently missing
    # section -- reported directly), and it appends round/pick when
    # that's actually on file (see offseason._draft_picks -- round/pick
    # weren't backfilled quite as universally as draft_year itself, so
    # a drafted player without them just shows without the extra detail).
    drafted = [r for r in changes["arrived"] if r.get("how") == "drafted" and r["pts"] >= MIN_OFFSEASON_PPG][:OFFSEASON_ROWS]
    print("    Drafted:")
    if drafted:
        for r in drafted:
            pick = (f" (Round {r['draft_round']}, Pick {r['draft_pick']})"
                    if "draft_round" in r and "draft_pick" in r else "")
            print(f"      {r['player']:<26} {r['pts']:>4.1f} ppg{pick}")
    else:
        print("      No draft picks this summer.")

    signed = [r for r in changes["arrived"] if r.get("how") != "drafted"]
    _show(signed, "Signed (new to the league):")
    _show(changes["lost"], "Left for another team:", "to")
    _show(changes["left_league"], "Out of the league:")
    # Scoped to everything OTHER than the draft -- that always prints
    # its own line above now, so this only fires when there's genuinely
    # nothing else to report either.
    other_rows = signed + changes["gained"] + changes["lost"] + changes["left_league"]
    if not any(r["pts"] >= MIN_OFFSEASON_PPG for r in other_rows):
        print("    Otherwise, a quiet summer -- nobody else who scored moved.")
    print()


def _run_offseason_browser(diff: dict, team_names: List[str]) -> None:
    """Look up any other team's summer, same number/'t'/Enter browser as
    the moves/injuries/averages ones. Team numbers are the NEXT season's
    teams, since that's the league you're about to play in -- a team
    that was renamed over the summer only exists under its new name."""
    while True:
        choice = _prompt(
            "See another team's moves? Enter a number, 't' for the team list, "
            "or press Enter to continue: "
        ).strip().lower()

        if choice == "":
            return
        if choice == "t":
            print_team_list(team_names)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(team_names):
            chosen = team_names[int(choice) - 1]
            print()
            _print_team_offseason(team_changes(diff, chosen), chosen)
            continue
        print("Please enter a number from the list, 't' to see it, or press Enter to continue.")


def _offseason_report(diff: dict, team_name: str, next_team_name: str,
                      team_names: Optional[List[str]] = None) -> None:
    """
    What changed for YOUR team between two seasons, plus any franchise
    renames league-wide. Scoped to your team by default, same rule as
    every other view here -- ninety league-wide arrivals is not a thing
    anyone reads -- with `team_names` (the next season's teams) enabling
    the opt-in browser for anyone else.
    """
    print()
    print(DIVIDER)
    print(_style(f"OFFSEASON: {diff['from_season']} -> {diff['to_season']}".center(LINE_WIDTH),
                 "bold", "cyan"))
    print(DIVIDER)

    for rename in diff["renamed"]:
        print(f"  {rename['from']} are now the {rename['to']}.")
    if diff["renamed"]:
        print()

    _print_team_offseason(team_changes(diff, next_team_name), next_team_name)
    if team_names:
        _run_offseason_browser(diff, team_names)


# One real, short headline per season -- the mockup's "what happened
# that year" column on the season-pick screen. These are recalled real
# NBA facts, NOT verified against a live API the way every stat this
# project actually simulates with is (see ACCURACY.md's "Season
# awards" section for how MVP/ROY/etc. ground truth IS verified) --
# they're flavor text on a picker screen,
# not an input to anything computed, so the cost of being wrong is
# "looks silly," not "breaks a number." Flag any that are wrong and
# they get fixed on the spot, same as TEAM_DIVISIONS/TRADE_DEADLINE_
# BY_SEASON's "stable real-world fact, not worth fetch infrastructure"
# precedent elsewhere in this file. The four lockout/COVID/Sandy-Hook
# seasons ALSO have a fuller note (SEASON_NOTES, printed after picking,
# which explains the schedule anomaly itself) -- this column still
# gets its own short entry for them, since leaving this screen blank
# for a season everyone's actually heard of read as a gap, not economy.
SEASON_HIGHLIGHTS = {
    "1996-97": "Jordan's 5th title",
    "1997-98": "The Last Dance -- Jordan's 6th title",
    "1998-99": "Lockout season -- only 50 games",
    "1999-00": "Shaq/Kobe three-peat begins",
    "2002-03": "Jordan's final season, with Washington",
    "2003-04": "LeBron's rookie year; Pistons upset the Lakers",
    "2004-05": "Bobcats join -- league goes to 30 teams",
    "2005-06": "Katrina: Hornets split OKC/New Orleans",
    "2008-09": "Sonics relocate to OKC as the Thunder",
    "2010-11": "The Decision -- LeBron joins Miami",
    "2011-12": "Lockout season -- only 66 games",
    "2012-13": "Nets move from New Jersey to Brooklyn",
    "2014-15": "Warriors' dynasty begins",
    "2015-16": "Warriors go 73-9, the best record ever",
    "2018-19": "Kawhi leads Toronto to its first title",
    "2019-20": "COVID -- season finishes in the Orlando bubble",
    "2020-21": "COVID -- 72-game season",
    "2022-23": "Nuggets win their first title",
    "2023-24": "Celtics win a record 18th title",
}


def select_season_range(seasons: List[str]) -> List[str]:
    """
    Screen 3 -- pick which season(s) of History Sim to play. Oldest
    first here (opposite of select_season's newest-first order above),
    since "history sim" starting at the actual beginning of history
    reads naturally, and the mockup this screen is drawn from defaults
    the same way.

    One row per season (matching the mockup) rather than the compact
    5-per-line block used elsewhere, since each row now carries the
    real best record that season (load_real_best_record -- blank for a
    season not yet cached; see data_source.build_and_cache_best_record)
    and a real highlight where one exists (SEASON_HIGHLIGHTS) -- also
    blank for a season with nothing notable, not padded with filler.

    Both prompts live on one screen, and the season list is only
    printed once -- typing an end season doesn't reprint the same 30
    lines a second time.

    Returns the seasons to actually play, oldest first. A single-
    season pick returns a length-1 list, not a special case -- a
    caller branches on len() to route to the richer single-season flow
    (all the per-team browsing screens) or the multi-season one.
    """
    ordered = seasons[::-1]
    print()
    print(DIVIDER)
    print(_style("HISTORY SIM -- PICK A SEASON".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    print(f"  {'#':>3}  {'SEASON':<10} {'THE TEAM TO BEAT':<28} WHAT HAPPENED THAT YEAR")
    print(SECTION)
    for i, s in enumerate(ordered):
        record = load_real_best_record(s)
        best = f"{record['team']} {record['wins']}-{record['losses']}" if record else ""
        print(f"  {i + 1:>3}  {s:<10} {best:<28} {SEASON_HIGHLIGHTS.get(s, '')}")
    print(SECTION)
    print()

    def _pick(label: str, default_index: int) -> int:
        while True:
            raw = _prompt(f"{label} (1-{len(ordered)}, Enter for "
                          f"{ordered[default_index]}): ").strip()
            if raw == "":
                return default_index
            if raw.isdigit() and 1 <= int(raw) <= len(ordered):
                return int(raw) - 1
            if raw in ordered:
                return ordered.index(raw)
            print(f"Please enter a number from 1 to {len(ordered)}.")

    start = _pick("Start season", 0)
    end = _pick("Play through to which season", start)
    if end < start:
        start, end = end, start
    return ordered[start:end + 1]


def run_multi_season_flow(abbrev: Dict[str, str], run: List[str]) -> None:
    """
    Play straight through real NBA history: simulate each season in
    `run`, in order, following one franchise the whole way. `run` is
    already the exact seasons to play, oldest first -- see
    select_season_range, the screen that picks it.

    Every season uses its OWN real rosters, so the real offseason
    already happened between them and offseason.py reports what changed
    (see that module's docstring for what this deliberately is NOT).
    The CHAMPIONS are simulated though, so they diverge from real
    history immediately -- that's the point of running it.

    Your team is followed through renames and relocations
    (offseason.franchise_map), so starting with the 1996-97 Seattle
    SuperSonics leaves you holding the Thunder in 2008-09 rather than
    losing the team mid-run.
    """
    print(f"-> {run[0]} through {run[-1]} ({len(run)} seasons)\n")

    teams = load_teams(run[0])
    team_names = sorted(teams)
    print_team_list_with_best_player(teams, team_names)
    my_team = select_team_number(team_names, "Select the franchise to follow:")
    if my_team is None:
        return
    print(f"-> {my_team}\n")

    history = []
    for index, season in enumerate(run):
        print(DIVIDER)
        print(_style(f"STARTING THE {season} SEASON".center(LINE_WIDTH), "bold", "cyan"))
        print(DIVIDER)
        print_season_note(season)

        teams = load_teams(season)
        league_avg = compute_league_averages(teams, load_league_pace_variation(season))
        # verbose=False -- same reason as run_season_flow: printing the
        # "N games simulated" line here would give away the season
        # before the pacing loop right below has shown a single game.
        simulate_season(season=season, fresh=True, verbose=False)
        conn = db.init_db()

        # Mandatory, BEFORE any outcome -- this loop used to go
        # straight from "season simulated" to the record/champion/MVP
        # recap below with no pacing option at all, a real gap found by
        # testing, not a deliberate shortcut. See run_season_flow's
        # docstring: reaching the end of the season (Enter through
        # every game, or 't'/'e' to fast-forward) is what unlocks
        # everything below, not a separate confirm.
        run_team_game_log_replay(conn, my_team, season, highlight=my_team)

        standings = db.get_standings(conn, season)
        record = next((r for r in standings if r["team"] == my_team), None)
        # Playoffs are simulated unconditionally -- the champion/finish
        # feed the dynasty summary at the very end of the whole run
        # regardless of whether THIS season's recap gets shown below,
        # so computing it can't be skipped, only its display can.
        playoff_result = run_playoffs(conn, season, teams, standings, league_avg)
        champion = playoff_result["finals"]["winner"]
        finish = _playoff_finish(playoff_result, my_team)
        history.append({"season": season, "team": my_team,
                        "W": record["W"] if record else 0, "L": record["L"] if record else 0,
                        "finish": finish, "champion": champion})

        # Gated -- this used to auto-print the instant the game log
        # replay ended with no prompt at all (reported directly:
        # "awards and playoff outcomes still show right after the
        # season ends"). Default Enter=yes (the normal _confirm
        # convention) -- the missing PROMPT was the actual bug, not
        # its default; per the user, 'n' is the explicit opt-out, not
        # the other way around.
        #
        # Deliberately NO champion/finish here -- those are PLAYOFF
        # outcomes (reported directly: "the player hasn't simmed the
        # playoffs yet"), and this confirm is regular-season awards
        # only. The champion is revealed by the 'b' bracket option
        # below (print_playoffs), the only place that's actually
        # simulated the playoffs at the point it's shown.
        if _confirm("See this season's awards?"):
            if record:
                print(f"  {_style(my_team, 'bold', 'cyan')}: {record['W']}-{record['L']}")
            # Full top-5 for every award, straight away -- per the
            # user, this shouldn't be a terse one-line-per-award recap
            # with the real breakdown only reachable as a SEPARATE 'm'
            # step afterward. Saying yes here means seeing them, not
            # just being told who won.
            print_season_mvp(conn, season, standings, highlight=my_team)
            print_season_roy(conn, season, standings, highlight=my_team)
            print_season_dpoy(conn, season, standings, highlight=my_team)
            print_season_mip(conn, season, standings, highlight=my_team)
            print_season_coy(conn, season, standings, highlight=my_team)

        is_last = index == len(run) - 1
        # Pause with the usual "show me more" options -- this used to
        # be skipped entirely for the LAST season in the range (which
        # is every season of a single-season run), so 'b' -- the option
        # that plays your team's own playoff series game by game and
        # lets you pull up a box score -- was simply never offered
        # (reported directly). It's offered every season now; only the
        # OFFSEASON REPORT below is skipped on the last one, since
        # there is no next season for it to describe.
        #
        # Enter (move on) is refused until 'b' has actually been
        # pressed at least once -- per the user, advancing to next
        # season without ever having simulated/seen THIS season's
        # playoffs made no sense. 'e' (stop the whole run here) is
        # always allowed regardless -- it's leaving the loop entirely,
        # not skipping ahead past something unseen.
        viewed_playoffs = False
        while True:
            next_label = "e=stop here" if is_last else "e=stop here"
            cmd = _prompt(f"  {'Enter=finish' if is_last else 'Enter=next season'}, "
                          f"s=standings, b=bracket (game by game, box scores), "
                          f"{next_label}: ").strip().lower()
            if cmd == "s":
                print_standings_by_conference(standings, teams, highlight=my_team)
            elif cmd == "b":
                print_playoffs(playoff_result, abbrev, highlight=my_team)
                viewed_playoffs = True
            elif cmd == "e":
                _print_dynasty_summary(history, my_team)
                return
            elif cmd == "" and not viewed_playoffs:
                print("  See the playoffs ('b') before moving on -- you haven't watched how this season ended yet.")
            elif cmd == "":
                break
            else:
                print("  Please enter 's', 'b', 'e', or press Enter once you've seen the playoffs.")

        if not is_last:
            # Follow the franchise across a rename into the next season.
            next_season = run[index + 1]
            # THIS run's own simulated PPG for the season just played --
            # per the user, a departing/traded player's "notable"-ness
            # should be judged by what happened in the sim, not his real
            # career averages. Read from `conn` BEFORE the next loop
            # iteration's simulate_season(fresh=True) wipes it -- this
            # is the one and only point where it's both available and
            # still accurate for `season`.
            diff = diff_seasons(season, next_season, sim_ppg_a=db.get_simulated_player_ppg(conn, season))
            next_team = franchise_map(season, next_season).get(my_team, my_team)
            _offseason_report(diff, my_team, next_team,
                              team_names=sorted(load_teams(next_season)))
            my_team = next_team

    _print_dynasty_summary(history, my_team)


def _playoff_finish(playoff_result: dict, team_name: str) -> str:
    """How far the followed team got, in plain words -- read off the
    playoff tree rather than tracked separately, so it can't disagree
    with what the bracket shows."""
    if playoff_result["finals"]["winner"] == team_name:
        return "WON THE TITLE"
    if team_name in (playoff_result["finals"]["winner"], playoff_result["finals"]["loser"]):
        return "lost the Finals"
    for conf in ("east", "west"):
        tree = playoff_result[conf]["tree"]
        if tree["round3"]["loser"] == team_name:
            return "lost the Conference Finals"
        if any(s["loser"] == team_name for s in tree["round2"]):
            return "lost in the Conference Semifinals"
        if any(s["loser"] == team_name for s in tree["round1"]):
            return "lost in the First Round"
    return "missed the playoffs"


def _print_dynasty_summary(history: List[dict], team_name: str) -> None:
    """One line per season -- the whole run at a glance, which is the
    reason for playing several seasons in the first place."""
    print()
    print(DIVIDER)
    print(_style("YOUR RUN".center(LINE_WIDTH), "bold", "cyan"))
    print(DIVIDER)
    # RESULT is 34 wide, not 30: the longest real value is "lost in the
    # Conference Semifinals" at 32 characters, which ran straight into
    # the CHAMPION column at 30. Sized to the longest real string, not
    # eyeballed.
    print(f"{'SEASON':<10}{'TEAM':<26}{'W-L':>7}  {'RESULT':<34}{'CHAMPION'}")
    print(SECTION)
    titles = 0
    for row in history:
        if row["finish"] == "WON THE TITLE":
            titles += 1
        line = (f"{row['season']:<10}{row['team']:<26}"
                f"{row['W']:>3}-{row['L']:<3}  {row['finish']:<34}{row['champion']}")
        print(_style(line, "bold", "cyan") if row["finish"] == "WON THE TITLE" else line)
    print(SECTION)
    wins = sum(r["W"] for r in history)
    losses = sum(r["L"] for r in history)
    print(f"{len(history)} seasons, {wins}-{losses} overall, "
          f"{titles} championship{'s' if titles != 1 else ''}.")
    print()


def _load_season(season: str) -> tuple:
    """
    Everything option 1/2 need for one season: its real teams, the
    league-wide baselines built from them, and the alphabetical team
    list the menus print. Its own function so switching seasons (see
    main's loop) is one call instead of four repeated lines.
    """
    print_season_note(season)
    teams = load_teams(season)
    team_names = sorted(teams.keys())
    # See season.py -- the season's own real pace swing, not a constant.
    league_avg = compute_league_averages(teams, load_league_pace_variation(season))
    return teams, team_names, league_avg


def _pick_game_sim_side(seasons: List[str], label: str) -> Optional[Tuple[str, str, Dict[str, Team], LeagueAverages]]:
    """
    One side of a Game Sim matchup: pick a season, then a team from it
    (with the same best-player column as History Sim's team-select --
    it's useful here for the exact same reason). Returns (season,
    team_name, teams, league_avg), or None if the user backed out of
    the team pick.
    """
    season = select_season(seasons) if len(seasons) > 1 else seasons[0]
    print(f"-> {season}\n")
    teams, team_names, league_avg = _load_season(season)
    print_team_list_with_best_player(teams, team_names)
    team_name = select_team_number(team_names, f"Select the {label} team:")
    if team_name is None:
        return None
    print(f"-> {team_name}\n")
    return season, team_name, teams, league_avg


def _run_game_sim(seasons: List[str]) -> None:
    """
    Game Sim: one exhibition game, no season structure -- and, per the
    user, possibly CROSS-ERA: the opponent can come from a different
    real season than yours (a 1996-97 team against a 2025-26 one). See
    _build_matchup for what that actually takes to do correctly.

    Loops on "new matchup" (run_single_game_flow returning "new")
    rather than ending after one game -- picking teams and seasons
    again from scratch, same as "retry" resimulates the SAME matchup
    without leaving run_single_game_flow at all.
    """
    while True:
        side_a = _pick_game_sim_side(seasons, "YOUR")
        if side_a is None:
            return
        season_a, name_a, teams_a, avg_a = side_a

        cross_era = _prompt(
            "Pick the opponent from the SAME season, or a DIFFERENT one? (s/d): "
        ).strip().lower() == "d"

        if cross_era:
            side_b = _pick_game_sim_side(seasons, "OPPONENT")
            if side_b is None:
                return
            season_b, name_b, teams_b, avg_b = side_b
        else:
            season_b, teams_b, avg_b = season_a, teams_a, avg_a
            print_team_list_with_best_player(teams_a, sorted(teams_a))
            name_b = select_team_number(sorted(teams_a), "Select the OPPONENT team:", exclude_name=name_a)
            if name_b is None:
                return
            print(f"-> {name_b}\n")

        team_a, team_b, league_avg = _build_matchup(
            teams_a[name_a], avg_a, season_a,
            teams_b[name_b], avg_b, season_b,
        )
        if run_single_game_flow(team_a, team_b, league_avg) != "new":
            return


def _run_history_sim(seasons: List[str], abbrev: Dict[str, str]) -> None:
    """History Sim: pick a season or a range (screen 3), then go
    straight into playing it -- a single season gets the richer
    single-season flow (per-team browsing screens throughout); a range
    gets the year-by-year multi-season flow. No menu in between; the
    range picked here is the only choice this mode asks up front."""
    run = select_season_range(seasons)
    if len(run) == 1:
        teams, team_names, league_avg = _load_season(run[0])
        run_season_flow(teams, team_names, league_avg, abbrev, run[0])
    else:
        run_multi_season_flow(abbrev, run)


def main() -> None:
    print_title()

    seasons = available_seasons()
    if not seasons:
        print("No season data is cached yet. Run `python data_source.py` first.")
        return
    # Real 3-letter team codes, only used for the compact playoff
    # bracket diagram -- doesn't depend on season, loaded once.
    abbrev = load_team_abbreviations()

    # Each mode is its own self-contained screen; when it's done,
    # control comes back HERE (the mode menu) rather than into some
    # bigger persistent "what would you like to do" dashboard -- that
    # reused menu was a wall of text read on every single loop, and is
    # gone now that Game Sim (a single game) is its own top-level mode
    # rather than an option buried inside History Sim's old menu.
    while True:
        mode = select_game_mode()
        if mode is None:
            print("Thanks for playing!")
            return
        if mode == "Game Sim":
            _run_game_sim(seasons)
        elif mode == "History Sim":
            _run_history_sim(seasons, abbrev)
        else:
            assert False, f"no dispatch for {mode}"


if __name__ == "__main__":
    main()
