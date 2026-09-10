"""
Unit tests for turnover_ingestion.py -- classification correctness and
resumable/interruption-safe/deterministic ingestion. All network calls are
faked; no real API access happens in this test file (real API behavior was
checked interactively and is documented in the module docstring, not
re-verified here on every test run).
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import turnover_ingestion as ti


class FakeGame:
    def __init__(self, game_id):
        self.game_id = game_id


class TestClassification(unittest.TestCase):
    def test_bad_pass_never_counts_as_handling(self):
        self.assertEqual(ti.classify_subtype("Bad Pass"), ti.CATEGORY_BAD_PASS)
        self.assertEqual(ti.classify_subtype("Out of Bounds - Bad Pass Turnover"), ti.CATEGORY_BAD_PASS)

    def test_offensive_foul_never_counts_as_handling(self):
        self.assertEqual(ti.classify_subtype("Offensive Foul Turnover"), ti.CATEGORY_OFFENSIVE_FOUL)
        self.assertEqual(ti.classify_subtype("Foul"), ti.CATEGORY_OFFENSIVE_FOUL)

    def test_illegal_screen_never_counts_as_handling(self):
        self.assertEqual(ti.classify_subtype("Illegal Pick"), ti.CATEGORY_OFFENSIVE_FOUL)

    def test_true_handling_violations_count(self):
        for subtype in ("Lost Ball", "Traveling", "Double Dribble", "Discontinue Dribble",
                         "Palming Turnover", "Backcourt Turnover"):
            self.assertEqual(ti.classify_subtype(subtype), ti.CATEGORY_HANDLING, subtype)

    def test_team_system_violations_classified(self):
        for subtype in ("Shot Clock Turnover", "3 Second Violation", "8 Second Violation"):
            self.assertEqual(ti.classify_subtype(subtype), ti.CATEGORY_TEAM_SYSTEM, subtype)

    def test_unknown_description_preserved_not_discarded(self):
        self.assertEqual(ti.classify_subtype("Some Brand New Subtype Nobody Has Seen"), ti.CATEGORY_UNKNOWN)
        self.assertEqual(ti.classify_subtype(""), ti.CATEGORY_UNKNOWN)
        self.assertEqual(ti.classify_subtype(None), ti.CATEGORY_UNKNOWN)

    def test_team_level_event_detected_by_franchise_id(self):
        self.assertTrue(ti._is_team_event(1610612743))
        self.assertFalse(ti._is_team_event(203999))
        self.assertFalse(ti._is_team_event(None))

    def test_reviewed_overturned_turnover_excluded_entirely(self):
        """A real replay-overturned event ('Smith No Turnover', empty
        subType) is not a turnover of any kind -- must not become
        handling_error, bad_pass, or even other_unclassified."""
        self.assertTrue(ti._is_reviewed_not_a_turnover("Smith No Turnover (P2.T8)"))
        self.assertFalse(ti._is_reviewed_not_a_turnover("Smith Bad Pass Turnover (P1.T1)"))
        self.assertFalse(ti._is_reviewed_not_a_turnover(""))
        self.assertFalse(ti._is_reviewed_not_a_turnover(None))


def _fake_events(game_id):
    """Deterministic fake per-game events keyed off game_id so different
    games produce different (but reproducible) data."""
    base = {
        "0000000001": [
            {"player_id": 1, "player_name": "Alpha", "category": ti.CATEGORY_HANDLING, "subtype": "Lost Ball", "description": "Alpha Lost Ball Turnover"},
            {"player_id": 1, "player_name": "Alpha", "category": ti.CATEGORY_BAD_PASS, "subtype": "Bad Pass", "description": "Alpha Bad Pass Turnover"},
            {"player_id": 2, "player_name": "Beta", "category": ti.CATEGORY_UNKNOWN, "subtype": "Mystery Turnover", "description": "Beta Mystery Turnover"},
        ],
        "0000000002": [
            {"player_id": 1, "player_name": "Alpha", "category": ti.CATEGORY_OFFENSIVE_FOUL, "subtype": "Offensive Foul Turnover", "description": "Alpha Offensive Foul Turnover"},
            {"player_id": 3, "player_name": "Gamma", "category": ti.CATEGORY_TEAM_SYSTEM, "subtype": "3 Second Violation", "description": "Gamma 3 Second Violation Turnover"},
        ],
        "0000000003": [
            {"player_id": 2, "player_name": "Beta", "category": ti.CATEGORY_HANDLING, "subtype": "Traveling", "description": "Beta Traveling Turnover"},
        ],
    }
    return base[game_id]


class TestResumableIngestion(unittest.TestCase):
    def setUp(self):
        self.season = "TEST-SEASON-9999"
        # Clean slate: remove any leftover cache from a previous failed run.
        cache_path = ti._turnover_cache_path(self.season)
        if cache_path.exists():
            cache_path.unlink()
        self.games = [FakeGame(gid) for gid in ("0000000001", "0000000002", "0000000003")]

    def tearDown(self):
        cache_path = ti._turnover_cache_path(self.season)
        if cache_path.exists():
            cache_path.unlink()
        season_dir = cache_path.parent
        if season_dir.exists() and not any(season_dir.iterdir()):
            season_dir.rmdir()

    def _run(self, **kwargs):
        with patch("loader.load_schedule", return_value=self.games), \
             patch.object(ti, "fetch_game_turnovers", side_effect=lambda gid, **kw: _fake_events(gid)):
            return ti.ingest_season_turnovers(self.season, sleep_between=0, save_every=1, **kwargs)

    def test_full_run_aggregates_correctly(self):
        state = self._run()
        self.assertEqual(sorted(state["games_done"]), ["0000000001", "0000000002", "0000000003"])
        alpha = state["players"]["1"]
        self.assertEqual(alpha["handling_error"], 1)
        self.assertEqual(alpha["bad_pass"], 1)
        self.assertEqual(alpha["offensive_foul_nonhandle"], 1)
        self.assertEqual(alpha["total"], 3)
        beta = state["players"]["2"]
        self.assertEqual(beta["handling_error"], 1)
        self.assertEqual(beta["other_unclassified"], 1)
        self.assertEqual(state["unknown_subtypes"]["Mystery Turnover"]["count"], 1)

    def test_resumable_skips_completed_games(self):
        seen_calls = []

        def fake_fetch(gid, **kw):
            seen_calls.append(gid)
            return _fake_events(gid)

        with patch("loader.load_schedule", return_value=self.games), \
             patch.object(ti, "fetch_game_turnovers", side_effect=fake_fetch):
            ti.ingest_season_turnovers(self.season, sleep_between=0, save_every=1, max_games=1)
            self.assertEqual(seen_calls, ["0000000001"])
            # Second run: game 1 already done, must not be re-fetched.
            ti.ingest_season_turnovers(self.season, sleep_between=0, save_every=1)
            self.assertEqual(seen_calls, ["0000000001", "0000000002", "0000000003"])

    def test_interrupted_ingestion_does_not_corrupt_completed_data(self):
        call_count = {"n": 0}

        def flaky_fetch(gid, **kw):
            call_count["n"] += 1
            if gid == "0000000002":
                raise RuntimeError("simulated interruption / API failure")
            return _fake_events(gid)

        with patch("loader.load_schedule", return_value=self.games), \
             patch.object(ti, "fetch_game_turnovers", side_effect=flaky_fetch):
            state = ti.ingest_season_turnovers(self.season, sleep_between=0, save_every=1)

        self.assertIn("0000000001", state["games_done"])
        self.assertIn("0000000003", state["games_done"])
        self.assertIn("0000000002", state["games_failed"])
        self.assertNotIn("0000000002", state["games_done"])
        # The cache file on disk must be valid JSON, not truncated.
        on_disk = ti.load_turnover_cache(self.season)
        self.assertEqual(on_disk["games_done"], state["games_done"])

        # A later run retries only the failed game, not the completed ones.
        with patch("loader.load_schedule", return_value=self.games), \
             patch.object(ti, "fetch_game_turnovers", side_effect=lambda gid, **kw: _fake_events(gid)):
            state2 = ti.ingest_season_turnovers(self.season, sleep_between=0, save_every=1)
        self.assertEqual(sorted(state2["games_done"]), ["0000000001", "0000000002", "0000000003"])
        self.assertEqual(state2["games_failed"], [])

    def test_aggregation_is_deterministic_regardless_of_batching(self):
        state_a = self._run()
        cache_path = ti._turnover_cache_path(self.season)
        cache_path.unlink()

        # Same real inputs, processed one game at a time with an
        # interruption between each, must reach the identical final state.
        with patch("loader.load_schedule", return_value=self.games), \
             patch.object(ti, "fetch_game_turnovers", side_effect=lambda gid, **kw: _fake_events(gid)):
            for _ in range(3):
                ti.ingest_season_turnovers(self.season, sleep_between=0, save_every=1, max_games=1)
                # widen max_games each loop is unrealistic; instead just
                # re-run repeatedly with no max -- idempotent no-op once done.
            state_b = ti.ingest_season_turnovers(self.season, sleep_between=0, save_every=1)

        self.assertEqual(state_a["players"], state_b["players"])
        self.assertEqual(state_a["unknown_subtypes"], state_b["unknown_subtypes"])

    def test_no_future_season_leakage(self):
        """Ingesting one season never reads or writes another season's cache file."""
        state = self._run()
        other_season_path = ti._turnover_cache_path("2099-00")
        self.assertFalse(other_season_path.exists())
        try:
            self.assertEqual(state["season"], self.season)
        finally:
            season_dir = ti._turnover_cache_path("2099-00").parent
            if season_dir.exists() and not any(season_dir.iterdir()):
                season_dir.rmdir()

    def test_cache_version_mismatch_requires_explicit_force(self):
        self._run()
        cache_path = ti._turnover_cache_path(self.season)
        with open(cache_path) as f:
            state = json.load(f)
        state["cache_version"] = 999
        with open(cache_path, "w") as f:
            json.dump(state, f)
        with patch("loader.load_schedule", return_value=self.games):
            with self.assertRaises(RuntimeError):
                ti.ingest_season_turnovers(self.season, sleep_between=0)
            # force=True rebuilds cleanly instead of crashing forever.
            with patch.object(ti, "fetch_game_turnovers", side_effect=lambda gid, **kw: _fake_events(gid)):
                rebuilt = ti.ingest_season_turnovers(self.season, sleep_between=0, force=True)
        self.assertEqual(rebuilt["cache_version"], ti.TURNOVER_CACHE_VERSION)


