"""
Phase 18A -- Perimeter Shot Resolution (3PT + midrange only).

Resolves an already-selected perimeter `ShotIntent`. Does NOT choose the
shot (Phase 16's job) and does NOT resolve rim attempts, floaters,
blocks, shooting fouls, free throws, putbacks, or rebounds.

============================ CONSTRUCT AUDIT (see docs/PHASE18A_PERIMETER_SHOT_RESOLUTION_REPORT.md Sec. 2/3) ============================
`three_point` (player_ability_estimation._extract_three_point): raw
input is real, whole-season box-score `FG3_PCT` -- a POOLED historical
composite. It does NOT condition on catch-vs-pull-up, defender
distance, shot location within the arc, assisted status, shot clock, or
dribble count. **Confirmed context-contaminated, not context-neutral.**

`midrange` (shot_zone_estimation.py): real NBA.com "Mid-Range" ZONE
FG% (LeagueDashPlayerShotLocations) -- also POOLED across release mode
and contest level. **Also confirmed context-contaminated.**

Both are reused AS-IS (no recalibration) as the base-skill input to
this module -- but because both already carry real, historical
shot-diet difficulty baked in, this module does NOT apply a
diet-centered adjustment on top (that would double-count, per HQ's
explicit instruction not to assume Gemini's diet-centering approach).
Instead, real 2023-24 population data (Sec. 8/9 of the report) showed
individual PLAYER-level release-mode deviation from the population
effect has only weak year-to-year persistence (r=0.151, n=139) --
empirically insufficient to support a diet-centered (C) or
partially-pooled player-specific (D) release-mode adjustment. A
POPULATION-LEVEL (B) release-mode/contest joint effect is what's
actually used here.

============================ THE FIREWALL ============================
`three_point_preference`, `midrange_preference`, `pullup_vs_catch`,
`perimeter_space_creation`, `poa_containment`, and raw `AdvantageModel`
internals are NOT imported and have NO parameter anywhere in this
module. Once `ShotIntent` and defender geometry are fixed, none of them
can reach shot resolution -- see test_shot_resolution.py's firewall
tests, which check this both by signature inspection and by direct
counterfactual (changing one of these values while holding every real
input fixed produces byte-for-byte identical resolution).
"""
import math
import random
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------
# Real, population-level constants -- ALL computed THIS PHASE from a
# live 2023-24 `nba_api.stats.endpoints.leaguedashplayerptshot` pull
# (the real, verified public closest-defender-distance endpoint; real
# bucket names "0-2 Feet - Very Tight"/"2-4 Feet - Tight"/
# "4-6 Feet - Open"/"6+ Feet - Wide Open", confirmed directly, not
# assumed). NOT re-derived per season/era -- explicit, flagged
# placeholders, same posture as every population constant in Phases
# 17A/17B.
# ---------------------------------------------------------------------


class ShotFamily:
    THREE_POINT = "THREE_POINT"
    MIDRANGE = "MIDRANGE"


class ReleaseMode:
    CATCH_AND_SHOOT = "CATCH_AND_SHOOT"
    PULL_UP = "PULL_UP"
    UNKNOWN = "UNKNOWN"  # missing/sparse release-mode evidence -- NOT treated as zero effect, see _contest_release_delta


class ContestBucket:
    VERY_TIGHT = "VERY_TIGHT"    # real NBA "0-2 Feet"
    TIGHT = "TIGHT"              # real NBA "2-4 Feet"
    OPEN = "OPEN"                # real NBA "4-6 Feet"
    WIDE_OPEN = "WIDE_OPEN"      # real NBA "6+ Feet"


