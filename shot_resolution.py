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
    # "Complete shot-family block occurrence" phase -- see `perimeter_block_probability`/
    # `resolve_perimeter_shot` below. Same two real outcome names `interior_shot_resolution.py`
    # already uses for RIM/FLOATER, reused verbatim rather than inventing a parallel vocabulary.
    BLOCKED_RETAINED_OFFENSE = "BLOCKED_RETAINED_OFFENSE"
    BLOCKED_SECURED_DEFENSE = "BLOCKED_SECURED_DEFENSE"


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


# =======================================================================
# Perimeter shot blocking (MIDRANGE + THREE_POINT) -- "Complete shot-family block occurrence"
# phase. AUDIT FINDING: before this phase, this module had NO block concept at all -- every
# MIDRANGE/THREE_POINT attempt went straight from the whistle check to clean make/miss,
# architecturally UNREACHABLE for a block regardless of any defender's ability (Class C, dead
# path -- confirmed by direct inspection of `possession_orchestrator._dispatch_shot`'s perimeter
# branch, which only ever called `apply_shot_resolution_to_engine`, never anything block-aware).
#
# This is the smallest causal addition that closes that gap: the SAME "family baseline/intercept +
# existing defender skill" architecture `interior_shot_resolution.py` already uses for RIM/FLOATER,
# reusing its OWN real, already-validated `defensive_playmaking` z-scoring and damping constant --
# no new defender skill, no OVR, no generic "defense". One deliberate, documented simplification:
# a jump-shot block is, in this V1 model, always by the PRIMARY/on-ball defender only -- a help
# defender recovering from elsewhere cannot realistically contest a live jumper in time, so
# interior's own HELPING-anchor secondary-defender path is NOT reused here (a real, structural
# choice, not an oversight -- see the phase report for the full reachability audit).
# =======================================================================
from interior_shot_resolution import (  # noqa: E402 -- imported here (not at module top) to keep
    BLOCK_LEVERAGE_SCALE, BLOCK_RETAINED_BY_OFFENSE_RATE,               # this module's THREE_POINT/
    DEFENSIVE_PLAYMAKING_POPULATION_MEAN, DEFENSIVE_PLAYMAKING_POPULATION_STDEV,  # MIDRANGE-only
)                                                                         # scope visible at a glance.

# CALIBRATION TARGETS (TRAIN 25000-25049, validated HELDOUT 25050-25099): each family base block
# logit is set so that, at a LEAGUE-AVERAGE primary defender (z=0, i.e. `defender_playmaking`
# exactly at `DEFENSIVE_PLAYMAKING_POPULATION_MEAN`), the resulting block probability reproduces
# this project's own trusted empirical `playbyplayv3` family block-rate anchors (MIDRANGE ~2.51%,
# THREE ~0.80% -- see docs/ phase report for the extraction; reused, not re-sourced). Deliberately
# MUCH lower than `interior_shot_resolution.BASE_BLOCK_LOGIT` (-2.6, ~7% before ability adjustment)
# -- a contested jumper is real-world materially harder to block than a rim/floater attempt; this
# is never simply the interior rate reused unchanged.
MIDRANGE_BASE_BLOCK_LOGIT = -3.70
THREE_POINT_BASE_BLOCK_LOGIT = -4.70


def _defender_z(value: Optional[float]) -> float:
    return ((value - DEFENSIVE_PLAYMAKING_POPULATION_MEAN) / DEFENSIVE_PLAYMAKING_POPULATION_STDEV
            if value is not None else 0.0)  # missing -> zero Z-CONTRIBUTION, not a fabricated skill value


@dataclass
class PerimeterBlockContext:
    """The ONE authorized ability input (`defender_playmaking`, the SAME already-existing
    `defensive_playmaking_per36` interior blocking already reads, caller-resolved) plus real,
    structural shot-family identity. No tendency, no shooter-side attribute, no `poa_containment`
    -- same firewall posture as `ShotResolutionContext` above."""
    shot_family: str
    defender_playmaking: Optional[float] = None


# Bug fix found during TRAIN calibration: the SHARED `_PROB_EPSILON` (0.01/1%) is the right floor
# for a shot MAKE probability (never legitimately below ~1%), but THREE_POINT's own trusted
# empirical block-rate anchor (~0.80%) is genuinely BELOW that floor -- reusing `_PROB_EPSILON`
# here would silently clip every neutral-defender THREE_POINT block probability up to exactly 1%
# regardless of `THREE_POINT_BASE_BLOCK_LOGIT`, making that constant unable to ever reach its own
# target (confirmed: a logit sweep from -5.2 to -4.6 produced IDENTICAL simulated block counts --
# the floor, not the logit, was binding). A SEPARATE, lower floor for this genuinely rarer event
# (never touching `_PROB_EPSILON`/shot-make-probability behavior at all) fixes this.
_BLOCK_PROB_EPSILON = 0.002


