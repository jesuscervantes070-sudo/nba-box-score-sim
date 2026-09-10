"""
Phase 21A -- On-Ball Contact, Strips, Reach-Ins & Pre-Shot Collisions.

BALL HANDLER HAS CONTROL -> DEFENDER ACTIVELY INTERRUPTS -> CONTROL/
CONTACT/FOUL CONSEQUENCE. Operates ONLY BEFORE gather/shooting motion
begins (`DribbleState.LIVE_DRIBBLE`) -- once the ball is `GATHERED` or
in a shooting motion, Phase 18C owns any contact. This module raises if
called on a non-live-dribble state, making the handoff boundary a real,
enforced precondition, not just a documented convention.

============================ THE THREE-BOUNDARY FIREWALL ============================
Phase 17A ownership (ordinary containment/leverage: CLEAN_PENETRATION/
PARTIAL_EDGE/CONTAINED/its own clean FORCED_PICKUP) is UNTOUCHED --
this module does not import or call `drive_resolution.py` at all. Phase
21A owns a DIFFERENT causal slice: active strip/reach/collision
INTERRUPTIONS, not ordinary POA leverage.

Phase 17B ownership (bad-pass turnovers) is UNTOUCHED -- `ball_security`
is never used to fix a bad pass, and `passing_accuracy` never appears
anywhere in this module (verified by namespace scan).

Phase 18C ownership (shooting-motion contact) is UNTOUCHED -- this
module's own precondition check (`ball_control.state == LIVE_DRIBBLE`)
structurally prevents the same contact from ever being evaluated by
both modules; once gathered, only Phase 18C's contact/whistle logic
applies.

============================ CONSTRUCT AUDITS (see docs/PHASE21A_ON_BALL_PRESSURE_REPORT.md) ============================
`ball_security` (Phase 4A/4B, KEEP BUT FLAG): real handling-error rate
(`handling_error / estimated_total_dribbles`) -- confirmed by direct
source read to ALREADY exclude bad-pass turnovers by construction
(`handling_error` is the real `CATEGORY_HANDLING`/"Lost Ball" PBP
subtype bucket specifically, Phase 4A's own taxonomy) -- this is
EXACTLY the "live-ball control loss" concept 21A needs, reused as-is,
not re-derived. Real 2023-24 population: mean 0.00858, stdev 0.00737
(n=392, `estimated_total_dribbles` >= 500).

`defensive_playmaking` (Phase 1-3, KEEP/STRONG): real STL+BLK per-36 --
already known (Phase 17B/18B) to combine two real, only-partially-
overlapping defensive event types. **No live isolation study
(controlling for team scheme/opponent ball-handling/role) was performed
this phase given time constraints** -- per the task's own explicit
allowance ("if you cannot isolate strip-specific signal, use limited
authority and flag"), this module gives it a DELIBERATELY SMALL weight
in the strip/disruption branch, flagged KEEP BUT FLAG for this specific
use (distinct from its existing, unchanged classification for blocks/
defensive events generally).
"""
import random
from dataclasses import dataclass
from typing import Optional

from possession_engine import PossessionEngine
from possession_events import EventType
from possession_state import BallState, DefensivePosture, DribbleState, _assert_player_id

# ---------------------------------------------------------------------
# Real population normalization -- ball_security computed live this
# phase (2023-24, n=392, real `estimated_total_dribbles` >= 500 exposure
# floor, reusing Phase 4A/4B's own real denominator). defensive_playmaking
# stats reused from Phase 17B/18B's own already-computed real population
# (mean=1.5, stdev=0.7 STL+BLK/36). NOT re-derived per season/era --
# explicit, flagged placeholders, same convention as every prior
# resolution phase.
# ---------------------------------------------------------------------
BALL_SECURITY_POPULATION_MEAN = 0.00858   # handling-error rate -- HIGHER means WORSE security
BALL_SECURITY_POPULATION_STDEV = 0.00737
DEFENSIVE_PLAYMAKING_POPULATION_MEAN = 1.5
DEFENSIVE_PLAYMAKING_POPULATION_STDEV = 0.7

# Deliberately SMALL weight -- per the explicit "no isolated strip-
# specific signal was verified this phase" finding (module docstring).
DEFENSIVE_PLAYMAKING_STRIP_WEIGHT = 0.35
FOUL_DISCIPLINE_WEIGHT = -0.15   # small, same posture as Phase 18C's own deliberately-small foul-attribute weights
FOUL_DRAWING_WEIGHT = 0.15       # small -- affects ONLY the whistle/no-call split, never contact existence itself

_PROB_EPSILON = 0.005


def _logit(p: float) -> float:
    import math
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))


def _z(value: Optional[float], mean: float, stdev: float) -> float:
    return (value - mean) / stdev if value is not None else 0.0  # missing -> zero Z-CONTRIBUTION, never a fabricated skill value


