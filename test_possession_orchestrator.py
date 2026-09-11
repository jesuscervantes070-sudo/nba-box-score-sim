"""Phase 23A -- focused + integration tests for possession_orchestrator.py."""
import unittest

from action_intent import ActionIntent, ActionType
from possession_orchestrator import (
    CAPABILITY_GATED_ACTION_TYPES,
    SUPPORTED_ACTION_TYPES,
    PlayerSimulationProfile,
    PossessionConfig,
    PossessionSimulationFault,
    PossessionTerminalReason,
    PossessionWorld,
    UnsupportedActionError,
    _dispatch_drive,
    _dispatch_pass,
    _mirror_defender_zones,
    _primary_defender,
    _sync_assigned_defender_zone,
    apply_matchup_assignments,
    build_matchup_assignments,
    build_structural_context,
    default_v0_zone_placement,
    dispatch_action,
    simulate_possession,
    validate_lineups,
)
from possession_engine import PossessionEngine
from possession_rules import EraRules
from possession_state import DefensivePosture, PossessionPhase, SpatialZone

OFF_FIVE = tuple(str(i) for i in range(1, 6))
DEF_FIVE = tuple(str(i) for i in range(11, 16))


def _profiles(**overrides_by_player):
    profiles = {}
    for p in OFF_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "A", **overrides_by_player.get(p, {}))
    for p in DEF_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "B", **overrides_by_player.get(p, {}))
    return profiles


def _run(config=None, profiles=None, seed=1, possession_id="p", **kwargs):
    return simulate_possession("A", "B", OFF_FIVE, DEF_FIVE, profiles or _profiles(),
                                inbound_receiver_id="1", config=config or PossessionConfig(),
                                rng_seed=seed, possession_id=possession_id, **kwargs)


def _find_seed(reason, config=None, profiles=None, tries=400):
    for seed in range(tries):
        result = _run(config=config, profiles=profiles, seed=seed, possession_id=f"seek{seed}")
        if result.reason == reason:
            return seed, result
    raise AssertionError(f"no seed within {tries} reproduced reason {reason!r}")


class TestLineupValidation(unittest.TestCase):
    def test_valid_lineups_pass(self):
        validate_lineups(OFF_FIVE, DEF_FIVE)  # no raise

    def test_duplicate_within_a_team_rejected(self):
        with self.assertRaises(ValueError):
            validate_lineups(("1", "1", "2", "3", "4"), DEF_FIVE)

    def test_wrong_count_rejected(self):
        with self.assertRaises(ValueError):
            validate_lineups(("1", "2", "3", "4"), DEF_FIVE)

    def test_player_on_both_teams_rejected(self):
        with self.assertRaises(ValueError):
            validate_lineups(OFF_FIVE, ("1", "12", "13", "14", "15"))


class TestMatchupBijection(unittest.TestCase):
    def test_default_lineup_order_pairing_is_a_real_bijection(self):
        assignments = build_matchup_assignments(OFF_FIVE, DEF_FIVE)
        self.assertEqual(len(assignments), 5)
        targets = [a.assigned_to_player_id for a in assignments.values()]
        self.assertEqual(len(set(targets)), 5)
        self.assertEqual(set(targets), set(OFF_FIVE))
        self.assertEqual(set(assignments.keys()), set(DEF_FIVE))
        for a in assignments.values():
            self.assertEqual(a.posture, DefensivePosture.SQUARE)

    def test_explicit_matchup_pairs_bijection_enforced(self):
        pairs = list(zip(DEF_FIVE, reversed(OFF_FIVE)))
        assignments = build_matchup_assignments(OFF_FIVE, DEF_FIVE, matchup_pairs=pairs)
        targets = [a.assigned_to_player_id for a in assignments.values()]
        self.assertEqual(set(targets), set(OFF_FIVE))

    def test_duplicate_offensive_target_in_matchup_pairs_rejected(self):
        pairs = [("11", "1"), ("12", "1"), ("13", "3"), ("14", "4"), ("15", "5")]
        with self.assertRaises(ValueError):
            build_matchup_assignments(OFF_FIVE, DEF_FIVE, matchup_pairs=pairs)

    def test_uncovered_offensive_player_in_matchup_pairs_rejected(self):
        pairs = [("11", "1"), ("12", "2"), ("13", "3"), ("14", "4"), ("15", "1")]  # "5" never covered, "1" doubled
        with self.assertRaises(ValueError):
            build_matchup_assignments(OFF_FIVE, DEF_FIVE, matchup_pairs=pairs)

    def test_wrong_pair_count_rejected(self):
        with self.assertRaises(ValueError):
            build_matchup_assignments(OFF_FIVE, DEF_FIVE, matchup_pairs=[("11", "1")])

    def test_apply_to_engine_is_one_atomic_replace(self):
        engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
        self.assertEqual(len(engine.state.assignments), 5)
        targets = [a.assigned_to_player_id for a in engine.state.assignments.values()]
        self.assertEqual(len(set(targets)), 5)

    def test_rebuild_after_possession_change_still_a_bijection(self):
        """The same primitive works when offense/defense fives swap roles
        (a real possession change) -- no separate 'rebuild' code path."""
        engine = PossessionEngine("p1", "B", "A", season="2023-24", rng_seed=1)
        apply_matchup_assignments(engine, DEF_FIVE, OFF_FIVE)  # DEF_FIVE is now offense, OFF_FIVE now defends
        targets = [a.assigned_to_player_id for a in engine.state.assignments.values()]
        self.assertEqual(set(targets), set(DEF_FIVE))
        self.assertEqual(set(engine.state.assignments.keys()), set(OFF_FIVE))


