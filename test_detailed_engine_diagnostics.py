"""Focused tests proving detailed_engine_diagnostics.py's telemetry is
correct and purely observational (First Diagnostic instrumentation)."""
import unittest

from detailed_engine_diagnostics import (
    ActionTelemetry,
    diagnose_game,
    diagnose_games,
    diagnose_possession,
)
from detailed_game import DetailedGameConfig, simulate_detailed_game
from possession_orchestrator import PlayerSimulationProfile, PossessionConfig

OFF_FIVE = tuple(str(i) for i in range(1, 6))
DEF_FIVE = tuple(str(i) for i in range(11, 16))


def _profiles():
    profiles = {}
    for p in OFF_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "HOME")
    for p in DEF_FIVE:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
    return profiles


def _game(seed=23024, config=None):
    return simulate_detailed_game("HOME", "AWAY", OFF_FIVE, DEF_FIVE, _profiles(), rng_seed=seed, config=config)


class TestPossessionDiagnostics(unittest.TestCase):
    def setUp(self):
        self.result = _game()

    def test_elapsed_equals_start_minus_end_and_never_negative(self):
        for record in self.result.possessions:
            d = diagnose_possession(record)
            self.assertAlmostEqual(d.elapsed_game_clock_seconds, record.start_game_clock - record.end_game_clock)
            self.assertGreaterEqual(d.elapsed_game_clock_seconds, 0.0)
            self.assertLessEqual(d.elapsed_game_clock_seconds, record.start_game_clock + 1e-9)

    def test_action_count_matches_real_dispatch_count(self):
        for record in self.result.possessions:
            d = diagnose_possession(record)
            self.assertEqual(d.action_count, len(record.terminal_result.world.action_log))
            # steps and actions are DELIBERATELY never conflated into one field -- `step_count` is
            # `PossessionTerminalResult`'s own 0-based loop-iteration index (Phase 23A semantics,
            # unchanged here), while `action_count` is a real COUNT of dispatched ActionIntents; they
            # measure different things by design and are not expected to satisfy a fixed numeric
            # relationship (e.g. a possession terminating on its very first dispatch has
            # step_count=0, action_count=1).
            self.assertGreaterEqual(d.step_count, 0)
            if d.terminal_reason in ("SHOT_CLOCK_VIOLATION", "PERIOD_END"):
                # Structural Timing Hook: the entry-stage charge itself can now exhaust the shot/game
                # clock before ANY action is ever dispatched (a real, intended possibility, e.g. very
                # little clock remains at a period's end) -- action_count==0 is legitimate here.
                self.assertGreaterEqual(d.action_count, 0)
            else:
                self.assertGreaterEqual(d.action_count, 1)

    def test_misses_counted_exactly_once_per_shot(self):
        for record in self.result.possessions:
            d = diagnose_possession(record)
            self.assertEqual(d.misses, d.fga - d.fgm)
            self.assertGreaterEqual(d.misses, 0)

    def test_rebound_opportunity_not_double_counted(self):
        """Exactly one REBOUND_OPPORTUNITY trace entry exists per real
        `_dispatch_rebound` invocation -- never duplicated by telemetry
        itself (telemetry only counts what's already in the trace, it
        never re-derives or re-triggers a resolution)."""
        for record in self.result.possessions:
            trace = record.terminal_result.world.trace
            real_count = sum(1 for e in trace if e.get("action") == "REBOUND_OPPORTUNITY")
            d = diagnose_possession(record)
            self.assertEqual(d.rebound_opportunities, real_count)

    def test_oreb_dreb_classification_matches_provisional_deltas(self):
        for record in self.result.possessions:
            d = diagnose_possession(record)
            self.assertEqual(d.oreb, record.provisional_deltas.oreb)
            self.assertEqual(d.dreb, record.provisional_deltas.dreb)

    def test_multiple_orebs_in_one_possession_represented(self):
        multi_oreb = [diagnose_possession(r) for r in self.result.possessions
                      if r.provisional_deltas.oreb >= 2]
        self.assertTrue(multi_oreb, "expected at least one possession with 2+ OREBs in this real game")
        for d in multi_oreb:
            self.assertEqual(d.second_chance_count, d.oreb)
            self.assertGreaterEqual(d.rebound_opportunities, d.oreb)

    def test_turnover_subtype_classification(self):
        found_subtypes = set()
        for record in self.result.possessions:
            d = diagnose_possession(record)
            if record.terminal_result.reason == "TURNOVER":
                self.assertIsNotNone(d.turnover_subtype)
                found_subtypes.add(d.turnover_subtype)
            elif record.terminal_result.reason == "OFFENSIVE_FOUL_TURNOVER":
                self.assertEqual(d.turnover_subtype, "OFFENSIVE_CHARGE")
            else:
                self.assertIsNone(d.turnover_subtype)
        self.assertTrue(found_subtypes, "expected at least one classified turnover subtype in this real game")
        self.assertLessEqual(found_subtypes, {"BAD_PASS_OUT_OF_BOUNDS", "BAD_PASS_TO_DEFENDER", "CLEAN_INTERCEPTION",
                                               "LOOSE_BALL_DEFENSE_RECOVERED", "OTHER_DEAD_BALL_TURNOVER",
                                               "OTHER_LIVE_BALL_TURNOVER", "OTHER_TURNOVER"})


