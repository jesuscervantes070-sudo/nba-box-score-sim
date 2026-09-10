"""
Unit tests for foul_estimation.py. Real cache reads faked via
monkeypatching -- no network dependency.
"""
import unittest
from unittest.mock import patch

import foul_estimation as fe
import foul_analysis as fa
from foul_analysis import PlayerFoulRow


def _row(pid, name, season, sfd=0, nsfd=0, and1=0, sfc=0, nsfc=0, fga=1000, minutes=2000):
    return PlayerFoulRow(
        player_id=str(pid), player_name=name, season=season,
        shooting_foul_drawn=sfd, nonshooting_def_foul_drawn=nsfd, and_ones=and1,
        total_drawn=sfd + nsfd, shooting_foul_committed=sfc, nonshooting_def_foul_committed=nsfc,
        offensive_foul_committed=0, total_committed=sfc + nsfc,
        fga=fga, fta=200, minutes=minutes, rim_paint_fga=400, touches=3000, drives=500,
        usg_pct=0.2, reb_pct=0.1, ast_pct=0.15, gp=70,
    )


class TestEstimateFoulDrawing(unittest.TestCase):
    def test_missing_cache_returns_no_evidence(self):
        with patch.object(fe.fli, "load_foul_cache", return_value=None):
            report = fe.estimate_foul_drawing("Nobody", "2020-21", ["2020-21"])
        self.assertIsNone(report.rating_0_99)

    def test_missing_player_returns_no_evidence(self):
        with patch.object(fe.fli, "load_foul_cache", return_value={"games_done": [], "games_total": 10}), \
             patch.object(fe.fa, "build_player_foul_rows", return_value=[]):
            report = fe.estimate_foul_drawing("Nobody", "2020-21", ["2020-21"])
        self.assertIsNone(report.rating_0_99)

    def test_normal_case_produces_rating(self):
        rows = [_row(1, "Player X", "2020-21", sfd=50, fga=1000),
                _row(2, "Player Y", "2020-21", sfd=20, fga=1000)]
        with patch.object(fe.fli, "load_foul_cache", return_value={"games_done": [1] * 60, "games_total": 60}), \
             patch.object(fe.fa, "build_player_foul_rows", return_value=rows), \
             patch.object(fe, "_resolve_draw_params", return_value=(0.6, 50.0, "fga", "calibrated")):
            report = fe.estimate_foul_drawing("Player X", "2020-21", ["2020-21"])
        self.assertIsNotNone(report.rating_0_99)
        self.assertEqual(report.shooting_foul_drawn, 50)

    def test_low_exposure_not_silently_elite(self):
        rows = [_row(1, "Tiny Sample", "2020-21", sfd=5, fga=5)]
        with patch.object(fe.fli, "load_foul_cache", return_value={"games_done": [1], "games_total": 60}), \
             patch.object(fe.fa, "build_player_foul_rows", return_value=rows), \
             patch.object(fe, "_resolve_draw_params", return_value=(0.6, 50.0, "fga", "calibrated")):
            report = fe.estimate_foul_drawing("Tiny Sample", "2020-21", ["2020-21"], min_exposure=30.0)
        self.assertIsNone(report.rating_0_99)
        self.assertEqual(report.confidence, "low")


class TestHistoricalDenominatorFallback(unittest.TestCase):
    def test_pre_tracking_season_falls_back_and_flags_low_confidence(self):
        rows = [_row(1, "Old Timer", "1996-97", sfd=50, fga=1000)]
        with patch.object(fe.fli, "load_foul_cache", return_value={"games_done": [1] * 60, "games_total": 60}), \
             patch.object(fe.fa, "build_player_foul_rows", return_value=rows), \
             patch.object(fe, "_resolve_draw_params", return_value=(0.9, 50.0, "drives", "calibrated")):
            report = fe.estimate_foul_drawing("Old Timer", "1996-97", ["1996-97"])
        self.assertEqual(report.exposure_denominator, fe.HISTORICAL_DRAW_DENOMINATOR)
        self.assertEqual(report.confidence, "low")
        self.assertIsNotNone(report.rating_0_99)

    def test_tracking_era_season_uses_calibrated_denominator(self):
        rows = [_row(1, "Modern Guy", "2020-21", sfd=50, fga=1000)]
        with patch.object(fe.fli, "load_foul_cache", return_value={"games_done": [1] * 60, "games_total": 60}), \
             patch.object(fe.fa, "build_player_foul_rows", return_value=rows), \
             patch.object(fe, "_resolve_draw_params", return_value=(0.9, 50.0, "drives", "calibrated")):
            report = fe.estimate_foul_drawing("Modern Guy", "2020-21", ["2020-21"])
        self.assertEqual(report.exposure_denominator, "drives")


class TestEstimateFoulDiscipline(unittest.TestCase):
    def test_rating_inverted_more_fouls_is_worse(self):
        # A wider reference population gives the percentile ranking real
        # resolution -- with only 2 players, both shrunk estimates can
        # land in the same coarse bucket relative to each other even
        # though their RAW rates clearly differ (a real property of
        # percentile-ranking against a tiny population, not a bug).
        rows = [_row(1, "Disciplined", "2020-21", sfc=5, nsfc=5, minutes=3000),
                _row(2, "Foul Prone", "2020-21", sfc=40, nsfc=40, minutes=3000)]
        rows += [_row(10 + i, f"Filler{i}", "2020-21", sfc=10 + i, nsfc=10, minutes=3000) for i in range(8)]
        with patch.object(fe.fli, "load_foul_cache", return_value={"games_done": [1] * 60, "games_total": 60}), \
             patch.object(fe.fa, "build_player_foul_rows", return_value=rows), \
             patch.object(fe, "_resolve_disc_params", return_value=(0.6, 100.0, "minutes", "calibrated")):
            good = fe.estimate_foul_discipline("Disciplined", "2020-21", ["2020-21"])
            bad = fe.estimate_foul_discipline("Foul Prone", "2020-21", ["2020-21"])
        self.assertGreater(good.rating_0_99, bad.rating_0_99)

    def test_offensive_fouls_never_affect_discipline_rate(self):
        r1 = _row(1, "A", "2020-21", sfc=10, nsfc=10, minutes=3000)
        r1.offensive_foul_committed = 999  # should have zero effect on total_committed / rate
        with patch.object(fe.fli, "load_foul_cache", return_value={"games_done": [1] * 60, "games_total": 60}), \
             patch.object(fe.fa, "build_player_foul_rows", return_value=[r1]), \
             patch.object(fe, "_resolve_disc_params", return_value=(0.6, 0.0, "minutes", "calibrated")):
            report = fe.estimate_foul_discipline("A", "2020-21", ["2020-21"])
        self.assertAlmostEqual(report.raw_rate, 20 / 3000)


class TestAttributeEstimateBridge(unittest.TestCase):
    def test_unestimated_when_no_rating(self):
        report = fe.FoulDrawingReport(player_name="X", season="2020-21")
        est = fe.result_to_attribute_estimate_drawing(report)
        self.assertIsNone(est.value)

    def test_serializes_with_confidence(self):
        report = fe.FoulDrawingReport(player_name="X", season="2020-21", rating_0_99=75.0, exposure=500, confidence="high")
        est = fe.result_to_attribute_estimate_drawing(report)
        self.assertEqual(est.value, 75.0)
        self.assertEqual(est.confidence, 0.8)


if __name__ == "__main__":
    unittest.main()
