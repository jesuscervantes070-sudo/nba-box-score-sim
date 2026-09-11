"""Focused guardrails for diagnostic-only shot-family telemetry."""
import inspect
import unittest

import shot_family_diagnostics
from action_intent import ActionType
from detailed_engine_benchmark import run_benchmark_sample
from interior_shot_resolution import InteriorShotFamily
from possession_state import SpatialZone
from shot_family_diagnostics import diagnose_shot_families, family_for_selected_shot
from shot_resolution import ShotFamily


class TestShotFamilyDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25010))
        cls.diagnosis = diagnose_shot_families(cls.games)

    def test_complete_supported_family_enumeration(self):
        self.assertEqual(set(self.diagnosis.by_family), {
            ShotFamily.THREE_POINT, InteriorShotFamily.FLOATER, InteriorShotFamily.RIM,
        })

    def test_midrange_is_structurally_unreachable(self):
        self.assertNotIn("MIDRANGE", {zone.value for zone in SpatialZone})
        self.assertNotIn(ShotFamily.MIDRANGE, self.diagnosis.by_family)
        for action in (ActionType.PULL_UP.value, ActionType.CATCH_AND_SHOOT.value):
            for zone in SpatialZone:
                self.assertNotEqual(family_for_selected_shot(action, zone.value, zone.value),
                                    ShotFamily.MIDRANGE)

    def test_shot_family_accounting_reconciles(self):
        self.assertEqual(self.diagnosis.accounting_mismatches, ())
        self.assertEqual(self.diagnosis.fga, self.diagnosis.two_pa + self.diagnosis.three_pa)
        self.assertEqual(self.diagnosis.three_pa,
                         self.diagnosis.by_family[ShotFamily.THREE_POINT].attempts)

    def test_opportunity_and_selection_layers_reconcile(self):
        selected_shots = sum(self.diagnosis.selected_actions.get(action.value, 0)
                             for action in (ActionType.PULL_UP, ActionType.CATCH_AND_SHOOT))
        self.assertEqual(selected_shots, sum(stats.selected_actions
                                             for stats in self.diagnosis.by_family.values()))
        for stats in self.diagnosis.by_family.values():
            self.assertEqual(stats.opportunity_instances, stats.perceived_instances)
            self.assertEqual(stats.perceived_instances, stats.feasible_instances)
            self.assertGreaterEqual(stats.opportunity_instances, stats.selected_actions)
            self.assertGreaterEqual(stats.selected_actions, stats.attempts)

    def test_late_clock_family_telemetry_reconciles(self):
        self.assertGreater(self.diagnosis.late_clock_activations, 0)
        self.assertEqual(sum(self.diagnosis.late_clock_selected_shot_actions.values()),
                         self.diagnosis.late_clock_activations)
        self.assertLessEqual(self.diagnosis.late_clock_fga,
                             self.diagnosis.late_clock_activations)
        self.assertTrue(set(self.diagnosis.late_clock_fga_by_family) <= {
            ShotFamily.THREE_POINT, InteriorShotFamily.FLOATER, InteriorShotFamily.RIM,
        })

    def test_diagnostics_are_deterministic_and_non_interfering(self):
        first = run_benchmark_sample([26123])[0]
        diagnosis = diagnose_shot_families([first])
        second = run_benchmark_sample([26123])[0]
        self.assertEqual(first.result, second.result)
        self.assertEqual(diagnosis, diagnose_shot_families([second]))
        source = inspect.getsource(shot_family_diagnostics)
        self.assertNotIn("import random", source)
        self.assertNotIn("simulate_detailed_game", source)


if __name__ == "__main__":
    unittest.main()
