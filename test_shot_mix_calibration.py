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
from possession_orchestrator import PlayerSimulationProfile, PossessionConfig
from possession_state import SpatialZone
from shot_family_diagnostics import diagnose_shot_families, family_for_selected_shot


OPTIONS = (SpatialZone.TOP_OF_KEY, SpatialZone.MIDRANGE)


class TestShotMixCalibration(unittest.TestCase):
    """NOTE ("Model action-specific jump-shot selection" phase): `shot_zone_probabilities` now
    takes an explicit leading `action_type` -- CATCH_AND_SHOOT and PULL_UP no longer share one
    generic weight (see that function's own docstring / `CATCH_AND_SHOOT_THREE_BASELINE_LOG_WEIGHT`
    for the full root-cause history). These tests use `ActionType.PULL_UP` throughout (matching
    this file's original intent -- OPTIONS mirrors the real `_shot_zone_options()` menu a live-
    dribble PULL_UP reaches from a MIDRANGE ball zone) and their expected probabilities are
    recomputed to include `PULLUP_THREE_BASELINE_LOG_WEIGHT` (0.2)."""

    def test_top_level_environment_bias_moves_three_probability(self):
        neutral = shot_zone_probabilities(
            ActionType.PULL_UP, OPTIONS, TendencyContext(), ShotFamilySelectionContext(0.0),
        )[0]
        calibrated = shot_zone_probabilities(
            ActionType.PULL_UP, OPTIONS, TendencyContext(), ShotFamilySelectionContext(-0.2),
        )[0]
        self.assertAlmostEqual(neutral, 1.0 / (1.0 + math.exp(-0.2)))  # PULLUP_THREE_BASELINE_LOG_WEIGHT=0.2
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
        self.assertAlmostEqual(probability, math.exp(0.0) / (math.exp(0.0) + 1.0))  # -0.2 (era) + 0.2 (PULL_UP prior) = 0.0
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


if __name__ == "__main__":
    unittest.main()
