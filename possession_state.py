"""
Phase 15 -- Possession State & Event Kernel: core state primitives.

ARCHITECTURAL BOUNDARY: nothing in this file (or any other Phase 15
file) computes a probability, a make/miss outcome, a pass-success rate,
or any other empirical quantity. This is state and transition-legality
infrastructure only -- the empirical action mechanics that will consume
it (drive success, shot make%, pass success, rebound formulas, foul
rates) are explicitly deferred to a future, dedicated phase.

CANONICAL IDENTITY: every player-referencing field here is a real,
stable NBA `player_id` string (see player_identity.py, Phase 14) --
never a name. `_assert_player_id` (bottom of this file) is a real,
enforced guard, not just a docstring promise -- see
possession_engine.py's public API, which calls it on every player
argument.
"""
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, Optional, Tuple


# --------------------------- ball state ---------------------------

class BallState(Enum):
    """The ball's own state -- deliberately separate from team/
    individual possession (see PossessionState below). A boolean
    "someone has the ball" is insufficient: a pass in flight, a shot in
    flight, and a loose ball after a block are all real, distinct states
    where `ball_carrier` is None but the game is very much still live."""
    HELD = "HELD"
    PASS_IN_FLIGHT = "PASS_IN_FLIGHT"
    SHOT_IN_FLIGHT = "SHOT_IN_FLIGHT"
    LOOSE = "LOOSE"
    DEAD = "DEAD"  # made basket / whistle / out of bounds -- awaiting inbound; distinct from LOOSE (nobody is scrambling for a dead ball)


class DribbleState(Enum):
    """Individual ball-CONTROL state -- only meaningful when
    `ball_carrier` is set and `ball_state == HELD`. Deliberately not
    modeled as footwork/physics -- just enough to make an illegal
    repeated-live-dribble state structurally unreachable (see
    PlayerBallControl.start_dribble's guard)."""
    LIVE_DRIBBLE = "LIVE_DRIBBLE"    # may pass, shoot, or continue dribbling
    GATHERED = "GATHERED"            # picked up the dribble, committed to pass/shoot -- may NOT dribble again
    DEAD_DRIBBLE = "DEAD_DRIBBLE"    # holding with no live dribble and no gather in progress (about to pass/shoot or get called for a violation) -- also may NOT dribble again


class IllegalControlTransition(Exception):
    """Raised when code tries to put a ball-control state machine into
    a real basketball violation (e.g. resuming a live dribble after it
    was already picked up) -- a structural guard, not a probability."""


@dataclass
class PlayerBallControl:
    """One player's ball-control sub-state while they are the ball
    carrier. A NEW PlayerBallControl is created every time a different
    player becomes the carrier (a new catch/recovery always starts at
    LIVE_DRIBBLE) -- there is no path back to LIVE_DRIBBLE from GATHERED
    or DEAD_DRIBBLE within the same control instance, which is exactly
    what makes an illegal double-dribble unreachable by construction
    rather than by a runtime probability check."""
    player_id: str
    state: DribbleState = DribbleState.LIVE_DRIBBLE

    def continue_dribble(self) -> "PlayerBallControl":
        if self.state != DribbleState.LIVE_DRIBBLE:
            raise IllegalControlTransition(
                f"{self.player_id} cannot dribble again from {self.state.value} -- "
                "the live dribble was already ended (gathered or dead) this touch")
        return self  # still LIVE_DRIBBLE -- dribbling doesn't change state, just elapses time (handled by the caller)

    def gather(self) -> "PlayerBallControl":
        if self.state == DribbleState.DEAD_DRIBBLE:
            raise IllegalControlTransition(f"{self.player_id} is already DEAD_DRIBBLE -- cannot gather again")
        return replace(self, state=DribbleState.GATHERED)

    def go_dead(self) -> "PlayerBallControl":
        return replace(self, state=DribbleState.DEAD_DRIBBLE)


# --------------------------- spatial topology ---------------------------

