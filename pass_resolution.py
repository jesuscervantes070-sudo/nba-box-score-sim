"""
Phase 17B -- Pass Resolution & In-Flight Disruption.

Resolves an ALREADY-SELECTED pass `ActionIntent` (Phase 16 chose it;
this module never chooses or re-chooses a pass). Output is a new,
selection-ready `PossessionState` -- Phase 16 runs again from it.

============================ THE THREE-CONCEPT FIREWALL ============================
`playmaking_vision` (Phase 8) -- NOT imported, NOT referenced anywhere
in this file. Perception ended in Phase 16; by the time an `ActionIntent`
reaches this module, the pass has already been selected.

`passing_accuracy` (this repo's `passing` attribute, AST_PCT-based) --
the ONLY passer-side execution input, and its effect is deliberately
SMALL. Empirically tested this phase (see
docs/PHASE17B_PASS_RESOLUTION_REPORT.md Sec. 2): real 2023-24 data shows
raw bad-pass rate correlates STRONGLY with passing burden (r=0.636,
potential-ast-per-pass as burden proxy) and, once burden is
residualized out, `passing_accuracy` (AST_PCT) shows an essentially
NULL independent relationship with bad-pass rate (r=-0.062, n=355). This
does NOT mean passing_accuracy is useless -- it means the honest, real
finding is that CONTEXT/GEOMETRY dominates bad-pass risk far more than
this repo's existing accuracy proxy does, so this module weights context
heavily and accuracy lightly, rather than assuming the opposite.

`ball_security` (Phase 4A/4B) -- NOT imported, NOT referenced anywhere
in this file. Once the ball is released (`PASS_IN_FLIGHT`), ball_security
has no authority -- there is no passer "lost-ball" roll and no receiver
"catch" roll anywhere in this module. There is no validated catching
attribute in this repository, and this module does not invent one.
"""
import random
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from action_intent import ActionIntent, ActionType, PASS_ACTIONS
from clock_semantics import ClockTerminalCause, advance_live_clocks
from possession_advantage import AdvantageModel
from possession_engine import PossessionEngine
from possession_events import EventType
from possession_state import (
    BallState, DefensivePosture, PossessionState, SpatialZone, _assert_player_id, ball_side,
)

# ---------------------------------------------------------------------
# Real, population-level normalization -- computed THIS PHASE from real
# 2023-24 data (turnover_ingestion.py's already-cached, real, id-keyed
# bad_pass counts; leaguedashptstats Passing measure's real PASSES_MADE).
# n=355 players with >=500 real passes. NOT re-derived per season/era --
# an explicit, flagged placeholder normalization, same convention as
# Phase 17A's rim_access/poa_containment constants.
# ---------------------------------------------------------------------
BAD_PASS_RATE_POPULATION_MEAN = 0.0219
BAD_PASS_RATE_POPULATION_STDEV = 0.0089

# passing_accuracy's (AST_PCT) weight is deliberately SMALL -- see
# module docstring's real, near-null residual finding (r=-0.062).
# Direction: HIGHER passing_accuracy -> LOWER bad-pass risk (the
# theoretically expected sign, even though the real residual magnitude
# is weak) -- a real, hand-set placeholder weight, explicitly small.
PASSING_ACCURACY_WEIGHT = 0.15

# defensive_playmaking (STL+BLK per-36) population stats -- real,
# reused as-is from Phase 1-3's own existing attribute (not re-derived
# this phase); used only to shift DISRUPTION ATTEMPT likelihood among
# GEOMETRICALLY ELIGIBLE defenders, never defender location itself.
DEFENSIVE_PLAYMAKING_POPULATION_MEAN = 1.5   # STL+BLK per 36 min, real, rough league-average order of magnitude
DEFENSIVE_PLAYMAKING_POPULATION_STDEV = 0.7
DEFENSIVE_PLAYMAKING_WEIGHT = 0.5


