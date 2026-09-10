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


# Action-type groupings used by selection/clock logic -- named sets, not
# a forced compositional constraint. An action can belong to more than
# one grouping conceptually; these three are the only ones this phase's
# policy actually consumes.
SHOT_ACTIONS = frozenset({ActionType.PULL_UP, ActionType.CATCH_AND_SHOOT})
PASS_ACTIONS = frozenset({ActionType.SWING_PASS, ActionType.KICKOUT, ActionType.POCKET_PASS, ActionType.RESET_PASS, ActionType.OUTLET_PASS})
TERMINAL_ACTIONS = frozenset({ActionType.DRIVE, ActionType.ISOLATION_ATTACK, ActionType.PULL_UP,
                               ActionType.CATCH_AND_SHOOT, ActionType.CLOSEOUT_ATTACK})
CREATION_ACTIONS = frozenset({ActionType.DRIVE, ActionType.ISOLATION_ATTACK, ActionType.PULL_UP, ActionType.POCKET_PASS,
                               ActionType.TRANSITION_PUSH})  # Phase 20B addition -- pushing the ball upcourt is initiation-adjacent, so role_off_initiation's existing CREATION_ACTIONS boost legitimately extends to it, reusing Phase 16's scoring function unmodified


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
