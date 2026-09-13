"""Focused guardrails for the first top-level TWO-vs-THREE calibration."""
import inspect
import math
import random
import unittest

import rebound_resolution
import shot_resolution
from action_intent import ActionType
from action_opportunity import ObjectiveOpportunity
from action_perception import NO_GATE_PROVENANCE, PerceivedOpportunity
from action_selection import (
    ClockContext, RoleContext, SelectionPolicy, ShotFamilySelectionContext,
    TendencyContext, evaluate_clock_feasibility, shot_zone_probabilities,
)
from detailed_engine_benchmark import run_benchmark_sample
from possession_engine import PossessionEngine
from possession_orchestrator import (
    PlayerSimulationProfile, PossessionConfig, PossessionWorld,
    apply_matchup_assignments, build_structural_context, dispatch_action,
)
from possession_state import PossessionPhase, PossessionState, SpatialZone
from shot_family_diagnostics import diagnose_shot_families, family_for_selected_shot


OPTIONS = (SpatialZone.TOP_OF_KEY, SpatialZone.MIDRANGE)


class TestShotMixCalibration(unittest.TestCase):
    """NOTE ("Model action-specific jump-shot selection" phase): `shot_zone_probabilities` now
    takes an explicit leading `action_type` -- CATCH_AND_SHOOT and PULL_UP no longer share one
    generic weight (see that function's own docstring / `CATCH_AND_SHOOT_THREE_BASELINE_LOG_WEIGHT`
    for the full root-cause history). These tests use `ActionType.PULL_UP` throughout (matching
    this file's original intent -- OPTIONS mirrors the real `_shot_zone_options()` menu a live-
    dribble PULL_UP reaches from a MIDRANGE ball zone) and their expected probabilities are
    recomputed to include `PULLUP_THREE_BASELINE_LOG_WEIGHT` (0.9, re-calibrated in "Calibrate
    action-specific jump-shot zones" -- see that constant's own docstring)."""

    def test_top_level_environment_bias_moves_three_probability(self):
        neutral = shot_zone_probabilities(
            ActionType.PULL_UP, OPTIONS, TendencyContext(), ShotFamilySelectionContext(0.0),
        )[0]
        calibrated = shot_zone_probabilities(
            ActionType.PULL_UP, OPTIONS, TendencyContext(), ShotFamilySelectionContext(-0.2),
        )[0]
        self.assertAlmostEqual(neutral, 1.0 / (1.0 + math.exp(-0.9)))  # PULLUP_THREE_BASELINE_LOG_WEIGHT=0.9
        self.assertLess(calibrated, neutral)

    def test_player_three_point_preference_is_monotonic(self):
        probabilities = [
            shot_zone_probabilities(
                ActionType.PULL_UP, OPTIONS, TendencyContext(three_point_preference=value),
                ShotFamilySelectionContext(-0.2),
            )[0]
            for value in (-1.0, 0.0, 1.0)
        ]
        self.assertLess(probabilities[0], probabilities[1])
        self.assertLess(probabilities[1], probabilities[2])

    def test_ability_does_not_enter_top_level_choice(self):
        """`three_point_shrunk_rate`/`midrange_shrunk_rate` (execution-layer ABILITY) must NEVER
        change shot-FAMILY selection -- unchanged invariant from before this phase."""
        low_ability = PlayerSimulationProfile.synthetic(
            "1", "A", three_point_shrunk_rate=0.10, midrange_shrunk_rate=0.90,
        )
        high_ability = PlayerSimulationProfile.synthetic(
            "1", "A", three_point_shrunk_rate=0.90, midrange_shrunk_rate=0.10,
        )
        family_source = inspect.getsource(shot_zone_probabilities)
        self.assertNotIn("three_point_shrunk_rate", family_source)
        self.assertNotIn("midrange_shrunk_rate", family_source)
        self.assertNotEqual(low_ability.three_point_shrunk_rate, high_ability.three_point_shrunk_rate)

    def test_midrange_preference_now_enters_the_eligible_top_level_choice(self):
        """ACTIVATED this phase: `midrange_preference` (a real shot-selection TENDENCY, not
        ability) now measurably shifts the PULL_UP/CATCH_AND_SHOOT family choice -- reversing the
        OLD invariant this test used to assert (`shot_zone_probabilities` used to hardcode
        "deliberately excluded"; see that function's own docstring for the full history). Still
        never reads any `*_shrunk_rate` field (see `test_ability_does_not_enter_top_level_choice`
        above) -- it is a real, live TENDENCY read, not an ability leak."""
        context = ShotFamilySelectionContext(-0.2)
        low_mid = shot_zone_probabilities(ActionType.PULL_UP, OPTIONS,
                                           TendencyContext(midrange_preference=-2.0), context)
        high_mid = shot_zone_probabilities(ActionType.PULL_UP, OPTIONS,
                                            TendencyContext(midrange_preference=2.0), context)
        self.assertNotEqual(low_mid, high_mid)
        self.assertGreater(low_mid[0], high_mid[0])  # higher midrange_preference -> LOWER P(three)

    def test_neutral_player_uses_environment_baseline(self):
        probability = shot_zone_probabilities(
            ActionType.PULL_UP, OPTIONS, TendencyContext(three_point_preference=0.0),
            ShotFamilySelectionContext(-0.2),
        )[0]
        self.assertAlmostEqual(probability, math.exp(0.7) / (math.exp(0.7) + 1.0))  # -0.2 (era) + 0.9 (PULL_UP prior) = 0.7
        self.assertEqual(PossessionConfig().three_point_family_log_weight, -0.2)

    def test_terminal_family_choice_is_early_mid_late_clock_compatible(self):
        opportunity = ObjectiveOpportunity(
            "p:PULL_UP:1", ActionType.PULL_UP, "1", target_zone=SpatialZone.TOP_OF_KEY,
            shot_zone_options=OPTIONS, source="live_dribble",
        )
        perceived = [PerceivedOpportunity(opportunity, NO_GATE_PROVENANCE)]
        for seconds in (20.0, 8.0, 2.0):
            clock = ClockContext(shot_clock_remaining=seconds)
            self.assertEqual(evaluate_clock_feasibility(perceived, clock).feasible,
                             tuple(perceived))
            intent = SelectionPolicy(random.Random(7)).select(
                perceived, RoleContext(), TendencyContext(), clock, "p",
                ShotFamilySelectionContext(-0.2),
            )
            self.assertIn(intent.target_zone,
                          {SpatialZone.TOP_OF_KEY.value, SpatialZone.MIDRANGE.value})

    def test_rim_and_floater_paths_are_outside_family_bias(self):
        context = ShotFamilySelectionContext(-5.0)
        self.assertEqual(shot_zone_probabilities(ActionType.PULL_UP, (SpatialZone.PAINT,),
                                                  TendencyContext(), context), [1.0])
        self.assertEqual(shot_zone_probabilities(ActionType.PULL_UP, (SpatialZone.RESTRICTED_RIM,),
                                                  TendencyContext(), context), [1.0])
        self.assertEqual(family_for_selected_shot(ActionType.PULL_UP.value, "PAINT", "PAINT"), "FLOATER")
        self.assertEqual(family_for_selected_shot(ActionType.PULL_UP.value, "RESTRICTED_RIM", "RESTRICTED_RIM"), "RIM")

    def test_deterministic_replay(self):
        first = run_benchmark_sample([28123])[0]
        second = run_benchmark_sample([28123])[0]
        self.assertEqual(first.result, second.result)
        self.assertEqual(diagnose_shot_families([first]), diagnose_shot_families([second]))

    def test_make_probability_firewall_is_unchanged(self):
        source = inspect.getsource(shot_resolution.shot_make_probability)
        self.assertNotIn("three_point_preference", source)
        self.assertNotIn("three_point_family_log_weight", source)
        self.assertNotIn("midrange_preference", source)

    def test_rebound_parameters_are_unchanged(self):
        self.assertEqual(rebound_resolution.OFFENSIVE_REBOUND_RATE_REFERENCE, 0.0482)
        self.assertEqual(rebound_resolution.DEFENSIVE_REBOUND_RATE_REFERENCE, 0.1313)
        self.assertEqual(rebound_resolution.OFFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT, -1.5)
        self.assertEqual(rebound_resolution.DEFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT, 0.0)

    def test_timing_parameters_are_unchanged(self):
        config = PossessionConfig()
        self.assertEqual(config.drive_action_seconds, 2.5)
        self.assertEqual(config.pull_up_action_seconds, 1.5)
        self.assertEqual(config.catch_and_shoot_action_seconds, 1.0)
        self.assertEqual(config.loose_ball_action_seconds, 0.5)
        self.assertEqual(config.ordinary_entry_seconds, 9.0)
        self.assertEqual(config.inter_action_seconds, 3.0)


