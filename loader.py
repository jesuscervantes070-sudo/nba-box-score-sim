"""
Turns the raw JSON data that data_source.py cached (cache/rosters.json,
cache/schedule.json) into actual objects the rest of the project can
use.

This is the ONLY file that should ever open those cache files directly
-- everywhere else in the project should just work with real Player/
Team/ScheduledGame objects, never raw dicts pulled straight from JSON.
"""
import json
from pathlib import Path
from typing import Dict, List

from models import Player, Team, ScheduledGame

CACHE_DIR = Path(__file__).parent / "cache"
ROSTER_CACHE_FILE = CACHE_DIR / "rosters.json"
SCHEDULE_CACHE_FILE = CACHE_DIR / "schedule.json"
TEAM_DEFENSE_CACHE_FILE = CACHE_DIR / "team_defense.json"


def load_teams() -> Dict[str, Team]:
    """
    Reads cache/rosters.json AND cache/team_defense.json and returns a
    dict mapping each team's name to a real Team object -- real Player
    objects for its roster, plus its own real defensive stats. Both
    files get merged here so every caller gets one fully-populated
    Team object, rather than every caller having to remember to load
    and merge defense data separately.

    Returns a dict (not a list) so calling code can look up a team
    directly by name, e.g. teams["Los Angeles Lakers"], instead of
    looping through a list every time to find the one it wants.
    """
    if not ROSTER_CACHE_FILE.exists():
        # Fail loudly with a clear next step, instead of a confusing
        # crash somewhere later when the code tries to use data that
        # was never there.
        raise FileNotFoundError(
            "No cached data found at cache/rosters.json. "
            "Run `python data_source.py` first to fetch rosters/stats."
        )
    if not TEAM_DEFENSE_CACHE_FILE.exists():
        raise FileNotFoundError(
            "No cached team defense data found at cache/team_defense.json. "
            "Run `python data_source.py` first to fetch it."
        )

    with open(ROSTER_CACHE_FILE) as f:
        raw = json.load(f)  # raw is now a plain Python dict -- not Player/Team objects yet
    with open(TEAM_DEFENSE_CACHE_FILE) as f:
        raw_defense = json.load(f)["teams"]

    teams: Dict[str, Team] = {}
    for team_name, player_dicts in raw["teams"].items():
        # Build the roster for this one team: turn every raw player dict
        # into a real Player object. The ** below unpacks a dict into
        # keyword arguments -- e.g. {"name": "X", "min": 30} becomes
        # Player(name="X", min=30), instead of us typing every field by
        # hand. This works because data_source.py's FIELD_MAP already
        # guarantees the JSON keys match Player's actual field names.
        players = [Player(**player_dict) for player_dict in player_dicts]
        # Same unpacking trick for the team's own real defensive stats
        # -- data_source.py's DEFENSE_FIELD_MAP guarantees these JSON
        # keys already match Team's opp_* field names.
        defense = raw_defense.get(team_name, {})
        teams[team_name] = Team(name=team_name, players=players, **defense)

    return teams


def load_schedule() -> List[ScheduledGame]:
    """
    Reads cache/schedule.json and returns the real regular-season
    schedule as a list of ScheduledGame objects, in the same
    chronological order data_source.py already sorted them into.
    """
    if not SCHEDULE_CACHE_FILE.exists():
        raise FileNotFoundError(
            "No cached schedule found at cache/schedule.json. "
            "Run `python data_source.py` first to fetch it."
        )

    with open(SCHEDULE_CACHE_FILE) as f:
        raw = json.load(f)

    return [ScheduledGame(**game_dict) for game_dict in raw["games"]]


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

    schedule = load_schedule()
    print(f"\nLoaded {len(schedule)} scheduled games.")
    print("First game:", schedule[0])
    print("Last game:", schedule[-1])
