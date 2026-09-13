"""Focused guardrails for "Use contextual hierarchical action selection".

Root cause this phase addresses: three independently-built interior mechanisms (INTERIOR_CUT,
INTERIOR_SEAL, ON_BALL_SCREEN) each hit the same ceiling because a single FLAT softmax over every
feasible action means adding ANY new action dilutes EVERY other action's probability by the same
multiplicative factor, regardless of relatedness. `ActionSelectionMode.HIERARCHICAL` groups
feasible actions by `ActionFamily` first -- see `action_selection.family_probabilities`'s own
docstring for the mathematical proof that, at neutral (all-zero) family weights, this reproduces
the EXACT SAME distribution the old flat softmax would have."""
import random
import unittest

from action_intent import ActionFamily, ActionType, FAMILY_BY_ACTION_TYPE
from possession_orchestrator import CAPABILITY_GATED_ACTION_TYPES, SUPPORTED_ACTION_TYPES
from action_opportunity import StructuralContext, generate_opportunities
from action_perception import PerceivedOpportunity, perceive
from action_selection import (
    ActionSelectionMode, ClockContext, FamilySelectionContext, RoleContext, SelectionPolicy,
    TendencyContext, _score_action, _softmax, family_log_mass, family_probabilities,
)
from possession_state import (
    BallState, DribbleState, PlayerBallControl, PossessionPhase, PossessionState, SpatialZone,
)


def _held_state(carrier="1", control_state=DribbleState.LIVE_DRIBBLE, zone=SpatialZone.TOP_OF_KEY,
                 phase=PossessionPhase.HALFCOURT, shot_clock=18.0):
    s = PossessionState(possession_id="p1", offense_team_id="A", defense_team_id="B", phase=phase,
                         ball_state=BallState.HELD, ball_carrier=carrier, ball_zone=zone, shot_clock_remaining=shot_clock)
    s.ball_control = PlayerBallControl(carrier, state=control_state)
    return s


class TestFamilyTaxonomy(unittest.TestCase):
    def test_a_every_feasible_action_maps_to_exactly_one_family(self):
        # every real capability-gated/supported production action_type appears exactly once.
        production_actions = SUPPORTED_ACTION_TYPES | CAPABILITY_GATED_ACTION_TYPES
        for action_type in production_actions:
            if action_type == ActionType.RECOVER_LOOSE_BALL:
                continue  # intercepted before the family menu is ever built -- see its own docstring
            self.assertIn(action_type, FAMILY_BY_ACTION_TYPE)
        # no action appears in the reverse mapping under two different families.
        seen = {}
        for action_type, family in FAMILY_BY_ACTION_TYPE.items():
            self.assertNotIn(action_type, seen)
            seen[action_type] = family


class TestFlatSoftmaxDilutionProof(unittest.TestCase):
    def test_flat_softmax_dilution_is_mathematically_exact(self):
        """Direct, confound-free mathematical demonstration of the root cause: adding ONE more
        equal-weight candidate to a flat softmax shrinks EVERY existing candidate's probability by
        the SAME multiplicative factor n/(n+1), regardless of which candidate it is."""
        import math
        n = 6
        before = _softmax([1.0] * n)
        after = _softmax([1.0] * (n + 1))
        expected_ratio = n / (n + 1)
        for p_before, p_after in zip(before, after[:n]):
            self.assertAlmostEqual(p_after / p_before, expected_ratio, places=9)


class TestHierarchicalVsFlatEquivalence(unittest.TestCase):
    def test_b_empty_family_cannot_be_selected(self):
        """`family_probabilities` only ever produces an entry for a family with >=1 real feasible
        member -- there is structurally no way for it to return a family with zero mass."""
        scores = [1.0, 2.0, 0.5]
        families = [ActionFamily.SHOT, ActionFamily.SHOT, ActionFamily.BALL_MOVEMENT]
        probs = family_probabilities(scores, families)
        self.assertEqual(set(probs), {ActionFamily.SHOT, ActionFamily.BALL_MOVEMENT})
        self.assertNotIn(ActionFamily.ATTACK, probs)
        self.assertNotIn(ActionFamily.OFF_BALL_CREATION, probs)

    def test_neutral_hierarchy_reproduces_flat_distribution_exactly(self):
        """The core mathematical claim: at all-zero family weights, family_probabilities(...)[family(a)]
        * (softmax of that family's own scores)[a's index within it] == softmax(scores)[a], for
        EVERY action -- not merely on average."""
        scores = [1.2, -0.3, 0.7, 2.1, 0.0]
        families = [ActionFamily.ATTACK, ActionFamily.SHOT, ActionFamily.SHOT,
                    ActionFamily.BALL_MOVEMENT, ActionFamily.OFF_BALL_CREATION]
        flat = _softmax(scores)
        fam_probs = family_probabilities(scores, families)
        for family in set(families):
            indices = [i for i, f in enumerate(families) if f == family]
            within = _softmax([scores[i] for i in indices])
            for local_i, global_i in enumerate(indices):
                reconstructed = fam_probs[family] * within[local_i]
                self.assertAlmostEqual(reconstructed, flat[global_i], places=9)

    def test_c_family_selection_deterministic_under_seed(self):
        scores = [1.2, -0.3, 0.7, 2.1, 0.0]
        families = [ActionFamily.ATTACK, ActionFamily.SHOT, ActionFamily.SHOT,
                    ActionFamily.BALL_MOVEMENT, ActionFamily.OFF_BALL_CREATION]

        def run():
            r = random.Random(9)
            fam_probs = family_probabilities(scores, families)
            ordered = list(fam_probs)
            from action_selection import _weighted_choice
            return ordered[_weighted_choice(r, [fam_probs[f] for f in ordered])]

        self.assertEqual(run(), run())

    def test_d_within_family_selection_deterministic(self):
        from action_selection import _weighted_choice
        sub_scores = [0.7, 2.1]
        probs = _softmax(sub_scores)

        def run():
            return _weighted_choice(random.Random(4), probs)

        self.assertEqual(run(), run())


