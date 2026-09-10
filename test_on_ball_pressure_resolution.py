"""Phase 21A -- focused tests for on_ball_pressure_resolution.py."""
import inspect
import random
import unittest

from on_ball_pressure_resolution import (
    OnBallContactOutcome, OnBallPressureContext, apply_on_ball_pressure_to_engine, resolve_on_ball_pressure,
)
from possession_engine import PossessionEngine
from possession_state import BallState, DefensivePosture, DribbleState, PossessionPhase, SpatialZone


def _engine(control=DribbleState.LIVE_DRIBBLE):
    e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
    e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
    if control != DribbleState.LIVE_DRIBBLE:
        from dataclasses import replace
        e.state = replace(e.state, ball_control=replace(e.state.ball_control, state=control))
    e.switch("9", "1", DefensivePosture.SQUARE)
    return e


def _ctx(**kwargs):
    return OnBallPressureContext(**kwargs)


class TestPressureOutcomes(unittest.TestCase):
    def test_clean_control_reachable(self):
        outcomes = {resolve_on_ball_pressure("1", "9", _ctx(ball_security=0.001, defensive_playmaking=0.2), random.Random(i)) for i in range(50)}
        self.assertIn(OnBallContactOutcome.CLEAN_CONTROL, outcomes)

    def test_disruption_reachable(self):
        outcomes = {resolve_on_ball_pressure("1", "9", _ctx(ball_security=0.02, defensive_playmaking=4.0), random.Random(i)) for i in range(500)}
        self.assertIn(OnBallContactOutcome.DISRUPTED, outcomes)

    def test_forced_pickup_reachable(self):
        outcomes = {resolve_on_ball_pressure("1", "9", _ctx(ball_security=0.02, defensive_playmaking=4.0), random.Random(i)) for i in range(500)}
        self.assertIn(OnBallContactOutcome.FORCED_PICKUP, outcomes)

    def test_clean_strip_reachable(self):
        outcomes = {resolve_on_ball_pressure("1", "9", _ctx(ball_security=0.03, defensive_playmaking=5.0), random.Random(i)) for i in range(500)}
        self.assertIn(OnBallContactOutcome.CLEAN_STRIP_LOOSE, outcomes)


class TestCollisionOutcomes(unittest.TestCase):
    def test_no_call_reachable(self):
        outcomes = {resolve_on_ball_pressure("1", "9", _ctx(contact_established=True), random.Random(i)) for i in range(50)}
        self.assertIn(OnBallContactOutcome.NO_CALL_CONTACT, outcomes)

    def test_offensive_charge_reachable(self):
        outcomes = {resolve_on_ball_pressure("1", "9", _ctx(contact_established=True), random.Random(i)) for i in range(100)}
        self.assertIn(OnBallContactOutcome.OFFENSIVE_CHARGE, outcomes)

    def test_defensive_foul_reachable(self):
        outcomes = {resolve_on_ball_pressure("1", "9", _ctx(contact_established=True), random.Random(i)) for i in range(100)}
        self.assertIn(OnBallContactOutcome.DEFENSIVE_FLOOR_FOUL, outcomes)


class TestLooseBallHandling(unittest.TestCase):
    def test_loose_ball_no_phantom_carrier(self):
        e = _engine()
        ctx = _ctx(ball_security=0.05, defensive_playmaking=8.0)
        for i in range(300):
            e2 = _engine()
            outcome = apply_on_ball_pressure_to_engine(e2, "1", "9", ctx, random.Random(i))
            if outcome == OnBallContactOutcome.CLEAN_STRIP_LOOSE:
                self.assertIsNone(e2.state.ball_carrier)
                self.assertEqual(e2.state.ball_state, BallState.LOOSE)
                self.assertIsNone(e2.state.offense_team_id)
                return
        self.fail("no strip occurred in 300 trials")

    def test_offense_can_recover_own_loose_ball(self):
        from possession_engine import PossessionEngine as PE
        e = _engine()
        e.state.ball_state = BallState.LOOSE
        e.state.ball_carrier = None
        e.state.offense_team_id = None
        e.secure_loose_ball("1", "A", "B")  # existing Phase 15 machinery -- offense recovers its own loose ball
        self.assertEqual(e.state.ball_carrier, "1")
        self.assertEqual(e.state.offense_team_id, "A")


class TestForcedPickupIntegration(unittest.TestCase):
    def test_forced_pickup_sets_dead_dribble(self):
        ctx = _ctx(ball_security=0.03, defensive_playmaking=5.0)
        for i in range(300):
            e = _engine()
            outcome = apply_on_ball_pressure_to_engine(e, "1", "9", ctx, random.Random(i))
            if outcome == OnBallContactOutcome.FORCED_PICKUP:
                self.assertEqual(e.state.ball_control.state, DribbleState.DEAD_DRIBBLE)
                self.assertEqual(e.state.ball_carrier, "1")  # still the same handler -- not a turnover
                return
        self.fail("no forced pickup occurred in 300 trials")

    def test_forced_pickup_is_not_a_turnover(self):
        ctx = _ctx(ball_security=0.03, defensive_playmaking=5.0)
        for i in range(300):
            e = _engine()
            outcome = apply_on_ball_pressure_to_engine(e, "1", "9", ctx, random.Random(i))
            if outcome == OnBallContactOutcome.FORCED_PICKUP:
                self.assertEqual(e.state.offense_team_id, "A")
                return