class SpatialZone(Enum):
    """Coarse topology -- 9 zones, deliberately chosen to align with
    the already-empirically-validated Phase 5 shot-zone taxonomy
    (restricted area / paint-non-RA / midrange / corner3 / above-break3)
    rather than inventing a new, disconnected one, plus BACKCOURT for
    transition/pre-halfcourt possession. NOT Gemini's un-examined
    7-zone proposal -- this set was chosen because it's the smallest
    partition that still supports every capability the phase requires
    (top/wing/corner distinction, paint, rim, strong/weak side via the
    derived `ball_side` helper below, passing targets, help rotation,
    shot-family selection, rebound positioning, and a transition
    bridge). Exact zone count is explicitly NOT locked -- a future
    phase may split TOP_OF_KEY into left/right-of-key if empirical work
    shows it matters; nothing here assumes exactly 8."""
    BACKCOURT = "BACKCOURT"
    TOP_OF_KEY = "TOP_OF_KEY"
    LEFT_WING = "LEFT_WING"
    RIGHT_WING = "RIGHT_WING"
    LEFT_CORNER = "LEFT_CORNER"
    RIGHT_CORNER = "RIGHT_CORNER"
    MIDRANGE = "MIDRANGE"
    PAINT = "PAINT"
    RESTRICTED_RIM = "RESTRICTED_RIM"


_LEFT_ZONES = frozenset({SpatialZone.LEFT_WING, SpatialZone.LEFT_CORNER})
_RIGHT_ZONES = frozenset({SpatialZone.RIGHT_WING, SpatialZone.RIGHT_CORNER})
_CENTRAL_ZONES = frozenset({SpatialZone.TOP_OF_KEY, SpatialZone.MIDRANGE, SpatialZone.PAINT,
                            SpatialZone.RESTRICTED_RIM, SpatialZone.BACKCOURT})


def ball_side(zone: SpatialZone) -> str:
    """'LEFT' | 'RIGHT' | 'CENTRAL' -- used to DERIVE strong-side/
    weak-side for another zone relative to the ball's zone, rather than
    storing strong/weak-side as its own persistent field (it's a pure
    function of two zones, so it can never drift out of sync)."""
    if zone in _LEFT_ZONES:
        return "LEFT"
    if zone in _RIGHT_ZONES:
        return "RIGHT"
    return "CENTRAL"


def is_strong_side(player_zone: SpatialZone, ball_zone: SpatialZone) -> bool:
    """True if `player_zone` is on the ball's side (or either is
    central, which is never weak-side by definition)."""
    ps, bs = ball_side(player_zone), ball_side(ball_zone)
    return ps == bs or ps == "CENTRAL" or bs == "CENTRAL"


# --------------------------- defensive assignment/posture ---------------------------

class DefensivePosture(Enum):
    """Persistent per-defender posture -- NOT a contest rating (that is
    explicitly derived at action-resolution time, never stored). Exact
    category set is NOT locked; this is a minimal, real-basketball-
    literate starting set."""
    SQUARE = "SQUARE"           # set, facing the assignment, in normal defensive position
    TRAILING = "TRAILING"       # beaten, behind the assignment's hip
    RECOVERING = "RECOVERING"   # closing back out after being pulled away
    HELPING = "HELPING"         # off their own assignment, providing help elsewhere


@dataclass
class DefensiveAssignment:
    """A persistent pointer, defender_id -> assigned offensive
    player_id, plus that defender's current posture. Updated in place
    (via `switch`) when a screen/switch changes who guards whom -- the
    pointer itself is the persistent state; the posture that comes with
    a fresh assignment defaults to SQUARE unless told otherwise (e.g. a
    late/scrambling switch should be constructed as RECOVERING, not
    silently defaulted)."""
    defender_id: str
    assigned_to_player_id: str
    posture: DefensivePosture = DefensivePosture.SQUARE

    def switch(self, new_assignment_player_id: str, posture: DefensivePosture = DefensivePosture.RECOVERING) -> "DefensiveAssignment":
        return replace(self, assigned_to_player_id=new_assignment_player_id, posture=posture)

    def update_posture(self, posture: DefensivePosture) -> "DefensiveAssignment":
        return replace(self, posture=posture)


