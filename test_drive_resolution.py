"""Phase 17A -- focused tests for drive_resolution.py."""
import random
import unittest

from drive_resolution import (
    CUMULATIVE_BASE_RATES, DriveOutcome, DriveResolutionContext, _net_leverage, _sample_outcome, resolve_drive,
)
from possession_engine import PossessionEngine
from possession_state import BallState, DefensivePosture, DribbleState, PossessionPhase, SpatialZone


def _engine_with_driver(zone=SpatialZone.LEFT_WING, control=DribbleState.LIVE_DRIBBLE, seed=1):
    e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=seed)
    e.inbound("1", zone, PossessionPhase.HALFCOURT)
    if control != DribbleState.LIVE_DRIBBLE:
        from dataclasses import replace
        e.state = replace(e.state, ball_control=replace(e.state.ball_control, state=control))
    e.switch("9", "1", DefensivePosture.SQUARE)
    return e


class TestOutcomeTaxonomy(unittest.TestCase):
    def test_all_four_core_outcomes_representable(self):
        self.assertEqual(set(DriveOutcome.ORDERED),
                          {DriveOutcome.FORCED_PICKUP, DriveOutcome.CONTAINED, DriveOutcome.PARTIAL_EDGE, DriveOutcome.CLEAN_PENETRATION})

    def test_clean_penetration_reachable(self):
        ctx = DriveResolutionContext(rim_access_creation=1.0, poa_containment=-0.13)
        outcomes = {_sample_outcome(ctx, random.Random(i)) for i in range(200)}
        self.assertIn(DriveOutcome.CLEAN_PENETRATION, outcomes)

    def test_partial_edge_reachable(self):
        ctx = DriveResolutionContext(rim_access_creation=0.62, poa_containment=-0.01)
        outcomes = {_sample_outcome(ctx, random.Random(i)) for i in range(500)}
        self.assertIn(DriveOutcome.PARTIAL_EDGE, outcomes)

    def test_contained_reachable(self):
        ctx = DriveResolutionContext(rim_access_creation=0.62, poa_containment=-0.01)
        outcomes = {_sample_outcome(ctx, random.Random(i)) for i in range(500)}
        self.assertIn(DriveOutcome.CONTAINED, outcomes)

    def test_forced_pickup_reachable(self):
        ctx = DriveResolutionContext(rim_access_creation=0.15, poa_containment=0.09)
        outcomes = {_sample_outcome(ctx, random.Random(i)) for i in range(200)}
        self.assertIn(DriveOutcome.FORCED_PICKUP, outcomes)


class TestNoAutomaticShot(unittest.TestCase):
    def test_drive_does_not_create_a_shot(self):
        e = _engine_with_driver()
        ctx = DriveResolutionContext(rim_access_creation=1.0, poa_containment=-0.13)
        resolve_drive(e, "1", "9", ctx, random.Random(1))
        self.assertNotEqual(e.state.ball_state, BallState.SHOT_IN_FLIGHT)
        self.assertTrue(all(ev.event_type.name != "SHOT_RELEASED" for ev in e.log.events))

    def test_clean_penetration_returns_to_selection_ready_state(self):
        e = _engine_with_driver()
        ctx = DriveResolutionContext(rim_access_creation=1.0, poa_containment=-0.13)
        outcome = resolve_drive(e, "1", "9", ctx, random.Random(1))
        self.assertEqual(outcome, DriveOutcome.CLEAN_PENETRATION)
        # a normal Phase 16 selection call must be runnable from here
        self.assertEqual(e.state.ball_state, BallState.HELD)
        self.assertEqual(e.state.ball_carrier, "1")
        from action_opportunity import StructuralContext, generate_opportunities
        opps = generate_opportunities(e.state, StructuralContext(nearest_teammate_id="2"))
        self.assertTrue(len(opps) > 0)


class TestAbilityBoundaries(unittest.TestCase):
    def test_rim_access_creation_does_not_touch_shooting_resolution(self):
        import inspect
        source = inspect.getsource(__import__("drive_resolution"))
        self.assertNotIn("make_probability", source)
        self.assertNotIn("shot_resolved", source.lower().replace("_", ""))

    def test_rim_finishing_not_imported_or_referenced(self):
        """Checks actual CODE symbols only (not the module's own prose
        docstring, which legitimately explains rim_finishing is NOT
        used) -- no imported name, function, or module reference
        anywhere in drive_resolution.py's namespace touches rim_finishing."""
        import drive_resolution
        for name, value in vars(drive_resolution).items():
            if name.startswith("__"):
                continue
            self.assertNotIn("rim_finishing", name.lower())
            self.assertNotIn("rim_finishing", getattr(value, "__module__", "") or "")

    def test_poa_containment_cannot_affect_rim_shot_resolution(self):
        """There is no shot-resolution code path in this module at all
        -- poa_containment only ever feeds _net_leverage."""
        ctx = DriveResolutionContext(poa_containment=0.09)
        leverage_high_poa = _net_leverage(ctx)
        ctx2 = DriveResolutionContext(poa_containment=-0.13)
        leverage_low_poa = _net_leverage(ctx2)
        self.assertLess(leverage_high_poa, leverage_low_poa)  # only affects leverage, monotonically, nothing else

    def test_physical_additions_disabled_by_default(self):
        ctx = DriveResolutionContext(rim_access_creation=0.6, physical_adjustment=999.0)  # enable flag NOT set
        leverage = _net_leverage(ctx)
        ctx_no_phys = DriveResolutionContext(rim_access_creation=0.6)
        self.assertEqual(leverage, _net_leverage(ctx_no_phys))  # the huge physical_adjustment had zero effect

    def test_physical_additions_only_apply_when_explicitly_enabled(self):
        ctx = DriveResolutionContext(rim_access_creation=0.6, enable_physical_adjustment=True, physical_adjustment=2.0)
        ctx_off = DriveResolutionContext(rim_access_creation=0.6)
        self.assertNotEqual(_net_leverage(ctx), _net_leverage(ctx_off))


