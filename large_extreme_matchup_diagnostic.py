"""LARGE / EXTREME MATCHUP COMPRESSION DIAGNOSTIC V1.

A narrow follow-on to `margin_compression_diagnostic.py`, triggered by FROZEN HISTORICAL BACKTEST
RE-EVALUATION V2's own finding: near-equal games are no longer the dominant failure mode, but
large (8-15 net-rating-point) and extreme (>=15) real strength gaps still fail to become
proportionally large simulated margins. This module traces that one gap -- REAL TEAM STRENGTH ->
PLAYER/ROTATION INPUTS -> TEAM-LEVEL ENGINE INPUTS -> POSSESSION OUTPUT -> SIMULATED MARGIN --
through the large/extreme subset specifically, and localizes where it collapses.

DIAGNOSTIC ONLY. No engine mechanic, calibration, or production parameter is touched here. Every
function is either a read-only measurement over already-built snapshots/profiles, or an isolated,
clearly-labeled synthetic/counterfactual simulation batch that is never written back into any
production cache.

============================ SAMPLE (disjoint from BACKTEST_V1_HOLDOUT) ============================
`select_large_extreme_games()` draws from `margin_compression_diagnostic.load_or_create_development
_sample()` -- the SAME dev sample MARGIN COMPRESSION DIAGNOSTIC V1 already used, itself disjoint by
construction from BACKTEST_V1_HOLDOUT. Games are bucketed by the SAME |strength_diff| thresholds
that diagnostic already established (large: [8, 15), extreme: [15, inf)) and selected by sorted
game_id -- a fully mechanical rule, never a hand-picked "memorable blowout". Locked to
`backtests/large_extreme_matchup_game_ids.json` before any diagnostic experiment runs.
"""
import hashlib
import json
import statistics
from pathlib import Path
from typing import Dict, List, Tuple

import margin_compression_diagnostic as mcd
import historical_game_snapshot as hgs
import historical_game_outcome as hgo
import historical_predictive_backtest as hpb
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault
from possession_orchestrator import PlayerSimulationProfile

SEASON = mcd.SEASON
ALL_SEASONS = mcd.ALL_SEASONS
BACKTESTS_DIR = mcd.BACKTESTS_DIR

LARGE_MIN, LARGE_MAX = 8.0, 15.0
EXTREME_MIN = 15.0

_mean = mcd._mean
_sd = mcd._sd
_pearson = mcd._pearson


# =====================================================================
# A. Deterministic large/extreme sample selection
# =====================================================================
def select_large_extreme_games(n_per_bucket: int = 8) -> Dict[str, List[str]]:
    dev_games = mcd.load_or_create_development_sample()
    records = mcd.build_dev_team_records(dev_games)
    by_game: Dict[str, float] = {}
    for r in records:
        by_game.setdefault(r["game_id"], abs(r["strength_diff"]))
    large = sorted(gid for gid, d in by_game.items() if LARGE_MIN <= d < LARGE_MAX)[:n_per_bucket]
    extreme = sorted(gid for gid, d in by_game.items() if d >= EXTREME_MIN)[:n_per_bucket]
    return {"large": large, "extreme": extreme}


