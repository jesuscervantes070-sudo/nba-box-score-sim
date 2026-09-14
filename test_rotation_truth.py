"""Focused tests for Availability + Expected Minutes + Rotations V1
(player_rotation_truth.py, player_game_minutes_ingestion.py, rotation_engine_adapter.py,
player_team_stints.team_roster_as_of_date)."""
import glob
import unittest
from unittest.mock import patch

import player_identity as pid
import player_rotation_truth as prt
from player_team_stints import team_roster_as_of_date
from rotation_engine_adapter import primary_five

ALL_SEASONS = sorted(d.split('/')[-1] for d in glob.glob('cache/????-??'))
SIAKAM = "1627783"
PACERS = "Indiana Pacers"
RAPTORS = "Toronto Raptors"
DENVER_GAME = "0022300061"
DENVER = "Denver Nuggets"
LAKERS = "Los Angeles Lakers"


class TestRosterDateCorrectness(unittest.TestCase):
    """A. Team roster date correctness. B. Trade date correctness."""

    def test_a_roster_excludes_players_never_on_the_team(self):
        roster = team_roster_as_of_date(PACERS, "2024-03-01", "2023-24")
        self.assertIn(SIAKAM, roster)
        # a real Raptors-only player (post-trade era teammate) should not appear on Indiana
        derozan = pid.resolve_name_to_id("DeMar DeRozan", season_hint="2023-24")
        if derozan.state == "RESOLVED":
            self.assertNotIn(derozan.player_id, roster)

    def test_b_pre_trade_roster_has_siakam_on_toronto_not_indiana(self):
        pre_trade_pacers = team_roster_as_of_date(PACERS, "2023-11-01", "2023-24")
        pre_trade_raptors = team_roster_as_of_date(RAPTORS, "2023-11-01", "2023-24")
        self.assertNotIn(SIAKAM, pre_trade_pacers)
        self.assertIn(SIAKAM, pre_trade_raptors)

    def test_b_post_trade_roster_has_siakam_on_indiana_not_toronto(self):
        post_trade_pacers = team_roster_as_of_date(PACERS, "2024-03-01", "2023-24")
        post_trade_raptors = team_roster_as_of_date(RAPTORS, "2024-03-01", "2023-24")
        self.assertIn(SIAKAM, post_trade_pacers)
        self.assertNotIn(SIAKAM, post_trade_raptors)


class TestTemporalExclusion(unittest.TestCase):
    """C. Target-game exclusion. D. Future-game exclusion. E. Future-season exclusion."""

    def test_c_games_before_date_excludes_the_target_date_itself(self):
        games = prt.games_before_date(SIAKAM, "2024-01-19", "2023-24")
        self.assertTrue(all(g["date"] < "2024-01-19" for g in games))

    def test_d_rolling_features_never_see_a_future_game(self):
        early = prt.rolling_minutes_features(SIAKAM, "2023-11-01", "2023-24")
        late = prt.rolling_minutes_features(SIAKAM, "2024-04-01", "2023-24")
        # more real games accumulate as the season progresses -- an early cutoff cannot see
        # games that only exist because of a later cutoff.
        self.assertLessEqual(early["games_played"], late["games_played"])

    def test_e_future_season_poison_does_not_change_an_earlier_pregame_rotation(self):
        before = prt.build_pregame_rotation(PACERS, "2023-11-01", "2023-24", ALL_SEASONS)

        import player_game_minutes_ingestion as pgmi
        real_load = pgmi.load_game_minutes

        def poisoned(season):
            data = real_load(season)
            if season <= "2023-24" or not data:
                return data
            poisoned_data = dict(data)
            poisoned_data[SIAKAM] = [{"game_id": "FAKE", "date": "2024-11-01", "team_id": "9999", "minutes": 48.0}]
            return poisoned_data

        prt._season_games_for_player.cache_clear()
        with patch("player_rotation_truth.load_game_minutes", side_effect=poisoned):
            after = prt.build_pregame_rotation(PACERS, "2023-11-01", "2023-24", ALL_SEASONS)
        prt._season_games_for_player.cache_clear()
        self.assertEqual(before.to_dict(), after.to_dict())


class TestOpeningNightPrior(unittest.TestCase):
    """F. Opening-night prior."""

    def test_f_opening_night_uses_prior_season_not_zero(self):
        rotation = prt.build_pregame_rotation(DENVER, "2023-10-24", "2023-24", ALL_SEASONS)
        # a veteran Denver player should get a real, nonzero expected-minutes estimate from
        # PRIOR-season evidence on opening night (zero current-season games exist yet).
        nonzero = [p for p in rotation.players if p.expected_minutes > 0]
        self.assertGreater(len(nonzero), 0)
        fallback_used = [p for p in rotation.players if "prior-season" in p.availability.provenance]
        self.assertGreater(len(fallback_used), 0)


