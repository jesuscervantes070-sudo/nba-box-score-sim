"""
Phase 16 -- Action Selection & Opportunity Generation: OBJECTIVE
opportunity generation.

WORLD OPPORTUNITY layer. An `ObjectiveOpportunity` is real, structural,
and exists (or doesn't) independent of whether any player has perceived
it -- see action_perception.py for the next layer. Nothing here reads a
hidden ability value; only Phase 15 state (ball state, dribble control,
zone, defensive posture/assignment, advantage) and caller-supplied
structural context (teammate positions, a live screen/roll flag) are
consulted.

Opportunities are generated LAZILY from the active state -- this module
does not enumerate every theoretical cut/lane/screen; it evaluates a
fixed, small set of real structural preconditions per action type
against the CURRENT state only.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from action_intent import ActionType
from possession_advantage import AdvantageModel
from possession_state import (
    BallState, DefensivePosture, DribbleState, PossessionPhase, PossessionState, SpatialZone,
)

PERIMETER_ZONES = frozenset({SpatialZone.TOP_OF_KEY, SpatialZone.LEFT_WING, SpatialZone.RIGHT_WING,
                              SpatialZone.LEFT_CORNER, SpatialZone.RIGHT_CORNER})
INTERIOR_ZONES = frozenset({SpatialZone.PAINT, SpatialZone.RESTRICTED_RIM})


@dataclass(frozen=True)
class ObjectiveOpportunity:
    """A real, structural possibility -- not yet filtered by perception,
    not yet chosen by policy. `opportunity_id` is stable within one
    `generate_opportunities` call so `action_perception.py`/
    `action_selection.py` can reference it without re-deriving it."""
    opportunity_id: str
    action_type: ActionType
    actor_player_id: str
    target_player_id: Optional[str] = None
    target_zone: Optional[SpatialZone] = None
    source: str = ""  # diagnostic: which structural condition produced this (e.g. "advantage:collapsed_paint")


@dataclass
class StructuralContext:
    """Caller-supplied, non-ability structural facts this phase is
    allowed to consult. Everything here is either Phase-15 state,
    Phase-13's already-validated KEEP-tier role signal presence, or a
    plain structural flag -- never a hidden ability value. All fields
    optional/default-empty so a caller can supply only what it has
    (missing != treated as zero-opportunity by default; each generator
    function below decides its own honest default)."""
    teammate_ids: List[str] = field(default_factory=list)              # real player_ids, any teammates on the floor
    perimeter_receiver_ids: Dict[str, SpatialZone] = field(default_factory=dict)  # teammate_id -> the perimeter zone they occupy, if any
    roller_id: Optional[str] = None            # a teammate currently rolling/popping in a live PnR action, if any
    screen_active: bool = False                # a live on-ball screen/DHO is currently engaged
    nearest_teammate_id: Optional[str] = None  # for the "obvious safety reset/swing" case
    just_caught_pass: bool = False             # True only on the frame immediately after a reception -- gates CATCH_AND_SHOOT/CLOSEOUT_ATTACK
    ball_handler_defender_id: Optional[str] = None  # the real defender currently assigned to the ball handler, if known


def generate_opportunities(state: PossessionState, context: StructuralContext,
                            advantage: Optional[AdvantageModel] = None) -> List[ObjectiveOpportunity]:
    """The single entry point. Returns only mechanically-possible
    opportunities for the CURRENT state -- never a fixed menu, never
    exactly two, never a full combinatorial enumeration."""
    opportunities: List[ObjectiveOpportunity] = []
    _idx = [0]

    def _next_id(action_type: ActionType) -> str:
        _idx[0] += 1
        return f"{state.possession_id}:{action_type.value}:{_idx[0]}"

    # ---- LOOSE ball: only a recovery action exists, for ANY player near it -- no normal offensive menu ----
    if state.ball_state == BallState.LOOSE:
        for pid in context.teammate_ids:
            opportunities.append(ObjectiveOpportunity(_next_id(ActionType.RECOVER_LOOSE_BALL),
                                                        ActionType.RECOVER_LOOSE_BALL, actor_player_id=pid, source="ball_loose"))
        return opportunities  # nothing else is mechanically possible while the ball is loose

    # ---- PASS_IN_FLIGHT / SHOT_IN_FLIGHT / DEAD: no player has ball control, so no ball-handler menu exists ----
    if state.ball_state in (BallState.PASS_IN_FLIGHT, BallState.SHOT_IN_FLIGHT, BallState.DEAD):
        return opportunities  # deliberately empty -- e.g. no pass can be thrown from a ball already in flight

    # ---- HELD: the normal offensive menu, gated on real structural preconditions ----
    if state.ball_state != BallState.HELD or state.ball_carrier is None or state.ball_control is None:
        return opportunities

    carrier = state.ball_carrier
    control = state.ball_control.state

    # Live-dribble-gated actions -- a picked-up/dead dribble cannot drive again (Phase 15's own control-state rule)
    if control == DribbleState.LIVE_DRIBBLE:
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.DRIVE), ActionType.DRIVE, carrier, source="live_dribble"))
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.ISOLATION_ATTACK), ActionType.ISOLATION_ATTACK, carrier, source="live_dribble"))
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.PULL_UP), ActionType.PULL_UP, carrier, target_zone=state.ball_zone, source="live_dribble"))

    # Catch-and-shoot: only on the frame right after a reception, before any dribble has happened
    if context.just_caught_pass and control == DribbleState.LIVE_DRIBBLE:
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.CATCH_AND_SHOOT), ActionType.CATCH_AND_SHOOT,
                                                    carrier, target_zone=state.ball_zone, source="just_caught"))

    # Closeout attack: STRUCTURAL defensive posture (recovering/trailing), not a hidden ability -- only right after a catch
    if context.just_caught_pass and context.ball_handler_defender_id is not None:
        defender = state.assignments.get(context.ball_handler_defender_id)
        if defender is not None and defender.posture in (DefensivePosture.RECOVERING, DefensivePosture.TRAILING):
            opportunities.append(ObjectiveOpportunity(_next_id(ActionType.CLOSEOUT_ATTACK), ActionType.CLOSEOUT_ATTACK,
                                                        carrier, source=f"defender_posture:{defender.posture.value}"))

    # Passing menu -- always includes an obvious, low-perception-cost swing/reset when a teammate exists
    if context.nearest_teammate_id is not None:
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.SWING_PASS), ActionType.SWING_PASS,
                                                    carrier, target_player_id=context.nearest_teammate_id, source="nearest_teammate"))
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.RESET_PASS), ActionType.RESET_PASS,
                                                    carrier, target_player_id=context.nearest_teammate_id, source="safety_valve"))

    # Kickout: requires BOTH a viable perimeter receiver AND an objective compromised interior area (advantage interface) --
    # multiple compromised areas can each independently license a kickout to a DIFFERENT receiver, not just one global check.
    if advantage is not None and context.perimeter_receiver_ids:
        for area in advantage.compromised_areas():
            if area.zone in INTERIOR_ZONES:
                for receiver_id, receiver_zone in context.perimeter_receiver_ids.items():
                    opportunities.append(ObjectiveOpportunity(_next_id(ActionType.KICKOUT), ActionType.KICKOUT, carrier,
                                                                target_player_id=receiver_id, target_zone=receiver_zone,
                                                                source=f"advantage:{area.zone.value}"))

    # Pocket pass: requires a live roller in an interior receiving window -- no roller, no pocket pass, ever
    if context.roller_id is not None and context.screen_active:
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.POCKET_PASS), ActionType.POCKET_PASS,
                                                    carrier, target_player_id=context.roller_id,
                                                    target_zone=SpatialZone.PAINT, source="live_roller"))

    # Transition push: only in the TRANSITION phase
    if state.phase == PossessionPhase.TRANSITION:
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.TRANSITION_PUSH), ActionType.TRANSITION_PUSH,
                                                    carrier, source="transition_phase"))

    return opportunities
