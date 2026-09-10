"""
Phase 12A -- Player Anthropometrics Foundation: analysis / diagnostics.

Everything here is READ-ONLY over the caches `anthropometrics_ingestion.py`
already built. No new API calls. No skill estimator is touched, read, or
modified anywhere in this file -- diagnostics that compare a physical
trait against an existing skill (e.g. reach vs rim_protection) belong in
a later, explicitly diagnostic-only pass and are NOT implemented here to
keep this phase's scope narrow (see docs/PHASE12A_ANTHROPOMETRICS_REPORT.md
Sec. 12 for why that comparison is deferred, not forgotten).

Sections:
  1. Identity linkage (combine PLAYER_ID vs. NBA static player_id space)
  2. Coverage diagnostics (per-trait, per-year)
  3. Mass longitudinal diagnostics (staleness hypothesis, NOT asserted)
  4. Small hand-rolled OLS (Gaussian elimination on the normal
     equations -- same "no ML library" convention used by
     playmaking_vision_analysis.py / shot_creation_analysis.py) +
     TRAIN/HELDOUT backtest for wingspan~height and
     standing_reach~height+wingspan.
"""
from typing import Dict, List, Optional, Tuple

import anthropometrics_ingestion as ai

COMBINE_YEARS = tuple(y for y in range(2000, 2026) if y != 2001)  # 2001 = real, confirmed 0-row gap year
INFERENCE_TRAIN_YEARS = tuple(y for y in COMBINE_YEARS if y < 2018)
INFERENCE_HELDOUT_YEARS = tuple(y for y in COMBINE_YEARS if y >= 2018)


# --------------------------- 1. Identity linkage ---------------------------

def combine_id_vs_static_player_id(draft_years=COMBINE_YEARS) -> dict:
    """Does the combine's real PLAYER_ID fall in the same numbering space
    as nba_api's real static player_id list, and does every combine
    PLAYER_ID that failed to match correspond to a player who simply
    never appeared in an NBA game (not a join failure)?"""
    from nba_api.stats.static import players as static_players
    static_ids = {str(p["id"]) for p in static_players.get_players()}

    matched, unmatched_ids = 0, []
    total = 0
    for y in draft_years:
        players = ai.load_combine_anthro(y)
        for pid, row in players.items():
            total += 1
            if pid in static_ids:
                matched += 1
            else:
                unmatched_ids.append((y, pid, row.get("player_name")))

    return {
        "total_combine_players": total,
        "matched_to_static_player_id": matched,
        "unmatched_count": len(unmatched_ids),
        "unmatched_sample": unmatched_ids[:15],
        "match_rate": matched / total if total else None,
    }


# --------------------------- 2. Coverage diagnostics ---------------------------

def combine_coverage_table(draft_years=COMBINE_YEARS) -> List[dict]:
    rows = []
    for y in draft_years:
        players = ai.load_combine_anthro(y)
        n = len(players)
        if n == 0:
            rows.append({"draft_year": y, "n": 0, "height": 0, "wingspan": 0, "weight": 0, "standing_reach": 0,
                          "joint_height_wingspan": 0})
            continue
        rows.append({
            "draft_year": y, "n": n,
            "height": sum(1 for p in players.values() if p["height_wo_shoes_in"] is not None),
            "wingspan": sum(1 for p in players.values() if p["wingspan_in"] is not None),
            "weight": sum(1 for p in players.values() if p["weight_lbs"] is not None),
            "standing_reach": sum(1 for p in players.values() if p["standing_reach_in"] is not None),
            "joint_height_wingspan": sum(1 for p in players.values()
                                          if p["height_wo_shoes_in"] is not None and p["wingspan_in"] is not None),
        })
    return rows


def roster_coverage_table(seasons: List[str]) -> List[dict]:
    rows = []
    for s in seasons:
        players = ai.load_roster_physicals(s)
        n = len(players)
        rows.append({
            "season": s, "n": n,
            "listed_height": sum(1 for p in players.values() if p.get("listed_height_in") is not None),
            "listed_weight": sum(1 for p in players.values() if p.get("listed_weight_lbs") is not None),
        })
    return rows


# --------------------------- 3. Mass longitudinal diagnostics ---------------------------

def mass_longitudinal_diagnostic(seasons: List[str]) -> dict:
    """Real, direct measurement of whether repeated players' LISTED
    weight changes across seasons. Reports the raw distribution --
    does NOT label repeated-identical-weight as "stale," per explicit
    instruction that this is a hypothesis to report evidence for, not
    assert as fact."""
    by_player: Dict[str, Dict[str, float]] = {}
    for s in seasons:
        for pid, row in ai.load_roster_physicals(s).items():
            w = row.get("listed_weight_lbs")
            if w is not None:
                by_player.setdefault(pid, {})[s] = w

    multi = {pid: obs for pid, obs in by_player.items() if len(obs) >= 2}
    identical_all = sum(1 for obs in multi.values() if len(set(obs.values())) == 1)
    changes = [max(obs.values()) - min(obs.values()) for obs in multi.values()]

    return {
        "seasons_compared": seasons,
        "unique_players_total": len(by_player),
        "players_with_multiple_observations": len(multi),
        "players_all_identical_weight": identical_all,
        "fraction_all_identical": (identical_all / len(multi)) if multi else None,
        "mean_max_minus_min_change_lbs": (sum(changes) / len(changes)) if changes else None,
        "max_change_lbs": max(changes) if changes else None,
    }


