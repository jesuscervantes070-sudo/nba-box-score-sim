"""
Unit tests for ball_security_analysis.py and ball_security_calibration.py.
All data is synthetic/injected -- no real network or cache-file
dependency, so these stay fast and deterministic regardless of what this
session's real (partial) ingestion happened to produce.
"""
import unittest
from unittest.mock import patch

import ball_security_analysis as bsa
import ball_security_calibration as bsc
from ball_security_analysis import PlayerSeasonRow


def _row(player_id, season, handling_error, touches, usg_pct=0.20, ast_pct=0.15, reb_pct=0.10,
         estimated_total_dribbles=None, time_of_poss=0.0, drives=0.0, name=None):
    return PlayerSeasonRow(
        player_id=player_id, player_name=name or f"Player{player_id}", season=season,
        handling_error=handling_error, bad_pass=0, offensive_foul_nonhandle=0,
        team_system=0, other_unclassified=0, total_turnovers=handling_error,
        exposure={"touches": touches, "estimated_total_dribbles": estimated_total_dribbles or touches * 3,
                  "time_of_poss": time_of_poss, "drives": drives},
        usg_pct=usg_pct, ast_pct=ast_pct, reb_pct=reb_pct, fga=None, fta=None, gp=70,
    )


class TestScaling(unittest.TestCase):
    def test_partial_coverage_scales_up(self):
        self.assertAlmostEqual(bsa._scale_to_full_season(10, games_done=30, games_total=60), 20.0)

    def test_full_coverage_unscaled(self):
        self.assertEqual(bsa._scale_to_full_season(10, games_done=60, games_total=60), 10.0)

    def test_zero_games_done_no_divide_by_zero(self):
        self.assertEqual(bsa._scale_to_full_season(0, games_done=0, games_total=60), 0.0)


class TestHandlingErrorRate(unittest.TestCase):
    def test_basic_rate(self):
        row = _row(1, "2020-21", handling_error=10, touches=1000)
        self.assertAlmostEqual(bsa.handling_error_rate(row, "touches"), 0.01)

    def test_low_exposure_returns_none_not_elite_rating(self):
        """A near-zero-touch player must never silently look like an
        elite (zero-turnover) ball-security rate."""
        row = _row(1, "2020-21", handling_error=0, touches=2)
        self.assertIsNone(bsa.handling_error_rate(row, "touches", min_exposure=50.0))


class TestCompareDenominators(unittest.TestCase):
    def test_predicts_next_season_and_reports_pairs(self):
        rows_by_season = {
            "2019-20": [_row(1, "2019-20", handling_error=20, touches=2000),
                        _row(2, "2019-20", handling_error=5, touches=500)],
            "2020-21": [_row(1, "2020-21", handling_error=18, touches=1900),
                        _row(2, "2020-21", handling_error=6, touches=520)],
        }
        result = bsa.compare_denominators(rows_by_season, min_exposure=50.0)
        self.assertEqual(result["season_pairs_used"], [("2019-20", "2020-21")])
        self.assertIn("touches", result["denominators"])
        self.assertGreaterEqual(result["denominators"]["touches"]["n_pairs"], 1)

    def test_no_future_leakage_in_pairing(self):
        """A season pair only ever looks at (T, T+1) in chronological
        order -- never (T, T-1)."""
        rows_by_season = {
            "2018-19": [_row(1, "2018-19", handling_error=10, touches=1000)],
            "2021-22": [_row(1, "2021-22", handling_error=10, touches=1000)],
        }
        result = bsa.compare_denominators(rows_by_season)
        self.assertEqual(result["season_pairs_used"], [("2018-19", "2021-22")])


class TestBurdenBias(unittest.TestCase):
    def test_detects_positive_usage_correlation(self):
        rows = []
        for i in range(20):
            usg = 0.10 + i * 0.01
            rate_bias = usg * 0.5  # deliberately correlated
            rows.append(_row(i, "2020-21", handling_error=10 + rate_bias * 1000, touches=1000, usg_pct=usg))
        report = bsa.burden_bias_report(rows, "touches", min_exposure=50.0)
        self.assertIsNotNone(report["corr_rate_vs_usage"])
        self.assertGreater(report["corr_rate_vs_usage"], 0.5)

    def test_insufficient_pairs_returns_none_not_a_fake_number(self):
        rows = [_row(1, "2020-21", handling_error=5, touches=500)]
        report = bsa.burden_bias_report(rows, "touches")
        self.assertIsNone(report["corr_rate_vs_usage"])


