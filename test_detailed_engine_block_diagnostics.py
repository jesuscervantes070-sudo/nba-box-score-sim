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

    def test_all_four_families_can_now_produce_a_block(self):
        """Re-purposed for "Complete shot-family block occurrence": MIDRANGE/THREE_POINT were
        structurally UNREACHABLE for a block before this phase (the old test's own name/docstring
        confirmed this as the engine's then-documented scope) -- `shot_resolution.py`'s new
        `perimeter_block_probability`/`resolve_perimeter_shot` close that gap, reusing the SAME
        `defensive_playmaking`-driven architecture `interior_shot_resolution.py` already uses for
        RIM/FLOATER (primary-defender-only, no help-defender anchor -- see that module's own
        updated docstring). All four families should now show a nonzero block count over a real
        sample, with RIM/FLOATER each notably more frequent than MIDRANGE/THREE_POINT (an
        empirically-anchored family ordering, not merely "nonzero")."""
        blocks = self.diagnosis.blocks_by_family
        for family in ("RIM", "FLOATER", "MIDRANGE", "THREE_POINT"):
            self.assertGreater(blocks.get(family, 0), 0, family)
        rim_rate = self.diagnosis.block_rate_per_check("RIM")
        floater_rate = self.diagnosis.block_rate_per_check("FLOATER")
        midrange_rate = self.diagnosis.block_rate_per_check("MIDRANGE")
        three_rate = self.diagnosis.block_rate_per_check("THREE_POINT")
        self.assertGreater(rim_rate, midrange_rate)
        self.assertGreater(floater_rate, midrange_rate)
        self.assertGreater(midrange_rate, three_rate)

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
