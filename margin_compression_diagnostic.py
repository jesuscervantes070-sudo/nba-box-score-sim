"""MARGIN COMPRESSION DIAGNOSTIC V1.

A diagnostic/error-decomposition layer, separate from every engine/estimator module and from
`historical_predictive_backtest.py`'s own production evaluation code -- this module NEVER tunes
production parameters. Its one job: trace real NBA team-strength variance from real data, through
player truth composition, through engine inputs, into simulated outputs, and locate WHERE that
variance collapses (the "margin compression" signature found in FIRST HISTORICAL PREDICTIVE
BACKTEST V1: predicted >=20pt games 6.0% vs actual 16.9%, predicted <=5pt games 33.7% vs actual
19.3%).

============================ DEVELOPMENT SAMPLE (separate from BACKTEST_V1_HOLDOUT) ============================
`select_development_games()` uses the SAME mechanical hash-selection scheme as the holdout
(`int(sha256(game_id),16) % 16 == N`) but with N=1 instead of N=0 -- disjoint from
BACKTEST_V1_HOLDOUT BY CONSTRUCTION (a game_id's hash mod 16 cannot equal both 0 and 1), so this
phase's diagnostic experiments never touch, score against, or tune toward the frozen V1 holdout.
Locked to `backtests/margin_compression_dev_game_ids.json` before any diagnostic experiment runs.

============================ WHAT THIS MODULE DOES NOT DO ============================
No probability temperature scaling, no margin scaling, no score correction, no shrinkage/adapter
math changes, no substitutions, no new ingestion. Every measurement here is READ-ONLY over already-
built snapshots/profiles, plus clearly-labeled, isolated counterfactual snapshots used ONLY for
diagnostic simulation batches (never written back, never fed into production).
"""
import functools
import hashlib
import json
import math
import statistics
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import historical_game_snapshot as hgs
import historical_game_outcome as hgo
import historical_predictive_backtest as hpb
import player_rebounding_truth as prt_reb
import player_defensive_truth as pdt
import rebound_engine_adapter as rebound_adapter
import defensive_engine_adapter as defense_adapter
import shot_zone_ingestion as szi
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault
from possession_orchestrator import PlayerSimulationProfile

DEV_SELECTION_MOD = 16
DEV_SELECTION_RESIDUE = 1  # disjoint from HOLDOUT_SELECTION_MOD residue 0
BACKTESTS_DIR = Path("backtests")
ALL_SEASONS = ["2021-22", "2022-23", "2023-24"]
SEASON = hpb.BACKTEST_SEASON

# =====================================================================
# Engine-input field groups (real PlayerSimulationProfile fields only -- no invented OVR)
# =====================================================================
SCORING_FIELDS = ("rim_finishing_shrunk_rate", "floater_short_mid_shrunk_rate", "midrange_shrunk_rate",
                   "three_point_shrunk_rate", "free_throw_shrunk_rate", "rim_access_creation_shrunk_rate")
PLAYMAKING_FIELDS = ("ball_security_error_rate", "passing_accuracy_ast_pct", "playmaking_vision_shrunk_rate")
REBOUNDING_FIELDS = ("offensive_rebounding_shrunk_rate", "defensive_rebounding_shrunk_rate")
DEFENSE_FIELDS = ("poa_containment_shrunk_rate", "rim_protection_suppression_rate", "defensive_playmaking_per36")
ROLE_FIELDS = ("role_off_initiation", "role_off_finishing", "role_off_spacing")
ALL_FIELDS = SCORING_FIELDS + PLAYMAKING_FIELDS + REBOUNDING_FIELDS + DEFENSE_FIELDS + ROLE_FIELDS
# ball_security_error_rate is the one field in this set where LOWER = better (a handling-error
# rate); every other field here is HIGHER = better/more. Kept as a single documented exception
# rather than silently flipping sign anywhere.
LOWER_IS_BETTER_FIELDS = {"ball_security_error_rate"}


