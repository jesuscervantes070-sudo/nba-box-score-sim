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
from typing import Dict, List, Optional

from loader import load_teams, load_team_abbreviations
from models import Player, Team
from game_engine import simulate_game, compute_league_averages, GameResult, LeagueAverages
from data_source import fetch_real_standings
from season import simulate_season
from playoffs import run_playoffs, compute_series_player_averages
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


def _print_team_box_score(team_name: str, players, score: float) -> None:
    print(f"{team_name} ({score:.0f})")
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


def print_box_score(result: GameResult) -> None:
    print()
    print(DIVIDER)
    final_line = f"FINAL: {result.home_team} {result.home_score:.0f} - {result.away_score:.0f} {result.away_team}"
    print(final_line.center(LINE_WIDTH))
    print(DIVIDER)
    print()
    _print_team_box_score(result.home_team, result.home_players, result.home_score)
    # A terminal always jumps to show whatever was JUST printed -- there's
    # no way for a plain print()-based script to keep it scrolled to the
    # top instead. Pausing here breaks one huge wall of text into two
    # smaller, readable chunks, so the jump after each one is much less
    # jarring, and there's time to actually read the first team's box
    # score before the second one pushes it further up.
    _prompt("Press Enter to see the other team's box score...")
    print()
    _print_team_box_score(result.away_team, result.away_players, result.away_score)


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


def _bracket_place_text(grid: dict, row: int, col: int, text: str) -> None:
    for i, ch in enumerate(text):
        grid.setdefault(row, {})[col + i] = ch


def _bracket_draw_connector(grid: dict, top_row: int, bot_row: int, conn_col: int, label_col: int, label: str) -> int:
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
    _bracket_place_text(grid, mid_row, label_col, label)
    return mid_row