def load_or_create_large_extreme_sample(n_per_bucket: int = 8) -> dict:
    BACKTESTS_DIR.mkdir(exist_ok=True)
    path = BACKTESTS_DIR / "large_extreme_matchup_game_ids.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    buckets = select_large_extreme_games(n_per_bucket)
    dev_games = set(mcd.load_or_create_development_sample())
    all_ids = sorted(set(buckets["large"]) | set(buckets["extreme"]))
    assert set(all_ids) <= dev_games, "large/extreme sample must be drawn from the development sample"
    holdout = set(hpb.load_or_create_holdout(SEASON))
    assert not (set(all_ids) & holdout), "large/extreme sample must be disjoint from BACKTEST_V1_HOLDOUT"
    out = {"n_per_bucket": n_per_bucket, "large_min": LARGE_MIN, "large_max": LARGE_MAX, "extreme_min": EXTREME_MIN,
           "buckets": buckets, "n_games": len(all_ids), "game_ids": all_ids}
    with open(path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    return out


def _strong_weak_sides(records_for_game: List[dict]) -> Tuple[dict, dict]:
    """(strong_side_record, weak_side_record) for a two-row (home/away) game record group."""
    a, b = records_for_game
    return (a, b) if a["net_rating"] >= b["net_rating"] else (b, a)


# =====================================================================
# B. Engine-input gap by category, large/extreme only
# =====================================================================
FIELD_GROUPS = {
    "scoring": mcd.SCORING_FIELDS,
    "playmaking": mcd.PLAYMAKING_FIELDS,
    "rebounding": mcd.REBOUNDING_FIELDS,
    "defense": mcd.DEFENSE_FIELDS,
    "role": mcd.ROLE_FIELDS,
}


def category_gap_report(game_ids: List[str]) -> dict:
    """For each field, the mean (strong - weak) engine-input gap across the given games, primary-
    five and full-roster, plus whether the strong team's mean actually exceeds the weak team's on
    a per-game basis (a real gap direction check, not just an average)."""
    records = mcd.build_dev_team_records(game_ids)
    by_game: Dict[str, List[dict]] = {}
    for r in records:
        by_game.setdefault(r["game_id"], []).append(r)
    out = {}
    for group, fields in FIELD_GROUPS.items():
        out[group] = {}
        for field in fields:
            lower_is_better = field in mcd.LOWER_IS_BETTER_FIELDS
            p5_gaps, roster_gaps, correct_direction = [], [], 0
            n_pairs = 0
            for gid, sides in by_game.items():
                if len(sides) != 2:
                    continue
                strong, weak = _strong_weak_sides(sides)
                s_p5 = strong["primary_five_summary"][field]["mean"]
                w_p5 = weak["primary_five_summary"][field]["mean"]
                s_full = strong["full_roster_summary"][field]["mean"]
                w_full = weak["full_roster_summary"][field]["mean"]
                if s_p5 is None or w_p5 is None:
                    continue
                n_pairs += 1
                gap = (s_p5 - w_p5) if not lower_is_better else (w_p5 - s_p5)
                p5_gaps.append(gap)
                if s_full is not None and w_full is not None:
                    roster_gaps.append((s_full - w_full) if not lower_is_better else (w_full - s_full))
                if gap > 0:
                    correct_direction += 1
            out[group][field] = {
                "n_game_pairs": n_pairs,
                "mean_primary_five_gap (strong-weak, sign-corrected)": _mean(p5_gaps),
                "mean_full_roster_gap (strong-weak, sign-corrected)": _mean(roster_gaps),
                "share_games_strong_actually_ahead": (correct_direction / n_pairs) if n_pairs else None,
            }
    return out


# =====================================================================
# C. Top-five vs full-rotation scoring-composite gap, large/extreme only
# =====================================================================
def scoring_composite_gap_rows(game_ids: List[str], n_sims: int = 60) -> List[dict]:
    """Per game: a SCORING-only composite (plain mean of the six scoring fields -- diagnostic-only,
    never an OVR, never fed back into any production path) for the strong and weak team, computed
    both top-five-only and real-minutes-weighted full-rotation, plus the simulated mean margin from
    the strong team's perspective for the SAME snapshot/profiles."""
    rows = []
    for game_id in game_ids:
        try:
            oracle_snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_ORACLE_PARTICIPANTS)
            pregame_snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        outcome = hgo.get_game_outcome(game_id, SEASON)
        if outcome is None:
            continue
        side_info = {}
        for team_snap, is_home in ((oracle_snap.home_team_snapshot, True), (oracle_snap.away_team_snapshot, False)):
            primary = [p for p in team_snap.players if p.is_primary_five]
            full = list(team_snap.players)
            top5_composite = _mean([m for m in (
                _mean([getattr(p.simulation_profile, f) for f in mcd.SCORING_FIELDS
                       if getattr(p.simulation_profile, f) is not None]) for p in primary) if m is not None])
            weighted_vals, weighted_minutes = [], []
            for p in full:
                m = _mean([getattr(p.simulation_profile, f) for f in mcd.SCORING_FIELDS
                            if getattr(p.simulation_profile, f) is not None])
                if m is not None and p.expected_minutes:
                    weighted_vals.append(m * p.expected_minutes)
                    weighted_minutes.append(p.expected_minutes)
            full_composite = (sum(weighted_vals) / sum(weighted_minutes)) if weighted_minutes else None
            side_info[team_snap.team_name] = {
                "is_home": is_home, "top5_composite": top5_composite, "full_composite": full_composite,
                "net_rating": hpb._team_net_rating_as_of(team_snap.team_name, oracle_snap.game_date, SEASON),
            }
        names = list(side_info.keys())
        if len(names) != 2:
            continue
        strong_name, weak_name = (names[0], names[1]) if side_info[names[0]]["net_rating"] >= side_info[names[1]]["net_rating"] \
            else (names[1], names[0])
        strong, weak = side_info[strong_name], side_info[weak_name]
        top5_gap = (strong["top5_composite"] - weak["top5_composite"]) \
            if strong["top5_composite"] is not None and weak["top5_composite"] is not None else None
        full_gap = (strong["full_composite"] - weak["full_composite"]) \
            if strong["full_composite"] is not None and weak["full_composite"] is not None else None
        actual_margin_strong = outcome.margin if strong["is_home"] else -outcome.margin

        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(pregame_snap)
        margins, _ = mcd._batch_margin(home_id, away_id, home_five, away_five, profiles, game_id, "scoring_composite", n_sims)
        sim_margin_home = _mean(margins)
        sim_margin_strong = (sim_margin_home if strong["is_home"] else (-sim_margin_home if sim_margin_home is not None else None))

        rows.append({
            "game_id": game_id, "strong_team": strong_name, "weak_team": weak_name,
            "strength_diff": strong["net_rating"] - weak["net_rating"],
            "top5_scoring_composite_gap": top5_gap, "full_rotation_scoring_composite_gap": full_gap,
            "full_minus_top5 (full-rotation gap larger => bench-relevant)": (
                full_gap - top5_gap if top5_gap is not None and full_gap is not None else None),
            "actual_margin_strong_perspective": actual_margin_strong,
            "simulated_mean_margin_strong_perspective": sim_margin_strong,
        })
    return rows


