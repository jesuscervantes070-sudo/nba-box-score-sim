"""Focused correctness tests for competing live-clock expiration."""
import random
import unittest

from action_intent import ActionIntent, ActionType
from clock_semantics import CLOCK_EPSILON_SECONDS, ClockTerminalCause, advance_live_clocks
from pass_resolution import PassResolutionContext, resolve_pass
from possession_engine import PossessionEngine
from possession_orchestrator import (
    PlayerSimulationProfile, PossessionConfig, PossessionTerminalReason,
    PossessionWorld, _charge_time, simulate_possession,
)
from possession_rules import EraRules
from possession_state import BallState, PossessionPhase, SpatialZone


OFF_FIVE = tuple(str(i) for i in range(1, 6))
DEF_FIVE = tuple(str(i) for i in range(11, 16))


def profiles():
    return {pid: PlayerSimulationProfile.synthetic(pid, "A" if pid in OFF_FIVE else "B")
            for pid in OFF_FIVE + DEF_FIVE}


def rules(shot_clock=24.0, period=720.0):
    return EraRules("clock-test", shot_clock, 14.0, 5, period, 4)


def run(config, seed=1, possession_id="clock"):
    return simulate_possession(
        "A", "B", OFF_FIVE, DEF_FIVE, profiles(), "1",
        config=config, rng_seed=seed, possession_id=possession_id,
    )


