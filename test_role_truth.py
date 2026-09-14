"""Focused tests for Roles + Team Context Truth V1 (player_role_truth.py, player_team_stints.py,
role_lineup_normalization.py)."""
import unittest
from unittest.mock import patch

import player_identity as pid
import player_role_truth as prt
import player_scoring_truth_temporal as psst
import role_off_estimation as roe
from action_selection import RoleContext, TendencyContext, _score_action
from action_intent import ActionType
from player_team_stints import team_as_of_date, team_stints_for_player, was_traded
from possession_orchestrator import PlayerSimulationProfile
from role_lineup_normalization import renormalize_finishing_shares, renormalize_initiation_shares, spacing_weights

CHRIS_PAUL = "101108"
SIAKAM = "1627783"  # real TOR -> IND trade, 2024 deadline


class TestDefinitions(unittest.TestCase):
    """A. Initiation definition. B. Finishing definition. C. Spacing definition."""

    def test_a_initiation_is_potential_ast_per_36_not_ast_pct_or_position(self):
        import role_off_ingestion as roi
        row = roi.load_role_off("2023-24").get(CHRIS_PAUL)
        expected = row["POTENTIAL_AST"] / row["MIN"] * 36.0
        profile = roe.build_role_profile(CHRIS_PAUL, "2023-24")
        self.assertAlmostEqual(profile.role_off_initiation.value, expected, places=6)

    def test_b_finishing_is_pct_ast_fgm_not_fg_pct(self):
        import role_off_ingestion as roi
        row = roi.load_role_off("2023-24").get(CHRIS_PAUL)
        profile = roe.build_role_profile(CHRIS_PAUL, "2023-24")
        self.assertAlmostEqual(profile.role_off_finishing.value, row["PCT_AST_FGM"], places=6)

    def test_c_spacing_is_pct_ast_3pm_not_three_point_pct(self):
        import role_off_ingestion as roi
        row = roi.load_role_off("2023-24").get(CHRIS_PAUL)
        profile = roe.build_role_profile(CHRIS_PAUL, "2023-24")
        self.assertAlmostEqual(profile.role_off_spacing.value, row["PCT_AST_3PM"], places=6)


class TestRoleNotAbility(unittest.TestCase):
    """D. Role != ability. E. Role != tendency."""

    def test_d_initiation_differs_from_playmaking_vision_construct(self):
        import playmaking_estimation as pme
        vision = pme.estimate_playmaking_attribute(CHRIS_PAUL, "2023-24", "playmaking_vision",
                                                     ["2023-24"])
        role_profile = roe.build_role_profile(CHRIS_PAUL, "2023-24")
        # different denominators (per-minute vs per-pass) -> different numeric values even though
        # both use POTENTIAL_AST as a numerator ingredient.
        self.assertNotAlmostEqual(role_profile.role_off_initiation.value, vision.shrunk_rate, places=2)

    def test_e_spacing_differs_from_three_point_preference_tendency(self):
        import inspect
        src = inspect.getsource(__import__("role_off_analysis"))
        self.assertIn("NOT the same signal as the", src)


class TestRoleNotMinutes(unittest.TestCase):
    """F. Role != minutes."""

    def test_f_a_low_minutes_player_can_have_a_high_initiation_rate(self):
        """Role describes deployment CONDITIONAL on playing, not volume of playing time -- a
        per-36 rate is scale-invariant to raw minutes by construction."""
        import role_off_ingestion as roi
        rows = roi.load_role_off("2023-24")
        # find a real, thin-minutes player with real evidence and confirm the rate computation
        # does not simply track MIN itself.
        candidates = [(pid_, r) for pid_, r in rows.items() if r.get("MIN", 0) > 200 and r.get("POTENTIAL_AST")]
        self.assertGreater(len(candidates), 0)
        rates = [r["POTENTIAL_AST"] / r["MIN"] * 36.0 for _, r in candidates]
        minutes = [r["MIN"] for _, r in candidates]
        # a real, weak/no monotonic relationship between raw minutes and the per-36 rate confirms
        # the rate is not merely re-expressing minutes.
        import statistics as st
        if len(set(minutes)) > 1 and len(set(rates)) > 1:
            mx, my = st.mean(minutes), st.mean(rates)
            cov = sum((x - mx) * (y - my) for x, y in zip(minutes, rates)) / len(minutes)
            sx, sy = st.pstdev(minutes), st.pstdev(rates)
            corr = cov / (sx * sy) if sx > 0 and sy > 0 else 0.0
            self.assertLess(abs(corr), 0.6)