class OnBallContactOutcome:
    """Every value describes a CONTROL/CONTACT state change -- never a
    scoring outcome. Not a locked Enum, per this project's own
    established convention (Phases 17A/18B) for representations that
    may need real-evidence-driven additions later."""
    CLEAN_CONTROL = "CLEAN_CONTROL"                # no meaningful interruption -- dribble continues unaffected
    DISRUPTED = "DISRUPTED"                        # dribble worsens (structural advantage may shrink) but stays live and under offensive control
    FORCED_PICKUP = "FORCED_PICKUP"                # pressure forces the dribble dead -- reuses Phase 15's own DribbleState.DEAD_DRIBBLE, NOT a turnover
    CLEAN_STRIP_LOOSE = "CLEAN_STRIP_LOOSE"        # ball knocked loose -- BallState.LOOSE, no immediate carrier, no team-possession assumption
    OFFENSIVE_CHARGE = "OFFENSIVE_CHARGE"          # offensive player-control foul -- live-ball turnover, no FGA, no steal credited
    DEFENSIVE_FLOOR_FOUL = "DEFENSIVE_FLOOR_FOUL"  # whistle on the defender -- offense retains the ball; bonus/FT administration is explicitly Phase 21B's job
    NO_CALL_CONTACT = "NO_CALL_CONTACT"            # real contact occurred, no whistle, no control change -- play continues


@dataclass
class OnBallPressureContext:
    """Every ability field is Optional and independently gate-able --
    missing != zero, it means 'no adjustment from this source.' No
    physical field exists (mass/height/reach/wingspan) -- per explicit
    instruction, none was validated as adding independent signal this
    phase, so none is even representable here (a structural, not just
    behavioral, exclusion)."""
    ball_security: Optional[float] = None            # real Phase 4A/4B rate for the BALL HANDLER (offense)
    defensive_playmaking: Optional[float] = None      # real Phase 1-3 STL+BLK/36 for the PRESSURING defender -- SMALL weight (see module docstring)
    defender_posture: DefensivePosture = DefensivePosture.SQUARE
    foul_discipline: Optional[float] = None           # real Phase 6 estimate for the defender -- small weight, whistle branch only
    foul_drawing: Optional[float] = None              # real Phase 6 estimate for the ball handler -- small weight, whistle branch only
    contact_established: bool = False                 # STRUCTURAL fact: is a collision/reach geometrically occurring at all -- set by the caller from real state, never invented here


# Real, hand-set, EXPLICITLY FLAGGED placeholder base rates -- no public
# per-possession on-ball-pressure-outcome dataset exists (same honest
# limitation as every prior resolution phase). Direction only is
# defended.
BASE_DISRUPTION_LOGIT = -1.6        # ~17% baseline chance of ANY meaningful disruption per pressured touch
BASE_FORCED_PICKUP_GIVEN_DISRUPTED = 0.35
BASE_STRIP_GIVEN_DISRUPTED = 0.20   # remainder of a disruption stays DISRUPTED (dribble worsens but stays live)

# Collision-context base rates -- only consulted when `contact_established=True`
BASE_NO_CALL_GIVEN_CONTACT = 0.55
BASE_OFFENSIVE_CHARGE_GIVEN_CONTACT = 0.20
BASE_DEFENSIVE_FOUL_GIVEN_CONTACT = 0.25


def _posture_disruption_delta(posture: DefensivePosture) -> float:
    # A defender who is SQUARE/set is structurally more able to pressure/strip; TRAILING/RECOVERING/HELPING
    # are less able to -- direction only defended, same posture as every prior phase's posture deltas.
    return {DefensivePosture.SQUARE: 0.3, DefensivePosture.RECOVERING: 0.0,
            DefensivePosture.TRAILING: -0.4, DefensivePosture.HELPING: -0.4}.get(posture, 0.0)


def resolve_on_ball_pressure(ball_handler_id: str, defender_id: str, context: OnBallPressureContext,
                              rng: random.Random) -> str:
    """Pure resolution function -- no engine mutation, no RNG beyond
    what's passed in. Two INDEPENDENT branches: a live-dribble-pressure
    branch (disruption/pickup/strip) and, ONLY if `contact_established`,
    a separate collision-classification branch (no-call/charge/
    defensive foul) -- kept structurally distinct per the required
    CONTACT != WHISTLE separation."""
    _assert_player_id(ball_handler_id)
    _assert_player_id(defender_id)

    if context.contact_established:
        return _resolve_collision(context, rng)
    return _resolve_pressure(context, rng)