class PassFamily:
    """Tested, not locked: DIRECT/KICKOUT/SKIP are kept as three
    distinct families ONLY because they differ in a real, tested
    dimension -- geometric eligibility (Sec. 9 of the report). Context
    labels like "swing"/"reset"/"dump-off"/"pocket" do NOT get separate
    resolver code -- they all map to DIRECT (adjacent-zone, same-side)
    below, since their geometric eligibility profile is identical.
    "outlet"/"alley-oop" are deferred entirely -- not represented."""
    DIRECT = "DIRECT"      # same-side, adjacent-ish zones (swing, reset, dump-off, pocket)
    KICKOUT = "KICKOUT"    # interior origin -> perimeter destination
    SKIP = "SKIP"          # cross-court: origin and destination on opposite sides


def classify_pass_family(origin: SpatialZone, destination: SpatialZone) -> str:
    """A general topological rule, NOT a hardcoded per-zone-pair lookup
    table -- built from `possession_state.ball_side` (already real,
    Phase 15 infrastructure, reused unmodified)."""
    origin_side, dest_side = ball_side(origin), ball_side(destination)
    interior_zones = frozenset({SpatialZone.PAINT, SpatialZone.RESTRICTED_RIM})
    if origin in interior_zones and destination not in interior_zones:
        return PassFamily.KICKOUT
    if origin_side != "CENTRAL" and dest_side != "CENTRAL" and origin_side != dest_side:
        return PassFamily.SKIP
    return PassFamily.DIRECT


# Coarse, real, flight-duration-class placeholders (SECONDS) -- NOT a
# universal invented duration claimed as empirically calibrated; a
# provisional configuration dict, swappable wholesale by a future
# calibration phase. Longer/more topologically complex passes take
# real, plausibly-longer flight time -- direction only is defended.
FLIGHT_DURATION_SECONDS = {
    PassFamily.DIRECT: 0.4,
    PassFamily.KICKOUT: 0.6,
    PassFamily.SKIP: 0.9,
}

PASS_CHECKPOINTS: Tuple[str, ...] = ("release", "geometric_eligibility_check", "disruption_attempt", "arrival")


class PassOutcome:
    """Plain string constants -- not a locked Enum, same convention as
    Phase 17A's DriveOutcome. Every value describes a possession-state
    change, never a shot outcome (there is no shot in this module)."""
    COMPLETED_CLEAN = "COMPLETED_CLEAN"                # arrival quality: immediate control
    COMPLETED_ADJUSTED = "COMPLETED_ADJUSTED"          # arrival quality: receiver must gather/adjust -- NOT a second accuracy penalty, the ONE consequence of a marginal delivery
    DEFLECTED_RETAINED_OFFENSE = "DEFLECTED_RETAINED_OFFENSE"  # pass continues live, ball still loose, offense still favored to recover -- no TOV, no STL yet
    DEFLECTED_LOOSE_BALL = "DEFLECTED_LOOSE_BALL"      # genuinely unresolved loose ball -- Phase 15's existing LOOSE state, no team possession assumed
    CLEAN_INTERCEPTION = "CLEAN_INTERCEPTION"          # defender secures control -- possession flips, STL is attributable
    BAD_PASS_OUT_OF_BOUNDS = "BAD_PASS_OUT_OF_BOUNDS"  # passer-attributed dead-ball TOV
    PERIOD_EXPIRATION_DURING_FLIGHT = "PERIOD_EXPIRATION_DURING_FLIGHT"
    BAD_PASS_TO_DEFENDER = "BAD_PASS_TO_DEFENDER"      # passer-attributed live-ball TOV via a legally-reachable eligible defender


@dataclass
class DefenderCandidate:
    """One geometrically-relevant real defender for this pass -- built
    by the caller (or `default_eligible_defenders`) from real
    `PossessionState.assignments`, never invented."""
    defender_id: str
    zone: SpatialZone
    posture: DefensivePosture
    defensive_playmaking: Optional[float] = None  # real Phase 1-3 STL+BLK/36 estimate, or None (missing != zero effect)
    is_receiver_defender: bool = False   # this defender is assigned to the RECEIVER (always eligible)
    is_passer_defender: bool = False     # this defender is assigned to the PASSER (eligible only at release)


