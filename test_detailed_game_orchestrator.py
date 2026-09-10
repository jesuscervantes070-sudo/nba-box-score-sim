"""Phase 23B tests for persistent, bounded possession chaining."""
import inspect
import unittest
from dataclasses import replace
from unittest.mock import patch

import detailed_game_orchestrator as game
from floor_foul_administration import FoulAdministrationState, OFFENSIVE_CHARGE
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
from possession_state import BallState, PossessionPhase, SpatialZone
from transition_state import PossessionChangeSource


HOME = tuple(str(i) for i in range(1, 6))
AWAY = tuple(str(i) for i in range(11, 16))


def profiles():
    return {
        **{pid: PlayerSimulationProfile.synthetic(pid, "HOME") for pid in HOME},
        **{pid: PlayerSimulationProfile.synthetic(pid, "AWAY") for pid in AWAY},
    }


def initial_state(clock=720.0, offense="HOME", fouls=None):
    return game.DetailedGameState(
        home_team_id="HOME", away_team_id="AWAY",
        current_offense_team_id=offense,
        current_defense_team_id="AWAY" if offense == "HOME" else "HOME",
        period=1, game_clock_seconds=clock,
        foul_state=fouls or FoulAdministrationState(),
    )


def fake_terminal(kwargs, reason, duration=5.0, points=0, oreb=0,
                  live_carrier=None, foul_player=None, defensive_team_foul=False):
    """Build a structurally valid forced Phase 23A terminal result."""
    offense = kwargs["offense_team_id"]
    defense = kwargs["defense_team_id"]
    config = kwargs["config"]
    possession_id = kwargs["possession_id"]
    start_clock = config.initial_game_clock_seconds
    if start_clock is None:
        start_clock = (config.era_rules.period_length_seconds if config.era_rules else 720.0)
    end_clock = max(0.0, start_clock - duration)
    engine = PossessionEngine(possession_id, offense, defense, era_rules=config.era_rules,
                              season=None if config.era_rules else config.season, rng_seed=1)
    phase = PossessionPhase.DEAD_BALL
    ball_state = BallState.DEAD
    carrier = None
    terminal_offense, terminal_defense = defense, offense
    events = [Event(EventType.POSSESSION_START, possession_id, 0.0,
                    primary_player_id=kwargs["inbound_receiver_id"], zone=config.initial_ball_zone.value)]
    stats = StatDeltas(points=points, oreb=oreb)
    foul_state = kwargs["foul_state"]

    for _ in range(oreb):
        events.append(Event(EventType.OFFENSIVE_REBOUND, possession_id, 0.0,
                            primary_player_id=kwargs["offensive_five"][0]))
    if reason == PossessionTerminalReason.MADE_FG:
        events.append(Event(EventType.SHOT_RESOLVED, possession_id, 0.0,
                            primary_player_id=kwargs["offensive_five"][0], metadata={"made": True}))
    elif reason == PossessionTerminalReason.DEFENSIVE_REBOUND:
        carrier = live_carrier or kwargs["defensive_five"][0]
        phase, ball_state = PossessionPhase.TRANSITION, BallState.HELD
        stats.dreb = 1
        events.append(Event(EventType.DEFENSIVE_REBOUND, possession_id, 0.0, primary_player_id=carrier))
    elif reason == PossessionTerminalReason.TURNOVER:
        stats.turnovers = 1
        if live_carrier:
            carrier, phase, ball_state = live_carrier, PossessionPhase.TRANSITION, BallState.HELD
            events.append(Event(EventType.LIVE_BALL_TURNOVER, possession_id, 0.0, primary_player_id=carrier))
        else:
            events.append(Event(EventType.DEAD_BALL_TURNOVER, possession_id, 0.0,
                                primary_player_id=kwargs["offensive_five"][0]))
    elif reason == PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER:
        offender = foul_player or kwargs["offensive_five"][0]
        stats.turnovers = 1
        stats.personal_fouls[offender] = 1
        foul_state = replace(foul_state, personal_fouls=foul_state.personal_fouls.increment(offender))
        events.extend([
            Event(EventType.DEAD_BALL_TURNOVER, possession_id, 0.0, primary_player_id=offender),
            Event(EventType.REACTION_CHECKPOINT, possession_id, 0.0, primary_player_id=offender,
                  metadata={"checkpoint": "floor_foul_administered", "foul_class": OFFENSIVE_CHARGE}),
        ])
    elif reason == PossessionTerminalReason.FINAL_FT_MADE:
        if foul_player:
            stats.personal_fouls[foul_player] = 1
            personal = foul_state.personal_fouls.increment(foul_player)
            team_fouls = dict(foul_state.team_fouls)
            if defensive_team_foul:
                team_fouls[defense] = team_fouls.get(defense, 0) + 1
            foul_state = replace(foul_state, personal_fouls=personal, team_fouls=team_fouls)
            events.append(Event(EventType.SHOOTING_FOUL, possession_id, 0.0,
                                primary_player_id=foul_player, secondary_player_id=kwargs["offensive_five"][0]))
        stats.fta = max(points, 1)
        stats.ftm = points
    elif reason == PossessionTerminalReason.SHOT_CLOCK_VIOLATION:
        events.append(Event(EventType.SHOT_CLOCK_VIOLATION, possession_id, 0.0))
    elif reason == PossessionTerminalReason.PERIOD_END:
        terminal_offense, terminal_defense = offense, defense
        end_clock = 0.0
        events.append(Event(EventType.PERIOD_EXPIRATION, possession_id, 0.0))

    state = replace(engine.state, game_clock_remaining=end_clock, phase=phase,
                    ball_state=ball_state, ball_carrier=carrier,
                    ball_control=engine.state.with_ball_carrier(carrier, ball_state).ball_control,
                    offense_team_id=terminal_offense if phase == PossessionPhase.TRANSITION else offense,
                    defense_team_id=terminal_defense if phase == PossessionPhase.TRANSITION else defense)
    world = PossessionWorld(
        team_a_id=offense, team_b_id=defense,
        team_a_five=kwargs["offensive_five"], team_b_five=kwargs["defensive_five"],
        profiles=kwargs["profiles"], foul_state=foul_state, stats=stats,
    )
    return PossessionTerminalResult(
        reason=reason, resulting_offense_team_id=terminal_offense,
        resulting_defense_team_id=terminal_defense, stats=stats, steps_taken=1,
        engine_state=state, events=tuple(events), world=world,
    )


