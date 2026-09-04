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
SCHEDULE_CACHE = CACHE_DIR / "schedule.json"
TEAM_DEFENSE_CACHE = CACHE_DIR / "team_defense.json"

# Maps our field names -> the NBA stats API's "Opponent" column names.
# These are real per-game stats about what a team's REAL OPPONENTS did
# against them -- i.e., a direct measure of that team's own defense.
# Checked directly against real data: Oklahoma City (the real league's
# best team) has the single lowest opp_fg_pct in the league, and
# Washington (the worst team) is near the highest -- confirms this
# data genuinely captures real defensive strength.
DEFENSE_FIELD_MAP = {
    "opp_fgm": "OPP_FGM", "opp_fga": "OPP_FGA",
    "opp_fg3m": "OPP_FG3M", "opp_fg3a": "OPP_FG3A",
}

# The real schedule endpoint (ScheduleLeagueV2) returns EVERY game the
# league plays -- preseason, All-Star weekend, the play-in tournament,
# every playoff round -- not just the 1,230 games (30 teams x 82 / 2)
# that actually count toward the regular-season standings. These
# gameLabel values were checked directly against the real 2025-26 data
# and confirmed to be everything that ISN'T a real regular-season game.
NON_REGULAR_SEASON_LABELS = {
    "Preseason", "All-Star", "All-Star Championship",
    "Rising Stars Semifinal", "Rising Stars Final",
    "East First Round", "West First Round",
    "East Conf. Semifinals", "West Conf. Semifinals",
    "East Conf. Finals", "West Conf. Finals",
    "NBA Finals", "SoFi Play-In Tournament",
}

# The Emirates NBA Cup is a real wrinkle: its group-stage, quarterfinal,
# and semifinal games ALL count toward the real regular-season standings
# (checked directly against the data -- excluding them undercounted the
# season by dozens of games). Only the single Championship game at the
# very end doesn't count -- it's an exhibition-style final at a neutral
# site, identified by this specific (gameLabel, gameSubLabel) pair.
NBA_CUP_LABEL = "Emirates NBA Cup"
NBA_CUP_FINAL_SUBLABEL = "Championship"

# Some nba_api endpoints spell one team's name differently ("LA
# Clippers") than every other endpoint used in this project ("Los
# Angeles Clippers") -- checked directly against both the schedule
# endpoint and the team-defense endpoint, it's the only mismatch
# either one has. Reused everywhere a team name comes back from
# nba_api, so a name mismatch can't silently break a lookup anywhere
# in the project.
NBA_API_TEAM_NAME_FIXES = {
    "LA Clippers": "Los Angeles Clippers",
}

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


def fetch_team_defense(season: str):
    """
    Real per-game stats about what each team's OPPONENTS did against
    them -- a direct measure of that team's own defense, since it's
    literally "how well do teams shoot when they play against this
    team." Returns a dict keyed by full team name (matching every other
    team-name key in this project), each value the raw makes/attempts
    needed to compute 2PT/3PT/overall opponent shooting %.
    """
    from nba_api.stats.endpoints import leaguedashteamstats
    df = leaguedashteamstats.LeagueDashTeamStats(
        season=season, measure_type_detailed_defense="Opponent", per_mode_detailed="PerGame", timeout=30,
    ).get_data_frames()[0]

    defense = {}
    for _, row in df.iterrows():
        team_name = NBA_API_TEAM_NAME_FIXES.get(row["TEAM_NAME"], row["TEAM_NAME"])
        entry = {our_key: float(row[api_key]) for our_key, api_key in DEFENSE_FIELD_MAP.items()}
        defense[team_name] = entry
    return defense


def build_and_cache_team_defense(season: str = "2025-26", force: bool = False) -> None:
    if TEAM_DEFENSE_CACHE.exists() and not force:
        print(f"Team defense cache already exists at {TEAM_DEFENSE_CACHE}. Use --refresh to force an update.")
        return

    print(f"Fetching {season} real opponent-shooting (defense) stats...")
    defense = fetch_team_defense(season)

    if len(defense) != 30:
        print(f"WARNING: expected 30 teams, got {len(defense)} -- double check the season string.")

    with open(TEAM_DEFENSE_CACHE, "w") as f:
        json.dump({"season": season, "teams": defense}, f, indent=2)

    print(f"Cached defensive stats for {len(defense)} teams -> {TEAM_DEFENSE_CACHE}")