def select_development_games(season: str = SEASON, mod: int = DEV_SELECTION_MOD,
                              residue: int = DEV_SELECTION_RESIDUE) -> List[str]:
    path = Path("cache") / season / "schedule.json"
    with open(path) as f:
        games = json.load(f)["games"]
    selected = [g["game_id"] for g in games
                if int(hashlib.sha256(g["game_id"].encode()).hexdigest(), 16) % mod == residue]
    return sorted(selected)


def load_or_create_development_sample(season: str = SEASON) -> List[str]:
    BACKTESTS_DIR.mkdir(exist_ok=True)
    path = BACKTESTS_DIR / "margin_compression_dev_game_ids.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)["game_ids"]
    game_ids = select_development_games(season)
    holdout = set(hpb.load_or_create_holdout(season))
    assert not (set(game_ids) & holdout), "development sample must be disjoint from BACKTEST_V1_HOLDOUT"
    with open(path, "w") as f:
        json.dump({"season": season, "selection_rule": f"sha256(game_id) % {DEV_SELECTION_MOD} == {DEV_SELECTION_RESIDUE}",
                   "n_games": len(game_ids), "game_ids": game_ids}, f, indent=2, sort_keys=True)
    return game_ids


# =====================================================================
# Stats helpers
# =====================================================================
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _sd(xs):
    xs = [x for x in xs if x is not None]
    return statistics.pstdev(xs) if len(xs) > 1 else (0.0 if xs else None)


def _pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs2, ys2 = zip(*pairs)
    mx, my = sum(xs2) / len(xs2), sum(ys2) / len(ys2)
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx = sum((x - mx) ** 2 for x in xs2)
    vy = sum((y - my) ** 2 for y in ys2)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


# =====================================================================
# Team snapshot summarization (C)
# =====================================================================
def summarize_players(players, field: str) -> dict:
    values = [getattr(p.simulation_profile, field) for p in players]
    values_present = [v for v in values if v is not None]
    if not values_present:
        return {"n": 0, "mean": None, "sd": None, "best": None, "worst": None}
    better = max if field not in LOWER_IS_BETTER_FIELDS else min
    worse = min if field not in LOWER_IS_BETTER_FIELDS else max
    best_p = better((p for p in players if getattr(p.simulation_profile, field) is not None),
                     key=lambda p: getattr(p.simulation_profile, field))
    worst_p = worse((p for p in players if getattr(p.simulation_profile, field) is not None),
                     key=lambda p: getattr(p.simulation_profile, field))
    return {
        "n": len(values_present), "mean": _mean(values_present), "sd": _sd(values_present),
        "best": {"player_id": best_p.player_id, "value": getattr(best_p.simulation_profile, field)},
        "worst": {"player_id": worst_p.player_id, "value": getattr(worst_p.simulation_profile, field)},
    }


def summarize_team_snapshot(team_snapshot: hgs.HistoricalTeamSnapshot) -> dict:
    primary = [p for p in team_snapshot.players if p.is_primary_five]
    full_roster = list(team_snapshot.players)
    out = {"team_name": team_snapshot.team_name, "n_primary_five": len(primary), "n_roster": len(full_roster),
           "primary_five": {}, "full_roster": {}}
    for field in ALL_FIELDS:
        out["primary_five"][field] = summarize_players(primary, field)
        out["full_roster"][field] = summarize_players(full_roster, field)
    return out


def provenance_shares(team_snapshot: hgs.HistoricalTeamSnapshot, primary_only: bool = True) -> dict:
    players = [p for p in team_snapshot.players if p.is_primary_five] if primary_only else list(team_snapshot.players)
    counts: Dict[str, Dict[str, int]] = {}
    total_by_group: Dict[str, int] = {}
    for p in players:
        for group, label in p.truth_provenance.items():
            counts.setdefault(group, {})
            counts[group][label] = counts[group].get(label, 0) + 1
            total_by_group[group] = total_by_group.get(group, 0) + 1
    shares = {}
    for group, label_counts in counts.items():
        total = total_by_group[group]
        shares[group] = {label: n / total for label, n in label_counts.items()}
    return shares


