"""
Phase 15 -- Possession State & Event Kernel: focused tests.

No empirical outcome (make%, pass success, etc.) is tested here -- every
test checks STATE TRANSITION CORRECTNESS and INTERFACE CONTRACTS only.
"""
import unittest
from unittest.mock import patch

from possession_advantage import DiscreteTierAdvantage, SpatialMagnitudeAdvantage
from possession_engine import PossessionEngine, resolve_legacy_name_to_engine_id
from possession_events import Event, EventLog, EventType
from possession_rules import get_era_rules, oreb_reset_value
from possession_state import (
    BallState, DefensivePosture, DribbleState, IllegalControlTransition, PlayerBallControl,
    PossessionPhase, PossessionState, SpatialZone, _assert_player_id, ball_side, is_strong_side,
)


def _engine(rng_seed=1, season="2023-24"):
    return PossessionEngine("poss-1", "TEAM_A", "TEAM_B", season=season, rng_seed=rng_seed)


class TestBallStateTransitions(unittest.TestCase):
    def test_held_to_pass_in_flight_to_held_transfer(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.pass_ball(dt=0.4)
        self.assertEqual(e.state.ball_state, BallState.PASS_IN_FLIGHT)
        self.assertIsNone(e.state.ball_carrier)
        e.receive_pass("2", dt=0.3)
        self.assertEqual(e.state.ball_state, BallState.HELD)
        self.assertEqual(e.state.ball_carrier, "2")

    def test_held_to_shot_in_flight_to_terminal_on_make(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.RESTRICTED_RIM, dt=1.0)
        self.assertEqual(e.state.ball_state, BallState.SHOT_IN_FLIGHT)
        self.assertIsNone(e.state.ball_carrier)
        e.resolve_shot_made("1", dt=0.0)
        self.assertEqual(e.state.ball_state, BallState.DEAD)
        self.assertEqual(e.state.phase, PossessionPhase.DEAD_BALL)

    def test_held_to_shot_in_flight_to_rebound(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.PAINT, dt=1.0)
        e.resolve_shot_missed_defensive_rebound("1", "9", dt=0.0)
        self.assertEqual(e.state.ball_carrier, "9")
        self.assertEqual(e.state.ball_state, BallState.HELD)

    def test_loose_ball_resolution(self):
        e = _engine()
        e.inbound("1", SpatialZone.RESTRICTED_RIM, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.RESTRICTED_RIM, dt=0.5)
        e.block_secured_by_defense("9", "1", dt=0.2)
        self.assertEqual(e.state.ball_state, BallState.LOOSE)
        self.assertIsNone(e.state.offense_team_id)  # genuinely unresolved
        e.secure_loose_ball("9", "TEAM_B", "TEAM_A", dt=0.3)
        self.assertEqual(e.state.ball_state, BallState.HELD)
        self.assertEqual(e.state.offense_team_id, "TEAM_B")

    def test_offense_team_possession_survives_ball_flight(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.pass_ball(dt=0.2)
        # ball_carrier is None mid-flight, but TEAM possession must be untouched
        self.assertIsNone(e.state.ball_carrier)
        self.assertEqual(e.state.offense_team_id, "TEAM_A")
        self.assertEqual(e.state.defense_team_id, "TEAM_B")


class TestDribbleControl(unittest.TestCase):
    def test_live_gather_dead_transitions(self):
        c = PlayerBallControl("1")
        self.assertEqual(c.state, DribbleState.LIVE_DRIBBLE)
        c2 = c.gather()
        self.assertEqual(c2.state, DribbleState.GATHERED)
        c3 = c2.go_dead()
        self.assertEqual(c3.state, DribbleState.DEAD_DRIBBLE)

    def test_illegal_redribble_after_gather_raises(self):
        c = PlayerBallControl("1").gather()
        with self.assertRaises(IllegalControlTransition):
            c.continue_dribble()

    def test_illegal_gather_after_dead_raises(self):
        c = PlayerBallControl("1").gather().go_dead()
        with self.assertRaises(IllegalControlTransition):
            c.gather()

    def test_new_carrier_always_starts_fresh_live_dribble(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.gather()
        self.assertEqual(e.state.ball_control.state, DribbleState.GATHERED)
        e.pass_ball(dt=0.1)
        e.receive_pass("2", dt=0.1)
        # a NEW carrier must not inherit the old carrier's GATHERED state
        self.assertEqual(e.state.ball_control.player_id, "2")
        self.assertEqual(e.state.ball_control.state, DribbleState.LIVE_DRIBBLE)


class TestReactiveSubevent(unittest.TestCase):
    def test_interruption_stops_before_remaining_checkpoints(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        checkpoints = ["drive_begins", "poa_interaction", "defender_beaten", "help_opportunity", "release"]
        seen = []

        def reaction(cp, state):
            seen.append(cp)
            return "INTERRUPT" if cp == "defender_beaten" else None

        last, interrupted = e.run_checkpointed_action(checkpoints, reaction)
        self.assertTrue(interrupted)
        self.assertEqual(last, "defender_beaten")
        self.assertEqual(seen, ["drive_begins", "poa_interaction", "defender_beaten"])  # never reached "help_opportunity"/"release"

    def test_no_interruption_runs_all_checkpoints(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        checkpoints = ["a", "b", "c"]
        last, interrupted = e.run_checkpointed_action(checkpoints, lambda cp, s: None)
        self.assertFalse(interrupted)
        self.assertEqual(last, "c")


class TestMatchupAndPosture(unittest.TestCase):
    def test_matchup_pointer_updates_after_switch(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.switch("9", "1")
        self.assertEqual(e.state.assignments["9"].assigned_to_player_id, "1")
        e.switch("9", "2")
        self.assertEqual(e.state.assignments["9"].assigned_to_player_id, "2")

    def test_posture_update_and_cleanup(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.switch("9", "1", posture=DefensivePosture.SQUARE)
        e.update_posture("9", DefensivePosture.HELPING)
        self.assertEqual(e.state.assignments["9"].posture, DefensivePosture.HELPING)
        # switching again must not silently retain a stale posture from before
        e.switch("9", "2", posture=DefensivePosture.SQUARE)
        self.assertEqual(e.state.assignments["9"].posture, DefensivePosture.SQUARE)


class TestObjectiveOpportunityVsPerception(unittest.TestCase):
    def test_compromised_area_exists_independent_of_any_player_perceiving_it(self):
        """A CompromisedArea is pure world state -- constructing one
        requires no player/perception object at all, proving the
        WORLD OPPORTUNITY layer is representable independent of the
        (unbuilt) PLAYER PERCEPTION layer."""
        model = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.6})
        areas = model.compromised_areas()
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0].zone, SpatialZone.PAINT)


class TestAdvantageAbstraction(unittest.TestCase):
    def test_survives_multiple_representations(self):
        """The SAME sequence of interface operations against two
        structurally different implementations -- only interface-level
        invariants are asserted, never a specific numeric formula."""
        for model in (DiscreteTierAdvantage(tiers={SpatialZone.PAINT: "COLLAPSED"}),
                      SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.8})):
            areas = model.compromised_areas()
            self.assertEqual(len(areas), 1)
            decayed = model.decay(1.0)
            self.assertLessEqual(len(decayed.compromised_areas()), len(areas))  # decay never increases compromise
            transferred = model.transfer(SpatialZone.PAINT, SpatialZone.LEFT_WING)
            self.assertEqual(transferred.compromised_areas()[0].zone, SpatialZone.LEFT_WING)
            compounded = model.compound(model)
            self.assertGreaterEqual(len(compounded.compromised_areas()), 1)

    def test_multiple_simultaneous_compromised_areas(self):
        model = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.5, SpatialZone.LEFT_CORNER: 0.3})
        areas = model.compromised_areas()
        self.assertEqual(len(areas), 2)
        zones = {a.zone for a in areas}
        self.assertEqual(zones, {SpatialZone.PAINT, SpatialZone.LEFT_CORNER})

    def test_compound_requires_same_representation(self):
        d = DiscreteTierAdvantage()
        s = SpatialMagnitudeAdvantage()
        with self.assertRaises(TypeError):
            d.compound(s)


