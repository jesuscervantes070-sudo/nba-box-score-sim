"""Phase 20B -- focused tests for transition_offense.py."""
import inspect
import random
import unittest

from action_intent import ActionType, CREATION_ACTIONS, PASS_ACTIONS
from action_opportunity import StructuralContext
from action_perception import perceive
from action_selection import ClockContext, RoleContext, SelectionPolicy, TendencyContext, _score_action
from possession_advantage import SpatialMagnitudeAdvantage
from possession_engine import PossessionEngine
from possession_state import PossessionPhase, SpatialZone
from transition_offense import decay_transition, generate_transition_opportunities
from transition_state import FloorPlayer, PossessionChangeSource, initialize_transition_state


def _engine_with_transition(zones=None, ball_zone=SpatialZone.BACKCOURT):
    e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
    ts = initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", ball_zone,
                                       zones or [])
    return e, ts


class TestPushOpportunity(unittest.TestCase):
    def test_transition_push_generated_in_transition_phase_with_a_real_receiver(self):
        """"Add interior shot-opportunity generation": TRANSITION_PUSH is now a real, dispatchable
        PASS targeting RESTRICTED_RIM -- it requires a real receiver (`nearest_teammate_id`), the
        same structural precondition SWING_PASS/RESET_PASS already use."""
        e, ts = _engine_with_transition()
        opps = generate_transition_opportunities(
            e.state, StructuralContext(nearest_teammate_id="2", nearest_teammate_zone=SpatialZone.TOP_OF_KEY), ts)
        push = next((o for o in opps if o.action_type == ActionType.TRANSITION_PUSH), None)
        self.assertIsNotNone(push)
        self.assertEqual(push.target_player_id, "2")
        self.assertEqual(push.target_zone, SpatialZone.RESTRICTED_RIM)

    def test_transition_push_not_generated_without_a_real_receiver(self):
        """No teammate -> no opportunity at all -- never a phantom/targetless action."""
        e, ts = _engine_with_transition()
        opps = generate_transition_opportunities(e.state, StructuralContext(), ts)
        self.assertFalse(any(o.action_type == ActionType.TRANSITION_PUSH for o in opps))


class TestOutletOpportunity(unittest.TestCase):
    def test_outlet_generated_for_ahead_of_ball_teammate(self):
        zones = [FloorPlayer("9", SpatialZone.BACKCOURT, "OFFENSE"), FloorPlayer("2", SpatialZone.TOP_OF_KEY, "OFFENSE")]
        e, ts = _engine_with_transition(zones)
        opps = generate_transition_opportunities(e.state, StructuralContext(), ts)
        outlets = [o for o in opps if o.action_type == ActionType.OUTLET_PASS]
        self.assertEqual(len(outlets), 1)
        self.assertEqual(outlets[0].target_player_id, "2")

    def test_no_outlet_without_eligible_receiver(self):
        zones = [FloorPlayer("9", SpatialZone.BACKCOURT, "OFFENSE")]  # only the carrier -- no one ahead
        e, ts = _engine_with_transition(zones)
        opps = generate_transition_opportunities(e.state, StructuralContext(), ts)
        self.assertFalse(any(o.action_type == ActionType.OUTLET_PASS for o in opps))

    def test_no_outlet_without_transition_state(self):
        e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        opps = generate_transition_opportunities(e.state, StructuralContext(nearest_teammate_id="2"), None)
        self.assertFalse(any(o.action_type == ActionType.OUTLET_PASS for o in opps))

    def test_receiver_must_be_offense_side(self):
        zones = [FloorPlayer("9", SpatialZone.BACKCOURT, "OFFENSE"), FloorPlayer("8", SpatialZone.TOP_OF_KEY, "DEFENSE")]
        e, ts = _engine_with_transition(zones)
        opps = generate_transition_opportunities(e.state, StructuralContext(), ts)
        self.assertFalse(any(o.action_type == ActionType.OUTLET_PASS for o in opps))


class TestEarlyAttackOpportunity(unittest.TestCase):
    def test_drive_and_pullup_available_in_transition(self):
        e, ts = _engine_with_transition()
        opps = generate_transition_opportunities(e.state, StructuralContext(), ts)
        types = {o.action_type for o in opps}
        self.assertIn(ActionType.DRIVE, types)
        self.assertIn(ActionType.PULL_UP, types)


class TestResetOpportunity(unittest.TestCase):
    def test_reset_pass_available(self):
        e, ts = _engine_with_transition(ball_zone=SpatialZone.BACKCOURT)
        opps = generate_transition_opportunities(e.state, StructuralContext(nearest_teammate_id="2"), ts)
        self.assertTrue(any(o.action_type == ActionType.RESET_PASS for o in opps))