# =====================================================================
# Development team records (E/F/G)
# =====================================================================
def build_dev_team_records(game_ids: List[str]) -> List[dict]:
    records = []
    for game_id in game_ids:
        outcome = hgo.get_game_outcome(game_id, SEASON)
        if outcome is None:
            continue
        try:
            snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        home_nr = hpb._team_net_rating_as_of(snap.home_team, snap.game_date, SEASON)
        away_nr = hpb._team_net_rating_as_of(snap.away_team, snap.game_date, SEASON)
        for side_snapshot, team, opp_nr, own_nr, is_home in (
            (snap.home_team_snapshot, snap.home_team, away_nr, home_nr, True),
            (snap.away_team_snapshot, snap.away_team, home_nr, away_nr, False),
        ):
            team_summary = summarize_team_snapshot(side_snapshot)
            team_margin = (outcome.margin if is_home else -outcome.margin)
            records.append({
                "game_id": game_id, "date": snap.game_date, "team": team, "is_home": is_home,
                "net_rating": own_nr, "opponent_net_rating": opp_nr, "strength_diff": own_nr - opp_nr,
                "actual_team_margin": team_margin,
                "primary_five_summary": team_summary["primary_five"],
                "full_roster_summary": team_summary["full_roster"],
                "provenance_primary_five": provenance_shares(side_snapshot, primary_only=True),
                "provenance_full_roster": provenance_shares(side_snapshot, primary_only=False),
            })
    return records


def correlate_fields_with_strength(records: List[dict]) -> dict:
    out = {}
    strengths = [r["net_rating"] for r in records]
    for field in ALL_FIELDS:
        means = [r["primary_five_summary"][field]["mean"] for r in records]
        out[field] = {"n": sum(1 for m in means if m is not None), "pearson_r_vs_net_rating": _pearson(strengths, means)}
    return out


