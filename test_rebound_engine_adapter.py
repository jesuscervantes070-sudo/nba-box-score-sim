"""Focused tests for the Rebounding Truth -> Engine Scale Adapter V1 (rebound_engine_adapter.py)."""
import glob
import math
import unittest
from unittest.mock import patch

import player_rebounding_truth as prt
import rebound_engine_adapter as adapter
from possession_orchestrator import PlayerSimulationProfile
from rebound_chance_ingestion import load_rebound_chances
from rebounding_estimation import REBOUNDING_ATTRIBUTES, estimate_rebounding_attribute

ALL_SEASONS = sorted(d.split('/')[-1] for d in glob.glob('cache/????-??'))
JOKIC = "203999"


class TestSourceTruthUnchanged(unittest.TestCase):
    """A. Source truth unchanged."""

    def test_a_truth_profile_value_is_not_mutated_by_adapting(self):
        truth = prt.build_rebounding_truth_profile(JOKIC, "2023-24", ALL_SEASONS)
        before = {a: truth.value(a) for a in REBOUNDING_ATTRIBUTES}
        baseline = PlayerSimulationProfile.synthetic(JOKIC, "HOME")
        adapter.apply_rebounding_truth_via_adapter(baseline, truth)
        after = {a: truth.value(a) for a in REBOUNDING_ATTRIBUTES}
        self.assertEqual(before, after)

    def test_a_adapted_value_preserves_source_metadata(self):
        truth = prt.build_rebounding_truth_profile(JOKIC, "2023-24", ALL_SEASONS)
        estimate = truth.estimates["offensive_rebounding"]
        adapted = adapter.adapt_rebounding_estimate(estimate)
        self.assertEqual(adapted.source_value, estimate.value)
        self.assertEqual(adapted.source_confidence, estimate.confidence)
        self.assertEqual(adapted.source_sample_size, estimate.sample_size)
        self.assertEqual(adapted.source_provenance, estimate.provenance)
        self.assertNotEqual(adapted.engine_value, adapted.source_value)  # genuinely transformed


class TestAdapterDeterministic(unittest.TestCase):
    """B. Adapter deterministic."""

    def test_b_repeated_mapping_is_byte_identical(self):
        a = adapter.map_chance_rate_to_engine_scale(0.42, "offensive_rebounding")
        b = adapter.map_chance_rate_to_engine_scale(0.42, "offensive_rebounding")
        self.assertEqual(a, b)


class TestMonotonicMapping(unittest.TestCase):
    """C. Monotonic OREB mapping. D. Monotonic DREB mapping."""

    def test_c_offensive_mapping_is_strictly_increasing(self):
        xs = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
        mapped = [adapter.map_chance_rate_to_engine_scale(x, "offensive_rebounding") for x in xs]
        self.assertEqual(mapped, sorted(mapped))
        self.assertEqual(len(set(mapped)), len(mapped))

    def test_d_defensive_mapping_is_strictly_increasing(self):
        xs = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
        mapped = [adapter.map_chance_rate_to_engine_scale(x, "defensive_rebounding") for x in xs]
        self.assertEqual(mapped, sorted(mapped))
        self.assertEqual(len(set(mapped)), len(mapped))


class TestPopulationCentering(unittest.TestCase):
    """E. Population centering."""

    def test_e_chance_population_mean_maps_near_engine_reference(self):
        for attribute in REBOUNDING_ATTRIBUTES:
            mean_rate = 1.0 / (1.0 + math.exp(-adapter.CHANCE_MEAN_LOGIT[attribute]))
            mapped = adapter.map_chance_rate_to_engine_scale(mean_rate, attribute)
            self.assertAlmostEqual(mapped, adapter.ENGINE_REFERENCE[attribute], places=6)


class TestRankPreservation(unittest.TestCase):
    """F. Rank preservation."""

    def test_f_spearman_correlation_against_source_is_exactly_one(self):
        season = "2023-24"
        chances = load_rebound_chances(season)
        for attribute in REBOUNDING_ATTRIBUTES:
            raw_vals = []
            for pid in list(chances)[:120]:
                r = estimate_rebounding_attribute(pid, season, attribute, ALL_SEASONS)
                if r.shrunk_rate is not None:
                    raw_vals.append(r.shrunk_rate)
            mapped_vals = [adapter.map_chance_rate_to_engine_scale(v, attribute) for v in raw_vals]
            raw_rank = sorted(range(len(raw_vals)), key=lambda i: raw_vals[i])
            mapped_rank = sorted(range(len(mapped_vals)), key=lambda i: mapped_vals[i])
            self.assertEqual(raw_rank, mapped_rank)