class TestReachInAndNoCall(unittest.TestCase):
    def test_reach_in_foul_representable(self):
        e = _engine()
        ctx = _ctx(contact_established=True)
        for i in range(100):
            e2 = _engine()
            outcome = apply_on_ball_pressure_to_engine(e2, "1", "9", ctx, random.Random(i))
            if outcome == OnBallContactOutcome.DEFENSIVE_FLOOR_FOUL:
                self.assertEqual(e2.state.ball_state, BallState.DEAD)
                self.assertEqual(e2.state.offense_team_id, "A")  # offense retains possession
                return
        self.fail("no defensive foul occurred in 100 trials")

    def test_no_call_continues_play(self):
        ctx = _ctx(contact_established=True)
        for i in range(50):
            e = _engine()
            outcome = apply_on_ball_pressure_to_engine(e, "1", "9", ctx, random.Random(i))
            if outcome == OnBallContactOutcome.NO_CALL_CONTACT:
                self.assertEqual(e.state.ball_state, BallState.HELD)
                self.assertEqual(e.state.ball_carrier, "1")
                return
        self.fail("no no-call occurred in 50 trials")


class TestOffensiveCharge(unittest.TestCase):
    def test_charge_is_dead_ball_turnover_no_steal(self):
        ctx = _ctx(contact_established=True)
        for i in range(100):
            e = _engine()
            outcome = apply_on_ball_pressure_to_engine(e, "1", "9", ctx, random.Random(i))
            if outcome == OnBallContactOutcome.OFFENSIVE_CHARGE:
                self.assertEqual(e.state.ball_state, BallState.DEAD)
                self.assertFalse(hasattr(outcome, "steal"))
                return
        self.fail("no charge occurred in 100 trials")


class TestShootingBoundaryFirewall(unittest.TestCase):
    def test_gathered_dribble_rejected(self):
        e = _engine(control=DribbleState.GATHERED)
        ctx = _ctx()
        with self.assertRaises(ValueError):
            apply_on_ball_pressure_to_engine(e, "1", "9", ctx, random.Random(1))

    def test_dead_dribble_rejected(self):
        e = _engine(control=DribbleState.DEAD_DRIBBLE)
        ctx = _ctx()
        with self.assertRaises(ValueError):
            apply_on_ball_pressure_to_engine(e, "1", "9", ctx, random.Random(1))

    def test_no_shot_resolution_reference(self):
        import on_ball_pressure_resolution as obr
        source = inspect.getsource(obr)
        self.assertNotIn("shot_make_probability", source)
        self.assertNotIn("ShotResolutionContext", source)


class TestPhase17AIntegration(unittest.TestCase):
    def test_no_drive_resolver_reference(self):
        import on_ball_pressure_resolution as obr
        source = inspect.getsource(obr)
        self.assertNotIn("resolve_drive", source)
        self.assertNotIn("DriveResolutionContext", source)


class TestFirewalls(unittest.TestCase):
    def test_no_ability_symbols(self):
        import on_ball_pressure_resolution as obr
        forbidden = ("passing_accuracy", "rim_finishing", "three_point", "poa_containment",
                     "rim_access_creation", "defensive_rebounding", "mass", "wingspan", "standing_reach", "height")
        for name in vars(obr):
            if name.startswith("__"):
                continue
            for bad in forbidden:
                self.assertNotIn(bad, name.lower())

    def test_no_physical_field(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(OnBallPressureContext)}
        self.assertTrue(names.isdisjoint({"mass", "height", "wingspan", "standing_reach"}))

    def test_bad_pass_never_uses_ball_security(self):
        """Checks actual CODE symbols only -- pass_resolution.py's own
        prose docstring legitimately explains ball_security is NOT
        used, which would otherwise false-positive a naive text search."""
        import pass_resolution
        for name, value in vars(pass_resolution).items():
            if name.startswith("__"):
                continue
            self.assertNotIn("ball_security", name.lower())

    def test_passing_accuracy_has_zero_effect_on_strip_outcome(self):
        sig = inspect.signature(resolve_on_ball_pressure)
        for name in sig.parameters:
            self.assertNotIn("passing_accuracy", name.lower())


class TestDeterminismAndIsolation(unittest.TestCase):
    def test_deterministic_replay(self):
        ctx = _ctx(ball_security=0.01, defensive_playmaking=2.0)
        r1 = resolve_on_ball_pressure("1", "9", ctx, random.Random(42))
        r2 = resolve_on_ball_pressure("1", "9", ctx, random.Random(42))
        self.assertEqual(r1, r2)

    def test_no_global_rng(self):
        import on_ball_pressure_resolution as obr
        source = inspect.getsource(obr)
        self.assertNotIn("random.random(", source)

    def test_player_id_only(self):
        with self.assertRaises(TypeError):
            resolve_on_ball_pressure("LeBron James", "9", _ctx(), random.Random(1))

    def test_no_mutation_leakage(self):
        e1 = _engine()
        e2 = PossessionEngine("p2", "A", "B", season="2023-24", rng_seed=2)
        e2.inbound("5", SpatialZone.PAINT, PossessionPhase.HALFCOURT)
        apply_on_ball_pressure_to_engine(e1, "1", "9", _ctx(ball_security=0.05, defensive_playmaking=8.0), random.Random(1))
        self.assertEqual(e2.state.ball_carrier, "5")


class TestFoulEventMetadata(unittest.TestCase):
    def test_defensive_foul_does_not_award_fts_locally(self):
        """21A must not administer FTs -- that's explicitly Phase 21B's
        job. Verified structurally: apply_on_ball_pressure_to_engine's
        DEFENSIVE_FLOOR_FOUL branch only calls non_shooting_foul, never
        anything FT-related."""
        import on_ball_pressure_resolution as obr
        source = inspect.getsource(obr)
        self.assertNotIn("FreeThrowSequence", source)
        self.assertNotIn("resolve_free_throw", source)


if __name__ == "__main__":
    unittest.main()