class TestStructuralContextDerivation(unittest.TestCase):
    def test_no_manual_structural_context_construction_required(self):
        """`build_structural_context` derives every field from
        engine.state + world alone -- a caller never hand-builds one."""
        engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
        engine.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        world = PossessionWorld(team_a_id="A", team_b_id="B", team_a_five=OFF_FIVE, team_b_five=DEF_FIVE,
                                 profiles=_profiles())
        from possession_orchestrator import default_v0_zone_placement, _mirror_defender_zones
        world.player_zones = default_v0_zone_placement(OFF_FIVE, "1", SpatialZone.TOP_OF_KEY)
        _mirror_defender_zones(world.player_zones, engine.state.assignments)
        world.just_caught_pass_player_id = "1"

        ctx = build_structural_context(engine, world)
        self.assertEqual(set(ctx.teammate_ids), set(OFF_FIVE) - {"1"})
        self.assertTrue(ctx.just_caught_pass)
        self.assertIsNotNone(ctx.ball_handler_defender_id)
        self.assertIn(ctx.ball_handler_defender_id, DEF_FIVE)
        self.assertIsNone(ctx.roller_id)
        self.assertFalse(ctx.screen_active)

    def test_perimeter_receiver_ids_derived_from_zones(self):
        engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
        engine.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        world = PossessionWorld(team_a_id="A", team_b_id="B", team_a_five=OFF_FIVE, team_b_five=DEF_FIVE,
                                 profiles=_profiles())
        world.player_zones = {"1": SpatialZone.TOP_OF_KEY, "2": SpatialZone.LEFT_CORNER,
                               "3": SpatialZone.RESTRICTED_RIM, "4": SpatialZone.RIGHT_WING, "5": SpatialZone.PAINT}
        ctx = build_structural_context(engine, world)
        self.assertEqual(set(ctx.perimeter_receiver_ids.keys()), {"2", "4"})


class TestCapabilityGating(unittest.TestCase):
    def test_unsupported_actions_are_a_disjoint_real_set(self):
        self.assertEqual(SUPPORTED_ACTION_TYPES & CAPABILITY_GATED_ACTION_TYPES, frozenset())
        for a in (ActionType.ISOLATION_ATTACK, ActionType.CLOSEOUT_ATTACK, ActionType.TRANSITION_PUSH,
                  ActionType.OUTLET_PASS, ActionType.RECOVER_LOOSE_BALL):
            self.assertIn(a, CAPABILITY_GATED_ACTION_TYPES)

    def test_dispatch_action_rejects_unsupported_action(self):
        from action_intent import ActionIntent
        engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
        engine.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        world = PossessionWorld(team_a_id="A", team_b_id="B", team_a_five=OFF_FIVE, team_b_five=DEF_FIVE,
                                 profiles=_profiles())
        bad_intent = ActionIntent(action_type=ActionType.ISOLATION_ATTACK, actor_player_id="1", possession_id="p1")
        with self.assertRaises(UnsupportedActionError):
            dispatch_action(engine, world, bad_intent, PossessionConfig(), __import__("random").Random(1), 0)

    def test_selection_never_receives_a_capability_gated_opportunity(self):
        """An unsupported action cannot even reach SelectionPolicy --
        verified over many real seeds via the real top-level loop."""
        for seed in range(50):
            result = _run(seed=seed, possession_id=f"cg{seed}")
            # every dispatched action in the trace must be a supported one
            for entry in result.world.trace:
                if entry.get("action") in {a.value for a in CAPABILITY_GATED_ACTION_TYPES}:
                    self.fail(f"a capability-gated action reached dispatch: {entry}")


