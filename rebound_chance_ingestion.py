"""
Rebounding Truth V1 -- ingestion. Entirely offline/parallel. Reuses the
existing cache/<season>/ convention only; writes its own new cache
file. Not imported by game_engine.py/season.py/awards.py/models.py/
transactions.py.

============================ SOURCE VERIFIED DIRECTLY ============================
`nba_api.stats.endpoints.leaguedashptstats`, `pt_measure_type="Rebounding"`,
`player_or_team="Player"`, `per_mode_simple="Totals"` -- the SAME endpoint
family `passing_tracking_ingestion.py`/`handling_exposure.py` already use
(a different `pt_measure_type` on the same real endpoint, not a new
source). Real, verified columns (probed live, 2023-24, 560 rows):
`PLAYER_ID`, `PLAYER_NAME`, `GP`, `MIN`, `OREB`, `OREB_CONTEST`,
`OREB_UNCONTEST`, `OREB_CONTEST_PCT`, `OREB_CHANCES`, `OREB_CHANCE_PCT`,
`OREB_CHANCE_DEFER`, `OREB_CHANCE_PCT_ADJ`, `AVG_OREB_DIST` (+ the exact
same set of fields mirrored for DREB and combined REB).

This exact measure was ALREADY identified and spot-probed once before,
in Phase 19 (see `rebound_resolution.py`'s own module docstring and
`docs/PHASE19_REBOUND_RESOLUTION_REPORT.md` Sec. 4/8: "VERIFIED PUBLIC
-- not previously used anywhere in this repo; confirmed live this
phase", real 2023-24 numbers: n=394 players >=30 OREB chances, mean
OREB_CHANCE_PCT=42.2%, mean OREB_CONTEST_PCT=48.8%) -- but that phase
only ran a ONE-OFF diagnostic script and never persisted a cache or
built a reusable estimator on top of it. This module is the first real
ingestion/caching layer for that already-verified source.

`OREB_CHANCE_PCT` is confirmed (2023-24 spot check, A.J. Lawson:
OREB=14, OREB_CHANCES=35, OREB_CHANCE_PCT=0.4=14/35 exactly) to be a
plain ratio of two real, individually-tracked counts -- this module
stores the raw counts (`oreb`, `oreb_chances`, ...) rather than trusting
the API's own rounded percentage columns, so downstream shrinkage math
gets full precision.

Real floor: checked directly, 2012-13 returns 0 rows, 2013-14 returns
477 real rows -- the SAME real SportVU camera-tracking floor as every
other `leaguedashpt*` endpoint this codebase already uses.

CONSTRUCT NOTE (see rebounding_estimation.py's own module docstring for
the full audit): OREB_CHANCE_PCT/DREB_CHANCE_PCT are a genuinely
DIFFERENT, more individually-granular normalization than the existing
`player_ability_estimation.py` `offensive_rebounding`/`defensive_rebounding`
attributes' real OREB_PCT/DREB_PCT (a TEAM-CONTEXT share of rebounds
available while the player was on the floor). Both are real and
opportunity-normalized in their own way; this module's own real,
individually-tracked CHANCE data is the stronger, more preferred
denominator per this phase's own explicit ranking (player rebound
chances > contested/uncontested > spatially-eligible > team-miss
exposure), so it is used as the PRIMARY truth construct here -- see
`player_rebounding_truth.py` for the resulting engine-scale-compatibility
finding.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from data_source import _season_cache_dir

REBOUND_CHANCE_CACHE_VERSION = 1
REBOUND_CHANCE_FIRST_SEASON = "2013-14"  # real camera-tracking floor, checked directly


def _rebound_chance_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_rebound_chances.json"


def fetch_rebound_chance_data(season: str, retries: int = 4, timeout: int = 45):
    from nba_api.stats.endpoints import leaguedashptstats
    last_err = None
    for attempt in range(retries):
        try:
            return leaguedashptstats.LeagueDashPtStats(
                season=season, pt_measure_type="Rebounding", player_or_team="Player",
                per_mode_simple="Totals", timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"leaguedashptstats(Rebounding, {season}) failed after {retries} attempts: {last_err}")


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_and_cache_rebound_chances(season: str, force: bool = False) -> Optional[dict]:
    cache_path = _rebound_chance_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)
    if season < REBOUND_CHANCE_FIRST_SEASON:
        print(f"{season} is before the real rebound-chance tracking floor ({REBOUND_CHANCE_FIRST_SEASON}) -- nothing cached.")
        return None
    print(f"Fetching {season} real player rebound-chance tracking data...")
    df = fetch_rebound_chance_data(season)
    if df is None or len(df) == 0:
        print(f"{season} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    players = {}
    for _, row in df.iterrows():
        pid = str(int(row["PLAYER_ID"]))
        players[pid] = {
            "player_name": row["PLAYER_NAME"],
            "gp": int(row["GP"]), "min": float(row["MIN"]),
            "oreb": float(row["OREB"]), "oreb_chances": float(row["OREB_CHANCES"]),
            "oreb_contest": float(row["OREB_CONTEST"]), "oreb_uncontest": float(row["OREB_UNCONTEST"]),
            "avg_oreb_dist": float(row["AVG_OREB_DIST"]) if row.get("AVG_OREB_DIST") is not None else None,
            "dreb": float(row["DREB"]), "dreb_chances": float(row["DREB_CHANCES"]),
            "dreb_contest": float(row["DREB_CONTEST"]), "dreb_uncontest": float(row["DREB_UNCONTEST"]),
            "avg_dreb_dist": float(row["AVG_DREB_DIST"]) if row.get("AVG_DREB_DIST") is not None else None,
        }
    payload = {"season": season, "source": "leaguedashptstats(Rebounding)",
               "schema_version": REBOUND_CHANCE_CACHE_VERSION,
               "provenance": "nba_api.stats.endpoints.leaguedashptstats", "players": players}
    _atomic_write(cache_path, payload)
    print(f"Cached real rebound-chance data for {len(players)} players -> {cache_path}")
    return payload


def load_rebound_chances(season: str) -> Dict[str, dict]:
    cache_path = _rebound_chance_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_rebound_chances_range(seasons, force: bool = False) -> dict:
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_rebound_chances(season, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
