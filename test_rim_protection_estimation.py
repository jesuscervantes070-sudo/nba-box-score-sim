"""Unit tests for rim_protection_estimation.py. Real cache reads faked."""
import unittest
from unittest.mock import patch

import rim_protection_estimation as rpe
import rim_protection_analysis as rpa
from rim_protection_analysis import PlayerRimRow


def _row(pid, name, season, fga=200, plusminus=0.05, blk36=1.0, minutes=1500):
    return PlayerRimRow(
        player_id=str(pid), player_name=name, season=season, team_name="Team A",
        rim_fga_defended=fga, rim_fgm_allowed=fga * 0.5,
        rim_fg_pct_allowed=0.5, rim_expected_fg_pct=0.6,
        rim_suppression_plusminus=plusminus, rim_freq_of_own_defended_shots=0.4,
        blk_per36=blk36, minutes=minutes, reb_pct=0.1, usg_pct=0.15,
        shooting_foul_committed=None, gp=60,
    )


class TestPreTrackingFloor(unittest.TestCase):
    def test_pre_2013_14_returns_unestimated(self):
        report = rpe.estimate_rim_protection("Old Timer", "1996-97", ["1996-97"])
        self.assertEqual(report.mode, rpe.MODE_INSUFFICIENT)
        self.assertIsNone(report.rating_0_99)


class TestEstimateRimProtection(unittest.TestCase):
    def test_missing_player_returns_no_evidence(self):
        with patch.object(rpa, "build_player_rim_rows", return_value=[]):
            report = rpe.estimate_rim_protection("Nobody", "2020-21", ["2020-21"])
        self.assertIsNone(report.rating_0_99)
        self.assertEqual(report.mode, rpe.MODE_MODERN_TRACKING)

    def test_low_opportunity_is_low_confidence_not_bad_rating(self):
        """Few opportunities must NOT be confused with bad rim protection."""
        rows = [_row(1, "Rarely At Rim", "2020-21", fga=5, plusminus=-0.5)]
        with patch.object(rpa, "build_player_rim_rows", return_value=rows):
            report = rpe.estimate_rim_protection("Rarely At Rim", "2020-21", ["2020-21"], min_exposure=30.0)
        self.assertIsNone(report.rating_0_99)
        self.assertEqual(report.confidence, "low")
        self.assertIn("NOT evidence of poor rim protection", report.coverage_note)

    def test_normal_case_produces_rating(self):
        rows = [_row(1, "Good Rim Protector", "2020-21", fga=300, plusminus=0.08),
                _row(2, "Bad Rim Protector", "2020-21", fga=300, plusminus=-0.08)]
        rows += [_row(10 + i, f"Filler{i}", "2020-21", fga=300, plusminus=-0.06 + i * 0.015) for i in range(8)]
        with patch.object(rpa, "build_player_rim_rows", return_value=rows), \
             patch.object(rpe, "_resolve_params", return_value=(0.5, 100.0, "calibrated")):
            good = rpe.estimate_rim_protection("Good Rim Protector", "2020-21", ["2020-21"])
            bad = rpe.estimate_rim_protection("Bad Rim Protector", "2020-21", ["2020-21"])
        self.assertGreater(good.rating_0_99, bad.rating_0_99)

    def test_no_temporal_leakage(self):
        history = {
            "2019-20": [_row(1, "Player X", "2019-20", fga=300, plusminus=0.05)],
            "2020-21": [_row(1, "Player X", "2020-21", fga=300, plusminus=0.06)],
            "2021-22": [_row(1, "Player X", "2021-22", fga=300, plusminus=-0.9)],  # must be excluded when as_of=2020-21
        }
        with patch.object(rpa, "build_player_rim_rows", side_effect=lambda s: history.get(s, [])), \
             patch.object(rpe, "_resolve_params", return_value=(0.6, 0.0, "calibrated")):
            report = rpe.estimate_rim_protection("Player X", "2020-21", ["2019-20", "2020-21", "2021-22"])
        self.assertGreater(report.shrunk_rate, 0.0)  # would be dragged very negative if 2021-22 leaked in

    def test_multi_year_history_increases_confidence_signal(self):
        history = {
            "2018-19": [_row(1, "Vet", "2018-19", fga=300, plusminus=0.05)],
            "2019-20": [_row(1, "Vet", "2019-20", fga=300, plusminus=0.06)],
            "2020-21": [_row(1, "Vet", "2020-21", fga=300, plusminus=0.04)],
        }
        with patch.object(rpa, "build_player_rim_rows", side_effect=lambda s: history.get(s, [])), \
             patch.object(rpe, "_resolve_params", return_value=(0.6, 0.0, "calibrated")):
            report = rpe.estimate_rim_protection("Vet", "2020-21", ["2018-19", "2019-20", "2020-21"])
        self.assertIn("3 season(s)", report.coverage_note)


class TestAttributeEstimateBridge(unittest.TestCase):
    def test_unestimated_when_no_rating(self):
        report = rpe.RimProtectionReport(player_name="X", season="2020-21")
        est = rpe.result_to_attribute_estimate(report)
        self.assertIsNone(est.value)

    def test_serializes_with_confidence_and_sample_size(self):
        report = rpe.RimProtectionReport(player_name="X", season="2020-21", rating_0_99=80.0,
                                          rim_fga_defended=400, confidence="high")
        est = rpe.result_to_attribute_estimate(report)
        self.assertEqual(est.value, 80.0)
        self.assertEqual(est.confidence, 0.8)
        self.assertEqual(est.sample_size, 400)

    def test_profile_wiring(self):
        from player_ability_profile import PlayerAbilityProfile
        report = rpe.RimProtectionReport(player_name="X", season="2020-21", rating_0_99=72.0,
                                          rim_fga_defended=250, confidence="medium")
        est = rpe.result_to_attribute_estimate(report)
        profile = PlayerAbilityProfile(name="X", as_of_season="2020-21").with_attribute("rim_protection", est)
        self.assertEqual(profile.get_attribute("rim_protection").value, 72.0)


if __name__ == "__main__":
    unittest.main()
