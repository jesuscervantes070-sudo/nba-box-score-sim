"""Read-only shot-family opportunity, selection, and accounting diagnostics."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

from action_intent import ActionType, PASS_ACTIONS, SHOT_ACTIONS
from action_opportunity import INTERIOR_ZONES
from detailed_engine_benchmark import BenchmarkGame
from detailed_game import DetailedGameResult
from interior_shot_resolution import InteriorShotFamily
from rebound_resolution import ReboundOutcome
from shot_resolution import ShotFamily


@dataclass(frozen=True)
class ShotFamilyStats:
    attempts: int
    makes: int
    points: int
    opportunity_instances: int
    perceived_instances: int
    feasible_instances: int
    selected_actions: int

    @property
    def fg_pct(self) -> float:
        return self.makes / self.attempts if self.attempts else 0.0

    @property
    def points_per_attempt(self) -> float:
        return self.points / self.attempts if self.attempts else 0.0

    @property
    def selection_rate_given_objective_opportunity(self) -> float:
        return self.selected_actions / self.opportunity_instances if self.opportunity_instances else 0.0


@dataclass(frozen=True)
class ShotFamilyDiagnosis:
    games: int
    possessions: int
    fga: int
    two_pa: int
    three_pa: int
    by_family: Dict[str, ShotFamilyStats]
    by_action_attempts: Dict[str, int]
    by_action_makes: Dict[str, int]
    selected_shot_actions_by_release_family: Dict[str, Dict[str, int]]
    fga_by_release_family: Dict[str, Dict[str, int]]
    makes_by_release_family: Dict[str, Dict[str, int]]
    by_zone_attempts: Dict[str, int]
    objective_action_opportunities: Dict[str, int]
    perceived_action_opportunities: Dict[str, int]
    feasible_action_opportunities: Dict[str, int]
    selected_actions: Dict[str, int]
    late_clock_activations: int
    late_clock_selected_shot_actions: Dict[str, int]
    late_clock_fga_by_family: Dict[str, int]
    drive_opportunities: int
    drives_selected: int
    drive_outcomes: Dict[str, int]
    action_immediately_after_drive: Dict[str, int]
    fga_immediately_after_drive: Dict[str, int]
    selected_passes: Dict[str, int]
    completed_passes: Dict[str, int]
    action_immediately_after_completed_pass: Dict[str, Dict[str, int]]
    fga_immediately_after_completed_pass: Dict[str, Dict[str, int]]
    made_and_one_fga: int
    missed_shooting_fouls_without_fga: int
    rebound_opportunities_by_family: Dict[str, int]
    rebound_oreb_by_family: Dict[str, int]
    rebound_dreb_by_family: Dict[str, int]
    accounting_mismatches: Tuple[str, ...]

    @property
    def three_point_attempt_rate(self) -> float:
        return self.three_pa / self.fga if self.fga else 0.0

    @property
    def late_clock_fga(self) -> int:
        return sum(self.late_clock_fga_by_family.values())

    @property
    def late_clock_three_point_rate(self) -> float:
        return (self.late_clock_fga_by_family.get(ShotFamily.THREE_POINT, 0) / self.late_clock_fga
                if self.late_clock_fga else 0.0)


def _results(items: Sequence[object]) -> Iterable[DetailedGameResult]:
    for item in items:
        yield item.result if isinstance(item, BenchmarkGame) else item


def family_for_selected_shot(action_type: Optional[str], target_zone: Optional[str],
                             ball_zone: Optional[str]) -> Optional[str]:
    """Mirror the current dispatch classification without changing it."""
    if action_type not in {action.value for action in SHOT_ACTIONS}:
        return None
    zone = target_zone or ball_zone
    if zone == "RESTRICTED_RIM":
        return InteriorShotFamily.RIM
    if zone == "PAINT":
        return InteriorShotFamily.FLOATER
    if zone == "MIDRANGE":
        return ShotFamily.MIDRANGE
    return ShotFamily.THREE_POINT


def _nested_dict(counter_by_key) -> Dict[str, Dict[str, int]]:
    return {key: dict(counter) for key, counter in counter_by_key.items()}


def diagnose_shot_families(items: Sequence[object]) -> ShotFamilyDiagnosis:
    results = tuple(_results(items))
    records = [record for result in results for record in result.possessions]
    family_attempts = Counter()
    family_makes = Counter()
    action_attempts = Counter()
    action_makes = Counter()
    selected_by_release_family = defaultdict(Counter)
    fga_by_release_family = defaultdict(Counter)
    makes_by_release_family = defaultdict(Counter)
    zone_attempts = Counter()
    objective_families = Counter()
    perceived_families = Counter()
    feasible_families = Counter()
    selected_families = Counter()
    objective_actions = Counter()
    perceived_actions = Counter()
    feasible_actions = Counter()
    selected_actions = Counter()
    late_selected = Counter()
    late_fga = Counter()
    drive_outcomes = Counter()
    after_drive = Counter()
    after_drive_fga = Counter()
    selected_passes = Counter()
    completed_passes = Counter()
    after_pass = defaultdict(Counter)
    after_pass_fga = defaultdict(Counter)
    made_and_one = 0
    missed_fouls = 0
    rebound_opps = Counter()
    rebound_oreb = Counter()
    rebound_dreb = Counter()

    for record in records:
        world = record.terminal_result.world
        decisions = {decision["step"]: decision for decision in world.decision_log}
        shots = {shot["step"]: shot for shot in world.shot_attempt_log}
        actions = world.action_log

        for decision in world.decision_log:
            for layer, family_counter, action_counter in (
                (decision.get("objective_opportunities", ()), objective_families, objective_actions),
                (decision.get("perceived_opportunities", ()), perceived_families, perceived_actions),
                (decision.get("feasible_opportunities", ()), feasible_families, feasible_actions),
            ):
                for opportunity in layer:
                    action_counter[opportunity["action_type"]] += 1
                    family = family_for_selected_shot(
                        opportunity["action_type"], opportunity.get("target_zone"), decision.get("ball_zone"),
                    )
                    if family is not None:
                        family_counter[family] += 1
            selected = decision.get("selected_action_type")
            if selected is not None:
                selected_actions[selected] += 1
            selected_family = family_for_selected_shot(
                selected, decision.get("selected_target_zone"), decision.get("ball_zone"),
            )
            if selected_family is not None:
                selected_families[selected_family] += 1
                selected_by_release_family[selected][selected_family] += 1
            if decision.get("late_clock_filter_activated") and selected_family is not None:
                late_selected[selected] += 1
                if decision["step"] in shots:
                    late_fga[shots[decision["step"]]["shot_family"]] += 1

        for shot in world.shot_attempt_log:
            family = shot["shot_family"]
            family_attempts[family] += 1
            family_makes[family] += bool(shot["made"])
            decision = decisions.get(shot["step"], {})
            action = decision.get("selected_action_type", "UNKNOWN")
            zone = decision.get("selected_target_zone") or decision.get("ball_zone", "UNKNOWN")
            action_attempts[action] += 1
            action_makes[action] += bool(shot["made"])
            fga_by_release_family[action][family] += 1
            makes_by_release_family[action][family] += bool(shot["made"])
            zone_attempts[zone] += 1

        for trace in world.trace:
            if trace.get("action") == "DRIVE":
                drive_outcomes[trace.get("outcome", "UNKNOWN")] += 1
            elif trace.get("action") == "SHOOTING_FOUL":
                if trace.get("made"):
                    made_and_one += 1
                else:
                    missed_fouls += 1

        for index, action in enumerate(actions):
            action_type = action["action_type"]
            next_action = actions[index + 1] if index + 1 < len(actions) else None
            next_type = next_action["action_type"] if next_action else "NO_NEXT_DISPATCH"
            if action_type == ActionType.DRIVE.value:
                after_drive[next_type] += 1
                if next_action and next_action["step"] in shots:
                    after_drive_fga[shots[next_action["step"]]["shot_family"]] += 1
            if action_type in {action.value for action in PASS_ACTIONS}:
                selected_passes[action_type] += 1
                trace = next((entry for entry in world.trace
                              if entry.get("step") == action["step"] and entry.get("action") == action_type), None)
                if trace and trace.get("outcome") in {"COMPLETED_CLEAN", "COMPLETED_ADJUSTED"}:
                    completed_passes[action_type] += 1
                    after_pass[action_type][next_type] += 1
                    if next_action and next_action["step"] in shots:
                        after_pass_fga[action_type][shots[next_action["step"]]["shot_family"]] += 1

        for rebound in world.rebound_opportunity_log:
            family = rebound["shot_family"]
            rebound_opps[family] += 1
            if rebound["outcome"] in {ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.TEAM_REBOUND_OFFENSE}:
                rebound_oreb[family] += 1
            elif rebound["outcome"] in {ReboundOutcome.SECURED_DEFENSE, ReboundOutcome.TEAM_REBOUND_DEFENSE}:
                rebound_dreb[family] += 1

    fga = sum(record.provisional_deltas.fga for record in records)
    three_pa = sum(record.provisional_deltas.fg3a for record in records)
    family_points = {
        family: family_makes[family] * (3 if family == ShotFamily.THREE_POINT else 2)
        for family in family_attempts
    }
    all_families = set(family_attempts) | set(objective_families) | set(selected_families)
    by_family = {
        family: ShotFamilyStats(
            attempts=family_attempts[family], makes=family_makes[family], points=family_points.get(family, 0),
            opportunity_instances=objective_families[family], perceived_instances=perceived_families[family],
            feasible_instances=feasible_families[family], selected_actions=selected_families[family],
        )
        for family in sorted(all_families)
    }
    mismatches = []
    if fga != sum(family_attempts.values()):
        mismatches.append(f"StatDelta FGA {fga} != shot log {sum(family_attempts.values())}")
    if three_pa != family_attempts[ShotFamily.THREE_POINT]:
        mismatches.append(f"StatDelta 3PA {three_pa} != THREE_POINT log {family_attempts[ShotFamily.THREE_POINT]}")
    two_point_attempts = (family_attempts[InteriorShotFamily.RIM]
                          + family_attempts[InteriorShotFamily.FLOATER]
                          + family_attempts[ShotFamily.MIDRANGE])
    if fga - three_pa != two_point_attempts:
        mismatches.append(f"StatDelta 2PA {fga-three_pa} != two-point shot log {two_point_attempts}")

    return ShotFamilyDiagnosis(
        games=len(results), possessions=len(records), fga=fga, two_pa=fga-three_pa, three_pa=three_pa,
        by_family=by_family, by_action_attempts=dict(action_attempts), by_action_makes=dict(action_makes),
        selected_shot_actions_by_release_family=_nested_dict(selected_by_release_family),
        fga_by_release_family=_nested_dict(fga_by_release_family),
        makes_by_release_family=_nested_dict(makes_by_release_family),
        by_zone_attempts=dict(zone_attempts), objective_action_opportunities=dict(objective_actions),
        perceived_action_opportunities=dict(perceived_actions), feasible_action_opportunities=dict(feasible_actions),
        selected_actions=dict(selected_actions),
        late_clock_activations=sum(decision.get("late_clock_filter_activated", False)
                                   for record in records for decision in record.terminal_result.world.decision_log),
        late_clock_selected_shot_actions=dict(late_selected), late_clock_fga_by_family=dict(late_fga),
        drive_opportunities=objective_actions[ActionType.DRIVE.value],
        drives_selected=selected_actions[ActionType.DRIVE.value], drive_outcomes=dict(drive_outcomes),
        action_immediately_after_drive=dict(after_drive), fga_immediately_after_drive=dict(after_drive_fga),
        selected_passes=dict(selected_passes), completed_passes=dict(completed_passes),
        action_immediately_after_completed_pass=_nested_dict(after_pass),
        fga_immediately_after_completed_pass=_nested_dict(after_pass_fga),
        made_and_one_fga=made_and_one, missed_shooting_fouls_without_fga=missed_fouls,
        rebound_opportunities_by_family=dict(rebound_opps), rebound_oreb_by_family=dict(rebound_oreb),
        rebound_dreb_by_family=dict(rebound_dreb), accounting_mismatches=tuple(mismatches),
    )
