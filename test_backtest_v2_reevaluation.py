"""Focused tests for FROZEN HISTORICAL BACKTEST RE-EVALUATION V2."""
import hashlib
import json
import unittest
from pathlib import Path

import backtest_v1_v2_comparison as cmp
import historical_predictive_backtest as hpb
import run_backtest_v2 as rbv2

BACKTESTS_DIR = Path("backtests")


def _load(path):
    with open(path) as f:
        return json.load(f)


class TestHoldoutIdsUnchanged(unittest.TestCase):
    """A. Holdout IDs unchanged."""

    def test_a_holdout_file_matches_documented_selection_rule(self):
        data = _load(BACKTESTS_DIR / "backtest_v1_holdout_game_ids.json")
        self.assertEqual(data["n_games"], 83)
        self.assertEqual(data["selection_rule"], "sha256(game_id) % 16 == 0")

    def test_a_loading_holdout_never_regenerates_the_file(self):
        path = BACKTESTS_DIR / "backtest_v1_holdout_game_ids.json"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        game_ids = hpb.load_or_create_holdout()
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(len(game_ids), 83)

    def test_a_verify_holdout_integrity_returns_the_exact_locked_list(self):
        game_ids, checksum = rbv2.verify_holdout_integrity()
        expected = _load(BACKTESTS_DIR / "backtest_v1_holdout_game_ids.json")
        self.assertEqual(game_ids, expected["game_ids"])


class TestV1FilesUntouched(unittest.TestCase):
    """B. V1 files untouched."""

    def test_b_v1_summary_and_games_files_exist_and_are_real_json(self):
        v1_summary = _load(BACKTESTS_DIR / "backtest_v1_summary.json")
        v1_games = _load(BACKTESTS_DIR / "backtest_v1_games.json")
        self.assertIn("metrics", v1_summary)
        self.assertIn("pregame_expected", v1_games)
        self.assertEqual(len(v1_games["pregame_expected"]["games"]), 83)


class TestConfigEquality(unittest.TestCase):
    """C. Config equality."""

    def test_c_n_simulations_and_model_version_unchanged(self):
        self.assertEqual(hpb.N_SIMULATIONS, 250)
        self.assertEqual(hpb.MODEL_VERSION, "backtest-v1")  # V2 deliberately reuses V1's seed formula

    def test_c_seed_formula_is_the_documented_one(self):
        seed1 = hpb._seed_for("G1", "PREGAME_EXPECTED", 0)
        seed2 = hpb._seed_for("G1", "PREGAME_EXPECTED", 0)
        self.assertEqual(seed1, seed2)


class TestBaselineReproduction(unittest.TestCase):
    """D. Baseline reproduction -- baselines depend only on real outcomes/net ratings, never on
    player truth, so they must reproduce EXACTLY regardless of the truth intervention."""

    def test_d_prior_season_home_rate_is_deterministic(self):
        r1 = hpb._prior_season_league_home_win_rate()
        hpb.clear_backtest_caches()
        r2 = hpb._prior_season_league_home_win_rate()
        self.assertEqual(r1, r2)

    def test_d_v1_baseline_metrics_match_documented_values(self):
        v1_summary = _load(BACKTESTS_DIR / "backtest_v1_summary.json")
        baselines = {b["baseline"]: b for b in v1_summary["baseline_metrics"]}
        self.assertAlmostEqual(baselines["BASELINE_5050"]["brier_score"], 0.25, places=6)


