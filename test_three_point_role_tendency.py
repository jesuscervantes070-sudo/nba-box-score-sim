"""Role vs tendency weighting for three-point allocation."""
import inspect
import unittest

import action_selection as asel
import shot_resolution as sr
import three_point_allocation_diagnostic as tad
import three_point_role_tendency_diagnostic as rtd
from action_intent import ActionType
from action_selection import ShotFamilySelectionContext, TendencyContext
from action_opportunity import PERIMETER_ZONES, MIDRANGE_ZONES
from possession_state import SpatialZone

N = 30
PREF = (1.0, 0.5, 0.0, -0.5, -1.0)


def _three_probability(pref):
    options = (SpatialZone.TOP_OF_KEY, SpatialZone.MIDRANGE)
    probs = asel.shot_zone_probabilities(ActionType.CATCH_AND_SHOOT, options,
                                          TendencyContext(three_point_preference=pref), ShotFamilySelectionContext())
    return probs[0]


class TestPreferenceWeight(unittest.TestCase):
    def test_weight_is_two_and_used(self):
        self.assertEqual(asel.THREE_POINT_PREFERENCE_WEIGHT, 2.0)
        self.assertIn("THREE_POINT_PREFERENCE_WEIGHT", inspect.getsource(asel.shot_zone_probabilities))

    def test_preference_is_monotonic_in_three_share(self):
        probs = [_three_probability(p) for p in PREF]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_zero_preference_is_unchanged_by_the_weight(self):
        with rtd.patched(pref_scale=1.0):
            self.assertAlmostEqual(_three_probability(0.0), _three_probability(0.0))
        self.assertAlmostEqual(_three_probability(0.0), 1 / (1 + 2.718281828 ** -(asel.CATCH_AND_SHOOT_THREE_BASELINE_LOG_WEIGHT
                                                                              + asel.ShotFamilySelectionContext().three_point_baseline_log_weight)), places=6)


class TestControlledSweeps(unittest.TestCase):
    def test_tendency_sweep_is_monotonic_and_volume_neutral(self):
        res = rtd.sweep("three_point_preference", PREF, N, "t-pref")
        shares = [r["share_3pa"] for r in res["rows"]]
        self.assertEqual(shares, sorted(shares, reverse=True))
        touches = [r["share_touches"] for r in res["rows"]]
        self.assertLess(max(touches) - min(touches), 0.04)

    def test_ability_does_not_drive_volume(self):
        res = rtd.sweep("three_point_shrunk_rate", (0.30, 0.33, 0.36, 0.39, 0.42), N, "t-ability")
        shares = [r["share_3pa"] for r in res["rows"]]
        self.assertLess(max(shares) - min(shares), 0.06)

    def test_initiation_is_not_a_generic_volume_weight(self):
        res = rtd.sweep("role_off_initiation", (1.0, 2.5, 4.0, 7.0, 10.0), N, "t-init")
        shares = [r["share_3pa"] for r in res["rows"]]
        self.assertLess(max(shares) - min(shares), 0.06)

    def test_spacing_raises_catch_and_shoot_share(self):
        res = rtd.sweep("role_off_spacing", (0.1, 0.3, 0.5, 0.7, 0.9), N, "t-space")
        cs = [r["cs_share_of_3pa"] for r in res["rows"]]
        self.assertGreater(cs[-1], cs[0])

    def test_finishing_still_matters_for_shot_volume(self):
        res = rtd.sweep("role_off_finishing", (0.2, 0.35, 0.5, 0.65, 0.8), N, "t-fin")
        fpt = [r["fga_per_touch"] for r in res["rows"]]
        self.assertGreater(fpt[-1], fpt[0])


class TestSymmetryPreserved(unittest.TestCase):
    def test_identical_players_share_equally(self):
        res = tad.permutation_test([{}] * 5, N, "t-ident")
        for s in res["mean_share_by_player"]:
            self.assertAlmostEqual(s, 0.2, delta=0.06)
        for s in res["mean_share_by_slot"]:
            self.assertLess(s, 0.35)

    def test_deterministic(self):
        ids, profiles = tad.identical_players([{"three_point_preference": p} for p in PREF])
        a = tad.simulate_side(ids, profiles, "t-det", 8)
        b = tad.simulate_side(ids, profiles, "t-det", 8)
        self.assertEqual(a["per_player"], b["per_player"])

    def test_team_3pa_and_concentration_stay_realistic(self):
        ids, profiles = tad.identical_players([{"three_point_preference": p} for p in (0.6, 0.3, 0.0, -0.3, -0.9)])
        res = tad.simulate_side(ids, profiles, "t-conc", 40)
        self.assertGreater(res["team_3pa_per_sim"], 28)
        self.assertLess(res["team_3pa_per_sim"], 42)
        shares = tad.slot_shares(res, ids)
        self.assertLess(tad.top_k_share(shares, 1), 0.5)


class TestPatchHelperAndMetrics(unittest.TestCase):
    def test_patched_restores_module_state(self):
        before = (asel.ROLE_FINISHING_WEIGHT, asel.ROLE_SPACING_WEIGHT, asel.shot_zone_probabilities)
        with rtd.patched(finishing_weight=0.0, spacing_weight=0.0, pref_scale=2.0, receiver_weights={"tendency": 1.0}):
            self.assertEqual(asel.ROLE_FINISHING_WEIGHT, 0.0)
        self.assertEqual((asel.ROLE_FINISHING_WEIGHT, asel.ROLE_SPACING_WEIGHT, asel.shot_zone_probabilities), before)

    def test_allocation_metrics_perfect_match(self):
        rows = [{"team": t, "real_share": s, "sim_share": s} for t in ("A", "B") for s in (0.1, 0.2, 0.3, 0.4)]
        m = rtd.allocation_metrics(rows, "sim_share")
        self.assertAlmostEqual(m["pearson"], 1.0)
        self.assertAlmostEqual(m["mae"], 0.0)
        self.assertAlmostEqual(m["top1"], 0.4)

    def test_team_split_is_deterministic_and_two_sided(self):
        teams = [f"Team {i}" for i in range(20)]
        splits = {rtd.team_split(t) for t in teams}
        self.assertEqual(splits, {"dev", "validation"})
        self.assertEqual(rtd.team_split("Boston Celtics"), rtd.team_split("Boston Celtics"))


class TestUnchangedElsewhere(unittest.TestCase):
    def test_shot_resolution_pinned(self):
        ctx = sr.ShotResolutionContext(shot_family=sr.ShotFamily.THREE_POINT, shooter_base_rate=0.36,
                                       contest_bucket=sr.ContestBucket.OPEN, release_mode=sr.ReleaseMode.CATCH_AND_SHOOT)
        self.assertAlmostEqual(sr.shot_make_probability(ctx), 0.3514, places=3)

    def test_role_weights_untouched(self):
        self.assertEqual(asel.ROLE_FINISHING_WEIGHT, 2.0)
        self.assertEqual(asel.ROLE_SPACING_WEIGHT, 1.5)

    def test_frozen_holdout_not_used(self):
        import run_three_point_role_tendency_diagnostic as runner
        source = inspect.getsource(rtd) + inspect.getsource(runner)
        self.assertNotIn("load_or_create_holdout", source)
        self.assertNotIn("select_holdout_games", source)


if __name__ == "__main__":
    unittest.main()
