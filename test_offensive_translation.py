"""OFFENSIVE TRANSLATION FAILURE DIAGNOSTIC V1: infrastructure tests (accounting, determinism, invariance, helpers)."""
import gzip
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

import offensive_translation_analysis as oa
import offensive_translation_capture as otc
import offensive_translation_experiments as ex
import player_input_team_strength_dataset as ds

RECORDS = None


def records():
    global RECORDS
    if RECORDS is None:
        with gzip.open(ds.DATASET_PATH, "rt") as f:
            RECORDS = json.load(f)["records"]
    return RECORDS


def one_record():
    return [r for r in records() if r.get("detailed_sim")][2]


class TestCaptureAccounting(unittest.TestCase):
    def test_reconstructed_game_has_five_players_a_side_and_matching_team_ids(self):
        h, a, profiles = otc.build_game(one_record())
        self.assertEqual((len(h), len(a)), (5, 5))
        self.assertTrue(all(profiles[p].team_id == "HOME" for p in h))
        self.assertTrue(all(profiles[p].team_id == "AWAY" for p in a))

    def test_points_equal_field_goal_points_plus_free_throws_in_every_simulation(self):
        h, a, profiles = otc.build_game(one_record())
        for i in range(4):
            totals, _, result = otc.simulate_once("HOME", "AWAY", h, a, profiles, otc.sim_seed("acct", i))
            for side, final in (("HOME", result.final_home_score), ("AWAY", result.final_away_score)):
                t = totals[side]
                self.assertEqual(t["points"], final)
                self.assertEqual(2 * (t["fgm"] - t["fg3m"]) + 3 * t["fg3m"] + t["ftm"], final)

    def test_expected_and_realized_open_shot_points_track_each_other(self):
        g = otc.simulate_game_record(one_record(), 30)
        exp = np.mean(g["arrays"]["HOME"]["exp_fg_pts_unblocked"])
        real = np.mean(g["arrays"]["HOME"]["real_fg_pts"])
        self.assertLess(abs(exp - real), 6.0)
        self.assertGreater(exp, 40.0)

    def test_capture_does_not_change_the_game(self):
        h, a, profiles = otc.build_game(one_record())
        plain = otc.simulate_detailed_game("HOME", "AWAY", h, a, profiles, rng_seed=99)
        _, _, captured = otc.simulate_once("HOME", "AWAY", h, a, profiles, 99)
        self.assertEqual((plain.final_home_score, plain.final_away_score), (captured.final_home_score, captured.final_away_score))

    def test_capture_restores_the_original_shot_functions(self):
        import possession_orchestrator as po
        before = (po.apply_perimeter_shot_to_engine, po.apply_interior_shot_to_engine)
        h, a, profiles = otc.build_game(one_record())
        otc.simulate_once("HOME", "AWAY", h, a, profiles, 5)
        self.assertEqual((po.apply_perimeter_shot_to_engine, po.apply_interior_shot_to_engine), before)


