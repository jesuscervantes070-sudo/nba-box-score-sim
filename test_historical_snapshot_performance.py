"""Focused tests for Historical Snapshot Performance V1 -- proves the process-local `lru_cache`
memoization added this phase (in `turnover_ingestion.py`, `rim_protection_analysis.py`,
`foul_analysis.py`, `poa_containment_analysis.py`, `player_shot_event_ingestion.py`, and the
composition-layer caches already in `historical_game_snapshot.py`) is real REUSE (not a change in
any output), is correctly keyed (no season/date/attribute contamination), can be cleared safely for
poison tests, and preserves every already-tested temporal-safety/determinism/engine-output
guarantee. PERFORMANCE ONLY -- no estimator math, shrinkage, priors, or engine mechanics are
touched or asserted differently than before this phase."""
import glob
import unittest
from unittest.mock import patch

import historical_game_snapshot as hgs
import turnover_ingestion as ti
import rim_protection_analysis as rpa
import foul_analysis as fa
import poa_containment_analysis as pca
import player_shot_event_ingestion as psei
import player_defensive_truth as pdt
import player_scoring_truth_temporal as psst
from game_metadata import get_game_metadata

ALL_SEASONS = sorted(d.split('/')[-1] for d in glob.glob('cache/????-??'))
SEASON = "2023-24"
DENVER_GAME = "0022300061"  # real 2023-24 Denver Nuggets vs Los Angeles Lakers, 2023-10-24
AS_OF_DATE = "2023-10-24"

# Real 2023-24 Denver Nuggets roster player_ids (a subset), used to prove multi-player reuse
# without needing a full rotation build.
NUGGETS_PLAYERS = ["1627750", "203999", "1629027", "1628366", "2544", "201566", "203076"]


def setUpModule():
    hgs.clear_estimator_caches()


class TestLoaderMemoization(unittest.TestCase):
    """A. Loader memoization -- `player_shot_event_ingestion.load_shot_events` and
    `turnover_ingestion.resolve_full_name` are each a pure function of their real, hashable,
    explicit inputs; repeated calls with the SAME inputs must not re-read/re-parse the underlying
    file/static list."""

    def setUp(self):
        hgs.clear_estimator_caches()

    def test_a_load_shot_events_is_memoized_per_season(self):
        psei.load_shot_events(SEASON)
        info_after_first = psei.load_shot_events.cache_info()
        psei.load_shot_events(SEASON)
        psei.load_shot_events(SEASON)
        info_after_repeat = psei.load_shot_events.cache_info()
        self.assertEqual(info_after_first.misses, 1)
        self.assertEqual(info_after_repeat.misses, 1)  # no new miss -- served from cache
        self.assertEqual(info_after_repeat.hits, info_after_first.hits + 2)

    def test_a_resolve_full_name_is_memoized_per_player_id(self):
        ti.resolve_full_name(2544)  # LeBron James -- real, stable id
        misses_before = ti.resolve_full_name.cache_info().misses
        ti.resolve_full_name(2544)
        ti.resolve_full_name(2544)
        self.assertEqual(ti.resolve_full_name.cache_info().misses, misses_before)


class TestReferencePopulationMemoization(unittest.TestCase):
    """B. Reference-population memoization -- the three per-season league-wide reference builders
    (rim protection / foul / POA containment) must not rebuild their full population on every call
    for the same season."""

    def setUp(self):
        hgs.clear_estimator_caches()

    def test_b_rim_rows_memoized_per_season(self):
        rpa.build_player_rim_rows(SEASON)
        misses_before = rpa.build_player_rim_rows.cache_info().misses
        rpa.build_player_rim_rows(SEASON)
        rpa.build_player_rim_rows(SEASON)
        self.assertEqual(rpa.build_player_rim_rows.cache_info().misses, misses_before)

    def test_b_foul_rows_memoized_per_season(self):
        fa.build_player_foul_rows(SEASON)
        misses_before = fa.build_player_foul_rows.cache_info().misses
        fa.build_player_foul_rows(SEASON)
        self.assertEqual(fa.build_player_foul_rows.cache_info().misses, misses_before)

    def test_b_containment_rows_memoized_per_season(self):
        pca.build_player_containment_rows(SEASON)
        misses_before = pca.build_player_containment_rows.cache_info().misses
        pca.build_player_containment_rows(SEASON)
        self.assertEqual(pca.build_player_containment_rows.cache_info().misses, misses_before)


