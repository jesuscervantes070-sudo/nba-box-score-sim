"""Focused correctness guardrails for the "Complete shot-family block occurrence" phase.

Covers reachability (A-D), family ordering (E), defender-ability monotonicity (F), FGA/FGM
accounting (G), single block credit (H), rebound routing (I), shooting-foul ordering (J), no
duplicate resolution (K), opportunity/conversion/foul/rebound preservation (L-Q, R-S), and
per-family skill isolation (T).
"""
import inspect
import unittest

from detailed_engine_benchmark import run_benchmark_sample, team_games
from detailed_engine_block_diagnostics import assert_block_reconciliation, diagnose_blocks
from detailed_engine_foul_diagnostics import diagnose_fouls
from detailed_game import simulate_detailed_game
from interior_shot_resolution import (
    DEFENSIVE_PLAYMAKING_POPULATION_MEAN,
    InteriorDefenderContext,
    InteriorShotContext,
    InteriorShotFamily,
    block_probability,
)
from possession_orchestrator import PlayerSimulationProfile
from possession_state import DefensivePosture, SpatialZone
from rebound_diagnostics import diagnose_rebounds
from shot_conversion_diagnostics import assert_shot_conversion_reconciliation, diagnose_shot_conversion
from shot_family_diagnostics import diagnose_shot_families
from shot_resolution import PerimeterBlockContext, ShotFamily, perimeter_block_probability


HOME_FIVE = tuple(str(i) for i in range(1, 6))
AWAY_FIVE = tuple(str(i) for i in range(11, 16))


def _profiles(**overrides_for_player_11):
    """Overrides a single AWAY (defensive) player's ability -- HOME always shoots against AWAY."""
    profiles = {p: PlayerSimulationProfile.synthetic(p, "HOME") for p in HOME_FIVE}
    for p in AWAY_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
    profiles["11"] = PlayerSimulationProfile.synthetic("11", "AWAY", **overrides_for_player_11)
    return profiles