def scoring_composite_correlations(rows: List[dict]) -> dict:
    top5_gaps = [r["top5_scoring_composite_gap"] for r in rows]
    full_gaps = [r["full_rotation_scoring_composite_gap"] for r in rows]
    actual = [r["actual_margin_strong_perspective"] for r in rows]
    simulated = [r["simulated_mean_margin_strong_perspective"] for r in rows]
    return {
        "n": len(rows),
        "corr_top5_gap_vs_actual_margin": _pearson(top5_gaps, actual),
        "corr_full_rotation_gap_vs_actual_margin": _pearson(full_gaps, actual),
        "corr_top5_gap_vs_simulated_margin": _pearson(top5_gaps, simulated),
        "corr_full_rotation_gap_vs_simulated_margin": _pearson(full_gaps, simulated),
        "mean_full_minus_top5_gap": _mean([r["full_minus_top5 (full-rotation gap larger => bench-relevant)"] for r in rows]),
    }


# =====================================================================
# D. Bench quality: strong-team bench vs weak-team bench, large/extreme only
# =====================================================================
def bench_quality_strong_vs_weak(game_ids: List[str]) -> dict:
    rows = mcd.fixed_five_information_loss(game_ids)
    by_game: Dict[str, List[dict]] = {}
    for r in rows:
        by_game.setdefault(r["game_id"], []).append(r)
    bench_field_groups = {
        "scoring": mcd.SCORING_FIELDS, "playmaking": mcd.PLAYMAKING_FIELDS,
        "rebounding": mcd.REBOUNDING_FIELDS, "defense": mcd.DEFENSE_FIELDS, "role": mcd.ROLE_FIELDS,
    }

    def _bench_composite(game_id, team_name, fields):
        try:
            snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_ORACLE_PARTICIPANTS)
        except Exception:
            return None
        team_snap = snap.home_team_snapshot if snap.home_team == team_name else snap.away_team_snapshot
        bench = [p for p in team_snap.players if not p.is_primary_five]
        vals = []
        for f in fields:
            m = _mean([getattr(p.simulation_profile, f) for p in bench if getattr(p.simulation_profile, f) is not None])
            if m is not None:
                vals.append(m if f not in mcd.LOWER_IS_BETTER_FIELDS else -m)
        return _mean(vals) if vals else None

    out = {group: {"strong_bench": [], "weak_bench": []} for group in bench_field_groups}
    top5_minute_shares = {"large_extreme": []}
    for gid, sides in by_game.items():
        if len(sides) != 2:
            continue
        strong, weak = (sides[0], sides[1]) if sides[0]["net_rating"] >= sides[1]["net_rating"] else (sides[1], sides[0])
        for group, fields in bench_field_groups.items():
            s_val = _bench_composite(gid, strong["team"], fields)
            w_val = _bench_composite(gid, weak["team"], fields)
            if s_val is not None:
                out[group]["strong_bench"].append(s_val)
            if w_val is not None:
                out[group]["weak_bench"].append(w_val)
        for r in sides:
            if r["top5_minute_share"] is not None:
                top5_minute_shares["large_extreme"].append(r["top5_minute_share"])

    report = {}
    for group, d in out.items():
        s_mean, w_mean = _mean(d["strong_bench"]), _mean(d["weak_bench"])
        report[group] = {"strong_bench_mean": s_mean, "weak_bench_mean": w_mean,
                          "gap (strong-weak)": (s_mean - w_mean) if s_mean is not None and w_mean is not None else None}
    report["mean_top5_minute_share_large_extreme"] = _mean(top5_minute_shares["large_extreme"])
    return report


