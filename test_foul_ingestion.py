"""
Unit tests for foul_ingestion.py -- classification, drawer/committer
attribution, and-1 handling, resumability. All network calls faked.
"""
import unittest
from unittest.mock import patch

import pandas as pd

import foul_ingestion as fi


class FakeGame:
    def __init__(self, game_id):
        self.game_id = game_id


def _row(person_id, action_type, subtype="", description="", team_id=1610612743):
    return {"personId": person_id, "teamId": team_id, "actionType": action_type,
            "subType": subtype, "description": description}


class TestClassification(unittest.TestCase):
    def test_shooting_variants(self):
        self.assertEqual(fi.classify_foul_subtype("Shooting"), fi.CATEGORY_SHOOTING_FOUL)
        self.assertEqual(fi.classify_foul_subtype("Shooting Block"), fi.CATEGORY_SHOOTING_FOUL)

    def test_nonshooting_variants(self):
        for st in ("Personal", "Loose Ball", "Personal Block"):
            self.assertEqual(fi.classify_foul_subtype(st), fi.CATEGORY_NONSHOOTING_DEF_FOUL)

    def test_offensive_foul_excluded_category(self):
        self.assertEqual(fi.classify_foul_subtype("Offensive"), fi.CATEGORY_OFFENSIVE_FOUL)
        self.assertEqual(fi.classify_foul_subtype("Offensive Charge"), fi.CATEGORY_OFFENSIVE_FOUL)

    def test_technical_flagrant_take_flop_3sec_awayfromplay_excluded(self):
        for st in ("Technical", "Double Technical", "Delay Technical", "Flagrant Type 1",
                   "Transition Take", "Personal Take", "Flopping", "Defense 3 Second", "Away From Play"):
            self.assertEqual(fi.classify_foul_subtype(st), fi.CATEGORY_EXCLUDED_OTHER, st)

    def test_unknown_preserved(self):
        self.assertEqual(fi.classify_foul_subtype("Some New Subtype"), fi.CATEGORY_UNKNOWN)
        self.assertEqual(fi.classify_foul_subtype(""), fi.CATEGORY_UNKNOWN)
        self.assertEqual(fi.classify_foul_subtype(None), fi.CATEGORY_UNKNOWN)


class TestFetchGameFoulEvents(unittest.TestCase):
    def _patch_pbp(self, df):
        class FakePBP:
            def __init__(self, *a, **kw):
                pass

            def get_data_frames(self):
                return [df]
        return patch("nba_api.stats.endpoints.playbyplayv3.PlayByPlayV3", FakePBP)

    def test_shooting_foul_drawer_attributed_via_next_free_throw(self):
        df = pd.DataFrame([
            _row(1, "Foul", "Shooting", "Def S.FOUL"),
            _row(2, "Free Throw", "Free Throw 1 of 2", "Att Free Throw 1 of 2"),
            _row(2, "Free Throw", "Free Throw 2 of 2", "Att Free Throw 2 of 2"),
        ])
        with self._patch_pbp(df):
            events = fi.fetch_game_foul_events("0000000001")
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["committer_player_id"], 1)
        self.assertEqual(ev["drawer_player_id"], 2)
        self.assertEqual(ev["category"], fi.CATEGORY_SHOOTING_FOUL)
        self.assertFalse(ev["and_one"])

    def test_and_one_detected_only_when_shooter_matches_and_single_ft(self):
        df = pd.DataFrame([
            _row(5, "Made Shot", "", "Made Shot"),
            _row(1, "Foul", "Shooting", "Def S.FOUL"),
            _row(5, "Free Throw", "Free Throw 1 of 1", "Att Free Throw 1 of 1 (and-1)"),
        ])
        with self._patch_pbp(df):
            events = fi.fetch_game_foul_events("0000000002")
        self.assertEqual(events[0]["and_one"], True)
        self.assertEqual(events[0]["drawer_player_id"], 5)

    def test_and_one_false_positive_avoided_different_shooter(self):
        """A made shot by player A followed by an unrelated foul whose
        free throws go to player B must NOT be flagged as an and-1."""
        df = pd.DataFrame([
            _row(9, "Made Shot", "", "A makes a shot, ending a possession"),
            _row(1, "Foul", "Shooting", "Def S.FOUL on a later possession"),
            _row(2, "Free Throw", "Free Throw 1 of 2", "B Free Throw 1 of 2"),
            _row(2, "Free Throw", "Free Throw 2 of 2", "B Free Throw 2 of 2"),
        ])
        with self._patch_pbp(df):
            events = fi.fetch_game_foul_events("0000000003")
        self.assertFalse(events[0]["and_one"])
        self.assertEqual(events[0]["drawer_player_id"], 2)

    def test_non_bonus_common_foul_has_no_drawer(self):
        df = pd.DataFrame([
            _row(1, "Foul", "Personal", "Def P.FOUL (not in penalty)"),
            _row(3, "Substitution", "", "SUB: X FOR Y"),
        ])
        with self._patch_pbp(df):
            events = fi.fetch_game_foul_events("0000000004")
        self.assertIsNone(events[0]["drawer_player_id"])
        self.assertEqual(events[0]["category"], fi.CATEGORY_NONSHOOTING_DEF_FOUL)

    def test_team_level_event_excluded(self):
        df = pd.DataFrame([_row(1610612743, "Foul", "Technical", "Team Technical", team_id=0)])
        with self._patch_pbp(df):
            events = fi.fetch_game_foul_events("0000000005")
        self.assertEqual(events, [])

    def test_offensive_foul_committer_recorded_no_drawer(self):
        df = pd.DataFrame([_row(1, "Foul", "Offensive Charge", "Att Offensive Charge Foul")])
        with self._patch_pbp(df):
            events = fi.fetch_game_foul_events("0000000006")
        self.assertEqual(events[0]["category"], fi.CATEGORY_OFFENSIVE_FOUL)
        self.assertIsNone(events[0]["drawer_player_id"])


