"""Phase 23C -- minimal complete detailed-game orchestration.

This module owns game initialization, period lifecycle, overtime, and typed
game termination. It delegates every possession and all within-period state
updates to Phase 23B. It is an isolated detailed-engine API and does not import
or replace the legacy aggregate game engine.
"""
import random
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Tuple

from detailed_game_orchestrator import (
    DetailedGameState,
    MultiPossessionResult,
    PossessionRecord,
    RestartContext,
    RestartType,
    SegmentStopReason,
    simulate_possessions,
    validate_detailed_game_inputs,
)
from floor_foul_administration import FoulAdministrationState
from possession_events import Event
from possession_orchestrator import PlayerSimulationProfile, PossessionConfig
from possession_rules import get_era_rules
from possession_state import SpatialZone
from transition_state import PossessionChangeSource


class DetailedGameTerminationReason:
    REGULATION_FINAL = "REGULATION_FINAL"
    OVERTIME_FINAL = "OVERTIME_FINAL"


class DetailedGameFaultCode:
    MAX_POSSESSIONS_PER_PERIOD = "MAX_POSSESSIONS_PER_PERIOD"
    MAX_POSSESSIONS_PER_GAME = "MAX_POSSESSIONS_PER_GAME"
    MAX_OVERTIMES = "MAX_OVERTIMES"
    INVALID_PERIOD_RESULT = "INVALID_PERIOD_RESULT"


class DetailedGameSimulationFault(RuntimeError):
    """Structured orchestration failure; never a fabricated basketball result."""

    def __init__(self, code: str, message: str, state: DetailedGameState,
                 possessions: Tuple[PossessionRecord, ...]):
        super().__init__(message)
        self.code = code
        self.state = state
        self.possessions = possessions


@dataclass(frozen=True)
class DetailedGameConfig:
    """Explicit V0 game wrapper configuration, not calibration knobs."""
    regulation_periods: int = 4
    regulation_period_seconds: float = 720.0
    overtime_period_seconds: float = 300.0
    max_overtimes: int = 5
    max_possessions_per_period: int = 500
    max_possessions_per_game: int = 3000
    opening_offense_team_id: Optional[str] = None  # None -> home, deterministically
    possession_config: PossessionConfig = field(default_factory=PossessionConfig)


@dataclass(frozen=True)
class PeriodRecord:
    period: int
    is_overtime: bool
    opening_offense_team_id: str
    start_clock_seconds: float
    end_clock_seconds: float
    start_score_home: int
    start_score_away: int
    end_score_home: int
    end_score_away: int
    first_possession_sequence: int
    last_possession_sequence: int
    possession_count: int

    @property
    def home_points(self) -> int:
        return self.end_score_home - self.start_score_home

    @property
    def away_points(self) -> int:
        return self.end_score_away - self.start_score_away


@dataclass(frozen=True)
class ProvisionalDetailedGameSummary:
    """Convenience aggregation; scoring/shot/FT fields are not authoritative."""
    points: int = 0
    fga: int = 0
    fgm: int = 0
    fg3a: int = 0
    fg3m: int = 0
    fta: int = 0
    ftm: int = 0
    oreb: int = 0
    dreb: int = 0
    turnovers: int = 0  # backward-compatible player-charged total
    team_turnovers: int = 0
    player_turnovers: int = 0
    steals: int = 0
    blocks: int = 0
    personal_fouls: int = 0


@dataclass(frozen=True)
class DetailedGameResult:
    """Typed detailed-engine result, intentionally distinct from legacy GameResult."""
    final_state: DetailedGameState
    regulation_periods: int
    overtime_periods: int
    possessions: Tuple[PossessionRecord, ...]
    periods: Tuple[PeriodRecord, ...]
    termination_reason: str
    seed: int
    provisional_summary: ProvisionalDetailedGameSummary

    @property
    def home_team_id(self) -> str:
        return self.final_state.home_team_id

    @property
    def away_team_id(self) -> str:
        return self.final_state.away_team_id

    @property
    def final_home_score(self) -> int:
        return self.final_state.score_home

    @property
    def final_away_score(self) -> int:
        return self.final_state.score_away

    @property
    def total_possessions(self) -> int:
        return len(self.possessions)

    @property
    def events(self) -> Tuple[Event, ...]:
        """Derived view over record-owned event streams, never a second log."""
        return tuple(event for record in self.possessions for event in record.events)