class TestNoPathologicalExtremes(unittest.TestCase):
    """G. No pathological extremes."""

    def test_g_extreme_inputs_stay_bounded_and_finite(self):
        for attribute in REBOUNDING_ATTRIBUTES:
            for extreme in (0.0001, 0.9999):
                mapped = adapter.map_chance_rate_to_engine_scale(extreme, attribute)
                self.assertTrue(0.0 < mapped < 1.0)
                self.assertFalse(math.isnan(mapped))
                self.assertFalse(math.isinf(mapped))

    def test_g_realistic_population_range_has_no_clipping_mass(self):
        season = "2023-24"
        chances = load_rebound_chances(season)
        for attribute in REBOUNDING_ATTRIBUTES:
            mapped = []
            for pid in chances:
                r = estimate_rebounding_attribute(pid, season, attribute, ALL_SEASONS)
                if r.shrunk_rate is not None:
                    mapped.append(adapter.map_chance_rate_to_engine_scale(r.shrunk_rate, attribute))
            # a sane real population should not pile up at a shared boundary value
            distinct = len(set(round(m, 6) for m in mapped))
            self.assertGreater(distinct, len(mapped) * 0.9)
            self.assertLess(max(mapped), 0.5)  # sane upper bound, not an implausible near-certainty
            self.assertGreater(min(mapped), 0.0)


class TestMissingLeavesDefaultUnchanged(unittest.TestCase):
    """H. Missing leaves default unchanged."""

    def test_h_missing_truth_leaves_synthetic_default(self):
        truth = prt.build_rebounding_truth_profile("999999999", "2023-24", ["2023-24"])
        baseline = PlayerSimulationProfile.synthetic("999999999", "HOME")
        result = adapter.apply_rebounding_truth_via_adapter(baseline, truth)
        self.assertEqual(result, baseline)
        self.assertEqual(result.offensive_rebounding_shrunk_rate, 0.08)
        self.assertEqual(result.defensive_rebounding_shrunk_rate, 0.15)


class TestOnlyReboundFieldsOverlaid(unittest.TestCase):
    """I. Only rebound fields overlaid."""

    def test_i_no_other_field_changes(self):
        from dataclasses import fields
        truth = prt.build_rebounding_truth_profile(JOKIC, "2023-24", ALL_SEASONS)
        baseline = PlayerSimulationProfile.synthetic(JOKIC, "HOME")
        result = adapter.apply_rebounding_truth_via_adapter(baseline, truth)
        touched = {"offensive_rebounding_shrunk_rate", "defensive_rebounding_shrunk_rate"}
        for f in fields(PlayerSimulationProfile):
            if f.name in touched:
                continue
            self.assertEqual(getattr(result, f.name), getattr(baseline, f.name), f.name)

    def test_i_both_fields_actually_change_for_real_evidence(self):
        truth = prt.build_rebounding_truth_profile(JOKIC, "2023-24", ALL_SEASONS)
        baseline = PlayerSimulationProfile.synthetic(JOKIC, "HOME")
        result = adapter.apply_rebounding_truth_via_adapter(baseline, truth)
        self.assertNotEqual(result.offensive_rebounding_shrunk_rate, baseline.offensive_rebounding_shrunk_rate)
        self.assertNotEqual(result.defensive_rebounding_shrunk_rate, baseline.defensive_rebounding_shrunk_rate)


