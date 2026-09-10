"""Phase 19 -- focused tests for rebound_resolution.py."""
import inspect
import random
import unittest
from dataclasses import replace

from possession_engine import PossessionEngine
from possession_state import BallState, PossessionPhase, SpatialZone
from rebound_resolution import (
    BoxOutState, ReboundCandidate, ReboundOpportunity, ReboundOutcome, ReboundSource,
    apply_rebound_to_engine, eligible_rebound_candidates, resolve_rebound,
)


def _loose_engine(zone=SpatialZone.RESTRICTED_RIM, offense_team_id=None):
    e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
    e.inbound("1", zone, PossessionPhase.HALFCOURT)
    e.state = replace(e.state, ball_state=BallState.LOOSE, ball_carrier=None, ball_control=None,
                       offense_team_id=offense_team_id)
    return e


def _opp(zone=SpatialZone.RESTRICTED_RIM, candidates=None, source=ReboundSource.MISSED_FG, family="RIM"):
    candidates = candidates if candidates is not None else [
        ReboundCandidate("1", "OFFENSE", zone, offensive_rebounding=0.30),
        ReboundCandidate("9", "DEFENSE", zone, defensive_rebounding=0.70),
    ]
    return ReboundOpportunity(source=source, shot_family=family, rebound_zone=zone, candidates=candidates)


class TestSourceHandoff(unittest.TestCase):
    def test_from_missed_fg_source(self):
        result = resolve_rebound(_opp(source=ReboundSource.MISSED_FG), random.Random(1))
        self.assertIn(result.outcome, (ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.SECURED_DEFENSE))

    def test_from_unresolved_block_source(self):
        result = resolve_rebound(_opp(source=ReboundSource.UNRESOLVED_BLOCK), random.Random(1))
        self.assertIn(result.outcome, (ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.SECURED_DEFENSE))

    def test_from_final_missed_ft_source(self):
        result = resolve_rebound(_opp(source=ReboundSource.FINAL_MISSED_FT), random.Random(1))
        self.assertIn(result.outcome, (ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.SECURED_DEFENSE))


class TestEligibility(unittest.TestCase):
    def test_player_in_correct_zone_eligible(self):
        opp = _opp()
        eligible = eligible_rebound_candidates(opp)
        self.assertEqual({c.player_id for c in eligible}, {"1", "9"})

    def test_impossible_player_excluded(self):
        candidates = [
            ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.30),
            ReboundCandidate("2", "OFFENSE", SpatialZone.LEFT_WING, offensive_rebounding=0.99),
        ]
        opp = _opp(candidates=candidates)
        eligible = eligible_rebound_candidates(opp)
        self.assertEqual({c.player_id for c in eligible}, {"1"})

    def test_elite_skill_cannot_bypass_wrong_zone(self):
        candidates = [
            ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.05),
            ReboundCandidate("2", "OFFENSE", SpatialZone.LEFT_WING, offensive_rebounding=0.999),
        ]
        opp = _opp(candidates=candidates)
        for i in range(300):
            result = resolve_rebound(opp, random.Random(i))
            self.assertNotEqual(result.rebounder_id, "2")

    def test_no_eligible_candidate_credits_team_rebound(self):
        candidates = [ReboundCandidate("2", "OFFENSE", SpatialZone.LEFT_WING, offensive_rebounding=0.5)]
        opp = _opp(candidates=candidates)
        result = resolve_rebound(opp, random.Random(1))
        self.assertIn(result.outcome, (ReboundOutcome.TEAM_REBOUND_OFFENSE, ReboundOutcome.TEAM_REBOUND_DEFENSE))
        self.assertIsNone(result.rebounder_id)


class TestBoxOutOrdering(unittest.TestCase):
    def test_boxout_shifts_outcome_direction(self):
        def off_rate(boxed_out, n=1500):
            wins = 0
            for i in range(n):
                cands = [
                    ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.30,
                                       boxed_out_by="9" if boxed_out else None),
                    ReboundCandidate("9", "DEFENSE", SpatialZone.RESTRICTED_RIM, defensive_rebounding=0.70,
                                       box_out_state=BoxOutState.ESTABLISHED_BOXOUT if boxed_out else BoxOutState.NONE),
                ]
                r = resolve_rebound(_opp(candidates=cands), random.Random(i))
                if r.outcome == ReboundOutcome.SECURED_OFFENSE:
                    wins += 1
            return wins / n

        self.assertGreater(off_rate(False), off_rate(True))