class TestSecondChanceContext(unittest.TestCase):
    def test_offensive_rebound_creates_second_chance_and_clears_advantage(self):
        e = _engine()
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9})
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.PAINT, dt=1.0)
        e.resolve_shot_missed_offensive_rebound("1", "2", dt=0.0)
        self.assertEqual(e.state.phase, PossessionPhase.SECOND_CHANCE)
        # must NOT automatically preserve the old advantage
        self.assertIsNone(e.advantage)

    def test_offensive_rebound_does_not_force_a_putback(self):
        """After OREB, the new carrier must be free to pass/reset, not
        forced directly into another shot -- verified by successfully
        passing instead of shooting immediately."""
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.PAINT, dt=1.0)
        e.resolve_shot_missed_offensive_rebound("1", "2", dt=0.0)
        e.pass_ball(dt=0.2)  # would raise if the engine forced a shot-only state
        self.assertEqual(e.state.ball_state, BallState.PASS_IN_FLIGHT)


class TestAdvantageHygieneOnDefensiveControl(unittest.TestCase):
    """Advantage hygiene fix -- see docs' "Advantage Hygiene Fix" section.
    Every method that transitions the engine into a real, new possession-
    control context (offensive rebound already covered above; defensive
    control below) must explicitly clear `self.advantage` rather than
    silently carrying the OLD offense's own advantage forward -- even
    though, under every current orchestration path, the possession
    terminates immediately after any of these and a brand-new engine
    (with `advantage=None`) is constructed for the next possession
    regardless (confirmed: the full suite is unaffected by this fix)."""

    def test_resolve_shot_missed_defensive_rebound_clears_advantage(self):
        e = _engine()
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9})
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.PAINT, dt=1.0)
        e.resolve_shot_missed_defensive_rebound("1", "11", dt=0.0)
        self.assertIsNone(e.advantage)

    def test_secure_defensive_rebound_from_loose_clears_advantage(self):
        e = _engine()
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9})
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.PAINT, dt=1.0)
        e.resolve_shot_missed_pending_rebound("1", dt=0.0)
        self.assertEqual(e.state.ball_state, BallState.LOOSE)
        e.secure_defensive_rebound_from_loose("11", "TEAM_B", "TEAM_A", dt=0.0)
        self.assertIsNone(e.advantage)

    def test_credit_team_rebound_defense_branch_clears_advantage(self):
        e = _engine()
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9})
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.PAINT, dt=1.0)
        e.resolve_shot_missed_pending_rebound("1", dt=0.0)
        e.credit_team_rebound("DEFENSE", new_offense_team_id="TEAM_B", new_defense_team_id="TEAM_A", dt=0.0)
        self.assertIsNone(e.advantage)

    def test_credit_team_rebound_offense_branch_still_clears_advantage(self):
        """Regression guard: the OFFENSE branch already cleared advantage
        before this fix -- confirm it still does (unchanged)."""
        e = _engine()
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9})
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.PAINT, dt=1.0)
        e.resolve_shot_missed_pending_rebound("1", dt=0.0)
        e.credit_team_rebound("OFFENSE", dt=0.0)
        self.assertIsNone(e.advantage)


