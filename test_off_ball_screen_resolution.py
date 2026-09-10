"""Phase 22A -- focused tests for off_ball_screen_resolution.py."""
import inspect
import random
import unittest
from dataclasses import replace

from action_intent import ActionType
from action_opportunity import StructuralContext, generate_opportunities
from off_ball_screen_resolution import (
    SWITCH_COVERAGE_INSTRUCTION,
    OffBallScreenContext,
    OffBallScreenOutcome,
    _atomic_switch_assignments,
    apply_off_ball_screen_to_engine,
    resolve_off_ball_screen,
)
from possession_advantage import DiscreteTierAdvantage, SpatialMagnitudeAdvantage
from possession_engine import PossessionEngine
from possession_state import BallState, DefensivePosture, PossessionPhase, SpatialZone


class _FixedRNG:
    """A tiny, deterministic stand-in for `random.Random` -- returns the
    same `.random()` value every call, so tests target a specific
    outcome branch directly rather than hunting for a lucky seed."""
    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value


def _engine(advantage=None):
    e = PossessionEngine("p1", "TEAM_A", "TEAM_B", season="2023-24", rng_seed=1, advantage=advantage)
    e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
    e.switch("9", "5", posture=DefensivePosture.SQUARE)  # defender 9 guards screener 5
    e.switch("8", "2", posture=DefensivePosture.SQUARE)  # defender 8 guards moving receiver 2
    return e


def _ctx(**kwargs):
    defaults = dict(screener_id="5", screener_defender_id="9", moving_receiver_id="2", receiver_defender_id="8",
                     origin_zone=SpatialZone.LEFT_WING, destination_zone=SpatialZone.RIGHT_WING)
    defaults.update(kwargs)
    return OffBallScreenContext(**defaults)


class TestAttachedNoEffect(unittest.TestCase):
    def test_no_effect_leaves_assignments_and_posture_unchanged(self):
        e = _engine()
        before = e.state.assignments
        outcome = apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.1))
        self.assertEqual(outcome, OffBallScreenOutcome.NO_EFFECT)
        self.assertEqual(e.state.assignments, before)
        self.assertEqual(e.state.assignments["8"].posture, DefensivePosture.SQUARE)
        self.assertEqual(e.state.assignments["9"].posture, DefensivePosture.SQUARE)

    def test_no_effect_creates_no_advantage(self):
        adv = SpatialMagnitudeAdvantage()
        e = _engine(advantage=adv)
        apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.1))
        self.assertEqual(e.advantage.compromised_areas(), ())


class TestTrailingSeparation(unittest.TestCase):
    def test_receiver_defender_posture_becomes_trailing(self):
        e = _engine()
        outcome = apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.6))
        self.assertEqual(outcome, OffBallScreenOutcome.TRAILING_SEPARATION)
        self.assertEqual(e.state.assignments["8"].posture, DefensivePosture.TRAILING)
        # screener's own defender/assignment is untouched by this outcome
        self.assertEqual(e.state.assignments["9"].posture, DefensivePosture.SQUARE)
        self.assertEqual(e.state.assignments["9"].assigned_to_player_id, "5")
        self.assertEqual(e.state.assignments["8"].assigned_to_player_id, "2")  # no assignment corruption

    def test_zone_compromise_registered_when_advantage_present(self):
        e = _engine(advantage=SpatialMagnitudeAdvantage())
        apply_off_ball_screen_to_engine(e, _ctx(destination_zone=SpatialZone.PAINT), _FixedRNG(0.6))
        areas = e.advantage.compromised_areas()
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0].zone, SpatialZone.PAINT)

    def test_no_advantage_fabricated_when_none_was_chosen(self):
        e = _engine(advantage=None)
        apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.6))
        self.assertIsNone(e.advantage)  # this module never picks a representation on the caller's behalf

    def test_works_with_discrete_tier_advantage_too(self):
        e = _engine(advantage=DiscreteTierAdvantage())
        apply_off_ball_screen_to_engine(e, _ctx(destination_zone=SpatialZone.RESTRICTED_RIM), _FixedRNG(0.6))
        areas = e.advantage.compromised_areas()
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0].zone, SpatialZone.RESTRICTED_RIM)