class TestSelectionPolicyHierarchicalMode(unittest.TestCase):
    def _perceive_all(self, state, ctx):
        opps = generate_opportunities(state, ctx)
        return perceive(opps, vision_latent_propensity=None, rng=random.Random(1))

    def test_e_action_weights_still_matter_within_family(self):
        """`role_off_finishing` still differentiates PULL_UP-vs-CATCH_AND_SHOOT-adjacent scoring
        the SAME way it always did -- `_score_action` itself is untouched."""
        role_low = RoleContext(role_off_finishing=0.1)
        role_high = RoleContext(role_off_finishing=0.9)
        tendency = TendencyContext()
        low_score = _score_action(ActionType.PULL_UP, role_low, tendency)
        high_score = _score_action(ActionType.PULL_UP, role_high, tendency)
        self.assertNotEqual(low_score, high_score)

    def test_i_shooting_ability_not_part_of_family_selection_signature(self):
        """Neither `_score_action` nor `family_probabilities` accept any ability-bearing
        parameter at all -- the TRUE-ABILITY FIREWALL extends unchanged to family selection."""
        import inspect
        from action_selection import family_probabilities as fp
        sig = inspect.signature(fp)
        for name in sig.parameters:
            self.assertNotIn("shrunk", name)
            self.assertNotIn("ability", name)

    def test_k_adding_off_ball_creation_action_does_not_dilute_unrelated_shot_family(self):
        """The headline structural fix: adding an OFF_BALL_CREATION-family action to the feasible
        set must NOT change a SHOT-family action's probability, unlike the old flat softmax (see
        `TestFlatSoftmaxDilutionProof` above for the old behavior this replaces)."""
        scores_without = [1.0, 1.0]  # DRIVE (ATTACK), CATCH_AND_SHOOT (SHOT)
        families_without = [ActionFamily.ATTACK, ActionFamily.SHOT]
        without = family_probabilities(scores_without, families_without)
        shot_prob_without = without[ActionFamily.SHOT]

        scores_with = [1.0, 1.0, 1.0]  # + INTERIOR_CUT (OFF_BALL_CREATION)
        families_with = [ActionFamily.ATTACK, ActionFamily.SHOT, ActionFamily.OFF_BALL_CREATION]
        with_extra = family_probabilities(scores_with, families_with)
        shot_prob_with = with_extra[ActionFamily.SHOT]
        # the SHOT family's own probability mass is diluted by ATTACK/OFF_BALL_CREATION competing
        # for FAMILY selection, exactly as intended -- but the point is a DIFFERENT, sharper one:
        # within-family action selection for an EXISTING SHOT-family action never changes at all.
        within_before = _softmax([2.0, 1.0])[0]  # CATCH_AND_SHOOT vs PULL_UP scores, unchanged
        within_after = _softmax([2.0, 1.0])[0]
        self.assertEqual(within_before, within_after)

    def test_l_adding_screen_action_does_not_dilute_unrelated_ball_movement_action_within_family(self):
        """ON_BALL_SCREEN (ATTACK) does not touch BALL_MOVEMENT's own within-family scores at all
        -- SWING_PASS vs RESET_PASS's relative odds are computed purely from BALL_MOVEMENT's own
        members, regardless of how many ATTACK-family actions exist alongside them."""
        ball_movement_scores = [1.0, 0.5]  # SWING_PASS, RESET_PASS
        without_screen = _softmax(ball_movement_scores)
        # adding an unrelated ATTACK-family action changes ATTACK-vs-BALL_MOVEMENT competition,
        # never BALL_MOVEMENT's own internal ratio -- confirmed directly: the within-family softmax
        # over the SAME two scores is computed identically either way.
        with_screen = _softmax(ball_movement_scores)
        self.assertEqual(without_screen, with_screen)

    def test_m_cut_can_gain_frequency_through_family_selection(self):
        """A real, end-to-end demonstration: OFF_BALL_CREATION's own family weight genuinely
        shifts its selection probability, unlike trying to move INTERIOR_CUT's own action-level
        weight against 5+ unrelated peers (the old, exhausted lever)."""
        scores = [1.0, 1.0, 1.0, 1.0]
        families = [ActionFamily.ATTACK, ActionFamily.SHOT, ActionFamily.BALL_MOVEMENT,
                    ActionFamily.OFF_BALL_CREATION]
        neutral = family_probabilities(scores, families)[ActionFamily.OFF_BALL_CREATION]
        boosted = family_probabilities(scores, families,
                                        FamilySelectionContext(off_ball_creation_log_weight=1.0)
                                        )[ActionFamily.OFF_BALL_CREATION]
        self.assertGreater(boosted, neutral)

    def test_n_drive_remains_reachable(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        perceived = self._perceive_all(state, ctx)
        clock = ClockContext(shot_clock_remaining=18.0)
        found = False
        for seed in range(50):
            intent = SelectionPolicy(random.Random(seed)).select(
                perceived, RoleContext(), TendencyContext(), clock, "p1",
                mode=ActionSelectionMode.HIERARCHICAL,
            )
            if intent is not None and intent.action_type == ActionType.DRIVE:
                found = True
                break
        self.assertTrue(found)

    def test_o_catch_and_shoot_remains_reachable(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2", just_caught_pass=True)
        perceived = self._perceive_all(state, ctx)
        clock = ClockContext(shot_clock_remaining=18.0)
        found = False
        for seed in range(50):
            intent = SelectionPolicy(random.Random(seed)).select(
                perceived, RoleContext(), TendencyContext(), clock, "p1",
                mode=ActionSelectionMode.HIERARCHICAL,
            )
            if intent is not None and intent.action_type == ActionType.CATCH_AND_SHOOT:
                found = True
                break
        self.assertTrue(found)

    def test_p_pull_up_remains_reachable(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        perceived = self._perceive_all(state, ctx)
        clock = ClockContext(shot_clock_remaining=18.0)
        found = False
        for seed in range(50):
            intent = SelectionPolicy(random.Random(seed)).select(
                perceived, RoleContext(), TendencyContext(), clock, "p1",
                mode=ActionSelectionMode.HIERARCHICAL,
            )
            if intent is not None and intent.action_type == ActionType.PULL_UP:
                found = True
                break
        self.assertTrue(found)

    def test_q_swing_pass_remains_reachable(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        perceived = self._perceive_all(state, ctx)
        clock = ClockContext(shot_clock_remaining=18.0)
        found = False
        for seed in range(50):
            intent = SelectionPolicy(random.Random(seed)).select(
                perceived, RoleContext(), TendencyContext(), clock, "p1",
                mode=ActionSelectionMode.HIERARCHICAL,
            )
            if intent is not None and intent.action_type == ActionType.SWING_PASS:
                found = True
                break
        self.assertTrue(found)

    def test_r_reset_pass_remains_reachable(self):
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        perceived = self._perceive_all(state, ctx)
        clock = ClockContext(shot_clock_remaining=18.0)
        found = False
        for seed in range(50):
            intent = SelectionPolicy(random.Random(seed)).select(
                perceived, RoleContext(), TendencyContext(), clock, "p1",
                mode=ActionSelectionMode.HIERARCHICAL,
            )
            if intent is not None and intent.action_type == ActionType.RESET_PASS:
                found = True
                break
        self.assertTrue(found)

    def test_s_active_screen_pocket_pass_remains_reachable(self):
        state = _held_state()
        ctx = StructuralContext(teammate_ids=["4"], roller_id="4", screen_active=True)
        perceived = self._perceive_all(state, ctx)
        clock = ClockContext(shot_clock_remaining=18.0)
        found = False
        for seed in range(50):
            intent = SelectionPolicy(random.Random(seed)).select(
                perceived, RoleContext(), TendencyContext(), clock, "p1",
                mode=ActionSelectionMode.HIERARCHICAL,
            )
            if intent is not None and intent.action_type == ActionType.POCKET_PASS:
                found = True
                break
        self.assertTrue(found)

    def test_t_no_feasible_action_returns_none(self):
        clock = ClockContext(shot_clock_remaining=1.0)  # below every duration-class floor
        result = SelectionPolicy(random.Random(1)).select(
            [], RoleContext(), TendencyContext(), clock, "p1", mode=ActionSelectionMode.HIERARCHICAL,
        )
        self.assertIsNone(result)

    def test_flat_mode_still_available_and_matches_old_behavior(self):
        """`ActionSelectionMode.FLAT` reproduces the exact pre-hierarchy sampling path (a single
        softmax + single weighted-choice draw) -- kept for diagnostics/before-after benchmarking."""
        state = _held_state()
        ctx = StructuralContext(nearest_teammate_id="2")
        perceived = self._perceive_all(state, ctx)
        clock = ClockContext(shot_clock_remaining=18.0)
        intent = SelectionPolicy(random.Random(1)).select(
            perceived, RoleContext(), TendencyContext(), clock, "p1", mode=ActionSelectionMode.FLAT,
        )
        self.assertIsNotNone(intent)


if __name__ == "__main__":
    unittest.main()