class TestDeterminismAndInvariance(unittest.TestCase):
    def test_captured_simulation_is_deterministic(self):
        a = otc.simulate_game_record(one_record(), 6)
        b = otc.simulate_game_record(one_record(), 6)
        self.assertEqual(a["arrays"], b["arrays"])
        self.assertEqual(a["families"], b["families"])

    def test_player_ids_and_lineup_order_do_not_change_a_game(self):
        rec = one_record()
        h1, a1, p1 = otc.build_game(rec)
        h2, a2, p2 = otc.build_game(rec, home_base=123456, away_base=654321, home_order=None, away_order=None)
        r1 = otc.simulate_detailed_game("HOME", "AWAY", h1, a1, p1, rng_seed=7)
        r2 = otc.simulate_detailed_game("HOME", "AWAY", h2, a2, p2, rng_seed=7)
        self.assertEqual((r1.final_home_score, r1.final_away_score), (r2.final_home_score, r2.final_away_score))

    def test_hash_seed_does_not_change_engine_output(self):
        outs = []
        for seed in ("1", "2"):
            res = subprocess.run([sys.executable, "-c", ex.HASH_SCRIPT, "700001"], capture_output=True, text=True,
                                 env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"}, cwd=".")
            self.assertEqual(res.returncode, 0, res.stderr[-300:])
            outs.append(res.stdout.strip().splitlines()[-1])
        self.assertEqual(outs[0], outs[1])

    def test_lineup_permutation_moves_offense_only_within_monte_carlo_noise(self):
        rec = one_record()
        vals = []
        for order in (None, [4, 3, 2, 1, 0] + list(range(5, len(rec["home"])))):
            g = ex._perm_task((rec, order, None, 60, "t"))
            vals.append(g[3]["points"])
        self.assertLess(abs(vals[0] - vals[1]), 6.0)


class TestHelpers(unittest.TestCase):
    def test_synthetic_team_builder_sets_only_the_requested_family(self):
        dists = ex.field_distributions(records())
        base = ex.median_fields(dists)
        elite = {f: ex.percentile_of(dists, f, 99) for f in ex.FAMILIES["rim_creation"]}
        ids, profiles = ex.five({**base, **elite}, "HOME", 810001)
        self.assertEqual(len(ids), 5)
        p = profiles[ids[0]]
        self.assertGreater(p.rim_access_creation_shrunk_rate, base["rim_access_creation_shrunk_rate"])
        self.assertEqual(p.three_point_shrunk_rate, base["three_point_shrunk_rate"])

    def test_ball_security_levels_are_reversed_because_lower_error_is_better(self):
        dists = ex.field_distributions(records())
        weak = ex.percentile_of(dists, "ball_security_error_rate", 10, lower_is_better=True)
        elite = ex.percentile_of(dists, "ball_security_error_rate", 99, lower_is_better=True)
        self.assertGreater(weak, elite)

    def test_feature_response_flags(self):
        self.assertEqual(oa.classify_response(1.0, 0.3, -0.5), "WRONG SIGN")
        self.assertEqual(oa.classify_response(1.0, 0.3, 0.05), "NEAR-ZERO SIM")
        self.assertEqual(oa.classify_response(0.2, 0.3, 2.0), "EXAGGERATED SIM")
        self.assertEqual(oa.classify_response(1.0, 0.3, 0.9), "ok")

    def test_variance_decomposition_recovers_the_dominant_factor(self):
        rng = np.random.default_rng(3)
        n = 5000
        poss = rng.normal(100, 3, n)
        fga = rng.normal(88, 4, n)
        efg = rng.normal(0.52, 0.06, n)
        fgm = efg * fga
        rows = {"points": 2 * fgm + 22 + rng.normal(0, 1, n), "poss": poss, "fga": fga, "fgm": fgm, "fg3m": fgm * 0.0,
                "fta": np.full(n, 22.0), "tov": np.full(n, 14.0), "oreb": np.full(n, 10.0)}
        dec = oa.four_factor_decomposition(rows)
        self.assertGreater(dec["shares_of_ortg_variance"]["shot_conversion_efg"], 0.5)
        self.assertGreater(dec["r2_four_factors_plus_volume"], 0.9)

    def test_monte_carlo_convergence_helper_shrinks_with_n(self):
        rng = np.random.default_rng(1)
        raw = {f"g{i}": {"home": (100 + rng.normal(0, 12, 500)).tolist(), "away": (98 + rng.normal(0, 12, 500)).tolist(),
                         "poss": [[100, 100]] * 500} for i in range(12)}
        conv = oa.mc_convergence(raw)
        self.assertGreater(conv["per_n"]["20"]["rms_deviation_margin_vs_n500"], conv["per_n"]["250"]["rms_deviation_margin_vs_n500"])
        self.assertAlmostEqual(oa.noise_share_of_residual(18.0, 40, 8.0), 18.0 ** 2 / 40 / 64.0)

    def test_pearson_spearman_and_ols(self):
        x = np.arange(50, dtype=float)
        self.assertAlmostEqual(oa.pearson(x, 2 * x + 1), 1.0)
        self.assertAlmostEqual(oa.spearman(x, x ** 3), 1.0)
        fit = oa.ols(x[:, None], 3 * x + 2)
        self.assertAlmostEqual(fit["coef"][0], 3.0)


class TestArtifacts(unittest.TestCase):
    def test_sim_cache_covers_the_sample_and_excludes_the_holdout(self):
        if not Path(otc.CACHE_PATH).exists():
            self.skipTest("cache not built")
        cache = otc.load_cache()
        holdout = ds.frozen_holdout_ids()
        self.assertFalse(set(cache) & holdout)
        self.assertEqual(set(cache), {r["game_id"] for r in records()})
        g = next(iter(cache.values()))
        self.assertEqual(g["n_valid"], otc.N_SIMS)
        self.assertEqual(set(g["arrays"]["HOME"]), set(otc.ARRAY_KEYS))

    def test_saved_diagnostic_is_serializable_and_complete(self):
        path = Path("backtests/offensive_translation_diagnostic_v1.json")
        if not path.exists():
            self.skipTest("diagnostic not generated")
        out = json.load(open(path))
        for key in ("monte_carlo_convergence", "expected_vs_realized", "family_response_table", "single_family_sweeps",
                    "action_families", "simulated_offense_variance_decomposition", "detailed_vs_aggregate_equivalent_response",
                    "stable_offense_ranking_report_only", "ranked_failure_modes", "recommended_intervention"):
            self.assertIn(key, out)
        self.assertFalse(out["sample"]["holdout_used"])

    def test_diagnostic_modules_never_reference_the_frozen_holdout_list(self):
        import inspect
        for module in (otc, oa, ex):
            self.assertNotIn("backtest_v1_holdout", inspect.getsource(module).replace("frozen_holdout_ids", ""))


if __name__ == "__main__":
    unittest.main()
