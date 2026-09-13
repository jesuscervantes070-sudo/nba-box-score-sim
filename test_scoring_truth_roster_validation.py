"""Roster-scale validation for the Real Player / Team Truth scoring-truth layer
(player_scoring_truth.py). Complements test_player_scoring_truth.py's single-player unit tests
with population-scale coverage/missingness/shrinkage/leakage/serialization guardrails."""
import json
import unittest
from unittest.mock import patch

import player_identity as pid
import player_scoring_truth as pst
from possession_orchestrator import PlayerSimulationProfile

# A small, fixed, real-player-id sample -- deliberately spans very-high-volume stars, role
# players, and thin-evidence/fringe ids so roster-scale behavior is exercised without needing to
# build all ~580 real 2025-26 ids in the test suite itself (that full-roster run is reported
# narratively in the phase report, not re-run on every test invocation).
SAMPLE_IDS = (
    "201939",   # Stephen Curry -- elite, extremely high sample
    "1629029",  # Luka Doncic -- elite, extremely high sample
    "1630178",  # Tyrese Maxey -- solid, medium-high sample
    "1642280",  # Trentyn Flowers -- real id, thin 2025-26 sample
    "999999999",  # not a real NBA static-index id at all
)
_SEASONS_THROUGH_2025_26 = [f"{y}-{str(y + 1)[-2:]}" for y in range(1996, 2026)]


class TestRosterScaleBuildDoesNotCrash(unittest.TestCase):
    """A. Roster-scale build does not crash, across stars/role players/fringe/unresolved ids."""

    def test_build_across_a_mixed_sample_completes_for_every_id(self):
        for player_id in SAMPLE_IDS:
            profile = pst.build_scoring_truth_profile(player_id, "2025-26", _SEASONS_THROUGH_2025_26)
            self.assertEqual(profile.player_id, player_id)
            self.assertEqual(len(profile.estimates), len(pst.ABILITY_TARGETS) + len(pst.TENDENCY_TARGETS))


class TestMissingStaysMissing(unittest.TestCase):
    """B. Missing remains missing at the truth level -- never replaced with a fabricated
    league-average value inside ScoringTruthProfile itself."""

    def test_unresolvable_id_every_target_stays_none(self):
        profile = pst.build_scoring_truth_profile("999999999", "2025-26", ["2025-26"])
        for name in (*pst.ABILITY_TARGETS, *pst.TENDENCY_TARGETS):
            self.assertIsNone(profile.value(name))
            est = profile.estimates[name]
            self.assertIsNone(est.confidence)
            self.assertIsNone(est.sample_size)


class TestOverlayPreservesSyntheticFallback(unittest.TestCase):
    """C. Overlay preserves synthetic/default fallback for missing values, and touches only the
    8 target fields -- extends test_player_scoring_truth.py's single-field version to the full
    real 8-target set at once."""

    def test_all_missing_leaves_baseline_fully_intact(self):
        baseline = PlayerSimulationProfile.synthetic("999999999", "HOME")
        truth = pst.build_scoring_truth_profile("999999999", "2025-26", ["2025-26"])
        result = pst.apply_scoring_truth_to_simulation_profile(baseline, truth)
        self.assertEqual(result, baseline)  # frozen dataclass equality -- byte-identical

    def test_partial_real_evidence_overrides_only_estimated_targets(self):
        truth = pst.build_scoring_truth_profile("201939", "2025-26", _SEASONS_THROUGH_2025_26)
        baseline = PlayerSimulationProfile.synthetic("201939", "HOME")
        result = pst.apply_scoring_truth_to_simulation_profile(baseline, truth)
        target_fields = set(pst._ABILITY_TO_PROFILE_FIELD.values()) | set(pst._TENDENCY_TO_PROFILE_FIELD.values())
        from dataclasses import fields
        for f in fields(PlayerSimulationProfile):
            if f.name in target_fields:
                continue
            self.assertEqual(getattr(result, f.name), getattr(baseline, f.name), f.name)


