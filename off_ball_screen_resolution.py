"""
Phase 22A -- Off-Ball Screen Interaction (structural primitive).

OFF-BALL STRUCTURAL INTERACTION -> DEFENSIVE ASSIGNMENT/POSTURE/ZONE-
COMPROMISE CONSEQUENCE -> return to ordinary Phase 16
(`action_opportunity.generate_opportunities` -> `action_perception.perceive`
-> `action_selection.SelectionPolicy.select`) -> ordinary pass/shot/drive
resolution. This module creates WORLD STATE only -- it never awards a
pass, a shot, an assist, a score, or a possession change itself, and it
is NOT a second detection engine for anything Phase 16/17/18/21A/21B
already own.

============================ WHY A STRUCTURAL INTERACTION, NOT A SKILL-RATING SYSTEM ============================
This project's current state representation has NO continuous
coordinates, screen angle, body orientation, defender pathing, or
over/under-screen navigation concept (confirmed by direct inspection of
`possession_state.py` -- coarse `SpatialZone`s, a `ball_zone`, four
coarse `DefensivePosture` values, and defender->offender pointer
`assignments`, nothing finer). A "screen quality" or "screen navigation"
RATING would therefore have to be invented wholesale with no supporting
geometry for it to act on -- exactly what Gemini's study and this task's
own instruction explicitly rejected. What the state representation DOES
already support -- posture change, an atomic assignment exchange, and a
zone-scoped `AdvantageModel` compromise -- is exactly the vocabulary this
module resolves into. No new player-owned rating, tendency, or physical
formula is introduced anywhere in this file (verified:
`test_no_new_ability_or_physical_fields`).

============================ THE FOUR FIREWALLS ============================
`poa_containment` (Phase 10, on-ball/primary-matchup containment),
`perimeter_space_creation` (on-ball/self-created perimeter separation),
`defensive_playmaking` (disruption/event creation, e.g. steals/blocks),
and `foul_discipline` (defensive foul discipline) are NOT referenced
anywhere in this module (verified: `test_attribute_firewalls`, a
namespace scan). None of them is repurposed as screen-navigation,
off-ball separation, switch IQ, or illegal-screen discipline -- the
explicit repurposing this task rejected. The only structural context
`OffBallScreenContext` reads is `DefensivePosture` (already-existing
Phase 15 state, reused the same way Phase 21A already reuses it for
pressure/collision resolution) plus an OPTIONAL, caller-supplied
`coverage_instruction` string -- never a hidden ability value.

============================ RNG / CALIBRATION POSTURE ============================
When `coverage_instruction` is not the recognized `"SWITCH"` override,
`resolve_off_ball_screen` samples among three coarse outcomes using a
real, HAND-SET, EXPLICITLY FLAGGED placeholder weight table (module
docstring convention identical to every prior resolution phase -- Phase
17A/18B/18C/21A all carry the same honest-placeholder posture). The
ONLY structural input shifting these weights is `DefensivePosture`
(already-existing state) -- no role, tendency, or physical value enters
this calculation, and no probability here is claimed as empirically
calibrated. RNG is a caller-supplied `random.Random` only (never the
global `random` module), matching every other resolver in this
codebase.
"""
import random
from dataclasses import dataclass, replace
from typing import Dict, Optional

from possession_advantage import AdvantageModel, DiscreteTierAdvantage, SpatialMagnitudeAdvantage
from possession_engine import PossessionEngine
from possession_events import EventType
from possession_state import DefensivePosture, SpatialZone, _assert_player_id

# ---------------------------------------------------------------------
# The one recognized external coverage instruction -- a real, coarse,
# binary hook ("the defense pre-committed to switching this") rather
# than a hedge/drop/blitz/trap taxonomy (explicitly rejected). Any other
# string (or None) falls through to the structural/placeholder model
# below. Not an enum -- kept as a plain string per this module's own
# smallest-vocabulary posture and this project's established convention
# (Phase 17A/18B/21A) of not locking taxonomies that may need real-
# evidence-driven additions later.
# ---------------------------------------------------------------------
SWITCH_COVERAGE_INSTRUCTION = "SWITCH"