class TestPostureAndContext(unittest.TestCase):
    def test_posture_changes_drive_context(self):
        square = DriveResolutionContext(rim_access_creation=0.6, poa_containment=0.0, defender_posture=DefensivePosture.SQUARE)
        trailing = DriveResolutionContext(rim_access_creation=0.6, poa_containment=0.0, defender_posture=DefensivePosture.TRAILING)
        self.assertGreater(_net_leverage(trailing), _net_leverage(square))

    def test_square_vs_compromised_handled_differently_structurally(self):
        outcomes_square = [_sample_outcome(DriveResolutionContext(rim_access_creation=0.62, poa_containment=-0.0147,
                                                                     defender_posture=DefensivePosture.SQUARE), random.Random(i))
                            for i in range(500)]
        outcomes_helping = [_sample_outcome(DriveResolutionContext(rim_access_creation=0.62, poa_containment=-0.0147,
                                                                      defender_posture=DefensivePosture.HELPING), random.Random(i))
                             for i in range(500)]
        clean_rate_square = outcomes_square.count(DriveOutcome.CLEAN_PENETRATION) / len(outcomes_square)
        clean_rate_helping = outcomes_helping.count(DriveOutcome.CLEAN_PENETRATION) / len(outcomes_helping)
        self.assertGreater(clean_rate_helping, clean_rate_square)


class TestAdvantageModelAgnostic(unittest.TestCase):
    def test_no_hardcoded_advantagemodel_internals(self):
        import inspect
        source = inspect.getsource(__import__("drive_resolution"))
        self.assertNotIn("DiscreteTierAdvantage", source)
        self.assertNotIn("SpatialMagnitudeAdvantage", source)
        self.assertNotIn(".tiers", source)
        self.assertNotIn(".magnitudes", source)

    def test_no_single_vulnerable_zone_assumption(self):
        import inspect
        source = inspect.getsource(__import__("drive_resolution"))
        self.assertNotIn("vulnerable_zone", source.lower())


class TestHelpOpportunityNoTeleport(unittest.TestCase):
    def test_help_opportunity_flag_set_without_moving_any_other_player(self):
        e = _engine_with_driver()
        e.switch("8", "2", DefensivePosture.SQUARE)  # a second, unrelated defender
        ctx = DriveResolutionContext(rim_access_creation=1.0, poa_containment=-0.13)
        resolve_drive(e, "1", "9", ctx, random.Random(1))
        drive_events = [ev for ev in e.log.events if ev.event_type.name == "DRIVE_RESOLVED"]
        self.assertTrue(drive_events[0].metadata["help_opportunity"])
        # the second defender's assignment/posture must be completely untouched -- no teleportation
        self.assertEqual(e.state.assignments["8"].assigned_to_player_id, "2")
        self.assertEqual(e.state.assignments["8"].posture, DefensivePosture.SQUARE)

    def test_no_help_opportunity_flag_on_contained(self):
        e = _engine_with_driver()
        ctx = DriveResolutionContext(rim_access_creation=0.15, poa_containment=0.09)
        outcome = resolve_drive(e, "1", "9", ctx, random.Random(1))
        drive_events = [ev for ev in e.log.events if ev.event_type.name == "DRIVE_RESOLVED"]
        if outcome in (DriveOutcome.CONTAINED, DriveOutcome.FORCED_PICKUP):
            self.assertFalse(drive_events[0].metadata["help_opportunity"])


class TestSpatialProgress(unittest.TestCase):
    def test_successful_penetration_does_not_automatically_reach_restricted_area(self):
        outcomes_and_zones = []
        for i in range(50):
            e = _engine_with_driver(seed=i)
            ctx = DriveResolutionContext(rim_access_creation=1.0, poa_containment=-0.13)
            outcome = resolve_drive(e, "1", "9", ctx, random.Random(i))
            if outcome == DriveOutcome.CLEAN_PENETRATION:
                outcomes_and_zones.append(e.state.ball_zone)
        zones_seen = set(outcomes_and_zones)
        self.assertTrue(len(zones_seen) >= 1)
        # both PAINT and RESTRICTED_RIM should be reachable across many trials -- not always RESTRICTED_AREA
        self.assertIn(SpatialZone.PAINT, zones_seen | {SpatialZone.RESTRICTED_RIM})  # sanity: real zones only


