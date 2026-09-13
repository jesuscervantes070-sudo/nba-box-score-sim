"""
Playmaking + Ball Security V1 -- season-level estimator layer.

Reuses `player_ability_estimation.py`'s already-tested GENERIC machinery (`SeasonEvidence`,
`_weighted_shrunk_estimate`, `_percentile_rating`, `_seasons_through_cutoff`) by IMPORT only --
same posture as `shot_zone_estimation.py` -- this file does not modify that module, and none of
its six previously-validated attributes' extractors, tests, or calibration are touched.

============================ CONSTRUCT DEFINITIONS (checked against real cached data first) ============================
`passing_accuracy` = execution quality of an attempted pass -- P(a thrown pass is NOT a bad-pass
turnover). NOT raw assists, NOT AST/game. Numerator/denominator: real `bad_pass` turnover-subtype
count (`turnover_ingestion.py`'s own already-tested, already-production PBP-derived classifier,
CATEGORY_BAD_PASS) over (real `passes_made`, `player_passing_tracking.json` -- real SportVU/Second
Spectrum tracking -- + that same `bad_pass` count, since a bad-pass turnover IS a thrown pass that
`passes_made` itself does not count). `rate = 1 - bad_pass / (passes_made + bad_pass)`.

`playmaking_vision` = productive opportunity-creation quality, conditional on passing volume (an
OPPORTUNITY-normalized rate, not a raw total) -- real `potential_ast` (a real pass that WOULD have
been an assist had the shot gone in -- Second Spectrum's own definition, independent of the
teammate's own finishing) over real `passes_made`. NOT passing_accuracy (a completely different
real field, a completely different real denominator concept -- see the module's own correlation
audit), NOT raw assists alone (assists REQUIRE a made shot -- a confound this project's own
existing `ATTRIBUTES_NEEDING_REBOUND_SPLITS`-style discipline explicitly avoids), NOT usage.

`ball_security` = ability to retain the ball given real handling exposure -- real `handling_error`
turnover-subtype count (Lost Ball / Traveling / Double-Dribble -- CATEGORY_HANDLING, deliberately
EXCLUDING Bad Pass, Offensive Foul, Illegal Screen, and team/shot-clock violations, per
`player_ability_turnover_prototype.py`'s own already-checked conceptual separation) over real
`touches` (`player_handling_exposure.json`, real SportVU/Second Spectrum tracking).
`rate = 1 - handling_error / touches`.

NOTE, explicit: `player_ability_estimation.py` ALREADY defines an attribute NAMED `ball_security`
(a cruder, box-score-only, ALL-turnovers proxy -- see its own extractor's docstring, which itself
already points at `player_ability_turnover_prototype.py` as the un-wired, more-correct future
path). That existing attribute feeds a SEPARATE, unrelated pipeline (`PlayerAbilityProfile`'s own
0-99 percentile track) and is NOT touched, NOT removed, NOT renamed here -- this module's OWN
`ball_security` is a genuinely different, better-evidenced definition for the SCORING/PLAYMAKING
truth-to-simulation-profile track this project has actually been building. Two real, honestly
distinct definitions coexisting under one plain-English name, in two separate modules, is
documented here rather than silently papered over.

============================ REAL DATA FLOOR ============================
All three targets require `player_passing_tracking.json` and/or `player_handling_exposure.json`
(both real SportVU/Second Spectrum camera tracking, floor 2013-14, checked directly in each
module's own docstring) -- `player_turnover_subtypes.json` (PBP-derived) independently covers the
full 1996-97+ range, but the EFFECTIVE floor for all three targets here is 2013-14, the tighter
tracking-data constraint.
"""
import functools
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from handling_exposure import HANDLING_EXPOSURE_FIRST_SEASON
from handling_exposure import load_handling_exposure as _load_handling_exposure_uncached
from passing_tracking_ingestion import PASSING_TRACKING_FIRST_SEASON
from passing_tracking_ingestion import load_passing_tracking as _load_passing_tracking_uncached
from player_ability_estimation import (
    RATING_MIN, RATING_MAX, SeasonEvidence, _percentile_rating, _seasons_through_cutoff,
    _weighted_shrunk_estimate,
)
from player_ability_profile import AttributeEstimate
from turnover_ingestion import CATEGORY_BAD_PASS, CATEGORY_HANDLING
from turnover_ingestion import load_turnover_cache as _load_turnover_cache_uncached

