"""
Availability + Expected Minutes + Rotations V1 -- real per-game minutes/team ingestion.

============================ SOURCE VERIFIED DIRECTLY ============================
Same real, already-used endpoint as `player_game_log_ingestion.py`
(`nba_api.stats.endpoints.leaguegamelog`, `player_or_team_abbreviation="P"`) -- that module's own
cache only extracts shooting fields (FGM/FGA/FG3M/FG3A/FTM/FTA); it does NOT store `MIN` or
`TEAM_ID`, even though both are real columns in the same raw response (confirmed directly: the
column list in that module's own docstring already lists `TEAM_ID`, `MIN`). This is the same
"additive extraction from an already-fetched endpoint" pattern used for OREB_PCT/DREB_PCT
(`data_source.build_and_cache_player_rebound_splits`) and box-outs (`rebound_chance_ingestion.py`)
-- a SEPARATE cache file, `player_game_minutes.json`, id-keyed, real per-game `(game_id, date,
team_id, minutes)`. `player_game_log_ingestion.py` itself is not modified.

A player's ABSENCE from this per-game list for a `game_id` their team actually played (per
`schedule.json`, real, already cached) is the real signal used for DNP/inactive detection
throughout this phase -- `leaguegamelog` only ever returns a row for a player who recorded a real
stat line; a healthy scratch, coach's-decision rest, or injury are indistinguishable in this source
(same real limitation `data_source.fetch_player_absence_stints`'s own docstring already documents
for the parallel, untouched `injuries.py` track) -- documented honestly, never guessed at.

No START_POSITION/starter field exists in this endpoint's response (confirmed directly) -- true
official starter data would need a separate, per-game endpoint (`boxscoretraditionalv2`, ~1
call/game, ~1,230 calls/season) explicitly out of this phase's proportionate scope. This phase's
oracle/pregame starter signals are an honestly-labeled PROXY (top-5 real minutes among a team's
available players in a game) -- see `rotation_estimation.py`'s own module docstring.

Real floor: same as `player_game_log_ingestion.GAME_LOG_FIRST_SEASON` (1996-97) -- `MIN`/`TEAM_ID`
are present in every real season this endpoint covers.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from data_source import _season_cache_dir
from player_game_log_ingestion import GAME_LOG_FIRST_SEASON, fetch_player_game_log

GAME_MINUTES_CACHE_VERSION = 1


def _game_minutes_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_game_minutes.json"


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def _parse_minutes(raw) -> float:
    """`leaguegamelog`'s real MIN column is a plain numeric field for modern seasons (e.g. 34.0)
    -- confirmed directly, no "MM:SS" string parsing needed for this endpoint (unlike some
    box-score endpoints)."""
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def build_and_cache_game_minutes(season: str, force: bool = False) -> Optional[dict]:
    cache_path = _game_minutes_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)
    if season < GAME_LOG_FIRST_SEASON:
        print(f"{season} is before the real per-player game-log floor ({GAME_LOG_FIRST_SEASON}) -- nothing cached.")
        return None
    print(f"Fetching {season} real per-player per-game minutes/team data (leaguegamelog)...")
    df = fetch_player_game_log(season)
    if df is None or len(df) == 0:
        print(f"{season} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    by_player: Dict[str, List[dict]] = {}
    for _, row in df.iterrows():
        pid = str(int(row["PLAYER_ID"]))
        by_player.setdefault(pid, []).append({
            "game_id": row["GAME_ID"],
            "date": row["GAME_DATE"],
            "team_id": str(int(row["TEAM_ID"])),
            "minutes": _parse_minutes(row.get("MIN")),
        })
    for pid, games in by_player.items():
        games.sort(key=lambda g: (g["date"], g["game_id"]))

    payload = {
        "season": season,
        "source": "leaguegamelog(player_or_team_abbreviation=P)",
        "schema_version": GAME_MINUTES_CACHE_VERSION,
        "provenance": "nba_api.stats.endpoints.leaguegamelog",
        "players": by_player,
    }
    _atomic_write(cache_path, payload)
    print(f"Cached real per-game minutes/team data for {len(by_player)} players -> {cache_path}")
    return payload


def load_game_minutes(season: str) -> Dict[str, List[dict]]:
    cache_path = _game_minutes_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_game_minutes_range(seasons, force: bool = False) -> dict:
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_game_minutes(season, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