class OffBallScreenOutcome:
    """Three outcomes -- deliberately collapsing the task's own
    5-item illustrative list: `DEFENDER_ATTACHED` and `NO_EFFECT`/
    `RECOVERED` are mechanically IDENTICAL under this state
    representation (no assignment/posture/advantage change either way),
    so they are ONE outcome here, not two (per "use the smallest result
    vocabulary... do not create a huge taxonomy"). `ZONE_COMPROMISED`/
    `SEPARATION_CREATED` is folded into `TRAILING_SEPARATION`'s own
    consequence (a real separation IS the zone compromise, not a
    separate roll) rather than a fourth branch."""
    NO_EFFECT = "NO_EFFECT"                        # defender(s) remain effectively attached -- no assignment/posture/advantage change
    TRAILING_SEPARATION = "TRAILING_SEPARATION"    # receiver's defender posture -> TRAILING; optional AdvantageModel compromise at the destination zone
    SWITCH = "SWITCH"                              # atomic two-defender assignment exchange -- no advantage effect asserted in V1 (no mismatch judgment is made, see report)


@dataclass
class OffBallScreenContext:
    """Every field is a real, structural, caller-supplied fact -- never
    a hidden ability value. Phase 22A does NOT choose WHO sets or uses a
    screen (no participant-selection AI) -- the caller already decided
    that and supplies the four real participant ids here."""
    screener_id: str
    screener_defender_id: str
    moving_receiver_id: str
    receiver_defender_id: str
    origin_zone: SpatialZone
    destination_zone: SpatialZone
    screener_defender_posture: DefensivePosture = DefensivePosture.SQUARE
    receiver_defender_posture: DefensivePosture = DefensivePosture.SQUARE
    coverage_instruction: Optional[str] = None  # e.g. "SWITCH" -- see SWITCH_COVERAGE_INSTRUCTION; None = structural/placeholder resolution decides


# Real, hand-set, EXPLICITLY FLAGGED placeholder base weights -- no public
# per-off-ball-screen-outcome dataset exists (same honest limitation as
# every prior resolution phase). Direction only is defended: a defender
# already out of position (TRAILING/RECOVERING/HELPING) going into the
# screen is more likely to end up separated by it; a screener's defender
# already out of position is more likely to produce a spontaneous switch.
_BASE_WEIGHTS = {
    OffBallScreenOutcome.NO_EFFECT: 0.45,
    OffBallScreenOutcome.TRAILING_SEPARATION: 0.40,
    OffBallScreenOutcome.SWITCH: 0.15,
}
_OUT_OF_POSITION_POSTURES = (DefensivePosture.TRAILING, DefensivePosture.RECOVERING, DefensivePosture.HELPING)


def _outcome_weights(context: OffBallScreenContext) -> Dict[str, float]:
    weights = dict(_BASE_WEIGHTS)
    if context.receiver_defender_posture in _OUT_OF_POSITION_POSTURES:
        weights[OffBallScreenOutcome.NO_EFFECT] -= 0.15
        weights[OffBallScreenOutcome.TRAILING_SEPARATION] += 0.15
    if context.screener_defender_posture in _OUT_OF_POSITION_POSTURES:
        weights[OffBallScreenOutcome.NO_EFFECT] -= 0.05
        weights[OffBallScreenOutcome.SWITCH] += 0.05
    for key in weights:
        weights[key] = max(weights[key], 0.01)  # never a literal-zero/negative weight from the adjustments above
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def resolve_off_ball_screen(context: OffBallScreenContext, rng: random.Random) -> str:
    """Pure resolution function -- no engine mutation, no RNG beyond
    what's passed in. A recognized `coverage_instruction` deterministically
    decides the outcome (no roll at all -- an externally pre-committed
    defensive call is not a probabilistic event); otherwise the coarse,
    posture-only placeholder weight table above is sampled."""
    if context.coverage_instruction == SWITCH_COVERAGE_INSTRUCTION:
        return OffBallScreenOutcome.SWITCH

    weights = _outcome_weights(context)
    roll = rng.random()
    cumulative = 0.0
    outcome = OffBallScreenOutcome.NO_EFFECT
    for outcome, weight in weights.items():
        cumulative += weight
        if roll < cumulative:
            return outcome
    return outcome  # floating-point fallback -- last outcome in iteration order