class TestDrebSteallDeadBallStates(unittest.TestCase):
    def test_dreb_initializes_transition_phase(self):
        e, ts = _engine_with_transition()
        self.assertEqual(e.state.phase, PossessionPhase.TRANSITION)

    def test_steal_also_transition_capable(self):
        e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        ts = initialize_transition_state(e, PossessionChangeSource.LIVE_STEAL, "B", "A", "9", SpatialZone.TOP_OF_KEY)
        self.assertEqual(e.state.phase, PossessionPhase.TRANSITION)

    def test_dead_ball_neutral_generates_no_outlet(self):
        e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        zones = [FloorPlayer("2", SpatialZone.TOP_OF_KEY, "OFFENSE")]
        ts = initialize_transition_state(e, PossessionChangeSource.MADE_BASKET_INBOUND, "B", "A", "9",
                                           SpatialZone.BACKCOURT, zones)
        opps = generate_transition_opportunities(e.state, StructuralContext(), ts)
        self.assertFalse(any(o.action_type == ActionType.OUTLET_PASS for o in opps))
        self.assertEqual(e.state.phase, PossessionPhase.HALFCOURT)


class TestPhase16Integration(unittest.TestCase):
    def test_selection_can_choose_transition_actions(self):
        zones = [FloorPlayer("9", SpatialZone.BACKCOURT, "OFFENSE"), FloorPlayer("2", SpatialZone.TOP_OF_KEY, "OFFENSE")]
        e, ts = _engine_with_transition(zones)
        opps = generate_transition_opportunities(e.state, StructuralContext(nearest_teammate_id="2"), ts)
        perceived = perceive(opps, vision_latent_propensity=1.0, rng=random.Random(1))
        policy = SelectionPolicy(random.Random(1))
        intent = policy.select(perceived, RoleContext(), TendencyContext(),
                                ClockContext(shot_clock_remaining=e.state.shot_clock_remaining), "p1")
        self.assertIsNotNone(intent)

    def test_no_new_selection_engine_created(self):
        source = inspect.getsource(__import__("transition_offense"))
        self.assertNotIn("class SelectionPolicy", source)


class TestRoleTendencyAuthority(unittest.TestCase):
    def test_role_off_initiation_boosts_transition_push(self):
        low = _score_action(ActionType.TRANSITION_PUSH, RoleContext(role_off_initiation=1.0), TendencyContext())
        high = _score_action(ActionType.TRANSITION_PUSH, RoleContext(role_off_initiation=10.0), TendencyContext())
        self.assertGreater(high, low)

    def test_pass_vs_shoot_boosts_outlet_pass(self):
        low = _score_action(ActionType.OUTLET_PASS, RoleContext(), TendencyContext(pass_vs_shoot=-0.5))
        high = _score_action(ActionType.OUTLET_PASS, RoleContext(), TendencyContext(pass_vs_shoot=0.5))
        self.assertGreater(high, low)

    def test_drive_aggression_boosts_early_attack(self):
        low = _score_action(ActionType.DRIVE, RoleContext(), TendencyContext(drive_aggression=-0.5))
        high = _score_action(ActionType.DRIVE, RoleContext(), TendencyContext(drive_aggression=0.5))
        self.assertGreater(high, low)


class TestAbilityFirewalls(unittest.TestCase):
    def test_no_ability_symbol_anywhere(self):
        import transition_offense as to
        forbidden = ("passing_accuracy", "rim_finishing", "three_point", "rim_protection",
                     "defensive_playmaking", "poa_containment")
        for name in vars(to):
            if name.startswith("__"):
                continue
            for bad in forbidden:
                self.assertNotIn(bad, name.lower())

    def test_outlet_opportunity_generation_has_no_ability_parameter(self):
        sig = inspect.signature(generate_transition_opportunities)
        forbidden = ("passing_accuracy", "ability")
        for name in sig.parameters:
            for bad in forbidden:
                self.assertNotIn(bad, name.lower())


class TestPassResolverReuse(unittest.TestCase):
    def test_no_duplicate_pass_resolver(self):
        source = inspect.getsource(__import__("transition_offense"))
        self.assertNotIn("def resolve_pass", source)
        self.assertNotIn("class PassResolutionContext", source)

    def test_outlet_pass_is_a_real_pass_action_type(self):
        self.assertIn(ActionType.OUTLET_PASS, PASS_ACTIONS)


