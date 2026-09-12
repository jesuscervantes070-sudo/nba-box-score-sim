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
from typing import Dict, List, Optional, Tuple

from action_intent import ActionType
from possession_advantage import AdvantageModel
from possession_state import (
    BallState, DefensivePosture, DribbleState, PossessionPhase, PossessionState, SpatialZone,
)

PERIMETER_ZONES = frozenset({SpatialZone.TOP_OF_KEY, SpatialZone.LEFT_WING, SpatialZone.RIGHT_WING,
                              SpatialZone.LEFT_CORNER, SpatialZone.RIGHT_CORNER})
MIDRANGE_ZONES = frozenset({SpatialZone.MIDRANGE})
INTERIOR_ZONES = frozenset({SpatialZone.PAINT, SpatialZone.RESTRICTED_RIM})
# "Expand halfcourt interior creation" phase -- the SAME centering reference
# `action_selection.ROLE_FINISHING_WEIGHT` already applies to `role_off_finishing` (`- 0.5`),
# reused here verbatim rather than inventing a second, independent threshold for the identical
# real [0,1] PCT_AST_FGM share.
FINISHING_ROLE_REFERENCE = 0.5


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
    shot_zone_options: Tuple[SpatialZone, ...] = ()  # hierarchical family alternatives; release action remains one opportunity
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
    nearest_teammate_zone: Optional[SpatialZone] = None  # receiver's actual coarse location; None preserves caller compatibility
    just_caught_pass: bool = False             # True only on the frame immediately after a reception -- gates CATCH_AND_SHOOT/CLOSEOUT_ATTACK
    ball_handler_defender_id: Optional[str] = None  # the real defender currently assigned to the ball handler, if known
    # "Expand halfcourt interior creation" phase -- the nearest teammate's real, already-existing
    # Phase 13 KEEP `role_off_finishing` value (PCT_AST_FGM share, [0,1], real per-player season
    # signal -- NOT invented for this phase, already read elsewhere in `action_selection.py`'s own
    # TERMINAL_ACTIONS term). Reused here, for the FIRST time, for a TEAMMATE rather than the ball
    # carrier -- see `ActionType.INTERIOR_CUT`'s own docstring for why this is the legitimate,
    # existing signal chosen (a player whose makes are disproportionately assisted is, structurally,
    # a real finisher who scores mostly off catches/cuts rather than self-created offense).
    nearest_teammate_finishing_role: Optional[float] = None


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

    def _shot_zone_options() -> Tuple[SpatialZone, ...]:
        """Structural family menu without changing action-level exposure.

        From an outer-floor or midrange live state, either a three-point
        release or a step-in/step-out midrange release is mechanically
        available. Interior access remains owned by drive geometry.
        """
        if state.ball_zone in PERIMETER_ZONES:
            return state.ball_zone, SpatialZone.MIDRANGE
        if state.ball_zone in MIDRANGE_ZONES:
            return SpatialZone.TOP_OF_KEY, SpatialZone.MIDRANGE
        return (state.ball_zone,)

    # Live-dribble-gated actions -- a picked-up/dead dribble cannot drive again (Phase 15's own control-state rule)
    if control == DribbleState.LIVE_DRIBBLE:
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.DRIVE), ActionType.DRIVE, carrier, source="live_dribble"))
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.ISOLATION_ATTACK), ActionType.ISOLATION_ATTACK, carrier, source="live_dribble"))
        opportunities.append(ObjectiveOpportunity(
            _next_id(ActionType.PULL_UP), ActionType.PULL_UP, carrier,
            target_zone=state.ball_zone, shot_zone_options=_shot_zone_options(), source="live_dribble",
        ))

    # Catch-and-shoot: only on the frame right after a reception, before any dribble has happened
    if context.just_caught_pass and control == DribbleState.LIVE_DRIBBLE:
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.CATCH_AND_SHOOT), ActionType.CATCH_AND_SHOOT,
                                                    carrier, target_zone=state.ball_zone,
                                                    shot_zone_options=_shot_zone_options(), source="just_caught"))

    # Closeout attack: STRUCTURAL defensive posture (recovering/trailing), not a hidden ability -- only right after a catch
    if context.just_caught_pass and context.ball_handler_defender_id is not None:
        defender = state.assignments.get(context.ball_handler_defender_id)
        if defender is not None and defender.posture in (DefensivePosture.RECOVERING, DefensivePosture.TRAILING):
            opportunities.append(ObjectiveOpportunity(_next_id(ActionType.CLOSEOUT_ATTACK), ActionType.CLOSEOUT_ATTACK,
                                                        carrier, source=f"defender_posture:{defender.posture.value}"))

    # Passing menu -- always includes an obvious, low-perception-cost swing/reset when a teammate exists
    if context.nearest_teammate_id is not None:
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.SWING_PASS), ActionType.SWING_PASS,
                                                    carrier, target_player_id=context.nearest_teammate_id,
                                                    target_zone=context.nearest_teammate_zone, source="nearest_teammate"))
        opportunities.append(ObjectiveOpportunity(_next_id(ActionType.RESET_PASS), ActionType.RESET_PASS,
                                                    carrier, target_player_id=context.nearest_teammate_id,
                                                    target_zone=context.nearest_teammate_zone, source="safety_valve"))

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

    # Transition push ("Add interior shot-opportunity generation"): only in the TRANSITION phase,
    # and only when a real teammate exists to receive it -- a genuine PASS (dispatched via the
    # EXISTING, unmodified `_dispatch_pass`/`resolve_pass`, not a new resolver), landing that
    # teammate at RESTRICTED_RIM. This is the one basketball-causal path this project's own
    # audit found for a NON-drive possession to ever place an off-ball player at an interior
    # zone: a live, fast-break-or-controlled-advance possession (`state.phase == TRANSITION`,
    # covering both LIVE_TRANSITION and CONTROLLED_ADVANCE -- see transition_state.py's own
    # restart-routing doctrine) pushing the ball ahead to a teammate already attacking the rim
    # ("transition rim run" -- one of this task's own named candidate sources). Receiver
    # selection reuses `context.nearest_teammate_id` -- the SAME real, already-computed receiver
    # SWING_PASS/RESET_PASS already use (no new "who is running the floor fastest" signal is
    # invented; this is the smallest defensible receiver choice, not a claim about who is
    # genuinely furthest ahead). If no teammate exists at all, the opportunity is NOT generated
    # (no target to dispatch to) -- this is a real, structural gate, never a phantom action.
    if state.phase == PossessionPhase.TRANSITION and context.nearest_teammate_id is not None:
        opportunities.append(ObjectiveOpportunity(
            _next_id(ActionType.TRANSITION_PUSH), ActionType.TRANSITION_PUSH, carrier,
            target_player_id=context.nearest_teammate_id, target_zone=SpatialZone.RESTRICTED_RIM,
            source="transition_phase",
        ))

    # Interior cut ("Expand interior scoring opportunities" phase): the one HALFCOURT (non-drive,
    # non-transition) path this project's own architecture audit found for a real teammate to reach
    # an interior zone -- see `ActionType.INTERIOR_CUT`'s own docstring in `action_intent.py` for the
    # full audit (POCKET_PASS was considered and rejected: its `roller_id`/`screen_active`
    # preconditions are hardcoded off in V0).
    #
    # GATE, REVISED TWICE FROM A FIRST DRAFT (see the docstring linked above for the full audit
    # trail): a first version gated this on `AdvantageModel.compromised_areas()` (the same interface
    # KICKOUT reads) -- direct inspection of `possession_orchestrator.py` found `engine.advantage` is
    # NEVER constructed anywhere in the actual game loop (`detailed_game.py`/
    # `detailed_game_orchestrator.py`/`simulate_possession` all leave it at its `None` default), so
    # KICKOUT/POCKET_PASS are themselves silently-dead opportunity types in current production play --
    # gating a NEW action on that same always-`None` interface would have made INTERIOR_CUT dead code
    # too. A second version gated this on the NEAREST TEAMMATE's own assigned defender's posture
    # (`state.assignments`) -- also dead: direct inspection found `PossessionEngine.update_posture`/
    # `.switch()` are ONLY ever invoked (a) by `drive_resolution.py`, for the DRIVER's OWN defender,
    # and (b) by `off_ball_screen_resolution.py`, which (like `AdvantageModel`) is caller-triggered
    # only and never invoked from this game loop -- so an OFF-BALL teammate's defender posture never
    # leaves its initial SQUARE value in production, ever.
    #
    # The one genuinely LIVE, non-SQUARE-forever posture signal in production is the CURRENT ball
    # handler's OWN on-ball defender (`context.ball_handler_defender_id`, already read by
    # CLOSEOUT_ATTACK above) -- it really does move to RECOVERING/TRAILING/HELPING, via
    # `drive_resolution.py`'s own `_POSTURE_AFTER_OUTCOME`, whenever a CLEAN_PENETRATION/PARTIAL_EDGE
    # drive has just happened. A dislodged on-ball defender is real basketball evidence that dribble
    # penetration has drawn attention/help -- exactly the condition under which a teammate would cut
    # into the interior (a dunker-spot/dive cut off dribble penetration), so INTERIOR_CUT reuses that
    # SAME live signal for a DIFFERENT real consequence (a cut for a teammate, not an attack by the
    # ball handler). Restricted to HALFCOURT (not TRANSITION, which already has its own
    # `TRANSITION_PUSH` interior path above) to keep the two sources structurally distinct and
    # separately diagnosable. `target_zone` here is a PLACEHOLDER only (PAINT) --
    # `possession_orchestrator._resolve_interior_pass_destination` rolls the REAL RESTRICTED_RIM/PAINT
    # destination at dispatch time, same as TRANSITION_PUSH.
    if (state.phase == PossessionPhase.HALFCOURT and context.nearest_teammate_id is not None
            and context.ball_handler_defender_id is not None):
        on_ball_defender = state.assignments.get(context.ball_handler_defender_id)
        if on_ball_defender is not None and on_ball_defender.posture in (
                DefensivePosture.RECOVERING, DefensivePosture.TRAILING, DefensivePosture.HELPING):
            opportunities.append(ObjectiveOpportunity(
                _next_id(ActionType.INTERIOR_CUT), ActionType.INTERIOR_CUT, carrier,
                target_player_id=context.nearest_teammate_id, target_zone=SpatialZone.PAINT,
                source=f"on_ball_defender_posture:{on_ball_defender.posture.value}",
            ))

    # Interior cut, ORDINARY-HALFCOURT variant ("Expand halfcourt interior creation" phase):
    # AUDIT FINDING that motivated this addition -- the drive-derived gate above makes
    # INTERIOR_CUT "effectively another DRIVE-derived interior mechanism" (this task's own
    # framing, confirmed by direct measurement): it requires a CLEAN_PENETRATION/PARTIAL_EDGE
    # drive to have JUST happened, and the very decision where it becomes available is the SAME
    # decision `action_selection.DRIVE_FOLLOWUP_LOG_WEIGHT` (a SEPARATE, frozen-this-phase
    # mechanism) heavily biases toward a SHOT and heavily AWAY from any PASS_ACTIONS member --
    # INTERIOR_CUT included. Measured directly: 621 objective opportunities but only 10 selections
    # across 100 canonical games (1.6%), almost entirely explained by that same-decision collision.
    # Real basketball also creates a cut/interior catch WITHOUT a preceding drive at all (ordinary
    # off-ball movement, overplay, weak-side cuts, ball-watching help) -- V1 does not model
    # continuous player movement/defender attention, but it can use a real, ALREADY-EXISTING,
    # non-drive structural signal instead of fabricating one: `context.nearest_teammate_finishing_role`
    # (Phase 13 KEEP `role_off_finishing`, real PCT_AST_FGM share) read for the nearest teammate. A
    # teammate whose own scoring is disproportionately ASSISTED is, structurally, a real finisher
    # who scores mostly off catches/cuts rather than self-creation -- exactly the real-world profile
    # of a player a halfcourt offense would actually try to get an interior catch for, independent
    # of whether the ball handler has drawn help via a drive. `FINISHING_ROLE_REFERENCE` (0.5) is
    # NOT a new arbitrary threshold -- it is the SAME centering reference
    # `action_selection.ROLE_FINISHING_WEIGHT` already uses (`role_off_finishing - 0.5`), reused
    # here rather than invented. Gated on `control == LIVE_DRIBBLE` (the SAME live-dribble
    # precondition DRIVE/ISOLATION_ATTACK/PULL_UP already require above) so this is a genuine,
    # already-legitimate HALFCOURT ball-handling context, not a fabricated new state -- and
    # crucially, this decision has NO active `post_drive_outcome` bias (nothing drove to create
    # it), so INTERIOR_CUT competes fairly here instead of being crushed by a same-decision
    # collision with an unrelated, frozen mechanism.
    #
    # THRESHOLD, DELIBERATELY `>=` NOT `>`: a league-AVERAGE finisher (`role_off_finishing == 0.5`,
    # the exact real synthetic-profile default `PlayerSimulationProfile.synthetic` uses, and a real
    # league-average value for an actual roster) is still a legitimate, ordinary target for an
    # interior catch -- only a teammate who is BELOW-average as a finisher (a real, meaningful
    # exclusion for an actual varied roster) makes this context "unavailable" (see test C: "remains
    # unavailable in impossible contexts"). Using `>` instead would silently make this entire
    # mechanism unreachable for every synthetic/benchmark profile (all exactly 0.5) without
    # representing any real basketball distinction at this coarse a signal.
    # Deliberately EXCLUDES a `context.just_caught_pass` decision ONLY when the catch occurred in a
    # PERIMETER zone -- that specific frame is the SAME one CATCH_AND_SHOOT's own real three-point
    # look is gated on, and letting INTERIOR_CUT compete there was measured to cannibalize
    # CATCH_AND_SHOOT's three-point volume (pushing overall THREE share below this task's own 38%
    # floor) far more than it should. A catch in the MIDRANGE zone (or a live-dribble decision after
    # any catch) is a DIFFERENT real context -- CATCH_AND_SHOOT's own menu there is already
    # {TOP_OF_KEY, MIDRANGE} (a real three is still reachable, just not the dominant option), so a
    # cut opportunity competing there draws disproportionately from what would otherwise become a
    # MIDRANGE catch-and-shoot, not from a genuine three -- the correct, realistic competition this
    # mechanism should draw volume from (PULL_UP's own shot mix skews the same way).
    just_caught_from_perimeter = context.just_caught_pass and state.ball_zone in PERIMETER_ZONES
    if (state.phase == PossessionPhase.HALFCOURT and control == DribbleState.LIVE_DRIBBLE
            and not just_caught_from_perimeter
            and context.nearest_teammate_id is not None
            and context.nearest_teammate_finishing_role is not None
            and context.nearest_teammate_finishing_role >= FINISHING_ROLE_REFERENCE):
        opportunities.append(ObjectiveOpportunity(
            _next_id(ActionType.INTERIOR_CUT), ActionType.INTERIOR_CUT, carrier,
            target_player_id=context.nearest_teammate_id, target_zone=SpatialZone.PAINT,
            source="ordinary_halfcourt_finishing_role",
        ))

    return opportunities
