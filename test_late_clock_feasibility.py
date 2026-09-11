"""Focused tests for the narrow pre-scoring late-clock continuation gate."""
import random
import unittest

from action_intent import ActionType
from action_opportunity import ObjectiveOpportunity
from action_perception import NO_GATE_PROVENANCE, PerceivedOpportunity
from action_selection import (
    BASE_WEIGHT, ClockContext, RoleContext, SelectionPolicy, TendencyContext,
    _clock_feasible, evaluate_clock_feasibility,
)
from clock_semantics import CLOCK_EPSILON_SECONDS
from pass_resolution import FLIGHT_DURATION_SECONDS, PassFamily
from possession_orchestrator import (
    PlayerSimulationProfile, PossessionConfig, PossessionTerminalReason,
    _continuation_minimum_seconds_by_opportunity, simulate_possession,
)
from possession_rules import EraRules
from possession_state import SpatialZone


OFF_FIVE = tuple(str(i) for i in range(1, 6))
DEF_FIVE = tuple(str(i) for i in range(11, 16))


def perceived(opportunity_id, action_type, target_player_id=None, target_zone=None):
    return PerceivedOpportunity(
        ObjectiveOpportunity(
            opportunity_id, action_type, "1", target_player_id=target_player_id,
            target_zone=target_zone,
        ),
        NO_GATE_PROVENANCE,
    )


def menu():
    return [
        perceived("shot", ActionType.PULL_UP, target_zone=SpatialZone.TOP_OF_KEY),
        perceived("pass", ActionType.SWING_PASS, target_player_id="2"),
    ]


def clock(seconds, required=3.4):
    return ClockContext(
        shot_clock_remaining=seconds,
        continuation_minimum_seconds_by_opportunity_id={"pass": required},
    )


def profiles():
    return {pid: PlayerSimulationProfile.synthetic(pid, "A" if pid in OFF_FIVE else "B")
            for pid in OFF_FIVE + DEF_FIVE}


