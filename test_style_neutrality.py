"""Focused infrastructure tests for the STRENGTH-NEUTRAL OFFENSIVE STYLE V1 evaluation (no production change)."""
import gzip
import json
import os
import unittest

import action_selection as acs
import player_input_team_strength_dataset as ds
import style_neutrality_eval as sne
import style_neutrality_report as snr


class ConfigTests(unittest.TestCase):
    def test_configs_only_touch_existing_constants(self):
        for name, cfg in sne.CONFIGS.items():
            for key in cfg:
                self.assertTrue(hasattr(acs, key), (name, key))

    def test_apply_config_is_reversible_and_leaves_production_defaults(self):
        before = {k: getattr(acs, k) for cfg in sne.CONFIGS.values() for k in cfg}
        try:
            sne.apply_config(sne.CONFIGS["w1.0_mid0.5_drive0.75"])
            self.assertEqual(acs.THREE_POINT_PREFERENCE_WEIGHT, 1.0)
        finally:
            sne.apply_config(before)
        self.assertEqual(acs.THREE_POINT_PREFERENCE_WEIGHT, 2.0)
        self.assertEqual(acs.MIDRANGE_PREFERENCE_WEIGHT, 1.0)
        self.assertEqual(acs.TENDENCY_DRIVE_WEIGHT, 1.5)

    def test_no_team_strength_scalar_in_tendency_context(self):
        fields = set(acs.TendencyContext.__dataclass_fields__)
        self.assertFalse(any("strength" in f or "rating" in f or "elo" in f for f in fields))

    def test_pct_helper(self):
        out = snr.pct([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertAlmostEqual(out["mean"], 3.0)
        self.assertAlmostEqual(out["p50"], 3.0)


@unittest.skipUnless(os.path.exists("backtests/_sn_metrics_baseline.json"), "stored metrics not present")
class StoredResultTests(unittest.TestCase):
    def test_baseline_reproduces_prior_diagnostic_and_ceiling_orders_above_it(self):
        base = json.load(open("backtests/_sn_metrics_baseline.json"))
        zero = json.load(open("backtests/_sn_metrics_all_zero.json"))
        self.assertAlmostEqual(base["team_season_three_point_attempt_rate"]["sim"]["sd"], 0.058, delta=0.005)
        self.assertGreater(zero["stable_ranking"]["pooled_within_season_z_pearson"], base["stable_ranking"]["pooled_within_season_z_pearson"] + 0.1)

    def test_resim_excludes_frozen_holdout(self):
        holdout = set(ds.frozen_holdout_ids())
        with gzip.open("backtests/_sn_resim_baseline.json.gz", "rt") as f:
            ids = {g["game_id"] for g in json.load(f)["games"]}
        self.assertFalse(ids & holdout)


if __name__ == "__main__":
    unittest.main()