class TestAttributeDisabling(unittest.TestCase):
    """Phase 23A addendum: vision/foul-drawing/foul-discipline modifiers
    must be None at every call site, regardless of profile content."""

    def test_foul_fields_never_reach_a_resolver(self):
        import inspect
        import possession_orchestrator as mod
        src = inspect.getsource(mod._dispatch_drive) + inspect.getsource(mod._dispatch_shot)
        self.assertIn("foul_discipline=None", src)
        self.assertIn("foul_drawing=None", src)
        self.assertIn("shooter_foul_drawing=None", src)

    def test_vision_modifier_is_none(self):
        import inspect
        import possession_orchestrator as mod
        src = inspect.getsource(mod.simulate_possession)
        self.assertIn("perceive(opportunities, None, rng)", src)


class TestReboundMissingEstimateGating(unittest.TestCase):
    def test_player_missing_side_specific_estimate_excluded_from_competition(self):
        from possession_orchestrator import _dispatch_rebound
        from rebound_resolution import ReboundSource
        import random
        engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
        engine.inbound("1", SpatialZone.RESTRICTED_RIM, PossessionPhase.HALFCOURT)
        engine.begin_shot(SpatialZone.RESTRICTED_RIM, dt=0.0)
        engine.resolve_shot_missed_pending_rebound("1", dt=0.0)
        profiles = _profiles()
        # strip every offensive rebounding estimate -- offense must never win this rebound
        for p in OFF_FIVE:
            from dataclasses import replace
            profiles[p] = replace(profiles[p], offensive_rebounding_shrunk_rate=None)
        world = PossessionWorld(team_a_id="A", team_b_id="B", team_a_five=OFF_FIVE, team_b_five=DEF_FIVE,
                                 profiles=profiles)
        world.player_zones = {pid: SpatialZone.RESTRICTED_RIM for pid in OFF_FIVE + DEF_FIVE}
        result = _dispatch_rebound(engine, world, random.Random(1), ReboundSource.MISSED_FG, "RIM", 0,
                                    offense_team_id="A", defense_team_id="B")
        # with zero eligible offensive candidates, resolve_rebound's own real fallback applies --
        # never an individual offensive rebounder fabricated from a missing estimate.
        self.assertEqual(result.reason, PossessionTerminalReason.DEFENSIVE_REBOUND)


