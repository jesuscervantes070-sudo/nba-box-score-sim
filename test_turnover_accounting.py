"""Focused team-vs-player turnover accounting reconciliation tests."""
import hashlib
import json
import unittest

from detailed_engine_benchmark import aggregate_team_game_stats, run_benchmark_sample
from detailed_game import DetailedGameConfig
from possession_events import EventType
from possession_orchestrator import PossessionConfig, derive_stat_deltas_from_events
from turnover_diagnostics import TurnoverCategory, classify_turnover, diagnose_turnovers


def _clean(value):
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


class TestTurnoverAccountingReconciliation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        historical_config = DetailedGameConfig(
            possession_config=PossessionConfig(pass_disruption_base_rate=0.12),
        )
        cls.games = run_benchmark_sample(range(25000, 25100), config=historical_config)
        cls.diagnosis = diagnose_turnovers(cls.games)
        cls.by_category = {}
        for game in cls.games:
            for record in game.result.possessions:
                observation = classify_turnover(record)
                if observation is not None:
                    cls.by_category.setdefault(observation.category, []).append((record, observation))

    def test_shot_clock_is_one_team_turnover(self):
        for record, _ in self.by_category[TurnoverCategory.SHOT_CLOCK_VIOLATION]:
            self.assertEqual(record.provisional_deltas.team_turnovers, 1)

    def test_shot_clock_is_zero_player_turnovers(self):
        for record, _ in self.by_category[TurnoverCategory.SHOT_CLOCK_VIOLATION]:
            self.assertEqual(record.provisional_deltas.turnovers, 0)
            self.assertEqual(record.provisional_deltas.player_turnovers, {})

    def test_shot_clock_is_zero_steals(self):
        for record, _ in self.by_category[TurnoverCategory.SHOT_CLOCK_VIOLATION]:
            self.assertEqual(record.provisional_deltas.steals, 0)

    def test_bad_pass_out_of_bounds_is_team_and_player_turnover(self):
        matches = [(r, o) for r, o in self.by_category[TurnoverCategory.PASS_BAD_PASS]
                   if o.raw_outcome == "BAD_PASS_OUT_OF_BOUNDS"]
        self.assertTrue(matches)
        for record, observation in matches:
            self.assertEqual(record.provisional_deltas.team_turnovers, 1)
            self.assertEqual(sum(record.provisional_deltas.player_turnovers.values()), 1)
            self.assertEqual(observation.steal_credited, 0)

    def test_clean_interception_is_team_player_turnover_and_steal(self):
        for record, _ in self.by_category[TurnoverCategory.PASS_CLEAN_INTERCEPTION]:
            self.assertEqual(record.provisional_deltas.team_turnovers, 1)
            self.assertEqual(sum(record.provisional_deltas.player_turnovers.values()), 1)
            self.assertEqual(record.provisional_deltas.steals, 1)

    def test_direct_bad_pass_to_defender_guarantees_control_and_steal(self):
        matches = [(r, o) for r, o in self.by_category[TurnoverCategory.PASS_BAD_PASS]
                   if o.raw_outcome == "BAD_PASS_TO_DEFENDER"]
        self.assertTrue(matches)
        for record, _ in matches:
            event = next(e for e in reversed(record.events)
                         if e.event_type == EventType.PASS_RESOLVED
                         and e.metadata.get("outcome") == "BAD_PASS_TO_DEFENDER")
            defender_id = event.metadata["disrupting_defender_id"]
            self.assertIsNotNone(defender_id)
            self.assertEqual(record.terminal_result.engine_state.ball_carrier, defender_id)
            self.assertEqual(record.provisional_deltas.steals, 1)

    def test_pass_loose_ball_recovery_does_not_automatically_credit_steal(self):
        for record, _ in self.by_category[TurnoverCategory.PASS_LOOSE_BALL_LOST]:
            self.assertEqual(record.provisional_deltas.steals, 0)
            self.assertEqual(record.provisional_deltas.team_turnovers, 1)

    def test_handle_strip_recovery_preserves_no_automatic_steal_doctrine(self):
        for record, _ in self.by_category[TurnoverCategory.HANDLE_STRIP_LOST]:
            self.assertEqual(record.provisional_deltas.steals, 0)
            self.assertEqual(record.provisional_deltas.team_turnovers, 1)

    def test_no_possession_has_duplicate_team_turnover(self):
        for _, observation in (item for values in self.by_category.values() for item in values):
            self.assertEqual(observation.engine_accounted_turnover, 1)

    def test_event_derived_team_player_and_steal_totals_reconcile(self):
        for game in self.games:
            for record in game.result.possessions:
                derived = derive_stat_deltas_from_events(record.events)
                direct = record.provisional_deltas
                self.assertEqual(derived.team_turnovers, direct.team_turnovers)
                self.assertEqual(derived.turnovers, direct.turnovers)
                self.assertEqual(derived.player_turnovers, direct.player_turnovers)
                self.assertEqual(derived.steals, direct.steals)

    def test_benchmark_uses_team_turnovers_and_preserves_player_total(self):
        team_total = player_total = 0
        for game in self.games:
            home, away = aggregate_team_game_stats(game.result, tuple(str(i) for i in range(1, 6)),
                                                    tuple(str(i) for i in range(11, 16)))
            team_total += home.turnovers + away.turnovers
            player_total += home.player_turnovers + away.player_turnovers
        # Structural shot-family reachability changes continuation/flip state
        # and therefore the later deterministic RNG trajectory; these pin the
        # reconnection baseline without changing turnover logic.
        self.assertEqual(team_total, 4868)
        self.assertEqual(player_total, 4679)

    def test_basketball_output_digest_matches_midrange_reconnection_baseline(self):
        payload = []
        for game in self.games:
            result = game.result
            rows = []
            for record in result.possessions:
                d = record.provisional_deltas
                rows.append({
                    "id": record.possession_id, "off": record.offense_team_id,
                    "def": record.defense_team_id,
                    "clocks": [record.start_game_clock, record.end_game_clock],
                    "scores": [record.start_score_home, record.start_score_away,
                               record.end_score_home, record.end_score_away],
                    "reason": record.terminal_result.reason,
                    "steps": record.terminal_result.steps_taken,
                    "actions": _clean(record.terminal_result.world.action_log),
                    "trace": _clean(record.terminal_result.world.trace),
                    "shots": _clean(record.terminal_result.world.shot_attempt_log),
                    "stages": _clean(record.terminal_result.world.stage_timing_log),
                    "inter": _clean(record.terminal_result.world.inter_action_log),
                    "events": [(e.event_type.value, e.delta_t, e.primary_player_id,
                                e.secondary_player_id, e.zone, _clean(e.metadata)) for e in record.events],
                    "non_tov_stats": [d.points, d.fga, d.fgm, d.fg3a, d.fg3m,
                                      d.fta, d.ftm, d.oreb, d.dreb, d.blocks,
                                      _clean(d.personal_fouls)],
                })
            payload.append({"seed": game.seed,
                            "final": [result.final_home_score, result.final_away_score],
                            "ot": result.overtime_periods, "rows": rows})
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, "bc67d315199c1c10f0c2f70a6d5322df90399db8c2ff9eb37ec5afda7802978c")


if __name__ == "__main__":
    unittest.main()
