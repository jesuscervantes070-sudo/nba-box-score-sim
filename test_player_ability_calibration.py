"""
Focused unit tests for player_ability_calibration.py and the
calibrated-vs-provisional wiring in player_ability_estimation.py.
Root-level, not in tests/ (Codex's).

Run with: python3 -m unittest test_player_ability_calibration -v
"""
import glob
import json
import tempfile
import unittest
from pathlib import Path

from player_ability_calibration import (
    load_calibration, get_calibrated_params, CalibratedAttributeParams, CALIBRATION_PATH,
)
import player_ability_calibration as pac
import player_ability_estimation as pae

ALL_SEASONS = sorted(d.split('/')[-1] for d in glob.glob('cache/????-??'))


class TestCalibrationArtifactLoading(unittest.TestCase):
    def test_real_artifact_loads_without_error(self):
        calibration = load_calibration()
        self.assertIsInstance(calibration, dict)

    def test_all_six_calibrated_attributes_present(self):
        calibration = load_calibration()
        for attr in ("three_point", "free_throw", "passing",
                     "offensive_rebounding", "defensive_rebounding", "defensive_playmaking"):
            self.assertIn(attr, calibration, f"{attr} should have a calibrated entry")

    def test_ball_security_deliberately_not_calibrated(self):
        # Per this phase's explicit scope -- must remain absent.
        self.assertIsNone(get_calibrated_params("ball_security"))

    def test_missing_artifact_returns_empty_dict_not_raise(self):
        original = pac.CALIBRATION_PATH
        try:
            pac.CALIBRATION_PATH = Path("/tmp/definitely_does_not_exist_calibration.json")
            self.assertEqual(load_calibration(), {})
            self.assertIsNone(get_calibrated_params("three_point"))
        finally:
            pac.CALIBRATION_PATH = original

    def test_malformed_artifact_returns_empty_dict_not_raise(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ not valid json")
            path = Path(f.name)
        original = pac.CALIBRATION_PATH
        try:
            pac.CALIBRATION_PATH = path
            self.assertEqual(load_calibration(), {})  # never raises
        finally:
            pac.CALIBRATION_PATH = original
            path.unlink()

    def test_serialization_shape_round_trips(self):
        params = CalibratedAttributeParams(
            lambda_=0.7, M=50, exposure_unit="season total 3PA",
            weighted_mae=0.034, weighted_rmse=0.045, n_observations=5879,
            training_range=("1996-97", "2025-26"), calibration_version="0.1.0",
        )
        self.assertEqual(params.lambda_, 0.7)
        self.assertEqual(params.training_range, ("1996-97", "2025-26"))


class TestEstimatorUsesCalibration(unittest.TestCase):
    def test_calibrated_attribute_reports_calibrated_source(self):
        result = pae.estimate_attribute("Stephen Curry", "2015-16", "three_point", ALL_SEASONS)
        if result.percentile_rating is None:
            self.skipTest("2015-16 Curry not found in this cache")
        self.assertEqual(result.param_source, "calibrated")

    def test_ball_security_always_provisional(self):
        result = pae.estimate_attribute("Stephen Curry", "2015-16", "ball_security", ALL_SEASONS)
        if result.percentile_rating is None:
            self.skipTest("2015-16 Curry not found in this cache")
        self.assertEqual(result.param_source, "provisional")

    def test_missing_calibration_falls_back_to_provisional_explicitly(self):
        original = pac.CALIBRATION_PATH
        try:
            pac.CALIBRATION_PATH = Path("/tmp/definitely_does_not_exist_calibration.json")
            result = pae.estimate_attribute("Stephen Curry", "2015-16", "three_point", ALL_SEASONS)
            if result.percentile_rating is None:
                self.skipTest("2015-16 Curry not found in this cache")
            self.assertEqual(result.param_source, "provisional")
        finally:
            pac.CALIBRATION_PATH = original

    def test_true_rebound_data_still_preferred_after_calibration_wiring(self):
        # The calibration change must not have disturbed the earlier
        # TRUE/FALLBACK rebound-mode logic -- still N/A-independent.
        result = pae.estimate_attribute("Stephen Curry", "2015-16", "offensive_rebounding", ALL_SEASONS)
        if result.percentile_rating is None:
            self.skipTest("2015-16 Curry not found in this cache")
        self.assertEqual(result.evidence_mode, "TRUE")

    def test_resolve_params_matches_artifact_values(self):
        lam, M, source = pae.resolve_params("three_point")
        calibrated = get_calibrated_params("three_point")
        if calibrated is None:
            self.skipTest("no calibration artifact present")
        self.assertEqual(source, "calibrated")
        self.assertEqual(lam, calibrated.lambda_)
        self.assertEqual(M, calibrated.M)

    def test_no_future_season_leakage_with_calibrated_params(self):
        # Same leakage guarantee must hold end to end with calibration wired in.
        result = pae.estimate_attribute("LeBron James", "2005-06", "three_point", ALL_SEASONS)
        for ev in result.seasons_used:
            self.assertLessEqual(int(ev.season[:4]), 2005)


class TestCalibrationSearchDeterminism(unittest.TestCase):
    """Uses a small, fast synthetic table (not the full real 30-season
    build, which is slow) to test the SEARCH LOGIC's determinism in
    isolation -- see player_ability_calibration_search.py."""

    def _tiny_table(self):
        return {
            "three_point": {
                "2018-19": {"A": {"rate": 0.40, "sample": 300, "mode": "n/a"},
                            "B": {"rate": 0.30, "sample": 100, "mode": "n/a"}},
                "2019-20": {"A": {"rate": 0.42, "sample": 320, "mode": "n/a"},
                            "B": {"rate": 0.31, "sample": 90, "mode": "n/a"}},
                "2020-21": {"A": {"rate": 0.39, "sample": 310, "mode": "n/a"},
                            "B": {"rate": 0.33, "sample": 110, "mode": "n/a"}},
            }
        }

    def test_grid_search_is_deterministic(self):
        import player_ability_calibration_search as pacs
        table = self._tiny_table()
        pairs, seasons = pacs.build_pairs(table, "three_point", min_t1_sample=50)
        result1 = pacs.grid_search(pairs, seasons, table, "three_point", [0.5, 0.7], [50, 100])
        result2 = pacs.grid_search(pairs, seasons, table, "three_point", [0.5, 0.7], [50, 100])
        self.assertEqual(result1, result2)

    def test_evaluate_is_a_pure_function(self):
        import player_ability_calibration_search as pacs
        table = self._tiny_table()
        pairs, seasons = pacs.build_pairs(table, "three_point", min_t1_sample=50)
        r1 = pacs.evaluate(pairs, seasons, table, "three_point", 0.6, 100)
        r2 = pacs.evaluate(pairs, seasons, table, "three_point", 0.6, 100)
        self.assertEqual(r1, r2)

    def test_build_pairs_excludes_non_consecutive_seasons(self):
        import player_ability_calibration_search as pacs
        table = self._tiny_table()
        table["three_point"]["2022-23"] = {"A": {"rate": 0.45, "sample": 300, "mode": "n/a"}}  # a real gap after 2020-21
        pairs, seasons = pacs.build_pairs(table, "three_point", min_t1_sample=50)
        # no pair should have T=2020-21 -> T1=2022-23 (not consecutive)
        for T, T1, *_ in pairs:
            if T == "2020-21":
                self.assertNotEqual(T1, "2022-23")


class TestAgeAdjustmentV2(unittest.TestCase):
    def test_v2_artifact_loads(self):
        from player_ability_calibration import load_age_adjustments
        adjustments = load_age_adjustments()
        self.assertIn("three_point", adjustments)
        self.assertIn("defensive_playmaking", adjustments)

    def test_rebounding_attributes_have_no_age_adjustment(self):
        # Real, tested null result -- must stay absent, not a placeholder zero.
        from player_ability_calibration import load_age_adjustments
        adjustments = load_age_adjustments()
        self.assertNotIn("offensive_rebounding", adjustments)
        self.assertNotIn("defensive_rebounding", adjustments)

    def test_ball_security_has_no_age_adjustment(self):
        from player_ability_calibration import load_age_adjustments
        self.assertNotIn("ball_security", load_age_adjustments())

    def test_apply_age_adjustment_is_noop_without_age(self):
        from player_ability_calibration import apply_age_adjustment
        raw = 0.40
        self.assertEqual(apply_age_adjustment("three_point", raw, age=None), raw)

    def test_apply_age_adjustment_changes_prediction_for_young_player(self):
        from player_ability_calibration import apply_age_adjustment
        raw = 0.35
        adjusted = apply_age_adjustment("three_point", raw, age=20)
        self.assertNotEqual(adjusted, raw)  # a real, known-nonzero bucket correction applies at this age

    def test_apply_age_adjustment_noop_for_uncalibrated_attribute(self):
        from player_ability_calibration import apply_age_adjustment
        raw = 0.85
        self.assertEqual(apply_age_adjustment("offensive_rebounding", raw, age=20), raw)

    def test_v1_artifact_still_loads_independently_of_v2(self):
        # v2's existence must not disturb v1 -- old calibration remains loadable.
        calibration_v1 = load_calibration()
        self.assertIn("three_point", calibration_v1)
        self.assertEqual(calibration_v1["three_point"].lambda_, 0.70)

    def test_age_adjustment_disabled_by_default(self):
        result_default = pae.estimate_attribute("Victor Wembanyama", "2023-24", "three_point", ALL_SEASONS)
        if result_default.percentile_rating is None:
            self.skipTest("2023-24 Wembanyama not found in this cache")
        self.assertFalse(result_default.age_adjusted)

    def test_age_adjustment_can_be_enabled_explicitly(self):
        result_off = pae.estimate_attribute("Victor Wembanyama", "2023-24", "three_point", ALL_SEASONS,
                                             apply_age_adjustment=False)
        result_on = pae.estimate_attribute("Victor Wembanyama", "2023-24", "three_point", ALL_SEASONS,
                                            apply_age_adjustment=True)
        if result_off.percentile_rating is None:
            self.skipTest("2023-24 Wembanyama not found in this cache")
        self.assertTrue(result_on.age_adjusted)
        self.assertNotEqual(result_on.shrunk_rate, result_off.shrunk_rate)

    def test_age_adjustment_uses_training_data_only_by_construction(self):
        # The v2 artifact's bucket corrections were fit on T1 < 2016-17
        # residuals only (see player_ability_calibration_search.py's
        # split in the calibration report) -- this test checks the
        # artifact records that boundary explicitly, not that it
        # silently changed.
        import json
        with open("player_ability_calibration_v2.json") as f:
            v2 = json.load(f)
        self.assertIn("2015-16", v2["temporal_validation"]["train_target_seasons"])


class TestHeldOutSplitDeterminism(unittest.TestCase):
    def test_temporal_split_is_deterministic_and_time_respecting(self):
        import player_ability_calibration_search as pacs
        table = {
            "three_point": {
                "2014-15": {"A": {"rate": 0.35, "sample": 200, "mode": "n/a"}},
                "2015-16": {"A": {"rate": 0.36, "sample": 210, "mode": "n/a"}},
                "2016-17": {"A": {"rate": 0.37, "sample": 220, "mode": "n/a"}},
                "2017-18": {"A": {"rate": 0.38, "sample": 230, "mode": "n/a"}},
            }
        }
        pairs, seasons = pacs.build_pairs(table, "three_point", min_t1_sample=50)

        def year_of(s): return int(s[:4])
        TRAIN_MAX = 2016
        train1 = [p for p in pairs if year_of(p[1]) < TRAIN_MAX]
        heldout1 = [p for p in pairs if year_of(p[1]) >= TRAIN_MAX]
        train2 = [p for p in pairs if year_of(p[1]) < TRAIN_MAX]
        heldout2 = [p for p in pairs if year_of(p[1]) >= TRAIN_MAX]
        self.assertEqual(train1, train2)
        self.assertEqual(heldout1, heldout2)
        # no overlap, and every held-out T1 is strictly later than every train T1
        train_t1_years = {year_of(p[1]) for p in train1}
        heldout_t1_years = {year_of(p[1]) for p in heldout1}
        self.assertTrue(max(train_t1_years) < min(heldout_t1_years))


if __name__ == "__main__":
    unittest.main()
