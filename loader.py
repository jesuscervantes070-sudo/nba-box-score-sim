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

# Default season every load function falls back to when the caller
# doesn't specify one -- keeps every existing call site (main.py,
# etc.) working unchanged while the season-picker UI for backtesting
# older seasons gets built out. Bump this when the next real season's
# cache becomes the default one to play.
DEFAULT_SEASON = "2025-26"


def _season_cache_dir(season: str) -> Path:
    """Same layout data_source.py caches into: cache/<season>/ -- see
    data_source._season_cache_dir. Doesn't create the directory (unlike
    that one) -- a MISSING season here should fail loudly (see the
    FileNotFoundErrors below), not silently create an empty folder."""
    return CACHE_DIR / season


def load_league_pace_variation(season: str = DEFAULT_SEASON):
    """
    This season's real game-to-game pace swing (see
    data_source.fetch_league_pace_variation) -- e.g. 0.052 for 2023-24.

    Returns None when the season has no cached file, which leaves
    game_engine on its GAME_PACE_VARIATION default rather than failing:
    this was added after every season was already cached, same as the
    consistency file. Optional here, never optional in meaning -- the
    real number genuinely differs by era (6.8% in the 1990s, 5.3% now).
    """
    pace_file = _season_cache_dir(season) / "league_pace.json"
    if not pace_file.exists():
        return None
    with open(pace_file) as f:
        return json.load(f).get("pace_variation")


def load_team_divisions(season: str = DEFAULT_SEASON) -> Dict[str, str]:
    """
    Each team's real division IN THAT SEASON (see
    data_source.fetch_team_divisions) -- four divisions before the
    2004-05 realignment, six after.

    Returns an empty dict when the season has no cached file, which
    leaves playoffs.py on its hardcoded modern map: this was added after
    every season was already cached, same as the consistency and pace
    files.
    """
    division_file = _season_cache_dir(season) / "team_divisions.json"
    if not division_file.exists():
        return {}
    with open(division_file) as f:
        return json.load(f)["teams"]


def available_seasons() -> List[str]:
    """
    Every season this project can actually play, newest first -- found
    by looking for the cache files each one needs rather than from a
    hardcoded list, so fetching a new season makes it playable with no
    code change at all.

    Lives here rather than in main.py because this is the only file that
    touches the cache directory directly (see the module docstring).

    A season missing any of these would crash partway through a game
    instead of at the menu, so the check happens up front.
    player_consistency.json and league_pace.json are deliberately NOT
    required: both were added after every season was already cached, and
    both fall back cleanly on their own (league-average streakiness, and
    game_engine's GAME_PACE_VARIATION).
    """
    required = ("rosters.json", "schedule.json", "team_defense.json",
                "team_conferences.json", "injuries.json", "roster_membership.json")
    if not CACHE_DIR.exists():
        return []
    return [d.name for d in sorted(CACHE_DIR.iterdir(), reverse=True)
            if d.is_dir() and all((d / f).exists() for f in required)]


def _consistency_fields(raw_consistency: dict, player_name: str) -> dict:
    """
    The two scoring-consistency fields for one player, ready to hand to
    Player(**...) -- or an empty dict if this player has no measured
    spread (too few real games, or a season cached before the file
    existed), which leaves Player's own defaults in place.

    Kept as its own function purely so the roster-building line in
    load_teams stays readable.
    """
    entry = raw_consistency.get(player_name)
    if not entry:
        return {}
    return {
        "scoring_spread": entry["spread_rate"],
        "scoring_spread_games": entry["games"],
        # The display half -- see Player.consistency_rating for why the
        # shown rating uses the raw spread and the sim uses the other.
        "scoring_spread_raw": entry["spread_raw"],
    }


