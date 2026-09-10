"""
Phase 8 -- Playmaking Vision: ingestion. Entirely offline/parallel.
Reuses the existing cache/<season>/ convention only; writes its own new
cache file. Not imported by game_engine.py/season.py/awards.py/models.py/
transactions.py.

============================ SOURCE VERIFIED DIRECTLY ============================
`nba_api.stats.endpoints.leaguedashptstats`, `pt_measure_type="Passing"`,
`player_or_team="Player"`, `per_mode_simple="Totals"` -- same real
endpoint FAMILY Phase 4B's `handling_exposure.py` already uses for
`Possessions`/`Drives` (this is a different `pt_measure_type` on the SAME
endpoint, not a new one). Real, verified columns: `PLAYER_ID`,
`PLAYER_NAME`, `GP`, `MIN`, `PASSES_MADE`, `PASSES_RECEIVED`, `AST`,
`FT_AST`, `SECONDARY_AST`, `POTENTIAL_AST`, `AST_PTS_CREATED`, `AST_ADJ`,
`AST_TO_PASS_PCT`, `AST_TO_PASS_PCT_ADJ`.

Real floor: checked directly, 2012-13 returns 0 rows, 2013-14 returns
real populated data -- same real camera-tracking floor as every other
`leaguedashpt*` endpoint this codebase already uses (Phase 4B/5/7).
Cheap: one real call per season, same as `handling_exposure.py`/
`shot_zone_ingestion.py`/`rim_protection_ingestion.py` -- a full
tracking-era historical backfill is genuinely trivial.

`POTENTIAL_AST` = real, NBA-tracked count of passes that WOULD have been
assists had the recipient made the shot -- the real, first-party field
this phase's central candidate family is built from (not invented).
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from data_source import _season_cache_dir

PASSING_TRACKING_CACHE_VERSION = 1
PASSING_TRACKING_FIRST_SEASON = "2013-14"  # real camera-tracking floor, checked directly


def _passing_tracking_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_passing_tracking.json"


def fetch_passing_tracking_data(season: str, retries: int = 4, timeout: int = 45):
    from nba_api.stats.endpoints import leaguedashptstats
    last_err = None
    for attempt in range(retries):
        try:
            return leaguedashptstats.LeagueDashPtStats(
                season=season, pt_measure_type="Passing", player_or_team="Player",
                per_mode_simple="Totals", timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"leaguedashptstats(Passing, {season}) failed after {retries} attempts: {last_err}")


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_and_cache_passing_tracking(season: str, force: bool = False) -> Optional[dict]:
    cache_path = _passing_tracking_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)
    if season < PASSING_TRACKING_FIRST_SEASON:
        print(f"{season} is before the real passing-tracking floor ({PASSING_TRACKING_FIRST_SEASON}) -- nothing cached.")
        return None
    print(f"Fetching {season} real player passing-tracking data...")
    df = fetch_passing_tracking_data(season)
    if df is None or len(df) == 0:
        print(f"{season} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    players = {}
    for _, row in df.iterrows():
        pid = str(int(row["PLAYER_ID"]))
        players[pid] = {
            "player_name": row["PLAYER_NAME"],
            "gp": int(row["GP"]), "min": float(row["MIN"]),
            "passes_made": float(row["PASSES_MADE"]), "passes_received": float(row["PASSES_RECEIVED"]),
            "ast": float(row["AST"]), "ft_ast": float(row["FT_AST"]),
            "secondary_ast": float(row["SECONDARY_AST"]), "potential_ast": float(row["POTENTIAL_AST"]),
            "ast_pts_created": float(row["AST_PTS_CREATED"]), "ast_adj": float(row["AST_ADJ"]),
            "ast_to_pass_pct": float(row["AST_TO_PASS_PCT"]) if row.get("AST_TO_PASS_PCT") is not None else None,
        }
    payload = {"season": season, "source": "leaguedashptstats(Passing)",
               "schema_version": PASSING_TRACKING_CACHE_VERSION,
               "provenance": "nba_api.stats.endpoints.leaguedashptstats", "players": players}
    _atomic_write(cache_path, payload)
    print(f"Cached real passing-tracking data for {len(players)} players -> {cache_path}")
    return payload


def load_passing_tracking(season: str) -> Dict[str, dict]:
    cache_path = _passing_tracking_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_passing_tracking_range(seasons, force: bool = False) -> dict:
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_passing_tracking(season, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
