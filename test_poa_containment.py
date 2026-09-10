"""
Focused tests for Phase 10 (poa_containment_analysis.py +
poa_containment_estimation.py). Kept minimal per this phase's own
efficiency directive.
"""
import unittest
from unittest.mock import patch

import poa_containment_analysis as pca
import poa_containment_estimation as pce
from poa_containment_analysis import PlayerContainmentRow


def _row(pid, name, season, total_matchup_fga=500, total_matchup_fgm=225, total_expected_fgm=250,
         expected_fga_covered=500, total_partial_poss=400.0, total_matchup_tov=20.0, total_sfl=5.0,
         total_matchup_time_sec=10000.0, n_opp=200, minutes=1500, usg=0.15, reb=0.1, stl36=1.5):
    return PlayerContainmentRow(
        player_id=str(pid), player_name=name, season=season,
        total_partial_poss=total_partial_poss, total_matchup_fga=total_matchup_fga,
        total_matchup_fgm=total_matchup_fgm, total_expected_fgm=total_expected_fgm,
        expected_fga_covered=expected_fga_covered, total_matchup_tov=total_matchup_tov,
        total_sfl=total_sfl, total_matchup_time_sec=total_matchup_time_sec, n_distinct_opponents=n_opp,
        minutes=minutes, usg_pct=usg, reb_pct=reb, stl_per36=stl36, gp=60,
    )


class TestCandidateMath(unittest.TestCase):
    def test_containment_rate_formula(self):
        row = _row(1, "A", "2020-21", total_expected_fgm=260, total_matchup_fgm=230, expected_fga_covered=500)
        self.assertAlmostEqual(pca.containment_rate(row), (260 - 230) / 500)

    def test_low_exposure_returns_none(self):
        row = _row(1, "A", "2020-21", expected_fga_covered=10)
        self.assertIsNone(pca.containment_rate(row, min_exposure=100.0))

    def test_higher_suppression_is_positive(self):
        good = _row(1, "Good", "2020-21", total_expected_fgm=280, total_matchup_fgm=220, expected_fga_covered=500)
        bad = _row(2, "Bad", "2020-21", total_expected_fgm=250, total_matchup_fgm=280, expected_fga_covered=500)
        self.assertGreater(pca.containment_rate(good), pca.containment_rate(bad))


class TestOverlapAndContamination(unittest.TestCase):
    def test_foul_interaction_report(self):
        rows = [_row(i, f"P{i}", "2020-21", total_sfl=5 + i, total_partial_poss=400) for i in range(10)]
        report = pca.foul_interaction_report(rows)
        self.assertEqual(report["n"], 10)

    def test_combined_diagnostic_pass_shape(self):
        rows = {"2020-21": [_row(i, f"P{i}", "2020-21", total_expected_fgm=250 + i * 5, total_matchup_fgm=225,
                                  usg=0.1 + i * 0.02, stl36=1.0 + i * 0.1) for i in range(10)],
                "2021-22": [_row(i, f"P{i}", "2021-22", total_expected_fgm=250 + i * 5, total_matchup_fgm=225) for i in range(10)]}
        result = pca.combined_diagnostic_pass(rows, "2020-21")
        self.assertIn("corr_vs_stl_per36", result["contamination_and_overlap"])
        self.assertIn("corr_vs_usg_pct", result["contamination_and_overlap"])
        self.assertIsNotNone(result["stability"])


class TestEstimator(unittest.TestCase):
    def test_pre_floor_returns_insufficient(self):
        report = pce.estimate_poa_containment("Old Timer", "2015-16", ["2015-16"])
        self.assertEqual(report.mode, "INSUFFICIENT")
        self.assertIsNone(report.rating_0_99)

    def test_low_exposure_low_confidence_not_bad_rating(self):
        rows = [_row(1, "Low Sample Guy", "2020-21", expected_fga_covered=10)]
        with patch.object(pca, "build_player_containment_rows", return_value=rows):
            report = pce.estimate_poa_containment("Low Sample Guy", "2020-21", ["2020-21"], min_exposure=100.0)
        self.assertIsNone(report.rating_0_99)
        self.assertEqual(report.confidence, "low")
        self.assertIn("NOT evidence of poor containment", report.coverage_note)

    def test_no_temporal_leakage(self):
        history = {
            "2019-20": [_row(1, "X", "2019-20", total_expected_fgm=260, total_matchup_fgm=230, expected_fga_covered=500)],
            "2020-21": [_row(1, "X", "2020-21", total_expected_fgm=260, total_matchup_fgm=232, expected_fga_covered=500)],
            "2021-22": [_row(1, "X", "2021-22", total_expected_fgm=200, total_matchup_fgm=400, expected_fga_covered=500)],  # excluded
        }
        with patch.object(pca, "build_player_containment_rows", side_effect=lambda s: history.get(s, [])):
            report = pce.estimate_poa_containment("X", "2020-21", ["2019-20", "2020-21", "2021-22"])
        self.assertGreater(report.shrunk_rate, -0.1)  # would collapse very negative if 2021-22 leaked backward


class TestSerializationAndProfileWiring(unittest.TestCase):
    def test_unestimated_when_no_rating(self):
        report = pce.PoaContainmentReport(player_name="X", season="2020-21")
        est = pce.result_to_attribute_estimate(report)
        self.assertIsNone(est.value)

    def test_profile_wiring(self):
        from player_ability_profile import PlayerAbilityProfile
        report = pce.PoaContainmentReport(player_name="X", season="2020-21", rating_0_99=70.0,
                                           expected_fga_covered=600, confidence="medium")
        est = pce.result_to_attribute_estimate(report)
        profile = PlayerAbilityProfile(name="X", as_of_season="2020-21").with_attribute(pce.PROFILE_ATTRIBUTE_KEY, est)
        self.assertEqual(profile.get_attribute(pce.PROFILE_ATTRIBUTE_KEY).value, 70.0)


if __name__ == "__main__":
    unittest.main()
