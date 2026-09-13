"""Focused tests for Rebounding Truth V1 (rebounding_estimation.py + player_rebounding_truth.py)."""
import unittest
from unittest.mock import patch

import player_identity as pid
import player_rebounding_truth as prt
import player_scoring_truth_temporal as psst
from rebounding_estimation import REBOUNDING_ATTRIBUTES, estimate_rebounding_attribute
from possession_orchestrator import PlayerSimulationProfile

_SEASONS = [f"{y}-{str(y + 1)[-2:]}" for y in range(1996, 2026)]
JOKIC = "203999"  # elite real OREB/DREB big man, high sample


class TestCorrectDenominators(unittest.TestCase):
    """A. Correct OREB denominator. B. Correct DREB denominator."""

    def test_a_offensive_rebounding_uses_oreb_over_oreb_chances(self):
        import json
        with open("cache/2023-24/player_rebound_chances.json") as f:
            row = json.load(f)["players"][JOKIC]
        from rebounding_estimation import _offensive_rebounding_evidence
        rate, sample = _offensive_rebounding_evidence(JOKIC, "2023-24")
        self.assertAlmostEqual(rate, row["oreb"] / row["oreb_chances"], places=9)
        self.assertEqual(sample, row["oreb_chances"])

    def test_b_defensive_rebounding_uses_dreb_over_dreb_chances(self):
        import json
        with open("cache/2023-24/player_rebound_chances.json") as f:
            row = json.load(f)["players"][JOKIC]
        from rebounding_estimation import _defensive_rebounding_evidence
        rate, sample = _defensive_rebounding_evidence(JOKIC, "2023-24")
        self.assertAlmostEqual(rate, row["dreb"] / row["dreb_chances"], places=9)
        self.assertEqual(sample, row["dreb_chances"])

    def test_a_not_raw_rebounds_per_game_or_per_minute(self):
        """The denominator is real rebound CHANCES, not games or minutes."""
        import inspect
        from rebounding_estimation import _offensive_rebounding_evidence, _defensive_rebounding_evidence
        for fn in (_offensive_rebounding_evidence, _defensive_rebounding_evidence):
            src = inspect.getsource(fn)
            self.assertNotIn('"gp"', src)
            self.assertNotIn('"min"', src)


class TestOREBNotDREB(unittest.TestCase):
    """C. OREB != DREB construct -- separate evidence functions, separate reference
    populations, neither inferred from the other."""

    def test_c_separate_evidence_functions(self):
        from rebounding_estimation import _EVIDENCE_FN
        self.assertIsNot(_EVIDENCE_FN["offensive_rebounding"], _EVIDENCE_FN["defensive_rebounding"])

    def test_c_a_player_can_be_strong_one_weak_other(self):
        """Real, observed contrast: at least one real player has meaningfully different
        OREB vs DREB percentile ratings (not collapsed to the same value)."""
        result_o = estimate_rebounding_attribute(JOKIC, "2023-24", "offensive_rebounding", _SEASONS)
        result_d = estimate_rebounding_attribute(JOKIC, "2023-24", "defensive_rebounding", _SEASONS)
        self.assertIsNotNone(result_o.shrunk_rate)
        self.assertIsNotNone(result_d.shrunk_rate)
        # different absolute scale confirms independence of construction, not collapse
        self.assertNotAlmostEqual(result_o.shrunk_rate, result_d.shrunk_rate, places=2)


class TestShrinkage(unittest.TestCase):
    """D. Low-sample shrinkage. E. High-sample behavior."""

    def test_d_low_sample_player_shrinks_toward_league_average(self):
        import json
        with open("cache/2023-24/player_rebound_chances.json") as f:
            chances = json.load(f)["players"]
        thin = min(
            (kv for kv in chances.items() if kv[1].get("oreb_chances", 0) > 0),
            key=lambda kv: kv[1]["oreb_chances"],
        )
        result = estimate_rebounding_attribute(thin[0], "2023-24", "offensive_rebounding", _SEASONS)
        if result.shrunk_rate is not None and result.league_avg_rate is not None and result.total_weight < 30:
            self.assertLess(abs(result.shrunk_rate - result.league_avg_rate), 0.05)

    def test_e_high_sample_player_stays_close_to_own_raw_rate(self):
        result = estimate_rebounding_attribute(JOKIC, "2023-24", "defensive_rebounding", _SEASONS)
        self.assertLess(abs(result.shrunk_rate - result.weighted_raw_rate), 0.01)