def _validate_context_matches_state(engine: PossessionEngine, context: OffBallScreenContext) -> None:
    """FAIL EXPLICITLY BEFORE ANY MUTATION if the caller-declared
    participants don't match the engine's own real assignment state --
    "missing/malformed assignments fail explicitly," never a silently
    fabricated or impossible matchup."""
    if context.screener_id == context.moving_receiver_id:
        raise ValueError("screener_id and moving_receiver_id must be two distinct real players")
    if context.screener_defender_id == context.receiver_defender_id:
        raise ValueError("screener_defender_id and receiver_defender_id must be two distinct real defenders")

    screener_assignment = engine.state.assignments.get(context.screener_defender_id)
    if screener_assignment is None or screener_assignment.assigned_to_player_id != context.screener_id:
        raise ValueError(
            f"screener_defender_id {context.screener_defender_id!r} is not currently assigned to "
            f"screener_id {context.screener_id!r} in engine.state.assignments")

    receiver_assignment = engine.state.assignments.get(context.receiver_defender_id)
    if receiver_assignment is None or receiver_assignment.assigned_to_player_id != context.moving_receiver_id:
        raise ValueError(
            f"receiver_defender_id {context.receiver_defender_id!r} is not currently assigned to "
            f"moving_receiver_id {context.moving_receiver_id!r} in engine.state.assignments")


def _atomic_switch_assignments(engine: PossessionEngine, defender_a_id: str, defender_b_id: str,
                                posture: DefensivePosture = DefensivePosture.RECOVERING, dt: float = 0.0) -> None:
    """The SMALLEST SAFE atomic two-defender assignment transaction --
    NOT two sequential calls to `PossessionEngine.switch()` (which is a
    single-pointer mutation with no cross-defender invariant check: two
    independent calls would, for one call's duration, leave the state
    with the OLD second defender still pointed at the just-reassigned
    offensive player -- a real duplicate-assignment window an observer
    calling `generate_opportunities` between the two calls could see).
    Here, the ENTIRE new `assignments` dict is computed first and
    applied via exactly ONE `replace()` -- no intermediate state is ever
    observable, and both preconditions (both defenders real and
    currently assigned, and to two DIFFERENT offensive players) are
    checked BEFORE that single replace() -- a violation raises with
    ZERO mutation, never a partial swap."""
    _assert_player_id(defender_a_id)
    _assert_player_id(defender_b_id)
    if defender_a_id == defender_b_id:
        raise ValueError("cannot switch a defender's assignment with itself")

    assignments = engine.state.assignments
    a = assignments.get(defender_a_id)
    b = assignments.get(defender_b_id)
    if a is None or b is None:
        raise ValueError(
            f"both defenders must already have a real assignment to switch -- "
            f"{defender_a_id!r}: {'present' if a else 'MISSING'}, {defender_b_id!r}: {'present' if b else 'MISSING'}")
    if a.assigned_to_player_id == b.assigned_to_player_id:
        raise ValueError(
            "cannot switch -- both defenders are already assigned to the same offensive player "
            "(a pre-existing invalid one-to-many matchup state); refusing to compound it")

    new_assignments = dict(assignments)
    new_assignments[defender_a_id] = a.switch(b.assigned_to_player_id, posture)
    new_assignments[defender_b_id] = b.switch(a.assigned_to_player_id, posture)
    engine.state = replace(engine.state, assignments=new_assignments)  # ONE atomic transition -- no intermediate state observable

    engine._log(EventType.ASSIGNMENT_SWITCH, dt, primary=defender_a_id,
                secondary=new_assignments[defender_a_id].assigned_to_player_id)
    engine._log(EventType.ASSIGNMENT_SWITCH, dt, primary=defender_b_id,
                secondary=new_assignments[defender_b_id].assigned_to_player_id)


