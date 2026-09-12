"""Focused guardrails for the V1 pass-created INTERIOR_SEAL mechanism."""
import inspect
import random
import unittest

from action_intent import ActionIntent, ActionType
from action_opportunity import ObjectiveOpportunity, StructuralContext, generate_opportunities
from action_perception import NO_GATE_PROVENANCE, PerceivedOpportunity
from action_selection import ClockContext, RoleContext, SelectionPolicy, TendencyContext
from detailed_engine_benchmark import run_benchmark_sample
from detailed_engine_opportunity_diagnostics import assert_opportunity_reconciliation, diagnose_opportunities
from possession_engine import PossessionEngine
from possession_orchestrator import (
    PlayerSimulationProfile, PossessionConfig, PossessionWorld, _resolve_interior_pass_destination,
    apply_matchup_assignments, build_structural_context, dispatch_action,
)
from possession_state import DribbleState, PossessionPhase, SpatialZone


OFF_FIVE = ("1", "2", "3", "4", "5")
DEF_FIVE = ("11", "12", "13", "14", "15")


def _profiles(passing=0.20, finishing_role=0.5):
    out = {}
    for pid in OFF_FIVE:
        out[pid] = PlayerSimulationProfile.synthetic(
            pid, "A", passing_accuracy_ast_pct=passing, role_off_finishing=finishing_role,
        )
    for pid in DEF_FIVE:
        out[pid] = PlayerSimulationProfile.synthetic(pid, "B")
    return out


def _engine_world(passing=0.20, finishing_role=0.5, phase=PossessionPhase.HALFCOURT):
    engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=0)
    apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
    engine.inbound("1", SpatialZone.TOP_OF_KEY, phase)
    zones = {
        "1": SpatialZone.TOP_OF_KEY, "2": SpatialZone.LEFT_WING,
        "3": SpatialZone.RIGHT_WING, "4": SpatialZone.MIDRANGE,
        "5": SpatialZone.RIGHT_CORNER,
    }
    for defender, assignment in engine.state.assignments.items():
        zones[defender] = zones[assignment.assigned_to_player_id]
    world = PossessionWorld(
        team_a_id="A", team_b_id="B", team_a_five=OFF_FIVE, team_b_five=DEF_FIVE,
        profiles=_profiles(passing, finishing_role), player_zones=zones,
    )
    return engine, world