# Real, joint (release_mode x contest_bucket) logit deltas relative to
# the real 2023-24 population mean 3PT% (36.58%, n=133,853 real league
# attempts pooled across both modes/4 buckets). Computed directly, not
# forced into a separable additive decomposition -- the real data is
# NOT perfectly additive (see report Sec. 8/12), so the joint cross-tab
# is used as-is rather than fabricating an interaction term beyond what
# the real numbers already show. VERY_TIGHT cells are REAL but SPARSE
# (145/250 total league attempts respectively) -- flagged low-confidence.
THREE_POINT_CONTEST_RELEASE_LOGIT_DELTA = {
    (ReleaseMode.CATCH_AND_SHOOT, ContestBucket.VERY_TIGHT): -0.4495,   # n=145 real attempts -- SPARSE
    (ReleaseMode.CATCH_AND_SHOOT, ContestBucket.TIGHT): -0.2254,
    (ReleaseMode.CATCH_AND_SHOOT, ContestBucket.OPEN): -0.0376,
    (ReleaseMode.CATCH_AND_SHOOT, ContestBucket.WIDE_OPEN): 0.1264,
    (ReleaseMode.PULL_UP, ContestBucket.VERY_TIGHT): -0.4140,           # n=250 real attempts -- SPARSE
    (ReleaseMode.PULL_UP, ContestBucket.TIGHT): -0.4127,
    (ReleaseMode.PULL_UP, ContestBucket.OPEN): -0.1218,
    (ReleaseMode.PULL_UP, ContestBucket.WIDE_OPEN): 0.0250,
}
# UNKNOWN release mode: real, computed mode-AVERAGE of the two known
# modes per bucket -- a real, evidence-grounded fallback, not a zero
# effect and not an arbitrarily chosen single mode (missing != zero).
THREE_POINT_CONTEST_RELEASE_LOGIT_DELTA.update({
    (ReleaseMode.UNKNOWN, ContestBucket.VERY_TIGHT): -0.4317,
    (ReleaseMode.UNKNOWN, ContestBucket.TIGHT): -0.3191,
    (ReleaseMode.UNKNOWN, ContestBucket.OPEN): -0.0797,
    (ReleaseMode.UNKNOWN, ContestBucket.WIDE_OPEN): 0.0757,
})

# Midrange: NO validated, release-mode-specific real cross-tab was
# obtained this phase (no clean public "pure midrange, by release mode"
# split was verified in time -- see report Sec. 16). The bucket-only,
# mode-AVERAGED deltas above are REUSED here as an explicit, flagged
# TRANSFER ASSUMPTION (the relative shape of the contest effect is
# assumed, not independently validated, to carry over from 3PT to
# midrange) -- classified KEEP BUT FLAG / REVISIT (Sec. 28).
MIDRANGE_CONTEST_LOGIT_DELTA = {
    ContestBucket.VERY_TIGHT: -0.4317,
    ContestBucket.TIGHT: -0.3191,
    ContestBucket.OPEN: -0.0797,
    ContestBucket.WIDE_OPEN: 0.0757,
}

# Real, contest-bucket-CONTROLLED late-clock finding (Sec. 15 of the
# report): WITHIN the real "Wide Open" bucket specifically, real
# 2023-24 3PT% drops from 39.7% (mid-clock) to 33.6% (shot clock
# 4-0 seconds) -- a real, independent effect that SURVIVES contest
# stratification, not merely a proxy for "late-clock shots are more
# contested." The boundary (4.0s) is the real NBA.com bucket edge used
# to compute this, not an arbitrary invented threshold.
LATE_CLOCK_THRESHOLD_SECONDS = 4.0
LATE_CLOCK_LOGIT_DELTA = -0.2632

# Posture -- STRUCTURAL, not a validated magnitude (per explicit
# instruction: "exact posture coefficients are NOT validated"). A real,
# hand-set placeholder enforcing the one required structural invariant:
# a defender who is not square-and-engaged must not exert the same
# frontal contest as one who is, at a comparable coarse distance bucket.
from possession_state import DefensivePosture

POSTURE_CONTEST_LOGIT_DELTA = {
    DefensivePosture.SQUARE: 0.0,
    DefensivePosture.RECOVERING: 0.15,   # still partially engaged, less able to contest fully square-on
    DefensivePosture.TRAILING: 0.35,     # behind the shooter's hip -- structurally cannot contest as a square defender would
    DefensivePosture.HELPING: 0.45,      # not even engaged on this shooter
}