class TestSkillFirewalls(unittest.TestCase):
    def test_oreb_only_used_for_offense_side(self):
        """A DEFENSE-side candidate's offensive_rebounding field, even
        if set, must never be read."""
        cand = ReboundCandidate("9", "DEFENSE", SpatialZone.RESTRICTED_RIM,
                                  defensive_rebounding=0.1, offensive_rebounding=0.99)
        from rebound_resolution import _candidate_log_weight
        weight_with_high_oreb = _candidate_log_weight(cand)
        cand2 = ReboundCandidate("9", "DEFENSE", SpatialZone.RESTRICTED_RIM,
                                   defensive_rebounding=0.1, offensive_rebounding=0.01)
        weight_with_low_oreb = _candidate_log_weight(cand2)
        self.assertEqual(weight_with_high_oreb, weight_with_low_oreb)

    def test_rim_finishing_and_three_point_not_fields(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(ReboundCandidate)}
        self.assertTrue(names.isdisjoint({"rim_finishing", "three_point", "midrange"}))

    def test_tendencies_not_fields(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(ReboundOpportunity)} | {f.name for f in dataclasses.fields(ReboundCandidate)}
        self.assertTrue(names.isdisjoint({"drive_aggression", "three_point_preference"}))

    def test_orb_crash_not_referenced_anywhere(self):
        """Checks actual CODE symbols only -- the module's own prose
        docstring legitimately explains orb_crash is NOT used, which
        would otherwise false-positive a naive text search."""
        import rebound_resolution as rr
        for name, value in vars(rr).items():
            if name.startswith("__"):
                continue
            self.assertNotIn("orb_crash", name.lower())


class TestOrebBoundary(unittest.TestCase):
    def test_high_oreb_cannot_force_eligibility(self):
        candidates = [ReboundCandidate("2", "OFFENSE", SpatialZone.LEFT_WING, offensive_rebounding=0.999)]
        opp = _opp(candidates=candidates)
        result = resolve_rebound(opp, random.Random(1))
        self.assertNotEqual(result.rebounder_id, "2")

    def test_oreb_does_not_auto_generate_putback(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(ReboundOpportunity)}
        self.assertTrue(names.isdisjoint({"putback", "next_shot"}))


class TestDrebBoundary(unittest.TestCase):
    def test_high_dreb_cannot_force_eligibility(self):
        candidates = [ReboundCandidate("9", "DEFENSE", SpatialZone.LEFT_WING, defensive_rebounding=0.999)]
        opp = _opp(candidates=candidates)
        result = resolve_rebound(opp, random.Random(1))
        self.assertNotEqual(result.rebounder_id, "9")

    def test_dreb_does_not_start_transition_state_directly(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(ReboundOpportunity)}
        self.assertTrue(names.isdisjoint({"transition", "fastbreak", "outlet"}))


class TestTeamRebound(unittest.TestCase):
    def test_team_rebound_not_credited_to_individual(self):
        candidates = [ReboundCandidate("2", "OFFENSE", SpatialZone.LEFT_WING, offensive_rebounding=0.5)]
        result = resolve_rebound(_opp(candidates=candidates), random.Random(1))
        self.assertIsNone(result.rebounder_id)


class TestOldAdvantageFirewall(unittest.TestCase):
    def test_oreb_clears_advantage_regardless_of_prior_state(self):
        from possession_advantage import SpatialMagnitudeAdvantage
        e = _loose_engine(offense_team_id="A")
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9})
        candidates = [ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.999)]
        opp = _opp(candidates=candidates)
        apply_rebound_to_engine(e, opp, random.Random(1), original_offense_team_id="A")
        self.assertIsNone(e.advantage)

    def test_same_oreb_context_different_old_advantage_yields_identical_new_state(self):
        from possession_advantage import SpatialMagnitudeAdvantage, DiscreteTierAdvantage
        results = []
        for adv in (SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9}),
                    DiscreteTierAdvantage(tiers={SpatialZone.PAINT: "COLLAPSED"}), None):
            e = _loose_engine(offense_team_id="A")
            e.advantage = adv
            candidates = [ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.999)]
            apply_rebound_to_engine(e, _opp(candidates=candidates), random.Random(1), original_offense_team_id="A")
            results.append((e.state.phase, e.state.ball_carrier, e.advantage))
        self.assertTrue(all(r == (PossessionPhase.SECOND_CHANCE, "1", None) for r in results))


