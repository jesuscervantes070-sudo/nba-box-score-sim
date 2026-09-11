"""Purely observational turnover root-cause telemetry.

This module consumes completed ``DetailedGameResult`` objects.  It never
imports randomness, mutates simulation state, or participates in selection,
resolution, clocks, accounting, or possession flow.  Raw resolver outcomes
remain available beside the normalized diagnostic categories below.

``SHOT_CLOCK_VIOLATION`` is intentionally distinguished from an
``engine_accounted_turnover``.  The current engine ends the possession but
does not increment ``StatDeltas.turnovers`` for that terminal.  Reports can
therefore show both the benchmark's existing turnover numerator and the
broader set of turnover-like possession losses without silently changing the
meaning of either.
"""
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from detailed_engine_benchmark import BenchmarkGame
from detailed_game import DetailedGameResult
from detailed_game_orchestrator import PossessionRecord, RestartType
from possession_events import EventType


class TurnoverCategory:
    PASS_CLEAN_INTERCEPTION = "PASS_CLEAN_INTERCEPTION"
    PASS_BAD_PASS = "PASS_BAD_PASS"
    PASS_LOOSE_BALL_LOST = "PASS_LOOSE_BALL_LOST"
    HANDLE_STRIP_LOST = "HANDLE_STRIP_LOST"
    OFFENSIVE_FOUL = "OFFENSIVE_FOUL"
    SHOT_CLOCK_VIOLATION = "SHOT_CLOCK_VIOLATION"
    OTHER_TURNOVER = "OTHER_TURNOVER"


PASS_ACTIONS = frozenset({"SWING_PASS", "KICKOUT", "RESET_PASS", "POCKET_PASS", "OUTLET_PASS"})
PASS_COMPLETIONS = frozenset({"COMPLETED_CLEAN", "COMPLETED_ADJUSTED"})
PASS_BAD_OUTCOMES = frozenset({"BAD_PASS_OUT_OF_BOUNDS", "BAD_PASS_TO_DEFENDER"})


@dataclass(frozen=True)
class TurnoverObservation:
    possession_id: str
    category: str
    raw_terminal: str
    raw_outcome: Optional[str]
    originating_action: Optional[str]
    cause_step: Optional[int]
    context: str
    action_stage: str
    live_ball: bool
    steal_credited: int
    engine_accounted_turnover: int
    player_attribution_id: Optional[str]
    shot_clock_source: Optional[str] = None
    shot_clock_remaining: Optional[float] = None
    prior_action_shot_clock_after: Optional[float] = None
    action_count_before_terminal: int = 0


@dataclass
class CategoryStats:
    category: str
    count: int = 0
    steals: int = 0
    engine_accounted_turnovers: int = 0
    live_ball: int = 0
    dead_ball: int = 0


@dataclass
class PassFamilyStats:
    family: str
    attempts: int = 0
    completed: int = 0
    turnovers: int = 0
    clean_interceptions: int = 0
    other_bad_pass_outcomes: int = 0
    deflected_loose_balls: int = 0
    steals: int = 0

    @property
    def turnover_rate(self) -> float:
        return self.turnovers / self.attempts if self.attempts else 0.0


@dataclass
class HandlingStats:
    opportunities: int = 0
    meaningful_disruptions: int = 0
    strip_attempts_observable: Optional[int] = None
    loose_balls_created: int = 0
    defense_recoveries: int = 0
    offense_recoveries: int = 0
    turnovers: int = 0
    steals: int = 0

    @property
    def turnover_rate(self) -> float:
        return self.turnovers / self.opportunities if self.opportunities else 0.0


@dataclass
class ContextStats:
    context: str
    possessions: int = 0
    engine_accounted_turnovers: int = 0
    possession_losses_including_shot_clock: int = 0


@dataclass
class TurnoverDiagnosis:
    games: int
    team_games: int
    true_possessions: int
    engine_accounted_turnovers: int
    possession_losses_including_shot_clock: int
    observations: Tuple[TurnoverObservation, ...]
    categories: Dict[str, CategoryStats]
    pass_families: Dict[str, PassFamilyStats]
    handling: HandlingStats
    contexts: Dict[str, ContextStats]
    action_stages: Dict[str, int]

    @property
    def turnovers_per_team_game(self) -> float:
        return self.engine_accounted_turnovers / self.team_games if self.team_games else 0.0

    @property
    def turnovers_per_true_possession(self) -> float:
        return self.engine_accounted_turnovers / self.true_possessions if self.true_possessions else 0.0


def _trace(record: PossessionRecord, action: str) -> List[dict]:
    return [entry for entry in record.terminal_result.world.trace if entry.get("action") == action]


def _last_pass(record: PossessionRecord) -> Optional[dict]:
    entries = [entry for entry in record.terminal_result.world.trace if entry.get("action") in PASS_ACTIONS]
    return entries[-1] if entries else None


