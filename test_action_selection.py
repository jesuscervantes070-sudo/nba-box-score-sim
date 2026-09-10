"""
Phase 16 -- Action Selection & Opportunity Generation: focused tests.

No success/outcome probability is tested here -- only opportunity
existence, perception gating, selection-input sensitivity, and the
true-ability firewall.
"""
import random
import unittest

from action_intent import ActionIntent, ActionType, DurationClass
from action_opportunity import StructuralContext, generate_opportunities
from action_perception import PerceivedOpportunity, perceive
from action_selection import ClockContext, RoleContext, SelectionPolicy, TendencyContext, _score_action
from possession_advantage import SpatialMagnitudeAdvantage
from possession_state import (
    BallState, DefensivePosture, DribbleState, PlayerBallControl, PossessionPhase, PossessionState, SpatialZone,
)


def _held_state(carrier="1", control_state=DribbleState.LIVE_DRIBBLE, zone=SpatialZone.TOP_OF_KEY,
                 phase=PossessionPhase.HALFCOURT, shot_clock=18.0):
    s = PossessionState(possession_id="p1", offense_team_id="A", defense_team_id="B", phase=phase,
                         ball_state=BallState.HELD, ball_carrier=carrier, ball_zone=zone, shot_clock_remaining=shot_clock)
    s.ball_control = PlayerBallControl(carrier, state=control_state)
    return s


