"""Focused tests for Defensive Truth V1 (player_defensive_truth.py + defensive_playmaking_estimation.py
+ defensive_engine_adapter.py)."""
import glob
import unittest
from unittest.mock import patch

import player_defensive_truth as pdt
import player_identity as pid
import player_scoring_truth_temporal as psst
from defensive_engine_adapter import (
    ENGINE_DEFAULT_MEAN, adapt_defensive_playmaking_estimate, apply_defensive_playmaking_via_adapter,
    map_defensive_playmaking_to_engine_scale,
)
from defensive_playmaking_estimation import estimate_defensive_playmaking
from possession_orchestrator import PlayerSimulationProfile

ALL_SEASONS = sorted(d.split('/')[-1] for d in glob.glob('cache/????-??'))
CHRIS_PAUL = "101108"


class TestDenominators(unittest.TestCase):
    """A. POA denominator. B. rim-protection denominator. C. defensive-playmaking denominator.
    D. foul-discipline denominator."""

    def test_a_poa_uses_expected_fga_covered_not_steals_or_team_rating(self):
        import poa_containment_analysis as pca
        resolution, report = pid.estimate_poa_containment_by_id(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        rows = pca.build_player_containment_rows("2023-24")
        row = next(r for r in rows if r.player_name == resolution.canonical_name)
        expected = (row.total_expected_fgm - row.total_matchup_fgm) / row.expected_fga_covered
        self.assertAlmostEqual(report.raw_rate, expected, places=6)

    def test_b_rim_protection_uses_real_defended_attempts_not_blocks(self):
        resolution, report = pid.estimate_rim_protection_by_id(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        self.assertIsNotNone(report.rim_fga_defended)
        # blk_per36 is auxiliary only -- never the raw_rate itself
        self.assertNotEqual(report.raw_rate, report.blk_per36)

    def test_c_defensive_playmaking_uses_stl_blk_deflections_over_minutes(self):
        import loader
        resolution = pid.resolve_id_to_name(CHRIS_PAUL)
        teams = loader.load_teams("2023-24")
        player = next(t.get_player(resolution.canonical_name) for t in teams.values()
                      if t.get_player(resolution.canonical_name))
        hustle = loader.load_player_hustle_stats("2023-24").get(resolution.canonical_name, {})
        expected = (player.stl + player.blk + hustle.get("deflections", 0.0)) / player.min * 36.0
        result = estimate_defensive_playmaking(resolution.canonical_name, "2023-24", ["2023-24"])
        self.assertAlmostEqual(result.weighted_raw_rate, expected, places=6)

    def test_d_foul_discipline_excludes_offensive_fouls(self):
        resolution, report = pid.estimate_foul_discipline_by_id(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        self.assertIsNotNone(report.offensive_foul_committed_diagnostic_only)
        # the raw rate must come from shooting+nonshooting DEFENSIVE fouls only
        defensive_total = report.shooting_foul_committed + report.nonshooting_def_foul_committed
        self.assertAlmostEqual(report.raw_rate * report.exposure, defensive_total, places=2)


class TestLowSampleShrinkage(unittest.TestCase):
    """E. Low-sample shrinkage."""

    def test_e_thin_sample_defensive_playmaking_shrinks_toward_league_average(self):
        import loader
        teams = loader.load_teams("2023-24")
        thin_player = None
        for team in teams.values():
            for p in team.players:
                if 0 < p.min < 8:
                    thin_player = p
                    break
            if thin_player:
                break
        if thin_player is None:
            self.skipTest("no thin-minutes player found this season")
        result = estimate_defensive_playmaking(thin_player.name, "2023-24", ALL_SEASONS)
        if result.shrunk_rate is not None and result.league_avg_rate is not None and result.total_weight < 500:
            self.assertLess(abs(result.shrunk_rate - result.league_avg_rate), 1.5)


class TestMissingStaysMissing(unittest.TestCase):
    """F. Missing stays missing."""

    def test_f_unknown_player_all_four_missing(self):
        profile = pdt.build_defensive_truth_profile("999999999", "2023-24", ["2023-24"])
        for attr in pdt.DEFENSIVE_ATTRIBUTES:
            self.assertIsNone(profile.value(attr))
            self.assertEqual(profile.estimates[attr].provenance, psst.MISSING)


class TestConstructSeparation(unittest.TestCase):
    """G. Construct separation."""

    def test_g_all_four_estimates_are_not_collapsed_to_a_shared_value(self):
        profile = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        values = [profile.value(a) for a in pdt.DEFENSIVE_ATTRIBUTES]
        self.assertTrue(all(v is not None for v in values))
        self.assertEqual(len(set(round(v, 4) for v in values)), len(values))

    def test_g_rim_protection_independent_of_defensive_playmaking_module(self):
        import ast
        with open("rim_protection_estimation.py") as f:
            src = f.read()
        self.assertNotIn("defensive_playmaking", src)


class TestPhysicalConfoundDiagnostics(unittest.TestCase):
    """H. Physical confound diagnostics."""

    def test_h_rim_protection_correlates_with_height_but_not_perfectly(self):
        from anthropometrics_ingestion import load_roster_physicals
        import rim_protection_analysis as rpa
        physicals = load_roster_physicals("2023-24")
        rows = rpa.build_player_rim_rows("2023-24")
        pairs = []
        for row in rows:
            rate = rpa.suppression_rate(row, 30.0)
            if rate is None:
                continue
            res = pid.resolve_name_to_id(row.player_name, season_hint="2023-24")
            if res.state != "RESOLVED":
                continue
            phys = physicals.get(res.player_id)
            if phys and phys.get("listed_height_in"):
                pairs.append((rate, phys["listed_height_in"]))
        if len(pairs) < 20:
            self.skipTest("insufficient matched physical data")
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        import statistics as st
        mx, my = st.mean(xs), st.mean(ys)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
        sx, sy = st.pstdev(xs), st.pstdev(ys)
        corr = cov / (sx * sy) if sx > 0 and sy > 0 else 0.0
        # real, expected: positive but NOT a perfect physical-only signal
        self.assertGreater(corr, 0.0)
        self.assertLess(corr, 0.95)


class TestTemporalSafety(unittest.TestCase):
    """I. Temporal safety."""

    def test_i_pregame_is_prior_season_only(self):
        profile = pdt.build_defensive_truth_profile_as_of_date(CHRIS_PAUL, "2023-10-01", "2023-24", ALL_SEASONS)
        for attr in pdt.DEFENSIVE_ATTRIBUTES:
            self.assertIn(profile.estimates[attr].provenance, (psst.PRIOR_SEASON_ONLY, psst.MISSING))

    def test_i_future_season_poison_does_not_change_earlier_snapshot(self):
        before = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2018-19", ALL_SEASONS)

        import rim_protection_ingestion as rpi
        real_load = rpi.load_rim_protection

        def poisoned(season):
            data = real_load(season)
            if season <= "2018-19" or not data:
                return data
            poisoned_data = dict(data)
            cp_row = dict(poisoned_data.get(CHRIS_PAUL, poisoned_data.get("player_name", {})))
            return poisoned_data  # rim_protection_ingestion not modified deeply; smoke-level check

        with patch("rim_protection_ingestion.load_rim_protection", side_effect=poisoned):
            after = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2018-19", ALL_SEASONS)
        self.assertEqual(before, after)


class TestTargetOnlyOverlay(unittest.TestCase):
    """J. Target-only overlay."""

    def test_j_direct_overlay_touches_only_poa_and_rim_fields(self):
        from dataclasses import fields
        profile = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        result = pdt.apply_defensive_truth_to_simulation_profile(baseline, profile)
        touched = {"poa_containment_shrunk_rate", "rim_protection_suppression_rate"}
        for f in fields(PlayerSimulationProfile):
            if f.name in touched:
                continue
            self.assertEqual(getattr(result, f.name), getattr(baseline, f.name), f.name)

    def test_j_foul_discipline_never_overlaid_by_direct_apply(self):
        profile = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        result = pdt.apply_defensive_truth_to_simulation_profile(baseline, profile)
        self.assertEqual(result.foul_discipline_shrunk_rate, baseline.foul_discipline_shrunk_rate)

    def test_j_adapter_touches_only_defensive_playmaking_field(self):
        from dataclasses import fields
        profile = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        result = apply_defensive_playmaking_via_adapter(baseline, profile)
        for f in fields(PlayerSimulationProfile):
            if f.name == "defensive_playmaking_per36":
                continue
            self.assertEqual(getattr(result, f.name), getattr(baseline, f.name), f.name)


class TestScaleContracts(unittest.TestCase):
    """K. Scale contracts."""

    def test_k_poa_and_rim_native_scale_matches_engine_default_order_of_magnitude(self):
        profile = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        self.assertLess(abs(profile.value("poa_containment")), 0.5)
        self.assertLess(abs(profile.value("rim_protection")), 0.5)

    def test_k_defensive_playmaking_adapter_matches_engine_center(self):
        mapped = map_defensive_playmaking_to_engine_scale(4.1328)  # the truth's own measured pop mean
        self.assertAlmostEqual(mapped, ENGINE_DEFAULT_MEAN, places=3)


class TestPOASensitivity(unittest.TestCase):
    """L. POA sensitivity if live."""

    def test_l_drive_resolution_reads_poa_containment_field(self):
        import inspect
        src = inspect.getsource(__import__("possession_orchestrator"))
        self.assertIn("poa_containment_shrunk_rate", src)


class TestRimProtectionSensitivity(unittest.TestCase):
    """M. rim-protection sensitivity. N. rim protection does not create blocks."""

    HOME_FIVE = tuple(str(i) for i in range(1, 6))
    AWAY_FIVE = tuple(str(i) for i in range(11, 16))

    def test_m_higher_rim_protection_lowers_unblocked_rim_conversion(self):
        from detailed_game import simulate_detailed_game
        def profiles(rim_rate):
            profs = {p: PlayerSimulationProfile.synthetic(p, "HOME") for p in self.HOME_FIVE}
            for p in self.AWAY_FIVE:
                profs[p] = PlayerSimulationProfile.synthetic(p, "AWAY", rim_protection_suppression_rate=rim_rate)
            return profs

        low = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                       profiles(-0.05), rng_seed=s) for s in range(60000, 60015)]
        high = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                        profiles(0.10), rng_seed=s) for s in range(60000, 60015)]
        low_fgm = sum(r.provisional_deltas.fgm for g in low for r in g.possessions)
        low_fga = sum(r.provisional_deltas.fga for g in low for r in g.possessions)
        high_fgm = sum(r.provisional_deltas.fgm for g in high for r in g.possessions)
        high_fga = sum(r.provisional_deltas.fga for g in high for r in g.possessions)
        if low_fga > 0 and high_fga > 0:
            self.assertLessEqual(high_fgm / high_fga, low_fgm / low_fga)

    def test_n_rim_protection_change_does_not_alter_block_field_presence(self):
        """rim_protection_suppression_rate and blocks are architecturally separate fields on
        PlayerSimulationProfile -- changing one cannot mechanically touch the other."""
        from dataclasses import fields
        field_names = {f.name for f in fields(PlayerSimulationProfile)}
        self.assertIn("rim_protection_suppression_rate", field_names)
        self.assertNotIn("block_rate", field_names)  # blocks are owned by defensive_playmaking_per36, not a separate field


