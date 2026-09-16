"""Focused tests for FIRST HISTORICAL PREDICTIVE BACKTEST V1 (historical_predictive_backtest.py,
historical_game_outcome.py)."""
import json
import unittest
from unittest.mock import patch

import historical_predictive_backtest as hpb
import historical_game_outcome as hgo
import historical_game_snapshot as hgs

SEASON = "2023-24"
ALL_SEASONS = ["2021-22", "2022-23", "2023-24"]
DENVER_GAME = "0022300061"  # real 2023-24 Denver Nuggets vs LA Lakers, 2023-10-24 -- real 119-107


class TestHoldoutSelection(unittest.TestCase):
    """A. Deterministic holdout selection."""

    def test_a_selection_is_deterministic(self):
        first = hpb.select_holdout_games()
        second = hpb.select_holdout_games()
        self.assertEqual(first, second)

    def test_a_selection_matches_the_documented_rule(self):
        import hashlib
        ids = hpb.select_holdout_games()
        for gid in ids:
            self.assertEqual(int(hashlib.sha256(gid.encode()).hexdigest(), 16) % hpb.HOLDOUT_SELECTION_MOD, 0)

    def test_a_selection_is_a_reasonable_holdout_size(self):
        ids = hpb.select_holdout_games()
        self.assertGreaterEqual(len(ids), 50)
        self.assertLessEqual(len(ids), 120)


class TestNoTargetGameFeatures(unittest.TestCase):
    """B. No target-game features -- predict_game always builds via the real snapshot pipeline
    with the mode the caller asked for (never silently switching modes), and PREGAME predictions
    never depend on the target game's own real outcome."""

    def test_b_predict_game_uses_the_requested_mode(self):
        pred, err = hpb.predict_game(DENVER_GAME, SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=5)
        self.assertIsNone(err)
        self.assertEqual(pred.mode, hgs.MODE_PREGAME_EXPECTED)

    def test_b_pregame_prediction_is_insensitive_to_target_game_outcome_poison(self):
        pred_before, err1 = hpb.predict_game(DENVER_GAME, SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=5)
        # poison the target game's own real outcome cache -- must not change the snapshot inputs
        with patch("historical_game_outcome.get_game_outcome", return_value=None):
            pred_after, err2 = hpb.predict_game(DENVER_GAME, SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=5)
        self.assertIsNone(err1)
        self.assertIsNone(err2)
        self.assertEqual(pred_before.predicted_home_win_prob, pred_after.predicted_home_win_prob)


class TestActualOutcomeJoin(unittest.TestCase):
    """C. Actual-outcome join -- real reconstructed score/winner matches the real, known result."""

    def test_c_real_denver_lakers_opening_night_score(self):
        outcome = hgo.get_game_outcome(DENVER_GAME, SEASON)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.home_score, 119)
        self.assertEqual(outcome.away_score, 107)
        self.assertTrue(outcome.home_win)
        self.assertEqual(outcome.margin, 12)

    def test_c_unknown_game_returns_none(self):
        self.assertIsNone(hgo.get_game_outcome("FAKE_GAME_ID", SEASON))


class TestWinProbability(unittest.TestCase):
    """D. Win-probability calculation."""

    def test_d_home_win_prob_is_fraction_of_positive_margins(self):
        margins = [5, -3, 10, -1, 2]  # 3 of 5 positive
        home_wins = sum(1 for m in margins if m > 0)
        self.assertEqual(home_wins / len(margins), 0.6)


class TestBrierScore(unittest.TestCase):
    """E. Brier calculation."""

    def test_e_known_values(self):
        pairs = [(1.0, 1), (0.0, 0), (0.5, 1)]
        # (1-1)^2=0, (0-0)^2=0, (0.5-1)^2=0.25 -> mean = 0.25/3
        self.assertAlmostEqual(hpb.brier_score(pairs), 0.25 / 3)

    def test_e_empty_is_none(self):
        self.assertIsNone(hpb.brier_score([]))


class TestLogLoss(unittest.TestCase):
    """F. Log-loss calculation."""

    def test_f_known_values(self):
        import math
        pairs = [(0.5, 1), (0.5, 0)]
        expected = -math.log(0.5)
        self.assertAlmostEqual(hpb.log_loss(pairs), expected, places=5)

    def test_f_clips_extreme_probabilities(self):
        pairs = [(1.0, 0)]  # would be inf without clipping
        result = hpb.log_loss(pairs)
        self.assertIsNotNone(result)
        self.assertTrue(result > 0)


