"""Focused tests for the Real Player / Team Truth phase's scoring-ability/tendency bridge
(player_scoring_truth.py) and its supporting player_identity.py adapter addition."""
import unittest
from unittest.mock import patch

import player_identity as pid
import player_scoring_truth as pst
from possession_orchestrator import PlayerSimulationProfile


class TestShotZoneAdapter(unittest.TestCase):
    """New this phase: player_identity.estimate_shot_zone_attribute_by_id."""

    def test_resolved_id_calls_unmodified_shot_zone_estimator_with_resolved_name(self):
        with patch("shot_zone_estimation.estimate_shot_zone_attribute") as mock_est:
            from shot_zone_estimation import ShotZoneEstimationResult
            mock_est.return_value = ShotZoneEstimationResult(
                attribute="rim_finishing", seasons_used=[], weighted_raw_rate=None,
                shrunk_rate=None, league_avg_rate=None, percentile_rating=None, total_weight=0.0,
            )
            resolution, result = pid.estimate_shot_zone_attribute_by_id(
                "1630178", "2023-24", "rim_finishing", ["2023-24"])
        mock_est.assert_called_once_with("Tyrese Maxey", "2023-24", "rim_finishing", ["2023-24"])
        self.assertEqual(resolution.state, pid.RESOLVED)
        self.assertIsNotNone(result)

    def test_unresolved_id_never_calls_the_estimator(self):
        with patch("shot_zone_estimation.estimate_shot_zone_attribute") as mock_est:
            resolution, result = pid.estimate_shot_zone_attribute_by_id(
                "999999999", "2023-24", "rim_finishing", ["2023-24"])
        mock_est.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(resolution.state, pid.NON_NBA_ONLY)


class TestScoringTruthEstimateInvariants(unittest.TestCase):
    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            pst.ScoringTruthEstimate(name="x", kind="not_a_kind", player_id="1", as_of_season="2023-24",
                                      value=None, confidence=None, sample_size=None, source="s", param_source=None)

    def test_confidence_out_of_bounds_rejected(self):
        with self.assertRaises(ValueError):
            pst.ScoringTruthEstimate(name="x", kind="ability", player_id="1", as_of_season="2023-24",
                                      value=0.5, confidence=1.5, sample_size=None, source="s", param_source=None)


class TestBuildScoringTruthProfileWithRealEvidence(unittest.TestCase):
    """Stephen Curry (real player_id 201939) -- real, cached, multi-season evidence exists for
    every one of this phase's 8 target fields as of 2023-24. Values are checked directionally
    (a real elite three-point shooter/high-usage guard), not pinned to exact decimals -- the
    underlying estimators' own exact-value tests already live in their own test files."""

    @classmethod
    def setUpClass(cls):
        cls.seasons = [f"{y}-{str(y + 1)[-2:]}" for y in range(1996, 2024)]
        cls.truth = pst.build_scoring_truth_profile("201939", "2023-24", cls.seasons)

    def test_identity_resolved_to_the_correct_real_player(self):
        self.assertEqual(self.truth.identity_state, pid.RESOLVED)
        self.assertEqual(self.truth.canonical_name, "Stephen Curry")

    def test_every_target_field_is_always_represented(self):
        for name in (*pst.ABILITY_TARGETS, *pst.TENDENCY_TARGETS):
            self.assertIn(name, self.truth.estimates)

    def test_real_evidence_produces_plausible_directional_values(self):
        # a real, elite three-point shooter: three_point rate well above a league-average look,
        # and a strongly positive three_point_preference (shoots threes far more than average).
        self.assertGreater(self.truth.value("three_point"), 0.37)
        self.assertGreater(self.truth.value("three_point_preference"), 0.0)
        # free_throw rate for a real elite FT shooter should be well above league average (~0.78).
        self.assertGreater(self.truth.value("free_throw"), 0.85)
        for name in (*pst.ABILITY_TARGETS, *pst.TENDENCY_TARGETS):
            self.assertTrue(self.truth.has_real_evidence(name), name)

    def test_ability_values_are_real_probabilities(self):
        for name in pst.ABILITY_TARGETS:
            value = self.truth.value(name)
            self.assertTrue(0.0 < value < 1.0, (name, value))

    def test_confidence_and_sample_size_are_populated_alongside_value(self):
        for name in (*pst.ABILITY_TARGETS, *pst.TENDENCY_TARGETS):
            est = self.truth.estimates[name]
            self.assertIsNotNone(est.confidence, name)
            self.assertIsNotNone(est.sample_size, name)
            self.assertGreater(est.sample_size, 0, name)

    def test_deterministic_pure_function(self):
        second = pst.build_scoring_truth_profile("201939", "2023-24", self.seasons)
        self.assertEqual(self.truth, second)


