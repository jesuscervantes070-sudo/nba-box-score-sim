"""
Focused tests for Phase 11 (player_tendencies_analysis.py +
player_tendencies_estimation.py). Kept minimal per this phase's own
efficiency directive.
"""
import unittest
from unittest.mock import patch

import player_tendencies_analysis as pta
import player_tendencies_estimation as pte
from player_tendencies_analysis import PlayerTendencyRow


def _row(name, season, fga=200, fg3a=60, oreb=50, reb=300, minutes=1500, usg=0.2,
         ra_fga=100, paint_fga=50, mid_fga=50, pu_fga=80, cs_fga=80, drives=300,
         touches=1000, passes=1500, fta=80, team="Team A"):
    return PlayerTendencyRow(
        player_name=name, season=season, team_name=team, fga=fga, fg3a=fg3a, oreb=oreb, reb=reb,
        minutes=minutes, usg_pct=usg, restricted_area_fga=ra_fga, paint_non_ra_fga=paint_fga,
        midrange_fga=mid_fga, pull_up_fga=pu_fga, catch_shoot_fga=cs_fga, drives=drives,
        touches=touches, passes_made=passes, fta=fta,
    )


class TestCandidateMath(unittest.TestCase):
    def test_three_point_preference(self):
        row = _row("A", "2020-21", fga=200, fg3a=80)
        self.assertAlmostEqual(pta.three_point_preference(row), 0.4)

    def test_three_point_preference_low_exposure(self):
        row = _row("A", "2020-21", fga=10)
        self.assertIsNone(pta.three_point_preference(row, min_fga=100.0))

    def test_midrange_preference(self):
        row = _row("A", "2020-21", ra_fga=100, paint_fga=50, mid_fga=50)
        self.assertAlmostEqual(pta.midrange_preference(row), 50 / 200)

    def test_pullup_vs_catch(self):
        row = _row("A", "2020-21", pu_fga=60, cs_fga=40)
        self.assertAlmostEqual(pta.pullup_vs_catch(row), 0.6)

    def test_drive_aggression(self):
        row = _row("A", "2020-21", drives=250, touches=1000)
        self.assertAlmostEqual(pta.drive_aggression(row), 0.25)

    def test_pass_vs_shoot(self):
        row = _row("A", "2020-21", passes=1500, fga=200, fta=80)
        self.assertAlmostEqual(pta.pass_vs_shoot(row), 1500 / (1500 + 200 + 80))

    def test_orb_crash_bounds(self):
        row = _row("A", "2020-21", oreb=100, minutes=2000)
        rate = pta.orb_crash_rate(row, min_minutes=500.0)
        self.assertAlmostEqual(rate, 100 / 2000 * 36.0)

    def test_zero_exposure_all_none(self):
        row = _row("A", "2020-21", fga=0, touches=0, minutes=0)
        row.pull_up_fga = None
        row.catch_shoot_fga = None
        self.assertIsNone(pta.three_point_preference(row, min_fga=1.0))
        self.assertIsNone(pta.drive_aggression(row, min_touches=1.0))
        self.assertIsNone(pta.orb_crash_rate(row, min_minutes=1.0))


class TestDiagnostics(unittest.TestCase):
    def test_team_switch_portability_shape(self):
        rows = {
            "2020-21": [_row(f"P{i}", "2020-21", team="Team A") for i in range(10)],
            "2021-22": [_row(f"P{i}", "2021-22", team=("Team B" if i % 2 == 0 else "Team A")) for i in range(10)],
        }
        result = pta.team_switch_portability(rows, "2020-21", "2021-22")
        self.assertIn("three_point_preference", result)
        self.assertGreater(result["three_point_preference"]["n_switched"], 0)

    def test_combined_diagnostic_pass_shape(self):
        rows = {"2020-21": [_row(f"P{i}", "2020-21", fg3a=40 + i, usg=0.1 + i * 0.02) for i in range(10)],
                "2021-22": [_row(f"P{i}", "2021-22", fg3a=40 + i) for i in range(10)]}
        result = pta.combined_diagnostic_pass(rows, "2020-21")
        self.assertIn("three_point_preference", result)
        self.assertIsNotNone(result["three_point_preference"]["stability"])


