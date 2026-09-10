"""
Phase 4A -- Ball Security: real modern (camera-tracking) handling-exposure
data. Entirely offline/parallel like every other player-ability file --
imported by nothing in game_engine.py/season.py/awards.py/models.py.

============================ DATA INSPECTED FIRST ============================
Checked directly against the real API before writing anything:

    leaguedashptstats(pt_measure_type="Possessions", player_or_team="Player")
    -> real columns: PLAYER_ID, PLAYER_NAME, TEAM_ID, TEAM_ABBREVIATION, GP,
       W, L, MIN, POINTS, TOUCHES, FRONT_CT_TOUCHES, TIME_OF_POSS,
       AVG_SEC_PER_TOUCH, AVG_DRIB_PER_TOUCH, PTS_PER_TOUCH, ELBOW_TOUCHES,
       POST_TOUCHES, PAINT_TOUCHES, PTS_PER_ELBOW_TOUCH, PTS_PER_POST_TOUCH,
       PTS_PER_PAINT_TOUCH.
    leaguedashptstats(pt_measure_type="Drives", player_or_team="Player")
    -> real columns include DRIVES, DRIVE_TOV, DRIVE_TOV_PCT (the NBA's own
       drive-specific turnover rate -- a real, narrower handling-exposure
       slice, separate from the season-wide numbers above).

Confirmed real season floor (checked directly, not assumed): 2012-13 comes
back with 0 rows for both measure types; 2013-14 comes back with 482 rows.
This matches RIM_DEFENSE_FIRST_SEASON's floor already documented in
data_source.py (same league-wide SportVU rollout) -- no new floor invented.

TOUCHES * AVG_DRIB_PER_TOUCH is used below as an "estimated total dribbles"
figure -- NOT an invented coefficient: both factors are real fields the API
already returns for that player-season, this is a direct multiplication of
two real reported numbers, not a fitted/guessed formula.
"""
import json
import time
from pathlib import Path
from typing import Dict, Optional

from data_source import _season_cache_dir

HANDLING_EXPOSURE_CACHE_VERSION = 1

# Same real camera-tracking floor as data_source.RIM_DEFENSE_FIRST_SEASON --
# checked directly for THIS endpoint too (0 rows in 2012-13, 482 in 2013-14).
HANDLING_EXPOSURE_FIRST_SEASON = "2013-14"

# Real fields kept verbatim -- no invented/derived fields except the one
# explicitly-labeled ESTIMATED_TOTAL_DRIBBLES multiplication (see docstring).
_POSSESSIONS_FIELDS = (
    "TOUCHES", "FRONT_CT_TOUCHES", "TIME_OF_POSS", "AVG_SEC_PER_TOUCH",
    "AVG_DRIB_PER_TOUCH", "PAINT_TOUCHES", "POST_TOUCHES", "ELBOW_TOUCHES",
)
_DRIVES_FIELDS = ("DRIVES", "DRIVE_TOV", "DRIVE_TOV_PCT", "DRIVE_PTS", "DRIVE_PASSES")


def _handling_exposure_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_handling_exposure.json"


def _fetch_ptstats(season: str, pt_measure_type: str, retries: int = 4, timeout: int = 40):
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


def build_and_cache_handling_exposure(season: str, force: bool = False) -> Optional[dict]:
    """
    Real per-player TOUCHES/TIME_OF_POSS/AVG_DRIB_PER_TOUCH/DRIVES etc for
    `season`. Returns None (and writes nothing) for a season before
    HANDLING_EXPOSURE_FIRST_SEASON -- a real camera-tracking floor, not a
    bug to route around with an invented pre-tracking value. See
    historical_handle_proxy.py for the pre-tracking-era proxy instead.
    """
    if season < HANDLING_EXPOSURE_FIRST_SEASON:
        print(f"{season} is before the real camera-tracking floor ({HANDLING_EXPOSURE_FIRST_SEASON}) -- no handling-exposure data exists.")
        return None

    cache_path = _handling_exposure_cache_path(season)
    if cache_path.exists() and not force:
        print(f"Handling-exposure cache already exists at {cache_path}. Use force=True to update.")
        with open(cache_path) as f:
            return json.load(f)

    print(f"Fetching {season} real player touches/dribbles/time-of-possession (Possessions)...")
    poss_df = _fetch_ptstats(season, "Possessions")
    print(f"Fetching {season} real player drives (Drives)...")
    drives_df = _fetch_ptstats(season, "Drives")

    drives_by_id = {int(row["PLAYER_ID"]): row for _, row in drives_df.iterrows()}

    players = {}
    for _, row in poss_df.iterrows():
        pid = int(row["PLAYER_ID"])
        entry = {"player_name": row["PLAYER_NAME"], "gp": int(row["GP"]), "min": float(row["MIN"])}
        for field in _POSSESSIONS_FIELDS:
            entry[field.lower()] = float(row[field])
        # Real multiplication of two real reported fields -- see module docstring.
        entry["estimated_total_dribbles"] = entry["touches"] * entry["avg_drib_per_touch"]

        drive_row = drives_by_id.get(pid)
        if drive_row is not None:
            for field in _DRIVES_FIELDS:
                entry[field.lower()] = float(drive_row[field])
        players[str(pid)] = entry

    payload = {
        "season": season,
        "source": "leaguedashptstats(Possessions+Drives)",
        "cache_version": HANDLING_EXPOSURE_CACHE_VERSION,
        "players": players,
    }
    with open(cache_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Cached real handling-exposure data for {len(players)} players -> {cache_path}")
    return payload


def load_handling_exposure(season: str) -> Dict[str, dict]:
    """
    {player_id_str: {...}} for `season`, or {} if not cached / before the
    real tracking floor -- never a silent fallback or invented value.
    """
    cache_path = _handling_exposure_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]