class TestMissingEvidenceIsNeverFabricated(unittest.TestCase):
    def test_unresolvable_player_id_leaves_every_target_unestimated(self):
        truth = pst.build_scoring_truth_profile("999999999", "2023-24", ["2023-24"])
        self.assertEqual(truth.identity_state, pid.NON_NBA_ONLY)
        for name in (*pst.ABILITY_TARGETS, *pst.TENDENCY_TARGETS):
            self.assertIsNone(truth.value(name), name)
            self.assertFalse(truth.has_real_evidence(name), name)
        self.assertEqual(truth.coverage_summary(), {name: False for name in truth.coverage_summary()})

    def test_no_evidence_before_a_players_real_debut_season(self):
        """A resolved real player with zero real cached seasons behind the as-of cutoff must
        report unestimated targets, never a fabricated rookie-year guess."""
        truth = pst.build_scoring_truth_profile("201939", "2007-08", ["2007-08"])  # before Curry's real debut
        self.assertEqual(truth.identity_state, pid.RESOLVED)
        for name in (*pst.ABILITY_TARGETS, *pst.TENDENCY_TARGETS):
            self.assertIsNone(truth.value(name), name)


class TestNoTemporalLeakage(unittest.TestCase):
    def test_as_of_season_passed_through_unchanged_to_every_sub_call(self):
        with patch("player_ability_estimation.estimate_attribute") as mock_ability, \
             patch("shot_zone_estimation.estimate_shot_zone_attribute") as mock_zone, \
             patch("player_tendencies_estimation.estimate_tendency") as mock_tendency:
            from player_ability_estimation import EstimationResult
            from shot_zone_estimation import ShotZoneEstimationResult
            from player_tendencies_estimation import PlayerTendencyEstimate
            mock_ability.return_value = EstimationResult(
                attribute="three_point", seasons_used=[], weighted_raw_rate=None, shrunk_rate=None,
                league_avg_rate=None, percentile_rating=None, total_weight=0.0)
            mock_zone.return_value = ShotZoneEstimationResult(
                attribute="rim_finishing", seasons_used=[], weighted_raw_rate=None, shrunk_rate=None,
                league_avg_rate=None, percentile_rating=None, total_weight=0.0)
            mock_tendency.return_value = PlayerTendencyEstimate(
                player_name="Tyrese Maxey", season="2018-19", tendency="drive_aggression")
            pst.build_scoring_truth_profile("1630178", "2018-19", ["2018-19"])
        for call in mock_ability.call_args_list:
            self.assertEqual(call.args[1], "2018-19")
        for call in mock_zone.call_args_list:
            self.assertEqual(call.args[1], "2018-19")
        for call in mock_tendency.call_args_list:
            self.assertEqual(call.args[1], "2018-19")