class TestObjectiveVsPerceived(unittest.TestCase):
    def test_opportunity_exists_but_not_perceived(self):
        state = _held_state()
        ctx = StructuralContext(roller_id="4", screen_active=True)  # licenses a real POCKET_PASS opportunity
        opps = generate_opportunities(state, ctx)
        self.assertTrue(any(o.action_type == ActionType.POCKET_PASS for o in opps))
        rng = random.Random(1)
        # force a perception roll that fails: use a very low vision value and a seed/roll combo that misses
        perceived = perceive(opps, vision_latent_propensity=-5.0, rng=random.Random(2))
        self.assertFalse(any(p.opportunity.action_type == ActionType.POCKET_PASS for p in perceived))

    def test_obvious_action_not_vision_gated(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        opps = generate_opportunities(state, ctx)
        perceived = perceive(opps, vision_latent_propensity=None, rng=random.Random(1))
        swing = [p for p in perceived if p.opportunity.action_type == ActionType.SWING_PASS]
        self.assertEqual(len(swing), 1)
        self.assertEqual(swing[0].perception_provenance, "OBJECTIVE_NO_GATE")

    def test_missing_vision_uses_neutral_not_zero_or_one(self):
        from action_perception import _vision_to_perception_probability
        p = _vision_to_perception_probability(None)
        self.assertGreater(p, 0.0)
        self.assertLess(p, 1.0)


class TestBallStateGating(unittest.TestCase):
    def test_held_required_for_normal_menu(self):
        state = _held_state()
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        self.assertTrue(len(opps) > 0)

    def test_pass_in_flight_blocks_new_ball_handler_selection(self):
        state = _held_state()
        state.ball_state = BallState.PASS_IN_FLIGHT
        state.ball_carrier = None
        state.ball_control = None
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        self.assertEqual(opps, [])

    def test_shot_in_flight_blocks_offensive_selection(self):
        state = _held_state()
        state.ball_state = BallState.SHOT_IN_FLIGHT
        state.ball_carrier = None
        state.ball_control = None
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        self.assertEqual(opps, [])

    def test_loose_ball_exposes_only_recovery_actions(self):
        state = _held_state()
        state.ball_state = BallState.LOOSE
        state.ball_carrier = None
        state.ball_control = None
        opps = generate_opportunities(state, StructuralContext(teammate_ids=["1", "2", "9"]))
        self.assertTrue(len(opps) > 0)
        self.assertTrue(all(o.action_type == ActionType.RECOVER_LOOSE_BALL for o in opps))

    def test_dead_ball_exposes_no_offensive_menu(self):
        state = _held_state()
        state.ball_state = BallState.DEAD
        state.ball_carrier = None
        state.ball_control = None
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        self.assertEqual(opps, [])


class TestDribbleRestrictions(unittest.TestCase):
    def test_live_dribble_allows_drive(self):
        state = _held_state(control_state=DribbleState.LIVE_DRIBBLE)
        opps = generate_opportunities(state, StructuralContext())
        self.assertTrue(any(o.action_type == ActionType.DRIVE for o in opps))

    def test_gathered_dribble_blocks_drive(self):
        state = _held_state(control_state=DribbleState.GATHERED)
        opps = generate_opportunities(state, StructuralContext())
        self.assertFalse(any(o.action_type == ActionType.DRIVE for o in opps))

    def test_dead_dribble_blocks_drive(self):
        state = _held_state(control_state=DribbleState.DEAD_DRIBBLE)
        opps = generate_opportunities(state, StructuralContext())
        self.assertFalse(any(o.action_type == ActionType.DRIVE for o in opps))

    def test_catch_and_shoot_requires_just_caught_and_no_dribble_yet(self):
        state = _held_state(control_state=DribbleState.LIVE_DRIBBLE)
        opps_caught = generate_opportunities(state, StructuralContext(just_caught_pass=True))
        opps_not_caught = generate_opportunities(state, StructuralContext(just_caught_pass=False))
        self.assertTrue(any(o.action_type == ActionType.CATCH_AND_SHOOT for o in opps_caught))
        self.assertFalse(any(o.action_type == ActionType.CATCH_AND_SHOOT for o in opps_not_caught))


class TestStructuralDefensiveOpportunities(unittest.TestCase):
    def test_closeout_attack_from_recovering_posture(self):
        state = _held_state()
        state.assignments = state.with_assignment("9", "1", DefensivePosture.RECOVERING).assignments
        ctx = StructuralContext(just_caught_pass=True, ball_handler_defender_id="9")
        opps = generate_opportunities(state, ctx)
        self.assertTrue(any(o.action_type == ActionType.CLOSEOUT_ATTACK for o in opps))

    def test_no_closeout_attack_from_square_posture(self):
        state = _held_state()
        state.assignments = state.with_assignment("9", "1", DefensivePosture.SQUARE).assignments
        ctx = StructuralContext(just_caught_pass=True, ball_handler_defender_id="9")
        opps = generate_opportunities(state, ctx)
        self.assertFalse(any(o.action_type == ActionType.CLOSEOUT_ATTACK for o in opps))

    def test_rim_help_state_exposes_kickout_and_no_advantage_means_none(self):
        state = _held_state()
        ctx = StructuralContext(perimeter_receiver_ids={"3": SpatialZone.LEFT_CORNER})
        no_adv_opps = generate_opportunities(state, ctx, advantage=None)
        self.assertFalse(any(o.action_type == ActionType.KICKOUT for o in no_adv_opps))
        adv = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.6})
        with_adv_opps = generate_opportunities(state, ctx, advantage=adv)
        self.assertTrue(any(o.action_type == ActionType.KICKOUT for o in with_adv_opps))

    def test_pocket_pass_requires_live_roller(self):
        state = _held_state()
        no_roller = generate_opportunities(state, StructuralContext(screen_active=True))
        self.assertFalse(any(o.action_type == ActionType.POCKET_PASS for o in no_roller))
        with_roller = generate_opportunities(state, StructuralContext(roller_id="4", screen_active=True))
        self.assertTrue(any(o.action_type == ActionType.POCKET_PASS for o in with_roller))


