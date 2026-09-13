"""
Rebounding Truth -> Engine Scale Adapter V1.

Explicit, versioned bridge from `ReboundingTruthProfile` (the strong, real, chance-based truth
built in `rebounding_estimation.py`/`player_rebounding_truth.py`) to `PlayerSimulationProfile`'s
frozen, calibrated `offensive_rebounding_shrunk_rate`/`defensive_rebounding_shrunk_rate` fields.

TRUTH != ENGINE ADAPTER: `player_rebounding_truth.py`'s own `apply_rebounding_truth_to_simulation_profile`
deliberately remains a documented no-op (a real, discovered raw-scale mismatch) -- this module is
the SEPARATE, explicit conversion layer the prior phase's report recommended, not a retroactive
edit of that decision. `ReboundingTruthProfile.value(...)` still means exactly what it always
meant (a real [0,1] chance-conversion rate); nothing in this module redefines truth.

============================ ENGINE FIELD SEMANTICS (audited BEFORE choosing a bridge) ============================
`rebound_resolution.rebound_rate_to_acquisition_log_weight(rate, side)` computes, verbatim:
    baseline + _bounded_logit(rate) - _bounded_logit(reference)
This is a CENTERED LOG-ODDS DEVIATION consumed by a softmax contest
(`_candidate_log_weight`/`_softmax_choice`), NOT a bare probability read literally anywhere. The
field's absolute scale only matters through its LOGIT DISTANCE from
`OFFENSIVE_REBOUND_RATE_REFERENCE`/`DEFENSIVE_REBOUND_RATE_REFERENCE` (both real, hand-set,
rounded 2024-25 `leaguedashplayerstats(Advanced)` OREB_PCT/DREB_PCT population means -- confirmed
directly from `rebound_resolution.py`'s own module comment, unchanged, never modified by this
module). A player's engine-facing rebounding field therefore does NOT need to literally represent
their true rebound probability -- it only needs to reproduce the correct RELATIVE log-odds
deviation a real OREB_PCT/DREB_PCT input would have produced. This licenses a monotonic rescale
rather than demanding literal probability preservation (a fact established by reading the
resolver's own formula, not assumed).

============================ CANDIDATE COMPARISON (real, measured, 2021-22 through 2023-24) ============================
Chance-conversion (`rebounding_estimation.py`'s OREB_CHANCE_PCT/DREB_CHANCE_PCT-based truth, this
project's STRONGER, individually-tracked construct) vs. the EXISTING, unmodified
`player_ability_estimation.py` `offensive_rebounding`/`defensive_rebounding` attribute (real
OREB_PCT/DREB_PCT, a team-context share) -- same real players, same real seasons, both read
through this project's own multi-season shrunk estimate:

    OREB: Pearson 0.652-0.701, Spearman 0.662-0.706 (n=521-543/season, 3 seasons) --
          moderate-strong, STABLE across seasons (no season swings wildly).
    DREB: Pearson 0.376-0.427, Spearman 0.311-0.371 (n=523-545/season, 3 seasons) --
          weak-moderate, meaningfully LOWER than OREB's own correlation.

These are REAL, meaningfully-below-1.0 correlations -- OREB_PCT/DREB_PCT are demonstrably NOT a
mere scale transform of chance conversion; they are a genuinely different construct with real,
substantial disagreement. Concrete, real 2023-24 rank-disagreement examples: several
limited-minutes bench bigs (Orlando Robinson, Oscar Tshiebwe, Zach Collins) rank in the top ~18%
of real per-chance OREB conversion but the BOTTOM ~10% of team-context OREB_PCT (their few
available minutes dilute the team-context share and their thin sample gets heavily shrunk toward
the league mean) -- exactly the MINUTES != ABILITY contamination this project's doctrine warns
against. Tyus Jones (DREB): ~91st-percentile real per-chance DREB conversion, but ~10th-percentile
DREB_PCT (a small guard rarely gets a defensive-rebound LOOK in team-context, even though he
converts well when he does) -- a clean, real OPPORTUNITY != ABILITY example.

DECISION: given this real, quantified divergence, directly overlaying OREB_PCT/DREB_PCT
(Candidate B) would silently discard 30-70% of the stronger truth's real rank information at the
exact moment of consumption. This phase's own instruction is explicit: "If the stronger chance-
based truth says Player A > Player B, the bridge should preserve that ordering as much as
possible. A scale bridge that destroys player ordering is unacceptable." Candidate B is REJECTED
on this basis -- not merely "it's the weaker denominator" as an intuition, but an empirically
demonstrated, real ordering conflict.

============================ CHOSEN BRIDGE: Candidate A -- logit-standardized rescale ============================
    z            = (logit(chance_rate) - CHANCE_MEAN_LOGIT[side]) / CHANCE_SD_LOGIT[side]
    mapped_logit = logit(ENGINE_REFERENCE[side]) + z * ENGINE_SD_LOGIT[side]
    mapped_rate  = sigmoid(mapped_logit)

All non-engine constants (`CHANCE_MEAN_LOGIT`, `CHANCE_SD_LOGIT`, `ENGINE_SD_LOGIT`) are REAL,
measured, PRECOMPUTED OFFLINE -- see the exact values and provenance below -- never refit at
runtime ("mapping should be trivial at runtime," per this phase's own instruction).
`ENGINE_REFERENCE` reuses `rebound_resolution.OFFENSIVE_REBOUND_RATE_REFERENCE`/
`DEFENSIVE_REBOUND_RATE_REFERENCE` BY IMPORT, unmodified.

This transform is EXACTLY monotonic in `chance_rate` (a composition of `logit`, an affine rescale
with a strictly positive multiplier, and `sigmoid` -- all strictly increasing functions) --
Spearman correlation against the source chance-truth is 1.0 BY CONSTRUCTION (mod exact ties), the
strongest possible rank preservation, verified empirically in this phase's test suite and
diagnostic report. It matches the engine's calibrated CENTER exactly (a player exactly at the
chance-truth population mean maps to exactly `ENGINE_REFERENCE`) and its calibrated logit-space
SPREAD (using the real, measured logit standard deviation of the engine's own OREB_PCT/DREB_PCT
reference population) -- both criteria this phase's spec asks for.

Real, measured constants (2024-25 cutoff, `_build_reference_population`-filtered reference
populations -- OREB: min 30 real chances; DREB: min 30 real chances; OREB_PCT/DREB_PCT: real
`min_gp=20`/`min_mpg=12` floor, matching `player_ability_estimation.py`'s own existing filter):

    CHANCE_MEAN_LOGIT: offensive_rebounding=-0.5092, defensive_rebounding=0.3796
    CHANCE_SD_LOGIT:   offensive_rebounding=0.4201,  defensive_rebounding=0.2748
    ENGINE_SD_LOGIT:   offensive_rebounding=0.7927,  defensive_rebounding=0.4478
    (n=4722/5304 real player-seasons for the chance populations, n=9475 for each of the
    OREB_PCT/DREB_PCT reference populations -- both measured directly, not estimated.)

============================ REJECTED: Candidate C -- quantile/percentile mapping ============================
Considered: map chance-truth's percentile rank into the empirical CDF of OREB_PCT/DREB_PCT. Also
exactly rank-preserving, and would additionally inherit the target distribution's full shape (not
merely its first two logit-moments). REJECTED for V1: requires persisting/maintaining a full
empirical CDF lookup table (materially more implementation surface -- interpolation edge cases, a
versioned data artifact to keep in sync) for a benefit (matching higher-order distributional
shape) not shown to matter given Candidate A already lands in a sane, correctly-centered,
correctly-spread range with no clipping (see this phase's report Sec. Q) -- "prefer the simplest
mapping that satisfies constraints." Not shipped; revisit only if Candidate A's mapped population
shows a real, measured shape problem in a future phase.

============================ WHAT THIS MODULE DOES NOT DO ============================
Does not change `OFFENSIVE_REBOUND_RATE_REFERENCE`/`DEFENSIVE_REBOUND_RATE_REFERENCE` (imported,
never modified). Does not touch `rebound_resolution.py`'s mechanics, eligibility gate, box-out
leverage, or softmax contest. Does not overlay any field other than
`offensive_rebounding_shrunk_rate`/`defensive_rebounding_shrunk_rate`. Does not fabricate a value
when the source truth is `None` (missing stays missing; the synthetic default is left untouched).
"""
import math
from dataclasses import dataclass, replace
from typing import Dict, Optional

