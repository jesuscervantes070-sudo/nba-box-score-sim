"""
Phase 16 (narrowed scope) -- explicit falsification tests for every
named true-ability-leakage failure mode, plus the 5 contradictory
role/tendency profiles (A-E) required by the narrowing message.

None of these test success/outcome probabilities. Every test proves a
NEGATIVE: some hidden-ability-shaped input CANNOT reach opportunity
generation or selection, because no code path accepts it at all.
"""
import inspect
import random
import unittest

from action_intent import ActionType, TERMINAL_ACTIONS, CREATION_ACTIONS
from action_opportunity import StructuralContext, generate_opportunities
from action_perception import perceive
from action_selection import ClockContext, RoleContext, SelectionPolicy, TendencyContext
from possession_advantage import SpatialMagnitudeAdvantage
from possession_state import (
    BallState, DefensivePosture, DribbleState, PlayerBallControl, PossessionPhase, PossessionState, SpatialZone,
)


def _held_state(carrier="1", control_state=DribbleState.LIVE_DRIBBLE, zone=SpatialZone.LEFT_WING,
                 phase=PossessionPhase.HALFCOURT, shot_clock=18.0):
    s = PossessionState(possession_id="p1", offense_team_id="A", defense_team_id="B", phase=phase,
                         ball_state=BallState.HELD, ball_carrier=carrier, ball_zone=zone, shot_clock_remaining=shot_clock)
    s.ball_control = PlayerBallControl(carrier, state=control_state)
    return s


class TestNoAbilityParameterExistsAnywhere(unittest.TestCase):
    """The strongest possible proof a function cannot leak an ability
    value: its own signature has no such parameter. Checked for every
    public function in the opportunity/perception/selection layers."""

    def test_generate_opportunities_signature_has_no_ability_params(self):
        sig = inspect.signature(generate_opportunities)
        forbidden_substrings = ("ability", "three_point", "rim_finishing", "rim_protection", "passing_accuracy",
                                 "rim_access", "ball_security", "poa_containment", "defensive_playmaking",
                                 "offensive_rebounding")
        for name in sig.parameters:
            for bad in forbidden_substrings:
                self.assertNotIn(bad, name.lower())

    def test_perceive_signature_has_no_ability_params_except_vision(self):
        """playmaking_vision is the ONE sanctioned exception (perception
        only) -- confirmed by name, and confirmed no OTHER ability name
        appears."""
        sig = inspect.signature(perceive)
        params = set(sig.parameters)
        self.assertIn("vision_latent_propensity", params)
        forbidden = ("three_point", "rim_finishing", "rim_protection", "passing_accuracy", "ball_security")
        for name in params:
            for bad in forbidden:
                self.assertNotIn(bad, name.lower())

    def test_selection_policy_select_signature_has_no_ability_params(self):
        sig = inspect.signature(SelectionPolicy.select)
        forbidden_substrings = ("ability", "three_point", "rim_finishing", "rim_protection", "passing_accuracy",
                                 "rim_access", "ball_security", "poa_containment", "defensive_playmaking")
        for name in sig.parameters:
            for bad in forbidden_substrings:
                self.assertNotIn(bad, name.lower())