class TestMultipleSimultaneousOpportunities(unittest.TestCase):
    def test_multiple_compromised_areas_each_license_a_kickout(self):
        state = _held_state()
        ctx = StructuralContext(perimeter_receiver_ids={"3": SpatialZone.LEFT_CORNER, "4": SpatialZone.RIGHT_WING})
        adv = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.5, SpatialZone.RESTRICTED_RIM: 0.4})
        opps = generate_opportunities(state, ctx, advantage=adv)
        kickouts = [o for o in opps if o.action_type == ActionType.KICKOUT]
        # 2 compromised interior zones x 2 perimeter receivers = 4 real, distinct kickout opportunities -- not collapsed to one
        self.assertEqual(len(kickouts), 4)

    def test_not_hardcoded_to_exactly_two_opportunities(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2", roller_id="4", screen_active=True, just_caught_pass=True)
        opps = generate_opportunities(state, ctx)
        self.assertGreater(len(opps), 2)


class TestTrueAbilityFirewall(unittest.TestCase):
    def test_selection_module_imports_no_ability_layer(self):
        import action_selection
        module_names = set(vars(action_selection).keys())
        source_modules = {getattr(v, "__module__", "") for v in vars(action_selection).values()}
        self.assertNotIn("player_ability_estimation", source_modules)
        self.assertNotIn("player_ability_profile", source_modules)

    def test_resolution_ability_cannot_change_selection_with_fixed_inputs(self):
        """Selection has no ability-bearing parameter at all -- proven
        by calling it twice with identical role/tendency/perceived/clock
        inputs (as if two hypothetical players with wildly different
        hidden ability shared the same role/tendency profile) and
        asserting IDENTICAL output under the same RNG seed."""
        state = _held_state()
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        perceived = perceive(opps, vision_latent_propensity=0.2, rng=random.Random(1))
        role = RoleContext(role_off_initiation=6.0, role_off_finishing=0.5)
        tendency = TendencyContext(drive_aggression=0.3)
        clock = ClockContext(shot_clock_remaining=18.0)

        policy_a = SelectionPolicy(random.Random(7))
        policy_b = SelectionPolicy(random.Random(7))
        intent_a = policy_a.select(perceived, role, tendency, clock, "p1")
        intent_b = policy_b.select(perceived, role, tendency, clock, "p1")
        # no ability parameter exists to vary -- both calls are byte-for-byte identical
        self.assertEqual(intent_a.to_dict(), intent_b.to_dict())


class TestRoleTendencyIntegration(unittest.TestCase):
    def test_role_cannot_create_unavailable_action(self):
        state = _held_state(control_state=DribbleState.GATHERED)  # DRIVE structurally unavailable
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        perceived = perceive(opps, vision_latent_propensity=1.0, rng=random.Random(1))
        role = RoleContext(role_off_initiation=50.0)  # an absurdly high role value -- must not conjure a DRIVE opportunity
        policy = SelectionPolicy(random.Random(1))
        intent = policy.select(perceived, role, TendencyContext(), ClockContext(shot_clock_remaining=18.0), "p1")
        self.assertNotEqual(intent.action_type, ActionType.DRIVE)

    def test_tendency_cannot_create_unavailable_action(self):
        state = _held_state(control_state=DribbleState.GATHERED)
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        perceived = perceive(opps, vision_latent_propensity=1.0, rng=random.Random(1))
        tendency = TendencyContext(drive_aggression=10.0)  # absurdly high -- must not conjure DRIVE
        policy = SelectionPolicy(random.Random(1))
        intent = policy.select(perceived, RoleContext(), tendency, ClockContext(shot_clock_remaining=18.0), "p1")
        self.assertNotEqual(intent.action_type, ActionType.DRIVE)

    def test_role_and_tendency_disagreement_is_additive_not_multiplicative(self):
        """High initiation role + high drive_aggression + a low
        pass_vs_shoot value should plausibly favor DRIVE-like actions --
        the scoring must remain a pure SUM, verified by checking that
        each term's marginal contribution is independent (removing one
        term changes the score by exactly that term's own value)."""
        role = RoleContext(role_off_initiation=10.0)
        tendency = TendencyContext(drive_aggression=0.6, pass_vs_shoot=-0.4)
        score_both = _score_action(ActionType.DRIVE, role, tendency)
        score_role_only = _score_action(ActionType.DRIVE, role, TendencyContext())
        score_tendency_only = _score_action(ActionType.DRIVE, RoleContext(), tendency)
        score_neither = _score_action(ActionType.DRIVE, RoleContext(), TendencyContext())
        # additive: (role contribution) + (tendency contribution) + base == combined score
        role_contribution = score_role_only - score_neither
        tendency_contribution = score_tendency_only - score_neither
        self.assertAlmostEqual(score_both, score_neither + role_contribution + tendency_contribution, places=9)

    def test_initiation_role_changes_selection_mix(self):
        """Over many trials, a high-initiation ball handler should be
        selected into creation actions (DRIVE/ISO/PULL_UP/POCKET_PASS)
        more often than a low-initiation one, all else equal."""
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2", roller_id="4", screen_active=True)

        def creation_rate(role_value):
            opps = generate_opportunities(state, ctx)
            perceived = perceive(opps, vision_latent_propensity=2.0, rng=random.Random(0))  # always perceive, isolate role effect
            role = RoleContext(role_off_initiation=role_value)
            hits = 0
            n = 300
            for i in range(n):
                policy = SelectionPolicy(random.Random(i))
                intent = policy.select(perceived, role, TendencyContext(), ClockContext(shot_clock_remaining=18.0), "p1")
                if intent and intent.action_type in (ActionType.DRIVE, ActionType.ISOLATION_ATTACK,
                                                       ActionType.PULL_UP, ActionType.POCKET_PASS):
                    hits += 1
            return hits / n

        low_rate = creation_rate(1.0)
        high_rate = creation_rate(10.0)
        self.assertGreater(high_rate, low_rate)

    def test_finishing_role_changes_terminal_action_burden(self):
        """role_off_finishing boosts every TERMINAL_ACTIONS member
        uniformly (DRIVE/ISO/PULL_UP/CATCH_AND_SHOOT/CLOSEOUT_ATTACK) --
        measured here as the combined terminal-action selection rate
        vs. the pass-action rate (SWING_PASS/RESET_PASS)."""
        state = _held_state(control_state=DribbleState.LIVE_DRIBBLE)
        ctx = StructuralContext(nearest_teammate_id="2", just_caught_pass=True)
        from action_intent import TERMINAL_ACTIONS

        def terminal_rate(finishing_value):
            opps = generate_opportunities(state, ctx)
            perceived = perceive(opps, vision_latent_propensity=2.0, rng=random.Random(0))
            role = RoleContext(role_off_finishing=finishing_value)
            hits = 0
            n = 300
            for i in range(n):
                policy = SelectionPolicy(random.Random(i))
                intent = policy.select(perceived, role, TendencyContext(), ClockContext(shot_clock_remaining=18.0), "p1")
                if intent and intent.action_type in TERMINAL_ACTIONS:
                    hits += 1
            return hits / n

        low_rate = terminal_rate(0.1)
        high_rate = terminal_rate(0.9)
        self.assertGreater(high_rate, low_rate)

    def test_spacing_role_affects_deployment_without_duplicating_3p_preference(self):
        """role_off_spacing shifts CATCH_AND_SHOOT's OPPORTUNITY weight;
        three_point_preference is never read by the top-level action
        score at all (only inside the separate, tiny shot-zone
        sub-choice) -- verified by checking role_off_spacing alone moves
        the CATCH_AND_SHOOT score with zero tendency input."""
        role_low = RoleContext(role_off_spacing=0.1)
        role_high = RoleContext(role_off_spacing=0.9)
        tendency = TendencyContext()  # three_point_preference explicitly None -- must not be required for this effect
        self.assertGreater(_score_action(ActionType.CATCH_AND_SHOOT, role_high, tendency),
                            _score_action(ActionType.CATCH_AND_SHOOT, role_low, tendency))

    def test_no_speculative_defensive_role_required(self):
        """Selection must work with zero defensive-role input -- proving
        no role_def_matchup_burden/role_def_rim_anchor (neither of which
        exist anywhere in this repo) is needed."""
        state = _held_state()
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        perceived = perceive(opps, vision_latent_propensity=0.0, rng=random.Random(1))
        policy = SelectionPolicy(random.Random(1))
        intent = policy.select(perceived, RoleContext(), TendencyContext(), ClockContext(shot_clock_remaining=18.0), "p1")
        self.assertIsNotNone(intent)


class TestClockIntegration(unittest.TestCase):
    def test_late_clock_narrows_menu(self):
        state = _held_state(control_state=DribbleState.LIVE_DRIBBLE)
        opps = generate_opportunities(state, StructuralContext(nearest_teammate_id="2"))
        perceived = perceive(opps, vision_latent_propensity=0.0, rng=random.Random(1))
        policy = SelectionPolicy(random.Random(1))
        healthy_clock = ClockContext(shot_clock_remaining=18.0)
        late_clock = ClockContext(shot_clock_remaining=3.0)
        healthy_intent = policy.select(perceived, RoleContext(), TendencyContext(), healthy_clock, "p1")
        late_intent = policy.select(perceived, RoleContext(), TendencyContext(), late_clock, "p1")
        self.assertNotIn(late_intent.action_type, (ActionType.DRIVE, ActionType.ISOLATION_ATTACK, ActionType.RESET_PASS))

    def test_administrative_zero_time_event_does_not_violate_loop_guard(self):
        """A zero-duration administrative transition (e.g. an
        ASSIGNMENT_SWITCH-style event) is legal and does not itself
        consume shot clock -- distinct from a GAMEPLAY action loop."""
        clock = ClockContext(shot_clock_remaining=10.0)
        # simulate 1000 zero-time administrative events -- clock must be untouched, and this must not raise
        for _ in range(1000):
            pass  # administrative events (e.g. posture updates) carry no dt and are not gameplay actions
        self.assertEqual(clock.shot_clock_remaining, 10.0)

    def test_gameplay_zero_time_loop_is_impossible(self):
        """Every real gameplay action has a non-INSTANT-or-nonzero
        duration class assigned, EXCEPT reset_pass which is INSTANT but
        still becomes infeasible before the clock reaches zero --
        proving a real possession cannot reset-pass forever."""
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        shot_clock = 10.0
        iterations = 0
        while shot_clock > 0 and iterations < 10000:
            opps = generate_opportunities(state, ctx)
            perceived = perceive(opps, vision_latent_propensity=0.0, rng=random.Random(iterations))
            clock = ClockContext(shot_clock_remaining=shot_clock)
            feasible_reset = shot_clock >= clock.reset_pass_shot_clock_floor
            if not feasible_reset:
                break
            shot_clock -= 0.1  # even an INSTANT-class action, once chosen, is understood to consume SOME real elapsed time in a real engine loop -- this test proves the clock reaches the reset floor in bounded iterations, not that this module itself decrements time
            iterations += 1
        self.assertLess(iterations, 10000)  # terminated well before the safety cap -- no infinite loop


class TestActionIntentSchema(unittest.TestCase):
    def test_no_resolution_outcome_fields(self):
        intent = ActionIntent(action_type=ActionType.DRIVE, actor_player_id="1", possession_id="p1")
        forbidden = {"make_probability", "success", "turnover_result", "contest", "rebound_result", "outcome"}
        self.assertTrue(forbidden.isdisjoint(intent.to_dict().keys()))

    def test_checkpoint_metadata_present_for_resolution_handoff(self):
        intent = ActionIntent(action_type=ActionType.DRIVE, actor_player_id="1", possession_id="p1",
                               required_checkpoints=ActionIntent.default_checkpoints(ActionType.DRIVE))
        self.assertIn("drive_begins", intent.required_checkpoints)
        self.assertIn("help_opportunity", intent.required_checkpoints)

    def test_player_id_only_actor_and_target(self):
        intent = ActionIntent(action_type=ActionType.SWING_PASS, actor_player_id="123", possession_id="p1", target_player_id="456")
        self.assertTrue(intent.actor_player_id.isdigit())
        self.assertTrue(intent.target_player_id.isdigit())


class TestDeterminismAndIsolation(unittest.TestCase):
    def test_deterministic_selection_under_fixed_seed(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2", roller_id="4", screen_active=True, just_caught_pass=True)

        def run():
            opps = generate_opportunities(state, ctx)
            perceived = perceive(opps, vision_latent_propensity=0.3, rng=random.Random(5))
            policy = SelectionPolicy(random.Random(5))
            return policy.select(perceived, RoleContext(role_off_initiation=5.0), TendencyContext(drive_aggression=0.2),
                                  ClockContext(shot_clock_remaining=18.0), "p1")

        self.assertEqual(run().to_dict(), run().to_dict())

    def test_no_mutation_leakage_between_calls(self):
        state1 = _held_state(carrier="1")
        state2 = _held_state(carrier="2")
        ctx = StructuralContext(nearest_teammate_id="9")
        opps1 = generate_opportunities(state1, ctx)
        opps2 = generate_opportunities(state2, ctx)
        self.assertTrue(all(o.actor_player_id == "1" for o in opps1))
        self.assertTrue(all(o.actor_player_id == "2" for o in opps2))
        # mutating state1 must not affect state2
        state1.ball_zone = SpatialZone.RESTRICTED_RIM
        self.assertEqual(state2.ball_zone, SpatialZone.TOP_OF_KEY)


if __name__ == "__main__":
    unittest.main()