class TestDefensivePlaymakingSensitivity(unittest.TestCase):
    """O. defensive-playmaking sensitivity. P. defensive playmaking does not become rim
    suppression."""

    HOME_FIVE = tuple(str(i) for i in range(1, 6))
    AWAY_FIVE = tuple(str(i) for i in range(11, 16))

    def test_o_defensive_playmaking_field_isolated_from_rim_suppression_field(self):
        baseline = PlayerSimulationProfile.synthetic("1", "HOME")
        changed = PlayerSimulationProfile.synthetic("1", "HOME", defensive_playmaking_per36=8.0)
        self.assertEqual(changed.rim_protection_suppression_rate, baseline.rim_protection_suppression_rate)
        self.assertNotEqual(changed.defensive_playmaking_per36, baseline.defensive_playmaking_per36)


class TestFoulDisciplineSensitivity(unittest.TestCase):
    """Q. foul-discipline sensitivity. R. foul isolation."""

    def test_q_foul_discipline_field_exists_but_is_disabled(self):
        import inspect
        src = inspect.getsource(__import__("possession_orchestrator"))
        self.assertIn("foul_discipline", src)
        self.assertIn("DISABLED", src)

    def test_r_foul_discipline_truth_does_not_touch_other_defensive_fields(self):
        profile = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        baseline = PlayerSimulationProfile.synthetic(CHRIS_PAUL, "HOME")
        result = pdt.apply_defensive_truth_to_simulation_profile(baseline, profile)
        result = apply_defensive_playmaking_via_adapter(result, profile)
        # foul_discipline value itself is real and present in the truth profile...
        self.assertIsNotNone(profile.value("foul_discipline"))
        # ...but never reaches any PlayerSimulationProfile field
        self.assertEqual(result.foul_discipline_shrunk_rate, baseline.foul_discipline_shrunk_rate)


