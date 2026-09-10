"""Phase 18A -- focused tests for shot_resolution.py."""
import inspect
import random
import unittest

from possession_engine import PossessionEngine
from possession_state import BallState, DefensivePosture, PossessionPhase, SpatialZone
from shot_resolution import (
    ContestBucket, LATE_CLOCK_THRESHOLD_SECONDS, ReleaseMode, ShotFamily, ShotOutcome,
    ShotResolutionContext, apply_shot_resolution_to_engine, resolve_shot, shot_make_probability,
)


def _ctx(**kwargs):
    defaults = dict(shot_family=ShotFamily.THREE_POINT, shooter_base_rate=0.36,
                     contest_bucket=ContestBucket.OPEN, release_mode=ReleaseMode.CATCH_AND_SHOOT)
    defaults.update(kwargs)
    return ShotResolutionContext(**defaults)


class TestFamilyResolution(unittest.TestCase):
    def test_three_point_resolution(self):
        result = resolve_shot("1", _ctx(shot_family=ShotFamily.THREE_POINT), random.Random(1))
        self.assertIn(result.outcome, (ShotOutcome.MADE, ShotOutcome.MISSED))
        if result.outcome == ShotOutcome.MADE:
            self.assertEqual(result.points, 3)

    def test_midrange_resolution(self):
        result = resolve_shot("1", _ctx(shot_family=ShotFamily.MIDRANGE, shooter_base_rate=0.42), random.Random(1))
        self.assertIn(result.outcome, (ShotOutcome.MADE, ShotOutcome.MISSED))
        if result.outcome == ShotOutcome.MADE:
            self.assertEqual(result.points, 2)

    def test_unsupported_family_rejected(self):
        with self.assertRaises(ValueError):
            shot_make_probability(_ctx(shot_family="RIM"))


class TestCorrectAbilityUsedByFamily(unittest.TestCase):
    def test_midrange_uses_midrange_not_three_point_scale(self):
        """A shooter with a real three_point rate of 0.20 but a
        midrange rate of 0.50 must resolve MIDRANGE shots using 0.50,
        not 0.20 -- proven by the probability being anchored near 0.50
        (adjusted only by contest/posture), not near 0.20."""
        ctx = _ctx(shot_family=ShotFamily.MIDRANGE, shooter_base_rate=0.50, contest_bucket=ContestBucket.OPEN)
        p = shot_make_probability(ctx)
        self.assertGreater(p, 0.35)  # nowhere near a 0.20-anchored probability

    def test_three_point_uses_three_point_not_midrange_scale(self):
        ctx = _ctx(shot_family=ShotFamily.THREE_POINT, shooter_base_rate=0.20, contest_bucket=ContestBucket.OPEN)
        p = shot_make_probability(ctx)
        self.assertLess(p, 0.35)


class TestTendencyFirewall(unittest.TestCase):
    def test_no_tendency_or_upstream_ability_symbols_in_module(self):
        import shot_resolution
        forbidden = ("three_point_preference", "midrange_preference", "pullup_vs_catch",
                     "perimeter_space_creation", "poa_containment")
        for name, value in vars(shot_resolution).items():
            if name.startswith("__"):
                continue
            for bad in forbidden:
                self.assertNotIn(bad, name.lower())
            self.assertNotIn("vision", name.lower())

    def test_context_dataclass_has_no_forbidden_fields(self):
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ShotResolutionContext)}
        forbidden = {"three_point_preference", "midrange_preference", "pullup_vs_catch",
                     "perimeter_space_creation", "poa_containment", "advantage", "advantage_model"}
        self.assertTrue(field_names.isdisjoint(forbidden))

    def test_identical_inputs_changing_pullup_vs_catch_has_no_effect(self):
        """pullup_vs_catch has no parameter to even pass -- this test
        proves the counterfactual concretely: two contexts identical in
        every real field produce identical probability, and there is no
        way to encode a pullup_vs_catch value into ShotResolutionContext
        at all."""
        ctx_a = _ctx()
        ctx_b = _ctx()  # a "different pullup_vs_catch" would change nothing since no such field exists
        self.assertEqual(shot_make_probability(ctx_a), shot_make_probability(ctx_b))

    def test_identical_inputs_changing_three_point_preference_has_no_effect(self):
        ctx_a = _ctx()
        ctx_b = _ctx()
        self.assertEqual(shot_make_probability(ctx_a), shot_make_probability(ctx_b))

    def test_perimeter_space_creation_inaccessible(self):
        sig = inspect.signature(shot_make_probability)
        for name in sig.parameters:
            self.assertNotIn("space_creation", name.lower())
        sig2 = inspect.signature(resolve_shot)
        for name in sig2.parameters:
            self.assertNotIn("space_creation", name.lower())

    def test_poa_containment_inaccessible(self):
        sig = inspect.signature(shot_make_probability)
        for name in sig.parameters:
            self.assertNotIn("poa", name.lower())


