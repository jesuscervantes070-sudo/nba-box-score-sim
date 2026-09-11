"""Read-only shot-clock violation telemetry and causal decomposition.

The simulation records already-computed clock mutations and decision menus;
this module only consumes completed game results. It imports no randomness and
does not participate in selection, resolution, clocks, or accounting.
"""
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

from detailed_engine_benchmark import BenchmarkGame
from detailed_game import DetailedGameResult
from detailed_game_orchestrator import PossessionRecord, RestartType
from possession_orchestrator import PossessionStage, PossessionTerminalReason


class ViolationCauseClass:
    LEGITIMATE_LIVE_TIMING = "A_LEGITIMATE_LIVE_ACTION_OR_TIMING"
    NO_FEASIBLE_ACTION = "B_NO_ACTION_WAS_FEASIBLE"
    LATE_CLOCK_SELECTION = "C_LATE_CLOCK_ACTION_NOT_ATTEMPTED"
    FIXED_TIMING_OVERSHOOT = "D_FIXED_TIMING_CHARGE_OVERSHOT_CLOCK"
    OTHER = "E_OTHER"


@dataclass(frozen=True)
class ShotClockViolationObservation:
    possession_id: str
    offense_team_id: str
    possession_origin: str
    initial_entry_origin: str
    action_index: int
    shot_clock_at_start_of_previous_decision: Optional[float]
    shot_clock_after_previous_action: Optional[float]
    shot_clock_immediately_before_final_timing_charge: Optional[float]
    timing_category_that_crossed_zero: Optional[str]
    pass_family: Optional[str]
    configured_final_timing_seconds: Optional[float]
    elapsed_final_timing_seconds: Optional[float]
    previous_action_type: Optional[str]
    previous_action_outcome: Optional[str]
    passes: int
    drives: int
    total_modeled_actions: int
    total_entry_time: float
    total_inter_action_time: float
    total_pass_time: float
    total_drive_time: float
    total_shot_time: float
    shot_attempt_occurred_previously: bool
    followed_oreb: bool
    terminal_source: str
    action_was_selected_before_expiration: bool
    feasible_action_existed_at_previous_decision: bool
    feasible_shot_existed_at_previous_decision: bool
    second_chance_reset_clock_before: Optional[float]
    second_chance_reset_clock_after: Optional[float]
    actions_after_last_oreb: int
    cause_classification: str


@dataclass(frozen=True)
class LateClockDecisionStats:
    threshold_seconds: float
    decisions: int
    pass_selected: int
    reset_pass_selected: int
    shot_selected: int
    pass_available: int
    shot_available: int


@dataclass(frozen=True)
class ShotClockDiagnosis:
    games: int
    team_games: int
    true_possessions: int
    team_turnovers: int
    observations: Tuple[ShotClockViolationObservation, ...]
    expiration_stages: Dict[str, int]
    terminal_sources: Dict[str, int]
    entry_origins: Dict[str, int]
    action_indices: Dict[int, int]
    prior_actions: Dict[str, int]
    pass_counts: Dict[int, int]
    drive_counts: Dict[int, int]
    cause_classes: Dict[str, int]
    second_chance_continuations: int
    second_chance_violations: int
    transition_origin_possessions: int
    transition_origin_violations: int
    late_clock_decisions: Dict[str, LateClockDecisionStats]

    @property
    def total_violations(self) -> int:
        return len(self.observations)

    @property
    def violations_per_game(self) -> float:
        return self.total_violations / self.games if self.games else 0.0

    @property
    def violations_per_team_game(self) -> float:
        return self.total_violations / self.team_games if self.team_games else 0.0

    @property
    def violations_per_true_possession(self) -> float:
        return self.total_violations / self.true_possessions if self.true_possessions else 0.0

    @property
    def share_of_team_turnovers(self) -> float:
        return self.total_violations / self.team_turnovers if self.team_turnovers else 0.0

    @property
    def second_chance_violation_rate(self) -> float:
        return (self.second_chance_violations / self.second_chance_continuations
                if self.second_chance_continuations else 0.0)

    @property
    def transition_violation_rate(self) -> float:
        return (self.transition_origin_violations / self.transition_origin_possessions
                if self.transition_origin_possessions else 0.0)


def _iter_results(items: Sequence[object]) -> Iterable[DetailedGameResult]:
    for item in items:
        yield item.result if isinstance(item, BenchmarkGame) else item


def _classify(raw: dict) -> str:
    if raw["terminal_source"] == "NO_FEASIBLE_ACTION":
        return ViolationCauseClass.NO_FEASIBLE_ACTION
    category = raw.get("timing_category_that_crossed_zero")
    before = raw.get("shot_clock_immediately_before_final_timing_charge")
    configured = raw.get("configured_final_timing_seconds")
    if category == "PASS_FLIGHT":
        if raw.get("feasible_shot_existed_at_previous_decision"):
            return ViolationCauseClass.LATE_CLOCK_SELECTION
        return ViolationCauseClass.LEGITIMATE_LIVE_TIMING
    if category is not None and before is not None and configured is not None and configured > before:
        return ViolationCauseClass.FIXED_TIMING_OVERSHOOT
    if category is not None:
        return ViolationCauseClass.LEGITIMATE_LIVE_TIMING
    return ViolationCauseClass.OTHER


