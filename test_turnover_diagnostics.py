"""Focused tests for diagnostic-only turnover decomposition."""
import inspect
import unittest

from detailed_engine_benchmark import run_benchmark_sample
from detailed_game import DetailedGameConfig
from possession_orchestrator import PossessionConfig
from turnover_diagnostics import (
    PASS_ACTIONS,
    TurnoverCategory,
    assert_turnover_reconciliation,
    classify_turnover,
    diagnose_turnovers,
)


class TestTurnoverDiagnosis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        historical_config = DetailedGameConfig(
            possession_config=PossessionConfig(pass_disruption_base_rate=0.12),
        )
        cls.games = run_benchmark_sample(range(25000, 25100), config=historical_config)
        cls.diagnosis = diagnose_turnovers(cls.games)

    def test_every_turnover_like_terminal_has_one_stable_category(self):
        expected = sum(
            record.terminal_result.reason in ("TURNOVER", "OFFENSIVE_FOUL_TURNOVER", "SHOT_CLOCK_VIOLATION")
            for game in self.games for record in game.result.possessions
        )
        self.assertEqual(len(self.diagnosis.observations), expected)
        self.assertNotIn(TurnoverCategory.OTHER_TURNOVER, self.diagnosis.categories)

    def test_engine_accounting_is_exactly_once_and_shot_clock_is_explicitly_separate(self):
        assert_turnover_reconciliation(self.diagnosis)
        # Re-pinned for "Fix missed and-one rebound continuation": the 28 previously-dead missed-
        # and-one bonus free throws (seeds 25000-25099) now continue live instead of vanishing, so
        # some of those continuations draw a real turnover (team- and player-charged) before the
        # possession eventually ends -- sanctioned drift from the bug fix itself, not a
        # turnover-logic change.
        self.assertEqual(self.diagnosis.engine_accounted_turnovers, 4893)
        self.assertEqual(self.diagnosis.player_charged_turnovers, 4682)
        self.assertEqual(self.diagnosis.categories[TurnoverCategory.SHOT_CLOCK_VIOLATION].engine_accounted_turnovers, 211)

    def test_steals_are_consistent_with_current_clean_interception_semantics(self):
        steals = sum(o.steal_credited for o in self.diagnosis.observations)
        clean = self.diagnosis.categories[TurnoverCategory.PASS_CLEAN_INTERCEPTION]
        direct_bad_passes = sum(o.raw_outcome == "BAD_PASS_TO_DEFENDER"
                                for o in self.diagnosis.observations
                                if o.category == TurnoverCategory.PASS_BAD_PASS)
        self.assertEqual(steals, clean.count + direct_bad_passes)
        self.assertEqual(clean.steals, clean.count)

    def test_no_loose_ball_sequence_double_counts_turnovers(self):
        for observation in self.diagnosis.observations:
            if observation.category in (TurnoverCategory.PASS_LOOSE_BALL_LOST,
                                         TurnoverCategory.HANDLE_STRIP_LOST):
                self.assertEqual(observation.engine_accounted_turnover, 1)

    def test_pass_exposure_denominator_matches_structured_trace(self):
        expected = sum(
            entry.get("action") in PASS_ACTIONS
            for game in self.games for record in game.result.possessions
            for entry in record.terminal_result.world.trace
        )
        self.assertEqual(sum(stats.attempts for stats in self.diagnosis.pass_families.values()), expected)

    def test_handling_exposure_denominator_matches_pressure_checks(self):
        expected = sum(
            entry.get("action") == "ON_BALL_PRESSURE"
            for game in self.games for record in game.result.possessions
            for entry in record.terminal_result.world.trace
        )
        self.assertEqual(self.diagnosis.handling.opportunities, expected)
        self.assertEqual(self.diagnosis.handling.loose_balls_created,
                         self.diagnosis.handling.defense_recoveries + self.diagnosis.handling.offense_recoveries)

    def test_shot_clock_violations_have_source_and_zero_terminal_clock(self):
        observations = [o for o in self.diagnosis.observations
                        if o.category == TurnoverCategory.SHOT_CLOCK_VIOLATION]
        self.assertTrue(observations)
        self.assertTrue(all(o.shot_clock_source in {"TOP_OF_LOOP", "PASS_ARRIVAL", "NO_FEASIBLE_ACTION"}
                            for o in observations))
        self.assertTrue(all(o.shot_clock_remaining == 0.0 for o in observations))

    def test_offensive_foul_category_is_reachable_under_existing_test_hook(self):
        config = DetailedGameConfig(
            regulation_period_seconds=30.0,
            max_possessions_per_period=50,
            possession_config=PossessionConfig(force_on_ball_contact_established=True),
        )
        found = None
        for seed in range(50):
            game = run_benchmark_sample([seed], config=config)[0]
            found = next((classify_turnover(record) for record in game.result.possessions
                          if record.terminal_result.reason == "OFFENSIVE_FOUL_TURNOVER"), None)
            if found is not None:
                break
        self.assertIsNotNone(found)
        self.assertEqual(found.category, TurnoverCategory.OFFENSIVE_FOUL)
        self.assertEqual(found.steal_credited, 0)
        self.assertEqual(found.engine_accounted_turnover, 1)
        self.assertEqual(found.player_charged_turnover, 1)

    def test_diagnosis_is_deterministic_and_non_mutating(self):
        before = tuple((g.result.final_home_score, g.result.final_away_score,
                        g.result.total_possessions, g.result.provisional_summary)
                       for g in self.games)
        second = diagnose_turnovers(self.games)
        after = tuple((g.result.final_home_score, g.result.final_away_score,
                       g.result.total_possessions, g.result.provisional_summary)
                      for g in self.games)
        self.assertEqual(before, after)
        self.assertEqual(self.diagnosis, second)

    def test_diagnostic_module_cannot_influence_simulation(self):
        import turnover_diagnostics as diagnostic
        import possession_orchestrator
        import detailed_game_orchestrator
        import detailed_game
        self.assertNotIn("random", vars(diagnostic))
        for module in (possession_orchestrator, detailed_game_orchestrator, detailed_game):
            self.assertNotIn("turnover_diagnostics", inspect.getsource(module))


if __name__ == "__main__":
    unittest.main()