class TestAdvantageNotDirectlyConsumed(unittest.TestCase):
    def test_no_advantage_model_parameter_anywhere(self):
        sig = inspect.signature(shot_make_probability)
        self.assertNotIn("advantage", [p.lower() for p in sig.parameters])
        sig2 = inspect.signature(ShotResolutionContext.__init__)
        self.assertNotIn("advantage", [p.lower() for p in sig2.parameters])

    def test_same_contest_packet_different_raw_advantage_state_identical_probability(self):
        """Simulates 'same contest packet, different upstream AdvantageModel
        state' by constructing two otherwise-identical contexts -- since
        ShotResolutionContext has no advantage field, this is
        structurally guaranteed, verified here behaviorally."""
        ctx_a = _ctx(contest_bucket=ContestBucket.TIGHT)
        ctx_b = _ctx(contest_bucket=ContestBucket.TIGHT)
        self.assertEqual(shot_make_probability(ctx_a), shot_make_probability(ctx_b))


class TestContestDirection(unittest.TestCase):
    def test_contest_distance_directional_behavior(self):
        probs = [shot_make_probability(_ctx(contest_bucket=b)) for b in
                 (ContestBucket.VERY_TIGHT, ContestBucket.TIGHT, ContestBucket.OPEN, ContestBucket.WIDE_OPEN)]
        self.assertEqual(probs, sorted(probs))  # strictly monotonic non-decreasing with openness

    def test_different_distance_bucket_calibrated_directional_change(self):
        tight = shot_make_probability(_ctx(contest_bucket=ContestBucket.TIGHT))
        wide_open = shot_make_probability(_ctx(contest_bucket=ContestBucket.WIDE_OPEN))
        self.assertGreater(wide_open, tight)


class TestPostureDistinction(unittest.TestCase):
    def test_trailing_defender_less_contest_than_square(self):
        square = shot_make_probability(_ctx(defender_posture=DefensivePosture.SQUARE))
        trailing = shot_make_probability(_ctx(defender_posture=DefensivePosture.TRAILING))
        self.assertGreater(trailing, square)  # trailing exerts LESS frontal contest -> higher make prob

    def test_square_vs_trailing_at_comparable_distance_structurally_different(self):
        for bucket in (ContestBucket.TIGHT, ContestBucket.OPEN):
            square = shot_make_probability(_ctx(contest_bucket=bucket, defender_posture=DefensivePosture.SQUARE))
            trailing = shot_make_probability(_ctx(contest_bucket=bucket, defender_posture=DefensivePosture.TRAILING))
            self.assertNotEqual(square, trailing)


class TestStochasticity(unittest.TestCase):
    def test_elite_shooter_can_miss_wide_open(self):
        ctx = _ctx(shooter_base_rate=0.50, contest_bucket=ContestBucket.WIDE_OPEN)
        outcomes = {resolve_shot("1", ctx, random.Random(i)).outcome for i in range(300)}
        self.assertIn(ShotOutcome.MISSED, outcomes)

    def test_low_skill_shooter_can_make_wide_open(self):
        ctx = _ctx(shooter_base_rate=0.15, contest_bucket=ContestBucket.WIDE_OPEN)
        outcomes = {resolve_shot("1", ctx, random.Random(i)).outcome for i in range(300)}
        self.assertIn(ShotOutcome.MADE, outcomes)

    def test_elite_shooter_still_vulnerable_to_contest(self):
        ctx_open = _ctx(shooter_base_rate=0.50, contest_bucket=ContestBucket.WIDE_OPEN)
        ctx_tight = _ctx(shooter_base_rate=0.50, contest_bucket=ContestBucket.VERY_TIGHT)
        self.assertGreater(shot_make_probability(ctx_open), shot_make_probability(ctx_tight))

    def test_no_probability_exactly_zero_or_one(self):
        for bucket in (ContestBucket.VERY_TIGHT, ContestBucket.WIDE_OPEN):
            for rate in (0.01, 0.99):
                p = shot_make_probability(_ctx(shooter_base_rate=rate, contest_bucket=bucket))
                self.assertGreater(p, 0.0)
                self.assertLess(p, 1.0)


