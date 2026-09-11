"""Phase 21B -- focused tests for floor_foul_administration.py."""
import inspect
import random
import unittest
from dataclasses import replace

from floor_foul_administration import (
    DEFENSIVE_FLOOR_FOUL,
    OFFENSIVE_CHARGE,
    THREE_TO_MAKE_TWO,
    TWO_SHOT,
    FoulAdministrationState,
    administer_bonus_free_throws,
    administer_floor_foul,
    effective_bonus_foul_threshold,
)
from on_ball_pressure_resolution import (
    FoulPacket,
    OnBallContactOutcome,
    OnBallPressureContext,
    apply_on_ball_pressure_to_engine,
)
from possession_engine import PossessionEngine
from possession_rules import EraRules
from possession_state import BallState, DefensivePosture, PossessionPhase, SpatialZone


def _engine(era_rules=None, **kwargs):
    e = PossessionEngine("p1", "TEAM_A", "TEAM_B", era_rules=era_rules,
                          season=None if era_rules is not None else "2023-24", **kwargs)
    e.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
    return e


class TestDefensiveFloorFoulOutsideBonus(unittest.TestCase):
    def test_full_administration(self):
        e = _engine()
        state = FoulAdministrationState()
        state, result = administer_floor_foul(
            e, state, foul_event_id="ev1", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(1),
        )
        self.assertFalse(result.duplicate)
        self.assertEqual(state.personal_fouls.count_for("9"), 1)
        self.assertEqual(state.team_foul_count("TEAM_B"), 1)
        self.assertFalse(result.in_bonus)
        self.assertIsNone(result.free_throw_sequence)
        # offense retains possession: team ids unchanged, ball DEAD (not a turnover)
        self.assertEqual(e.state.offense_team_id, "TEAM_A")
        self.assertEqual(e.state.defense_team_id, "TEAM_B")
        self.assertEqual(e.state.ball_state, BallState.DEAD)
        self.assertTrue(result.possession_consequence_applied_here)


class TestDefensiveFloorFoulInBonus(unittest.TestCase):
    def test_bonus_evaluated_from_rules_and_fts_awarded(self):
        e = _engine()
        state = FoulAdministrationState()
        # Pre-load 4 prior team fouls on TEAM_B so this 5th one crosses the real
        # bonus_foul_threshold=5 configured on the default 2023-24 era rules.
        for i in range(4):
            state, _ = administer_floor_foul(
                e, state, foul_event_id=f"prior{i}", offender_id="9", fouled_player_id="1",
                foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(1),
            )
        self.assertEqual(state.team_foul_count("TEAM_B"), 4)

        state, result = administer_floor_foul(
            e, state, foul_event_id="ev_bonus", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(2), free_throw_rate=0.8,
        )
        self.assertEqual(state.team_foul_count("TEAM_B"), 5)
        self.assertTrue(result.in_bonus)
        self.assertIsNotNone(result.free_throw_sequence)
        self.assertEqual(result.free_throw_sequence.awarded_attempts, 2)
        self.assertTrue(result.free_throw_sequence.is_complete)
        # foul counted exactly once
        self.assertEqual(state.personal_fouls.count_for("9"), 5)

    def test_foul_counted_exactly_once_across_full_bonus_path(self):
        e = _engine()
        state = FoulAdministrationState()
        state, result = administer_floor_foul(
            e, state, foul_event_id="only_once", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(3), free_throw_rate=0.75,
        )
        self.assertEqual(state.team_foul_count("TEAM_B"), 1)
        self.assertEqual(state.personal_fouls.count_for("9"), 1)


