"""Invariants for three-point shot resolution and the read-only capture helper."""
import hashlib
import unittest

import shot_resolution as sr
import three_point_resolution_diagnostic as tpd
from detailed_game import simulate_detailed_game
from possession_orchestrator import PlayerSimulationProfile

RATES = (0.25, 0.30, 0.35, 0.40, 0.45)


class TestMakeProbability(unittest.TestCase):
    def test_monotonic_in_ability(self):
        probs = [tpd.make_probability(r) for r in RATES]
        self.assertEqual(probs, sorted(probs))

    def test_weak_average_elite_ordering_in_every_context(self):
        for contest in (sr.ContestBucket.WIDE_OPEN, sr.ContestBucket.OPEN, sr.ContestBucket.TIGHT):
            for mode in (sr.ReleaseMode.CATCH_AND_SHOOT, sr.ReleaseMode.PULL_UP):
                weak, avg, elite = (tpd.make_probability(r, release_mode=mode, contest=contest) for r in (0.30, 0.36, 0.42))
                self.assertLess(weak, avg)
                self.assertLess(avg, elite)

    def test_contest_ordering(self):
        order = [sr.ContestBucket.VERY_TIGHT, sr.ContestBucket.TIGHT, sr.ContestBucket.OPEN, sr.ContestBucket.WIDE_OPEN]
        probs = [tpd.make_probability(0.36, contest=c) for c in order]
        self.assertEqual(probs, sorted(probs))

    def test_catch_and_shoot_beats_pull_up_when_open(self):
        self.assertGreater(tpd.make_probability(0.36), tpd.make_probability(0.36, release_mode=sr.ReleaseMode.PULL_UP))

    def test_ability_sensitivity_is_near_one_to_one(self):
        for row in tpd.response_curve(rates=RATES) + tpd.response_curve(rates=RATES, release_mode=sr.ReleaseMode.PULL_UP):
            self.assertGreater(row["dp_d_base"], 0.9)
            self.assertLess(row["dp_d_base"], 1.05)

    def test_no_clipping_over_realistic_range(self):
        for r in (0.20, 0.25, 0.45, 0.50):
            for contest in (sr.ContestBucket.VERY_TIGHT, sr.ContestBucket.WIDE_OPEN):
                p = tpd.make_probability(r, contest=contest)
                self.assertGreater(p, 0.02)
                self.assertLess(p, 0.98)


class TestCaptureHelper(unittest.TestCase):
    def _run(self, capture):
        home = tuple(str(940001 + i) for i in range(5))
        away = tuple(str(940011 + i) for i in range(5))
        profiles = {p: PlayerSimulationProfile.synthetic(p, "HOME") for p in home}
        profiles.update({p: PlayerSimulationProfile.synthetic(p, "AWAY") for p in away})
        seed = int(hashlib.sha256(b"capture-test").hexdigest()[:16], 16)
        sink = []
        if capture:
            with tpd.capture_three_point_attempts(sink):
                r = simulate_detailed_game("HOME", "AWAY", home, away, profiles, rng_seed=seed)
        else:
            r = simulate_detailed_game("HOME", "AWAY", home, away, profiles, rng_seed=seed)
        return r.final_home_score, r.final_away_score, sink, profiles

    def test_capture_does_not_change_the_game(self):
        with_capture = self._run(True)
        without = self._run(False)
        self.assertEqual(with_capture[:2], without[:2])
        self.assertTrue(with_capture[2])

    def test_capture_restores_original_function(self):
        import possession_orchestrator as po
        original = po.apply_perimeter_shot_to_engine
        self._run(True)
        self.assertIs(po.apply_perimeter_shot_to_engine, original)

    def test_capture_does_not_mutate_profiles(self):
        _, _, _, profiles = self._run(True)
        fresh = PlayerSimulationProfile.synthetic("940001", "HOME")
        self.assertEqual(profiles["940001"], fresh)

    def test_deterministic_seed(self):
        self.assertEqual(self._run(True)[:2], self._run(True)[:2])


if __name__ == "__main__":
    unittest.main()
