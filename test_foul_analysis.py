"""
Unit tests for foul_analysis.py. Synthetic PlayerFoulRow data only -- no
real network/cache dependency.
"""
import unittest

import foul_analysis as fa
from foul_analysis import PlayerFoulRow


def _row(pid, name, season, sfd=0, nsfd=0, and1=0, sfc=0, nsfc=0, ofc=0,
         fga=1000, fta=200, minutes=2000, rim_paint_fga=400, touches=3000, drives=500,
         usg=0.20, reb=0.10, ast=0.15, gp=70):
    return PlayerFoulRow(
        player_id=str(pid), player_name=name, season=season,
        shooting_foul_drawn=sfd, nonshooting_def_foul_drawn=nsfd, and_ones=and1,
        total_drawn=sfd + nsfd,
        shooting_foul_committed=sfc, nonshooting_def_foul_committed=nsfc,
        offensive_foul_committed=ofc, total_committed=sfc + nsfc,
        fga=fga, fta=fta, minutes=minutes, rim_paint_fga=rim_paint_fga,
        touches=touches, drives=drives, usg_pct=usg, reb_pct=reb, ast_pct=ast, gp=gp,
    )


class TestDrawDenominators(unittest.TestCase):
    def test_esa_subtracts_and_ones(self):
        row = _row(1, "A", "2020-21", sfd=10, and1=3, fga=500)
        esa = fa.draw_denominator_value(row, "esa")
        self.assertEqual(esa, 500 + 10 - 3)

    def test_fga_plus_sfd(self):
        row = _row(1, "A", "2020-21", sfd=10, fga=500)
        self.assertEqual(fa.draw_denominator_value(row, "fga_plus_sfd"), 510)

    def test_unknown_denominator_raises(self):
        row = _row(1, "A", "2020-21")
        with self.assertRaises(ValueError):
            fa.draw_denominator_value(row, "not_a_real_denominator")

    def test_low_exposure_returns_none(self):
        row = _row(1, "A", "2020-21", fga=5)
        self.assertIsNone(fa.foul_drawing_rate(row, "fga", min_exposure=30.0))

    def test_normal_rate(self):
        row = _row(1, "A", "2020-21", sfd=50, fga=1000)
        self.assertAlmostEqual(fa.foul_drawing_rate(row, "fga"), 0.05)


class TestCompareDrawDenominators(unittest.TestCase):
    def test_reports_real_season_pairs(self):
        rows_by_season = {
            "2019-20": [_row(1, "A", "2019-20", sfd=40, fga=900)],
            "2020-21": [_row(1, "A", "2020-21", sfd=45, fga=950)],
        }
        result = fa.compare_draw_denominators(rows_by_season, min_exposure=30.0)
        self.assertEqual(result["season_pairs_used"], [("2019-20", "2020-21")])
        self.assertIn("fga", result["denominators"])
        self.assertGreaterEqual(result["denominators"]["fga"]["n_pairs"], 1)


class TestDiscipline(unittest.TestCase):
    def test_minutes_denominator(self):
        row = _row(1, "A", "2020-21", sfc=20, nsfc=10, minutes=3000)
        self.assertAlmostEqual(fa.foul_discipline_rate(row, "minutes", min_exposure=100.0), 30 / 3000)

    def test_def_possessions_proxy_requires_league_mean(self):
        row = _row(1, "A", "2020-21", sfc=20, nsfc=10, minutes=2400)
        self.assertIsNone(fa.foul_discipline_rate(row, "def_possessions_proxy", min_exposure=1.0, league_mean_possessions=None))
        rate = fa.foul_discipline_rate(row, "def_possessions_proxy", min_exposure=1.0, league_mean_possessions=100.0)
        expected_denom = (2400 / 48.0) * 100.0
        self.assertAlmostEqual(rate, 30 / expected_denom)

    def test_low_exposure_returns_none(self):
        row = _row(1, "A", "2020-21", sfc=1, minutes=10)
        self.assertIsNone(fa.foul_discipline_rate(row, "minutes", min_exposure=100.0))

    def test_offensive_fouls_never_counted_in_total_committed(self):
        row = _row(1, "A", "2020-21", sfc=5, nsfc=5, ofc=999, minutes=2000)
        self.assertEqual(row.total_committed, 10)  # offensive_foul_committed excluded from total_committed


class TestRoleBias(unittest.TestCase):
    def test_draw_role_bias_reports_correlations(self):
        rows = [_row(i, f"P{i}", "2020-21", sfd=10 + i, fga=1000, usg=0.1 + i * 0.01, reb=0.05 + i * 0.005) for i in range(10)]
        report = fa.draw_role_bias_report(rows, "fga", min_exposure=30.0)
        self.assertIn("corr_rate_vs_usage", report)
        self.assertIn("corr_rate_vs_reb_pct", report)

    def test_insufficient_data_returns_none_corr(self):
        rows = [_row(1, "A", "2020-21", sfd=10, fga=1000)]
        report = fa.draw_role_bias_report(rows, "fga")
        self.assertIsNone(report["corr_rate_vs_usage"])


if __name__ == "__main__":
    unittest.main()