class TestShotDriveHandoffReuse(unittest.TestCase):
    def test_no_duplicate_shot_or_drive_resolver(self):
        source = inspect.getsource(__import__("transition_offense"))
        for bad in ("def resolve_shot", "def resolve_interior_shot", "def resolve_drive"):
            self.assertNotIn(bad, source)


class TestTransitionDecay(unittest.TestCase):
    def test_decay_reduces_advantage_over_time(self):
        e, ts = _engine_with_transition([FloorPlayer("1", SpatialZone.LEFT_WING, "DEFENSE")])
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 1.0})
        before = dict(e.advantage.magnitudes)
        decay_transition(e, dt=1.0)
        self.assertLess(sum(e.advantage.magnitudes.values()), sum(before.values()))

    def test_decay_eventually_reaches_halfcourt_set(self):
        e, ts = _engine_with_transition()
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 1.0})
        reached = False
        for _ in range(200):
            reached = decay_transition(e, dt=1.0)
            if reached:
                break
        self.assertTrue(reached)
        self.assertEqual(e.state.phase, PossessionPhase.HALFCOURT)

    def test_decay_does_not_end_possession(self):
        e, ts = _engine_with_transition()
        possession_id_before = e.state.possession_id
        offense_before = e.state.offense_team_id
        carrier_before = e.state.ball_carrier
        for _ in range(50):
            decay_transition(e, dt=1.0)
        self.assertEqual(e.state.possession_id, possession_id_before)
        self.assertEqual(e.state.offense_team_id, offense_before)
        self.assertEqual(e.state.ball_carrier, carrier_before)

    def test_decay_does_not_reset_shot_clock(self):
        e, ts = _engine_with_transition()
        e.state.shot_clock_remaining = 18.0
        decay_transition(e, dt=1.0)
        self.assertEqual(e.state.shot_clock_remaining, 18.0)

    def test_no_advantage_means_immediate_halfcourt_set(self):
        e, ts = _engine_with_transition()
        e.advantage = None
        reached = decay_transition(e, dt=0.0)
        self.assertTrue(reached)
        self.assertEqual(e.state.phase, PossessionPhase.HALFCOURT)


class TestHalfcourtSetTransition(unittest.TestCase):
    def test_halfcourt_set_preserves_ball_carrier_and_zones(self):
        e, ts = _engine_with_transition()
        e.advantage = None
        decay_transition(e, dt=0.0)
        self.assertEqual(e.state.ball_carrier, "9")
        self.assertEqual(e.state.ball_zone, SpatialZone.BACKCOURT)


class TestPossessionShotClockContinuity(unittest.TestCase):
    def test_possession_id_unchanged_through_transition_lifecycle(self):
        e, ts = _engine_with_transition()
        pid = e.state.possession_id
        generate_transition_opportunities(e.state, StructuralContext(), ts)
        decay_transition(e, dt=1.0)
        self.assertEqual(e.state.possession_id, pid)


class TestDeterminism(unittest.TestCase):
    def test_deterministic_opportunity_generation(self):
        zones = [FloorPlayer("9", SpatialZone.BACKCOURT, "OFFENSE"), FloorPlayer("2", SpatialZone.TOP_OF_KEY, "OFFENSE")]
        e1, ts1 = _engine_with_transition(list(zones))
        e2, ts2 = _engine_with_transition(list(zones))
        opps1 = generate_transition_opportunities(e1.state, StructuralContext(), ts1)
        opps2 = generate_transition_opportunities(e2.state, StructuralContext(), ts2)
        self.assertEqual([o.action_type for o in opps1], [o.action_type for o in opps2])

    def test_no_global_rng(self):
        source = inspect.getsource(__import__("transition_offense"))
        self.assertNotIn("random.random(", source)


class TestHistoricalFallback(unittest.TestCase):
    def test_works_in_classic_era(self):
        e = PossessionEngine("p1", "A", "B", season="1996-97", rng_seed=1)
        ts = initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.BACKCOURT)
        opps = generate_transition_opportunities(e.state, StructuralContext(), ts)
        self.assertTrue(len(opps) > 0)


class TestPlayerIdOnly(unittest.TestCase):
    def test_outlet_target_uses_real_player_id(self):
        zones = [FloorPlayer("9", SpatialZone.BACKCOURT, "OFFENSE"), FloorPlayer("2", SpatialZone.TOP_OF_KEY, "OFFENSE")]
        e, ts = _engine_with_transition(zones)
        opps = generate_transition_opportunities(e.state, StructuralContext(), ts)
        outlet = next(o for o in opps if o.action_type == ActionType.OUTLET_PASS)
        self.assertTrue(outlet.target_player_id.isdigit())


if __name__ == "__main__":
    unittest.main()
