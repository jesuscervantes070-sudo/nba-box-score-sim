"""Phase 20A -- focused tests for transition_state.py."""
import inspect
import random
import unittest

from possession_advantage import DiscreteTierAdvantage, SpatialMagnitudeAdvantage
from possession_engine import PossessionEngine
from possession_state import BallState, PossessionPhase, SpatialZone
from transition_state import (
    AHEAD_OF_BALL, BEHIND_BALL, DEAD_BALL_INBOUND, LIVE_TRANSITION_CAPABLE, NEAR_BALL,
    FloorPlayer, PossessionChangeSource, TransitionState, _COURT_FLIP_MAP, classify_source,
    flip_zone_to_new_offense_frame, initialize_transition_state, relational_tag,
)


def _engine():
    e = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
    e.inbound("1", SpatialZone.RESTRICTED_RIM, PossessionPhase.HALFCOURT)
    return e


class TestSourceClassification(unittest.TestCase):
    def test_dreb_is_live(self):
        self.assertEqual(classify_source(PossessionChangeSource.DEFENSIVE_REBOUND), LIVE_TRANSITION_CAPABLE)

    def test_steal_is_live(self):
        self.assertEqual(classify_source(PossessionChangeSource.LIVE_STEAL), LIVE_TRANSITION_CAPABLE)

    def test_dead_ball_turnover_is_dead(self):
        self.assertEqual(classify_source(PossessionChangeSource.DEAD_BALL_TURNOVER), DEAD_BALL_INBOUND)

    def test_made_basket_is_dead(self):
        self.assertEqual(classify_source(PossessionChangeSource.MADE_BASKET_INBOUND), DEAD_BALL_INBOUND)

    def test_period_start_is_dead(self):
        self.assertEqual(classify_source(PossessionChangeSource.PERIOD_START), DEAD_BALL_INBOUND)

    def test_unknown_source_defaults_safe(self):
        self.assertEqual(classify_source("SOMETHING_NEW"), DEAD_BALL_INBOUND)


class TestRelationalTags(unittest.TestCase):
    def test_ahead_behind_near(self):
        self.assertEqual(relational_tag(SpatialZone.RESTRICTED_RIM, SpatialZone.BACKCOURT), AHEAD_OF_BALL)
        self.assertEqual(relational_tag(SpatialZone.BACKCOURT, SpatialZone.RESTRICTED_RIM), BEHIND_BALL)
        self.assertEqual(relational_tag(SpatialZone.PAINT, SpatialZone.PAINT), NEAR_BALL)


class TestDrebHandoff(unittest.TestCase):
    def test_dreb_creates_new_team_possession(self):
        e = _engine()
        ts = initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM)
        self.assertEqual(e.state.offense_team_id, "B")
        self.assertEqual(e.state.defense_team_id, "A")
        self.assertEqual(e.state.ball_carrier, "9")


class TestStealHandoff(unittest.TestCase):
    def test_live_steal_creates_new_possession(self):
        e = _engine()
        ts = initialize_transition_state(e, PossessionChangeSource.LIVE_STEAL, "B", "A", "9", SpatialZone.TOP_OF_KEY)
        self.assertEqual(e.state.offense_team_id, "B")
        self.assertEqual(classify_source(ts.source), LIVE_TRANSITION_CAPABLE)


class TestDeadBallTurnover(unittest.TestCase):
    def test_no_live_transition_residue(self):
        e = _engine()
        zones = [FloorPlayer("9", SpatialZone.RESTRICTED_RIM, "DEFENSE")]
        ts = initialize_transition_state(e, PossessionChangeSource.DEAD_BALL_TURNOVER, "B", "A", "9",
                                           SpatialZone.BACKCOURT, zones)
        self.assertEqual(ts.player_zones, ())
        self.assertIsNone(ts.advantage)


class TestMadeBasketInbound(unittest.TestCase):
    def test_does_not_copy_old_advantage(self):
        e = _engine()
        e.advantage = SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9})
        ts = initialize_transition_state(e, PossessionChangeSource.MADE_BASKET_INBOUND, "B", "A", "9", SpatialZone.BACKCOURT)
        self.assertIsNone(ts.advantage)
        self.assertIsNone(e.advantage)


