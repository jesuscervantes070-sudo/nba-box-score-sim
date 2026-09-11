"""Phase 23B -- bounded chained possessions with persistent game state.

Ownership is intentionally narrow:

* ``DetailedGameState`` persists game-level clock, score, team control,
  period, foul administration, and the next possession sequence.
* Phase 23A ``PossessionState``/``PossessionWorld`` are rebuilt for each
  possession; only an explicit ``RestartContext`` crosses the boundary.
* each ``PossessionRecord.events`` tuple is the accounting truth.
* ``PossessionTerminalResult`` owns control-flow termination and the next
  team ownership.

Score is the one temporary exception to fully event-derived accounting:
Phase 23A's event schema cannot yet derive points/FTs/shot value, so this
module applies ``StatDeltas.points`` once as a documented provisional
orchestration bridge. Every event-derivable delta is cross-checked before
state is advanced.
"""
import random
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Tuple

from floor_foul_administration import FoulAdministrationState, effective_bonus_foul_threshold
from possession_events import Event, EventType
from possession_orchestrator import (
    EventDerivedStats,
    PlayerSimulationProfile,
    PossessionConfig,
    PossessionTerminalReason,
    PossessionTerminalResult,
    StatDeltas,
    derive_stat_deltas_from_events,
    simulate_possession,
    validate_lineups,
)
from possession_rules import EraRules, get_era_rules
from possession_state import BallState, PossessionPhase, SpatialZone, _assert_player_id
from transition_state import PossessionChangeSource, TransitionDiagnostic, build_transition_diagnostic


class DetailedGameInvariantError(RuntimeError):
    """Raised for orchestration/state corruption, never converted to a game outcome."""


class RestartType:
    DEAD_BALL_INBOUND = "DEAD_BALL_INBOUND"
    LIVE_TRANSITION = "LIVE_TRANSITION"


class SegmentStopReason:
    MAX_POSSESSIONS = "MAX_POSSESSIONS"
    PERIOD_COMPLETE = "PERIOD_COMPLETE"


SUPPORTED_TERMINAL_REASONS = frozenset({
    PossessionTerminalReason.MADE_FG,
    PossessionTerminalReason.FINAL_FT_MADE,
    PossessionTerminalReason.DEFENSIVE_REBOUND,
    PossessionTerminalReason.TURNOVER,
    PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER,
    PossessionTerminalReason.SHOT_CLOCK_VIOLATION,
    PossessionTerminalReason.PERIOD_END,
})


@dataclass(frozen=True)
class RestartContext:
    """The only possession-boundary basketball context carried forward.

    V0 reconstructs coarse geometry and matchups for every possession.
    Live turnovers/rebounds preserve the real carrier and transition
    classification, but start at TOP_OF_KEY because Phase 23A deliberately
    gates transition advancement -- `restart_type`/`ball_carrier_id`/
    `ball_zone` remain UNCHANGED by this addition; ordinary orchestration
    still ignores `transition_diagnostic` entirely.

    `transition_diagnostic`: DIAGNOSTIC ONLY (see `transition_state.py`'s
    own "Observational Transition Classifier" section) -- a
    `transition_state.TransitionDiagnostic` snapshot of the structural
    state available at this restart, or `None` for a DEAD_BALL_INBOUND
    source. NEVER consulted by any simulation decision anywhere in this
    module or `possession_orchestrator.py` -- confirmed by test
    (`test_transition_diagnostic_cannot_affect_simulation_decisions`).
    A future, SEPARATE, explicit change would be required to ever let
    this field influence `restart_type`/timing/matchups.
    """
    restart_type: str
    source: str
    ball_carrier_id: Optional[str] = None
    ball_zone: SpatialZone = SpatialZone.TOP_OF_KEY
    transition_diagnostic: Optional["TransitionDiagnostic"] = None