def default_eligible_defenders(state: PossessionState, origin: SpatialZone, destination: SpatialZone,
                                candidates: List[DefenderCandidate]) -> List[DefenderCandidate]:
    """GEOMETRIC eligibility -- purely topological, computed BEFORE any
    `defensive_playmaking` value is consulted. A defender is eligible
    if: (a) they are assigned to the passer or the receiver (always "in
    the play"), or (b) their own zone is on the SAME side as the origin
    OR the destination, or is a CENTRAL zone (the interior sits
    topologically between left and right, and is always crossed by a
    kickout). A defender on the side OPPOSITE both origin and
    destination is NEVER eligible -- this is the concrete mechanism
    preventing "opposite-side defender intercepting an impossible
    pass." A general rule, not a per-zone-pair table."""
    origin_side, dest_side = ball_side(origin), ball_side(destination)
    eligible = []
    for c in candidates:
        if c.is_receiver_defender or c.is_passer_defender:
            eligible.append(c)
            continue
        cand_side = ball_side(c.zone)
        if cand_side == "CENTRAL" or cand_side == origin_side or cand_side == dest_side:
            eligible.append(c)
    return eligible


def _net_disruption_leverage(defender: DefenderCandidate) -> float:
    leverage = 0.0
    if defender.defensive_playmaking is not None:
        leverage += DEFENSIVE_PLAYMAKING_WEIGHT * (
            (defender.defensive_playmaking - DEFENSIVE_PLAYMAKING_POPULATION_MEAN) / DEFENSIVE_PLAYMAKING_POPULATION_STDEV)
    if defender.posture == DefensivePosture.HELPING:
        leverage += 0.3  # a real, hand-set placeholder -- a helping defender is more likely positioned to jump a passing lane
    elif defender.posture in (DefensivePosture.TRAILING,):
        leverage -= 0.3  # trailing their own assignment -- less able to also disrupt a pass
    return leverage


def disruption_attempt_probability(base_rate: float, defender: DefenderCandidate) -> float:
    """One eligible defender's exact disruption-attempt probability."""
    if not 0.0 < base_rate < 1.0:
        raise ValueError("disruption base rate must be strictly between 0 and 1")
    return _logistic(_logit(base_rate) + _net_disruption_leverage(defender))


def analytic_any_disruption_probability(base_rate: float,
                                         defenders: List[DefenderCandidate]) -> float:
    """Exact probability that the sequential first-success loop fires.

    This is conditional on reaching the disruption loop; the independent
    bad-pass branch executes first in the actual resolver.
    """
    no_disruption = 1.0
    for defender in defenders:
        no_disruption *= 1.0 - disruption_attempt_probability(base_rate, defender)
    return 1.0 - no_disruption


def derive_rng(parent_rng: random.Random, label: str) -> random.Random:
    """Deterministic RNG-substream isolation -- a fresh `random.Random`
    seeded from `parent_rng`'s own state plus a fixed label, so pass
    resolution's randomness is isolated from (does not consume from, and
    is not consumed by) Phase 16 selection or Phase 17A drive execution,
    while remaining fully deterministic given the parent's seed. No
    specific PRNG algorithm is required by this interface -- any
    `random.Random`-compatible object works."""
    seed_material = f"{parent_rng.getrandbits(64)}:{label}"
    return random.Random(seed_material)


@dataclass
class PassResolutionContext:
    passing_accuracy: Optional[float] = None        # real Phase 1-3 AST_PCT-based estimate for the PASSER, or None
    eligible_defenders: List[DefenderCandidate] = field(default_factory=list)  # already geometrically filtered, or raw candidates to filter
    already_filtered: bool = False                  # if False, default_eligible_defenders() is applied first
    advantage: Optional[AdvantageModel] = None       # read-only context; never mutated unless advantage_updater is supplied
    advantage_updater: Optional[Callable[[AdvantageModel], AdvantageModel]] = None  # explicit opt-in hook -- no automatic preserve/reset/decay
    # Narrow calibration seam. None preserves the module's calibrated default;
    # only the per-eligible-defender disruption-attempt base reads this.
    disruption_base_rate: Optional[float] = None