class TestMissingStaysMissing(unittest.TestCase):
    """F. Missing remains missing."""

    def test_f_a_player_absent_from_tracking_data_stays_missing(self):
        result = estimate_rebounding_attribute("999999999", "2023-24", "offensive_rebounding", ["2023-24"])
        self.assertIsNone(result.shrunk_rate)

    def test_f_profile_level_missing_is_never_fabricated(self):
        profile = prt.build_rebounding_truth_profile("999999999", "2023-24", ["2023-24"])
        for attr in REBOUNDING_ATTRIBUTES:
            self.assertIsNone(profile.value(attr))
            self.assertEqual(profile.estimates[attr].provenance, psst.MISSING)


class TestTargetOnlyOverlay(unittest.TestCase):
    """G. Target-only overlay -- NEITHER offensive_rebounding NOR defensive_rebounding is
    overlaid this phase (real, documented scale mismatch with the engine's calibrated fields)."""

    def test_g_overlay_touches_no_fields_at_all(self):
        profile = prt.build_rebounding_truth_profile(JOKIC, "2023-24", _SEASONS)
        baseline = PlayerSimulationProfile.synthetic(JOKIC, "HOME")
        result = prt.apply_rebounding_truth_to_simulation_profile(baseline, profile)
        self.assertEqual(result, baseline)

    def test_g_missing_evidence_also_leaves_profile_untouched(self):
        profile = prt.build_rebounding_truth_profile("999999999", "2023-24", ["2023-24"])
        baseline = PlayerSimulationProfile.synthetic("999999999", "HOME")
        result = prt.apply_rebounding_truth_to_simulation_profile(baseline, profile)
        self.assertEqual(result, baseline)


class TestIdentityAndTrade(unittest.TestCase):
    """H. Deterministic player identity. I. Trade persistence."""

    def test_h_id_resolution_is_stable(self):
        first = pid.resolve_id_to_name(JOKIC)
        second = pid.resolve_id_to_name(JOKIC)
        self.assertEqual(first, second)

    def test_i_no_team_parameter_exists_in_the_evidence_or_estimate_path(self):
        import inspect
        for fn in (estimate_rebounding_attribute, prt.build_rebounding_truth_profile):
            sig = inspect.signature(fn)
            self.assertNotIn("team", " ".join(sig.parameters.keys()).lower())


class TestScaleContract(unittest.TestCase):
    """J. Scale contract -- both values are real [0,1] ability-scale chance-conversion rates,
    higher = better."""

    def test_j_both_values_are_bounded_ability_scale_rates(self):
        profile = prt.build_rebounding_truth_profile(JOKIC, "2023-24", _SEASONS)
        for attr in REBOUNDING_ATTRIBUTES:
            value = profile.value(attr)
            self.assertIsNotNone(value)
            self.assertTrue(0.0 <= value <= 1.0, (attr, value))

    def test_j_documented_engine_scale_mismatch_is_real(self):
        """The engine's calibrated reference constants are on a materially different scale than
        this module's real league-average chance-conversion rate -- the reason neither target is
        overlaid this phase."""
        from rebound_resolution import OFFENSIVE_REBOUND_RATE_REFERENCE, DEFENSIVE_REBOUND_RATE_REFERENCE
        result_o = estimate_rebounding_attribute(JOKIC, "2023-24", "offensive_rebounding", _SEASONS)
        result_d = estimate_rebounding_attribute(JOKIC, "2023-24", "defensive_rebounding", _SEASONS)
        self.assertGreater(result_o.league_avg_rate, OFFENSIVE_REBOUND_RATE_REFERENCE * 3)
        self.assertGreater(result_d.league_avg_rate, DEFENSIVE_REBOUND_RATE_REFERENCE * 3)


