"""
Turns the raw JSON data that data_source.py cached (cache/rosters.json)
into actual Player/Team objects the rest of the project can use.

This is the ONLY file that should ever open rosters.json directly --
everywhere else in the project should just work with real Player/Team
objects, never raw dicts pulled straight from the JSON file.
"""
import json
from pathlib import Path
from typing import Dict

from models import Player, Team

CACHE_FILE = Path(__file__).parent / "cache" / "rosters.json"


def load_teams() -> Dict[str, Team]:
    """
    Reads cache/rosters.json and returns a dict mapping each team's name
    to a real Team object (which holds real Player objects).

    Returns a dict (not a list) so calling code can look up a team
    directly by name, e.g. teams["Los Angeles Lakers"], instead of
    looping through a list every time to find the one it wants.
    """
    if not CACHE_FILE.exists():
        # Fail loudly with a clear next step, instead of a confusing
        # crash somewhere later when the code tries to use data that
        # was never there.
        raise FileNotFoundError(
            "No cached data found at cache/rosters.json. "
            "Run `python data_source.py` first to fetch rosters/stats."
        )

    with open(CACHE_FILE) as f:
        raw = json.load(f)  # raw is now a plain Python dict -- not Player/Team objects yet

    teams: Dict[str, Team] = {}
    for team_name, player_dicts in raw["teams"].items():
        # Build the roster for this one team: turn every raw player dict
        # into a real Player object. The ** below unpacks a dict into
        # keyword arguments -- e.g. {"name": "X", "min": 30} becomes
        # Player(name="X", min=30), instead of us typing every field by
        # hand. This works because data_source.py's FIELD_MAP already
        # guarantees the JSON keys match Player's actual field names.
        players = [Player(**player_dict) for player_dict in player_dicts]
        teams[team_name] = Team(name=team_name, players=players)

    return teams


if __name__ == "__main__":
    # Quick manual sanity check when running this file directly:
    # load everything, then print one team's roster so a human can eyeball
    # that the numbers look like a real box score.
    teams = load_teams()
    print(f"Loaded {len(teams)} teams.")

    first_team = next(iter(teams.values()))
    print(f"\n-- {first_team.name} --")
    for p in sorted(first_team.players, key=lambda player: -player.pts):
        print(f"{p.name:<22} MIN {p.min:>4.1f}  PTS {p.pts:>5.1f}  "
              f"REB {p.reb:>4.1f} (OREB {p.oreb:.1f})  AST {p.ast:>4.1f}  "
              f"FG% {p.fg_pct:.3f}")