def strong_vs_weak_quartiles(records: List[dict]) -> dict:
    sorted_recs = sorted(records, key=lambda r: r["net_rating"])
    n = len(sorted_recs)
    q = max(1, n // 4)
    weak, strong = sorted_recs[:q], sorted_recs[-q:]
    out = {"n_weak": len(weak), "n_strong": len(strong),
           "mean_net_rating_weak": _mean([r["net_rating"] for r in weak]),
           "mean_net_rating_strong": _mean([r["net_rating"] for r in strong])}
    for field in ALL_FIELDS:
        weak_mean = _mean([r["primary_five_summary"][field]["mean"] for r in weak])
        strong_mean = _mean([r["primary_five_summary"][field]["mean"] for r in strong])
        out[field] = {"weak_mean": weak_mean, "strong_mean": strong_mean,
                       "diff (strong-weak)": (strong_mean - weak_mean) if (weak_mean is not None and strong_mean is not None) else None}
    return out


# =====================================================================
# Year-over-year truth lag (G)
# =====================================================================
def year_over_year_truth_lag(records: List[dict]) -> dict:
    """Teams whose real net rating changed a lot from the PRIOR season vs THIS season-to-date --
    do PRIOR_SEASON_ONLY-heavy fields fail to reflect that real change?"""
    prior_team_avg = hpb._prior_season_team_avg_diff(hpb.PRIOR_SEASON)
    by_team: Dict[str, List[dict]] = {}
    for r in records:
        by_team.setdefault(r["team"], []).append(r)
    rows = []
    for team, recs in by_team.items():
        if team not in prior_team_avg:
            continue
        cur_nr = _mean([r["net_rating"] for r in recs])
        delta = cur_nr - prior_team_avg[team]
        rows.append({"team": team, "prior_season_net_rating": prior_team_avg[team],
                     "current_season_net_rating": cur_nr, "delta": delta, "n_games": len(recs)})
    rows.sort(key=lambda r: -abs(r["delta"]))
    top_movers = rows[:8]
    return {"n_teams": len(rows), "top_movers_by_abs_delta": top_movers,
            "pearson_delta_vs_current_nr": _pearson([r["delta"] for r in rows], [r["current_season_net_rating"] for r in rows])}


# =====================================================================
# Fixed-five / bench information loss (H/I)
# =====================================================================
def fixed_five_information_loss(game_ids: List[str]) -> List[dict]:
    rows = []
    for game_id in game_ids:
        try:
            oracle_snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_ORACLE_PARTICIPANTS)
        except Exception:
            continue
        outcome = hgo.get_game_outcome(game_id, SEASON)
        if outcome is None:
            continue
        for team_snap, is_home in ((oracle_snap.home_team_snapshot, True), (oracle_snap.away_team_snapshot, False)):
            primary = [p for p in team_snap.players if p.is_primary_five]
            bench = [p for p in team_snap.players if not p.is_primary_five]
            top5_minutes = sum(p.expected_minutes for p in primary)
            bench_minutes = sum(p.expected_minutes for p in bench)
            total_minutes = top5_minutes + bench_minutes
            top5_quality = {f: _mean([getattr(p.simulation_profile, f) for p in primary]) for f in SCORING_FIELDS}
            bench_quality = {f: _mean([getattr(p.simulation_profile, f) for p in bench]) for f in SCORING_FIELDS}
            # rotation-weighted (real minutes-weighted) profile vs top-5-only, for one representative field
            all_players = primary + bench
            weighted_3pt = (sum(getattr(p.simulation_profile, "three_point_shrunk_rate", 0) * p.expected_minutes
                                 for p in all_players if getattr(p.simulation_profile, "three_point_shrunk_rate", None) is not None)
                            / total_minutes) if total_minutes else None
            rows.append({
                "game_id": game_id, "team": team_snap.team_name, "is_home": is_home,
                "n_bench_players": len(bench), "top5_minutes": top5_minutes, "bench_minutes": bench_minutes,
                "top5_minute_share": (top5_minutes / total_minutes) if total_minutes else None,
                "bench_minute_share": (bench_minutes / total_minutes) if total_minutes else None,
                "top5_quality": top5_quality, "bench_quality": bench_quality,
                "rotation_weighted_three_point": weighted_3pt,
                "top5_only_three_point": top5_quality.get("three_point_shrunk_rate"),
                "actual_team_margin": outcome.margin if is_home else -outcome.margin,
                "net_rating": hpb._team_net_rating_as_of(team_snap.team_name, oracle_snap.game_date, SEASON),
            })
    return rows


def rotation_weighted_vs_top5_correlation(rows: List[dict]) -> dict:
    net_ratings = [r["net_rating"] for r in rows]
    rotation_weighted = [r["rotation_weighted_three_point"] for r in rows]
    top5_only = [r["top5_only_three_point"] for r in rows]
    return {
        "n": len(rows),
        "corr_top5_only_three_point_vs_net_rating": _pearson(top5_only, net_ratings),
        "corr_rotation_weighted_three_point_vs_net_rating": _pearson(rotation_weighted, net_ratings),
        "mean_bench_minute_share": _mean([r["bench_minute_share"] for r in rows]),
    }


# =====================================================================
# Controlled counterfactuals (J/K)
# =====================================================================
def _batch_margin(home_id, away_id, home_five, away_five, profiles, game_id, mode_label, n_sims=100):
    margins = []
    faults = 0
    for i in range(n_sims):
        seed = int(hashlib.sha256(f"diagnostic|{mode_label}|{game_id}|{i}".encode()).hexdigest()[:16], 16)
        try:
            result = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
        except DetailedGameSimulationFault:
            faults += 1
            continue
        if result.final_home_score == result.final_away_score:
            faults += 1
            continue
        margins.append(result.final_home_score - result.final_away_score)
    return margins, faults