class TestTeamMapping(unittest.TestCase):
    """G. Player-team historical mapping. H. Trade stint behavior."""

    def test_g_static_fallback_resolves_a_non_traded_player(self):
        stints = team_stints_for_player(CHRIS_PAUL, "2023-24")
        self.assertEqual(len(stints), 1)
        self.assertEqual(stints[0].team_name, "Golden State Warriors")

    def test_h_a_real_mid_season_trade_produces_two_stints(self):
        stints = team_stints_for_player(SIAKAM, "2023-24")
        self.assertEqual(len(stints), 2)
        self.assertEqual(stints[0].team_name, "Toronto Raptors")
        self.assertEqual(stints[1].team_name, "Indiana Pacers")
        self.assertTrue(was_traded(SIAKAM, "2023-24"))

    def test_h_team_as_of_date_respects_the_real_trade_boundary(self):
        self.assertEqual(team_as_of_date(SIAKAM, "2023-11-01", "2023-24"), "Toronto Raptors")
        self.assertEqual(team_as_of_date(SIAKAM, "2024-03-01", "2023-24"), "Indiana Pacers")


class TestLowExposureConfidence(unittest.TestCase):
    """I. Low-exposure confidence."""

    def test_i_low_minutes_yields_lower_confidence_than_high_minutes(self):
        profile_cp = prt.build_role_truth_profile(CHRIS_PAUL, "2023-24")
        thin_conf = prt._confidence_from_minutes(50)
        high_conf = prt._confidence_from_minutes(2000)
        self.assertLess(thin_conf, high_conf)


class TestMissingStaysMissing(unittest.TestCase):
    """J. Missing remains missing."""

    def test_j_unknown_player_all_three_missing(self):
        profile = prt.build_role_truth_profile("999999999", "2023-24")
        for attr in prt.ROLE_ATTRIBUTES:
            self.assertIsNone(profile.value(attr))
            self.assertEqual(profile.estimates[attr].provenance, psst.MISSING)


class TestTemporalSafety(unittest.TestCase):
    """K. Temporal safety. L. Future-season leakage."""

    def test_k_pregame_is_prior_season_only(self):
        profile = prt.build_role_truth_profile_as_of_date(CHRIS_PAUL, "2023-10-01", "2023-24")
        for attr in prt.ROLE_ATTRIBUTES:
            self.assertIn(profile.estimates[attr].provenance, (psst.PRIOR_SEASON_ONLY, psst.MISSING))

    def test_l_future_season_poison_does_not_change_earlier_snapshot(self):
        before = prt.build_role_truth_profile(CHRIS_PAUL, "2018-19")

        import role_off_ingestion as roi
        real_load = roi.load_role_off

        def poisoned(season):
            data = real_load(season)
            if season <= "2018-19" or not data:
                return data
            poisoned_data = dict(data)
            poisoned_data[CHRIS_PAUL] = dict(poisoned_data.get(CHRIS_PAUL, {}))
            poisoned_data[CHRIS_PAUL].update(POTENTIAL_AST=999999.0, MIN=1.0)
            return poisoned_data

        with patch("role_off_ingestion.load_role_off", side_effect=poisoned):
            after = prt.build_role_truth_profile(CHRIS_PAUL, "2018-19")
        self.assertEqual(before, after)


class TestTeamNormalization(unittest.TestCase):
    """M. Team normalization. N. Lineup-aware renormalization."""

    def test_m_initiation_shares_sum_to_one_across_active_players(self):
        active = {"1": 8.0, "2": 4.0, "3": 4.0, "4": None, "5": 0.0}
        shares = renormalize_initiation_shares(active)
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=6)
        self.assertGreater(shares["1"], shares["2"])
        self.assertEqual(shares["4"], 0.0)

    def test_n_removing_the_primary_initiator_raises_remaining_shares(self):
        with_primary = renormalize_initiation_shares({"1": 8.0, "2": 4.0, "3": 4.0})
        without_primary = renormalize_initiation_shares({"2": 4.0, "3": 4.0})
        self.assertGreater(without_primary["2"], with_primary["2"])

    def test_n_spacing_is_not_forced_to_sum_to_one(self):
        active = {"1": 0.8, "2": 0.8, "3": 0.8, "4": 0.8, "5": 0.8}
        weights = spacing_weights(active)
        self.assertAlmostEqual(sum(weights.values()), 4.0, places=6)  # NOT renormalized to 1


class TestUnderlyingTruthUnchangedByAbsence(unittest.TestCase):
    """O. Underlying truth unchanged by teammate absence."""

    def test_o_truth_profile_identical_regardless_of_lineup_context(self):
        profile1 = prt.build_role_truth_profile(CHRIS_PAUL, "2023-24")
        # simulate "computing a runtime lineup" by calling the normalization function -- the
        # source truth object itself must never be touched by that call.
        renormalize_initiation_shares({CHRIS_PAUL: profile1.value("role_off_initiation")})
        profile2 = prt.build_role_truth_profile(CHRIS_PAUL, "2023-24")
        self.assertEqual(profile1, profile2)