# --------------------------- possession phase ---------------------------

class PossessionPhase(Enum):
    TRANSITION = "TRANSITION"
    HALFCOURT = "HALFCOURT"
    SECOND_CHANCE = "SECOND_CHANCE"
    DEAD_BALL = "DEAD_BALL"


# --------------------------- the possession state container ---------------------------

@dataclass
class PossessionState:
    """The single source of truth for one possession's live state.
    TEAM possession (`offense_team_id`) is explicitly separate from
    INDIVIDUAL ball possession (`ball_carrier`) -- e.g. during
    `PASS_IN_FLIGHT`, `offense_team_id` is still set (the offense still
    owns the possession) while `ball_carrier` is None; during a loose
    ball after a block, `offense_team_id` may itself be temporarily
    None (unresolved) until a player secures it."""
    possession_id: str
    offense_team_id: Optional[str]   # None only while team possession is genuinely unresolved (e.g. a live loose-ball scramble)
    defense_team_id: Optional[str]
    phase: PossessionPhase
    ball_state: BallState = BallState.DEAD
    ball_carrier: Optional[str] = None            # real player_id, or None -- never a placeholder/sentinel string
    ball_control: Optional[PlayerBallControl] = None  # only set when ball_carrier is set and ball_state == HELD
    ball_zone: SpatialZone = SpatialZone.BACKCOURT
    assignments: Dict[str, DefensiveAssignment] = field(default_factory=dict)  # defender_id -> DefensiveAssignment
    shot_clock_remaining: Optional[float] = None  # None if the era has no shot clock (see possession_rules.py)
    game_clock_remaining: Optional[float] = None

    def with_ball_carrier(self, player_id: Optional[str], ball_state: BallState) -> "PossessionState":
        """The one supported way to change who (if anyone) holds the
        ball -- always resets `ball_control` to a fresh LIVE_DRIBBLE
        instance for a NEW carrier, and always clears it when nobody
        holds the ball, so a stale control-state can never leak from one
        carrier/possession segment to the next."""
        control = PlayerBallControl(player_id=player_id) if (player_id is not None and ball_state == BallState.HELD) else None
        return replace(self, ball_carrier=player_id, ball_state=ball_state, ball_control=control)

    def with_assignment(self, defender_id: str, offensive_player_id: str,
                         posture: DefensivePosture = DefensivePosture.SQUARE) -> "PossessionState":
        new_assignments = dict(self.assignments)
        new_assignments[defender_id] = DefensiveAssignment(defender_id, offensive_player_id, posture)
        return replace(self, assignments=new_assignments)

    def with_switch(self, defender_id: str, new_assignment_player_id: str,
                     posture: DefensivePosture = DefensivePosture.RECOVERING) -> "PossessionState":
        current = self.assignments.get(defender_id)
        if current is None:
            return self.with_assignment(defender_id, new_assignment_player_id, posture)
        new_assignments = dict(self.assignments)
        new_assignments[defender_id] = current.switch(new_assignment_player_id, posture)
        return replace(self, assignments=new_assignments)


# --------------------------- identity guard ---------------------------

def _assert_player_id(value: Optional[str], field_name: str = "player_id") -> None:
    """Real, enforced guard (not just documentation) that a
    name-keyed value can never enter the Phase 15 engine as an
    identity. A real NBA player_id is a numeric string; a name
    contains a space or non-digit characters. `None` is always allowed
    (an explicit "no player" state, e.g. an unresolved loose ball)."""
    if value is None:
        return
    if not isinstance(value, str) or not value.isdigit():
        raise TypeError(
            f"{field_name} must be a real, stable NBA player_id (a numeric string) or None -- "
            f"got {value!r}. The Phase 15 engine is player_id-only; a name-keyed lookup must be "
            f"resolved through player_identity.py BEFORE calling into this engine, never passed in directly.")
