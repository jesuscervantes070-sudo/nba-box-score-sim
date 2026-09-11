"""Guardrails for the narrow first pass-disruption calibration."""
import unittest

import on_ball_pressure_resolution as pressure
import pass_resolution as passing
from detailed_game import DetailedGameConfig, simulate_detailed_game
from possession_orchestrator import PlayerSimulationProfile, PossessionConfig
from possession_state import DefensivePosture, SpatialZone


HOME = tuple(str(i) for i in range(1, 6))
AWAY = tuple(str(i) for i in range(11, 16))


def profiles():
    return {pid: PlayerSimulationProfile.synthetic(pid, "HOME" if pid in HOME else "AWAY")
            for pid in HOME + AWAY}


def neutral_defender(player_id):
    return passing.DefenderCandidate(player_id, SpatialZone.TOP_OF_KEY,
                                     DefensivePosture.SQUARE,
                                     defensive_playmaking=None)


class TestPassDisruptionCalibration(unittest.TestCase):
    def test_default_and_explicit_disruption_base_configuration(self):
        self.assertEqual(passing.BASE_RATE_ANY_DISRUPTION_ATTEMPT, 0.08)
        self.assertIsNone(PossessionConfig().pass_disruption_base_rate)
        self.assertEqual(PossessionConfig(pass_disruption_base_rate=0.12).pass_disruption_base_rate, 0.12)

    def test_analytic_two_neutral_defender_compounding(self):
        defenders = [neutral_defender("11"), neutral_defender("12")]
        actual = passing.analytic_any_disruption_probability(0.08, defenders)
        self.assertAlmostEqual(actual, 1.0 - (1.0 - 0.08) ** 2)
        self.assertAlmostEqual(actual, 0.1536)

    def test_configuration_is_deterministic(self):
        cfg = DetailedGameConfig(possession_config=PossessionConfig(pass_disruption_base_rate=0.08))
        a = simulate_detailed_game("HOME", "AWAY", HOME, AWAY, profiles(), 26123, cfg)
        b = simulate_detailed_game("HOME", "AWAY", HOME, AWAY, profiles(), 26123, cfg)
        self.assertEqual(a, b)

    def test_independent_bad_pass_formula_is_unchanged(self):
        self.assertEqual(passing.BASE_RATE_BAD_PASS_INDEPENDENT_OF_DEFENSE, 0.02)
        self.assertEqual(passing.PASSING_ACCURACY_WEIGHT, 0.15)
        probability = passing._logistic(
            passing._logit(passing.BASE_RATE_BAD_PASS_INDEPENDENT_OF_DEFENSE)
            - passing.PASSING_ACCURACY_WEIGHT * 0.18)
        self.assertAlmostEqual(probability, 0.01947760201320103)

    def test_disruption_outcome_weights_are_unchanged(self):
        self.assertEqual(passing.BASE_RATE_DISRUPTION_IS_CLEAN_INTERCEPTION, 0.35)
        self.assertEqual(passing.BASE_RATE_DISRUPTION_IS_LOOSE_BALL, 0.30)
        self.assertAlmostEqual(1.0 - 0.35 - 0.30, 0.35)

    def test_timing_defaults_are_unchanged(self):
        cfg = PossessionConfig()
        self.assertEqual((cfg.ordinary_entry_seconds, cfg.transition_entry_seconds,
                          cfg.second_chance_reset_seconds, cfg.inter_action_seconds),
                         (9.0, 1.5, 1.0, 3.0))

    def test_handling_formula_constants_are_unchanged(self):
        self.assertEqual(pressure.BASE_DISRUPTION_LOGIT, -1.6)
        self.assertEqual(pressure.BASE_STRIP_GIVEN_DISRUPTED, 0.20)
        self.assertEqual(pressure.BASE_FORCED_PICKUP_GIVEN_DISRUPTED, 0.35)
        self.assertEqual(pressure.DEFENSIVE_PLAYMAKING_STRIP_WEIGHT, 0.35)


if __name__ == "__main__":
    unittest.main()
