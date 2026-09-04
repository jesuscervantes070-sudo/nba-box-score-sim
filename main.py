"""
The playable, text-based front end for the sim. Run this file directly:

    python3 main.py

Flow: welcome -> pick your team -> pick an opponent -> simulate one game
-> print the full box score -> play again? -> loop until the user quits.

This file only prints/reads text -- all the actual simulation logic
lives in game_engine.py, and loading real team data lives in loader.py.
Keeping this file "dumb" (just I/O) means the simulation itself stays
fully testable on its own, without needing a keyboard in the loop.
"""
from typing import Dict, List

from loader import load_teams
from models import Player, Team
from game_engine import simulate_game, GameResult

# Plain ASCII only for every divider/border in this file, on purpose --
# no fancy unicode box-drawing characters, just characters already on a
# standard keyboard, so the output looks right in any terminal.
LINE_WIDTH = 96
DIVIDER = "=" * LINE_WIDTH
SECTION = "-" * LINE_WIDTH


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


def select_team_number(team_names: List[str], prompt_label: str, exclude_name: str = None) -> Team:
    """
    Read the user's choice by number, against the list already printed
    by print_team_list. Loops until it gets a valid number, and (if
    `exclude_name` is set -- used for picking the OPPONENT) refuses to
    let the user pick the same team twice.
    """
    print(prompt_label)
    while True:
        choice = input("> ").strip()

        if not choice.isdigit():
            print("Please enter a number from the list above.")
            continue

        index = int(choice)
        if index < 1 or index > len(team_names):
            print(f"Please enter a number between 1 and {len(team_names)}.")
            continue

        chosen_name = team_names[index - 1]
        if chosen_name == exclude_name:
            print("That's already your team -- pick a different opponent.")
            continue

        return chosen_name


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
COLUMNS = [
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


def _header_row() -> str:
    return "  ".join(f"{label:{align}{width}}" for label, align, width in COLUMNS)


def _pct_str(pct: float) -> str:
    """0.417 -> ' 41.7%' -- an actual percentage, not a raw decimal."""
    return f"{pct * 100:.1f}%"


def _format_player_row(p: Player) -> str:
    """One formatted box-score line for a single player (or the TOTAL
    row, since that's also just a Player -- see _team_totals_row)."""
    if p.min == 0:
        # A real box score doesn't print stats for someone who didn't
        # play -- it just marks them DNP. Uses COLUMNS[0]'s width
        # directly (rather than a second hardcoded number) so this can
        # never quietly drift out of alignment with the header again.
        name_width = COLUMNS[0][2]
        return f"{p.name:<{name_width}}  DNP"

    values = [
        p.name, f"{p.min:.0f}", f"{p.pts:.0f}",
        f"{p.reb:.0f}", f"{p.oreb:.0f}", f"{p.ast:.0f}",
        f"{p.stl:.0f}", f"{p.blk:.0f}", f"{p.tov:.0f}", f"{p.pf:.0f}",
        f"{p.fgm:.0f}-{p.fga:.0f}", _pct_str(p.fg_pct),
        f"{p.fg3m:.0f}-{p.fg3a:.0f}", _pct_str(p.fg3_pct),
        f"{p.ftm:.0f}-{p.fta:.0f}", _pct_str(p.ft_pct),
    ]
    return "  ".join(f"{v:{align}{width}}" for v, (_, align, width) in zip(values, COLUMNS))


def _print_team_box_score(team_name: str, players, score: float) -> None:
    print(f"{team_name} ({score:.0f})")
    print(SECTION)
    print(_header_row())
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
    input("Press Enter to see the other team's box score...")
    print()
    _print_team_box_score(result.away_team, result.away_players, result.away_score)


def main() -> None:
    print_welcome()
    teams = load_teams()
    team_names = sorted(teams.keys())  # alphabetical, so it's easy to scan

    while True:
        # Printed at the start of every round (including replays) --
        # by the time a box score has scrolled by, the list is long
        # gone off-screen, so it needs to come back for the next pick.
        print_team_list(team_names)

        my_team_name = select_team_number(team_names, "Select YOUR team:")
        print(f"-> {my_team_name}\n")

        opponent_team_name = select_team_number(team_names, "Select the OPPONENT team:", exclude_name=my_team_name)
        print(f"-> {opponent_team_name}\n")

        print(f"Simulating: {my_team_name} vs. {opponent_team_name} ...")
        result = simulate_game(teams[my_team_name], teams[opponent_team_name])
        print_box_score(result)

        again = input("Play again? (y/n): ").strip().lower()
        print()
        if again != "y":
            print("Thanks for playing!")
            break


if __name__ == "__main__":
    main()
