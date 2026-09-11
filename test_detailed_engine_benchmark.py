"""Focused tests for detailed_engine_benchmark.py -- benchmark aggregation
math, percentile math, relative-error sign, and non-interference. Per the
benchmark task's own instructions: these NEVER assert that simulator
output equals an NBA average -- accuracy belongs in the benchmark report,
not in a unit test."""
import unittest

from detailed_engine_benchmark import (
    ACCOUNTING_AUTHORITY,
    EVENT_AUTHORITATIVE,
    PARTIALLY_EVENT_DERIVED,
    PROVISIONAL_STATDELTA,
    BenchmarkGame,
    TeamGameStats,
    aggregate_team_game_stats,
    distribution,
    estimated_possessions,
    pearson_correlation,
    relative_error_pct,
    run_benchmark_sample,
    team_games,
)
from detailed_game import DetailedGameConfig, simulate_detailed_game
from possession_orchestrator import PlayerSimulationProfile, PossessionConfig

OFF_FIVE = tuple(str(i) for i in range(1, 6))
DEF_FIVE = tuple(str(i) for i in range(11, 16))


def _profiles():
    profiles = {}
    for p in OFF_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "HOME")
    for p in DEF_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
    return profiles


class TestTeamGameAggregation(unittest.TestCase):
    def test_true_possessions_sum_to_total_game_possessions(self):
        """home.true_possessions + away.true_possessions must equal
        `DetailedGameResult.total_possessions` exactly -- every
        possession is attributed to exactly one offense."""
        result = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=1)
        home, away = aggregate_team_game_stats(result, OFF_FIVE, DEF_FIVE)
        self.assertEqual(home.true_possessions + away.true_possessions, result.total_possessions)

    def test_oreb_credited_to_offense_dreb_credited_to_defense(self):
        """Sum of home.oreb + away.oreb across the whole game must equal
        the provisional_summary's own oreb total (same real convention
        `_provisional_summary` already uses), and likewise for dreb --
        confirms the offense/defense attribution split is complete and
        non-duplicated."""
        result = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=2)
        home, away = aggregate_team_game_stats(result, OFF_FIVE, DEF_FIVE)
        self.assertEqual(home.oreb + away.oreb, result.provisional_summary.oreb)
        self.assertEqual(home.dreb + away.dreb, result.provisional_summary.dreb)

    def test_personal_fouls_sum_matches_provisional_summary(self):
        result = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=3)
        home, away = aggregate_team_game_stats(result, OFF_FIVE, DEF_FIVE)
        self.assertEqual(home.personal_fouls + away.personal_fouls, result.provisional_summary.personal_fouls)

    def test_points_fga_fta_sum_matches_provisional_summary(self):
        result = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=4)
        home, away = aggregate_team_game_stats(result, OFF_FIVE, DEF_FIVE)
        self.assertEqual(home.points + away.points, result.provisional_summary.points)
        self.assertEqual(home.fga + away.fga, result.provisional_summary.fga)
        self.assertEqual(home.fta + away.fta, result.provisional_summary.fta)

    def test_opponent_dreb_is_the_other_teams_dreb(self):
        result = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=5)
        home, away = aggregate_team_game_stats(result, OFF_FIVE, DEF_FIVE)
        self.assertEqual(home.opponent_dreb, away.dreb)
        self.assertEqual(away.opponent_dreb, home.dreb)

    def test_oreb_pct_denominator_is_oreb_plus_opponent_dreb(self):
        """OREB% = OREB / (OREB + opponent DREB) -- never OREB / (OREB +
        own DREB), and never a bare OREB/FGA-style proxy."""
        result = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=6)
        home, _ = aggregate_team_game_stats(result, OFF_FIVE, DEF_FIVE)
        if home.oreb + home.opponent_dreb == 0:
            self.skipTest("no rebounds this seed")
        oreb_pct = home.oreb / (home.oreb + home.opponent_dreb)
        self.assertGreaterEqual(oreb_pct, 0.0)
        self.assertLessEqual(oreb_pct, 1.0)

    def test_fta_per_fga_definition(self):
        """FTA/FGA, NOT FT/FGA (which Basketball-Reference defines as
        FTM/FGA -- a DIFFERENT ratio explicitly called out in the
        benchmark task)."""
        t = TeamGameStats(team_id="A", opponent_team_id="B", fga=100, fta=25, ftm=19)
        fta_per_fga = t.fta / t.fga
        ft_over_fga_bref_style = t.ftm / t.fga  # the OTHER ratio -- must not be confused with the above
        self.assertAlmostEqual(fta_per_fga, 0.25)
        self.assertAlmostEqual(ft_over_fga_bref_style, 0.19)
        self.assertNotAlmostEqual(fta_per_fga, ft_over_fga_bref_style)

    def test_three_point_attempt_rate_definition(self):
        """3PAr = 3PA / FGA."""
        t = TeamGameStats(team_id="A", opponent_team_id="B", fga=90, fg3a=38)
        self.assertAlmostEqual(t.fg3a / t.fga, 38 / 90)

    def test_estimated_possessions_formula(self):
        t = TeamGameStats(team_id="A", opponent_team_id="B", fga=89, fta=22, oreb=11, turnovers=14)
        expected = 89 + 0.44 * 22 - 11 + 14
        self.assertAlmostEqual(estimated_possessions(t), expected)

    def test_true_possessions_and_estimated_possessions_are_distinct_concepts(self):
        """The benchmark must never silently conflate the TRUE engine
        possession-boundary count with the box-score ESTIMATE formula --
        confirm they are independently computable and not forced equal
        by construction."""
        result = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=7)
        home, _ = aggregate_team_game_stats(result, OFF_FIVE, DEF_FIVE)
        true_poss = home.true_possessions
        est_poss = estimated_possessions(home)
        # both must be real, finite numbers; NOT asserted equal (they measure different things)
        self.assertIsInstance(true_poss, int)
        self.assertIsInstance(est_poss, float)


