"""Focused tests for the Playmaking + Ball Security V1 phase
(playmaking_estimation.py + player_playmaking_truth.py)."""
import unittest
from unittest.mock import patch

import player_identity as pid
import player_playmaking_truth as ppt
import player_scoring_truth_temporal as psst
from playmaking_estimation import PLAYMAKING_ATTRIBUTES, estimate_playmaking_attribute
from possession_orchestrator import PlayerSimulationProfile

_SEASONS = [f"{y}-{str(y + 1)[-2:]}" for y in range(1996, 2026)]
CHRIS_PAUL = "101108"


class TestCorrectDenominators(unittest.TestCase):
    """A. Correct denominators -- each target reads its own real, distinct evidence pair, never
    a raw totals-only proxy (raw assists / AST-per-game / raw turnovers-per-game)."""

    def test_a_passing_accuracy_uses_bad_pass_over_passes_made_plus_bad_pass(self):
        import json
        with open("cache/2023-24/player_passing_tracking.json") as f:
            passing = json.load(f)["players"][CHRIS_PAUL]
        with open("cache/2023-24/player_turnover_subtypes.json") as f:
            turnover = json.load(f)["players"][CHRIS_PAUL]
        expected = 1 - turnover["bad_pass"] / (passing["passes_made"] + turnover["bad_pass"])
        from playmaking_estimation import _passing_accuracy_evidence
        rate, sample = _passing_accuracy_evidence(CHRIS_PAUL, "2023-24")
        self.assertAlmostEqual(rate, expected, places=9)

    def test_a_playmaking_vision_uses_potential_ast_over_passes_made_not_raw_assists(self):
        import json
        with open("cache/2023-24/player_passing_tracking.json") as f:
            passing = json.load(f)["players"][CHRIS_PAUL]
        from playmaking_estimation import _playmaking_vision_evidence
        rate, sample = _playmaking_vision_evidence(CHRIS_PAUL, "2023-24")
        self.assertAlmostEqual(rate, passing["potential_ast"] / passing["passes_made"], places=9)
        self.assertNotAlmostEqual(rate, passing["ast"] / passing["passes_made"], places=2)

    def test_a_ball_security_uses_handling_error_over_touches_not_all_turnovers(self):
        import json
        with open("cache/2023-24/player_handling_exposure.json") as f:
            handling = json.load(f)["players"][CHRIS_PAUL]
        with open("cache/2023-24/player_turnover_subtypes.json") as f:
            turnover = json.load(f)["players"][CHRIS_PAUL]
        from playmaking_estimation import _ball_security_evidence
        rate, sample = _ball_security_evidence(CHRIS_PAUL, "2023-24")
        expected = 1 - turnover["handling_error"] / handling["touches"]
        self.assertAlmostEqual(rate, expected, places=9)
        # NOT the naive "all turnovers" proxy -- handling_error excludes bad_pass/offensive fouls.
        self.assertLess(turnover["handling_error"], turnover["total"])


class TestShrinkage(unittest.TestCase):
    """B. Shrinkage low/high sample."""

    def test_b_low_sample_player_shrinks_toward_league_average(self):
        # a real, thin-minutes 2023-24 player (few touches/passes) should land close to league avg.
        import json
        with open("cache/2023-24/player_handling_exposure.json") as f:
            handling = json.load(f)["players"]
        thin = min(handling.items(), key=lambda kv: kv[1].get("touches", 1e9) if kv[1].get("touches", 0) > 0 else 1e9)
        result = estimate_playmaking_attribute(thin[0], "2023-24", "ball_security", _SEASONS)
        if result.shrunk_rate is not None and result.league_avg_rate is not None and result.total_weight < 300:
            self.assertLess(abs(result.shrunk_rate - result.league_avg_rate), 0.02)

    def test_b_high_sample_player_stays_close_to_own_raw_rate(self):
        result = estimate_playmaking_attribute(CHRIS_PAUL, "2023-24", "ball_security", _SEASONS)
        self.assertLess(abs(result.shrunk_rate - result.weighted_raw_rate), 0.005)


class TestPassingAccuracyNotVision(unittest.TestCase):
    """C. passing_accuracy != playmaking_vision."""

    def test_c_the_two_constructs_are_not_the_same_statistic_for_a_real_playmaker(self):
        acc = estimate_playmaking_attribute(CHRIS_PAUL, "2023-24", "passing_accuracy", _SEASONS)
        vision = estimate_playmaking_attribute(CHRIS_PAUL, "2023-24", "playmaking_vision", _SEASONS)
        # different absolute scale (accuracy near 1.0, vision a much smaller share) and different
        # percentile rank for the SAME real player -- not a deterministic transform of one another.
        self.assertGreater(acc.shrunk_rate, 0.9)
        self.assertLess(vision.shrunk_rate, 0.5)
        self.assertNotAlmostEqual(acc.percentile_rating, vision.percentile_rating, delta=5)