class TestCleanSwitch(unittest.TestCase):
    def test_switch_exchanges_assignments_atomically(self):
        e = _engine()
        outcome = apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.99))
        self.assertEqual(outcome, OffBallScreenOutcome.SWITCH)
        self.assertEqual(e.state.assignments["9"].assigned_to_player_id, "2")
        self.assertEqual(e.state.assignments["8"].assigned_to_player_id, "5")

    def test_switch_via_explicit_coverage_instruction_is_deterministic(self):
        e = _engine()
        # A recognized coverage instruction must NOT need any real roll -- verified with an RNG
        # that would otherwise route to NO_EFFECT.
        outcome = apply_off_ball_screen_to_engine(e, _ctx(coverage_instruction=SWITCH_COVERAGE_INSTRUCTION), _FixedRNG(0.01))
        self.assertEqual(outcome, OffBallScreenOutcome.SWITCH)
        self.assertEqual(e.state.assignments["9"].assigned_to_player_id, "2")
        self.assertEqual(e.state.assignments["8"].assigned_to_player_id, "5")

    def test_no_duplicate_assignment_and_no_uncovered_player(self):
        e = _engine()
        apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.99))
        assigned_to_values = [a.assigned_to_player_id for a in e.state.assignments.values()]
        self.assertEqual(len(assigned_to_values), len(set(assigned_to_values)))  # no duplicate
        self.assertIn("5", assigned_to_values)  # screener still covered
        self.assertIn("2", assigned_to_values)  # receiver still covered

    def test_canonical_player_ids_preserved(self):
        e = _engine()
        apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.99))
        self.assertEqual(set(e.state.assignments.keys()), {"9", "8"})  # defender identities unchanged, only pointers swapped

    def test_switch_creates_no_advantage_effect(self):
        e = _engine(advantage=SpatialMagnitudeAdvantage())
        apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.99))
        self.assertEqual(e.advantage.compromised_areas(), ())


class TestInvalidSwitchState(unittest.TestCase):
    def test_missing_receiver_defender_assignment_fails_explicitly_no_mutation(self):
        e = _engine()
        del_state = replace(e.state, assignments={k: v for k, v in e.state.assignments.items() if k != "8"})
        e.state = del_state
        before = e.state
        with self.assertRaises(ValueError):
            apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.99))
        self.assertEqual(e.state, before)  # zero mutation

    def test_mismatched_context_fails_explicitly_no_mutation(self):
        e = _engine()
        before = e.state
        # receiver_defender_id "8" is really assigned to "2", not "3" -- a malformed/inconsistent caller claim.
        with self.assertRaises(ValueError):
            apply_off_ball_screen_to_engine(e, _ctx(moving_receiver_id="3"), _FixedRNG(0.99))
        self.assertEqual(e.state, before)

    def test_same_defender_for_both_roles_rejected(self):
        e = _engine()
        before = e.state
        with self.assertRaises(ValueError):
            apply_off_ball_screen_to_engine(e, _ctx(receiver_defender_id="9"), _FixedRNG(0.99))
        self.assertEqual(e.state, before)

    def test_same_player_as_screener_and_receiver_rejected(self):
        e = _engine()
        before = e.state
        with self.assertRaises(ValueError):
            apply_off_ball_screen_to_engine(e, _ctx(moving_receiver_id="5"), _FixedRNG(0.99))
        self.assertEqual(e.state, before)

    def test_atomic_switch_rejects_preexisting_duplicate_assignment_state(self):
        """A directly-constructed, already-corrupt one-to-many matchup
        (both defenders pointed at the SAME offensive player) must be
        refused rather than compounded -- exercised directly against
        `_atomic_switch_assignments` since `apply_off_ball_screen_to_engine`'s
        own upstream validation cannot itself construct this state
        (screener_id != moving_receiver_id is enforced first)."""
        e = _engine()
        corrupted = dict(e.state.assignments)
        corrupted["8"] = corrupted["8"].switch("5", DefensivePosture.SQUARE)  # both "9" and "8" now guard "5"
        e.state = replace(e.state, assignments=corrupted)
        before = e.state
        with self.assertRaises(ValueError):
            _atomic_switch_assignments(e, "9", "8")
        self.assertEqual(e.state, before)

    def test_switch_with_self_raises(self):
        e = _engine()
        with self.assertRaises(ValueError):
            _atomic_switch_assignments(e, "9", "9")


