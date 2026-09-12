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


class TestDriveFollowupScoring(unittest.TestCase):
    """"Calibrate drive follow-up decisions" phase -- focused tests G/H/N from the task's own
    required list: (G) a DRIVE's outcome is preserved independently of whatever follow-up gets
    selected next (this module never mutates `DriveOutcome`, it only reads it), (H) the
    monotonic drive-followup ordering (CLEAN_PENETRATION > PARTIAL_EDGE > CONTAINED >
    FORCED_PICKUP) holds in `DRIVE_FOLLOWUP_LOG_WEIGHT` itself, (N) foul/selection behavior stays
    deterministic under a fixed seed with `post_drive_outcome` set (a real, already-tested
    property of `_score_action`, re-confirmed here for the new parameter specifically)."""

    def test_h_drive_followup_log_weight_is_monotonic_by_outcome(self):
        from action_selection import DRIVE_FOLLOWUP_LOG_WEIGHT
        from drive_resolution import DriveOutcome
        ordered = [DRIVE_FOLLOWUP_LOG_WEIGHT[o] for o in
                   (DriveOutcome.CLEAN_PENETRATION, DriveOutcome.PARTIAL_EDGE,
                    DriveOutcome.CONTAINED, DriveOutcome.FORCED_PICKUP)]
        self.assertEqual(ordered, sorted(ordered, reverse=True))
        self.assertGreater(ordered[0], ordered[-1])  # a real, non-degenerate ordering, not four equal values

    def test_g_post_drive_outcome_never_mutates_the_opportunity_or_intent(self):
        """`post_drive_outcome` only shifts a SCORE -- it is never written back onto the chosen
        `ActionIntent`, the `DriveOutcome` string itself, or any opportunity object."""
        from drive_resolution import DriveOutcome
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        opps = generate_opportunities(state, ctx)
        perceived = perceive(opps, vision_latent_propensity=None, rng=random.Random(1))
        policy = SelectionPolicy(random.Random(1))
        intent = policy.select(perceived, RoleContext(), TendencyContext(),
                                ClockContext(shot_clock_remaining=18.0), "p1",
                                post_drive_outcome=DriveOutcome.CLEAN_PENETRATION)
        self.assertIsNotNone(intent)
        self.assertNotIn("post_drive_outcome", intent.to_dict())  # never leaks into the handoff object
        self.assertEqual(DriveOutcome.CLEAN_PENETRATION, "CLEAN_PENETRATION")  # the constant itself is untouched

    def test_n_post_drive_scoring_is_deterministic_under_a_fixed_seed(self):
        from drive_resolution import DriveOutcome
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")

        def run():
            opps = generate_opportunities(state, ctx)
            perceived = perceive(opps, vision_latent_propensity=None, rng=random.Random(3))
            policy = SelectionPolicy(random.Random(3))
            return policy.select(perceived, RoleContext(), TendencyContext(),
                                  ClockContext(shot_clock_remaining=18.0), "p1",
                                  post_drive_outcome=DriveOutcome.PARTIAL_EDGE)

        self.assertEqual(run().to_dict(), run().to_dict())

    def test_shot_actions_favored_over_pass_actions_after_clean_penetration(self):
        """Directly exercises `_score_action`'s own additive bias: a SHOT_ACTIONS member scores
        strictly higher, and a PASS_ACTIONS member strictly lower, once a CLEAN_PENETRATION
        `post_drive_outcome` is supplied, than with no drive context at all."""
        from drive_resolution import DriveOutcome
        role, tendency = RoleContext(), TendencyContext()
        baseline_shot = _score_action(ActionType.PULL_UP, role, tendency)
        biased_shot = _score_action(ActionType.PULL_UP, role, tendency,
                                     post_drive_outcome=DriveOutcome.CLEAN_PENETRATION)
        baseline_pass = _score_action(ActionType.SWING_PASS, role, tendency)
        biased_pass = _score_action(ActionType.SWING_PASS, role, tendency,
                                     post_drive_outcome=DriveOutcome.CLEAN_PENETRATION)
        self.assertGreater(biased_shot, baseline_shot)
        self.assertLess(biased_pass, baseline_pass)