def load_teams(season: str = DEFAULT_SEASON) -> Dict[str, Team]:
    """
    Reads cache/<season>/rosters.json, team_defense.json, and
    team_conferences.json, and returns a dict mapping each team's name
    to a real Team object -- real Player objects for its roster, plus
    its own real defensive stats and conference. All three files get
    merged here so every caller gets one fully-populated Team object,
    rather than every caller having to remember to load and merge each
    piece separately.

    Returns a dict (not a list) so calling code can look up a team
    directly by name, e.g. teams["Los Angeles Lakers"], instead of
    looping through a list every time to find the one it wants.
    """
    season_dir = _season_cache_dir(season)
    roster_file = season_dir / "rosters.json"
    defense_file = season_dir / "team_defense.json"
    conference_file = season_dir / "team_conferences.json"

    if not roster_file.exists():
        # Fail loudly with a clear next step, instead of a confusing
        # crash somewhere later when the code tries to use data that
        # was never there.
        raise FileNotFoundError(
            f"No cached data found at {roster_file}. "
            f"Run `python data_source.py --season {season}` first to fetch rosters/stats."
        )
    if not defense_file.exists():
        raise FileNotFoundError(
            f"No cached team defense data found at {defense_file}. "
            f"Run `python data_source.py --season {season}` first to fetch it."
        )
    if not conference_file.exists():
        raise FileNotFoundError(
            f"No cached team conference data found at {conference_file}. "
            f"Run `python data_source.py --season {season}` first to fetch it."
        )

    with open(roster_file) as f:
        raw = json.load(f)  # raw is now a plain Python dict -- not Player/Team objects yet
    with open(defense_file) as f:
        raw_defense = json.load(f)["teams"]
    with open(conference_file) as f:
        raw_conference = json.load(f)["teams"]

    # How streaky each player's real scoring was. Unlike the three files
    # above this one is OPTIONAL: it was added after every season was
    # already cached, so a season fetched before it existed simply has
    # no such file. Missing means "no player has a measured spread,"
    # which the sim already handles by falling back to the league
    # average -- not a reason to refuse to load the season at all.
    raw_consistency = {}
    consistency_file = season_dir / "player_consistency.json"
    if consistency_file.exists():
        with open(consistency_file) as f:
            raw_consistency = json.load(f)["players"]

    teams: Dict[str, Team] = {}
    for team_name, player_dicts in raw["teams"].items():
        # Build the roster for this one team: turn every raw player dict
        # into a real Player object. The ** below unpacks a dict into
        # keyword arguments -- e.g. {"name": "X", "min": 30} becomes
        # Player(name="X", min=30), instead of us typing every field by
        # hand. This works because data_source.py's FIELD_MAP already
        # guarantees the JSON keys match Player's actual field names.
        players = [
            Player(**player_dict, **_consistency_fields(raw_consistency, player_dict["name"]))
            for player_dict in player_dicts
        ]
        # Same unpacking trick for the team's own real defensive stats
        # -- data_source.py's DEFENSE_FIELD_MAP guarantees these JSON
        # keys already match Team's opp_* field names.
        defense = raw_defense.get(team_name, {})
        conference = raw_conference.get(team_name, "")
        # A team with NO players did not exist in this season, and must
        # not be loaded as if it did. The roster endpoint is keyed by
        # team_id and happily returns an empty roster for a franchise
        # that had not been founded yet or had already relocated -- so
        # 2002-03 and 2003-04 both come back with an empty "Charlotte
        # Hornets" (they had moved to New Orleans in 2002-03; the
        # Bobcats only arrive in 2004-05).
        #
        # This is not cosmetic. Every league-wide average in
        # game_engine.compute_league_averages divides a real total by
        # len(teams), so one phantom team made every one of them 3.3%
        # too low for the entire real 29-team era (1995-96 through
        # 2003-04) -- league-average steals, blocks, shot attempts,
        # turnovers and pace all wrong, in the eight backtested seasons
        # that fall in that range. It also crashes the active-roster
        # draw outright if anything asks such a team to field a lineup.
        # Verified against the schedule: the dropped team plays exactly
        # zero real games in those seasons, which is why the sim itself
        # never noticed.
        if not players:
            continue
        teams[team_name] = Team(name=team_name, players=players, conference=conference, **defense)

    return teams


def load_schedule(season: str = DEFAULT_SEASON) -> List[ScheduledGame]:
    """
    Reads cache/<season>/schedule.json and returns the real regular-
    season schedule as a list of ScheduledGame objects, in the same
    chronological order data_source.py already sorted them into.
    """
    schedule_file = _season_cache_dir(season) / "schedule.json"
    if not schedule_file.exists():
        raise FileNotFoundError(
            f"No cached schedule found at {schedule_file}. "
            f"Run `python data_source.py --season {season}` first to fetch it."
        )

    with open(schedule_file) as f:
        raw = json.load(f)

    return [ScheduledGame(**game_dict) for game_dict in raw["games"]]


def load_player_injuries(season: str = DEFAULT_SEASON) -> Dict[str, dict]:
    """
    Reads cache/<season>/injuries.json: each real player's real absence
    pattern this season -- {"games_considered": 82, "stints": [{"start":
    0, "length": 16}, {"start": 40, "length": 1}]} means their team
    played 82 games and they missed two separate stretches: one 16
    games long starting at the literal first game of the season, one a
    single game starting at index 40. See data_source.py's
    fetch_player_absence_stints for exactly what "absence" means here
    and its caveats (injury, rest, a trade, personal reasons all look
    identical in this data).

    Returned as plain dicts, not a dataclass -- this is season-shape
    metadata only injuries.py needs to build a SIMULATED season's injury
    calendar, not a per-game stat that belongs on the Player model.
    """
    injuries_file = _season_cache_dir(season) / "injuries.json"
    if not injuries_file.exists():
        raise FileNotFoundError(
            f"No cached injury data found at {injuries_file}. "
            f"Run `python data_source.py --season {season}` first to fetch it."
        )
    with open(injuries_file) as f:
        raw = json.load(f)
    return raw["players"]


def load_roster_membership(season: str = DEFAULT_SEASON) -> Dict[str, list]:
    """
    Reads cache/<season>/roster_membership.json: for each real team,
    every player who actually suited up for them at any point this
    season, and the first/last game (in that team's own schedule) they
    played for them. See data_source.py's fetch_roster_membership --
    this is what transactions.py uses to make in-season trades real in
    the sim, instead of every team using one static, whole-season
    roster snapshot.
    """
    membership_file = _season_cache_dir(season) / "roster_membership.json"
    if not membership_file.exists():
        raise FileNotFoundError(
            f"No cached roster membership found at {membership_file}. "
            f"Run `python data_source.py --season {season}` first to fetch it."
        )
    with open(membership_file) as f:
        raw = json.load(f)
    return raw["teams"]


def load_team_abbreviations() -> Dict[str, str]:
    """
    Real 3-letter team codes (e.g. "Boston Celtics" -> "BOS"), used only
    for compact displays -- right now, main.py's playoff bracket diagram,
    where a full team name would blow out the width. Pulled straight
    from nba_api's own static team list, which is bundled with the
    package and read entirely offline (no live API call), so unlike
    everything else in this file there's no cache file to read first.
    """
    from nba_api.stats.static import teams as static_teams
    return {t["full_name"]: t["abbreviation"] for t in static_teams.get_teams()}


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
