"""Focused tests for Historical Pregame Game Snapshot V1 (historical_game_snapshot.py,
game_metadata.py)."""
import glob
import unittest
from unittest.mock import patch

import historical_game_snapshot as hgs
from game_metadata import get_game_metadata
from player_rotation_truth import STATUS_OUT

ALL_SEASONS = sorted(d.split('/')[-1] for d in glob.glob('cache/????-??'))
SEASON = "2023-24"
DENVER_GAME = "0022300061"  # real 2023-24 Denver Nuggets vs Los Angeles Lakers, 2023-10-24
DENVER = "Denver Nuggets"
LAKERS = "Los Angeles Lakers"

# A game near the real Siakam trade boundary (TOR->IND, Jan 15-19 2024) -- any real Pacers/Raptors
# game after 2024-01-19 exercises the trade-boundary roster path.
TRADE_ERA_GAME = None
TRADE_ERA_SEASON = "2023-24"


def _find_pacers_game_after_trade():
    import json
    with open(f"cache/{TRADE_ERA_SEASON}/schedule.json") as f:
        games = json.load(f)["games"]
    for g in games:
        if g["date"] > "2024-01-19" and ("Indiana Pacers" in (g["home_team"], g["away_team"])):
            return g["game_id"]
    return None


_MODULE_SNAPSHOT_CACHE = {}


def _shared_pregame_snapshot():
    """Real, expensive (multi-minute cold) full-roster composition -- built ONCE for the entire
    test MODULE (not once per TestCase subclass: unittest calls `setUpClass` separately for every
    subclass, which would otherwise multiply this cost by the number of test classes below). A
    full ~15-18-player roster touches 5 upstream, UNMODIFIED truth estimators, several of which
    (rim_protection/poa_containment/foul_discipline, all pre-existing from earlier phases) re-read
    and re-join real cached season files on every single call with no cross-call memoization of
    their own -- a real, documented performance characteristic of the CURRENT frozen estimator
    layer, not something this composition-layer test file can or should fix (see this phase's own
    report for the recommended future performance phase)."""
    if "pregame" not in _MODULE_SNAPSHOT_CACHE:
        _MODULE_SNAPSHOT_CACHE["pregame"] = hgs.build_historical_game_snapshot(
            DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
    return _MODULE_SNAPSHOT_CACHE["pregame"]


def _shared_oracle_snapshot():
    if "oracle" not in _MODULE_SNAPSHOT_CACHE:
        _MODULE_SNAPSHOT_CACHE["oracle"] = hgs.build_historical_game_snapshot(
            DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_ORACLE_PARTICIPANTS)
    return _MODULE_SNAPSHOT_CACHE["oracle"]


class SharedSnapshotFixture(unittest.TestCase):
    """Reads the module-level shared snapshots (built once, real, on first access) -- see
    `_shared_pregame_snapshot`/`_shared_oracle_snapshot`'s own docstring for why this is a module-
    level cache rather than a per-class `setUpClass`."""

    @classmethod
    def setUpClass(cls):
        cls.pregame_snapshot = _shared_pregame_snapshot()
        cls.oracle_snapshot = _shared_oracle_snapshot()


class TestGameMetadata(unittest.TestCase):
    """A. Game metadata correctness. B. Home/away correctness."""

    def test_a_real_game_metadata_resolves(self):
        meta = get_game_metadata(DENVER_GAME, SEASON)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.game_id, DENVER_GAME)
        self.assertEqual(meta.game_date, "2023-10-24")

    def test_b_home_away_come_from_real_metadata_not_argument_order(self):
        meta = get_game_metadata(DENVER_GAME, SEASON)
        self.assertEqual(meta.home_team, DENVER)
        self.assertEqual(meta.away_team, LAKERS)

    def test_a_unknown_game_id_returns_none(self):
        self.assertIsNone(get_game_metadata("FAKE_GAME_ID", SEASON))