class TestTransitionToHalfcourt(unittest.TestCase):
    def test_transition_phase_can_move_to_halfcourt(self):
        e = _engine()
        e.inbound("1", SpatialZone.BACKCOURT, PossessionPhase.TRANSITION)
        self.assertEqual(e.state.phase, PossessionPhase.TRANSITION)
        e.state.phase = PossessionPhase.HALFCOURT  # a future engine method would encapsulate this; state itself permits the transition
        self.assertEqual(e.state.phase, PossessionPhase.HALFCOURT)


class TestEraRulesHooks(unittest.TestCase):
    def test_shot_clock_reset_hook_differs_by_era(self):
        modern = get_era_rules("2023-24")
        classic = get_era_rules("2005-06")
        pre_clock = get_era_rules("1950-51")
        self.assertEqual(oreb_reset_value(modern, 20.0), 14.0)
        self.assertEqual(oreb_reset_value(classic, 20.0), 24.0)
        self.assertIsNone(oreb_reset_value(pre_clock, 20.0))

    def test_bonus_rules_hook(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        in_bonus = e.non_shooting_foul("9", "1", team_foul_count=5, dt=0.0)
        self.assertTrue(in_bonus)
        not_bonus = e.non_shooting_foul("9", "1", team_foul_count=1, dt=0.0)
        self.assertFalse(not_bonus)


class TestBlockedShotLiveState(unittest.TestCase):
    def test_block_retained_by_offense_keeps_team_possession(self):
        e = _engine()
        e.inbound("1", SpatialZone.RESTRICTED_RIM, PossessionPhase.HALFCOURT)
        e.begin_shot(SpatialZone.RESTRICTED_RIM, dt=0.5)
        e.block_retained_by_offense("9", "1", dt=0.1)
        self.assertEqual(e.state.ball_state, BallState.LOOSE)
        self.assertEqual(e.state.offense_team_id, "TEAM_A")  # NOT unresolved -- offense retained it


class TestTurnoverKinds(unittest.TestCase):
    def test_dead_ball_vs_live_ball_turnover(self):
        e1 = _engine()
        e1.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e1.live_ball_turnover("9", "TEAM_B", "TEAM_A", dt=0.2)
        self.assertEqual(e1.state.ball_state, BallState.HELD)  # ball stays live

        e2 = _engine()
        e2.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e2.dead_ball_turnover("1", dt=0.0)
        self.assertEqual(e2.state.ball_state, BallState.DEAD)
        self.assertEqual(e2.state.phase, PossessionPhase.DEAD_BALL)


class TestJumpBallAndShotClockViolation(unittest.TestCase):
    def test_jump_ball_leaves_team_possession_unresolved(self):
        e = _engine()
        e.jump_ball(dt=0.0)
        self.assertEqual(e.state.ball_state, BallState.LOOSE)
        self.assertIsNone(e.state.offense_team_id)
        self.assertIsNone(e.state.defense_team_id)

    def test_shot_clock_violation_is_dead_ball(self):
        e = _engine()
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e.shot_clock_violation(dt=0.0)
        self.assertEqual(e.state.ball_state, BallState.DEAD)
        self.assertEqual(e.state.phase, PossessionPhase.DEAD_BALL)


class TestEventLogAccounting(unittest.TestCase):
    def test_retained_mode_keeps_events(self):
        log = EventLog(mode="retained")
        log.record(Event(event_type=EventType.PASS_RELEASED, possession_id="p", delta_t=0.1, primary_player_id="1"))
        self.assertEqual(len(log), 1)
        self.assertEqual(log.events[0].primary_player_id, "1")

    def test_streaming_mode_does_not_retain(self):
        seen = []
        log = EventLog(mode="streaming", aggregator=seen.append)
        log.record(Event(event_type=EventType.PASS_RELEASED, possession_id="p", delta_t=0.1))
        self.assertEqual(len(log), 0)  # nothing retained
        self.assertEqual(len(seen), 1)  # but the aggregator saw it

    def test_streaming_mode_requires_aggregator(self):
        with self.assertRaises(ValueError):
            EventLog(mode="streaming")


class TestPlayerIdOnlyInterface(unittest.TestCase):
    def test_name_keyed_path_rejected(self):
        e = _engine()
        with self.assertRaises(TypeError):
            e.inbound("LeBron James", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)

    def test_none_is_a_valid_player_id_value(self):
        _assert_player_id(None)  # must not raise -- an explicit "no player" state

    def test_legacy_bridge_resolves_through_identity_layer(self):
        with patch("player_identity.resolve_name_to_id") as mock_resolve:
            import player_identity as pid
            mock_resolve.return_value = pid.IdentityResolution(query="Tyrese Maxey", state=pid.RESOLVED,
                                                                 player_id="1630178", canonical_name="Tyrese Maxey")
            result = resolve_legacy_name_to_engine_id("Tyrese Maxey")
        self.assertEqual(result, "1630178")

    def test_legacy_bridge_refuses_ambiguous(self):
        with patch("player_identity.resolve_name_to_id") as mock_resolve:
            import player_identity as pid
            mock_resolve.return_value = pid.IdentityResolution(query="Patrick Ewing", state=pid.AMBIGUOUS,
                                                                 candidates=("121", "201607"))
            with self.assertRaises(ValueError):
                resolve_legacy_name_to_engine_id("Patrick Ewing")


class TestDeterministicReplay(unittest.TestCase):
    def test_same_seed_produces_same_rng_sequence(self):
        e1 = _engine(rng_seed=99)
        e2 = _engine(rng_seed=99)
        seq1 = [e1.rng.random() for _ in range(10)]
        seq2 = [e2.rng.random() for _ in range(10)]
        self.assertEqual(seq1, seq2)

    def test_different_seed_produces_different_sequence(self):
        e1 = _engine(rng_seed=1)
        e2 = _engine(rng_seed=2)
        seq1 = [e1.rng.random() for _ in range(10)]
        seq2 = [e2.rng.random() for _ in range(10)]
        self.assertNotEqual(seq1, seq2)


class TestNoCrossPossessionLeakage(unittest.TestCase):
    def test_two_engines_do_not_share_mutable_state(self):
        e1 = _engine()
        e2 = _engine()
        e1.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        e1.switch("9", "1")
        # e2 must be completely unaffected
        self.assertEqual(e2.state.ball_state, BallState.DEAD)
        self.assertEqual(e2.state.assignments, {})


class TestSpatialTopology(unittest.TestCase):
    def test_ball_side_classification(self):
        self.assertEqual(ball_side(SpatialZone.LEFT_CORNER), "LEFT")
        self.assertEqual(ball_side(SpatialZone.RIGHT_WING), "RIGHT")
        self.assertEqual(ball_side(SpatialZone.PAINT), "CENTRAL")

    def test_strong_side_derivation(self):
        self.assertTrue(is_strong_side(SpatialZone.LEFT_CORNER, SpatialZone.LEFT_WING))
        self.assertFalse(is_strong_side(SpatialZone.RIGHT_CORNER, SpatialZone.LEFT_WING))
        self.assertTrue(is_strong_side(SpatialZone.PAINT, SpatialZone.LEFT_WING))  # central is never weak-side


if __name__ == "__main__":
    unittest.main()
