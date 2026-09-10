"""Unit tests for poa_containment_ingestion.py. Real network calls faked."""
import unittest
from unittest.mock import patch

import pandas as pd

import poa_containment_ingestion as pci


def _matchup_df(rows):
    cols = ["OFF_PLAYER_ID", "OFF_PLAYER_NAME", "DEF_PLAYER_ID", "DEF_PLAYER_NAME",
            "PARTIAL_POSS", "MATCHUP_FGM", "MATCHUP_FGA", "MATCHUP_TOV", "SFL", "MATCHUP_TIME_SEC"]
    return pd.DataFrame([[r.get(c) for c in cols] for r in rows], columns=cols)


class TestFetchAndCache(unittest.TestCase):
    def setUp(self):
        self.season = "TEST-POA-SEASON"
        self.cache_path = pci._poa_cache_path(self.season)
        if self.cache_path.exists():
            self.cache_path.unlink()

    def tearDown(self):
        if self.cache_path.exists():
            self.cache_path.unlink()
        d = self.cache_path.parent
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    def test_before_floor_returns_none(self):
        self.assertIsNone(pci.build_and_cache_poa_containment("2015-16"))

    def test_aggregation_with_opponent_quality(self):
        df = _matchup_df([
            {"OFF_PLAYER_ID": 1, "OFF_PLAYER_NAME": "Shooter A", "DEF_PLAYER_ID": 10, "DEF_PLAYER_NAME": "Defender X",
             "PARTIAL_POSS": 20.0, "MATCHUP_FGM": 5, "MATCHUP_FGA": 10, "MATCHUP_TOV": 2, "SFL": 1, "MATCHUP_TIME_SEC": 300.0},
            {"OFF_PLAYER_ID": 2, "OFF_PLAYER_NAME": "Shooter B", "DEF_PLAYER_ID": 10, "DEF_PLAYER_NAME": "Defender X",
             "PARTIAL_POSS": 10.0, "MATCHUP_FGM": 3, "MATCHUP_FGA": 6, "MATCHUP_TOV": 0, "SFL": 0, "MATCHUP_TIME_SEC": 150.0},
        ])
        with patch.object(pci, "fetch_matchup_data", return_value=df), \
             patch.object(pci, "_real_season_fg_pct_by_name", return_value={"Shooter A": 0.55, "Shooter B": 0.45}):
            result = pci.build_and_cache_poa_containment(self.season)
        row = result["players"]["10"]
        self.assertEqual(row["total_matchup_fga"], 16)
        self.assertEqual(row["total_matchup_fgm"], 8)
        self.assertAlmostEqual(row["total_expected_fgm"], 0.55 * 10 + 0.45 * 6)
        self.assertEqual(row["n_distinct_opponents"], 2)

    def test_empty_response_returns_none(self):
        with patch.object(pci, "fetch_matchup_data", return_value=_matchup_df([])):
            self.assertIsNone(pci.build_and_cache_poa_containment(self.season))
        self.assertFalse(self.cache_path.exists())

    def test_cache_first_skips_refetch(self):
        df = _matchup_df([{"OFF_PLAYER_ID": 1, "OFF_PLAYER_NAME": "A", "DEF_PLAYER_ID": 10, "DEF_PLAYER_NAME": "X",
                            "PARTIAL_POSS": 10.0, "MATCHUP_FGM": 3, "MATCHUP_FGA": 6, "MATCHUP_TOV": 0, "SFL": 0, "MATCHUP_TIME_SEC": 100.0}])
        calls = {"n": 0}

        def fake_fetch(*a, **kw):
            calls["n"] += 1
            return df

        with patch.object(pci, "fetch_matchup_data", side_effect=fake_fetch), \
             patch.object(pci, "_real_season_fg_pct_by_name", return_value={"A": 0.5}):
            pci.build_and_cache_poa_containment(self.season)
            pci.build_and_cache_poa_containment(self.season)
        self.assertEqual(calls["n"], 1)

    def test_missing_expected_fg_pct_excluded_not_zero(self):
        """An offensive player with no real box-stat match must not
        silently contribute a fake expected FG% of 0."""
        df = _matchup_df([{"OFF_PLAYER_ID": 1, "OFF_PLAYER_NAME": "Unknown Guy", "DEF_PLAYER_ID": 10,
                            "DEF_PLAYER_NAME": "X", "PARTIAL_POSS": 10.0, "MATCHUP_FGM": 3, "MATCHUP_FGA": 6,
                            "MATCHUP_TOV": 0, "SFL": 0, "MATCHUP_TIME_SEC": 100.0}])
        with patch.object(pci, "fetch_matchup_data", return_value=df), \
             patch.object(pci, "_real_season_fg_pct_by_name", return_value={}):
            result = pci.build_and_cache_poa_containment(self.season)
        row = result["players"]["10"]
        self.assertEqual(row["total_expected_fgm"], 0.0)
        self.assertEqual(row["expected_fga_covered"], 0.0)  # correct denominator stays 0, not silently diluted
        self.assertEqual(row["total_matchup_fga"], 6)  # real exposure still counted


if __name__ == "__main__":
    unittest.main()