class TestEstimator(unittest.TestCase):
    def test_pre_floor_returns_insufficient(self):
        result = pte.estimate_tendency("Old Timer", "1990-91", ["1990-91"], "drive_aggression")
        self.assertEqual(result.mode, "INSUFFICIENT")
        self.assertIsNone(result.latent_propensity)

    def test_low_exposure_low_confidence_not_extreme(self):
        rows = [_row("Low Sample Guy", "2020-21", touches=10)]
        with patch.object(pta, "build_player_tendency_rows", return_value=rows):
            result = pte.estimate_tendency("Low Sample Guy", "2020-21", ["2020-21"], "drive_aggression")
        self.assertIsNone(result.latent_propensity)
        self.assertEqual(result.confidence, "low")
        self.assertIn("uncertain/prior-dominated", result.coverage_note)

    def test_no_temporal_leakage(self):
        # A real population (not just the tracked player) so the
        # current-season league average is meaningfully different from
        # the tracked player's own rate -- otherwise a single-player
        # population trivially sets league_avg = the player's own rate,
        # which would mask a real leakage bug.
        history = {
            "2019-20": [_row("X", "2019-20", fg3a=140, fga=200)] + [_row(f"F{i}", "2019-20", fg3a=40, fga=200) for i in range(5)],
            "2020-21": [_row("X", "2020-21", fg3a=142, fga=205)] + [_row(f"F{i}", "2020-21", fg3a=40, fga=200) for i in range(5)],
            "2021-22": [_row("X", "2021-22", fg3a=0, fga=200)] + [_row(f"F{i}", "2021-22", fg3a=40, fga=200) for i in range(5)],  # must be excluded when as_of=2020-21
        }
        with patch.object(pta, "build_player_tendency_rows", side_effect=lambda s: history.get(s, [])):
            result = pte.estimate_tendency("X", "2020-21", ["2019-20", "2020-21", "2021-22"], "three_point_preference")
        # X's real rate is far above league average in both 2019-20/2020-21;
        # if 2021-22 (X's real rate collapsed to 0) leaked backward, this
        # would be pulled sharply negative instead of staying positive.
        self.assertGreater(result.latent_propensity, 0.5)

    def test_orb_crash_not_logit_clipped(self):
        """Real regression test: orb_crash is OREB-per-36 (unbounded,
        NOT a [0,1] share) -- a naive logit transform silently clips
        every real value above ~1.0 to the same constant, erasing all
        signal. A player with a real rate far above league average must
        get a clearly positive latent propensity, not a collapsed 0.0."""
        rows = ([_row("Elite Rebounder", "2020-21", oreb=300, minutes=2000)]
                + [_row(f"Filler{i}", "2020-21", oreb=60, minutes=2000) for i in range(9)])
        with patch.object(pta, "build_player_tendency_rows", return_value=rows):
            result = pte.estimate_tendency("Elite Rebounder", "2020-21", ["2020-21"], "orb_crash")
        self.assertGreater(result.latent_propensity, 0.3)

    def test_value_bounds_and_display_percentile(self):
        rows = [_row(f"P{i}", "2020-21", fg3a=20 + i * 5, fga=200) for i in range(10)]
        with patch.object(pta, "build_player_tendency_rows", return_value=rows):
            result = pte.estimate_tendency("P5", "2020-21", ["2020-21"], "three_point_preference")
        self.assertIsInstance(result.latent_propensity, float)
        self.assertTrue(0.0 <= result.display_percentile <= 99.0)


if __name__ == "__main__":
    unittest.main()