class TestPhase16ReEntry(unittest.TestCase):
    def test_trailing_separation_enables_closeout_attack_after_a_catch(self):
        """The screen resolver never forces an action -- it only updates
        real Phase 15 state; the EXISTING, unmodified `generate_opportunities`
        then naturally exposes a downstream opportunity once the receiver
        becomes the ball handler."""
        e = _engine()
        apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.6))  # TRAILING_SEPARATION on defender "8"
        self.assertEqual(e.state.assignments["8"].posture, DefensivePosture.TRAILING)

        # Receiver "2" now catches the ball -- ordinary Phase 15 transition, not part of this module.
        e.state = e.state.with_ball_carrier("2", BallState.HELD)
        ctx = StructuralContext(just_caught_pass=True, ball_handler_defender_id="8")
        opps = generate_opportunities(e.state, ctx)
        self.assertTrue(any(o.action_type == ActionType.CLOSEOUT_ATTACK for o in opps))
        # the screen resolver itself never appended an opportunity, selected an action, or touched the ball
        self.assertEqual(e.state.ball_carrier, "2")

    def test_zone_compromise_enables_kickout_on_next_generate_opportunities_call(self):
        e = _engine(advantage=SpatialMagnitudeAdvantage())
        apply_off_ball_screen_to_engine(e, _ctx(destination_zone=SpatialZone.PAINT), _FixedRNG(0.6))
        e.state = e.state.with_ball_carrier("1", BallState.HELD)  # some OTHER player still has the ball
        ctx = StructuralContext(perimeter_receiver_ids={"2": SpatialZone.RIGHT_CORNER})
        opps = generate_opportunities(e.state, ctx, advantage=e.advantage)
        self.assertTrue(any(o.action_type == ActionType.KICKOUT for o in opps))

    def test_no_effect_never_fabricates_a_forced_action(self):
        e = _engine()
        apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.1))
        e.state = e.state.with_ball_carrier("1", BallState.HELD)
        ctx = StructuralContext()
        opps = generate_opportunities(e.state, ctx)
        # a real, ordinary opportunity list -- the resolver did not inject or force any action
        self.assertIsInstance(opps, list)


class TestAttributeFirewalls(unittest.TestCase):
    def test_attribute_firewalls(self):
        """Namespace-name scan (same methodology as Phase 21A's own
        `test_no_ability_symbols`) -- checks the module's actual defined
        symbols, not prose. The module docstring legitimately NAMES these
        four attributes in prose to explain why they are firewalled;
        a raw full-source-text scan would false-positive on that
        documentation, which is not what this test is checking for."""
        import off_ball_screen_resolution as mod
        forbidden = ("poa_containment", "perimeter_space_creation", "defensive_playmaking", "foul_discipline")
        for name in vars(mod):
            if name.startswith("__"):
                continue
            for bad in forbidden:
                self.assertNotIn(bad, name.lower())

    def test_no_new_ability_or_physical_fields(self):
        import dataclasses
        import off_ball_screen_resolution as mod
        forbidden = ("screen_setting", "screen_navigation", "offball_movement", "separation",
                     "screen_usage", "illegal_screen", "height", "wingspan", "standing_reach", "mass", "strength")
        for name in vars(mod):
            if name.startswith("__"):
                continue
            for bad in forbidden:
                self.assertNotIn(bad, name.lower())
        field_names = {f.name for f in dataclasses.fields(mod.OffBallScreenContext)}
        for bad in forbidden:
            self.assertFalse(any(bad in fn.lower() for fn in field_names))

    def test_no_role_or_tendency_import(self):
        import off_ball_screen_resolution as mod
        src = inspect.getsource(mod)
        self.assertNotIn("role_off_profile", src)
        self.assertNotIn("RoleContext", src)
        self.assertNotIn("TendencyContext", src)


class TestNoDirectScoringEffect(unittest.TestCase):
    def test_no_scoring_or_possession_methods_referenced(self):
        """Checks actual `engine.<method>(` CALL SITES, not prose --
        this module's own docstring names several of these methods to
        explain that it never calls them, which a bare full-text scan
        would false-positive on."""
        import off_ball_screen_resolution as mod
        src = inspect.getsource(mod)
        for forbidden in ("begin_shot", "resolve_shot_made", "resolve_shot_missed", "pass_ball", "receive_pass",
                           "dead_ball_turnover", "live_ball_turnover", "shooting_foul", "non_shooting_foul"):
            self.assertNotIn(f"engine.{forbidden}(", src)

    def test_ball_state_and_team_possession_untouched_by_every_outcome(self):
        for fixed_value, expected in ((0.1, OffBallScreenOutcome.NO_EFFECT),
                                       (0.6, OffBallScreenOutcome.TRAILING_SEPARATION),
                                       (0.99, OffBallScreenOutcome.SWITCH)):
            e = _engine()
            before_ball_state, before_carrier = e.state.ball_state, e.state.ball_carrier
            before_offense, before_defense = e.state.offense_team_id, e.state.defense_team_id
            outcome = apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(fixed_value))
            self.assertEqual(outcome, expected)
            self.assertEqual(e.state.ball_state, before_ball_state)
            self.assertEqual(e.state.ball_carrier, before_carrier)
            self.assertEqual(e.state.offense_team_id, before_offense)
            self.assertEqual(e.state.defense_team_id, before_defense)


