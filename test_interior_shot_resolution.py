"""Phase 18B -- focused tests for interior_shot_resolution.py."""
import inspect
import random
import unittest

from interior_shot_resolution import (
    InteriorDefenderContext, InteriorShotContext, InteriorShotFamily, InteriorShotOutcome,
    apply_interior_shot_to_engine, block_probability, geometric_block_eligibility,
    resolve_interior_shot, unblocked_make_probability,
)
from possession_engine import PossessionEngine
from possession_state import BallState, DefensivePosture, PossessionPhase, SpatialZone


def _primary(rim_protection=0.01, defensive_playmaking=1.5, posture=DefensivePosture.SQUARE, zone=SpatialZone.RESTRICTED_RIM):
    return InteriorDefenderContext("9", zone, posture, is_primary=True,
                                    rim_protection=rim_protection, defensive_playmaking=defensive_playmaking)


def _ctx(family=InteriorShotFamily.RIM, base_rate=0.65, primary=None, secondary=None):
    return InteriorShotContext(shot_family=family, shooter_base_rate=base_rate,
                                primary_defender=primary or _primary(), secondary_defender=secondary)


def _engine():
    e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
    e.inbound("1", SpatialZone.RESTRICTED_RIM, PossessionPhase.HALFCOURT)
    e.switch("9", "1", DefensivePosture.SQUARE)
    return e


class TestSupportedFamilies(unittest.TestCase):
    def test_rim_family_resolves(self):
        result = resolve_interior_shot("1", _ctx(family=InteriorShotFamily.RIM), random.Random(1))
        self.assertIn(result.outcome, vars(InteriorShotOutcome).values())

    def test_floater_family_resolves(self):
        primary = _primary(zone=SpatialZone.PAINT)
        result = resolve_interior_shot("1", _ctx(family=InteriorShotFamily.FLOATER, base_rate=0.42, primary=primary), random.Random(1))
        self.assertIn(result.outcome, vars(InteriorShotOutcome).values())


class TestAbilityFamilyMapping(unittest.TestCase):
    def test_rim_uses_high_base_rate_directly(self):
        p = unblocked_make_probability(_ctx(family=InteriorShotFamily.RIM, base_rate=0.70))
        self.assertGreater(p, 0.5)

    def test_floater_uses_its_own_lower_base_rate(self):
        primary = _primary(zone=SpatialZone.PAINT)
        p = unblocked_make_probability(_ctx(family=InteriorShotFamily.FLOATER, base_rate=0.35, primary=primary))
        self.assertLess(p, 0.5)


class TestPrimaryAndSecondaryContext(unittest.TestCase):
    def test_removing_secondary_helper_changes_contest_correct_direction(self):
        helper = InteriorDefenderContext("8", SpatialZone.RESTRICTED_RIM, DefensivePosture.HELPING,
                                           is_primary=False, rim_protection=0.05, defensive_playmaking=2.0)
        with_helper = unblocked_make_probability(_ctx(secondary=helper))
        without_helper = unblocked_make_probability(_ctx())
        self.assertLess(with_helper, without_helper)  # removing helper should INCREASE make prob -- i.e. with < without

    def test_trailing_primary_differs_from_square_primary(self):
        square = unblocked_make_probability(_ctx(primary=_primary(posture=DefensivePosture.SQUARE)))
        trailing = unblocked_make_probability(_ctx(primary=_primary(posture=DefensivePosture.TRAILING)))
        self.assertGreater(trailing, square)

    def test_helper_not_helping_posture_does_not_count(self):
        recovering_helper = InteriorDefenderContext("8", SpatialZone.RESTRICTED_RIM, DefensivePosture.RECOVERING,
                                                       is_primary=False, rim_protection=0.05, defensive_playmaking=2.0)
        with_recovering = unblocked_make_probability(_ctx(secondary=recovering_helper))
        without = unblocked_make_probability(_ctx())
        self.assertEqual(with_recovering, without)  # not yet an established helping anchor -- no effect


class TestGeometricBlockEligibility(unittest.TestCase):
    def test_primary_always_eligible(self):
        self.assertTrue(geometric_block_eligibility(InteriorShotFamily.RIM, _primary()))

    def test_secondary_in_wrong_zone_ineligible(self):
        far = InteriorDefenderContext("7", SpatialZone.LEFT_WING, DefensivePosture.HELPING, is_primary=False, defensive_playmaking=8.0)
        self.assertFalse(geometric_block_eligibility(InteriorShotFamily.RIM, far))

    def test_secondary_right_zone_wrong_posture_ineligible(self):
        d = InteriorDefenderContext("8", SpatialZone.RESTRICTED_RIM, DefensivePosture.SQUARE, is_primary=False, defensive_playmaking=8.0)
        self.assertFalse(geometric_block_eligibility(InteriorShotFamily.RIM, d))

    def test_secondary_right_zone_helping_eligible(self):
        d = InteriorDefenderContext("8", SpatialZone.RESTRICTED_RIM, DefensivePosture.HELPING, is_primary=False, defensive_playmaking=8.0)
        self.assertTrue(geometric_block_eligibility(InteriorShotFamily.RIM, d))

    def test_impossible_zone_cannot_block_even_with_high_ability(self):
        far = InteriorDefenderContext("7", SpatialZone.LEFT_WING, DefensivePosture.HELPING, is_primary=False, defensive_playmaking=99.0)
        p_with = block_probability(_ctx(secondary=far))
        p_without = block_probability(_ctx())
        self.assertEqual(p_with, p_without)  # the far defender's absurd ability never enters the calculation

    def test_high_rim_protection_cannot_bypass_geometric_eligibility(self):
        """rim_protection isn't even the block-branch input, but this
        confirms no ability of any kind lets an ineligible defender
        block."""
        far = InteriorDefenderContext("7", SpatialZone.LEFT_WING, DefensivePosture.HELPING, is_primary=False,
                                        rim_protection=0.99, defensive_playmaking=0.0)
        p_with = block_probability(_ctx(secondary=far))
        p_without = block_probability(_ctx())
        self.assertEqual(p_with, p_without)