def _observe(record: PossessionRecord) -> ShotClockViolationObservation:
    logs = record.terminal_result.world.shot_clock_violation_log
    if len(logs) != 1:
        raise AssertionError(f"{record.possession_id}: expected exactly one violation telemetry row, got {len(logs)}")
    raw = logs[0]
    charges = record.terminal_result.world.clock_charge_log
    resets = [entry for entry in charges if entry.get("timing_category") == PossessionStage.SECOND_CHANCE_RESET]
    last_reset = resets[-1] if resets else None
    reset_step = last_reset.get("step") if last_reset else None
    actions_after_oreb = sum(
        entry.get("step", -1) > reset_step
        for entry in record.terminal_result.world.action_log
    ) if reset_step is not None else 0
    return ShotClockViolationObservation(
        **raw,
        second_chance_reset_clock_before=last_reset.get("shot_clock_before") if last_reset else None,
        second_chance_reset_clock_after=last_reset.get("shot_clock_after") if last_reset else None,
        actions_after_last_oreb=actions_after_oreb,
        cause_classification=_classify(raw),
    )


def _late_clock_stats(results: Sequence[DetailedGameResult], threshold: float) -> LateClockDecisionStats:
    rows = [row for result in results for record in result.possessions
            for row in record.terminal_result.world.decision_log
            if row.get("shot_clock_remaining") is not None
            and row["shot_clock_remaining"] <= threshold]
    return LateClockDecisionStats(
        threshold_seconds=threshold,
        decisions=len(rows),
        pass_selected=sum(row.get("selected_action_type") in
                          {"SWING_PASS", "KICKOUT", "RESET_PASS", "POCKET_PASS", "OUTLET_PASS"}
                          for row in rows),
        reset_pass_selected=sum(row.get("selected_action_type") == "RESET_PASS" for row in rows),
        shot_selected=sum(row.get("selected_action_type") in {"PULL_UP", "CATCH_AND_SHOOT"} for row in rows),
        pass_available=sum(bool(set(row.get("feasible_action_types", ())) &
                                {"SWING_PASS", "KICKOUT", "RESET_PASS", "POCKET_PASS", "OUTLET_PASS"})
                           for row in rows),
        shot_available=sum(bool(set(row.get("feasible_action_types", ())) &
                                {"PULL_UP", "CATCH_AND_SHOOT"}) for row in rows),
    )


def diagnose_shot_clock_violations(items: Sequence[object]) -> ShotClockDiagnosis:
    results = tuple(_iter_results(items))
    records = [record for result in results for record in result.possessions]
    violation_records = [record for record in records
                         if record.terminal_result.reason == PossessionTerminalReason.SHOT_CLOCK_VIOLATION]
    stray_logs = [record.possession_id for record in records
                  if record.terminal_result.reason != PossessionTerminalReason.SHOT_CLOCK_VIOLATION
                  and record.terminal_result.world.shot_clock_violation_log]
    if stray_logs:
        raise AssertionError(f"non-violation possessions carried violation telemetry: {stray_logs[:3]}")
    observations = tuple(_observe(record) for record in violation_records)
    second_chance_continuations = sum(
        entry.get("stage") == PossessionStage.SECOND_CHANCE_RESET
        for record in records for entry in record.terminal_result.world.stage_timing_log
    )
    transition_records = [record for record in records
                          if record.restart_context.restart_type == RestartType.LIVE_TRANSITION]
    return ShotClockDiagnosis(
        games=len(results), team_games=2 * len(results), true_possessions=len(records),
        team_turnovers=sum(record.provisional_deltas.team_turnovers for record in records),
        observations=observations,
        expiration_stages=dict(Counter(o.timing_category_that_crossed_zero or "NO_CROSSING_CHARGE"
                                       for o in observations)),
        terminal_sources=dict(Counter(o.terminal_source for o in observations)),
        entry_origins=dict(Counter(o.possession_origin for o in observations)),
        action_indices=dict(Counter(o.action_index for o in observations)),
        prior_actions=dict(Counter(o.previous_action_type or "NONE" for o in observations)),
        pass_counts=dict(Counter(o.passes for o in observations)),
        drive_counts=dict(Counter(o.drives for o in observations)),
        cause_classes=dict(Counter(o.cause_classification for o in observations)),
        second_chance_continuations=second_chance_continuations,
        second_chance_violations=sum(o.followed_oreb for o in observations),
        transition_origin_possessions=len(transition_records),
        transition_origin_violations=sum(o.initial_entry_origin == "TRANSITION" for o in observations),
        late_clock_decisions={str(int(threshold)): _late_clock_stats(results, threshold)
                              for threshold in (7.0, 4.0, 2.0)},
    )
