"""
Phase 5 -- Two-Point Shot-Zone Ability Foundation: ingestion.

Entirely offline/parallel, same discipline as the Phase 4A/4B ball-security
files -- imports nothing from and is imported by nothing in
game_engine.py/season.py/awards.py/models.py/transactions.py.

============================ SOURCE VERIFIED DIRECTLY ============================
`nba_api.stats.endpoints.leaguedashplayershotlocations.LeagueDashPlayerShotLocations`
(default `distance_range="By Zone"`) -- checked directly, not trusted from any
outside claim:

- Returns ONE real MultiIndex-column DataFrame, one row per player-season.
  Top level = zone name, second level = stat. Confirmed real zones:
  `Restricted Area`, `In The Paint (Non-RA)`, `Mid-Range`, `Left Corner 3`,
  `Right Corner 3`, `Above the Break 3`, `Backcourt`, and a redundant combined
  `Corner 3` (= Left + Right Corner 3). Each zone carries real `FGM`, `FGA`,
  `FG_PCT` columns -- exactly the expected shape, confirmed by direct
  inspection, not assumed.
- Real season floor: checked 1994-95 and 1995-96 directly -- BOTH return a
  genuinely EMPTY (0-row) DataFrame, not partial or malformed data. 1996-97
  is the first season with real rows (441 players) -- same real floor as
  every other per-player endpoint this codebase already uses.
- Checked 1996-97, 2001-02, 2013-14, 2025-26 (current/latest cached) --
  all return real, populated data. No partial/truncated response found in
  any tested season.
- Real per-call latency: 0.15s (already-warm/small season) to ~2.8s: no
  observed timeouts or rate-limit failures across 9 real test calls in this
  phase -- much lighter than `playbyplayv3` (one call per SEASON here, not
  per game), so no retry/backoff loop was found necessary in practice, but
  one is still included defensively (same shape as other fetchers in this
  codebase).
- Coverage check (real shot-location total FGA vs. real box-score total FGA,
  reconstructed from `loader.load_teams` + `player_advanced.json`'s real
  `gp`): ratio 1.01-1.03 across 1996-97/2013-14/2023-24 -- consistently
  *slightly above* 1.0 (an artifact of box-score per-game-average rounding
  in the reconstruction, not a real shot-location gap) in EVERY era tested,
  including the earliest season. No missing early-season coverage found.
- `PBP Stats` (the `AtRimFGM`/`AtRimAssistedFGM`/etc. fields Gemini research
  suggested) is a THIRD-PARTY community site (pbpstats.com), NOT part of the
  installed `nba_api` package -- confirmed by searching every endpoint name
  in `nba_api.stats.endpoints` for anything resembling it: nothing exists.
  The only first-party path to assisted/unassisted shot detail is
  `ShotChartDetail` (ONE CALL PER PLAYER PER SEASON, ~500+ calls/season --
  a real, much larger cost than this file's one-call-per-season base
  pipeline) which itself has NO assisted/unassisted flag in its own real
  columns either (checked directly: `SHOT_ZONE_BASIC`, `SHOT_MADE_FLAG`,
  `ACTION_TYPE`, no `AST`-type field) -- it would require cross-referencing
  each make against `playbyplayv3`'s assist events, a real, separate,
  expensive enrichment. NOT built this phase -- explicitly optional/deferred,
  does not block the base shot-zone pipeline (see task's own instruction).

============================ 1996-97 / SHORTENED-3PT-LINE INVESTIGATION ============================
The real NBA 3PT line was shortened (flat 22' all around) for 1994-95
through 1996-97, then reverted to the longer distance (23'9" top, 22'
corners unchanged) starting 1997-98. Measured directly, real zone FGA
shares (of all real zone attempts):

| Season | Restricted Area | Paint Non-RA | Mid-Range | Corner 3 | Above-Break 3 |
|---|---|---|---|---|---|
| 1996-97 (short line) | 37.4% | 11.9% | 39.9% | 3.1% | 7.6% |
| 1997-98 (line reverted) | 33.3% | 14.1% | 36.8% | 2.9% | 12.7% |

The 3PT-zone shift (Above-Break 3 share nearly doubling) is real and
directly tied to the 3PT-line-length era, but Restricted Area / Paint Non-RA
/ Mid-Range -- the three zones THIS phase's attributes actually use -- move
gradually, not with an inverted or discontinuous jump that would indicate
shots crossing the Mid-Range/3PT boundary due to the shortened line (if that
were happening, Mid-Range share would be LOWER, not HIGHER, in the
shortened-line season -- the opposite of what's measured). No explicit era
correction is applied for `rim_finishing`/`floater_short_mid`/`midrange` as
a result -- the real, measured discontinuity is confined to the 3PT zones,
which are out of scope for this phase. This is a MEASURED conclusion, not an
assumed one -- a future 3PT-attribute phase should re-run this exact check
before trusting 1994-95-1996-97 3PT-zone data.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from data_source import _season_cache_dir

SHOT_ZONE_CACHE_VERSION = 1

# Real season floor -- checked directly (1995-96 returns 0 rows, 1996-97
# returns 441). Matches this codebase's overall real per-player-endpoint floor.
SHOT_ZONE_FIRST_SEASON = "1996-97"

# Real zone names (top-level MultiIndex columns), verified directly.
# "Corner 3" is a real but REDUNDANT combined Left+Right Corner 3 column --
# kept for completeness (already free, no extra API cost) but not used by
# any of this phase's three target attributes.
ZONES = (
    "Restricted Area", "In The Paint (Non-RA)", "Mid-Range",
    "Left Corner 3", "Right Corner 3", "Corner 3", "Above the Break 3", "Backcourt",
)

# Our field-name prefix for each real zone -- only these three back this
# phase's target attributes; the rest are preserved as raw evidence for
# possible future (3PT-attribute) use, per "preserve raw counts" guidance.
ZONE_FIELD_PREFIX = {
    "Restricted Area": "restricted_area",
    "In The Paint (Non-RA)": "paint_non_ra",
    "Mid-Range": "midrange",
    "Left Corner 3": "left_corner3",
    "Right Corner 3": "right_corner3",
    "Corner 3": "corner3_combined",
    "Above the Break 3": "above_break3",
    "Backcourt": "backcourt",
}


def _clean_zero(value) -> float:
    """None or real NaN (see build_and_cache_shot_zones's docstring note
    on the confirmed low-volume-player NaN quirk) both mean a true zero
    here -- never propagated as NaN into the cache (NaN silently poisons
    every downstream weighted-average calculation with no error)."""
    if value is None:
        return 0.0
    try:
        if value != value:  # real, cheap NaN check (NaN != NaN is True) -- no extra import needed
            return 0.0
    except TypeError:
        pass
    return float(value)


def _shot_zone_cache_path(season: str, season_type: str = "Regular Season") -> Path:
    suffix = "" if season_type == "Regular Season" else "_playoffs"
    return _season_cache_dir(season) / f"player_shot_zones{suffix}.json"


def fetch_shot_zone_data(season: str, season_type: str = "Regular Season", retries: int = 4, timeout: int = 30):
    """One real league-wide call -- NOT per-game, NOT per-player. Returns
    the raw MultiIndex DataFrame, or an empty one for a season before
    SHOT_ZONE_FIRST_SEASON (checked directly, a real floor)."""
    from nba_api.stats.endpoints import leaguedashplayershotlocations
    last_err = None
    for attempt in range(retries):
        try:
            return leaguedashplayershotlocations.LeagueDashPlayerShotLocations(
                season=season, season_type_all_star=season_type, timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"leaguedashplayershotlocations({season}, {season_type}) failed after {retries} attempts: {last_err}")


def _atomic_write(path: Path, payload: dict) -> None:
    """Same write-to-temp-then-rename discipline as turnover_ingestion.py --
    never leaves a half-written cache file."""
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_and_cache_shot_zones(season: str, season_type: str = "Regular Season", force: bool = False) -> Optional[dict]:
    """
    Cache-first, one-call-per-season ingestion. Returns None (writes
    nothing) for a season before SHOT_ZONE_FIRST_SEASON or one that comes
    back genuinely empty -- a real "no data exists" case, kept
    distinguishable from a season that was fetched and legitimately has
    zero-attempt players (which DOES get a cache file, with explicit
    fgm=0/fga=0 rows -- see module docstring's "missing vs true zero"
    requirement).
    """
    cache_path = _shot_zone_cache_path(season, season_type)
    if cache_path.exists() and not force:
        print(f"Shot-zone cache already exists at {cache_path}. Use force=True to update.")
        with open(cache_path) as f:
            return json.load(f)

    if season < SHOT_ZONE_FIRST_SEASON:
        print(f"{season} is before the real shot-location floor ({SHOT_ZONE_FIRST_SEASON}) -- no data exists, nothing cached.")
        return None

    print(f"Fetching {season} real per-player shot-zone data ({season_type})...")
    df = fetch_shot_zone_data(season, season_type)
    if df is None or len(df) == 0:
        print(f"{season} ({season_type}) returned 0 rows -- genuinely no data (not a partial/error response), nothing cached.")
        return None

    # Guard against a caller passing in an already-flattened DataFrame
    # (e.g. a reused/cached object in a test double) -- flattening twice
    # would silently corrupt column names, since the second pass would
    # iterate over each already-flat string's individual CHARACTERS.
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = ["_".join([c for c in col if c]) for col in df.columns]

    players = {}
    for _, row in df.iterrows():
        pid = str(int(row["PLAYER_ID"]))
        entry = {"player_name": row["PLAYER_NAME"]}
        for zone in ZONES:
            prefix = ZONE_FIELD_PREFIX[zone]
            fgm = row.get(f"{zone}_FGM")
            fga = row.get(f"{zone}_FGA")
            # Real API already reports 0/0 for a zone a player never
            # attempted -- preserved as literal 0, not None (this player
            # WAS in the response; a true zero is real evidence, not
            # missing evidence -- see module docstring). CONFIRMED, real
            # quirk: for a small number of extremely low-volume players
            # (112 occurrences across all 30 real cached seasons, always
            # a fringe/low-minutes player -- e.g. Bruce Bowen's real
            # 1996-97 Mid-Range row), the raw API returns NaN instead of
            # 0 for a zone with genuinely zero attempts. Treated as a
            # true zero (0.0/0.0), not missing evidence -- every other
            # zone on the SAME player-season row has real, non-NaN
            # values, confirming the row itself is real and present, just
            # this one zone's 0-count rendered as NaN rather than 0.
            entry[f"{prefix}_fgm"] = _clean_zero(fgm)
            entry[f"{prefix}_fga"] = _clean_zero(fga)
        players[pid] = entry

    payload = {
        "season": season,
        "season_type": season_type,
        "source": "leaguedashplayershotlocations(By Zone)",
        "schema_version": SHOT_ZONE_CACHE_VERSION,
        "provenance": "nba_api.stats.endpoints.leaguedashplayershotlocations",
        "players": players,
    }
    _atomic_write(cache_path, payload)
    print(f"Cached real shot-zone data for {len(players)} players -> {cache_path}")
    return payload


def load_shot_zones(season: str, season_type: str = "Regular Season") -> Dict[str, dict]:
    """
    {player_id_str: {...}} for `season`, or {} if not cached / before the
    real floor / genuinely empty that season -- NEVER a silent fallback.
    A player_id absent from this dict for a cached season is real MISSING
    evidence (they weren't in the API response at all -- extremely rare
    per this phase's own coverage check, but possible), distinguishable
    from a present player_id with fgm=0/fga=0 (real true zero).
    """
    cache_path = _shot_zone_cache_path(season, season_type)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_shot_zones_range(seasons: List[str], season_type: str = "Regular Season", force: bool = False) -> Dict[str, Optional[dict]]:
    """
    Resumable across a list of seasons: cache-first per season (skips any
    season already cached unless force=True), and a real API failure on
    one season does not lose progress already cached for the others --
    each season's cache file is independent and atomically written.
    """
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_shot_zones(season, season_type, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