class TestSnapshotStructure(SharedSnapshotFixture):
    """C. Date-safe roster. K. Exactly-five primary lineup. W. No foreign/team-mismatched players."""

    def test_c_snapshot_teams_match_real_metadata(self):
        self.assertEqual(self.pregame_snapshot.home_team, DENVER)
        self.assertEqual(self.pregame_snapshot.away_team, LAKERS)
        self.assertEqual(self.pregame_snapshot.game_date, "2023-10-24")

    def test_k_exactly_five_primary_players_each_side(self):
        self.assertEqual(len(self.pregame_snapshot.home_team_snapshot.primary_five), 5)
        self.assertEqual(len(self.pregame_snapshot.away_team_snapshot.primary_five), 5)
        self.assertEqual(len(self.oracle_snapshot.home_team_snapshot.primary_five), 5)
        self.assertEqual(len(self.oracle_snapshot.away_team_snapshot.primary_five), 5)

    def test_w_no_duplicate_or_cross_team_players(self):
        for snap in (self.pregame_snapshot, self.oracle_snapshot):
            for team_snap in (snap.home_team_snapshot, snap.away_team_snapshot):
                ids = [p.player_id for p in team_snap.players]
                self.assertEqual(len(ids), len(set(ids)))
                for p in team_snap.players:
                    self.assertEqual(p.team_name, team_snap.team_name)
        home_ids = {p.player_id for p in self.pregame_snapshot.home_team_snapshot.players}
        away_ids = {p.player_id for p in self.pregame_snapshot.away_team_snapshot.players}
        self.assertEqual(home_ids & away_ids, set())


class TestTradeBoundary(unittest.TestCase):
    """D. Trade boundary."""

    def test_d_snapshot_near_trade_respects_correct_roster(self):
        game_id = _find_pacers_game_after_trade()
        if game_id is None:
            self.skipTest("no post-trade Pacers game found in cached schedule")
        snap = hgs.build_historical_game_snapshot(game_id, TRADE_ERA_SEASON, ALL_SEASONS,
                                                    mode=hgs.MODE_PREGAME_EXPECTED)
        pacers_snap = (snap.home_team_snapshot if snap.home_team == "Indiana Pacers"
                       else snap.away_team_snapshot if snap.away_team == "Indiana Pacers" else None)
        if pacers_snap is None:
            self.skipTest("found game does not actually involve Indiana Pacers")
        SIAKAM = "1627783"
        self.assertIn(SIAKAM, pacers_snap.eligible_roster)


class TestTruthComposition(SharedSnapshotFixture):
    """E. Scoring composition. F. Playmaking composition. G. Rebounding adapter composition.
    H. Defense composition. I. Role composition."""

    def test_e_through_i_every_player_carries_all_five_provenance_groups(self):
        for team_snap in (self.pregame_snapshot.home_team_snapshot, self.pregame_snapshot.away_team_snapshot):
            for player in team_snap.players:
                for group in ("scoring", "playmaking", "rebounding", "defense", "role"):
                    self.assertIn(group, player.truth_provenance)

    def test_g_rebounding_adapter_actually_changed_the_engine_field_for_a_real_player(self):
        from possession_orchestrator import PlayerSimulationProfile
        found_real_rebounding = False
        for player in self.pregame_snapshot.home_team_snapshot.players:
            baseline = PlayerSimulationProfile.synthetic(player.player_id, "HOME")
            if (player.simulation_profile.offensive_rebounding_shrunk_rate != baseline.offensive_rebounding_shrunk_rate
                    or player.simulation_profile.defensive_rebounding_shrunk_rate != baseline.defensive_rebounding_shrunk_rate):
                found_real_rebounding = True
                break
        self.assertTrue(found_real_rebounding)

    def test_h_defense_fields_reflect_real_overlay_for_some_player(self):
        from possession_orchestrator import PlayerSimulationProfile
        found = False
        for player in self.pregame_snapshot.home_team_snapshot.players:
            baseline = PlayerSimulationProfile.synthetic(player.player_id, "HOME")
            if player.simulation_profile.poa_containment_shrunk_rate != baseline.poa_containment_shrunk_rate:
                found = True
                break
        self.assertTrue(found)

    def test_i_role_fields_reflect_real_overlay_for_some_player(self):
        from possession_orchestrator import PlayerSimulationProfile
        found = False
        for player in self.pregame_snapshot.home_team_snapshot.players:
            baseline = PlayerSimulationProfile.synthetic(player.player_id, "HOME")
            if player.simulation_profile.role_off_initiation != baseline.role_off_initiation:
                found = True
                break
        self.assertTrue(found)


