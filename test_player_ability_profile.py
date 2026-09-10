"""
Focused unit tests for player_ability_profile.py ONLY -- the new
foundational representation, not the existing simulator. Deliberately
NOT placed in tests/ (that directory belongs to the parallel Codex
workstream on this same checkout). Run with:

    python3 -m unittest test_player_ability_profile -v
"""
import unittest

from player_ability_profile import (
    AttributeEstimate, RoleProfile, PlayerAbilityProfile, UNESTIMATED,
    SKILL_ATTRIBUTES, ABILITY_OUTPUTS, ROLE_ARCHETYPES, SCHEMA_VERSION,
)


class TestAttributeEstimate(unittest.TestCase):
    def test_missing_stays_missing_not_zero(self):
        est = AttributeEstimate()
        self.assertIsNone(est.value)
        self.assertFalse(est.is_estimated)
        self.assertIsNot(est.value, 0.0)  # explicit: None, never a silent 0.0

    def test_valid_value_accepted(self):
        est = AttributeEstimate(value=72.5, confidence=0.6, sample_size=340)
        self.assertTrue(est.is_estimated)
        self.assertEqual(est.value, 72.5)

    def test_value_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            AttributeEstimate(value=150.0)
        with self.assertRaises(ValueError):
            AttributeEstimate(value=-1.0)

    def test_confidence_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            AttributeEstimate(value=50.0, confidence=1.5)

    def test_negative_sample_size_rejected(self):
        with self.assertRaises(ValueError):
            AttributeEstimate(value=50.0, sample_size=-5)

    def test_frozen(self):
        est = AttributeEstimate(value=50.0)
        with self.assertRaises(Exception):
            est.value = 60.0  # dataclasses.FrozenInstanceError

    def test_serialization_round_trip(self):
        est = AttributeEstimate(value=81.0, confidence=0.4, sample_size=120)
        restored = AttributeEstimate.from_dict(est.to_dict())
        self.assertEqual(est, restored)

    def test_unestimated_round_trip(self):
        restored = AttributeEstimate.from_dict(UNESTIMATED.to_dict())
        self.assertEqual(restored, UNESTIMATED)
        self.assertFalse(restored.is_estimated)


class TestRoleProfile(unittest.TestCase):
    def test_valid_scores_accepted(self):
        role = RoleProfile(as_of_season="2024-25", scores={"rim_runner": 0.8, "defensive_anchor": 0.3})
        self.assertEqual(role.scores["rim_runner"], 0.8)

    def test_unknown_archetype_rejected(self):
        with self.assertRaises(ValueError):
            RoleProfile(as_of_season="2024-25", scores={"point_forward": 0.5})

    def test_score_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            RoleProfile(as_of_season="2024-25", scores={"rim_runner": 1.4})

    def test_every_named_archetype_is_valid(self):
        # The exact five archetypes named in the design doc must all be accepted.
        scores = {a: 0.2 for a in ROLE_ARCHETYPES}
        role = RoleProfile(as_of_season="2024-25", scores=scores)
        self.assertEqual(len(role.scores), 5)

    def test_serialization_round_trip(self):
        role = RoleProfile(as_of_season="2024-25", scores={"off_ball_spacer": 0.7})
        restored = RoleProfile.from_dict(role.to_dict())
        self.assertEqual(role, restored)