def _loose_ball_origin(record: PossessionRecord) -> Tuple[Optional[str], Optional[dict]]:
    """Return the last structured action capable of creating the terminal loose ball."""
    candidates = []
    for entry in record.terminal_result.world.trace:
        outcome = entry.get("outcome")
        if entry.get("action") in PASS_ACTIONS and outcome in (
                "DEFLECTED_LOOSE_BALL", "DEFLECTED_RETAINED_OFFENSE", "BAD_PASS_TO_DEFENDER"):
            candidates.append(("PASS", entry))
        elif entry.get("action") == "ON_BALL_PRESSURE" and outcome == "CLEAN_STRIP_LOOSE":
            candidates.append(("HANDLE", entry))
    return candidates[-1] if candidates else (None, None)


def _context(record: PossessionRecord, cause_step: Optional[int]) -> str:
    if cause_step is not None:
        for entry in record.terminal_result.world.trace:
            if entry.get("action") == "REBOUND_OPPORTUNITY" and entry.get("step", -1) < cause_step \
                    and entry.get("outcome") in ("SECURED_OFFENSE", "TEAM_REBOUND_OFFENSE"):
                return "SECOND_CHANCE_CONTINUATION"
    return "TRANSITION_ENTRY" if record.restart_context.restart_type == RestartType.LIVE_TRANSITION else "ORDINARY_ENTRY"


def _action_stage(record: PossessionRecord, cause_step: Optional[int]) -> str:
    steps = [entry.get("step") for entry in record.terminal_result.world.action_log]
    steps = [step for step in steps if isinstance(step, int)]
    if cause_step is None or not steps:
        return "BEFORE_FIRST_ACTION"
    return "FIRST_MODELED_ACTION" if cause_step == min(steps) else "LATER_ACTION"


def classify_turnover(record: PossessionRecord) -> Optional[TurnoverObservation]:
    reason = record.terminal_result.reason
    deltas = record.provisional_deltas
    if reason not in ("TURNOVER", "OFFENSIVE_FOUL_TURNOVER", "SHOT_CLOCK_VIOLATION"):
        return None

    category = TurnoverCategory.OTHER_TURNOVER
    raw_outcome = None
    action = None
    cause_step = None
    live = False
    player_id = None
    shot_source = None
    shot_remaining = None

    if reason == "SHOT_CLOCK_VIOLATION":
        category = TurnoverCategory.SHOT_CLOCK_VIOLATION
        diagnostic = _trace(record, "SHOT_CLOCK_VIOLATION_DIAGNOSTIC")
        if diagnostic:
            cause = diagnostic[-1]
            cause_step = cause.get("step")
            shot_source = cause.get("source")
            shot_remaining = cause.get("shot_clock_remaining")
        raw_outcome = shot_source
    elif reason == "OFFENSIVE_FOUL_TURNOVER":
        category = TurnoverCategory.OFFENSIVE_FOUL
        pressure = _trace(record, "ON_BALL_PRESSURE")
        if pressure:
            cause = pressure[-1]
            raw_outcome = cause.get("outcome")
            action = "DRIVE"
            cause_step = cause.get("step")
            player_id = cause.get("driver")
    else:
        recovery = [event for event in record.events
                    if event.event_type == EventType.REACTION_CHECKPOINT
                    and event.metadata.get("checkpoint") == "generic_loose_ball_recovered"
                    and event.metadata.get("recovery") == "DEFENSE_RECOVERED"]
        if recovery:
            origin, cause = _loose_ball_origin(record)
            if cause is not None:
                raw_outcome = cause.get("outcome")
                action = cause.get("action")
                cause_step = cause.get("step")
                category = (TurnoverCategory.HANDLE_STRIP_LOST if origin == "HANDLE"
                            else TurnoverCategory.PASS_LOOSE_BALL_LOST)
            live = record.terminal_result.engine_state.ball_carrier is not None
        else:
            passed = _last_pass(record)
            if passed is not None:
                raw_outcome = passed.get("outcome")
                action = passed.get("action")
                cause_step = passed.get("step")
                if raw_outcome == "CLEAN_INTERCEPTION":
                    category = TurnoverCategory.PASS_CLEAN_INTERCEPTION
                    live = True
                elif raw_outcome in PASS_BAD_OUTCOMES:
                    category = TurnoverCategory.PASS_BAD_PASS
                    live = record.terminal_result.engine_state.ball_carrier is not None
            pass_events = [event for event in record.events if event.event_type == EventType.PASS_RESOLVED]
            if pass_events:
                player_id = pass_events[-1].primary_player_id

    steal = deltas.steals
    accounted = deltas.turnovers
    action_log = record.terminal_result.world.action_log
    prior_action_shot_clock_after = action_log[-1].get("shot_clock_after") if action_log else None
    return TurnoverObservation(
        possession_id=record.possession_id, category=category, raw_terminal=reason,
        raw_outcome=raw_outcome, originating_action=action, cause_step=cause_step,
        context=_context(record, cause_step), action_stage=_action_stage(record, cause_step),
        live_ball=live, steal_credited=steal, engine_accounted_turnover=accounted,
        player_attribution_id=player_id, shot_clock_source=shot_source,
        shot_clock_remaining=shot_remaining,
        prior_action_shot_clock_after=prior_action_shot_clock_after,
        action_count_before_terminal=len(action_log),
    )