@dataclass(frozen=True)
class DetailedGameState:
    """Only state that genuinely persists across possession boundaries."""
    home_team_id: str
    away_team_id: str
    current_offense_team_id: str
    current_defense_team_id: str
    period: int
    game_clock_seconds: float
    score_home: int = 0
    score_away: int = 0
    foul_state: FoulAdministrationState = field(default_factory=FoulAdministrationState)
    next_possession_sequence: int = 1
    last_terminal_reason: Optional[str] = None

    @property
    def team_fouls_by_team(self) -> Dict[str, int]:
        return dict(self.foul_state.team_fouls)

    def in_bonus(self, team_id: str, rules: EraRules, is_overtime: bool = False) -> bool:
        """Whether ``team_id`` has committed enough qualifying fouls.

        This is derived from Phase 21B's persisted category-aware counter and
        threshold helper; Phase 23B never detects or reclassifies a foul.
        """
        threshold = effective_bonus_foul_threshold(rules, is_overtime=is_overtime)
        return threshold is not None and self.foul_state.team_foul_count(team_id) >= threshold


@dataclass(frozen=True)
class PossessionStart:
    possession_id: str
    offense_team_id: str
    defense_team_id: str
    offensive_five: Tuple[str, ...]
    defensive_five: Tuple[str, ...]
    inbound_receiver_id: str
    phase: PossessionPhase
    ball_zone: SpatialZone
    restart_context: RestartContext


@dataclass(frozen=True)
class PossessionRecord:
    possession_id: str
    offense_team_id: str
    defense_team_id: str
    start_game_clock: float
    end_game_clock: float
    start_score_home: int
    start_score_away: int
    end_score_home: int
    end_score_away: int
    restart_context: RestartContext
    terminal_result: PossessionTerminalResult
    events: Tuple[Event, ...]
    provisional_deltas: StatDeltas


@dataclass(frozen=True)
class MultiPossessionResult:
    final_state: DetailedGameState
    possessions: Tuple[PossessionRecord, ...]
    stop_reason: str
    next_restart_context: Optional[RestartContext]

    @property
    def events(self) -> Tuple[Event, ...]:
        """Derived ordered view; no second mutable game-level event log."""
        return tuple(event for record in self.possessions for event in record.events)


def _lineup_for(team_id: str, state: DetailedGameState,
                home_five: Tuple[str, ...], away_five: Tuple[str, ...]) -> Tuple[str, ...]:
    if team_id == state.home_team_id:
        return home_five
    if team_id == state.away_team_id:
        return away_five
    raise DetailedGameInvariantError(f"unknown configured team_id {team_id!r}")


def validate_detailed_game_inputs(state: DetailedGameState,
                                  home_five: Tuple[str, ...], away_five: Tuple[str, ...],
                                  profiles: Dict[str, PlayerSimulationProfile]) -> None:
    if not state.home_team_id or not state.away_team_id or state.home_team_id == state.away_team_id:
        raise ValueError("home_team_id and away_team_id must be distinct non-empty ids")
    configured = {state.home_team_id, state.away_team_id}
    if {state.current_offense_team_id, state.current_defense_team_id} != configured:
        raise ValueError("current offense/defense must be the two configured teams, exactly once")
    if state.period < 1:
        raise ValueError("period must be >= 1")
    if state.game_clock_seconds < 0.0:
        raise ValueError("game clock cannot be negative")
    if state.score_home < 0 or state.score_away < 0:
        raise ValueError("scores cannot be negative")
    if state.next_possession_sequence < 1:
        raise ValueError("next_possession_sequence must be >= 1")

    validate_lineups(home_five, away_five)
    for player_id in home_five + away_five:
        _assert_player_id(player_id)
    expected_ids = set(home_five + away_five)
    if set(profiles) != expected_ids:
        raise ValueError("profiles must contain exactly the ten on-court player_ids; no synthetic fallback is allowed")
    for player_id in home_five:
        if profiles[player_id].player_id != player_id or profiles[player_id].team_id != state.home_team_id:
            raise ValueError(f"home profile identity/team mismatch for {player_id!r}")
    for player_id in away_five:
        if profiles[player_id].player_id != player_id or profiles[player_id].team_id != state.away_team_id:
            raise ValueError(f"away profile identity/team mismatch for {player_id!r}")
    if any(team_id not in configured or count < 0 for team_id, count in state.foul_state.team_fouls.items()):
        raise ValueError("team foul counters must be non-negative and belong to a configured team")