class TestDribbleGating(unittest.TestCase):
    def test_dead_dribble_prevents_another_drive(self):
        e = _engine_with_driver(control=DribbleState.DEAD_DRIBBLE)
        ctx = DriveResolutionContext(rim_access_creation=0.6, poa_containment=0.0)
        with self.assertRaises(ValueError):
            resolve_drive(e, "1", "9", ctx, random.Random(1))

    def test_gathered_dribble_prevents_drive(self):
        e = _engine_with_driver(control=DribbleState.GATHERED)
        ctx = DriveResolutionContext(rim_access_creation=0.6, poa_containment=0.0)
        with self.assertRaises(ValueError):
            resolve_drive(e, "1", "9", ctx, random.Random(1))

    def test_forced_pickup_produces_dead_dribble(self):
        outcome = None
        e = None
        for i in range(300):
            e = _engine_with_driver(seed=i)
            ctx = DriveResolutionContext(rim_access_creation=0.15, poa_containment=0.09)
            outcome = resolve_drive(e, "1", "9", ctx, random.Random(i))
            if outcome == DriveOutcome.FORCED_PICKUP:
                break
        self.assertEqual(outcome, DriveOutcome.FORCED_PICKUP)
        self.assertEqual(e.state.ball_control.state, DribbleState.DEAD_DRIBBLE)


class TestLostBallScaffold(unittest.TestCase):
    def test_lost_ball_disabled_by_default(self):
        ctx = DriveResolutionContext(rim_access_creation=0.3, poa_containment=0.09, lost_ball_rate=0.9)  # high rate, but not enabled
        outcomes = {_sample_outcome(ctx, random.Random(i)) for i in range(200)}
        self.assertNotIn(DriveOutcome.LOST_BALL, outcomes)

    def test_lost_ball_distinct_from_bad_pass_turnover(self):
        """LOST_BALL (this module's scaffolded strip concept) must never
        be confused with a bad-pass turnover -- there is no pass-related
        code anywhere in this module to conflate it with (Phase 17B's
        job)."""
        import inspect
        source = inspect.getsource(__import__("drive_resolution"))
        self.assertNotIn("bad_pass", source.lower())
        self.assertNotIn("pass_accuracy", source.lower())

    def test_lost_ball_reachable_only_when_explicitly_enabled(self):
        ctx = DriveResolutionContext(rim_access_creation=0.3, poa_containment=0.09,
                                       enable_lost_ball=True, lost_ball_rate=1.0)  # forced for test determinism
        outcome = _sample_outcome(ctx, random.Random(1))
        self.assertEqual(outcome, DriveOutcome.LOST_BALL)


class TestIdentityDeterminismIsolation(unittest.TestCase):
    def test_player_id_only(self):
        e = _engine_with_driver()
        ctx = DriveResolutionContext(rim_access_creation=0.6, poa_containment=0.0)
        with self.assertRaises(TypeError):
            resolve_drive(e, "LeBron James", "9", ctx, random.Random(1))

    def test_deterministic_replay(self):
        def run():
            e = _engine_with_driver()
            ctx = DriveResolutionContext(rim_access_creation=0.6, poa_containment=0.0)
            return resolve_drive(e, "1", "9", ctx, random.Random(42)), e.state.ball_zone

        self.assertEqual(run(), run())

    def test_no_global_rng_used(self):
        import inspect
        source = inspect.getsource(__import__("drive_resolution"))
        # only "random.Random" (the class) may appear, never a bare "random.random(" call using the global module instance
        self.assertNotIn("random.random(", source)
        self.assertNotIn("random.choice(", source)

    def test_no_mutation_leakage_between_engines(self):
        e1 = _engine_with_driver(seed=1)
        e2 = _engine_with_driver(seed=2)
        ctx = DriveResolutionContext(rim_access_creation=1.0, poa_containment=-0.13)
        resolve_drive(e1, "1", "9", ctx, random.Random(1))
        self.assertEqual(e2.state.ball_zone, SpatialZone.LEFT_WING)  # untouched by e1's resolution


class TestSelectionResolutionSeparation(unittest.TestCase):
    def test_phase16_selection_outputs_remain_separate_from_resolution(self):
        """An ActionIntent for DRIVE carries no outcome field, and
        resolve_drive takes driver/defender ids directly -- it does not
        consume or require an ActionIntent object at all, keeping the
        two phases' interfaces decoupled."""
        import inspect
        sig = inspect.signature(resolve_drive)
        self.assertNotIn("action_intent", [p.lower() for p in sig.parameters])
        self.assertNotIn("intent", [p.lower() for p in sig.parameters])


if __name__ == "__main__":
    unittest.main()
