"""
The empirical calibration search itself -- walk-forward backtest +
grid search over (lambda, M) per attribute. This is what produced
player_ability_calibration.json; kept here as real, reproducible
project code (not a throwaway script) so the artifact can be
regenerated and the search's determinism can be tested.

Not imported by player_ability_estimation.py at runtime -- that file
only reads the already-produced JSON artifact (see
player_ability_calibration.py). This module is the OFFLINE tool that
builds it.

METHODOLOGY (see player_ability_calibration.json's own "methodology"
field for the short version): for every real consecutive season pair
(T, T+1) in the cache, predict T+1's real rate using ONLY evidence
through T (see player_ability_estimation.py's own extractors), scored
by weighted MAE/RMSE (weighted by real T+1 sample size so a 5-attempt
season doesn't count the same as a 500-attempt one). Grid search picks
the (lambda, M) minimizing weighted MAE across ALL such pairs.

KNOWN LIMITATION (repeated from player_ability_calibration.py):
parameter selection uses the full set of walk-forward pairs, not a
further held-out split of them -- every single PREDICTION is genuinely
out-of-sample (T+1 never touches the estimate), but the exact (lambda,
M) chosen was not itself cross-validated against a separate holdout.
"""
import glob
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loader import load_teams, load_player_advanced_stats, load_player_rebound_splits
import player_ability_estimation as pae

CALIBRATABLE_ATTRIBUTES = (
    "three_point", "free_throw", "passing",
    "offensive_rebounding", "defensive_rebounding", "defensive_playmaking",
)  # ball_security deliberately excluded -- see player_ability_calibration.json


def _season_year(s: str) -> int:
    return int(s[:4])


def build_raw_evidence_table(all_seasons: List[str]) -> Dict[str, Dict[str, Dict[str, dict]]]:
    """table[attribute][season][player_name] = {"rate", "sample", "mode"}
    -- every qualifying player-season's raw evidence for every
    calibratable attribute, reusing player_ability_estimation.py's own
    extractors directly (no reimplementation, no drift between the
    production estimator and what this search calibrates against)."""
    table = {attr: {} for attr in CALIBRATABLE_ATTRIBUTES}
    for s in all_seasons:
        try:
            teams = load_teams(s)
        except FileNotFoundError:
            continue
        advanced = load_player_advanced_stats(s)
        if not teams or not advanced:
            continue
        splits = load_player_rebound_splits(s)
        for attr in CALIBRATABLE_ATTRIBUTES:
            extractor = pae.ATTRIBUTE_EXTRACTORS[attr]
            needs_splits = attr in pae.ATTRIBUTES_NEEDING_REBOUND_SPLITS
            season_rows = {}
            for team in teams.values():
                for player in team.players:
                    adv_row = advanced.get(player.name)
                    if not adv_row:
                        continue
                    rebound_row = splits.get(player.name) if needs_splits else None
                    result = extractor(player, adv_row, rebound_row)
                    if result is None:
                        continue
                    rate, sample, mode = result
                    if sample > 0:
                        season_rows[player.name] = {"rate": rate, "sample": sample, "mode": mode}
            table[attr][s] = season_rows
    return table


def build_pairs(table: Dict[str, Dict[str, dict]], attr: str, min_t1_sample: float):
    """All (T, T1, name, target_rate, target_weight, evidence) rows for
    one attribute -- filtered to real T+1 sample >= min_t1_sample AND a
    real T-season record too (so Baseline A is always computable on
    the identical row set every other method is scored on)."""
    data = table[attr]
    seasons = sorted(data.keys())
    pairs = []
    for i in range(len(seasons) - 1):
        T, T1 = seasons[i], seasons[i + 1]
        if _season_year(T1) != _season_year(T) + 1:
            continue
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
    """M=None means NO shrinkage at all (Baseline C's shape)."""
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
    """Weighted MAE/RMSE for one (lambda, M) over `pairs`. Pure
    function of its inputs -- same inputs always produce the same
    output (determinism is tested against exactly this property)."""
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
    """Returns (best_mae, best_rmse, best_lambda, best_M, n) --
    deterministic: the same pairs/grids always produce the same
    result, since evaluate() has no randomness anywhere."""
    best = None
    for lam in lambda_grid:
        for M in m_grid:
            mae, rmse, n = evaluate(pairs, seasons, table, attr, lam, M)
            if mae is not None and (best is None or mae < best[0]):
                best = (mae, rmse, lam, M, n)
    return best
