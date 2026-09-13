"""Focused guardrails for the V1 production on-ball-screen/POCKET_PASS activation mechanism.

"Activate on-ball screen roll creation" phase -- ON_BALL_SCREEN is the smallest legitimate
production interaction that finally makes the ALREADY-EXISTING POCKET_PASS opportunity/resolver
reachable (it previously required `roller_id`/`screen_active`, which production never supplied).
This module tests ONLY the new activation link -- opportunity generation, roller selection, screen
state lifetime, and downstream decision competition -- never a new pass/shot resolver (there isn't
one; POCKET_PASS still dispatches through the SAME `_dispatch_pass`/`resolve_pass` machinery every
other pass action uses)."""
import random
import unittest

from action_intent import ActionIntent, ActionType
from action_opportunity import generate_opportunities, StructuralContext
from possession_engine import PossessionEngine
from possession_orchestrator import (
    PlayerSimulationProfile, PossessionConfig, PossessionWorld,
    apply_matchup_assignments, build_structural_context, dispatch_action,
)
from possession_state import DefensivePosture, DribbleState, PossessionPhase, SpatialZone


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


def _engine_world(passing=0.20, finishing_role=0.5, phase=PossessionPhase.HALFCOURT,
                   ball_zone=SpatialZone.TOP_OF_KEY):
    engine = PossessionEngine("p1", "A", "B", season="2023-24", rng_seed=0)
    apply_matchup_assignments(engine, OFF_FIVE, DEF_FIVE)
    engine.inbound("1", ball_zone, phase)
    zones = {
        "1": ball_zone, "2": SpatialZone.LEFT_WING,
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


class TestOnBallScreenCreation(unittest.TestCase):
    def test_a_reachable_in_valid_halfcourt_state(self):
        engine, world = _engine_world()
        opps = generate_opportunities(engine.state, build_structural_context(engine, world))
        screen = next((o for o in opps if o.action_type == ActionType.ON_BALL_SCREEN), None)
        self.assertIsNotNone(screen)
        self.assertEqual(screen.actor_player_id, "1")

    def test_b_invalid_context_cannot_generate_screen(self):
        # TRANSITION phase -- this mechanism is HALFCOURT-only by construction.
        engine, world = _engine_world(phase=PossessionPhase.TRANSITION)
        opps = generate_opportunities(engine.state, build_structural_context(engine, world))
        self.assertFalse(any(o.action_type == ActionType.ON_BALL_SCREEN for o in opps))
        # no eligible screener at all (no role evidence on any teammate).
        engine2, world2 = _engine_world()
        for pid in OFF_FIVE:
            world2.profiles[pid] = PlayerSimulationProfile.synthetic(pid, "A", role_off_finishing=None)
        ctx = build_structural_context(engine2, world2)
        opps2 = generate_opportunities(engine2.state, ctx)
        self.assertFalse(any(o.action_type == ActionType.ON_BALL_SCREEN for o in opps2))

    def test_c_ball_handler_cannot_be_roller(self):
        engine, world = _engine_world()
        opps = generate_opportunities(engine.state, build_structural_context(engine, world))
        for opp in opps:
            if opp.action_type == ActionType.ON_BALL_SCREEN:
                self.assertNotEqual(opp.target_player_id, opp.actor_player_id)

    def test_d_roller_selection_is_a_real_valid_teammate(self):
        engine, world = _engine_world()
        ctx = build_structural_context(engine, world)
        opps = generate_opportunities(engine.state, ctx)
        screen = next(o for o in opps if o.action_type == ActionType.ON_BALL_SCREEN)
        self.assertIn(screen.target_player_id, OFF_FIVE)
        self.assertNotEqual(screen.target_player_id, "1")

    def test_e_screen_state_activates_correctly(self):
        engine, world = _engine_world()
        intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                               possession_id="p1", target_player_id="4")
        result = dispatch_action(engine, world, intent, PossessionConfig(), random.Random(1), 0)
        self.assertIsNone(result)  # non-terminal
        self.assertTrue(world.screen_active)
        self.assertEqual(world.roller_id, "4")
        self.assertEqual(world.screen_ball_handler_id, "1")
        self.assertIsNotNone(world.active_screen_id)

    def test_f_pocket_pass_unavailable_without_legitimate_screen(self):
        engine, world = _engine_world()
        ctx = build_structural_context(engine, world)  # screen never dispatched -- world.screen_active is False
        opps = generate_opportunities(engine.state, ctx)
        self.assertFalse(any(o.action_type == ActionType.POCKET_PASS for o in opps))

    def test_g_pocket_pass_available_with_legitimate_screen(self):
        engine, world = _engine_world()
        intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                               possession_id="p1", target_player_id="4")
        dispatch_action(engine, world, intent, PossessionConfig(), random.Random(1), 0)
        ctx = build_structural_context(engine, world)
        self.assertTrue(ctx.screen_active)
        self.assertEqual(ctx.roller_id, "4")
        opps = generate_opportunities(engine.state, ctx)
        pocket = next((o for o in opps if o.action_type == ActionType.POCKET_PASS), None)
        self.assertIsNotNone(pocket)
        self.assertEqual(pocket.target_player_id, "4")

    def test_h_pocket_pass_uses_ordinary_pass_resolution(self):
        """No new resolver -- POCKET_PASS dispatches through the SAME `_dispatch_pass`/
        `resolve_pass` every other pass action already uses."""
        import inspect
        import possession_orchestrator as po
        source = inspect.getsource(po.dispatch_action)
        self.assertIn("PASS_ACTIONS", source)
        from action_intent import PASS_ACTIONS
        self.assertIn(ActionType.POCKET_PASS, PASS_ACTIONS)

    def test_i_pocket_pass_can_fail_or_turnover(self):
        outcomes = set()
        for seed in range(60):
            engine, world = _engine_world(passing=0.02)
            screen_intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                                          possession_id="p1", target_player_id="4")
            dispatch_action(engine, world, screen_intent, PossessionConfig(), random.Random(seed), 0)
            pass_intent = ActionIntent(action_type=ActionType.POCKET_PASS, actor_player_id="1",
                                        possession_id="p1", target_player_id="4",
                                        target_zone=SpatialZone.PAINT.value)
            dispatch_action(engine, world, pass_intent, PossessionConfig(), random.Random(seed + 1000), 0)
            outcomes.add(world.player_zones["4"])
        self.assertIn(SpatialZone.MIDRANGE, outcomes)  # at least one failed/unchanged-zone outcome

    def test_j_successful_pocket_pass_can_reach_paint(self):
        found = False
        for seed in range(40):
            engine, world = _engine_world(passing=0.9)
            screen_intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                                          possession_id="p1", target_player_id="4")
            dispatch_action(engine, world, screen_intent, PossessionConfig(on_ball_screen_clean_advantage_probability=0.0),
                             random.Random(seed), 0)
            pass_intent = ActionIntent(action_type=ActionType.POCKET_PASS, actor_player_id="1",
                                        possession_id="p1", target_player_id="4",
                                        target_zone=SpatialZone.PAINT.value)
            dispatch_action(engine, world, pass_intent, PossessionConfig(), random.Random(seed), 0)
            if world.player_zones["4"] == SpatialZone.PAINT:
                found = True
                break
        self.assertTrue(found)

    def test_k_successful_pocket_pass_can_reach_restricted_rim(self):
        found = False
        for seed in range(40):
            engine, world = _engine_world(passing=0.9)
            screen_intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                                          possession_id="p1", target_player_id="4")
            dispatch_action(engine, world, screen_intent, PossessionConfig(on_ball_screen_clean_advantage_probability=1.0),
                             random.Random(seed), 0)
            pass_intent = ActionIntent(action_type=ActionType.POCKET_PASS, actor_player_id="1",
                                        possession_id="p1", target_player_id="4",
                                        target_zone=SpatialZone.RESTRICTED_RIM.value)
            dispatch_action(engine, world, pass_intent, PossessionConfig(), random.Random(seed), 0)
            if world.player_zones["4"] == SpatialZone.RESTRICTED_RIM:
                found = True
                break
        self.assertTrue(found)

    def test_l_screen_does_not_force_pocket_pass(self):
        """After a screen, the menu still includes DRIVE/PULL_UP/SWING_PASS/RESET_PASS -- POCKET_PASS
        is one option among several, never the only one."""
        engine, world = _engine_world()
        intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                               possession_id="p1", target_player_id="4")
        dispatch_action(engine, world, intent, PossessionConfig(), random.Random(1), 0)
        ctx = build_structural_context(engine, world)
        opps = generate_opportunities(engine.state, ctx)
        action_types = {o.action_type for o in opps}
        self.assertIn(ActionType.POCKET_PASS, action_types)
        self.assertIn(ActionType.DRIVE, action_types)
        self.assertIn(ActionType.SWING_PASS, action_types)
        self.assertGreater(len(action_types), 2)

    def test_m_drive_remains_available_after_screen(self):
        engine, world = _engine_world()
        intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                               possession_id="p1", target_player_id="4")
        dispatch_action(engine, world, intent, PossessionConfig(), random.Random(1), 0)
        self.assertEqual(engine.state.ball_control.state, DribbleState.LIVE_DRIBBLE)
        ctx = build_structural_context(engine, world)
        opps = generate_opportunities(engine.state, ctx)
        self.assertTrue(any(o.action_type == ActionType.DRIVE for o in opps))

    def test_n_pull_up_remains_available_after_screen(self):
        engine, world = _engine_world()
        intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                               possession_id="p1", target_player_id="4")
        dispatch_action(engine, world, intent, PossessionConfig(), random.Random(1), 0)
        ctx = build_structural_context(engine, world)
        opps = generate_opportunities(engine.state, ctx)
        self.assertTrue(any(o.action_type == ActionType.PULL_UP for o in opps))

    def test_o_defender_posture_transition(self):
        engine, world = _engine_world()
        defender_id = next(d for d, a in engine.state.assignments.items() if a.assigned_to_player_id == "1")
        self.assertEqual(engine.state.assignments[defender_id].posture, DefensivePosture.SQUARE)
        intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                               possession_id="p1", target_player_id="4")
        dispatch_action(engine, world, intent, PossessionConfig(on_ball_screen_clean_advantage_probability=1.0),
                         random.Random(1), 0)
        self.assertIn(engine.state.assignments[defender_id].posture,
                      (DefensivePosture.TRAILING, DefensivePosture.RECOVERING))

    def test_p_screen_state_clears_after_consumption(self):
        engine, world = _engine_world()
        screen_intent = ActionIntent(action_type=ActionType.ON_BALL_SCREEN, actor_player_id="1",
                                      possession_id="p1", target_player_id="4")
        dispatch_action(engine, world, screen_intent, PossessionConfig(), random.Random(1), 0)
        self.assertTrue(world.screen_active)
        swing_intent = ActionIntent(action_type=ActionType.SWING_PASS, actor_player_id="1",
                                     possession_id="p1", target_player_id="2",
                                     target_zone=SpatialZone.LEFT_WING.value)
        from possession_orchestrator import _clear_screen_context
        _clear_screen_context(engine, world, 1, "SWING_PASS")
        self.assertFalse(world.screen_active)
        self.assertIsNone(world.roller_id)
        self.assertIsNone(world.active_screen_id)

    def test_q_no_screen_state_crosses_possession(self):
        world = PossessionWorld(team_a_id="A", team_b_id="B", team_a_five=OFF_FIVE, team_b_five=DEF_FIVE,
                                 profiles=_profiles())
        self.assertFalse(world.screen_active)
        self.assertIsNone(world.roller_id)
        self.assertIsNone(world.active_screen_id)


if __name__ == "__main__":
    unittest.main()