def initialize_next_possession(state: DetailedGameState,
                               home_five: Tuple[str, ...], away_five: Tuple[str, ...],
                               restart_context: Optional[RestartContext] = None) -> PossessionStart:
    """Authoritative game-state + lineups + restart -> Phase 23A inputs.

    Callers never construct ``StructuralContext`` or matchups. Phase 23A
    rebuilds both from these inputs. Dead-ball V0 inbounds and live-transition
    handoffs both use TOP_OF_KEY as the explicit coarse start-zone policy;
    the latter retains TRANSITION phase and its real carrier but no unsafe
    stale/orientation-dependent geometry.
    """
    offense_five = _lineup_for(state.current_offense_team_id, state, home_five, away_five)
    defense_five = _lineup_for(state.current_defense_team_id, state, home_five, away_five)
    validate_lineups(offense_five, defense_five)
    restart = restart_context or RestartContext(
        restart_type=RestartType.DEAD_BALL_INBOUND,
        source=PossessionChangeSource.PERIOD_START,
        ball_carrier_id=offense_five[0],
    )
    if restart.restart_type not in (RestartType.DEAD_BALL_INBOUND, RestartType.LIVE_TRANSITION):
        raise ValueError(f"unsupported restart_type {restart.restart_type!r}")
    receiver = restart.ball_carrier_id or offense_five[0]
    if receiver not in offense_five:
        raise ValueError("restart ball carrier must belong to the current offensive five")
    phase = PossessionPhase.TRANSITION if restart.restart_type == RestartType.LIVE_TRANSITION \
        else PossessionPhase.HALFCOURT
    return PossessionStart(
        possession_id=f"period{state.period}-possession{state.next_possession_sequence}",
        offense_team_id=state.current_offense_team_id,
        defense_team_id=state.current_defense_team_id,
        offensive_five=offense_five,
        defensive_five=defense_five,
        inbound_receiver_id=receiver,
        phase=phase,
        ball_zone=restart.ball_zone,
        restart_context=restart,
    )


def _assert_event_accounting_parity(result: PossessionTerminalResult) -> EventDerivedStats:
    derived = derive_stat_deltas_from_events(result.events)
    direct = result.stats
    for name in ("oreb", "dreb", "turnovers", "team_turnovers", "steals", "blocks"):
        if getattr(derived, name) != getattr(direct, name):
            raise DetailedGameInvariantError(f"event/provisional {name} mismatch")
    if derived.player_turnovers != direct.player_turnovers:
        raise DetailedGameInvariantError("event/provisional player_turnovers mismatch")
    if derived.personal_fouls != direct.personal_fouls:
        raise DetailedGameInvariantError("event/provisional personal_fouls mismatch")
    return derived


