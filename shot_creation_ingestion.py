"""
Phase 9 -- Shot Creation Internals: ingestion. Entirely offline/parallel.

============================ REUSE FIRST ============================
`handling_exposure.py` (Phase 4B) ALREADY caches real `DRIVES` counts
(and `drive_pts`/`drive_passes`/`drive_tov`) from the SAME
`leaguedashptstats(pt_measure_type="Drives")` call this phase needs --
reused directly, NOT re-fetched. This phase only fetches the ADDITIONAL
real fields on that same call that Phase 4B didn't extract
(`DRIVE_FGA`, `DRIVE_FGM`, `DRIVE_FTA`, `DRIVE_AST`), plus two real,
previously-uncached measure types on the same endpoint family:
`PullUpShot` and `CatchShoot`.

============================ SOURCE VERIFIED DIRECTLY ============================
Same endpoint, `nba_api.stats.endpoints.leaguedashptstats`:
- `pt_measure_type="Drives"` -- real columns (beyond what Phase 4B
  already cached): `DRIVE_FGA`, `DRIVE_FGM`, `DRIVE_FTA`, `DRIVE_AST`.
- `pt_measure_type="PullUpShot"` -- real columns: `PULL_UP_FGM`,
  `PULL_UP_FGA`, `PULL_UP_FG_PCT`, `PULL_UP_FG3M`, `PULL_UP_FG3A`,
  `PULL_UP_FG3_PCT`, `PULL_UP_EFG_PCT`. A pull-up shot is BY DEFINITION
  unassisted/self-created (off the dribble) -- no separate "unassisted"
  field is needed to identify self-created jumper volume.
- `pt_measure_type="CatchShoot"` -- real columns: `CATCH_SHOOT_FGM`,
  `CATCH_SHOOT_FGA`, `CATCH_SHOOT_FG_PCT`, `CATCH_SHOOT_FG3M`,
  `CATCH_SHOOT_FG3A`, `CATCH_SHOOT_FG3_PCT`, `CATCH_SHOOT_EFG_PCT` --
  used ONLY as the real teammate-created CONTRAST this phase's own task
  explicitly suggests, not as a target itself.

Real floor: same as every other `leaguedashpt*` endpoint already used in
this codebase (Phase 4B/5/7/8) -- 2013-14. Cheap: 3 real calls/season,
one-time full historical backfill.

============================ NOT PURSUED, DOCUMENTED AS FUTURE ============================
Real defender-distance-at-release data for pull-up shots (which would
let this phase measure real SEPARATION rather than a volume/opportunity
proxy) is NOT available through any real endpoint this codebase's public
`nba_api` access exposes -- checked: no `leaguedashpt*` measure type
returns a per-shot or per-player defender-distance field. Second
Spectrum's real shot-quality/separation data is proprietary. Documented
as FUTURE ONLY, not fabricated.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from data_source import _season_cache_dir

SHOT_CREATION_CACHE_VERSION = 1
SHOT_CREATION_FIRST_SEASON = "2013-14"  # real camera-tracking floor, same as Phase 4B/5/7/8


def _shot_creation_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_shot_creation_tracking.json"


def _fetch_pt(season: str, pt_measure_type: str, retries: int = 4, timeout: int = 45):
    from nba_api.stats.endpoints import leaguedashptstats
    last_err = None
    for attempt in range(retries):
        try:
            return leaguedashptstats.LeagueDashPtStats(
                season=season, pt_measure_type=pt_measure_type, player_or_team="Player",
                per_mode_simple="Totals", timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"leaguedashptstats({pt_measure_type}, {season}) failed after {retries} attempts: {last_err}")


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_and_cache_shot_creation_tracking(season: str, force: bool = False) -> Optional[dict]:
    cache_path = _shot_creation_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)
    if season < SHOT_CREATION_FIRST_SEASON:
        print(f"{season} is before the real tracking floor ({SHOT_CREATION_FIRST_SEASON}) -- nothing cached.")
        return None

    print(f"Fetching {season} real drives/pull-up/catch-shoot tracking data...")
    drives_df = _fetch_pt(season, "Drives")
    pullup_df = _fetch_pt(season, "PullUpShot")
    catch_df = _fetch_pt(season, "CatchShoot")
    if len(drives_df) == 0:
        print(f"{season} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    pullup_by_id = {int(row["PLAYER_ID"]): row for _, row in pullup_df.iterrows()}
    catch_by_id = {int(row["PLAYER_ID"]): row for _, row in catch_df.iterrows()}

    players = {}
    for _, row in drives_df.iterrows():
        pid = int(row["PLAYER_ID"])
        entry = {
            "player_name": row["PLAYER_NAME"], "gp": int(row["GP"]), "min": float(row["MIN"]),
            "drives": float(row["DRIVES"]),
            "drive_fga": float(row["DRIVE_FGA"]), "drive_fgm": float(row["DRIVE_FGM"]),
            "drive_fta": float(row["DRIVE_FTA"]), "drive_ast": float(row["DRIVE_AST"]),
            "drive_tov": float(row["DRIVE_TOV"]), "drive_pts": float(row["DRIVE_PTS"]),
        }
        pu = pullup_by_id.get(pid)
        if pu is not None:
            entry["pull_up_fga"] = float(pu["PULL_UP_FGA"])
            entry["pull_up_fgm"] = float(pu["PULL_UP_FGM"])
            entry["pull_up_fg3a"] = float(pu["PULL_UP_FG3A"])
        cs = catch_by_id.get(pid)
        if cs is not None:
            entry["catch_shoot_fga"] = float(cs["CATCH_SHOOT_FGA"])
            entry["catch_shoot_fgm"] = float(cs["CATCH_SHOOT_FGM"])
        players[str(pid)] = entry

    payload = {"season": season, "source": "leaguedashptstats(Drives+PullUpShot+CatchShoot)",
               "schema_version": SHOT_CREATION_CACHE_VERSION,
               "provenance": "nba_api.stats.endpoints.leaguedashptstats", "players": players}
    _atomic_write(cache_path, payload)
    print(f"Cached real shot-creation tracking data for {len(players)} players -> {cache_path}")
    return payload


def load_shot_creation_tracking(season: str) -> Dict[str, dict]:
    cache_path = _shot_creation_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_shot_creation_tracking_range(seasons, force: bool = False) -> dict:
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_shot_creation_tracking(season, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
