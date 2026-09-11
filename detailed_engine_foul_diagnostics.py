"""Read-only detailed-engine foul and free-throw diagnostics.

This module consumes completed ``DetailedGameResult`` objects.  It does not
simulate, mutate state, consume RNG, or supply any value to production code.
Free throws are reconciled against the existing structured shooting-foul trace
because the event schema still has no free-throw-attempt event type.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Counter as CounterType, Dict, List, Sequence, Tuple, TYPE_CHECKING

from floor_foul_administration import DEFENSIVE_FLOOR_FOUL, OFFENSIVE_CHARGE
from possession_events import EventType
from possession_orchestrator import derive_stat_deltas_from_events

if TYPE_CHECKING:
    from detailed_game import DetailedGameResult


SHOT_ACTIONS = frozenset({"PULL_UP", "CATCH_AND_SHOOT"})
COLLISION_OUTCOMES = frozenset({"NO_CALL_CONTACT", OFFENSIVE_CHARGE, DEFENSIVE_FLOOR_FOUL})


@dataclass(frozen=True)
class FreeThrowTripObservation:
    game_index: int
    possession_id: str
    source: str
    shot_family: str
    attempts: int
    makes: int
    final_attempt_missed: bool
    live_rebound_handoff: bool


@dataclass(frozen=True)
class FoulGameSummary:
    game_index: int
    home_score: int
    away_score: int
    home_pf: int
    away_pf: int
    home_fta: int
    away_fta: int
    shooting_fouls: int
    defensive_floor_fouls: int
    offensive_fouls: int
    ft_trips: int


@dataclass
class FoulDiagnostics:
    game_count: int = 0
    total_possessions: int = 0
    objective_opportunities_by_action: CounterType[str] = field(default_factory=Counter)
    dispatched_actions_by_action: CounterType[str] = field(default_factory=Counter)
    shot_dispatches_by_family: CounterType[str] = field(default_factory=Counter)
    foul_checks_by_family: CounterType[str] = field(default_factory=Counter)
    shooting_fouls_by_family: CounterType[str] = field(default_factory=Counter)
    shooting_foul_fta_by_family: CounterType[str] = field(default_factory=Counter)
    shooting_foul_ftm_by_family: CounterType[str] = field(default_factory=Counter)
    drive_collision_foul_checks: int = 0
    # "Model drive floor fouls as observable outcomes" -- keyed by drive_floor_foul_resolution.py's
    # own DriveFloorFoulOutcome values (NO_FLOOR_FOUL / OFFENSIVE_CHARGE / DEFENSIVE_FLOOR_FOUL).
    # Population is currently 100% NO_FLOOR_FOUL: both hazards are UNCALIBRATED (None/0.0) in
    # production, so this stage never produces a foul yet -- this counter exists so a future
    # calibration pass has an eligible-opportunity denominator ready before any rate is sourced.
    drive_floor_foul_checks_by_outcome: CounterType[str] = field(default_factory=Counter)
    defensive_floor_fouls: int = 0
    offensive_fouls: int = 0
    personal_fouls_from_events: int = 0
    personal_fouls_from_stat_deltas: int = 0
    fta_from_structured_trips: int = 0
    ftm_from_structured_trips: int = 0
    fta_from_stat_deltas: int = 0
    ftm_from_stat_deltas: int = 0
    free_throw_trips: Tuple[FreeThrowTripObservation, ...] = ()
    and_one_trips: int = 0
    and_one_final_ft_misses: int = 0
    and_one_missed_ft_rebound_handoffs: int = 0
    team_periods_reaching_five_qualifying_fouls: int = 0
    max_qualifying_team_fouls_in_period: int = 0
    game_summaries: Tuple[FoulGameSummary, ...] = ()

    @property
    def total_dispatched_actions(self) -> int:
        return sum(self.dispatched_actions_by_action.values())

    @property
    def total_shot_foul_checks(self) -> int:
        return sum(self.foul_checks_by_family.values())

    @property
    def total_shooting_fouls(self) -> int:
        return sum(self.shooting_fouls_by_family.values())

    @property
    def total_fouls(self) -> int:
        return self.total_shooting_fouls + self.defensive_floor_fouls + self.offensive_fouls

    @property
    def total_ft_trips(self) -> int:
        return len(self.free_throw_trips)

    @property
    def eligible_drive_floor_foul_opportunities(self) -> int:
        return sum(self.drive_floor_foul_checks_by_outcome.values())

    @property
    def drive_floor_foul_charge_outcomes(self) -> int:
        return self.drive_floor_foul_checks_by_outcome.get("OFFENSIVE_CHARGE", 0)

    @property
    def drive_floor_foul_defensive_outcomes(self) -> int:
        return self.drive_floor_foul_checks_by_outcome.get("DEFENSIVE_FLOOR_FOUL", 0)

    @property
    def drive_floor_foul_no_foul_continuations(self) -> int:
        return self.drive_floor_foul_checks_by_outcome.get("NO_FLOOR_FOUL", 0)


def _trace_rows_for_step(world, step: int) -> List[dict]:
    return [row for row in world.trace if row.get("step") == step]


def _shot_trace(rows: Sequence[dict], action_type: str):
    foul = next((row for row in rows if row.get("action") == "SHOOTING_FOUL"), None)
    if foul is not None:
        return foul
    return next(
        (row for row in rows
         if row.get("action") == action_type and row.get("shot_family") is not None),
        None,
    )


def _player_team(player_id: str, record) -> str:
    if player_id in record.terminal_result.world.team_a_five:
        return record.terminal_result.world.team_a_id
    if player_id in record.terminal_result.world.team_b_five:
        return record.terminal_result.world.team_b_id
    raise ValueError(f"foul attributed to player {player_id!r} outside the fixed lineups")


def diagnose_fouls(results: Sequence["DetailedGameResult"]) -> FoulDiagnostics:
    """Aggregate the complete foul-check/PF/FT funnel without re-simulation."""
    diagnosis = FoulDiagnostics(game_count=len(results))
    trips: List[FreeThrowTripObservation] = []
    game_summaries: List[FoulGameSummary] = []

    for game_index, result in enumerate(results):
        diagnosis.total_possessions += result.total_possessions
        team_pf: CounterType[str] = Counter()
        team_fta: CounterType[str] = Counter()
        game_shooting_fouls = 0
        game_defensive_floor_fouls = 0
        game_offensive_fouls = 0
        game_trip_start = len(trips)
        qualifying_by_period_team: CounterType[Tuple[int, str]] = Counter()
        period_by_sequence: Dict[int, int] = {}
        for period in result.periods:
            for sequence in range(period.first_possession_sequence, period.last_possession_sequence + 1):
                period_by_sequence[sequence] = period.period

        for sequence, record in enumerate(result.possessions, start=1):
            world = record.terminal_result.world
            deltas = record.provisional_deltas
            for decision in world.decision_log:
                for opportunity in decision.get("objective_opportunities", ()):
                    diagnosis.objective_opportunities_by_action[opportunity["action_type"]] += 1

            for action in world.action_log:
                action_type = action["action_type"]
                diagnosis.dispatched_actions_by_action[action_type] += 1
                rows = _trace_rows_for_step(world, action["step"])
                if action_type in SHOT_ACTIONS:
                    shot = _shot_trace(rows, action_type)
                    family = shot.get("shot_family", "UNKNOWN") if shot is not None else "UNKNOWN"
                    diagnosis.shot_dispatches_by_family[family] += 1
                    diagnosis.foul_checks_by_family[family] += 1
                elif action_type == "DRIVE":
                    pressure = next((row for row in rows if row.get("action") == "ON_BALL_PRESSURE"), None)
                    if pressure is not None and pressure.get("outcome") in COLLISION_OUTCOMES:
                        diagnosis.drive_collision_foul_checks += 1
                    # "Model drive floor fouls as observable outcomes" -- every ELIGIBLE drive (a
                    # defender was assigned) logs exactly one DRIVE_FLOOR_FOUL_CHECK trace row,
                    # regardless of outcome, at the same step as this action_log entry.
                    floor_foul_check = next((row for row in rows if row.get("action") == "DRIVE_FLOOR_FOUL_CHECK"), None)
                    if floor_foul_check is not None:
                        diagnosis.drive_floor_foul_checks_by_outcome[floor_foul_check.get("outcome", "UNKNOWN")] += 1

            derived = derive_stat_deltas_from_events(record.events)
            event_pf = sum(derived.personal_fouls.values())
            stat_pf = sum(deltas.personal_fouls.values())
            diagnosis.personal_fouls_from_events += event_pf
            diagnosis.personal_fouls_from_stat_deltas += stat_pf
            diagnosis.fta_from_stat_deltas += deltas.fta
            diagnosis.ftm_from_stat_deltas += deltas.ftm
            team_fta[record.offense_team_id] += deltas.fta
            for player_id, count in deltas.personal_fouls.items():
                team_pf[_player_team(player_id, record)] += count

            period = period_by_sequence.get(sequence, 0)
            for event in record.events:
                if event.event_type == EventType.SHOOTING_FOUL and event.primary_player_id is not None:
                    qualifying_by_period_team[(period, _player_team(event.primary_player_id, record))] += 1
                elif (event.event_type == EventType.REACTION_CHECKPOINT
                      and event.metadata.get("checkpoint") == "floor_foul_administered"):
                    foul_class = event.metadata.get("foul_class")
                    if foul_class == DEFENSIVE_FLOOR_FOUL and event.primary_player_id is not None:
                        qualifying_by_period_team[(period, _player_team(event.primary_player_id, record))] += 1

            for row in world.trace:
                if row.get("action") == "SHOOTING_FOUL":
                    family = row.get("shot_family", "UNKNOWN")
                    attempts = int(row.get("awarded_fts", 0))
                    makes = int(row.get("ft_makes", 0))
                    source = "AND_ONE" if row.get("made") else "MISSED_SHOOTING_FOUL"
                    live_rebound = any(
                        rebound.get("source") == "FINAL_MISSED_FT"
                        for rebound in world.rebound_opportunity_log
                    )
                    trip = FreeThrowTripObservation(
                        game_index=game_index,
                        possession_id=record.possession_id,
                        source=source,
                        shot_family=family,
                        attempts=attempts,
                        makes=makes,
                        final_attempt_missed=makes < attempts if attempts == 1 else False,
                        live_rebound_handoff=live_rebound,
                    )
                    trips.append(trip)
                    diagnosis.shooting_fouls_by_family[family] += 1
                    diagnosis.shooting_foul_fta_by_family[family] += attempts
                    diagnosis.shooting_foul_ftm_by_family[family] += makes
                    diagnosis.fta_from_structured_trips += attempts
                    diagnosis.ftm_from_structured_trips += makes
                    diagnosis.and_one_trips += source == "AND_ONE"
                    if source == "AND_ONE" and makes == 0:
                        diagnosis.and_one_final_ft_misses += 1
                        diagnosis.and_one_missed_ft_rebound_handoffs += live_rebound
                    game_shooting_fouls += 1
                elif (row.get("action") == "ON_BALL_PRESSURE"
                      and row.get("outcome") == DEFENSIVE_FLOOR_FOUL):
                    diagnosis.defensive_floor_fouls += 1
                    game_defensive_floor_fouls += 1
                elif (row.get("action") == "ON_BALL_PRESSURE"
                      and row.get("outcome") == OFFENSIVE_CHARGE):
                    diagnosis.offensive_fouls += 1
                    game_offensive_fouls += 1

        period_team_counts = tuple(qualifying_by_period_team.values())
        diagnosis.team_periods_reaching_five_qualifying_fouls += sum(
            count >= 5 for count in period_team_counts
        )
        diagnosis.max_qualifying_team_fouls_in_period = max(
            diagnosis.max_qualifying_team_fouls_in_period,
            max(period_team_counts, default=0),
        )
        game_summaries.append(FoulGameSummary(
            game_index=game_index,
            home_score=result.final_home_score,
            away_score=result.final_away_score,
            home_pf=team_pf[result.home_team_id],
            away_pf=team_pf[result.away_team_id],
            home_fta=team_fta[result.home_team_id],
            away_fta=team_fta[result.away_team_id],
            shooting_fouls=game_shooting_fouls,
            defensive_floor_fouls=game_defensive_floor_fouls,
            offensive_fouls=game_offensive_fouls,
            ft_trips=len(trips) - game_trip_start,
        ))

    diagnosis.free_throw_trips = tuple(trips)
    diagnosis.game_summaries = tuple(game_summaries)
    return diagnosis


def assert_foul_reconciliation(diagnosis: FoulDiagnostics) -> None:
    """Raise when existing event/trace/stat projections disagree."""
    if diagnosis.personal_fouls_from_events != diagnosis.personal_fouls_from_stat_deltas:
        raise AssertionError("event-derived personal fouls do not match StatDeltas")
    if diagnosis.total_fouls != diagnosis.personal_fouls_from_stat_deltas:
        raise AssertionError("classified foul sources do not match personal fouls")
    if diagnosis.fta_from_structured_trips != diagnosis.fta_from_stat_deltas:
        raise AssertionError("structured free-throw trips do not match StatDeltas FTA")
    if diagnosis.ftm_from_structured_trips != diagnosis.ftm_from_stat_deltas:
        raise AssertionError("structured free-throw trips do not match StatDeltas FTM")
