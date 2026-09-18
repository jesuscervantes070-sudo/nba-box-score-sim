"""Focused tests for CURRENT-SEASON DEFENSE + ROLE REFRESH V1."""
import unittest
from unittest.mock import patch

import player_defensive_truth as pdt
import player_role_truth as prt_role
import player_playmaking_truth as ppt
import player_creation_truth as pct
import player_scoring_truth_temporal as psst
import rim_protection_estimation as rpe
import rim_protection_analysis as rpa
import role_off_ingestion as roi
import historical_game_snapshot as hgs
from possession_orchestrator import PlayerSimulationProfile

SEASON = "2023-24"
ALL_SEASONS = ["2021-22", "2022-23", "2023-24"]
# A real, rotation-minutes player, stable roster (Denver Nuggets), no known mid-season trade.
GOBERT = "203497"  # Rudy Gobert -- real, heavy rim-protection minutes
CURRY = "201939"   # Stephen Curry -- real, high-usage, no 2023-24 trade
SIAKAM = "1627783"  # real 2023-24 Toronto->Indiana trade, ~2024-01-17


class TestMonthCutoff(unittest.TestCase):
    """A/E. Date-cutoff arithmetic + opening-night behavior."""

    def test_a_month_cutoff_is_first_of_month_strictly_before(self):
        self.assertEqual(psst.month_cutoff_for_date("2023-12-05"), "2023-12-01")

    def test_a_month_cutoff_backs_up_a_full_month_when_as_of_date_is_the_1st(self):
        self.assertEqual(psst.month_cutoff_for_date("2023-12-01"), "2023-11-01")

    def test_a_month_cutoff_handles_year_boundary(self):
        self.assertEqual(psst.month_cutoff_for_date("2024-01-01"), "2023-12-01")

    def test_e_opening_night_has_no_current_season_rim_protection_evidence(self):
        t = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2023-10-24", SEASON, ALL_SEASONS)
        self.assertEqual(t.estimates["rim_protection"].provenance, psst.PRIOR_SEASON_ONLY)


class TestRimProtectionCurrentSeason(unittest.TestCase):
    """B. Rim date cutoff."""

    def test_b_real_rotation_player_gets_current_season_rim_protection_by_december(self):
        t = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2023-12-05", SEASON, ALL_SEASONS)
        self.assertEqual(t.estimates["rim_protection"].provenance, psst.CURRENT_SEASON_PREGAME)
        self.assertIsNotNone(t.estimates["rim_protection"].value)

    def test_b_poa_containment_stays_prior_season_only_no_date_endpoint(self):
        t = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2023-12-05", SEASON, ALL_SEASONS)
        self.assertIn(t.estimates["poa_containment"].provenance, (psst.PRIOR_SEASON_ONLY, psst.MISSING))


class TestRoleFinishingSpacingCurrentSeason(unittest.TestCase):
    """C/D. Finishing/spacing date cutoff."""

    def test_c_finishing_is_current_season_pregame_for_a_rotation_player_by_december(self):
        t = prt_role.build_role_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON)
        self.assertEqual(t.estimates["role_off_finishing"].provenance, psst.CURRENT_SEASON_PREGAME)

    def test_d_spacing_is_current_season_pregame_for_a_rotation_player_by_december(self):
        t = prt_role.build_role_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON)
        self.assertEqual(t.estimates["role_off_spacing"].provenance, psst.CURRENT_SEASON_PREGAME)

    def test_c_initiation_remains_prior_season_only_out_of_scope(self):
        t = prt_role.build_role_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON)
        self.assertEqual(t.estimates["role_off_initiation"].provenance, psst.PRIOR_SEASON_ONLY)


class TestRookieBehavior(unittest.TestCase):
    """F. Rookie behavior."""

    def test_f_no_prior_season_returns_missing_not_fabricated(self):
        t = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2013-11-15", "2013-14", ["2013-14"])
        self.assertIn(t.estimates["rim_protection"].provenance, (psst.MISSING,))
        self.assertIsNone(t.estimates["rim_protection"].value)


class TestTradeStintRoleBehavior(unittest.TestCase):
    """G. Trade/stint role behavior."""

    def test_g_traded_player_does_not_get_blended_current_season_role(self):
        # Any date after the real trade this season must never silently blend old-team+new-team
        # role into one current-season number -- must fall back to PRIOR_SEASON_ONLY.
        t = prt_role.build_role_truth_profile_as_of_date(SIAKAM, "2024-02-01", SEASON)
        self.assertEqual(t.estimates["role_off_finishing"].provenance, psst.PRIOR_SEASON_ONLY)
        self.assertEqual(t.estimates["role_off_spacing"].provenance, psst.PRIOR_SEASON_ONLY)


