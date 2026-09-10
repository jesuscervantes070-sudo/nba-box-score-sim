"""Focused tests for Phase 13 (role_off_ingestion.py, role_off_analysis.py,
role_off_profile.py, role_off_estimation.py). Real network calls faked."""
import unittest
from unittest.mock import patch

import role_off_analysis as roa
import role_off_estimation as roe
import role_off_ingestion as roi
from role_off_profile import MEASURED_TRACKING, UNAVAILABLE, PlayerRoleProfile, RoleObservation


def _row(min_=1000.0, potential_ast=200.0, pct_ast_fgm=0.5, pct_ast_3pm=0.6, drive_ast_pct=0.1):
    return {"MIN": min_, "POTENTIAL_AST": potential_ast, "PCT_AST_FGM": pct_ast_fgm,
            "PCT_AST_3PM": pct_ast_3pm, "DRIVE_AST_PCT": drive_ast_pct}


class TestCandidateMath(unittest.TestCase):
    def test_role_off_initiation(self):
        row = _row(min_=720.0, potential_ast=200.0)
        self.assertAlmostEqual(roa.role_off_initiation(row), 200.0 / 720.0 * 36.0)

    def test_role_off_initiation_low_exposure_none(self):
        row = _row(min_=50.0)
        self.assertIsNone(roa.role_off_initiation(row))

    def test_role_off_finishing_is_raw_share_not_renamed_usage(self):
        row = _row(pct_ast_fgm=0.42)
        self.assertEqual(roa.role_off_finishing(row), 0.42)

    def test_role_off_spacing(self):
        row = _row(pct_ast_3pm=0.7)
        self.assertEqual(roa.role_off_spacing(row), 0.7)

    def test_missing_field_returns_none_not_zero(self):
        row = {"MIN": 1000.0}  # no POTENTIAL_AST at all
        self.assertIsNone(roa.role_off_initiation(row))


class TestPersistenceLeakage(unittest.TestCase):
    """Year-to-year persistence must compare each player to THEMSELVES a
    year later (no cross-player leakage), and heldout_prediction must
    never use the heldout season's own value as a predictor."""

    def test_year_to_year_uses_matching_player_id_only(self):
        train = {"1": _row(potential_ast=100.0), "2": _row(potential_ast=500.0)}
        heldout = {"1": _row(potential_ast=110.0), "9": _row(potential_ast=999.0)}  # "9" has no train-season counterpart
        with patch.object(roi, "load_role_off", side_effect=lambda s: train if s == "2022-23" else heldout):
            result = roa.year_to_year_persistence("role_off_initiation", "2022-23", "2023-24")
        self.assertEqual(result["n"], 1)  # only player "1" appears in both -- "2" and "9" correctly excluded

    def test_heldout_prediction_baseline_uses_train_only_mean(self):
        train = {"1": _row(potential_ast=100.0), "2": _row(potential_ast=300.0)}
        heldout = {"1": _row(potential_ast=999.0), "2": _row(potential_ast=999.0)}  # heldout values must not leak into the baseline
        with patch.object(roi, "load_role_off", side_effect=lambda s: train if s == "2022-23" else heldout):
            result = roa.heldout_prediction("role_off_initiation", "2022-23", "2023-24")
        train_mean_rate = ((100.0 / 1000.0 * 36.0) + (300.0 / 1000.0 * 36.0)) / 2
        # baseline MAE is computed against the TRAIN mean, not anything derived from the (leaked) heldout values
        expected_heldout_val = 999.0 / 1000.0 * 36.0
        expected_baseline_mae = abs(train_mean_rate - expected_heldout_val)
        self.assertAlmostEqual(result["league_mean_baseline_mae"], expected_baseline_mae)


class TestRoleObservation(unittest.TestCase):
    def test_unavailable_must_not_carry_value(self):
        with self.assertRaises(ValueError):
            RoleObservation(value=1.0, evidence_mode=UNAVAILABLE, source="x", as_of_season="2020-21")

    def test_measured_must_carry_value(self):
        with self.assertRaises(ValueError):
            RoleObservation(value=None, evidence_mode=MEASURED_TRACKING, source="x", as_of_season="2020-21")

    def test_round_trip_serialization(self):
        obs = RoleObservation(value=5.0, evidence_mode=MEASURED_TRACKING, source="x", as_of_season="2020-21", sample_size=1000)
        self.assertEqual(RoleObservation.from_dict(obs.to_dict()), obs)


