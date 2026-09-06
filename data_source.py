"""
Pulls real NBA team rosters and real per-game player stats from the NBA's
own stats endpoints (via the community-maintained `nba_api` package -- same
underlying data source sites like basketball-reference mirror) and caches
the result locally as JSON, so the rest of the project can just read a file
instead of hitting the network every time.

Both the roster AND the stats always come from the SAME season on purpose
-- every player on THAT season's roster necessarily played that season,
so every single player is guaranteed to have a real stat line for it. No
rookies-with-no-stats edge case to handle here at all.

Every cache file lives under cache/<season>/ (see _season_cache_dir), not
one shared set of filenames -- fetching a second season never overwrites
another one's cache. This is what backtesting past seasons is actually
built on: `python data_source.py --season 2024-25` fetches and caches
that season entirely independently of whatever's already cached for
2025-26, and both can be loaded and simulated side by side.

Install once:
    pip install nba_api

Usage:
    python data_source.py                    # fetch + cache (skips if cache exists)
    python data_source.py --refresh          # force re-fetch, overwrite cache
    python data_source.py --season 2025-26   # which season to pull (default 2025-26)
"""
import argparse
import json
import math
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _season_cache_dir(season: str) -> Path:
    """
    Every season's fetched data lives in its own subfolder
    (cache/2025-26/, cache/2024-25/, ...) instead of one shared set of
    filenames. This is what makes backtesting past seasons possible at
    all -- fetching a second season used to silently overwrite the
    first one's cache; now each season simulated keeps its own
    permanent, independent snapshot, and multiple seasons can sit
    side by side (needed for the offseason player-movement diff too --
    that's a comparison BETWEEN two seasons' cached rosters).
    """
    season_dir = CACHE_DIR / season
    season_dir.mkdir(exist_ok=True)
    return season_dir


# One small path-building function per cache file, all rooted under
# _season_cache_dir -- every build_and_cache_* function below already
# takes `season` as a parameter, so this is just "where does this
# season's copy of this file live," not a new argument to thread
# through anywhere.
def _roster_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "rosters.json"


def _schedule_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "schedule.json"


def _team_defense_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "team_defense.json"


def _team_conference_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "team_conferences.json"


def _injuries_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "injuries.json"


def _roster_membership_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "roster_membership.json"


def _player_consistency_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_consistency.json"


def _league_pace_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "league_pace.json"


def _team_division_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "team_divisions.json"

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
# that actually count toward the regular-season standings.
#
# USED TO filter this by a hardcoded set of `gameLabel` strings, tuned
# only against 2025-26 -- broke the moment a second real season got
# fetched for backtesting: 2024-25 uses "Rising Stars Championship"
# where 2025-26 uses "Rising Stars Final" for the exact same event,
# so those 1-2 games silently slipped through as "regular season"
# (found by testing: fetching 2024-25 came back with 1233 games
# instead of the expected 1230, and the extra 3 traced to real Rising
# Stars mini-tournament games under "teams" named things like "Global
# Stars"/"Rising Stars" -- not real NBA teams at all).
#
# Fixed with something far more robust than a label whitelist that
# needs updating every time an event gets renamed: a real NBA game_id
# is 10 digits, and the 3RD digit is a season-type code (checked
# directly against 2025-26 AND 2024-25's full label lists) --
# '1'=preseason, '2'=regular season, '3'=All-Star weekend, '4'=
# playoffs, '5'=play-in, '6'=the one exhibition-style Emirates NBA Cup
# Championship game (its earlier group-stage/quarterfinal/semifinal
# rounds are all '2' -- they DO count toward the real standings, only
# the neutral-site Cup Final doesn't). Verified byte-for-byte identical
# to the old label-based filter's result on 2025-26 (same exact 1230
# games, not just the same count) before replacing it -- see
# REGULAR_SEASON_GAME_ID_DIGIT below.
REGULAR_SEASON_GAME_ID_DIGIT = "2"

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