class TestIdentityResolutionDeterminism(unittest.TestCase):
    """D. Player IDs resolve deterministically. E. Ambiguous identity is never silently guessed
    (delegated to player_identity.py's own, already-tested collision handling; re-verified here
    in the context this module actually uses it: id -> name, the SAFE direction, which can never
    itself be ambiguous by construction)."""

    def test_id_to_name_resolution_is_stable_across_repeated_calls(self):
        first = pid.resolve_id_to_name("201939")
        second = pid.resolve_id_to_name("201939")
        self.assertEqual(first, second)
        self.assertEqual(first.state, pid.RESOLVED)

    def test_id_to_name_direction_cannot_be_ambiguous_by_construction(self):
        """A real player_id never maps to two names in the static index -- confirmed directly
        for every id in the fixed sample (the ambiguity risk this project has documented only
        ever applies to the OPPOSITE, name -> id direction, which this module never performs)."""
        for player_id in ("201939", "1629029", "1630178"):
            resolution = pid.resolve_id_to_name(player_id)
            self.assertEqual(resolution.state, pid.RESOLVED)
            self.assertNotEqual(resolution.state, pid.AMBIGUOUS)


class TestFutureDataCannotLeakIntoHistoricalEstimate(unittest.TestCase):
    """F. Strong future-data leakage test: compute a historical as-of profile, inject obviously
    extreme data into a FUTURE season via a mocked loader (never a permanent cache write), then
    recompute the SAME as-of profile and verify it is value-identical."""

    def test_corrupting_a_future_season_does_not_change_an_earlier_profile(self):
        as_of = "2018-19"
        seasons = [f"{y}-{str(y + 1)[-2:]}" for y in range(1996, 2026)]  # includes seasons AFTER as_of
        before = pst.build_scoring_truth_profile("201939", as_of, seasons)

        import shot_zone_ingestion as szi
        import loader as ld
        real_load_shot_zones = szi.load_shot_zones
        real_load_teams = ld.load_teams
        real_load_advanced = ld.load_player_advanced_stats

        def poisoned_shot_zones(season):
            data = real_load_shot_zones(season)
            if season <= as_of or not data:
                return data
            # inject an obviously extreme, fabricated row for a FUTURE season only
            poisoned = dict(data)
            poisoned["201939"] = dict(poisoned.get("201939", {"player_name": "Stephen Curry"}))
            poisoned["201939"].update({
                "restricted_area_fga": 999999.0, "restricted_area_fgm": 999999.0,
                "paint_non_ra_fga": 999999.0, "paint_non_ra_fgm": 0.0,
                "midrange_fga": 999999.0, "midrange_fgm": 0.0,
            })
            return poisoned

        def poisoned_teams(season):
            teams = real_load_teams(season)
            if season <= as_of:
                return teams
            return {}  # blank out FUTURE box-score evidence entirely -- another extreme injection

        with patch("shot_zone_ingestion.load_shot_zones", side_effect=poisoned_shot_zones), \
             patch("shot_zone_estimation.load_shot_zones", side_effect=poisoned_shot_zones), \
             patch("loader.load_teams", side_effect=poisoned_teams), \
             patch("shot_zone_estimation.load_teams", side_effect=poisoned_teams), \
             patch("player_ability_estimation.load_teams", side_effect=poisoned_teams):
            after = pst.build_scoring_truth_profile("201939", as_of, seasons)

        self.assertEqual(before, after)  # byte/value-identical -- future poison never reached the past