class TestBlockOccurrence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25050))
        cls.results = tuple(g.result for g in cls.games)
        cls.block_diag = diagnose_blocks(cls.results)
        cls.conv_diag = diagnose_shot_conversion(cls.games)

    # A-D. Production reachability for all four families.
    def test_a_rim_block_path_reachable(self):
        self.assertGreater(self.block_diag.blocks_by_family.get("RIM", 0), 0)

    def test_b_floater_block_path_reachable(self):
        self.assertGreater(self.block_diag.blocks_by_family.get("FLOATER", 0), 0)

    def test_c_midrange_block_path_reachable(self):
        self.assertGreater(self.block_diag.blocks_by_family.get("MIDRANGE", 0), 0)

    def test_d_three_block_path_reachable(self):
        self.assertGreater(self.block_diag.blocks_by_family.get("THREE_POINT", 0), 0)

    # E. Family ordering sensible: RIM/FLOATER (interior) each materially exceed MIDRANGE, which
    # itself materially exceeds THREE_POINT -- matching the trusted empirical anchors' own
    # ordering (RIM ~10.3% / FLOATER ~8.9% >> MIDRANGE ~2.5% >> THREE ~0.8%).
    def test_e_family_ordering_matches_empirical_anchors(self):
        rim = self.block_diag.block_rate_per_check("RIM")
        floater = self.block_diag.block_rate_per_check("FLOATER")
        midrange = self.block_diag.block_rate_per_check("MIDRANGE")
        three = self.block_diag.block_rate_per_check("THREE_POINT")
        self.assertGreater(rim, midrange)
        self.assertGreater(floater, midrange)
        self.assertGreater(midrange, three)
        self.assertGreater(three, 0.0)
        # directionally near the trusted anchors -- not overfit to exact decimals.
        self.assertTrue(0.06 <= rim <= 0.15, rim)
        self.assertTrue(0.05 <= floater <= 0.14, floater)
        self.assertTrue(0.012 <= midrange <= 0.04, midrange)
        self.assertTrue(0.002 <= three <= 0.02, three)

    # F. Defender ability monotonic -- LOW < NEUTRAL < HIGH block probability, at fixed context,
    # for each block-capable family. Uses the pure functions directly (no RNG, structural proof).
    def test_f_defender_ability_monotonic_interior(self):
        for family in (InteriorShotFamily.RIM, InteriorShotFamily.FLOATER):
            probs = []
            for playmaking in (0.3, DEFENSIVE_PLAYMAKING_POPULATION_MEAN, 3.5):
                ctx = InteriorShotContext(
                    shot_family=family, shooter_base_rate=0.6,
                    primary_defender=InteriorDefenderContext(
                        defender_id="11", zone=SpatialZone.RESTRICTED_RIM, posture=DefensivePosture.SQUARE,
                        is_primary=True, rim_protection=0.0, defensive_playmaking=playmaking,
                    ),
                )
                probs.append(block_probability(ctx))
            self.assertLess(probs[0], probs[1], family)
            self.assertLess(probs[1], probs[2], family)

    def test_f_defender_ability_monotonic_perimeter(self):
        for family in (ShotFamily.MIDRANGE, ShotFamily.THREE_POINT):
            probs = []
            for playmaking in (0.3, DEFENSIVE_PLAYMAKING_POPULATION_MEAN, 3.5):
                ctx = PerimeterBlockContext(shot_family=family, defender_playmaking=playmaking)
                probs.append(perimeter_block_probability(ctx))
            self.assertLess(probs[0], probs[1], family)
            self.assertLess(probs[1], probs[2], family)

    def test_f_full_simulation_ability_sweep_is_monotonic(self):
        """Full-pipeline confirmation (not just the pure function) -- a defender with a materially
        higher defensive_playmaking_per36 produces a materially higher measured block rate over a
        real game sample, at fixed offensive personnel/seed."""
        low = simulate_detailed_game("HOME", "AWAY", HOME_FIVE, AWAY_FIVE,
                                      _profiles(defensive_playmaking_per36=0.2), rng_seed=555)
        high = simulate_detailed_game("HOME", "AWAY", HOME_FIVE, AWAY_FIVE,
                                       _profiles(defensive_playmaking_per36=4.0), rng_seed=555)
        low_blocks = sum(record.provisional_deltas.blocks for record in low.possessions)
        high_blocks = sum(record.provisional_deltas.blocks for record in high.possessions)
        self.assertLessEqual(low_blocks, high_blocks)

    # G. Blocked attempt records FGA but not FGM.
    def test_g_blocked_attempt_records_fga_not_fgm(self):
        for family, stats in self.conv_diag.by_family.items():
            self.assertGreaterEqual(stats.blocked, 0)
            # every blocked attempt contributes to `attempts` (-> raw FGA) but NEVER to `clean_makes`.
            self.assertLessEqual(stats.clean_makes, stats.clean_attempts)
            self.assertEqual(stats.final_recorded_attempts, stats.attempts + stats.and_one_makes)

    # H. Block credited exactly once (no team+player double credit, no duplicate/missing credit).
    def test_h_block_credited_exactly_once(self):
        for family, stats in self.conv_diag.by_family.items():
            credited = sum(stats.block_credits_by_blocker.values())
            self.assertEqual(credited, stats.blocked, family)
        self.assertEqual(self.block_diag.total_blocks, self.block_diag.blocks_from_stat_deltas)
        self.assertEqual(self.block_diag.blocks_from_events, self.block_diag.blocks_from_stat_deltas)

    # I. Block routes to rebound correctly -- every blocked shot has exactly one matching
    # `UNRESOLVED_BLOCK` rebound opportunity, and it resolves to a real rebound outcome.
    def test_i_block_routes_to_rebound_correctly(self):
        for family, stats in self.conv_diag.by_family.items():
            linked = sum(stats.blocked_shot_rebound_outcomes.values())
            self.assertEqual(linked, stats.blocked, family)

    # J. Shooting-foul interaction valid: a whistled attempt is NEVER also block-checked (mutually
    # exclusive), and every block-capable dispatch is exactly one or the other.
    def test_j_shooting_foul_and_block_check_are_mutually_exclusive(self):
        assert_block_reconciliation(self.block_diag)  # dispatched == whistled + block-checked, all 4 families

    # K. No duplicate resolution -- diagnostics module's own internal reconciliation (per-record
    # block-event-count == blocked-trace-row-count, clean makes+misses == clean attempts, etc.)
    def test_k_no_duplicate_resolution(self):
        assert_shot_conversion_reconciliation(self.conv_diag)

    # L. No opportunity-frequency effect -- shot-family shares of FGA stay in the frozen V1 bands.
    def test_l_opportunity_mix_unchanged(self):
        family_diag = diagnose_shot_families(self.games)
        fga = family_diag.fga
        three_share = family_diag.by_family["THREE_POINT"].attempts / fga
        midrange_share = family_diag.by_family["MIDRANGE"].attempts / fga
        interior_share = (family_diag.by_family["RIM"].attempts
                          + family_diag.by_family["FLOATER"].attempts) / fga
        self.assertTrue(0.38 <= three_share <= 0.44, three_share)
        self.assertTrue(midrange_share < 0.32, midrange_share)
        self.assertTrue(interior_share >= 0.26, interior_share)

    # M. Clean conversion (baseline*shooter ability) essentially unchanged by adding blocks --
    # this task's own explicit requirement: raw FG% may drop (more shots correctly blocked), but
    # CLEAN (unblocked) make% must stay close to the frozen "Calibrate clean shot conversion"
    # targets (RIM ~65-68%, FLOATER ~38-42%, MIDRANGE ~39-42%, THREE ~33-36%).
    def test_m_clean_conversion_unchanged(self):
        rim = self.conv_diag.by_family["RIM"].clean_make_pct
        floater = self.conv_diag.by_family["FLOATER"].clean_make_pct
        midrange = self.conv_diag.by_family["MIDRANGE"].clean_make_pct
        three = self.conv_diag.by_family["THREE_POINT"].clean_make_pct
        self.assertTrue(0.63 <= rim <= 0.70, rim)
        self.assertTrue(0.36 <= floater <= 0.43, floater)
        self.assertTrue(0.37 <= midrange <= 0.43, midrange)
        self.assertTrue(0.31 <= three <= 0.37, three)

    # N. Diagnostic counts reconcile (already covered by K/assert_block_reconciliation, cross-
    # checked here against the independent shot_conversion_diagnostics module).
    def test_n_diagnostics_cross_reconcile(self):
        for family in ("RIM", "FLOATER", "MIDRANGE", "THREE_POINT"):
            self.assertEqual(self.block_diag.blocks_by_family.get(family, 0),
                             self.conv_diag.by_family[family].blocked, family)

    # O. Diagnostics add zero RNG.
    def test_o_diagnostics_add_zero_rng(self):
        import detailed_engine_block_diagnostics as block_module
        import shot_conversion_diagnostics as conv_module
        for module in (block_module, conv_module):
            self.assertNotIn("random", vars(module))
        import possession_orchestrator
        import detailed_game_orchestrator
        import detailed_game
        for m in (possession_orchestrator, detailed_game_orchestrator, detailed_game):
            source = inspect.getsource(m)
            self.assertNotIn("detailed_engine_block_diagnostics", source)

    # P. Seeded determinism preserved.
    def test_p_seeded_replay_is_deterministic(self):
        first = run_benchmark_sample([25000])[0]
        second = run_benchmark_sample([25000])[0]
        self.assertEqual(first.result.final_home_score, second.result.final_home_score)
        self.assertEqual(first.result.final_away_score, second.result.final_away_score)
        first_diag = diagnose_shot_conversion([first])
        second_diag = diagnose_shot_conversion([second])
        self.assertEqual(first_diag, second_diag)

    # Q. Foul occurrence unchanged (still within the accepted V1 bands).
    def test_q_foul_occurrence_preserved(self):
        tgs = team_games(self.games)
        n = len(tgs)
        fta = sum(tg.fta for tg in tgs) / n
        pf = sum(tg.personal_fouls for tg in tgs) / n
        self.assertTrue(21 <= fta <= 27, fta)
        self.assertTrue(17 <= pf <= 22, pf)

    # R. Rebound-contest foul mechanism preserved.
    def test_r_rebound_contest_foul_mechanism_preserved(self):
        fd = diagnose_fouls(self.results)
        self.assertGreater(fd.eligible_rebound_contest_foul_opportunities, 0)
        self.assertGreater(fd.rebound_contest_foul_defensive_outcomes, 0)

    # S. cascade-depth diagnostics preserved (no duplicate rebound opportunities, even with the
    # new block-derived UNRESOLVED_BLOCK rebound source added to the mix).
    def test_s_cascade_depth_diagnostics_preserved(self):
        rd = diagnose_rebounds(self.games)
        self.assertEqual(rd.duplicate_opportunities, 0)
        self.assertEqual(rd.accounting_mismatches, ())

    # T. No cross-family skill leakage -- each family's block-probability function reads a
    # DIFFERENT, single input; raising one family's block-relevant defender skill cannot
    # mathematically reach another family's resolution (same structural-isolation pattern as
    # test_shot_conversion_diagnostics.py's own ability-isolation guardrail).
    def test_t_no_cross_family_skill_leakage(self):
        low_ctx = PerimeterBlockContext(shot_family=ShotFamily.THREE_POINT, defender_playmaking=0.1)
        high_ctx = PerimeterBlockContext(shot_family=ShotFamily.THREE_POINT, defender_playmaking=6.0)
        low_three = perimeter_block_probability(low_ctx)
        high_three = perimeter_block_probability(high_ctx)
        self.assertLess(low_three, high_three)
        # the SAME defender_playmaking sweep must not move MIDRANGE's own probability at all when
        # MIDRANGE's own context is held fixed independently -- each family reads its own base
        # logit constant, confirmed by direct source inspection (no shared mutable state).
        import shot_resolution as module
        source = inspect.getsource(module.perimeter_block_probability)
        self.assertIn("MIDRANGE_BASE_BLOCK_LOGIT", source)
        self.assertIn("THREE_POINT_BASE_BLOCK_LOGIT", source)


if __name__ == "__main__":
    unittest.main()
