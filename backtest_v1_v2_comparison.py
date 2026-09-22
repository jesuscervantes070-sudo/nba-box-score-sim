"""FROZEN HISTORICAL BACKTEST RE-EVALUATION V2 -- paired V1-vs-V2 analysis. Reads the already-
written backtests/backtest_v1_games.json (untouched, prior phase) and backtest_v2_games.json (this
phase), pairs games by game_id, and computes every comparison the task calls for. Read-only over
both raw result files -- never mutates either. No estimator/engine code is touched here."""
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List

import historical_predictive_backtest as hpb

BACKTESTS_DIR = Path("backtests")
BOOTSTRAP_SEED = 20260921  # fixed, deterministic -- documented, never re-rolled after seeing results
N_BOOTSTRAP = 10000


def load_games(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def paired_game_records(v1_pregame_games: List[dict], v2_pregame_games: List[dict]) -> List[dict]:
    """One record per game present in BOTH V1 and V2 (should be all 83 -- both used the identical,
    unmodified holdout list); each carries the real actual outcome (identical in both, sourced
    from the same real historical_game_outcome data) plus every V1/V2 prediction field needed for
    downstream deltas."""
    v1_by_id = {g["game_id"]: g for g in v1_pregame_games}
    v2_by_id = {g["game_id"]: g for g in v2_pregame_games}
    common_ids = sorted(set(v1_by_id) & set(v2_by_id))
    records = []
    for gid in common_ids:
        g1, g2 = v1_by_id[gid], v2_by_id[gid]
        assert g1["actual_home_score"] == g2["actual_home_score"], f"{gid}: actual outcome mismatch between V1/V2 (evaluation drift!)"
        assert g1["actual_away_score"] == g2["actual_away_score"], f"{gid}: actual outcome mismatch between V1/V2 (evaluation drift!)"
        y = 1 if g1["actual_winner"] == g1["home_team"] else 0
        p1, p2 = g1["predicted_home_win_prob"], g2["predicted_home_win_prob"]
        m1, m2 = g1["predicted_mean_margin"], g2["predicted_mean_margin"]
        actual_margin = g1["actual_margin"]
        records.append({
            "game_id": gid, "home_team": g1["home_team"], "away_team": g1["away_team"], "date": g1["date"],
            "actual_home_win": y, "actual_margin": actual_margin,
            "actual_home_score": g1["actual_home_score"], "actual_away_score": g1["actual_away_score"],
            "v1_home_win_prob": p1, "v2_home_win_prob": p2, "delta_win_prob": p2 - p1,
            "v1_margin": m1, "v2_margin": m2, "delta_margin": m2 - m1,
            "v1_home_score": g1["mean_simulated_home_score"], "v2_home_score": g2["mean_simulated_home_score"],
            "delta_home_score": g2["mean_simulated_home_score"] - g1["mean_simulated_home_score"],
            "v1_away_score": g1["mean_simulated_away_score"], "v2_away_score": g2["mean_simulated_away_score"],
            "delta_away_score": g2["mean_simulated_away_score"] - g1["mean_simulated_away_score"],
            "v1_brier": (p1 - y) ** 2, "v2_brier": (p2 - y) ** 2,
            "delta_brier": (p2 - y) ** 2 - (p1 - y) ** 2,
            "v1_abs_margin_error": abs(m1 - actual_margin), "v2_abs_margin_error": abs(m2 - actual_margin),
            "delta_abs_margin_error": abs(m2 - actual_margin) - abs(m1 - actual_margin),
            "v1_provenance": g1.get("snapshot_provenance", {}), "v2_provenance": g2.get("snapshot_provenance", {}),
        })
    return records


def paired_deltas_summary(records: List[dict]) -> dict:
    fields = ("delta_win_prob", "delta_margin", "delta_home_score", "delta_away_score", "delta_brier", "delta_abs_margin_error")
    out = {"n_games": len(records)}
    for f in fields:
        vals = [r[f] for r in records]
        out[f] = {"mean": statistics.mean(vals), "median": statistics.median(vals),
                   "sd": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
                   "min": min(vals), "max": max(vals)}
    return out


# =====================================================================
# Deterministic paired bootstrap
# =====================================================================
def _rng_stream(seed: int):
    """A tiny, dependency-free, fully deterministic PRNG (no reliance on Python's `random` module
    internals changing across versions) -- xorshift64, seeded once, consumed sequentially."""
    state = seed & 0xFFFFFFFFFFFFFFFF or 0x9E3779B97F4A7C15
    while True:
        state ^= (state << 13) & 0xFFFFFFFFFFFFFFFF
        state ^= (state >> 7)
        state ^= (state << 17) & 0xFFFFFFFFFFFFFFFF
        yield state / 0xFFFFFFFFFFFFFFFF


def paired_bootstrap_ci(records: List[dict], seed: int = BOOTSTRAP_SEED, n_boot: int = N_BOOTSTRAP) -> dict:
    """Deterministic paired bootstrap (resample GAME INDICES with replacement -- preserves the V1/
    V2 pairing within each resampled game) for Brier delta, margin-MAE delta, and accuracy delta.
    Fixed seed, documented, never re-rolled after seeing results."""
    n = len(records)
    rng = _rng_stream(seed)
    brier_deltas, mae_deltas, acc_deltas = [], [], []
    idx_pool = list(range(n))
    for _ in range(n_boot):
        sample_idx = [idx_pool[int(next(rng) * n)] for _ in range(n)]
        sample = [records[i] for i in sample_idx]
        brier_deltas.append(statistics.mean(r["delta_brier"] for r in sample))
        mae_deltas.append(statistics.mean(r["delta_abs_margin_error"] for r in sample))
        v1_correct = sum(1 for r in sample if (r["v1_home_win_prob"] >= 0.5) == bool(r["actual_home_win"]))
        v2_correct = sum(1 for r in sample if (r["v2_home_win_prob"] >= 0.5) == bool(r["actual_home_win"]))
        acc_deltas.append((v2_correct - v1_correct) / n)

    def _ci(vals):
        s = sorted(vals)
        lo = s[int(0.025 * len(s))]
        hi = s[int(0.975 * len(s)) - 1]
        return {"mean": statistics.mean(vals), "ci_2_5": lo, "ci_97_5": hi}

    return {
        "n_bootstrap": n_boot, "seed": seed,
        "brier_delta": _ci(brier_deltas), "margin_mae_delta": _ci(mae_deltas), "accuracy_delta": _ci(acc_deltas),
    }


# =====================================================================
# Margin-compression re-measurement
# =====================================================================
def margin_compression_summary(games: List[dict]) -> dict:
    pred_margins = [g["predicted_mean_margin"] for g in games]
    actual_margins = [g["actual_margin"] for g in games]
    n = len(games)
    return {
        "n_games": n,
        "predicted_ge20pt_rate": sum(1 for m in pred_margins if abs(m) >= 20) / n,
        "actual_ge20pt_rate": sum(1 for m in actual_margins if abs(m) >= 20) / n,
        "predicted_le5pt_rate": sum(1 for m in pred_margins if abs(m) <= 5) / n,
        "actual_le5pt_rate": sum(1 for m in actual_margins if abs(m) <= 5) / n,
        "predicted_margin_sd": statistics.pstdev(pred_margins) if n > 1 else 0.0,
        "actual_margin_sd": statistics.pstdev(actual_margins) if n > 1 else 0.0,
        "mean_abs_predicted_margin": statistics.mean(abs(m) for m in pred_margins),
        "mean_abs_actual_margin": statistics.mean(abs(m) for m in actual_margins),
    }


# =====================================================================
# Strength -> margin correlation
# =====================================================================
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


def strength_margin_correlation(games: List[dict], season: str = hpb.BACKTEST_SEASON) -> dict:
    strengths, margins = [], []
    for g in games:
        home_nr = hpb._team_net_rating_as_of(g["home_team"], g["date"], season)
        away_nr = hpb._team_net_rating_as_of(g["away_team"], g["date"], season)
        strengths.append(home_nr - away_nr)
        margins.append(g["predicted_mean_margin"])
    return {"n_games": len(games), "pearson_r_net_rating_diff_vs_predicted_margin": _pearson(strengths, margins)}


# =====================================================================
# Matchup-strength buckets
# =====================================================================
def matchup_strength_buckets(v1_games: List[dict], v2_games: List[dict], season: str = hpb.BACKTEST_SEASON) -> List[dict]:
    v1_by_id = {g["game_id"]: g for g in v1_games}
    v2_by_id = {g["game_id"]: g for g in v2_games}
    common = sorted(set(v1_by_id) & set(v2_by_id))
    buckets = {"near_equal (<3)": [], "modest (3-8)": [], "large (8-15)": [], "extreme (>=15)": []}
    for gid in common:
        g1, g2 = v1_by_id[gid], v2_by_id[gid]
        home_nr = hpb._team_net_rating_as_of(g1["home_team"], g1["date"], season)
        away_nr = hpb._team_net_rating_as_of(g1["away_team"], g1["date"], season)
        diff = abs(home_nr - away_nr)
        bucket = ("near_equal (<3)" if diff < 3 else "modest (3-8)" if diff < 8
                  else "large (8-15)" if diff < 15 else "extreme (>=15)")
        buckets[bucket].append((abs(g1["actual_margin"]), abs(g1["predicted_mean_margin"]), abs(g2["predicted_mean_margin"])))
    out = []
    for name, rows in buckets.items():
        if not rows:
            out.append({"bucket": name, "n_games": 0})
            continue
        out.append({
            "bucket": name, "n_games": len(rows),
            "mean_actual_abs_margin": statistics.mean(r[0] for r in rows),
            "mean_v1_simulated_abs_margin": statistics.mean(r[1] for r in rows),
            "mean_v2_simulated_abs_margin": statistics.mean(r[2] for r in rows),
        })
    return out


# =====================================================================
# Calibration comparison
# =====================================================================
def calibration_comparison(v1_games: List[dict], v2_games: List[dict]) -> dict:
    def pairs_from(games):
        return [(g["predicted_home_win_prob"], 1 if g["actual_winner"] == g["home_team"] else 0) for g in games]
    v1_bins = hpb.calibration_table(pairs_from(v1_games))
    v2_bins = hpb.calibration_table(pairs_from(v2_games))
    return {"v1_bins": v1_bins, "v2_bins": v2_bins}


# =====================================================================
# ORACLE V1 -> V2
# =====================================================================
def oracle_comparison(v1_oracle_games: List[dict], v2_oracle_games: List[dict]) -> dict:
    def metrics_from(games):
        pairs = [(g["predicted_home_win_prob"], 1 if g["actual_winner"] == g["home_team"] else 0) for g in games]
        pred_margins = [g["predicted_mean_margin"] for g in games]
        actual_margins = [g["actual_margin"] for g in games]
        return {
            "n_games": len(games), "brier_score": hpb.brier_score(pairs), "log_loss": hpb.log_loss(pairs),
            "winner_accuracy": hpb.winner_accuracy(pairs),
            "margin_mae": hpb.margin_mae(pred_margins, actual_margins),
        }
    v1m, v2m = metrics_from(v1_oracle_games), metrics_from(v2_oracle_games)
    return {
        "v1_oracle": v1m, "v2_oracle": v2m,
        "delta_brier": v2m["brier_score"] - v1m["brier_score"] if v1m["brier_score"] is not None and v2m["brier_score"] is not None else None,
        "delta_accuracy": v2m["winner_accuracy"] - v1m["winner_accuracy"] if v1m["winner_accuracy"] is not None and v2m["winner_accuracy"] is not None else None,
        "delta_margin_mae": v2m["margin_mae"] - v1m["margin_mae"] if v1m["margin_mae"] is not None and v2m["margin_mae"] is not None else None,
    }


# =====================================================================
# Period comparison (V1 vs V2 by opening/midseason/late season)
# =====================================================================
def _period_of(date: str) -> str:
    if date < "2023-12-01":
        return "opening (Oct-Nov)"
    if date < "2024-02-15":
        return "midseason (Dec-mid Feb)"
    return "late season (mid Feb-Apr)"


def period_comparison(records: List[dict]) -> dict:
    groups: Dict[str, List[dict]] = {}
    for r in records:
        groups.setdefault(_period_of(r["date"]), []).append(r)
    out = {}
    for period, recs in sorted(groups.items()):
        out[period] = {
            "n_games": len(recs),
            "mean_delta_brier": statistics.mean(r["delta_brier"] for r in recs),
            "mean_delta_abs_margin_error": statistics.mean(r["delta_abs_margin_error"] for r in recs),
        }
    return out


# =====================================================================
# Team-level improvement/regression
# =====================================================================
def team_level_comparison(records: List[dict]) -> List[dict]:
    by_team: Dict[str, List[float]] = {}
    for r in records:
        by_team.setdefault(r["home_team"], []).append(r["delta_brier"])
        by_team.setdefault(r["away_team"], []).append(r["delta_brier"])
    out = [{"team": t, "n_games": len(v), "mean_delta_brier": statistics.mean(v)} for t, v in by_team.items()]
    out.sort(key=lambda r: r["mean_delta_brier"])
    return out


# =====================================================================
# Top improvements/regressions
# =====================================================================
def intervention_exposure(game_ids: List[str], season: str = hpb.BACKTEST_SEASON,
                           all_seasons=None) -> dict:
    """For each holdout game's primary-five players (both sides), directly re-inspects the
    per-attribute truth (NOT the group-level `snapshot_provenance` summary, which is too coarse --
    a group reading "MIXED" doesn't say WHICH field is current-season) to count real exposure to
    each of the 5 refreshed/resolved targets: CURRENT_SEASON rim_protection, CURRENT_SEASON
    role_off_finishing/spacing, and non-default (real) passing_accuracy_ast_pct/
    rim_access_creation_shrunk_rate. Rebuilds snapshots in-process (reusing warm reference-
    population caches) -- descriptive only, no engine call."""
    import historical_game_snapshot as hgs
    import player_defensive_truth as pdt
    import player_role_truth as prt_role
    import player_scoring_truth_temporal as psst

    all_seasons = all_seasons or ["2021-22", "2022-23", "2023-24"]
    rows = []
    for game_id in game_ids:
        try:
            snap = hgs.build_historical_game_snapshot(game_id, season, all_seasons, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        for team_snap in (snap.home_team_snapshot, snap.away_team_snapshot):
            primary = [p for p in team_snap.players if p.is_primary_five]
            n_current_rim, n_current_fin, n_current_spc = 0, 0, 0
            n_real_ast_pct, n_real_rim_access = 0, 0
            for p in primary:
                def_t = pdt.build_defensive_truth_profile_as_of_date(p.player_id, snap.game_date, season, all_seasons)
                if def_t.estimates["rim_protection"].provenance == psst.CURRENT_SEASON_PREGAME:
                    n_current_rim += 1
                role_t = prt_role.build_role_truth_profile_as_of_date(p.player_id, snap.game_date, season)
                if role_t.estimates["role_off_finishing"].provenance == psst.CURRENT_SEASON_PREGAME:
                    n_current_fin += 1
                if role_t.estimates["role_off_spacing"].provenance == psst.CURRENT_SEASON_PREGAME:
                    n_current_spc += 1
                if p.simulation_profile.passing_accuracy_ast_pct != 0.18:
                    n_real_ast_pct += 1
                if p.simulation_profile.rim_access_creation_shrunk_rate != 0.5:
                    n_real_rim_access += 1
            rows.append({
                "game_id": game_id, "team": team_snap.team_name,
                "n_primary_five": len(primary),
                "n_current_season_rim_protection": n_current_rim,
                "n_current_season_finishing": n_current_fin,
                "n_current_season_spacing": n_current_spc,
                "n_real_ast_pct": n_real_ast_pct,
                "n_real_rim_access_creation": n_real_rim_access,
            })
    n_teams = len(rows)
    if n_teams == 0:
        return {"n_team_games": 0}
    return {
        "n_team_games": n_teams,
        "mean_current_season_rim_protection_per_team": statistics.mean(r["n_current_season_rim_protection"] for r in rows),
        "mean_current_season_finishing_per_team": statistics.mean(r["n_current_season_finishing"] for r in rows),
        "mean_current_season_spacing_per_team": statistics.mean(r["n_current_season_spacing"] for r in rows),
        "mean_real_ast_pct_per_team": statistics.mean(r["n_real_ast_pct"] for r in rows),
        "mean_real_rim_access_creation_per_team": statistics.mean(r["n_real_rim_access_creation"] for r in rows),
        "rows_sample": rows[:10],
        "_rows": rows,
    }


def exposure_vs_improvement(exposure_rows: List[dict], records: List[dict]) -> dict:
    """Descriptive only: do higher-exposure GAMES (more refreshed-field primary-five players)
    improve more (lower delta_brier) than lower-exposure games? No causal claim from this sample
    size."""
    by_game: Dict[str, int] = {}
    for r in exposure_rows:
        by_game[r["game_id"]] = by_game.get(r["game_id"], 0) + (
            r["n_current_season_rim_protection"] + r["n_current_season_finishing"] + r["n_current_season_spacing"])
    pairs = [(by_game.get(r["game_id"], 0), r["delta_brier"]) for r in records if r["game_id"] in by_game]
    if len(pairs) < 3:
        return {"note": "insufficient data"}
    exposures = [p[0] for p in pairs]
    deltas = [p[1] for p in pairs]
    median_exp = statistics.median(exposures)
    high = [d for e, d in pairs if e > median_exp]
    low = [d for e, d in pairs if e <= median_exp]
    return {
        "n_games": len(pairs), "median_exposure": median_exp,
        "pearson_r_exposure_vs_delta_brier": _pearson(exposures, deltas),
        "mean_delta_brier_high_exposure": statistics.mean(high) if high else None,
        "mean_delta_brier_low_exposure": statistics.mean(low) if low else None,
        "caveat": "descriptive only -- sample size far too small to support a causal claim.",
    }


def top_improvements_and_regressions(records: List[dict], k: int = 10) -> dict:
    by_brier = sorted(records, key=lambda r: r["delta_brier"])
    by_margin_err = sorted(records, key=lambda r: r["delta_abs_margin_error"])

    def row(r):
        return {k2: r[k2] for k2 in ("game_id", "home_team", "away_team", "date", "delta_brier",
                                      "delta_abs_margin_error", "v1_margin", "v2_margin", "actual_margin")}
    return {
        "most_improved_by_brier": [row(r) for r in by_brier[:k]],
        "most_worsened_by_brier": [row(r) for r in by_brier[-k:][::-1]],
        "most_improved_by_margin_error": [row(r) for r in by_margin_err[:k]],
        "most_worsened_by_margin_error": [row(r) for r in by_margin_err[-k:][::-1]],
    }
