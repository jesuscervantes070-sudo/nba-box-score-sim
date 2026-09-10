"""
Loader + search for the Ball Security calibration artifact
(ball_security_calibration.json) -- kept COMPLETELY SEPARATE from
player_ability_calibration.json/_v2.json (never touches or overwrites
either) since ball_security was explicitly excluded from that artifact
this phase (see player_ability_estimation.resolve_params's own docstring).

Same safe-fallback contract as player_ability_calibration.py: a missing
or malformed artifact returns None from get_ball_security_params(), never
raises, and callers must fall back to a documented provisional default.
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CALIBRATION_PATH = Path(__file__).parent / "ball_security_calibration.json"


@dataclass(frozen=True)
class CalibratedBallSecurityParams:
    lambda_: float
    M: float
    denominator: str
    exposure_unit: str
    weighted_mae: float
    weighted_rmse: float
    n_observations: int
    season_pairs: Tuple[Tuple[str, str], ...]
    status: str  # LOCK_V1 / KEEP_BUT_FLAG / REVISIT / INSUFFICIENT_EVIDENCE
    calibration_version: str


def load_ball_security_calibration() -> Optional[CalibratedBallSecurityParams]:
    if not CALIBRATION_PATH.exists():
        return None
    try:
        with open(CALIBRATION_PATH) as f:
            data = json.load(f)
        entry = data["ball_security"]
        return CalibratedBallSecurityParams(
            lambda_=entry["lambda"], M=entry["M"], denominator=entry["denominator"],
            exposure_unit=entry["exposure_unit"], weighted_mae=entry["weighted_mae"],
            weighted_rmse=entry["weighted_rmse"], n_observations=entry["n_observations"],
            season_pairs=tuple(tuple(p) for p in entry["season_pairs"]),
            status=entry["status"], calibration_version=data.get("calibration_version", "unknown"),
        )
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def get_ball_security_params() -> Optional[CalibratedBallSecurityParams]:
    return load_ball_security_calibration()


# =====================================================================
# Grid search -- same nested, time-respecting shape as
# player_ability_calibration_search.py, applied to whatever real
# season-pairs this session's turnover-ingestion sample actually covers.
# =====================================================================

LAMBDA_GRID = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
# M=0.0 is a real candidate (the "no-shrink multi-year" baseline) --
# included so the grid search can genuinely select "no shrinkage helps"
# rather than only ever returning some positive M by construction.
M_GRID = (0.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0)


def _shrunk_rate(seasons_rates: List[Tuple[str, float, float]], as_of_year: int, lambda_: float, M: float, league_avg: float) -> Optional[float]:
    """seasons_rates: [(season, rate, exposure_weight), ...] up through
    as_of_year only (caller's job to have already excluded later
    seasons -- no leakage check happens in here)."""
    total_w, weighted_sum = 0.0, 0.0
    for season, rate, weight in seasons_rates:
        if weight <= 0:
            continue
        age = as_of_year - int(season[:4])
        if age < 0:
            continue  # future season relative to as_of -- never used (leakage guard)
        w = (lambda_ ** age) * weight
        weighted_sum += rate * w
        total_w += w
    if total_w <= 0:
        return None
    return (weighted_sum + M * league_avg) / (total_w + M)


def grid_search(rows_by_season: Dict[str, list], denominator: str, min_exposure: float = 50.0) -> dict:
    """
    rows_by_season: {season: [PlayerSeasonRow, ...]} from
    ball_security_analysis.build_player_season_rows.

    For each (lambda, M): recency-shrink each player's evidence THROUGH
    season T (using only T and earlier -- real leakage guard, same
    `_seasons_through_cutoff` discipline as player_ability_estimation.py)
    to predict that SAME player's real T+1 rate. Picks the (lambda, M)
    with the lowest weighted MAE across every real (T, T+1) pair this
    session's data actually has.
    """
    from ball_security_analysis import handling_error_rate

    seasons = sorted(rows_by_season.keys())
    if len(seasons) < 2:
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": f"only {len(seasons)} season(s) with joined evidence -- need >= 2 for any T->T+1 pair."}

    # Real league-average rate per season (unweighted mean across
    # players with enough exposure) -- the shrinkage prior target.
    league_avg_by_season = {}
    for s, rows in rows_by_season.items():
        rates = [r for r in (handling_error_rate(row, denominator, min_exposure) for row in rows) if r is not None]
        league_avg_by_season[s] = sum(rates) / len(rates) if rates else None

    pairs_evaluated = [(s, seasons[i + 1]) for i, s in enumerate(seasons[:-1])]

    best = None
    for lam in LAMBDA_GRID:
        for M in M_GRID:
            residuals = []  # (abs_error, sq_error, weight)
            for s_now, s_next in pairs_evaluated:
                league_avg = league_avg_by_season.get(s_now)
                if league_avg is None:
                    continue
                # every real season <= s_now this player has evidence for
                by_player_hist: Dict[str, List[Tuple[str, float, float]]] = {}
                for s in seasons:
                    if int(s[:4]) > int(s_now[:4]):
                        continue
                    for row in rows_by_season[s]:
                        rate = handling_error_rate(row, denominator, min_exposure)
                        if rate is None:
                            continue
                        by_player_hist.setdefault(row.player_id, []).append((s, rate, row.exposure.get(denominator, 0.0)))

                next_by_id = {r.player_id: r for r in rows_by_season[s_next]}
                for pid, hist in by_player_hist.items():
                    r_next = next_by_id.get(pid)
                    if r_next is None:
                        continue
                    actual_next = handling_error_rate(r_next, denominator, min_exposure)
                    if actual_next is None:
                        continue
                    pred = _shrunk_rate(hist, int(s_now[:4]), lam, M, league_avg)
                    if pred is None:
                        continue
                    weight = r_next.exposure.get(denominator, 0.0)
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
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": "no real (T, T+1) player pairs with sufficient exposure in either season."}

    best["season_pairs"] = pairs_evaluated
    best["status"] = "REVISIT" if best["n_observations"] < 200 else "KEEP_BUT_FLAG"
    return best


def save_calibration(best: dict, denominator: str, exposure_unit: str, status_override: Optional[str] = None, version: str = "v1") -> None:
    """Writes ball_security_calibration.json -- a NEW, separate artifact.
    Never touches player_ability_calibration.json/_v2.json."""
    payload = {
        "calibration_version": version,
        "ball_security": {
            "lambda": best["lambda"], "M": best["M"], "denominator": denominator,
            "exposure_unit": exposure_unit, "weighted_mae": round(best["weighted_mae"], 5),
            "weighted_rmse": round(best["weighted_rmse"], 5), "n_observations": best["n_observations"],
            "season_pairs": best["season_pairs"], "status": status_override or best["status"],
        },
    }
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(payload, f, indent=2)