def player_replacement_counterfactual(game_id: str, replace_side: str, replace_player_id: str, n_sims: int = 100) -> dict:
    """Replaces ONE primary-five player's profile with a league-average synthetic profile (same
    player_id/team_id, all other fields at PlayerSimulationProfile.synthetic() defaults) and
    compares simulated margin/win-prob/score against the real, unmodified snapshot. Diagnostic
    ONLY -- never writes back to any snapshot or production cache."""
    snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
    home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)

    baseline_margins, _ = _batch_margin(home_id, away_id, home_five, away_five, profiles, game_id, "baseline", n_sims)

    replaced_profiles = dict(profiles)
    side_id = replace_side
    replaced_profiles[replace_player_id] = PlayerSimulationProfile.synthetic(replace_player_id, side_id)
    counterfactual_margins, _ = _batch_margin(home_id, away_id, home_five, away_five, replaced_profiles,
                                               game_id, f"replace_{replace_player_id}", n_sims)

    return {
        "game_id": game_id, "replaced_player_id": replace_player_id, "replace_side": replace_side,
        "n_sims": n_sims,
        "baseline_mean_margin": _mean(baseline_margins), "counterfactual_mean_margin": _mean(counterfactual_margins),
        "margin_shift": (_mean(counterfactual_margins) - _mean(baseline_margins))
        if baseline_margins and counterfactual_margins else None,
        "baseline_home_win_prob": (sum(1 for m in baseline_margins if m > 0) / len(baseline_margins)) if baseline_margins else None,
        "counterfactual_home_win_prob": (sum(1 for m in counterfactual_margins if m > 0) / len(counterfactual_margins)) if counterfactual_margins else None,
    }


# =====================================================================
# Offense/defense output variance across teams (L/M)
# =====================================================================
def real_team_shooting_splits(game_ids: List[str]) -> Dict[str, dict]:
    """Real per-team shooting splits (2P%, 3P%, FT rate) aggregated from real per-player game logs
    for the given games -- the same real summation logic as historical_game_outcome.py, extended
    to shot-mix detail."""
    from player_game_log_ingestion import load_player_game_log
    from player_team_stints import team_as_of_date
    log = load_player_game_log(SEASON)
    by_game = {}
    for pid, rows in log.items():
        for row in rows:
            by_game.setdefault(row["game_id"], []).append((pid, row))

    team_totals: Dict[str, dict] = {}
    for game_id in game_ids:
        meta_rows = by_game.get(game_id, [])
        if not meta_rows:
            continue
        from game_metadata import get_game_metadata
        meta = get_game_metadata(game_id, SEASON)
        if meta is None:
            continue
        for pid, row in meta_rows:
            team = team_as_of_date(pid, meta.game_date, SEASON)
            if team not in (meta.home_team, meta.away_team):
                continue
            t = team_totals.setdefault(team, {"fgm": 0, "fga": 0, "fg3m": 0, "fg3a": 0, "ftm": 0, "fta": 0})
            for k in t:
                t[k] += row.get(k, 0) or 0
    splits = {}
    for team, t in team_totals.items():
        fg2m, fg2a = t["fgm"] - t["fg3m"], t["fga"] - t["fg3a"]
        splits[team] = {
            "two_pt_pct": (fg2m / fg2a) if fg2a else None,
            "three_pt_pct": (t["fg3m"] / t["fg3a"]) if t["fg3a"] else None,
            "ft_rate": (t["fta"] / t["fga"]) if t["fga"] else None,
        }
    return splits


