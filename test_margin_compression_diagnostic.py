"""Focused tests for MARGIN COMPRESSION DIAGNOSTIC V1 (margin_compression_diagnostic.py)."""
import json
import unittest

import margin_compression_diagnostic as mcd
import historical_predictive_backtest as hpb
import historical_game_snapshot as hgs
from possession_orchestrator import PlayerSimulationProfile

DEV_SAMPLE_PROBE = None  # filled lazily in setUpModule to avoid repeated I/O


def setUpModule():
    global DEV_SAMPLE_PROBE
    DEV_SAMPLE_PROBE = mcd.select_development_games()[:3]


class TestDeterministicDevelopmentSample(unittest.TestCase):
    """A. Deterministic development sample."""

    def test_a_selection_is_deterministic(self):
        self.assertEqual(mcd.select_development_games(), mcd.select_development_games())

    def test_a_selection_matches_documented_rule(self):
        import hashlib
        for gid in mcd.select_development_games():
            self.assertEqual(int(hashlib.sha256(gid.encode()).hexdigest(), 16) % mcd.DEV_SELECTION_MOD,
                              mcd.DEV_SELECTION_RESIDUE)


class TestHoldoutExclusion(unittest.TestCase):
    """B. Holdout exclusion."""

    def test_b_development_sample_disjoint_from_holdout(self):
        dev = set(mcd.select_development_games())
        holdout = set(hpb.load_or_create_holdout())
        self.assertEqual(dev & holdout, set())