# Real, hand-set placeholder base rates for the disruption/turnover
# branch, in the SAME "explicit, flagged, not fit to public data"
# posture as Phase 17A's ordinal base rates (Sec. 2 of the report notes
# no public per-pass disruption-outcome dataset exists). Order matters:
# checked in sequence against independent rolls, never compounded into
# one probability.
BASE_RATE_ANY_DISRUPTION_ATTEMPT = 0.08         # FIRST-PASS CALIBRATED (was .12): per ELIGIBLE defender, before defensive_playmaking/posture adjustment
BASE_RATE_DISRUPTION_IS_CLEAN_INTERCEPTION = 0.35   # of an actual disruption: how often it's a clean, possession-flipping takeaway
BASE_RATE_DISRUPTION_IS_LOOSE_BALL = 0.30           # vs. retained-by-offense (the remainder)
BASE_RATE_BAD_PASS_INDEPENDENT_OF_DEFENSE = 0.02    # a passer-attributed unforced error (sails out of bounds / behind a cutter) -- independent of any defender


def _logistic(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))


def resolve_pass(engine: PossessionEngine, intent: ActionIntent, context: PassResolutionContext,
                  rng: random.Random) -> str:
    """The single entry point. `intent.action_type` must be one of
    `PASS_ACTIONS` (Phase 16's already-selected pass intents) --
    resolution does not choose or re-choose the pass. Mutates
    `engine.state` via existing Phase 15 methods only."""
    if intent.action_type not in PASS_ACTIONS:
        raise ValueError(f"resolve_pass requires a pass-family ActionIntent, got {intent.action_type}")
    passer_id = intent.actor_player_id
    receiver_id = intent.target_player_id
    _assert_player_id(passer_id)
    _assert_player_id(receiver_id)
    if engine.state.ball_carrier != passer_id:
        raise ValueError("resolve_pass requires the passer to be the current ball carrier")
    if engine.state.ball_state != BallState.HELD:
        raise ValueError("resolve_pass requires the ball to be HELD before release")

    origin_zone = engine.state.ball_zone
    destination_zone = SpatialZone(intent.target_zone) if intent.target_zone else origin_zone
    family = classify_pass_family(origin_zone, destination_zone)

    engine.pass_ball(dt=0.0)  # HELD -> PASS_IN_FLIGHT; team possession untouched (Phase 15's own guarantee, reused)
    engine._log(EventType.REACTION_CHECKPOINT, 0.0, meta={"checkpoint": "release"})

    eligible = context.eligible_defenders if context.already_filtered else \
        default_eligible_defenders(engine.state, origin_zone, destination_zone, context.eligible_defenders)
    engine._log(EventType.REACTION_CHECKPOINT, 0.0, meta={"checkpoint": "geometric_eligibility_check",
                                                            "n_eligible": len(eligible)})

    flight_dt = FLIGHT_DURATION_SECONDS[family]
    # One authoritative competing-clock rule, shared with every other
    # live timing category. Pass outcomes resolve at arrival, so either
    # horn reached before arrival owns the terminal result.
    clock_advance = advance_live_clocks(
        flight_dt, engine.state.shot_clock_remaining, engine.state.game_clock_remaining,
    )
    engine.state = replace(
        engine.state,
        shot_clock_remaining=clock_advance.shot_clock_after,
        game_clock_remaining=clock_advance.game_clock_after,
    )
    clock_meta = {
        "pass_family": family,
        "nominal_flight_seconds": flight_dt,
        "actual_elapsed_seconds": clock_advance.actual_elapsed_seconds,
        "truncated_by_shot_clock_seconds": clock_advance.truncated_by_shot_clock_seconds,
        "truncated_by_period_clock_seconds": clock_advance.truncated_by_period_clock_seconds,
        "clock_terminal_cause": clock_advance.terminal_cause,
    }
    if clock_advance.terminal_cause == ClockTerminalCause.SHOT_CLOCK:
        engine._log(EventType.REACTION_CHECKPOINT, 0.0,
                    meta={"checkpoint": "disruption_attempt", "outcome": "NOT_RESOLVED_CLOCK_EXPIRATION"})
        engine.shot_clock_violation(dt=0.0)
        engine._log(
            EventType.PASS_RESOLVED, clock_advance.actual_elapsed_seconds,
            primary=passer_id, secondary=receiver_id,
            meta={**clock_meta, "outcome": "SHOT_CLOCK_VIOLATION_ON_ARRIVAL",
                  "disrupting_defender_id": None},
        )
        return "SHOT_CLOCK_VIOLATION_ON_ARRIVAL"
    if clock_advance.terminal_cause == ClockTerminalCause.PERIOD:
        engine._log(EventType.REACTION_CHECKPOINT, 0.0,
                    meta={"checkpoint": "disruption_attempt", "outcome": "NOT_RESOLVED_CLOCK_EXPIRATION"})
        engine.period_expiration(dt=0.0)
        engine._log(
            EventType.PASS_RESOLVED, clock_advance.actual_elapsed_seconds,
            primary=passer_id, secondary=receiver_id,
            meta={**clock_meta, "outcome": PassOutcome.PERIOD_EXPIRATION_DURING_FLIGHT,
                  "disrupting_defender_id": None},
        )
        return PassOutcome.PERIOD_EXPIRATION_DURING_FLIGHT

    outcome, disrupting_defender_id = _resolve_disruption_and_delivery(context, eligible, rng)
    engine._log(EventType.REACTION_CHECKPOINT, 0.0,
                meta={"checkpoint": "disruption_attempt", "outcome": outcome})
    _apply_outcome(engine, passer_id, receiver_id, disrupting_defender_id, destination_zone, outcome, flight_dt)
    engine._log(EventType.REACTION_CHECKPOINT, 0.0, meta={"checkpoint": "arrival"})

    if context.advantage is not None and context.advantage_updater is not None:
        engine.advantage = context.advantage_updater(context.advantage)
    # if no updater was supplied, engine.advantage is left EXACTLY as it was -- no automatic preserve/reset/decay

    engine._log(EventType.PASS_RESOLVED, clock_advance.actual_elapsed_seconds,
                primary=passer_id, secondary=receiver_id, zone=destination_zone,
                meta={**clock_meta, "outcome": outcome,
                      "disrupting_defender_id": disrupting_defender_id})
    return outcome


