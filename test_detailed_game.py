"""Phase 23C tests for the minimal complete detailed-game wrapper."""
import inspect
import unittest
from dataclasses import replace
from unittest.mock import patch

import detailed_game as complete
from detailed_game_orchestrator import (
    MultiPossessionResult,
    PossessionRecord,
    RestartContext,
    RestartType,
    SegmentStopReason,
)
from floor_foul_administration import FoulAdministrationState
from possession_engine import PossessionEngine
from possession_events import Event, EventType
from possession_orchestrator import (
    PlayerSimulationProfile,
    PossessionConfig,
    PossessionSimulationFault,
    PossessionTerminalReason,
    PossessionTerminalResult,
    PossessionWorld,
    StatDeltas,
)
from possession_rules import EraRules
from possession_state import PossessionPhase, SpatialZone
from transition_state import PossessionChangeSource


HOME = tuple(str(i) for i in range(1, 6))
AWAY = tuple(str(i) for i in range(11, 16))


def profiles():
    return {
        **{pid: PlayerSimulationProfile.synthetic(pid, "HOME") for pid in HOME},
        **{pid: PlayerSimulationProfile.synthetic(pid, "AWAY") for pid in AWAY},
    }


def config(**overrides):
    values = dict(regulation_period_seconds=12.0, overtime_period_seconds=5.0,
                  max_overtimes=3, max_possessions_per_period=20,
                  max_possessions_per_game=200)
    values.update(overrides)
    return complete.DetailedGameConfig(**values)


def one_possession_period(state, home_five, away_five, player_profiles,
                          points=0, foul_state=None, stop=SegmentStopReason.PERIOD_COMPLETE,
                          end_clock=0.0):
    """A typed deterministic Phase 23B period packet for lifecycle tests."""
    possession_id = f"period{state.period}-possession{state.next_possession_sequence}"
    offense = state.current_offense_team_id
    defense = state.current_defense_team_id
    offense_five = home_five if offense == state.home_team_id else away_five
    defense_five = away_five if offense == state.home_team_id else home_five
    engine = PossessionEngine(possession_id, offense, defense, season="2023-24", rng_seed=1)
    engine.state = replace(engine.state, game_clock_remaining=end_clock,
                           phase=PossessionPhase.DEAD_BALL)
    event = Event(EventType.POSSESSION_START, possession_id, 0.0,
                  primary_player_id=offense_five[0], zone=SpatialZone.TOP_OF_KEY.value)
    deltas = StatDeltas(points=points)
    world = PossessionWorld(offense, defense, offense_five, defense_five,
                            player_profiles, foul_state=foul_state or state.foul_state, stats=deltas)
    terminal = PossessionTerminalResult(
        reason=PossessionTerminalReason.PERIOD_END if end_clock == 0.0 else PossessionTerminalReason.MADE_FG,
        resulting_offense_team_id=offense,
        resulting_defense_team_id=defense,
        stats=deltas,
        steps_taken=1,
        engine_state=engine.state,
        events=(event,),
        world=world,
    )
    score_home = state.score_home + (points if offense == state.home_team_id else 0)
    score_away = state.score_away + (points if offense == state.away_team_id else 0)
    final = replace(
        state,
        game_clock_seconds=end_clock,
        score_home=score_home,
        score_away=score_away,
        foul_state=foul_state or state.foul_state,
        next_possession_sequence=state.next_possession_sequence + 1,
        last_terminal_reason=terminal.reason,
    )
    restart = RestartContext(RestartType.DEAD_BALL_INBOUND,
                             PossessionChangeSource.PERIOD_START, None)
    record = PossessionRecord(
        possession_id=possession_id,
        offense_team_id=offense,
        defense_team_id=defense,
        start_game_clock=state.game_clock_seconds,
        end_game_clock=end_clock,
        start_score_home=state.score_home,
        start_score_away=state.score_away,
        end_score_home=score_home,
        end_score_away=score_away,
        restart_context=restart,
        terminal_result=terminal,
        events=(event,),
        provisional_deltas=deltas,
    )
    return MultiPossessionResult(final, (record,), stop, None)