from possession_orchestrator import PlayerSimulationProfile
from rebound_resolution import (
    DEFENSIVE_REBOUND_RATE_REFERENCE as _ENGINE_DREB_REFERENCE,
    OFFENSIVE_REBOUND_RATE_REFERENCE as _ENGINE_OREB_REFERENCE,
    _bounded_logit,
)

ADAPTER_VERSION = "logit_standardized_v1"

ENGINE_REFERENCE: Dict[str, float] = {
    "offensive_rebounding": _ENGINE_OREB_REFERENCE,
    "defensive_rebounding": _ENGINE_DREB_REFERENCE,
}

# Real, measured, precomputed offline (2024-25 cutoff) -- see module docstring for exact
# provenance/sample sizes. Never refit at runtime.
CHANCE_MEAN_LOGIT: Dict[str, float] = {
    "offensive_rebounding": -0.5092,
    "defensive_rebounding": 0.3796,
}
CHANCE_SD_LOGIT: Dict[str, float] = {
    "offensive_rebounding": 0.4201,
    "defensive_rebounding": 0.2748,
}
ENGINE_SD_LOGIT: Dict[str, float] = {
    "offensive_rebounding": 0.7927,
    "defensive_rebounding": 0.4478,
}

_FIELD_NAME: Dict[str, str] = {
    "offensive_rebounding": "offensive_rebounding_shrunk_rate",
    "defensive_rebounding": "defensive_rebounding_shrunk_rate",
}


