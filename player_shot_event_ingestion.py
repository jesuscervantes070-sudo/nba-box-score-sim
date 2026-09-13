"""
Pregame-Safe Scoring Truth phase (part 2) -- per-shot historical evidence ingestion.

============================ SOURCE VERIFIED DIRECTLY ============================
`nba_api.stats.endpoints.shotchartdetail.ShotChartDetail` -- checked directly (real 2023-24
Stephen Curry call, then a real league-wide probe) before writing any code, not assumed:

Real returned columns (one row per real shot attempt): `GRID_TYPE`, `GAME_ID`, `GAME_EVENT_ID`,
`PLAYER_ID`, `PLAYER_NAME`, `TEAM_ID`, `TEAM_NAME`, `PERIOD`, `MINUTES_REMAINING`,
`SECONDS_REMAINING`, `EVENT_TYPE` ("Made Shot"/"Missed Shot"), `ACTION_TYPE` (free-text, e.g.
"Running Pull-Up Jump Shot"), `SHOT_TYPE` ("2PT Field Goal"/"3PT Field Goal"), `SHOT_ZONE_BASIC`,
`SHOT_ZONE_AREA`, `SHOT_ZONE_RANGE`, `SHOT_DISTANCE`, `LOC_X`, `LOC_Y`, `SHOT_ATTEMPTED_FLAG`,
`SHOT_MADE_FLAG`, `GAME_DATE` (real "YYYYMMDD" string), `HTM`/`VTM` (home/visitor team
abbreviation). Confirmed `SHOT_ZONE_BASIC`'s real values (`Restricted Area`, `In The Paint
(Non-RA)`, `Mid-Range`, `Left Corner 3`, `Right Corner 3`, `Above the Break 3`, `Backcourt`) are
the EXACT SAME 7 real NBA.com zone names `shot_zone_ingestion.py`'s own `ZONES` tuple already uses
(that endpoint's season-aggregate zone system and this per-shot endpoint's own zone system are
literally the same taxonomy) -- so this module classifies shots using `SHOT_ZONE_BASIC` directly,
never a distance/action-type heuristic, for exact compatibility with the already-existing
season-level estimator's own definitions (see module docstring section below for the full
reconciliation).

============================ CALL-BUDGET INVESTIGATION (real, before committing to a design) ============================
- `player_id=<real id>, team_id=0` (one real player, one season): works, but confirms the ~500+
  calls/season Phase 5 estimate for a full-roster per-player strategy (real 2023-24 has ~570
  rostered players).
- `player_id=0, team_id=0` (ALL players, ONE call, no month/date filter): returns exactly
  **102,400 rows** for 2023-24 -- a hard, silent SERVER-SIDE ROW CAP (confirmed: real total 2023-24
  league FGA is 218,700, and the truncated response's own games only span 2023-10-24 through
  2024-01-17, 575 of the real 1230 games). **This single "one call for everything" shortcut is
  UNRELIABLE and was rejected** -- it would silently drop the second half of every season.
- `player_id=0, team_id=0, month=<1..10>` (ALL players, ONE call per REAL calendar month of the
  season): real, verified working strategy. Real 2023-24 test: months 1-7 returned non-empty data
  (9,693 / 38,928 / 37,195 / 40,931 / 31,176 / 40,375 / 20,402 rows respectively, well under the
  102,400 cap in every single month), months 8-10 returned 0 rows (season already over) --
  **TOTAL 218,700 rows, 1,230 unique real GAME_IDs** -- an EXACT match to both the real total
  league FGA already verified in an earlier phase and the real full 82-game x 30-team schedule.
  **This is the chosen strategy: ~10 calls/season (7 non-empty + a few cheap empty probes),
  instead of ~500+.**

Call budget at this rate: **~10 calls/season** (a handful of ~2-4s non-empty calls + a few ~0.5s
empty tail-month probes) -> **~50 calls / 5 seasons**, **~100 calls / 10 seasons**. Real measured
payload: ~200-220k rows/season (~15-25MB of raw JSON per season, comparable in kind to this
project's existing per-season tracking caches, not a new order of magnitude).

============================ WHAT THIS PRESERVES FOR LATER USE ============================
Every real returned field is kept in the normalized cache (not thrown away just because V1's own
classifier only reads `SHOT_ZONE_BASIC`/`SHOT_MADE_FLAG`) -- `action_type`, `distance`, `loc_x`/
`loc_y`, `period`/clock remaining are all preserved raw, per this phase's own explicit instruction,
for a future phase that might need them (e.g. a real drive-origin heuristic, corner-vs-above-break
3 splits, or shot-quality-by-distance work).
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from data_source import _season_cache_dir

SHOT_EVENT_CACHE_VERSION = 1
SHOT_EVENT_FIRST_SEASON = "1996-97"  # same real per-player-endpoint floor as every other source here

# Regular-season months always fall in this real range for the `month` parameter (NBA's own
# season-relative month index -- confirmed directly: month=1 was real October 2023, months 8-10
# returned genuinely empty real responses for 2023-24, i.e. safely past the real season's end).
# A defensive, slightly generous range -- extra empty-month probes cost ~0.5s each, negligible.
_MONTH_RANGE = range(1, 11)


def _shot_event_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_shot_events.json"


def _fmt_date(raw: str) -> str:
    """'20231103' -> '2023-11-03' -- matches player_game_log_ingestion.py's own real GAME_DATE
    format exactly, so the two sources' dates are directly comparable/sortable without a second
    date-parsing convention anywhere in this project."""
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def fetch_shot_events_for_month(season: str, month: int, retries: int = 4, timeout: int = 30):
    """One real league-wide call for one real calendar month of `season`. Returns an empty
    DataFrame (never raises) for a month genuinely outside the season -- confirmed real, not
    assumed -- so callers can safely probe the full `_MONTH_RANGE` every time."""
    from nba_api.stats.endpoints import shotchartdetail
    last_err = None
    for attempt in range(retries):
        try:
            return shotchartdetail.ShotChartDetail(
                team_id=0, player_id=0, season_nullable=season, season_type_all_star="Regular Season",
                context_measure_simple="FGA", month=month, timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"shotchartdetail({season}, month={month}) failed after {retries} attempts: {last_err}")


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_and_cache_shot_events(season: str, force: bool = False) -> Optional[dict]:
    """Cache-first, ~10-call-per-season ingestion (see module docstring's call-budget
    investigation). Each player's shots are stored ALREADY sorted by (date, game_id,
    game_event_id) -- game_event_id is a real, stable within-game event order, a strictly better
    same-date/same-game tie-break than game_id alone (see player_game_log_ingestion.py's own
    documented same-date limitation, closed here for shots specifically)."""
    cache_path = _shot_event_cache_path(season)
    if cache_path.exists() and not force:
        print(f"Shot-event cache already exists at {cache_path}. Use force=True to update.")
        with open(cache_path) as f:
            return json.load(f)

    if season < SHOT_EVENT_FIRST_SEASON:
        print(f"{season} is before the real shot-chart floor ({SHOT_EVENT_FIRST_SEASON}) -- nothing cached.")
        return None

    print(f"Fetching {season} real league-wide shot events (shotchartdetail, {len(_MONTH_RANGE)} month-calls)...")
    by_player: Dict[str, List[dict]] = {}
    total_rows = 0
    game_ids = set()
    for month in _MONTH_RANGE:
        df = fetch_shot_events_for_month(season, month)
        if df is None or len(df) == 0:
            continue
        total_rows += len(df)
        for _, row in df.iterrows():
            pid = str(int(row["PLAYER_ID"]))
            game_ids.add(row["GAME_ID"])
            by_player.setdefault(pid, []).append({
                "game_id": row["GAME_ID"],
                "game_event_id": int(row["GAME_EVENT_ID"]),
                "date": _fmt_date(row["GAME_DATE"]),
                "made": bool(row["SHOT_MADE_FLAG"]),
                "shot_type": row["SHOT_TYPE"],          # "2PT Field Goal" / "3PT Field Goal"
                "zone_basic": row["SHOT_ZONE_BASIC"],   # the classifier field -- see module docstring
                "zone_area": row["SHOT_ZONE_AREA"],
                "zone_range": row["SHOT_ZONE_RANGE"],
                "distance": int(row["SHOT_DISTANCE"]),
                "action_type": row["ACTION_TYPE"],      # preserved raw, not used by the V1 classifier
                "loc_x": int(row["LOC_X"]), "loc_y": int(row["LOC_Y"]),
            })

    if total_rows == 0:
        print(f"{season} returned 0 shot-chart rows across every real month probed -- nothing cached.")
        return None

    for pid, events in by_player.items():
        events.sort(key=lambda e: (e["date"], e["game_id"], e["game_event_id"]))

    payload = {
        "season": season,
        "source": "shotchartdetail(team_id=0, player_id=0, month=1..10)",
        "schema_version": SHOT_EVENT_CACHE_VERSION,
        "provenance": "nba_api.stats.endpoints.shotchartdetail",
        "total_shot_events": total_rows,
        "total_games": len(game_ids),
        "players": by_player,
    }
    _atomic_write(cache_path, payload)
    print(f"Cached {total_rows} real shot events for {len(by_player)} players across {len(game_ids)} games -> {cache_path}")
    return payload


def load_shot_events(season: str) -> Dict[str, List[dict]]:
    """{player_id_str: [ {game_id, game_event_id, date, made, shot_type, zone_basic, zone_area,
    zone_range, distance, action_type, loc_x, loc_y}, ... ]}, already sorted by (date, game_id,
    game_event_id), or {} if not cached / before the real floor / genuinely empty."""
    cache_path = _shot_event_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_shot_events_range(seasons: List[str], force: bool = False) -> Dict[str, Optional[dict]]:
    """Resumable across seasons -- same convention as every other `*_ingestion.py` range builder
    in this project. A real API failure on one season never loses another season's cache."""
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_shot_events(season, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
