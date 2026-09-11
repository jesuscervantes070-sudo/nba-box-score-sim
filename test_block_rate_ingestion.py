"""Unit tests for block_rate_ingestion.py. All network calls faked, per this project's own
existing ingestion-test convention (see test_foul_ingestion.py/test_shot_zone_ingestion.py)."""
import json
import unittest
from unittest.mock import patch

import pandas as pd

import block_rate_ingestion as bri


class TestClassifyShotFamily(unittest.TestCase):
    def test_three_point_is_exact_not_a_proxy(self):
        self.assertEqual(bri.classify_shot_family(3, 24.0), "THREE_POINT")
        self.assertEqual(bri.classify_shot_family(3, 0.5), "THREE_POINT")  # heave/backcourt -- still exact on shot_value

    def test_rim_boundary(self):
        self.assertEqual(bri.classify_shot_family(2, 0.0), "RIM")
        self.assertEqual(bri.classify_shot_family(2, 4.0), "RIM")

    def test_floater_short_boundary(self):
        self.assertEqual(bri.classify_shot_family(2, 4.1), "FLOATER_SHORT")
        self.assertEqual(bri.classify_shot_family(2, 13.0), "FLOATER_SHORT")

    def test_midrange_boundary(self):
        self.assertEqual(bri.classify_shot_family(2, 13.1), "MIDRANGE")
        self.assertEqual(bri.classify_shot_family(2, 22.0), "MIDRANGE")

    def test_missing_distance_or_value_is_unknown_not_zero(self):
        self.assertEqual(bri.classify_shot_family(2, None), "UNKNOWN")
        self.assertEqual(bri.classify_shot_family(None, 5.0), "UNKNOWN")


def _row(action_number, person_id, player_name, team_tricode, description, action_type,
         sub_type="", shot_distance=None, shot_result="", is_field_goal=0, shot_value=0):
    return {
        "gameId": "0022500001", "actionNumber": action_number, "personId": person_id,
        "playerName": player_name, "teamTricode": team_tricode, "description": description,
        "actionType": action_type, "subType": sub_type, "shotDistance": shot_distance,
        "shotResult": shot_result, "isFieldGoal": is_field_goal, "shotValue": shot_value,
    }