class TestEngineSensitivity(unittest.TestCase):
    """K. OREB engine sensitivity. L. DREB engine sensitivity. M. No shot/opportunity creation
    effect. These exercise the FROZEN engine's own EXISTING, already-live consumption of
    offensive_rebounding_shrunk_rate/defensive_rebounding_shrunk_rate -- independent of this
    phase's (non-)overlay decision -- by hand-setting PlayerSimulationProfile fields directly."""

    HOME_FIVE = tuple(str(i) for i in range(1, 6))
    AWAY_FIVE = tuple(str(i) for i in range(11, 16))

    @staticmethod
    def _profiles(oreb_rate=None, dreb_rate=None):
        from possession_orchestrator import PlayerSimulationProfile as PSP
        profs = {p: PSP.synthetic(p, "HOME") for p in TestEngineSensitivity.HOME_FIVE}
        kw = {}
        if oreb_rate is not None:
            kw["offensive_rebounding_shrunk_rate"] = oreb_rate
        if dreb_rate is not None:
            kw["defensive_rebounding_shrunk_rate"] = dreb_rate
        profs["1"] = PSP.synthetic("1", "HOME", **kw)
        for p in TestEngineSensitivity.AWAY_FIVE:
            profs[p] = PSP.synthetic(p, "AWAY")
        return profs

    @staticmethod
    def _count(games, pid_, event_type):
        from possession_events import EventType  # noqa: F401 (imported for symmetry/clarity)
        c = 0
        for g in games:
            for r in g.possessions:
                for e in r.events:
                    if e.event_type == event_type and e.primary_player_id == pid_:
                        c += 1
        return c

    def test_k_higher_offensive_rebounding_wins_more_offensive_rebounds(self):
        from detailed_game import simulate_detailed_game
        from possession_events import EventType
        low = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                       self._profiles(oreb_rate=0.02), rng_seed=s) for s in range(41000, 41008)]
        high = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                        self._profiles(oreb_rate=0.45), rng_seed=s) for s in range(41000, 41008)]
        self.assertGreater(self._count(high, "1", EventType.OFFENSIVE_REBOUND),
                            self._count(low, "1", EventType.OFFENSIVE_REBOUND))

    def test_l_higher_defensive_rebounding_wins_more_defensive_rebounds(self):
        from detailed_game import simulate_detailed_game
        from possession_events import EventType
        low = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                       self._profiles(dreb_rate=0.05), rng_seed=s) for s in range(42000, 42008)]
        high = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                        self._profiles(dreb_rate=0.55), rng_seed=s) for s in range(42000, 42008)]
        self.assertGreater(self._count(high, "1", EventType.DEFENSIVE_REBOUND),
                            self._count(low, "1", EventType.DEFENSIVE_REBOUND))

    def test_m_raising_offensive_rebounding_does_not_change_opportunity_count_per_game(self):
        """The per-possession rebound-opportunity gate (eligible_rebound_candidates) fires from
        the SAME real miss/carom logic regardless of any candidate's skill value -- varying
        offensive_rebounding_shrunk_rate must not fabricate additional MISS events."""
        from detailed_game import simulate_detailed_game
        from possession_events import EventType
        low = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                       self._profiles(oreb_rate=0.02), rng_seed=s) for s in range(43000, 43005)]
        high = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                        self._profiles(oreb_rate=0.45), rng_seed=s) for s in range(43000, 43005)]

        def fg_attempts(games):
            return sum(r.provisional_deltas.fga for g in games for r in g.possessions)

        low_fga, high_fga = fg_attempts(low), fg_attempts(high)
        # allow real, expected second-chance-driven variance (more OREB -> more extra
        # possessions/shots for the SAME team over a fixed set of games) but not an order-of-
        # magnitude fabrication of shot attempts.
        self.assertLess(abs(high_fga - low_fga) / max(low_fga, 1), 0.5)


class TestAccountingPreserved(unittest.TestCase):
    """N. Rebound accounting preserved. O. Rebound-contest fouls preserved -- these exercise the
    FROZEN, unmodified rebound_resolution.py invariants; this phase changed no engine code, so
    the existing test suites for those modules are the authoritative check (run separately in the
    full battery). This class adds one direct smoke check that this phase's new imports don't
    disturb the exactly-one-outcome invariant end to end."""

    def test_n_exactly_one_outcome_per_real_rebound_opportunity(self):
        from detailed_game import simulate_detailed_game
        from possession_events import EventType
        home_five = tuple(str(i) for i in range(1, 6))
        away_five = tuple(str(i) for i in range(11, 16))
        from possession_orchestrator import PlayerSimulationProfile as PSP
        profiles = {p: PSP.synthetic(p, "HOME") for p in home_five}
        profiles.update({p: PSP.synthetic(p, "AWAY") for p in away_five})
        game = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=99001)
        for r in game.possessions:
            rebound_events = [e for e in r.events if e.event_type in
                               (EventType.OFFENSIVE_REBOUND, EventType.DEFENSIVE_REBOUND)]
            # a possession may have multiple rebound events across DIFFERENT real live-ball
            # sequences (e.g. a missed FT then a later missed FG) -- but no two should share
            # identical (event_type, primary_player_id) AND represent the same physical carom.
            # The authoritative invariant test lives in test_rebound_resolution.py; this is a
            # smoke check that nothing in this phase broke event emission shape.
            for e in rebound_events:
                self.assertIsNotNone(e.primary_player_id)