_PROB_EPSILON = 0.01  # keeps probability strictly within (0.01, 0.99) -- make/miss must remain stochastic, never a hard 0/1 unless a rule (not this module) makes the attempt impossible


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class ShotResolutionContext:
    """Every field here is either the ONE authorized ability input
    (`shooter_base_rate`, already resolved by the caller from the
    existing `three_point`/`midrange` estimator -- this module does not
    fetch it itself) or real, structural, non-ability context. No
    tendency, no `perimeter_space_creation`, no `poa_containment`, no
    raw `AdvantageModel` object appears anywhere in this dataclass."""
    shot_family: str
    shooter_base_rate: float               # the player's own existing three_point/midrange estimate (a real [0,1] rate), caller-resolved
    contest_bucket: str = ContestBucket.OPEN
    release_mode: str = ReleaseMode.UNKNOWN
    defender_posture: DefensivePosture = DefensivePosture.SQUARE
    shot_clock_remaining: Optional[float] = None  # None = no shot clock this era, or genuinely unknown -- no late-clock penalty applied either way


def shot_make_probability(context: ShotResolutionContext) -> float:
    """Pure function -- no RNG, no state mutation. Every term is
    ADDITIVE in logit space; none is multiplied by another (same
    convention as every prior resolution phase in this project)."""
    logit = _logit(context.shooter_base_rate)

    if context.shot_family == ShotFamily.THREE_POINT:
        logit += THREE_POINT_CONTEST_RELEASE_LOGIT_DELTA[(context.release_mode, context.contest_bucket)]
    elif context.shot_family == ShotFamily.MIDRANGE:
        logit += MIDRANGE_CONTEST_LOGIT_DELTA[context.contest_bucket]
    else:
        raise ValueError(f"shot_resolution only supports THREE_POINT/MIDRANGE, got {context.shot_family!r}")

    logit += POSTURE_CONTEST_LOGIT_DELTA.get(context.defender_posture, 0.0)

    if context.shot_clock_remaining is not None and context.shot_clock_remaining < LATE_CLOCK_THRESHOLD_SECONDS:
        logit += LATE_CLOCK_LOGIT_DELTA

    p = _sigmoid(logit)
    return min(max(p, _PROB_EPSILON), 1.0 - _PROB_EPSILON)


class ShotOutcome:
    MADE = "MADE"
    MISSED = "MISSED"


@dataclass
class ShotResolutionResult:
    outcome: str
    points: int
    probability_used: float
    shot_family: str
    shooter_id: str
    zone: Optional[str] = None


def resolve_shot(shooter_id: str, context: ShotResolutionContext, rng: random.Random,
                  zone: Optional[str] = None) -> ShotResolutionResult:
    """The single entry point. `rng` is caller-supplied (never the
    global `random` module) -- deterministic replay. Stochastic
    make/miss only -- no deterministic threshold anywhere."""
    from possession_state import _assert_player_id
    _assert_player_id(shooter_id)
    p = shot_make_probability(context)
    made = rng.random() < p
    points = (3 if context.shot_family == ShotFamily.THREE_POINT else 2) if made else 0
    return ShotResolutionResult(
        outcome=ShotOutcome.MADE if made else ShotOutcome.MISSED, points=points,
        probability_used=p, shot_family=context.shot_family, shooter_id=shooter_id, zone=zone,
    )


def apply_shot_resolution_to_engine(engine, shooter_id: str, context: ShotResolutionContext, rng: random.Random,
                                     zone: Optional[str] = None, assisted_by: Optional[str] = None):
    """Thin integration layer: begins the shot (`engine.begin_shot`,
    Phase 15's own, unmodified SHOT SELECTION->IN_FLIGHT transition --
    NOT touched by this phase's resolution math), resolves it via this
    module's pure `resolve_shot`, then applies the outcome using
    existing Phase 15 engine methods only. On a MISS, uses the new,
    purely-additive `resolve_shot_missed_pending_rebound` (Sec. above)
    -- no rebound winner is ever selected here; that is explicitly
    Phase 19's job."""
    from possession_state import SpatialZone
    zone_enum = SpatialZone(zone) if zone else engine.state.ball_zone
    engine.begin_shot(zone_enum, dt=0.0)
    result = resolve_shot(shooter_id, context, rng, zone=zone)
    if result.outcome == ShotOutcome.MADE:
        engine.resolve_shot_made(shooter_id, assisted_by=assisted_by, dt=0.0)
    else:
        engine.resolve_shot_missed_pending_rebound(shooter_id, dt=0.0)
    return result