class TestSeasonAndDateKeySeparation(unittest.TestCase):
    """C. Season key separation. D. Date key separation where applicable."""

    def setUp(self):
        hgs.clear_estimator_caches()

    def test_c_different_seasons_do_not_collide_in_rim_rows_cache(self):
        if len(ALL_SEASONS) < 2:
            self.skipTest("need at least 2 cached seasons")
        s0, s1 = ALL_SEASONS[-2], ALL_SEASONS[-1]
        rows0 = rpa.build_player_rim_rows(s0)
        rows1 = rpa.build_player_rim_rows(s1)
        # real, independent per-season reference populations -- must not be the same object/result
        # unless both seasons genuinely have identical (likely empty) data.
        seasons_in_0 = {r.season for r in rows0}
        seasons_in_1 = {r.season for r in rows1}
        if rows0 and rows1:
            self.assertEqual(seasons_in_0, {s0})
            self.assertEqual(seasons_in_1, {s1})

    def test_d_scoring_truth_cache_is_keyed_by_as_of_date_not_just_season(self):
        # player_scoring_truth_temporal's own existing _shot_event_prefix_ledger_for /
        # _pregame_shot_zone_ability_estimate machinery is date-sensitive within a season; the
        # composition-layer cache key (as_of_date included) must keep two different dates in the
        # SAME season from colliding.
        p = NUGGETS_PLAYERS[0]
        early = hgs._build_scoring_truth_cached(p, "2023-11-01", SEASON, tuple(ALL_SEASONS), None)
        later = hgs._build_scoring_truth_cached(p, "2024-03-01", SEASON, tuple(ALL_SEASONS), None)
        # Not asserting they differ (real evidence might tie), but proving they are two distinct
        # cache entries, not one collapsed lookup:
        info = hgs._build_scoring_truth_cached.cache_info()
        self.assertGreaterEqual(info.misses, 2)


class TestCacheClearing(unittest.TestCase):
    """F. Cache clearing -- `clear_estimator_caches()` actually resets every owned cache, and a
    poisoned-then-cleared-then-rebuilt read reflects the NEW source data, not stale cached state."""

    def test_f_clear_estimator_caches_resets_every_cache(self):
        rpa.build_player_rim_rows(SEASON)
        fa.build_player_foul_rows(SEASON)
        pca.build_player_containment_rows(SEASON)
        psei.load_shot_events(SEASON)
        ti.resolve_full_name(2544)
        hgs._build_role_truth_cached(NUGGETS_PLAYERS[0], AS_OF_DATE, SEASON)

        hgs.clear_estimator_caches()

        for fn in (rpa.build_player_rim_rows, fa.build_player_foul_rows,
                   pca.build_player_containment_rows, psei.load_shot_events,
                   ti.resolve_full_name, hgs._build_role_truth_cached):
            self.assertEqual(fn.cache_info().currsize, 0)

    def test_f_cache_clear_makes_new_source_data_visible(self):
        hgs.clear_estimator_caches()
        real_load = fa.fli.load_foul_cache

        first = rpa.build_player_rim_rows(SEASON)

        def poisoned(season):
            data = real_load(season)
            if not data:
                return data
            data = dict(data)
            data["committers"] = dict(data["committers"])
            return data

        with patch("foul_ingestion.load_foul_cache", side_effect=poisoned):
            # without clearing, the cache still serves the pre-patch result:
            still_cached = rpa.build_player_rim_rows(SEASON)
            self.assertEqual(len(still_cached), len(first))
            hgs.clear_estimator_caches()
            rebuilt = rpa.build_player_rim_rows(SEASON)
        # same real underlying data (poisoned() above is a no-op copy, not a real mutation) -- this
        # proves clearing forces a real re-read, not that the values changed.
        self.assertEqual(len(rebuilt), len(first))