def _validate_config(config: DetailedGameConfig) -> None:
    if config.regulation_periods != 4:
        raise ValueError("Phase 23C supports exactly four regulation periods")
    if config.regulation_period_seconds <= 0.0 or config.overtime_period_seconds <= 0.0:
        raise ValueError("regulation and overtime clocks must be positive")
    if config.max_overtimes < 0:
        raise ValueError("max_overtimes cannot be negative")
    if config.max_possessions_per_period <= 0 or config.max_possessions_per_game <= 0:
        raise ValueError("possession safety limits must be positive")
    rules = config.possession_config.era_rules or get_era_rules(config.possession_config.season)
    if config.regulation_period_seconds > rules.period_length_seconds:
        raise ValueError("configured regulation clock exceeds the selected era's period length")


def _other_team(state: DetailedGameState, team_id: str) -> str:
    if team_id == state.home_team_id:
        return state.away_team_id
    if team_id == state.away_team_id:
        return state.home_team_id
    raise ValueError(f"opening offense {team_id!r} is not a configured team")


def period_opening_offense(home_team_id: str, away_team_id: str,
                           opening_offense_team_id: str, period: int) -> str:
    """Deterministic V0 policy: Q1 opener starts odd periods, opponent even.

    Overtimes continue the same absolute-period alternation. This is a fixed
    orchestration policy, not a simulated jump ball or possession arrow, and
    deliberately ignores the previous period's terminal possession.
    """
    if opening_offense_team_id not in (home_team_id, away_team_id):
        raise ValueError("opening offense must be home or away")
    other = away_team_id if opening_offense_team_id == home_team_id else home_team_id
    return opening_offense_team_id if period % 2 == 1 else other


def initialize_detailed_game(home_team_id: str, away_team_id: str,
                             config: Optional[DetailedGameConfig] = None) -> DetailedGameState:
    """Authoritative regulation-game initialization path."""
    config = config or DetailedGameConfig()
    _validate_config(config)
    opening = config.opening_offense_team_id or home_team_id
    if home_team_id == away_team_id:
        raise ValueError("home and away teams must differ")
    defense = away_team_id if opening == home_team_id else home_team_id
    if opening not in (home_team_id, away_team_id):
        raise ValueError("opening_offense_team_id must be home or away")
    return DetailedGameState(
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        current_offense_team_id=opening,
        current_defense_team_id=defense,
        period=1,
        game_clock_seconds=config.regulation_period_seconds,
    )


def advance_to_next_period(state: DetailedGameState, config: DetailedGameConfig) -> DetailedGameState:
    """The one period-boundary transition: clock/opener/team-foul reset."""
    if state.game_clock_seconds != 0.0:
        raise ValueError("cannot advance period before the current period clock expires")
    next_period = state.period + 1
    opening_q1 = config.opening_offense_team_id or state.home_team_id
    next_offense = period_opening_offense(
        state.home_team_id, state.away_team_id, opening_q1, next_period)
    next_clock = config.regulation_period_seconds if next_period <= config.regulation_periods \
        else config.overtime_period_seconds
    return replace(
        state,
        period=next_period,
        game_clock_seconds=next_clock,
        current_offense_team_id=next_offense,
        current_defense_team_id=_other_team(state, next_offense),
        foul_state=state.foul_state.reset_team_fouls(),
        last_terminal_reason=None,
    )


def _provisional_summary(records: Tuple[PossessionRecord, ...]) -> ProvisionalDetailedGameSummary:
    values = {name: 0 for name in ProvisionalDetailedGameSummary.__dataclass_fields__}
    for record in records:
        deltas = record.provisional_deltas
        for name in ("points", "fga", "fgm", "fg3a", "fg3m", "fta", "ftm",
                     "oreb", "dreb", "turnovers", "team_turnovers", "steals", "blocks"):
            values[name] += getattr(deltas, name)
        values["player_turnovers"] += sum(deltas.player_turnovers.values())
        values["personal_fouls"] += sum(deltas.personal_fouls.values())
    return ProvisionalDetailedGameSummary(**values)