class TestClockExpirationSemantics(unittest.TestCase):
    def test_inter_action_shorter_than_shot_clock_charges_full_duration(self):
        advance = advance_live_clocks(3.0, 8.0, 20.0)
        self.assertEqual(advance.actual_elapsed_seconds, 3.0)
        self.assertEqual(advance.shot_clock_after, 5.0)
        self.assertEqual(advance.game_clock_after, 17.0)
        self.assertEqual(advance.terminal_cause, ClockTerminalCause.NONE)

    def test_inter_action_longer_than_shot_clock_charges_only_available_time(self):
        advance = advance_live_clocks(3.0, 1.2, 100.0)
        self.assertAlmostEqual(advance.actual_elapsed_seconds, 1.2)
        self.assertAlmostEqual(advance.game_clock_after, 98.8)
        self.assertEqual(advance.truncated_by_shot_clock_seconds, 1.8)

    def test_exact_shot_clock_boundary_is_a_violation(self):
        advance = advance_live_clocks(3.0, 3.0, 10.0)
        self.assertEqual(advance.terminal_cause, ClockTerminalCause.SHOT_CLOCK)
        self.assertEqual(advance.actual_elapsed_seconds, 3.0)
        self.assertEqual(advance.shot_clock_after, 0.0)

    def test_period_clock_expiring_first_truncates_elapsed_time(self):
        advance = advance_live_clocks(3.0, 8.0, 1.5)
        self.assertEqual(advance.terminal_cause, ClockTerminalCause.PERIOD)
        self.assertEqual(advance.actual_elapsed_seconds, 1.5)
        self.assertEqual(advance.shot_clock_after, 6.5)
        self.assertEqual(advance.game_clock_after, 0.0)

    def test_period_clock_expiring_first_does_not_create_turnover(self):
        config = PossessionConfig(era_rules=rules(shot_clock=8.0), initial_game_clock_seconds=1.5)
        result = run(config)
        self.assertEqual(result.reason, PossessionTerminalReason.PERIOD_END)
        self.assertEqual(result.stats.team_turnovers, 0)
        self.assertEqual(result.stats.turnovers, 0)
        self.assertEqual(result.stats.steals, 0)

    def test_shot_clock_expiration_creates_only_team_turnover(self):
        result = run(PossessionConfig(era_rules=rules(shot_clock=1.0)))
        self.assertEqual(result.reason, PossessionTerminalReason.SHOT_CLOCK_VIOLATION)
        self.assertEqual(result.stats.team_turnovers, 1)
        self.assertEqual(result.stats.turnovers, 0)
        self.assertEqual(result.stats.steals, 0)

    def test_simultaneous_boundary_preserves_shot_clock_first_precedence(self):
        config = PossessionConfig(era_rules=rules(shot_clock=1.0), initial_game_clock_seconds=1.0)
        result = run(config)
        self.assertEqual(result.reason, PossessionTerminalReason.SHOT_CLOCK_VIOLATION)
        self.assertEqual(result.engine_state.game_clock_remaining, 0.0)

    def test_loose_ball_timing_crossing_uses_actual_elapsed_time(self):
        engine = PossessionEngine("loose", "A", "B", era_rules=rules(), rng_seed=1)
        engine.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        engine.state = engine.state.with_ball_carrier(None, BallState.LOOSE)
        engine.state.shot_clock_remaining = 0.2
        world = PossessionWorld("A", "B", OFF_FIVE, DEF_FIVE, profiles())
        advance = _charge_time(engine, 0.5, world, "LOOSE_BALL_RECOVERY", 1)
        self.assertEqual(advance.terminal_cause, ClockTerminalCause.SHOT_CLOCK)
        self.assertAlmostEqual(advance.actual_elapsed_seconds, 0.2)
        self.assertAlmostEqual(world.clock_charge_log[-1]["truncated_by_shot_clock_seconds"], 0.3)

    def test_pass_flight_crossing_uses_authoritative_clock_rule(self):
        engine = PossessionEngine("pass", "A", "B", season="2023-24", rng_seed=1)
        engine.inbound("1", SpatialZone.LEFT_WING, PossessionPhase.HALFCOURT)
        engine.state.shot_clock_remaining = 0.2
        engine.state.game_clock_remaining = 100.0
        intent = ActionIntent(ActionType.SWING_PASS, "1", "pass", "2", SpatialZone.LEFT_CORNER.value)
        outcome = resolve_pass(engine, intent, PassResolutionContext(), random.Random(1))
        self.assertEqual(outcome, "SHOT_CLOCK_VIOLATION_ON_ARRIVAL")
        self.assertEqual(engine.state.shot_clock_remaining, 0.0)

    def test_pass_arrival_violation_decrements_game_clock_by_actual_flight(self):
        engine = PossessionEngine("pass", "A", "B", season="2023-24", rng_seed=1)
        engine.inbound("1", SpatialZone.LEFT_WING, PossessionPhase.HALFCOURT)
        engine.state.shot_clock_remaining = 0.2
        engine.state.game_clock_remaining = 100.0
        intent = ActionIntent(ActionType.SWING_PASS, "1", "pass", "2", SpatialZone.LEFT_CORNER.value)
        resolve_pass(engine, intent, PassResolutionContext(), random.Random(1))
        self.assertAlmostEqual(engine.state.game_clock_remaining, 99.8)
        pass_event = engine.log.events[-1]
        self.assertAlmostEqual(pass_event.metadata["actual_elapsed_seconds"], 0.2)

    def test_no_action_dispatches_after_entry_clock_expiration(self):
        result = run(PossessionConfig(era_rules=rules(shot_clock=1.0)))
        self.assertEqual(result.world.action_log, [])
        self.assertEqual(result.world.decision_log, [])

    def test_game_clock_never_becomes_negative(self):
        advance = advance_live_clocks(30.0, 100.0, 0.1)
        self.assertEqual(advance.game_clock_after, 0.0)

    def test_shot_clock_never_becomes_negative(self):
        advance = advance_live_clocks(30.0, 0.1, 100.0)
        self.assertEqual(advance.shot_clock_after, 0.0)

    def test_legal_shot_release_is_not_truncated_by_shot_clock(self):
        advance = advance_live_clocks(1.5, 0.5, 100.0, shot_clock_stops_segment=False)
        self.assertEqual(advance.actual_elapsed_seconds, 1.5)
        self.assertEqual(advance.game_clock_after, 98.5)
        self.assertEqual(advance.shot_clock_after, 0.0)
        self.assertEqual(advance.terminal_cause, ClockTerminalCause.NONE)

    def test_deterministic_replay(self):
        config = PossessionConfig()
        self.assertEqual(run(config, seed=99, possession_id="det"),
                         run(config, seed=99, possession_id="det"))

    def test_diagnostics_record_nominal_actual_and_truncation(self):
        result = run(PossessionConfig(era_rules=rules(shot_clock=1.2)))
        charge = result.world.clock_charge_log[0]
        self.assertEqual(charge["configured_seconds"], 9.0)
        self.assertAlmostEqual(charge["actual_elapsed_seconds"], 1.2)
        self.assertAlmostEqual(charge["truncated_by_shot_clock_seconds"], 7.8)
        self.assertEqual(charge["truncated_by_period_clock_seconds"], 0.0)
        self.assertEqual(charge["terminal_cause"], ClockTerminalCause.SHOT_CLOCK)

    def test_tiny_positive_residue_is_treated_as_expired(self):
        advance = advance_live_clocks(0.4, CLOCK_EPSILON_SECONDS / 10.0, 10.0)
        self.assertEqual(advance.actual_elapsed_seconds, 0.0)
        self.assertEqual(advance.terminal_cause, ClockTerminalCause.SHOT_CLOCK)


if __name__ == "__main__":
    unittest.main()
