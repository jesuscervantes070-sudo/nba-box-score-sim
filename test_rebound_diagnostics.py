"""Focused guardrails for diagnostic-only rebound root-cause telemetry."""
import inspect
import unittest

import rebound_diagnostics
from detailed_engine_benchmark import run_benchmark_sample
from rebound_diagnostics import diagnose_rebounds
from rebound_resolution import ReboundOutcome


class TestReboundDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = run_benchmark_sample(range(25000, 25100))
        cls.diagnosis = diagnose_rebounds(cls.games)
        cls.records = [record for game in cls.games for record in game.result.possessions]
        cls.rows = [row for record in cls.records
                    for row in record.terminal_result.world.rebound_opportunity_log]

    def test_one_rebound_opportunity_per_reboundable_miss(self):
        self.assertEqual(self.diagnosis.reboundable_misses, self.diagnosis.rebound_opportunities)
        self.assertEqual(self.diagnosis.opportunities_per_reboundable_miss, 1.0)
        self.assertEqual(self.diagnosis.misses_without_opportunity, 0)
        self.assertEqual(self.diagnosis.opportunities_without_miss, 0)

    def test_no_duplicate_rebound_accounting(self):
        self.assertEqual(self.diagnosis.duplicate_opportunities, 0)
        self.assertEqual(self.diagnosis.accounting_mismatches, ())
        for record in self.records:
            rows = record.terminal_result.world.rebound_opportunity_log
            self.assertEqual(len({(row["step"], row["source"]) for row in rows}), len(rows))

    def test_eligibility_telemetry_reconciles_candidates(self):
        self.assertTrue(self.rows)
        for row in self.rows:
            eligible = [candidate for candidate in row["candidates"] if candidate["eligible"]]
            self.assertEqual(row["eligible_count"], len(eligible))
            self.assertEqual(row["eligible_offensive_count"],
                             sum(candidate["side"] == "OFFENSE" for candidate in eligible))
            self.assertEqual(row["eligible_defensive_count"],
                             sum(candidate["side"] == "DEFENSE" for candidate in eligible))

    def test_shot_family_classification_covers_every_opportunity(self):
        self.assertEqual(sum(stats.opportunities for stats in self.diagnosis.by_family.values()),
                         self.diagnosis.rebound_opportunities)
        self.assertEqual(set(self.diagnosis.by_family), {"RIM", "FLOATER", "THREE_POINT"})

    def test_oreb_dreb_outcomes_reconcile_to_stats_and_events(self):
        self.assertEqual(self.diagnosis.offensive_rebounds,
                         self.diagnosis.direct_offensive_rebounds +
                         self.diagnosis.team_offensive_rebounds)
        self.assertEqual(self.diagnosis.defensive_rebounds,
                         self.diagnosis.direct_defensive_rebounds +
                         self.diagnosis.team_defensive_rebounds)
        self.assertEqual(self.diagnosis.event_offensive_rebounds,
                         self.diagnosis.direct_offensive_rebounds)
        self.assertEqual(self.diagnosis.event_defensive_rebounds,
                         self.diagnosis.direct_defensive_rebounds)

    def test_loose_and_team_rebound_categories_are_exhaustive(self):
        categorized = (self.diagnosis.direct_offensive_rebounds +
                       self.diagnosis.direct_defensive_rebounds +
                       self.diagnosis.team_offensive_rebounds +
                       self.diagnosis.team_defensive_rebounds +
                       self.diagnosis.unresolved_rebound_outcomes)
        self.assertEqual(categorized, self.diagnosis.rebound_opportunities)
        self.assertEqual(self.diagnosis.rebound_opportunities_beginning_loose,
                         self.diagnosis.rebound_opportunities)
        self.assertTrue(all(row["outcome"] in {
            ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.SECURED_DEFENSE,
            ReboundOutcome.TEAM_REBOUND_OFFENSE, ReboundOutcome.TEAM_REBOUND_DEFENSE,
        } for row in self.rows))

    def test_second_chance_chain_counts_reconcile(self):
        oreb_counts = [record.provisional_deltas.oreb for record in self.records]
        self.assertEqual(self.diagnosis.possessions_with_one_oreb,
                         sum(count == 1 for count in oreb_counts))
        self.assertEqual(self.diagnosis.possessions_with_two_oreb,
                         sum(count == 2 for count in oreb_counts))
        self.assertEqual(self.diagnosis.possessions_with_three_plus_oreb,
                         sum(count >= 3 for count in oreb_counts))
        self.assertEqual(self.diagnosis.repeat_orebs_beyond_first,
                         sum(max(0, count - 1) for count in oreb_counts))

    def test_diagnostics_are_deterministic_and_cannot_consume_rng(self):
        first = run_benchmark_sample([26123])[0]
        before = first.result
        first_diagnosis = diagnose_rebounds([first])
        second = run_benchmark_sample([26123])[0]
        self.assertEqual(before, second.result)
        self.assertEqual(first_diagnosis, diagnose_rebounds([second]))
        source = inspect.getsource(rebound_diagnostics)
        self.assertNotIn("import random", source)
        self.assertNotIn(".random(", source)


if __name__ == "__main__":
    unittest.main()
