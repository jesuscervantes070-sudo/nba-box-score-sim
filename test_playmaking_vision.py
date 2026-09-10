"""
Focused tests for Phase 8 (playmaking_vision_analysis.py +
playmaking_vision_estimation.py). Avoids excessive synthetic-test
explosion per this phase's own efficiency directive -- covers exactly
the required checklist (parsing/cache, zero/low exposure, shrinkage,
temporal cutoff, fallback mode, serialization/profile wiring, candidate
math) and nothing more.
"""
import unittest
from unittest.mock import patch

import playmaking_vision_analysis as pva
import playmaking_vision_estimation as pve
from playmaking_vision_analysis import PlayerVisionRow


def _row(pid, name, season, potential_ast=100, ast=80, touches=1000, top=200.0,
         drives=300, usg=0.2, ast_pct=0.2, reb_pct=0.1, minutes=1500):
    return PlayerVisionRow(
        player_id=str(pid), player_name=name, season=season,
        potential_ast=potential_ast, ast=ast, secondary_ast=10, ast_pts_created=150,
        passes_made=2000, touches=touches, time_of_poss=top, drives=drives,
        minutes=minutes, usg_pct=usg, ast_pct=ast_pct, reb_pct=reb_pct, gp=60,
    )


class TestCandidateMath(unittest.TestCase):
    def test_vision_rate_normal(self):
        row = _row(1, "A", "2020-21", potential_ast=100, touches=1000)
        self.assertAlmostEqual(pva.vision_rate(row, "touches", 200.0), 0.1)

    def test_vision_rate_low_exposure_none(self):
        row = _row(1, "A", "2020-21", touches=50)
        self.assertIsNone(pva.vision_rate(row, "touches", 200.0))

    def test_unknown_denominator_raises(self):
        row = _row(1, "A", "2020-21")
        with self.assertRaises(ValueError):
            pva.denom_value(row, "bogus")

    def test_residual_removes_role_signal(self):
        """Two players with identical raw rate but very different usage/
        drives/TOP-per-touch must get DIFFERENT residuals."""
        low_role = _row(1, "Low Role", "2020-21", potential_ast=100, touches=1000,
                         usg=0.10, drives=50, top=50.0)
        high_role = _row(2, "High Role", "2020-21", potential_ast=100, touches=1000,
                          usg=0.35, drives=500, top=500.0)
        r_low = pva.residual_vision_rate(low_role, min_touches=200.0)
        r_high = pva.residual_vision_rate(high_role, min_touches=200.0)
        self.assertIsNotNone(r_low)
        self.assertIsNotNone(r_high)
        self.assertGreater(r_low, r_high)  # same raw rate, less role support -> higher residual "vision"

    def test_residual_none_when_role_inputs_missing(self):
        row = _row(1, "A", "2020-21")
        row.drives = None
        self.assertIsNone(pva.residual_vision_rate(row, min_touches=200.0))


class TestContaminationAndOverlap(unittest.TestCase):
    def test_contamination_report_shape(self):
        rows = [_row(i, f"P{i}", "2020-21", potential_ast=50 + i * 5, touches=1000,
                      usg=0.1 + i * 0.02, drives=100 + i * 20) for i in range(10)]
        report = pva.contamination_report(rows, "touches", min_exposure=200.0)
        self.assertIn("corr_vs_usg_pct", report)
        self.assertIn("corr_vs_ast_pct", report)

    def test_ast_vs_potential_ast_report(self):
        rows = [_row(i, f"P{i}", "2020-21", potential_ast=50 + i * 3, ast=30 + i * 2, touches=1000) for i in range(10)]
        report = pva.raw_ast_vs_potential_ast_report(rows, min_touches=200.0)
        self.assertEqual(report["n"], 10)


class TestEstimator(unittest.TestCase):
    def test_pre_floor_returns_insufficient(self):
        report = pve.estimate_playmaking_vision("Old Timer", "1996-97", ["1996-97"])
        self.assertEqual(report.mode, "INSUFFICIENT")
        self.assertIsNone(report.rating_0_99)

    def test_missing_player_no_evidence(self):
        with patch.object(pva, "build_player_vision_rows", return_value=[]):
            report = pve.estimate_playmaking_vision("Nobody", "2020-21", ["2020-21"])
        self.assertIsNone(report.rating_0_99)
        self.assertEqual(report.mode, "MODERN_TRACKING")

    def test_low_touches_low_confidence_not_bad_rating(self):
        rows = [_row(1, "Low Touch Guy", "2020-21", touches=50)]
        with patch.object(pva, "build_player_vision_rows", return_value=rows):
            report = pve.estimate_playmaking_vision("Low Touch Guy", "2020-21", ["2020-21"], min_touches=200.0)
        self.assertIsNone(report.rating_0_99)
        self.assertEqual(report.confidence, "low")
        self.assertIn("NOT evidence of poor vision", report.coverage_note)

    def test_no_temporal_leakage(self):
        history = {
            "2019-20": [_row(1, "X", "2019-20", potential_ast=100, touches=1000)],
            "2020-21": [_row(1, "X", "2020-21", potential_ast=105, touches=1000)],
            "2021-22": [_row(1, "X", "2021-22", potential_ast=1, touches=1000)],  # must be excluded when as_of=2020-21
        }
        with patch.object(pva, "build_player_vision_rows", side_effect=lambda s: history.get(s, [])), \
             patch.object(pve, "_resolve_params", return_value=(0.6, 0.0, "calibrated")):
            report = pve.estimate_playmaking_vision("X", "2020-21", ["2019-20", "2020-21", "2021-22"])
        self.assertGreater(report.shrunk_rate, -0.5)  # would collapse toward near-zero potential_ast if 2021-22 leaked

    def test_shrinkage_pulls_toward_league_average(self):
        rows = [_row(1, "Hot", "2020-21", potential_ast=300, touches=1000, usg=0.2, drives=200, top=200),
                _row(2, "Avg1", "2020-21", potential_ast=100, touches=1000, usg=0.2, drives=200, top=200),
                _row(3, "Avg2", "2020-21", potential_ast=100, touches=1000, usg=0.2, drives=200, top=200)]
        with patch.object(pva, "build_player_vision_rows", return_value=rows), \
             patch.object(pve, "_resolve_params", return_value=(0.6, 5000.0, "calibrated")):
            report = pve.estimate_playmaking_vision("Hot", "2020-21", ["2020-21"], min_touches=200.0)
        self.assertLess(report.shrunk_rate, report.residual_rate)  # heavy shrinkage pulls the outlier down


class TestSerializationAndProfileWiring(unittest.TestCase):
    def test_unestimated_when_no_rating(self):
        report = pve.PlaymakingVisionReport(player_name="X", season="2020-21")
        est = pve.result_to_attribute_estimate(report)
        self.assertIsNone(est.value)

    def test_profile_wiring(self):
        from player_ability_profile import PlayerAbilityProfile
        report = pve.PlaymakingVisionReport(player_name="X", season="2020-21", rating_0_99=68.0,
                                             touches=3000, confidence="medium")
        est = pve.result_to_attribute_estimate(report)
        profile = PlayerAbilityProfile(name="X", as_of_season="2020-21").with_attribute(pve.PROFILE_ATTRIBUTE_KEY, est)
        self.assertEqual(profile.get_attribute(pve.PROFILE_ATTRIBUTE_KEY).value, 68.0)


if __name__ == "__main__":
    unittest.main()