# Process-local memoization ONLY -- these real per-season JSON caches never change within a
# single process, and every real value/estimate downstream is unaffected (same file, same
# dict, read once instead of re-read from disk on every one of thousands of per-player-per-season
# lookups). This module never modifies the underlying `*_ingestion.py`/`turnover_ingestion.py`
# files or their own caching -- purely a local read-through wrapper for real perf, not a semantic
# or correctness change. `functools.lru_cache` is keyed on `season` alone (a plain string), so it
# is safe and deterministic.
load_passing_tracking = functools.lru_cache(maxsize=None)(_load_passing_tracking_uncached)
load_handling_exposure = functools.lru_cache(maxsize=None)(_load_handling_exposure_uncached)
load_turnover_cache = functools.lru_cache(maxsize=None)(_load_turnover_cache_uncached)

PLAYMAKING_ATTRIBUTES = ("passing_accuracy", "playmaking_vision", "ball_security")

# Effective real evidence floor for every target here -- see module docstring.
PLAYMAKING_FIRST_SEASON = max(PASSING_TRACKING_FIRST_SEASON, HANDLING_EXPOSURE_FIRST_SEASON)

# Provisional (pre-calibration) constants -- same conservative-starting-point role as every other
# estimator's own provisional constants before a dedicated calibration phase exists for THIS
# attribute group. NOT swept against a real backtest yet -- explicitly future work, not done here.
PROVISIONAL_RECENCY_DECAY = 0.6
PROVISIONAL_SHRINKAGE_M: Dict[str, float] = {
    "passing_accuracy": 300.0,    # real pass-attempt-equivalent units (passes_made + bad_pass)
    "playmaking_vision": 300.0,   # real passes_made units
    "ball_security": 300.0,       # real touches units
}


def resolve_params(attribute: str) -> Tuple[float, float, str]:
    """(recency_decay, prior_strength, source) -- no dedicated calibration artifact exists yet
    for this attribute group, so this always resolves to "provisional" (mirrors
    `shot_zone_estimation.resolve_params`'s exact contract/shape, for a future calibration phase
    to extend without changing any caller)."""
    return PROVISIONAL_RECENCY_DECAY, PROVISIONAL_SHRINKAGE_M[attribute], "provisional"


@dataclass
class PlaymakingEstimationResult:
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


def _passing_accuracy_evidence(player_id: str, season: str) -> Optional[Tuple[float, float]]:
    """(rate, sample) for one real player-season, or None if the real tracking/turnover data
    needed doesn't exist for this player this season (a true 'no evidence', never a fabricated
    0.0/1.0)."""
    passing_row = _player_row_by_id(load_passing_tracking(season), player_id)
    if passing_row is None or passing_row.get("passes_made") is None:
        return None
    turnover_row = _player_row_by_id(load_turnover_cache(season).get("players", {}), player_id) \
        if load_turnover_cache(season) else None
    bad_pass = turnover_row.get(CATEGORY_BAD_PASS, 0) if turnover_row else 0
    passes_made = passing_row["passes_made"]
    denom = passes_made + bad_pass
    if denom <= 0:
        return None
    return 1.0 - bad_pass / denom, denom


def _playmaking_vision_evidence(player_id: str, season: str) -> Optional[Tuple[float, float]]:
    passing_row = _player_row_by_id(load_passing_tracking(season), player_id)
    if passing_row is None:
        return None
    passes_made = passing_row.get("passes_made")
    potential_ast = passing_row.get("potential_ast")
    if not passes_made or potential_ast is None:
        return None
    return potential_ast / passes_made, passes_made