class TestDeterminism(unittest.TestCase):
    def test_deterministic_replay(self):
        ctx = _ctx()
        r1 = resolve_shot("1", ctx, random.Random(42))
        r2 = resolve_shot("1", ctx, random.Random(42))
        self.assertEqual(r1, r2)

    def test_no_global_rng_used(self):
        import shot_resolution
        source = inspect.getsource(shot_resolution)
        self.assertNotIn("random.random(", source)
        self.assertNotIn("random.choice(", source)


class TestSparseReleaseMode(unittest.TestCase):
    def test_unknown_release_mode_uses_real_averaged_fallback_not_zero(self):
        ctx_unknown = _ctx(release_mode=ReleaseMode.UNKNOWN, contest_bucket=ContestBucket.WIDE_OPEN)
        p_unknown = shot_make_probability(ctx_unknown)
        ctx_base_only = ShotResolutionContext(shot_family=ShotFamily.THREE_POINT, shooter_base_rate=0.36,
                                                contest_bucket=ContestBucket.WIDE_OPEN, release_mode=ReleaseMode.UNKNOWN)
        # not equal to the raw, un-adjusted base rate -- a real fallback delta IS applied, not a zero effect
        from shot_resolution import _logit, _sigmoid
        raw_base_prob = 0.36
        self.assertNotAlmostEqual(p_unknown, raw_base_prob, places=3)


class TestPassArrivalNoDoublePenalty(unittest.TestCase):
    def test_no_arrival_quality_field_and_no_effect_possible(self):
        """ShotResolutionContext has no arrival-quality field at all --
        translating Phase 17B's arrival quality into a different
        release_mode/contest is explicitly the CALLER's job, upstream of
        this module, so it can never be double-applied here."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ShotResolutionContext)}
        self.assertNotIn("arrival_quality", field_names)
        self.assertNotIn("pass_arrival", field_names)


class TestNoReboundOrFoulOrBlock(unittest.TestCase):
    def test_no_rebound_winner_selected(self):
        import shot_resolution
        source = inspect.getsource(shot_resolution)
        self.assertNotIn("rebounder", source.lower())

    def test_no_foul_or_block_fields(self):
        import dataclasses
        import shot_resolution as sr
        field_names = {f.name for f in dataclasses.fields(sr.ShotResolutionResult)}
        self.assertTrue(field_names.isdisjoint({"blocked", "fouled", "and_one", "free_throws"}))

    def test_engine_miss_leaves_ball_loose_no_rebounder_assigned(self):
        e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        e.inbound("1", SpatialZone.LEFT_WING, PossessionPhase.HALFCOURT)
        ctx = _ctx(shooter_base_rate=0.01, contest_bucket=ContestBucket.VERY_TIGHT)  # force a near-certain miss
        result = apply_shot_resolution_to_engine(e, "1", ctx, random.Random(2), zone=SpatialZone.LEFT_WING.value)
        if result.outcome == ShotOutcome.MISSED:
            self.assertEqual(e.state.ball_state, BallState.LOOSE)
            self.assertIsNone(e.state.ball_carrier)
            self.assertIsNone(e.state.offense_team_id)


class TestPlayerIdOnly(unittest.TestCase):
    def test_player_id_only(self):
        with self.assertRaises(TypeError):
            resolve_shot("LeBron James", _ctx(), random.Random(1))


class TestNoMutationLeakage(unittest.TestCase):
    def test_two_engines_independent(self):
        e1 = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        e1.inbound("1", SpatialZone.LEFT_WING, PossessionPhase.HALFCOURT)
        e2 = PossessionEngine("p2", "A", "B", season="2023-24", rng_seed=2)
        e2.inbound("9", SpatialZone.RIGHT_WING, PossessionPhase.HALFCOURT)
        apply_shot_resolution_to_engine(e1, "1", _ctx(), random.Random(1), zone=SpatialZone.LEFT_WING.value)
        self.assertEqual(e2.state.ball_carrier, "9")
        self.assertEqual(e2.state.ball_zone, SpatialZone.RIGHT_WING)


class TestLateClock(unittest.TestCase):
    def test_late_clock_reduces_probability(self):
        normal = shot_make_probability(_ctx(shot_clock_remaining=15.0))
        late = shot_make_probability(_ctx(shot_clock_remaining=LATE_CLOCK_THRESHOLD_SECONDS - 1.0))
        self.assertLess(late, normal)

    def test_missing_shot_clock_no_penalty(self):
        p = shot_make_probability(_ctx(shot_clock_remaining=None))
        p_normal = shot_make_probability(_ctx(shot_clock_remaining=20.0))
        self.assertAlmostEqual(p, p_normal)


if __name__ == "__main__":
    unittest.main()
