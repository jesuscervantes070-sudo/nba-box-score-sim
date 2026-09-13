"""
Rebounding Truth V1 -- season-level estimator layer.

Reuses `player_ability_estimation.py`'s already-tested GENERIC machinery (`SeasonEvidence`,
`_weighted_shrunk_estimate`, `_percentile_rating`, `_seasons_through_cutoff`) by IMPORT only --
same posture as `shot_zone_estimation.py`/`playmaking_estimation.py` -- this file does not modify
that module, and none of its six previously-validated attributes' extractors, tests, or
calibration are touched.

============================ CONSTRUCT DEFINITIONS (checked against real cached data first) ============================
`offensive_rebounding` = latent probability/ability to secure an offensive rebound GIVEN a real,
individually-tracked offensive rebound CHANCE -- `rate = oreb / oreb_chances`
(`rebound_chance_ingestion.py`'s real `leaguedashptstats(Rebounding)` tracking: `OREB_CHANCES` is a
real, per-player count of tracked rebound opportunities the player was spatially/temporally part
of, not a raw team-miss-exposure proxy). NOT raw OREB/game, NOT OREB/minute, NOT the combined
REB_PCT split.

`defensive_rebounding` = latent probability/ability to secure a defensive rebound GIVEN a real
defensive rebound chance -- `rate = dreb / dreb_chances`. Same source, mirrored fields.

============================ DENOMINATOR AUDIT (this phase's own ranking, applied) ============================
Preferred ranking given in this phase's spec: (1) player rebound chances, (2) contested/uncontested
opportunities, (3) spatially-eligible opportunities, (4) team/opponent-miss exposure as fallback.
`OREB_CHANCES`/`DREB_CHANCES` (this module) are tier (1) -- the real, strongest available
denominator, confirmed live via `leaguedashptstats(Rebounding)` (see rebound_chance_ingestion.py).
`player_ability_estimation.py`'s EXISTING `offensive_rebounding`/`defensive_rebounding` attributes
(Phase 1-3) use real OREB_PCT/DREB_PCT -- a real, opportunity-normalized rate, but a TEAM-CONTEXT
share of rebounds available while the player was on the floor (closer to tier 4, team-miss
exposure) rather than an individually-tracked chance. That existing attribute is NOT touched, NOT
duplicated here -- see `player_rebounding_truth.py`'s own module docstring for the full comparison
and the resulting engine-scale-compatibility finding (the two constructs are on genuinely different
numeric scales: this module's real 2023-24 population means are OREB_CHANCE_PCT=~0.42,
DREB_CHANCE_PCT=~0.60, vs. Phase 1-3's OREB_PCT/DREB_PCT population means of ~0.048/~0.131).

CONTESTED CONTEXT: `oreb_contest`/`oreb_uncontest` (and DREB mirrors) are real, tracked per-chance
contest labels. This module does NOT split V1's core estimate by contested-vs-uncontested (kept
simple per this phase's own "do not overcomplicate V1" instruction) -- `OREB_CONTEST_PCT`/
`DREB_CONTEST_PCT` are surfaced only as diagnostic context (see the roster-diagnostics report), not
folded into the shrinkage math.

OREB and DREB are estimated with COMPLETELY SEPARATE evidence functions, separate reference
populations, and separate shrinkage. Neither is inferred from the other -- a player can score high
on one and merely average on the other; the architecture makes no assumption they move together
(see the correlation diagnostic in the phase report).

============================ REAL DATA FLOOR ============================
Both targets require `player_rebound_chances.json` (real SportVU/Second Spectrum camera tracking,
floor 2013-14, checked directly -- see rebound_chance_ingestion.py's own module docstring).
"""
import functools
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from player_ability_estimation import (
    RATING_MIN, RATING_MAX, SeasonEvidence, _percentile_rating, _seasons_through_cutoff,
    _weighted_shrunk_estimate,
)
from player_ability_profile import AttributeEstimate
from rebound_chance_ingestion import REBOUND_CHANCE_FIRST_SEASON
from rebound_chance_ingestion import load_rebound_chances as _load_rebound_chances_uncached

# Process-local memoization ONLY -- see playmaking_estimation.py's identical rationale: this real
# per-season JSON cache never changes within a single process: read once, not once per
# per-player-per-season lookup. Never modifies rebound_chance_ingestion.py's own caching.
load_rebound_chances = functools.lru_cache(maxsize=None)(_load_rebound_chances_uncached)

REBOUNDING_ATTRIBUTES = ("offensive_rebounding", "defensive_rebounding")

# Effective real evidence floor for both targets -- see module docstring.
REBOUNDING_FIRST_SEASON = REBOUND_CHANCE_FIRST_SEASON

# Provisional (pre-calibration) constants -- same conservative-starting-point role as every other
# attribute group's own provisional constants before a dedicated calibration phase exists. NOT
# swept against a real backtest yet -- explicitly future work, not done here.
PROVISIONAL_RECENCY_DECAY = 0.6
PROVISIONAL_SHRINKAGE_M: Dict[str, float] = {
    "offensive_rebounding": 150.0,  # real oreb_chances units
    "defensive_rebounding": 150.0,  # real dreb_chances units
}


