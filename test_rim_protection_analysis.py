"""Unit tests for rim_protection_analysis.py. Synthetic data only."""
import unittest

import rim_protection_analysis as rpa
from rim_protection_analysis import PlayerRimRow


def _row(pid, name, season, fga=200, fgm=100, plusminus=0.05, freq=0.4, blk36=1.0,
         minutes=1500, reb=0.1, usg=0.15, sfc=None, gp=60, team="Team A"):
    return PlayerRimRow(
        player_id=str(pid), player_name=name, season=season, team_name=team,
        rim_fga_defended=fga, rim_fgm_allowed=fgm,
        rim_fg_pct_allowed=fgm / fga if fga else None, rim_expected_fg_pct=0.6,
        rim_suppression_plusminus=plusminus, rim_freq_of_own_defended_shots=freq,
        blk_per36=blk36, minutes=minutes, reb_pct=reb, usg_pct=usg,
        shooting_foul_committed=sfc, gp=gp,
    )


class TestSuppressionRate(unittest.TestCase):
    def test_normal_case(self):
        row = _row(1, "A", "2020-21", fga=200, plusminus=0.08)
        self.assertEqual(rpa.suppression_rate(row), 0.08)

    def test_low_exposure_returns_none(self):
        row = _row(1, "A", "2020-21", fga=5)
        self.assertIsNone(rpa.suppression_rate(row, min_exposure=30.0))

    def test_missing_plusminus_returns_none(self):
        row = _row(1, "A", "2020-21", fga=200, plusminus=None)
        self.assertIsNone(rpa.suppression_rate(row))


class TestWeightValue(unittest.TestCase):
    def test_rim_fga(self):
        row = _row(1, "A", "2020-21", fga=250)
        self.assertEqual(rpa.weight_value(row, "rim_fga_defended"), 250)

    def test_minutes(self):
        row = _row(1, "A", "2020-21", minutes=1800)
        self.assertEqual(rpa.weight_value(row, "minutes"), 1800)

    def test_def_possessions_proxy(self):
        row = _row(1, "A", "2020-21", minutes=2400)
        val = rpa.weight_value(row, "def_possessions_proxy", league_mean_possessions=100.0)
        self.assertAlmostEqual(val, (2400 / 48.0) * 100.0)

    def test_unknown_weight_raises(self):
        row = _row(1, "A", "2020-21")
        with self.assertRaises(ValueError):
            rpa.weight_value(row, "bogus")


class TestBlocksOverlap(unittest.TestCase):
    def test_reports_correlation(self):
        rows = [_row(i, f"P{i}", "2020-21", fga=200, plusminus=0.02 * i, blk36=0.5 * i) for i in range(10)]
        report = rpa.blocks_overlap_report(rows)
        self.assertEqual(report["n"], 10)
        self.assertIsNotNone(report["corr_suppression_vs_blk_per36"])

    def test_insufficient_data(self):
        rows = [_row(1, "A", "2020-21")]
        report = rpa.blocks_overlap_report(rows)
        self.assertIsNone(report["corr_suppression_vs_blk_per36"])


class TestFoulInteraction(unittest.TestCase):
    def test_requires_foul_data(self):
        rows = [_row(i, f"P{i}", "2020-21", sfc=None) for i in range(10)]
        report = rpa.foul_interaction_report(rows)
        self.assertEqual(report["n"], 0)

    def test_computes_when_present(self):
        rows = [_row(i, f"P{i}", "2020-21", sfc=10 + i, minutes=2000) for i in range(10)]
        report = rpa.foul_interaction_report(rows)
        self.assertEqual(report["n"], 10)


class TestRoleBias(unittest.TestCase):
    def test_reports_reb_pct_correlation(self):
        rows = [_row(i, f"P{i}", "2020-21", reb=0.05 + i * 0.01, plusminus=0.01 * i) for i in range(10)]
        report = rpa.role_bias_report(rows)
        self.assertIn("corr_suppression_vs_reb_pct (big-proxy)", report)


class TestCompareWeights(unittest.TestCase):
    def test_reports_season_pairs(self):
        rows_by_season = {
            "2019-20": [_row(1, "A", "2019-20", fga=200, plusminus=0.05)],
            "2020-21": [_row(1, "A", "2020-21", fga=210, plusminus=0.06)],
        }
        result = rpa.compare_weights(rows_by_season, min_exposure=30.0)
        self.assertEqual(result["season_pairs_used"], [("2019-20", "2020-21")])
        self.assertIn("rim_fga_defended", result["weights"])


if __name__ == "__main__":
    unittest.main()
