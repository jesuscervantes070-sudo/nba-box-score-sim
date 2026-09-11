"""Focused tests for detailed_engine_pace_diagnosis.py -- diagnostic
reconciliation only. Per this diagnostic task's own instructions: these
NEVER assert a target pace/FGA value, and never assert the simulator is
"correct" -- only that the ALREADY-LOGGED clock/action/terminal ledgers
are self-consistent and that this module adds zero RNG consumption / zero
behavioral interference."""
import unittest

from detailed_engine_pace_diagnosis import (
    CLOCK_CATEGORIES,
    DURATION_BUCKETS,
    PossessionRow,
    _duration_bucket,
    all_possession_rows,
    clock_ledger_reconciliation_gap,
    game_possession_rows,
    percentiles,
    possession_row,
    run_pace_diagnosis_sample,
    true_possession_count,
)
from detailed_engine_benchmark import run_benchmark_sample
from detailed_game import DetailedGameConfig, simulate_detailed_game
from possession_orchestrator import PlayerSimulationProfile

OFF_FIVE = tuple(str(i) for i in range(1, 6))
DEF_FIVE = tuple(str(i) for i in range(11, 16))


class TestDurationBucketing(unittest.TestCase):
    def test_bucket_boundaries_cover_every_bucket_exactly_once(self):
        # every DURATION_BUCKETS entry's own low edge must classify into ITSELF
        for label, low, high in DURATION_BUCKETS:
            self.assertEqual(_duration_bucket(low), label)

    def test_bucket_is_never_out_of_range_for_nonnegative_values(self):
        for v in (0.0, 2.9, 3.0, 7.5, 23.999, 24.0, 100.0):
            self.assertNotEqual(_duration_bucket(v), "OUT_OF_RANGE")

    def test_buckets_are_contiguous_and_exhaustive_from_zero(self):
        sorted_buckets = sorted(DURATION_BUCKETS, key=lambda b: b[1])
        self.assertEqual(sorted_buckets[0][1], 0.0)
        for (_, _, high_a), (_, low_b, _) in zip(sorted_buckets, sorted_buckets[1:]):
            self.assertEqual(high_a, low_b)
        self.assertEqual(sorted_buckets[-1][2], float("inf"))


class TestPercentiles(unittest.TestCase):
    def test_percentiles_on_known_sequence(self):
        values = list(range(1, 101))  # 1..100
        p = percentiles(values, [0.0, 0.5, 1.0])
        self.assertEqual(p[0.0], 1)
        self.assertEqual(p[1.0], 100)

    def test_percentiles_rejects_empty(self):
        with self.assertRaises(ValueError):
            percentiles([], [0.5])

    def test_percentiles_never_extrapolate(self):
        values = [10.0, 20.0, 30.0]
        p = percentiles(values, [0.1, 0.5, 0.9])
        for v in p.values():
            self.assertGreaterEqual(v, min(values))
            self.assertLessEqual(v, max(values))


class TestPossessionRowExtraction(unittest.TestCase):
    def setUp(self):
        self.games = run_pace_diagnosis_sample(seeds=[9001, 9002, 9003])

    def test_action_count_matches_real_action_log_length(self):
        for game in self.games:
            for record in game.result.possessions:
                row = possession_row(record, game.seed, 0, is_period_opener=False)
                self.assertEqual(row.action_count, len(record.terminal_result.world.action_log))
                self.assertEqual(len(row.action_types), row.action_count)

    def test_fga_count_matches_real_shot_attempt_log_length(self):
        for game in self.games:
            for record in game.result.possessions:
                row = possession_row(record, game.seed, 0, is_period_opener=False)
                self.assertEqual(row.fga_count, len(record.terminal_result.world.shot_attempt_log))
                self.assertLessEqual(row.fga_made_count, row.fga_count)

    def test_oreb_count_matches_provisional_deltas(self):
        for game in self.games:
            for record in game.result.possessions:
                row = possession_row(record, game.seed, 0, is_period_opener=False)
                self.assertEqual(row.oreb_count, record.provisional_deltas.oreb)

    def test_elapsed_seconds_matches_start_minus_end(self):
        for game in self.games:
            for record in game.result.possessions:
                row = possession_row(record, game.seed, 0, is_period_opener=False)
                self.assertAlmostEqual(row.elapsed_seconds, record.start_game_clock - record.end_game_clock)
                self.assertGreaterEqual(row.elapsed_seconds, 0.0)

    def test_period_opener_flag_forces_period_opener_labels(self):
        record = self.games[0].result.possessions[0]
        row = possession_row(record, 9001, 0, is_period_opener=True)
        self.assertEqual(row.restart_type, "PERIOD_OPENER")
        self.assertEqual(row.restart_source, "PERIOD_OPENER")

    def test_non_opener_uses_real_restart_context(self):
        record = self.games[0].result.possessions[1]
        row = possession_row(record, 9001, 1, is_period_opener=False)
        self.assertEqual(row.restart_type, record.restart_context.restart_type)
        self.assertEqual(row.restart_source, record.restart_context.source)

    def test_terminal_subtype_only_set_for_dreb_and_turnover_reasons(self):
        for game in self.games:
            for record in game.result.possessions:
                row = possession_row(record, game.seed, 0, is_period_opener=False)
                if row.terminal_reason in ("MADE_FG", "FINAL_FT_MADE", "SHOT_CLOCK_VIOLATION", "PERIOD_END"):
                    self.assertIsNone(row.terminal_subtype)
                elif row.terminal_reason == "DEFENSIVE_REBOUND":
                    self.assertIsNotNone(row.terminal_subtype)
                    self.assertTrue(row.terminal_subtype.startswith("DEFENSIVE_REBOUND_AFTER_"))
                elif row.terminal_reason in ("TURNOVER", "OFFENSIVE_FOUL_TURNOVER"):
                    self.assertIsNotNone(row.terminal_subtype)

    def test_clock_by_category_only_contains_real_logged_categories(self):
        for game in self.games:
            for record in game.result.possessions:
                row = possession_row(record, game.seed, 0, is_period_opener=False)
                logged_categories = {e.get("timing_category") for e in record.terminal_result.world.clock_charge_log}
                for category in logged_categories:
                    self.assertIn(category, row.clock_by_category)