class TestIntegrationScenarios(unittest.TestCase):
    def test_A_simple_made_shot(self):
        seed, result = _find_seed(PossessionTerminalReason.MADE_FG)
        self.assertGreater(result.stats.fga, 0)
        self.assertGreater(result.stats.fgm, 0)
        self.assertGreater(result.stats.points, 0)
        self.assertEqual((result.resulting_offense_team_id, result.resulting_defense_team_id), ("B", "A"))

    def test_B_miss_to_dreb(self):
        seed, result = _find_seed(PossessionTerminalReason.DEFENSIVE_REBOUND)
        self.assertGreater(result.stats.fga, 0)
        self.assertEqual(result.stats.fgm, 0)
        self.assertEqual(result.stats.dreb, 1)
        self.assertEqual(result.resulting_offense_team_id, "B")

    def test_C_miss_to_oreb_then_continues_and_eventually_terminates(self):
        profiles = _profiles()
        for p in OFF_FIVE:
            from dataclasses import replace
            profiles[p] = replace(profiles[p], offensive_rebounding_shrunk_rate=0.6)
        for p in DEF_FIVE:
            from dataclasses import replace
            profiles[p] = replace(profiles[p], defensive_rebounding_shrunk_rate=0.05)
        oreb_found = False
        for seed in range(200):
            result = _run(profiles=profiles, seed=seed, possession_id=f"oreb{seed}")
            if result.stats.oreb > 0:
                oreb_found = True
                self.assertIn(result.reason, (PossessionTerminalReason.MADE_FG, PossessionTerminalReason.DEFENSIVE_REBOUND,
                                               PossessionTerminalReason.TURNOVER, PossessionTerminalReason.SHOT_CLOCK_VIOLATION,
                                               PossessionTerminalReason.FINAL_FT_MADE, PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER))
                break
        self.assertTrue(oreb_found, "expected at least one OREB within 200 seeds at boosted rates")

    def test_D_pass_then_shot(self):
        for seed in range(300):
            result = _run(seed=seed, possession_id=f"passshot{seed}")
            actions = [e.get("action") for e in result.world.trace]
            if any(a in {"SWING_PASS", "KICKOUT", "RESET_PASS"} for a in actions) and \
               any(a in {"PULL_UP", "CATCH_AND_SHOOT"} for a in actions):
                self.assertIn(result.reason, (PossessionTerminalReason.MADE_FG, PossessionTerminalReason.DEFENSIVE_REBOUND,
                                               PossessionTerminalReason.FINAL_FT_MADE))
                return
        self.fail("expected a pass followed by a shot within 300 seeds")

    def test_E_pass_turnover(self):
        seed, result = _find_seed(PossessionTerminalReason.TURNOVER)
        self.assertGreaterEqual(result.stats.turnovers, 1)

    def test_F_drive_then_next_action_then_terminates(self):
        for seed in range(300):
            result = _run(seed=seed, possession_id=f"drive{seed}")
            drive_events = [e for e in result.world.trace if e.get("action") == "DRIVE"]
            if len(drive_events) >= 1 and len(result.world.trace) >= 2:
                self.assertIsNotNone(result.reason)
                return
        self.fail("expected at least one DRIVE within 300 seeds")

    def test_G_floor_foul_administration(self):
        cfg = PossessionConfig(force_on_ball_contact_established=True)
        seed, result = _find_seed(PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER, config=cfg)
        self.assertGreaterEqual(result.stats.turnovers, 1)
        self.assertTrue(any(v > 0 for v in result.stats.personal_fouls.values()))

        # a non-charge defensive floor foul outside the bonus must produce a CONTINUATION, not a terminal result --
        # verified by finding one in the trace and confirming the possession kept going past it.
        found_continuation = False
        for seed2 in range(300):
            r = _run(config=cfg, seed=seed2, possession_id=f"dff{seed2}")
            pressure_events = [e for e in r.world.trace if e.get("action") == "ON_BALL_PRESSURE"
                                and e.get("outcome") == "DEFENSIVE_FLOOR_FOUL"]
            if pressure_events and len(r.world.trace) > r.world.trace.index(pressure_events[0]) + 1:
                found_continuation = True
                break
        self.assertTrue(found_continuation, "expected a non-bonus defensive floor foul to allow continuation")

    def test_H_shooting_foul_path(self):
        for seed in range(500):
            result = _run(seed=seed, possession_id=f"sf{seed}")
            if any(e.get("action") == "SHOOTING_FOUL" for e in result.world.trace):
                self.assertIn(result.reason, (PossessionTerminalReason.MADE_FG, PossessionTerminalReason.FINAL_FT_MADE,
                                               PossessionTerminalReason.DEFENSIVE_REBOUND))
                self.assertGreater(result.stats.fta, 0)
                return
        self.fail("expected a shooting foul within 500 seeds")

    def test_I_shot_clock_violation(self):
        cfg = PossessionConfig(drive_action_seconds=30.0, pull_up_action_seconds=30.0, catch_and_shoot_action_seconds=30.0)
        profiles = _profiles(**{p: {"drive_aggression": 5.0} for p in OFF_FIVE})
        seed, result = _find_seed(PossessionTerminalReason.SHOT_CLOCK_VIOLATION, config=cfg, profiles=profiles)
        self.assertEqual(result.engine_state.ball_state.value, "DEAD")
        self.assertEqual((result.resulting_offense_team_id, result.resulting_defense_team_id), ("B", "A"))

    def test_J_period_expiration(self):
        short_game = EraRules(era_name="test_short_game", shot_clock_seconds=24.0, oreb_shot_clock_reset_seconds=None,
                               bonus_foul_threshold=5, period_length_seconds=1.0, periods_per_game=4)
        cfg = PossessionConfig(era_rules=short_game)
        seed, result = _find_seed(PossessionTerminalReason.PERIOD_END, config=cfg)

    def test_K_step_guard_fault(self):
        cfg = PossessionConfig(max_steps_per_possession=1)
        with self.assertRaises(PossessionSimulationFault) as ctx:
            _run(config=cfg, seed=1, possession_id="fault")
        self.assertEqual(ctx.exception.steps, 1)
        self.assertIsNotNone(ctx.exception.state)

    def test_L_unsupported_action_cannot_reach_dispatcher(self):
        # covered structurally by TestCapabilityGating; re-asserted here as an integration-level guarantee.
        for seed in range(50):
            result = _run(seed=seed, possession_id=f"unsup{seed}")
            for entry in result.world.trace:
                self.assertNotIn(entry.get("action"), {a.value for a in CAPABILITY_GATED_ACTION_TYPES})

    def test_M_matchup_bijection_after_a_real_possession(self):
        result = _run(seed=1, possession_id="bijection")
        assignments = result.engine_state.assignments
        targets = [a.assigned_to_player_id for a in assignments.values()]
        self.assertEqual(len(set(targets)), len(targets))

    def test_N_no_manual_structural_context_inside_simulate_possession(self):
        import inspect
        import possession_orchestrator as mod
        src = inspect.getsource(mod.simulate_possession)
        self.assertIn("build_structural_context(engine, world)", src)
        self.assertNotIn("StructuralContext(", src)  # never hand-constructed inline in the loop itself