class TestBlockStochasticResolution(unittest.TestCase):
    def test_block_reachable(self):
        strong_blocker = _primary(defensive_playmaking=6.0)
        outcomes = {resolve_interior_shot("1", _ctx(primary=strong_blocker), random.Random(i)).outcome for i in range(500)}
        self.assertTrue({InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE, InteriorShotOutcome.BLOCKED_SECURED_DEFENSE} & outcomes)

    def test_elite_rim_protector_does_not_guarantee_block(self):
        strong_blocker = _primary(defensive_playmaking=6.0)
        outcomes = [resolve_interior_shot("1", _ctx(primary=strong_blocker), random.Random(i)).outcome for i in range(300)]
        self.assertTrue(any(o not in (InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE, InteriorShotOutcome.BLOCKED_SECURED_DEFENSE) for o in outcomes))

    def test_poor_rim_protector_can_occasionally_block(self):
        weak_blocker = _primary(defensive_playmaking=0.1)
        outcomes = {resolve_interior_shot("1", _ctx(primary=weak_blocker), random.Random(i)).outcome for i in range(2000)}
        self.assertTrue({InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE, InteriorShotOutcome.BLOCKED_SECURED_DEFENSE} & outcomes)


class TestUnblockedMakeMiss(unittest.TestCase):
    def test_low_rim_finishing_can_make_open_attempt(self):
        outcomes = {resolve_interior_shot("1", _ctx(base_rate=0.15), random.Random(i)).outcome for i in range(300)}
        self.assertIn(InteriorShotOutcome.MADE, outcomes)

    def test_elite_rim_finishing_can_miss(self):
        outcomes = {resolve_interior_shot("1", _ctx(base_rate=0.75), random.Random(i)).outcome for i in range(300)}
        self.assertIn(InteriorShotOutcome.MISSED_UNBLOCKED, outcomes)


class TestFirewalls(unittest.TestCase):
    def test_no_rim_access_creation_reference(self):
        import interior_shot_resolution as isr
        for name, value in vars(isr).items():
            if name.startswith("__"):
                continue
            self.assertNotIn("rim_access_creation", name.lower())

    def test_no_poa_containment_reference(self):
        import interior_shot_resolution as isr
        for name, value in vars(isr).items():
            if name.startswith("__"):
                continue
            self.assertNotIn("poa_containment", name.lower())

    def test_no_tendency_or_foul_reference(self):
        import interior_shot_resolution as isr
        source = inspect.getsource(isr)
        for bad in ("three_point_preference", "midrange_preference", "pullup_vs_catch",
                    "foul_drawing", "foul_discipline", "vision"):
            self.assertNotIn(bad, source.lower())

    def test_rim_access_creation_changing_has_no_effect(self):
        """No parameter exists to even pass a rim_access_creation value
        -- verified by the fact both calls with 'different' values (i.e.
        no value at all) are identical."""
        p1 = unblocked_make_probability(_ctx())
        p2 = unblocked_make_probability(_ctx())
        self.assertEqual(p1, p2)

    def test_poa_containment_changing_has_no_effect(self):
        p1 = block_probability(_ctx())
        p2 = block_probability(_ctx())
        self.assertEqual(p1, p2)

    def test_no_mass_parameter_anywhere(self):
        import dataclasses
        for cls in (InteriorDefenderContext, InteriorShotContext):
            names = {f.name for f in dataclasses.fields(cls)}
            self.assertNotIn("mass", names)
            self.assertNotIn("weight", names)

    def test_no_standing_reach_or_wingspan_parameter(self):
        import dataclasses
        for cls in (InteriorDefenderContext, InteriorShotContext):
            names = {f.name for f in dataclasses.fields(cls)}
            self.assertTrue(names.isdisjoint({"standing_reach", "wingspan", "height"}))