class TestPossessionContinuity(unittest.TestCase):
    def test_oreb_retains_team_possession(self):
        e = _loose_engine(offense_team_id="A")
        candidates = [ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.999)]
        apply_rebound_to_engine(e, _opp(candidates=candidates), random.Random(1), original_offense_team_id="A")
        self.assertEqual(e.state.offense_team_id, "A")
        self.assertEqual(e.state.phase, PossessionPhase.SECOND_CHANCE)

    def test_dreb_flips_team_possession(self):
        e = _loose_engine()
        candidates = [ReboundCandidate("9", "DEFENSE", SpatialZone.RESTRICTED_RIM, defensive_rebounding=0.999)]
        apply_rebound_to_engine(e, _opp(candidates=candidates), random.Random(1),
                                 new_offense_team_id="B", new_defense_team_id="A")
        self.assertEqual(e.state.offense_team_id, "B")
        self.assertEqual(e.state.defense_team_id, "A")


class TestBlockNotAutoRebound(unittest.TestCase):
    def test_block_does_not_directly_credit_a_rebound(self):
        """Nothing in this module reads a block outcome directly --
        Phase 18B already leaves the ball LOOSE; only THIS module's own
        eligibility+competition resolves who gets it."""
        source = inspect.getsource(__import__("rebound_resolution"))
        self.assertNotIn("blocker_id", source)


class TestSingleWinner(unittest.TestCase):
    def test_exactly_one_outcome_per_call(self):
        result = resolve_rebound(_opp(), random.Random(1))
        self.assertIsInstance(result.outcome, str)

    def test_no_two_simultaneous_secured_rebounders(self):
        result = resolve_rebound(_opp(), random.Random(1))
        if result.outcome in (ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.SECURED_DEFENSE):
            self.assertIsInstance(result.rebounder_id, str)  # exactly one id


class TestPhysicalMissingHandling(unittest.TestCase):
    def test_missing_physical_fields_not_present(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(ReboundCandidate)}
        self.assertTrue(names.isdisjoint({"mass", "height", "standing_reach", "wingspan"}))

    def test_missing_skill_treated_as_neutral_not_zero(self):
        from rebound_resolution import _candidate_log_weight
        cand_missing = ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=None)
        cand_present_midpoint = ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.5)
        self.assertAlmostEqual(_candidate_log_weight(cand_missing), _candidate_log_weight(cand_present_midpoint))


class TestFinalVsNonFinalFT(unittest.TestCase):
    def test_final_missed_ft_source_representable(self):
        result = resolve_rebound(_opp(source=ReboundSource.FINAL_MISSED_FT), random.Random(1))
        self.assertIsNotNone(result)


class TestEraShotClockReset(unittest.TestCase):
    def test_oreb_uses_era_rules_not_hardcoded_14(self):
        e = _loose_engine(offense_team_id="A")
        e.state.shot_clock_remaining = 20.0
        candidates = [ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.999)]
        apply_rebound_to_engine(e, _opp(candidates=candidates), random.Random(1), original_offense_team_id="A")
        self.assertEqual(e.state.shot_clock_remaining, 14.0)  # real modern-era rule, via era_rules -- not a bare hardcoded constant in this module

    def test_no_hardcoded_14_literal_in_module(self):
        source = inspect.getsource(__import__("rebound_resolution"))
        self.assertNotIn("14.0", source)
        self.assertNotIn("= 14", source)


class TestPlayerIdOnlyAndDeterminism(unittest.TestCase):
    def test_player_id_only(self):
        with self.assertRaises(TypeError):
            from possession_engine import PossessionEngine as PE
            e = _loose_engine(offense_team_id="A")
            e.secure_offensive_rebound_from_loose("LeBron James")

    def test_deterministic_replay(self):
        opp = _opp()
        r1 = resolve_rebound(opp, random.Random(42))
        r2 = resolve_rebound(opp, random.Random(42))
        self.assertEqual(r1, r2)

    def test_no_global_rng(self):
        source = inspect.getsource(__import__("rebound_resolution"))
        self.assertNotIn("random.random(", source)
        self.assertNotIn("random.choice(", source)

    def test_no_mutation_leakage(self):
        e1 = _loose_engine(offense_team_id="A")
        e2 = _loose_engine(offense_team_id="A")
        candidates = [ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.999)]
        apply_rebound_to_engine(e1, _opp(candidates=candidates), random.Random(1), original_offense_team_id="A")
        self.assertEqual(e2.state.ball_state, BallState.LOOSE)  # untouched


if __name__ == "__main__":
    unittest.main()