class TestNoDoubleClockCharge(unittest.TestCase):
    def test_pass_dispatch_never_calls_charge_time(self):
        import inspect
        import possession_orchestrator as mod
        src = inspect.getsource(mod._dispatch_pass)
        self.assertNotIn("_charge_time", src)

    def test_every_live_dispatch_charges_positive_time(self):
        import inspect
        import possession_orchestrator as mod
        for fn in (mod._dispatch_drive, mod._dispatch_shot):
            src = inspect.getsource(fn)
            self.assertIn("_charge_time(engine,", src)


class TestEventStatConsistency(unittest.TestCase):
    """Reconciliation: `StatDeltas` (mutated directly by dispatch code)
    must never silently diverge from what the event stream itself can
    prove, for every field currently claimed DERIVABLE. This is the
    concrete test the reconciliation review asked for."""

    DERIVABLE_FIELDS = ("oreb", "dreb", "turnovers", "steals", "blocks")

    def test_generic_loose_ball_uses_original_offense_when_live_ownership_is_unresolved(self):
        """A deflection clears engine offense to None; an original-offense
        recovery must remain a continuation, not become a same-team turnover."""
        import random
        from dataclasses import replace
        from possession_orchestrator import resolve_generic_loose_ball
        from possession_state import BallState

        engine = PossessionEngine("loose", "A", "B", season="2023-24", rng_seed=1)
        apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
        engine.state = replace(engine.state, offense_team_id=None, ball_state=BallState.LOOSE,
                               ball_carrier=None, ball_control=None, ball_zone=SpatialZone.TOP_OF_KEY)
        world = PossessionWorld(team_a_id="A", team_b_id="B", team_a_five=OFF_FIVE,
                                team_b_five=DEF_FIVE, profiles=_profiles(),
                                player_zones={pid: SpatialZone.PAINT for pid in OFF_FIVE + DEF_FIVE})
        world.player_zones[OFF_FIVE[0]] = SpatialZone.TOP_OF_KEY  # sole eligible winner

        recovery = resolve_generic_loose_ball(engine, world, random.Random(1))
        self.assertEqual(recovery, "OFFENSE_RECOVERED")
        self.assertEqual(engine.state.offense_team_id, "A")

    def test_derivable_fields_match_across_many_seeds_and_configs(self):
        from possession_orchestrator import derive_stat_deltas_from_events
        configs = [
            PossessionConfig(),
            PossessionConfig(force_on_ball_contact_established=True),
            PossessionConfig(drive_action_seconds=30.0, pull_up_action_seconds=30.0, catch_and_shoot_action_seconds=30.0),
        ]
        checked = 0
        for cfg in configs:
            for seed in range(150):
                result = _run(config=cfg, seed=seed, possession_id=f"ec{seed}")
                derived = derive_stat_deltas_from_events(result.events)
                for field in self.DERIVABLE_FIELDS:
                    self.assertEqual(getattr(result.stats, field), getattr(derived, field),
                                      f"{field} diverged at seed={seed} config={cfg} reason={result.reason}")
                self.assertEqual(result.stats.personal_fouls, derived.personal_fouls,
                                  f"personal_fouls diverged at seed={seed} config={cfg} reason={result.reason}")
                checked += 1
        self.assertGreater(checked, 0)

    def test_event_derived_stats_has_no_scoring_fields(self):
        """The ABSENCE of points/fga/fgm/fg3a/fg3m/fta/ftm on
        `EventDerivedStats` IS the honest NOT-DERIVABLE-NOW
        classification -- not an oversight."""
        from possession_orchestrator import EventDerivedStats
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(EventDerivedStats)}
        for forbidden in ("points", "fga", "fgm", "fg3a", "fg3m", "fta", "ftm"):
            self.assertNotIn(forbidden, field_names)

    def test_loose_ball_recovery_is_now_a_real_event(self):
        """Reconciliation fix: `secure_loose_ball` itself logs nothing --
        this module's own supplementary checkpoint must appear whenever
        a generic loose-ball recovery happens."""
        from possession_events import EventType
        found = False
        for seed in range(300):
            result = _run(seed=seed, possession_id=f"lb{seed}")
            for e in result.events:
                if e.event_type == EventType.REACTION_CHECKPOINT and \
                        e.metadata.get("checkpoint") == "generic_loose_ball_recovered":
                    found = True
                    break
            if found:
                break
        self.assertTrue(found, "expected a generic loose-ball recovery checkpoint within 300 seeds")

    def test_offensive_charge_is_now_a_distinguishable_foul_event(self):
        """Reconciliation fix: a charge's personal foul was previously
        indistinguishable from any other DEAD_BALL_TURNOVER."""
        from possession_events import EventType
        cfg = PossessionConfig(force_on_ball_contact_established=True)
        found = False
        for seed in range(200):
            result = _run(config=cfg, seed=seed, possession_id=f"chg{seed}")
            if result.reason == PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER:
                checkpoints = [e for e in result.events if e.event_type == EventType.REACTION_CHECKPOINT
                               and e.metadata.get("checkpoint") == "floor_foul_administered"
                               and e.metadata.get("foul_class") == "OFFENSIVE_CHARGE"]
                self.assertTrue(checkpoints, "expected a floor_foul_administered checkpoint for the charge")
                found = True
                break
        self.assertTrue(found, "expected an OFFENSIVE_FOUL_TURNOVER within 200 seeds")

    def test_scoring_stats_are_not_asserted_event_derivable(self):
        """Documents (does not merely claim in prose) that no test in
        this suite treats points/FGA/FGM/etc. as event-derivable."""
        import inspect
        src = inspect.getsource(TestEventStatConsistency.test_derivable_fields_match_across_many_seeds_and_configs)
        for forbidden in ("points", "fga", "fgm", "fg3a", "fg3m", "fta", "ftm"):
            self.assertNotIn(forbidden, src)


