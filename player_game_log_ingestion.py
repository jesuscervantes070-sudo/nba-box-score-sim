"""
Pregame-Safe Scoring Truth phase -- game/date-level ingestion.

============================ SOURCE VERIFIED DIRECTLY ============================
`nba_api.stats.endpoints.leaguegamelog.LeagueGameLog(player_or_team_abbreviation="P")` --
ALREADY a used endpoint in this codebase (`data_source._fetch_normalized_game_log`, feeding
`fetch_player_absence_stints`/`fetch_roster_membership`), but never before cached or extracted for
box-score STAT purposes -- only for team/game_id/date matching. Checked directly this phase (one
real 2023-24 call): returns one row per real player-game, with real `PLAYER_ID` (numeric, the SAME
id space `shot_zone_ingestion.py`/`player_identity.py` already use), `GAME_ID`, `GAME_DATE`
(`YYYY-MM-DD`), and real per-game `FGM`/`FGA`/`FG3M`/`FG3A`/`FTM`/`FTA` -- exactly the raw
ingredients this phase needs for a date-filterable three_point/free_throw/three_point_preference
evidence stream. One call per season (26,401 real rows for 2023-24 alone) -- same "one league-wide
call, not one per player" economy as `shot_zone_ingestion.py`.

============================ WHAT THIS DOES NOT SOLVE ============================
This endpoint has NO shot-zone breakdown (Restricted Area / Paint Non-RA / Mid-Range) and NO
touches/drives breakdown -- confirmed by its own real column list (SEASON_ID, PLAYER_ID,
PLAYER_NAME, TEAM_ID, TEAM_ABBREVIATION, TEAM_NAME, GAME_ID, GAME_DATE, MATCHUP, WL, MIN, FGM, FGA,
FG_PCT, FG3M, FG3A, FG3_PCT, FTM, FTA, FT_PCT, OREB, DREB, REB, AST, STL, BLK, TOV, PF, PTS,
PLUS_MINUS, FANTASY_PTS, VIDEO_AVAILABLE -- no zone/touch/drive field anywhere). So this ingestion
closes the pregame-safety gap for `three_point`, `free_throw`, and `three_point_preference` only --
`rim_finishing`/`floater_short_mid`/`midrange`/`midrange_preference`/`drive_aggression` remain
PRIOR_SEASON_ONLY this phase (see player_scoring_truth_temporal.py's own module docstring for the
explicit per-target classification and the real, verified reason each one is not obtainable at
per-game granularity from any endpoint already used in this repo: shot-zone data is only ever
published as a season aggregate by `leaguedashplayershotlocations`, and the one real per-shot
endpoint that WOULD carry a date/zone/game_id per attempt, `ShotChartDetail`, requires ~500+ calls
per season and was explicitly deferred by Phase 5, unchanged by this phase).
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from data_source import _season_cache_dir

GAME_LOG_CACHE_VERSION = 1
GAME_LOG_FIRST_SEASON = "1996-97"  # same real per-player-endpoint floor as every other source here


def _game_log_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_game_log.json"


def fetch_player_game_log(season: str, retries: int = 4, timeout: int = 30):
    """One real league-wide call -- every player's every regular-season game, at once. Real,
    already-used endpoint (`data_source._fetch_normalized_game_log`'s own docstring); this
    function is independent of that one (does not import it) so this ingestion module stays
    self-contained, matching every other `*_ingestion.py` file's own convention."""
    from nba_api.stats.endpoints import leaguegamelog
    last_err = None
    for attempt in range(retries):
        try:
            return leaguegamelog.LeagueGameLog(
                season=season, player_or_team_abbreviation="P",
                season_type_all_star="Regular Season", timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"leaguegamelog({season}) failed after {retries} attempts: {last_err}")


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_and_cache_player_game_log(season: str, force: bool = False) -> Optional[dict]:
    """Cache-first, one-call-per-season ingestion. Each player's games are stored ALREADY sorted
    by (GAME_DATE, GAME_ID) -- the one deterministic tie-break this phase's temporal cutoff logic
    relies on (see player_scoring_truth_temporal.py's own "same-date ordering" documentation:
    `leaguegamelog` carries no game START TIMESTAMP, only a calendar date, so a real, rare
    same-date doubleheader is ordered by the real, stable `GAME_ID` string -- documented as a
    known, conservative limitation, not silently assumed away)."""
    cache_path = _game_log_cache_path(season)
    if cache_path.exists() and not force:
        print(f"Player game-log cache already exists at {cache_path}. Use force=True to update.")
        with open(cache_path) as f:
            return json.load(f)

    if season < GAME_LOG_FIRST_SEASON:
        print(f"{season} is before the real per-player game-log floor ({GAME_LOG_FIRST_SEASON}) -- nothing cached.")
        return None

    print(f"Fetching {season} real per-player per-game box scores (leaguegamelog)...")
    df = fetch_player_game_log(season)
    if df is None or len(df) == 0:
        print(f"{season} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    by_player: Dict[str, List[dict]] = {}
    for _, row in df.iterrows():
        pid = str(int(row["PLAYER_ID"]))
        by_player.setdefault(pid, []).append({
            "game_id": row["GAME_ID"],
            "date": row["GAME_DATE"],  # real "YYYY-MM-DD" string -- lexicographically sortable
            "fgm": int(row["FGM"]), "fga": int(row["FGA"]),
            "fg3m": int(row["FG3M"]), "fg3a": int(row["FG3A"]),
            "ftm": int(row["FTM"]), "fta": int(row["FTA"]),
        })
    for pid, games in by_player.items():
        games.sort(key=lambda g: (g["date"], g["game_id"]))  # deterministic tie-break, see docstring

    payload = {
        "season": season,
        "source": "leaguegamelog(player_or_team_abbreviation=P)",
        "schema_version": GAME_LOG_CACHE_VERSION,
        "provenance": "nba_api.stats.endpoints.leaguegamelog",
        "players": by_player,
    }
    _atomic_write(cache_path, payload)
    print(f"Cached real per-game box scores for {len(by_player)} players -> {cache_path}")
    return payload


def load_player_game_log(season: str) -> Dict[str, List[dict]]:
    """{player_id_str: [ {game_id, date, fgm, fga, fg3m, fg3a, ftm, fta}, ... ]} already sorted by
    (date, game_id), or {} if not cached / before the real floor / genuinely empty. A player_id
    absent means real MISSING evidence (never played, or not yet fetched), never a fabricated
    empty list masquerading as 'played zero games'."""
    cache_path = _game_log_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_player_game_log_range(seasons: List[str], force: bool = False) -> Dict[str, Optional[dict]]:
    """Resumable across seasons -- same convention as shot_zone_ingestion's own range builder."""
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_player_game_log(season, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