def adjacent_season_mass_staleness(season_a: str, season_b: str) -> dict:
    """Direct, TRUE 1-year-apart staleness check (as opposed to the
    multi-year-gap comparison above, which conflates real gradual
    change with staleness). Only meaningful when season_a/season_b are
    real, actually-consecutive NBA seasons."""
    pa, pb = ai.load_roster_physicals(season_a), ai.load_roster_physicals(season_b)
    common = set(pa) & set(pb)
    identical, changes = 0, []
    for pid in common:
        wa, wb = pa[pid].get("listed_weight_lbs"), pb[pid].get("listed_weight_lbs")
        if wa is None or wb is None:
            continue
        if wa == wb:
            identical += 1
        changes.append(wb - wa)
    n = len(changes)
    return {
        "season_a": season_a, "season_b": season_b, "n_common_players_with_weight": n,
        "identical_weight_both_seasons": identical,
        "fraction_identical": (identical / n) if n else None,
        "mean_change_lbs": (sum(changes) / n) if n else None,
    }


# --------------------------- 4. Small OLS + backtest ---------------------------

def _solve_normal_equations(X: List[Tuple[float, ...]], y: List[float]) -> List[float]:
    """Solve (X^T X) b = X^T y via Gaussian elimination. X rows already
    include the leading 1.0 intercept term. No external ML library --
    same hand-rolled convention as the residualization work in
    playmaking_vision_analysis.py / shot_creation_analysis.py."""
    k = len(X[0])
    XtX = [[sum(row[i] * row[j] for row in X) for j in range(k)] for i in range(k)]
    Xty = [sum(row[i] * yi for row, yi in zip(X, y)) for i in range(k)]

    # augmented matrix Gaussian elimination with partial pivoting
    aug = [XtX[i] + [Xty[i]] for i in range(k)]
    for col in range(k):
        pivot_row = max(range(col, k), key=lambda r: abs(aug[r][col]))
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        if abs(pivot) < 1e-12:
            raise ValueError("singular normal-equations matrix -- degenerate/collinear predictors")
        aug[col] = [v / pivot for v in aug[col]]
        for r in range(k):
            if r != col:
                factor = aug[r][col]
                aug[r] = [aug[r][c] - factor * aug[col][c] for c in range(k + 1)]
    return [aug[i][k] for i in range(k)]


def _collect_pairs(draft_years, fields: Tuple[str, ...], target: str):
    """Real combine rows with every needed field present (no imputation)."""
    xs, ys = [], []
    for y in draft_years:
        for row in ai.load_combine_anthro(y).values():
            vals = [row.get(f) for f in fields]
            target_val = row.get(target)
            if any(v is None for v in vals) or target_val is None:
                continue
            xs.append((1.0,) + tuple(vals))
            ys.append(target_val)
    return xs, ys


def _mae_rmse(preds: List[float], actual: List[float]) -> dict:
    errs = [p - a for p, a in zip(preds, actual)]
    n = len(errs)
    mae = sum(abs(e) for e in errs) / n
    rmse = (sum(e * e for e in errs) / n) ** 0.5
    bias = sum(errs) / n
    return {"n": n, "mae": mae, "rmse": rmse, "bias": bias, "max_abs_error": max(abs(e) for e in errs)}


def backtest_regression(predictor_fields: Tuple[str, ...], target_field: str,
                         train_years=INFERENCE_TRAIN_YEARS, heldout_years=INFERENCE_HELDOUT_YEARS) -> dict:
    """Real chronological TRAIN (<2018 draft class) / HELDOUT (>=2018)
    split -- genuine generalization test, not a random shuffle of the
    same population. Reports a population-mean baseline, a
    predictor-only-mean-of-target baseline is NOT meaningful here since
    the "candidate" model already includes an intercept; instead we
    compare against a simple mean-of-TRAIN-target baseline."""
    X_train, y_train = _collect_pairs(train_years, predictor_fields, target_field)
    X_heldout, y_heldout = _collect_pairs(heldout_years, predictor_fields, target_field)
    if len(X_train) < 10 or len(X_heldout) < 5:
        return {"insufficient_data": True, "n_train": len(X_train), "n_heldout": len(X_heldout)}

    train_mean = sum(y_train) / len(y_train)
    baseline_preds = [train_mean] * len(y_heldout)
    baseline_metrics = _mae_rmse(baseline_preds, y_heldout)

    coeffs = _solve_normal_equations(X_train, y_train)
    model_preds = [sum(b * xi for b, xi in zip(coeffs, row)) for row in X_heldout]
    model_metrics = _mae_rmse(model_preds, y_heldout)

    return {
        "predictor_fields": predictor_fields, "target_field": target_field,
        "n_train": len(X_train), "n_heldout": len(X_heldout),
        "coefficients": coeffs,
        "baseline_mean_only": baseline_metrics,
        "model": model_metrics,
        "heldout_improvement_mae": baseline_metrics["mae"] - model_metrics["mae"],
    }
