"""Focused tests for read-only detailed-engine BLOCK diagnostics."""
import unittest

from detailed_engine_benchmark import run_benchmark_sample
from detailed_engine_block_diagnostics import (
    BLOCK_CAPABLE_FAMILIES, assert_block_reconciliation, diagnose_blocks,
)


class TestBlockDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25020))
        cls.results = tuple(game.result for game in cls.games)
        cls.diagnosis = diagnose_blocks(cls.results)

    def test_block_accounting_reconciles_exactly(self):
        assert_block_reconciliation(self.diagnosis)

    def test_only_rim_and_floater_ever_produce_a_block(self):
        """Diagnostic confirmation (not a behavior assertion) of the engine's own documented
        scope: MIDRANGE/THREE_POINT structurally cannot be blocked today."""
        self.assertEqual(set(self.diagnosis.blocks_by_family.keys()) - {"RIM", "FLOATER"}, set())
        self.assertGreater(self.diagnosis.dispatched_shots_by_family.get("MIDRANGE", 0), 0)
        self.assertGreater(self.diagnosis.dispatched_shots_by_family.get("THREE_POINT", 0), 0)

    def test_every_block_capable_dispatch_is_whistled_xor_block_checked(self):
        for family in BLOCK_CAPABLE_FAMILIES:
            dispatched = self.diagnosis.dispatched_shots_by_family.get(family, 0)
            whistled = self.diagnosis.shooting_foul_attempts_by_family.get(family, 0)
            checked = self.diagnosis.block_checks_by_family.get(family, 0)
            self.assertGreater(dispatched, 0, family)
            self.assertEqual(dispatched, whistled + checked, family)

    def test_diagnosis_is_deterministic_and_non_mutating(self):
        before = tuple(
            (result.final_home_score, result.final_away_score, result.total_possessions)
            for result in self.results
        )
        second = diagnose_blocks(self.results)
        after = tuple(
            (result.final_home_score, result.final_away_score, result.total_possessions)
            for result in self.results
        )
        self.assertEqual(before, after)
        self.assertEqual(self.diagnosis, second)

    def test_diagnostic_module_has_no_rng_or_simulation_entry_point(self):
        import detailed_engine_block_diagnostics as module
        self.assertNotIn("random", vars(module))
        self.assertNotIn("simulate_detailed_game", vars(module))
        self.assertNotIn("run_benchmark_sample", vars(module))


if __name__ == "__main__":
    unittest.main()