class TestTeamInputSummarization(unittest.TestCase):
    """C. Team-input summarization."""

    def test_c_summary_has_all_field_groups(self):
        snap = hgs.build_historical_game_snapshot(DEV_SAMPLE_PROBE[0], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        summary = mcd.summarize_team_snapshot(snap.home_team_snapshot)
        for field in mcd.ALL_FIELDS:
            self.assertIn(field, summary["primary_five"])
            self.assertIn(field, summary["full_roster"])

    def test_c_primary_five_summary_uses_exactly_five_players(self):
        snap = hgs.build_historical_game_snapshot(DEV_SAMPLE_PROBE[0], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        summary = mcd.summarize_team_snapshot(snap.home_team_snapshot)
        field = "three_point_shrunk_rate"
        self.assertLessEqual(summary["primary_five"][field]["n"], 5)


class TestProvenanceShareCalculation(unittest.TestCase):
    """D. Provenance-share calculation."""

    def test_d_shares_sum_to_one_per_group(self):
        snap = hgs.build_historical_game_snapshot(DEV_SAMPLE_PROBE[0], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        shares = mcd.provenance_shares(snap.home_team_snapshot, primary_only=True)
        for group, label_shares in shares.items():
            self.assertAlmostEqual(sum(label_shares.values()), 1.0, places=6)


class TestOracleTopFiveMinuteShare(unittest.TestCase):
    """E. Oracle top-five minute share."""

    def test_e_top5_and_bench_shares_sum_to_one(self):
        rows = mcd.fixed_five_information_loss(DEV_SAMPLE_PROBE[:1])
        for r in rows:
            if r["top5_minute_share"] is not None:
                self.assertAlmostEqual(r["top5_minute_share"] + r["bench_minute_share"], 1.0, places=6)

    def test_e_top5_minute_share_is_reasonable(self):
        rows = mcd.fixed_five_information_loss(DEV_SAMPLE_PROBE[:1])
        for r in rows:
            if r["top5_minute_share"] is not None:
                self.assertGreater(r["top5_minute_share"], 0.4)
                self.assertLess(r["top5_minute_share"], 1.0)


class TestBenchProfileCalculation(unittest.TestCase):
    """F. Bench profile calculation."""

    def test_f_bench_excludes_primary_five_player_ids(self):
        snap = hgs.build_historical_game_snapshot(DEV_SAMPLE_PROBE[0], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_ORACLE_PARTICIPANTS)
        team_snap = snap.home_team_snapshot
        primary_ids = {p.player_id for p in team_snap.players if p.is_primary_five}
        bench_ids = {p.player_id for p in team_snap.players if not p.is_primary_five}
        self.assertEqual(primary_ids & bench_ids, set())


class TestRawTruthEngineVarianceCalculation(unittest.TestCase):
    """G. Raw->truth->engine variance calculation."""

    def test_g_variance_transfer_returns_real_numbers(self):
        records = mcd.build_dev_team_records(DEV_SAMPLE_PROBE[:2])
        result = mcd.raw_truth_engine_variance_transfer(records)
        self.assertGreater(result["raw_population_n"], 0)
        self.assertIsNotNone(result["raw_population_sd"])
        self.assertGreaterEqual(result["raw_population_sd"], 0.0)


class TestOffenseDefenseVarianceCalculation(unittest.TestCase):
    """H. Offense/defense variance calculation."""

    def test_h_real_shooting_splits_structure(self):
        splits = mcd.real_team_shooting_splits(DEV_SAMPLE_PROBE[:2])
        self.assertGreater(len(splits), 0)
        for team, s in splits.items():
            for key in ("two_pt_pct", "three_pt_pct", "ft_rate"):
                self.assertIn(key, s)
                if s[key] is not None:
                    self.assertGreaterEqual(s[key], 0.0)
                    self.assertLessEqual(s[key], 1.0)


class TestCounterfactualReplacementDeterminism(unittest.TestCase):
    """I. Counterfactual replacement determinism."""

    def test_i_repeated_counterfactual_is_identical(self):
        snap = hgs.build_historical_game_snapshot(DEV_SAMPLE_PROBE[0], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        p5 = [p for p in snap.home_team_snapshot.players if p.is_primary_five]
        player_id = p5[0].player_id
        r1 = mcd.player_replacement_counterfactual(DEV_SAMPLE_PROBE[0], "HOME", player_id, n_sims=10)
        r2 = mcd.player_replacement_counterfactual(DEV_SAMPLE_PROBE[0], "HOME", player_id, n_sims=10)
        self.assertEqual(r1, r2)


class TestNoMutationOfProductionProfiles(unittest.TestCase):
    """J. No mutation of production profiles."""

    def test_j_replacement_does_not_mutate_original_snapshot_profiles(self):
        snap = hgs.build_historical_game_snapshot(DEV_SAMPLE_PROBE[0], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        before = dict(profiles)
        p5 = [p for p in snap.home_team_snapshot.players if p.is_primary_five]
        player_id = p5[0].player_id
        original_profile = profiles[player_id]

        mcd.player_replacement_counterfactual(DEV_SAMPLE_PROBE[0], "HOME", player_id, n_sims=5)

        # re-fetch the snapshot's engine input -- must be unaffected by the counterfactual call
        _, _, _, _, profiles_after = hgs.snapshot_to_engine_input(snap)
        self.assertEqual(profiles_after[player_id], original_profile)
        self.assertIsInstance(original_profile, PlayerSimulationProfile)


class TestMonteCarloDiagnosticDeterminism(unittest.TestCase):
    """K. Monte Carlo diagnostic determinism."""

    def test_k_batch_margin_is_deterministic(self):
        snap = hgs.build_historical_game_snapshot(DEV_SAMPLE_PROBE[0], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        m1, f1 = mcd._batch_margin(home_id, away_id, home_five, away_five, profiles, DEV_SAMPLE_PROBE[0], "det_test", 10)
        m2, f2 = mcd._batch_margin(home_id, away_id, home_five, away_five, profiles, DEV_SAMPLE_PROBE[0], "det_test", 10)
        self.assertEqual(m1, m2)
        self.assertEqual(f1, f2)


class TestDiagnosticSerialization(unittest.TestCase):
    """L. Diagnostic serialization."""

    def test_l_output_structure_round_trips_through_json(self):
        records = mcd.build_dev_team_records(DEV_SAMPLE_PROBE[:2])
        payload = {
            "team_strength_correlations": mcd.correlate_fields_with_strength(records),
            "strong_vs_weak_quartiles": mcd.strong_vs_weak_quartiles(records),
            "home_court_contribution_estimate": mcd.home_court_contribution_estimate(records),
        }
        text = json.dumps(payload, default=str)
        reloaded = json.loads(text)
        self.assertIn("team_strength_correlations", reloaded)
        self.assertIn("strong_vs_weak_quartiles", reloaded)


if __name__ == "__main__":
    unittest.main()
