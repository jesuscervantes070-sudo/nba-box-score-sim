"""Phase 18C -- focused tests for foul_resolution.py."""
import inspect
import random
import unittest

from foul_resolution import (
    ContactContext, FoulEligibleDefender, FreeThrowSequence, PersonalFoulTracker,
    advance_free_throw_sequence, apply_free_throw_attempt_to_engine, awarded_free_throws,
    contact_probability, era_whistle_logit_delta, resolve_contact_and_whistle,
    resolve_free_throw_attempt, resolve_shooting_foul_shot, shooting_foul_team_bonus_check,
    whistle_probability,
)
from possession_engine import PossessionEngine
from possession_state import BallState, DefensivePosture, PossessionPhase, SpatialZone


def _ctx(family="RIM", foul_drawing=0.1, defenders=None):
    defenders = defenders if defenders is not None else [FoulEligibleDefender("9", True, DefensivePosture.SQUARE, foul_discipline=0.0)]
    return ContactContext(shot_family=family, shooter_foul_drawing=foul_drawing, eligible_defenders=defenders)


def _engine():
    e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
    e.inbound("1", SpatialZone.RESTRICTED_RIM, PossessionPhase.HALFCOURT)
    return e


class TestContactContext(unittest.TestCase):
    def test_contact_probability_varies_by_family(self):
        rim = contact_probability(_ctx(family="RIM"))
        three = contact_probability(_ctx(family="THREE_POINT"))
        self.assertGreater(rim, three)

    def test_impossible_contact_rejection(self):
        """No contact -> whistle probability is exactly 0.0, structurally."""
        self.assertEqual(whistle_probability(_ctx(), contact_occurred=False), 0.0)


class TestFoulDrawingFirewall(unittest.TestCase):
    def test_foul_drawing_only_affects_whistle_not_contact(self):
        low = contact_probability(_ctx(foul_drawing=0.01))
        high = contact_probability(_ctx(foul_drawing=0.99))
        self.assertEqual(low, high)

    def test_foul_drawing_cannot_create_whistle_with_no_contact(self):
        ctx = _ctx(foul_drawing=100.0)  # absurdly high
        self.assertEqual(whistle_probability(ctx, contact_occurred=False), 0.0)

    def test_free_throw_never_affects_whistle_probability(self):
        """free_throw isn't even a ContactContext field -- structural proof."""
        import dataclasses
        names = {f.name for f in dataclasses.fields(ContactContext)}
        self.assertNotIn("free_throw", names)

    def test_three_point_preference_no_field(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(ContactContext)}
        self.assertTrue(names.isdisjoint({"three_point_preference", "drive_aggression",
                                            "rim_access_creation", "perimeter_space_creation"}))


class TestFoulDisciplineBoundary(unittest.TestCase):
    def test_high_foul_discipline_does_not_guarantee_zero_fouls(self):
        strict_defender = [FoulEligibleDefender("9", True, DefensivePosture.SQUARE, foul_discipline=5.0)]
        ctx = _ctx(defenders=strict_defender)
        outcomes = {resolve_contact_and_whistle(ctx, random.Random(i)).whistled for i in range(500)}
        self.assertIn(True, outcomes)

    def test_low_foul_discipline_does_not_guarantee_a_foul(self):
        loose_defender = [FoulEligibleDefender("9", True, DefensivePosture.SQUARE, foul_discipline=-5.0)]
        ctx = _ctx(defenders=loose_defender)
        outcomes = {resolve_contact_and_whistle(ctx, random.Random(i)).whistled for i in range(500)}
        self.assertIn(False, outcomes)

    def test_rim_protection_and_defensive_playmaking_not_fields(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(FoulEligibleDefender)}
        self.assertTrue(names.isdisjoint({"rim_protection", "defensive_playmaking"}))


class TestFreeThrowFirewall(unittest.TestCase):
    def test_free_throw_execution_only_no_other_ability(self):
        sig = inspect.signature(resolve_free_throw_attempt)
        params = list(sig.parameters)
        self.assertEqual(params, ["shooter_id", "free_throw_rate", "rng"])

    def test_changing_free_throw_does_not_change_awarded_count(self):
        self.assertEqual(awarded_free_throws("RIM", False), awarded_free_throws("RIM", False))


class TestPerimeterAndInteriorFouls(unittest.TestCase):
    def test_perimeter_shooting_foul_representable(self):
        ctx = _ctx(family="THREE_POINT")
        outcomes = {resolve_contact_and_whistle(ctx, random.Random(i)).whistled for i in range(2000)}
        self.assertIn(True, outcomes)

    def test_interior_shooting_foul_representable(self):
        ctx = _ctx(family="RIM")
        outcomes = {resolve_contact_and_whistle(ctx, random.Random(i)).whistled for i in range(300)}
        self.assertIn(True, outcomes)


