"""
Phase 15 -- Possession State & Event Kernel: event log / accounting
skeleton.

The event log is the primary simulation accounting truth -- box-score
mutation is NOT baked into individual mechanics; a future stat-
accounting layer derives FGA/FGM/3PA/3PM/FTA/AST/TOV/STL/BLK/OREB/DREB/
PF/possessions (and tracking-like diagnostics: drives, touches, passes,
potential assists, shot-contest state, advantage creation/transfer)
FROM the event stream. No such derivation is implemented this phase --
only the event shape and the log itself.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


class EventType(Enum):
    """Named outcome/state pathways this phase must be able to
    REPRESENT without corrupting possession ownership -- resolution
    logic (deciding WHICH of these happens) is explicitly out of scope."""
    POSSESSION_START = "POSSESSION_START"
    PASS_RELEASED = "PASS_RELEASED"
    PASS_RECEIVED = "PASS_RECEIVED"
    DRIBBLE_GATHERED = "DRIBBLE_GATHERED"
    SHOT_RELEASED = "SHOT_RELEASED"          # shot selection -- distinct from resolution (see SHOT_RESOLVED)
    SHOT_RESOLVED = "SHOT_RESOLVED"          # made/missed FG or FT -- distinct from selection
    SHOOTING_FOUL = "SHOOTING_FOUL"
    NON_SHOOTING_FOUL = "NON_SHOOTING_FOUL"
    LIVE_BALL_TURNOVER = "LIVE_BALL_TURNOVER"    # e.g. a steal/deflection -- ball stays live
    DEAD_BALL_TURNOVER = "DEAD_BALL_TURNOVER"    # e.g. an offensive foul/travel/out-of-bounds violation -- play stops
    BLOCK_RETAINED_BY_OFFENSE = "BLOCK_RETAINED_BY_OFFENSE"
    BLOCK_SECURED_BY_DEFENSE = "BLOCK_SECURED_BY_DEFENSE"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    JUMP_BALL = "JUMP_BALL"
    SHOT_CLOCK_VIOLATION = "SHOT_CLOCK_VIOLATION"
    PERIOD_EXPIRATION = "PERIOD_EXPIRATION"
    OFFENSIVE_REBOUND = "OFFENSIVE_REBOUND"
    DEFENSIVE_REBOUND = "DEFENSIVE_REBOUND"
    ASSIGNMENT_SWITCH = "ASSIGNMENT_SWITCH"
    ADVANTAGE_CHANGE = "ADVANTAGE_CHANGE"
    REACTION_CHECKPOINT = "REACTION_CHECKPOINT"  # a reactive-subevent checkpoint fired (see possession_engine.py)
    DRIVE_RESOLVED = "DRIVE_RESOLVED"  # Phase 17A -- one coarse drive-resolution outcome (see drive_resolution.py); NOT a shot/pass/turnover result itself
    PASS_RESOLVED = "PASS_RESOLVED"  # Phase 17B -- one coarse pass-resolution outcome (see pass_resolution.py); distinct from PASS_RELEASED/PASS_RECEIVED (Phase 15's own selection-adjacent events)


@dataclass(frozen=True)
class Event:
    """One real-time-ordered occurrence. `primary_player_id`/
    `secondary_player_id` are real player_ids or None -- e.g. a pass has
    primary=passer, secondary=receiver; a shot has primary=shooter,
    secondary=assister (or None, unassisted); a block has
    primary=shot-blocker, secondary=shooter."""
    event_type: EventType
    possession_id: str
    delta_t: float  # seconds consumed by this event -- event-driven, not a fixed tick
    primary_player_id: Optional[str] = None
    secondary_player_id: Optional[str] = None
    zone: Optional[str] = None  # SpatialZone.value -- stored as a plain string so the log has no hard dependency on possession_state's enum identity across process boundaries
    metadata: Dict[str, object] = field(default_factory=dict)


class EventLog:
    """Two modes, one interface:
      - "retained": every Event is kept in a real Python list, for
        debugging/replay -- the default, and what every test in this
        phase uses.
      - "streaming": events are NOT retained; each one is handed to an
        `aggregator` callback and then discarded, so a long
        multi-season simulation doesn't have to hold millions of Event
        objects in RAM. The aggregator is caller-supplied (e.g. a
        future box-score accumulator) -- this class does not know or
        care what it does with an event.
    """

    def __init__(self, mode: str = "retained", aggregator: Optional[Callable[[Event], None]] = None):
        if mode not in ("retained", "streaming"):
            raise ValueError(f"EventLog mode must be 'retained' or 'streaming' -- got {mode!r}")
        if mode == "streaming" and aggregator is None:
            raise ValueError("streaming mode requires an aggregator callback -- otherwise every event would simply vanish")
        self.mode = mode
        self._aggregator = aggregator
        self._events: List[Event] = []  # only ever populated in "retained" mode

    def record(self, event: Event) -> None:
        if self.mode == "retained":
            self._events.append(event)
        else:
            self._aggregator(event)

    @property
    def events(self) -> Tuple[Event, ...]:
        """Only meaningful in 'retained' mode -- returns an empty tuple
        in 'streaming' mode (nothing was kept, not an error: the caller
        chose streaming mode precisely to avoid retention)."""
        return tuple(self._events)

    def __len__(self) -> int:
        return len(self._events)