class TestGameDiagnostics(unittest.TestCase):
    def setUp(self):
        self.result = _game()
        self.diag = diagnose_game(self.result)

    def test_terminal_reason_aggregation_sums_to_total_possessions(self):
        self.assertEqual(sum(self.diag.terminal_reason_distribution.values()), self.diag.total_possessions)

    def test_game_level_equals_sum_of_possession_diagnostics(self):
        per_possession = [diagnose_possession(r) for r in self.result.possessions]
        self.assertEqual(self.diag.oreb, sum(d.oreb for d in per_possession))
        self.assertEqual(self.diag.dreb, sum(d.dreb for d in per_possession))
        self.assertEqual(self.diag.fga, sum(d.fga for d in per_possession))
        self.assertEqual(self.diag.fgm, sum(d.fgm for d in per_possession))
        self.assertEqual(self.diag.rebound_opportunities, sum(d.rebound_opportunities for d in per_possession))
        self.assertEqual(self.diag.turnovers, sum(1 for d in per_possession if d.turnover_subtype is not None))
        self.assertEqual(self.diag.personal_fouls, sum(d.personal_fouls for d in per_possession))
        self.assertEqual(self.diag.fta, sum(d.fta for d in per_possession))
        for name, telem in self.diag.action_telemetry.items():
            expected_count = sum(pd.action_telemetry.get(name, ActionTelemetry(name)).count for pd in per_possession)
            self.assertEqual(telem.count, expected_count)

    def test_oreb_share_matches_oreb_over_oreb_plus_dreb(self):
        self.assertAlmostEqual(self.diag.oreb_share, self.diag.oreb / (self.diag.oreb + self.diag.dreb))

    def test_oreb_count_distribution_sums_to_total_possessions(self):
        self.assertEqual(sum(self.diag.oreb_count_distribution.values()), self.diag.total_possessions)

    def test_rebound_opportunities_per_miss_is_close_to_one_not_duplicated(self):
        """The primary diagnostic question: is exactly one rebound
        opportunity produced per miss? (Not asserting realism -- just
        that telemetry can answer the question and there is no gross
        multiplicative duplication.)"""
        self.assertIsNotNone(self.diag.rebound_opportunities_per_miss)
        self.assertLess(self.diag.rebound_opportunities_per_miss, 1.5)

    def test_no_faults_and_deterministic_output_unaffected_by_telemetry(self):
        """Computing diagnostics twice from the SAME already-produced
        result must never mutate it, and re-running the simulation with
        the identical seed (with or without ever calling diagnostics)
        must reproduce the identical basketball result."""
        again = diagnose_game(self.result)
        self.assertEqual(again.total_possessions, self.diag.total_possessions)
        self.assertEqual(again.oreb, self.diag.oreb)

        fresh_result = _game()  # a brand-new simulate_detailed_game call, same seed
        self.assertEqual(fresh_result.final_home_score, self.result.final_home_score)
        self.assertEqual(fresh_result.final_away_score, self.result.final_away_score)
        self.assertEqual(fresh_result.total_possessions, self.result.total_possessions)
        # and diagnosing it produces identical aggregate telemetry
        fresh_diag = diagnose_game(fresh_result)
        self.assertEqual(fresh_diag.oreb, self.diag.oreb)
        self.assertEqual(fresh_diag.turnovers, self.diag.turnovers)