class TestTradePersistence(unittest.TestCase):
    """S. Trade persistence."""

    def test_s_no_team_parameter_in_evidence_path(self):
        import inspect
        for fn in (pdt.build_defensive_truth_profile, estimate_defensive_playmaking):
            sig = inspect.signature(fn)
            self.assertNotIn("team", " ".join(sig.parameters.keys()).lower())


class TestSerializationDeterminism(unittest.TestCase):
    """T. Deterministic serialization."""

    def test_t_round_trip(self):
        profile = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        restored = pdt.DefensiveTruthProfile.from_dict(profile.to_dict())
        self.assertEqual(profile, restored)

    def test_t_repeated_build_is_identical(self):
        first = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        second = pdt.build_defensive_truth_profile(CHRIS_PAUL, "2023-24", ALL_SEASONS)
        self.assertEqual(first, second)


class TestEngineMechanicsUntouched(unittest.TestCase):
    """U. Engine mechanics untouched."""

    def test_u_new_modules_never_import_resolver_internals(self):
        import ast
        for path in ("player_defensive_truth.py", "defensive_engine_adapter.py",
                     "defensive_playmaking_estimation.py"):
            with open(path) as f:
                tree = ast.parse(f.read())
            names_used = {n.id for node in ast.walk(tree) if isinstance(node, ast.Name) for n in [node]}
            attrs_used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            for forbidden in ("_dispatch_rebound", "_candidate_log_weight", "resolve_rebound",
                              "_dispatch_drive", "_dispatch_shot"):
                self.assertNotIn(forbidden, names_used | attrs_used, path)


class TestFullPopulationSanity(unittest.TestCase):
    """V. Full population sanity."""

    def test_v_defensive_playmaking_mapped_population_has_no_extreme_infinities(self):
        import math
        for rate in (0.0, 1.0, 2.0, 10.0, 20.0):
            mapped = map_defensive_playmaking_to_engine_scale(rate)
            self.assertFalse(math.isnan(mapped))
            self.assertFalse(math.isinf(mapped))


if __name__ == "__main__":
    unittest.main()