class TestTurnoverSubtypeFiltering(unittest.TestCase):
    """D. Turnover subtype filtering -- handling_error and bad_pass are real, distinct categories,
    never conflated."""

    def test_d_handling_error_and_bad_pass_are_distinct_categories(self):
        from turnover_ingestion import CATEGORY_BAD_PASS, CATEGORY_HANDLING, ALL_CATEGORIES
        self.assertIn(CATEGORY_HANDLING, ALL_CATEGORIES)
        self.assertIn(CATEGORY_BAD_PASS, ALL_CATEGORIES)
        self.assertNotEqual(CATEGORY_HANDLING, CATEGORY_BAD_PASS)


class TestMissingStaysMissing(unittest.TestCase):
    """E. Missing remains missing."""

    def test_e_a_player_absent_from_tracking_data_stays_missing(self):
        result = estimate_playmaking_attribute("999999999", "2023-24", "ball_security", ["2023-24"])
        self.assertIsNone(result.shrunk_rate)

    def test_e_profile_level_missing_is_never_fabricated(self):
        profile = ppt.build_playmaking_truth_profile("999999999", "2023-24", ["2023-24"])
        for attr in PLAYMAKING_ATTRIBUTES:
            self.assertIsNone(profile.value(attr))
            self.assertEqual(profile.estimates[attr].provenance, psst.MISSING)


class TestTargetOnlyOverlay(unittest.TestCase):
    """F. Target-only overlay -- passing_accuracy is NEVER overlaid (real scale mismatch), only
    playmaking_vision/ball_security are, and only when real evidence exists."""

    def test_f_passing_accuracy_never_touches_the_engine_field(self):
        profile = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2023-24", _SEASONS)
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        result = ppt.apply_playmaking_truth_to_simulation_profile(baseline, profile)
        self.assertEqual(result.passing_accuracy_ast_pct, baseline.passing_accuracy_ast_pct)

    def test_f_overlay_touches_only_the_two_target_fields(self):
        from dataclasses import fields
        profile = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2023-24", _SEASONS)
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        result = ppt.apply_playmaking_truth_to_simulation_profile(baseline, profile)
        touched = {"playmaking_vision_shrunk_rate", "ball_security_error_rate"}
        for f in fields(PlayerSimulationProfile):
            if f.name in touched:
                continue
            self.assertEqual(getattr(result, f.name), getattr(baseline, f.name), f.name)

    def test_f_missing_evidence_leaves_fields_untouched(self):
        profile = ppt.build_playmaking_truth_profile("999999999", "2023-24", ["2023-24"])
        baseline = PlayerSimulationProfile.synthetic("999999999", "HOME")
        result = ppt.apply_playmaking_truth_to_simulation_profile(baseline, profile)
        self.assertEqual(result, baseline)


class TestIdentityAndTrade(unittest.TestCase):
    """G. Player-ID deterministic. H. Trade persistence."""

    def test_g_id_resolution_is_stable(self):
        first = pid.resolve_id_to_name(CHRIS_PAUL)
        second = pid.resolve_id_to_name(CHRIS_PAUL)
        self.assertEqual(first, second)

    def test_h_no_team_parameter_exists_in_the_evidence_or_estimate_path(self):
        import inspect
        for fn in (estimate_playmaking_attribute, ppt.build_playmaking_truth_profile):
            sig = inspect.signature(fn)
            self.assertNotIn("team", " ".join(sig.parameters.keys()).lower())


class TestFutureSeasonSafety(unittest.TestCase):
    """I. Future-season safety."""

    def test_i_a_future_seasons_data_never_leaks_into_an_earlier_snapshot(self):
        before = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2018-19", _SEASONS)

        import passing_tracking_ingestion as pti
        real_load = pti.load_passing_tracking

        def poisoned(season):
            data = real_load(season)
            if season <= "2018-19" or not data:
                return data
            poisoned_data = dict(data)
            poisoned_data[CHRIS_PAUL] = dict(poisoned_data.get(CHRIS_PAUL, {}))
            poisoned_data[CHRIS_PAUL].update(passes_made=999999.0, potential_ast=999999.0)
            return poisoned_data

        with patch("passing_tracking_ingestion.load_passing_tracking", side_effect=poisoned), \
             patch("playmaking_estimation.load_passing_tracking", side_effect=poisoned):
            after = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2018-19", _SEASONS)
        self.assertEqual(before, after)