def map_chance_rate_to_engine_scale(rate: float, attribute: str) -> float:
    """The one transform. Deterministic, closed-form, O(1) -- no fitting, no I/O, safe to call
    once per player per overlay. `attribute` must be 'offensive_rebounding' or
    'defensive_rebounding'."""
    if attribute not in ENGINE_REFERENCE:
        raise ValueError(f"Unknown rebounding attribute {attribute!r}")
    z = (_bounded_logit(rate) - CHANCE_MEAN_LOGIT[attribute]) / CHANCE_SD_LOGIT[attribute]
    mapped_logit = _bounded_logit(ENGINE_REFERENCE[attribute]) + z * ENGINE_SD_LOGIT[attribute]
    return 1.0 / (1.0 + math.exp(-mapped_logit))


@dataclass(frozen=True)
class AdaptedReboundValue:
    """Keeps the source truth's own metadata alongside the transformed engine value, so the
    transformed number is never mistaken for raw truth (per this phase's own instruction: "Do not
    make the transformed engine value look like raw truth")."""
    attribute: str
    engine_value: float
    source_value: float
    source_confidence: Optional[float]
    source_sample_size: Optional[float]
    source_provenance: str
    mapping_version: str = ADAPTER_VERSION


def adapt_rebounding_estimate(estimate) -> Optional[AdaptedReboundValue]:
    """`estimate` is a `player_rebounding_truth.ReboundingTruthEstimate`. Returns None (never a
    fabricated value) when the source truth itself has no real evidence."""
    if estimate.value is None:
        return None
    engine_value = map_chance_rate_to_engine_scale(estimate.value, estimate.name)
    return AdaptedReboundValue(
        attribute=estimate.name, engine_value=engine_value, source_value=estimate.value,
        source_confidence=estimate.confidence, source_sample_size=estimate.sample_size,
        source_provenance=estimate.provenance,
    )


def apply_rebounding_truth_via_adapter(profile: PlayerSimulationProfile, truth) -> PlayerSimulationProfile:
    """The real overlay entry point: `ReboundingTruthProfile -> adapter -> PlayerSimulationProfile`.
    Overlays ONLY `offensive_rebounding_shrunk_rate`/`defensive_rebounding_shrunk_rate` -- no other
    field is ever touched. Missing source truth leaves the corresponding field at its existing
    (synthetic-default or otherwise unmodified) value -- never fabricates a league-average
    stand-in."""
    overrides = {}
    for attribute, field_name in _FIELD_NAME.items():
        estimate = truth.estimates.get(attribute)
        if estimate is None:
            continue
        adapted = adapt_rebounding_estimate(estimate)
        if adapted is None:
            continue
        overrides[field_name] = adapted.engine_value
    return replace(profile, **overrides) if overrides else profile