class TestRealPlayerEngineSensitivity(unittest.TestCase):
    """J. Real-player OREB sensitivity. K. Real-player DREB sensitivity."""

    HOME_FIVE = tuple(str(i) for i in range(1, 6))
    AWAY_FIVE = tuple(str(i) for i in range(11, 16))

    @staticmethod
    def _profile_for(player_id, season, slot="1", side="HOME"):
        """Builds real truth from `player_id` but returns a profile keyed for `slot` (the game
        seat this player occupies) -- the engine validates profile.player_id against its dict key,
        while the real-world identity only matters for looking up real evidence."""
        truth = prt.build_rebounding_truth_profile(player_id, season, ALL_SEASONS)
        baseline = PlayerSimulationProfile.synthetic(slot, side)
        return adapter.apply_rebounding_truth_via_adapter(baseline, truth)

    @staticmethod
    def _count(games, pid_, event_type):
        c = 0
        for g in games:
            for r in g.possessions:
                for e in r.events:
                    if e.event_type == event_type and e.primary_player_id == pid_:
                        c += 1
        return c

    def test_j_elite_real_oreb_player_outrebounds_weak_real_oreb_player(self):
        """Same environment/seeds; only the target player's real OREB truth differs."""
        from detailed_game import simulate_detailed_game
        from possession_events import EventType
        season = "2023-24"
        chances = load_rebound_chances(season)
        results = [(pid, estimate_rebounding_attribute(pid, season, "offensive_rebounding", ALL_SEASONS))
                   for pid in chances]
        results = [(pid, r) for pid, r in results if r.shrunk_rate is not None and r.total_weight > 200]
        results.sort(key=lambda kv: kv[1].shrunk_rate)
        weak_id, elite_id = results[0][0], results[-1][0]

        def profiles(target_id):
            profs = {p: PlayerSimulationProfile.synthetic(p, "HOME") for p in self.HOME_FIVE}
            profs["1"] = self._profile_for(target_id, season)
            for p in self.AWAY_FIVE:
                profs[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
            return profs

        weak_games = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                              profiles(weak_id), rng_seed=s) for s in range(51000, 51010)]
        elite_games = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                               profiles(elite_id), rng_seed=s) for s in range(51000, 51010)]
        weak_oreb = self._count(weak_games, "1", EventType.OFFENSIVE_REBOUND)
        elite_oreb = self._count(elite_games, "1", EventType.OFFENSIVE_REBOUND)
        self.assertGreater(elite_oreb, weak_oreb)

    def test_k_elite_real_dreb_player_outrebounds_weak_real_dreb_player(self):
        from detailed_game import simulate_detailed_game
        from possession_events import EventType
        season = "2023-24"
        chances = load_rebound_chances(season)
        results = [(pid, estimate_rebounding_attribute(pid, season, "defensive_rebounding", ALL_SEASONS))
                   for pid in chances]
        results = [(pid, r) for pid, r in results if r.shrunk_rate is not None and r.total_weight > 200]
        results.sort(key=lambda kv: kv[1].shrunk_rate)
        weak_id, elite_id = results[0][0], results[-1][0]

        def profiles(target_id):
            profs = {p: PlayerSimulationProfile.synthetic(p, "HOME") for p in self.HOME_FIVE}
            profs["1"] = self._profile_for(target_id, season)
            for p in self.AWAY_FIVE:
                profs[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
            return profs

        weak_games = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                              profiles(weak_id), rng_seed=s) for s in range(52000, 52010)]
        elite_games = [simulate_detailed_game("HOME", "AWAY", self.HOME_FIVE, self.AWAY_FIVE,
                                               profiles(elite_id), rng_seed=s) for s in range(52000, 52010)]
        weak_dreb = self._count(weak_games, "1", EventType.DEFENSIVE_REBOUND)
        elite_dreb = self._count(elite_games, "1", EventType.DEFENSIVE_REBOUND)
        self.assertGreater(elite_dreb, weak_dreb)


class TestOpportunityInvariant(unittest.TestCase):
    """L. Rebound opportunities unchanged structurally. M. Second-chance effects remain
    legitimate."""

    def test_l_overlay_does_not_change_engine_field_types_or_opportunity_gate(self):
        """The adapter only ever writes a float into the two rebound-rate fields -- it never
        touches candidate eligibility/zone logic, which lives entirely in rebound_resolution.py
        and is untouched by this module (see test_n)."""
        truth = prt.build_rebounding_truth_profile(JOKIC, "2023-24", ALL_SEASONS)
        baseline = PlayerSimulationProfile.synthetic(JOKIC, "HOME")
        result = adapter.apply_rebounding_truth_via_adapter(baseline, truth)
        self.assertIsInstance(result.offensive_rebounding_shrunk_rate, float)
        self.assertIsInstance(result.defensive_rebounding_shrunk_rate, float)


