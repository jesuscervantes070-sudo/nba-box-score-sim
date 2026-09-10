"""Unit tests for anthropometrics_ingestion.py. Real network calls faked."""
import unittest
from unittest.mock import patch

import pandas as pd

import anthropometrics_ingestion as ai


def _combine_df(rows):
    cols = ["PLAYER_ID", "PLAYER_NAME", "POSITION", "HEIGHT_WO_SHOES", "HEIGHT_W_SHOES",
            "WINGSPAN", "STANDING_REACH", "WEIGHT"]
    return pd.DataFrame([[r.get(c) for c in cols] for r in rows], columns=cols)


class TestCombineIngestion(unittest.TestCase):
    def setUp(self):
        self.year = 1901  # real, before-floor year for the floor test only
        self.test_year = 2001  # a real, post-floor year for every other test in this class
        self.cache_path = ai._combine_cache_path(self.year)
        self.test_cache_path = ai._combine_cache_path(self.test_year)
        if self.test_cache_path.exists():
            self.test_cache_path.unlink()
        if self.cache_path.exists():
            self.cache_path.unlink()

    def tearDown(self):
        if self.cache_path.exists():
            self.cache_path.unlink()
        if self.test_cache_path.exists():
            self.test_cache_path.unlink()

    def test_before_floor_returns_none(self):
        self.assertIsNone(ai.build_and_cache_combine_anthro(1999))

    def test_normal_case(self):
        df = _combine_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "POSITION": "SG",
                            "HEIGHT_WO_SHOES": 75.25, "HEIGHT_W_SHOES": None,
                            "WINGSPAN": 82.0, "STANDING_REACH": 101.5, "WEIGHT": 200.0}])
        with patch.object(ai, "fetch_combine_anthro", return_value=df):
            result = ai.build_and_cache_combine_anthro(self.test_year)
        row = result["players"]["1"]
        self.assertEqual(row["height_wo_shoes_in"], 75.25)
        self.assertIsNone(row["height_w_shoes_in"])
        self.assertEqual(row["wingspan_in"], 82.0)

    def test_nan_becomes_none_not_zero(self):
        df = _combine_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "POSITION": "SG",
                            "HEIGHT_WO_SHOES": float("nan"), "HEIGHT_W_SHOES": None,
                            "WINGSPAN": float("nan"), "STANDING_REACH": 101.5, "WEIGHT": 200.0}])
        with patch.object(ai, "fetch_combine_anthro", return_value=df):
            result = ai.build_and_cache_combine_anthro(self.test_year)
        row = result["players"]["1"]
        self.assertIsNone(row["height_wo_shoes_in"])
        self.assertIsNone(row["wingspan_in"])
        self.assertEqual(row["standing_reach_in"], 101.5)  # real, present measurement preserved

    def test_empty_string_becomes_none_not_a_crash(self):
        """Real regression: 2016's real WEIGHT column carries an empty
        string ('') rather than NaN/None for some rows -- must become
        None, not crash `float('')`."""
        df = _combine_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "POSITION": "SG",
                            "HEIGHT_WO_SHOES": 75.0, "HEIGHT_W_SHOES": None,
                            "WINGSPAN": 80.0, "STANDING_REACH": 100.0, "WEIGHT": ""}])
        with patch.object(ai, "fetch_combine_anthro", return_value=df):
            result = ai.build_and_cache_combine_anthro(self.test_year)
        self.assertIsNone(result["players"]["1"]["weight_lbs"])

    def test_empty_response_returns_none(self):
        with patch.object(ai, "fetch_combine_anthro", return_value=_combine_df([])):
            self.assertIsNone(ai.build_and_cache_combine_anthro(self.test_year))
        self.assertFalse(self.cache_path.exists())

    def test_cache_first_skips_refetch(self):
        df = _combine_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "POSITION": "SG",
                            "HEIGHT_WO_SHOES": 75.0, "HEIGHT_W_SHOES": None,
                            "WINGSPAN": 80.0, "STANDING_REACH": 100.0, "WEIGHT": 190.0}])
        calls = {"n": 0}

        def fake(*a, **kw):
            calls["n"] += 1
            return df

        with patch.object(ai, "fetch_combine_anthro", side_effect=fake):
            ai.build_and_cache_combine_anthro(self.test_year)
            ai.build_and_cache_combine_anthro(self.test_year)
        self.assertEqual(calls["n"], 1)


class TestHeightParsing(unittest.TestCase):
    def test_parses_feet_inches(self):
        self.assertAlmostEqual(ai._parse_listed_height("6-5"), 77.0)

    def test_none_input(self):
        self.assertIsNone(ai._parse_listed_height(None))

    def test_unparseable_input(self):
        self.assertIsNone(ai._parse_listed_height("N/A"))
        self.assertIsNone(ai._parse_listed_height(""))


if __name__ == "__main__":
    unittest.main()