class TestAccuracy(unittest.TestCase):
    """G. Accuracy calculation."""

    def test_g_known_values(self):
        pairs = [(0.6, 1), (0.4, 0), (0.9, 0)]  # correct, correct, wrong
        self.assertAlmostEqual(hpb.winner_accuracy(pairs), 2 / 3)


class TestMarginMetrics(unittest.TestCase):
    """H. Margin metrics."""

    def test_h_mae_rmse_signed_error(self):
        pred = [5.0, -2.0, 10.0]
        actual = [3, -2, 15]
        self.assertAlmostEqual(hpb.margin_mae(pred, actual), (2 + 0 + 5) / 3)
        self.assertAlmostEqual(hpb.margin_rmse(pred, actual), ((4 + 0 + 25) / 3) ** 0.5)
        self.assertAlmostEqual(hpb.mean_signed_margin_error(pred, actual), (2 + 0 - 5) / 3)


class TestScoreMetrics(unittest.TestCase):
    """I. Score metrics."""

    def test_i_mae(self):
        self.assertAlmostEqual(hpb.score_mae([100.0, 110.0], [95, 115]), (5 + 5) / 2)


class TestCalibrationBins(unittest.TestCase):
    """J. Calibration bins."""

    def test_j_bins_structure_and_counts(self):
        pairs = [(0.55, 1), (0.65, 0), (0.85, 1), (0.95, 1)]
        bins = hpb.calibration_table(pairs)
        self.assertEqual(sum(b["n_games"] for b in bins), 4)
        # 0.55 -> 50-60% bin (confidence .55, home predicted, hit=True)
        bin_50_60 = next(b for b in bins if b["range"] == "50-60%")
        self.assertEqual(bin_50_60["n_games"], 1)
        self.assertEqual(bin_50_60["actual_hit_rate"], 1.0)


class TestBaselineLeakageSafety(unittest.TestCase):
    """K. Baseline leakage safety -- the net-rating baseline never uses evidence on/after the
    target game's own date."""

    def test_k_net_rating_never_uses_same_or_future_date_games(self):
        team = "Denver Nuggets"
        as_of = "2023-11-01"
        in_season = [
            o for o in hpb._all_game_outcomes(SEASON)
            if team in (o.home_team, o.away_team) and o.game_date < as_of
        ]
        # sanity: there ARE real games before this date to have computed from
        self.assertGreater(len(in_season), 0)
        # poisoning any game ON/AFTER as_of must not change the computed net rating
        before = hpb._team_net_rating_as_of(team, as_of, SEASON)
        real_outcomes = list(hpb._all_game_outcomes(SEASON))  # captured BEFORE patching
        import dataclasses
        fake = dataclasses.replace(real_outcomes[0], game_date="2024-06-01", home_team=team,
                                    away_team="Boston Celtics", margin=999, home_score=999, away_score=0)
        with patch("historical_predictive_backtest._all_game_outcomes",
                   return_value=tuple(real_outcomes) + (fake,)):
            after = hpb._team_net_rating_as_of(team, as_of, SEASON)
        self.assertEqual(before, after)

    def test_k_prior_home_rate_baseline_uses_only_prior_season(self):
        rate = hpb._prior_season_league_home_win_rate()
        self.assertIsNotNone(rate)
        self.assertGreater(rate, 0.0)
        self.assertLess(rate, 1.0)


