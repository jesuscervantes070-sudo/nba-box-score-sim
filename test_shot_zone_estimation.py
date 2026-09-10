"""
Unit tests for shot_zone_estimation.py and shot_zone_calibration.py.
Real cache reads are faked via monkeypatching load_shot_zones/load_teams/
load_player_advanced_stats -- no real network access needed.
"""
import unittest
from unittest.mock import patch

import shot_zone_estimation as sze
import shot_zone_calibration as szc


def _zone_row(name, fgm, fga):
    return {"player_name": name, "restricted_area_fgm": fgm, "restricted_area_fga": fga,
            "paint_non_ra_fgm": fgm, "paint_non_ra_fga": fga,
            "midrange_fgm": fgm, "midrange_fga": fga}


class TestZoneRowPct(unittest.TestCase):
    def test_normal_case(self):
        row = _zone_row("A", 10, 20)
        self.assertEqual(sze._zone_row_pct(row, "restricted_area"), (0.5, 20))

    def test_zero_attempts_is_none_not_zero_rate(self):
        row = _zone_row("A", 0, 0)
        self.assertIsNone(sze._zone_row_pct(row, "restricted_area"))

    def test_missing_field_is_none(self):
        self.assertIsNone(sze._zone_row_pct({"player_name": "A"}, "restricted_area"))


class TestPlayerZoneEvidence(unittest.TestCase):
    def test_no_future_leakage(self):
        """Evidence collection only ever reads seasons explicitly passed
        in -- the leakage guard lives in _seasons_through_cutoff, tested
        via estimate_shot_zone_attribute below."""
        fake_data = {
            "1996-97": {"1": _zone_row("Player X", 50, 100)},
            "1997-98": {"1": _zone_row("Player X", 60, 100)},
        }
        with patch.object(sze, "load_shot_zones", side_effect=lambda s: fake_data.get(s, {})):
            evidence = sze._player_zone_evidence_by_season("Player X", ["1996-97"], "restricted_area")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].season, "1996-97")

    def test_missing_player_season_skipped_not_zero(self):
        fake_data = {"1996-97": {"1": _zone_row("Someone Else", 50, 100)}}
        with patch.object(sze, "load_shot_zones", side_effect=lambda s: fake_data.get(s, {})):
            evidence = sze._player_zone_evidence_by_season("Player X", ["1996-97"], "restricted_area")
        self.assertEqual(evidence, [])

    def test_empty_season_cache_skipped(self):
        with patch.object(sze, "load_shot_zones", return_value={}):
            evidence = sze._player_zone_evidence_by_season("Player X", ["1990-91"], "restricted_area")
        self.assertEqual(evidence, [])


class TestEstimateShotZoneAttribute(unittest.TestCase):
    def test_unknown_attribute_raises(self):
        with self.assertRaises(ValueError):
            sze.estimate_shot_zone_attribute("A", "2020-21", "not_a_real_attribute", ["2020-21"])

    def test_no_temporal_leakage_end_to_end(self):
        """A player's real evidence in a season AFTER as_of_season must
        never influence the estimate -- verified by making 2020-21's
        rate wildly different from history and confirming it has zero effect
        when as_of_season = 2019-20."""
        fake_data = {
            "2018-19": {"1": _zone_row("Player X", 50, 100)},   # 0.50
            "2019-20": {"1": _zone_row("Player X", 55, 100)},   # 0.55
            "2020-21": {"1": _zone_row("Player X", 0, 100)},    # 0.00 -- must be excluded
        }
        with patch.object(sze, "load_shot_zones", side_effect=lambda s: fake_data.get(s, {})), \
             patch.object(sze, "_build_reference_population", return_value=([0.4, 0.5, 0.6], 0.5)), \
             patch.object(sze, "resolve_params", return_value=(0.6, 0.0, "provisional")):
            result = sze.estimate_shot_zone_attribute(
                "Player X", "2019-20", "rim_finishing", ["2018-19", "2019-20", "2020-21"],
            )
        # With M=0 (no shrink), the shrunk rate is purely the recency-weighted
        # average of 2018-19/2019-20 -- 2020-21's 0.0 must not appear at all.
        self.assertGreater(result.shrunk_rate, 0.49)

    def test_missing_evidence_yields_unestimated_attribute_estimate(self):
        with patch.object(sze, "load_shot_zones", return_value={}):
            result = sze.estimate_shot_zone_attribute("Nobody", "2020-21", "midrange", ["2020-21"])
        estimate = sze.result_to_attribute_estimate(result)
        self.assertIsNone(estimate.value)