def period_script(points_by_period, foul_states=None, captures=None):
    points = list(points_by_period)
    foul_states = list(foul_states or [None] * len(points))

    def run(**kwargs):
        state = kwargs["initial_state"]
        if captures is not None:
            captures.append(state)
        return one_possession_period(
            state, kwargs["home_five"], kwargs["away_five"], kwargs["profiles"],
            points=points.pop(0), foul_state=foul_states.pop(0),
        )
    return run


class TestInitializationAndPeriodPolicy(unittest.TestCase):
    def test_game_begins_period_one_at_regulation_clock(self):
        state = complete.initialize_detailed_game("HOME", "AWAY", config())
        self.assertEqual((state.period, state.game_clock_seconds), (1, 12.0))
        self.assertEqual((state.current_offense_team_id, state.current_defense_team_id), ("HOME", "AWAY"))

    def test_explicit_opening_offense(self):
        cfg = config(opening_offense_team_id="AWAY")
        state = complete.initialize_detailed_game("HOME", "AWAY", cfg)
        self.assertEqual(state.current_offense_team_id, "AWAY")

    def test_period_openers_alternate_without_previous_terminal_dependency(self):
        self.assertEqual([complete.period_opening_offense("H", "A", "H", p) for p in range(1, 7)],
                         ["H", "A", "H", "A", "H", "A"])

    def test_period_cannot_advance_while_clock_live(self):
        state = complete.initialize_detailed_game("HOME", "AWAY", config())
        with self.assertRaises(ValueError):
            complete.advance_to_next_period(state, config())

    def test_period_advancement_resets_clock_and_team_fouls(self):
        personal = FoulAdministrationState().personal_fouls.increment(HOME[0])
        fouls = FoulAdministrationState(personal_fouls=personal, team_fouls={"HOME": 4})
        state = replace(complete.initialize_detailed_game("HOME", "AWAY", config()),
                        game_clock_seconds=0.0, foul_state=fouls)
        q2 = complete.advance_to_next_period(state, config())
        self.assertEqual((q2.period, q2.game_clock_seconds), (2, 12.0))
        self.assertEqual(q2.foul_state.team_fouls, {})
        self.assertEqual(q2.foul_state.personal_fouls.counts.get(HOME[0]), 1)

    def test_overtime_clock_is_300_seconds_by_default(self):
        cfg = complete.DetailedGameConfig()
        q4 = replace(complete.initialize_detailed_game("HOME", "AWAY", cfg), period=4,
                     game_clock_seconds=0.0)
        ot = complete.advance_to_next_period(q4, cfg)
        self.assertEqual((ot.period, ot.game_clock_seconds), (5, 300.0))

    def test_overtime_bonus_threshold_is_reused_from_phase21b(self):
        rules = EraRules("test", 24.0, 14.0, 5, 720.0, 4,
                         overtime_bonus_foul_threshold=4)
        state = replace(complete.initialize_detailed_game("HOME", "AWAY", config()),
                        foul_state=FoulAdministrationState(team_fouls={"AWAY": 4}))
        self.assertFalse(state.in_bonus("AWAY", rules, is_overtime=False))
        self.assertTrue(state.in_bonus("AWAY", rules, is_overtime=True))