def _ball_security_evidence(player_id: str, season: str) -> Optional[Tuple[float, float]]:
    handling_row = _player_row_by_id(load_handling_exposure(season), player_id)
    if handling_row is None or not handling_row.get("touches"):
        return None
    turnover_cache = load_turnover_cache(season)
    turnover_row = _player_row_by_id(turnover_cache.get("players", {}), player_id) if turnover_cache else None
    handling_error = turnover_row.get(CATEGORY_HANDLING, 0) if turnover_row else 0
    touches = handling_row["touches"]
    return 1.0 - handling_error / touches, touches


_EVIDENCE_FN = {
    "passing_accuracy": _passing_accuracy_evidence,
    "playmaking_vision": _playmaking_vision_evidence,
    "ball_security": _ball_security_evidence,
}


def _player_evidence_by_season(player_id: str, seasons: List[str], attribute: str) -> List[SeasonEvidence]:
    fn = _EVIDENCE_FN[attribute]
    evidence = []
    for s in seasons:
        if s < PLAYMAKING_FIRST_SEASON:
            continue
        result = fn(player_id, s)
        if result is None:
            continue
        rate, sample = result
        evidence.append(SeasonEvidence(season=s, rate=rate, sample=sample))
    return evidence


def _build_reference_population(attribute: str, as_of_season: str, all_seasons: List[str],
                                 min_touches_or_passes: float = 100.0) -> Tuple[List[float], Optional[float]]:
    """Real per-season rates for every qualifying real player-season THROUGH `as_of_season` ONLY
    -- the leak-free reference distribution this attribute's shrinkage-prior league average is
    built from. Same `_seasons_through_cutoff` discipline every other estimator in this project
    already uses."""
    fn = _EVIDENCE_FN[attribute]
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    values = []
    for s in seasons:
        if s < PLAYMAKING_FIRST_SEASON:
            continue
        passing_cache = load_passing_tracking(s)
        for player_id in passing_cache:
            result = fn(player_id, s)
            if result is None:
                continue
            rate, sample = result
            if sample >= min_touches_or_passes:
                values.append(rate)
    league_avg = sum(values) / len(values) if values else None
    return values, league_avg


def estimate_playmaking_attribute(player_id: str, as_of_season: str, attribute: str,
                                   all_seasons: List[str]) -> PlaymakingEstimationResult:
    """The one public entry point -- id-KEYED directly (every source this module reads is already
    real player_id-keyed, unlike the older name-keyed box/advanced caches `player_ability_estimation.py`
    reads -- no `player_identity.py` name-resolution adapter is needed for THIS attribute group).
    Same no-future-leakage discipline as every other estimator in this project."""
    if attribute not in PLAYMAKING_ATTRIBUTES:
        raise ValueError(f"Unknown playmaking attribute {attribute!r}")
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    seasons_evidence = _player_evidence_by_season(player_id, seasons, attribute)

    reference_values, league_avg = _build_reference_population(attribute, as_of_season, all_seasons)
    recency_decay, prior_strength, param_source = resolve_params(attribute)

    weighted_raw, shrunk, total_weight = _weighted_shrunk_estimate(
        seasons_evidence, as_of_season, prior_strength, league_avg, recency_decay=recency_decay,
    )
    rating = _percentile_rating(shrunk, reference_values) if shrunk is not None else None

    return PlaymakingEstimationResult(
        attribute=attribute, seasons_used=seasons_evidence, weighted_raw_rate=weighted_raw,
        shrunk_rate=shrunk, league_avg_rate=league_avg, percentile_rating=rating,
        total_weight=total_weight, param_source=param_source,
    )


def result_to_attribute_estimate(result: PlaymakingEstimationResult) -> AttributeEstimate:
    """Same bridge shape as `shot_zone_estimation.result_to_attribute_estimate` -- UNESTIMATED
    (value=None) when there's no real evidence, never a guessed midpoint."""
    if result.percentile_rating is None:
        return AttributeEstimate()
    _, prior, _ = resolve_params(result.attribute)
    confidence = round(result.total_weight / (result.total_weight + prior), 3)
    return AttributeEstimate(
        value=result.percentile_rating, confidence=confidence, sample_size=int(round(result.total_weight)),
    )
