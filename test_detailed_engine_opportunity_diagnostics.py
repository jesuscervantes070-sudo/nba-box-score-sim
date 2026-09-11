"""Focused tests for read-only detailed-engine opportunity-generation diagnostics."""
import unittest

from detailed_engine_benchmark import run_benchmark_sample
from detailed_engine_opportunity_diagnostics import (
    assert_opportunity_reconciliation, diagnose_opportunities,
)


class TestOpportunityDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25020))
        cls.results = tuple(game.result for game in cls.games)
        cls.diagnosis = diagnose_opportunities(cls.results)

    def test_reconciliation_passes(self):
        assert_opportunity_reconciliation(self.diagnosis)

    def test_every_possession_has_a_source_and_restart_type(self):
        self.assertGreater(self.diagnosis.total_possessions, 0)
        self.assertEqual(sum(self.diagnosis.possessions_by_source.values()), self.diagnosis.total_possessions)
        self.assertIn("DEFENSIVE_REBOUND", self.diagnosis.possessions_by_source)
        self.assertIn("MADE_BASKET_INBOUND", self.diagnosis.possessions_by_source)

    def test_interior_shot_families_never_appear_in_one_action_possessions(self):
        """Headline structural finding, confirmed by direct measurement: a RIM/FLOATER attempt
        requires `state.ball_zone` to already be interior, which requires a prior DRIVE whose
        outcome advanced the zone -- a possession's FIRST action can never already be at an
        interior zone (the engine's own initial/inbound ball-zone policy is always a perimeter
        zone), so a one-action possession that ends in a shot can only ever be MIDRANGE or
        THREE_POINT."""
        interior = {"RIM", "FLOATER"}
        self.assertEqual(set(self.diagnosis.one_action_by_shot_family) & interior, set())
        self.assertGreater(sum(self.diagnosis.one_action_by_shot_family.values()), 0)

    def test_drive_outcomes_are_a_real_subset_of_drives_selected(self):
        """NOT an exact reconciliation: a dispatched DRIVE can be intercepted by the on-ball-
        pressure pre-check (FORCED_PICKUP/CLEAN_STRIP_LOOSE) or the drive-floor-foul-check stage
        BEFORE ever reaching `resolve_drive` at all -- confirmed directly, those drives never log
        a "DRIVE" trace row (only "ON_BALL_PRESSURE"/"DRIVE_FLOOR_FOUL_CHECK"). `drive_outcomes`
        is therefore always <= `drives_selected`, never equal to it."""
        total_outcomes = sum(self.diagnosis.drive_outcomes.values())
        self.assertGreater(total_outcomes, 0)
        self.assertLessEqual(total_outcomes, self.diagnosis.drives_selected)

    def test_only_clean_or_partial_drives_can_ever_produce_an_interior_shot_next(self):
        """CONTAINED/FORCED_PICKUP never advance ball_zone (drive_resolution.py's own
        `_advance_zone` returns the SAME zone for those two outcomes) -- so of the families
        recorded in drive_then_shot_family, RIM/FLOATER could only ever arise from a drive that
        actually progressed, never from a contained/forced-pickup one. This test only confirms
        the diagnostic captured SOME interior shots immediately after a drive (a real, nonzero
        fraction) -- not a behavioral claim about the resolver itself (see drive_resolution.py's
        own focused tests for that)."""
        interior_after_drive = (self.diagnosis.drive_then_shot_family.get("RIM", 0)
                                 + self.diagnosis.drive_then_shot_family.get("FLOATER", 0))
        self.assertGreater(interior_after_drive, 0)

    def test_diagnosis_is_deterministic_and_non_mutating(self):
        before = tuple(
            (r.final_home_score, r.final_away_score, r.total_possessions) for r in self.results
        )
        second = diagnose_opportunities(self.results)
        after = tuple(
            (r.final_home_score, r.final_away_score, r.total_possessions) for r in self.results
        )
        self.assertEqual(before, after)
        self.assertEqual(self.diagnosis, second)

    def test_diagnostic_module_has_no_rng_or_simulation_entry_point(self):
        import detailed_engine_opportunity_diagnostics as module
        self.assertNotIn("random", vars(module))
        self.assertNotIn("simulate_detailed_game", vars(module))
        self.assertNotIn("run_benchmark_sample", vars(module))


if __name__ == "__main__":
    unittest.main()
