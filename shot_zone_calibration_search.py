"""
The real, reproducible walk-forward backtest + grid search that produces
shot_zone_calibration.json -- same methodology as
player_ability_calibration_search.py (not imported by shot_zone_estimation.py
at runtime; that file only reads the already-produced JSON artifact).

METHODOLOGY: for every real CONSECUTIVE season pair (T, T+1) across the
full 1996-97-2025-26 shot-zone corpus, predict T+1's real zone FG% using
ONLY evidence through T, scored by weighted MAE/RMSE (weighted by real
T+1 zone FGA). Grid search picks the (lambda, M) minimizing weighted MAE
across ALL such pairs -- independently per attribute (rim_finishing,
floater_short_mid, midrange are NOT forced to share one lambda/M).
"""
import math
from typing import Dict, List, Optional, Tuple

from shot_zone_estimation import SHOT_ZONE_ATTRIBUTES, ZONE_PREFIX_FOR_ATTRIBUTE, _zone_row_pct
from shot_zone_ingestion import load_shot_zones, SHOT_ZONE_FIRST_SEASON


def _season_year(s: str) -> int:
    return int(s[:4])


def build_raw_evidence_table(all_seasons: List[str]) -> Dict[str, Dict[str, Dict[str, dict]]]:
    """table[attribute][season][player_name] = {"rate", "sample"}"""
    seasons = sorted(s for s in all_seasons if s >= SHOT_ZONE_FIRST_SEASON)
    table = {attr: {} for attr in SHOT_ZONE_ATTRIBUTES}
    for s in seasons:
        zone_data = load_shot_zones(s)
        if not zone_data:
            continue
        for attr in SHOT_ZONE_ATTRIBUTES:
            prefix = ZONE_PREFIX_FOR_ATTRIBUTE[attr]
            season_rows = {}
            for row in zone_data.values():
                result = _zone_row_pct(row, prefix)
                if result is None:
                    continue
                rate, fga = result
                season_rows[row["player_name"]] = {"rate": rate, "sample": fga}
            table[attr][s] = season_rows
    return table


def build_pairs(table: Dict[str, Dict[str, dict]], attr: str, min_t1_sample: float):
    data = table[attr]
    seasons = sorted(data.keys())
    pairs = []
    for i in range(len(seasons) - 1):
        T, T1 = seasons[i], seasons[i + 1]
        if _season_year(T1) != _season_year(T) + 1:
            continue  # consecutive-year pairs only, same discipline as the six locked attributes
        for name, t1row in data[T1].items():
            if t1row["sample"] < min_t1_sample or name not in data[T]:
                continue
            evidence = [(s2, data[s2][name]) for s2 in seasons[:i + 1] if name in data[s2]]
            pairs.append((T, T1, name, t1row["rate"], t1row["sample"], evidence))
    return pairs, seasons


def league_avg_through(table: Dict[str, Dict[str, dict]], attr: str, cutoff_season: str, seasons: List[str]) -> Optional[float]:
    data = table[attr]
    vals = []
    for s in seasons:
        if _season_year(s) > _season_year(cutoff_season):
            break
        vals.extend(r["rate"] for r in data[s].values())
    return sum(vals) / len(vals) if vals else None


def predict(evidence, as_of_season: str, lam: float, M: Optional[float], league_avg: Optional[float]) -> Optional[float]:
    as_of_year = _season_year(as_of_season)
    total_w = wsum = 0.0
    for s2, row in evidence:
        age = as_of_year - _season_year(s2)
        if age < 0:
            continue
        w = (lam ** age) * row["sample"]
        wsum += row["rate"] * w
        total_w += w
    if total_w <= 0:
        return None
    if M is None or league_avg is None:
        return wsum / total_w
    return (wsum + M * league_avg) / (total_w + M)


def evaluate(pairs, seasons, table, attr, lam: float, M: Optional[float]) -> Tuple[Optional[float], Optional[float], int]:
    league_avg_cache = {T: league_avg_through(table, attr, T, seasons) for T in {p[0] for p in pairs}}
    abs_err_w = sq_err_w = total_w = 0.0
    n = 0
    for T, T1, name, target_rate, target_w, evidence in pairs:
        pred = predict(evidence, T, lam, M, league_avg_cache[T])
        if pred is None:
            continue
        err = pred - target_rate
        abs_err_w += abs(err) * target_w
        sq_err_w += (err ** 2) * target_w
        total_w += target_w
        n += 1
    if total_w == 0:
        return None, None, 0
    return abs_err_w / total_w, math.sqrt(sq_err_w / total_w), n


def grid_search(pairs, seasons, table, attr: str, lambda_grid: List[float], m_grid: List[float]):
    """Returns (best_mae, best_rmse, best_lambda, best_M, n) -- deterministic."""
    best = None
    for lam in lambda_grid:
        for M in m_grid:
            mae, rmse, n = evaluate(pairs, seasons, table, attr, lam, M)
            if mae is not None and (best is None or mae < best[0]):
                best = (mae, rmse, lam, M, n)
    return best


def rank_correlation(pairs, seasons, table, attr: str, lam: float, M: Optional[float]) -> Optional[float]:
    """Spearman rank correlation between predicted and real T+1 rate,
    across all real pairs -- a real, held-out RANKING check to
    complement weighted MAE (same rationale as ball_security_analysis's
    percentile-rank comparison: absolute-error optimum and rank-order
    fidelity can disagree, and both matter for a displayed 0-99 rating)."""
    league_avg_cache = {T: league_avg_through(table, attr, T, seasons) for T in {p[0] for p in pairs}}
    preds, actuals = [], []
    for T, T1, name, target_rate, target_w, evidence in pairs:
        pred = predict(evidence, T, lam, M, league_avg_cache[T])
        if pred is None:
            continue
        preds.append(pred)
        actuals.append(target_rate)
    n = len(preds)
    if n < 5:
        return None

    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        for r, i in enumerate(order):
            ranks[i] = r
        return ranks

    rx, ry = rank(preds), rank(actuals)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0 or vy <= 0:
        return None
    return round(cov / math.sqrt(vx * vy), 4)