# =====================================================================
# E. Synthetic stacking experiments (scoring / defense / rebounding)
# =====================================================================
ELITE_SCORING_KWARGS = dict(rim_finishing_shrunk_rate=0.85, floater_short_mid_shrunk_rate=0.65,
                             midrange_shrunk_rate=0.70, three_point_shrunk_rate=0.55, free_throw_shrunk_rate=0.92)
WEAK_SCORING_KWARGS = dict(rim_finishing_shrunk_rate=0.49, floater_short_mid_shrunk_rate=0.23,
                            midrange_shrunk_rate=0.28, three_point_shrunk_rate=0.24, free_throw_shrunk_rate=0.64)
ELITE_DEFENSE_KWARGS = dict(poa_containment_shrunk_rate=0.12, rim_protection_suppression_rate=0.045,
                             defensive_playmaking_per36=3.2)
ELITE_REBOUND_KWARGS = dict(offensive_rebounding_shrunk_rate=0.16, defensive_rebounding_shrunk_rate=0.28)


def _stack_team(pids: Tuple[str, ...], side: str, n_stacked: int, stacked_kwargs: dict) -> Dict[str, PlayerSimulationProfile]:
    profiles = {}
    for i, pid in enumerate(pids):
        if i < n_stacked:
            profiles[pid] = PlayerSimulationProfile.synthetic(pid, side, **stacked_kwargs)
        else:
            profiles[pid] = PlayerSimulationProfile.synthetic(pid, side)
    return profiles


