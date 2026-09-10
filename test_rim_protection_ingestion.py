"""Unit tests for rim_protection_ingestion.py. Real network calls faked."""
import unittest
from unittest.mock import patch

import pandas as pd

import rim_protection_ingestion as rpi


def _fake_df(rows):
    cols = ["CLOSE_DEF_PERSON_ID", "PLAYER_NAME", "GP", "FREQ", "FGM_LT_06", "FGA_LT_06",
            "LT_06_PCT", "NS_LT_06_PCT", "PLUSMINUS"]
    return pd.DataFrame([[r.get(c) for c in cols] for r in rows], columns=cols)


class TestFetchAndCache(unittest.TestCase):
    def setUp(self):
        self.season = "TEST-RP-SEASON"
        self.cache_path = rpi._rim_protection_cache_path(self.season)
        if self.cache_path.exists():
            self.cache_path.unlink()

    def tearDown(self):
        if self.cache_path.exists():
            self.cache_path.unlink()
        d = self.cache_path.parent
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    def test_before_floor_returns_none(self):
        result = rpi.build_and_cache_rim_protection("2010-11")
        self.assertIsNone(result)
        self.assertFalse(rpi._rim_protection_cache_path("2010-11").exists())

    def test_empty_response_returns_none(self):
        with patch.object(rpi, "fetch_rim_protection_data", return_value=_fake_df([])):
            result = rpi.build_and_cache_rim_protection(self.season)
        self.assertIsNone(result)
        self.assertFalse(self.cache_path.exists())

    def test_sign_flip_higher_is_better(self):
        # Real PLUSMINUS is signed actual-minus-expected (negative = good
        # defense); cache must flip so higher = better, this project's
        # universal convention.
        df = _fake_df([{"CLOSE_DEF_PERSON_ID": 1, "PLAYER_NAME": "Good Defender", "GP": 70,
                         "FREQ": 0.4, "FGM_LT_06": 100, "FGA_LT_06": 300,
                         "LT_06_PCT": 0.5, "NS_LT_06_PCT": 0.6, "PLUSMINUS": -0.1}])
        with patch.object(rpi, "fetch_rim_protection_data", return_value=df):
            result = rpi.build_and_cache_rim_protection(self.season)
        row = result["players"]["1"]
        self.assertAlmostEqual(row["rim_suppression_plusminus"], 0.1)
        self.assertEqual(row["rim_fga_defended"], 300)
        self.assertEqual(row["rim_fgm_allowed"], 100)

    def test_cache_first_skips_refetch(self):
        df = _fake_df([{"CLOSE_DEF_PERSON_ID": 1, "PLAYER_NAME": "A", "GP": 70, "FREQ": 0.3,
                         "FGM_LT_06": 50, "FGA_LT_06": 100, "LT_06_PCT": 0.5, "NS_LT_06_PCT": 0.55,
                         "PLUSMINUS": -0.05}])
        calls = {"n": 0}

        def fake_fetch(*a, **kw):
            calls["n"] += 1
            return df

        with patch.object(rpi, "fetch_rim_protection_data", side_effect=fake_fetch):
            rpi.build_and_cache_rim_protection(self.season)
            rpi.build_and_cache_rim_protection(self.season)
        self.assertEqual(calls["n"], 1)

    def test_force_refetches(self):
        df = _fake_df([{"CLOSE_DEF_PERSON_ID": 1, "PLAYER_NAME": "A", "GP": 70, "FREQ": 0.3,
                         "FGM_LT_06": 50, "FGA_LT_06": 100, "LT_06_PCT": 0.5, "NS_LT_06_PCT": 0.55,
                         "PLUSMINUS": -0.05}])
        calls = {"n": 0}

        def fake_fetch(*a, **kw):
            calls["n"] += 1
            return df

        with patch.object(rpi, "fetch_rim_protection_data", side_effect=fake_fetch):
            rpi.build_and_cache_rim_protection(self.season)
            rpi.build_and_cache_rim_protection(self.season, force=True)
        self.assertEqual(calls["n"], 2)

    def test_missing_field_stays_none_not_zero(self):
        df = _fake_df([{"CLOSE_DEF_PERSON_ID": 1, "PLAYER_NAME": "A", "GP": 70, "FREQ": None,
                         "FGM_LT_06": 50, "FGA_LT_06": 100, "LT_06_PCT": None, "NS_LT_06_PCT": None,
                         "PLUSMINUS": None}])
        with patch.object(rpi, "fetch_rim_protection_data", return_value=df):
            result = rpi.build_and_cache_rim_protection(self.season)
        row = result["players"]["1"]
        self.assertIsNone(row["rim_expected_fg_pct"])
        self.assertIsNone(row["rim_suppression_plusminus"])

    def test_range_ingestion_resumable_and_isolated_failures(self):
        df = _fake_df([{"CLOSE_DEF_PERSON_ID": 1, "PLAYER_NAME": "A", "GP": 70, "FREQ": 0.3,
                         "FGM_LT_06": 50, "FGA_LT_06": 100, "LT_06_PCT": 0.5, "NS_LT_06_PCT": 0.55,
                         "PLUSMINUS": -0.05}])

        def flaky(season, *a, **kw):
            if season == "TEST-RP-BAD":
                raise RuntimeError("simulated failure")
            return df

        seasons = ["TEST-RP-BAD", "TEST-RP-GOOD"]
        try:
            with patch.object(rpi, "fetch_rim_protection_data", side_effect=flaky):
                results = rpi.build_and_cache_rim_protection_range(seasons)
            self.assertIsNone(results["TEST-RP-BAD"])
            self.assertIsNotNone(results["TEST-RP-GOOD"])
        finally:
            for s in seasons:
                p = rpi._rim_protection_cache_path(s)
                if p.exists():
                    p.unlink()
                d = p.parent
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()


if __name__ == "__main__":
    unittest.main()
