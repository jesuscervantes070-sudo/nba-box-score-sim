"""
Phase 5 -- Two-Point Shot-Zone Ability Foundation: the estimator.

Produces `rim_finishing`, `floater_short_mid`, and `midrange` --
multi-year, recency-weighted, Bayesian-shrunk PERCENTILE ratings built
from real `leaguedashplayershotlocations` zone FGM/FGA (see
shot_zone_ingestion.py for the verified source). Deliberately reuses
player_ability_estimation.py's already-tested GENERIC machinery
(`SeasonEvidence`, `_weighted_shrunk_estimate`, `_percentile_rating`,
`_seasons_through_cutoff`) by IMPORT only -- this file does not modify
that module, and none of the six previously-validated attributes'
extractors, tests, or calibration are touched.

============================ V1 SEMANTIC DEFINITIONS (deliberately conservative) ============================
- `rim_finishing` = stabilized ability to convert REAL Restricted Area
  attempts. NOT shot creation, NOT rim pressure/frequency, NOT dunk rate.
- `floater_short_mid` = stabilized ability to convert REAL "In The Paint
  (Non-RA)" attempts. Despite the display name, this is NOT a parsed
  "floater" action-type stat -- it is every non-restricted-area paint
  attempt (hooks, runners, short jumpers, post shots inside the paint),
  exactly what the real NBA.com zone contains. No text-based action-type
  parsing is done this phase.
- `midrange` = stabilized ability to convert REAL NBA.com "Mid-Range"
  zone attempts (everything outside the paint, inside the 3PT line).

None of the three uses volume/usage/role as an input -- FGA is used ONLY
as the real sample-size weight for recency/shrinkage, never as a value
that raises or lowers the rating itself (see `_extract_zone_pct` below:
the rate returned is always real FG_PCT for that zone, nothing else).
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from loader import load_teams, load_player_advanced_stats
from player_ability_profile import AttributeEstimate
from player_ability_estimation import (
    SeasonEvidence, _seasons_through_cutoff, _weighted_shrunk_estimate, _percentile_rating,
    RATING_MIN, RATING_MAX,
)
from shot_zone_ingestion import load_shot_zones, SHOT_ZONE_FIRST_SEASON

SHOT_ZONE_ATTRIBUTES = ("rim_finishing", "floater_short_mid", "midrange")

ZONE_PREFIX_FOR_ATTRIBUTE: Dict[str, str] = {
    "rim_finishing": "restricted_area",
    "floater_short_mid": "paint_non_ra",
    "midrange": "midrange",
}

# Provisional (pre-calibration) constants -- same conservative-starting-
# point role as player_ability_estimation.py's own RECENCY_DECAY/
# SHRINKAGE_PRIOR_STRENGTH before those six attributes were calibrated.
# Used as Baseline C ("existing general provisional decay/shrink") in
# the calibration comparison -- NOT assumed to be the final answer for
# any of these three attributes.
PROVISIONAL_RECENCY_DECAY = 0.6
PROVISIONAL_SHRINKAGE_M: Dict[str, float] = {
    "rim_finishing": 200.0,      # real RA FGA units -- same round starting magnitude as three_point's provisional M
    "floater_short_mid": 200.0,  # real paint-non-RA FGA units
    "midrange": 200.0,           # real mid-range FGA units
}


def resolve_params(attribute: str) -> Tuple[float, float, str]:
    """(recency_decay, prior_strength, source) -- prefers the empirically
    calibrated value (shot_zone_calibration.json) when one exists,
    otherwise falls back to this module's own provisional constants.
    Mirrors player_ability_estimation.resolve_params's contract exactly,
    against a SEPARATE calibration artifact (never touches
    player_ability_calibration.json/_v2.json)."""
    from shot_zone_calibration import get_calibrated_params
    calibrated = get_calibrated_params(attribute)
    if calibrated is not None:
        return calibrated.lambda_, calibrated.M, "calibrated"
    return PROVISIONAL_RECENCY_DECAY, PROVISIONAL_SHRINKAGE_M[attribute], "provisional"


@dataclass
class ShotZoneEstimationResult:
    attribute: str
    seasons_used: List[SeasonEvidence]
    weighted_raw_rate: Optional[float]
    shrunk_rate: Optional[float]
    league_avg_rate: Optional[float]
    percentile_rating: Optional[float]
    total_weight: float
    param_source: str = "provisional"


def _zone_row_pct(zone_row: dict, prefix: str) -> Optional[Tuple[float, float]]:
    """(fg_pct, fga) for one real player-season's zone row, or None if
    real FGA is 0 (a true zero -- see shot_zone_ingestion's own "missing
    vs true zero" distinction; a real zero-attempt zone contributes NO
    evidence, exactly like every other attribute's own "sample <= 0 ->
    skip" rule -- it is not a real rate of any kind)."""
    fgm = zone_row.get(f"{prefix}_fgm")
    fga = zone_row.get(f"{prefix}_fga")
    if fgm is None or fga is None or fga <= 0:
        return None
    return fgm / fga, fga


def _player_zone_evidence_by_season(name: str, seasons: List[str], prefix: str) -> List[SeasonEvidence]:
    """Real per-season evidence for one player/zone -- looks up
    `load_shot_zones(season)` (keyed by player_id, real player_name
    field) by NAME, same load-bearing key the rest of this codebase
    already uses. A player_name absent from a season's cache is real
    MISSING evidence for that season (skipped, never a guessed 0)."""
    evidence = []
    for s in seasons:
        zone_data = load_shot_zones(s)
        if not zone_data:
            continue
        row = next((r for r in zone_data.values() if r["player_name"] == name), None)
        if row is None:
            continue
        result = _zone_row_pct(row, prefix)
        if result is None:
            continue
        rate, sample = result
        evidence.append(SeasonEvidence(season=s, rate=rate, sample=sample, mode="n/a"))
    return evidence


def _build_reference_population(attribute: str, as_of_season: str, all_seasons: List[str],
                                 min_gp: int = 20, min_mpg: float = 12.0, min_zone_fga: float = 20.0
                                 ) -> Tuple[List[float], Optional[float]]:
    """Real single-season FG% for every qualifying, real rotation
    player-season (gp/mpg floor, same as player_ability_estimation.py's
    own reference-population convention) through `as_of_season` ONLY --
    the leak-free source for both the percentile mapping and the
    shrinkage-prior league average."""
    prefix = ZONE_PREFIX_FOR_ATTRIBUTE[attribute]
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    values = []
    for s in seasons:
        if s < SHOT_ZONE_FIRST_SEASON:
            continue
        zone_data = load_shot_zones(s)
        if not zone_data:
            continue
        try:
            teams = load_teams(s)
        except FileNotFoundError:
            continue
        advanced = load_player_advanced_stats(s)
        rotation_names = {
            player.name for team in teams.values() for player in team.players
            if (advanced.get(player.name, {}).get("gp", 0) >= min_gp
                and advanced.get(player.name, {}).get("mpg", 0) >= min_mpg)
        }
        for row in zone_data.values():
            if row["player_name"] not in rotation_names:
                continue
            result = _zone_row_pct(row, prefix)
            if result is None:
                continue
            rate, fga = result
            if fga >= min_zone_fga:
                values.append(rate)
    league_avg = sum(values) / len(values) if values else None
    return values, league_avg


def estimate_shot_zone_attribute(name: str, as_of_season: str, attribute: str,
                                  all_seasons: List[str]) -> ShotZoneEstimationResult:
    """The one public entry point -- same no-future-leakage discipline as
    player_ability_estimation.estimate_attribute (see
    _seasons_through_cutoff, the one place that's enforced)."""
    if attribute not in SHOT_ZONE_ATTRIBUTES:
        raise ValueError(f"Unknown shot-zone attribute {attribute!r}")
    prefix = ZONE_PREFIX_FOR_ATTRIBUTE[attribute]
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    seasons_evidence = _player_zone_evidence_by_season(name, seasons, prefix)

    reference_values, league_avg = _build_reference_population(attribute, as_of_season, all_seasons)
    recency_decay, prior_strength, param_source = resolve_params(attribute)

    weighted_raw, shrunk, total_weight = _weighted_shrunk_estimate(
        seasons_evidence, as_of_season, prior_strength, league_avg, recency_decay=recency_decay,
    )
    rating = _percentile_rating(shrunk, reference_values) if shrunk is not None else None

    return ShotZoneEstimationResult(
        attribute=attribute, seasons_used=seasons_evidence,
        weighted_raw_rate=weighted_raw, shrunk_rate=shrunk,
        league_avg_rate=league_avg, percentile_rating=rating, total_weight=total_weight,
        param_source=param_source,
    )


def result_to_attribute_estimate(result: ShotZoneEstimationResult) -> AttributeEstimate:
    """Same bridge as player_ability_estimation.result_to_attribute_estimate
    -- UNESTIMATED (value=None) when there's no real evidence, never a
    guessed 0.0/50.0."""
    if result.percentile_rating is None:
        return AttributeEstimate()
    _, prior, _ = resolve_params(result.attribute)
    confidence = round(result.total_weight / (result.total_weight + prior), 3)
    return AttributeEstimate(
        value=result.percentile_rating, confidence=confidence,
        sample_size=int(round(result.total_weight)),
    )
