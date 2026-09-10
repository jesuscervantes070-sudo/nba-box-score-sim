"""Focused tests for Phase 14 (player_identity.py). Real static-index
lookups are used directly where cheap/local (no network call --
nba_api's static player list ships locally); ingestion-layer calls are
mocked where a real cache lookup would be needed for full generality."""
import unittest
from unittest.mock import patch

import player_identity as pid


class TestIdentityResolutionInvariants(unittest.TestCase):
    def test_resolved_must_carry_id_and_name(self):
        with self.assertRaises(ValueError):
            pid.IdentityResolution(query="x", state=pid.RESOLVED)

    def test_non_resolved_must_not_carry_id(self):
        with self.assertRaises(ValueError):
            pid.IdentityResolution(query="x", state=pid.UNRESOLVED, player_id="123")

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            pid.IdentityResolution(query="x", state="NOT_A_REAL_STATE")


class TestIdenticalNamesDifferentEntities(unittest.TestCase):
    """Real, confirmed collision: 'Patrick Ewing' = two distinct real
    NBA players (ids 121 and 201607)."""

    def test_duplicate_name_collisions_found_for_real_known_case(self):
        collisions = pid.duplicate_name_collisions()
        self.assertIn("Patrick Ewing", collisions)
        self.assertEqual(set(collisions["Patrick Ewing"]), {"121", "201607"})

    def test_ambiguous_name_never_silently_resolved(self):
        result = pid.resolve_name_to_id("Patrick Ewing")
        self.assertEqual(result.state, pid.AMBIGUOUS)
        self.assertIsNone(result.player_id)
        self.assertEqual(set(result.candidates), {"121", "201607"})

    def test_ambiguous_disambiguated_by_real_season_evidence(self):
        """Gerald Henderson (76993/201945) -- real evidence: only
        201945 has real 2013-14 shot-zone data cached."""
        with patch("shot_zone_ingestion.load_shot_zones", return_value={"201945": {}}), \
             patch("role_off_ingestion.load_role_off", return_value={}):
            result = pid.resolve_name_to_id("Gerald Henderson", season_hint="2013-14")
        self.assertEqual(result.state, pid.RESOLVED)
        self.assertEqual(result.player_id, "201945")

    def test_ambiguous_stays_ambiguous_if_season_evidence_does_not_narrow(self):
        with patch("shot_zone_ingestion.load_shot_zones", return_value={}), \
             patch("role_off_ingestion.load_role_off", return_value={}):
            result = pid.resolve_name_to_id("Patrick Ewing", season_hint="1985-86")
        self.assertEqual(result.state, pid.AMBIGUOUS)


class TestAliasesAndUnresolved(unittest.TestCase):
    def test_unresolved_name_no_fabricated_id(self):
        result = pid.resolve_name_to_id("Totally Fake Player Name Zyx")
        self.assertEqual(result.state, pid.UNRESOLVED)
        self.assertIsNone(result.player_id)

    def test_id_not_in_static_index_is_non_nba_only_not_lookup_failed(self):
        """A combine-only id (never played in the NBA) must be
        distinguished from a genuine lookup failure -- no network call
        was made, so this is never LOOKUP_FAILED."""
        result = pid.resolve_id_to_name("999999999")
        self.assertEqual(result.state, pid.NON_NBA_ONLY)
        self.assertIsNone(result.player_id)


class TestFailedLookupVsUnresolved(unittest.TestCase):
    def test_lookup_failure_is_a_distinct_state_from_unresolved(self):
        """UNRESOLVED means the real index was checked and had no match.
        LOOKUP_FAILED (reserved for a real network/API error path) must
        never be conflated with UNRESOLVED -- verified here by asserting
        they are distinct enum-like string values with distinct meaning,
        not that this module currently exercises a live network failure."""
        self.assertIn(pid.LOOKUP_FAILED, pid.RESOLUTION_STATES)
        self.assertNotEqual(pid.LOOKUP_FAILED, pid.UNRESOLVED)