class TestOldAdvantageFirewall(unittest.TestCase):
    def test_old_advantage_never_survives_possession_change(self):
        for old_adv in (SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9}),
                        DiscreteTierAdvantage(tiers={SpatialZone.PAINT: "COLLAPSED"}), None):
            e = _engine()
            e.advantage = old_adv
            initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM)
            self.assertNotEqual(e.advantage, old_adv) if old_adv is not None else None

    def test_identical_geometry_different_old_advantage_yields_identical_new_state(self):
        zones = [FloorPlayer("9", SpatialZone.RESTRICTED_RIM, "OFFENSE"),
                 FloorPlayer("1", SpatialZone.BACKCOURT, "DEFENSE")]
        results = []
        for old_adv in (SpatialMagnitudeAdvantage(magnitudes={SpatialZone.PAINT: 0.9}),
                        DiscreteTierAdvantage(tiers={SpatialZone.PAINT: "COLLAPSED"}), None):
            e = _engine()
            e.advantage = old_adv
            ts = initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9",
                                               SpatialZone.RESTRICTED_RIM, zones)
            results.append((ts.defenders_back_count, ts.offense_ahead_count, e.advantage.magnitudes if e.advantage else None))
        self.assertTrue(all(r == results[0] for r in results))


class TestSkillFirewalls(unittest.TestCase):
    def test_no_ability_or_tendency_fields_anywhere(self):
        import dataclasses
        import transition_state as ts_mod
        forbidden = {"defensive_rebounding", "offensive_rebounding", "rim_protection", "three_point",
                     "role_off_initiation", "pass_vs_shoot", "drive_aggression", "three_point_preference"}
        for cls in (FloorPlayer, TransitionState):
            names = {f.name for f in dataclasses.fields(cls)}
            self.assertTrue(names.isdisjoint(forbidden))

    def test_no_such_symbol_in_module_namespace(self):
        import transition_state as ts_mod
        forbidden = ("defensive_rebounding", "role_off_initiation", "pass_vs_shoot", "drive_aggression",
                     "three_point_preference", "rim_protection")
        for name in vars(ts_mod):
            if name.startswith("__"):
                continue
            for bad in forbidden:
                self.assertNotIn(bad, name.lower())


class TestNumericalAdvantageIsCountNotTier(unittest.TestCase):
    def test_counts_are_plain_integers_not_enum(self):
        zones = [FloorPlayer("2", SpatialZone.PAINT, "OFFENSE"), FloorPlayer("3", SpatialZone.PAINT, "OFFENSE"),
                 FloorPlayer("9", SpatialZone.BACKCOURT, "DEFENSE")]
        ts = TransitionState(source=PossessionChangeSource.DEFENSIVE_REBOUND, new_offense_team_id="B",
                              new_defense_team_id="A", ball_carrier_id="2", ball_zone=SpatialZone.BACKCOURT,
                              player_zones=tuple(zones))
        self.assertIsInstance(ts.offense_ahead_count, int)
        self.assertIsInstance(ts.defenders_back_count, int)
        self.assertEqual(ts.offense_ahead_count, 2)  # both offense players closer to rim than the ball
        self.assertEqual(ts.defenders_back_count, 0)  # the lone defender is NEAR_BALL (same rank), not ahead


class TestBallOwnership(unittest.TestCase):
    def test_ball_carrier_belongs_to_new_offense(self):
        e = _engine()
        initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM)
        self.assertEqual(e.state.ball_carrier, "9")
        self.assertEqual(e.state.offense_team_id, "B")

    def test_exactly_one_team_owns_possession(self):
        e = _engine()
        initialize_transition_state(e, PossessionChangeSource.LIVE_STEAL, "B", "A", "9", SpatialZone.TOP_OF_KEY)
        self.assertNotEqual(e.state.offense_team_id, e.state.defense_team_id)

    def test_no_stale_old_offense_carrier(self):
        e = _engine()  # old carrier was "1"
        initialize_transition_state(e, PossessionChangeSource.LIVE_STEAL, "B", "A", "9", SpatialZone.TOP_OF_KEY)
        self.assertNotEqual(e.state.ball_carrier, "1")