def simulated_team_shooting_splits(game_ids: List[str], n_sims: int = 50) -> Dict[str, dict]:
    """Aggregate simulated shot-mix splits per team across a small batch per game (kept small --
    this is a diagnostic scan across many games, not a per-game precision estimate)."""
    team_totals: Dict[str, dict] = {}
    for game_id in game_ids:
        try:
            snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        for i in range(n_sims):
            seed = int(hashlib.sha256(f"shooting-splits|{game_id}|{i}".encode()).hexdigest()[:16], 16)
            try:
                result = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
            except DetailedGameSimulationFault:
                continue
            for team_name, team_id in ((snap.home_team, home_id), (snap.away_team, away_id)):
                t = team_totals.setdefault(team_name, {"fgm": 0, "fga": 0, "fg3m": 0, "fg3a": 0, "ftm": 0, "fta": 0})
            # aggregate from possession records for each side
            for rec in result.possessions:
                off_team = snap.home_team if rec.offense_team_id == home_id else snap.away_team
                d = rec.provisional_deltas
                t = team_totals[off_team]
                t["fgm"] += d.fgm; t["fga"] += d.fga; t["fg3m"] += d.fg3m; t["fg3a"] += d.fg3a
                t["ftm"] += d.ftm; t["fta"] += d.fta
    splits = {}
    for team, t in team_totals.items():
        fg2m, fg2a = t["fgm"] - t["fg3m"], t["fga"] - t["fg3a"]
        splits[team] = {
            "two_pt_pct": (fg2m / fg2a) if fg2a else None,
            "three_pt_pct": (t["fg3m"] / t["fg3a"]) if t["fg3a"] else None,
            "ft_rate": (t["fta"] / t["fga"]) if t["fga"] else None,
        }
    return splits


# =====================================================================
# Pace / total-possessions variance (N)
# =====================================================================
def simulated_possessions_variance(game_ids: List[str], n_sims: int = 30) -> dict:
    per_game_means = []
    for game_id in game_ids:
        try:
            snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        counts = []
        for i in range(n_sims):
            seed = int(hashlib.sha256(f"pace|{game_id}|{i}".encode()).hexdigest()[:16], 16)
            try:
                result = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
            except DetailedGameSimulationFault:
                continue
            counts.append(result.total_possessions)
        if counts:
            per_game_means.append(_mean(counts))
    return {"n_games": len(per_game_means), "mean_possessions_across_games": _mean(per_game_means),
            "sd_possessions_across_games": _sd(per_game_means)}


# =====================================================================
# Raw -> truth -> engine variance transfer (O)
# =====================================================================
def raw_truth_engine_variance_transfer(records: List[dict], season: str = SEASON) -> dict:
    """For three_point_shrunk_rate: RAW = real, unshrunk single-season 3PT% across qualified
    players (from shot_zone_ingestion, min 20 3PA); TRUTH/ENGINE = the already-collected primary-
    five engine-input values from the dev sample's own team snapshots (post-shrinkage, post-
    composition)."""
    zone_data = szi.load_shot_zones(season)
    raw_values = []
    for row in zone_data.values():
        fga = row.get("above_break3_fga", 0) + row.get("left_corner3_fga", 0) + row.get("right_corner3_fga", 0)
        fgm = row.get("above_break3_fgm", 0) + row.get("left_corner3_fgm", 0) + row.get("right_corner3_fgm", 0)
        if fga and fga >= 20:
            raw_values.append(fgm / fga)
    engine_values = []
    for r in records:
        m = r["primary_five_summary"]["three_point_shrunk_rate"]["mean"]
        if m is not None:
            engine_values.append(m)
    return {
        "raw_population_n": len(raw_values), "raw_population_sd": _sd(raw_values), "raw_population_mean": _mean(raw_values),
        "team_primary_five_engine_input_n": len(engine_values),
        "team_primary_five_engine_input_sd": _sd(engine_values), "team_primary_five_engine_input_mean": _mean(engine_values),
        "note": "raw is PLAYER-level population SD; engine-input is TEAM-primary-five-MEAN SD -- "
                "team means are expected to be tighter than the raw player population by construction "
                "(averaging 5 players reduces variance); the comparison here is about whether real team-quality "
                "separation SURVIVES into team-level engine inputs, not a like-for-like SD ratio.",
    }