def fetch_real_standings(season: str) -> dict:
    """
    The REAL final standings for `season` -- not cached, since it's
    used on demand for comparing a simulated season against reality
    (the whole original point of this project), not as an input the
    simulation itself depends on every run. Returns {team_name: wins}.
    """
    from nba_api.stats.endpoints import leaguestandingsv3
    df = leaguestandingsv3.LeagueStandingsV3(season=season, timeout=30).get_data_frames()[0]

    standings = {}
    for _, row in df.iterrows():
        team_name = f"{row['TeamCity']} {row['TeamName']}"
        team_name = NBA_API_TEAM_NAME_FIXES.get(team_name, team_name)
        standings[team_name] = int(row["WINS"])
    return standings


def fetch_schedule(season: str):
    """
    The real regular-season schedule for `season`: a list of dicts, one
    per game, each {game_id, date, home_team, away_team} -- home_team/
    away_team are full team names matching the keys used everywhere
    else in this project (models.Team.name, the rosters.json keys),
    sorted into real chronological order.
    """
    from nba_api.stats.endpoints import scheduleleaguev2
    df = scheduleleaguev2.ScheduleLeagueV2(season=season, timeout=30).get_data_frames()[0]

    is_regular_season = ~df["gameLabel"].isin(NON_REGULAR_SEASON_LABELS)
    is_cup_final = (df["gameLabel"] == NBA_CUP_LABEL) & (df["gameSubLabel"] == NBA_CUP_FINAL_SUBLABEL)
    # Sorting by "gameDate" (a "10/02/2025 00:00:00" string) was a real
    # bug caught by testing -- MM/DD/YYYY doesn't sort correctly as
    # plain text (e.g. "01/01/2026" sorts before "12/31/2025", since
    # '0' < '1'), which put January games before December ones.
    # "gameDateEst" is ISO format ("2025-10-02T00:00:00Z"), which DOES
    # sort correctly as a plain string -- using that instead for both
    # sorting and the date actually stored.
    regular_season = df[is_regular_season & ~is_cup_final].sort_values(["gameDateEst", "gameId"])

    def _team_name(city: str, name: str) -> str:
        full_name = f"{city} {name}"
        return NBA_API_TEAM_NAME_FIXES.get(full_name, full_name)

    games = []
    for _, row in regular_season.iterrows():
        games.append({
            "game_id": row["gameId"],
            # "2025-10-02T00:00:00Z" -> "2025-10-02" -- there's no real
            # game-time info worth storing, just the date.
            "date": row["gameDateEst"].split("T")[0],
            "home_team": _team_name(row["homeTeam_teamCity"], row["homeTeam_teamName"]),
            "away_team": _team_name(row["awayTeam_teamCity"], row["awayTeam_teamName"]),
        })
    return games


def build_and_cache_schedule(season: str = "2025-26", force: bool = False) -> None:
    if SCHEDULE_CACHE.exists() and not force:
        print(f"Schedule cache already exists at {SCHEDULE_CACHE}. Use --refresh to force an update.")
        return

    print(f"Fetching {season} regular-season schedule...")
    games = fetch_schedule(season)

    expected = 30 * 82 // 2
    if len(games) != expected:
        # Not necessarily wrong (a real schedule can shift slightly due
        # to in-season changes), but different enough from the expected
        # count to be worth a human noticing rather than silently
        # trusting it.
        print(f"WARNING: expected {expected} regular-season games, got {len(games)} -- "
              f"double check NON_REGULAR_SEASON_LABELS still covers this season correctly.")

    with open(SCHEDULE_CACHE, "w") as f:
        json.dump({"season": season, "games": games}, f, indent=2)

    print(f"Cached {len(games)} regular-season games -> {SCHEDULE_CACHE}")


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
    parser.add_argument("--season", default="2025-26", help="season to pull roster + stats + schedule from, e.g. 2025-26")
    args = parser.parse_args()
    build_and_cache(season=args.season, force=args.refresh)
    build_and_cache_schedule(season=args.season, force=args.refresh)
    build_and_cache_team_defense(season=args.season, force=args.refresh)