class TestMatchupCleanup(unittest.TestCase):
    def test_stale_matchups_cleared(self):
        e = _engine()
        e.switch("9", "1")
        self.assertTrue(len(e.state.assignments) > 0)
        initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM)
        self.assertEqual(e.state.assignments, {})

    def test_ball_control_reset_for_new_carrier(self):
        e = _engine()
        e.gather()
        initialize_transition_state(e, PossessionChangeSource.LIVE_STEAL, "B", "A", "9", SpatialZone.TOP_OF_KEY)
        from possession_state import DribbleState
        self.assertEqual(e.state.ball_control.state, DribbleState.LIVE_DRIBBLE)


class TestNoActionExecution(unittest.TestCase):
    def test_no_pass_shot_or_advance_logic(self):
        """Checks actual CODE symbols only -- the module's own prose
        docstring legitimately explains what it does NOT do, which
        would otherwise false-positive a naive text search."""
        import transition_state as ts_mod
        for name, value in vars(ts_mod).items():
            if name.startswith("__"):
                continue
            for bad in ("resolve_pass", "resolve_shot", "advance_dribble", "outlet"):
                self.assertNotIn(bad, name.lower())


class TestEraShotClock(unittest.TestCase):
    def test_shot_clock_from_era_rules_not_hardcoded(self):
        e = _engine()
        ts = initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM)
        self.assertEqual(ts.shot_clock_remaining, e.era_rules.shot_clock_seconds)

    def test_no_hardcoded_24_literal(self):
        source = inspect.getsource(__import__("transition_state"))
        self.assertNotIn("= 24", source)
        self.assertNotIn("24.0", source)


class TestDeterminismAndMissingState(unittest.TestCase):
    def test_deterministic_no_rng_needed(self):
        e1 = _engine()
        e2 = _engine()
        zones = [FloorPlayer("9", SpatialZone.RESTRICTED_RIM, "OFFENSE")]
        ts1 = initialize_transition_state(e1, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM, zones)
        ts2 = initialize_transition_state(e2, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM, zones)
        self.assertEqual(ts1.defenders_back_count, ts2.defenders_back_count)
        self.assertEqual(ts1.offense_ahead_count, ts2.offense_ahead_count)

    def test_no_global_rng(self):
        source = inspect.getsource(__import__("transition_state"))
        self.assertNotIn("random.random(", source)

    def test_missing_player_zones_does_not_crash(self):
        e = _engine()
        ts = initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM, None)
        self.assertEqual(ts.player_zones, ())
        self.assertEqual(ts.defenders_back_count, 0)
        self.assertEqual(ts.offense_ahead_count, 0)


class TestHistoricalFallback(unittest.TestCase):
    def test_works_without_modern_tracking_fields(self):
        e = PossessionEngine("p1", "A", "B", season="1996-97", rng_seed=1)
        e.inbound("1", SpatialZone.RESTRICTED_RIM, PossessionPhase.HALFCOURT)
        ts = initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "9", SpatialZone.RESTRICTED_RIM)
        self.assertEqual(e.state.ball_carrier, "9")
        self.assertEqual(ts.shot_clock_remaining, 24.0)  # classic-era rule, still routed through era_rules


class TestPlayerIdOnly(unittest.TestCase):
    def test_rejects_name_keyed_carrier(self):
        e = _engine()
        with self.assertRaises(TypeError):
            initialize_transition_state(e, PossessionChangeSource.DEFENSIVE_REBOUND, "B", "A", "LeBron James", SpatialZone.RESTRICTED_RIM)