class TestCompleteGameLifecycle(unittest.TestCase):
    def test_complete_four_period_regulation_game(self):
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([2, 0, 0, 0])):
            result = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                     profiles(), 1, config())
        self.assertEqual(len(result.periods), 4)
        self.assertEqual(result.overtime_periods, 0)
        self.assertEqual(result.termination_reason, complete.DetailedGameTerminationReason.REGULATION_FINAL)
        self.assertNotEqual(result.final_home_score, result.final_away_score)

    def test_all_regulation_boundaries_and_halftime_are_automatic(self):
        captured = []
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([2, 0, 0, 0], captures=captured)):
            complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY, profiles(), 1, config())
        self.assertEqual([s.period for s in captured], [1, 2, 3, 4])
        self.assertEqual([s.game_clock_seconds for s in captured], [12.0] * 4)
        self.assertEqual([s.current_offense_team_id for s in captured], ["HOME", "AWAY", "HOME", "AWAY"])

    def test_score_persists_and_stays_with_correct_team(self):
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([3, 2, 3, 2])):
            result = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                     profiles(), 1, config())
        self.assertEqual((result.final_home_score, result.final_away_score), (6, 4))
        self.assertTrue(all(p.end_score_home >= p.start_score_home and
                            p.end_score_away >= p.start_score_away for p in result.periods))

    def test_possession_sequence_is_global_across_periods(self):
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([2, 0, 0, 0])):
            result = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                     profiles(), 1, config())
        self.assertEqual([r.possession_id for r in result.possessions],
                         [f"period{p}-possession{p}" for p in range(1, 5)])
        self.assertEqual(result.final_state.next_possession_sequence, 5)

    def test_regulation_tie_enters_overtime(self):
        captured = []
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([0, 0, 0, 0, 2], captures=captured)):
            result = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                     profiles(), 1, config())
        self.assertEqual(result.overtime_periods, 1)
        self.assertEqual(captured[4].game_clock_seconds, 5.0)
        self.assertEqual(result.termination_reason, complete.DetailedGameTerminationReason.OVERTIME_FINAL)

    def test_only_overtime_periods_pass_overtime_context_to_phase23a(self):
        flags = []
        points = [0, 0, 0, 0, 2]

        def run(**kwargs):
            flags.append(kwargs["possession_config"].is_overtime)
            return one_possession_period(
                kwargs["initial_state"], kwargs["home_five"], kwargs["away_five"], kwargs["profiles"],
                points=points.pop(0))

        with patch.object(complete, "simulate_possessions", side_effect=run):
            complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                            profiles(), 1, config())
        self.assertEqual(flags, [False, False, False, False, True])

    def test_tied_first_overtime_enters_second_overtime(self):
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([0, 0, 0, 0, 0, 2])):
            result = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                     profiles(), 1, config())
        self.assertEqual(result.overtime_periods, 2)
        self.assertEqual([p.period for p in result.periods], [1, 2, 3, 4, 5, 6])

    def test_max_overtime_guard_is_structured_fault(self):
        cfg = config(max_overtimes=1)
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([0, 0, 0, 0, 0])):
            with self.assertRaises(complete.DetailedGameSimulationFault) as caught:
                complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                profiles(), 1, cfg)
        self.assertEqual(caught.exception.code, complete.DetailedGameFaultCode.MAX_OVERTIMES)

    def test_typed_result_is_internally_consistent(self):
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([2, 0, 0, 0])):
            result = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                     profiles(), 123, config())
        self.assertEqual(result.home_team_id, "HOME")
        self.assertEqual(result.away_team_id, "AWAY")
        self.assertEqual(result.total_possessions, len(result.possessions))
        self.assertEqual(result.seed, 123)
        self.assertEqual(result.provisional_summary.points,
                         result.final_home_score + result.final_away_score)
        self.assertEqual(tuple(e for r in result.possessions for e in r.events), result.events)


