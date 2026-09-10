"""Focused tests for anthropometrics_profile.py + anthropometrics_estimation.py.
Real network calls faked via the same cache-file-based fakery pattern
used by test_anthropometrics_ingestion.py."""
import json
import unittest
from unittest.mock import patch

import anthropometrics_estimation as ae
import anthropometrics_ingestion as ai
from anthropometrics_profile import (
    INFERRED_REGRESSION, MEASURED_COMBINE, MEASURED_ROSTER, UNAVAILABLE,
    PhysicalObservation, PlayerPhysicalProfile,
)


class TestPhysicalObservation(unittest.TestCase):
    def test_unavailable_must_not_carry_value(self):
        with self.assertRaises(ValueError):
            PhysicalObservation(value=75.0, unit="inches", evidence_mode=UNAVAILABLE, source="x", as_of="2020")

    def test_measured_must_carry_value(self):
        with self.assertRaises(ValueError):
            PhysicalObservation(value=None, unit="inches", evidence_mode=MEASURED_COMBINE, source="x", as_of="2020")

    def test_inferred_requires_model_version(self):
        with self.assertRaises(ValueError):
            PhysicalObservation(value=75.0, unit="inches", evidence_mode=INFERRED_REGRESSION, source="x", as_of="2020")

    def test_round_trip_serialization(self):
        obs = PhysicalObservation(value=82.5, unit="inches", evidence_mode=MEASURED_COMBINE,
                                   source="draftcombineplayeranthro", as_of="2018")
        self.assertEqual(PhysicalObservation.from_dict(obs.to_dict()), obs)


class TestPlayerPhysicalProfile(unittest.TestCase):
    def test_missing_fields_default_unavailable_not_zero(self):
        profile = PlayerPhysicalProfile(player_id="1")
        self.assertEqual(profile.height_in.evidence_mode, UNAVAILABLE)
        self.assertIsNone(profile.height_in.value)  # never a fabricated 0.0

    def test_latest_mass_picks_most_recent_not_average(self):
        obs = (
            PhysicalObservation(value=200.0, unit="lbs", evidence_mode=MEASURED_ROSTER, source="x", as_of="2013-14"),
            PhysicalObservation(value=210.0, unit="lbs", evidence_mode=MEASURED_ROSTER, source="x", as_of="2023-24"),
        )
        profile = PlayerPhysicalProfile(player_id="1", mass_observations=obs)
        self.assertEqual(profile.latest_mass.value, 210.0)  # not (200+210)/2

    def test_mass_observations_never_collapsed(self):
        obs = (
            PhysicalObservation(value=200.0, unit="lbs", evidence_mode=MEASURED_ROSTER, source="x", as_of="2013-14"),
            PhysicalObservation(value=210.0, unit="lbs", evidence_mode=MEASURED_ROSTER, source="x", as_of="2023-24"),
        )
        profile = PlayerPhysicalProfile(player_id="1", mass_observations=obs)
        self.assertEqual(len(profile.mass_observations), 2)

    def test_round_trip_serialization(self):
        profile = PlayerPhysicalProfile(
            player_id="1",
            height_in=PhysicalObservation(value=75.0, unit="inches", evidence_mode=MEASURED_COMBINE, source="x", as_of="2018"),
        )
        restored = PlayerPhysicalProfile.from_dict(json.loads(json.dumps(profile.to_dict())))
        self.assertEqual(restored.height_in.value, 75.0)
        self.assertEqual(restored.player_id, "1")