def scripted_runner(specs):
    pending = list(specs)

    def run(**kwargs):
        return fake_terminal(kwargs, **pending.pop(0))
    return run


class TestStateAndLineupContract(unittest.TestCase):
    def test_state_fields_and_team_foul_view(self):
        state = initial_state()
        self.assertEqual(state.next_possession_sequence, 1)
        self.assertEqual(state.team_fouls_by_team, {})

    def test_exactly_five_unique_players_per_team(self):
        with self.assertRaises(ValueError):
            game.validate_detailed_game_inputs(initial_state(), HOME[:4], AWAY, profiles())

    def test_no_player_can_appear_on_both_teams(self):
        with self.assertRaises(ValueError):
            game.validate_detailed_game_inputs(initial_state(), HOME, (HOME[0],) + AWAY[1:], profiles())

    def test_profiles_are_exact_canonical_ten_without_fallback(self):
        bad = profiles()
        del bad[HOME[0]]
        with self.assertRaises(ValueError):
            game.validate_detailed_game_inputs(initial_state(), HOME, AWAY, bad)

    def test_profile_team_binding_is_enforced(self):
        bad = profiles()
        bad[HOME[0]] = replace(bad[HOME[0]], team_id="AWAY")
        with self.assertRaises(ValueError):
            game.validate_detailed_game_inputs(initial_state(), HOME, AWAY, bad)

    def test_invalid_game_state_rejected(self):
        with self.assertRaises(ValueError):
            game.validate_detailed_game_inputs(replace(initial_state(), game_clock_seconds=-1), HOME, AWAY, profiles())