class TestFetchGameTurnoversFiltersReviewedEvents(unittest.TestCase):
    def test_no_turnover_row_excluded_end_to_end(self):
        import pandas as pd

        fake_df = pd.DataFrame([
            {"actionType": "Turnover", "subType": "Bad Pass", "personId": 1, "playerName": "Real",
             "playerNameI": "R. Real", "description": "Real Bad Pass Turnover (P1.T1)"},
            {"actionType": "Turnover", "subType": "", "personId": 2, "playerName": "Smith",
             "playerNameI": "J. Smith", "description": "Smith No Turnover (P2.T8)"},
            {"actionType": "Shot", "subType": "", "personId": 3, "playerName": "Other",
             "playerNameI": "O. Other", "description": "Other Made Shot"},
        ])

        class FakePBP:
            def __init__(self, *a, **kw):
                pass

            def get_data_frames(self):
                return [fake_df]

        with patch("nba_api.stats.endpoints.playbyplayv3.PlayByPlayV3", FakePBP):
            events = ti.fetch_game_turnovers("0000000099")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["player_name"], "Real")


class TestResolveFullName(unittest.TestCase):
    def test_resolves_real_full_name_across_eras(self):
        self.assertEqual(ti.resolve_full_name(893), "Michael Jordan")  # 1990s era
        self.assertEqual(ti.resolve_full_name(203999), "Nikola Jokić")  # modern era

    def test_unknown_id_returns_none_not_a_guess(self):
        self.assertIsNone(ti.resolve_full_name(-1))


class TestSummarizeCoverage(unittest.TestCase):
    def test_classified_and_unknown_percentages(self):
        state = ti._new_state("X")
        ti._apply_events(state, [
            {"player_id": 1, "player_name": "A", "category": ti.CATEGORY_HANDLING, "subtype": "Lost Ball", "description": "d"},
            {"player_id": 1, "player_name": "A", "category": ti.CATEGORY_BAD_PASS, "subtype": "Bad Pass", "description": "d"},
            {"player_id": 1, "player_name": "A", "category": ti.CATEGORY_UNKNOWN, "subtype": "Weird", "description": "d"},
        ])
        summary = ti.summarize_coverage(state)
        self.assertEqual(summary["total_turnover_events"], 3)
        self.assertAlmostEqual(summary["unknown_pct"], 33.33, places=1)
        self.assertAlmostEqual(summary["classified_pct"], 66.67, places=1)


if __name__ == "__main__":
    unittest.main()