def _resolve_pressure(context: OnBallPressureContext, rng: random.Random) -> str:
    logit = BASE_DISRUPTION_LOGIT
    if context.ball_security is not None:
        # HIGHER ball_security rate = WORSE security (real handling-error rate) -> MORE disruption risk.
        logit += _z(context.ball_security, BALL_SECURITY_POPULATION_MEAN, BALL_SECURITY_POPULATION_STDEV)
    if context.defensive_playmaking is not None:
        logit += DEFENSIVE_PLAYMAKING_STRIP_WEIGHT * _z(
            context.defensive_playmaking, DEFENSIVE_PLAYMAKING_POPULATION_MEAN, DEFENSIVE_PLAYMAKING_POPULATION_STDEV)
    logit += _posture_disruption_delta(context.defender_posture)

    p_disruption = min(max(_sigmoid(logit), _PROB_EPSILON), 1.0 - _PROB_EPSILON)
    if rng.random() >= p_disruption:
        return OnBallContactOutcome.CLEAN_CONTROL

    roll = rng.random()
    if roll < BASE_STRIP_GIVEN_DISRUPTED:
        return OnBallContactOutcome.CLEAN_STRIP_LOOSE
    elif roll < BASE_STRIP_GIVEN_DISRUPTED + BASE_FORCED_PICKUP_GIVEN_DISRUPTED:
        return OnBallContactOutcome.FORCED_PICKUP
    else:
        return OnBallContactOutcome.DISRUPTED


def _resolve_collision(context: OnBallPressureContext, rng: random.Random) -> str:
    logit_no_call = _logit(BASE_NO_CALL_GIVEN_CONTACT)
    # foul_drawing/foul_discipline shift the whistle-vs-no-call split ONLY -- never whether contact occurred (already fixed by the caller).
    if context.foul_drawing is not None:
        logit_no_call -= FOUL_DRAWING_WEIGHT * context.foul_drawing
    if context.foul_discipline is not None:
        logit_no_call -= FOUL_DISCIPLINE_WEIGHT * context.foul_discipline
    p_no_call = min(max(_sigmoid(logit_no_call), _PROB_EPSILON), 1.0 - _PROB_EPSILON)

    if rng.random() < p_no_call:
        return OnBallContactOutcome.NO_CALL_CONTACT

    remaining = BASE_OFFENSIVE_CHARGE_GIVEN_CONTACT + BASE_DEFENSIVE_FOUL_GIVEN_CONTACT
    roll = rng.random() * remaining
    if roll < BASE_OFFENSIVE_CHARGE_GIVEN_CONTACT:
        return OnBallContactOutcome.OFFENSIVE_CHARGE
    return OnBallContactOutcome.DEFENSIVE_FLOOR_FOUL


@dataclass
class FoulPacket:
    """The minimal, clean handoff packet for Phase 21B -- this module
    does NOT administer team fouls, bonus state, or FT consequences
    itself."""
    offender_id: str
    fouled_player_id: str
    foul_class: str    # "OFFENSIVE_CHARGE" | "DEFENSIVE_FLOOR_FOUL"
    live_ball: bool     # False for both (both stop play) -- kept as an explicit field for Phase 21B's own real semantics, not assumed


def apply_on_ball_pressure_to_engine(engine: PossessionEngine, ball_handler_id: str, defender_id: str,
                                      context: OnBallPressureContext, rng: random.Random,
                                      team_foul_count: int = 0) -> str:
    """Requires the ball handler to be the current carrier with a real
    LIVE dribble -- this precondition IS the Phase 18C handoff boundary,
    enforced, not just documented. Applies the outcome via existing
    Phase 15 engine methods only (`dead_ball_turnover` for a charge --
    reused verbatim; `non_shooting_foul` for a defensive floor foul --
    reused verbatim, including its own real bonus-check return value)."""
    if engine.state.ball_carrier != ball_handler_id:
        raise ValueError("resolve_on_ball_pressure requires the ball handler to be the current carrier")
    if engine.state.ball_control is None or engine.state.ball_control.state != DribbleState.LIVE_DRIBBLE:
        raise ValueError("on-ball pressure only applies before gather/shooting motion -- Phase 18C owns post-gather contact")

    outcome = resolve_on_ball_pressure(ball_handler_id, defender_id, context, rng)

    from dataclasses import replace
    if outcome == OnBallContactOutcome.CLEAN_CONTROL:
        pass  # no state change at all
    elif outcome == OnBallContactOutcome.DISRUPTED:
        pass  # dribble stays LIVE_DRIBBLE; a caller may separately reduce structural advantage via the existing AdvantageModel interface
    elif outcome == OnBallContactOutcome.FORCED_PICKUP:
        engine.dead_dribble(dt=0.0)
    elif outcome == OnBallContactOutcome.CLEAN_STRIP_LOOSE:
        engine.state = replace(engine.state, ball_state=BallState.LOOSE, ball_carrier=None,
                                ball_control=None, offense_team_id=None)
    elif outcome == OnBallContactOutcome.OFFENSIVE_CHARGE:
        engine.dead_ball_turnover(ball_handler_id, dt=0.0)
    elif outcome == OnBallContactOutcome.DEFENSIVE_FLOOR_FOUL:
        engine.non_shooting_foul(defender_id, ball_handler_id, team_foul_count, dt=0.0)
    elif outcome == OnBallContactOutcome.NO_CALL_CONTACT:
        pass
    else:
        raise ValueError(f"unhandled on-ball pressure outcome {outcome!r}")

    engine._log(EventType.REACTION_CHECKPOINT, 0.0, primary=ball_handler_id, secondary=defender_id,
                meta={"checkpoint": "on_ball_pressure_resolved", "outcome": outcome})
    return outcome