class TestMultiGameDiagnostics(unittest.TestCase):
    def test_ten_game_sample_matches_known_reported_aggregate(self):
        results = [_game(seed=s) for s in range(23024, 23034)]
        multi = diagnose_games(results)
        self.assertEqual(multi.game_count, 10)
        # Loose bounds -- not a calibration assertion, just confirming the telemetry reconstructs the same
        # order of magnitude every run. Widened three times: once after the defender-zone staleness fix
        # (see "Defender-Zone Staleness Correction"), again after the Structural Timing Hook (see
        # "Structural Timing Hook"), and again after the Inter-Action Timing Structure (see that section)
        # -- possessions per game legitimately DROPPED further once real, nonzero live inter-action time
        # started consuming game clock between decisions within a possession (fewer, longer possessions
        # fit in 48 minutes), which is that hook's own intended, demonstrated structural effect, not a bug.
        self.assertGreater(multi.mean_total_possessions, 250)
        self.assertLess(multi.mean_total_possessions, 900)
        self.assertGreater(multi.mean_oreb, 80)


class TestTelemetryIsObservationalOnly(unittest.TestCase):
    def test_diagnostics_module_never_imports_rng_or_selection(self):
        """Namespace-name scan (same methodology this project already
        established for firewall checks) -- checks the module's actual
        top-level symbols/imports, not prose. The module's own docstring
        legitimately NAMES `SelectionPolicy`/`simulate_possession` in
        prose to explain the doctrine boundary; a raw full-text scan
        would false-positive on that documentation."""
        import detailed_engine_diagnostics as mod
        self.assertNotIn("random", vars(mod))
        self.assertNotIn("SelectionPolicy", vars(mod))
        self.assertNotIn("simulate_possession", vars(mod))
        self.assertNotIn("simulate_detailed_game", vars(mod))

    def test_diagnostics_never_imported_by_simulation_modules(self):
        import inspect
        import possession_orchestrator
        import detailed_game_orchestrator
        import detailed_game
        for mod in (possession_orchestrator, detailed_game_orchestrator, detailed_game):
            src = inspect.getsource(mod)
            self.assertNotIn("detailed_engine_diagnostics", src)


