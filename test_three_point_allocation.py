"""Tests for three-point shot allocation: lineup-slot symmetry and the diagnostic helpers."""
import unittest

import three_point_allocation_diagnostic as tad
import possession_orchestrator as po
import shot_resolution as sr
from possession_orchestrator import PlayerSimulationProfile

N = 40


class TestSymmetricOffset(unittest.TestCase):
    def test_deterministic_and_in_range(self):
        for n in (2, 4, 5):
            for key in ("a", "period1-possession7", "x|y|3"):
                v = po.symmetric_offset(key, n)
                self.assertEqual(v, po.symmetric_offset(key, n))
                self.assertTrue(0 <= v < n)

    def test_spreads_over_all_offsets(self):
        seen = {po.symmetric_offset(f"k{i}", 5) for i in range(200)}
        self.assertEqual(seen, {0, 1, 2, 3, 4})

    def test_placement_without_key_keeps_lineup_order(self):
        five = tuple(str(100000 + i) for i in range(5))
        zones = po.default_v0_zone_placement(five, five[0], po.SpatialZone.TOP_OF_KEY)
        self.assertEqual(zones[five[1]], po._V0_SPACING_CYCLE[0])

    def test_placement_with_key_is_a_rotation(self):
        five = tuple(str(100000 + i) for i in range(5))
        plain = po.default_v0_zone_placement(five, five[0], po.SpatialZone.TOP_OF_KEY)
        rotated = po.default_v0_zone_placement(five, five[0], po.SpatialZone.TOP_OF_KEY, rotation_key="p9")
        self.assertEqual(sorted(z.value for z in plain.values()), sorted(z.value for z in rotated.values()))


class TestIdenticalPlayerSymmetry(unittest.TestCase):
    def test_no_slot_dominates_when_players_are_identical(self):
        result = tad.permutation_test([{}] * 5, N, "test-identical")
        for share in result["mean_share_by_slot"]:
            self.assertGreater(share, 0.10)
            self.assertLess(share, 0.35)

    def test_lineup_permutation_invariance_of_player_share(self):
        result = tad.permutation_test([{}] * 5, N, "test-perm")
        for share in result["mean_share_by_player"]:
            self.assertAlmostEqual(share, 0.2, delta=0.06)

    def test_no_player_zero_fallback_bias(self):
        ids, profiles = tad.identical_players()
        res = tad.simulate_side(ids, profiles, "test-p0", N)
        starts = tad.shares([res["per_player"][p]["starts"] for p in ids])
        self.assertLess(starts[0], 0.35)

    def test_tendency_controls_allocation_after_slot_removal(self):
        kwargs = [{"three_point_preference": v} for v in (1.0, 0.5, 0.0, -0.5, -1.0)]
        result = tad.permutation_test(kwargs, N, "test-tendency")
        shares = result["mean_share_by_player"]
        self.assertGreater(shares[0], shares[4])

    def test_deterministic_allocation(self):
        ids, profiles = tad.identical_players()
        a = tad.simulate_side(ids, profiles, "test-det", 10)
        b = tad.simulate_side(ids, profiles, "test-det", 10)
        self.assertEqual(a["per_player"], b["per_player"])


class TestStackingBalance(unittest.TestCase):
    def test_elite_stacking_is_slot_balanced_and_ordered(self):
        rows = tad.balanced_stacking(tad.ELITE, n_sims=8, levels=(0, 5))
        self.assertGreater(rows[1]["team_3pt_pct"], rows[0]["team_3pt_pct"])

    def test_weak_stacking_is_slot_balanced_and_ordered(self):
        rows = tad.balanced_stacking(tad.WEAK, n_sims=8, levels=(0, 5))
        self.assertLess(rows[1]["team_3pt_pct"], rows[0]["team_3pt_pct"])


class TestHelpers(unittest.TestCase):
    def test_shares_sum_to_one(self):
        self.assertAlmostEqual(sum(tad.shares([3, 1, 0, 6])), 1.0)
        self.assertEqual(tad.shares([0, 0]), [0.0, 0.0])

    def test_herfindahl_and_top_k(self):
        self.assertAlmostEqual(tad.herfindahl([0.5, 0.5]), 0.5)
        self.assertAlmostEqual(tad.herfindahl([0.2] * 5), 0.2)
        self.assertAlmostEqual(tad.top_k_share([0.5, 0.3, 0.2], 2), 0.8)

    def test_spearman_handles_ties_and_monotonic(self):
        self.assertAlmostEqual(tad.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(tad.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)

    def test_within_team_r2_recovers_a_perfect_predictor(self):
        rows = [{"team": t, "x": x, "y": 2.0 * x} for t in ("A", "B") for x in (1.0, 2.0, 3.0, 4.0)]
        self.assertAlmostEqual(tad.within_team_r2(rows, ["x"], "y")["r2"], 1.0)


class TestResolutionUnchanged(unittest.TestCase):
    def test_shot_make_probability_pinned(self):
        ctx = sr.ShotResolutionContext(shot_family=sr.ShotFamily.THREE_POINT, shooter_base_rate=0.36,
                                       contest_bucket=sr.ContestBucket.OPEN, release_mode=sr.ReleaseMode.CATCH_AND_SHOOT)
        self.assertAlmostEqual(sr.shot_make_probability(ctx), 0.3514, places=3)

    def test_holdout_not_used(self):
        import inspect
        import historical_predictive_backtest as hpb
        source = inspect.getsource(tad) + inspect.getsource(__import__("run_three_point_allocation_diagnostic"))
        self.assertNotIn("load_or_create_holdout", source)
        self.assertNotIn(hpb.select_holdout_games.__name__, source)


if __name__ == "__main__":
    unittest.main()