def stacking_progression(stacked_kwargs: dict, label: str, n_sims: int = 100,
                          stack_levels: Tuple[int, ...] = (0, 1, 2, 3, 4, 5)) -> dict:
    """Isolated fully-synthetic 5v5: AVG team (5 league-average synthetics) on one side, STACK
    team (k players carrying `stacked_kwargs`, 5-k league-average) on the other, for
    k in stack_levels. Every other field (role, defense unless `stacked_kwargs` itself sets
    defense, playmaking, rebounding) stays at synthetic-average on both sides -- isolates exactly
    ONE dimension of team-quality stacking per call. Never touches any real snapshot or cache."""
    AVG = tuple(str(910001 + i) for i in range(5))
    STACK = tuple(str(910011 + i) for i in range(5))
    avg_profiles = {pid: PlayerSimulationProfile.synthetic(pid, "AWAY") for pid in AVG}
    progression = []
    for k in stack_levels:
        profiles = dict(avg_profiles)
        profiles.update(_stack_team(STACK, "HOME", k, stacked_kwargs))
        margins, faults = mcd._batch_margin("HOME", "AWAY", STACK, AVG, profiles, f"stack_{label}", f"k{k}", n_sims)
        progression.append({"n_stacked": k, "mean_margin_stack_perspective": _mean(margins),
                             "sd_margin": _sd(margins), "n_valid": len(margins), "n_faults": faults})
    marginal = []
    for i in range(1, len(progression)):
        prev, cur = progression[i - 1]["mean_margin_stack_perspective"], progression[i]["mean_margin_stack_perspective"]
        marginal.append(None if prev is None or cur is None else cur - prev)
    return {"label": label, "progression": progression, "marginal_gain_per_additional_stacked_player": marginal}


def rebounding_stacking_progression(n_sims: int = 60, stack_levels: Tuple[int, ...] = (0, 1, 3, 5)) -> dict:
    """Same isolated-synthetic structure as `stacking_progression`, but measures real captured
    OREB/DREB counts from `provisional_deltas` across the batch instead of margin -- rebounding
    stacking may not move final score enough to show up cleanly in margin alone."""
    AVG = tuple(str(920001 + i) for i in range(5))
    STACK = tuple(str(920011 + i) for i in range(5))
    avg_profiles = {pid: PlayerSimulationProfile.synthetic(pid, "AWAY") for pid in AVG}
    progression = []
    for k in stack_levels:
        profiles = dict(avg_profiles)
        profiles.update(_stack_team(STACK, "HOME", k, ELITE_REBOUND_KWARGS))
        stack_oreb = stack_dreb = avg_oreb = avg_dreb = n_valid = 0
        for i in range(n_sims):
            seed = int(hashlib.sha256(f"reb_stack|k{k}|{i}".encode()).hexdigest()[:16], 16)
            try:
                result = simulate_detailed_game("HOME", "AWAY", STACK, AVG, profiles, rng_seed=seed)
            except DetailedGameSimulationFault:
                continue
            n_valid += 1
            for rec in result.possessions:
                d = rec.provisional_deltas
                if rec.offense_team_id == "HOME":
                    stack_oreb += d.oreb; avg_dreb += d.dreb
                else:
                    avg_oreb += d.oreb; stack_dreb += d.dreb
        progression.append({"n_stacked": k, "n_valid_sims": n_valid,
                             "stack_oreb_per_sim": (stack_oreb / n_valid) if n_valid else None,
                             "stack_dreb_per_sim": (stack_dreb / n_valid) if n_valid else None,
                             "avg_oreb_per_sim": (avg_oreb / n_valid) if n_valid else None,
                             "avg_dreb_per_sim": (avg_dreb / n_valid) if n_valid else None})
    return {"progression": progression}