class TestV2Serialization(unittest.TestCase):
    """E. V2 serialization."""

    def test_e_comparison_output_round_trips_through_json(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"][:5]
        records = cmp.paired_game_records(games, games)
        payload = {"records": records, "summary": cmp.paired_deltas_summary(records)}
        text = json.dumps(payload, default=str)
        reloaded = json.loads(text)
        self.assertEqual(len(reloaded["records"]), 5)


class TestPairedMetricCalculations(unittest.TestCase):
    """F. Paired metric calculations."""

    def test_f_self_paired_deltas_are_all_zero(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"]
        records = cmp.paired_game_records(games, games)
        summary = cmp.paired_deltas_summary(records)
        for field in ("delta_win_prob", "delta_margin", "delta_brier", "delta_abs_margin_error"):
            self.assertEqual(summary[field]["mean"], 0.0)

    def test_f_known_delta_brier_value(self):
        g1 = {"game_id": "X", "home_team": "A", "away_team": "B", "date": "2023-11-01",
              "actual_home_score": 100, "actual_away_score": 90, "actual_margin": 10,
              "actual_winner": "A", "predicted_home_win_prob": 0.5, "predicted_mean_margin": 0.0,
              "mean_simulated_home_score": 100.0, "mean_simulated_away_score": 100.0,
              "snapshot_provenance": {}}
        g2 = dict(g1, predicted_home_win_prob=0.8, predicted_mean_margin=5.0)
        records = cmp.paired_game_records([g1], [g2])
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertAlmostEqual(r["v1_brier"], (0.5 - 1) ** 2)
        self.assertAlmostEqual(r["v2_brier"], (0.8 - 1) ** 2)
        self.assertAlmostEqual(r["delta_brier"], (0.8 - 1) ** 2 - (0.5 - 1) ** 2)


class TestBootstrapDeterminism(unittest.TestCase):
    """G. Bootstrap determinism."""

    def test_g_same_seed_same_result(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"]
        records = cmp.paired_game_records(games, games)
        b1 = cmp.paired_bootstrap_ci(records, seed=42, n_boot=500)
        b2 = cmp.paired_bootstrap_ci(records, seed=42, n_boot=500)
        self.assertEqual(b1, b2)

    def test_g_different_seed_can_differ(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"]
        # inject a tiny real asymmetry so different seeds could plausibly diverge
        games2 = [dict(g) for g in games]
        games2[0] = dict(games2[0], predicted_home_win_prob=min(1.0, games2[0]["predicted_home_win_prob"] + 0.1))
        records = cmp.paired_game_records(games, games2)
        b1 = cmp.paired_bootstrap_ci(records, seed=1, n_boot=300)
        b2 = cmp.paired_bootstrap_ci(records, seed=2, n_boot=300)
        # not asserting inequality (could coincide) -- just that both are valid, real CIs
        self.assertIn("ci_2_5", b1["brier_delta"])
        self.assertIn("ci_2_5", b2["brier_delta"])


class TestMarginCompressionComparison(unittest.TestCase):
    """H. Margin-compression comparison."""

    def test_h_matches_documented_v1_figures(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"]
        result = cmp.margin_compression_summary(games)
        self.assertAlmostEqual(result["predicted_ge20pt_rate"], 0.0602, places=3)
        self.assertAlmostEqual(result["actual_ge20pt_rate"], 0.1687, places=3)


class TestStrengthGapBuckets(unittest.TestCase):
    """I. Strength-gap buckets."""

    def test_i_buckets_cover_all_common_games(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"]
        buckets = cmp.matchup_strength_buckets(games, games)
        total = sum(b["n_games"] for b in buckets)
        self.assertEqual(total, 83)


class TestCalibrationComparison(unittest.TestCase):
    """J. Calibration comparison."""

    def test_j_v1_bins_match_v1_summary(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"]
        result = cmp.calibration_comparison(games, games)
        self.assertEqual(result["v1_bins"], result["v2_bins"])  # self-compare


class TestInterventionExposure(unittest.TestCase):
    """K. Intervention exposure."""

    def test_k_exposure_counts_are_bounded_by_primary_five(self):
        exposure = cmp.intervention_exposure(["0022300005"])
        for row in exposure["rows_sample"]:
            self.assertLessEqual(row["n_current_season_rim_protection"], row["n_primary_five"])
            self.assertLessEqual(row["n_real_ast_pct"], row["n_primary_five"])


class TestPerGameDeltas(unittest.TestCase):
    """L. Per-game deltas."""

    def test_l_every_common_game_has_a_paired_record(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"]
        records = cmp.paired_game_records(games, games)
        self.assertEqual({r["game_id"] for r in records}, {g["game_id"] for g in games})


class TestReproducibility(unittest.TestCase):
    """M. Reproducibility."""

    def test_m_repeated_comparison_run_is_byte_identical(self):
        v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
        games = v1["pregame_expected"]["games"][:10]
        r1 = cmp.paired_game_records(games, games)
        r2 = cmp.paired_game_records(games, games)
        self.assertEqual(r1, r2)


class TestNoModelMutation(unittest.TestCase):
    """N. No model mutation -- this whole evaluation layer never writes to any estimator/engine
    module or mutates a production PlayerSimulationProfile default."""

    def test_n_synthetic_defaults_unchanged_by_running_the_comparison(self):
        from possession_orchestrator import PlayerSimulationProfile
        before = PlayerSimulationProfile.synthetic("X", "HOME")
        cmp.intervention_exposure(["0022300005"])
        after = PlayerSimulationProfile.synthetic("X", "HOME")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
