"""Unit tests for transition_rate_ingestion.py. All network calls faked, per this project's own
existing ingestion-test convention."""
import json
import unittest
from unittest.mock import patch

import pandas as pd

import transition_rate_ingestion as tri


def _row(action_number, person_id, team_id, description, action_type, sub_type="", clock="PT12M00.00S", period=1):
    return {
        "gameId": "0022500001", "actionNumber": action_number, "clock": clock, "period": period,
        "teamId": team_id, "personId": person_id, "description": description,
        "actionType": action_type, "subType": sub_type,
    }


class TestClockParsing(unittest.TestCase):
    def test_parses_minutes_and_seconds(self):
        self.assertEqual(tri._clock_to_seconds("PT11M38.00S"), 11 * 60 + 38.0)
        self.assertEqual(tri._clock_to_seconds("PT00M04.50S"), 4.5)

    def test_missing_or_malformed_returns_none(self):
        self.assertIsNone(tri._clock_to_seconds(None))
        self.assertIsNone(tri._clock_to_seconds(""))
        self.assertIsNone(tri._clock_to_seconds("garbage"))


class TestExtractTransitionRateSample(unittest.TestCase):
    def _patch_endpoints(self, game_ids, pbp_by_game):
        class FakeGameFinder:
            def __init__(self, *a, **kw):
                self.df = pd.DataFrame({
                    "GAME_ID": game_ids,
                    "GAME_DATE": [f"2025-10-{i + 1:02d}" for i in range(len(game_ids))],
                })

            def get_data_frames(self):
                return [self.df]

        class FakePBP:
            def __init__(self, game_id, *a, **kw):
                self._df = pbp_by_game[game_id]

            def get_data_frames(self):
                return [self._df]

        return (
            patch("nba_api.stats.endpoints.leaguegamefinder.LeagueGameFinder", FakeGameFinder),
            patch("nba_api.stats.endpoints.playbyplayv3.PlayByPlayV3", FakePBP),
        )

    def test_defensive_rebound_followed_by_a_shot_measures_real_elapsed_time(self):
        df = pd.DataFrame([
            _row(10, 201, 100, "Player REBOUND (Off:0 Def:1)", "Rebound", clock="PT11M00.00S"),
            _row(12, 202, 100, "MISS Player Jump Shot", "Missed Shot", clock="PT10M53.00S"),
        ])
        gf_patch, pbp_patch = self._patch_endpoints(["0022500001"], {"0022500001": df})
        with gf_patch, pbp_patch:
            payload = tri.extract_transition_rate_sample("TEST-TRANSITION-RATE-SEASON", sample_size=1, force=True)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["source"], "DEFENSIVE_REBOUND")
        self.assertAlmostEqual(payload["events"][0]["elapsed_seconds"], 7.0)

    def test_offensive_rebound_is_excluded(self):
        df = pd.DataFrame([
            _row(10, 201, 100, "Player REBOUND (Off:1 Def:0)", "Rebound", clock="PT11M00.00S"),
        ])
        gf_patch, pbp_patch = self._patch_endpoints(["0022500002"], {"0022500002": df})
        with gf_patch, pbp_patch:
            payload = tri.extract_transition_rate_sample("TEST-TRANSITION-RATE-SEASON", sample_size=1, force=True)
        self.assertEqual(payload["events"], [])

    def test_steal_and_bad_pass_interception_distinguished_via_companion_row(self):
        df = pd.DataFrame([
            _row(20, 301, 100, "Player Bad Pass Turnover", "Turnover", sub_type="Bad Pass", clock="PT09M00.00S"),
            _row(20, 302, 200, "Player STEAL (1 STL)", ""),
            _row(22, 303, 200, "Player Jump Shot", "Made Shot", clock="PT08M55.00S"),
            _row(30, 304, 100, "Player Bad Pass Turnover", "Turnover", sub_type="Bad Pass", clock="PT07M00.00S"),
        ])
        gf_patch, pbp_patch = self._patch_endpoints(["0022500003"], {"0022500003": df})
        with gf_patch, pbp_patch:
            payload = tri.extract_transition_rate_sample("TEST-TRANSITION-RATE-SEASON", sample_size=1, force=True)
        sources = [e["source"] for e in payload["events"]]
        self.assertIn("LIVE_BAD_PASS_INTERCEPTION", sources)
        # the SECOND bad pass has no STEAL companion row -> dead ball (no steal credited), excluded here
        # because it has no qualifying outcome row either -- confirms no false LIVE classification.
        self.assertEqual(sources.count("LIVE_BAD_PASS_INTERCEPTION"), 1)

    def test_lost_ball_turnover_is_excluded_not_guessed(self):
        """Coverage note (see module docstring): LOOSE_BALL_RECOVERY timing is NOT resolved by
        this extraction -- a bare "Lost Ball" turnover row with no STEAL companion must never be
        misclassified into another source."""
        df = pd.DataFrame([
            _row(10, 201, 100, "Player Lost Ball Turnover", "Turnover", sub_type="Lost Ball", clock="PT11M00.00S"),
        ])
        gf_patch, pbp_patch = self._patch_endpoints(["0022500004"], {"0022500004": df})
        with gf_patch, pbp_patch:
            payload = tri.extract_transition_rate_sample("TEST-TRANSITION-RATE-SEASON", sample_size=1, force=True)
        self.assertEqual(payload["events"], [])

    def test_period_boundary_crossing_is_dropped_not_measured(self):
        df = pd.DataFrame([
            _row(10, 201, 100, "Player REBOUND (Off:0 Def:1)", "Rebound", clock="PT00M02.00S", period=1),
            _row(12, 202, 100, "MISS Player Jump Shot", "Missed Shot", clock="PT11M50.00S", period=2),
        ])
        gf_patch, pbp_patch = self._patch_endpoints(["0022500005"], {"0022500005": df})
        with gf_patch, pbp_patch:
            payload = tri.extract_transition_rate_sample("TEST-TRANSITION-RATE-SEASON", sample_size=1, force=True)
        self.assertEqual(payload["events"], [])

    def test_unreachable_network_raises_connection_error_never_fabricates_data(self):
        with patch("nba_api.stats.endpoints.leaguegamefinder.LeagueGameFinder", side_effect=Exception("boom")):
            with self.assertRaises(ConnectionError):
                tri.extract_transition_rate_sample("TEST-TRANSITION-RATE-SEASON", sample_size=1, force=True)


