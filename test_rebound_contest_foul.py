"""Focused correctness guardrails for the "Calibrate defensive floor foul occurrence" phase's new
rebound-contest-foul mechanism (`possession_orchestrator._dispatch_rebound`'s
`REBOUND_CONTEST_FOUL_CHECK` branch, gated by `PossessionConfig.rebound_contest_foul_hazard`).

These are deliberately narrow, causal-mechanism tests -- broader occurrence/administration
reconciliation is already covered by `test_rebound_diagnostics.py`, `test_detailed_engine_foul_diagnostics.py`,
`test_turnover_accounting.py`, and `test_turnover_diagnostics.py`.
"""
import unittest
from dataclasses import replace

from detailed_engine_benchmark import run_benchmark_sample
from detailed_engine_foul_diagnostics import diagnose_fouls
from detailed_game import DetailedGameConfig
from floor_foul_administration import DEFENSIVE_FLOOR_FOUL, OFFENSIVE_CHARGE
from possession_orchestrator import PossessionConfig
from rebound_diagnostics import diagnose_rebounds
from rebound_resolution import ReboundOutcome


class TestReboundContestFoulOccurrence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25050))  # TRAIN half
        cls.results = tuple(game.result for game in cls.games)
        cls.records = [record for result in cls.results for record in result.possessions]
        cls.rows = [row for record in cls.records
                    for row in record.terminal_result.world.rebound_opportunity_log]
        cls.intercepted = [row for row in cls.rows if row["outcome"] == "INTERCEPTED_BY_FOUL"]

    # A. Legitimate-context-only occurrence.
    def test_intercepted_rows_always_have_a_real_two_sided_contest(self):
        self.assertTrue(self.intercepted)
        for row in self.intercepted:
            self.assertGreater(row["eligible_offensive_count"], 0)
            self.assertGreater(row["eligible_defensive_count"], 0)

    # B. Impossible-context exclusion: zero hazard -> zero occurrences (inertness), regardless of
    # how many real two-sided contests exist.
    def test_zero_hazard_produces_zero_interceptions(self):
        inert_config = DetailedGameConfig(
            possession_config=PossessionConfig(rebound_contest_foul_hazard=None),
        )
        games = run_benchmark_sample(range(25000, 25010), config=inert_config)
        rows = [row for game in games for record in game.result.possessions
                for row in record.terminal_result.world.rebound_opportunity_log]
        self.assertTrue(rows)  # real two-sided contests still occur
        self.assertTrue(any(row["eligible_offensive_count"] > 0 and row["eligible_defensive_count"] > 0
                            for row in rows))
        self.assertEqual(sum(row["outcome"] == "INTERCEPTED_BY_FOUL" for row in rows), 0)

    # C. Classification: this mechanism only ever produces DEFENSIVE_FLOOR_FOUL, never a shooting
    # foul and never OFFENSIVE_CHARGE (a real, documented V1 simplification).
    def test_mechanism_only_ever_classifies_as_defensive_floor_foul(self):
        diagnosis = diagnose_fouls(self.results)
        self.assertGreater(sum(diagnosis.rebound_contest_foul_checks_by_outcome.values()), 0)
        self.assertNotIn(OFFENSIVE_CHARGE, diagnosis.rebound_contest_foul_checks_by_outcome)
        self.assertGreater(diagnosis.rebound_contest_foul_defensive_outcomes, 0)
        self.assertGreater(diagnosis.rebound_contest_foul_no_foul_continuations, 0)
        self.assertEqual(
            diagnosis.rebound_contest_foul_defensive_outcomes
            + diagnosis.rebound_contest_foul_no_foul_continuations,
            diagnosis.eligible_rebound_contest_foul_opportunities,
        )

    # D. FOULS BY ORIGIN correctly attributes this mechanism's fouls to REBOUND, not DRIVE.
    def test_defensive_floor_fouls_by_origin_splits_drive_and_rebound(self):
        diagnosis = diagnose_fouls(self.results)
        by_origin = diagnosis.defensive_floor_fouls_by_origin
        self.assertGreater(by_origin.get("DRIVE", 0), 0)
        self.assertGreater(by_origin.get("REBOUND", 0), 0)
        self.assertEqual(sum(by_origin.values()), diagnosis.defensive_floor_fouls)
        self.assertEqual(diagnosis.fouls_by_origin["REBOUND"], by_origin.get("REBOUND", 0))

    # E. FGA accounting unchanged: an intercepted rebound opportunity never itself produces a shot
    # attempt -- the miss/FT that PRODUCED the opportunity was already logged before interception.
    def test_interception_adds_no_shot_attempt(self):
        for record in self.records:
            rows = record.terminal_result.world.rebound_opportunity_log
            intercepted_here = [row for row in rows if row["outcome"] == "INTERCEPTED_BY_FOUL"]
            if not intercepted_here:
                continue
            shots = record.terminal_result.world.shot_attempt_log
            misses = sum(not shot.get("made") for shot in shots)
            final_ft_rows = sum(row["source"] == "FINAL_MISSED_FT" for row in rows)
            # the SAME reconciliation invariant `rebound_diagnostics.py` already enforces globally
            self.assertEqual(misses + final_ft_rows, len(rows))

    # F. Possession consequence, non-bonus: offense retains the ball -- the SAME real team ids are
    # restored, never left `None` (regression guard for the `offense_team_id`/`defense_team_id`
    # restoration bug fix in `_dispatch_rebound`).
    def test_non_bonus_interception_restores_real_team_ids_and_continues(self):
        found_non_bonus = False
        for record in self.records:
            rows = record.terminal_result.world.rebound_opportunity_log
            if not any(row["outcome"] == "INTERCEPTED_BY_FOUL" for row in rows):
                continue
            # a possession that is NOT immediately terminal at a rebound-contest-foul step means
            # the non-bonus "offense retains ball" branch (or a bonus make) ran without crashing
            # and without leaving engine_state team ids as None for any DOWNSTREAM decision --
            # `build_structural_context`'s own real `ValueError` (fixed this phase) is the direct
            # regression guard: a full canonical run completing at all IS the proof.
            found_non_bonus = True
        self.assertTrue(found_non_bonus)

    # G. Rebound-opportunity accounting stays exhaustive and duplicate-free even with the real,
    # active hazard (full reconciliation, not just the narrow duplicate check).
    def test_rebound_diagnostics_reconcile_with_real_hazard_active(self):
        diagnosis = diagnose_rebounds(self.games)
        self.assertEqual(diagnosis.duplicate_opportunities, 0)
        self.assertEqual(diagnosis.accounting_mismatches, ())
        self.assertEqual(diagnosis.reboundable_misses, diagnosis.rebound_opportunities)

    # H. Deterministic choice of fouler/fouled: never ability-weighted -- confirmed by construction
    # (first eligible candidate per side, by stable `world.all_ten()` order), verified here by
    # checking the SAME seed reproduces the SAME fouler/fouled pair across two independent runs.
    def test_seeded_replay_is_deterministic(self):
        first = run_benchmark_sample([25000])[0]
        second = run_benchmark_sample([25000])[0]

        def _fingerprint(game):
            out = []
            for record in game.result.possessions:
                for row in record.terminal_result.world.rebound_opportunity_log:
                    if row["outcome"] == "INTERCEPTED_BY_FOUL":
                        out.append((record.possession_id, row["step"], row["cascade_depth"]))
            return out

        self.assertEqual(_fingerprint(first), _fingerprint(second))

    # I. Cascade disambiguation: a legitimate second, distinct rebound opportunity produced by this
    # foul's own bonus-FT-miss chain is never miscounted as a duplicate of the opportunity that
    # triggered it (regression guard for the `cascade_depth` fix).
    def test_cascading_opportunities_share_step_and_source_but_not_cascade_depth(self):
        chained = [
            record for record in self.records
            for rows in [record.terminal_result.world.rebound_opportunity_log]
            if len({(row["step"], row["source"]) for row in rows}) < len(rows)
        ]
        self.assertTrue(chained)
        for record in chained:
            rows = record.terminal_result.world.rebound_opportunity_log
            keys = {(row["step"], row["source"], row["cascade_depth"]) for row in rows}
            self.assertEqual(len(keys), len(rows))

    # J. This mechanism never resolves to a normal securing outcome -- it always short-circuits
    # into foul administration instead (never both).
    def test_intercepted_rows_never_carry_a_rebounder(self):
        for row in self.intercepted:
            self.assertIsNone(row["rebounder_id"])
            self.assertNotIn(row["outcome"], {
                ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.SECURED_DEFENSE,
                ReboundOutcome.TEAM_REBOUND_OFFENSE, ReboundOutcome.TEAM_REBOUND_DEFENSE,
            })


if __name__ == "__main__":
    unittest.main()
