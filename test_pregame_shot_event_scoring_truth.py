"""Focused tests for the "Add pregame shot-event scoring evidence" phase
(player_shot_event_ingestion.py + player_scoring_truth_temporal.py's shot-zone pregame paths)."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import player_scoring_truth as pst
import player_scoring_truth_temporal as psst
from player_shot_event_ingestion import load_shot_events as _load_events
from possession_orchestrator import PlayerSimulationProfile

_SEASONS = [f"{y}-{str(y + 1)[-2:]}" for y in range(1996, 2026)]
CURRY = "201939"


class TestShotFamilyClassification(unittest.TestCase):
    """A. Exact shot-family classification -- SHOT_ZONE_BASIC maps to the SAME 4 families the
    existing season-level player_shot_zones.json already uses, with Backcourt excluded from THREE
    (matching this project's own prior real zone-share audit)."""

    def test_a_zone_mapping_matches_existing_season_level_taxonomy(self):
        self.assertEqual(psst._ZONE_TO_FAMILY["Restricted Area"], "rim_finishing")
        self.assertEqual(psst._ZONE_TO_FAMILY["In The Paint (Non-RA)"], "floater_short_mid")
        self.assertEqual(psst._ZONE_TO_FAMILY["Mid-Range"], "midrange")
        for zone in ("Left Corner 3", "Right Corner 3", "Above the Break 3"):
            self.assertEqual(psst._ZONE_TO_FAMILY[zone], "THREE")
        self.assertNotIn("Backcourt", psst._ZONE_TO_FAMILY)  # deliberately excluded, see docstring


class TestSeasonAndBoxScoreReconciliation(unittest.TestCase):
    """B. Per-season aggregation reconciliation. I. Box-score reconciliation."""

    def test_b_league_family_totals_match_existing_shot_zone_cache_exactly(self):
        import json
        events = _load_events("2023-24")
        with open("cache/2023-24/player_shot_zones.json") as f:
            zones = json.load(f)["players"]
        fam_fga = {"rim_finishing": 0, "floater_short_mid": 0, "midrange": 0}
        fam_fgm = {"rim_finishing": 0, "floater_short_mid": 0, "midrange": 0}
        for pid, evs in events.items():
            for e in evs:
                fam = psst._ZONE_TO_FAMILY.get(e["zone_basic"])
                if fam in fam_fga:
                    fam_fga[fam] += 1
                    fam_fgm[fam] += e["made"]
        z_rim_fga = sum(r.get("restricted_area_fga", 0) for r in zones.values())
        z_rim_fgm = sum(r.get("restricted_area_fgm", 0) for r in zones.values())
        self.assertEqual(fam_fga["rim_finishing"], z_rim_fga)
        self.assertEqual(fam_fgm["rim_finishing"], z_rim_fgm)
        z_floater_fga = sum(r.get("paint_non_ra_fga", 0) for r in zones.values())
        self.assertEqual(fam_fga["floater_short_mid"], z_floater_fga)
        z_mid_fga = sum(r.get("midrange_fga", 0) for r in zones.values())
        self.assertEqual(fam_fga["midrange"], z_mid_fga)

    def test_i_player_level_fga_fgm_3pa_3pm_reconciles_to_game_log_exactly(self):
        from player_game_log_ingestion import load_player_game_log
        events = _load_events("2023-24")[CURRY]
        gamelog = load_player_game_log("2023-24")[CURRY]
        ev_fga = len(events)
        ev_fgm = sum(e["made"] for e in events)
        ev_3pa = sum(1 for e in events if e["shot_type"] == "3PT Field Goal")
        ev_3pm = sum(1 for e in events if e["shot_type"] == "3PT Field Goal" and e["made"])
        gl_fga = sum(g["fga"] for g in gamelog)
        gl_fgm = sum(g["fgm"] for g in gamelog)
        gl_3pa = sum(g["fg3a"] for g in gamelog)
        gl_3pm = sum(g["fg3m"] for g in gamelog)
        self.assertEqual(ev_fga, gl_fga)
        self.assertEqual(ev_fgm, gl_fgm)
        self.assertEqual(ev_3pa, gl_3pa)
        self.assertEqual(ev_3pm, gl_3pm)


class TestGameDateJoinAndOrdering(unittest.TestCase):
    """C. Game-date join. D. Deterministic event ordering."""

    def test_c_shot_event_dates_use_the_same_format_as_the_game_log(self):
        events = _load_events("2023-24")[CURRY]
        for e in events[:5]:
            self.assertRegex(e["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_d_events_are_sorted_by_date_game_id_event_id(self):
        events = _load_events("2023-24")[CURRY]
        keys = [(e["date"], e["game_id"], e["game_event_id"]) for e in events]
        self.assertEqual(keys, sorted(keys))


class TestLeakageInvariants(unittest.TestCase):
    """E. Strict target-game exclusion. F. Future-game exclusion. G. Future-season exclusion."""

    def test_e_target_games_own_shots_never_influence_its_own_pregame_snapshot(self):
        events = _load_events("2023-24")[CURRY]
        target_date = events[10]["date"]
        before = psst.build_scoring_truth_profile_as_of_date(CURRY, target_date, "2023-24", _SEASONS)

        poisoned = dict(_load_events("2023-24"))
        poisoned_events = [dict(e) for e in poisoned[CURRY]]
        for e in poisoned_events:
            if e["date"] == target_date:
                e.update(zone_basic="Restricted Area", made=True)
        poisoned[CURRY] = poisoned_events

        with patch("player_scoring_truth_temporal.load_shot_events", return_value=poisoned):
            psst._shot_event_prefix_ledger_for.cache_clear()
            after = psst.build_scoring_truth_profile_as_of_date(CURRY, target_date, "2023-24", _SEASONS)
        psst._shot_event_prefix_ledger_for.cache_clear()
        self.assertEqual(before, after)

    def test_f_later_shot_events_never_leak_into_an_earlier_snapshot(self):
        events = _load_events("2023-24")[CURRY]
        cutoff_date = events[10]["date"]
        before = psst.build_scoring_truth_profile_as_of_date(CURRY, cutoff_date, "2023-24", _SEASONS)

        poisoned = dict(_load_events("2023-24"))
        poisoned_events = [dict(e) for e in poisoned[CURRY]]
        for e in poisoned_events:
            if e["date"] > cutoff_date:
                e.update(zone_basic="Restricted Area", made=True)
        poisoned[CURRY] = poisoned_events

        with patch("player_scoring_truth_temporal.load_shot_events", return_value=poisoned):
            psst._shot_event_prefix_ledger_for.cache_clear()
            after = psst.build_scoring_truth_profile_as_of_date(CURRY, cutoff_date, "2023-24", _SEASONS)
        psst._shot_event_prefix_ledger_for.cache_clear()
        self.assertEqual(before, after)

    def test_g_a_future_seasons_shot_cache_never_leaks_into_an_earlier_season_snapshot(self):
        before = psst.build_scoring_truth_profile_as_of_date(CURRY, "2018-12-01", "2018-19", _SEASONS)

        poisoned_future = {CURRY: [{"game_id": "FAKE", "game_event_id": 1, "date": "2019-11-01",
                                    "made": True, "shot_type": "2PT Field Goal",
                                    "zone_basic": "Restricted Area", "zone_area": "Center(C)",
                                    "zone_range": "Less Than 8 ft.", "distance": 0,
                                    "action_type": "Layup Shot", "loc_x": 0, "loc_y": 0}]}

        def fake_load(season):
            return poisoned_future if season == "2019-20" else _load_events(season)

        with patch("player_scoring_truth_temporal.load_shot_events", side_effect=fake_load):
            psst._shot_event_prefix_ledger_for.cache_clear()
            after = psst.build_scoring_truth_profile_as_of_date(CURRY, "2018-12-01", "2018-19", _SEASONS)
        psst._shot_event_prefix_ledger_for.cache_clear()
        self.assertEqual(before, after)


class TestOpeningNightAndRookie(unittest.TestCase):
    """H. Opening-night prior-only behavior. I. Rookie missing behavior."""

    def test_h_opening_night_rim_floater_mid_rest_on_prior_seasons(self):
        profile = psst.build_scoring_truth_profile_as_of_date(CURRY, "2023-10-01", "2023-24", _SEASONS)
        for target in ("rim_finishing", "floater_short_mid", "midrange", "midrange_preference"):
            self.assertIn(profile.estimates[target].provenance, (psst.PRIOR_SEASON_ONLY, psst.MISSING), target)
        self.assertIsNotNone(profile.value("rim_finishing"))

    def test_i_before_a_real_players_debut_every_shot_zone_target_is_missing(self):
        profile = psst.build_scoring_truth_profile_as_of_date(CURRY, "2007-11-01", "2007-08", ["2007-08"])
        for target in ("rim_finishing", "floater_short_mid", "midrange", "midrange_preference"):
            self.assertIsNone(profile.value(target), target)


class TestEvolutionAndConvergence(unittest.TestCase):
    """J. RIM evolution. K. FLOATER evolution. L. MID evolution. M. MID preference evolution.
    O. End-season convergence."""

    def test_j_k_l_m_sample_grows_and_estimates_can_evolve_across_the_season(self):
        events = _load_events("2023-24")[CURRY]
        mid_date = events[len(events) // 2]["date"]
        final_date = events[-1]["date"]
        mid = psst.build_scoring_truth_profile_as_of_date(CURRY, mid_date, "2023-24", _SEASONS)
        late = psst.build_scoring_truth_profile_as_of_date(CURRY, final_date, "2023-24", _SEASONS)
        for target in ("rim_finishing", "floater_short_mid", "midrange", "midrange_preference"):
            self.assertLessEqual(mid.estimates[target].sample_size, late.estimates[target].sample_size, target)

    def test_o_end_of_season_pregame_converges_toward_the_season_level_estimator(self):
        events = _load_events("2023-24")[CURRY]
        last_date = events[-1]["date"]
        day_after = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        pregame_end = psst.build_scoring_truth_profile_as_of_date(CURRY, day_after, "2023-24", _SEASONS)
        season_level = pst.build_scoring_truth_profile(CURRY, "2023-24", _SEASONS)
        for target in ("rim_finishing", "floater_short_mid", "midrange"):
            diff = abs(pregame_end.value(target) - season_level.value(target))
            self.assertLess(diff, 0.01, f"{target} discrepancy {diff} too large for a definition difference")
        # midrange_preference: a real, larger, EXPLAINED discrepancy (documented in
        # player_scoring_truth_temporal.py) -- the logit transform's higher sensitivity at this
        # tendency's lower base-rate range amplifies the deliberate prior-season-reference
        # anti-leakage choice. Bounded generously here, not silently ignored.
        mp_diff = abs(pregame_end.value("midrange_preference") - season_level.value("midrange_preference"))
        self.assertLess(mp_diff, 0.15, f"midrange_preference discrepancy {mp_diff} exceeds the documented bound")


class TestContemporaneousLeagueReferenceCutoff(unittest.TestCase):
    """N. Contemporaneous league-reference cutoff -- the league-average reference used for
    shrinkage must itself come only from a fully-completed prior season, never the in-progress
    as_of_season (which would leak the rest of the league's future games)."""

    def test_n_league_reference_season_is_always_strictly_before_as_of_season(self):
        self.assertEqual(psst._season_before("2023-24"), "2022-23")
        self.assertEqual(psst._season_before("2018-19"), "2017-18")


class TestCacheReuseAndSerialization(unittest.TestCase):
    """P. Cache reuse/no unnecessary fetch. Q. Serialization/provenance."""

    def test_p_loading_an_already_cached_season_makes_no_live_api_call(self):
        with patch("nba_api.stats.endpoints.shotchartdetail.ShotChartDetail") as mock_endpoint:
            events = _load_events("2023-24")
            self.assertTrue(events)
            mock_endpoint.assert_not_called()

    def test_q_provenance_and_shot_event_estimates_survive_serialization_round_trip(self):
        profile = psst.build_scoring_truth_profile_as_of_date(CURRY, "2023-12-15", "2023-24", _SEASONS)
        restored = pst.ScoringTruthProfile.from_dict(profile.to_dict())
        self.assertEqual(profile, restored)
        for target in ("rim_finishing", "floater_short_mid", "midrange", "midrange_preference"):
            self.assertEqual(restored.estimates[target].provenance, profile.estimates[target].provenance)


class TestEngineUntouched(unittest.TestCase):
    """R. Engine untouched."""

    def test_r_ingestion_and_temporal_modules_never_import_engine_resolver_internals(self):
        import inspect
        import player_shot_event_ingestion as psei
        for module_source in (inspect.getsource(psei), inspect.getsource(psst)):
            for forbidden in ("possession_engine", "possession_orchestrator._dispatch",
                              "shot_resolution.", "interior_shot_resolution.", "drive_resolution."):
                self.assertNotIn(forbidden, module_source)

    def test_r_overlay_still_touches_only_target_fields_with_shot_event_evidence(self):
        profile = psst.build_scoring_truth_profile_as_of_date(CURRY, "2023-12-15", "2023-24", _SEASONS)
        baseline = PlayerSimulationProfile.synthetic(CURRY, "HOME")
        result = pst.apply_scoring_truth_to_simulation_profile(baseline, profile)
        self.assertEqual(result.rim_finishing_shrunk_rate, profile.value("rim_finishing"))
        self.assertEqual(result.defensive_playmaking_per36, baseline.defensive_playmaking_per36)


if __name__ == "__main__":
    unittest.main()
