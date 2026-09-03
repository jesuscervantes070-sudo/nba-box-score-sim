"""
Pulls the CURRENT team rosters and real per-game player stats from the NBA's
own stats endpoints (via the community-maintained `nba_api` package -- same
underlying data source sites like basketball-reference mirror) and caches
the result locally as JSON.

Three different "seasons" are in play here, and they're intentionally NOT
all the same value:
  - roster_season:      what --season means. The season you're building the
                         sim for (e.g. "2026-27"). Rosters are always fetched
                         for THIS season, so trades/free agency/draft picks
                         from the current offseason show up correctly. (The
                         old version of this file forgot to pass season into
                         the roster call at all, so it silently returned
                         nba_api's hardcoded default roster season instead
                         of the current one -- fixed here.)
  - stats_season:        where real per-game stats come from. If roster_season
                         hasn't started yet (true before ~October), there are
                         zero regular-season games to average, so this
                         defaults to the PREVIOUS season automatically.
  - summer_league_season: fallback for anyone on the roster_season roster
                         with no stats_season line at all -- true rookies,
                         a draft-and-stash returning from overseas, etc.
                         Summer League for roster_season "2026-27" is the
                         July 2026 Vegas/Salt Lake Summer League, which
                         nba_api confusingly also labels "2026-27".
                         NOTE: these are raw per-game averages over only
                         1-5 games -- noisy by nature, not shrunk toward any
                         baseline. Fine as a first pass; revisit if a
                         rookie's simulated stats look unrealistic.

Install once:
    pip install nba_api

Usage:
    python data_source.py                    # fetch + cache (skips if cache exists)
    python data_source.py --refresh          # force re-fetch, overwrite cache
    python data_source.py --season 2026-27   # the season to build rosters/sim for
"""
import argparse
import json
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
ROSTER_CACHE = CACHE_DIR / "rosters.json"

# Maps our field names -> the NBA stats API's column names
FIELD_MAP = {
    "min": "MIN", "pts": "PTS", "reb": "REB", "oreb": "OREB", "dreb": "DREB",
    "ast": "AST", "stl": "STL", "blk": "BLK", "tov": "TOV", "pf": "PF",
    "fgm": "FGM", "fga": "FGA", "fg3m": "FG3M", "fg3a": "FG3A",
    "ftm": "FTM", "fta": "FTA",
}


def previous_season(season: str) -> str:
    """'2026-27' -> '2025-26'"""
    start = int(season[:4])
    return f"{start - 1}-{str(start)[-2:]}"


def fetch_player_season_stats(season: str):
    from nba_api.stats.endpoints import leaguedashplayerstats
    stats = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season, season_type_all_star="Regular Season", per_mode_detailed="PerGame"
    )
    return stats.get_data_frames()[0]


def fetch_summer_league_stats(season: str):
    """Per-game Summer League stats for `season` (nba_api's league_id '15').
    Returns an empty-safe DataFrame; rows with no PLAYER_NAME are dropped."""
    from nba_api.stats.endpoints import leaguedashplayerstats
    stats = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season, season_type_all_star="Regular Season",
        league_id_nullable="15", per_mode_detailed="PerGame"
    )
    df = stats.get_data_frames()[0]
    return df.dropna(subset=["PLAYER_NAME"])


def fetch_team_rosters(season: str):
    from nba_api.stats.static import teams as static_teams
    from nba_api.stats.endpoints import commonteamroster

    rosters = {}
    for t in static_teams.get_teams():
        roster_df = commonteamroster.CommonTeamRoster(team_id=t["id"], season=season).get_data_frames()[0]
        rosters[t["full_name"]] = roster_df["PLAYER"].tolist()
        time.sleep(0.6)  # stay polite to the API -- don't hammer it
    return rosters


def build_and_cache(season: str = "2026-27", force: bool = False) -> None:
    if ROSTER_CACHE.exists() and not force:
        print(f"Cache already exists at {ROSTER_CACHE}. Use --refresh to force an update.")
        return

    stats_season = previous_season(season)
    summer_season = season

    print(f"Fetching {stats_season} player season stats (most recent completed season)...")
    stats_df = fetch_player_season_stats(stats_season)
    if stats_df.empty:
        raise RuntimeError(
            f"No regular-season stats found for {stats_season} either -- "
            "check the season string or nba_api's current data availability."
        )

    print(f"Fetching {summer_season} Summer League stats (rookie/no-stat-line fallback)...")
    summer_df = fetch_summer_league_stats(summer_season)
    print(f"  ({len(summer_df)} Summer League player-rows available)")

    print(f"Fetching {season} team rosters (this loops all 30 teams, ~20-30s)...")
    rosters = fetch_team_rosters(season)

    teams_data = {}
    missing = []
    used_summer_league = []
    for team_name, player_names in rosters.items():
        team_players = []
        for pname in player_names:
            row = stats_df[stats_df["PLAYER_NAME"] == pname]
            source = "season_stats"
            if row.empty:
                row = summer_df[summer_df["PLAYER_NAME"] == pname]
                source = "summer_league"
            if row.empty:
                missing.append(pname)  # no season stats AND no Summer League line
                continue
            r = row.iloc[0]
            entry = {"name": pname, "team": team_name, "source": source}
            for our_key, api_key in FIELD_MAP.items():
                entry[our_key] = float(r[api_key])
            team_players.append(entry)
            if source == "summer_league":
                used_summer_league.append(pname)
        teams_data[team_name] = team_players

    with open(ROSTER_CACHE, "w") as f:
        json.dump({
            "roster_season": season,
            "stats_season": stats_season,
            "summer_league_season": summer_season,
            "teams": teams_data,
        }, f, indent=2)

    print(f"Cached {len(teams_data)} teams -> {ROSTER_CACHE}")
    if used_summer_league:
        print(f"({len(used_summer_league)} players used Summer League fallback: "
              f"{', '.join(used_summer_league[:5])}{'...' if len(used_summer_league) > 5 else ''})")
    if missing:
        print(f"({len(missing)} players had NO stat line at all (season or Summer League), skipped: "
              f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="force re-fetch even if cache exists")
    parser.add_argument("--season", default="2026-27", help="season to build rosters/sim for, e.g. 2026-27")
    args = parser.parse_args()
    build_and_cache(season=args.season, force=args.refresh)