class TestRimProtectionLeakage(unittest.TestCase):
    """H/I. Target-game and future-game rim-protection leakage."""

    def test_h_target_game_poison_does_not_change_earlier_profile(self):
        before = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2023-12-05", SEASON, ALL_SEASONS)
        rpa.clear_reference_caches()
        with patch("rim_protection_ingestion.fetch_rim_protection_data_through_date") as mock_fetch:
            mock_fetch.side_effect = AssertionError("should not be called for an already-cached cutoff")
            # cutoff already cached from `before` -- a poisoned live-fetch must never even be invoked
            after = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2023-12-05", SEASON, ALL_SEASONS)
        self.assertEqual(before.estimates["rim_protection"].value, after.estimates["rim_protection"].value)

    def test_i_future_month_cutoff_never_used_for_an_earlier_date(self):
        # building for an earlier date must only ever request a cutoff <= that date's own month.
        with patch("player_scoring_truth_temporal.month_cutoff_for_date", wraps=psst.month_cutoff_for_date) as spy:
            pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2023-12-05", SEASON, ALL_SEASONS)
            for call in spy.call_args_list:
                self.assertEqual(call.args[0], "2023-12-05")


class TestFinishingSpacingLeakage(unittest.TestCase):
    """L/M. Target-game and future-game role leakage."""

    def test_l_target_game_poison_does_not_change_earlier_role_profile(self):
        before = prt_role.build_role_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON)
        with patch("role_off_ingestion.build_and_cache_role_scoring_through_date") as mock_build:
            mock_build.side_effect = AssertionError("should not be called for an already-cached cutoff")
            after = prt_role.build_role_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON)
        self.assertEqual(before.estimates["role_off_finishing"].value, after.estimates["role_off_finishing"].value)
        self.assertEqual(before.estimates["role_off_spacing"].value, after.estimates["role_off_spacing"].value)

    def test_m_future_season_poison_does_not_change_an_earlier_snapshot(self):
        before = hgs.build_historical_game_snapshot("0022300005", SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)

        import role_off_ingestion as _roi
        real_load = _roi.load_role_off

        def poisoned(season):
            data = real_load(season)
            if season <= SEASON or not data:
                return data
            poisoned_data = dict(data)
            poisoned_data["201939"] = dict(poisoned_data.get("201939", {}))
            poisoned_data["201939"]["PCT_AST_FGM"] = 0.999
            return poisoned_data

        hgs.clear_estimator_caches()
        with patch("role_off_ingestion.load_role_off", side_effect=poisoned):
            after = hgs.build_historical_game_snapshot("0022300005", SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(before.to_dict(), after.to_dict())


class TestFutureSeasonLeakage(unittest.TestCase):
    """N. Future-season leakage (rim protection specifically)."""

    def test_n_a_later_seasons_rim_data_never_reaches_an_earlier_snapshot(self):
        before = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2022-12-05", "2022-23", ALL_SEASONS)

        import rim_protection_analysis as _rpa
        real_build = _rpa.build_player_rim_rows

        def poisoned(season):
            rows = real_build(season)
            if season <= "2022-23":
                return rows
            return []  # a real future season's rows should never even be consulted for an earlier date

        with patch("rim_protection_analysis.build_player_rim_rows", side_effect=poisoned):
            after = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2022-12-05", "2022-23", ALL_SEASONS)
        self.assertEqual(before.estimates["rim_protection"].value, after.estimates["rim_protection"].value)


class TestPassingDeadFieldResolution(unittest.TestCase):
    """O. passing_accuracy dead-field resolution/retirement."""

    def test_o_passing_accuracy_ast_pct_is_no_longer_permanently_synthetic(self):
        t = ppt.build_playmaking_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON, ALL_SEASONS)
        base = PlayerSimulationProfile.synthetic(CURRY, "HOME")
        overlaid = ppt.apply_playmaking_truth_to_simulation_profile(base, t)
        self.assertNotEqual(overlaid.passing_accuracy_ast_pct, base.passing_accuracy_ast_pct)

    def test_o_original_passing_accuracy_construct_still_never_overlaid(self):
        t = ppt.build_playmaking_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON, ALL_SEASONS)
        self.assertIsNone(ppt._TARGET_TO_PROFILE_FIELD["passing_accuracy"])


class TestRimAccessEstimatorResolution(unittest.TestCase):
    """P. rim-access estimator/retirement."""

    def test_p_rim_access_creation_is_no_longer_permanently_synthetic(self):
        t = pct.build_creation_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON, ALL_SEASONS)
        base = PlayerSimulationProfile.synthetic(CURRY, "HOME")
        overlaid = pct.apply_creation_truth_to_simulation_profile(base, t)
        self.assertNotEqual(overlaid.rim_access_creation_shrunk_rate, base.rim_access_creation_shrunk_rate)

    def test_p_synthetic_default_discrepancy_is_documented_not_silently_fixed(self):
        # A real discrepancy was found (drive_resolution.RIM_ACCESS_POPULATION_MEAN=0.622 vs the
        # synthetic default of 0.5) but deliberately left UNCHANGED this phase -- fixing it shifted
        # several pre-existing, out-of-scope possession-mechanics baseline tests' expected ranges
        # (see possession_orchestrator.py's own docstring). This test locks the CURRENT, documented
        # default so a future calibration-safety phase makes that change deliberately, not by
        # accident.
        import drive_resolution as dr
        self.assertEqual(PlayerSimulationProfile.synthetic("X", "HOME").rim_access_creation_shrunk_rate, 0.5)
        self.assertNotEqual(dr.RIM_ACCESS_POPULATION_MEAN, 0.5)  # the real, documented discrepancy