class TestExtractBlockRateSample(unittest.TestCase):
    """Validates the exact pairing logic audited directly against real playbyplayv3 rows: a
    blocked shot is a "Missed Shot" row followed by a SAME-actionNumber row with an EMPTY
    actionType whose description matches "<Name> BLOCK (<N> BLK)" -- personId on THAT row is the
    real blocker, never inferred from a generic PLAYER1_ID/PLAYER2_ID/PLAYER3_ID field."""

    def _patch_endpoints(self, game_ids, pbp_by_game):
        class FakeGameFinder:
            def __init__(self, *a, **kw):
                self.df = pd.DataFrame({
                    "GAME_ID": game_ids, "GAME_DATE": [f"2025-{10 + i // 28:02d}-{1 + i % 28:02d}" for i in range(len(game_ids))],
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

    def test_blocked_shot_pairs_by_action_number_and_credits_the_correct_blocker(self):
        df = pd.DataFrame([
            _row(7, 201, "Hukporti", "NYK", "MISS Hukporti 3' Driving Layup", "Missed Shot",
                 sub_type="Driving Layup Shot", shot_distance=3.0, shot_result="Missed", is_field_goal=1, shot_value=2),
            _row(7, 305, "Miller", "CHA", "Miller BLOCK (1 BLK)", ""),
            _row(9, 202, "Ball", "CHA", "Ball 25' 3PT Jump Shot", "Made Shot",
                 sub_type="Jump Shot", shot_distance=25.0, shot_result="Made", is_field_goal=1, shot_value=3),
        ])
        gf_patch, pbp_patch = self._patch_endpoints(["0022500001"], {"0022500001": df})
        with gf_patch, pbp_patch:
            payload = bri.extract_block_rate_sample("2025-26", sample_size=1, force=True)

        self.assertEqual(len(payload["shot_events"]), 2)
        self.assertEqual(len(payload["block_events"]), 1)
        block = payload["block_events"][0]
        self.assertEqual(block["blocker_id"], 305)
        self.assertEqual(block["shooter_id"], 201)
        self.assertEqual(block["shot_distance"], 3.0)
        self.assertEqual(block["shot_value"], 2)

    def test_unpaired_block_row_is_excluded_never_guessed(self):
        """A block row whose actionNumber has no matching Missed Shot row (a real, rare schema
        edge case) must be dropped, not attributed to a fabricated shot."""
        df = pd.DataFrame([
            _row(7, 305, "Miller", "CHA", "Miller BLOCK (1 BLK)", ""),  # no paired Missed Shot at all
        ])
        gf_patch, pbp_patch = self._patch_endpoints(["0022500002"], {"0022500002": df})
        with gf_patch, pbp_patch:
            payload = bri.extract_block_rate_sample("2025-26", sample_size=1, force=True)
        self.assertEqual(payload["block_events"], [])

    def test_made_shot_never_produces_a_block_event(self):
        df = pd.DataFrame([
            _row(9, 202, "Ball", "CHA", "Ball 25' 3PT Jump Shot", "Made Shot",
                 shot_distance=25.0, shot_result="Made", is_field_goal=1, shot_value=3),
        ])
        gf_patch, pbp_patch = self._patch_endpoints(["0022500003"], {"0022500003": df})
        with gf_patch, pbp_patch:
            payload = bri.extract_block_rate_sample("2025-26", sample_size=1, force=True)
        self.assertEqual(payload["block_events"], [])
        self.assertEqual(len(payload["shot_events"]), 1)
        self.assertTrue(payload["shot_events"][0]["made"])

    def test_unreachable_network_raises_connection_error_never_fabricates_data(self):
        with patch("nba_api.stats.endpoints.leaguegamefinder.LeagueGameFinder", side_effect=Exception("boom")):
            with self.assertRaises(ConnectionError):
                bri.extract_block_rate_sample("2025-26", sample_size=1, force=True)


class TestSummarizeAndReconcile(unittest.TestCase):
    def test_summarize_matches_hand_computed_totals(self, tmp_season="TEST-BLOCK-SEASON"):
        payload = {
            "cache_version": bri.BLOCK_RATE_CACHE_VERSION, "season": tmp_season,
            "sample_size": 1, "failed_game_ids": [],
            "shot_events": [
                {"shot_value": 2, "shot_distance": 2.0, "made": True},   # RIM make
                {"shot_value": 2, "shot_distance": 2.0, "made": False},  # RIM miss (unblocked)
                {"shot_value": 3, "shot_distance": 24.0, "made": False},  # THREE_POINT miss
            ],
            "block_events": [
                {"shot_value": 2, "shot_distance": 2.0},  # a RIM block
            ],
        }
        path = bri._block_rate_cache_path(tmp_season)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f)
        try:
            rows = bri.summarize_block_rate_sample(tmp_season)
            rim = rows["RIM"]
            self.assertEqual(rim.fga, 2)
            self.assertEqual(rim.fgm, 1)
            self.assertEqual(rim.blocks, 1)
            self.assertEqual(rim.unblocked_attempts, 1)
            self.assertAlmostEqual(rim.p_make_given_not_blocked, 1.0)  # 1 make / 1 unblocked attempt
            three = rows["THREE_POINT"]
            self.assertEqual(three.fga, 1)
            self.assertEqual(three.blocks, 0)
        finally:
            path.unlink(missing_ok=True)

    def test_summarize_raises_when_no_cache_exists(self):
        with self.assertRaises(FileNotFoundError):
            bri.summarize_block_rate_sample("TEST-BLOCK-SEASON-NEVER-EXTRACTED")


if __name__ == "__main__":
    unittest.main()