# =====================================================================
# F. Real-player sequential (cumulative) replacement -- saturation check on REAL data
# =====================================================================
def sequential_replacement_progression(game_id: str, side: str, n_sims: int = 80) -> dict:
    """Starting from the real, unmodified pregame snapshot for `game_id`, cumulatively replaces
    `side`'s primary-five players (in real player_id sort order, for determinism) with league-
    average synthetics one at a time -- 0..5 replaced -- and records the batch mean margin at each
    step. `side` is the literal engine side string ("HOME"/"AWAY") whose real players are being
    progressively removed."""
    snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
    home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
    target_five = home_five if side == home_id else away_five
    ordered = sorted(target_five)
    progression = []
    for k in range(0, 6):
        step_profiles = dict(profiles)
        for pid in ordered[:k]:
            step_profiles[pid] = PlayerSimulationProfile.synthetic(pid, side)
        margins, _ = mcd._batch_margin(home_id, away_id, home_five, away_five, step_profiles, game_id,
                                        f"seqrepl_{side}_k{k}", n_sims)
        progression.append({"n_replaced": k, "mean_margin_home_perspective": _mean(margins), "n_valid": len(margins)})
    marginal = []
    for i in range(1, len(progression)):
        prev, cur = progression[i - 1]["mean_margin_home_perspective"], progression[i]["mean_margin_home_perspective"]
        marginal.append(None if prev is None or cur is None else cur - prev)
    return {"game_id": game_id, "side": side, "progression": progression,
            "marginal_shift_per_additional_replacement": marginal}


def full_team_average_replacement(game_id: str, n_sims: int = 100) -> dict:
    """Baseline (both sides real) vs both sides fully replaced by league-average synthetics --
    isolates how much of the real simulated margin traces to real player-quality INPUT
    differences at all (as opposed to matchup structure / engine mechanics unrelated to inputs)."""
    snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
    home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
    baseline_margins, _ = mcd._batch_margin(home_id, away_id, home_five, away_five, profiles, game_id, "baseline_real", n_sims)
    avg_profiles = {pid: PlayerSimulationProfile.synthetic(pid, home_id) for pid in home_five}
    avg_profiles.update({pid: PlayerSimulationProfile.synthetic(pid, away_id) for pid in away_five})
    avg_margins, _ = mcd._batch_margin(home_id, away_id, home_five, away_five, avg_profiles, game_id, "both_average", n_sims)
    return {"game_id": game_id, "n_sims": n_sims,
            "baseline_real_mean_margin": _mean(baseline_margins), "both_average_mean_margin": _mean(avg_margins),
            "margin_explained_by_player_quality_inputs": (
                (_mean(baseline_margins) - _mean(avg_margins))
                if baseline_margins and avg_margins else None)}


# =====================================================================
# G. Output-rate decomposition (offense vs defense/hustle), large/extreme only
# =====================================================================
def simulated_team_extended_splits(game_ids: List[str], n_sims: int = 50) -> Dict[str, dict]:
    """Like `margin_compression_diagnostic.simulated_team_shooting_splits`, extended with
    turnover rate and OREB rate (both per 100 real offensive possessions credited to that team),
    for offense/defense/hustle output-rate localization."""
    team_totals: Dict[str, dict] = {}
    for game_id in game_ids:
        try:
            snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        for i in range(n_sims):
            seed = int(hashlib.sha256(f"ext-splits|{game_id}|{i}".encode()).hexdigest()[:16], 16)
            try:
                result = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
            except DetailedGameSimulationFault:
                continue
            for team_name, team_id in ((snap.home_team, home_id), (snap.away_team, away_id)):
                team_totals.setdefault(team_name, {"fgm": 0, "fga": 0, "fg3m": 0, "fg3a": 0, "ftm": 0, "fta": 0,
                                                     "turnovers": 0, "oreb": 0, "possessions": 0})
            for rec in result.possessions:
                off_team = snap.home_team if rec.offense_team_id == home_id else snap.away_team
                d = rec.provisional_deltas
                t = team_totals[off_team]
                t["fgm"] += d.fgm; t["fga"] += d.fga; t["fg3m"] += d.fg3m; t["fg3a"] += d.fg3a
                t["ftm"] += d.ftm; t["fta"] += d.fta
                t["turnovers"] += d.turnovers; t["oreb"] += d.oreb
                t["possessions"] += 1
    splits = {}
    for team, t in team_totals.items():
        fg2m, fg2a = t["fgm"] - t["fg3m"], t["fga"] - t["fg3a"]
        efg = ((t["fgm"] + 0.5 * t["fg3m"]) / t["fga"]) if t["fga"] else None
        splits[team] = {
            "two_pt_pct": (fg2m / fg2a) if fg2a else None,
            "three_pt_pct": (t["fg3m"] / t["fg3a"]) if t["fg3a"] else None,
            "ft_rate": (t["fta"] / t["fga"]) if t["fga"] else None,
            "effective_fg_pct": efg,
            "turnover_rate_per_100_poss": (t["turnovers"] / t["possessions"] * 100) if t["possessions"] else None,
            "oreb_rate_per_100_poss": (t["oreb"] / t["possessions"] * 100) if t["possessions"] else None,
        }
    return splits


