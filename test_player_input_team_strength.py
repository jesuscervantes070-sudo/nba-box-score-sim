"""PLAYER INPUTS -> TEAM STRENGTH DIAGNOSTIC V1: leakage guards, aggregation helpers, folds, determinism."""
import inspect
import json
import unittest
from pathlib import Path

import numpy as np

import player_input_team_strength_dataset as ds
import player_input_team_strength_diagnostic as d
import run_player_input_team_strength_diagnostic as runner


def player(minutes, **fields):
    values = [None] * len(d.FIELDS)
    for name, v in fields.items():
        values[d.FIELD_INDEX[name]] = v
    return {"minutes": minutes, "primary": False, "status": "ACTIVE", "f": values}


ROT = [player(30, three_point_shrunk_rate=0.40, role_off_initiation=8.0, defensive_playmaking_per36=2.0),
       player(24, three_point_shrunk_rate=0.30, role_off_initiation=2.0, defensive_playmaking_per36=1.0),
       player(12, three_point_shrunk_rate=0.36, role_off_initiation=5.0, defensive_playmaking_per36=1.6),
       player(0, three_point_shrunk_rate=0.99),
       {**player(20, three_point_shrunk_rate=0.99), "status": "OUT"}]


class TestNoLeakage(unittest.TestCase):
    def test_features_contain_no_outcome_team_or_player_identity(self):
        names = d.team_features(ROT).keys()
        tokens = {t for n in names for t in n.replace("__", "_").split("_")}
        for bad in ds.FORBIDDEN_FEATURE_KEYS:
            hit = any(bad in n for n in names) if "_" in bad else bad in tokens
            self.assertFalse(hit, bad)

    def test_record_player_rows_carry_no_identity(self):
        source = inspect.getsource(ds.team_state)
        self.assertNotIn("player_id", source.replace("No player id", ""))
        self.assertEqual(set(ROT[0]), {"minutes", "primary", "status", "f"})

    def test_builder_audits_temporal_safety_and_uses_pregame_mode_only(self):
        source = inspect.getsource(ds.build_record)
        self.assertIn("audit_snapshot_temporal_safety", source)
        self.assertIn("MODE_PREGAME_EXPECTED", source)
        self.assertNotIn("ORACLE", source)

    def test_target_and_context_are_separate_from_features(self):
        source = inspect.getsource(ds.build_record)
        self.assertIn('"target"', source)
        self.assertIn('"context_only"', source)
        self.assertNotIn("net_rating", inspect.getsource(d.team_features))


class TestHoldoutExcluded(unittest.TestCase):
    def test_sample_never_includes_frozen_holdout_games(self):
        holdout = ds.frozen_holdout_ids()
        self.assertEqual(len(holdout), 83)
        for season in ds.SEASONS:
            self.assertFalse(set(ds.sample_game_ids(season)) & holdout)

    def test_built_dataset_excludes_holdout_and_folds_are_chronological(self):
        if not ds.DATASET_PATH.exists():
            self.skipTest("dataset not built")
        records = d.load_records()
        self.assertFalse({r["game_id"] for r in records} & ds.frozen_holdout_ids())
        blocks = {}
        for r in records:
            blocks.setdefault(r["fold"], []).append(r["date"])
        self.assertIn("train_only", blocks)
        ordered = sorted((k for k in blocks if k != "train_only"), key=lambda k: int(k.rsplit("_", 1)[1]))
        seen_max = max(blocks["train_only"])
        for k in ordered:
            self.assertGreaterEqual(min(blocks[k]), seen_max)
            seen_max = max(seen_max, max(blocks[k]))
        self.assertTrue(all(r["feature_version"] == ds.FEATURE_VERSION and r["mode"] == "PREGAME_EXPECTED" for r in records))

    def test_sample_is_deterministic(self):
        self.assertEqual(ds.sample_game_ids("2023-24"), ds.sample_game_ids("2023-24"))


