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
TEAM_CONFERENCE_CACHE = CACHE_DIR / "team_conferences.json"
INJURIES_CACHE = CACHE_DIR / "injuries.json"
ROSTER_MEMBERSHIP_CACHE = CACHE_DIR / "roster_membership.json"

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


def fetch_team_conferences(season: str) -> dict:
    """
    Each team's real conference (East/West). Cached, unlike
    fetch_real_standings -- a team's conference is essentially
    permanent structural fact (not something to re-check every
    comparison), the same category as its name or roster.
    """
    from nba_api.stats.endpoints import leaguestandingsv3
    df = leaguestandingsv3.LeagueStandingsV3(season=season, timeout=30).get_data_frames()[0]

    conferences = {}
    for _, row in df.iterrows():
        team_name = f"{row['TeamCity']} {row['TeamName']}"
        team_name = NBA_API_TEAM_NAME_FIXES.get(team_name, team_name)
        conferences[team_name] = row["Conference"]
    return conferences


def build_and_cache_team_conferences(season: str = "2025-26", force: bool = False) -> None:
    if TEAM_CONFERENCE_CACHE.exists() and not force:
        print(f"Team conference cache already exists at {TEAM_CONFERENCE_CACHE}. Use --refresh to force an update.")
        return

    print(f"Fetching {season} team conference assignments...")
    conferences = fetch_team_conferences(season)

    if len(conferences) != 30:
        print(f"WARNING: expected 30 teams, got {len(conferences)} -- double check the season string.")

    with open(TEAM_CONFERENCE_CACHE, "w") as f:
        json.dump({"season": season, "teams": conferences}, f, indent=2)

    print(f"Cached conference assignments for {len(conferences)} teams -> {TEAM_CONFERENCE_CACHE}")


def _fetch_normalized_game_log(season: str):
    """
    One league-wide game log call -- every player's every game played
    this season, all at once -- instead of one API call per player
    (450+ separate calls would be slow and impolite to the API for the
    same information). Shared by fetch_player_absence_stints AND
    fetch_roster_membership below, since both are derived from
    literally the same rows; see build_and_cache_player_history, which
    fetches this ONCE and hands it to both.

    Applies the same team-name spelling fix already used for the
    schedule and defense endpoints ("LA Clippers" vs "Los Angeles
    Clippers") -- this endpoint has the same inconsistency, confirmed
    by testing: without it, Kawhi Leonard's real games silently failed
    to match his team at all, and looked like a 100%-missed season.
    """
    from nba_api.stats.endpoints import leaguegamelog

    df = leaguegamelog.LeagueGameLog(
        season=season, player_or_team_abbreviation="P",
        season_type_all_star="Regular Season", timeout=30,
    ).get_data_frames()[0]
    df["TEAM_NAME"] = df["TEAM_NAME"].replace(NBA_API_TEAM_NAME_FIXES)
    return df


def _team_schedule_map(schedule_games: list) -> dict:
    """team_name -> that team's own games, in real chronological order
    (schedule_games is already sorted -- see fetch_schedule)."""
    team_schedule = {}
    for g in schedule_games:
        team_schedule.setdefault(g["home_team"], []).append(g["game_id"])
        team_schedule.setdefault(g["away_team"], []).append(g["game_id"])
    return team_schedule


def fetch_player_absence_stints(df, rosters: dict, schedule_games: list) -> dict:
    """
    Real per-player absence stints this season: for every player, how many
    SEPARATE stretches of their own team's games they missed, and how long
    each stretch actually was (e.g. a player might show [16, 1] -- one real
    16-game absence, plus one unrelated single-game miss later).

    IMPORTANT CAVEAT: "missed" here just means "team played a game, this
    player has no stat line for it." The real NBA data has no field for
    WHY -- injury, rest/load management, a trade, personal reasons all look
    identical. This is used as an injury proxy on purpose (matches the
    project's goal of an available/unavailable pattern that's accurate to
    this season), not a claim about diagnosed injuries. A player with ZERO
    real games all season (e.g. a real season-long Achilles tear) isn't
    covered by this at all -- they have no stat line to measure a rate
    from, and are already excluded from the whole player pool by the
    existing "no real stat line" rule (see CLAUDE.md).

    `df` is the already-fetched, already team-name-fixed league game log
    from _fetch_normalized_game_log -- passed in rather than fetched here
    so the same one call can also feed fetch_roster_membership.
    """
    team_schedule = _team_schedule_map(schedule_games)

    absences = {}
    for team_name, players in rosters.items():
        full_sched = team_schedule.get(team_name, [])
        if not full_sched:
            continue
        for p in players:
            name = p["name"]
            all_rows = df[df["PLAYER_NAME"] == name]
            rows = all_rows[all_rows["TEAM_NAME"] == team_name]
            played_game_ids = set(rows["GAME_ID"])

            # Only clip to "games since first appearance for this team"
            # when there's real evidence of a mid-season trade (this
            # player's log shows them on a DIFFERENT team at some point).
            # Otherwise an early-season gap is a genuine absence (e.g.
            # hurt since day one, returned late) -- not a "wasn't traded
            # here yet" artifact. Caught by testing against Jayson Tatum's
            # real season-long Achilles absence, which the naive version
            # (always clip to first appearance) silently threw away.
            other_teams = set(all_rows["TEAM_NAME"]) - {team_name}
            if other_teams and played_game_ids:
                first_index = min(full_sched.index(gid) for gid in played_game_ids if gid in full_sched)
                considered = full_sched[first_index:]
            else:
                considered = full_sched

            # Walk the team's games in order, grouping consecutive misses
            # into stints -- a real injury is a contiguous stretch, not
            # scattered single-game gaps.
            stints = []
            current_stint = 0
            for gid in considered:
                if gid in played_game_ids:
                    if current_stint:
                        stints.append(current_stint)
                        current_stint = 0
                else:
                    current_stint += 1
            if current_stint:
                stints.append(current_stint)  # still out when the season ended

            absences[name] = {"games_considered": len(considered), "stints": stints}
    return absences


