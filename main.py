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
import sys
from typing import Dict, List, Optional

from loader import load_teams
from models import Player, Team
from game_engine import simulate_game, compute_league_averages, GameResult, LeagueAverages
from data_source import fetch_real_standings
from season import simulate_season
import db

# Plain ASCII only for every divider/border in this file, on purpose --
# no fancy unicode box-drawing characters, just characters already on a
# standard keyboard, so the output looks right in any terminal.
LINE_WIDTH = 96
DIVIDER = "=" * LINE_WIDTH
SECTION = "-" * LINE_WIDTH

# A plain-text marker (no color codes -- same "keyboard symbols only"
# rule as everything else in this file) appended to a followed team's
# row wherever standings are printed.
YOUR_TEAM_MARKER = "  <-- YOUR TEAM"


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


# =====================================================================
# TEAM SELECTION
# =====================================================================

def print_welcome() -> None:
    print(DIVIDER)
    print("NBA BOX SCORE SIM".center(LINE_WIDTH))
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

        again = _prompt("Play again? (y/n): ").strip().lower()
        print()
        if again != "y":
            return


# =====================================================================
# STANDINGS DISPLAY
# =====================================================================

def _standings_row(rank: int, row: dict, highlight: str) -> str:
    marker = YOUR_TEAM_MARKER if row["team"] == highlight else ""
    return f"{rank:>3}. {row['team']:<28}{row['W']:>5}{row['L']:>5}{marker}"


def print_standings(standings: List[dict], highlight: str = None) -> None:
    print()
    print(DIVIDER)
    print("STANDINGS".center(LINE_WIDTH))
    print(DIVIDER)
    print(f"{'#':>3}  {'TEAM':<28}{'W':>5}{'L':>5}")
    print(SECTION)
    for i, row in enumerate(standings, start=1):
        print(_standings_row(i, row, highlight))
    print()


def print_standings_by_conference(standings: List[dict], teams: Dict[str, Team], highlight: str = None) -> None:
    print()
    print(DIVIDER)
    print("STANDINGS BY CONFERENCE".center(LINE_WIDTH))
    print(DIVIDER)
    for conference in ("East", "West"):
        conf_rows = [row for row in standings if teams[row["team"]].conference == conference]
        print()
        print(f"-- {conference} --")
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
    print("SIMULATED VS. REAL STANDINGS".center(LINE_WIDTH))
    print(DIVIDER)
    print(f"{'TEAM':<28}{'REAL W':>8}{'SIM W':>8}{'DIFF':>7}")
    print(SECTION)

    rows = sorted(standings, key=lambda r: -real_standings.get(r["team"], 0))
    diffs = []
    for row in rows:
        real_w = real_standings.get(row["team"])
        sim_w = row["W"]
        marker = YOUR_TEAM_MARKER if row["team"] == highlight else ""
        if real_w is None:
            print(f"{row['team']:<28}{'?':>8}{sim_w:>8}{marker}")
            continue
        diff = sim_w - real_w
        diffs.append(abs(diff))
        print(f"{row['team']:<28}{real_w:>8}{sim_w:>8}{diff:>+7}{marker}")

    print(SECTION)
    if diffs:
        print(f"Mean absolute error: {sum(diffs) / len(diffs):.1f} games across {len(diffs)} teams")
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

def run_season_flow(teams: Dict[str, Team], team_names: List[str], season: str = "2025-26") -> None:
    """
    Simulates the full real season (overwriting any previously
    simulated one -- see season.py's simulate_season for why re-
    running isn't additive), then shows standings (overall or by
    conference), the real-vs-simulated comparison, and the followed
    team's simulated season averages -- all shown automatically, no
    "do you want to see this? (y/n)" gates in front of them (removed
    per feedback: those gates were in front of exactly the numbers
    this whole project exists to produce, not optional side content).
    Afterward, offers a look at any OTHER team's season averages too.
    """
    print()
    confirm = _prompt("Simulate the full season now? (y/n): ").strip().lower()
    if confirm != "y":
        return

    print()
    print_team_list(team_names)
    my_team_name = select_team_number(team_names, "Select YOUR team (highlighted in standings below):")
    if my_team_name is None:
        return
    print(f"-> {my_team_name}\n")

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
            run_season_flow(teams, team_names)
        elif choice == "3":
            print("Thanks for playing!")
            break
        else:
            print("Please enter 1, 2, or 3.")
        print()


if __name__ == "__main__":
    main()
