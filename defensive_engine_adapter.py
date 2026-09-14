"""
Defensive Truth -> Engine Scale Adapter V1 -- `defensive_playmaking` only.

`poa_containment`/`rim_protection` are DIRECTLY compatible with the engine's own fields (see
`player_defensive_truth.py`'s own engine-consumption audit) and are overlaid straight from
`player_defensive_truth.apply_defensive_truth_to_simulation_profile` -- no adapter needed for
those two. `foul_discipline` is a confirmed SEMANTIC MISMATCH (the engine's own prior
documentation) and is never overlaid, by anyone, this phase.

`defensive_playmaking` is the one target needing an explicit, separate scale bridge -- following
the Rebounding Truth phase's own precedent (`rebound_engine_adapter.py`): TRUTH != ENGINE ADAPTER.
`player_defensive_truth.py`'s own `apply_defensive_truth_to_simulation_profile` deliberately never
touches `defensive_playmaking_per36`; this module is the separate, explicit conversion layer.

============================ SCALE AUDIT ============================
`PlayerSimulationProfile.defensive_playmaking_per36` (default 1.5) is fed by
`player_ability_estimation.py`'s OLDER, cruder STL+BLK-per-36 construct -- 1.5 is that
construct's own real, documented 2024-25 population mean. This phase's own, richer
`defensive_playmaking_estimation.py` construct (STL+BLK+DEFLECTIONS per 36) has a real, measured
2024-25 population mean/SD of 4.13/1.43 vs. the old construct's 1.89/0.75 (both measured directly)
-- a genuine ~2.2x scale gap, driven mainly by adding deflections (a real, additional disruptive-
event category the old construct never counted). This is NOT a [0,1] rate (it is an unbounded
per-36 count-rate), so the bridge uses plain Z-SCORE standardization rather than the rebounding
adapter's logit-standardization (logit only applies to bounded rates) -- the same underlying
principle (re-anchor the richer truth's own real distribution onto the engine's calibrated
center/spread), simplified for an unbounded quantity.

    z            = (new_rate - NEW_MEAN) / NEW_SD
    mapped_rate  = ENGINE_DEFAULT_MEAN + z * OLD_SD

All constants are REAL, measured, PRECOMPUTED OFFLINE (2024-25 cutoff reference populations) --
never refit at runtime. `ENGINE_DEFAULT_MEAN` reuses `PlayerSimulationProfile.synthetic()`'s own
literal `defensive_playmaking_per36=1.5` default (itself the old construct's real population
mean, per that module's own comment) -- not redefined here.

    NEW_MEAN = 4.1328, NEW_SD = 1.4270  (n=3255 real player-seasons, this phase's own construct)
    OLD_SD   = 0.7518                    (n=9475 real player-seasons, the existing construct)
    ENGINE_DEFAULT_MEAN = 1.5

This transform is exactly monotonic in `new_rate` (affine, strictly positive multiplier) --
Spearman correlation against the source truth is 1.0 by construction. It maps the new
construct's own population mean to exactly `ENGINE_DEFAULT_MEAN`, and rescales spread to match the
engine's own calibrated (old-construct) real spread. No clipping is applied -- the mapped value can
go negative for a very weak real defender, which is a legitimate real modeling outcome for an
unbounded per-36 rate (the engine consumes this as "a small, flagged weight," not a probability;
see `possession_orchestrator.py`'s own comment) and is checked for realism in this phase's own
diagnostic report rather than force-clipped here.

============================ WHAT THIS MODULE DOES NOT DO ============================
Does not touch `player_ability_estimation.py`'s own `defensive_playmaking` attribute/extractor.
Does not touch `possession_orchestrator.py`/any resolver mechanics. Does not overlay any field
other than `defensive_playmaking_per36`. Does not fabricate a value when source truth is `None`.
"""
from dataclasses import dataclass, replace
from typing import Optional

from possession_orchestrator import PlayerSimulationProfile

ADAPTER_VERSION = "zscore_standardized_v1"

NEW_MEAN = 4.1328
NEW_SD = 1.4270
OLD_SD = 0.7518
ENGINE_DEFAULT_MEAN = 1.5  # PlayerSimulationProfile.synthetic()'s own literal default, reused not redefined

_FIELD_NAME = "defensive_playmaking_per36"


def map_defensive_playmaking_to_engine_scale(rate: float) -> float:
    """The one transform. Deterministic, closed-form, O(1)."""
    z = (rate - NEW_MEAN) / NEW_SD
    return ENGINE_DEFAULT_MEAN + z * OLD_SD


@dataclass(frozen=True)
class AdaptedDefensivePlaymakingValue:
    """Keeps the source truth's own metadata alongside the transformed engine value -- the mapped
    number is never mistaken for raw truth."""
    engine_value: float
    source_value: float
    source_confidence: Optional[float]
    source_sample_size: Optional[float]
    source_provenance: str
    mapping_version: str = ADAPTER_VERSION


def adapt_defensive_playmaking_estimate(estimate) -> Optional[AdaptedDefensivePlaymakingValue]:
    """`estimate` is a `player_defensive_truth.DefensiveTruthEstimate` for 'defensive_playmaking'.
    Returns None (never a fabricated value) when the source truth has no real evidence."""
    if estimate.value is None:
        return None
    engine_value = map_defensive_playmaking_to_engine_scale(estimate.value)
    return AdaptedDefensivePlaymakingValue(
        engine_value=engine_value, source_value=estimate.value, source_confidence=estimate.confidence,
        source_sample_size=estimate.sample_size, source_provenance=estimate.provenance,
    )


def apply_defensive_playmaking_via_adapter(profile: PlayerSimulationProfile, truth) -> PlayerSimulationProfile:
    """The real overlay entry point for `defensive_playmaking` ONLY -- `truth` is a
    `DefensiveTruthProfile`. Missing source truth leaves `defensive_playmaking_per36` at its
    existing (synthetic-default or otherwise unmodified) value."""
    estimate = truth.estimates.get("defensive_playmaking")
    if estimate is None:
        return profile
    adapted = adapt_defensive_playmaking_estimate(estimate)
    if adapted is None:
        return profile
    return replace(profile, **{_FIELD_NAME: adapted.engine_value})