def _compromise_zone(advantage: AdvantageModel, zone: SpatialZone) -> AdvantageModel:
    """Reuses `AdvantageModel.compound()` generically across whichever
    concrete representation the caller/engine already chose (Phase 15's
    own interface -- neither candidate is picked as final here). A real,
    hand-set, explicitly flagged SMALLEST-real-tier/placeholder magnitude
    is compounded in -- no exact separation distance, defender
    displacement, or screen-quality number is claimed anywhere."""
    if isinstance(advantage, DiscreteTierAdvantage):
        delta: AdvantageModel = DiscreteTierAdvantage(tiers={zone: "TILTED"})
    elif isinstance(advantage, SpatialMagnitudeAdvantage):
        delta = SpatialMagnitudeAdvantage(magnitudes={zone: 0.3})
    else:
        raise TypeError(f"unsupported AdvantageModel implementation {type(advantage)!r} for off-ball screen compromise")
    return advantage.compound(delta)


def apply_off_ball_screen_to_engine(engine: PossessionEngine, context: OffBallScreenContext,
                                     rng: random.Random) -> str:
    """The single entry point. Resolves the coarse structural outcome,
    then applies ONLY the real Phase 15 state changes that outcome
    licenses -- posture (`engine.update_posture`, reused verbatim),
    assignments (`_atomic_switch_assignments`, above), and/or
    `engine.advantage` (reused via `compound()`, never a new
    representation). Never calls `begin_shot`/`pass_ball`/any scoring or
    possession-change method -- verified structurally
    (`test_no_direct_scoring_effect`, a namespace scan of this module for
    any such reference) and behaviorally (every outcome-path test in
    `test_off_ball_screen_resolution.py` asserts `ball_state`/
    `ball_carrier`/`offense_team_id`/`defense_team_id` are untouched)."""
    _validate_context_matches_state(engine, context)

    outcome = resolve_off_ball_screen(context, rng)

    if outcome == OffBallScreenOutcome.NO_EFFECT:
        pass  # no state change at all -- structure resets cleanly, no fabricated impact
    elif outcome == OffBallScreenOutcome.TRAILING_SEPARATION:
        engine.update_posture(context.receiver_defender_id, DefensivePosture.TRAILING)
        if engine.advantage is not None:
            # Only compromised when the caller/engine already carries a real AdvantageModel instance --
            # this module never fabricates a representation choice on the caller's behalf (Phase 15's
            # own "no representation chosen yet" is left exactly as-is when engine.advantage is None).
            engine.advantage = _compromise_zone(engine.advantage, context.destination_zone)
    elif outcome == OffBallScreenOutcome.SWITCH:
        _atomic_switch_assignments(engine, context.screener_defender_id, context.receiver_defender_id)
        # No AdvantageModel effect asserted for a clean switch in V1 -- a real mismatch judgment
        # would require comparing player-specific attributes this phase is explicitly barred from
        # inventing (screen-navigation/switch-IQ ratings); a bare assignment exchange alone is not,
        # by itself, a claimed structural compromise.
    else:
        raise ValueError(f"unhandled off-ball screen outcome {outcome!r}")

    engine._log(EventType.REACTION_CHECKPOINT, 0.0, primary=context.screener_id, secondary=context.moving_receiver_id,
                meta={"checkpoint": "off_ball_screen_resolved", "outcome": outcome})
    return outcome