def fetch_roster_membership(df, schedule_games: list) -> dict:
    """
    Real per-team roster membership this season: for every player who
    suited up for a given team AT ALL, the first and last game (in that
    team's own real chronological schedule) they actually played for
    them.

    This is what makes an in-season TRADE real in the sim: rosters.json
    only ever files a player under ONE team (whichever team's roster
    call happened to return them -- in practice, their real FINAL team
    that season), so today a traded player is simulated as if they'd
    been on their new team all along, and never existed on their old
    team at all -- even for the real games they actually played there.
    transactions.py uses this cache to fix both sides: add the player
    back to their old team's pool for their real pre-trade games, and
    restrict their new team's pool to only their real post-trade games.

    For the vast majority of players (never traded this season), this
    is just "team's first game" to "team's last game" -- identical to
    today's whole-season-static-roster behavior, so this only actually
    changes anything for real in-season trades.
    """
    team_schedule = _team_schedule_map(schedule_games)

    # Only players with real game-log evidence of MORE THAN ONE team
    # this season count as "moved" here. A player who simply debuted
    # late for their only team (a rookie, a late 10-day signing) isn't
    # a trade -- restricting their window would just duplicate what
    # injuries.py already does more appropriately for that case (treat
    # the early gap as one long absence stint on their one real team).
    # Caught by testing: without this filter, 510 of 522 players got a
    # "restricted" window just from ordinary end-of-season rest games
    # not landing exactly on the schedule's literal last game_id.
    teams_by_player = df.groupby("PLAYER_NAME")["TEAM_NAME"].unique()
    traded_players = {name for name, teams_ in teams_by_player.items() if len(teams_) > 1}

    membership = {}  # team_name -> [{"name", "first_game_id", "last_game_id"}, ...]
    for team_name, game_ids in team_schedule.items():
        game_index = {gid: i for i, gid in enumerate(game_ids)}
        team_rows = df[(df["TEAM_NAME"] == team_name) & (df["PLAYER_NAME"].isin(traded_players))]
        for player_name, player_rows in team_rows.groupby("PLAYER_NAME"):
            indices = sorted(game_index[gid] for gid in player_rows["GAME_ID"] if gid in game_index)
            if not indices:
                continue
            membership.setdefault(team_name, []).append({
                "name": player_name,
                "first_game_id": game_ids[indices[0]],
                "last_game_id": game_ids[indices[-1]],
            })
    return membership


def _count_traded_players(membership: dict) -> int:
    """How many distinct players show up under 2+ different teams'
    membership lists -- i.e., real in-season trades, for a human-
    readable count when the cache is built."""
    teams_by_player: dict = {}
    for team_name, entries in membership.items():
        for e in entries:
            teams_by_player.setdefault(e["name"], set()).add(team_name)
    return sum(1 for teams in teams_by_player.values() if len(teams) > 1)


def build_and_cache_player_history(season: str = "2025-26", force: bool = False) -> None:
    """
    Builds BOTH cache/injuries.json (real absence stints) and
    cache/roster_membership.json (real per-team windows, capturing
    in-season trades) from ONE shared league-wide game log fetch --
    they're both derived from literally the same rows, so there's no
    reason to hit the API for it twice.
    """
    if INJURIES_CACHE.exists() and ROSTER_MEMBERSHIP_CACHE.exists() and not force:
        print(f"Injuries + roster-membership caches already exist. Use --refresh to force an update.")
        return

    if not ROSTER_CACHE.exists() or not SCHEDULE_CACHE.exists():
        print("Needs rosters.json and schedule.json first -- run this script normally to build those, then re-run.")
        return

    with open(ROSTER_CACHE) as f:
        rosters = json.load(f)["teams"]
    with open(SCHEDULE_CACHE) as f:
        schedule_games = json.load(f)["games"]

    print(f"Fetching {season} league-wide player game log (shared by injuries + roster history)...")
    df = _fetch_normalized_game_log(season)

    print("Computing real per-player absence stints...")
    absences = fetch_player_absence_stints(df, rosters, schedule_games)
    with open(INJURIES_CACHE, "w") as f:
        json.dump({"season": season, "players": absences}, f, indent=2)
    with_absences = sum(1 for a in absences.values() if a["stints"])
    print(f"Cached real absence data for {len(absences)} players "
          f"({with_absences} had at least one real absence) -> {INJURIES_CACHE}")

    print("Computing real per-team roster membership...")
    membership = fetch_roster_membership(df, schedule_games)
    with open(ROSTER_MEMBERSHIP_CACHE, "w") as f:
        json.dump({"season": season, "teams": membership}, f, indent=2)
    traded = _count_traded_players(membership)
    print(f"Cached roster membership for {len(membership)} teams "
          f"({traded} players appeared on 2+ teams -- real in-season trades) -> {ROSTER_MEMBERSHIP_CACHE}")


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
    build_and_cache_team_conferences(season=args.season, force=args.refresh)
    build_and_cache_player_history(season=args.season, force=args.refresh)