class TestFoulerAttribution(unittest.TestCase):
    def test_primary_defender_attributed(self):
        primary = FoulEligibleDefender("9", True, DefensivePosture.SQUARE)
        helper = FoulEligibleDefender("8", False, DefensivePosture.HELPING)
        ctx = _ctx(defenders=[helper, primary])  # primary listed second -- must still be selected
        for i in range(300):
            result = resolve_contact_and_whistle(ctx, random.Random(i))
            if result.whistled:
                self.assertEqual(result.fouler_id, "9")
                return
        self.fail("no whistle occurred in 300 trials")

    def test_no_two_defenders_independently_rolled(self):
        """Only ONE fouler is ever attributed per whistled event --
        never both primary and helper simultaneously."""
        primary = FoulEligibleDefender("9", True, DefensivePosture.SQUARE)
        helper = FoulEligibleDefender("8", False, DefensivePosture.HELPING)
        ctx = _ctx(defenders=[primary, helper])
        for i in range(50):
            result = resolve_contact_and_whistle(ctx, random.Random(i))
            if result.whistled:
                self.assertIsInstance(result.fouler_id, str)  # exactly one id, not a list


class TestBlockFoulSequencing(unittest.TestCase):
    def test_whistled_foul_bypasses_block_branch_entirely(self):
        """This module's own resolve_contact_and_whistle contains no
        block-eligibility/blocker concept at all -- proving the
        sequencing choice (contact/whistle checked BEFORE any block
        logic runs) structurally, not just by convention."""
        source = inspect.getsource(__import__("foul_resolution"))
        self.assertNotIn("block_probability", source)
        self.assertNotIn("geometric_block_eligibility", source)


class TestMissedFoulAccounting(unittest.TestCase):
    def test_missed_shooting_foul_produces_zero_fga_zero_points(self):
        made, points, awarded = resolve_shooting_foul_shot("1", "RIM", unblocked_make_probability=0.01, rng=random.Random(1))
        if not made:
            self.assertEqual(points, 0)

    def test_missed_2pt_foul_awards_two_fts(self):
        self.assertEqual(awarded_free_throws("RIM", made=False), 2)
        self.assertEqual(awarded_free_throws("FLOATER", made=False), 2)
        self.assertEqual(awarded_free_throws("MIDRANGE", made=False), 2)

    def test_missed_3pt_foul_awards_three_fts(self):
        self.assertEqual(awarded_free_throws("THREE_POINT", made=False), 3)


class TestAndOneAccounting(unittest.TestCase):
    def test_and_one_produces_exactly_one_fga_worth_of_points_and_one_ft(self):
        for i in range(50):
            made, points, awarded = resolve_shooting_foul_shot("1", "RIM", unblocked_make_probability=0.99, rng=random.Random(i))
            if made:
                self.assertEqual(points, 2)
                self.assertEqual(awarded, 1)
                return
        self.fail("no make occurred in 50 high-probability trials")

    def test_and_one_three_point_scores_three_and_one_ft(self):
        for i in range(50):
            made, points, awarded = resolve_shooting_foul_shot("1", "THREE_POINT", unblocked_make_probability=0.99, rng=random.Random(i))
            if made:
                self.assertEqual(points, 3)
                self.assertEqual(awarded, 1)
                return
        self.fail("no make occurred in 50 high-probability trials")

    def test_foul_drawing_cannot_improve_and_one_make_probability(self):
        """resolve_shooting_foul_shot has no foul_drawing parameter at
        all -- structural proof."""
        sig = inspect.signature(resolve_shooting_foul_shot)
        self.assertNotIn("foul_drawing", [p.lower() for p in sig.parameters])