class TestClockAccounting(unittest.TestCase):
    """Focused tests for the Timing/Pace Root-Cause Diagnosis's own clock
    reconciliation claims -- see docs/DETAILED_ENGINE_FIRST_DIAGNOSTIC_REPORT.md's
    "Timing / Pace Root-Cause Diagnosis" section."""

    def test_possession_elapsed_seconds_reconcile_with_total_period_length(self):
        """The sum of every possession's own elapsed game clock, across a
        complete regulation game, must equal exactly regulation_periods *
        regulation_period_seconds -- no double subtraction, no lost time."""
        result = _game()
        total_elapsed = sum(r.start_game_clock - r.end_game_clock for r in result.possessions)
        self.assertAlmostEqual(total_elapsed, 4 * 720.0, places=6)

    def test_action_and_loose_ball_time_fully_explains_total_elapsed(self):
        """Every second of game clock consumed must be attributable to a
        known, already-tracked category (dispatched-action time, generic
        loose-ball recovery time, possession-stage-timing time -- Structural
        Timing Hook -- or inter-action time -- Inter-Action Timing
        Structure) -- confirms no clock is being silently consumed/lost by
        an unaccounted-for code path."""
        result = _game()
        diag = diagnose_game(result)
        action_seconds = sum(t.total_seconds for t in diag.action_telemetry.values())
        loose_ball_count = sum(1 for r in result.possessions for e in r.terminal_result.world.trace
                                if e.get("action") == "LOOSE_BALL_RECOVERY")
        loose_ball_seconds = loose_ball_count * PossessionConfig().loose_ball_action_seconds
        stage_timing_seconds = sum(
            e.get("elapsed_game_clock_seconds") or 0.0
            for r in result.possessions for e in r.terminal_result.world.stage_timing_log
        )
        inter_action_seconds = sum(
            e.get("elapsed_game_clock_seconds") or 0.0
            for r in result.possessions for e in r.terminal_result.world.inter_action_log
        )
        total_elapsed = sum(r.start_game_clock - r.end_game_clock for r in result.possessions)
        # small floating-point tolerance only -- not a calibration fudge factor
        self.assertAlmostEqual(action_seconds + loose_ball_seconds + stage_timing_seconds + inter_action_seconds,
                                total_elapsed, delta=1.0)

    def test_floor_foul_branch_now_charges_the_same_drive_time_as_any_other_drive_outcome(self):
        """Regression test for the fixed clock-bookkeeping bug (see the
        diagnosis report's Sec. J and the "Structural Timing Hook"
        section's own correction). A drive routing into the floor-foul
        branch (`OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL`) must now
        charge EXACTLY `drive_action_seconds` -- the SAME real constant
        every other drive outcome already charges -- exactly once, not
        zero and not twice."""
        cfg = DetailedGameConfig(possession_config=PossessionConfig(force_on_ball_contact_established=True))
        result = _game(config=cfg)
        found_foul = False
        for record in result.possessions:
            trace = record.terminal_result.world.trace
            for e in trace:
                if e.get("action") == "ON_BALL_PRESSURE" and e.get("outcome") in ("OFFENSIVE_CHARGE", "DEFENSIVE_FLOOR_FOUL"):
                    step = e.get("step")
                    drive_entries = [a for a in record.terminal_result.world.action_log
                                     if a.get("step") == step and a.get("action_type") == "DRIVE"]
                    self.assertEqual(len(drive_entries), 1)  # charged exactly once -- no double-charge
                    self.assertAlmostEqual(drive_entries[0]["elapsed_game_clock_seconds"],
                                            PossessionConfig().drive_action_seconds, places=6)
                    found_foul = True
        self.assertTrue(found_foul, "expected at least one floor foul with forced contact enabled")


class TestShotClockAtAttemptAggregation(unittest.TestCase):
    def setUp(self):
        from detailed_engine_diagnostics import diagnose_shot_clock_at_attempt
        self.result = _game()
        self.diag = diagnose_shot_clock_at_attempt(self.result)

    def test_total_fga_matches_raw_shot_attempt_log_count(self):
        raw_count = sum(len(r.terminal_result.world.shot_attempt_log) for r in self.result.possessions)
        self.assertEqual(self.diag.total_fga, raw_count)
        self.assertGreater(raw_count, 0)

    def test_bin_counts_sum_to_total_fga(self):
        self.assertEqual(sum(b.count for b in self.diag.bin_stats.values()), self.diag.total_fga)

    def test_stage_origin_fga_sums_to_total_fga(self):
        self.assertEqual(sum(s.fga for s in self.diag.by_stage_origin.values()), self.diag.total_fga)

    def test_first_action_fga_never_exceeds_total(self):
        self.assertLessEqual(self.diag.first_action_fga_count, self.diag.total_fga)

    def test_ten_game_shot_clock_aggregate_matches_known_order_of_magnitude(self):
        from detailed_engine_diagnostics import diagnose_shot_clock_at_attempt_multi
        results = [_game(seed=s) for s in range(23024, 23034)]
        multi = diagnose_shot_clock_at_attempt_multi(results)
        self.assertEqual(multi.game_count, 10)
        self.assertGreater(multi.mean_total_fga, 0)
        self.assertIsNotNone(multi.mean_shot_clock_remaining)
        self.assertGreater(multi.mean_shot_clock_remaining, 0.0)
        self.assertLessEqual(multi.mean_shot_clock_remaining, 24.0)


if __name__ == "__main__":
    unittest.main()