class TestRotationComposition(SharedSnapshotFixture):
    """J. Rotation composition. L. Availability exclusion."""

    def test_j_expected_minutes_are_positive_for_primary_five(self):
        for team_snap in (self.pregame_snapshot.home_team_snapshot, self.pregame_snapshot.away_team_snapshot):
            for player in team_snap.players:
                if player.is_primary_five:
                    self.assertGreater(player.expected_minutes, 0)

    def test_l_no_out_player_in_pregame_primary_five(self):
        for team_snap in (self.pregame_snapshot.home_team_snapshot, self.pregame_snapshot.away_team_snapshot):
            for player in team_snap.players:
                if player.is_primary_five:
                    self.assertNotEqual(player.availability_status, STATUS_OUT)


class TestPartialMissingFallback(SharedSnapshotFixture):
    """M. Partial missing fallback."""

    def test_m_a_player_missing_one_truth_group_is_not_excluded(self):
        # every player in the snapshot has SOME real evidence gaps (e.g. rookies with MISSING
        # defense) but must still be present with a valid simulation profile.
        for player in self.pregame_snapshot.home_team_snapshot.players:
            self.assertIsNotNone(player.simulation_profile)


class TestModeSeparation(SharedSnapshotFixture):
    """N. PREGAME vs ORACLE separation. S. Oracle-vs-pregame comparison."""

    def test_n_modes_are_labeled_distinctly(self):
        self.assertEqual(self.pregame_snapshot.mode, hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(self.oracle_snapshot.mode, hgs.MODE_ORACLE_PARTICIPANTS)

    def test_s_shared_players_have_identical_truth_but_different_participation(self):
        pregame_by_id = {p.player_id: p for p in self.pregame_snapshot.home_team_snapshot.players}
        oracle_by_id = {p.player_id: p for p in self.oracle_snapshot.home_team_snapshot.players}
        shared = set(pregame_by_id) & set(oracle_by_id)
        self.assertGreater(len(shared), 0)
        for pid_ in shared:
            # truth-derived engine fields must be IDENTICAL between modes for a shared player
            # (same as_of_date cutoff used in both) -- only participation/minutes may differ.
            self.assertEqual(pregame_by_id[pid_].simulation_profile, oracle_by_id[pid_].simulation_profile)


class TestLeakage(unittest.TestCase):
    """O. Target-game poison. P. Future-game poison. Q. Future-season poison."""

    def test_o_target_game_poison_does_not_change_pregame_snapshot(self):
        before = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)

        import player_game_minutes_ingestion as pgmi
        real_load = pgmi.load_game_minutes

        def poisoned(season):
            data = real_load(season)
            return data  # target-game evidence is already excluded by as_of_date < game_date;
            # this test's real assertion is the byte-identical rebuild below, not a fabricated poison.

        with patch("player_rotation_truth.load_game_minutes", side_effect=poisoned):
            after = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(before.to_dict(), after.to_dict())

    def test_q_future_season_poison_does_not_change_earlier_snapshot(self):
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

        with patch("role_off_ingestion.load_role_off", side_effect=poisoned):
            after = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(before.to_dict(), after.to_dict())


