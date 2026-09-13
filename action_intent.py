"""
Phase 16 -- Action Selection & Opportunity Generation: the ActionIntent
schema.

ActionIntent is the HANDOFF OBJECT between selection (this phase) and
resolution (a future phase). It intentionally carries NO outcome field
of any kind -- no make probability, no pass-accuracy result, no
turnover flag, no contest level, no rebound winner. Resolution decides
what happens; selection only decides WHAT WAS ATTEMPTED and under what
conditions/checkpoints it can be interrupted.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple


class ActionType(Enum):
    """The V1 action set -- deliberately smaller than the full task
    candidate list (drive/isolation/PnR/catch-and-shoot/pull-up/swing/
    kickout/skip/pocket/drop-off/reset/cut/screen-DHO/closeout-attack/
    transition-push/outlet). Chosen as the smallest set that (a) is
    mechanically distinguishable under the Phase 15 state model, (b) has
    a real, available empirical frequency source (Phase 8/9/11/13's
    already-built touches/drives/pull-up/catch-shoot/potential-ast
    tracking data), and (c) exercises every required precondition
    category (ball-state gating, advantage-gating, vision-gating,
    clock-sensitivity). Skip pass, drop-off, cut, and screen/DHO
    initiation are real, valid future V2 additions -- not built this
    phase because they would not add a NEW precondition category beyond
    what KICKOUT/POCKET_PASS/SWING_PASS already exercise, and this
    phase's own instruction is to find the smallest USEFUL set, not
    enumerate the full list."""
    DRIVE = "DRIVE"
    ISOLATION_ATTACK = "ISOLATION_ATTACK"
    PULL_UP = "PULL_UP"
    CATCH_AND_SHOOT = "CATCH_AND_SHOOT"
    SWING_PASS = "SWING_PASS"
    KICKOUT = "KICKOUT"
    POCKET_PASS = "POCKET_PASS"
    RESET_PASS = "RESET_PASS"
    CLOSEOUT_ATTACK = "CLOSEOUT_ATTACK"
    TRANSITION_PUSH = "TRANSITION_PUSH"
    RECOVER_LOOSE_BALL = "RECOVER_LOOSE_BALL"  # the one action available while the ball is LOOSE -- not a normal offensive menu item
    OUTLET_PASS = "OUTLET_PASS"  # Phase 20B addition -- a pass to a real, structurally AHEAD_OF_BALL teammate (Phase 20A's relational tag); distinct from SWING_PASS/KICKOUT because its objective precondition is transition-geometry-specific, not nearest-teammate or advantage-driven
    INTERIOR_CUT = "INTERIOR_CUT"  # "Expand interior scoring opportunities" phase -- the one HALFCOURT (non-transition, non-drive) path this
    # project's own architecture audit found for a real teammate to reach an interior zone: a pass to the nearest teammate,
    # gated on the SAME real `AdvantageModel.compromised_areas()` signal KICKOUT already reads (a compromised defensive
    # area implies a real cutting lane somewhere on the floor), dispatched as a genuine pass via the EXISTING, unmodified
    # `_dispatch_pass`/`resolve_pass` machinery (same reuse posture as TRANSITION_PUSH). Audited and rejected before
    # adding this: POCKET_PASS already lands a receiver at PAINT, but only when `context.roller_id`/`context.screen_active`
    # are set, and `build_structural_context` hardcodes BOTH to None/False in V0 (Phase 22A's off-ball screen primitive
    # is caller-triggered only) -- activating that path would require fabricating a live-screen-engagement frequency
    # model, which is a materially bigger, separate feature (a real off-ball screen/roll cadence) this phase was not
    # asked to build, not a minimal activation of existing state. INTERIOR_CUT reuses strictly EXISTING signals
    # (advantage, nearest-teammate) instead.
    INTERIOR_SEAL = "INTERIOR_SEAL"  # pass-created halfcourt paint touch: an off-ball teammate
    # already deployed at the midrange/high-post slot establishes a seal and receives a real pass.
    # Distinct from INTERIOR_CUT:
    # no drive, displaced on-ball defender, cut, screen, roller, or invented post skill is required.
    ON_BALL_SCREEN = "ON_BALL_SCREEN"  # non-terminal two-player setup event: assigns a real
    # teammate as the temporary roller and creates one downstream screen decision. It does not
    # itself shoot, drive, pass, or move either offensive player into an interior zone.


# Action-type groupings used by selection/clock logic -- named sets, not
# a forced compositional constraint. An action can belong to more than
# one grouping conceptually; these three are the only ones this phase's
# policy actually consumes.
SHOT_ACTIONS = frozenset({ActionType.PULL_UP, ActionType.CATCH_AND_SHOOT})
PASS_ACTIONS = frozenset({ActionType.SWING_PASS, ActionType.KICKOUT, ActionType.POCKET_PASS, ActionType.RESET_PASS,
                           ActionType.OUTLET_PASS, ActionType.TRANSITION_PUSH, ActionType.INTERIOR_CUT,
                           ActionType.INTERIOR_SEAL})  # "Add interior shot-opportunity
# generation" -- TRANSITION_PUSH is now dispatched as a real pass (see possession_orchestrator.dispatch_action),
# so it belongs in this grouping too (pass_vs_shoot's existing tendency term now legitimately applies to it,
# same reuse-not-reinvent posture as every other grouping extension in this file). INTERIOR_CUT ("Expand interior
# scoring opportunities" phase) is likewise dispatched as a real pass and belongs in this grouping for the same reason.
TERMINAL_ACTIONS = frozenset({ActionType.DRIVE, ActionType.ISOLATION_ATTACK, ActionType.PULL_UP,
                               ActionType.CATCH_AND_SHOOT, ActionType.CLOSEOUT_ATTACK})
CREATION_ACTIONS = frozenset({ActionType.DRIVE, ActionType.ISOLATION_ATTACK, ActionType.PULL_UP, ActionType.POCKET_PASS,
                               ActionType.TRANSITION_PUSH, ActionType.INTERIOR_CUT, ActionType.INTERIOR_SEAL,
                               ActionType.ON_BALL_SCREEN})  # Phase 20B addition -- pushing the ball
# upcourt is initiation-adjacent, so role_off_initiation's existing CREATION_ACTIONS boost legitimately extends to it,
# reusing Phase 16's scoring function unmodified. INTERIOR_CUT ("Expand interior scoring opportunities" phase) is the
# same kind of scoring-chance-creating pass POCKET_PASS already is, so it belongs in this grouping for the same reason.


class ActionFamily(Enum):
    """"Use contextual hierarchical action selection" phase -- the decision-CLASS partition
    `action_selection.SelectionPolicy` groups feasible actions into before choosing a specific
    action within the chosen family. THE ROOT CAUSE THIS TASK ADDRESSES: three independently-built
    interior mechanisms (INTERIOR_CUT, INTERIOR_SEAL, ON_BALL_SCREEN) each hit the same ceiling --
    adding ONE more feasible action to a single FLAT softmax mathematically shrinks EVERY other
    feasible action's probability by the identical multiplicative factor Z/(Z+exp(new_score))
    (proven directly: `_softmax([1.0]*6)[0]` vs `_softmax([1.0]*7)[0]` -- see
    `test_hierarchical_action_selection.py`'s own `test_flat_softmax_dilution_is_mathematically_exact`),
    regardless of whether that action has anything to do with the others. Grouping first means a
    new OFF_BALL_CREATION action only ever dilutes OTHER OFF_BALL_CREATION actions, never
    unrelated SHOT/BALL_MOVEMENT probability mass.

    Every `ActionType` a normal offensive decision can ever perceive belongs to EXACTLY ONE family
    (`FAMILY_BY_ACTION_TYPE` below is a total function over `SUPPORTED_ACTION_TYPES ∪
    CAPABILITY_GATED_ACTION_TYPES`, `RECOVER_LOOSE_BALL` excluded -- see that constant's own
    docstring for why: a LOOSE ball is intercepted before this menu is ever built at all, so it
    never needs a family). Chosen by AUDITING EACH ACTION'S OWN SEMANTICS, not by any preferred
    illustrative grouping:
      ATTACK            -- a live-dribble, ball-handler-driven bid to create a scoring edge:
                           DRIVE, ISOLATION_ATTACK, CLOSEOUT_ATTACK, ON_BALL_SCREEN (initiating a
                           screen is itself a proactive attacking decision by the ball handler, not
                           a pass -- it moves no ball and creates no shot by itself).
      SHOT              -- an immediate release: PULL_UP, CATCH_AND_SHOOT. Exactly `SHOT_ACTIONS`.
      OFF_BALL_CREATION -- the ball handler looks for a TEAMMATE's own off-ball scoring chance
                           without a live screen already active: INTERIOR_CUT, INTERIOR_SEAL.
      BALL_MOVEMENT     -- every other real pass: SWING_PASS, RESET_PASS, KICKOUT, OUTLET_PASS,
                           TRANSITION_PUSH, and POCKET_PASS (a screen-context-conditional pass --
                           still fundamentally "move the ball to a teammate", not a fresh creation
                           decision the ball handler is initiating from scratch).
    """
    ATTACK = "ATTACK"
    SHOT = "SHOT"
    OFF_BALL_CREATION = "OFF_BALL_CREATION"
    BALL_MOVEMENT = "BALL_MOVEMENT"


FAMILY_BY_ACTION_TYPE: Dict[ActionType, ActionFamily] = {
    ActionType.DRIVE: ActionFamily.ATTACK,
    ActionType.ISOLATION_ATTACK: ActionFamily.ATTACK,
    ActionType.CLOSEOUT_ATTACK: ActionFamily.ATTACK,
    ActionType.ON_BALL_SCREEN: ActionFamily.ATTACK,
    ActionType.PULL_UP: ActionFamily.SHOT,
    ActionType.CATCH_AND_SHOOT: ActionFamily.SHOT,
    ActionType.INTERIOR_CUT: ActionFamily.OFF_BALL_CREATION,
    ActionType.INTERIOR_SEAL: ActionFamily.OFF_BALL_CREATION,
    ActionType.SWING_PASS: ActionFamily.BALL_MOVEMENT,
    ActionType.RESET_PASS: ActionFamily.BALL_MOVEMENT,
    ActionType.KICKOUT: ActionFamily.BALL_MOVEMENT,
    ActionType.OUTLET_PASS: ActionFamily.BALL_MOVEMENT,
    ActionType.TRANSITION_PUSH: ActionFamily.BALL_MOVEMENT,
    ActionType.POCKET_PASS: ActionFamily.BALL_MOVEMENT,
}
# RECOVER_LOOSE_BALL deliberately has NO family: a BallState.LOOSE possession is intercepted at
# the top of the possession loop, before generate_opportunities ever builds a normal offensive
# menu -- it never reaches SelectionPolicy.select() at all (see possession_orchestrator.py's own
# "RECOVER_LOOSE_BALL is gated for a different reason" documentation, unchanged by this phase).


class DurationClass(Enum):
    """A calibratable KEY, not a hardcoded empirical duration -- a
    future phase attaches real timing distributions to these keys (see
    module docstring's "duration-model key" field below). No numeric
    duration is asserted anywhere in Phase 16 outside of test fixtures."""
    INSTANT = "INSTANT"      # e.g. an immediate reset/safety pass
    QUICK = "QUICK"          # e.g. catch-and-shoot, kickout
    MODERATE = "MODERATE"    # e.g. pull-up, swing pass, pocket pass
    EXTENDED = "EXTENDED"    # e.g. a drive, an isolation attack, a closeout attack


_DEFAULT_CHECKPOINTS: Dict[ActionType, Tuple[str, ...]] = {
    ActionType.DRIVE: ("drive_begins", "poa_interaction", "defender_beaten_check", "help_opportunity", "help_arrives_or_fails", "release_opportunity"),
    ActionType.ISOLATION_ATTACK: ("iso_begins", "poa_interaction", "release_opportunity"),
    ActionType.PULL_UP: ("gather", "release"),
    ActionType.CATCH_AND_SHOOT: ("catch", "release"),
    ActionType.SWING_PASS: ("release", "reception"),
    ActionType.KICKOUT: ("release", "reception"),
    ActionType.POCKET_PASS: ("release", "reception"),
    ActionType.RESET_PASS: ("release", "reception"),
    ActionType.CLOSEOUT_ATTACK: ("closeout_engaged", "beat_or_hold_check", "release_opportunity"),
    ActionType.TRANSITION_PUSH: ("push_begins", "numbers_check", "release_opportunity"),
    ActionType.RECOVER_LOOSE_BALL: ("scramble", "secure_or_fail"),
    ActionType.OUTLET_PASS: ("release", "reception"),
    ActionType.INTERIOR_CUT: ("cut_begins", "release", "reception"),
    ActionType.INTERIOR_SEAL: ("seal_established", "release", "reception"),
    ActionType.ON_BALL_SCREEN: ("screen_arrives", "screen_contact", "roller_releases"),
}


@dataclass(frozen=True)
class ActionIntent:
    """The selection→resolution handoff object.

    Deliberately EXCLUDES: final success probability, make probability,
    turnover result, final defensive contest level, rebound result --
    ALL resolution-layer concerns, not selection-layer ones. Including
    any of them here would let a future resolution phase accidentally
    read a pre-baked outcome instead of computing one from real ability/
    context, defeating the whole point of keeping selection and
    resolution separate.
    """
    action_type: ActionType
    actor_player_id: str
    possession_id: str
    target_player_id: Optional[str] = None      # real player_id, for pass-type/pocket/kickout/swing actions
    target_zone: Optional[str] = None           # SpatialZone.value -- plain string, same convention as possession_events.Event
    originating_opportunity_id: str = ""        # links back to the ObjectiveOpportunity this intent came from
    required_checkpoints: Tuple[str, ...] = ()  # Phase 15-compatible checkpoint names a resolution phase must honor (see possession_engine.run_checkpointed_action)
    duration_class: DurationClass = DurationClass.MODERATE
    perception_provenance: str = ""             # e.g. "OBJECTIVE_NO_GATE", "VISION_GATED:playmaking_vision", "STRUCTURAL" -- diagnostic only, not a probability
    context_snapshot_ref: str = ""              # opaque reference (e.g. a possession_id + event-count) a resolution phase can use to re-fetch the exact state this intent was chosen against -- not the state itself, to avoid duplicating Phase 15's own state object

    @staticmethod
    def default_checkpoints(action_type: ActionType) -> Tuple[str, ...]:
        """Real, Phase-15-`run_checkpointed_action`-compatible checkpoint
        names for each action type -- reused by resolution phases, not
        invented per-call. Kept as a lookup here (schema-adjacent) rather
        than in action_selection.py, so a resolution-phase author only
        needs to import this one file to know what checkpoints to expect."""
        return _DEFAULT_CHECKPOINTS.get(action_type, ())

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type.value, "actor_player_id": self.actor_player_id,
            "possession_id": self.possession_id, "target_player_id": self.target_player_id,
            "target_zone": self.target_zone, "originating_opportunity_id": self.originating_opportunity_id,
            "required_checkpoints": list(self.required_checkpoints), "duration_class": self.duration_class.value,
            "perception_provenance": self.perception_provenance, "context_snapshot_ref": self.context_snapshot_ref,
        }