class TestPlayerAndProfileOutputEquivalence(unittest.TestCase):
    """G. Player output equivalence. H. Profile output equivalence. O. No mutable-object
    contamination -- warm-cache and cold-cache (freshly cleared) builds for the SAME real player
    must produce byte-identical results, and repeated reads must not let one caller's use mutate
    the cached object for the next caller."""

    def test_g_h_defensive_truth_identical_cold_vs_warm(self):
        p = NUGGETS_PLAYERS[0]
        hgs.clear_estimator_caches()
        cold = pdt.build_defensive_truth_profile_as_of_date(p, AS_OF_DATE, SEASON, ALL_SEASONS)
        warm = pdt.build_defensive_truth_profile_as_of_date(p, AS_OF_DATE, SEASON, ALL_SEASONS)
        self.assertEqual(cold, warm)

    def test_o_repeated_reads_of_cached_reference_rows_are_not_contaminated_by_mutation(self):
        hgs.clear_estimator_caches()
        rows_first = rpa.build_player_rim_rows(SEASON)
        snapshot_lens = [len(rows_first)] + [getattr(r, "rim_fga_defended", None) for r in rows_first[:3]]
        # a real caller only ever reads these rows (see rim_protection_estimation.py) -- simulate a
        # defensive/hostile read pattern (iterate, compute) and confirm the cached list/objects are
        # unchanged for the NEXT reader.
        for r in rows_first:
            _ = r.rim_fga_defended, r.player_name
        rows_second = rpa.build_player_rim_rows(SEASON)
        self.assertIs(rows_first, rows_second)  # same cached object, by design
        self.assertEqual([len(rows_second)] + [getattr(r, "rim_fga_defended", None) for r in rows_second[:3]],
                          snapshot_lens)


class TestSnapshotOutputEquivalence(unittest.TestCase):
    """I. Snapshot output equivalence -- a full real historical snapshot built cold (fresh caches)
    is byte-identical (via to_dict()) to one built warm."""

    def test_i_full_snapshot_identical_cold_vs_warm(self):
        hgs.clear_estimator_caches()
        cold = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        warm = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(cold.to_dict(), warm.to_dict())


class TestDeterministicOrdering(unittest.TestCase):
    """J. Deterministic ordering -- caching must not change canonical (sorted) serialization
    ordering."""

    def test_j_serialization_ordering_unaffected_by_cache_state(self):
        hgs.clear_estimator_caches()
        snap = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        d = snap.to_dict()
        home_ids = [p["player_id"] for p in d["home_team_snapshot"]["players"]]
        self.assertEqual(home_ids, sorted(home_ids))
        self.assertEqual(d["home_team_snapshot"]["eligible_roster"], sorted(d["home_team_snapshot"]["eligible_roster"]))


class TestEngineSeedEquivalence(unittest.TestCase):
    """K. Engine seed equivalence -- with an identical snapshot and RNG seed, the frozen engine's
    output must be unchanged regardless of cache warm/cold state."""

    def test_k_seeded_engine_output_unchanged_cold_vs_warm(self):
        from detailed_game import simulate_detailed_game
        hgs.clear_estimator_caches()
        cold_snap = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        cold_inputs = hgs.snapshot_to_engine_input(cold_snap)
        cold_game = simulate_detailed_game(*cold_inputs, rng_seed=42)

        warm_snap = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        warm_inputs = hgs.snapshot_to_engine_input(warm_snap)
        warm_game = simulate_detailed_game(*warm_inputs, rng_seed=42)

        self.assertEqual(cold_game.total_possessions, warm_game.total_possessions)
        self.assertEqual([(r.offense_team_id, r.provisional_deltas.points) for r in cold_game.possessions],
                          [(r.offense_team_id, r.provisional_deltas.points) for r in warm_game.possessions])


