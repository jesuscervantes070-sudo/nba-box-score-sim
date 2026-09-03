"""
Turns the cached rosters.json (produced by data_source.py) into Team/Player
objects the rest of the sim will use. This is the only file that should
ever touch the JSON directly -- everything else works with Team/Player.
"""
import json
from pathlib import Path
from typing import Dict

from models import Player, Team

CACHE_FILE = Path(__file__).parent / "cache" / "rosters.json"


def load_teams() -> Dict[str, Team]:
    if not CACHE_FILE.exists():
        raise FileNotFoundError(
            "No cached data found. Run `python data_source.py` first to fetch rosters/stats."
        )

    with open(CACHE_FILE) as f:
        raw = json.load(f)

    teams: Dict[str, Team] = {}
    for team_name, players in raw["teams"].items():
        team = Team(name=team_name)
        team.players = [Player.from_dict(p) for p in players]
        teams[team_name] = team
    return teams


if __name__ == "__main__":
    teams = load_teams()
    # Quick sanity check: print one team's roster
    first_team = next(iter(teams.values()))
    first_team.print_roster()