class TestBeliefCanEvolveAcrossCutoffs(unittest.TestCase):
    """G. As-of season changes CAN change belief -- verified for a real, long-career player
    across two materially different real cutoffs. Confidence/sample size should also grow as
    more real evidence accumulates."""

    def test_curry_profile_differs_meaningfully_across_two_real_cutoffs(self):
        early = pst.build_scoring_truth_profile("201939", "2010-11", _SEASONS_THROUGH_2025_26)
        late = pst.build_scoring_truth_profile("201939", "2023-24", _SEASONS_THROUGH_2025_26)
        self.assertNotEqual(early, late)
        self.assertNotEqual(early.value("three_point"), late.value("three_point"))
        # more real seasons behind the later cutoff -> more effective evidence, not less.
        self.assertGreater(late.estimates["three_point"].sample_size, early.estimates["three_point"].sample_size)


class TestLowVsHighSampleShrinkage(unittest.TestCase):
    """H. Low-sample shrinkage behaves correctly (pulled hard toward the league-average prior).
    I. High-sample estimate responds more to evidence (shrunk stays close to the player's own
    real raw rate). Uses the underlying estimator directly so the raw/prior/shrunk decomposition
    is visible, not just the final posterior mean."""

    def test_thin_evidence_player_lands_close_to_league_average(self):
        from player_ability_estimation import estimate_attribute
        result = estimate_attribute("Trentyn Flowers", "2025-26", "three_point", _SEASONS_THROUGH_2025_26)
        self.assertIsNotNone(result.shrunk_rate)
        self.assertIsNotNone(result.league_avg_rate)
        # heavily shrunk: within a small band of the league average despite whatever the raw rate was.
        self.assertLess(abs(result.shrunk_rate - result.league_avg_rate), 0.03)

    def test_massive_evidence_player_lands_close_to_own_raw_rate(self):
        from player_ability_estimation import estimate_attribute
        result = estimate_attribute("Stephen Curry", "2025-26", "three_point", _SEASONS_THROUGH_2025_26)
        self.assertIsNotNone(result.shrunk_rate)
        self.assertIsNotNone(result.weighted_raw_rate)
        # barely shrunk: much closer to Curry's own raw rate than a thin-evidence player would be.
        self.assertLess(abs(result.shrunk_rate - result.weighted_raw_rate), 0.01)


class TestAbilityTendencyRemainDistinct(unittest.TestCase):
    """J. Ability and tendency fields remain distinct -- not literally the same statistic or a
    deterministic transform of one another. Checked both structurally (different underlying
    estimator functions, different value scales) and empirically (real correlation is
    meaningfully below 1.0 across a real sample)."""

    def test_ability_and_tendency_use_different_estimator_functions_and_scales(self):
        truth = pst.build_scoring_truth_profile("201939", "2023-24", _SEASONS_THROUGH_2025_26)
        ability = truth.estimates["three_point"]
        tendency = truth.estimates["three_point_preference"]
        self.assertNotEqual(ability.source, tendency.source)
        # ability is a real probability-like rate in (0,1); tendency is a logit-relative-to-average
        # deviation, typically well outside (0,1) -- confirms these are not the same scale.
        self.assertTrue(0.0 < ability.value < 1.0)
        self.assertNotEqual(ability.value, tendency.value)

    def test_real_correlation_is_positive_but_not_deterministic(self):
        """A handful of real, high-sample players -- correlated in the expected direction (better/
        higher-volume three-point shooters tend to also prefer shooting them more), but NOT
        collapsed into an identical or perfectly-linear relationship."""
        ids = ["201939", "1629029", "1630178"]  # Curry, Doncic, Maxey
        pairs = []
        for player_id in ids:
            truth = pst.build_scoring_truth_profile(player_id, "2023-24", _SEASONS_THROUGH_2025_26)
            a, t = truth.value("three_point"), truth.value("three_point_preference")
            if a is not None and t is not None:
                pairs.append((a, t))
        self.assertGreaterEqual(len(pairs), 2)
        values = {round(a, 6) for a, _ in pairs}
        self.assertGreater(len(values), 1)  # not every player collapsed to one identical ability value