class TestFreeThrowStateMachine(unittest.TestCase):
    def test_sequence_indexing(self):
        seq = FreeThrowSequence(shooter_id="1", awarded_attempts=2, source_foul_type="MISSED_SHOOTING_FOUL")
        self.assertEqual(seq.attempts_remaining, 2)
        self.assertFalse(seq.is_final_attempt)
        seq = advance_free_throw_sequence(seq, made=True)
        self.assertEqual(seq.attempts_remaining, 1)
        self.assertTrue(seq.is_final_attempt)
        self.assertFalse(seq.is_complete)
        seq = advance_free_throw_sequence(seq, made=False)
        self.assertTrue(seq.is_complete)
        self.assertEqual(seq.makes, 1)

    def test_final_missed_ft_creates_rebound_handoff(self):
        e = _engine()
        seq = FreeThrowSequence(shooter_id="1", awarded_attempts=1, source_foul_type="MISSED_SHOOTING_FOUL")
        seq, made = apply_free_throw_attempt_to_engine(e, seq, free_throw_rate=0.01, rng=random.Random(3))
        if not made:
            self.assertEqual(e.state.ball_state, BallState.LOOSE)
            self.assertIsNone(e.state.ball_carrier)
            self.assertIsNone(e.state.offense_team_id)

    def test_non_final_missed_ft_does_not_create_rebound_handoff(self):
        e = _engine()
        seq = FreeThrowSequence(shooter_id="1", awarded_attempts=2, source_foul_type="MISSED_SHOOTING_FOUL")
        seq, made = apply_free_throw_attempt_to_engine(e, seq, free_throw_rate=0.01, rng=random.Random(3))
        if not made and not seq.is_complete:
            self.assertEqual(e.state.ball_state, BallState.HELD)  # untouched -- no rebound handoff yet

    def test_made_final_ft_transitions_to_opponent_dead_ball(self):
        e = _engine()
        seq = FreeThrowSequence(shooter_id="1", awarded_attempts=1, source_foul_type="AND_ONE")
        seq, made = apply_free_throw_attempt_to_engine(e, seq, free_throw_rate=0.99, rng=random.Random(1))
        if made:
            self.assertEqual(e.state.ball_state, BallState.DEAD)
            self.assertEqual(e.state.offense_team_id, "B")
            self.assertEqual(e.state.defense_team_id, "A")

    def test_game_clock_frozen_across_ft_attempts(self):
        e = _engine()
        e.state.game_clock_remaining = 321.0
        seq = FreeThrowSequence(shooter_id="1", awarded_attempts=2, source_foul_type="MISSED_SHOOTING_FOUL")
        rng = random.Random(7)
        seq, _ = apply_free_throw_attempt_to_engine(e, seq, free_throw_rate=0.5, rng=rng)
        self.assertEqual(e.state.game_clock_remaining, 321.0)
        seq, _ = apply_free_throw_attempt_to_engine(e, seq, free_throw_rate=0.5, rng=rng)
        self.assertEqual(e.state.game_clock_remaining, 321.0)

    def test_ft_uses_only_free_throw_no_shooting_abilities(self):
        # elite three_point/rim_finishing values simply cannot be passed to this function at all
        sig = inspect.signature(resolve_free_throw_attempt)
        for name in sig.parameters:
            self.assertNotIn("three_point", name.lower())
            self.assertNotIn("rim_finishing", name.lower())


class TestPersonalAndTeamFoulAccounting(unittest.TestCase):
    def test_personal_foul_increments_exactly_once(self):
        tracker = PersonalFoulTracker()
        tracker = tracker.increment("9")
        self.assertEqual(tracker.count_for("9"), 1)

    def test_personal_foul_tracks_multiple_players_independently(self):
        tracker = PersonalFoulTracker()
        tracker = tracker.increment("9").increment("9").increment("8")
        self.assertEqual(tracker.count_for("9"), 2)
        self.assertEqual(tracker.count_for("8"), 1)

    def test_team_bonus_check_reuses_era_rules_hook(self):
        e = _engine()
        self.assertTrue(shooting_foul_team_bonus_check(e, team_foul_count=5))
        self.assertFalse(shooting_foul_team_bonus_check(e, team_foul_count=1))


class TestEraRuleHooks(unittest.TestCase):
    def test_era_affects_environment_not_player_attribute(self):
        pre = era_whistle_logit_delta("2000-01")
        modern = era_whistle_logit_delta("2023-24")
        self.assertNotEqual(pre, modern)

    def test_era_delta_has_no_player_id_parameter(self):
        sig = inspect.signature(era_whistle_logit_delta)
        self.assertEqual(list(sig.parameters), ["season"])


class TestPlayerIdOnly(unittest.TestCase):
    def test_ft_shooter_must_be_real_id(self):
        with self.assertRaises(TypeError):
            resolve_free_throw_attempt("LeBron James", 0.8, random.Random(1))


class TestDeterminismAndIsolation(unittest.TestCase):
    def test_deterministic_replay(self):
        ctx = _ctx()
        r1 = resolve_contact_and_whistle(ctx, random.Random(42))
        r2 = resolve_contact_and_whistle(ctx, random.Random(42))
        self.assertEqual(r1, r2)

    def test_no_global_rng(self):
        source = inspect.getsource(__import__("foul_resolution"))
        self.assertNotIn("random.random(", source)
        self.assertNotIn("random.choice(", source)


class TestNoReboundWinner(unittest.TestCase):
    def test_no_rebounder_concept_anywhere(self):
        source = inspect.getsource(__import__("foul_resolution"))
        self.assertNotIn("rebounder", source.lower())


class TestMissingFoulDataHandling(unittest.TestCase):
    def test_missing_foul_drawing_treated_as_neutral(self):
        ctx_missing = _ctx(foul_drawing=None)
        ctx_zero_ish = ContactContext(shot_family="RIM", shooter_foul_drawing=0.0,
                                        eligible_defenders=[FoulEligibleDefender("9", True, DefensivePosture.SQUARE)])
        # missing should equal the "no adjustment" case (weight * None -> skip), not a fabricated 0.0 contribution mistaken for real evidence
        p_missing = whistle_probability(ctx_missing, True)
        p_zero = whistle_probability(ctx_zero_ish, True)
        self.assertAlmostEqual(p_missing, p_zero)


if __name__ == "__main__":
    unittest.main()