def _resolve_disruption_and_delivery(context: PassResolutionContext, eligible: List[DefenderCandidate],
                                      rng: random.Random) -> Tuple[str, Optional[str]]:
    # 1) an unforced, passer-attributed error -- independent of any defender, real per the module's own honest
    #    finding that geometry/burden (not modeled numerically here beyond family) dominates real bad-pass risk;
    #    passing_accuracy shifts this small, real, flagged-weak term.
    accuracy_term = 0.0
    if context.passing_accuracy is not None:
        accuracy_term = -PASSING_ACCURACY_WEIGHT * context.passing_accuracy  # higher accuracy -> lower unforced-error logit
    unforced_rate = _logistic(_logit(BASE_RATE_BAD_PASS_INDEPENDENT_OF_DEFENSE) + accuracy_term)
    if rng.random() < unforced_rate:
        return (PassOutcome.BAD_PASS_OUT_OF_BOUNDS if rng.random() < 0.5 else PassOutcome.BAD_PASS_TO_DEFENDER,
                None if rng.random() >= 0.5 else (eligible[0].defender_id if eligible else None))

    # 2) each eligible defender gets ONE independent disruption-attempt roll -- no cross-zone teleportation,
    #    no automatic steal; at most the FIRST successful attempt (in caller-supplied order) matters.
    disruption_base_rate = BASE_RATE_ANY_DISRUPTION_ATTEMPT \
        if context.disruption_base_rate is None else context.disruption_base_rate
    for defender in eligible:
        attempt_rate = disruption_attempt_probability(disruption_base_rate, defender)
        if rng.random() < attempt_rate:
            roll = rng.random()
            if roll < BASE_RATE_DISRUPTION_IS_CLEAN_INTERCEPTION:
                return PassOutcome.CLEAN_INTERCEPTION, defender.defender_id
            elif roll < BASE_RATE_DISRUPTION_IS_CLEAN_INTERCEPTION + BASE_RATE_DISRUPTION_IS_LOOSE_BALL:
                return PassOutcome.DEFLECTED_LOOSE_BALL, defender.defender_id
            else:
                return PassOutcome.DEFLECTED_RETAINED_OFFENSE, defender.defender_id

    # 3) no disruption at all -- arrival quality (the ONE downstream consequence of delivery quality; no second penalty)
    # NOTE: `accuracy_term` above is signed NEGATIVE-for-higher-accuracy (it lowers an ERROR rate in step 1) --
    # here we want the OPPOSITE direction (higher accuracy -> MORE clean arrivals), so it is subtracted, not added.
    arrival_quality_rate = _logistic(0.5 - accuracy_term)
    return (PassOutcome.COMPLETED_CLEAN if rng.random() < arrival_quality_rate else PassOutcome.COMPLETED_ADJUSTED), None


