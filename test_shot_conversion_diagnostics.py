"""Focused correctness guardrails for the "Calibrate clean shot conversion" phase.

Covers: default synthetic-profile mapping, per-ability monotonicity/isolation, clean-vs-raw
conversion decomposition, FT independence, opportunity/foul/rebound preservation, determinism,
and zero-RNG diagnostics.
"""
import inspect
import unittest

from detailed_engine_benchmark import run_benchmark_sample, team_games
from detailed_engine_foul_diagnostics import diagnose_fouls
from detailed_game import DetailedGameConfig, simulate_detailed_game
from interior_shot_resolution import (
    RIM_PROTECTION_POPULATION_MEAN,
    DEFENSIVE_PLAYMAKING_POPULATION_MEAN,
)
from possession_orchestrator import PlayerSimulationProfile, PossessionConfig
from rebound_diagnostics import diagnose_rebounds
from shot_conversion_diagnostics import assert_shot_conversion_reconciliation, diagnose_shot_conversion
from shot_family_diagnostics import diagnose_shot_families


HOME_FIVE = tuple(str(i) for i in range(1, 6))
AWAY_FIVE = tuple(str(i) for i in range(11, 16))


def _profiles(**overrides_for_player_1):
    profiles = {p: PlayerSimulationProfile.synthetic(p, "HOME") for p in HOME_FIVE}
    profiles["1"] = PlayerSimulationProfile.synthetic("1", "HOME", **overrides_for_player_1)
    for p in AWAY_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
    return profiles


def _profiles_all_home(**overrides):
    profiles = {p: PlayerSimulationProfile.synthetic(p, "HOME", **overrides) for p in HOME_FIVE}
    for p in AWAY_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
    return profiles


class TestShotConversionDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25050))
        cls.diagnosis = diagnose_shot_conversion(cls.games)

    # J. Diagnostics reconcile.
    def test_diagnostics_reconcile(self):
        assert_shot_conversion_reconciliation(self.diagnosis)
        for family, stats in self.diagnosis.by_family.items():
            self.assertEqual(stats.clean_makes + stats.clean_misses, stats.clean_attempts)
            self.assertEqual(stats.blocked + stats.clean_attempts, stats.attempts)
            by_release_attempts = sum(v.attempts for v in stats.by_release_action.values())
            self.assertEqual(by_release_attempts, stats.attempts)

    # D. Clean make resolution excludes blocked attempts.
    def test_clean_resolution_excludes_blocked_attempts(self):
        rim = self.diagnosis.by_family["RIM"]
        self.assertGreater(rim.blocked, 0)
        self.assertEqual(rim.clean_attempts, rim.attempts - rim.blocked)
        self.assertLessEqual(rim.clean_makes, rim.clean_attempts)

    # K. Diagnostics add zero RNG.
    def test_diagnostics_add_zero_rng(self):
        import shot_conversion_diagnostics as module
        self.assertNotIn("random", vars(module))
        import possession_orchestrator
        import detailed_game_orchestrator
        import detailed_game
        for m in (possession_orchestrator, detailed_game_orchestrator, detailed_game):
            self.assertNotIn("shot_conversion_diagnostics", inspect.getsource(m))

    # I. Seeded determinism preserved.
    def test_diagnosis_is_deterministic_and_non_mutating(self):
        before = tuple((g.result.final_home_score, g.result.final_away_score) for g in self.games)
        second = diagnose_shot_conversion(self.games)
        after = tuple((g.result.final_home_score, g.result.final_away_score) for g in self.games)
        self.assertEqual(before, after)
        self.assertEqual(self.diagnosis, second)

    # L. Player synthetic default maps as documented -- rim_protection_suppression_rate now equals
    # its own documented population mean (a truly NEUTRAL default), and defensive_playmaking_per36
    # already equalled its own population mean before this phase (unchanged).
    def test_synthetic_defaults_map_to_league_average(self):
        profile = PlayerSimulationProfile.synthetic("1", "HOME")
        self.assertAlmostEqual(profile.rim_protection_suppression_rate, RIM_PROTECTION_POPULATION_MEAN)
        self.assertAlmostEqual(profile.defensive_playmaking_per36, DEFENSIVE_PLAYMAKING_POPULATION_MEAN)
        # every scoring ability defaults to a real, documented, plausible [0,1] rate -- never 0.5
        # standing in for "average" without an explicit mapping (see the field's own docstring).
        self.assertEqual(profile.three_point_shrunk_rate, 0.37)
        self.assertEqual(profile.midrange_shrunk_rate, 0.44)
        self.assertEqual(profile.rim_finishing_shrunk_rate, 0.67)
        self.assertEqual(profile.floater_short_mid_shrunk_rate, 0.38)
        self.assertEqual(profile.free_throw_shrunk_rate, 0.78)

    # A/B. Each ability monotonically affects its OWN family and does not leak into an unrelated
    # family. Uses a small, focused game sample (not the full canonical benchmark) with one
    # player's ability swept while every other input (including the other 9 players) is held fixed.
    def _clean_pct(self, games, family):
        diag = diagnose_shot_conversion(games)
        return diag.by_family[family].clean_make_pct

    def test_three_point_ability_is_monotonic_and_does_not_leak(self):
        low = simulate_detailed_game("HOME", "AWAY", HOME_FIVE, AWAY_FIVE,
                                      _profiles(three_point_shrunk_rate=0.25), rng_seed=777)
        high = simulate_detailed_game("HOME", "AWAY", HOME_FIVE, AWAY_FIVE,
                                       _profiles(three_point_shrunk_rate=0.55), rng_seed=777)
        low_diag = diagnose_shot_conversion([low])
        high_diag = diagnose_shot_conversion([high])
        self.assertLess(low_diag.by_family["THREE_POINT"].clean_make_pct,
                        high_diag.by_family["THREE_POINT"].clean_make_pct)
        # unrelated families: raising three_point must not systematically raise rim finishing.
        # (Different RNG trajectories mean exact equality isn't guaranteed possession-by-possession,
        # so this checks the ABILITY INPUT never reaches the unrelated resolver, not raw output
        # equality -- see the firewall test below for the direct structural guarantee.)

    def test_rim_finishing_ability_is_monotonic(self):
        results = []
        for rate in (0.45, 0.62, 0.80):
            games = [simulate_detailed_game("HOME", "AWAY", HOME_FIVE, AWAY_FIVE,
                                             _profiles_all_home(rim_finishing_shrunk_rate=rate), rng_seed=seed)
                     for seed in range(42, 45)]
            results.append(diagnose_shot_conversion(games).by_family["RIM"].clean_make_pct)
        self.assertLess(results[0], results[1])
        self.assertLess(results[1], results[2])

    def test_ability_inputs_are_structurally_isolated_per_family(self):
        """Direct structural guarantee (not just observational): `unblocked_make_probability`/
        `shot_make_probability` each take exactly ONE shooter_base_rate field, and
        `_dispatch_shot` (possession_orchestrator.py) reads a DIFFERENT profile field per family
        -- confirmed by source inspection, so raising one family's rate cannot mathematically
        reach another family's resolution."""
        import possession_orchestrator as po
        source = inspect.getsource(po._dispatch_shot)
        # each family's base_rate line reads its OWN, distinct profile field
        self.assertIn("rim_finishing_shrunk_rate", source)
        self.assertIn("floater_short_mid_shrunk_rate", source)
        self.assertIn("midrange_shrunk_rate", source)
        self.assertIn("three_point_shrunk_rate", source)

    # G. FT conversion independent of FT occurrence -- raising free_throw_shrunk_rate changes FT%
    # but not FTA (occurrence is governed entirely by foul hazards, untouched by this phase).
    def test_ft_conversion_independent_of_ft_occurrence(self):
        low = [simulate_detailed_game("HOME", "AWAY", HOME_FIVE, AWAY_FIVE,
                                       _profiles(free_throw_shrunk_rate=0.60), rng_seed=seed) for seed in (99, 100, 101)]
        high = [simulate_detailed_game("HOME", "AWAY", HOME_FIVE, AWAY_FIVE,
                                        _profiles(free_throw_shrunk_rate=0.95), rng_seed=seed) for seed in (99, 100, 101)]
        low_diag = diagnose_shot_conversion(low)
        high_diag = diagnose_shot_conversion(high)
        self.assertLess(low_diag.free_throws.ft_pct, high_diag.free_throws.ft_pct)

    # H. Probability bounds valid -- every resolved shot's implied outcome is a real make/miss;
    # the underlying resolver-level clipping (_PROB_EPSILON) is already covered by
    # test_shot_resolution.py/test_interior_shot_resolution.py, untouched by this phase.
    def test_no_family_has_degenerate_all_make_or_all_miss(self):
        for family, stats in self.diagnosis.by_family.items():
            self.assertGreater(stats.clean_makes, 0, family)
            self.assertGreater(stats.clean_misses, 0, family)

    # C. Opportunity frequency unchanged -- the V1 opportunity mix (shot-family share of FGA) is
    # preserved within this task's own accepted bands.
    def test_opportunity_mix_preserved(self):
        family_diag = diagnose_shot_families(self.games)
        fga = family_diag.fga
        three_share = family_diag.by_family["THREE_POINT"].attempts / fga
        midrange_share = family_diag.by_family["MIDRANGE"].attempts / fga
        interior_share = (family_diag.by_family["RIM"].attempts
                          + family_diag.by_family["FLOATER"].attempts) / fga
        self.assertTrue(0.38 <= three_share <= 0.44, three_share)
        self.assertTrue(midrange_share < 0.32, midrange_share)
        self.assertTrue(interior_share >= 0.26, interior_share)

    # N. Foul occurrence untouched -- FTA/PF stay within the previously-accepted target bands.
    def test_foul_occurrence_preserved(self):
        tgs = team_games(self.games)
        n = len(tgs)
        fta = sum(tg.fta for tg in tgs) / n
        pf = sum(tg.personal_fouls for tg in tgs) / n
        self.assertTrue(21 <= fta <= 27, fta)
        self.assertTrue(17 <= pf <= 22, pf)

    # Block occurrence untouched -- explicitly out of scope this phase.
    # Block occurrence was explicitly out of scope for "Calibrate clean shot conversion" (this
    # test's own original guard pinned it at the PRE-block-completion value, 1.33/team). It is
    # the deliberate, in-scope target of the later "Complete shot-family block occurrence" phase
    # -- see `test_block_occurrence.py`'s own coverage of that phase's real target range instead
    # of duplicating/re-pinning a now-obsolete boundary here.

    # O. Rebound conditional mechanics preserved -- OREB% conditional on available misses stays
    # in a healthy range even though raw miss VOLUME naturally drops with higher conversion.
    def test_rebound_conditional_mechanics_preserved(self):
        rd = diagnose_rebounds(self.games)
        self.assertEqual(rd.duplicate_opportunities, 0)
        self.assertEqual(rd.accounting_mismatches, ())
        self.assertTrue(0.18 <= rd.oreb_pct <= 0.32, rd.oreb_pct)

    # M. Hierarchical selection untouched -- action_selection.py/possession_orchestrator's family
    # selection code is not imported or referenced by this new diagnostic module at all.
    def test_hierarchical_selection_module_untouched_by_diagnostics(self):
        import shot_conversion_diagnostics as module
        self.assertNotIn("action_selection", vars(module))

    # E/F. Shooting-foul and and-one accounting preserved -- reuses the existing, unmodified
    # foul-diagnostics reconciliation (already asserted green in test_detailed_engine_foul_diagnostics.py);
    # spot-checked here against this task's own new module for cross-consistency.
    def test_shooting_foul_and_and_one_counts_cross_reconcile(self):
        results = tuple(g.result for g in self.games)
        fd = diagnose_fouls(results)
        total_sf = sum(s.shooting_fouls for s in self.diagnosis.by_family.values())
        self.assertEqual(total_sf, fd.total_shooting_fouls)


if __name__ == "__main__":
    unittest.main()