# Real NBA teams that relocated/renamed -- found by testing while
# backtesting 2013-14: commonteamroster's response has NO team-name
# field at all (checked directly -- only a numeric TeamID), so
# fetch_team_rosters had no choice but to label every team with its
# CURRENT name (from nba_api's static team list) regardless of season.
# For 2013-14 that meant rosters.json said "Charlotte Hornets" while
# the SAME season's schedule/defense/standings endpoints (which DO
# carry a season-accurate name) all correctly said "Charlotte
# Bobcats" -- a silent mismatch that would make season.py's own
# `teams[scheduled_game.home_team]` raise a KeyError on every real
# Bobcats game. Small, hardcoded, season-ranged overrides for the few
# real relocations/renames within the range this project backtests --
# same "stable fact, not worth fetch infrastructure" reasoning as
# NBA_API_TEAM_NAME_FIXES above. Keyed by team_id (stable across a
# rename; the franchise's real per-game numbers keep flowing to the
# same id), season ranges are real-world, inclusive on both ends.
HISTORICAL_TEAM_NAMES = {
    1610612766: [("2004-05", "2013-14", "Charlotte Bobcats")],  # -> Charlotte Hornets 2014-15+
    1610612740: [
        ("1988-89", "2001-02", "Charlotte Hornets"),  # the ORIGINAL Hornets, before relocating
        ("2002-03", "2004-05", "New Orleans Hornets"),
        ("2005-06", "2006-07", "New Orleans/Oklahoma City Hornets"),  # post-Katrina relocation
        ("2007-08", "2012-13", "New Orleans Hornets"),
    ],  # -> New Orleans Pelicans 2013-14+
    1610612760: [("1996-97", "2007-08", "Seattle SuperSonics")],  # -> Oklahoma City Thunder 2008-09+
    1610612751: [("1977-78", "2011-12", "New Jersey Nets")],  # -> Brooklyn Nets 2012-13+
    1610612763: [("1995-96", "2000-01", "Vancouver Grizzlies")],  # -> Memphis Grizzlies 2001-02+
    1610612764: [("1974-75", "1996-97", "Washington Bullets")],  # -> Washington Wizards 1997-98+
}


def _historical_team_name(team_id: int, season: str, current_name: str) -> str:
    """`current_name` (from nba_api's static, always-current team list)
    unless `season` falls inside one of this team_id's real relocation/
    rename eras above -- see HISTORICAL_TEAM_NAMES's docstring. Season
    strings compare correctly as plain text here (both bounds and the
    season argument are always "YYYY-YY", same 4-digit-year format)."""
    for start, end, name in HISTORICAL_TEAM_NAMES.get(team_id, []):
        if start <= season <= end:
            return name
    return current_name

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
        # See HISTORICAL_TEAM_NAMES -- t["full_name"] is always this
        # team's CURRENT name, wrong for a season before a real
        # relocation/rename.
        team_name = _historical_team_name(t["id"], season, t["full_name"])
        rosters[team_name] = roster_df["PLAYER"].tolist()
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
    cache_path = _team_defense_cache_path(season)
    if cache_path.exists() and not force:
        print(f"Team defense cache already exists at {cache_path}. Use --refresh to force an update.")
        return

    print(f"Fetching {season} real opponent-shooting (defense) stats...")
    defense = fetch_team_defense(season)

    if len(defense) != 30:
        print(f"WARNING: expected 30 teams, got {len(defense)} -- double check the season string.")

    with open(cache_path, "w") as f:
        json.dump({"season": season, "teams": defense}, f, indent=2)

    print(f"Cached defensive stats for {len(defense)} teams -> {cache_path}")


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