def apply_possession_result(state: DetailedGameState, start: PossessionStart,
                            result: PossessionTerminalResult) -> DetailedGameState:
    """Apply one terminal result exactly once to persistent state."""
    if start.offense_team_id != state.current_offense_team_id or start.defense_team_id != state.current_defense_team_id:
        raise DetailedGameInvariantError("possession start does not match current game-state ownership")
    if result.engine_state.possession_id != start.possession_id:
        raise DetailedGameInvariantError("terminal result possession_id does not match its start")
    if any(event.possession_id != start.possession_id for event in result.events):
        raise DetailedGameInvariantError("a possession record contains an event from another possession")

    end_clock = result.engine_state.game_clock_remaining
    if end_clock is None:
        raise DetailedGameInvariantError("Phase 23B requires an authoritative game clock")
    if end_clock < 0.0 or end_clock > state.game_clock_seconds:
        raise DetailedGameInvariantError("game clock became negative or increased during a possession")
    if result.reason not in SUPPORTED_TERMINAL_REASONS:
        raise DetailedGameInvariantError(f"unsupported terminal reason {result.reason!r}")
    if result.reason == PossessionTerminalReason.PERIOD_END and end_clock != 0.0:
        raise DetailedGameInvariantError("PERIOD_END requires an expired game clock")
    _assert_event_accounting_parity(result)

    points = result.stats.points  # PROVISIONAL bridge; see module docstring/report.
    if not isinstance(points, int) or points < 0:
        raise DetailedGameInvariantError("provisional possession points must be a non-negative integer")
    score_home = state.score_home + (points if start.offense_team_id == state.home_team_id else 0)
    score_away = state.score_away + (points if start.offense_team_id == state.away_team_id else 0)

    if result.reason == PossessionTerminalReason.PERIOD_END:
        next_offense, next_defense = state.current_offense_team_id, state.current_defense_team_id
    else:
        expected_offense, expected_defense = state.current_defense_team_id, state.current_offense_team_id
        if (result.resulting_offense_team_id, result.resulting_defense_team_id) != (expected_offense, expected_defense):
            raise DetailedGameInvariantError(
                "typed terminal team ownership did not flip exactly once at the possession boundary")
        next_offense, next_defense = expected_offense, expected_defense

    foul_state = result.world.foul_state
    configured = {state.home_team_id, state.away_team_id}
    if any(team_id not in configured or count < 0 for team_id, count in foul_state.team_fouls.items()):
        raise DetailedGameInvariantError("invalid persistent team-foul state returned by possession")
    if any(foul_state.team_foul_count(team_id) < state.foul_state.team_foul_count(team_id)
           for team_id in configured):
        raise DetailedGameInvariantError("team-foul count decreased inside a period")
    if any(foul_state.personal_fouls.counts.get(player_id, 0) < count
           for player_id, count in state.foul_state.personal_fouls.counts.items()):
        raise DetailedGameInvariantError("personal-foul count decreased across possessions")

    return replace(
        state,
        current_offense_team_id=next_offense,
        current_defense_team_id=next_defense,
        game_clock_seconds=end_clock,
        score_home=score_home,
        score_away=score_away,
        foul_state=foul_state,
        next_possession_sequence=state.next_possession_sequence + 1,
        last_terminal_reason=result.reason,
    )


def next_restart_context(result: PossessionTerminalResult,
                         new_state: DetailedGameState,
                         home_five: Tuple[str, ...], away_five: Tuple[str, ...]) -> Optional[RestartContext]:
    """Centralized terminal reason -> next-possession restart policy."""
    if result.reason == PossessionTerminalReason.PERIOD_END or new_state.game_clock_seconds <= 0.0:
        return None
    next_five = _lineup_for(new_state.current_offense_team_id, new_state, home_five, away_five)
    carrier = result.engine_state.ball_carrier
    # Individual HELD control by the new offense is the authoritative live
    # fact. Generic loose-ball recovery may retain the old phase enum even
    # though control has genuinely flipped, so phase alone is not a sound
    # live/dead discriminator at this boundary.
    live_control = carrier in next_five and result.engine_state.ball_state == BallState.HELD

    if result.reason == PossessionTerminalReason.DEFENSIVE_REBOUND and live_control:
        source = PossessionChangeSource.DEFENSIVE_REBOUND
        diagnostic = build_transition_diagnostic(source, result.world, new_state.current_offense_team_id,
                                                  new_state.current_defense_team_id, carrier)
        return RestartContext(RestartType.LIVE_TRANSITION, source,
                              carrier, SpatialZone.TOP_OF_KEY, transition_diagnostic=diagnostic)
    if result.reason == PossessionTerminalReason.TURNOVER and live_control:
        source = PossessionChangeSource.LIVE_STEAL
        for event in result.events:
            if event.event_type == EventType.REACTION_CHECKPOINT \
                    and event.metadata.get("checkpoint") == "generic_loose_ball_recovered":
                source = PossessionChangeSource.LOOSE_BALL_RECOVERY
            elif event.event_type == EventType.PASS_RESOLVED \
                    and event.metadata.get("outcome") == "BAD_PASS_TO_DEFENDER":
                source = PossessionChangeSource.LIVE_BAD_PASS_INTERCEPTION
        diagnostic = build_transition_diagnostic(source, result.world, new_state.current_offense_team_id,
                                                  new_state.current_defense_team_id, carrier)
        return RestartContext(RestartType.LIVE_TRANSITION, source,
                              carrier, SpatialZone.TOP_OF_KEY, transition_diagnostic=diagnostic)
    if result.reason in (PossessionTerminalReason.MADE_FG, PossessionTerminalReason.FINAL_FT_MADE):
        source = PossessionChangeSource.MADE_BASKET_INBOUND
    else:
        source = PossessionChangeSource.DEAD_BALL_TURNOVER
    return RestartContext(RestartType.DEAD_BALL_INBOUND, source, next_five[0], SpatialZone.TOP_OF_KEY)