class TestFoulsGuardsAndFirewalls(unittest.TestCase):
    def test_team_fouls_reset_before_every_new_period(self):
        captured = []
        period_fouls = [FoulAdministrationState(team_fouls={"HOME": 3}) for _ in range(4)]
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([2, 0, 0, 0], period_fouls, captured)):
            complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY, profiles(), 1, config())
        self.assertEqual(captured[0].foul_state.team_fouls, {})
        self.assertTrue(all(state.foul_state.team_fouls == {} for state in captured[1:]))

    def test_charge_personal_foul_persists_without_team_foul_pollution(self):
        captured = []
        personal = FoulAdministrationState().personal_fouls.increment(HOME[0])
        charge_state = FoulAdministrationState(personal_fouls=personal, team_fouls={})
        foul_states = [charge_state, None, None, None]
        with patch.object(complete, "simulate_possessions",
                          side_effect=period_script([2, 0, 0, 0], foul_states, captured)):
            complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY, profiles(), 1, config())
        self.assertEqual(captured[1].foul_state.team_fouls, {})
        self.assertEqual(captured[1].foul_state.personal_fouls.counts.get(HOME[0]), 1)

    def test_period_possession_guard_is_fault_not_final(self):
        def stuck(**kwargs):
            return one_possession_period(kwargs["initial_state"], kwargs["home_five"],
                                         kwargs["away_five"], kwargs["profiles"],
                                         stop=SegmentStopReason.MAX_POSSESSIONS, end_clock=1.0)
        with patch.object(complete, "simulate_possessions", side_effect=stuck):
            with self.assertRaises(complete.DetailedGameSimulationFault) as caught:
                complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                profiles(), 1, config())
        self.assertEqual(caught.exception.code, complete.DetailedGameFaultCode.MAX_POSSESSIONS_PER_PERIOD)

    def test_game_possession_guard_is_fault(self):
        cfg = config(max_possessions_per_game=1)
        def stuck(**kwargs):
            return one_possession_period(kwargs["initial_state"], kwargs["home_five"],
                                         kwargs["away_five"], kwargs["profiles"],
                                         stop=SegmentStopReason.MAX_POSSESSIONS, end_clock=1.0)
        with patch.object(complete, "simulate_possessions", side_effect=stuck):
            with self.assertRaises(complete.DetailedGameSimulationFault) as caught:
                complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                profiles(), 1, cfg)
        self.assertEqual(caught.exception.code, complete.DetailedGameFaultCode.MAX_POSSESSIONS_PER_GAME)

    def test_phase23a_fault_propagates(self):
        state = complete.initialize_detailed_game("HOME", "AWAY", config())
        fault = PossessionSimulationFault("forced", 1,
                                          PossessionEngine("p", "HOME", "AWAY").state, ())
        with patch.object(complete, "simulate_possessions", side_effect=fault):
            with self.assertRaises(PossessionSimulationFault):
                complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                profiles(), 1, config())

    def test_no_manual_glue_or_legacy_engine_reference(self):
        src = inspect.getsource(complete)
        self.assertNotIn("StructuralContext(", src)
        self.assertNotIn("game_engine", src)
        self.assertIn("simulate_possessions(", src)
        for filename in ("main.py", "season.py", "playoffs.py", "db.py", "game_engine.py", "models.py"):
            with open(filename, encoding="utf-8") as handle:
                self.assertNotIn("simulate_detailed_game", handle.read())


class TestRealKernelIntegration(unittest.TestCase):
    def test_same_seed_complete_game_replay_and_valid_matchups(self):
        cfg = config(regulation_period_seconds=30.0, overtime_period_seconds=15.0,
                     max_overtimes=5, max_possessions_per_period=100)
        first = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                profiles(), 2401, cfg)
        second = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY,
                                                 profiles(), 2401, cfg)
        summary = lambda result: (
            result.final_home_score, result.final_away_score, result.overtime_periods,
            [(r.possession_id, r.terminal_result.reason, r.end_game_clock)
             for r in result.possessions],
        )
        self.assertEqual(summary(first), summary(second))
        for record in first.possessions:
            assignments = record.terminal_result.engine_state.assignments
            expected_defense = HOME if record.defense_team_id == "HOME" else AWAY
            expected_offense = HOME if record.offense_team_id == "HOME" else AWAY
            self.assertEqual(set(assignments), set(expected_defense))
            self.assertEqual({a.assigned_to_player_id for a in assignments.values()}, set(expected_offense))

    def test_different_seeds_may_produce_different_complete_games(self):
        cfg = config(regulation_period_seconds=30.0, overtime_period_seconds=15.0,
                     max_overtimes=5, max_possessions_per_period=100)
        first = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY, profiles(), 2401, cfg)
        second = complete.simulate_detailed_game("HOME", "AWAY", HOME, AWAY, profiles(), 2402, cfg)
        self.assertNotEqual(
            (first.final_home_score, first.final_away_score,
             [r.terminal_result.reason for r in first.possessions]),
            (second.final_home_score, second.final_away_score,
             [r.terminal_result.reason for r in second.possessions]),
        )


if __name__ == "__main__":
    unittest.main()