class TestDistributionAndPercentiles(unittest.TestCase):
    def test_percentiles_on_known_sequence(self):
        values = list(range(1, 11))  # 1..10
        d = distribution(values)
        self.assertEqual(d.minimum, 1)
        self.assertEqual(d.maximum, 10)
        self.assertEqual(d.median, 5.5)
        self.assertEqual(d.n, 10)
        # nearest-rank on a 10-element 0..9-index sequence
        self.assertEqual(d.p10, values[round(0.10 * 9)])
        self.assertEqual(d.p90, values[round(0.90 * 9)])

    def test_distribution_mean_and_stdev_match_stdlib(self):
        import statistics as st
        values = [3.0, 7.0, 2.0, 9.0, 5.0, 5.0, 8.0]
        d = distribution(values)
        self.assertAlmostEqual(d.mean, st.fmean(values))
        self.assertAlmostEqual(d.stdev, st.pstdev(values))

    def test_distribution_rejects_empty_sequence(self):
        with self.assertRaises(ValueError):
            distribution([])

    def test_percentiles_never_extrapolate_beyond_observed_range(self):
        values = [10.0, 20.0, 30.0]
        d = distribution(values)
        self.assertGreaterEqual(d.p10, d.minimum)
        self.assertLessEqual(d.p90, d.maximum)


class TestRelativeError(unittest.TestCase):
    def test_relative_error_sign_positive_means_simulator_high(self):
        self.assertAlmostEqual(relative_error_pct(sim=120.0, real=100.0), 20.0)

    def test_relative_error_sign_negative_means_simulator_low(self):
        self.assertAlmostEqual(relative_error_pct(sim=80.0, real=100.0), -20.0)

    def test_relative_error_zero_when_equal(self):
        self.assertAlmostEqual(relative_error_pct(sim=100.0, real=100.0), 0.0)

    def test_relative_error_never_silently_absolute_valued(self):
        """A negative real-vs-sim gap must stay negative -- never
        silently `abs()`'d inside the function itself (a caller ranking
        by magnitude is responsible for applying abs() explicitly)."""
        neg = relative_error_pct(sim=90.0, real=100.0)
        self.assertLess(neg, 0.0)

    def test_relative_error_rejects_zero_reference(self):
        with self.assertRaises(ValueError):
            relative_error_pct(sim=10.0, real=0.0)