class TestTouchesProxy(unittest.TestCase):
    def _synthetic_rows_and_advanced(self, seasons, n_per_season=30):
        rows, advanced = [], {}
        for s in seasons:
            advanced[s] = {}
            for i in range(n_per_season):
                usg = 0.10 + (i % 10) * 0.02
                ast = 0.08 + (i % 7) * 0.01
                touches_per_min = 0.3 + usg * 3.0 + ast * 1.0  # real deterministic relationship for the test
                gp, mpg = 70, 30
                name = f"P{s}_{i}"
                advanced[s][name] = {"mpg": mpg, "gp": gp}
                rows.append(PlayerSeasonRow(
                    player_id=f"{s}_{i}", player_name=name, season=s,
                    handling_error=0, bad_pass=0, offensive_foul_nonhandle=0, team_system=0,
                    other_unclassified=0, total_turnovers=0,
                    exposure={"touches": touches_per_min * mpg * gp, "estimated_total_dribbles": 0,
                              "time_of_poss": 0, "drives": 0},
                    usg_pct=usg, ast_pct=ast, reb_pct=0.10, fga=None, fta=None, gp=gp,
                ))
        return rows, advanced

    def test_fit_and_evaluate_on_held_out_season(self):
        train_rows, train_adv = self._synthetic_rows_and_advanced(["2016-17", "2017-18"])
        test_rows, test_adv = self._synthetic_rows_and_advanced(["2018-19"])
        advanced = {**train_adv, **test_adv}

        model = bsa.fit_touches_proxy(train_rows, advanced)
        self.assertIsNotNone(model)
        result = bsa.evaluate_touches_proxy(model, test_rows, advanced)
        # The synthetic relationship is exactly linear in usg/ast, so a
        # correctly-implemented OLS fit should explain it almost perfectly
        # on held-out data.
        self.assertGreater(result["r2"], 0.9)

    def test_trained_only_on_train_seasons_not_test(self):
        train_rows, train_adv = self._synthetic_rows_and_advanced(["2016-17"])
        model = bsa.fit_touches_proxy(train_rows, train_adv)
        self.assertEqual(model.train_seasons, ("2016-17",))

    def test_insufficient_training_data_returns_none(self):
        rows, advanced = self._synthetic_rows_and_advanced(["2016-17"], n_per_season=3)
        model = bsa.fit_touches_proxy(rows, advanced)
        self.assertIsNone(model)


class TestSubgroupBias(unittest.TestCase):
    def test_reports_group_means_by_usage_and_reb_tercile(self):
        rows = []
        for i in range(30):
            usg = 0.10 + (i % 10) * 0.02
            reb = 0.05 + (i % 6) * 0.03
            rows.append(_row(i, "2020-21", handling_error=5, touches=1000, usg_pct=usg, reb_pct=reb))
        report = bsa.subgroup_bias_report(rows, "touches", min_exposure=50.0)
        self.assertIn("by_usage_tercile", report)
        self.assertIn("by_reb_pct_tercile_APPROXIMATE_ROLE_PROXY", report)
        for tier in ("low", "mid", "high"):
            self.assertGreater(report["by_usage_tercile"][tier]["n"], 0)

    def test_insufficient_rows_reported_not_faked(self):
        rows = [_row(1, "2020-21", handling_error=5, touches=1000)]
        report = bsa.subgroup_bias_report(rows, "touches")
        self.assertEqual(report["status"], "insufficient_data")


class TestDownstreamProxyFidelity(unittest.TestCase):
    def test_perfect_proxy_yields_perfect_agreement(self):
        # Build rows whose real touches-per-minute is EXACTLY the linear
        # function the model will predict -- proxy should reproduce the
        # true ranking (and thus rating) almost exactly.
        rows, advanced = [], {}
        for i in range(40):
            usg = 0.10 + (i % 10) * 0.02
            ast = 0.08 + (i % 7) * 0.01
            mpg, gp = 30, 70
            touches_per_min = 0.3 + usg * 3.0 + ast * 1.0
            name = f"P{i}"
            advanced[name] = {"mpg": mpg, "gp": gp}
            rows.append(PlayerSeasonRow(
                player_id=str(i), player_name=name, season="2020-21",
                handling_error=3 + (i % 5), bad_pass=0, offensive_foul_nonhandle=0, team_system=0,
                other_unclassified=0, total_turnovers=0,
                exposure={"touches": touches_per_min * mpg * gp, "estimated_total_dribbles": 0,
                          "time_of_poss": 0, "drives": 0},
                usg_pct=usg, ast_pct=ast, reb_pct=0.10, fga=None, fta=None, gp=gp,
            ))
        model = bsa.fit_touches_proxy(rows, {"2020-21": advanced})
        result = bsa.downstream_proxy_fidelity(model, rows, advanced, min_exposure=50.0)
        self.assertGreater(result["rank_correlation_true_vs_proxy_rate"], 0.99)
        self.assertGreater(result["top_decile_agreement"], 0.5)

    def test_insufficient_data_reported(self):
        model = bsa.ProxyModel(intercept=0.5, coef_usg=1.0, coef_ast=1.0, train_seasons=("2020-21",))
        result = bsa.downstream_proxy_fidelity(model, [], {})
        self.assertEqual(result["status"], "insufficient_data")


class TestBallSecurityCalibration(unittest.TestCase):
    def test_insufficient_seasons_flagged_not_forced(self):
        result = bsc.grid_search({"2020-21": []}, "touches")
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")

    def test_missing_artifact_returns_none(self):
        with patch.object(bsc, "CALIBRATION_PATH") as mock_path:
            mock_path.exists.return_value = False
            self.assertIsNone(bsc.load_ball_security_calibration())

    def test_grid_search_leakage_guard(self):
        """_shrunk_rate must never use a season strictly after as_of_year."""
        seasons_rates = [("2019-20", 0.05, 100.0), ("2021-22", 0.99, 100.0)]
        pred = bsc._shrunk_rate(seasons_rates, as_of_year=2020, lambda_=0.5, M=0.0, league_avg=0.05)
        # 2021-22 must be excluded (age negative relative to as_of 2020)
        self.assertAlmostEqual(pred, 0.05, places=4)


if __name__ == "__main__":
    unittest.main()