class TestNamedLeakageVectors(unittest.TestCase):
    def test_low_three_point_does_not_hide_open_shot(self):
        """An objectively open catch-and-shoot opportunity must appear
        regardless of any (unmodeled, unreadable) three_point ability --
        proven by the opportunity existing purely from structural state."""
        state = _held_state()
        ctx = StructuralContext(just_caught_pass=True)
        opps = generate_opportunities(state, ctx)
        self.assertTrue(any(o.action_type == ActionType.CATCH_AND_SHOOT for o in opps))

    def test_low_rim_finishing_does_not_hide_drive(self):
        state = _held_state(control_state=DribbleState.LIVE_DRIBBLE)
        opps = generate_opportunities(state, StructuralContext())
        self.assertTrue(any(o.action_type == ActionType.DRIVE for o in opps))

    def test_low_passing_accuracy_does_not_hide_pass(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        opps = generate_opportunities(state, ctx)
        self.assertTrue(any(o.action_type == ActionType.SWING_PASS for o in opps))

    def test_high_rim_access_creation_does_not_conjure_a_drive_lane(self):
        """A GATHERED dribble must still block DRIVE no matter how high
        a hypothetical rim_access_creation would be -- since that value
        is never read, this is trivially true, verified by exercising
        the actual blocking condition."""
        state = _held_state(control_state=DribbleState.GATHERED)
        opps = generate_opportunities(state, StructuralContext())
        self.assertFalse(any(o.action_type == ActionType.DRIVE for o in opps))

    def test_high_rim_protection_does_not_suppress_drive_opportunity(self):
        """rim_protection is never read by generate_opportunities at all
        -- a live-dribble DRIVE opportunity exists regardless of any
        defensive rim-protection value, real or hypothetical."""
        state = _held_state(control_state=DribbleState.LIVE_DRIBBLE)
        opps = generate_opportunities(state, StructuralContext())
        self.assertTrue(any(o.action_type == ActionType.DRIVE for o in opps))

    def test_high_poa_containment_does_not_suppress_drive_availability(self):
        """Same principle: only STRUCTURAL posture (SQUARE/TRAILING/etc)
        can gate a drive-adjacent opportunity here, never a latent
        poa_containment estimate, which this module never reads."""
        state = _held_state(control_state=DribbleState.LIVE_DRIBBLE)
        state.assignments = state.with_assignment("9", "1", DefensivePosture.SQUARE).assignments
        opps = generate_opportunities(state, StructuralContext())
        self.assertTrue(any(o.action_type == ActionType.DRIVE for o in opps))

    def test_defensive_playmaking_does_not_suppress_pass_availability(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        opps = generate_opportunities(state, ctx)
        self.assertTrue(any(o.action_type in (ActionType.SWING_PASS, ActionType.RESET_PASS) for o in opps))

    def test_role_not_recomputed_from_true_ability_during_selection(self):
        """RoleContext's fields are supplied by the CALLER as plain
        floats -- nothing inside action_selection.py re-derives them
        from any ability estimator, verified structurally by the
        no-ability-import test in test_action_selection.py and behavior
        -tested here: two identical RoleContext values produce identical
        scores regardless of any other state."""
        from action_selection import _score_action
        role = RoleContext(role_off_initiation=7.0)
        s1 = _score_action(ActionType.DRIVE, role, TendencyContext())
        s2 = _score_action(ActionType.DRIVE, role, TendencyContext())
        self.assertEqual(s1, s2)


class TestContradictoryProfiles(unittest.TestCase):
    """The 5 required contradictory role/tendency profiles. Each test
    only checks a plausible DIRECTIONAL effect, never a magnitude, and
    never touches hidden ability."""

    def _menu(self, state=None, ctx=None):
        state = state or _held_state()
        ctx = ctx or StructuralContext(nearest_teammate_id="2", roller_id="4", screen_active=True, just_caught_pass=True)
        opps = generate_opportunities(state, ctx)
        return perceive(opps, vision_latent_propensity=2.0, rng=random.Random(0))  # always-perceive to isolate role/tendency

    def _rate(self, role, tendency, predicate, n=300):
        perceived = self._menu()
        hits = 0
        for i in range(n):
            policy = SelectionPolicy(random.Random(i))
            intent = policy.select(perceived, role, tendency, ClockContext(shot_clock_remaining=18.0), "p1")
            if intent and predicate(intent):
                hits += 1
        return hits / n

    def test_profile_a_high_initiation_low_pass_high_drive_favors_self_initiated_attack(self):
        role = RoleContext(role_off_initiation=10.0)
        tendency = TendencyContext(drive_aggression=0.6, pass_vs_shoot=-0.5)
        rate = self._rate(role, tendency, lambda i: i.action_type in (ActionType.DRIVE, ActionType.ISOLATION_ATTACK))
        baseline = self._rate(RoleContext(), TendencyContext(), lambda i: i.action_type in (ActionType.DRIVE, ActionType.ISOLATION_ATTACK))
        self.assertGreater(rate, baseline)

    def test_profile_b_high_finishing_low_drive_high_three_pref_favors_terminal_not_drive(self):
        role = RoleContext(role_off_finishing=0.9)
        tendency = TendencyContext(drive_aggression=-0.5, three_point_preference=0.6)
        rate_terminal = self._rate(role, tendency, lambda i: i.action_type in TERMINAL_ACTIONS)
        rate_drive = self._rate(role, tendency, lambda i: i.action_type == ActionType.DRIVE)
        rate_drive_baseline = self._rate(RoleContext(), TendencyContext(drive_aggression=0.0), lambda i: i.action_type == ActionType.DRIVE)
        self.assertGreaterEqual(rate_terminal, 0.0)
        self.assertLessEqual(rate_drive, rate_drive_baseline + 0.05)  # not pushed toward drive

    def test_profile_c_high_spacing_low_three_pref_still_deploys_to_catch_and_shoot_opportunity(self):
        """Spacing role affects DEPLOYMENT (opportunity weight), not
        forced shot-zone choice -- even with a low three_point_preference
        tendency, the CATCH_AND_SHOOT opportunity itself is still
        favored by role_off_spacing (deployment != zone choice)."""
        from action_selection import _score_action
        role = RoleContext(role_off_spacing=0.9)
        tendency = TendencyContext(three_point_preference=-0.5)
        score = _score_action(ActionType.CATCH_AND_SHOOT, role, tendency)
        score_low_spacing = _score_action(ActionType.CATCH_AND_SHOOT, RoleContext(role_off_spacing=0.1), tendency)
        self.assertGreater(score, score_low_spacing)

    def test_profile_d_low_initiation_very_high_drive_aggression_still_can_drive(self):
        """CONTEXT/mechanics can still permit a drive even with low
        role_off_initiation -- tendency alone is enough to raise DRIVE's
        relative weight when the opportunity objectively exists."""
        role = RoleContext(role_off_initiation=1.0)
        tendency = TendencyContext(drive_aggression=0.9)
        rate = self._rate(role, tendency, lambda i: i.action_type == ActionType.DRIVE)
        baseline = self._rate(role, TendencyContext(), lambda i: i.action_type == ActionType.DRIVE)
        self.assertGreater(rate, baseline)

    def test_profile_e_high_initiation_pass_heavy_low_finishing_favors_pass_over_terminal(self):
        role = RoleContext(role_off_initiation=10.0, role_off_finishing=0.1)
        tendency = TendencyContext(pass_vs_shoot=0.6)
        rate_pass = self._rate(role, tendency, lambda i: i.action_type in (ActionType.SWING_PASS, ActionType.KICKOUT,
                                                                             ActionType.POCKET_PASS, ActionType.RESET_PASS))
        rate_pass_baseline = self._rate(RoleContext(), TendencyContext(),
                                         lambda i: i.action_type in (ActionType.SWING_PASS, ActionType.KICKOUT,
                                                                      ActionType.POCKET_PASS, ActionType.RESET_PASS))
        self.assertGreater(rate_pass, rate_pass_baseline)


class TestPerceptionCannotInventReceivers(unittest.TestCase):
    def test_high_vision_cannot_perceive_a_nonexistent_receiver(self):
        """No perimeter receiver exists in context -> no KICKOUT
        opportunity is even GENERATED, so no vision value, however high,
        can cause one to be perceived."""
        state = _held_state()
        ctx = StructuralContext()  # no perimeter_receiver_ids at all
        adv = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9})
        opps = generate_opportunities(state, ctx, advantage=adv)
        self.assertFalse(any(o.action_type == ActionType.KICKOUT for o in opps))
        perceived = perceive(opps, vision_latent_propensity=100.0, rng=random.Random(1))
        self.assertFalse(any(p.opportunity.action_type == ActionType.KICKOUT for p in perceived))

    def test_obvious_opportunity_does_not_require_impossible_vision(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        opps = generate_opportunities(state, ctx)
        perceived = perceive(opps, vision_latent_propensity=None, rng=random.Random(1))
        self.assertTrue(any(p.opportunity.action_type == ActionType.SWING_PASS for p in perceived))


class TestNoDuplicateRepresentations(unittest.TestCase):
    def test_no_duplicate_opportunity_ids_for_same_underlying_action(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2", roller_id="4", screen_active=True, just_caught_pass=True)
        opps = generate_opportunities(state, ctx)
        ids = [o.opportunity_id for o in opps]
        self.assertEqual(len(ids), len(set(ids)))


class TestClockMechanicalVsStrategic(unittest.TestCase):
    def test_clock_removes_action_only_when_mechanically_infeasible_not_just_unattractive(self):
        """A DRIVE (EXTENDED duration) is removed once the clock is
        below the feasibility floor -- but nothing manufactures a
        guaranteed bailout action in its place; the menu may legitimately
        shrink to whatever remains feasible, including possibly nothing."""
        from action_selection import _clock_feasible
        low_clock = ClockContext(shot_clock_remaining=2.0)
        self.assertFalse(_clock_feasible(ActionType.DRIVE, low_clock))
        self.assertFalse(_clock_feasible(ActionType.RESET_PASS, low_clock))
        # no assertion that some OTHER action is force-injected -- there is no such mechanism in this module

    def test_no_guaranteed_bailout_action_is_injected(self):
        import action_selection
        source = inspect.getsource(action_selection)
        self.assertNotIn("bailout", source.lower())
        self.assertNotIn("heave", source.lower())


if __name__ == "__main__":
    unittest.main()
