"""
Unit tests for shot_zone_ingestion.py. All network calls are faked; real
API behavior was verified interactively and is documented in the module
docstring, not re-verified here on every test run.
"""
import json
import unittest
from unittest.mock import patch

import pandas as pd

import shot_zone_ingestion as szi


def _fake_multiindex_df(rows):
    """rows: list of dicts like {"PLAYER_ID":1,"PLAYER_NAME":"A", ("Restricted Area","FGM"):5, ...}"""
    cols = [("", "PLAYER_ID"), ("", "PLAYER_NAME")]
    for zone in szi.ZONES:
        cols += [(zone, "FGM"), (zone, "FGA"), (zone, "FG_PCT")]
    data = []
    for r in rows:
        row = []
        for c in cols:
            key = c if c[0] else c[1]
            row.append(r.get(key, r.get(c, 0.0)))
        data.append(row)
    df = pd.DataFrame(data, columns=pd.MultiIndex.from_tuples(cols))
    return df


class TestFetchAndCache(unittest.TestCase):
    def setUp(self):
        self.season = "TEST-SZ-SEASON"
        self.cache_path = szi._shot_zone_cache_path(self.season)
        if self.cache_path.exists():
            self.cache_path.unlink()

    def tearDown(self):
        if self.cache_path.exists():
            self.cache_path.unlink()
        d = self.cache_path.parent
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    def test_before_floor_returns_none_writes_nothing(self):
        result = szi.build_and_cache_shot_zones("1990-91")
        self.assertIsNone(result)
        self.assertFalse(szi._shot_zone_cache_path("1990-91").exists())

    def test_empty_response_returns_none_writes_nothing(self):
        empty_df = _fake_multiindex_df([])
        with patch.object(szi, "fetch_shot_zone_data", return_value=empty_df):
            result = szi.build_and_cache_shot_zones(self.season)
        self.assertIsNone(result)
        self.assertFalse(self.cache_path.exists())

    def test_nan_from_api_treated_as_true_zero(self):
        """Confirmed real quirk: a small number of extremely low-volume
        players get NaN instead of 0 for a zone with genuinely zero
        attempts. Must become 0.0, never propagate as NaN (which would
        silently poison every downstream weighted average)."""
        df = _fake_multiindex_df([
            {"PLAYER_ID": 1, "PLAYER_NAME": "Fringe Guy",
             ("Mid-Range", "FGM"): float("nan"), ("Mid-Range", "FGA"): float("nan"),
             ("Restricted Area", "FGM"): 1.0, ("Restricted Area", "FGA"): 2.0},
        ])
        with patch.object(szi, "fetch_shot_zone_data", return_value=df):
            result = szi.build_and_cache_shot_zones(self.season)
        row = result["players"]["1"]
        self.assertEqual(row["midrange_fgm"], 0.0)
        self.assertEqual(row["midrange_fga"], 0.0)
        self.assertEqual(row["restricted_area_fga"], 2.0)

    def test_zero_attempts_preserved_as_true_zero(self):
        df = _fake_multiindex_df([
            {"PLAYER_ID": 1, "PLAYER_NAME": "Zero Guy",
             ("Restricted Area", "FGM"): 0.0, ("Restricted Area", "FGA"): 0.0,
             ("Mid-Range", "FGM"): 10.0, ("Mid-Range", "FGA"): 20.0},
        ])
        with patch.object(szi, "fetch_shot_zone_data", return_value=df):
            result = szi.build_and_cache_shot_zones(self.season)
        row = result["players"]["1"]
        self.assertEqual(row["restricted_area_fgm"], 0.0)
        self.assertEqual(row["restricted_area_fga"], 0.0)
        self.assertEqual(row["midrange_fgm"], 10.0)
        self.assertEqual(row["midrange_fga"], 20.0)

    def test_cache_first_skips_refetch(self):
        df = _fake_multiindex_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A"}])
        call_count = {"n": 0}

        def fake_fetch(*a, **kw):
            call_count["n"] += 1
            return df

        with patch.object(szi, "fetch_shot_zone_data", side_effect=fake_fetch):
            szi.build_and_cache_shot_zones(self.season)
            szi.build_and_cache_shot_zones(self.season)  # second call: cache-first, no refetch
        self.assertEqual(call_count["n"], 1)

    def test_force_true_refetches(self):
        df = _fake_multiindex_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A"}])
        call_count = {"n": 0}

        def fake_fetch(*a, **kw):
            call_count["n"] += 1
            return df

        with patch.object(szi, "fetch_shot_zone_data", side_effect=fake_fetch):
            szi.build_and_cache_shot_zones(self.season)
            szi.build_and_cache_shot_zones(self.season, force=True)
        self.assertEqual(call_count["n"], 2)

    def test_missing_player_distinguishable_from_true_zero(self):
        df = _fake_multiindex_df([{"PLAYER_ID": 1, "PLAYER_NAME": "Present"}])
        with patch.object(szi, "fetch_shot_zone_data", return_value=df):
            szi.build_and_cache_shot_zones(self.season)
        loaded = szi.load_shot_zones(self.season)
        self.assertIn("1", loaded)
        self.assertNotIn("999", loaded)  # a player_id never in the response is simply absent, not a fake zero row

    def test_schema_version_and_provenance_present(self):
        df = _fake_multiindex_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A"}])
        with patch.object(szi, "fetch_shot_zone_data", return_value=df):
            result = szi.build_and_cache_shot_zones(self.season)
        self.assertEqual(result["schema_version"], szi.SHOT_ZONE_CACHE_VERSION)
        self.assertIn("provenance", result)
        self.assertIn("source", result)

    def test_range_ingestion_is_resumable_across_seasons(self):
        df = _fake_multiindex_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A"}])
        seasons = ["TEST-SZ-A", "TEST-SZ-B"]
        paths = [szi._shot_zone_cache_path(s) for s in seasons]
        try:
            with patch.object(szi, "fetch_shot_zone_data", return_value=df):
                results = szi.build_and_cache_shot_zones_range(seasons)
            self.assertTrue(all(results[s] is not None for s in seasons))
            for p in paths:
                self.assertTrue(p.exists())
        finally:
            for p in paths:
                if p.exists():
                    p.unlink()
                d = p.parent
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()

    def test_one_season_failure_does_not_block_others(self):
        def flaky_fetch(season, *a, **kw):
            if season == "TEST-SZ-BAD":
                raise RuntimeError("simulated API failure")
            return _fake_multiindex_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A"}])

        seasons = ["TEST-SZ-BAD", "TEST-SZ-GOOD"]
        try:
            with patch.object(szi, "fetch_shot_zone_data", side_effect=flaky_fetch):
                results = szi.build_and_cache_shot_zones_range(seasons)
            self.assertIsNone(results["TEST-SZ-BAD"])
            self.assertIsNotNone(results["TEST-SZ-GOOD"])
        finally:
            for s in seasons:
                p = szi._shot_zone_cache_path(s)
                if p.exists():
                    p.unlink()
                d = p.parent
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()


if __name__ == "__main__":
    unittest.main()