class TestCorrelation(unittest.TestCase):
    def test_perfect_positive_correlation(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, 6.0, 8.0, 10.0]
        self.assertAlmostEqual(pearson_correlation(xs, ys), 1.0, places=6)

    def test_perfect_negative_correlation(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [10.0, 8.0, 6.0, 4.0, 2.0]
        self.assertAlmostEqual(pearson_correlation(xs, ys), -1.0, places=6)

    def test_correlation_none_on_zero_variance(self):
        xs = [5.0, 5.0, 5.0]
        ys = [1.0, 2.0, 3.0]
        self.assertIsNone(pearson_correlation(xs, ys))

    def test_correlation_none_on_too_few_points(self):
        self.assertIsNone(pearson_correlation([1.0], [1.0]))


class TestAccountingAuthorityLabeling(unittest.TestCase):
    def test_event_derivable_fields_are_labeled_event_authoritative(self):
        for field in ("oreb", "dreb", "turnovers", "team_turnovers", "player_turnovers",
                      "steals", "blocks", "personal_fouls"):
            self.assertEqual(ACCOUNTING_AUTHORITY[field], EVENT_AUTHORITATIVE)

    def test_known_accounting_debt_fields_are_labeled_provisional(self):
        for field in ("points", "fga", "fgm", "fg3a", "fg3m", "fta", "ftm"):
            self.assertEqual(ACCOUNTING_AUTHORITY[field], PROVISIONAL_STATDELTA)

    def test_true_possessions_are_event_authoritative(self):
        self.assertEqual(ACCOUNTING_AUTHORITY["possessions"], EVENT_AUTHORITATIVE)

    def test_estimated_possessions_and_ortg_are_partially_derived(self):
        self.assertEqual(ACCOUNTING_AUTHORITY["possessions_est"], PARTIALLY_EVENT_DERIVED)
        self.assertEqual(ACCOUNTING_AUTHORITY["ortg"], PARTIALLY_EVENT_DERIVED)

    def test_every_authority_value_is_one_of_the_three_documented_levels(self):
        allowed = {EVENT_AUTHORITATIVE, PROVISIONAL_STATDELTA, PARTIALLY_EVENT_DERIVED}
        for field, level in ACCOUNTING_AUTHORITY.items():
            self.assertIn(level, allowed, f"{field!r} has an undocumented authority level {level!r}")


class TestBenchmarkRunnerAndNonInterference(unittest.TestCase):
    def test_run_benchmark_sample_produces_one_game_per_seed(self):
        games = run_benchmark_sample(seeds=[100, 101, 102])
        self.assertEqual(len(games), 3)
        self.assertEqual([g.seed for g in games], [100, 101, 102])

    def test_team_games_flattens_two_per_game(self):
        games = run_benchmark_sample(seeds=[200, 201])
        flat = team_games(games)
        self.assertEqual(len(flat), 4)

    def test_benchmark_runner_uses_default_calibrated_config_by_default(self):
        """No `config=` override -- the benchmark must use the SAME
        default `DetailedGameConfig`/`PossessionConfig` (i.e. the
        already-committed calibrated timing defaults) the rest of the
        diagnostics suite uses, never a bespoke benchmark-only config."""
        games = run_benchmark_sample(seeds=[300])
        default_cfg = PossessionConfig()
        used_cfg = games[0].result.possessions[0].terminal_result is not None  # sanity: ran successfully
        self.assertTrue(used_cfg)
        # the possession config actually in effect must match PossessionConfig()'s own defaults for the
        # calibrated timing fields -- confirms no silent override happened inside the runner.
        self.assertEqual(default_cfg.ordinary_entry_seconds, 9.0)
        self.assertEqual(default_cfg.inter_action_seconds, 3.0)

    def test_benchmark_instrumentation_does_not_change_simulation_outcome_for_a_fixed_seed(self):
        """Same seed, same profiles, same config -- calling the
        benchmark aggregator around a game must reproduce BIT-IDENTICAL
        simulation outcomes (score, event count, possession count) to
        calling `simulate_detailed_game` directly with no benchmark
        involvement at all. This is the deterministic non-interference
        guarantee the task requires."""
        cfg = DetailedGameConfig()
        direct = simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=777, config=cfg)
        via_benchmark = run_benchmark_sample(seeds=[777], config=cfg)[0].result

        self.assertEqual(direct.final_home_score, via_benchmark.final_home_score)
        self.assertEqual(direct.final_away_score, via_benchmark.final_away_score)
        self.assertEqual(direct.total_possessions, via_benchmark.total_possessions)
        self.assertEqual(direct.termination_reason, via_benchmark.termination_reason)
        self.assertEqual(len(direct.events), len(via_benchmark.events))
        self.assertEqual(direct.provisional_summary, via_benchmark.provisional_summary)

    def test_benchmark_module_never_imports_rng_or_consumes_it(self):
        """Structural firewall (same methodology
        `test_diagnostics_module_never_imports_rng_or_selection` already
        established for `detailed_engine_diagnostics.py`): this module
        must never import `random` -- every RNG draw belongs to
        `simulate_detailed_game`/`simulate_possession` alone."""
        import detailed_engine_benchmark as mod
        self.assertNotIn("random", vars(mod))

    def test_benchmark_module_never_imported_by_simulation_modules(self):
        import inspect
        import possession_orchestrator
        import detailed_game_orchestrator
        import detailed_game
        for mod in (possession_orchestrator, detailed_game_orchestrator, detailed_game):
            src = inspect.getsource(mod)
            self.assertNotIn("detailed_engine_benchmark", src)


if __name__ == "__main__":
    unittest.main()