def resolve_params(attribute: str) -> Tuple[float, float, str]:
    """(recency_decay, prior_strength, source) -- no dedicated calibration artifact exists yet for
    this attribute group, so this always resolves to "provisional" (mirrors
    playmaking_estimation.resolve_params's exact contract/shape, for a future calibration phase to
    extend without changing any caller)."""
    return PROVISIONAL_RECENCY_DECAY, PROVISIONAL_SHRINKAGE_M[attribute], "provisional"


@dataclass
class ReboundingEstimationResult:
    attribute: str
    seasons_used: List[SeasonEvidence]
    weighted_raw_rate: Optional[float]
    shrunk_rate: Optional[float]
    league_avg_rate: Optional[float]
    percentile_rating: Optional[float]
    total_weight: float
    param_source: str = "provisional"


def _player_row_by_id(cache: Dict[str, dict], player_id: str) -> Optional[dict]:
    return cache.get(player_id)


def _offensive_rebounding_evidence(player_id: str, season: str) -> Optional[Tuple[float, float]]:
    """(rate, sample) for one real player-season, or None if this player has no real rebound-
    chance tracking row this season (a true 'no evidence', never a fabricated 0.0)."""
    row = _player_row_by_id(load_rebound_chances(season), player_id)
    if row is None:
        return None
    chances = row.get("oreb_chances")
    if not chances:
        return None
    return row["oreb"] / chances, chances


def _defensive_rebounding_evidence(player_id: str, season: str) -> Optional[Tuple[float, float]]:
    row = _player_row_by_id(load_rebound_chances(season), player_id)
    if row is None:
        return None
    chances = row.get("dreb_chances")
    if not chances:
        return None
    return row["dreb"] / chances, chances


_EVIDENCE_FN = {
    "offensive_rebounding": _offensive_rebounding_evidence,
    "defensive_rebounding": _defensive_rebounding_evidence,
}


def _player_evidence_by_season(player_id: str, seasons: List[str], attribute: str) -> List[SeasonEvidence]:
    fn = _EVIDENCE_FN[attribute]
    evidence = []
    for s in seasons:
        if s < REBOUNDING_FIRST_SEASON:
            continue
        result = fn(player_id, s)
        if result is None:
            continue
        rate, sample = result
        evidence.append(SeasonEvidence(season=s, rate=rate, sample=sample))
    return evidence


def _build_reference_population(attribute: str, as_of_season: str, all_seasons: List[str],
                                 min_chances: float = 30.0) -> Tuple[List[float], Optional[float]]:
    """Real per-season rates for every qualifying real player-season THROUGH `as_of_season` ONLY
    -- the leak-free reference distribution this attribute's shrinkage-prior league average is
    built from. Same `_seasons_through_cutoff` discipline every other estimator in this project
    already uses. `min_chances=30` matches the real threshold Phase 19's own diagnostic already
    used for this exact source (see rebound_resolution.py's module docstring)."""
    fn = _EVIDENCE_FN[attribute]
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    values = []
    for s in seasons:
        if s < REBOUNDING_FIRST_SEASON:
            continue
        chance_cache = load_rebound_chances(s)
        for player_id in chance_cache:
            result = fn(player_id, s)
            if result is None:
                continue
            rate, sample = result
            if sample >= min_chances:
                values.append(rate)
    league_avg = sum(values) / len(values) if values else None
    return values, league_avg


def estimate_rebounding_attribute(player_id: str, as_of_season: str, attribute: str,
                                   all_seasons: List[str]) -> ReboundingEstimationResult:
    """The one public entry point -- id-KEYED directly (rebound_chance_ingestion.py's cache is
    already real player_id-keyed, unlike the older name-keyed box/advanced caches
    `player_ability_estimation.py` reads -- no `player_identity.py` name-resolution adapter is
    needed for THIS attribute group). Same no-future-leakage discipline as every other estimator
    in this project."""
    if attribute not in REBOUNDING_ATTRIBUTES:
        raise ValueError(f"Unknown rebounding attribute {attribute!r}")
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    seasons_evidence = _player_evidence_by_season(player_id, seasons, attribute)

    reference_values, league_avg = _build_reference_population(attribute, as_of_season, all_seasons)
    recency_decay, prior_strength, param_source = resolve_params(attribute)

    weighted_raw, shrunk, total_weight = _weighted_shrunk_estimate(
        seasons_evidence, as_of_season, prior_strength, league_avg, recency_decay=recency_decay,
    )
    rating = _percentile_rating(shrunk, reference_values) if shrunk is not None else None

    return ReboundingEstimationResult(
        attribute=attribute, seasons_used=seasons_evidence, weighted_raw_rate=weighted_raw,
        shrunk_rate=shrunk, league_avg_rate=league_avg, percentile_rating=rating,
        total_weight=total_weight, param_source=param_source,
    )


def result_to_attribute_estimate(result: ReboundingEstimationResult) -> AttributeEstimate:
    """Same bridge shape as playmaking_estimation.result_to_attribute_estimate -- UNESTIMATED
    (value=None) when there's no real evidence, never a guessed midpoint."""
    if result.percentile_rating is None:
        return AttributeEstimate()
    _, prior, _ = resolve_params(result.attribute)
    confidence = round(result.total_weight / (result.total_weight + prior), 3)
    return AttributeEstimate(
        value=result.percentile_rating, confidence=confidence, sample_size=int(round(result.total_weight)),
    )
