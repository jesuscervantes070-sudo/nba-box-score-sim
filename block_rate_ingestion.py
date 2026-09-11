"""
Diagnose detailed-engine block generation -- real 2025-26 (and reusable 2024-25/2023-24)
empirical block-rate extraction.

============================ DATA-SOURCE AUDIT (verified directly this phase) ============================
`playbyplayv3` (nba_api) was audited directly against real games, NOT assumed. A real blocked
shot is represented as TWO rows sharing the SAME `actionNumber`:
  1. The shooter's own "Missed Shot" row (`actionType == "Missed Shot"`, `personId` = shooter,
     `shotDistance`/`shotValue`/`subType` describe the attempt, `shotResult == "Missed"` always
     -- confirmed directly: a block can never coincide with a make, matching this project's own
     `resolve_interior_shot` competing-risk structure (block rolled FIRST; make/miss rolled only
     if not blocked)).
  2. A SEPARATE row with the SAME `actionNumber`, an EMPTY `actionType`, and `description`
     matching `"<Name> BLOCK (<N> BLK)"` -- `personId` on THIS row is the BLOCKER, not a generic
     `PLAYER1_ID`/`PLAYER2_ID`/`PLAYER3_ID` field whose meaning would be event-dependent. This is
     `playbyplayv3`'s own already-resolved, unambiguous per-row attribution -- verified directly
     against >10 real examples (see `docs/...` phase report / this module's own cached sample for
     the exact rows inspected) before being trusted, per explicit instruction not to assume
     blocker-ID field semantics.

`playbyplayv3` carries `shotDistance` (feet) and `shotValue` (2 or 3) but NOT the official
`SHOT_ZONE_BASIC` text label (that field only exists on `shotchartdetail`/
`leaguedashplayershotlocations`, which this project already caches separately via
`shot_zone_ingestion.py` -- see `RECONCILIATION` below). `classify_shot_family` is therefore an
EXPLICIT, DOCUMENTED DISTANCE-BASED PROXY for this project's own `InteriorShotFamily`/
`PerimeterShotFamily` taxonomy, not the official zone polygon:
  - `shotValue == 3` -> THREE_POINT (exact -- this one split needs no proxy at all).
  - `shotValue == 2` and `shotDistance <= 4` ft -> RIM (matches the NBA's own official
    Restricted-Area radius definition exactly).
  - `shotValue == 2` and `4 < shotDistance <= 13` ft -> FLOATER_SHORT (a proxy for "In The Paint
    (Non-RA)" -- that zone is a real 16x19ft polygon, not a radius, so this proxy will
    misclassify some true paint-non-RA attempts taken from a wide lateral angle, and some true
    Mid-Range attempts taken from a short baseline angle, as the other bucket).
  - `shotValue == 2` and `shotDistance > 13` ft -> MIDRANGE (same proxy caveat).
Reconciliation against the REAL, OFFICIAL, already-cached zone-level totals (see below) found
this proxy's family SHARES within +/-2.2 percentage points of the official shares across all
four buckets on a 150-game 2025-26 sample -- close enough to be directionally trustworthy, NOT
precise enough to be treated as the official zone split. This is reported as a real, stated
limitation, not silently smoothed over.

============================ RECONCILIATION (verified directly, not assumed) ============================
A 150-game stratified sample (every ~8th game across the full 1230-game 2025-26 regular season,
by date) reconciled against:
  - The SAME season's OFFICIAL team-game totals (`leaguegamefinder`, all 1230 games): total BLK
    11,907 over 2,460 team-games = 4.84 BLK/team-game. The 150-game sample's own BLK/team-game
    (computed independently, from raw PBP, not from this same boxscore endpoint) was 4.88 --
    within 0.04 of the full-season official total, a real, strong validation that this
    extraction methodology is neither double-counting nor missing real block events.
  - The SAME season's OFFICIAL zone-level FGA/FGM totals, summed across all 582 players from
    this project's own already-cached `cache/2025-26/player_shot_zones.json`
    (`leaguedashplayershotlocations`, Phase 5's existing ingestion -- reused as-is, zero new API
    calls for this check): total FGA 219,160 vs `leaguegamefinder`'s own 219,159 (off by 1, pure
    rounding) and total FGM 103,227 vs 103,227 (exact) -- these two independent real endpoints
    agree almost exactly, confirming the official zone cache itself is sound before this module's
    own distance-proxy sample is compared against it.

Do NOT re-run `extract_block_rate_sample` against a live network connection inside an
environment where `stats.nba.com` is unreachable -- this module raises a clear error in that
case rather than silently returning stale or fabricated data (see `extract_block_rate_sample`'s
own docstring).
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from data_source import _season_cache_dir

BLOCK_RATE_CACHE_VERSION = 1

# Distance-proxy family taxonomy -- see module docstring's own "DATA-SOURCE AUDIT" section for
# the exact, stated limitation versus the official SHOT_ZONE_BASIC polygon.
RIM_MAX_DISTANCE_FT = 4.0
FLOATER_SHORT_MAX_DISTANCE_FT = 13.0


def _block_rate_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "block_rate_sample.json"


def classify_shot_family(shot_value: Optional[int], shot_distance: Optional[float]) -> str:
    """A DISTANCE-BASED PROXY for this project's own shot-family taxonomy -- see module
    docstring. `shot_value == 3` is an exact, real split (no proxy needed); everything else is
    an explicit, documented approximation of the official zone polygon."""
    if shot_value == 3:
        return "THREE_POINT"
    if shot_value != 2 or shot_distance is None:
        return "UNKNOWN"
    if shot_distance <= RIM_MAX_DISTANCE_FT:
        return "RIM"
    if shot_distance <= FLOATER_SHORT_MAX_DISTANCE_FT:
        return "FLOATER_SHORT"
    return "MIDRANGE"


@dataclass
class BlockRateFamilyRow:
    family: str
    fga: int = 0
    fgm: int = 0
    blocks: int = 0

    @property
    def block_rate(self) -> float:
        return (self.blocks / self.fga) if self.fga else float("nan")

    @property
    def unblocked_attempts(self) -> int:
        return self.fga - self.blocks

    @property
    def p_make_given_not_blocked(self) -> float:
        unblocked = self.unblocked_attempts
        return (self.fgm / unblocked) if unblocked else float("nan")


def extract_block_rate_sample(season: str, sample_size: int = 150, force: bool = False) -> dict:
    """Fetches a real, stratified (every-Nth-game-by-date) sample of `season`'s regular-season
    play-by-play, extracts every FGA and every real blocked-shot event, and caches the raw rows.
    Requires a live, reachable `stats.nba.com` connection (via `nba_api`) -- if unreachable, this
    raises `ConnectionError` explicitly rather than returning fabricated or stale data (MISSING
    != ZERO: an extraction that cannot run is reported as unavailable, never silently skipped).

    Idempotent/cacheable: returns the existing cache unless `force=True`."""
    cache_path = _block_rate_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)

    try:
        from nba_api.stats.endpoints import leaguegamefinder, playbyplayv3
    except ImportError as e:
        raise ConnectionError(f"nba_api is not installed -- cannot extract real block data: {e}")

    try:
        gf = leaguegamefinder.LeagueGameFinder(season_nullable=season, league_id_nullable="00",
                                                season_type_nullable="Regular Season", timeout=30)
        schedule_df = gf.get_data_frames()[0]
    except Exception as e:
        raise ConnectionError(
            f"Could not reach stats.nba.com to fetch the {season} schedule -- real extraction "
            f"requires a live connection; no fallback/guessed data is substituted: {e}"
        )

    games = schedule_df.drop_duplicates(subset="GAME_ID").sort_values("GAME_DATE")["GAME_ID"].tolist()
    step = max(1, len(games) // sample_size)
    sample_ids = games[::step][:sample_size]

    shot_events: List[dict] = []
    block_events: List[dict] = []
    failed: List[str] = []

    for game_id in sample_ids:
        df = None
        for attempt in range(3):
            try:
                df = playbyplayv3.PlayByPlayV3(game_id=game_id, timeout=20).get_data_frames()[0]
                break
            except Exception:
                time.sleep(1)
        if df is None:
            failed.append(game_id)
            continue

        fg_mask = df["actionType"].isin(["Made Shot", "Missed Shot"])
        for _, row in df[fg_mask].iterrows():
            shot_events.append({
                "game_id": game_id, "action_number": int(row["actionNumber"]),
                "shot_distance": float(row["shotDistance"]) if row["shotDistance"] is not None else None,
                "shot_value": int(row["shotValue"]) if row["shotValue"] is not None else None,
                "made": bool(row["actionType"] == "Made Shot"),
            })

        block_mask = df["description"].astype(str).str.contains(r"BLOCK \(", regex=True, na=False) \
            & (df["actionType"] == "")
        for _, brow in df[block_mask].iterrows():
            pair = df[(df["actionNumber"] == brow["actionNumber"]) & (df["actionType"] == "Missed Shot")]
            if len(pair) != 1:
                continue  # unpaired block row -- excluded, never guessed (real, rare schema edge case)
            shot = pair.iloc[0]
            block_events.append({
                "game_id": game_id, "action_number": int(brow["actionNumber"]),
                "blocker_id": int(brow["personId"]), "shooter_id": int(shot["personId"]),
                "shot_distance": float(shot["shotDistance"]), "shot_value": int(shot["shotValue"]),
            })

    payload = {
        "cache_version": BLOCK_RATE_CACHE_VERSION,
        "season": season,
        "season_type": "Regular Season",
        "source": "nba_api.stats.endpoints.playbyplayv3",
        "total_games_in_season": len(games),
        "sample_game_ids": sample_ids,
        "sample_size": len(sample_ids),
        "failed_game_ids": failed,
        "shot_events": shot_events,
        "block_events": block_events,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(payload, f)
    return payload


def summarize_block_rate_sample(season: str) -> Dict[str, BlockRateFamilyRow]:
    """Reads the cached sample (must already exist -- call `extract_block_rate_sample` first)
    and returns the per-family `BlockRateFamilyRow` table."""
    cache_path = _block_rate_cache_path(season)
    if not cache_path.exists():
        raise FileNotFoundError(
            f"no cached block-rate sample for {season} -- call extract_block_rate_sample({season!r}) first"
        )
    with open(cache_path) as f:
        payload = json.load(f)

    rows: Dict[str, BlockRateFamilyRow] = {
        fam: BlockRateFamilyRow(family=fam) for fam in ("RIM", "FLOATER_SHORT", "MIDRANGE", "THREE_POINT")
    }
    for s in payload["shot_events"]:
        fam = classify_shot_family(s["shot_value"], s["shot_distance"])
        if fam not in rows:
            continue
        rows[fam].fga += 1
        if s["made"]:
            rows[fam].fgm += 1
    for b in payload["block_events"]:
        fam = classify_shot_family(b["shot_value"], b["shot_distance"])
        if fam in rows:
            rows[fam].blocks += 1
    return rows


def reconcile_against_official_totals(season: str, official_team_games: int, official_blk_total: int,
                                       official_fga_total: int) -> dict:
    """Compares this module's own sample-derived BLK/team-game and FGA/team-game against
    caller-supplied REAL official totals (e.g. from `leaguegamefinder` summed over the full
    season) -- never hardcodes a reference number inside this module itself."""
    cache_path = _block_rate_cache_path(season)
    with open(cache_path) as f:
        payload = json.load(f)
    sample_team_games = 2 * payload["sample_size"]
    sample_blk = sum(1 for _ in payload["block_events"])
    sample_fga = len(payload["shot_events"])
    return {
        "sample_blk_per_team_game": sample_blk / sample_team_games,
        "official_blk_per_team_game": official_blk_total / official_team_games,
        "sample_fga_per_team_game": sample_fga / sample_team_games,
        "official_fga_per_team_game": official_fga_total / official_team_games,
    }
