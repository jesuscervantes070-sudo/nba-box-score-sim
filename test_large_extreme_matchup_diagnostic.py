"""Focused tests for LARGE / EXTREME MATCHUP COMPRESSION DIAGNOSTIC V1
(large_extreme_matchup_diagnostic.py)."""
import copy
import unittest

import large_extreme_matchup_diagnostic as led
import margin_compression_diagnostic as mcd
import historical_predictive_backtest as hpb
import historical_game_snapshot as hgs
from possession_orchestrator import PlayerSimulationProfile

SAMPLE_PROBE = None


def setUpModule():
    global SAMPLE_PROBE
    buckets = led.select_large_extreme_games(n_per_bucket=3)
    SAMPLE_PROBE = {"large": buckets["large"], "extreme": buckets["extreme"]}


class TestDeterministicLargeExtremeSample(unittest.TestCase):
    """A. Deterministic large/extreme sample."""

    def test_a_selection_is_deterministic(self):
        self.assertEqual(led.select_large_extreme_games(n_per_bucket=3), led.select_large_extreme_games(n_per_bucket=3))

    def test_a_buckets_respect_thresholds(self):
        dev_games = mcd.load_or_create_development_sample()
        records = mcd.build_dev_team_records(dev_games)
        by_game = {}
        for r in records:
            by_game.setdefault(r["game_id"], abs(r["strength_diff"]))
        for gid in SAMPLE_PROBE["large"]:
            self.assertGreaterEqual(by_game[gid], led.LARGE_MIN)
            self.assertLess(by_game[gid], led.LARGE_MAX)
        for gid in SAMPLE_PROBE["extreme"]:
            self.assertGreaterEqual(by_game[gid], led.EXTREME_MIN)

    def test_a_no_overlap_between_buckets(self):
        self.assertEqual(set(SAMPLE_PROBE["large"]) & set(SAMPLE_PROBE["extreme"]), set())


class TestHoldoutExclusion(unittest.TestCase):
    """B. Holdout exclusion."""

    def test_b_large_extreme_sample_disjoint_from_holdout(self):
        all_ids = set(SAMPLE_PROBE["large"]) | set(SAMPLE_PROBE["extreme"])
        holdout = set(hpb.load_or_create_holdout())
        self.assertEqual(all_ids & holdout, set())

    def test_b_large_extreme_sample_subset_of_dev_sample(self):
        all_ids = set(SAMPLE_PROBE["large"]) | set(SAMPLE_PROBE["extreme"])
        dev = set(mcd.load_or_create_development_sample())
        self.assertTrue(all_ids <= dev)


class TestTopFiveVsFullRotation(unittest.TestCase):
    """C. Top-five vs full-rotation comparison."""

    def test_c_composite_gap_rows_have_required_keys(self):
        rows = led.scoring_composite_gap_rows(SAMPLE_PROBE["large"][:1], n_sims=5)
        self.assertTrue(rows)
        for key in ("top5_scoring_composite_gap", "full_rotation_scoring_composite_gap",
                    "actual_margin_strong_perspective", "simulated_mean_margin_strong_perspective"):
            self.assertIn(key, rows[0])

    def test_c_correlation_report_shape(self):
        rows = led.scoring_composite_gap_rows(SAMPLE_PROBE["large"][:2], n_sims=5)
        report = led.scoring_composite_correlations(rows)
        self.assertIn("corr_top5_gap_vs_actual_margin", report)
        self.assertIn("corr_full_rotation_gap_vs_actual_margin", report)


class TestBenchQualityCalculation(unittest.TestCase):
    """D. Bench-quality calculation."""

    def test_d_bench_quality_report_has_all_groups(self):
        report = led.bench_quality_strong_vs_weak(SAMPLE_PROBE["large"][:2])
        for group in ("scoring", "playmaking", "rebounding", "defense", "role"):
            self.assertIn(group, report)
            self.assertIn("strong_bench_mean", report[group])
            self.assertIn("weak_bench_mean", report[group])


class TestEliteStackingSyntheticSetup(unittest.TestCase):
    """E. Elite-stacking synthetic setup."""

    def test_e_stack_team_zero_stacked_is_all_average(self):
        profiles = led._stack_team(("1", "2", "3", "4", "5"), "HOME", 0, led.ELITE_SCORING_KWARGS)
        avg = PlayerSimulationProfile.synthetic("1", "HOME")
        for pid, profile in profiles.items():
            self.assertEqual(profile.three_point_shrunk_rate, avg.three_point_shrunk_rate)

    def test_e_stack_team_first_k_carry_elite_kwargs(self):
        profiles = led._stack_team(("1", "2", "3", "4", "5"), "HOME", 2, led.ELITE_SCORING_KWARGS)
        self.assertEqual(profiles["1"].three_point_shrunk_rate, led.ELITE_SCORING_KWARGS["three_point_shrunk_rate"])
        self.assertEqual(profiles["2"].three_point_shrunk_rate, led.ELITE_SCORING_KWARGS["three_point_shrunk_rate"])
        avg = PlayerSimulationProfile.synthetic("3", "HOME")
        self.assertEqual(profiles["3"].three_point_shrunk_rate, avg.three_point_shrunk_rate)

    def test_e_stacking_progression_shape(self):
        result = led.stacking_progression(led.ELITE_SCORING_KWARGS, "elite_smoke", n_sims=8, stack_levels=(0, 1))
        self.assertEqual(len(result["progression"]), 2)
        self.assertEqual(result["progression"][0]["n_stacked"], 0)
        self.assertEqual(len(result["marginal_gain_per_additional_stacked_player"]), 1)