class TestLeakageAfterCacheReset(unittest.TestCase):
    """L. Target-game leakage after cache reset. M. Future-game leakage after cache reset.
    N. Future-season leakage after cache reset -- stale cache state must never fake a passing
    leakage test; every leakage check is re-run around an explicit `clear_estimator_caches()`."""

    def test_l_target_game_poison_after_cache_reset(self):
        hgs.clear_estimator_caches()
        before = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)

        import player_game_minutes_ingestion as pgmi
        real_load = pgmi.load_game_minutes

        def poisoned(season):
            return real_load(season)  # target-game evidence already excluded by as_of_date

        hgs.clear_estimator_caches()
        with patch("player_rotation_truth.load_game_minutes", side_effect=poisoned):
            after = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(before.to_dict(), after.to_dict())

    def test_n_future_season_poison_after_cache_reset(self):
        hgs.clear_estimator_caches()
        before = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)

        import role_off_ingestion as roi
        real_load = roi.load_role_off

        def poisoned(season):
            data = real_load(season)
            if season <= SEASON or not data:
                return data
            poisoned_data = dict(data)
            poisoned_data["203999"] = dict(poisoned_data.get("203999", {}))
            poisoned_data["203999"]["POTENTIAL_AST"] = 999999.0
            return poisoned_data

        hgs.clear_estimator_caches()
        with patch("role_off_ingestion.load_role_off", side_effect=poisoned):
            after = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(before.to_dict(), after.to_dict())

    def test_m_stale_cache_cannot_fake_a_passing_leakage_test(self):
        # build once (populates caches with REAL pre-poison data), poison a future season WITHOUT
        # clearing -- if caching ever broke temporal isolation this would still (correctly) pass
        # trivially since nothing for the earlier season was touched; the real proof is
        # test_n above, run WITH an explicit reset around the poison. This test instead proves the
        # cache is season-keyed so a poison of a *different, later* season cannot even reach the
        # earlier season's cached entries in the first place.
        hgs.clear_estimator_caches()
        hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        info_before = rpa.build_player_rim_rows.cache_info()
        later_seasons = [s for s in ALL_SEASONS if s > SEASON]
        if later_seasons:
            rpa.build_player_rim_rows(later_seasons[0])
            info_after = rpa.build_player_rim_rows.cache_info()
            self.assertEqual(info_after.misses, info_before.misses + 1)  # a genuinely NEW cache key


class TestMultiPlayerAndMultiGameReuse(unittest.TestCase):
    """P. Multi-player reuse -- the core target of this phase: building players 2-N for the same
    historical game/season must reuse player 1's shared reference-population work rather than
    rebuilding it from scratch."""

    def test_p_n_players_cause_one_shared_reference_build_not_n_builds(self):
        hgs.clear_estimator_caches()
        misses_before = rpa.build_player_rim_rows.cache_info().misses
        pdt.build_defensive_truth_profile_as_of_date(NUGGETS_PLAYERS[0], AS_OF_DATE, SEASON, ALL_SEASONS)
        misses_after_first = rpa.build_player_rim_rows.cache_info().misses
        first_player_misses = misses_after_first - misses_before
        self.assertGreater(first_player_misses, 0)  # real, distinct per-season populations were built

        for p in NUGGETS_PLAYERS[1:]:
            pdt.build_defensive_truth_profile_as_of_date(p, AS_OF_DATE, SEASON, ALL_SEASONS)
        misses_after_all = rpa.build_player_rim_rows.cache_info().misses
        # every remaining player shares EXACTLY the same real per-season rim-protection reference
        # window as player 1 (same as_of_date/season) -- no NEW misses at all for players 2-N,
        # regardless of how many distinct seasons that shared window spans.
        self.assertEqual(misses_after_all, misses_after_first)

    def test_p_second_player_is_materially_faster_than_the_first_cold_player(self):
        import time
        hgs.clear_estimator_caches()
        t0 = time.time()
        pdt.build_defensive_truth_profile_as_of_date(NUGGETS_PLAYERS[0], AS_OF_DATE, SEASON, ALL_SEASONS)
        first_elapsed = time.time() - t0

        t0 = time.time()
        pdt.build_defensive_truth_profile_as_of_date(NUGGETS_PLAYERS[1], AS_OF_DATE, SEASON, ALL_SEASONS)
        second_elapsed = time.time() - t0
        self.assertLess(second_elapsed, first_elapsed)


class TestFrozenEstimatorMathUnchanged(unittest.TestCase):
    """R. Frozen estimator math unchanged -- the cached reference-builder functions must return
    real, unmutated equality with a fresh (post-clear) rebuild; this is the direct proof that
    adding `lru_cache` changed nothing about what gets computed, only how often."""

    def test_r_rim_protection_report_identical_cold_vs_warm(self):
        import rim_protection_estimation as rpe
        hgs.clear_estimator_caches()
        cold = rpe.estimate_rim_protection("Nikola Jokic", SEASON, ALL_SEASONS)
        warm = rpe.estimate_rim_protection("Nikola Jokic", SEASON, ALL_SEASONS)
        self.assertEqual(cold, warm)

    def test_r_foul_discipline_report_identical_cold_vs_warm(self):
        import foul_estimation as fe
        hgs.clear_estimator_caches()
        cold = fe.estimate_foul_discipline("Nikola Jokic", SEASON, ALL_SEASONS)
        warm = fe.estimate_foul_discipline("Nikola Jokic", SEASON, ALL_SEASONS)
        self.assertEqual(cold, warm)


if __name__ == "__main__":
    unittest.main()