class TestInitializationAndFlipRules(unittest.TestCase):
    def test_initial_dead_ball_start_builds_phase23a_inputs(self):
        start = game.initialize_next_possession(initial_state(), HOME, AWAY)
        self.assertEqual(start.possession_id, "period1-possession1")
        self.assertEqual(start.inbound_receiver_id, HOME[0])
        self.assertEqual(start.phase, PossessionPhase.HALFCOURT)

    def test_made_shot_flips_to_opponent_dead_ball_inbound(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.MADE_FG, "points": 3},
            {"reason": PossessionTerminalReason.MADE_FG, "points": 2},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
        self.assertEqual([r.offense_team_id for r in result.possessions], ["HOME", "AWAY"])
        self.assertEqual(result.possessions[1].restart_context.source, PossessionChangeSource.MADE_BASKET_INBOUND)
        self.assertEqual(result.possessions[1].restart_context.restart_type, game.RestartType.DEAD_BALL_INBOUND)

    def test_defensive_rebound_hands_off_transition_carrier(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.DEFENSIVE_REBOUND, "live_carrier": AWAY[2]},
            {"reason": PossessionTerminalReason.MADE_FG},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
        second = result.possessions[1]
        self.assertEqual(second.restart_context.restart_type, game.RestartType.LIVE_TRANSITION)
        self.assertEqual(second.restart_context.ball_carrier_id, AWAY[2])
        self.assertEqual(second.restart_context.source, PossessionChangeSource.DEFENSIVE_REBOUND)

    def test_live_turnover_hands_off_transition_carrier(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.TURNOVER, "live_carrier": AWAY[1]},
            {"reason": PossessionTerminalReason.MADE_FG},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
        self.assertEqual(result.possessions[1].restart_context.restart_type, game.RestartType.LIVE_TRANSITION)
        self.assertEqual(result.possessions[1].restart_context.ball_carrier_id, AWAY[1])

    def test_dead_ball_turnover_and_charge_restart_dead(self):
        for reason in (PossessionTerminalReason.TURNOVER, PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER):
            with self.subTest(reason=reason), patch.object(game, "simulate_possession", side_effect=scripted_runner([
                {"reason": reason}, {"reason": PossessionTerminalReason.MADE_FG},
            ])):
                result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
            self.assertEqual(result.possessions[1].restart_context.restart_type, game.RestartType.DEAD_BALL_INBOUND)

    def test_shot_clock_violation_flips_to_dead_ball_restart(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.SHOT_CLOCK_VIOLATION},
            {"reason": PossessionTerminalReason.MADE_FG},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
        self.assertEqual(result.possessions[1].offense_team_id, "AWAY")
        self.assertEqual(result.possessions[1].restart_context.restart_type, game.RestartType.DEAD_BALL_INBOUND)

    def test_shooting_foul_free_throws_score_then_flip(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.FINAL_FT_MADE, "points": 2,
             "foul_player": AWAY[0], "defensive_team_foul": True},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 1, 1)
        self.assertEqual(result.final_state.score_home, 2)
        self.assertEqual(result.final_state.current_offense_team_id, "AWAY")
        self.assertEqual(result.final_state.team_fouls_by_team, {"AWAY": 1})

    def test_orebs_and_multiple_second_chances_stay_one_possession_id(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.MADE_FG, "points": 2, "oreb": 2},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 1, 1)
        self.assertEqual(len(result.possessions), 1)
        self.assertEqual(result.possessions[0].provisional_deltas.oreb, 2)
        self.assertEqual(result.final_state.next_possession_sequence, 2)
        self.assertEqual({e.possession_id for e in result.events}, {"period1-possession1"})