def output_rate_strong_weak_decomposition(game_ids: List[str], n_sims: int = 50) -> dict:
    records = mcd.build_dev_team_records(game_ids)
    net_rating_by_team_game: Dict[Tuple[str, str], float] = {}
    for r in records:
        net_rating_by_team_game[(r["game_id"], r["team"])] = r["net_rating"]
    real_splits = mcd.real_team_shooting_splits(game_ids)
    sim_splits = simulated_team_extended_splits(game_ids, n_sims=n_sims)

    strong_teams, weak_teams = set(), set()
    by_game: Dict[str, List[Tuple[str, float]]] = {}
    for (gid, team), nr in net_rating_by_team_game.items():
        by_game.setdefault(gid, []).append((team, nr))
    for gid, pairs in by_game.items():
        if len(pairs) != 2:
            continue
        pairs.sort(key=lambda p: -p[1])
        strong_teams.add(pairs[0][0]); weak_teams.add(pairs[1][0])

    def _bucketed(splits, field, teams):
        vals = [splits[t][field] for t in teams if t in splits and splits[t].get(field) is not None]
        return {"n": len(vals), "mean": _mean(vals)}

    out = {}
    for field in ("effective_fg_pct", "ft_rate", "turnover_rate_per_100_poss", "oreb_rate_per_100_poss",
                  "two_pt_pct", "three_pt_pct"):
        splits_source = real_splits if field in ("two_pt_pct", "three_pt_pct", "ft_rate") else sim_splits
        # real splits lack turnover/oreb (no team box score cache) -- simulated-only for those
        sim_strong = _bucketed(sim_splits, field, strong_teams)
        sim_weak = _bucketed(sim_splits, field, weak_teams)
        entry = {
            "simulated_strong_mean": sim_strong["mean"], "simulated_weak_mean": sim_weak["mean"],
            "simulated_gap (strong-weak)": (
                sim_strong["mean"] - sim_weak["mean"] if sim_strong["mean"] is not None and sim_weak["mean"] is not None else None),
        }
        if field in ("two_pt_pct", "three_pt_pct", "ft_rate"):
            real_strong = _bucketed(real_splits, field, strong_teams)
            real_weak = _bucketed(real_splits, field, weak_teams)
            entry["real_strong_mean"] = real_strong["mean"]
            entry["real_weak_mean"] = real_weak["mean"]
            entry["real_gap (strong-weak)"] = (
                real_strong["mean"] - real_weak["mean"] if real_strong["mean"] is not None and real_weak["mean"] is not None else None)
        else:
            entry["real_gap (strong-weak)"] = None
            entry["note"] = "no real per-game team turnover/OREB box-score cache exists in this repo; simulated-only comparison"
        out[field] = entry
    return out