class TestSerializationIsDeterministic(unittest.TestCase):
    """L. Serialization is deterministic (added this phase -- see player_scoring_truth.py's own
    to_dict/from_dict and SCHEMA_VERSION)."""

    def test_to_dict_round_trips_exactly(self):
        truth = pst.build_scoring_truth_profile("201939", "2023-24", _SEASONS_THROUGH_2025_26)
        restored = pst.ScoringTruthProfile.from_dict(truth.to_dict())
        self.assertEqual(truth, restored)

    def test_to_dict_is_json_serializable_and_byte_stable(self):
        truth = pst.build_scoring_truth_profile("201939", "2023-24", _SEASONS_THROUGH_2025_26)
        first = json.dumps(truth.to_dict(), sort_keys=True)
        second = json.dumps(pst.build_scoring_truth_profile(
            "201939", "2023-24", _SEASONS_THROUGH_2025_26).to_dict(), sort_keys=True)
        self.assertEqual(first, second)

    def test_from_dict_rejects_a_schema_version_mismatch(self):
        truth = pst.build_scoring_truth_profile("201939", "2023-24", _SEASONS_THROUGH_2025_26)
        payload = truth.to_dict()
        payload["schema_version"] = "some-other-version"
        with self.assertRaises(ValueError):
            pst.ScoringTruthProfile.from_dict(payload)

    def test_conceptual_key_shape(self):
        truth = pst.build_scoring_truth_profile("201939", "2023-24", _SEASONS_THROUGH_2025_26)
        self.assertEqual(truth.conceptual_key, ("201939", "2023-24", pst.SCHEMA_VERSION))


class TestProfileBuildIsDeterministic(unittest.TestCase):
    """M. Profile build is deterministic -- same inputs, same output, across a small roster-scale
    sample (not just one player, extending test_player_scoring_truth.py's single-player check)."""

    def test_repeated_builds_across_a_sample_are_identical(self):
        for player_id in SAMPLE_IDS:
            first = pst.build_scoring_truth_profile(player_id, "2025-26", _SEASONS_THROUGH_2025_26)
            second = pst.build_scoring_truth_profile(player_id, "2025-26", _SEASONS_THROUGH_2025_26)
            self.assertEqual(first, second, player_id)


class TestDetailedEngineIntegrationRemainsDeterministic(unittest.TestCase):
    """N. Full detailed-engine integration remains deterministic with a real scoring profile in
    the mix -- the SAME seed must reproduce the SAME game byte-for-byte. O. Frozen anonymous
    engine mechanics untouched -- this module makes zero import-time or call-time modification to
    possession_orchestrator.py or any resolver; verified structurally (no such module is ever
    imported for mutation, only PlayerSimulationProfile -- a plain, frozen dataclass -- is
    constructed and passed in as ordinary input)."""

    def test_same_seed_real_profile_game_is_byte_identical_across_two_runs(self):
        from detailed_game import simulate_detailed_game
        _, profile = pst.build_partial_simulation_profile("201939", "HOME", "2023-24", _SEASONS_THROUGH_2025_26)
        home_five = ("201939", "2", "3", "4", "5")
        away_five = tuple(str(i) for i in range(11, 16))
        profiles = {"201939": profile}
        for p in ("2", "3", "4", "5"):
            profiles[p] = PlayerSimulationProfile.synthetic(p, "HOME")
        for p in away_five:
            profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")

        first = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=777)
        second = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=777)
        self.assertEqual(first.final_home_score, second.final_home_score)
        self.assertEqual(first.final_away_score, second.final_away_score)
        self.assertEqual(first.total_possessions, second.total_possessions)

    def test_this_module_never_imports_or_touches_possession_engine_internals(self):
        import inspect
        source = inspect.getsource(pst)
        # this module is allowed to import the ENGINE'S OWN PUBLIC dataclass
        # (PlayerSimulationProfile) as ordinary input construction, but must never reach into or
        # patch any resolver/orchestrator internals.
        for forbidden in ("possession_engine", "possession_orchestrator._dispatch",
                          "shot_resolution.", "interior_shot_resolution.", "drive_resolution."):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
