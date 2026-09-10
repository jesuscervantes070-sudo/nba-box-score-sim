"""Unit tests for passing_tracking_ingestion.py. Real network calls faked."""
import unittest
from unittest.mock import patch

import pandas as pd

import passing_tracking_ingestion as pti


def _fake_df(rows):
    cols = ["PLAYER_ID", "PLAYER_NAME", "GP", "MIN", "PASSES_MADE", "PASSES_RECEIVED",
            "AST", "FT_AST", "SECONDARY_AST", "POTENTIAL_AST", "AST_PTS_CREATED",
            "AST_ADJ", "AST_TO_PASS_PCT", "AST_TO_PASS_PCT_ADJ"]
    return pd.DataFrame([[r.get(c) for c in cols] for r in rows], columns=cols)


class TestFetchAndCache(unittest.TestCase):
    def setUp(self):
        self.season = "TEST-PT-SEASON"
        self.cache_path = pti._passing_tracking_cache_path(self.season)
        if self.cache_path.exists():
            self.cache_path.unlink()

    def tearDown(self):
        if self.cache_path.exists():
            self.cache_path.unlink()
        d = self.cache_path.parent
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    def test_before_floor_returns_none(self):
        self.assertIsNone(pti.build_and_cache_passing_tracking("2010-11"))

    def test_empty_response_returns_none(self):
        with patch.object(pti, "fetch_passing_tracking_data", return_value=_fake_df([])):
            self.assertIsNone(pti.build_and_cache_passing_tracking(self.season))
        self.assertFalse(self.cache_path.exists())

    def test_normal_case_cached_correctly(self):
        df = _fake_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "GP": 70, "MIN": 2000,
                         "PASSES_MADE": 3000, "PASSES_RECEIVED": 2500, "AST": 400, "FT_AST": 20,
                         "SECONDARY_AST": 50, "POTENTIAL_AST": 700, "AST_PTS_CREATED": 950,
                         "AST_ADJ": 430, "AST_TO_PASS_PCT": 0.133, "AST_TO_PASS_PCT_ADJ": 0.143}])
        with patch.object(pti, "fetch_passing_tracking_data", return_value=df):
            result = pti.build_and_cache_passing_tracking(self.season)
        row = result["players"]["1"]
        self.assertEqual(row["potential_ast"], 700)
        self.assertEqual(row["passes_made"], 3000)

    def test_cache_first_skips_refetch(self):
        df = _fake_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "GP": 70, "MIN": 2000,
                         "PASSES_MADE": 3000, "PASSES_RECEIVED": 2500, "AST": 400, "FT_AST": 20,
                         "SECONDARY_AST": 50, "POTENTIAL_AST": 700, "AST_PTS_CREATED": 950,
                         "AST_ADJ": 430, "AST_TO_PASS_PCT": 0.133, "AST_TO_PASS_PCT_ADJ": 0.143}])
        calls = {"n": 0}

        def fake_fetch(*a, **kw):
            calls["n"] += 1
            return df

        with patch.object(pti, "fetch_passing_tracking_data", side_effect=fake_fetch):
            pti.build_and_cache_passing_tracking(self.season)
            pti.build_and_cache_passing_tracking(self.season)
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