def _iter_results(items: Sequence[object]) -> Iterable[DetailedGameResult]:
    for item in items:
        yield item.result if isinstance(item, BenchmarkGame) else item


def diagnose_turnovers(items: Sequence[object]) -> TurnoverDiagnosis:
    results = tuple(_iter_results(items))
    observations: List[TurnoverObservation] = []
    categories: Dict[str, CategoryStats] = {}
    pass_families: Dict[str, PassFamilyStats] = {family: PassFamilyStats(family) for family in sorted(PASS_ACTIONS)}
    handling = HandlingStats()
    contexts: Dict[str, ContextStats] = {}
    action_stages: Dict[str, int] = {}
    true_possessions = 0

    for result in results:
        for record in result.possessions:
            true_possessions += 1
            base_context = _context(record, None)
            contexts.setdefault(base_context, ContextStats(base_context)).possessions += 1
            if any(entry.get("action") == "REBOUND_OPPORTUNITY"
                   and entry.get("outcome") in ("SECURED_OFFENSE", "TEAM_REBOUND_OFFENSE")
                   for entry in record.terminal_result.world.trace):
                contexts.setdefault("SECOND_CHANCE_CONTINUATION",
                                    ContextStats("SECOND_CHANCE_CONTINUATION")).possessions += 1

            obs = classify_turnover(record)
            if obs is not None:
                observations.append(obs)
                bucket = categories.setdefault(obs.category, CategoryStats(obs.category))
                bucket.count += 1
                bucket.steals += obs.steal_credited
                bucket.engine_accounted_turnovers += obs.engine_accounted_turnover
                if obs.live_ball:
                    bucket.live_ball += 1
                else:
                    bucket.dead_ball += 1
                ctx = contexts.setdefault(obs.context, ContextStats(obs.context))
                ctx.engine_accounted_turnovers += obs.engine_accounted_turnover
                ctx.possession_losses_including_shot_clock += 1
                action_stages[obs.action_stage] = action_stages.get(obs.action_stage, 0) + 1

            for entry in record.terminal_result.world.trace:
                action = entry.get("action")
                outcome = entry.get("outcome")
                if action in PASS_ACTIONS:
                    p = pass_families[action]
                    p.attempts += 1
                    if outcome in PASS_COMPLETIONS:
                        p.completed += 1
                    if outcome == "CLEAN_INTERCEPTION":
                        p.clean_interceptions += 1
                    if outcome in PASS_BAD_OUTCOMES:
                        p.other_bad_pass_outcomes += 1
                    if outcome in ("DEFLECTED_LOOSE_BALL", "DEFLECTED_RETAINED_OFFENSE"):
                        p.deflected_loose_balls += 1
                elif action == "ON_BALL_PRESSURE":
                    handling.opportunities += 1
                    if outcome != "CLEAN_CONTROL":
                        handling.meaningful_disruptions += 1
                    if outcome == "CLEAN_STRIP_LOOSE":
                        handling.loose_balls_created += 1

            if obs is not None and obs.originating_action in PASS_ACTIONS:
                p = pass_families[obs.originating_action]
                p.turnovers += obs.engine_accounted_turnover
                p.steals += obs.steal_credited
            if obs is not None and obs.category == TurnoverCategory.HANDLE_STRIP_LOST:
                handling.defense_recoveries += 1
                handling.turnovers += obs.engine_accounted_turnover
                handling.steals += obs.steal_credited

    accounted = sum(obs.engine_accounted_turnover for obs in observations)
    # Every CLEAN_STRIP_LOOSE is synchronously resolved on the next loose-ball
    # iteration in the current engine.  Subtracting terminal defensive wins is
    # safer than associating unrelated pass/rebound loose-ball trace entries.
    handling.offense_recoveries = handling.loose_balls_created - handling.defense_recoveries
    return TurnoverDiagnosis(
        games=len(results), team_games=2 * len(results), true_possessions=true_possessions,
        engine_accounted_turnovers=accounted,
        possession_losses_including_shot_clock=len(observations), observations=tuple(observations),
        categories=categories, pass_families=pass_families, handling=handling,
        contexts=contexts, action_stages=action_stages,
    )


def assert_turnover_reconciliation(diagnosis: TurnoverDiagnosis) -> None:
    """Fail loudly if observational classification detects duplicate accounting."""
    for obs in diagnosis.observations:
        expected = 0 if obs.category == TurnoverCategory.SHOT_CLOCK_VIOLATION else 1
        if obs.engine_accounted_turnover != expected:
            raise AssertionError(
                f"{obs.possession_id}: {obs.category} has {obs.engine_accounted_turnover} "
                f"engine-accounted turnovers; expected {expected}"
            )
        if obs.steal_credited not in (0, 1):
            raise AssertionError(f"{obs.possession_id}: duplicate steal accounting ({obs.steal_credited})")
        if obs.steal_credited and obs.category != TurnoverCategory.PASS_CLEAN_INTERCEPTION:
            raise AssertionError(f"{obs.possession_id}: steal credited to unsupported category {obs.category}")
