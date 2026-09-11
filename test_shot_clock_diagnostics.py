"""Focused guardrails for diagnostic-only shot-clock root-cause telemetry."""
import inspect
import unittest

import shot_clock_diagnostics
from detailed_engine_benchmark import run_benchmark_sample
from possession_orchestrator import PossessionTerminalReason
from shot_clock_diagnostics import diagnose_shot_clock_violations


class TestShotClockViolationDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25100))
        cls.diagnosis = diagnose_shot_clock_violations(cls.games)
        cls.records = [record for game in cls.games for record in game.result.possessions]

    def test_complete_violation_coverage_is_exactly_once(self):
        violations = [record for record in self.records
                      if record.terminal_result.reason == PossessionTerminalReason.SHOT_CLOCK_VIOLATION]
        violation_records = {id(record) for record in violations}
        self.assertEqual(len(self.diagnosis.observations), len(violations))
        self.assertTrue(all(len(record.terminal_result.world.shot_clock_violation_log) == 1
                            for record in violations))
        self.assertTrue(all(not record.terminal_result.world.shot_clock_violation_log
                            for record in self.records if id(record) not in violation_records))

    def test_expiration_telemetry_contains_required_causal_fields(self):
        self.assertTrue(self.diagnosis.observations)
        for obs in self.diagnosis.observations:
            self.assertEqual(obs.action_index, obs.total_modeled_actions)
            self.assertEqual(obs.offense_team_id in {"HOME", "AWAY"}, True)
            self.assertIsNotNone(obs.previous_action_type)
            self.assertGreaterEqual(obs.passes, 0)
            self.assertGreaterEqual(obs.drives, 0)
            self.assertGreaterEqual(obs.total_entry_time, 0.0)
            self.assertGreaterEqual(obs.total_inter_action_time, 0.0)
            self.assertGreaterEqual(obs.total_pass_time, 0.0)
            self.assertGreaterEqual(obs.total_drive_time, 0.0)
            self.assertGreaterEqual(obs.total_shot_time, 0.0)

    def test_causal_timing_stage_is_distinct_from_terminal_source(self):
        observed_stages = set(self.diagnosis.expiration_stages)
        self.assertTrue(observed_stages <= {"INTER_ACTION", "PASS_FLIGHT", "LOOSE_BALL_RECOVERY"})
        self.assertIn("TOP_OF_LOOP", self.diagnosis.terminal_sources)
        self.assertNotEqual(self.diagnosis.expiration_stages, self.diagnosis.terminal_sources)

    def test_top_of_loop_has_prior_causal_charge_and_no_new_dispatch(self):
        top = [obs for obs in self.diagnosis.observations if obs.terminal_source == "TOP_OF_LOOP"]
        self.assertTrue(top)
        self.assertTrue(all(obs.timing_category_that_crossed_zero is not None for obs in top))
        self.assertTrue(all(obs.action_was_selected_before_expiration for obs in top))
        self.assertTrue(all(obs.shot_clock_at_start_of_previous_decision is not None for obs in top))

    def test_canonical_late_gate_removes_pass_arrival_violations(self):
        arrivals = [obs for obs in self.diagnosis.observations if obs.terminal_source == "PASS_ARRIVAL"]
        self.assertEqual(arrivals, [])

    def test_oreb_context_records_real_fourteen_second_reset(self):
        second_chances = [obs for obs in self.diagnosis.observations if obs.followed_oreb]
        self.assertTrue(second_chances)
        for obs in second_chances:
            self.assertEqual(obs.possession_origin, "SECOND_CHANCE")
            self.assertEqual(obs.second_chance_reset_clock_before, 14.0)
            self.assertEqual(obs.second_chance_reset_clock_after, 13.0)
            self.assertGreaterEqual(obs.actions_after_last_oreb, 1)
        self.assertGreater(self.diagnosis.second_chance_continuations, 0)

    def test_telemetry_is_deterministic_and_cannot_consume_rng(self):
        first = run_benchmark_sample([26123])[0]
        second = run_benchmark_sample([26123])[0]
        self.assertEqual(first.result, second.result)
        source = inspect.getsource(shot_clock_diagnostics)
        self.assertNotIn("import random", source)
        self.assertNotIn(".select(", source)
        self.assertNotIn("perceive(", source)


if __name__ == "__main__":
    unittest.main()
