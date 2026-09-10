"""
Focused unit tests for player_ability_estimation.py -- the offline
attribute-estimation prototype. Root-level, not in tests/ (Codex's),
same convention as test_player_ability_profile.py.

Run with: python3 -m unittest test_player_ability_estimation -v

Some tests use real cached data (this project's own cache/*.json) --
they're skipped gracefully if a specific season/player isn't cached,
rather than failing the whole suite on missing data.
"""
import glob
import unittest

from player_ability_estimation import (
    estimate_attribute, result_to_attribute_estimate, _weighted_shrunk_estimate,
    _percentile_rating, _seasons_through_cutoff, SeasonEvidence, RATING_MIN, RATING_MAX,
)

ALL_SEASONS = sorted(d.split('/')[-1] for d in glob.glob('cache/????-??'))


class TestCutoffLeakage(unittest.TestCase):
    def test_seasons_through_cutoff_excludes_future(self):
        seasons = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23"]
        result = _seasons_through_cutoff("2020-21", seasons)
        self.assertEqual(result, ["2018-19", "2019-20", "2020-21"])
        self.assertNotIn("2021-22", result)
        self.assertNotIn("2022-23", result)

    def test_real_estimate_uses_no_future_seasons(self):
        # A real player's profile as of an early season must never
        # reference a later one, even though this project's cache
        # covers 30 real seasons total.
        result = estimate_attribute("LeBron James", "2005-06", "three_point", ALL_SEASONS)
        for ev in result.seasons_used:
            self.assertLessEqual(int(ev.season[:4]), 2005)


class TestWeightedShrunkEstimate(unittest.TestCase):
    def test_no_evidence_returns_none_not_zero(self):
        raw, shrunk, weight = _weighted_shrunk_estimate([], "2020-21", 100.0, 0.35)
        self.assertIsNone(raw)
        self.assertIsNone(shrunk)
        self.assertEqual(weight, 0.0)

    def test_zero_sample_seasons_contribute_nothing(self):
        evidence = [SeasonEvidence(season="2020-21", rate=0.9, sample=0.0)]
        raw, shrunk, weight = _weighted_shrunk_estimate(evidence, "2020-21", 100.0, 0.35)
        self.assertIsNone(raw)  # sample=0 means no real evidence, not a real 0.9 rate

    def test_small_sample_shrinks_toward_league_average(self):
        # A tiny real sample (10 attempts) at an extreme rate should land
        # much closer to league average than to the raw extreme rate.
        evidence = [SeasonEvidence(season="2020-21", rate=1.0, sample=10.0)]
        raw, shrunk, weight = _weighted_shrunk_estimate(evidence, "2020-21", 200.0, 0.35)
        self.assertEqual(raw, 1.0)
        self.assertLess(shrunk, 0.5)  # heavily pulled toward 0.35, nowhere near the raw 1.0
        self.assertGreater(shrunk, 0.35)  # but still pulled up somewhat by the real (if thin) evidence

    def test_large_sample_stays_close_to_raw(self):
        evidence = [SeasonEvidence(season="2020-21", rate=0.45, sample=5000.0)]
        raw, shrunk, weight = _weighted_shrunk_estimate(evidence, "2020-21", 200.0, 0.35)
        self.assertAlmostEqual(shrunk, 0.45, delta=0.01)  # a huge real sample barely gets shrunk at all

    def test_recent_seasons_weighted_more_than_old(self):
        # Two seasons with IDENTICAL sample size but different rates and
        # different recency -- the weighted average must land closer to
        # the MORE RECENT season's rate.
        evidence = [
            SeasonEvidence(season="2015-16", rate=0.30, sample=500.0),
            SeasonEvidence(season="2020-21", rate=0.50, sample=500.0),
        ]
        raw, shrunk, weight = _weighted_shrunk_estimate(evidence, "2020-21", 1_000_000.0, 0.40)
        # with a huge prior_strength, shrunk ~= league_avg regardless -- test the RAW weighted average instead
        self.assertGreater(raw, 0.40)  # closer to the recent 0.50 than the midpoint 0.40

    def test_no_division_by_zero_with_all_zero_samples(self):
        evidence = [SeasonEvidence(season="2020-21", rate=0.5, sample=0.0),
                    SeasonEvidence(season="2019-20", rate=0.5, sample=0.0)]
        # must not raise ZeroDivisionError
        raw, shrunk, weight = _weighted_shrunk_estimate(evidence, "2020-21", 100.0, 0.35)
        self.assertIsNone(raw)

    def test_deterministic(self):
        evidence = [SeasonEvidence(season="2019-20", rate=0.38, sample=300.0),
                    SeasonEvidence(season="2020-21", rate=0.41, sample=280.0)]
        r1 = _weighted_shrunk_estimate(evidence, "2020-21", 150.0, 0.355)
        r2 = _weighted_shrunk_estimate(evidence, "2020-21", 150.0, 0.355)
        self.assertEqual(r1, r2)