def fetch_team_divisions(season: str) -> dict:
    """
    Each team's real DIVISION in `season` -- and unlike its conference,
    this genuinely changes: the league played with four divisions
    (Atlantic/Central in the East, Midwest/Pacific in the West) until it
    realigned to today's six in 2004-05. The endpoint returns the right
    ones for whichever season is asked, so this is real per-season data
    rather than a table someone has to remember to update.

    Only the playoff-seeding tiebreakers use it (division leader,
    division record) -- see playoffs.py, which used to hold a hardcoded
    MODERN six-division map and therefore applied the wrong divisions to
    every season before 2004-05.
    """
    from nba_api.stats.endpoints import leaguestandingsv3
    df = leaguestandingsv3.LeagueStandingsV3(season=season, timeout=30).get_data_frames()[0]

    divisions = {}
    for _, row in df.iterrows():
        team_name = f"{row['TeamCity']} {row['TeamName']}"
        team_name = NBA_API_TEAM_NAME_FIXES.get(team_name, team_name)
        divisions[team_name] = row["Division"]
    return divisions


def build_and_cache_team_conferences(season: str = "2025-26", force: bool = False) -> None:
    """Conferences AND divisions -- both come from the same standings
    endpoint, so one call fills both cache files."""
    division_path = _team_division_cache_path(season)
    if force or not division_path.exists():
        divisions = fetch_team_divisions(season)
        with open(division_path, "w") as f:
            json.dump({"season": season, "teams": divisions}, f, indent=2)
        print(f"Cached real divisions for {len(divisions)} teams "
              f"({len(set(divisions.values()))} divisions) -> {division_path}")

    cache_path = _team_conference_cache_path(season)
    if cache_path.exists() and not force:
        print(f"Team conference cache already exists at {cache_path}. Use --refresh to force an update.")
        return

    print(f"Fetching {season} team conference assignments...")
    conferences = fetch_team_conferences(season)

    if len(conferences) != 30:
        print(f"WARNING: expected 30 teams, got {len(conferences)} -- double check the season string.")

    with open(cache_path, "w") as f:
        json.dump({"season": season, "teams": conferences}, f, indent=2)

    print(f"Cached conference assignments for {len(conferences)} teams -> {cache_path}")


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
    SEPARATE stretches of their own team's games they missed, how long
    each stretch actually was, AND where it actually started (as a game
    INDEX into that team's own real schedule, e.g. a stint starting at
    index 0 means "missed games from the literal start of the season" --
    a real day-one injury, not one that happened to land there). The
    start index is what lets injuries.py anchor a simulated injury to
    roughly WHEN it really happened, instead of scattering it randomly
    across the whole season regardless of when the real one actually
    began (e.g. a player who was hurt before the season even started,
    like a real preseason Achilles tear, should still be hurt from the
    start in a simulated season too, not randomly mid-season).

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

            # A player with real game-log evidence of playing for a
            # DIFFERENT team, but ZERO real games logged for the team
            # rosters.json currently lists them under, isn't "hurt for
            # this team's entire season" -- that's a roster
            # misattribution (e.g. a trade that happened after this
            # team's season was already over), which
            # fetch_roster_membership handles instead (routes them to
            # their real team, fully excludes them here). Recording an
            # 82-game stint here too would just duplicate that exclusion
            # as a misleading fake "injury" in the display.
            if all_rows["TEAM_NAME"].nunique() and team_name not in set(all_rows["TEAM_NAME"]):
                absences[name] = {"games_considered": 0, "stints": []}
                continue

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
                first_index = 0
                considered = full_sched

            # Walk the team's games in order, grouping consecutive misses
            # into stints -- a real injury is a contiguous stretch, not
            # scattered single-game gaps. `start` is recorded relative to
            # the team's FULL schedule (not just `considered`), so it
            # lines up directly with injuries.py's own game-index list,
            # which is always the full season regardless of any trade clip.
            stints = []
            current_length = 0
            current_start = None
            for i, gid in enumerate(considered):
                if gid in played_game_ids:
                    if current_length:
                        stints.append({"start": first_index + current_start, "length": current_length})
                        current_length = 0
                else:
                    if current_length == 0:
                        current_start = i
                    current_length += 1
            if current_length:
                stints.append({"start": first_index + current_start, "length": current_length})  # still out when the season ended

            absences[name] = {"games_considered": len(considered), "stints": stints}
    return absences