class TestCourtFlipTransform(unittest.TestCase):
    """Focused tests for `flip_zone_to_new_offense_frame` -- see that
    function's own module-level comment block for the full derivation.
    Every one of the 9 `SpatialZone` members is tested explicitly, per
    this task's own instruction -- no loop-over-all-members shortcut
    that could silently skip a value."""

    def test_restricted_rim_flips_to_backcourt(self):
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.RESTRICTED_RIM), SpatialZone.BACKCOURT)

    def test_paint_flips_to_backcourt(self):
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.PAINT), SpatialZone.BACKCOURT)

    def test_midrange_flips_to_backcourt(self):
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.MIDRANGE), SpatialZone.BACKCOURT)

    def test_left_wing_flips_to_backcourt(self):
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.LEFT_WING), SpatialZone.BACKCOURT)

    def test_right_wing_flips_to_backcourt(self):
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.RIGHT_WING), SpatialZone.BACKCOURT)

    def test_left_corner_flips_to_backcourt(self):
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.LEFT_CORNER), SpatialZone.BACKCOURT)

    def test_right_corner_flips_to_backcourt(self):
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.RIGHT_CORNER), SpatialZone.BACKCOURT)

    def test_top_of_key_flips_to_backcourt(self):
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.TOP_OF_KEY), SpatialZone.BACKCOURT)

    def test_backcourt_flips_to_backcourt_the_one_fixed_point(self):
        """Documented as the ONE zone this transform cannot re-derive
        real information for -- see the module's own "REVERSE direction
        is GENUINELY AMBIGUOUS" comment. A conservative fixed point, not
        a guessed frontcourt zone."""
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.BACKCOURT), SpatialZone.BACKCOURT)

    def test_every_spatial_zone_member_is_covered_explicitly(self):
        """Regression guard: if a future phase adds a new `SpatialZone`
        member, this transform must fail loudly (KeyError), never
        silently default it -- confirms the map has no fallback branch
        and currently covers exactly the 9 known members."""
        self.assertEqual(set(SpatialZone), set(_COURT_FLIP_MAP.keys()))
        for zone in SpatialZone:
            flip_zone_to_new_offense_frame(zone)  # must not raise for any current member

    def test_flip_is_idempotent_for_every_zone(self):
        """flip(flip(zone)) == flip(zone) holds for ALL 9 zones -- this
        IS the true, general invariant of this design (a many-to-one
        projection onto a single fixed point), distinct from a full
        involution (flip(flip(zone)) == zone), which this task's own
        instructions anticipate may not hold everywhere."""
        for zone in SpatialZone:
            once = flip_zone_to_new_offense_frame(zone)
            twice = flip_zone_to_new_offense_frame(once)
            self.assertEqual(twice, once)

    def test_flip_flip_equals_original_only_for_the_orientation_neutral_zone(self):
        """flip(flip(zone)) == zone holds ONLY for BACKCOURT (the one
        zone this review found to be genuinely orientation-neutral,
        i.e. a fixed point) -- documented explicitly as NOT holding for
        the other 8 zones, rather than silently only testing the case
        that happens to pass."""
        holds_for = {SpatialZone.BACKCOURT}
        for zone in SpatialZone:
            round_tripped = flip_zone_to_new_offense_frame(flip_zone_to_new_offense_frame(zone))
            if zone in holds_for:
                self.assertEqual(round_tripped, zone)
            else:
                self.assertNotEqual(round_tripped, zone)

    def test_flip_consumes_no_rng_and_is_a_pure_function(self):
        import inspect as _inspect
        sig = _inspect.signature(flip_zone_to_new_offense_frame)
        self.assertNotIn("rng", sig.parameters)
        # deterministic: same input, same output, called repeatedly
        for _ in range(5):
            self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.PAINT), SpatialZone.BACKCOURT)

    def test_matches_this_modules_own_documented_dreb_example(self):
        """Directly verifies the exact scenario `initialize_transition_state`'s
        own docstring describes: a rebound secured at the shooting
        team's basket (old-offense-relative RESTRICTED_RIM) must become
        BACKCOURT for the new offense -- not RESTRICTED_RIM."""
        self.assertEqual(flip_zone_to_new_offense_frame(SpatialZone.RESTRICTED_RIM), SpatialZone.BACKCOURT)
        self.assertNotEqual(flip_zone_to_new_offense_frame(SpatialZone.RESTRICTED_RIM), SpatialZone.RESTRICTED_RIM)


if __name__ == "__main__":
    unittest.main()