class TestGamePossessionRowsSequencing(unittest.TestCase):
    def test_first_possession_of_each_period_is_flagged_as_opener(self):
        games = run_pace_diagnosis_sample(seeds=[9101])
        rows = game_possession_rows(games[0])
        opener_indices = {p.first_possession_sequence - 1 for p in games[0].result.periods}
        for row in rows:
            expected_opener = row.sequence_index in opener_indices
            actual_opener = row.restart_type == "PERIOD_OPENER"
            self.assertEqual(actual_opener, expected_opener,
                              f"possession {row.sequence_index} opener mismatch")

    def test_row_count_matches_total_possessions(self):
        games = run_pace_diagnosis_sample(seeds=[9102, 9103])
        for g in games:
            rows = game_possession_rows(g)
            self.assertEqual(len(rows), g.result.total_possessions)

    def test_all_possession_rows_flattens_every_game(self):
        games = run_pace_diagnosis_sample(seeds=[9104, 9105, 9106])
        flat = all_possession_rows(games)
        self.assertEqual(len(flat), sum(g.result.total_possessions for g in games))


class TestTruePossessionCount(unittest.TestCase):
    def test_matches_detailed_game_result_total_possessions(self):
        games = run_pace_diagnosis_sample(seeds=[9201, 9202])
        expected = sum(g.result.total_possessions for g in games)
        self.assertEqual(true_possession_count(games), expected)


class TestClockLedgerReconciliation(unittest.TestCase):
    def test_reconciliation_gap_is_near_zero_for_a_broad_seed_sample(self):
        """The primary reconciliation proof: every possession's own
        elapsed game-clock time must be fully explained by the sum of its
        own `clock_by_category` ledger, within floating-point tolerance.
        This does NOT assert a specific numeric ledger breakdown -- only
        that nothing is silently missing or double-charged."""
        games = run_pace_diagnosis_sample(seeds=range(9300, 9310))
        rows = all_possession_rows(games)
        self.assertGreater(len(rows), 0)
        max_gap = max(abs(clock_ledger_reconciliation_gap(r)) for r in rows)
        self.assertLess(max_gap, 1e-6, f"clock ledger reconciliation gap too large: {max_gap}")

    def test_reconciliation_gap_reported_per_possession_not_averaged_away(self):
        """A per-possession check, not a sample-mean check -- a single
        possession with a real accounting bug must not be hidden by
        averaging against many correct ones."""
        games = run_pace_diagnosis_sample(seeds=[9401])
        rows = all_possession_rows(games)
        for row in rows:
            gap = clock_ledger_reconciliation_gap(row)
            self.assertLess(abs(gap), 1e-6, f"possession {row.possession_id} (seed {row.game_seed}) "
                                            f"ledger gap {gap}")


class TestBehaviorAndRNGNonInterference(unittest.TestCase):
    def test_pace_diagnosis_module_never_imports_rng(self):
        import detailed_engine_pace_diagnosis as mod
        self.assertNotIn("random", vars(mod))

    def test_pace_diagnosis_module_never_imported_by_simulation_modules(self):
        import inspect
        import possession_orchestrator
        import detailed_game_orchestrator
        import detailed_game
        for mod in (possession_orchestrator, detailed_game_orchestrator, detailed_game):
            src = inspect.getsource(mod)
            self.assertNotIn("detailed_engine_pace_diagnosis", src)

    def test_same_seed_reproduces_identical_outcome_via_diagnosis_runner(self):
        """Calling `run_pace_diagnosis_sample` (which delegates to
        `run_benchmark_sample` -> `simulate_detailed_game`) must produce a
        BIT-IDENTICAL outcome to calling `simulate_detailed_game` directly
        -- confirms this diagnostic module adds no RNG draw, no reordering,
        and no behavioral change of any kind."""
        profiles = {}
        for p in OFF_FIVE:
            profiles[p] = PlayerSimulationProfile.synthetic(p, "HOME")
        for p in DEF_FIVE:
            profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
        cfg = DetailedGameConfig()
        direct = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, profiles, rng_seed=5555, config=cfg)
        via_diagnosis = run_pace_diagnosis_sample(seeds=[5555], config=cfg)[0].result

        self.assertEqual(direct.final_home_score, via_diagnosis.final_home_score)
        self.assertEqual(direct.total_possessions, via_diagnosis.total_possessions)
        self.assertEqual(direct.termination_reason, via_diagnosis.termination_reason)
        self.assertEqual(len(direct.events), len(via_diagnosis.events))
        self.assertEqual(direct.provisional_summary, via_diagnosis.provisional_summary)

    def test_running_diagnosis_twice_on_same_seeds_reproduces_identical_rows(self):
        """Same seed set, two independent runs -- every extracted
        `PossessionRow` field must match exactly (determinism at the
        diagnostic-row level, not just the raw game result level)."""
        games_a = run_pace_diagnosis_sample(seeds=[7001, 7002])
        games_b = run_pace_diagnosis_sample(seeds=[7001, 7002])
        rows_a = all_possession_rows(games_a)
        rows_b = all_possession_rows(games_b)
        self.assertEqual(len(rows_a), len(rows_b))
        for a, b in zip(rows_a, rows_b):
            self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