class TestPercentileRating(unittest.TestCase):
    def test_bounded_0_to_99(self):
        ref = [0.1 * i for i in range(1, 101)]
        self.assertGreaterEqual(_percentile_rating(-5.0, ref), RATING_MIN)
        self.assertLessEqual(_percentile_rating(-5.0, ref), RATING_MAX)
        self.assertLessEqual(_percentile_rating(500.0, ref), RATING_MAX)

    def test_ordering_preserved(self):
        ref = [0.20, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55]
        low = _percentile_rating(0.25, ref)
        high = _percentile_rating(0.50, ref)
        self.assertLess(low, high)

    def test_empty_reference_returns_neutral(self):
        self.assertEqual(_percentile_rating(0.5, []), 50.0)


class TestRealPlayerCases(unittest.TestCase):
    """These exercise the real pipeline end to end against real cached
    data -- skipped (not failed) if the specific season isn't cached."""

    def test_non_shooter_stays_unestimated_not_zero(self):
        result = estimate_attribute("Rudy Gobert", "2017-18", "three_point", ALL_SEASONS)
        if not result.seasons_used and result.weighted_raw_rate is None:
            self.assertIsNone(result.percentile_rating)
            est = result_to_attribute_estimate(result)
            self.assertFalse(est.is_estimated)
            self.assertIsNone(est.value)
        else:
            self.skipTest("Gobert had measurable 3PT attempts in this cache slice -- inspect manually")

    def test_valid_rating_bounds_for_a_real_elite_shooter(self):
        result = estimate_attribute("Stephen Curry", "2015-16", "three_point", ALL_SEASONS)
        if result.percentile_rating is None:
            self.skipTest("2015-16 Curry not found in this cache")
        self.assertGreaterEqual(result.percentile_rating, RATING_MIN)
        self.assertLessEqual(result.percentile_rating, RATING_MAX)
        self.assertGreater(result.percentile_rating, 80.0)  # a real, historically elite shooter should rate clearly above average

    def test_single_season_rookie_is_heavily_shrunk(self):
        result = estimate_attribute("Victor Wembanyama", "2023-24", "passing", ALL_SEASONS)
        if result.weighted_raw_rate is None:
            self.skipTest("2023-24 Wembanyama not found in this cache")
        self.assertEqual(len(result.seasons_used), 1)  # only one real season of evidence exists
        # shrunk value must sit between the raw single-season rate and league average
        lo, hi = sorted([result.weighted_raw_rate, result.league_avg_rate])
        self.assertTrue(lo <= result.shrunk_rate <= hi)

    def test_confidence_and_sample_size_persist_into_attribute_estimate(self):
        result = estimate_attribute("Stephen Curry", "2015-16", "three_point", ALL_SEASONS)
        if result.percentile_rating is None:
            self.skipTest("2015-16 Curry not found in this cache")
        est = result_to_attribute_estimate(result)
        self.assertTrue(est.is_estimated)
        self.assertIsNotNone(est.confidence)
        self.assertTrue(0.0 <= est.confidence <= 1.0)
        self.assertIsNotNone(est.sample_size)
        self.assertGreater(est.sample_size, 0)


if __name__ == "__main__":
    unittest.main()


class TestTrueReboundSplits(unittest.TestCase):
    """New in this phase: true OREB_PCT/DREB_PCT preferred over the
    combined-reb_pct-split fallback approximation."""

    def test_true_data_preferred_when_available(self):
        result = estimate_attribute("Stephen Curry", "2015-16", "offensive_rebounding", ALL_SEASONS)
        if result.percentile_rating is None:
            self.skipTest("2015-16 Curry not found in this cache")
        self.assertEqual(result.evidence_mode, "TRUE")
        for ev in result.seasons_used:
            self.assertEqual(ev.mode, "true")

    def test_missing_true_data_falls_back_explicitly_not_silently(self):
        import player_ability_estimation as pae
        orig = pae.load_player_rebound_splits
        try:
            pae.load_player_rebound_splits = lambda s: {}  # simulate no true data cached for any season
            result = pae.estimate_attribute("Stephen Curry", "2015-16", "offensive_rebounding", ALL_SEASONS)
            if result.percentile_rating is None:
                self.skipTest("2015-16 Curry not found in this cache")
            self.assertEqual(result.evidence_mode, "FALLBACK")
            for ev in result.seasons_used:
                self.assertEqual(ev.mode, "fallback")
        finally:
            pae.load_player_rebound_splits = orig

    def test_other_attributes_report_na_mode(self):
        result = estimate_attribute("Stephen Curry", "2015-16", "three_point", ALL_SEASONS)
        if result.percentile_rating is None:
            self.skipTest("2015-16 Curry not found in this cache")
        self.assertEqual(result.evidence_mode, "N/A")

    def test_no_evidence_reports_missing_mode(self):
        result = estimate_attribute("Not A Real Player", "2020-21", "offensive_rebounding", ALL_SEASONS)
        self.assertEqual(result.evidence_mode, "MISSING")
        self.assertIsNone(result.percentile_rating)

    def test_rebound_splits_respect_cutoff_too(self):
        # Same leakage guarantee must hold for the new true-data path.
        result = estimate_attribute("LeBron James", "2005-06", "defensive_rebounding", ALL_SEASONS)
        for ev in result.seasons_used:
            self.assertLessEqual(int(ev.season[:4]), 2005)


if __name__ == "__main__":
    unittest.main()