class TestOpeningNight(unittest.TestCase):
    """L. Opening-night behavior."""

    def test_l_pregame_before_current_season_rests_on_prior_season_only(self):
        profile = ppt.build_playmaking_truth_profile_as_of_date(CHRIS_PAUL, "2023-10-01", "2023-24", _SEASONS)
        for attr in PLAYMAKING_ATTRIBUTES:
            self.assertIn(profile.estimates[attr].provenance, (psst.PRIOR_SEASON_ONLY, psst.MISSING))
        self.assertIsNotNone(profile.value("ball_security"))


class TestScaleContract(unittest.TestCase):
    """M. Scale contract -- every value is a real [0,1] ability-scale rate, higher = better,
    for all three targets (even though ball_security is INVERTED at overlay time only)."""

    def test_m_all_three_values_are_bounded_ability_scale_rates(self):
        profile = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2023-24", _SEASONS)
        for attr in PLAYMAKING_ATTRIBUTES:
            value = profile.value(attr)
            self.assertIsNotNone(value)
            self.assertTrue(0.0 <= value <= 1.0, (attr, value))


class TestSimulationProfileIntegration(unittest.TestCase):
    """N. Simulation-profile integration."""

    def test_n_real_profile_plugs_into_a_real_simulated_game(self):
        from detailed_game import simulate_detailed_game
        profile = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2023-24", _SEASONS)
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        overlaid = ppt.apply_playmaking_truth_to_simulation_profile(baseline, profile)
        home_five = (CHRIS_PAUL, "2", "3", "4", "5")
        away_five = tuple(str(i) for i in range(11, 16))
        profiles = {CHRIS_PAUL: overlaid}
        for p in ("2", "3", "4", "5"):
            profiles[p] = PlayerSimulationProfile.synthetic(p, "HOME")
        for p in away_five:
            profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
        result = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=25000)
        self.assertGreater(result.total_possessions, 0)


class TestEngineUntouchedAndDeterminism(unittest.TestCase):
    """O. Engine mechanics untouched. P. Deterministic serialization. Q. Repeated build deterministic."""

    def test_o_modules_never_import_engine_resolver_internals(self):
        import inspect
        for module in (__import__("playmaking_estimation"), ppt):
            source = inspect.getsource(module)
            for forbidden in ("possession_engine", "possession_orchestrator._dispatch",
                              "shot_resolution.", "interior_shot_resolution.", "drive_resolution."):
                self.assertNotIn(forbidden, source)

    def test_p_serialization_round_trips_exactly(self):
        profile = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2023-24", _SEASONS)
        restored = ppt.PlaymakingTruthProfile.from_dict(profile.to_dict())
        self.assertEqual(profile, restored)

    def test_q_repeated_build_is_byte_identical(self):
        first = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2023-24", _SEASONS)
        second = ppt.build_playmaking_truth_profile(CHRIS_PAUL, "2023-24", _SEASONS)
        self.assertEqual(first, second)


class TestDormantFieldDocumented(unittest.TestCase):
    """R. Dormant-field behavior documented -- playmaking_vision_shrunk_rate is a real,
    pre-existing field that no resolver currently reads (confirmed directly), and this phase does
    not secretly wire it into engine logic."""

    def test_r_playmaking_vision_field_is_not_read_by_any_resolver_module(self):
        import inspect
        modules_to_check = ["possession_orchestrator", "pass_resolution", "drive_resolution",
                             "shot_resolution", "interior_shot_resolution"]
        for name in modules_to_check:
            module = __import__(name)
            source = inspect.getsource(module)
            # the field NAME may appear once (its own definition/passthrough), but never as a
            # ".playmaking_vision_shrunk_rate" attribute READ feeding a resolver formula.
            reads = source.count("playmaking_vision_shrunk_rate")
            if name == "possession_orchestrator":
                # field definition (dataclass attribute) + one documentation comment explicitly
                # stating it is never read by perceive() -- confirmed by direct inspection, not a
                # real consumption site.
                self.assertLessEqual(reads, 2, "field should only be defined/documented, never consumed")
                reads_line = [ln for ln in source.splitlines() if "playmaking_vision_shrunk_rate" in ln]
                self.assertTrue(any(ln.strip().startswith("#") for ln in reads_line))
                self.assertTrue(any("Optional[float] = None" in ln for ln in reads_line))
            else:
                self.assertEqual(reads, 0, name)


if __name__ == "__main__":
    unittest.main()