def fetch_roster_membership(df, rosters: dict, schedule_games: list) -> dict:
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

    membership = {}  # team_name -> [{"name", "first_game_id", "last_game_id", "real_min"}, ...]
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
                # This team-STINT's real per-game minutes -- NOT the same
                # as rosters.json's season-long blended average across
                # every team a player suited up for. Confirmed by
                # testing (benchmark_accuracy.py): reusing the blended
                # average for both stints was the actual cause of real
                # trades making standings accuracy WORSE, not better --
                # gaps of 8-13 real minutes/game between the blend and a
                # specific stint are common, and minutes is both the
                # weight game_engine._active_roster_for_game uses to
                # decide who plays AND the denominator every other real
                # stat gets divided by to build a per-minute rate. See
                # transactions.py for where this actually gets applied.
                "real_min": float(player_rows["MIN"].mean()),
            })

    # A separate, smaller case from an ordinary in-season trade above:
    # a player whose CURRENT roster listing (rosters.json -- "who holds
    # their rights right now") doesn't match ANY team they logged a
    # real game for this season -- e.g. a trade that happened after the
    # season's games were already all played. The `len(teams_) > 1`
    # check above can't catch this: such a player shows under exactly
    # ONE team in the log (their real one), never the one rosters.json
    # currently lists them under. Confirmed by testing: exactly 4 such
    # players this season (Emanuel Miller, D'Angelo Russell, Anthony
    # Davis, Tosan Evbuomwan), all real established players with real
    # per-game stats -- not noise. Without this, each would be
    # simulated as having missed literally every game for a team they
    # never suited up for even once, AND never appear in the pool for
    # the team they actually played for at all.
    for wrong_team, players in rosters.items():
        for p in players:
            name = p["name"]
            real_teams = set(df[df["PLAYER_NAME"] == name]["TEAM_NAME"].unique())
            if not real_teams or wrong_team in real_teams:
                continue  # no real game-log evidence either way, or it does match -- not this case

            # Route them to their real team, same as an ordinary trade.
            real_team = next(iter(real_teams))  # always exactly one in practice -- see docstring above
            real_game_ids = team_schedule.get(real_team, [])
            real_game_index = {gid: i for i, gid in enumerate(real_game_ids)}
            real_rows = df[(df["PLAYER_NAME"] == name) & (df["TEAM_NAME"] == real_team)]
            indices = sorted(real_game_index[gid] for gid in real_rows["GAME_ID"] if gid in real_game_index)
            if indices:
                membership.setdefault(real_team, []).append({
                    "name": name,
                    "first_game_id": real_game_ids[indices[0]],
                    "last_game_id": real_game_ids[indices[-1]],
                    "real_min": float(real_rows["MIN"].mean()),
                })

            # Fully exclude them from the wrongly-listed team -- None
            # first/last_game_id is a sentinel transactions.py reads as
            # "zero real games here," not a normal restricted window
            # (there's no real game to anchor a first/last id to).
            # real_min is meaningless here for the same reason.
            membership.setdefault(wrong_team, []).append({
                "name": name, "first_game_id": None, "last_game_id": None, "real_min": None,
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


# A game a player suited up for but barely appeared in (MIN rounds to 0 in
# this endpoint -- 118 such rows in 2023-24) isn't evidence about how
# streaky a scorer they are, it's a garbage-time cameo. Dropped for the
# same reason db.py refuses to store 0-minute rows: "per game played"
# means games actually played.
MIN_PLAYED_MINUTES = 1

# A standard deviation needs at least two numbers to exist at all. Below
# that there's simply nothing to measure -- those players are left out of
# the file entirely, and whatever consumes it falls back to the league
# average stored alongside. Note this is NOT a "trustworthy sample"
# threshold: a 3-game spread is real but very noisy, which is why every
# entry also carries its own `games` count, so a consumer can weight a
# 70-game measurement more heavily than a 3-game one.
MIN_GAMES_FOR_SPREAD = 2

# Players used to compute the LEAGUE-WIDE averages (the fallback value,
# and the target any future shrinkage pulls small samples toward). Set
# well above MIN_GAMES_FOR_SPREAD on purpose: the league average should
# describe a normal NBA workload, not be dragged around by the noise of
# 40 players with four games each.
MIN_GAMES_FOR_LEAGUE_AVERAGE = 20


def fetch_player_consistency(df) -> dict:
    """
    How STREAKY each player's real scoring was, game to game -- the raw
    measurement behind the eventual 1-99 consistency rating, and what
    game_engine's per-player usage split actually runs on (see
    _scoring_concentrations there). Before this existed one global
    constant made all 450 players equally streaky relative to their own
    average.

    `df` is the same already-fetched league-wide player game log that
    injuries and roster membership are derived from (see
    _fetch_normalized_game_log) -- every player's every game is already
    sitting in it, so this costs no extra API call at all.

    TWO numbers per player, because they mean different things:

      spread_raw   how much their POINTS bounced, full stop.
      spread_rate  how much their points bounced once the games where
                   they simply played more or fewer MINUTES are
                   accounted for -- i.e. streakiness at a fixed workload.

    Both are expressed relative to the bounce PURE CHANCE alone would
    produce, so they can be compared between a 30-point scorer and a
    10-point one. For counting events like made shots, chance alone
    gives a standard deviation of about sqrt(average) -- so a value of
    1.0 means "as steady as it is physically possible to be, all the
    variation is coin flips" and 2.0 means "twice as bouncy as luck can
    explain, something real is going on." Real players run about
    1.2 to 2.5 on spread_raw.

    WHICH ONE THE SIM SHOULD EAT: spread_rate. The sim already draws
    every player's minutes per game (game_engine's Dirichlet-Multinomial
    split of the team's 240), and measured against 2023-24 its minute
    swings are already slightly WIDER than real ones (sd 6.19 vs 5.39).
    Feeding it spread_raw would apply minute-wobble twice -- the same
    double-counting family as the three box-score bugs already fixed
    here. It genuinely changes who counts as streaky: GG Jackson had
    the single highest spread_raw in 2023-24 (2.54) purely because a
    rookie's playing time swung from 10 minutes to 35, and is ordinary
    at 1.56 once that's removed, while Jordan Clarkson played steady
    minutes and stays streaky either way (2.16 -> 1.88).

    spread_raw is kept anyway because it's the honest number for a
    DISPLAYED rating: a player you can't predict because you don't know
    if he'll play 12 minutes or 32 really is unpredictable to watch.
    Same fetch, same rows, so storing both costs nothing.

    A traded player's games are pooled across both of his real teams on
    purpose -- how streaky a scorer someone is travels with the player,
    unlike the absence and roster-membership data above, which is
    genuinely per-team.
    """
    played = df[df["MIN"] >= MIN_PLAYED_MINUTES]

    players = {}
    for name, games in played.groupby("PLAYER_NAME"):
        pts, mins = games["PTS"], games["MIN"]
        # A player who never scored has no scoring spread to describe,
        # and dividing by sqrt(0) below would blow up anyway.
        if len(games) < MIN_GAMES_FOR_SPREAD or pts.sum() <= 0:
            continue

        # The bounce luck alone would produce, used to put every player
        # on the same scale regardless of how much they score.
        chance = math.sqrt(pts.mean())

        # Points predicted by MINUTES alone: this player's own season
        # points-per-minute, times the minutes he actually played that
        # night. Whatever is left over (`residual`) is the part minutes
        # can't explain -- a genuinely hot or cold shooting night.
        rate = pts.sum() / mins.sum()
        residual = pts - rate * mins

        players[name] = {
            "games": int(len(games)),
            "ppg": round(float(pts.mean()), 3),
            "mpg": round(float(mins.mean()), 3),
            "spread_raw": round(float(pts.std()) / chance, 4),
            "spread_rate": round(float(residual.std()) / chance, 4),
        }

    # The fallback for anyone not in the file (too few games, no points,
    # a rookie in a season this wasn't measured for), and the anchor any
    # future small-sample shrinkage pulls toward.
    regulars = [p for p in players.values() if p["games"] >= MIN_GAMES_FOR_LEAGUE_AVERAGE]
    league = {
        "players_counted": len(regulars),
        "spread_raw": round(sum(p["spread_raw"] for p in regulars) / len(regulars), 4),
        "spread_rate": round(sum(p["spread_rate"] for p in regulars) / len(regulars), 4),
    }
    return {"league": league, "players": players}


def fetch_league_pace_variation(df) -> dict:
    """
    How much a single game's PACE really varied in this season -- the
    number game_engine's shared pace draw is supposed to reproduce.

    Both teams in a game share one pace (they alternate possessions), so
    the game's pace is the two teams' average, and what matters is how
    far a game strays from the season's typical one. Returned as a
    RELATIVE swing (standard deviation over mean) so it is directly the
    multiplier's spread, and stays meaningful across eras where the
    absolute number of possessions differs a lot.

    Cached per season rather than hardcoded because it genuinely drifts:
    measured 6.8% in 1996-97 falling to 5.3% in 2024-25. A single
    constant taken from a modern season leaves 1990s games with about a
    quarter too little pace variation.

    Possessions use the same standard estimate as everywhere else in
    this project: FGA + 0.44*FTA + TOV - OREB.
    """
    per_team = df.groupby(["GAME_ID", "TEAM_NAME"])[["FGA", "FTA", "TOV", "OREB"]].sum()
    per_team["poss"] = (per_team.FGA + 0.44 * per_team.FTA
                        + per_team.TOV - per_team.OREB)
    per_game = per_team.groupby("GAME_ID")["poss"].mean()
    mean = float(per_game.mean())
    return {
        "games": int(len(per_game)),
        "mean_possessions": round(mean, 3),
        "pace_variation": round(float(per_game.std()) / mean, 5) if mean else 0.0,
    }


def build_and_cache_player_history(season: str = "2025-26", force: bool = False) -> None:
    """
    Builds ALL THREE of cache/injuries.json (real absence stints),
    cache/roster_membership.json (real per-team windows, capturing
    in-season trades) and cache/player_consistency.json (how streaky
    each player's real scoring was) from ONE shared league-wide game
    log fetch -- they're all derived from literally the same rows, so
    there's no reason to hit the API for it three times.

    Each file is written only if it's actually missing (or --refresh is
    passed), so adding a new one later backfills just that file across
    already-cached seasons instead of silently rewriting the other two.
    """
    injuries_path = _injuries_cache_path(season)
    membership_path = _roster_membership_cache_path(season)
    consistency_path = _player_consistency_cache_path(season)
    pace_path = _league_pace_cache_path(season)
    roster_path = _roster_cache_path(season)
    schedule_path = _schedule_cache_path(season)

    need_injuries = force or not injuries_path.exists()
    need_membership = force or not membership_path.exists()
    need_consistency = force or not consistency_path.exists()
    need_pace = force or not pace_path.exists()
    if not (need_injuries or need_membership or need_consistency or need_pace):
        print("Injuries + roster-membership + consistency + pace caches already exist. Use --refresh to force an update.")
        return

    if not roster_path.exists() or not schedule_path.exists():
        print("Needs rosters.json and schedule.json first -- run this script normally to build those, then re-run.")
        return

    with open(roster_path) as f:
        rosters = json.load(f)["teams"]
    with open(schedule_path) as f:
        schedule_games = json.load(f)["games"]

    print(f"Fetching {season} league-wide player game log (shared by injuries + roster history)...")
    df = _fetch_normalized_game_log(season)

    if need_injuries:
        print("Computing real per-player absence stints...")
        absences = fetch_player_absence_stints(df, rosters, schedule_games)
        with open(injuries_path, "w") as f:
            json.dump({"season": season, "players": absences}, f, indent=2)
        with_absences = sum(1 for a in absences.values() if a["stints"])
        print(f"Cached real absence data for {len(absences)} players "
              f"({with_absences} had at least one real absence) -> {injuries_path}")

    if need_membership:
        print("Computing real per-team roster membership...")
        membership = fetch_roster_membership(df, rosters, schedule_games)
        with open(membership_path, "w") as f:
            json.dump({"season": season, "teams": membership}, f, indent=2)
        traded = _count_traded_players(membership)
        print(f"Cached roster membership for {len(membership)} teams "
              f"({traded} players appeared on 2+ teams -- real in-season trades) -> {membership_path}")

    if need_consistency:
        print("Computing real per-player scoring consistency...")
        consistency = fetch_player_consistency(df)
        with open(consistency_path, "w") as f:
            json.dump({"season": season, **consistency}, f, indent=2)
        league = consistency["league"]
        print(f"Cached scoring consistency for {len(consistency['players'])} players "
              f"(league average spread: {league['spread_raw']:.2f} raw, "
              f"{league['spread_rate']:.2f} minutes-adjusted) -> {consistency_path}")

    if need_pace:
        print("Computing this season's real game-to-game pace variation...")
        pace = fetch_league_pace_variation(df)
        with open(pace_path, "w") as f:
            json.dump({"season": season, **pace}, f, indent=2)
        print(f"Cached real pace variation: {pace['pace_variation'] * 100:.2f}% swing "
              f"around {pace['mean_possessions']:.1f} possessions "
              f"({pace['games']} games) -> {pace_path}")


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

    # See REGULAR_SEASON_GAME_ID_DIGIT's comment for why this is a
    # game_id digit check, not a gameLabel whitelist.
    is_regular_season = df["gameId"].str[2] == REGULAR_SEASON_GAME_ID_DIGIT
    # Sorting by "gameDate" (a "10/02/2025 00:00:00" string) was a real
    # bug caught by testing -- MM/DD/YYYY doesn't sort correctly as
    # plain text (e.g. "01/01/2026" sorts before "12/31/2025", since
    # '0' < '1'), which put January games before December ones.
    # "gameDateEst" is ISO format ("2025-10-02T00:00:00Z"), which DOES
    # sort correctly as a plain string -- using that instead for both
    # sorting and the date actually stored.
    regular_season = df[is_regular_season].sort_values(["gameDateEst", "gameId"])

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
    cache_path = _schedule_cache_path(season)
    if cache_path.exists() and not force:
        print(f"Schedule cache already exists at {cache_path}. Use --refresh to force an update.")
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
              f"a real schedule change (e.g. a canceled/rescheduled game), or REGULAR_SEASON_GAME_ID_DIGIT "
              f"needs a second look for this season -- worth checking which before trusting this season's data.")

    with open(cache_path, "w") as f:
        json.dump({"season": season, "games": games}, f, indent=2)

    print(f"Cached {len(games)} regular-season games -> {cache_path}")


def build_and_cache(season: str = "2025-26", force: bool = False) -> None:
    cache_path = _roster_cache_path(season)
    if cache_path.exists() and not force:
        print(f"Cache already exists at {cache_path}. Use --refresh to force an update.")
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

    with open(cache_path, "w") as f:
        json.dump({"season": season, "teams": teams_data}, f, indent=2)

    print(f"Cached {len(teams_data)} teams -> {cache_path}")
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
