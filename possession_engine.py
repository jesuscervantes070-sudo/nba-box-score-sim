"""
Phase 15 -- Possession State & Event Kernel: the orchestration layer.

Ties together possession_state.py (state primitives), possession_advantage.py
(the advantage interface), possession_rules.py (era rules), and
possession_events.py (the event log) into one coherent, event-driven
possession object. NO empirical action mechanics live here -- every
method below is a STATE TRANSITION with a caller-supplied outcome (e.g.
`resolve_shot_made(shooter_id, ...)` -- the caller decides the shot was
made; this method only makes that decision's consequences on state/
event-log CORRECT, never decides whether it happens).

PLAYER_ID-ONLY: every public method that takes a player argument calls
`_assert_player_id` (from possession_state.py) on it. A name-keyed value
raises immediately -- see `test_name_keyed_path_rejected` in
test_possession_kernel.py. The ONE sanctioned bridge from a legacy name
to an engine-usable id is `resolve_legacy_name_to_engine_id` at the
bottom of this file, which goes through player_identity.py (Phase 14)
and refuses to guess on an ambiguous/unresolved name, exactly like every
other Phase 14 caller.
"""
import random
from dataclasses import replace
from typing import Callable, List, Optional, Tuple

from possession_advantage import AdvantageModel
from possession_events import Event, EventLog, EventType
from possession_rules import EraRules, get_era_rules, oreb_reset_value
from possession_state import (
    BallState, DefensivePosture, PossessionPhase, PossessionState, SpatialZone,
    _assert_player_id,
)