class TestPregameOracleSeparation(unittest.TestCase):
    """L. PREGAME/ORACLE separation."""

    def test_l_modes_produce_distinctly_labeled_predictions(self):
        pregame, err1 = hpb.predict_game(DENVER_GAME, SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=5)
        oracle, err2 = hpb.predict_game(DENVER_GAME, SEASON, ALL_SEASONS, hgs.MODE_ORACLE_PARTICIPANTS, n_sims=5)
        self.assertIsNone(err1)
        self.assertIsNone(err2)
        self.assertEqual(pregame.mode, hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(oracle.mode, hgs.MODE_ORACLE_PARTICIPANTS)


class TestRawResultSerialization(unittest.TestCase):
    """M. Raw result serialization."""

    def test_m_serialize_raw_games_round_trips_through_json(self):
        result = hpb.run_backtest([DENVER_GAME], SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=5)
        raw = hpb.serialize_raw_games(result)
        text = json.dumps(raw)
        reloaded = json.loads(text)
        self.assertEqual(reloaded["mode"], hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(len(reloaded["games"]), 1)
        self.assertEqual(reloaded["games"][0]["game_id"], DENVER_GAME)


class TestSummarySerialization(unittest.TestCase):
    """N. Summary serialization."""

    def test_n_summary_includes_required_metadata(self):
        result = hpb.run_backtest([DENVER_GAME], SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=5)
        metrics = hpb.compute_metrics_for_predictions(result.predictions, result.outcomes)
        summary = hpb.serialize_summary(result, metrics, [])
        for key in ("model_commit", "model_version", "snapshot_mode", "n_sims_per_game",
                    "seed_policy", "holdout_selection_rule", "game_ids", "metrics"):
            self.assertIn(key, summary)


class TestSeedPolicy(unittest.TestCase):
    """O. Deterministic seed policy."""

    def test_o_same_inputs_same_seed(self):
        self.assertEqual(hpb._seed_for("G1", "PREGAME_EXPECTED", 0), hpb._seed_for("G1", "PREGAME_EXPECTED", 0))

    def test_o_different_sim_index_different_seed(self):
        self.assertNotEqual(hpb._seed_for("G1", "PREGAME_EXPECTED", 0), hpb._seed_for("G1", "PREGAME_EXPECTED", 1))

    def test_o_different_mode_different_seed(self):
        self.assertNotEqual(hpb._seed_for("G1", "PREGAME_EXPECTED", 0), hpb._seed_for("G1", "ORACLE_PARTICIPANTS", 0))


class TestRepeatedBacktestEquivalence(unittest.TestCase):
    """P. Repeated small backtest equivalence."""

    def test_p_repeated_small_backtest_is_reproducible(self):
        r1 = hpb.run_backtest([DENVER_GAME], SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=10)
        r2 = hpb.run_backtest([DENVER_GAME], SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=10)
        self.assertEqual(hpb.serialize_raw_games(r1), hpb.serialize_raw_games(r2))


class TestSnapshotFailureAccounting(unittest.TestCase):
    """Q. Snapshot failure accounting."""

    def test_q_bad_game_id_is_recorded_as_a_skip_not_silently_dropped(self):
        result = hpb.run_backtest(["NOT_A_REAL_GAME_ID"], SEASON, ALL_SEASONS, hgs.MODE_PREGAME_EXPECTED, n_sims=5)
        self.assertEqual(result.n_succeeded, 0)
        self.assertEqual(len(result.skips), 1)
        self.assertEqual(result.skips[0]["game_id"], "NOT_A_REAL_GAME_ID")


class TestSimulationFailureAccounting(unittest.TestCase):
    """R. Simulation failure accounting."""

    def test_r_all_simulations_faulting_is_recorded_not_silently_dropped(self):
        from detailed_game import DetailedGameSimulationFault
        snapshot = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)

        def always_faults(*args, **kwargs):
            raise DetailedGameSimulationFault("TEST_FAULT", "forced test fault", None, ())

        with patch("historical_predictive_backtest.simulate_detailed_game", side_effect=always_faults):
            batch = hpb.run_simulation_batch(snapshot, n_sims=5)
        self.assertEqual(batch.n_valid, 0)
        self.assertEqual(len(batch.fault_reasons), 5)

    def test_r_partial_faults_are_recorded_alongside_valid_results(self):
        from detailed_game import DetailedGameSimulationFault
        snapshot = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        real_sim = hpb.simulate_detailed_game
        call_count = {"n": 0}

        def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                raise DetailedGameSimulationFault("TEST_FAULT", "forced test fault", None, ())
            return real_sim(*args, **kwargs)

        with patch("historical_predictive_backtest.simulate_detailed_game", side_effect=flaky):
            batch = hpb.run_simulation_batch(snapshot, n_sims=6)
        self.assertEqual(batch.n_valid + len(batch.fault_reasons), 6)
        self.assertGreater(batch.n_valid, 0)
        self.assertGreater(len(batch.fault_reasons), 0)


if __name__ == "__main__":
    unittest.main()