class TestSameEntityAcrossContexts(unittest.TestCase):
    def test_same_player_id_resolves_identically_regardless_of_season(self):
        r_a = pid.resolve_id_to_name("1630178")
        self.assertEqual(r_a.canonical_name, "Tyrese Maxey")
        # id->name resolution takes no season argument at all -- there is
        # no season-dependent code path here to accidentally vary by season


class TestJoinsAcrossProfiles(unittest.TestCase):
    def test_unified_context_joins_all_four_layers_by_id(self):
        with patch("anthropometrics_estimation.build_physical_profile") as mock_phys, \
             patch("role_off_estimation.build_role_profile") as mock_role:
            mock_phys.return_value.to_dict.return_value = {"player_id": "1630178"}
            mock_role.return_value.to_dict.return_value = {"player_id": "1630178"}
            ctx = pid.get_unified_player_context("1630178", "2023-24", ["2023-24"])
        self.assertEqual(ctx["identity"]["canonical_name"], "Tyrese Maxey")
        self.assertEqual(ctx["physical"]["player_id"], "1630178")
        self.assertEqual(ctx["role"]["player_id"], "1630178")
        mock_phys.assert_called_once_with("1630178", "2023-24")
        mock_role.assert_called_once_with("1630178", "2023-24")

    def test_unresolved_id_skips_ability_tendency_but_still_returns_physical_role(self):
        """Physical/role are already id-keyed and do not depend on
        name resolution at all -- an unresolved ability/tendency name
        join must not block them."""
        with patch("anthropometrics_estimation.build_physical_profile") as mock_phys, \
             patch("role_off_estimation.build_role_profile") as mock_role:
            mock_phys.return_value.to_dict.return_value = {}
            mock_role.return_value.to_dict.return_value = {}
            ctx = pid.get_unified_player_context("999999999", "2023-24", ["2023-24"], attributes=("three_point",))
        self.assertEqual(ctx["identity"]["state"], pid.NON_NBA_ONLY)
        self.assertEqual(ctx["abilities"], {})  # not attempted -- no name to call the old estimator with


class TestTemporalAsOfIsolation(unittest.TestCase):
    def test_unified_context_passes_the_same_as_of_season_to_every_sub_call(self):
        """A shared player_id must never let a future season's physical
        or role observation leak into an earlier as_of_season purely
        because the identity is shared -- verified by asserting each
        sub-call receives EXACTLY the requested as_of_season, not
        'latest available' or any other season."""
        with patch("anthropometrics_estimation.build_physical_profile") as mock_phys, \
             patch("role_off_estimation.build_role_profile") as mock_role:
            mock_phys.return_value.to_dict.return_value = {}
            mock_role.return_value.to_dict.return_value = {}
            pid.get_unified_player_context("1627783", "2018-19", ["2018-19"])  # Siakam, well before his 2024 trade
        mock_phys.assert_called_once_with("1627783", "2018-19")
        mock_role.assert_called_once_with("1627783", "2018-19")


class TestAdapterPreservesEstimatorMath(unittest.TestCase):
    def test_tendency_adapter_calls_unmodified_estimator_with_resolved_name(self):
        with patch("player_tendencies_estimation.estimate_tendency") as mock_est:
            from player_tendencies_estimation import PlayerTendencyEstimate
            mock_est.return_value = PlayerTendencyEstimate(player_name="Tyrese Maxey", season="2023-24", tendency="drive_aggression")
            resolution, result = pid.estimate_tendency_by_id("1630178", "2023-24", ["2023-24"], "drive_aggression")
        mock_est.assert_called_once_with("Tyrese Maxey", "2023-24", ["2023-24"], "drive_aggression")
        self.assertEqual(result.player_id, "1630178")  # additive field filled in, nothing else changed

    def test_ability_adapter_returns_none_without_calling_estimator_when_unresolved(self):
        with patch("player_ability_estimation.estimate_attribute") as mock_est:
            resolution, result = pid.estimate_attribute_by_id("999999999", "2023-24", "three_point", ["2023-24"])
        mock_est.assert_not_called()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