class TestPlayerAbilityProfile(unittest.TestCase):
    def test_attribute_count_matches_design(self):
        # 7 scoring + 3 playmaking + 6 defense + 2 rebounding = 18
        self.assertEqual(len(SKILL_ATTRIBUTES), 18)

    def test_default_profile_has_no_estimates(self):
        profile = PlayerAbilityProfile(name="Test Player", as_of_season="2024-25")
        for attr in SKILL_ATTRIBUTES:
            est = profile.get_attribute(attr)
            self.assertFalse(est.is_estimated)
        self.assertEqual(profile.estimated_attribute_count, 0)
        self.assertEqual(profile.model_version, SCHEMA_VERSION)

    def test_unknown_attribute_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            PlayerAbilityProfile(
                name="Test Player", as_of_season="2024-25",
                attributes={"handles": AttributeEstimate(value=50.0)},  # not a real attribute name
            )

    def test_get_attribute_rejects_unknown_name(self):
        profile = PlayerAbilityProfile(name="Test Player", as_of_season="2024-25")
        with self.assertRaises(ValueError):
            profile.get_attribute("not_a_real_attribute")

    def test_with_attribute_sets_one_without_mutating_original(self):
        profile = PlayerAbilityProfile(name="Test Player", as_of_season="2024-25")
        updated = profile.with_attribute("three_point", AttributeEstimate(value=68.0, confidence=0.5))
        # original is untouched
        self.assertFalse(profile.get_attribute("three_point").is_estimated)
        # new profile has the estimate
        self.assertEqual(updated.get_attribute("three_point").value, 68.0)
        self.assertEqual(updated.estimated_attribute_count, 1)

    def test_partial_profile_keeps_rest_unestimated(self):
        profile = PlayerAbilityProfile(name="Test Player", as_of_season="2024-25")
        profile = profile.with_attribute("rim_protection", AttributeEstimate(value=90.0))
        self.assertTrue(profile.get_attribute("rim_protection").is_estimated)
        # every OTHER attribute must still be explicitly unestimated, not defaulted to a number
        for attr in SKILL_ATTRIBUTES:
            if attr != "rim_protection":
                self.assertFalse(profile.get_attribute(attr).is_estimated)

    def test_ability_outputs_are_separate_namespace_from_skills(self):
        profile = PlayerAbilityProfile(name="Test Player", as_of_season="2024-25")
        with self.assertRaises(ValueError):
            profile.with_attribute("overall_ability", AttributeEstimate(value=80.0))  # output, not a skill
        with self.assertRaises(ValueError):
            profile.with_ability_output("three_point", AttributeEstimate(value=80.0))  # skill, not an output
        updated = profile.with_ability_output("overall_ability", AttributeEstimate(value=85.0))
        self.assertEqual(updated.get_ability_output("overall_ability").value, 85.0)

    def test_role_kept_separate_from_ability(self):
        role = RoleProfile(as_of_season="2024-25", scores={"primary_offensive_engine": 0.9})
        profile = PlayerAbilityProfile(name="Test Player", as_of_season="2024-25", role=role)
        profile = profile.with_attribute("shot_creation", AttributeEstimate(value=88.0))
        # role and ability attributes live on separate fields and don't interact
        self.assertEqual(profile.role.scores["primary_offensive_engine"], 0.9)
        self.assertEqual(profile.get_attribute("shot_creation").value, 88.0)
        self.assertNotIn("primary_offensive_engine", profile.attributes)

    def test_provenance_fields_persist(self):
        profile = PlayerAbilityProfile(
            name="Test Player", as_of_season="2024-25", player_id="203999",
            source_cutoff="2025-04-13", evidence_strength=0.72,
            uncertainty_note="single-season evidence only, wide interval",
        )
        self.assertEqual(profile.player_id, "203999")
        self.assertEqual(profile.source_cutoff, "2025-04-13")
        self.assertEqual(profile.evidence_strength, 0.72)
        self.assertIn("single-season", profile.uncertainty_note)

    def test_serialization_round_trip_full_profile(self):
        role = RoleProfile(as_of_season="2024-25", scores={"defensive_anchor": 0.6})
        profile = PlayerAbilityProfile(
            name="Test Player", as_of_season="2024-25", player_id="203999",
            source_cutoff="2025-04-13", evidence_strength=0.72,
            uncertainty_note="test note", role=role,
        )
        profile = profile.with_attribute("rim_finishing", AttributeEstimate(value=77.0, confidence=0.5, sample_size=200))
        profile = profile.with_ability_output("overall_ability", AttributeEstimate(value=81.0))

        restored = PlayerAbilityProfile.from_dict(profile.to_dict())

        self.assertEqual(restored.name, profile.name)
        self.assertEqual(restored.player_id, profile.player_id)
        self.assertEqual(restored.get_attribute("rim_finishing"), profile.get_attribute("rim_finishing"))
        self.assertEqual(restored.get_attribute("three_point").is_estimated, False)  # never set -- stays unestimated after round-trip
        self.assertEqual(restored.get_ability_output("overall_ability").value, 81.0)
        self.assertEqual(restored.role.scores, profile.role.scores)

    def test_serialization_round_trip_minimal_profile(self):
        # No attributes, no role, no optional provenance -- must still round-trip cleanly.
        profile = PlayerAbilityProfile(name="Minimal Player", as_of_season="1999-00")
        restored = PlayerAbilityProfile.from_dict(profile.to_dict())
        self.assertEqual(restored.name, "Minimal Player")
        self.assertIsNone(restored.role)
        self.assertEqual(restored.estimated_attribute_count, 0)


if __name__ == "__main__":
    unittest.main()
