"""Focused safeguards for the first rebound-acquisition mapping calibration."""
import inspect
import math
import random
import unittest

import action_selection
from possession_state import SpatialZone
from rebound_resolution import (
    BoxOutState, DEFENSIVE_REBOUND_RATE_REFERENCE,
    OFFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT, OFFENSIVE_REBOUND_RATE_REFERENCE,
    ReboundCandidate, ReboundOpportunity, ReboundOutcome, _candidate_log_weight,
    eligible_rebound_candidates, rebound_rate_to_acquisition_log_weight, resolve_rebound,
)


def _one_on_one_probability(offense_rate: float, defense_rate: float) -> float:
    offense = rebound_rate_to_acquisition_log_weight(offense_rate, "OFFENSE")
    defense = rebound_rate_to_acquisition_log_weight(defense_rate, "DEFENSE")
    return math.exp(offense) / (math.exp(offense) + math.exp(defense))


class TestReboundAcquisitionMapping(unittest.TestCase):
    def test_mapping_is_side_centered_logit_plus_explicit_baseline(self):
        self.assertAlmostEqual(
            rebound_rate_to_acquisition_log_weight(OFFENSIVE_REBOUND_RATE_REFERENCE, "OFFENSE"),
            OFFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT,
        )
        self.assertAlmostEqual(
            rebound_rate_to_acquisition_log_weight(DEFENSIVE_REBOUND_RATE_REFERENCE, "DEFENSE"), 0.0,
        )

    def test_analytical_synthetic_probability(self):
        self.assertAlmostEqual(_one_on_one_probability(0.08, 0.15), 0.24707696348041758)

    def test_higher_offensive_evidence_never_lowers_offense_probability(self):
        probabilities = [_one_on_one_probability(rate, 0.15) for rate in (0.02, 0.05, 0.08, 0.12, 0.20)]
        self.assertEqual(probabilities, sorted(probabilities))

    def test_higher_defensive_evidence_never_raises_offense_probability(self):
        probabilities = [_one_on_one_probability(0.08, rate) for rate in (0.05, 0.10, 0.15, 0.20, 0.30)]
        self.assertEqual(probabilities, sorted(probabilities, reverse=True))

    def test_poor_average_and_strong_evidence_remain_meaningfully_separated(self):
        poor, average, strong = (_one_on_one_probability(rate, 0.15) for rate in (0.04, 0.08, 0.16))
        self.assertLess(poor, average)
        self.assertLess(average, strong)
        self.assertGreater(strong - poor, 0.20)

    def test_missing_evidence_is_not_silently_imputed(self):
        with self.assertRaisesRegex(ValueError, "missing rebound-rate evidence"):
            rebound_rate_to_acquisition_log_weight(None, "OFFENSE")

    def test_boxout_delta_remains_additive_and_scale_compatible(self):
        neutral = ReboundCandidate("9", "DEFENSE", SpatialZone.RESTRICTED_RIM,
                                   defensive_rebounding=0.15)
        boxout = ReboundCandidate("9", "DEFENSE", SpatialZone.RESTRICTED_RIM,
                                  defensive_rebounding=0.15,
                                  box_out_state=BoxOutState.ESTABLISHED_BOXOUT)
        boxed_out = ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM,
                                     offensive_rebounding=0.08, boxed_out_by="9")
        unboxed = ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM,
                                   offensive_rebounding=0.08)
        self.assertAlmostEqual(_candidate_log_weight(boxout) - _candidate_log_weight(neutral), 0.6)
        self.assertAlmostEqual(_candidate_log_weight(boxed_out) - _candidate_log_weight(unboxed), -0.6)

    def test_deterministic_replay(self):
        zone = SpatialZone.RESTRICTED_RIM
        opportunity = ReboundOpportunity("MISSED_FG", "RIM", zone, [
            ReboundCandidate("1", "OFFENSE", zone, offensive_rebounding=0.08),
            ReboundCandidate("9", "DEFENSE", zone, defensive_rebounding=0.15),
        ])
        self.assertEqual(resolve_rebound(opportunity, random.Random(42)),
                         resolve_rebound(opportunity, random.Random(42)))

    def test_geometry_is_unchanged_exact_zone_eligibility(self):
        opportunity = ReboundOpportunity("MISSED_FG", "RIM", SpatialZone.RESTRICTED_RIM, [
            ReboundCandidate("1", "OFFENSE", SpatialZone.RESTRICTED_RIM, offensive_rebounding=0.08),
            ReboundCandidate("2", "OFFENSE", SpatialZone.LEFT_WING, offensive_rebounding=0.20),
        ])
        self.assertEqual([candidate.player_id for candidate in eligible_rebound_candidates(opportunity)], ["1"])

    def test_action_selection_has_no_acquisition_mapping_dependency(self):
        source = inspect.getsource(action_selection)
        self.assertNotIn("rebound_rate_to_acquisition_log_weight", source)
        self.assertNotIn("OFFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT", source)


if __name__ == "__main__":
    unittest.main()