class TestAggregation(unittest.TestCase):
    def test_apply_events_deterministic(self):
        state = fi._new_state("X")
        events = [
            {"committer_player_id": 1, "drawer_player_id": 2, "category": fi.CATEGORY_SHOOTING_FOUL,
             "subtype": "Shooting", "and_one": False, "description": "d"},
            {"committer_player_id": 1, "drawer_player_id": 2, "category": fi.CATEGORY_NONSHOOTING_DEF_FOUL,
             "subtype": "Personal", "and_one": False, "description": "d"},
            {"committer_player_id": 3, "drawer_player_id": None, "category": fi.CATEGORY_OFFENSIVE_FOUL,
             "subtype": "Offensive", "and_one": False, "description": "d"},
        ]
        fi._apply_events(state, events)
        self.assertEqual(state["committers"]["1"]["shooting_foul"], 1)
        self.assertEqual(state["committers"]["1"]["nonshooting_def_foul"], 1)
        self.assertEqual(state["committers"]["3"]["offensive_foul"], 1)
        self.assertEqual(state["drawers"]["2"]["shooting_foul_drawn"], 1)
        self.assertEqual(state["drawers"]["2"]["nonshooting_def_foul_drawn"], 1)
        self.assertEqual(state["drawers"]["2"]["total_drawn"], 2)

    def test_offensive_foul_never_credits_a_drawer(self):
        state = fi._new_state("X")
        fi._apply_events(state, [
            {"committer_player_id": 1, "drawer_player_id": 99, "category": fi.CATEGORY_OFFENSIVE_FOUL,
             "subtype": "Offensive", "and_one": False, "description": "d"},
        ])
        self.assertEqual(state["drawers"], {})  # even if a stray drawer_id were set, offensive fouls never count


class TestResumableIngestion(unittest.TestCase):
    def setUp(self):
        self.season = "TEST-FOUL-SEASON"
        self.cache_path = fi._foul_cache_path(self.season)
        if self.cache_path.exists():
            self.cache_path.unlink()
        self.games = [FakeGame(g) for g in ("0000000001", "0000000002")]

    def tearDown(self):
        if self.cache_path.exists():
            self.cache_path.unlink()
        d = self.cache_path.parent
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    def test_resumable_skips_completed_games(self):
        seen = []

        def fake_fetch(gid, **kw):
            seen.append(gid)
            return []

        with patch("loader.load_schedule", return_value=self.games), \
             patch.object(fi, "fetch_game_foul_events", side_effect=fake_fetch):
            fi.ingest_season_fouls(self.season, sleep_between=0, save_every=1, max_games=1)
            self.assertEqual(seen, ["0000000001"])
            fi.ingest_season_fouls(self.season, sleep_between=0, save_every=1)
            self.assertEqual(seen, ["0000000001", "0000000002"])

    def test_games_total_reflects_real_schedule_not_max_games_cap(self):
        """Real bug, fixed this phase: games_total must be the real full
        schedule length, never the max_games cap -- otherwise a
        max_games=1 sample of a 2-game schedule would look 100%
        complete to any downstream partial-coverage scaling logic."""
        with patch("loader.load_schedule", return_value=self.games), \
             patch.object(fi, "fetch_game_foul_events", return_value=[]):
            state = fi.ingest_season_fouls(self.season, sleep_between=0, save_every=1, max_games=1)
        self.assertEqual(state["games_total"], len(self.games))  # NOT 1, the max_games cap
        self.assertEqual(len(state["games_done"]), 1)

    def test_interrupted_ingestion_preserves_completed_data(self):
        def flaky(gid, **kw):
            if gid == "0000000002":
                raise RuntimeError("simulated failure")
            return []

        with patch("loader.load_schedule", return_value=self.games), \
             patch.object(fi, "fetch_game_foul_events", side_effect=flaky):
            state = fi.ingest_season_fouls(self.season, sleep_between=0, save_every=1)
        self.assertIn("0000000001", state["games_done"])
        self.assertIn("0000000002", state["games_failed"])
        on_disk = fi.load_foul_cache(self.season)
        self.assertEqual(on_disk["games_done"], state["games_done"])


if __name__ == "__main__":
    unittest.main()