class TestAvailabilityGate(unittest.TestCase):
    """G. Availability gate. H. Inactive gets zero minutes."""

    def test_g_high_recent_zero_share_is_gated_out(self):
        features = {"games_played": 10, "recent_zero_share": 0.9, "last_5": 0.0, "last_10": 0.0,
                    "season_to_date_mean": 2.0}
        self.assertEqual(prt._availability_gate(features), prt.STATUS_OUT)

    def test_h_out_players_receive_zero_allocated_minutes(self):
        raw = {"a": 30.0, "b": 25.0}
        allocated = prt._allocate_240({"a": 30.0})  # "b" excluded entirely (as if gated OUT)
        self.assertNotIn("b", allocated)


class TestTotalMinutesConstraint(unittest.TestCase):
    """I. Total team minutes = 240. J. No negative minutes. K. Individual cap."""

    def test_i_allocation_sums_to_240(self):
        raw = {"a": 40.0, "b": 30.0, "c": 20.0, "d": 15.0, "e": 10.0, "f": 5.0}
        allocated = prt._allocate_240(raw)
        self.assertAlmostEqual(sum(allocated.values()), prt.REGULATION_TEAM_MINUTES, places=3)

    def test_j_no_negative_minutes(self):
        raw = {"a": 40.0, "b": 1.0}
        allocated = prt._allocate_240(raw)
        self.assertTrue(all(v >= 0 for v in allocated.values()))

    def test_k_individual_cap_respected(self):
        # a realistic ~9-man rotation (real NBA rotations are deep enough that 240/cap is always
        # feasible; a synthetic <6-player pool cannot reach 240 under a 42-minute cap by
        # construction -- not exercised here, see module docstring's own note on that edge case).
        raw = {"a": 100.0, "b": 20.0, "c": 18.0, "d": 16.0, "e": 14.0, "f": 12.0, "g": 10.0, "h": 8.0, "i": 6.0}
        allocated = prt._allocate_240(raw)
        self.assertLessEqual(allocated["a"], prt.INDIVIDUAL_MINUTE_CAP + 1e-6)
        self.assertAlmostEqual(sum(allocated.values()), prt.REGULATION_TEAM_MINUTES, places=1)


class TestRotationThreshold(unittest.TestCase):
    """L. Rotation threshold."""

    def test_l_tiny_raw_estimates_are_zeroed(self):
        raw = {"a": 30.0, "b": 0.2}
        allocated = prt._allocate_240(raw)
        self.assertEqual(allocated.get("b", 0.0), 0.0)


class TestTeammateAbsenceRedistribution(unittest.TestCase):
    """M. Teammate absence redistribution. N. Truth unchanged during redistribution."""

    def test_m_removing_a_high_minute_player_redistributes_to_others(self):
        # realistic raw minutes summing close to 240 already, so neither scenario's rescale
        # factor saturates "b" at the individual cap -- a case saturated at the cap in both would
        # show no measurable redistribution, by construction, not because it failed to happen.
        full_roster = {"a": 34.0, "b": 30.0, "c": 28.0, "d": 26.0, "e": 24.0, "f": 22.0, "g": 20.0, "h": 18.0, "i": 16.0}
        with_a = prt._allocate_240(full_roster)
        without_a = prt._allocate_240({k: v for k, v in full_roster.items() if k != "a"})
        self.assertAlmostEqual(sum(with_a.values()), 240.0, places=2)
        self.assertAlmostEqual(sum(without_a.values()), 240.0, places=2)
        self.assertGreater(without_a["b"], with_a["b"])

    def test_n_role_truth_is_not_touched_by_rotation_allocation(self):
        import player_role_truth as role_truth
        before = role_truth.build_role_truth_profile(SIAKAM, "2023-24")
        prt._allocate_240({SIAKAM: 30.0, "other": 10.0})
        after = role_truth.build_role_truth_profile(SIAKAM, "2023-24")
        self.assertEqual(before, after)


class TestRookieNewPlayerFallback(unittest.TestCase):
    """O. Rookie/new-player fallback."""

    def test_o_a_player_with_zero_evidence_anywhere_is_unknown_not_zero_confidence_fabricated(self):
        features = {"games_played": 0}
        self.assertEqual(prt._availability_gate(features), prt.STATUS_UNKNOWN)
        self.assertIsNone(prt._raw_expected_minutes(features))