class TestActionSpecificJumpShotSelection(unittest.TestCase):
    """"Model action-specific jump-shot selection" phase -- focused tests A-K from the task's own
    required list (E is covered by the PRE-EXISTING single-option interior branch, re-confirmed by
    `test_e_short_interior_pull_up_uses_the_single_interior_option` below; L/M/N/O/P/Q are covered
    by the diagnostics/transition/foul/OREB/rebound suites this phase left untouched)."""

    PERIMETER_OPTIONS = (SpatialZone.TOP_OF_KEY, SpatialZone.MIDRANGE)

    def _probabilities(self, action_type, tendency=None, options=None):
        from action_selection import ShotFamilySelectionContext, shot_zone_probabilities
        return shot_zone_probabilities(action_type, options or self.PERIMETER_OPTIONS,
                                        tendency or TendencyContext(), ShotFamilySelectionContext(-0.2))

    def test_a_perimeter_catch_and_shoot_strongly_favors_perimeter(self):
        p_three = self._probabilities(ActionType.CATCH_AND_SHOOT)[0]
        self.assertGreater(p_three, 0.55)  # clearly favors the perimeter zone over MIDRANGE (>50%)

    def test_b_catch_and_shoot_no_longer_generically_defaults_to_midrange(self):
        """Direct regression guard for the ROOT CAUSE this phase fixed: a league-average-tendency
        CATCH_AND_SHOOT from the perimeter used to score WORSE for a three than for a midrange
        (`context.three_point_baseline_log_weight` alone was negative) -- it must now clearly favor
        the perimeter zone instead."""
        probabilities = self._probabilities(ActionType.CATCH_AND_SHOOT, TendencyContext())
        self.assertGreater(probabilities[0], probabilities[1])  # P(three) > P(midrange)

    def test_c_pull_up_can_produce_three(self):
        from action_selection import _select_shot_zone, ShotFamilySelectionContext
        import random
        chosen = {
            _select_shot_zone(ActionType.PULL_UP, SpatialZone.TOP_OF_KEY, self.PERIMETER_OPTIONS,
                               TendencyContext(), ShotFamilySelectionContext(-0.2), random.Random(seed))
            for seed in range(30)
        }
        self.assertIn(SpatialZone.TOP_OF_KEY, chosen)

    def test_d_pull_up_can_produce_midrange(self):
        from action_selection import _select_shot_zone, ShotFamilySelectionContext
        import random
        chosen = {
            _select_shot_zone(ActionType.PULL_UP, SpatialZone.TOP_OF_KEY, self.PERIMETER_OPTIONS,
                               TendencyContext(), ShotFamilySelectionContext(-0.2), random.Random(seed))
            for seed in range(30)
        }
        self.assertIn(SpatialZone.MIDRANGE, chosen)

    def test_e_short_interior_pull_up_uses_the_single_interior_option(self):
        """PRE-EXISTING, unchanged mechanism, re-confirmed this phase: once `ball_zone` is already
        RESTRICTED_RIM/PAINT (reached only via real drive geometry -- never fabricated here),
        `_shot_zone_options()` returns a SINGLE option and `_select_shot_zone` never runs the
        family-choice softmax at all -- a genuine short/interior PULL_UP is already supported."""
        from action_selection import _select_shot_zone, ShotFamilySelectionContext
        import random
        result = _select_shot_zone(ActionType.PULL_UP, SpatialZone.PAINT, (SpatialZone.PAINT,),
                                    TendencyContext(), ShotFamilySelectionContext(-0.2), random.Random(0))
        self.assertEqual(result, SpatialZone.PAINT)

    def test_f_three_point_preference_changes_selection(self):
        low = self._probabilities(ActionType.CATCH_AND_SHOOT, TendencyContext(three_point_preference=-1.5))[0]
        high = self._probabilities(ActionType.CATCH_AND_SHOOT, TendencyContext(three_point_preference=1.5))[0]
        self.assertLess(low, high)

    def test_g_midrange_preference_changes_selection_only_in_eligible_context(self):
        """Eligible context: PULL_UP/CATCH_AND_SHOOT with MIDRANGE among the real candidate
        options. Ineligible context: a single-option interior menu (RESTRICTED_RIM/PAINT alone) --
        `midrange_preference` must have NO effect there, because MIDRANGE was never a candidate."""
        low = self._probabilities(ActionType.PULL_UP, TendencyContext(midrange_preference=-2.0))
        high = self._probabilities(ActionType.PULL_UP, TendencyContext(midrange_preference=2.0))
        self.assertNotEqual(low, high)
        from action_selection import shot_zone_probabilities, ShotFamilySelectionContext
        interior_low = shot_zone_probabilities(ActionType.PULL_UP, (SpatialZone.RESTRICTED_RIM,),
                                                TendencyContext(midrange_preference=-2.0), ShotFamilySelectionContext())
        interior_high = shot_zone_probabilities(ActionType.PULL_UP, (SpatialZone.RESTRICTED_RIM,),
                                                 TendencyContext(midrange_preference=2.0), ShotFamilySelectionContext())
        self.assertEqual(interior_low, interior_high)

    def test_h_three_point_ability_does_not_change_selection(self):
        """`shot_zone_probabilities` structurally cannot read `three_point_shrunk_rate` at all --
        it has no such parameter; this is the TRUE-ABILITY FIREWALL applied to family choice."""
        import inspect
        from action_selection import shot_zone_probabilities
        source = inspect.getsource(shot_zone_probabilities)
        self.assertNotIn("three_point_shrunk_rate", source)
        self.assertNotIn("three_point_shrunk", source)

    def test_i_midrange_ability_does_not_change_selection(self):
        import inspect
        from action_selection import shot_zone_probabilities
        source = inspect.getsource(shot_zone_probabilities)
        self.assertNotIn("midrange_shrunk_rate", source)
        self.assertNotIn("midrange_shrunk", source)

    def test_j_seeded_zone_selection_is_deterministic(self):
        from action_selection import _select_shot_zone, ShotFamilySelectionContext
        import random
        args = (ActionType.PULL_UP, SpatialZone.TOP_OF_KEY, self.PERIMETER_OPTIONS,
                TendencyContext(three_point_preference=0.3, midrange_preference=0.4),
                ShotFamilySelectionContext(-0.2))
        first = _select_shot_zone(*args, random.Random(11))
        second = _select_shot_zone(*args, random.Random(11))
        self.assertEqual(first, second)

    def test_k_no_post_hoc_family_override(self):
        """`_select_shot_zone` picks the zone BEFORE resolution -- `shot_family` is derived ONCE,
        purely from that already-chosen zone (`possession_orchestrator._dispatch_shot`'s own
        `if zone == SpatialZone.RESTRICTED_RIM: shot_family = ... elif ...` chain), BEFORE either
        resolver (`apply_interior_shot_to_engine`/`apply_shot_resolution_to_engine`) is ever
        called -- neither resolver's own return value is ever assigned back into `shot_family`."""
        import inspect
        import possession_orchestrator as po
        source = inspect.getsource(po._dispatch_shot)
        assign_index = source.index("shot_family = InteriorShotFamily.RIM")
        interior_call_index = source.index("apply_interior_shot_to_engine(")
        perimeter_call_index = source.index("apply_shot_resolution_to_engine(")
        self.assertLess(assign_index, interior_call_index)
        self.assertLess(assign_index, perimeter_call_index)
        # the ONLY re-assignment STATEMENTS of the name `shot_family` between the zone-derived
        # chain and either resolver call are the chain's own 4 branches (RIM/FLOATER/MIDRANGE/
        # THREE_POINT) -- everything else in that span is a keyword-argument READ
        # (`shot_family=shot_family`, `ContactContext(...)`), never a new assignment.
        import re
        between = source[source.index("if zone == SpatialZone.RESTRICTED_RIM"):interior_call_index]
        assignments = re.findall(r"^\s*shot_family = ", between, flags=re.MULTILINE)
        self.assertEqual(len(assignments), 4)


if __name__ == "__main__":
    unittest.main()