class TestInteriorSealCreation(unittest.TestCase):
    def test_a_b_reachable_in_legitimate_halfcourt_without_drive(self):
        engine, world = _engine_world()
        opps = generate_opportunities(engine.state, build_structural_context(engine, world))
        seal = next(o for o in opps if o.action_type == ActionType.INTERIOR_SEAL)
        self.assertEqual(seal.target_player_id, "4")
        self.assertEqual(seal.source, "midrange_deployment_seal")
        self.assertEqual(engine.state.ball_zone, SpatialZone.TOP_OF_KEY)

    def test_c_impossible_contexts_do_not_offer_seal(self):
        engine, world = _engine_world(finishing_role=0.2)
        self.assertFalse(any(o.action_type == ActionType.INTERIOR_SEAL for o in
                             generate_opportunities(engine.state, build_structural_context(engine, world))))
        engine2, world2 = _engine_world(phase=PossessionPhase.TRANSITION)
        self.assertFalse(any(o.action_type == ActionType.INTERIOR_SEAL for o in
                             generate_opportunities(engine2.state, build_structural_context(engine2, world2))))
        engine3, world3 = _engine_world()
        world3.player_zones["4"] = SpatialZone.LEFT_CORNER
        self.assertFalse(any(o.action_type == ActionType.INTERIOR_SEAL for o in
                             generate_opportunities(engine3.state, build_structural_context(engine3, world3))))
        engine4, world4 = _engine_world()
        engine4.dead_dribble()
        self.assertFalse(any(o.action_type == ActionType.INTERIOR_SEAL for o in
                             generate_opportunities(engine4.state, build_structural_context(engine4, world4))))

    def test_d_e_paint_and_rim_destinations_are_reachable(self):
        config = PossessionConfig()
        destinations = {
            _resolve_interior_pass_destination(ActionType.INTERIOR_SEAL, config, random.Random(seed))
            for seed in range(40)
        }
        self.assertEqual(destinations, {SpatialZone.PAINT, SpatialZone.RESTRICTED_RIM})

    def test_f_opportunity_does_not_guarantee_selection(self):
        seal = ObjectiveOpportunity("s", ActionType.INTERIOR_SEAL, "1", "4", SpatialZone.PAINT)
        shot = ObjectiveOpportunity(
            "j", ActionType.PULL_UP, "1", target_zone=SpatialZone.TOP_OF_KEY,
            shot_zone_options=(SpatialZone.TOP_OF_KEY, SpatialZone.MIDRANGE),
        )
        perceived = [PerceivedOpportunity(seal, NO_GATE_PROVENANCE),
                     PerceivedOpportunity(shot, NO_GATE_PROVENANCE)]
        selected = {
            SelectionPolicy(random.Random(seed)).select(
                perceived, RoleContext(), TendencyContext(), ClockContext(12.0), "p1",
                interior_seal_selection_log_weight=-1.2,
            ).action_type
            for seed in range(80)
        }
        self.assertEqual(selected, {ActionType.INTERIOR_SEAL, ActionType.PULL_UP})

    def test_g_h_i_pass_success_and_failure_are_real_and_zone_changes_only_on_success(self):
        saw_success = saw_failure = saw_turnover = False
        for seed in range(200):
            engine, world = _engine_world(passing=0.02)
            original_zone = world.player_zones["4"]
            intent = ActionIntent(ActionType.INTERIOR_SEAL, "1", "p1", "4", SpatialZone.PAINT.value)
            terminal = dispatch_action(engine, world, intent, PossessionConfig(), random.Random(seed), 0)
            moved = world.player_zones["4"] in (SpatialZone.PAINT, SpatialZone.RESTRICTED_RIM)
            if moved:
                saw_success = True
            else:
                saw_failure = True
                self.assertEqual(world.player_zones["4"], original_zone)
            if terminal is not None:
                saw_turnover = True
        self.assertTrue(saw_success)
        self.assertTrue(saw_failure)
        self.assertTrue(saw_turnover)

    def test_j_shot_selection_remains_downstream_after_success(self):
        for seed in range(100):
            engine, world = _engine_world()
            intent = ActionIntent(ActionType.INTERIOR_SEAL, "1", "p1", "4", SpatialZone.PAINT.value)
            terminal = dispatch_action(engine, world, intent, PossessionConfig(), random.Random(seed), 0)
            if terminal is None and world.player_zones["4"] in (SpatialZone.PAINT, SpatialZone.RESTRICTED_RIM):
                opps = generate_opportunities(engine.state, StructuralContext(just_caught_pass=True))
                shot = next(o for o in opps if o.action_type == ActionType.CATCH_AND_SHOOT)
                self.assertEqual(shot.shot_zone_options, (world.player_zones["4"],))
                return
        self.fail("no completed interior-seal pass found")

    def test_k_l_no_family_override_or_ability_driven_opportunity(self):
        import action_opportunity
        import possession_orchestrator
        self.assertNotIn("INTERIOR_SEAL", inspect.getsource(possession_orchestrator._dispatch_shot))
        source = inspect.getsource(action_opportunity.generate_opportunities)
        self.assertNotIn("shrunk_rate", source)
        self.assertNotIn("rim_finishing", source)

    def test_m_three_point_action_remains_in_same_menu(self):
        engine, world = _engine_world()
        opps = generate_opportunities(engine.state, build_structural_context(engine, world))
        pull_up = next(o for o in opps if o.action_type == ActionType.PULL_UP)
        self.assertIn(SpatialZone.TOP_OF_KEY, pull_up.shot_zone_options)
        self.assertTrue(any(o.action_type == ActionType.INTERIOR_SEAL for o in opps))

    def test_n_o_p_existing_cut_drive_and_transition_paths_remain_reachable(self):
        engine, world = _engine_world()
        ctx = build_structural_context(engine, world)
        opps = generate_opportunities(engine.state, ctx)
        self.assertTrue(any(o.action_type == ActionType.INTERIOR_CUT for o in opps))
        self.assertTrue(any(o.action_type == ActionType.DRIVE for o in opps))
        engine2, world2 = _engine_world(phase=PossessionPhase.TRANSITION)
        opps2 = generate_opportunities(engine2.state, build_structural_context(engine2, world2))
        self.assertTrue(any(o.action_type == ActionType.TRANSITION_PUSH for o in opps2))

    def test_c_reset_is_preserved_when_seal_is_not_available(self):
        engine, world = _engine_world(finishing_role=0.2)
        opps = generate_opportunities(engine.state, build_structural_context(engine, world))
        self.assertTrue(any(o.action_type == ActionType.RESET_PASS for o in opps))
        self.assertFalse(any(o.action_type == ActionType.INTERIOR_SEAL for o in opps))

    def test_q_offensive_rebound_continuation_is_preserved(self):
        games = run_benchmark_sample(range(25000, 25003))
        for game in games:
            for record in game.result.possessions:
                world = record.terminal_result.world
                oreb = next((row for row in world.trace
                             if row.get("action") == "REBOUND_OPPORTUNITY"
                             and row.get("outcome") in ("SECURED_OFFENSE", "TEAM_REBOUND_OFFENSE")), None)
                if oreb is not None:
                    self.assertTrue(any(action["step"] > oreb["step"] for action in world.action_log))
                    return
        self.fail("canonical sample contained no offensive-rebound continuation")

    def test_r_diagnostics_reconcile_and_consume_no_rng(self):
        games = run_benchmark_sample(range(25000, 25003))
        results = tuple(game.result for game in games)
        first = diagnose_opportunities(results)
        assert_opportunity_reconciliation(first)
        self.assertEqual(first, diagnose_opportunities(results))
        funnel = first.interior_origin_funnels["INTERIOR_SEAL"]
        self.assertGreater(funnel.opportunities_offered, 0)
        self.assertEqual(funnel.selected, funnel.pass_attempts)


if __name__ == "__main__":
    unittest.main()
