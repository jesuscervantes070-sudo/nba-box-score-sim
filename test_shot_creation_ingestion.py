"""Unit tests for shot_creation_ingestion.py. Real network calls faked."""
import unittest
from unittest.mock import patch

import pandas as pd

import shot_creation_ingestion as sci


def _drives_df(rows):
    cols = ["PLAYER_ID", "PLAYER_NAME", "GP", "MIN", "DRIVES", "DRIVE_FGA", "DRIVE_FGM",
            "DRIVE_FTA", "DRIVE_AST", "DRIVE_TOV", "DRIVE_PTS"]
    return pd.DataFrame([[r.get(c) for c in cols] for r in rows], columns=cols)


def _pullup_df(rows):
    cols = ["PLAYER_ID", "PULL_UP_FGA", "PULL_UP_FGM", "PULL_UP_FG3A"]
    return pd.DataFrame([[r.get(c) for c in cols] for r in rows], columns=cols)


def _catch_df(rows):
    cols = ["PLAYER_ID", "CATCH_SHOOT_FGA", "CATCH_SHOOT_FGM"]
    return pd.DataFrame([[r.get(c) for c in cols] for r in rows], columns=cols)


class TestFetchAndCache(unittest.TestCase):
    def setUp(self):
        self.season = "TEST-SC-SEASON"
        self.cache_path = sci._shot_creation_cache_path(self.season)
        if self.cache_path.exists():
            self.cache_path.unlink()

    def tearDown(self):
        if self.cache_path.exists():
            self.cache_path.unlink()
        d = self.cache_path.parent
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    def test_before_floor_returns_none(self):
        self.assertIsNone(sci.build_and_cache_shot_creation_tracking("2010-11"))

    def test_normal_case_joins_all_three_calls(self):
        drives = _drives_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "GP": 70, "MIN": 2000,
                               "DRIVES": 300, "DRIVE_FGA": 150, "DRIVE_FGM": 75, "DRIVE_FTA": 40,
                               "DRIVE_AST": 30, "DRIVE_TOV": 25, "DRIVE_PTS": 200}])
        pullup = _pullup_df([{"PLAYER_ID": 1, "PULL_UP_FGA": 200, "PULL_UP_FGM": 80, "PULL_UP_FG3A": 100}])
        catch = _catch_df([{"PLAYER_ID": 1, "CATCH_SHOOT_FGA": 150, "CATCH_SHOOT_FGM": 60}])

        def fake_fetch(season, mtype, **kw):
            return {"Drives": drives, "PullUpShot": pullup, "CatchShoot": catch}[mtype]

        with patch.object(sci, "_fetch_pt", side_effect=fake_fetch):
            result = sci.build_and_cache_shot_creation_tracking(self.season)
        row = result["players"]["1"]
        self.assertEqual(row["drives"], 300)
        self.assertEqual(row["pull_up_fga"], 200)
        self.assertEqual(row["catch_shoot_fga"], 150)

    def test_missing_pullup_row_leaves_field_absent_not_zero(self):
        drives = _drives_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "GP": 70, "MIN": 2000,
                               "DRIVES": 300, "DRIVE_FGA": 150, "DRIVE_FGM": 75, "DRIVE_FTA": 40,
                               "DRIVE_AST": 30, "DRIVE_TOV": 25, "DRIVE_PTS": 200}])
        pullup = _pullup_df([])
        catch = _catch_df([])

        def fake_fetch(season, mtype, **kw):
            return {"Drives": drives, "PullUpShot": pullup, "CatchShoot": catch}[mtype]

        with patch.object(sci, "_fetch_pt", side_effect=fake_fetch):
            result = sci.build_and_cache_shot_creation_tracking(self.season)
        row = result["players"]["1"]
        self.assertNotIn("pull_up_fga", row)

    def test_cache_first_skips_refetch(self):
        drives = _drives_df([{"PLAYER_ID": 1, "PLAYER_NAME": "A", "GP": 70, "MIN": 2000,
                               "DRIVES": 300, "DRIVE_FGA": 150, "DRIVE_FGM": 75, "DRIVE_FTA": 40,
                               "DRIVE_AST": 30, "DRIVE_TOV": 25, "DRIVE_PTS": 200}])
        calls = {"n": 0}

        def fake_fetch(season, mtype, **kw):
            calls["n"] += 1
            return {"Drives": drives, "PullUpShot": _pullup_df([]), "CatchShoot": _catch_df([])}[mtype]

        with patch.object(sci, "_fetch_pt", side_effect=fake_fetch):
            sci.build_and_cache_shot_creation_tracking(self.season)
            sci.build_and_cache_shot_creation_tracking(self.season)
        self.assertEqual(calls["n"], 3)  # only the FIRST call's 3 fetches, second call is cache-first


if __name__ == "__main__":
    unittest.main()