def _render_conference_tree(tree: dict, abbrev: Dict[str, str], highlight: Optional[str] = None) -> List[str]:
    """
    Draws the actual bracket shape (seed 1-8, not the play-in) as ASCII
    art: leaves -> Round 1 winners -> Semifinal winners -> conference
    champion, connected the way a real bracket sheet is. Plain text
    only, no ANSI color -- the layout below is column math built on
    every label being exactly _BRACKET_LEAF_WIDTH wide, and splicing
    invisible escape bytes into a label would make it silently overrun
    into its neighboring column instead of raising an error, corrupting
    the diagram. The followed team is still marked, just with a plain
    "*" in a reserved column instead -- an ordinary visible character
    is exactly as safe as any other in a fixed grid. Color stays on
    the plain-text detail lines below this diagram.

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
    leaf_rows = [i * 2 for i in range(8)]
    for row, (seed, team) in zip(leaf_rows, tree["leaves"]):
        _bracket_place_text(grid, row, LEAF_COL, seed_label(seed, team))

    round2_rows = []
    for i, result in enumerate(tree["round1"]):
        top, bot = leaf_rows[2 * i], leaf_rows[2 * i + 1]
        label = seed_label(result["winner_seed"], result["winner"])
        round2_rows.append(_bracket_draw_connector(grid, top, bot, conn1_col, r2_label_col, label))

    round3_rows = []
    for i, result in enumerate(tree["round2"]):
        top, bot = round2_rows[2 * i], round2_rows[2 * i + 1]
        label = seed_label(result["winner_seed"], result["winner"])
        round3_rows.append(_bracket_draw_connector(grid, top, bot, conn2_col, r3_label_col, label))

    champion = tree["round3"]["winner"]
    champ_label = champion + (f" {_BRACKET_MARKER}" if champion == highlight else "")
    _bracket_draw_connector(grid, round3_rows[0], round3_rows[1], conn3_col, champ_col, champ_label)

    max_row = max(grid.keys())
    max_col = max(col for row in grid.values() for col in row)
    return ["".join(grid.get(row, {}).get(col, " ") for col in range(max_col + 1)).rstrip()
            for row in range(max_row + 1)]


def _print_conference_bracket(conf_result: dict, abbrev: Dict[str, str], highlight: Optional[str] = None) -> None:
    """One conference's play-in log + bracket diagram + every round's
    detail + its champion line -- split out of print_playoffs so it
    can pause BETWEEN conferences (see print_playoffs's docstring)."""
    print()
    print(_style(f"-- {conf_result['conference']} Play-In Tournament --", "bold"))
    for line in conf_result["play_in_log"]:
        print(_highlight_team(line, highlight))

    print()
    for line in _render_conference_tree(conf_result["tree"], abbrev, highlight):
        print(line)

    for round_lines in conf_result["round_logs"]:
        print()
        header, *series_lines = round_lines
        print(_style(header, "bold"))
        for line in series_lines:
            print(_format_playoff_line(line, highlight))

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
    print(_format_playoff_line(
        f"  {finals['winner']} def. {finals['loser']}, "
        f"{finals['wins'][finals['winner']]}-{finals['wins'][finals['loser']]}",
        highlight,
    ))
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

def print_team_season_averages(conn, team: Team, season: str) -> None:
    """
    For one team's roster: real per-game averages next to simulated
    season averages (from the games just simulated and stored) -- the
    original point of this whole project, finally visible in the game
    itself rather than only in a test script.
    """
    print()
    print(DIVIDER)
    print(f"{team.name.upper()} -- REAL VS. SIMULATED SEASON AVERAGES".center(LINE_WIDTH))
    print(DIVIDER)
    print(f"{'PLAYER':<25}{'GP':>4}  {'PTS':>13}  {'REB':>13}  {'AST':>13}  {'FG%':>13}")
    print(f"{'':<25}{'':>4}  {'real':>6}{'sim':>7}  {'real':>6}{'sim':>7}  {'real':>6}{'sim':>7}  {'real':>6}{'sim':>7}")
    print(SECTION)

    for player in sorted(team.players, key=lambda p: -p.pts):
        avg = db.get_player_season_averages(conn, player.name, season)
        if not avg:
            print(f"{player.name:<25}{'--':>4}  (no simulated games played)")
            continue
        print(
            f"{player.name:<25}{avg['games_played']:>4}  "
            f"{player.pts:>6.1f}{avg['pts']:>7.1f}  "
            f"{player.reb:>6.1f}{avg['reb']:>7.1f}  "
            f"{player.ast:>6.1f}{avg['ast']:>7.1f}  "
            f"{player.fg_pct * 100:>5.1f}%{avg['fg_pct'] * 100:>6.1f}%"
        )
    print()


# =====================================================================
# SEASON FLOW
# =====================================================================

def run_season_flow(teams: Dict[str, Team], team_names: List[str], league_avg: LeagueAverages,
                     abbrev: Dict[str, str], season: str = "2025-26") -> None:
    """
    Picks the followed team FIRST (so it's known before anything else
    runs, and can be highlighted everywhere below -- standings, the
    comparison, and now the playoffs too), then simulates the full
    real season (overwriting any previously simulated one -- see
    season.py's simulate_season for why re-running isn't additive),
    then shows standings (overall or by conference), the real-vs-
    simulated comparison, and the followed team's simulated season
    averages -- all shown automatically, no "do you want to see this?
    (y/n)" gates in front of them (removed per feedback: those gates
    were in front of exactly the numbers this whole project exists to
    produce, not optional side content). Afterward, offers a look at
    any OTHER team's season averages too.
    """
    print()
    print_team_list(team_names)
    my_team_name = select_team_number(team_names, "Select YOUR team (highlighted throughout):")
    if my_team_name is None:
        return
    print(f"-> {my_team_name}\n")

    if not _confirm("Simulate the full season now?"):
        return

    simulate_season(season=season, fresh=True)

    conn = db.init_db()
    standings = db.get_standings(conn, season)

    view = _prompt("View standings by conference, or overall? (c/o): ").strip().lower()
    if view == "c":
        print_standings_by_conference(standings, teams, highlight=my_team_name)
    else:
        print_standings(standings, highlight=my_team_name)

    real_standings = fetch_real_standings(season)
    print_standings_comparison(standings, real_standings, highlight=my_team_name)

    if _confirm("Simulate the playoffs too?"):
        playoff_result = run_playoffs(teams, standings, league_avg)
        print_playoffs(playoff_result, abbrev, highlight=my_team_name)

    print_team_season_averages(conn, teams[my_team_name], season)

    _run_season_averages_browser(conn, teams, team_names, season)


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

def main() -> None:
    print_welcome()
    teams = load_teams()
    team_names = sorted(teams.keys())  # alphabetical, so it's easy to scan

    # Real, league-wide baselines (what's an average defense, an
    # average steal/block rate) -- computed ONCE here, not per game.
    league_avg = compute_league_averages(teams)
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
            run_season_flow(teams, team_names, league_avg, abbrev)
        elif choice == "3":
            print("Thanks for playing!")
            break
        else:
            print("Please enter 1, 2, or 3.")
        print()


if __name__ == "__main__":
    main()