class TestWeakStackingSyntheticSetup(unittest.TestCase):
    """F. Weak-stacking synthetic setup."""

    def test_f_weak_kwargs_are_below_synthetic_average(self):
        avg = PlayerSimulationProfile.synthetic("1", "HOME")
        for field, value in led.WEAK_SCORING_KWARGS.items():
            self.assertLess(value, getattr(avg, field))

    def test_f_weak_stacking_progression_shape(self):
        result = led.stacking_progression(led.WEAK_SCORING_KWARGS, "weak_smoke", n_sims=8, stack_levels=(0, 1))
        self.assertEqual(len(result["progression"]), 2)


class TestDeterministicCounterfactuals(unittest.TestCase):
    """G. Deterministic counterfactuals."""

    def test_g_sequential_replacement_is_deterministic(self):
        game_id = SAMPLE_PROBE["large"][0]
        snap = hgs.build_historical_game_snapshot(game_id, led.SEASON, led.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        home_id, _, _, _, _ = hgs.snapshot_to_engine_input(snap)
        a = led.sequential_replacement_progression(game_id, home_id, n_sims=6)
        b = led.sequential_replacement_progression(game_id, home_id, n_sims=6)
        self.assertEqual(a["progression"], b["progression"])

    def test_g_full_team_average_replacement_is_deterministic(self):
        game_id = SAMPLE_PROBE["large"][0]
        a = led.full_team_average_replacement(game_id, n_sims=6)
        b = led.full_team_average_replacement(game_id, n_sims=6)
        self.assertEqual(a["baseline_real_mean_margin"], b["baseline_real_mean_margin"])
        self.assertEqual(a["both_average_mean_margin"], b["both_average_mean_margin"])


class TestNoMutationOfProductionProfiles(unittest.TestCase):
    """H. No mutation of production profiles."""

    def test_h_sequential_replacement_does_not_mutate_snapshot_profiles(self):
        game_id = SAMPLE_PROBE["large"][0]
        snap_before = hgs.build_historical_game_snapshot(game_id, led.SEASON, led.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        _, _, _, _, profiles_before = hgs.snapshot_to_engine_input(snap_before)
        snapshot_profiles_copy = copy.deepcopy(profiles_before)

        home_id, _, _, _, _ = hgs.snapshot_to_engine_input(snap_before)
        led.sequential_replacement_progression(game_id, home_id, n_sims=6)

        snap_after = hgs.build_historical_game_snapshot(game_id, led.SEASON, led.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        _, _, _, _, profiles_after = hgs.snapshot_to_engine_input(snap_after)
        self.assertEqual(profiles_after, snapshot_profiles_copy)


class TestSerialization(unittest.TestCase):
    """I. Serialization."""

    def test_i_sample_round_trips_through_json(self):
        import json
        sample = {"large": SAMPLE_PROBE["large"], "extreme": SAMPLE_PROBE["extreme"]}
        self.assertEqual(json.loads(json.dumps(sample, sort_keys=True)), sample)

    def test_i_category_gap_report_round_trips_through_json(self):
        import json
        report = led.category_gap_report(SAMPLE_PROBE["large"][:1])
        json.loads(json.dumps(report, default=str))


class TestNoEngineBehaviorChanges(unittest.TestCase):
    """J. No engine behavior changes -- this diagnostic module must never import-time-mutate any
    engine constant, and repeated identical calls must be byte-identical (no hidden global state)."""

    def test_j_synthetic_defaults_unchanged(self):
        avg = PlayerSimulationProfile.synthetic("1", "HOME")
        self.assertEqual(avg.three_point_shrunk_rate, 0.37)
        self.assertEqual(avg.rim_finishing_shrunk_rate, 0.67)

    def test_j_repeated_stacking_call_is_identical(self):
        a = led.stacking_progression(led.ELITE_SCORING_KWARGS, "det_check", n_sims=6, stack_levels=(0, 2))
        b = led.stacking_progression(led.ELITE_SCORING_KWARGS, "det_check", n_sims=6, stack_levels=(0, 2))
        self.assertEqual(a["progression"], b["progression"])


if __name__ == "__main__":
    unittest.main()