class PossessionEngine:
    def __init__(self, possession_id: str, offense_team_id: str, defense_team_id: str,
                 era_rules: Optional[EraRules] = None, season: Optional[str] = None,
                 rng_seed: Optional[int] = None, event_log: Optional[EventLog] = None,
                 advantage: Optional[AdvantageModel] = None):
        if era_rules is None:
            era_rules = get_era_rules(season) if season is not None else get_era_rules("2023-24")
        self.era_rules = era_rules
        self.rng = random.Random(rng_seed)  # a real, explicit, per-engine RNG instance -- never the global `random` module -- required for deterministic replay under a fixed seed
        self.log = event_log if event_log is not None else EventLog(mode="retained")
        self.advantage: Optional[AdvantageModel] = advantage  # possession context, not player ability -- may be None (no representation chosen yet)

        self.state = PossessionState(
            possession_id=possession_id, offense_team_id=offense_team_id, defense_team_id=defense_team_id,
            phase=PossessionPhase.DEAD_BALL, ball_state=BallState.DEAD,
            shot_clock_remaining=era_rules.shot_clock_seconds,
            game_clock_remaining=era_rules.period_length_seconds,
        )

    # --------------------------- lifecycle ---------------------------

    def inbound(self, receiver_id: str, zone: SpatialZone, phase: PossessionPhase, dt: float = 0.0) -> None:
        _assert_player_id(receiver_id)
        self.state = replace(self.state.with_ball_carrier(receiver_id, BallState.HELD), phase=phase, ball_zone=zone)
        self._log(EventType.POSSESSION_START, dt, primary=receiver_id, zone=zone)

    # --------------------------- passing ---------------------------

    def pass_ball(self, dt: float) -> None:
        """Releases the ball from the current carrier -- offense TEAM
        possession is untouched (still `self.state.offense_team_id`);
        only individual carrier/ball-state changes."""
        _assert_player_id(self.state.ball_carrier)
        if self.state.ball_state != BallState.HELD or self.state.ball_carrier is None:
            raise ValueError("cannot pass -- ball is not currently HELD by a carrier")
        passer = self.state.ball_carrier
        self.state = self.state.with_ball_carrier(None, BallState.PASS_IN_FLIGHT)
        self._log(EventType.PASS_RELEASED, dt, primary=passer)

    def receive_pass(self, receiver_id: str, dt: float) -> None:
        _assert_player_id(receiver_id)
        if self.state.ball_state != BallState.PASS_IN_FLIGHT:
            raise ValueError("cannot receive -- no pass is in flight")
        self.state = self.state.with_ball_carrier(receiver_id, BallState.HELD)
        self._log(EventType.PASS_RECEIVED, dt, primary=receiver_id)

    # --------------------------- dribble / control ---------------------------

    def gather(self, dt: float = 0.0) -> None:
        _assert_player_id(self.state.ball_carrier)
        if self.state.ball_control is None:
            raise ValueError("no ball control state to gather -- ball is not HELD")
        self.state = replace(self.state, ball_control=self.state.ball_control.gather())
        self._log(EventType.DRIBBLE_GATHERED, dt, primary=self.state.ball_carrier)

    def dead_dribble(self, dt: float = 0.0) -> None:
        """Phase 17A addition -- a real, small gap: no existing method
        reached DEAD_DRIBBLE directly (only via gather() -> go_dead()
        chained by a caller). Purely additive; `gather`/`begin_shot`/
        every other Phase 15 method is unchanged."""
        _assert_player_id(self.state.ball_carrier)
        if self.state.ball_control is None:
            raise ValueError("no ball control state to end -- ball is not HELD")
        self.state = replace(self.state, ball_control=self.state.ball_control.go_dead())

    def advance_ball_zone(self, new_zone: SpatialZone, dt: float = 0.0) -> None:
        """Phase 17A addition -- a real, small gap: Phase 15 had no
        zone-only transition (every existing method that touches
        `ball_zone` does so as a side effect of a shot/inbound). Used by
        drive_resolution.py to move the ball zone WITHOUT changing
        carrier/ball-state -- coarse, structural (still one of the
        provisional 8 zones), never a continuous XY coordinate."""
        self.state = replace(self.state, ball_zone=new_zone)

    # --------------------------- shooting ---------------------------

    def begin_shot(self, zone: SpatialZone, dt: float) -> str:
        """SHOT SELECTION -- distinct from SHOT RESOLUTION
        (resolve_shot_*, below). Returns the shooter's player_id so the
        caller doesn't have to remember it separately."""
        shooter = self.state.ball_carrier
        _assert_player_id(shooter)
        if self.state.ball_state != BallState.HELD or shooter is None:
            raise ValueError("cannot shoot -- ball is not currently HELD by a carrier")
        self.state = self.state.with_ball_carrier(None, BallState.SHOT_IN_FLIGHT)
        self.state = replace(self.state, ball_zone=zone)
        self._log(EventType.SHOT_RELEASED, dt, primary=shooter, zone=zone)
        return shooter

    def resolve_shot_made(self, shooter_id: str, assisted_by: Optional[str] = None, dt: float = 0.0) -> None:
        _assert_player_id(shooter_id)
        _assert_player_id(assisted_by)
        self._require_shot_in_flight()
        self.state = replace(self.state, ball_state=BallState.DEAD, phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.SHOT_RESOLVED, dt, primary=shooter_id, secondary=assisted_by, meta={"made": True})

    def resolve_shot_missed_defensive_rebound(self, shooter_id: str, rebounder_id: str, dt: float = 0.0) -> None:
        """The original offense's possession TERMINATES here -- the
        rebounding team becomes the new offense. This engine does not
        itself flip `offense_team_id`/`defense_team_id` to real team ids
        (it doesn't know team rosters) -- the caller starts a NEW
        PossessionEngine (or calls a future `start_new_possession`
        helper) with the swapped team ids; this method only makes the
        CURRENT possession's terminal state correct."""
        _assert_player_id(shooter_id)
        _assert_player_id(rebounder_id)
        self._require_shot_in_flight()
        self.state = self.state.with_ball_carrier(rebounder_id, BallState.HELD)
        self.state = replace(self.state, phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.SHOT_RESOLVED, dt, primary=shooter_id, meta={"made": False})
        self._log(EventType.DEFENSIVE_REBOUND, 0.0, primary=rebounder_id)

    def resolve_shot_missed_pending_rebound(self, shooter_id: str, dt: float = 0.0) -> None:
        """Phase 18A addition -- a real, small gap: a shot-resolution
        phase (18A) that must NOT select a rebound winner needs a way to
        represent "missed, ball now loose, rebound outcome deliberately
        left to a future phase" -- neither existing miss method fits,
        since both already require a real `rebounder_id`. Ball becomes
        `LOOSE` and `offense_team_id` genuinely unresolved, same
        convention as Phase 15's own `block_secured_by_defense` and
        Phase 17B's `DEFLECTED_LOOSE_BALL` handling -- reused, not
        reinvented. Purely additive; every other method is unchanged."""
        _assert_player_id(shooter_id)
        self._require_shot_in_flight()
        self.state = replace(self.state, ball_state=BallState.LOOSE, ball_carrier=None,
                              ball_control=None, offense_team_id=None, phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.SHOT_RESOLVED, dt, primary=shooter_id, meta={"made": False})

    def resolve_shot_missed_offensive_rebound(self, shooter_id: str, rebounder_id: str, dt: float = 0.0) -> None:
        """A real, offense-context-preserving CONTINUATION, not a
        termination -- but explicitly creates a NEW SECOND_CHANCE
        context: does NOT automatically preserve old advantage, does
        NOT reset to a forced-neutral state, and does NOT force a
        putback attempt. `self.advantage` is simply cleared to None
        here (an explicit "not yet re-established" state) -- a caller
        wanting a specific fresh-advantage representation must set one
        explicitly, matching "must NOT automatically preserve old
        advantage" and "must NOT automatically reset to neutral" (a
        silent reset to a NEUTRAL discrete-tier instance would itself be
        an automatic choice of representation and value)."""
        _assert_player_id(shooter_id)
        _assert_player_id(rebounder_id)
        self._require_shot_in_flight()
        old_shot_clock = self.state.shot_clock_remaining
        self.state = self.state.with_ball_carrier(rebounder_id, BallState.HELD)
        self.state = replace(self.state, phase=PossessionPhase.SECOND_CHANCE,
                              shot_clock_remaining=oreb_reset_value(self.era_rules, old_shot_clock))
        self.advantage = None
        self._log(EventType.SHOT_RESOLVED, dt, primary=shooter_id, meta={"made": False})
        self._log(EventType.OFFENSIVE_REBOUND, 0.0, primary=rebounder_id)

    def _require_shot_in_flight(self) -> None:
        if self.state.ball_state != BallState.SHOT_IN_FLIGHT:
            raise ValueError("no shot is currently in flight to resolve")

    def secure_offensive_rebound_from_loose(self, rebounder_id: str, offense_team_id: Optional[str] = None, dt: float = 0.0) -> None:
        """Phase 19 addition -- a real, small gap: `resolve_shot_missed_offensive_rebound`
        (above) requires `SHOT_IN_FLIGHT`, but by the time Phase 19 acts,
        Phases 18A/18B/18C have already transitioned the ball to `LOOSE`
        (via `resolve_shot_missed_pending_rebound`/`block_secured_by_defense`/
        the final-missed-FT handoff). This method is the IDENTICAL real
        logic as `resolve_shot_missed_offensive_rebound` (same SECOND_CHANCE
        phase transition, same era-rule shot-clock reset, same advantage-
        clearing rule), just guarded on the real state Phase 19 actually
        receives. Not a parallel model -- the same rules, applied at the
        correct point in the real pipeline. `offense_team_id`, if given,
        RESTORES team possession -- several upstream LOOSE-ball
        transitions (e.g. `block_secured_by_defense`) genuinely null
        `offense_team_id` as "unresolved"; once an offensive rebound
        confirms who kept it, that team id is real and known again, and
        this is the one place it gets restored (never guessed)."""
        _assert_player_id(rebounder_id)
        if self.state.ball_state != BallState.LOOSE:
            raise ValueError("no loose ball to secure as an offensive rebound")
        old_shot_clock = self.state.shot_clock_remaining
        self.state = self.state.with_ball_carrier(rebounder_id, BallState.HELD)
        self.state = replace(self.state, phase=PossessionPhase.SECOND_CHANCE,
                              shot_clock_remaining=oreb_reset_value(self.era_rules, old_shot_clock),
                              offense_team_id=offense_team_id if offense_team_id is not None else self.state.offense_team_id)
        self.advantage = None
        self._log(EventType.OFFENSIVE_REBOUND, dt, primary=rebounder_id)

    def secure_defensive_rebound_from_loose(self, rebounder_id: str, new_offense_team_id: str,
                                              new_defense_team_id: str, dt: float = 0.0) -> None:
        """Phase 19 addition -- same real logic as
        `resolve_shot_missed_defensive_rebound`, guarded on `LOOSE`
        instead of `SHOT_IN_FLIGHT`, and taking the real new team ids
        directly (the caller knows them; this engine instance's own
        `offense_team_id`/`defense_team_id` are updated in place rather
        than requiring a brand-new `PossessionEngine`, since the
        rebounding team's very next action -- Phase 16 selection --
        happens on this SAME engine)."""
        _assert_player_id(rebounder_id)
        if self.state.ball_state != BallState.LOOSE:
            raise ValueError("no loose ball to secure as a defensive rebound")
        self.state = self.state.with_ball_carrier(rebounder_id, BallState.HELD)
        self.state = replace(self.state, phase=PossessionPhase.TRANSITION,
                              offense_team_id=new_offense_team_id, defense_team_id=new_defense_team_id)
        self._log(EventType.DEFENSIVE_REBOUND, dt, primary=rebounder_id)

    def credit_team_rebound(self, rebounding_side: str, new_offense_team_id: Optional[str] = None,
                              new_defense_team_id: Optional[str] = None, dt: float = 0.0) -> None:
        """A team rebound -- no individual player secures it (e.g. the
        ball goes out of bounds off the miss). `rebounding_side` is
        `"OFFENSE"` (a real offensive team rebound: SECOND_CHANCE, same
        era-rule shot-clock handling as an individual OREB, no
        individual carrier, current team ids unchanged) or `"DEFENSE"`
        (possession flips to the real new team ids the caller supplies
        -- this engine doesn't know team rosters, same convention as
        every other terminal method in this class)."""
        if self.state.ball_state != BallState.LOOSE:
            raise ValueError("no loose ball to credit as a team rebound")
        if rebounding_side == "OFFENSE":
            old_shot_clock = self.state.shot_clock_remaining
            self.state = replace(self.state, ball_state=BallState.DEAD, ball_carrier=None, ball_control=None,
                                  phase=PossessionPhase.SECOND_CHANCE,
                                  shot_clock_remaining=oreb_reset_value(self.era_rules, old_shot_clock))
            self.advantage = None
        elif rebounding_side == "DEFENSE":
            self.state = replace(self.state, ball_state=BallState.DEAD, ball_carrier=None, ball_control=None,
                                  phase=PossessionPhase.DEAD_BALL,
                                  offense_team_id=new_offense_team_id, defense_team_id=new_defense_team_id)
        else:
            raise ValueError(f"rebounding_side must be 'OFFENSE' or 'DEFENSE', got {rebounding_side!r}")
        self._log(EventType.OUT_OF_BOUNDS, dt)

    # --------------------------- fouls ---------------------------

    def shooting_foul(self, shooter_id: str, fouler_id: str, dt: float = 0.0) -> None:
        _assert_player_id(shooter_id)
        _assert_player_id(fouler_id)
        self.state = replace(self.state, ball_state=BallState.DEAD, ball_carrier=None,
                              ball_control=None, phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.SHOOTING_FOUL, dt, primary=fouler_id, secondary=shooter_id)

    def non_shooting_foul(self, fouler_id: str, fouled_id: str, team_foul_count: int, dt: float = 0.0) -> bool:
        """Returns whether this foul puts the fouled team in the bonus,
        per `self.era_rules.bonus_foul_threshold` -- a real, minimal use
        of the era-rules hook (NOT a full penalty/free-throw
        implementation, which is out of scope this phase)."""
        _assert_player_id(fouler_id)
        _assert_player_id(fouled_id)
        self.state = replace(self.state, ball_state=BallState.DEAD, ball_carrier=None,
                              ball_control=None, phase=PossessionPhase.DEAD_BALL)
        in_bonus = (self.era_rules.bonus_foul_threshold is not None
                    and team_foul_count >= self.era_rules.bonus_foul_threshold)
        self._log(EventType.NON_SHOOTING_FOUL, dt, primary=fouler_id, secondary=fouled_id, meta={"in_bonus": in_bonus})
        return in_bonus

    # --------------------------- turnovers ---------------------------

    def live_ball_turnover(self, recovered_by: str, new_offense_team_id: str, new_defense_team_id: str, dt: float = 0.0) -> None:
        """e.g. a steal -- the ball stays LIVE, possession flips
        immediately without a dead-ball stoppage."""
        _assert_player_id(recovered_by)
        self.state = self.state.with_ball_carrier(recovered_by, BallState.HELD)
        self.state = replace(self.state, offense_team_id=new_offense_team_id, defense_team_id=new_defense_team_id,
                              phase=PossessionPhase.TRANSITION)
        self._log(EventType.LIVE_BALL_TURNOVER, dt, primary=recovered_by)

    def dead_ball_turnover(self, committed_by: str, dt: float = 0.0) -> None:
        _assert_player_id(committed_by)
        self.state = replace(self.state, ball_state=BallState.DEAD, ball_carrier=None,
                              ball_control=None, phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.DEAD_BALL_TURNOVER, dt, primary=committed_by)

    def shot_clock_violation(self, dt: float = 0.0) -> None:
        self.state = replace(self.state, ball_state=BallState.DEAD, ball_carrier=None,
                              ball_control=None, phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.SHOT_CLOCK_VIOLATION, dt)

    def period_expiration(self, dt: float = 0.0) -> None:
        self.state = replace(self.state, ball_state=BallState.DEAD, ball_carrier=None,
                              ball_control=None, phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.PERIOD_EXPIRATION, dt)

    # --------------------------- blocks / loose balls / jump balls ---------------------------

    def block_retained_by_offense(self, blocker_id: str, shooter_id: str, dt: float = 0.0) -> None:
        _assert_player_id(blocker_id)
        _assert_player_id(shooter_id)
        self._require_shot_in_flight()
        self.state = replace(self.state, ball_state=BallState.LOOSE, ball_carrier=None, ball_control=None)
        # offense_team_id deliberately left AS-IS: a block retained by the offense means the offense's own team possession
        # is not in question, only which individual player will secure the loose ball -- team ownership must not go
        # unresolved here (see block_secured_by_defense for the genuinely-unresolved case).
        self._log(EventType.BLOCK_RETAINED_BY_OFFENSE, dt, primary=blocker_id, secondary=shooter_id)

    def block_secured_by_defense(self, blocker_id: str, shooter_id: str, dt: float = 0.0) -> None:
        _assert_player_id(blocker_id)
        _assert_player_id(shooter_id)
        self._require_shot_in_flight()
        self.state = replace(self.state, ball_state=BallState.LOOSE, ball_carrier=None,
                              ball_control=None, offense_team_id=None)  # genuinely unresolved until secured
        self._log(EventType.BLOCK_SECURED_BY_DEFENSE, dt, primary=blocker_id, secondary=shooter_id)

    def secure_loose_ball(self, player_id: str, team_id: str, other_team_id: str, dt: float = 0.0) -> None:
        _assert_player_id(player_id)
        if self.state.ball_state != BallState.LOOSE:
            raise ValueError("no loose ball to secure")
        self.state = self.state.with_ball_carrier(player_id, BallState.HELD)
        self.state = replace(self.state, offense_team_id=team_id, defense_team_id=other_team_id, phase=PossessionPhase.TRANSITION)

    def out_of_bounds(self, new_offense_team_id: str, new_defense_team_id: str, dt: float = 0.0) -> None:
        self.state = replace(self.state, ball_state=BallState.DEAD, ball_carrier=None, ball_control=None,
                              offense_team_id=new_offense_team_id, defense_team_id=new_defense_team_id,
                              phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.OUT_OF_BOUNDS, dt)

    def jump_ball(self, dt: float = 0.0) -> None:
        self.state = replace(self.state, ball_state=BallState.LOOSE, ball_carrier=None,
                              ball_control=None, offense_team_id=None, defense_team_id=None,
                              phase=PossessionPhase.DEAD_BALL)
        self._log(EventType.JUMP_BALL, dt)

    # --------------------------- defensive assignment ---------------------------

    def switch(self, defender_id: str, new_assignment_player_id: str,
               posture: DefensivePosture = DefensivePosture.RECOVERING, dt: float = 0.0) -> None:
        _assert_player_id(defender_id)
        _assert_player_id(new_assignment_player_id)
        self.state = self.state.with_switch(defender_id, new_assignment_player_id, posture)
        self._log(EventType.ASSIGNMENT_SWITCH, dt, primary=defender_id, secondary=new_assignment_player_id)

    def update_posture(self, defender_id: str, posture: DefensivePosture) -> None:
        _assert_player_id(defender_id)
        current = self.state.assignments.get(defender_id)
        if current is None:
            raise ValueError(f"no assignment exists for defender {defender_id} to update posture on")
        new_assignments = dict(self.state.assignments)
        new_assignments[defender_id] = current.update_posture(posture)
        self.state = replace(self.state, assignments=new_assignments)

    # --------------------------- reactive subevent / interruption mechanism ---------------------------

    def run_checkpointed_action(self, checkpoints: List[str],
                                 reaction_fn: Callable[[str, PossessionState], Optional[str]],
                                 dt_per_checkpoint: float = 0.0) -> Tuple[str, bool]:
        """Runs an action through an ordered list of named checkpoints
        (e.g. ["drive_begins", "poa_interaction", "defender_beaten",
        "help_opportunity", "help_arrives_or_fails", "release_opportunity"]),
        calling `reaction_fn(checkpoint_name, current_state)` at each one
        BEFORE proceeding to the next. If `reaction_fn` returns the
        string "INTERRUPT", the action stops immediately at that
        checkpoint (the remaining checkpoints never run) -- this IS the
        interruption mechanism: event-driven does not mean
        non-interruptible. Any other return value (including None)
        means "continue." Returns (last_checkpoint_reached, was_interrupted).

        No event DURATION is invented beyond the caller-supplied
        `dt_per_checkpoint` (defaults to 0.0 -- pure sequencing, no
        fabricated timing), consistent with "do not invent event
        durations yet unless required only for test fixtures."."""
        last = ""
        for checkpoint in checkpoints:
            last = checkpoint
            self._log(EventType.REACTION_CHECKPOINT, dt_per_checkpoint, meta={"checkpoint": checkpoint})
            signal = reaction_fn(checkpoint, self.state)
            if signal == "INTERRUPT":
                return checkpoint, True
        return last, False

    # --------------------------- internal ---------------------------

    def _log(self, event_type: EventType, dt: float, primary: Optional[str] = None,
              secondary: Optional[str] = None, zone: Optional[SpatialZone] = None, meta: Optional[dict] = None) -> None:
        self.log.record(Event(
            event_type=event_type, possession_id=self.state.possession_id, delta_t=dt,
            primary_player_id=primary, secondary_player_id=secondary,
            zone=zone.value if zone is not None else None, metadata=meta or {},
        ))


# --------------------------- legacy-name isolation bridge ---------------------------

def resolve_legacy_name_to_engine_id(player_name: str, season_hint: Optional[str] = None) -> str:
    """The ONE sanctioned path from a legacy, name-keyed value into this
    player_id-only engine -- goes through player_identity.py (Phase 14)
    and raises rather than guessing on anything not RESOLVED. There is
    no other function anywhere in the Phase 15 engine that accepts a
    name."""
    import player_identity as pid
    resolution = pid.resolve_name_to_id(player_name, season_hint=season_hint)
    if resolution.state != pid.RESOLVED:
        raise ValueError(f"cannot bridge legacy name {player_name!r} into the engine -- resolution state was "
                          f"{resolution.state}, not RESOLVED ({resolution.note})")
    return resolution.player_id