def _period_record(start: DetailedGameState, end: DetailedGameState,
                   possession_count: int, is_overtime: bool) -> PeriodRecord:
    first = start.next_possession_sequence
    last = end.next_possession_sequence - 1
    return PeriodRecord(
        period=start.period,
        is_overtime=is_overtime,
        opening_offense_team_id=start.current_offense_team_id,
        start_clock_seconds=start.game_clock_seconds,
        end_clock_seconds=end.game_clock_seconds,
        start_score_home=start.score_home,
        start_score_away=start.score_away,
        end_score_home=end.score_home,
        end_score_away=end.score_away,
        first_possession_sequence=first,
        last_possession_sequence=last,
        possession_count=possession_count,
    )


def simulate_detailed_game(home_team_id: str, away_team_id: str,
                           home_five: Tuple[str, ...], away_five: Tuple[str, ...],
                           profiles: Dict[str, PlayerSimulationProfile],
                           rng_seed: int,
                           config: Optional[DetailedGameConfig] = None) -> DetailedGameResult:
    """Autonomously run four regulation periods and necessary overtimes."""
    config = config or DetailedGameConfig()
    state = initialize_detailed_game(home_team_id, away_team_id, config)
    validate_detailed_game_inputs(state, home_five, away_five, profiles)

    top_rng = random.Random(rng_seed)
    all_possessions = []
    period_records = []
    overtime_periods = 0

    while True:
        if len(all_possessions) >= config.max_possessions_per_game:
            raise DetailedGameSimulationFault(
                DetailedGameFaultCode.MAX_POSSESSIONS_PER_GAME,
                "game reached max_possessions_per_game before a valid final result",
                state, tuple(all_possessions))

        period_start = state
        remaining_game_slots = config.max_possessions_per_game - len(all_possessions)
        period_limit = min(config.max_possessions_per_period, remaining_game_slots)
        restart = RestartContext(
            restart_type=RestartType.DEAD_BALL_INBOUND,
            source=PossessionChangeSource.PERIOD_START,
            ball_carrier_id=None,
            ball_zone=SpatialZone.TOP_OF_KEY,
        )
        segment: MultiPossessionResult = simulate_possessions(
            home_five=home_five,
            away_five=away_five,
            profiles=profiles,
            initial_state=state,
            max_possessions=period_limit,
            rng_seed=top_rng.getrandbits(64),
            possession_config=replace(
                config.possession_config,
                is_overtime=state.period > config.regulation_periods,
            ),
            initial_restart_context=restart,
        )
        all_possessions.extend(segment.possessions)
        state = segment.final_state

        if segment.stop_reason != SegmentStopReason.PERIOD_COMPLETE or state.game_clock_seconds != 0.0:
            code = DetailedGameFaultCode.MAX_POSSESSIONS_PER_GAME \
                if len(all_possessions) >= config.max_possessions_per_game \
                else DetailedGameFaultCode.MAX_POSSESSIONS_PER_PERIOD
            raise DetailedGameSimulationFault(
                code, "possession guard reached before the period clock expired",
                state, tuple(all_possessions))
        if state.period != period_start.period:
            raise DetailedGameSimulationFault(
                DetailedGameFaultCode.INVALID_PERIOD_RESULT,
                "Phase 23B segment changed period ownership",
                state, tuple(all_possessions))
        if state.score_home < period_start.score_home or state.score_away < period_start.score_away:
            raise DetailedGameSimulationFault(
                DetailedGameFaultCode.INVALID_PERIOD_RESULT,
                "score decreased within a period",
                state, tuple(all_possessions))

        is_overtime = state.period > config.regulation_periods
        if is_overtime:
            overtime_periods = state.period - config.regulation_periods
        period_records.append(_period_record(period_start, state, len(segment.possessions), is_overtime))

        if state.period < config.regulation_periods:
            state = advance_to_next_period(state, config)
            continue

        tied = state.score_home == state.score_away
        if not tied:
            termination = DetailedGameTerminationReason.OVERTIME_FINAL if is_overtime \
                else DetailedGameTerminationReason.REGULATION_FINAL
            possessions = tuple(all_possessions)
            return DetailedGameResult(
                final_state=state,
                regulation_periods=config.regulation_periods,
                overtime_periods=overtime_periods,
                possessions=possessions,
                periods=tuple(period_records),
                termination_reason=termination,
                seed=rng_seed,
                provisional_summary=_provisional_summary(possessions),
            )
        if overtime_periods >= config.max_overtimes:
            raise DetailedGameSimulationFault(
                DetailedGameFaultCode.MAX_OVERTIMES,
                "game remains tied after max_overtimes; no fake tie-break score was created",
                state, tuple(all_possessions))
        state = advance_to_next_period(state, config)