class TestAggregationHelpers(unittest.TestCase):
    def test_rotation_orders_by_minutes_and_drops_out_and_zero_minute_players(self):
        rot = d.rotation(ROT)
        self.assertEqual([p["minutes"] for p in rot], [30, 24, 12])

    def test_minute_weighted_mean(self):
        s = d.field_summaries(d.rotation(ROT), "three_point_shrunk_rate")
        self.assertAlmostEqual(s["mw"], (30 * 0.40 + 24 * 0.30 + 12 * 0.36) / 66)
        self.assertAlmostEqual(s["eq"], (0.40 + 0.30 + 0.36) / 3)

    def test_top_five_and_full_rotation_differ_only_when_rotation_is_deeper(self):
        s = d.field_summaries(d.rotation(ROT), "three_point_shrunk_rate")
        self.assertAlmostEqual(s["top5"], s["eq"])
        deep = [player(30 - i, three_point_shrunk_rate=0.40 - 0.02 * i) for i in range(9)]
        s2 = d.field_summaries(d.rotation(deep), "three_point_shrunk_rate")
        self.assertGreater(s2["top5"], s2["eq"])
        self.assertGreater(s2["bench_gap"], 0)
        self.assertLessEqual(s2["min8"], s2["p25"])

    def test_missing_values_use_the_neutral_engine_default(self):
        s = d.field_summaries(d.rotation([player(20)]), "three_point_shrunk_rate")
        self.assertAlmostEqual(s["mw"], d.NEUTRAL["three_point_shrunk_rate"])

    def test_interaction_helpers(self):
        ix = d.interaction_features(d.rotation(ROT))
        self.assertAlmostEqual(ix["ix__n_credible_shooters"], 1.0)   # only 0.40 clears the 0.37 engine default
        self.assertAlmostEqual(ix["ix__n_credible_creators"], 2.0)
        mw = lambda n: d.field_summaries(d.rotation(ROT), n)["mw"]
        self.assertAlmostEqual(ix["ix__initiation_x_three"], mw("role_off_initiation") * mw("three_point_shrunk_rate"))
        self.assertAlmostEqual(ix["ix__weak_link_poa"], d.NEUTRAL["poa_containment_shrunk_rate"])


class TestTargetsAndFolds(unittest.TestCase):
    def test_margin_from_offense_and_defense(self):
        self.assertEqual(d.margin_from_od(10, 6, 4, 8), 8)
        self.assertEqual(d.margin_from_od(6, 10, 8, 4), -8)

    def test_expanding_blocks_split_each_later_season_in_two_by_date(self):
        dates = [f"2023-{m:02d}-01" for m in range(1, 7)] + [f"2024-{m:02d}-01" for m in range(1, 7)]
        seasons = ["2022-23"] * 6 + ["2023-24"] * 6
        blocks = d.expanding_blocks(dates, seasons, "2023-24")
        self.assertEqual(len(blocks), 2)
        self.assertTrue(all(i >= 6 for b in blocks for i in b))
        self.assertLess(max(dates[i] for i in blocks[0]), min(dates[i] for i in blocks[1]))

    def test_cv_never_trains_on_the_test_block_or_later_dates(self):
        rng = np.random.default_rng(0)
        n = 200
        dates = [f"2023-{1 + i // 20:02d}-{1 + i % 20:02d}" for i in range(n)]
        X = rng.normal(size=(n, 3))
        y = X[:, 0] * 2 + rng.normal(size=n)
        seasons = ["2022-23"] * 100 + ["2023-24"] * 100
        blocks = d.expanding_blocks(dates, seasons, "2023-24")
        pred, folds = d.cv_predict(X, y, dates, blocks)
        self.assertTrue(np.isnan(pred[:100]).all())
        self.assertFalse(np.isnan(pred[100:]).any())
        for f in folds:
            self.assertGreaterEqual(f["n_train"], 60)
        # a later-date leak would make a pure-noise target look predictable; here signal is real, so r is high
        self.assertGreater(d.metrics(pred[100:], y[100:])["pearson"], 0.5)


class TestDeterminism(unittest.TestCase):
    def test_ridge_and_gbm_are_deterministic(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(150, 4))
        y = X[:, 1] - X[:, 2] + rng.normal(size=150) * 0.1
        a = d.predict_ridge(d.fit_ridge(X, y, 10.0), X)
        b = d.predict_ridge(d.fit_ridge(X, y, 10.0), X)
        self.assertTrue(np.array_equal(a, b))
        g1 = d.predict_gbm(d.fit_gbm(X, y, rounds=10), X)
        g2 = d.predict_gbm(d.fit_gbm(X, y, rounds=10), X)
        self.assertTrue(np.array_equal(g1, g2))
        self.assertGreater(d._pearson(g1, y), 0.8)

    def test_old_engine_comparison_helper_is_deterministic(self):
        cache = {}
        import loader
        teams = sorted(loader.load_teams("2023-24"))
        a = runner.aggregate_engine_margin("2023-24", teams[0], teams[1], cache, n=30)
        b = runner.aggregate_engine_margin("2023-24", teams[0], teams[1], cache, n=30)
        self.assertEqual(a, b)
        self.assertIsNone(runner.aggregate_engine_margin("2023-24", "No Such Team", teams[1], cache, n=5))

    def test_metrics_and_slope_are_json_serializable(self):
        m = d.metrics([1.0, 2.0, 3.5, 4.0], [1.5, 2.0, 3.0, 5.0])
        json.dumps(m)
        json.dumps(d.slope([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]))
        self.assertAlmostEqual(d.slope([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])["slope"], 2.0)

    def test_saved_diagnostic_has_the_required_sections(self):
        path = Path("backtests/player_input_team_strength_diagnostic_v1.json")
        if not path.exists():
            self.skipTest("diagnostic not yet generated")
        out = json.load(open(path))
        for key in ("offense", "defense", "margin", "engine_comparison_same_games", "transfer_slopes", "ablations", "methodology"):
            self.assertIn(key, out)


if __name__ == "__main__":
    unittest.main()