class TestSummarize(unittest.TestCase):
    def test_summarize_matches_hand_computed_distribution(self, tmp_season="TEST-TRANSITION-SUMMARY-SEASON"):
        payload = {
            "cache_version": tri.TRANSITION_RATE_CACHE_VERSION, "season": tmp_season,
            "sample_size": 1, "failed_game_ids": [],
            "events": [
                {"source": "DEFENSIVE_REBOUND", "elapsed_seconds": 4.0, "game_id": "g1"},
                {"source": "DEFENSIVE_REBOUND", "elapsed_seconds": 10.0, "game_id": "g1"},
                {"source": "MADE_BASKET_INBOUND", "elapsed_seconds": 16.0, "game_id": "g1"},
            ],
        }
        path = tri._transition_rate_cache_path(tmp_season)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f)
        try:
            summaries = tri.summarize_transition_rate_sample(tmp_season)
            dreb = summaries["DEFENSIVE_REBOUND"]
            self.assertEqual(dreb.n, 2)
            self.assertAlmostEqual(dreb.mean_seconds, 7.0)
            self.assertAlmostEqual(dreb.share_under_8s, 0.5)
            self.assertAlmostEqual(dreb.share_under_12s, 1.0)
        finally:
            path.unlink(missing_ok=True)

    def test_summarize_raises_when_no_cache_exists(self):
        with self.assertRaises(FileNotFoundError):
            tri.summarize_transition_rate_sample("TEST-TRANSITION-SEASON-NEVER-EXTRACTED")


if __name__ == "__main__":
    unittest.main()
