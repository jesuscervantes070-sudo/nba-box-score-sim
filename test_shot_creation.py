"""
Focused tests for Phase 9 (shot_creation_analysis.py +
shot_creation_estimation.py). Kept minimal per this phase's own
efficiency directive.
"""
import unittest
from unittest.mock import patch

import shot_creation_analysis as sca
import shot_creation_estimation as sce
from shot_creation_analysis import PlayerCreationRow


def _row(pid, name, season, drives=200, drive_fga=100, drive_fta=30, drive_ast=20, drive_tov=15,
         pull_up_fga=100, catch_shoot_fga=80, touches=1000, top=200.0, usg=0.2, reb=0.1):
    return PlayerCreationRow(
        player_id=str(pid), player_name=name, season=season,
        drives=drives, drive_fga=drive_fga, drive_fta=drive_fta, drive_ast=drive_ast, drive_tov=drive_tov,
        pull_up_fga=pull_up_fga, catch_shoot_fga=catch_shoot_fga,
        touches=touches, time_of_poss=top, usg_pct=usg, reb_pct=reb, ast_pct=0.15, gp=60,
    )


class TestCandidateMath(unittest.TestCase):
    def test_rim_access_rate_formula(self):
        row = _row(1, "A", "2020-21", drives=200, drive_fga=100, drive_fta=30, drive_ast=20, drive_tov=15)
        self.assertAlmostEqual(sca.rim_access_rate(row), (100 + 30 + 20 - 15) / 200)

    def test_rim_access_low_drives_none(self):
        row = _row(1, "A", "2020-21", drives=10)
        self.assertIsNone(sca.rim_access_rate(row, min_drives=50.0))

    def test_perimeter_rate_formula(self):
        row = _row(1, "A", "2020-21", pull_up_fga=150, touches=1000)
        self.assertAlmostEqual(sca.perimeter_space_rate(row), 0.15)

    def test_perimeter_low_touches_none(self):
        row = _row(1, "A", "2020-21", touches=50)
        self.assertIsNone(sca.perimeter_space_rate(row, min_touches=200.0))

    def test_perimeter_missing_pullup_none(self):
        row = _row(1, "A", "2020-21")
        row.pull_up_fga = None
        self.assertIsNone(sca.perimeter_space_rate(row))


class TestResidualization(unittest.TestCase):
    def test_residual_removes_role_signal(self):
        """Two players, identical raw perimeter rate, very different
        usage/TOP-per-touch -- residuals must differ."""
        low_role = _row(1, "Low Role", "2020-21", pull_up_fga=100, touches=1000, usg=0.10, top=50.0)
        high_role = _row(2, "High Role", "2020-21", pull_up_fga=100, touches=1000, usg=0.35, top=500.0)
        r_low = sce.perimeter_residual(low_role)
        r_high = sce.perimeter_residual(high_role)
        self.assertIsNotNone(r_low)
        self.assertIsNotNone(r_high)
        self.assertGreater(r_low, r_high)

    def test_residual_none_when_inputs_missing(self):
        row = _row(1, "A", "2020-21")
        row.usg_pct = None
        self.assertIsNone(sce.perimeter_residual(row))


class TestCombinedDiagnostic(unittest.TestCase):
    def test_component_correlation_shape(self):
        rows = {"2020-21": [_row(i, f"P{i}", "2020-21", drives=200 + i * 5, drive_fga=100 + i,
                                  pull_up_fga=50 + i * 3, touches=1000 + i * 20) for i in range(10)],
                "2021-22": [_row(i, f"P{i}", "2021-22", drives=200 + i * 5, drive_fga=100 + i,
                                  pull_up_fga=50 + i * 3, touches=1000 + i * 20) for i in range(10)]}
        result = sca.combined_diagnostic_pass(rows, "2020-21")
        self.assertIn("rim_access", result["contamination"])
        self.assertIn("perimeter_space", result["contamination"])
        self.assertIn("corr", result["component_correlation"])


class TestEstimator(unittest.TestCase):
    def test_pre_floor_returns_insufficient(self):
        report = sce.estimate_rim_access_creation("Old Timer", "1996-97", ["1996-97"])
        self.assertEqual(report.mode, "INSUFFICIENT")
        self.assertIsNone(report.rating_0_99)

    def test_low_exposure_low_confidence_not_bad_rating(self):
        rows = [_row(1, "Low Drives Guy", "2020-21", drives=10)]
        with patch.object(sca, "build_player_creation_rows", return_value=rows):
            report = sce.estimate_rim_access_creation("Low Drives Guy", "2020-21", ["2020-21"], min_drives=50.0)
        self.assertIsNone(report.rating_0_99)
        self.assertEqual(report.confidence, "low")
        self.assertIn("NOT evidence of poor", report.coverage_note)

    def test_no_temporal_leakage(self):
        history = {
            "2019-20": [_row(1, "X", "2019-20", drives=300, drive_fga=150, drive_fta=40, drive_ast=30, drive_tov=20)],
            "2020-21": [_row(1, "X", "2020-21", drives=300, drive_fga=150, drive_fta=40, drive_ast=30, drive_tov=20)],
            "2021-22": [_row(1, "X", "2021-22", drives=300, drive_fga=0, drive_fta=0, drive_ast=0, drive_tov=290)],  # must be excluded
        }
        with patch.object(sca, "build_player_creation_rows", side_effect=lambda s: history.get(s, [])):
            report = sce.estimate_rim_access_creation("X", "2020-21", ["2019-20", "2020-21", "2021-22"])
        self.assertGreater(report.shrunk_rate, 0.0)  # would collapse if 2021-22 leaked backward

    def test_serialization_unestimated(self):
        report = sce.CreationComponentReport(player_name="X", season="2020-21", component="rim_access_creation")
        est = sce.result_to_attribute_estimate(report)
        self.assertIsNone(est.value)

    def test_serialization_with_value(self):
        report = sce.CreationComponentReport(player_name="X", season="2020-21", component="rim_access_creation",
                                              rating_0_99=75.0, exposure=300, confidence="high")
        est = sce.result_to_attribute_estimate(report)
        self.assertEqual(est.value, 75.0)
        self.assertEqual(est.confidence, 0.8)
        self.assertEqual(est.sample_size, 300)


if __name__ == "__main__":
    unittest.main()