class TestBuildPhysicalProfile(unittest.TestCase):
    """Fakes the ingestion caches directly (no real network calls, no
    real cache file writes) via unittest.mock.patch on the loader
    functions build_physical_profile actually calls."""

    @classmethod
    def setUpClass(cls):
        # Warm the module-level regression-fit cache from the REAL
        # combine caches before any test patches load_combine_anthro --
        # otherwise a test's fake (empty) combine data would poison the
        # lazily-cached production fit for every other test.
        ae._fit_production_models()

    def _combine_side_effect(self, combine_by_year):
        return lambda y: combine_by_year.get(y, {})

    def _roster_side_effect(self, roster_by_season):
        return lambda s: roster_by_season.get(s, {})

    def test_prefers_measured_combine_over_roster(self):
        combine = {2018: {"999": {"player_name": "Test Player", "position": "G",
                                   "height_wo_shoes_in": 75.0, "height_w_shoes_in": None,
                                   "wingspan_in": 79.0, "standing_reach_in": 99.0, "weight_lbs": 200.0}}}
        roster = {"2018-19": {"999": {"player_name": "Test Player", "listed_height_in": 76.0, "listed_weight_lbs": 205.0}}}
        with patch.object(ai, "load_combine_anthro", side_effect=self._combine_side_effect(combine)), \
             patch.object(ai, "load_roster_physicals", side_effect=self._roster_side_effect(roster)):
            profile = ae.build_physical_profile("999", "2018-19")
        self.assertEqual(profile.height_in.evidence_mode, MEASURED_COMBINE)
        self.assertEqual(profile.height_in.value, 75.0)  # not the 76.0 roster-listed value

    def test_falls_back_to_roster_when_no_combine(self):
        with patch.object(ai, "load_combine_anthro", side_effect=self._combine_side_effect({})), \
             patch.object(ai, "load_roster_physicals",
                          side_effect=self._roster_side_effect(
                              {"2013-14": {"999": {"listed_height_in": 74.0, "listed_weight_lbs": 190.0}}})):
            profile = ae.build_physical_profile("999", "2013-14")
        self.assertEqual(profile.height_in.evidence_mode, MEASURED_ROSTER)
        self.assertEqual(profile.height_in.value, 74.0)

    def test_no_temporal_leakage_combine_after_as_of(self):
        """A combine measurement dated AFTER as_of_season must never be used."""
        combine = {2018: {"999": {"height_wo_shoes_in": 75.0, "wingspan_in": 79.0,
                                   "standing_reach_in": 99.0, "weight_lbs": 200.0}}}
        with patch.object(ai, "load_combine_anthro", side_effect=self._combine_side_effect(combine)), \
             patch.object(ai, "load_roster_physicals", side_effect=self._roster_side_effect({})):
            profile = ae.build_physical_profile("999", "2010-11")  # before the 2018 draft
        self.assertEqual(profile.height_in.evidence_mode, UNAVAILABLE)

    def test_no_temporal_leakage_roster_after_as_of(self):
        roster = {
            "2013-14": {"999": {"listed_height_in": 74.0, "listed_weight_lbs": 190.0}},
            "2023-24": {"999": {"listed_height_in": 74.0, "listed_weight_lbs": 210.0}},  # future -- must not leak back
        }
        with patch.object(ai, "load_combine_anthro", side_effect=self._combine_side_effect({})), \
             patch.object(ai, "load_roster_physicals", side_effect=self._roster_side_effect(roster)):
            profile = ae.build_physical_profile("999", "2013-14", roster_seasons=("2013-14", "2023-24"))
        self.assertEqual(len(profile.mass_observations), 1)
        self.assertEqual(profile.mass_observations[0].value, 190.0)

    def test_missing_player_returns_unavailable_not_zero(self):
        with patch.object(ai, "load_combine_anthro", side_effect=self._combine_side_effect({})), \
             patch.object(ai, "load_roster_physicals", side_effect=self._roster_side_effect({})):
            profile = ae.build_physical_profile("nonexistent", "2020-21")
        self.assertEqual(profile.height_in.evidence_mode, UNAVAILABLE)
        self.assertEqual(profile.wingspan_in.evidence_mode, UNAVAILABLE)
        self.assertEqual(len(profile.mass_observations), 0)

    def test_wingspan_inferred_only_when_combine_missing_but_height_known(self):
        with patch.object(ai, "load_combine_anthro", side_effect=self._combine_side_effect({})), \
             patch.object(ai, "load_roster_physicals",
                          side_effect=self._roster_side_effect(
                              {"2013-14": {"999": {"listed_height_in": 80.0, "listed_weight_lbs": 220.0}}})):
            profile = ae.build_physical_profile("999", "2013-14")
        self.assertEqual(profile.wingspan_in.evidence_mode, INFERRED_REGRESSION)
        self.assertIsNotNone(profile.wingspan_in.model_version)
        self.assertGreater(profile.wingspan_in.value, 80.0)  # wingspan > height is the real, expected direction

    def test_mass_time_series_preserved_across_multiple_seasons(self):
        combine = {2013: {"999": {"height_wo_shoes_in": 80.0, "wingspan_in": 84.0,
                                   "standing_reach_in": 105.0, "weight_lbs": 215.0}}}
        roster = {
            "2013-14": {"999": {"listed_height_in": 81.0, "listed_weight_lbs": 220.0}},
            "2018-19": {"999": {"listed_height_in": 81.0, "listed_weight_lbs": 225.0}},
        }
        with patch.object(ai, "load_combine_anthro", side_effect=self._combine_side_effect(combine)), \
             patch.object(ai, "load_roster_physicals", side_effect=self._roster_side_effect(roster)):
            profile = ae.build_physical_profile("999", "2018-19", roster_seasons=("2013-14", "2018-19"))
        self.assertEqual(len(profile.mass_observations), 3)  # combine + 2 roster snapshots, never collapsed
        self.assertEqual(profile.latest_mass.value, 225.0)


if __name__ == "__main__":
    unittest.main()