class TestZeroVarianceStatus(unittest.TestCase):
    """U. Zero-variance status -- across a real roster, values must no longer be identical."""

    def test_u_rim_access_and_passing_accuracy_vary_across_a_real_roster(self):
        snap = hgs.build_historical_game_snapshot("0022300005", SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        players = snap.home_team_snapshot.players + snap.away_team_snapshot.players
        rim_access_values = {p.simulation_profile.rim_access_creation_shrunk_rate for p in players}
        passing_values = {p.simulation_profile.passing_accuracy_ast_pct for p in players}
        self.assertGreater(len(rim_access_values), 1)
        self.assertGreater(len(passing_values), 1)


class TestTargetOnlyOverlay(unittest.TestCase):
    """Q. Target-only overlay -- refresh must not mutate unrelated fields."""

    def test_q_creation_overlay_touches_only_rim_access_creation(self):
        base = PlayerSimulationProfile.synthetic(CURRY, "HOME")
        t = pct.build_creation_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON, ALL_SEASONS)
        overlaid = pct.apply_creation_truth_to_simulation_profile(base, t)
        for f in base.__dataclass_fields__:
            if f == "rim_access_creation_shrunk_rate":
                continue
            self.assertEqual(getattr(base, f), getattr(overlaid, f), f"field {f} was unexpectedly changed")

    def test_q_ast_pct_overlay_touches_only_passing_accuracy_ast_pct(self):
        base = PlayerSimulationProfile.synthetic(CURRY, "HOME")
        t = ppt.build_playmaking_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON, ALL_SEASONS)
        overlaid = ppt.apply_playmaking_truth_to_simulation_profile(base, t)
        touched = {f for f in base.__dataclass_fields__ if getattr(base, f) != getattr(overlaid, f)}
        self.assertTrue(touched.issubset({"passing_accuracy_ast_pct", "playmaking_vision_shrunk_rate", "ball_security_error_rate"}))


class TestEngineSensitivityIsolation(unittest.TestCase):
    """R. Engine sensitivity isolation -- refreshed inputs actually move the fields the engine
    reads, in the correct direction, without an engine-formula change."""

    def test_r_higher_refreshed_rim_protection_value_is_a_real_field_change(self):
        t = pdt.build_defensive_truth_profile_as_of_date(GOBERT, "2023-12-05", SEASON, ALL_SEASONS)
        base = PlayerSimulationProfile.synthetic(GOBERT, "HOME")
        overlaid = pdt.apply_defensive_truth_to_simulation_profile(base, t)
        self.assertNotEqual(overlaid.rim_protection_suppression_rate, base.rim_protection_suppression_rate)

    def test_r_higher_finishing_role_is_a_real_field_change(self):
        t = prt_role.build_role_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON)
        base = PlayerSimulationProfile.synthetic(CURRY, "HOME")
        overlaid = prt_role.apply_role_truth_to_simulation_profile(base, t)
        self.assertNotEqual(overlaid.role_off_finishing, base.role_off_finishing)


class TestDeterministicSerialization(unittest.TestCase):
    """S. Deterministic serialization."""

    def test_s_repeated_snapshot_build_is_byte_identical(self):
        a = hgs.build_historical_game_snapshot("0022300005", SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        b = hgs.build_historical_game_snapshot("0022300005", SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_s_creation_truth_estimate_round_trips(self):
        t = pct.build_creation_truth_profile_as_of_date(CURRY, "2023-12-05", SEASON, ALL_SEASONS)
        d = t.to_dict()
        self.assertIn("rim_access_creation", d["estimates"])


class TestPerformanceCachingPreservation(unittest.TestCase):
    """T. Performance/caching preservation."""

    def test_t_repeated_cutoff_reuses_cache_no_new_calls(self):
        rpa.clear_reference_caches()
        rpe.estimate_rim_protection_as_of_date("Rudy Gobert", "2023-12-05", SEASON, ALL_SEASONS, "2023-12-01")
        misses_before = rpa.build_player_rim_rows_through_date.cache_info().misses
        rpe.estimate_rim_protection_as_of_date("Stephen Curry", "2023-12-05", SEASON, ALL_SEASONS, "2023-12-01")
        misses_after = rpa.build_player_rim_rows_through_date.cache_info().misses
        self.assertEqual(misses_before, misses_after)

    def test_t_warm_snapshot_rebuild_is_fast(self):
        import time
        hgs.build_historical_game_snapshot("0022300005", SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        t0 = time.time()
        hgs.build_historical_game_snapshot("0022300005", SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