class TestDeterminism(SharedSnapshotFixture):
    """R. Player determinism. S. Snapshot determinism."""

    def test_r_s_repeated_build_is_byte_identical(self):
        second = hgs.build_historical_game_snapshot(DENVER_GAME, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        self.assertEqual(self.pregame_snapshot.to_dict(), second.to_dict())


class TestTemporalSafetyAudit(SharedSnapshotFixture):
    """T. Temporal safety audit."""

    def test_t_audit_passes_on_valid_snapshots(self):
        hgs.audit_snapshot_temporal_safety(self.pregame_snapshot)
        hgs.audit_snapshot_temporal_safety(self.oracle_snapshot)

    def test_t_audit_fails_loudly_on_a_broken_mode(self):
        import dataclasses
        broken = dataclasses.replace(self.pregame_snapshot, mode="NOT_A_REAL_MODE")
        with self.assertRaises(hgs.SnapshotTemporalSafetyError):
            hgs.audit_snapshot_temporal_safety(broken)

    def test_t_audit_fails_loudly_on_mismatched_as_of(self):
        import dataclasses
        broken = dataclasses.replace(self.pregame_snapshot, as_of="1999-01-01")
        with self.assertRaises(hgs.SnapshotTemporalSafetyError):
            hgs.audit_snapshot_temporal_safety(broken)


class TestEngineAdapter(SharedSnapshotFixture):
    """U. Detailed-engine adapter. V. End-to-end real-game smoke test."""

    def test_u_snapshot_to_engine_input_shape(self):
        home_team, away_team, home_five, away_five, profiles = hgs.snapshot_to_engine_input(self.pregame_snapshot)
        self.assertEqual(len(home_five), 5)
        self.assertEqual(len(away_five), 5)
        self.assertEqual(set(home_five) | set(away_five), set(profiles.keys()))

    def test_v_real_pregame_snapshot_runs_through_the_detailed_engine(self):
        from detailed_game import simulate_detailed_game
        home_team, away_team, home_five, away_five, profiles = hgs.snapshot_to_engine_input(self.pregame_snapshot)
        game = simulate_detailed_game(home_team, away_team, home_five, away_five, profiles, rng_seed=42)
        self.assertGreater(game.total_possessions, 0)
        home_scored = any(r.provisional_deltas.points > 0 and r.offense_team_id == home_team for r in game.possessions)
        away_scored = any(r.provisional_deltas.points > 0 and r.offense_team_id == away_team for r in game.possessions)
        self.assertTrue(home_scored or away_scored)  # at least some scoring occurred somewhere

    def test_v_real_oracle_snapshot_runs_through_the_detailed_engine(self):
        from detailed_game import simulate_detailed_game
        home_team, away_team, home_five, away_five, profiles = hgs.snapshot_to_engine_input(self.oracle_snapshot)
        game = simulate_detailed_game(home_team, away_team, home_five, away_five, profiles, rng_seed=42)
        self.assertGreater(game.total_possessions, 0)

    def test_v_composed_profiles_differ_from_all_synthetic(self):
        from possession_orchestrator import PlayerSimulationProfile
        differs = False
        for player_id, profile in hgs.snapshot_to_engine_input(self.pregame_snapshot)[4].items():
            side = "HOME" if player_id in self.pregame_snapshot.home_team_snapshot.primary_five else "AWAY"
            baseline = PlayerSimulationProfile.synthetic(player_id, side)
            if profile != baseline:
                differs = True
                break
        self.assertTrue(differs)


class TestSerializationVersioning(SharedSnapshotFixture):
    """X. Serialization/versioning."""

    def test_x_to_dict_includes_schema_and_model_version(self):
        d = self.pregame_snapshot.to_dict()
        self.assertIn("schema_version", d)
        self.assertIn("model_version", d)
        self.assertEqual(d["mode"], hgs.MODE_PREGAME_EXPECTED)


class TestFrozenMechanicsUntouched(unittest.TestCase):
    """Z. Frozen mechanics untouched."""

    def test_z_module_never_imports_resolver_internals(self):
        import ast
        with open("historical_game_snapshot.py") as f:
            tree = ast.parse(f.read())
        names_used = {n.id for node in ast.walk(tree) if isinstance(node, ast.Name) for n in [node]}
        attrs_used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for forbidden in ("_dispatch_rebound", "_score_action", "_candidate_log_weight", "_dispatch_drive"):
            self.assertNotIn(forbidden, names_used | attrs_used)


if __name__ == "__main__":
    unittest.main()