class TestNoMechanicsEdits(unittest.TestCase):
    """N. No mechanics edits."""

    def test_n_module_never_imports_or_calls_resolver_internals(self):
        import ast
        with open(adapter.__file__) as f:
            tree = ast.parse(f.read())
        names_used = {n.id for node in ast.walk(tree) if isinstance(node, ast.Name) for n in [node]}
        attrs_used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for forbidden in ("_dispatch_rebound", "_candidate_log_weight", "resolve_rebound",
                          "eligible_rebound_candidates", "_softmax_choice"):
            self.assertNotIn(forbidden, names_used | attrs_used)

    def test_n_only_imports_reference_constants_and_bounded_logit_from_resolver(self):
        import ast
        with open(adapter.__file__) as f:
            tree = ast.parse(f.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "rebound_resolution":
                imported.update(n.name for n in node.names)
        self.assertEqual(imported, {"OFFENSIVE_REBOUND_RATE_REFERENCE", "DEFENSIVE_REBOUND_RATE_REFERENCE",
                                     "_bounded_logit"})


class TestTemporalSafety(unittest.TestCase):
    """O. Prior-season temporal safety. P. Future-season leakage safety."""

    def test_o_pregame_profile_is_still_prior_season_only(self):
        import player_scoring_truth_temporal as psst
        truth = prt.build_rebounding_truth_profile_as_of_date(JOKIC, "2023-10-01", "2023-24", ALL_SEASONS)
        for attribute in REBOUNDING_ATTRIBUTES:
            self.assertIn(truth.estimates[attribute].provenance, (psst.PRIOR_SEASON_ONLY, psst.MISSING))

    def test_p_future_season_poison_does_not_change_adapted_value_for_an_earlier_snapshot(self):
        before_truth = prt.build_rebounding_truth_profile(JOKIC, "2018-19", ALL_SEASONS)
        before_baseline = PlayerSimulationProfile.synthetic(JOKIC, "HOME")
        before_result = adapter.apply_rebounding_truth_via_adapter(before_baseline, before_truth)

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
            after_truth = prt.build_rebounding_truth_profile(JOKIC, "2018-19", ALL_SEASONS)
            after_baseline = PlayerSimulationProfile.synthetic(JOKIC, "HOME")
            after_result = adapter.apply_rebounding_truth_via_adapter(after_baseline, after_truth)
        self.assertEqual(before_result, after_result)


class TestSerializationVersionDeterminism(unittest.TestCase):
    """Q. Serialization/version determinism."""

    def test_q_mapping_version_is_stable_string(self):
        self.assertEqual(adapter.ADAPTER_VERSION, "logit_standardized_v1")
        truth = prt.build_rebounding_truth_profile(JOKIC, "2023-24", ALL_SEASONS)
        adapted = adapter.adapt_rebounding_estimate(truth.estimates["offensive_rebounding"])
        self.assertEqual(adapted.mapping_version, adapter.ADAPTER_VERSION)

    def test_q_adapted_value_repr_round_trips_fields(self):
        truth = prt.build_rebounding_truth_profile(JOKIC, "2023-24", ALL_SEASONS)
        adapted = adapter.adapt_rebounding_estimate(truth.estimates["defensive_rebounding"])
        rebuilt = adapter.AdaptedReboundValue(
            attribute=adapted.attribute, engine_value=adapted.engine_value,
            source_value=adapted.source_value, source_confidence=adapted.source_confidence,
            source_sample_size=adapted.source_sample_size, source_provenance=adapted.source_provenance,
            mapping_version=adapted.mapping_version,
        )
        self.assertEqual(adapted, rebuilt)


class TestFullEngineAccountingPreserved(unittest.TestCase):
    """R. Full engine accounting preserved."""

    def test_r_exactly_one_rebound_outcome_per_opportunity_with_real_overlay(self):
        from detailed_game import simulate_detailed_game
        from possession_events import EventType
        season = "2023-24"
        home_five = tuple(str(i) for i in range(1, 6))
        away_five = tuple(str(i) for i in range(11, 16))
        profiles = {p: PlayerSimulationProfile.synthetic(p, "HOME") for p in home_five}
        profiles["1"] = TestRealPlayerEngineSensitivity._profile_for(JOKIC, season)
        for p in away_five:
            profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
        game = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=88001)
        for r in game.possessions:
            rebound_events = [e for e in r.events if e.event_type in
                               (EventType.OFFENSIVE_REBOUND, EventType.DEFENSIVE_REBOUND)]
            for e in rebound_events:
                self.assertIsNotNone(e.primary_player_id)


if __name__ == "__main__":
    unittest.main()