class TestCalibrateActionSpecificJumpShotZones(unittest.TestCase):
    """"Calibrate action-specific jump-shot zones" phase -- focused tests A-R from the task's own
    required list, specific to the PULL_UP recalibration (0.2 -> 0.9) done under the now-corrected
    hierarchical/LOGMEANEXP architecture."""

    PERIMETER_OPTIONS = (SpatialZone.TOP_OF_KEY, SpatialZone.MIDRANGE)

    def test_a_perimeter_catch_and_shoot_menu_respects_geometry(self):
        """A catch-and-shoot menu is built from the REAL current ball zone, never a fabricated
        one -- `_shot_zone_options` returns `(actual_zone, MIDRANGE)` for any real PERIMETER zone,
        not just TOP_OF_KEY (audited directly: LEFT_WING, RIGHT_WING, and the corners all produce
        their OWN zone as the first option, unchanged by this phase)."""
        from action_opportunity import StructuralContext, generate_opportunities
        for zone in (SpatialZone.LEFT_WING, SpatialZone.RIGHT_WING,
                     SpatialZone.LEFT_CORNER, SpatialZone.RIGHT_CORNER, SpatialZone.TOP_OF_KEY):
            state = _held_state_for_zone(zone)
            ctx = StructuralContext(just_caught_pass=True)
            opps = generate_opportunities(state, ctx)
            catch = next(o for o in opps if o.action_type == ActionType.CATCH_AND_SHOOT)
            self.assertEqual(catch.shot_zone_options, (zone, SpatialZone.MIDRANGE))

    def test_b_interior_locations_cannot_become_perimeter_catch_threes(self):
        """A catch at an already-interior zone (RESTRICTED_RIM/PAINT) gets a SINGLE-option menu --
        it can never resolve to a perimeter three regardless of any baseline weight."""
        from action_opportunity import StructuralContext, generate_opportunities
        for zone in (SpatialZone.RESTRICTED_RIM, SpatialZone.PAINT):
            state = _held_state_for_zone(zone)
            ctx = StructuralContext(just_caught_pass=True)
            opps = generate_opportunities(state, ctx)
            catch = next(o for o in opps if o.action_type == ActionType.CATCH_AND_SHOOT)
            self.assertEqual(catch.shot_zone_options, (zone,))

    def test_c_pull_up_retains_valid_midrange_option(self):
        low = shot_zone_probabilities(ActionType.PULL_UP, self.PERIMETER_OPTIONS,
                                       TendencyContext(midrange_preference=2.0), ShotFamilySelectionContext(-0.2))
        self.assertGreater(low[1], 0.0)  # MIDRANGE probability strictly positive

    def test_d_pull_up_retains_valid_three_option(self):
        high = shot_zone_probabilities(ActionType.PULL_UP, self.PERIMETER_OPTIONS,
                                        TendencyContext(three_point_preference=2.0), ShotFamilySelectionContext(-0.2))
        self.assertGreater(high[0], 0.0)  # THREE probability strictly positive

    def test_e_pullup_baseline_recalibrated_from_02_to_09(self):
        """The single change this phase makes -- a direct regression guard on the exact value,
        recalibrated under the now-corrected hierarchical architecture (see this constant's own
        docstring for the full audit: PULL_UP was resolving ~55% MIDRANGE post-hierarchy at the
        old 0.2 value, the engine's single largest lever on overall MIDRANGE share).
        CATCH_AND_SHOOT's own baseline is UNCHANGED -- the two are independently calibrated and
        neither is constrained to be less than the other (see the docstring's own note on why 0.9
        numerically exceeds 0.7: a data-driven aggregate fit, not a claim about per-shot reliability)."""
        from action_selection import PULLUP_THREE_BASELINE_LOG_WEIGHT, CATCH_AND_SHOOT_THREE_BASELINE_LOG_WEIGHT
        self.assertEqual(PULLUP_THREE_BASELINE_LOG_WEIGHT, 0.9)
        self.assertEqual(CATCH_AND_SHOOT_THREE_BASELINE_LOG_WEIGHT, 0.7)  # UNCHANGED this phase

    def test_f_late_clock_bailout_preserved(self):
        from action_selection import LATE_CLOCK_MIDRANGE_LOG_WEIGHT, LATE_CLOCK_SHOT_CLOCK_THRESHOLD_SECONDS
        self.assertEqual(LATE_CLOCK_MIDRANGE_LOG_WEIGHT, 1.2)
        self.assertEqual(LATE_CLOCK_SHOT_CLOCK_THRESHOLD_SECONDS, 6.0)
        early = shot_zone_probabilities(ActionType.PULL_UP, self.PERIMETER_OPTIONS, TendencyContext(),
                                         ShotFamilySelectionContext(-0.2), shot_clock_remaining=20.0)
        late = shot_zone_probabilities(ActionType.PULL_UP, self.PERIMETER_OPTIONS, TendencyContext(),
                                        ShotFamilySelectionContext(-0.2), shot_clock_remaining=3.0)
        self.assertGreater(late[1], early[1])  # late clock -> higher P(MIDRANGE) than early clock

    def test_j_shot_family_selected_before_make_miss_resolution(self):
        """`_select_shot_zone` runs entirely inside `SelectionPolicy.select` -- before
        `possession_orchestrator._dispatch_shot` ever calls a make-probability resolver."""
        import inspect
        import possession_orchestrator as po
        source = inspect.getsource(po._dispatch_shot)
        zone_index = source.index("if zone == SpatialZone.RESTRICTED_RIM")
        make_prob_index = source.index("unblocked_make_probability(")
        self.assertLess(zone_index, make_prob_index)

    def test_l_family_hierarchy_unchanged_by_this_phase(self):
        from action_selection import ActionSelectionMode
        self.assertEqual(PossessionConfig().action_selection_mode, ActionSelectionMode.HIERARCHICAL)

    def test_m_logmeanexp_unchanged_by_this_phase(self):
        from action_selection import FamilyAggregator
        self.assertEqual(PossessionConfig().family_aggregator, FamilyAggregator.LOGMEANEXP)
        self.assertEqual(PossessionConfig().attack_family_log_weight, -0.7)
        self.assertEqual(PossessionConfig().off_ball_creation_family_log_weight, 0.9)

    def test_h_screen_ball_handler_zone_is_not_stale_before_pull_up(self):
        """User-requested verification: an ON_BALL_SCREEN does not itself relocate the ball
        handler (see its own docstring -- it never moves either player into an interior zone), so
        the ball handler's zone AFTER a screen must be the SAME real, live zone as before it --
        never reset to a generic default (e.g. TOP_OF_KEY) -- and the immediately-following PULL_UP
        opportunity must read that SAME live zone, not a stale/cached copy."""
        from action_intent import ActionIntent
        from action_opportunity import generate_opportunities
        off_five = ("1", "2", "3", "4", "5")
        def_five = ("11", "12", "13", "14", "15")
        profiles = {p: PlayerSimulationProfile.synthetic(p, "A") for p in off_five}
        profiles.update({p: PlayerSimulationProfile.synthetic(p, "B") for p in def_five})
        engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=0)
        apply_matchup_assignments(engine, off_five, def_five)
        engine.inbound("1", SpatialZone.LEFT_WING, PossessionPhase.HALFCOURT)
        world = PossessionWorld(team_a_id="A", team_b_id="B", team_a_five=off_five, team_b_five=def_five,
                                 profiles=profiles,
                                 player_zones={pid: SpatialZone.LEFT_WING for pid in off_five + def_five})
        zone_before = engine.state.ball_zone
        screen_intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                                      possession_id="p1", target_player_id="4")
        dispatch_action(engine, world, screen_intent, PossessionConfig(), random.Random(1), 0)
        self.assertEqual(engine.state.ball_zone, zone_before)  # unchanged -- no fabricated relocation
        self.assertTrue(world.screen_active)
        ctx = build_structural_context(engine, world)
        opps = generate_opportunities(engine.state, ctx)
        pull_up = next(o for o in opps if o.action_type == ActionType.PULL_UP)
        self.assertEqual(pull_up.target_zone, zone_before)
        self.assertEqual(pull_up.shot_zone_options, (zone_before, SpatialZone.MIDRANGE))

    def test_q_deterministic_seeded_behavior(self):
        import random
        from action_selection import _select_shot_zone
        args = (ActionType.PULL_UP, SpatialZone.TOP_OF_KEY, self.PERIMETER_OPTIONS,
                TendencyContext(three_point_preference=0.3), ShotFamilySelectionContext(-0.2))
        first = _select_shot_zone(*args, random.Random(11))
        second = _select_shot_zone(*args, random.Random(11))
        self.assertEqual(first, second)


def _held_state_for_zone(zone):
    from possession_state import BallState, DribbleState, PlayerBallControl
    s = PossessionState(possession_id="p1", offense_team_id="A", defense_team_id="B",
                         phase=PossessionPhase.HALFCOURT, ball_state=BallState.HELD,
                         ball_carrier="1", ball_zone=zone, shot_clock_remaining=18.0)
    s.ball_control = PlayerBallControl("1", state=DribbleState.LIVE_DRIBBLE)
    return s


if __name__ == "__main__":
    unittest.main()