def simulate_possessions(home_five: Tuple[str, ...], away_five: Tuple[str, ...],
                         profiles: Dict[str, PlayerSimulationProfile],
                         initial_state: DetailedGameState,
                         max_possessions: int,
                         rng_seed: int,
                         possession_config: Optional[PossessionConfig] = None,
                         initial_restart_context: Optional[RestartContext] = None) -> MultiPossessionResult:
    """Run a deterministic bounded detailed-game segment.

    One RNG stream deterministically emits one 64-bit child seed per new
    possession. Phase 23A receives that seed and owns every intra-possession
    draw. No system randomness or per-possession reseeding is used.
    ``PossessionSimulationFault`` and other faults intentionally propagate.
    """
    if max_possessions < 0:
        raise ValueError("max_possessions cannot be negative")
    validate_detailed_game_inputs(initial_state, home_five, away_five, profiles)
    base_config = possession_config or PossessionConfig()
    rules = base_config.era_rules or get_era_rules(base_config.season)
    if initial_state.game_clock_seconds > rules.period_length_seconds:
        raise ValueError("initial game clock exceeds the configured period length")

    state = initial_state
    records = []
    restart = initial_restart_context
    rng = random.Random(rng_seed)

    while len(records) < max_possessions and state.game_clock_seconds > 0.0:
        start = initialize_next_possession(state, home_five, away_five, restart)
        config = replace(
            base_config,
            initial_game_clock_seconds=state.game_clock_seconds,
            initial_phase=start.phase,
            initial_ball_zone=start.ball_zone,
        )
        terminal = simulate_possession(
            offense_team_id=start.offense_team_id,
            defense_team_id=start.defense_team_id,
            offensive_five=start.offensive_five,
            defensive_five=start.defensive_five,
            profiles=profiles,
            inbound_receiver_id=start.inbound_receiver_id,
            config=config,
            foul_state=state.foul_state,
            rng_seed=rng.getrandbits(64),
            possession_id=start.possession_id,
        )
        previous = state
        state = apply_possession_result(previous, start, terminal)
        restart = next_restart_context(terminal, state, home_five, away_five)
        records.append(PossessionRecord(
            possession_id=start.possession_id,
            offense_team_id=start.offense_team_id,
            defense_team_id=start.defense_team_id,
            start_game_clock=previous.game_clock_seconds,
            end_game_clock=state.game_clock_seconds,
            start_score_home=previous.score_home,
            start_score_away=previous.score_away,
            end_score_home=state.score_home,
            end_score_away=state.score_away,
            restart_context=start.restart_context,
            terminal_result=terminal,
            events=terminal.events,
            provisional_deltas=terminal.stats,
        ))
        if terminal.reason == PossessionTerminalReason.PERIOD_END or state.game_clock_seconds <= 0.0:
            return MultiPossessionResult(state, tuple(records), SegmentStopReason.PERIOD_COMPLETE, None)

    stop = SegmentStopReason.PERIOD_COMPLETE if state.game_clock_seconds <= 0.0 else SegmentStopReason.MAX_POSSESSIONS
    return MultiPossessionResult(state, tuple(records), stop, restart)