class TestApplyScoringTruthToSimulationProfile(unittest.TestCase):
    def setUp(self):
        self.baseline = PlayerSimulationProfile.synthetic("1", "HOME")

    def test_real_values_override_target_fields_only(self):
        truth = pst.ScoringTruthProfile(
            player_id="1", canonical_name="Test Player", as_of_season="2023-24", identity_state=pid.RESOLVED,
            estimates={
                "three_point": pst.ScoringTruthEstimate(
                    name="three_point", kind="ability", player_id="1", as_of_season="2023-24",
                    value=0.42, confidence=0.9, sample_size=1000.0, source="s", param_source="calibrated"),
            },
        )
        result = pst.apply_scoring_truth_to_simulation_profile(self.baseline, truth)
        self.assertEqual(result.three_point_shrunk_rate, 0.42)
        # every other field, including other targets absent from `estimates`, must stay at the
        # baseline's own existing value -- never zeroed or overwritten with None.
        self.assertEqual(result.midrange_shrunk_rate, self.baseline.midrange_shrunk_rate)
        self.assertEqual(result.rim_finishing_shrunk_rate, self.baseline.rim_finishing_shrunk_rate)
        self.assertEqual(result.defensive_playmaking_per36, self.baseline.defensive_playmaking_per36)

    def test_missing_target_field_never_overwrites_baseline_with_none(self):
        truth = pst.ScoringTruthProfile(
            player_id="1", canonical_name=None, as_of_season="2023-24", identity_state=pid.NON_NBA_ONLY,
            estimates={
                "three_point": pst.ScoringTruthEstimate(
                    name="three_point", kind="ability", player_id="1", as_of_season="2023-24",
                    value=None, confidence=None, sample_size=None, source="s", param_source=None),
            },
        )
        result = pst.apply_scoring_truth_to_simulation_profile(self.baseline, truth)
        self.assertEqual(result.three_point_shrunk_rate, self.baseline.three_point_shrunk_rate)
        self.assertIsNotNone(result.three_point_shrunk_rate)  # never set to None

    def test_baseline_is_not_mutated(self):
        truth = pst.ScoringTruthProfile(
            player_id="1", canonical_name=None, as_of_season="2023-24", identity_state=pid.RESOLVED,
            estimates={
                "three_point": pst.ScoringTruthEstimate(
                    name="three_point", kind="ability", player_id="1", as_of_season="2023-24",
                    value=0.42, confidence=0.9, sample_size=1000.0, source="s", param_source=None),
            },
        )
        before = self.baseline.three_point_shrunk_rate
        pst.apply_scoring_truth_to_simulation_profile(self.baseline, truth)
        self.assertEqual(self.baseline.three_point_shrunk_rate, before)  # frozen dataclass, replace() never mutates


class TestBuildPartialSimulationProfileIntegration(unittest.TestCase):
    """Full real-evidence-to-engine-ready-profile pipeline, and a real simulated game proving
    the resulting profile is fully consumable by the frozen detailed engine with zero engine
    changes."""

    def test_real_player_profile_plugs_directly_into_a_real_simulated_game(self):
        from detailed_game import simulate_detailed_game
        seasons = [f"{y}-{str(y + 1)[-2:]}" for y in range(1996, 2024)]
        truth, profile = pst.build_partial_simulation_profile("201939", "HOME", "2023-24", seasons)
        self.assertTrue(truth.has_real_evidence("three_point"))
        self.assertIsNotNone(profile.three_point_shrunk_rate)
        # non-target fields fall back to the same synthetic default -- explicitly out of scope.
        self.assertEqual(profile.defensive_playmaking_per36,
                         PlayerSimulationProfile.synthetic("201939", "HOME").defensive_playmaking_per36)

        home_five = ("201939", "2", "3", "4", "5")
        away_five = tuple(str(i) for i in range(11, 16))
        profiles = {"201939": profile}
        for p in ("2", "3", "4", "5"):
            profiles[p] = PlayerSimulationProfile.synthetic(p, "HOME")
        for p in away_five:
            profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")

        result = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=25000)
        self.assertGreater(result.final_home_score, 0)
        self.assertGreater(result.final_away_score, 0)
        self.assertGreater(result.total_possessions, 0)


if __name__ == "__main__":
    unittest.main()
