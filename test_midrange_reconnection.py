"""Focused structural guardrails for autonomous midrange reconnection."""
import inspect
import unittest
from dataclasses import fields
from unittest.mock import patch

import action_selection
import possession_orchestrator
import rebound_resolution
from action_intent import ActionType
from detailed_engine_benchmark import run_benchmark_sample
from interior_shot_resolution import InteriorShotFamily
from possession_orchestrator import PlayerSimulationProfile, PossessionConfig
from possession_state import SpatialZone
from shot_family_diagnostics import diagnose_shot_families, family_for_selected_shot
from shot_resolution import ShotFamily


class TestMidrangeReconnection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25010))
        cls.diagnosis = diagnose_shot_families(cls.games)

    def test_01_genuine_midrange_is_autonomously_reachable(self):
        game = self.games[0]
        found = None
        for record in game.result.possessions:
            world = record.terminal_result.world
            shots = {shot["step"]: shot for shot in world.shot_attempt_log}
            for decision in world.decision_log:
                shot = shots.get(decision["step"])
                if shot and shot["shot_family"] == ShotFamily.MIDRANGE:
                    found = decision
                    break
            if found:
                break
        self.assertIsNotNone(found)
        self.assertEqual(found["selected_target_zone"], SpatialZone.MIDRANGE.value)
        self.assertTrue(any(SpatialZone.MIDRANGE.value in row["shot_zone_options"]
                            for row in found["objective_opportunities"]
                            if row["action_type"] in {ActionType.PULL_UP.value,
                                                      ActionType.CATCH_AND_SHOOT.value}))

    def test_02_profile_carries_native_midrange_rate(self):
        self.assertIn("midrange_shrunk_rate", {field.name for field in fields(PlayerSimulationProfile)})
        self.assertEqual(PlayerSimulationProfile.synthetic("1", "A").midrange_shrunk_rate, 0.42)
        custom = PlayerSimulationProfile.synthetic("1", "A", midrange_shrunk_rate=0.317)
        self.assertEqual(custom.midrange_shrunk_rate, 0.317)

    def test_03_autonomous_midrange_uses_existing_perimeter_resolver(self):
        real_apply = possession_orchestrator.apply_shot_resolution_to_engine
        with patch.object(possession_orchestrator, "apply_shot_resolution_to_engine",
                          wraps=real_apply) as resolver:
            run_benchmark_sample([25000])
        contexts = [call.args[2] for call in resolver.call_args_list]
        self.assertTrue(any(context.shot_family == ShotFamily.MIDRANGE for context in contexts))

    def test_04_midrange_counts_as_two_point_attempt(self):
        midrange = self.diagnosis.by_family[ShotFamily.MIDRANGE].attempts
        interior = (self.diagnosis.by_family[InteriorShotFamily.RIM].attempts
                    + self.diagnosis.by_family[InteriorShotFamily.FLOATER].attempts)
        self.assertGreater(midrange, 0)
        self.assertEqual(self.diagnosis.two_pa, midrange + interior)
        self.assertEqual(self.diagnosis.accounting_mismatches, ())

    def test_05_three_point_family_remains_three_point_accounting(self):
        self.assertGreater(self.diagnosis.three_pa, 0)
        self.assertEqual(self.diagnosis.three_pa,
                         self.diagnosis.by_family[ShotFamily.THREE_POINT].attempts)

    def test_06_rim_and_floater_dispatch_is_unchanged(self):
        for action in (ActionType.PULL_UP.value, ActionType.CATCH_AND_SHOOT.value):
            self.assertEqual(family_for_selected_shot(action, SpatialZone.RESTRICTED_RIM.value,
                                                      SpatialZone.RESTRICTED_RIM.value),
                             InteriorShotFamily.RIM)
            self.assertEqual(family_for_selected_shot(action, SpatialZone.PAINT.value,
                                                      SpatialZone.PAINT.value),
                             InteriorShotFamily.FLOATER)

    def test_07_release_mode_is_independent_of_midrange_and_three_family(self):
        matrix = self.diagnosis.fga_by_release_family
        for release in (ActionType.PULL_UP.value, ActionType.CATCH_AND_SHOOT.value):
            self.assertGreater(matrix[release][ShotFamily.MIDRANGE], 0)
            self.assertGreater(matrix[release][ShotFamily.THREE_POINT], 0)

    def test_08_ability_does_not_enter_selection_or_tendency(self):
        selection_source = inspect.getsource(action_selection)
        self.assertNotIn("midrange_shrunk_rate", selection_source)
        profile = PlayerSimulationProfile.synthetic(
            "1", "A", midrange_shrunk_rate=0.11, midrange_preference=0.73,
        )
        self.assertEqual(profile.midrange_shrunk_rate, 0.11)
        self.assertEqual(profile.midrange_preference, 0.73)

    def test_09_missing_midrange_ability_fails_explicitly(self):
        profiles = {
            str(pid): PlayerSimulationProfile.synthetic(
                str(pid), "HOME" if pid < 6 else "AWAY", midrange_shrunk_rate=None,
            )
            for pid in list(range(1, 6)) + list(range(11, 16))
        }
        from detailed_game import simulate_detailed_game
        with self.assertRaisesRegex(ValueError, "midrange_shrunk_rate"):
            simulate_detailed_game(
                "HOME", "AWAY", tuple(str(i) for i in range(1, 6)),
                tuple(str(i) for i in range(11, 16)), profiles, rng_seed=25000,
            )

    def test_10_deterministic_replay(self):
        first = run_benchmark_sample([26123])[0]
        second = run_benchmark_sample([26123])[0]
        self.assertEqual(first.result, second.result)
        self.assertEqual(diagnose_shot_families([first]), diagnose_shot_families([second]))

    def test_11_timing_constants_unchanged(self):
        config = PossessionConfig()
        self.assertEqual(config.drive_action_seconds, 2.5)
        self.assertEqual(config.pull_up_action_seconds, 1.5)
        self.assertEqual(config.catch_and_shoot_action_seconds, 1.0)
        self.assertEqual(config.loose_ball_action_seconds, 0.5)
        self.assertEqual(config.inter_action_seconds, 3.0)

    def test_12_rebound_parameters_unchanged(self):
        self.assertEqual(rebound_resolution.OFFENSIVE_REBOUND_RATE_REFERENCE, 0.0482)
        self.assertEqual(rebound_resolution.DEFENSIVE_REBOUND_RATE_REFERENCE, 0.1313)
        self.assertEqual(rebound_resolution.OFFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT, -1.5)
        self.assertEqual(rebound_resolution.DEFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT, 0.0)
        self.assertEqual(rebound_resolution._BOXOUT_LEVERAGE_BONUS, 0.6)
        self.assertEqual(rebound_resolution._BOXED_OUT_LEVERAGE_PENALTY, -0.6)


if __name__ == "__main__":
    unittest.main()
