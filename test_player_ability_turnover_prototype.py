"""
Focused unit tests for player_ability_turnover_prototype.py -- pure
classification logic, no live API calls (so this suite runs offline
and fast). Root-level, not in tests/ (Codex's).

Run with: python3 -m unittest test_player_ability_turnover_prototype -v
"""
import unittest

from player_ability_turnover_prototype import classify_turnover_description


class TestTurnoverClassification(unittest.TestCase):
    def test_lost_ball_is_handling_error(self):
        self.assertEqual(classify_turnover_description("Caldwell-Pope Lost Ball Turnover (P1.T2)"), "handling_error")

    def test_traveling_is_handling_error(self):
        self.assertEqual(classify_turnover_description("Wood Traveling Turnover (P1.T4)"), "handling_error")

    def test_bad_pass_never_counted_as_handling_error(self):
        result = classify_turnover_description("Jokic Bad Pass Turnover (P1.T1)")
        self.assertNotEqual(result, "handling_error")
        self.assertEqual(result, "excluded")

    def test_offensive_foul_never_counted_as_handling_error(self):
        result = classify_turnover_description("Brickowski Offensive Foul Turnover (P1.T5)")
        self.assertNotEqual(result, "handling_error")
        self.assertEqual(result, "excluded")

    def test_unrecognized_real_subtype_reported_not_silently_bucketed(self):
        # Real subtypes found in this project's own investigation that
        # a small starter keyword list doesn't yet recognize -- MUST
        # come back as an explicit "needs more work" bucket, never
        # silently misclassified as handling_error.
        result = classify_turnover_description("Ellison 3 Second Violation Turnover (P2.T4)")
        self.assertEqual(result, "other_unclassified")
        self.assertNotEqual(result, "handling_error")

    def test_non_turnover_text_not_misclassified(self):
        self.assertEqual(classify_turnover_description("Jordan makes 26' 3PT Jump Shot"), "not_a_turnover")

    def test_empty_description(self):
        self.assertEqual(classify_turnover_description(""), "other_unclassified")


if __name__ == "__main__":
    unittest.main()
