"""Integrity and helper tests for FROZEN HISTORICAL BACKTEST RE-EVALUATION V3."""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

import backtest_v1_v2_comparison as cmp
import historical_predictive_backtest as hpb
import run_v2_v3_comparison as v23

B = Path("backtests")


def _load(name):
    with open(B / name) as f:
        return json.load(f)


class TestHoldoutAndPriorArtifactsUntouched(unittest.TestCase):
    def test_holdout_file_is_the_frozen_83_games(self):
        data = _load("backtest_v1_holdout_game_ids.json")
        self.assertEqual(data["n_games"], 83)
        self.assertEqual(len(data["game_ids"]), 83)
        self.assertEqual(data["game_ids"], sorted(data["game_ids"]))
        self.assertEqual(hashlib.sha256((B / "backtest_v1_holdout_game_ids.json").read_bytes()).hexdigest(),
                         _load("backtest_v3_summary.json")["holdout_file_sha256"])

    def test_v3_used_the_same_holdout_checksum_as_v2(self):
        self.assertEqual(_load("backtest_v3_summary.json")["holdout_file_sha256"],
                         _load("backtest_v2_summary.json")["holdout_file_sha256"])

    def test_v1_and_v2_result_files_are_unmodified(self):
        result = subprocess.run(["git", "diff", "--quiet", "--", "backtests/backtest_v1_games.json",
                                 "backtests/backtest_v1_summary.json", "backtests/backtest_v2_games.json",
                                 "backtests/backtest_v2_summary.json", "backtests/backtest_v1_v2_comparison.json",
                                 "backtests/backtest_v1_holdout_game_ids.json"])
        self.assertEqual(result.returncode, 0)


class TestV3Serialization(unittest.TestCase):
    def test_summary_config_matches_frozen_protocol(self):
        s = _load("backtest_v3_summary.json")
        self.assertEqual(s["n_sims_per_game"], hpb.N_SIMULATIONS)
        self.assertEqual(s["model_version"], hpb.MODEL_VERSION)
        self.assertEqual((s["n_attempted"], s["n_succeeded"], s["n_skipped"]), (83, 83, 0))

    def test_games_file_has_both_modes_in_holdout_order(self):
        games = _load("backtest_v3_games.json")
        holdout = _load("backtest_v1_holdout_game_ids.json")["game_ids"]
        self.assertEqual(sorted(g["game_id"] for g in games["pregame_expected"]["games"]), holdout)
        self.assertEqual(sorted(g["game_id"] for g in games["oracle_participants"]["games"]), holdout)

    def test_seed_policy_is_unchanged(self):
        expected = int(hashlib.sha256(b"backtest-v1|PREGAME_EXPECTED|0022300008|0").hexdigest()[:16], 16)
        self.assertEqual(hpb._seed_for("0022300008", "PREGAME_EXPECTED", 0), expected)


class TestPairedComparison(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = _load("backtest_v2_games.json")["pregame_expected"]["games"]
        cls.v3 = _load("backtest_v3_games.json")["pregame_expected"]["games"]

    def test_same_games_same_actual_outcomes(self):
        self.assertEqual([g["game_id"] for g in self.v2], [g["game_id"] for g in self.v3])
        for a, b in zip(self.v2, self.v3):
            self.assertEqual((a["actual_home_score"], a["actual_away_score"]), (b["actual_home_score"], b["actual_away_score"]))

    def test_baselines_reproduce_exactly(self):
        report = v23.baseline_reproduction(_load("backtest_v2_summary.json"), _load("backtest_v3_summary.json"))
        self.assertEqual(len(report), 3)
        self.assertTrue(all(r["identical"] for r in report.values()))

    def test_paired_records_and_relabel(self):
        raw = cmp.paired_game_records(self.v2, self.v3)
        self.assertEqual(len(raw), 83)
        rec = v23.relabel(raw[0])
        self.assertIn("v2_brier", rec)
        self.assertIn("v3_brier", rec)
        self.assertNotIn("v1_brier", rec)
        self.assertAlmostEqual(rec["delta_brier"], rec["v3_brier"] - rec["v2_brier"])

    def test_bootstrap_is_deterministic(self):
        raw = cmp.paired_game_records(self.v2, self.v3)
        self.assertEqual(cmp.paired_bootstrap_ci(raw, n_boot=200), cmp.paired_bootstrap_ci(raw, n_boot=200))

    def test_comparison_file_matches_recomputed_headline(self):
        table = _load("backtest_v2_v3_comparison.json")["headline_metrics_table"]
        head, _ = v23.headline(self.v3, "v3")
        self.assertAlmostEqual(table["brier_score"]["v3"], head["brier_score"])
        self.assertAlmostEqual(table["margin_mae"]["v3"], head["margin_mae"])


class TestMetricHelpers(unittest.TestCase):
    def test_headline_on_tiny_perfect_predictor(self):
        games = [{"game_id": str(i), "home_team": "H", "away_team": "A", "date": "2023-11-01",
                  "actual_winner": "H", "predicted_home_win_prob": 1.0, "predicted_mean_margin": 10.0,
                  "actual_margin": 10, "mean_simulated_home_score": 110.0, "mean_simulated_away_score": 100.0,
                  "actual_home_score": 110, "actual_away_score": 100} for i in range(3)]
        head, _ = v23.headline(games, "x")
        self.assertEqual(head["accuracy"], 1.0)
        self.assertAlmostEqual(head["brier_score"], 0.0)
        self.assertAlmostEqual(head["margin_mae"], 0.0)
        self.assertAlmostEqual(head["combined_score_mae"], 0.0)


if __name__ == "__main__":
    unittest.main()