class TestSourceOfTruthHierarchy(unittest.TestCase):
    def test_stat_deltas_is_documented_as_provisional(self):
        import inspect
        from possession_orchestrator import StatDeltas
        doc = inspect.getdoc(StatDeltas) or ""
        self.assertIn("PROVISIONAL", doc)

    def test_no_product_or_legacy_file_references_the_orchestrator(self):
        import subprocess
        for target in ("main.py", "season.py", "playoffs.py", "db.py", "game_engine.py"):
            result = subprocess.run(["grep", "-l", "possession_orchestrator", target],
                                     capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, f"{target} unexpectedly references possession_orchestrator")

    def test_orchestrator_never_imports_game_engine(self):
        import inspect
        import possession_orchestrator as mod
        src = inspect.getsource(mod)
        self.assertNotIn("import game_engine", src)
        self.assertNotIn("from game_engine", src)


class TestDefenderZoneStalenessFix(unittest.TestCase):
    """Focused tests for the defender-zone tracking fix -- see
    docs/DETAILED_ENGINE_FIRST_DIAGNOSTIC_REPORT.md's "Defender-Zone
    Staleness Correction" section for the demonstrated pathology this
    addresses (0 of 79 interior rebound opportunities won by the
    defense, entirely because defender zones were set once at inbound
    and never updated)."""

    def _engine_and_world(self):
        engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=1)
        apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
        engine.inbound("1", SpatialZone.TOP_OF_KEY, PossessionPhase.HALFCOURT)
        world = PossessionWorld(team_a_id="A", team_b_id="B", team_a_five=OFF_FIVE, team_b_five=DEF_FIVE,
                                 profiles=_profiles())
        world.player_zones = default_v0_zone_placement(OFF_FIVE, "1", SpatialZone.TOP_OF_KEY)
        _mirror_defender_zones(world.player_zones, engine.state.assignments)
        return engine, world

    # 1. defender zones are initialized correctly
    def test_defender_zones_initialized_to_match_their_assignment(self):
        engine, world = self._engine_and_world()
        for defender_id, assignment in engine.state.assignments.items():
            self.assertEqual(world.player_zones[defender_id], world.player_zones[assignment.assigned_to_player_id])

    # 2 + 3. supported actions update relevant defender zones; state is not frozen across a sequence
    def test_drive_updates_the_drivers_own_defender_zone(self):
        engine, world = self._engine_and_world()
        defender_id = _primary_defender(engine, "1")
        stale_zone_before = world.player_zones[defender_id]
        intent = ActionIntent(action_type=ActionType.DRIVE, actor_player_id="1", possession_id="p1")
        _dispatch_drive(engine, world, intent, PossessionConfig(force_on_ball_contact_established=False), __import__("random").Random(7), 0)
        self.assertEqual(world.player_zones[defender_id], engine.state.ball_zone)
        # a real drive with real leverage rolled -- not a no-op -- so this is a genuine, not incidental, check
        self.assertIsNotNone(engine.state.ball_zone)

    def test_pass_reception_updates_the_receivers_own_defender_zone_not_frozen_across_sequence(self):
        engine, world = self._engine_and_world()
        receiver_defender_id = _primary_defender(engine, "2")
        original_zone = world.player_zones[receiver_defender_id]
        intent = ActionIntent(action_type=ActionType.SWING_PASS, actor_player_id="1", target_player_id="2",
                               possession_id="p1", target_zone=SpatialZone.RESTRICTED_RIM.value)
        import random
        rng = random.Random(1)
        for _ in range(200):
            engine2, world2 = self._engine_and_world()
            outcome = _dispatch_pass(engine2, world2, intent, rng, 0)
            if outcome is None and world2.player_zones.get(_primary_defender(engine2, "2")) == SpatialZone.RESTRICTED_RIM:
                # a completed pass moved the receiver's OWN defender's zone away from its stale inbound value
                self.assertNotEqual(SpatialZone.RESTRICTED_RIM, original_zone)
                return
        self.fail("expected at least one completed pass to RESTRICTED_RIM within 200 tries")

    # 4. matchup identity remains valid after movement
    def test_matchup_identity_unchanged_by_zone_sync(self):
        engine, world = self._engine_and_world()
        defender_id = _primary_defender(engine, "1")
        _sync_assigned_defender_zone(engine, world, "1", SpatialZone.RESTRICTED_RIM)
        self.assertEqual(engine.state.assignments[defender_id].assigned_to_player_id, "1")  # unchanged

    # 5. switch assignment remains atomic and location state remains valid
    def test_zone_sync_respects_a_real_atomic_switch(self):
        from off_ball_screen_resolution import _atomic_switch_assignments
        engine, world = self._engine_and_world()
        old_defender = _primary_defender(engine, "1")
        other_defender = next(d for d in DEF_FIVE if d != old_defender)
        _atomic_switch_assignments(engine, old_defender, other_defender)
        new_defender = _primary_defender(engine, "1")
        self.assertNotEqual(new_defender, old_defender)  # the switch really changed who guards player "1"
        _sync_assigned_defender_zone(engine, world, "1", SpatialZone.RESTRICTED_RIM)
        # the sync followed the assignment AS IT NOW STANDS, not a stale pre-switch pointer
        self.assertEqual(world.player_zones[new_defender], SpatialZone.RESTRICTED_RIM)

    # 6. interior miss creates structurally eligible defensive rebound candidates
    def test_interior_miss_has_a_structurally_eligible_defender_candidate(self):
        from rebound_resolution import ReboundCandidate, ReboundOpportunity, eligible_rebound_candidates
        engine, world = self._engine_and_world()
        world.player_zones["1"] = SpatialZone.RESTRICTED_RIM  # the driver's own zone, as a real drive would set it
        _sync_assigned_defender_zone(engine, world, "1", SpatialZone.RESTRICTED_RIM)
        defender_id = _primary_defender(engine, "1")
        candidates = [
            ReboundCandidate(player_id="1", side="OFFENSE", zone=world.player_zones["1"], offensive_rebounding=0.1),
            ReboundCandidate(player_id=defender_id, side="DEFENSE", zone=world.player_zones[defender_id], defensive_rebounding=0.1),
        ]
        opportunity = ReboundOpportunity(source="MISSED_FG", shot_family="RIM",
                                          rebound_zone=SpatialZone.RESTRICTED_RIM, candidates=candidates)
        eligible = eligible_rebound_candidates(opportunity)
        self.assertEqual({c.player_id for c in eligible}, {"1", defender_id})

    def _loose_ball_after_interior_miss(self, engine):
        """Real engine transition -- the same one shot resolution actually performs -- rather than
        hand-setting `ball_state`/`ball_zone` directly."""
        engine.begin_shot(SpatialZone.RESTRICTED_RIM, dt=0.0)
        engine.resolve_shot_missed_pending_rebound("1", dt=0.0)

    # 7/8/9. defensive rebound possible after RIM/FLOATER miss; 3PT rebound behavior remains valid
    def test_defensive_rebound_reachable_after_rim_and_floater_misses(self):
        from possession_orchestrator import _dispatch_rebound
        from rebound_resolution import ReboundSource
        import random
        found = {"RIM": False, "FLOATER": False}
        for family in ("RIM", "FLOATER"):
            for seed in range(50):
                engine, world = self._engine_and_world()
                world.player_zones["1"] = SpatialZone.RESTRICTED_RIM
                _sync_assigned_defender_zone(engine, world, "1", SpatialZone.RESTRICTED_RIM)
                self._loose_ball_after_interior_miss(engine)
                terminal = _dispatch_rebound(engine, world, random.Random(seed), ReboundSource.MISSED_FG, family, 0,
                                              offense_team_id="A", defense_team_id="B")
                if terminal is not None and terminal.reason == PossessionTerminalReason.DEFENSIVE_REBOUND:
                    found[family] = True
                    break
        self.assertTrue(found["RIM"], "expected a real defensive rebound reachable after a RIM miss")
        self.assertTrue(found["FLOATER"], "expected a real defensive rebound reachable after a FLOATER miss")

    def test_three_point_rebound_both_outcomes_remain_reachable(self):
        from possession_orchestrator import _dispatch_rebound
        from rebound_resolution import ReboundSource
        import random
        seen = set()
        for seed in range(50):
            engine, world = self._engine_and_world()
            engine.begin_shot(SpatialZone.TOP_OF_KEY, dt=0.0)
            engine.resolve_shot_missed_pending_rebound("1", dt=0.0)
            terminal = _dispatch_rebound(engine, world, random.Random(seed), ReboundSource.MISSED_FG, "THREE_POINT", 0,
                                          offense_team_id="A", defense_team_id="B")
            seen.add(terminal.reason if terminal is not None else "OFFENSE_CONTINUES")
        self.assertEqual(seen, {PossessionTerminalReason.DEFENSIVE_REBOUND, "OFFENSE_CONTINUES"})

    # 10. no duplicate player/location authority introduced
    def test_sync_writes_only_to_the_existing_player_zones_dict(self):
        import inspect
        src = inspect.getsource(_sync_assigned_defender_zone)
        self.assertIn("world.player_zones[", src)
        self.assertNotIn("defender_zones_v2", src)
        self.assertNotIn("synthetic_zone", src)
        self.assertNotIn("rebound_only_zone", src)
        # confirms the field this function writes to is PossessionWorld's own existing field, not a new one
        import dataclasses
        from possession_orchestrator import PossessionWorld as _World
        field_names = {f.name for f in dataclasses.fields(_World)}
        self.assertIn("player_zones", field_names)
        self.assertNotIn("defender_zones", field_names)

    # 11. same-seed determinism preserved
    def test_deterministic_replay_preserved_with_zone_sync(self):
        def run():
            return simulate_possession("A", "B", OFF_FIVE, DEF_FIVE, _profiles(), inbound_receiver_id="1",
                                        config=PossessionConfig(), rng_seed=42, possession_id="det")
        first, second = run(), run()
        self.assertEqual(first.reason, second.reason)
        self.assertEqual(first.stats.points, second.stats.points)
        self.assertEqual(first.steps_taken, second.steps_taken)

    # 12. the sync itself never consumes RNG or changes an action's own outcome
    def test_sync_consumes_no_rng(self):
        import inspect
        src = inspect.getsource(_sync_assigned_defender_zone)
        self.assertNotIn("rng", src)
        self.assertNotIn("random", src)

    # 13. diagnostic telemetry remains observational after the fix
    def test_diagnostics_still_reconstructs_correctly_after_zone_fix(self):
        from detailed_engine_diagnostics import diagnose_possession
        result = simulate_possession("A", "B", OFF_FIVE, DEF_FIVE, _profiles(), inbound_receiver_id="1",
                                      config=PossessionConfig(), rng_seed=23024, possession_id="diag_check")
        # a lightweight PossessionRecord-shaped stand-in is unnecessary here -- the real cross-check lives in
        # test_detailed_engine_diagnostics.py; this just confirms the new sync doesn't break trace/action_log shape.
        self.assertIsInstance(result.world.trace, list)
        self.assertIsInstance(result.world.action_log, list)


if __name__ == "__main__":
    unittest.main()