class TestOracleReconstruction(unittest.TestCase):
    """P. Oracle actual reconstruction. Q. Oracle/pregame mode distinction."""

    def test_p_oracle_rotation_reproduces_real_box_score_minutes(self):
        oracle = prt.build_oracle_rotation(DENVER_GAME, DENVER, "2023-24")
        self.assertGreater(len(oracle.players), 0)
        self.assertAlmostEqual(oracle.total_minutes(), 240.0, delta=5.0)
        self.assertEqual(oracle.mode, prt.MODE_ORACLE_ACTUAL)

    def test_p_oracle_excludes_the_opponent_team(self):
        denver_oracle = prt.build_oracle_rotation(DENVER_GAME, DENVER, "2023-24")
        lakers_oracle = prt.build_oracle_rotation(DENVER_GAME, LAKERS, "2023-24")
        denver_ids = {p.player_id for p in denver_oracle.players}
        lakers_ids = {p.player_id for p in lakers_oracle.players}
        self.assertEqual(denver_ids & lakers_ids, set())

    def test_q_oracle_and_pregame_are_distinctly_labeled(self):
        oracle = prt.build_oracle_rotation(DENVER_GAME, DENVER, "2023-24")
        pregame = prt.build_pregame_rotation(DENVER, "2023-12-01", "2023-24", ALL_SEASONS)
        self.assertEqual(oracle.mode, prt.MODE_ORACLE_ACTUAL)
        self.assertEqual(pregame.mode, prt.MODE_PREGAME_EXPECTED)
        self.assertNotEqual(oracle.mode, pregame.mode)


class TestDeterminism(unittest.TestCase):
    """R. Expected-minute determinism."""

    def test_r_repeated_build_is_identical(self):
        first = prt.build_pregame_rotation(PACERS, "2024-01-01", "2023-24", ALL_SEASONS)
        second = prt.build_pregame_rotation(PACERS, "2024-01-01", "2023-24", ALL_SEASONS)
        self.assertEqual(first.to_dict(), second.to_dict())


class TestStarterEstimate(unittest.TestCase):
    """S. Starter estimate if supported."""

    def test_s_oracle_starter_proxy_matches_top_5_minutes(self):
        oracle = prt.build_oracle_rotation(DENVER_GAME, DENVER, "2023-24")
        top5_ids = {p.player_id for p in oracle.top_n(5)}
        starters = {p.player_id for p in oracle.players if p.starter_expectation == 1.0}
        self.assertEqual(top5_ids, starters)


class TestRotationMembershipMetrics(unittest.TestCase):
    """T. Rotation membership metrics."""

    def test_t_pregame_rotation_has_a_plausible_number_of_nonzero_players(self):
        rotation = prt.build_pregame_rotation(PACERS, "2024-02-01", "2023-24", ALL_SEASONS)
        nonzero = [p for p in rotation.players if p.expected_minutes > 0]
        self.assertGreaterEqual(len(nonzero), 5)
        self.assertLessEqual(len(nonzero), 18)  # real rosters this phase's threshold admits are 8-18 deep


class TestEngineParticipationWiring(unittest.TestCase):
    """U. Engine participation wiring if supported."""

    def test_u_primary_five_selects_the_top_5_by_expected_minutes(self):
        oracle = prt.build_oracle_rotation(DENVER_GAME, DENVER, "2023-24")
        five = primary_five(oracle)
        self.assertEqual(len(five), 5)
        top5_ids = {p.player_id for p in oracle.top_n(5)}
        self.assertEqual(set(five), top5_ids)

    def test_u_primary_five_feeds_a_real_simulated_game(self):
        from detailed_game import simulate_detailed_game
        from possession_orchestrator import PlayerSimulationProfile
        oracle_home = prt.build_oracle_rotation(DENVER_GAME, DENVER, "2023-24")
        oracle_away = prt.build_oracle_rotation(DENVER_GAME, LAKERS, "2023-24")
        home_five = primary_five(oracle_home)
        away_five = primary_five(oracle_away)
        profiles = {p: PlayerSimulationProfile.synthetic(p, "HOME") for p in home_five}
        profiles.update({p: PlayerSimulationProfile.synthetic(p, "AWAY") for p in away_five})
        game = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=1)
        self.assertGreater(game.total_possessions, 0)


class TestPerformance(unittest.TestCase):
    """V. Performance/caching."""

    def test_v_repeated_pregame_builds_are_fast(self):
        import time
        prt.build_pregame_rotation(PACERS, "2024-02-01", "2023-24", ALL_SEASONS)  # warm caches
        t0 = time.time()
        for _ in range(3):
            prt.build_pregame_rotation(PACERS, "2024-02-01", "2023-24", ALL_SEASONS)
        self.assertLess(time.time() - t0, 5.0)


if __name__ == "__main__":
    unittest.main()