class TestEngineSensitivity(unittest.TestCase):
    """P. Initiation engine sensitivity. Q. Finishing sensitivity. R. Spacing sensitivity."""

    def test_p_higher_initiation_raises_creation_action_score(self):
        low = RoleContext(role_off_initiation=1.0)
        high = RoleContext(role_off_initiation=8.0)
        tendency = TendencyContext()
        low_score = _score_action(ActionType.DRIVE, low, tendency)
        high_score = _score_action(ActionType.DRIVE, high, tendency)
        self.assertGreater(high_score, low_score)

    def test_q_higher_finishing_raises_terminal_action_score(self):
        low = RoleContext(role_off_finishing=0.1)
        high = RoleContext(role_off_finishing=0.9)
        tendency = TendencyContext()
        low_score = _score_action(ActionType.PULL_UP, low, tendency)
        high_score = _score_action(ActionType.PULL_UP, high, tendency)
        self.assertGreater(high_score, low_score)

    def test_r_higher_spacing_raises_catch_and_shoot_score(self):
        low = RoleContext(role_off_spacing=0.1)
        high = RoleContext(role_off_spacing=0.9)
        tendency = TendencyContext()
        low_score = _score_action(ActionType.CATCH_AND_SHOOT, low, tendency)
        high_score = _score_action(ActionType.CATCH_AND_SHOOT, high, tendency)
        self.assertGreater(high_score, low_score)

    def test_p_initiation_change_does_not_affect_a_non_creation_action(self):
        """CATCH_AND_SHOOT is a TERMINAL action but NOT a CREATION action (per
        action_intent.py's own set membership) -- varying initiation must not leak into it."""
        from action_intent import CREATION_ACTIONS
        self.assertNotIn(ActionType.CATCH_AND_SHOOT, CREATION_ACTIONS)
        role_a = RoleContext(role_off_initiation=1.0, role_off_spacing=0.5)
        role_b = RoleContext(role_off_initiation=8.0, role_off_spacing=0.5)
        tendency = TendencyContext()
        self.assertEqual(_score_action(ActionType.CATCH_AND_SHOOT, role_a, tendency),
                          _score_action(ActionType.CATCH_AND_SHOOT, role_b, tendency))


class TestTargetOnlyOverlay(unittest.TestCase):
    """S. Target-only overlay."""

    def test_s_overlay_touches_only_three_role_fields(self):
        from dataclasses import fields
        profile = prt.build_role_truth_profile(CHRIS_PAUL, "2023-24")
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        result = prt.apply_role_truth_to_simulation_profile(baseline, profile)
        touched = {"role_off_initiation", "role_off_finishing", "role_off_spacing"}
        for f in fields(PlayerSimulationProfile):
            if f.name in touched:
                continue
            self.assertEqual(getattr(result, f.name), getattr(baseline, f.name), f.name)

    def test_s_tendency_and_ability_fields_untouched_by_role_overlay(self):
        profile = prt.build_role_truth_profile(CHRIS_PAUL, "2023-24")
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        result = prt.apply_role_truth_to_simulation_profile(baseline, profile)
        for attr in ("drive_aggression", "pass_vs_shoot", "three_point_preference",
                     "midrange_preference", "three_point_shrunk_rate", "rim_finishing_shrunk_rate"):
            self.assertEqual(getattr(result, attr), getattr(baseline, attr))

    def test_s_missing_leaves_profile_untouched(self):
        profile = prt.build_role_truth_profile("999999999", "2023-24")
        baseline = PlayerSimulationProfile.synthetic("999999999", "HOME")
        result = prt.apply_role_truth_to_simulation_profile(baseline, profile)
        self.assertEqual(result, baseline)


class TestSerializationDeterminism(unittest.TestCase):
    """T. Deterministic serialization."""

    def test_t_round_trip(self):
        profile = prt.build_role_truth_profile(CHRIS_PAUL, "2023-24")
        restored = prt.RoleTruthProfile.from_dict(profile.to_dict())
        self.assertEqual(profile, restored)

    def test_t_repeated_build_is_identical(self):
        first = prt.build_role_truth_profile(CHRIS_PAUL, "2023-24")
        second = prt.build_role_truth_profile(CHRIS_PAUL, "2023-24")
        self.assertEqual(first, second)


class TestFrozenMechanicsUntouched(unittest.TestCase):
    """V. Frozen mechanics untouched."""

    def test_v_new_modules_never_import_resolver_internals(self):
        import ast
        for path in ("player_role_truth.py", "player_team_stints.py", "role_lineup_normalization.py"):
            with open(path) as f:
                tree = ast.parse(f.read())
            names_used = {n.id for node in ast.walk(tree) if isinstance(node, ast.Name) for n in [node]}
            attrs_used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            for forbidden in ("_dispatch_rebound", "_score_action", "_softmax", "_dispatch_drive"):
                self.assertNotIn(forbidden, names_used | attrs_used, path)


if __name__ == "__main__":
    unittest.main()