# =====================================================================
# Adapter compression check (Q)
# =====================================================================
def adapter_compression_check(dev_records_players: List[Tuple[str, str, str]]) -> dict:
    """For a sample of real (player_id, as_of_date, as_of_season) triples, compares
    rebounding/defensive-playmaking TRUTH source_value spread against the adapted ENGINE_VALUE
    spread, and rank-correlation between the two (adapters may compress magnitude while preserving
    rank -- a materially different finding than compressing rank itself)."""
    reb_pairs = {"offensive_rebounding": [], "defensive_rebounding": []}
    def_pairs = []
    for player_id, as_of_date, as_of_season in dev_records_players:
        reb_truth = prt_reb.build_rebounding_truth_profile_as_of_date(player_id, as_of_date, as_of_season, ALL_SEASONS)
        for attr in reb_pairs:
            est = reb_truth.estimates.get(attr)
            if est is None:
                continue
            adapted = rebound_adapter.adapt_rebounding_estimate(est)
            if adapted is not None:
                reb_pairs[attr].append((adapted.source_value, adapted.engine_value))
        def_truth = pdt.build_defensive_truth_profile_as_of_date(player_id, as_of_date, as_of_season, ALL_SEASONS)
        est = def_truth.estimates.get("defensive_playmaking")
        if est is not None:
            adapted = defense_adapter.adapt_defensive_playmaking_estimate(est)
            if adapted is not None:
                def_pairs.append((adapted.source_value, adapted.engine_value))

    def _pair_stats(pairs):
        if len(pairs) < 3:
            return {"n": len(pairs), "source_sd": None, "engine_sd": None, "rank_corr": None}
        sources = [p[0] for p in pairs]
        engines = [p[1] for p in pairs]
        return {"n": len(pairs), "source_sd": _sd(sources), "engine_sd": _sd(engines),
                "rank_corr (pearson, proxy)": _pearson(sources, engines)}

    return {
        "offensive_rebounding": _pair_stats(reb_pairs["offensive_rebounding"]),
        "defensive_rebounding": _pair_stats(reb_pairs["defensive_rebounding"]),
        "defensive_playmaking": _pair_stats(def_pairs),
    }


# =====================================================================
# Role amplification (R) -- isolated synthetic diagnostic, no real snapshot involved
# =====================================================================
def role_amplification_diagnostic(n_sims: int = 100) -> dict:
    """Isolated, fully-synthetic 5v5 diagnostic (NOT a real historical game): one team's five
    players are identical league-average synthetics EXCEPT one "elite scorer" whose scoring
    fields are set far above the population (0.95 across shot families). Compares simulated team
    offensive output when that elite scorer's ROLE fields are (a) at synthetic defaults (neutral
    role) vs (b) set to a high role_off_finishing/spacing (heavily featured). Defense on both
    sides is held at synthetic-average. Purely diagnostic; profiles are constructed here and
    discarded, never written to any cache."""
    # Fake but VALID-SHAPED numeric-string ids (the engine's own input validation requires a real,
    # stable-looking numeric player_id string, never a name-keyed placeholder) -- these are
    # constructed-for-this-diagnostic-only ids, never resolved against real player identity.
    HOME = [str(900001 + i) for i in range(5)]
    AWAY = [str(900011 + i) for i in range(5)]
    profiles = {}
    for pid in AWAY:
        profiles[pid] = PlayerSimulationProfile.synthetic(pid, "AWAY")
    elite_id = HOME[0]
    elite_kwargs = dict(rim_finishing_shrunk_rate=0.85, floater_short_mid_shrunk_rate=0.65,
                         midrange_shrunk_rate=0.70, three_point_shrunk_rate=0.55, free_throw_shrunk_rate=0.92)
    for pid in HOME[1:]:
        profiles[pid] = PlayerSimulationProfile.synthetic(pid, "HOME")

    results = {}
    for label, role_kwargs in (
        ("neutral_role", {}),
        ("high_role", dict(role_off_finishing=0.85, role_off_spacing=0.75, role_off_initiation=8.0)),
    ):
        profiles_variant = dict(profiles)
        profiles_variant[elite_id] = PlayerSimulationProfile.synthetic(elite_id, "HOME", **elite_kwargs, **role_kwargs)
        margins, _ = _batch_margin("HOME", "AWAY", tuple(HOME), tuple(AWAY), profiles_variant,
                                    "role_amp_diag", label, n_sims)
        results[label] = {"mean_margin": _mean(margins), "n_valid": len(margins)}
    return {
        "neutral_role_mean_margin": results["neutral_role"]["mean_margin"],
        "high_role_mean_margin": results["high_role"]["mean_margin"],
        "margin_shift_from_role_amplification": (
            results["high_role"]["mean_margin"] - results["neutral_role"]["mean_margin"]
            if results["neutral_role"]["mean_margin"] is not None and results["high_role"]["mean_margin"] is not None else None
        ),
    }