def perimeter_block_probability(context: PerimeterBlockContext) -> float:
    """Pure function -- no RNG, no state mutation. Single eligible defender (primary only, see
    module docstring) -- no STRONGEST-EFFECTIVE combination needed since there is only one
    candidate, unlike `interior_shot_resolution._block_leverage`."""
    base_logit = {
        ShotFamily.MIDRANGE: MIDRANGE_BASE_BLOCK_LOGIT,
        ShotFamily.THREE_POINT: THREE_POINT_BASE_BLOCK_LOGIT,
    }[context.shot_family]
    leverage = BLOCK_LEVERAGE_SCALE * _defender_z(context.defender_playmaking)
    return min(max(_sigmoid(base_logit + leverage), _BLOCK_PROB_EPSILON), 1.0 - _BLOCK_PROB_EPSILON)


@dataclass
class PerimeterShotResult:
    outcome: str
    points: int
    block_probability_used: float
    make_probability_used: Optional[float]
    blocker_id: Optional[str] = None
    shot_family: str = ""
    shooter_id: str = ""
    zone: Optional[str] = None


def resolve_perimeter_shot(shooter_id: str, shot_context: ShotResolutionContext,
                            block_context: PerimeterBlockContext, blocker_id: str,
                            rng: random.Random, zone: Optional[str] = None) -> PerimeterShotResult:
    """The single entry point for a block-aware perimeter (MIDRANGE/THREE_POINT) attempt. Two
    INDEPENDENT rolls -- block, then (only if not blocked) make/miss -- the SAME RNG-isolation
    convention `interior_shot_resolution.resolve_interior_shot` already established, so a
    counterfactual that changes only block-relevant inputs never reshuffles the make/miss roll."""
    from possession_state import _assert_player_id
    _assert_player_id(shooter_id)
    _assert_player_id(blocker_id)
    p_block = perimeter_block_probability(block_context)
    if rng.random() < p_block:
        retained = rng.random() < BLOCK_RETAINED_BY_OFFENSE_RATE
        outcome = ShotOutcome.BLOCKED_RETAINED_OFFENSE if retained else ShotOutcome.BLOCKED_SECURED_DEFENSE
        return PerimeterShotResult(outcome=outcome, points=0, block_probability_used=p_block,
                                    make_probability_used=None, blocker_id=blocker_id,
                                    shot_family=shot_context.shot_family, shooter_id=shooter_id, zone=zone)
    shot_result = resolve_shot(shooter_id, shot_context, rng, zone=zone)
    return PerimeterShotResult(
        outcome=shot_result.outcome, points=shot_result.points, block_probability_used=p_block,
        make_probability_used=shot_result.probability_used, blocker_id=None,
        shot_family=shot_context.shot_family, shooter_id=shooter_id, zone=zone,
    )


def apply_perimeter_shot_to_engine(engine, shooter_id: str, shot_context: ShotResolutionContext,
                                    block_context: PerimeterBlockContext, blocker_id: str, rng: random.Random,
                                    zone: Optional[str] = None, assisted_by: Optional[str] = None) -> PerimeterShotResult:
    """Thin integration layer -- reuses Phase 15's own, unmodified `begin_shot`/`resolve_shot_made`/
    `block_retained_by_offense`/`block_secured_by_defense` and this module's own
    `resolve_shot_missed_pending_rebound`-calling convention (via `resolve_shot`'s own MISS branch,
    replicated here rather than double-dispatched) -- mirrors
    `interior_shot_resolution.apply_interior_shot_to_engine` exactly. No rebound winner is ever
    selected here."""
    from possession_state import SpatialZone
    zone_enum = SpatialZone(zone) if zone else engine.state.ball_zone
    engine.begin_shot(zone_enum, dt=0.0)
    result = resolve_perimeter_shot(shooter_id, shot_context, block_context, blocker_id, rng, zone=zone)
    if result.outcome == ShotOutcome.MADE:
        engine.resolve_shot_made(shooter_id, assisted_by=assisted_by, dt=0.0)
    elif result.outcome == ShotOutcome.MISSED:
        engine.resolve_shot_missed_pending_rebound(shooter_id, dt=0.0)
    elif result.outcome == ShotOutcome.BLOCKED_RETAINED_OFFENSE:
        engine.block_retained_by_offense(result.blocker_id, shooter_id, dt=0.0)
    elif result.outcome == ShotOutcome.BLOCKED_SECURED_DEFENSE:
        engine.block_secured_by_defense(result.blocker_id, shooter_id, dt=0.0)
    return result