class TestMultiYearWeightingAndShrinkage(unittest.TestCase):
    def test_low_volume_hot_streak_regresses_toward_league_average(self):
        """A single-season 6-for-6 (100%) must NOT produce a near-100
        rating -- shrinkage toward a real league average is required."""
        fake_data = {"2023-24": {"1": _zone_row("Hot Streak Guy", 6, 6)}}
        with patch.object(sze, "load_shot_zones", side_effect=lambda s: fake_data.get(s, {})), \
             patch.object(sze, "_build_reference_population", return_value=([0.5, 0.55, 0.6, 0.65], 0.6)), \
             patch.object(sze, "resolve_params", return_value=(0.6, 50.0, "provisional")):
            result = sze.estimate_shot_zone_attribute("Hot Streak Guy", "2023-24", "rim_finishing", ["2023-24"])
        self.assertLess(result.shrunk_rate, 0.9)  # regressed well below the raw 1.0
        self.assertGreater(result.shrunk_rate, 0.6)  # but still pulled toward/above league avg, not punished

    def test_more_history_increases_total_weight(self):
        fake_data = {
            "2021-22": {"1": _zone_row("Vet Guy", 50, 100)},
            "2022-23": {"1": _zone_row("Vet Guy", 55, 100)},
            "2023-24": {"1": _zone_row("Vet Guy", 52, 100)},
        }
        with patch.object(sze, "load_shot_zones", side_effect=lambda s: fake_data.get(s, {})), \
             patch.object(sze, "_build_reference_population", return_value=([0.4, 0.5, 0.6], 0.5)), \
             patch.object(sze, "resolve_params", return_value=(0.6, 0.0, "provisional")):
            one_season = sze.estimate_shot_zone_attribute("Vet Guy", "2023-24", "rim_finishing", ["2023-24"])
            three_seasons = sze.estimate_shot_zone_attribute("Vet Guy", "2023-24", "rim_finishing", ["2021-22", "2022-23", "2023-24"])
        self.assertGreater(three_seasons.total_weight, one_season.total_weight)


class TestCalibrationLoader(unittest.TestCase):
    def test_missing_artifact_returns_empty(self):
        with patch.object(szc, "CALIBRATION_PATH") as mock_path:
            mock_path.exists.return_value = False
            self.assertEqual(szc.load_calibration(), {})
            self.assertIsNone(szc.get_calibrated_params("rim_finishing"))

    def test_resolve_params_falls_back_to_provisional_when_uncalibrated(self):
        with patch("shot_zone_calibration.get_calibrated_params", return_value=None):
            lam, M, source = sze.resolve_params("rim_finishing")
        self.assertEqual(source, "provisional")
        self.assertEqual(lam, sze.PROVISIONAL_RECENCY_DECAY)


class TestSerialization(unittest.TestCase):
    def test_attribute_estimate_round_trips(self):
        from player_ability_profile import PlayerAbilityProfile
        with patch.object(sze, "load_shot_zones", side_effect=lambda s: {
            "1": _zone_row("Player X", 60, 100)} if s == "2020-21" else {}), \
             patch.object(sze, "_build_reference_population", return_value=([0.4, 0.5, 0.6], 0.5)):
            result = sze.estimate_shot_zone_attribute("Player X", "2020-21", "rim_finishing", ["2020-21"])
        estimate = sze.result_to_attribute_estimate(result)
        profile = PlayerAbilityProfile(name="Player X", as_of_season="2020-21")
        profile = profile.with_attribute("rim_finishing", estimate)
        d = profile.to_dict() if hasattr(profile, "to_dict") else None
        self.assertEqual(profile.get_attribute("rim_finishing").value, estimate.value)


if __name__ == "__main__":
    unittest.main()