def _logit(p: float) -> float:
    import math
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _apply_outcome(engine: PossessionEngine, passer_id: str, receiver_id: str, disrupting_defender_id: Optional[str],
                    destination_zone: SpatialZone, outcome: str, flight_dt: float) -> None:
    if outcome in (PassOutcome.COMPLETED_CLEAN, PassOutcome.COMPLETED_ADJUSTED):
        engine.state = engine.state.with_ball_carrier(receiver_id, BallState.HELD)
        engine.advance_ball_zone(destination_zone)
        return
    if outcome == PassOutcome.CLEAN_INTERCEPTION:
        # a real, possession-changing recovery -- STL is attributable (accounting layer's job to record it as such)
        engine.state = engine.state.with_ball_carrier(disrupting_defender_id, BallState.HELD)
        old_offense, old_defense = engine.state.offense_team_id, engine.state.defense_team_id
        from dataclasses import replace as _replace
        engine.state = _replace(engine.state, offense_team_id=old_defense, defense_team_id=old_offense)
        return
    if outcome == PassOutcome.DEFLECTED_LOOSE_BALL:
        engine.state = engine.state.with_ball_carrier(None, BallState.LOOSE)
        from dataclasses import replace as _replace
        engine.state = _replace(engine.state, offense_team_id=None)  # genuinely unresolved, same convention as Phase 15's block_secured_by_defense
        return
    if outcome == PassOutcome.DEFLECTED_RETAINED_OFFENSE:
        # ball continues live and loose but the offense remains favored/attributed -- no team-possession flip,
        # no STL, no TOV; a future phase may model a brief scramble, this phase only preserves offense's claim.
        engine.state = engine.state.with_ball_carrier(None, BallState.LOOSE)
        return
    if outcome == PassOutcome.BAD_PASS_OUT_OF_BOUNDS:
        from dataclasses import replace as _replace
        old_offense, old_defense = engine.state.offense_team_id, engine.state.defense_team_id
        engine.state = _replace(engine.state.with_ball_carrier(None, BallState.DEAD),
                                 offense_team_id=old_defense, defense_team_id=old_offense)
        return
    if outcome == PassOutcome.BAD_PASS_TO_DEFENDER:
        target = disrupting_defender_id
        if target is not None:
            engine.state = engine.state.with_ball_carrier(target, BallState.HELD)
            from dataclasses import replace as _replace
            old_offense, old_defense = engine.state.offense_team_id, engine.state.defense_team_id
            engine.state = _replace(engine.state, offense_team_id=old_defense, defense_team_id=old_offense)
        else:
            engine.state = engine.state.with_ball_carrier(None, BallState.LOOSE)
        return
    raise ValueError(f"unhandled pass outcome {outcome!r}")