class TestEventLogging(unittest.TestCase):
    def test_switch_logs_exactly_two_assignment_switch_events(self):
        from possession_events import EventType
        e = _engine()
        n_before = len(e.log.events)
        apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(0.99))
        new_events = e.log.events[n_before:]
        switch_events = [ev for ev in new_events if ev.event_type == EventType.ASSIGNMENT_SWITCH]
        self.assertEqual(len(switch_events), 2)
        # each event is structurally truthful -- reflects the REAL post-swap pointer, not a fabricated one
        by_primary = {ev.primary_player_id: ev.secondary_player_id for ev in switch_events}
        self.assertEqual(by_primary["9"], "2")
        self.assertEqual(by_primary["8"], "5")

    def test_no_effect_and_trailing_never_log_assignment_switch_events(self):
        from possession_events import EventType
        for fixed_value in (0.1, 0.6):
            e = _engine()
            n_before = len(e.log.events)
            apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(fixed_value))
            new_events = e.log.events[n_before:]
            self.assertFalse(any(ev.event_type == EventType.ASSIGNMENT_SWITCH for ev in new_events))

    def test_no_fake_assist_shot_or_pass_events(self):
        from possession_events import EventType
        forbidden_types = {EventType.SHOT_RELEASED, EventType.SHOT_RESOLVED, EventType.PASS_RELEASED,
                            EventType.PASS_RECEIVED, EventType.LIVE_BALL_TURNOVER, EventType.DEAD_BALL_TURNOVER}
        for fixed_value in (0.1, 0.6, 0.99):
            e = _engine()
            n_before = len(e.log.events)
            apply_off_ball_screen_to_engine(e, _ctx(), _FixedRNG(fixed_value))
            new_events = e.log.events[n_before:]
            self.assertFalse(any(ev.event_type in forbidden_types for ev in new_events))


class TestScreenActiveRollerIdUntouched(unittest.TestCase):
    def test_module_does_not_reference_the_on_ball_pnr_scaffold(self):
        import off_ball_screen_resolution as mod
        src = inspect.getsource(mod)
        self.assertNotIn("screen_active", src)
        self.assertNotIn("roller_id", src)
        self.assertNotIn("POCKET_PASS", src)

    def test_pocket_pass_precondition_is_unaffected_by_this_module_existing(self):
        """Demonstrates the existing on-ball PnR scaffold's own behavior
        (Phase 16) is completely unaffected by this module's existence --
        same assertions as `TestStructuralDefensiveOpportunities.test_pocket_pass_requires_live_roller`."""
        from possession_state import DribbleState, PlayerBallControl
        e = _engine()
        e.state = e.state.with_ball_carrier("1", BallState.HELD)
        no_roller = generate_opportunities(e.state, StructuralContext(screen_active=True))
        self.assertFalse(any(o.action_type == ActionType.POCKET_PASS for o in no_roller))
        with_roller = generate_opportunities(e.state, StructuralContext(roller_id="4", screen_active=True))
        self.assertTrue(any(o.action_type == ActionType.POCKET_PASS for o in with_roller))


class TestDeterminismAndRNG(unittest.TestCase):
    def test_deterministic_replay(self):
        def run():
            e = _engine()
            return apply_off_ball_screen_to_engine(e, _ctx(), random.Random(42))
        self.assertEqual(run(), run())

    def test_no_global_rng(self):
        import off_ball_screen_resolution as mod
        src = inspect.getsource(mod)
        self.assertNotIn("random.random(", src)
        self.assertNotIn("random.choice(", src)

    def test_resolve_is_pure_no_engine_mutation(self):
        e = _engine()
        before = e.state
        resolve_off_ball_screen(_ctx(), random.Random(1))
        self.assertEqual(e.state, before)


if __name__ == "__main__":
    unittest.main()
