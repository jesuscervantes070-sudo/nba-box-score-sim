"""
Pulls real NBA team rosters and real per-game player stats from the NBA's
own stats endpoints (via the community-maintained `nba_api` package -- same
underlying data source sites like basketball-reference mirror) and caches
the result locally as JSON, so the rest of the project can just read a file
instead of hitting the network every time.

Both the roster AND the stats come from the SAME season (2025-26, the last
full season) on purpose -- every player on a 2025-26 roster necessarily
played that season, so every single player is guaranteed to have a real
stat line. No rookies-with-no-stats edge case to handle here at all.

(Later, once the next NBA season is underway and rookies start building up
real per-game numbers of their own, this can be pointed at that season
instead -- but that's a future update, not something to build now.)

Install once:
    pip install nba_api

Usage:
    python data_source.py                    # fetch + cache (skips if cache exists)
    python data_source.py --refresh          # force re-fetch, overwrite cache
    python data_source.py --season 2025-26   # which season to pull (default 2025-26)
"""
import argparse
import json
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
ROSTER_CACHE = CACHE_DIR / "rosters.json"

# Maps OUR field names (matching models.py's Player fields) -> the NBA
# stats API's column names. Keeping this in one place means if the API
# ever renames a column, there's exactly one line to fix.
FIELD_MAP = {
    "min": "MIN", "fgm": "FGM", "fga": "FGA", "fg3m": "FG3M", "fg3a": "FG3A",
    "ftm": "FTM", "fta": "FTA", "reb": "REB", "oreb": "OREB",
    "ast": "AST", "stl": "STL", "blk": "BLK", "tov": "TOV", "pf": "PF",
}


def fetch_player_season_stats(season: str):
    """Real per-game averages for every player who played in `season`."""
    from nba_api.stats.endpoints import leaguedashplayerstats
    stats = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season, season_type_all_star="Regular Season", per_mode_detailed="PerGame"
    )
    return stats.get_data_frames()[0]


def fetch_team_rosters(season: str):
    """Every team's roster AS OF `season` -- passing a past season here
    returns that season's historical roster (trades/departures included),
    not whatever the team's roster happens to be today."""
    from nba_api.stats.static import teams as static_teams
    from nba_api.stats.endpoints import commonteamroster

    rosters = {}
    for t in static_teams.get_teams():
        roster_df = commonteamroster.CommonTeamRoster(team_id=t["id"], season=season).get_data_frames()[0]
        rosters[t["full_name"]] = roster_df["PLAYER"].tolist()
        time.sleep(0.6)  # stay polite to the API -- don't hammer it
    return rosters


def build_and_cache(season: str = "2025-26", force: bool = False) -> None:
    if ROSTER_CACHE.exists() and not force:
        print(f"Cache already exists at {ROSTER_CACHE}. Use --refresh to force an update.")
        return

    print(f"Fetching {season} player season stats...")
    stats_df = fetch_player_season_stats(season)
    if stats_df.empty:
        raise RuntimeError(f"No regular-season stats found for {season} -- check the season string.")

    print(f"Fetching {season} team rosters (this loops all 30 teams, ~20-30s)...")
    rosters = fetch_team_rosters(season)

    teams_data = {}
    missing = []  # players on the roster with no stat line at all (e.g. injured before ever playing)
    for team_name, player_names in rosters.items():
        team_players = []
        for pname in player_names:
            row = stats_df[stats_df["PLAYER_NAME"] == pname]
            if row.empty:
                missing.append(pname)
                continue
            r = row.iloc[0]
            entry = {"name": pname, "team": team_name}
            for our_key, api_key in FIELD_MAP.items():
                entry[our_key] = float(r[api_key])
            team_players.append(entry)
        teams_data[team_name] = team_players

    with open(ROSTER_CACHE, "w") as f:
        json.dump({"season": season, "teams": teams_data}, f, indent=2)

    print(f"Cached {len(teams_data)} teams -> {ROSTER_CACHE}")
    if missing:
        print(f"({len(missing)} players had no {season} stat line at all, skipped: "
              f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="force re-fetch even if cache exists")
    parser.add_argument("--season", default="2025-26", help="season to pull roster + stats from, e.g. 2025-26")
    args = parser.parse_args()
    build_and_cache(season=args.season, force=args.refresh)