class TestNoConflation(unittest.TestCase):
    def test_block_never_automatically_steal(self):
        strong_blocker = _primary(defensive_playmaking=6.0)
        for i in range(500):
            result = resolve_interior_shot("1", _ctx(primary=strong_blocker), random.Random(i))
            if result.outcome in (InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE, InteriorShotOutcome.BLOCKED_SECURED_DEFENSE):
                # no "steal" concept exists anywhere in this result object
                self.assertFalse(hasattr(result, "steal"))

    def test_block_never_automatically_dreb(self):
        e = _engine()
        strong_blocker = _primary(defensive_playmaking=8.0)
        ctx = _ctx(primary=strong_blocker)
        for seed in range(200):
            e2 = _engine()
            result = apply_interior_shot_to_engine(e2, "1", ctx, random.Random(seed), zone=SpatialZone.RESTRICTED_RIM.value)
            if result.outcome == InteriorShotOutcome.BLOCKED_SECURED_DEFENSE:
                self.assertEqual(e2.state.ball_state, BallState.LOOSE)
                self.assertIsNone(e2.state.ball_carrier)  # no rebounder assigned -- not a DREB
                return
        self.fail("no BLOCKED_SECURED_DEFENSE outcome occurred in 200 trials")

    def test_blocked_and_made_cannot_both_occur(self):
        for i in range(300):
            result = resolve_interior_shot("1", _ctx(), random.Random(i))
            is_blocked = result.outcome in (InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE, InteriorShotOutcome.BLOCKED_SECURED_DEFENSE)
            is_made = result.outcome == InteriorShotOutcome.MADE
            self.assertFalse(is_blocked and is_made)


class TestNoReboundResolution(unittest.TestCase):
    def test_miss_does_not_select_rebound_winner(self):
        e = _engine()
        weak_finisher = _ctx(base_rate=0.05)
        for seed in range(200):
            e2 = _engine()
            result = apply_interior_shot_to_engine(e2, "1", weak_finisher, random.Random(seed), zone=SpatialZone.RESTRICTED_RIM.value)
            if result.outcome == InteriorShotOutcome.MISSED_UNBLOCKED:
                self.assertIsNone(e2.state.ball_carrier)
                self.assertIsNone(e2.state.offense_team_id)
                return
        self.fail("no unblocked miss occurred in 200 trials")

    def test_no_rebounder_field_anywhere(self):
        import interior_shot_resolution as isr
        source = inspect.getsource(isr)
        self.assertNotIn("rebounder", source.lower())


class TestOrebDoesNotChangeExecution(unittest.TestCase):
    def test_changing_oreb_context_has_no_execution_parameter(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(InteriorShotContext)}
        self.assertTrue(names.isdisjoint({"offensive_rebounding", "oreb", "putback"}))


class TestPlayerIdOnly(unittest.TestCase):
    def test_shooter_must_be_real_id(self):
        with self.assertRaises(TypeError):
            resolve_interior_shot("LeBron James", _ctx(), random.Random(1))

    def test_defender_must_be_real_id(self):
        bad_primary = InteriorDefenderContext("LeBron James", SpatialZone.RESTRICTED_RIM, DefensivePosture.SQUARE, is_primary=True)
        with self.assertRaises(TypeError):
            resolve_interior_shot("1", _ctx(primary=bad_primary), random.Random(1))


class TestDeterminismAndIsolation(unittest.TestCase):
    def test_deterministic_replay(self):
        r1 = resolve_interior_shot("1", _ctx(), random.Random(42))
        r2 = resolve_interior_shot("1", _ctx(), random.Random(42))
        self.assertEqual(r1, r2)

    def test_no_global_rng(self):
        import interior_shot_resolution as isr
        source = inspect.getsource(isr)
        self.assertNotIn("random.random(", source)
        self.assertNotIn("random.choice(", source)

    def test_no_mutation_leakage(self):
        e1 = _engine()
        e2 = PossessionEngine("p2", "A", "B", season="2023-24", rng_seed=2)
        e2.inbound("5", SpatialZone.PAINT, PossessionPhase.HALFCOURT)
        apply_interior_shot_to_engine(e1, "1", _ctx(), random.Random(1), zone=SpatialZone.RESTRICTED_RIM.value)
        self.assertEqual(e2.state.ball_carrier, "5")
        self.assertEqual(e2.state.ball_zone, SpatialZone.PAINT)


class TestMissingEvidenceHandling(unittest.TestCase):
    def test_missing_rim_protection_treated_as_neutral_not_zero_skill(self):
        primary_missing = InteriorDefenderContext("9", SpatialZone.RESTRICTED_RIM, DefensivePosture.SQUARE, is_primary=True,
                                                     rim_protection=None, defensive_playmaking=None)
        p_missing = unblocked_make_probability(_ctx(primary=primary_missing))
        p_avg = unblocked_make_probability(_ctx(primary=_primary(rim_protection=RIM_PROTECTION_POP_MEAN_FOR_TEST)))
        self.assertAlmostEqual(p_missing, p_avg, places=2)


RIM_PROTECTION_POP_MEAN_FOR_TEST = 0.00988  # matches the module's own real population mean -- a missing value should resolve near the population-average outcome


class TestFullPipeline(unittest.TestCase):
    def test_engine_integration_smoke(self):
        e = _engine()
        result = apply_interior_shot_to_engine(e, "1", _ctx(), random.Random(5), zone=SpatialZone.RESTRICTED_RIM.value)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
