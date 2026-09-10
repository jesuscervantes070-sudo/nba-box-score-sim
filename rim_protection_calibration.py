"""
Loader + grid search for the rim-protection calibration artifact
(rim_protection_calibration.json) -- a SEPARATE artifact, never touches
any prior phase's calibration file. Same safe-fallback contract as all
others: missing/malformed returns None, never raises, never invents.

Per this phase's explicit instruction: parameter selection uses a TRAIN
split only; a separate HELDOUT split (later seasons, never touched during
grid search) is used for the final reported performance number -- a
stricter discipline than every prior phase's own calibration (which
selected params on the full pair set). See docs/PHASE7_RIM_PROTECTION_REPORT.md.
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CALIBRATION_PATH = Path(__file__).parent / "rim_protection_calibration.json"

LAMBDA_GRID = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
M_GRID = (0.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1500.0, 3000.0)


@dataclass(frozen=True)
class CalibratedRimProtectionParams:
    lambda_: float
    M: float
    weight: str
    train_weighted_mae: float
    heldout_weighted_mae: float
    heldout_rank_corr: Optional[float]
    n_train_observations: int
    n_heldout_observations: int
    status: str
    calibration_version: str


def load_calibration() -> Optional[CalibratedRimProtectionParams]:
    if not CALIBRATION_PATH.exists():
        return None
    try:
        with open(CALIBRATION_PATH) as f:
            data = json.load(f)
        entry = data["rim_protection"]
        return CalibratedRimProtectionParams(
            lambda_=entry["lambda"], M=entry["M"], weight=entry["weight"],
            train_weighted_mae=entry["train_weighted_mae"], heldout_weighted_mae=entry["heldout_weighted_mae"],
            heldout_rank_corr=entry.get("heldout_rank_corr"),
            n_train_observations=entry["n_train_observations"], n_heldout_observations=entry["n_heldout_observations"],
            status=entry["status"], calibration_version=data.get("calibration_version", "unknown"),
        )
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def get_calibrated_params() -> Optional[CalibratedRimProtectionParams]:
    return load_calibration()


def save_calibration(entry: dict, version: str = "v1") -> None:
    payload = {"calibration_version": version, "rim_protection": entry}
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(payload, f, indent=2)


def _season_year(s: str) -> int:
    return int(s[:4])


def _shrunk_rate(seasons_rates: List[Tuple[str, float, float]], as_of_year: int, lambda_: float, M: float, league_avg: float) -> Optional[float]:
    total_w, weighted_sum = 0.0, 0.0
    for season, rate, weight in seasons_rates:
        if weight <= 0:
            continue
        age = as_of_year - _season_year(season)
        if age < 0:
            continue
        w = (lambda_ ** age) * weight
        weighted_sum += rate * w
        total_w += w
    if total_w <= 0:
        return None
    return (weighted_sum + M * league_avg) / (total_w + M)


def _build_pairs_and_league_avg(rows_by_season: Dict[str, list], rate_fn, weight_fn):
    seasons = sorted(rows_by_season.keys())
    league_avg_by_season = {}
    for s, rows in rows_by_season.items():
        rates = [r for r in (rate_fn(row) for row in rows) if r is not None]
        league_avg_by_season[s] = sum(rates) / len(rates) if rates else None
    pairs = [(s, seasons[i + 1]) for i, s in enumerate(seasons[:-1])]
    return seasons, pairs, league_avg_by_season


def evaluate(rows_by_season: Dict[str, list], rate_fn, weight_fn, lam: float, M: float,
             restrict_pairs: Optional[List[Tuple[str, str]]] = None):
    seasons, all_pairs, league_avg = _build_pairs_and_league_avg(rows_by_season, rate_fn, weight_fn)
    pairs_to_use = restrict_pairs if restrict_pairs is not None else all_pairs
    residuals = []
    preds_actuals = []
    for s_now, s_next in pairs_to_use:
        la = league_avg.get(s_now)
        if la is None:
            continue
        by_player_hist: Dict[str, List[Tuple[str, float, float]]] = {}
        for s in seasons:
            if _season_year(s) > _season_year(s_now):
                continue
            for row in rows_by_season[s]:
                rate = rate_fn(row)
                if rate is None:
                    continue
                by_player_hist.setdefault(row.player_id, []).append((s, rate, weight_fn(row)))
        next_by_id = {r.player_id: r for r in rows_by_season[s_next]}
        for pid, hist in by_player_hist.items():
            r_next = next_by_id.get(pid)
            if r_next is None:
                continue
            actual = rate_fn(r_next)
            if actual is None:
                continue
            pred = _shrunk_rate(hist, _season_year(s_now), lam, M, la)
            if pred is None:
                continue
            w = weight_fn(r_next)
            residuals.append((abs(pred - actual), (pred - actual) ** 2, w))
            preds_actuals.append((pred, actual))
    if not residuals:
        return None
    total_w = sum(w for _, _, w in residuals)
    mae = sum(a * w for a, _, w in residuals) / total_w
    rmse = math.sqrt(sum(s * w for _, s, w in residuals) / total_w)
    return {"weighted_mae": mae, "weighted_rmse": rmse, "n": len(residuals), "preds_actuals": preds_actuals}


def grid_search_on_train(rows_by_season: Dict[str, list], rate_fn, weight_fn, train_pairs: List[Tuple[str, str]]) -> Optional[dict]:
    best = None
    for lam in LAMBDA_GRID:
        for M in M_GRID:
            result = evaluate(rows_by_season, rate_fn, weight_fn, lam, M, restrict_pairs=train_pairs)
            if result is None:
                continue
            if best is None or result["weighted_mae"] < best["weighted_mae"]:
                best = {"lambda": lam, "M": M, "weighted_mae": result["weighted_mae"],
                        "weighted_rmse": result["weighted_rmse"], "n": result["n"]}
    return best


def rank_correlation(preds_actuals: List[Tuple[float, float]]) -> Optional[float]:
    n = len(preds_actuals)
    if n < 5:
        return None
    preds = [p for p, _ in preds_actuals]
    actuals = [a for _, a in preds_actuals]
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