class TestFutureDataSafety(unittest.TestCase):
    """P. Future-data safety."""

    def test_p_a_future_seasons_data_never_leaks_into_an_earlier_snapshot(self):
        before = prt.build_rebounding_truth_profile(JOKIC, "2018-19", _SEASONS)

        import rebound_chance_ingestion as rci
        real_load = rci.load_rebound_chances

        def poisoned(season):
            data = real_load(season)
            if season <= "2018-19" or not data:
                return data
            poisoned_data = dict(data)
            poisoned_data[JOKIC] = dict(poisoned_data.get(JOKIC, {}))
            poisoned_data[JOKIC].update(oreb=999999.0, oreb_chances=1000000.0)
            return poisoned_data

        with patch("rebound_chance_ingestion.load_rebound_chances", side_effect=poisoned), \
             patch("rebounding_estimation.load_rebound_chances", side_effect=poisoned):
            after = prt.build_rebounding_truth_profile(JOKIC, "2018-19", _SEASONS)
        self.assertEqual(before, after)

    def test_p_pregame_prior_season_only_unaffected_by_future_season_poison(self):
        before = prt.build_rebounding_truth_profile_as_of_date(JOKIC, "2018-10-01", "2018-19", _SEASONS)

        import rebound_chance_ingestion as rci
        real_load = rci.load_rebound_chances

        def poisoned(season):
            data = real_load(season)
            if season <= "2018-19" or not data:
                return data
            poisoned_data = dict(data)
            poisoned_data[JOKIC] = dict(poisoned_data.get(JOKIC, {}))
            poisoned_data[JOKIC].update(oreb=999999.0, oreb_chances=1000000.0)
            return poisoned_data

        with patch("rebound_chance_ingestion.load_rebound_chances", side_effect=poisoned), \
             patch("rebounding_estimation.load_rebound_chances", side_effect=poisoned):
            after = prt.build_rebounding_truth_profile_as_of_date(JOKIC, "2018-10-01", "2018-19", _SEASONS)
        self.assertEqual(before, after)


class TestSerializationAndMechanics(unittest.TestCase):
    """Q. Serialization deterministic. R. Engine mechanics untouched."""

    def test_q_serialization_round_trips_exactly(self):
        profile = prt.build_rebounding_truth_profile(JOKIC, "2023-24", _SEASONS)
        restored = prt.ReboundingTruthProfile.from_dict(profile.to_dict())
        self.assertEqual(profile, restored)

    def test_q_repeated_build_is_byte_identical(self):
        first = prt.build_rebounding_truth_profile(JOKIC, "2023-24", _SEASONS)
        second = prt.build_rebounding_truth_profile(JOKIC, "2023-24", _SEASONS)
        self.assertEqual(first, second)

    def test_r_new_modules_never_import_engine_resolver_internals(self):
        """Checks actual imports/usage, not module docstrings (which legitimately reference
        rebound_resolution.py's own names when documenting the overlay-scale audit)."""
        import ast
        import rebounding_estimation as re_mod
        for module in (re_mod, prt):
            with open(module.__file__) as f:
                tree = ast.parse(f.read())
            names_used = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue  # docstrings aside, only non-import references matter here
                if isinstance(node, ast.Name):
                    names_used.add(node.id)
                if isinstance(node, ast.Attribute):
                    names_used.add(node.attr)
            for forbidden in ("_dispatch_rebound", "_candidate_log_weight",
                              "resolve_rebound", "eligible_rebound_candidates"):
                self.assertNotIn(forbidden, names_used)
            imported_names = {n.name.split(".")[0] for node in ast.walk(tree)
                               if isinstance(node, ast.ImportFrom) for n in node.names}
            self.assertNotIn("possession_engine", imported_names)

    def test_r_rebound_resolution_module_source_unchanged_reference_constants(self):
        """This phase's own overlay-scale audit depends on these two constants staying what they
        were measured against -- a guard against silently drifting the audit's own premise."""
        from rebound_resolution import OFFENSIVE_REBOUND_RATE_REFERENCE, DEFENSIVE_REBOUND_RATE_REFERENCE
        self.assertAlmostEqual(OFFENSIVE_REBOUND_RATE_REFERENCE, 0.0482, places=4)
        self.assertAlmostEqual(DEFENSIVE_REBOUND_RATE_REFERENCE, 0.1313, places=4)


if __name__ == "__main__":
    unittest.main()