class TestClockScoreFoulsAndEvents(unittest.TestCase):
    def test_clock_is_written_from_terminal_once_without_double_charge(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.MADE_FG, "duration": 7.0},
            {"reason": PossessionTerminalReason.MADE_FG, "duration": 4.0},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(clock=100), 2, 1)
        self.assertEqual([(r.start_game_clock, r.end_game_clock) for r in result.possessions],
                         [(100, 93), (93, 89)])
        self.assertEqual(result.final_state.game_clock_seconds, 89)

    def test_period_expiration_stops_without_starting_another_possession(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.PERIOD_END},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(clock=2), 10, 1)
        self.assertEqual(result.stop_reason, game.SegmentStopReason.PERIOD_COMPLETE)
        self.assertEqual(len(result.possessions), 1)
        self.assertIsNone(result.next_restart_context)

    def test_max_possession_bound_stops_cleanly(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.MADE_FG},
            {"reason": PossessionTerminalReason.MADE_FG},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
        self.assertEqual(result.stop_reason, game.SegmentStopReason.MAX_POSSESSIONS)
        self.assertEqual(len(result.possessions), 2)

    def test_score_is_monotonic_and_applied_to_possession_offense(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.MADE_FG, "points": 3},
            {"reason": PossessionTerminalReason.MADE_FG, "points": 2},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
        self.assertEqual((result.final_state.score_home, result.final_state.score_away), (3, 2))
        self.assertTrue(all(r.end_score_home >= r.start_score_home and r.end_score_away >= r.start_score_away
                            for r in result.possessions))

    def test_team_fouls_persist_across_possessions(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.FINAL_FT_MADE, "points": 1,
             "foul_player": AWAY[0], "defensive_team_foul": True},
            {"reason": PossessionTerminalReason.FINAL_FT_MADE, "points": 1,
             "foul_player": HOME[0], "defensive_team_foul": True},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
        self.assertEqual(result.final_state.team_fouls_by_team, {"AWAY": 1, "HOME": 1})

    def test_offensive_charge_is_personal_foul_but_not_team_foul(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER, "foul_player": HOME[0]},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 1, 1)
        self.assertEqual(result.final_state.team_fouls_by_team, {})
        self.assertEqual(result.final_state.foul_state.personal_fouls.counts.get(HOME[0]), 1)

    def test_bonus_is_derived_with_phase21b_threshold(self):
        fouls = FoulAdministrationState(team_fouls={"AWAY": 5})
        rules = EraRules("test", 24.0, 14.0, 5, 720.0, 4)
        self.assertTrue(initial_state(fouls=fouls).in_bonus("AWAY", rules))

    def test_game_event_view_preserves_record_order(self):
        with patch.object(game, "simulate_possession", side_effect=scripted_runner([
            {"reason": PossessionTerminalReason.MADE_FG},
            {"reason": PossessionTerminalReason.TURNOVER},
        ])):
            result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 2, 1)
        self.assertEqual([e.possession_id for e in result.events],
                         [e.possession_id for r in result.possessions for e in r.events])
        self.assertEqual([r.possession_id for r in result.possessions],
                         ["period1-possession1", "period1-possession2"])

    def test_mismatched_event_accounting_fails_loudly(self):
        start = game.initialize_next_possession(initial_state(), HOME, AWAY)
        result = fake_terminal({
            "offense_team_id": "HOME", "defense_team_id": "AWAY", "config": PossessionConfig(),
            "possession_id": start.possession_id, "inbound_receiver_id": HOME[0],
            "offensive_five": HOME, "defensive_five": AWAY, "profiles": profiles(),
            "foul_state": FoulAdministrationState(),
        }, PossessionTerminalReason.MADE_FG)
        result.stats.turnovers = 1
        with self.assertRaises(game.DetailedGameInvariantError):
            game.apply_possession_result(initial_state(), start, result)


class TestReplayFaultsAndFirewalls(unittest.TestCase):
    def test_same_seed_replays_identically_with_real_phase23a(self):
        a = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 5, 8675309)
        b = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 5, 8675309)
        summary = lambda r: [(p.terminal_result.reason, p.end_game_clock,
                              p.end_score_home, p.end_score_away) for p in r.possessions]
        self.assertEqual(summary(a), summary(b))

    def test_different_seed_may_diverge(self):
        a = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 10, 1)
        b = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 10, 2)
        summary = lambda r: [(p.terminal_result.reason, p.end_game_clock,
                              p.end_score_home, p.end_score_away) for p in r.possessions]
        self.assertNotEqual(summary(a), summary(b))

    def test_phase23a_fault_propagates(self):
        with self.assertRaises(PossessionSimulationFault):
            game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 1, 1,
                                      possession_config=PossessionConfig(max_steps_per_possession=0))

    def test_matchup_bijection_is_rebuilt_each_possession(self):
        result = game.simulate_possessions(HOME, AWAY, profiles(), initial_state(), 4, 99)
        for record in result.possessions:
            assignments = record.terminal_result.engine_state.assignments
            self.assertEqual(set(assignments), set(record.defense_team_id == "HOME" and HOME or AWAY))
            self.assertEqual({a.assigned_to_player_id for a in assignments.values()},
                             set(record.offense_team_id == "HOME" and HOME or AWAY))

    def test_no_manual_structural_context_or_legacy_routing(self):
        src = inspect.getsource(game)
        self.assertNotIn("StructuralContext(", src)
        self.assertNotIn("game_engine", src)
        for filename in ("main.py", "season.py", "playoffs.py", "db.py", "game_engine.py", "models.py"):
            with open(filename, encoding="utf-8") as handle:
                self.assertNotIn("detailed_game_orchestrator", handle.read())


if __name__ == "__main__":
    unittest.main()