class TestPlayerRoleProfile(unittest.TestCase):
    def test_no_compositional_constraint(self):
        """Nothing in PlayerRoleProfile enforces the four dimensions to
        sum to 1 or to any fixed relation -- all can be high, or all low."""
        profile = PlayerRoleProfile(
            player_id="1", as_of_season="2023-24",
            role_off_initiation=RoleObservation(value=20.0, evidence_mode=MEASURED_TRACKING, source="x", as_of_season="2023-24"),
            role_off_finishing=RoleObservation(value=0.9, evidence_mode=MEASURED_TRACKING, source="x", as_of_season="2023-24"),
            role_off_spacing=RoleObservation(value=0.9, evidence_mode=MEASURED_TRACKING, source="x", as_of_season="2023-24"),
        )
        # no exception raised, no normalization applied -- values kept exactly as given
        self.assertEqual(profile.role_off_finishing.value, 0.9)
        self.assertEqual(profile.role_off_spacing.value, 0.9)

    def test_missing_defaults_to_unavailable_not_zero(self):
        profile = PlayerRoleProfile(player_id="1", as_of_season="2023-24")
        self.assertEqual(profile.role_off_initiation.evidence_mode, UNAVAILABLE)
        self.assertIsNone(profile.role_off_initiation.value)

    def test_round_trip_serialization(self):
        profile = PlayerRoleProfile(
            player_id="1", as_of_season="2023-24",
            role_off_initiation=RoleObservation(value=12.0, evidence_mode=MEASURED_TRACKING, source="x", as_of_season="2023-24"),
        )
        restored = PlayerRoleProfile.from_dict(profile.to_dict())
        self.assertEqual(restored.role_off_initiation.value, 12.0)
        self.assertEqual(restored.player_id, "1")


class TestBuildRoleProfile(unittest.TestCase):
    def test_stable_player_id_used_as_key(self):
        fake_row = _row()
        with patch.object(roi, "load_role_off", return_value={"999": fake_row}), \
             patch.object(roe, "_defensive_axis_for_season", return_value={}):
            profile = roe.build_role_profile("999", "2023-24")
        self.assertEqual(profile.role_off_initiation.evidence_mode, MEASURED_TRACKING)

    def test_pre_tracking_floor_returns_unavailable_not_zero(self):
        profile = roe.build_role_profile("999", "2010-11")
        self.assertEqual(profile.role_off_initiation.evidence_mode, UNAVAILABLE)
        self.assertEqual(profile.role_def_perimeter_interior.evidence_mode, UNAVAILABLE)

    def test_missing_player_returns_unavailable_not_zero(self):
        with patch.object(roi, "load_role_off", return_value={}), \
             patch.object(roe, "_defensive_axis_for_season", return_value={}):
            profile = roe.build_role_profile("nonexistent", "2023-24")
        self.assertEqual(profile.role_off_initiation.evidence_mode, UNAVAILABLE)
        self.assertEqual(profile.role_off_finishing.evidence_mode, UNAVAILABLE)

    def test_defensive_axis_uses_its_own_real_source_separate_from_offense(self):
        """role_def_perimeter_interior must come from the matchup/shot-zone
        join, not be silently derived from the offensive tracking row."""
        fake_off_row = _row()
        fake_def_row = {"player_name": "Test", "matchup_minutes": 500.0,
                         "role_def_perimeter_minus_interior": -0.3}
        with patch.object(roi, "load_role_off", return_value={"999": fake_off_row}), \
             patch.object(roe, "_defensive_axis_for_season", return_value={"999": fake_def_row}):
            profile = roe.build_role_profile("999", "2023-24")
        self.assertEqual(profile.role_def_perimeter_interior.value, -0.3)
        self.assertEqual(profile.role_def_perimeter_interior.source, "leagueseasonmatchups+shot_zone_join")

    def test_role_ability_interface_separation(self):
        """Building a role profile must not import or touch
        player_ability_profile / player_ability_estimation."""
        import sys
        self.assertNotIn("player_ability_estimation", getattr(roe, "__dict__", {}))
        # role_off_estimation's own module globals must not reference the ability layer directly
        self.assertFalse(any("ability" in name for name in dir(roe)))

    def test_role_tendency_interface_separation(self):
        self.assertFalse(any("tendency" in name for name in dir(roe)))


class TestDefensiveDeploymentAxis(unittest.TestCase):
    def test_matchup_minutes_parsing(self):
        self.assertAlmostEqual(roa._parse_matchup_minutes("43:45"), 43.75)
        self.assertAlmostEqual(roa._parse_matchup_minutes("0:30"), 0.5)


if __name__ == "__main__":
    unittest.main()