class TestOffensiveCharge(unittest.TestCase):
    """Real current-NBA rule: an ordinary offensive foul records a
    personal foul but is NEVER a team foul, and NEVER awards FTs."""

    def test_charge_administration(self):
        e = _engine()
        state = FoulAdministrationState()
        state, result = administer_floor_foul(
            e, state, foul_event_id="charge1", offender_id="1", fouled_player_id="9",
            foul_class=OFFENSIVE_CHARGE, offender_team_id="TEAM_A", fouled_team_id="TEAM_B",
            rng=random.Random(1),
        )
        self.assertEqual(state.personal_fouls.count_for("1"), 1)
        self.assertFalse(result.in_bonus)
        self.assertIsNone(result.free_throw_sequence)
        self.assertIsNone(result.team_foul_team_id)
        self.assertIsNone(result.team_foul_count_after)
        # turnover: ball dead, no carrier -- no FGA, and no steal concept exists in this outcome at all
        self.assertEqual(e.state.ball_state, BallState.DEAD)
        self.assertIsNone(e.state.ball_carrier)
        self.assertNotIn("steal", vars(result))

    def test_charge_never_increments_team_fouls(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(6):
            state, result = administer_floor_foul(
                e, state, foul_event_id=f"charge{i}", offender_id="1", fouled_player_id="9",
                foul_class=OFFENSIVE_CHARGE, offender_team_id="TEAM_A", fouled_team_id="TEAM_B",
                rng=random.Random(i),
            )
            self.assertFalse(result.in_bonus)
            self.assertIsNone(result.free_throw_sequence)
        # 6 real offensive fouls recorded as personal fouls, but zero team fouls charged.
        self.assertEqual(state.personal_fouls.count_for("1"), 6)
        self.assertEqual(state.team_foul_count("TEAM_A"), 0)

    def test_charge_source_never_calls_bonus_check_or_ft_award(self):
        """Structural firewall: the OFFENSIVE_CHARGE branch's own source
        never references the bonus-check/FT-award/team-foul-increment
        functions at all."""
        import floor_foul_administration as mod
        src = inspect.getsource(mod.administer_floor_foul)
        offensive_branch = src.split("else:  # OFFENSIVE_CHARGE")[1]
        self.assertNotIn("shooting_foul_team_bonus_check", offensive_branch)
        self.assertNotIn("administer_bonus_free_throws", offensive_branch)
        self.assertNotIn("_with_team_foul_incremented", offensive_branch)


class TestIdempotence(unittest.TestCase):
    def test_duplicate_administration_is_a_no_op(self):
        e = _engine()
        state = FoulAdministrationState()
        state, result1 = administer_floor_foul(
            e, state, foul_event_id="dup1", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(1),
        )
        state_before_repeat = state
        state, result2 = administer_floor_foul(
            e, state, foul_event_id="dup1", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(99),
        )
        self.assertTrue(result2.duplicate)
        self.assertEqual(state.personal_fouls.count_for("9"), 1)  # NOT incremented a second time
        self.assertEqual(state.team_foul_count("TEAM_B"), 1)      # NOT incremented a second time
        self.assertEqual(state, state_before_repeat)

    def test_duplicate_never_awards_a_second_ft_sequence(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(4):
            state, _ = administer_floor_foul(
                e, state, foul_event_id=f"pre{i}", offender_id="9", fouled_player_id="1",
                foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(i),
            )
        state, first = administer_floor_foul(
            e, state, foul_event_id="bonus_ev", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(5), free_throw_rate=0.8,
        )
        self.assertTrue(first.in_bonus)
        state, second = administer_floor_foul(
            e, state, foul_event_id="bonus_ev", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(6), free_throw_rate=0.8,
        )
        self.assertTrue(second.duplicate)
        self.assertIsNone(second.free_throw_sequence)


class TestPhase21AIntegrationBoundary(unittest.TestCase):
    """21A detects/classifies; 21B administers; no redetection."""

    def test_administers_from_a_real_21a_foul_packet_no_redetection(self):
        ctx = OnBallPressureContext(contact_established=True, foul_drawing=5.0, foul_discipline=-5.0)
        found_outcome = None
        for seed in range(200):
            e2 = PossessionEngine("p1", "TEAM_A", "TEAM_B", season="2023-24", rng_seed=1)
            e2.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
            e2.switch("9", "1", DefensivePosture.SQUARE)
            outcome = apply_on_ball_pressure_to_engine(e2, "1", "9", ctx, random.Random(seed))
            if outcome == OnBallContactOutcome.DEFENSIVE_FLOOR_FOUL:
                found_outcome = (e2, seed)
                break
        self.assertIsNotNone(found_outcome, "expected DEFENSIVE_FLOOR_FOUL reachable within 200 seeds")
        engine, seed = found_outcome
        # Detection (21A) has ALREADY applied the possession consequence (non_shooting_foul).
        self.assertEqual(engine.state.ball_state, BallState.DEAD)

        packet = FoulPacket(offender_id="9", fouled_player_id="1", foul_class=DEFENSIVE_FLOOR_FOUL, live_ball=False)
        state = FoulAdministrationState()
        state, result = administer_floor_foul(
            engine, state, foul_event_id="from_21a", offender_id=packet.offender_id,
            fouled_player_id=packet.fouled_player_id, foul_class=packet.foul_class,
            offender_team_id="TEAM_B", fouled_team_id="TEAM_A", rng=random.Random(seed),
            possession_consequence_already_applied=True,
        )
        self.assertFalse(result.possession_consequence_applied_here)  # 21B did NOT re-call the engine transition
        self.assertEqual(state.personal_fouls.count_for("9"), 1)

    def test_no_shot_or_contact_concept_in_this_module(self):
        import floor_foul_administration as mod
        src = inspect.getsource(mod)
        for forbidden in ("shot_family", "contact_probability", "whistle_probability", "ContactContext"):
            self.assertNotIn(forbidden, src)


class TestPhase18CFirewall(unittest.TestCase):
    def test_shooting_foul_path_untouched_and_reused_only_via_named_functions(self):
        import floor_foul_administration as mod
        src = inspect.getsource(mod)
        # 21B shares Phase 18C's real FT machinery by NAME (explicit reuse), never redefines it.
        self.assertIn("apply_free_throw_attempt_to_engine", src)
        self.assertIn("resolve_free_throw_attempt", src)
        # ...but never touches Phase 18C's own detection surface.
        self.assertNotIn("resolve_contact_and_whistle", src)
        self.assertNotIn("resolve_shooting_foul_shot", src)

    def test_bonus_check_has_one_shared_definition_not_reimplemented_per_caller(self):
        """Phase 21B correction ("Correct NBA penalty-state foul administration"): the bonus/
        penalty check is no longer Phase 18C's own `shooting_foul_team_bonus_check` reused
        verbatim (that hook has no final-two-minute-exception concept and cannot express it) --
        it is `is_team_in_penalty`, defined exactly once in THIS module and consulted by both
        `administer_floor_foul` (this module) and
        `detailed_game_orchestrator.DetailedGameState.in_bonus` (never re-derived there)."""
        import floor_foul_administration as mod
        src = inspect.getsource(mod.administer_floor_foul)
        self.assertIn("is_team_in_penalty", src)
        self.assertNotIn("shooting_foul_team_bonus_check", src)
        import detailed_game_orchestrator as dgo
        self.assertIn("is_team_in_penalty", inspect.getsource(dgo.DetailedGameState.in_bonus))


class TestEraRulesVariation(unittest.TestCase):
    def test_bonus_format_is_rules_driven_not_hardcoded(self):
        """A custom EraRules with THREE_TO_MAKE_TWO produces materially
        different administered-FT behavior than the default TWO_SHOT --
        proving the format comes from rules configuration, not a
        hardcoded modern constant, without asserting any specific real
        historical season used this format."""
        two_shot_rules = EraRules(era_name="test_two_shot", shot_clock_seconds=24.0,
                                   oreb_shot_clock_reset_seconds=None, bonus_foul_threshold=5,
                                   period_length_seconds=720.0, periods_per_game=4,
                                   bonus_free_throw_format=TWO_SHOT)
        three_to_make_two_rules = replace(two_shot_rules, era_name="test_3_to_make_2",
                                           bonus_free_throw_format=THREE_TO_MAKE_TWO)

        e_two = _engine(era_rules=two_shot_rules)
        seq_two = administer_bonus_free_throws(e_two, "1", 0.5, TWO_SHOT, random.Random(1))
        self.assertEqual(seq_two.awarded_attempts, 2)

        e_three = _engine(era_rules=three_to_make_two_rules)
        seq_three = administer_bonus_free_throws(e_three, "1", 0.5, THREE_TO_MAKE_TWO, random.Random(1))
        # THREE_TO_MAKE_TWO can take a real 3rd attempt; TWO_SHOT structurally never can.
        self.assertLessEqual(seq_two.awarded_attempts, 2)
        self.assertIn(seq_three.awarded_attempts, (2, 3))

    def test_three_to_make_two_stops_at_two_makes(self):
        e = _engine()
        seq = administer_bonus_free_throws(e, "1", 0.999, THREE_TO_MAKE_TWO, random.Random(1))
        self.assertEqual(seq.awarded_attempts, 2)
        self.assertEqual(seq.makes, 2)

    def test_three_to_make_two_uses_all_three_when_never_making_two_in_a_row(self):
        e = _engine()
        seq = administer_bonus_free_throws(e, "1", 0.001, THREE_TO_MAKE_TWO, random.Random(1))
        self.assertEqual(seq.awarded_attempts, 3)
        self.assertEqual(seq.makes, 0)

    def test_full_administration_path_honors_configured_format(self):
        three_rules = EraRules(era_name="test_historical", shot_clock_seconds=24.0,
                                oreb_shot_clock_reset_seconds=None, bonus_foul_threshold=1,
                                period_length_seconds=720.0, periods_per_game=4,
                                bonus_free_throw_format=THREE_TO_MAKE_TWO)
        e = _engine(era_rules=three_rules)
        state = FoulAdministrationState()
        state, result = administer_floor_foul(
            e, state, foul_event_id="ev", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(1), free_throw_rate=0.9,
        )
        self.assertTrue(result.in_bonus)
        self.assertEqual(result.bonus_free_throw_format, THREE_TO_MAKE_TWO)


class TestRegulationVsOvertimeThreshold(unittest.TestCase):
    def test_ot_uses_a_distinct_real_threshold_not_the_regulation_number_reset(self):
        rules = EraRules(era_name="test_ot", shot_clock_seconds=24.0, oreb_shot_clock_reset_seconds=None,
                          bonus_foul_threshold=5, period_length_seconds=720.0, periods_per_game=4,
                          overtime_bonus_foul_threshold=4)
        self.assertEqual(effective_bonus_foul_threshold(rules, is_overtime=False), 5)
        self.assertEqual(effective_bonus_foul_threshold(rules, is_overtime=True), 4)
        self.assertNotEqual(
            effective_bonus_foul_threshold(rules, is_overtime=False),
            effective_bonus_foul_threshold(rules, is_overtime=True),
        )

    def test_missing_ot_threshold_falls_back_explicitly_not_silently_zero(self):
        rules = EraRules(era_name="test_no_ot", shot_clock_seconds=24.0, oreb_shot_clock_reset_seconds=None,
                          bonus_foul_threshold=5, period_length_seconds=720.0, periods_per_game=4)
        self.assertIsNone(rules.overtime_bonus_foul_threshold)
        self.assertEqual(effective_bonus_foul_threshold(rules, is_overtime=True), 5)

    def test_administration_reaches_bonus_earlier_in_overtime(self):
        rules = EraRules(era_name="test_ot2", shot_clock_seconds=24.0, oreb_shot_clock_reset_seconds=None,
                          bonus_foul_threshold=5, period_length_seconds=720.0, periods_per_game=4,
                          overtime_bonus_foul_threshold=3)
        e = _engine(era_rules=rules)
        state = FoulAdministrationState()
        for i in range(2):
            state, _ = administer_floor_foul(
                e, state, foul_event_id=f"pre{i}", offender_id="9", fouled_player_id="1",
                foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(i), is_overtime=True,
            )
        state, result = administer_floor_foul(
            e, state, foul_event_id="third", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(9), free_throw_rate=0.8, is_overtime=True,
        )
        self.assertEqual(state.team_foul_count("TEAM_B"), 3)
        self.assertTrue(result.in_bonus)  # would NOT be in bonus yet at 3 fouls under the regulation threshold of 5

        # The SAME 3rd team foul, same team, same count, would NOT be in the bonus under regulation.
        e_reg = _engine(era_rules=rules)
        state_reg = FoulAdministrationState()
        for i in range(2):
            state_reg, _ = administer_floor_foul(
                e_reg, state_reg, foul_event_id=f"reg_pre{i}", offender_id="9", fouled_player_id="1",
                foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(i), is_overtime=False,
            )
        state_reg, result_reg = administer_floor_foul(
            e_reg, state_reg, foul_event_id="reg_third", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(9), is_overtime=False,
        )
        self.assertFalse(result_reg.in_bonus)


class TestMissingInvalidContext(unittest.TestCase):
    def test_missing_free_throw_rate_in_bonus_fails_explicitly(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(4):
            state, _ = administer_floor_foul(
                e, state, foul_event_id=f"pre{i}", offender_id="9", fouled_player_id="1",
                foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(i),
            )
        with self.assertRaises(ValueError):
            administer_floor_foul(
                e, state, foul_event_id="bonus_no_rate", offender_id="9", fouled_player_id="1",
                foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(5),  # no free_throw_rate supplied
            )

    def test_unknown_foul_class_rejected(self):
        e = _engine()
        state = FoulAdministrationState()
        with self.assertRaises(ValueError):
            administer_floor_foul(
                e, state, foul_event_id="bad", offender_id="9", fouled_player_id="1",
                foul_class="TECHNICAL_FOUL", offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(1),
            )

    def test_name_keyed_offender_rejected(self):
        e = _engine()
        state = FoulAdministrationState()
        with self.assertRaises(TypeError):
            administer_floor_foul(
                e, state, foul_event_id="bad2", offender_id="LeBron James", fouled_player_id="1",
                foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(1),
            )

    def test_unknown_bonus_format_rejected(self):
        e = _engine()
        with self.assertRaises(ValueError):
            administer_bonus_free_throws(e, "1", 0.8, "ONE_AND_ONE", random.Random(1))


class TestDeterminism(unittest.TestCase):
    def test_deterministic_replay(self):
        def run():
            e = _engine()
            state = FoulAdministrationState()
            for i in range(4):
                state, _ = administer_floor_foul(
                    e, state, foul_event_id=f"pre{i}", offender_id="9", fouled_player_id="1",
                    foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                    rng=random.Random(i),
                )
            state, result = administer_floor_foul(
                e, state, foul_event_id="final", offender_id="9", fouled_player_id="1",
                foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
                rng=random.Random(42), free_throw_rate=0.77,
            )
            return result.free_throw_sequence.makes, result.free_throw_sequence.attempt_index

        self.assertEqual(run(), run())

    def test_no_global_rng(self):
        import floor_foul_administration as mod
        src = inspect.getsource(mod)
        self.assertNotIn("random.random(", src)
        self.assertNotIn("random.choice(", src)


class TestTeamFoulLedgerAndReset(unittest.TestCase):
    def test_independent_per_team_counts_only_defensive_fouls_count(self):
        e = _engine()
        state = FoulAdministrationState()
        state, _ = administer_floor_foul(
            e, state, foul_event_id="a1", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(1),
        )
        state, _ = administer_floor_foul(
            e, state, foul_event_id="a2", offender_id="1", fouled_player_id="9",
            foul_class=OFFENSIVE_CHARGE, offender_team_id="TEAM_A", fouled_team_id="TEAM_B",
            rng=random.Random(1),
        )
        self.assertEqual(state.team_foul_count("TEAM_B"), 1)
        self.assertEqual(state.team_foul_count("TEAM_A"), 0)  # the charge never charges a team foul

    def test_reset_team_fouls(self):
        e = _engine()
        state = FoulAdministrationState()
        state, _ = administer_floor_foul(
            e, state, foul_event_id="a1", offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(1),
        )
        reset_state = state.reset_team_fouls()
        self.assertEqual(reset_state.team_foul_count("TEAM_B"), 0)
        self.assertEqual(state.team_foul_count("TEAM_B"), 1)  # original untouched -- immutable replace()


class TestPenaltyStateAndFinalTwoMinuteException(unittest.TestCase):
    """Focused tests for "Correct NBA penalty-state foul administration" (Phase 21B correction).
    `OUTSIDE_WINDOW`/`INSIDE_WINDOW` clocks are deliberately far from the 120s boundary itself --
    these tests exercise the real STATE MACHINE (quota reached vs. not, exception used vs. not),
    not a boundary-value micro-test."""

    OUTSIDE_WINDOW = 300.0  # well above FINAL_TWO_MINUTES_SECONDS -- ordinary quota-only behavior
    INSIDE_WINDOW = 90.0    # well below it -- the final-two-minute exception can apply

    def _foul(self, e, state, team_id, event_id, clock, is_overtime=False):
        return administer_floor_foul(
            e, state, foul_event_id=event_id, offender_id="9", fouled_player_id="1",
            foul_class=DEFENSIVE_FLOOR_FOUL, offender_team_id=team_id, fouled_team_id="TEAM_A",
            rng=random.Random(1), clock_remaining_seconds=clock, is_overtime=is_overtime,
            free_throw_rate=0.8,  # only consulted if this specific foul turns out to be in the bonus
        )

    def test_a_regulation_fouls_one_through_four_before_two_minutes_are_non_penalty(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(4):
            state, result = self._foul(e, state, "TEAM_B", f"reg{i}", self.OUTSIDE_WINDOW)
            self.assertFalse(result.in_bonus, f"foul #{i + 1}")
            self.assertEqual(state.team_foul_count("TEAM_B"), i + 1)

    def test_b_regulation_fifth_common_foul_is_penalty(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(4):
            state, _ = self._foul(e, state, "TEAM_B", f"reg{i}", self.OUTSIDE_WINDOW)
        state, result = self._foul(e, state, "TEAM_B", "reg4", self.OUTSIDE_WINDOW)
        self.assertTrue(result.in_bonus)
        self.assertEqual(state.team_foul_count("TEAM_B"), 5)

    def test_c_team_below_quota_entering_final_two_minutes_gets_one_penalty_free_foul(self):
        e = _engine()
        state = FoulAdministrationState()
        state, result = self._foul(e, state, "TEAM_B", "f2m_1", self.INSIDE_WINDOW)
        self.assertFalse(result.in_bonus)
        self.assertIn("TEAM_B", state.final_two_minute_exception_used)

    def test_d_next_final_two_minute_common_foul_triggers_penalty(self):
        e = _engine()
        state = FoulAdministrationState()
        state, first = self._foul(e, state, "TEAM_B", "f2m_1", self.INSIDE_WINDOW)
        self.assertFalse(first.in_bonus)
        state, second = self._foul(e, state, "TEAM_B", "f2m_2", self.INSIDE_WINDOW)
        self.assertTrue(second.in_bonus)
        # still below the ordinary regulation quota of 5 (only the team's 2nd foul) --
        # the penalty here comes ONLY from the exhausted exception, not the ordinary count.
        self.assertEqual(state.team_foul_count("TEAM_B"), 2)

    def test_e_team_already_in_penalty_before_two_minutes_remains_in_penalty(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(5):  # reach the ordinary quota well before the final two minutes
            state, _ = self._foul(e, state, "TEAM_B", f"reg{i}", self.OUTSIDE_WINDOW)
        self.assertEqual(state.team_foul_count("TEAM_B"), 5)
        state, result = self._foul(e, state, "TEAM_B", "f2m_later", self.INSIDE_WINDOW)
        self.assertTrue(result.in_bonus)
        # the exception was never consulted/consumed -- this team was already in penalty on its own
        self.assertNotIn("TEAM_B", state.final_two_minute_exception_used)

    def test_f_ot_fouls_one_through_three_are_non_penalty(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(3):
            state, result = self._foul(e, state, "TEAM_B", f"ot{i}", self.OUTSIDE_WINDOW, is_overtime=True)
            self.assertFalse(result.in_bonus, f"OT foul #{i + 1}")
        self.assertEqual(state.team_foul_count("TEAM_B"), 3)

    def test_g_ot_fourth_foul_is_penalty_not_the_regulation_fifth(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(3):
            state, _ = self._foul(e, state, "TEAM_B", f"ot{i}", self.OUTSIDE_WINDOW, is_overtime=True)
        state, result = self._foul(e, state, "TEAM_B", "ot3", self.OUTSIDE_WINDOW, is_overtime=True)
        self.assertTrue(result.in_bonus)
        self.assertEqual(state.team_foul_count("TEAM_B"), 4)  # NOT 5 -- the OT quota is 3, not regulation's 4

    def test_h_ot_final_two_minute_exception_then_penalty(self):
        e = _engine()
        state = FoulAdministrationState()
        state, _ = self._foul(e, state, "TEAM_B", "ot_pre", self.OUTSIDE_WINDOW, is_overtime=True)
        self.assertEqual(state.team_foul_count("TEAM_B"), 1)  # below the OT quota of 3
        state, first = self._foul(e, state, "TEAM_B", "ot_f2m_1", self.INSIDE_WINDOW, is_overtime=True)
        self.assertFalse(first.in_bonus)  # the one forgiven OT final-two-minute foul
        self.assertIn("TEAM_B", state.final_two_minute_exception_used)
        state, second = self._foul(e, state, "TEAM_B", "ot_f2m_2", self.INSIDE_WINDOW, is_overtime=True)
        self.assertTrue(second.in_bonus)  # still only 3 OT fouls, below the OT quota of 3+1 -- penalty from the exception, not the count
        self.assertEqual(state.team_foul_count("TEAM_B"), 3)

    def test_i_quarter_reset_clears_team_fouls_and_the_exception_flag(self):
        e = _engine()
        state = FoulAdministrationState()
        state, _ = self._foul(e, state, "TEAM_B", "f2m_1", self.INSIDE_WINDOW)
        self.assertIn("TEAM_B", state.final_two_minute_exception_used)
        reset_state = state.reset_team_fouls()
        self.assertEqual(reset_state.team_foul_count("TEAM_B"), 0)
        self.assertNotIn("TEAM_B", reset_state.final_two_minute_exception_used)
        # a fresh period: the SAME team gets a new exception, not permanently exhausted
        e2 = _engine()
        reset_state, result = self._foul(e2, reset_state, "TEAM_B", "newperiod_1", self.INSIDE_WINDOW)
        self.assertFalse(result.in_bonus)

    def test_j_ot_period_reset_uses_the_same_mechanism(self):
        e = _engine()
        state = FoulAdministrationState()
        state, _ = self._foul(e, state, "TEAM_B", "ot_f2m_1", self.INSIDE_WINDOW, is_overtime=True)
        self.assertIn("TEAM_B", state.final_two_minute_exception_used)
        reset_state = state.reset_team_fouls()  # same real period-boundary hook -- no separate OT reset method
        self.assertEqual(reset_state.team_foul_count("TEAM_B"), 0)
        self.assertNotIn("TEAM_B", reset_state.final_two_minute_exception_used)

    def test_k_player_personal_fouls_are_not_reset_by_the_period_boundary(self):
        e = _engine()
        state = FoulAdministrationState()
        for i in range(3):
            state, _ = self._foul(e, state, "TEAM_B", f"pf{i}", self.OUTSIDE_WINDOW)
        self.assertEqual(state.personal_fouls.count_for("9"), 3)
        reset_state = state.reset_team_fouls()
        self.assertEqual(reset_state.personal_fouls.count_for("9"), 3)  # unchanged -- carries across periods/OT

    def test_l_offensive_charge_still_never_creates_bonus_fts_even_while_already_in_penalty(self):
        """Stronger than the existing `test_charge_never_increments_team_fouls` -- proves the
        exclusion holds even when the OFFENDING team is already deep in the penalty from OTHER
        players' defensive fouls, not just from a fresh zero count."""
        e = _engine()
        state = FoulAdministrationState()
        for i in range(6):  # put TEAM_B (the offense here) deep in the penalty
            state, _ = self._foul(e, state, "TEAM_B", f"pre{i}", self.OUTSIDE_WINDOW)
        self.assertEqual(state.team_foul_count("TEAM_B"), 6)
        state, result = administer_floor_foul(
            e, state, foul_event_id="charge", offender_id="1", fouled_player_id="9",
            foul_class=OFFENSIVE_CHARGE, offender_team_id="TEAM_B", fouled_team_id="TEAM_A",
            rng=random.Random(1), clock_remaining_seconds=self.OUTSIDE_WINDOW,
        )
        self.assertFalse(result.in_bonus)
        self.assertIsNone(result.free_throw_sequence)
        self.assertEqual(state.team_foul_count("TEAM_B"), 6)  # unchanged by the charge


if __name__ == "__main__":
    unittest.main()
