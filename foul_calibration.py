"""
Loader + grid search for the foul-attribute calibration artifact
(foul_calibration.json) -- a SEPARATE artifact, never touches
player_ability_calibration.json/_v2.json, ball_security_calibration.json,
or shot_zone_calibration.json. Same safe-fallback contract as all three:
missing/malformed returns None, never raises, never invents a value.

`foul_drawing` and `foul_discipline` are calibrated INDEPENDENTLY -- never
forced to share one lambda/M (see grid_search's per-attribute call shape).
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CALIBRATION_PATH = Path(__file__).parent / "foul_calibration.json"

LAMBDA_GRID = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
M_GRID = (0.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1500.0)


@dataclass(frozen=True)
class CalibratedFoulParams:
    lambda_: float
    M: float
    denominator: str
    weighted_mae: float
    weighted_rmse: float
    n_observations: int
    status: str
    calibration_version: str


def load_calibration() -> Dict[str, CalibratedFoulParams]:
    if not CALIBRATION_PATH.exists():
        return {}
    try:
        with open(CALIBRATION_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    version = data.get("calibration_version", "unknown")
    result = {}
    for attr, entry in data.get("attributes", {}).items():
        try:
            result[attr] = CalibratedFoulParams(
                lambda_=entry["lambda"], M=entry["M"], denominator=entry["denominator"],
                weighted_mae=entry["weighted_mae"], weighted_rmse=entry["weighted_rmse"],
                n_observations=entry["n_observations"], status=entry["status"],
                calibration_version=version,
            )
        except KeyError:
            continue
    return result


def get_calibrated_params(attribute: str) -> Optional[CalibratedFoulParams]:
    return load_calibration().get(attribute)


def save_calibration(results: Dict[str, dict], version: str = "v1") -> None:
    payload = {"calibration_version": version, "attributes": results}
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(payload, f, indent=2)


# =====================================================================
# Nested, time-respecting grid search -- same shape as
# ball_security_calibration.grid_search / shot_zone_calibration_search.py.
# =====================================================================

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


def grid_search(rows_by_season: Dict[str, list], rate_fn, denom_fn, min_exposure: float) -> dict:
    """
    rows_by_season: {season: [row, ...]}. `rate_fn(row, min_exposure) ->
    Optional[float]`, `denom_fn(row) -> float` -- generic over both
    foul_drawing and foul_discipline (and over whichever denominator won
    each attribute's own comparison in foul_analysis.py).
    """
    seasons = sorted(rows_by_season.keys())
    if len(seasons) < 2:
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": f"only {len(seasons)} season(s) -- need >= 2 for any T->T+1 pair."}

    league_avg_by_season = {}
    for s, rows in rows_by_season.items():
        rates = [r for r in (rate_fn(row, min_exposure) for row in rows) if r is not None]
        league_avg_by_season[s] = sum(rates) / len(rates) if rates else None

    pairs_evaluated = [(s, seasons[i + 1]) for i, s in enumerate(seasons[:-1])]

    best = None
    for lam in LAMBDA_GRID:
        for M in M_GRID:
            residuals = []
            for s_now, s_next in pairs_evaluated:
                league_avg = league_avg_by_season.get(s_now)
                if league_avg is None:
                    continue
                by_player_hist: Dict[str, List[Tuple[str, float, float]]] = {}
                for s in seasons:
                    if _season_year(s) > _season_year(s_now):
                        continue
                    for row in rows_by_season[s]:
                        rate = rate_fn(row, min_exposure)
                        if rate is None:
                            continue
                        by_player_hist.setdefault(row.player_id, []).append((s, rate, denom_fn(row)))

                next_by_id = {r.player_id: r for r in rows_by_season[s_next]}
                for pid, hist in by_player_hist.items():
                    r_next = next_by_id.get(pid)
                    if r_next is None:
                        continue
                    actual_next = rate_fn(r_next, min_exposure)
                    if actual_next is None:
                        continue
                    pred = _shrunk_rate(hist, _season_year(s_now), lam, M, league_avg)
                    if pred is None:
                        continue
                    weight = denom_fn(r_next)
                    residuals.append((abs(pred - actual_next), (pred - actual_next) ** 2, weight))

            if not residuals:
                continue
            total_w = sum(w for _, _, w in residuals)
            mae = sum(a * w for a, _, w in residuals) / total_w
            rmse = math.sqrt(sum(s * w for _, s, w in residuals) / total_w)
            n_obs = len(residuals)
            candidate = {"lambda": lam, "M": M, "weighted_mae": mae, "weighted_rmse": rmse, "n_observations": n_obs}
            if best is None or candidate["weighted_mae"] < best["weighted_mae"]:
                best = candidate

    if best is None:
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": "no real (T, T+1) player pairs with sufficient exposure."}
    best["season_pairs"] = pairs_evaluated
    best["status"] = "REVISIT" if best["n_observations"] < 200 else "KEEP_BUT_FLAG"
    return best
