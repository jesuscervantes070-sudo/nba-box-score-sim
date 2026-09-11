"""Focused tests for read-only detailed-engine foul diagnostics."""
import unittest

from detailed_engine_benchmark import run_benchmark_sample
from detailed_engine_foul_diagnostics import assert_foul_reconciliation, diagnose_fouls


class TestFoulDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25005))
        cls.results = tuple(game.result for game in cls.games)
        cls.diagnosis = diagnose_fouls(cls.results)

    def test_action_and_opportunity_counts_reconcile_to_raw_logs(self):
        self.assertEqual(
            self.diagnosis.total_dispatched_actions,
            sum(len(record.terminal_result.world.action_log)
                for result in self.results for record in result.possessions),
        )
        self.assertEqual(
            sum(self.diagnosis.objective_opportunities_by_action.values()),
            sum(len(decision.get("objective_opportunities", ()))
                for result in self.results for record in result.possessions
                for decision in record.terminal_result.world.decision_log),
        )

    def test_every_dispatched_shot_executes_one_foul_check(self):
        self.assertEqual(
            self.diagnosis.total_shot_foul_checks,
            sum(self.diagnosis.shot_dispatches_by_family.values()),
        )

    def test_default_production_executes_no_drive_collision_foul_checks(self):
        self.assertGreater(self.diagnosis.dispatched_actions_by_action["DRIVE"], 0)
        self.assertEqual(self.diagnosis.drive_collision_foul_checks, 0)
        self.assertEqual(self.diagnosis.defensive_floor_fouls, 0)
        self.assertEqual(self.diagnosis.offensive_fouls, 0)

    def test_every_eligible_drive_gets_a_floor_foul_check_currently_all_no_foul(self):
        """"Model drive floor fouls as observable outcomes": every dispatched drive with an
        assigned defender now produces exactly one DRIVE_FLOOR_FOUL_CHECK opportunity, and (both
        hazards being UNCALIBRATED/None in production) every single one currently resolves
        NO_FLOOR_FOUL -- this is the new eligible-opportunity denominator a future calibration
        pass will need, not yet a nonzero rate."""
        self.assertGreater(self.diagnosis.eligible_drive_floor_foul_opportunities, 0)
        # every dispatched drive is eligible except the rare one with no assigned defender at all
        # (the same `if defender_id is not None:` guard the legacy on-ball-pressure check already uses).
        dispatched = self.diagnosis.dispatched_actions_by_action["DRIVE"]
        missing_defender_drives = dispatched - self.diagnosis.eligible_drive_floor_foul_opportunities
        self.assertGreaterEqual(missing_defender_drives, 0)
        self.assertLess(missing_defender_drives, dispatched * 0.05)  # a small minority, not systematic
        self.assertEqual(self.diagnosis.drive_floor_foul_charge_outcomes, 0)
        self.assertEqual(self.diagnosis.drive_floor_foul_defensive_outcomes, 0)
        self.assertEqual(self.diagnosis.drive_floor_foul_no_foul_continuations,
                          self.diagnosis.eligible_drive_floor_foul_opportunities)

    def test_pf_and_free_throw_projections_reconcile(self):
        assert_foul_reconciliation(self.diagnosis)

    def test_missed_and_one_free_throws_now_resolve_via_a_live_rebound(self):
        """Regression guard for "Fix missed and-one rebound continuation": this test used to
        prove the GAP existed (`and_one_missed_ft_rebound_handoffs == 0` despite real misses).
        `_dispatch_shooting_foul` (possession_orchestrator.py) now checks the FINAL free throw's
        own live-ball state before returning a terminal result, instead of short-circuiting on
        the underlying field goal's own make/miss -- so every missed and-one bonus FT now reaches
        the same live rebound dispatch an ordinary missed bonus FT always did."""
        sample = diagnose_fouls(tuple(
            game.result for game in run_benchmark_sample(range(25000, 25020))
        ))
        self.assertGreater(sample.and_one_final_ft_misses, 0)
        self.assertEqual(sample.and_one_missed_ft_rebound_handoffs, sample.and_one_final_ft_misses)

    def test_diagnosis_is_deterministic_and_non_mutating(self):
        before = tuple(
            (result.final_home_score, result.final_away_score, result.total_possessions,
             result.provisional_summary)
            for result in self.results
        )
        second = diagnose_fouls(self.results)
        after = tuple(
            (result.final_home_score, result.final_away_score, result.total_possessions,
             result.provisional_summary)
            for result in self.results
        )
        self.assertEqual(before, after)
        self.assertEqual(self.diagnosis, second)

    def test_diagnostic_module_has_no_rng_or_simulation_entry_point(self):
        import detailed_engine_foul_diagnostics as module
        self.assertNotIn("random", vars(module))
        self.assertNotIn("simulate_detailed_game", vars(module))
        self.assertNotIn("run_benchmark_sample", vars(module))


if __name__ == "__main__":
    unittest.main()