class TestLateClockFeasibility(unittest.TestCase):
    def test_pass_remains_available_with_ample_clock(self):
        result = evaluate_clock_feasibility(menu(), clock(8.0))
        self.assertIn("pass", {p.opportunity.opportunity_id for p in result.feasible})
        self.assertFalse(result.late_clock_filter_activated)

    def test_impossible_continuation_is_removed(self):
        result = evaluate_clock_feasibility(menu(), clock(1.5))
        self.assertEqual([p.opportunity.opportunity_id for p in result.removed_for_late_clock], ["pass"])
        self.assertTrue(result.late_clock_filter_activated)

    def test_terminal_shot_remains_available(self):
        result = evaluate_clock_feasibility(menu(), clock(1.5))
        self.assertEqual([p.opportunity.action_type for p in result.feasible], [ActionType.PULL_UP])

    def test_selector_chooses_only_from_filtered_menu(self):
        intent = SelectionPolicy(random.Random(1)).select(
            menu(), RoleContext(), TendencyContext(), clock(1.5), "p",
        )
        self.assertEqual(intent.action_type, ActionType.PULL_UP)

    def test_no_shot_available_does_not_invent_one(self):
        passes_only = [perceived("pass", ActionType.SWING_PASS, target_player_id="2")]
        result = evaluate_clock_feasibility(passes_only, clock(1.5))
        self.assertFalse(result.late_clock_filter_activated)
        intent = SelectionPolicy(random.Random(1)).select(
            passes_only, RoleContext(), TendencyContext(), clock(1.5), "p",
        )
        self.assertEqual(intent.action_type, ActionType.SWING_PASS)

    def test_exact_boundary_remains_feasible(self):
        result = evaluate_clock_feasibility(menu(), clock(3.4))
        self.assertIn("pass", {p.opportunity.opportunity_id for p in result.feasible})

    def test_shared_tolerance_controls_boundary(self):
        within = evaluate_clock_feasibility(menu(), clock(3.4 - CLOCK_EPSILON_SECONDS / 2.0))
        beyond = evaluate_clock_feasibility(menu(), clock(3.4 - CLOCK_EPSILON_SECONDS * 2.0))
        self.assertFalse(within.late_clock_filter_activated)
        self.assertTrue(beyond.late_clock_filter_activated)

    def test_pass_minimum_uses_real_flight_plus_inter_action(self):
        config = PossessionConfig()
        requirements = _continuation_minimum_seconds_by_opportunity(
            [perceived("pass", ActionType.SWING_PASS, target_player_id="2")],
            SpatialZone.TOP_OF_KEY,
            config,
        )
        self.assertEqual(
            requirements["pass"],
            FLIGHT_DURATION_SECONDS[PassFamily.DIRECT] + config.inter_action_seconds,
        )

    def test_early_clock_action_set_is_unchanged(self):
        items = menu()
        context = clock(18.0)
        expected = [p for p in items if _clock_feasible(p.opportunity.action_type, context)]
        self.assertEqual(list(evaluate_clock_feasibility(items, context).feasible), expected)

    def test_selection_probability_constants_are_unchanged(self):
        self.assertEqual(BASE_WEIGHT, 1.0)

    def test_timing_constants_are_unchanged(self):
        config = PossessionConfig()
        self.assertEqual(config.inter_action_seconds, 3.0)
        self.assertEqual(config.ordinary_entry_seconds, 9.0)
        self.assertEqual(config.transition_entry_seconds, 1.5)
        self.assertEqual(config.second_chance_reset_seconds, 1.0)
        self.assertEqual(FLIGHT_DURATION_SECONDS[PassFamily.DIRECT], 0.4)

    def test_deterministic_replay_and_telemetry_non_interference(self):
        config = PossessionConfig()
        kwargs = dict(
            offense_team_id="A", defense_team_id="B", offensive_five=OFF_FIVE,
            defensive_five=DEF_FIVE, profiles=profiles(), inbound_receiver_id="1",
            config=config, rng_seed=73, possession_id="det-late",
        )
        first = simulate_possession(**kwargs)
        second = simulate_possession(**kwargs)
        self.assertEqual(first, second)
        for row in first.world.decision_log:
            self.assertIn("late_clock_filter_activated", row)
            self.assertIn("late_clock_possession_still_violated", row)

    def test_feasibility_telemetry_consumes_no_rng(self):
        items = menu()
        context = clock(1.5)
        rng_with_observation = random.Random(41)
        rng_without_observation = random.Random(41)
        evaluate_clock_feasibility(items, context)
        observed = SelectionPolicy(rng_with_observation).select(
            items, RoleContext(), TendencyContext(), context, "p",
        )
        control = SelectionPolicy(rng_without_observation).select(
            items, RoleContext(), TendencyContext(), context, "p",
        )
        self.assertEqual(observed, control)
        self.assertEqual(rng_with_observation.random(), rng_without_observation.random())

    def test_telemetry_records_removed_terminal_and_replacement(self):
        config = PossessionConfig()
        found = None
        for seed in range(300):
            result = simulate_possession(
                "A", "B", OFF_FIVE, DEF_FIVE, profiles(), "1",
                config=config, rng_seed=seed, possession_id=f"telem-{seed}",
            )
            found = next((d for d in result.world.decision_log
                          if d.get("late_clock_filter_activated")), None)
            if found is not None:
                break
        self.assertIsNotNone(found)
        self.assertTrue(found["late_clock_removed_actions"])
        self.assertTrue(found["late_clock_terminal_shots_available"])
        self.assertIn(found["late_clock_selected_replacement_action"],
                      found["late_clock_terminal_shots_available"])
        self.assertIsInstance(found["late_clock_possession_still_violated"], bool)

    def test_legitimate_shot_clock_violation_remains_possible(self):
        rules = EraRules("legitimate", 1.0, 14.0, 5, 720.0, 4)
        result = simulate_possession(
            "A", "B", OFF_FIVE, DEF_FIVE, profiles(), "1",
            config=PossessionConfig(era_rules=rules), rng_seed=1,
            possession_id="legitimate",
        )
        self.assertEqual(result.reason, PossessionTerminalReason.SHOT_CLOCK_VIOLATION)


if __name__ == "__main__":
    unittest.main()