# =====================================================================
# Monte Carlo noise check (S)
# =====================================================================
def monte_carlo_noise_check(game_ids: List[str], counts=(250, 1000)) -> List[dict]:
    rows = []
    for game_id in game_ids:
        try:
            snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        row = {"game_id": game_id}
        for n in counts:
            margins, _ = _batch_margin(home_id, away_id, home_five, away_five, profiles, game_id, f"noise_n{n}", n)
            row[f"n{n}_mean_margin"] = _mean(margins)
            row[f"n{n}_margin_sd"] = _sd(margins)
        rows.append(row)
    return rows


# =====================================================================
# Matchup-strength compression curve (T)
# =====================================================================
def matchup_strength_compression_curve(game_ids: List[str], n_sims: int = 50) -> List[dict]:
    buckets = {"near_equal (<3)": [], "modest (3-8)": [], "large (8-15)": [], "extreme (>=15)": []}
    for game_id in game_ids:
        outcome = hgo.get_game_outcome(game_id, SEASON)
        if outcome is None:
            continue
        try:
            snap = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        home_nr = hpb._team_net_rating_as_of(snap.home_team, snap.game_date, SEASON)
        away_nr = hpb._team_net_rating_as_of(snap.away_team, snap.game_date, SEASON)
        diff = abs(home_nr - away_nr)
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        margins, _ = _batch_margin(home_id, away_id, home_five, away_five, profiles, game_id, "compression_curve", n_sims)
        if not margins:
            continue
        sim_abs_margin = abs(_mean(margins))
        actual_abs_margin = abs(outcome.margin)
        bucket = ("near_equal (<3)" if diff < 3 else "modest (3-8)" if diff < 8
                  else "large (8-15)" if diff < 15 else "extreme (>=15)")
        buckets[bucket].append((actual_abs_margin, sim_abs_margin, diff))
    out = []
    for name, rows in buckets.items():
        if not rows:
            out.append({"bucket": name, "n_games": 0}); continue
        out.append({
            "bucket": name, "n_games": len(rows),
            "mean_abs_strength_diff": _mean([r[2] for r in rows]),
            "mean_actual_abs_margin": _mean([r[0] for r in rows]),
            "mean_simulated_abs_margin": _mean([r[1] for r in rows]),
        })
    return out


# =====================================================================
# Home-court contribution estimate (U) -- measurement only, nothing implemented
# =====================================================================
def home_court_contribution_estimate(records: List[dict]) -> dict:
    """Real home margin minus the net-rating-implied margin, averaged -- a real, leak-safe,
    descriptive estimate of home-court's point contribution NOT explained by team-strength alone.
    Measurement only; nothing implemented in the engine."""
    home_only = [r for r in records if r["is_home"]]
    implied_vs_actual = [(r["strength_diff"], r["actual_team_margin"]) for r in home_only]
    residuals = [actual - implied for implied, actual in implied_vs_actual]
    return {
        "n_home_games": len(home_only),
        "mean_home_margin_residual_over_net_rating (points)": _mean(residuals),
        "note": "positive => real home teams outperform their net-rating-implied margin by this many "
                "points on average (a real home-court signal); the engine models no such effect.",
    }
