"""Empirical Monte Carlo simulation-count stability study for FIRST HISTORICAL PREDICTIVE
BACKTEST V1 -- run BEFORE `N_SIMULATIONS` is locked in `historical_predictive_backtest.py`.

For a few real games, compares estimated home win probability (and mean margin) across simulation
counts (100/250/500/1000) and across independent seed batches at each count, to find the smallest
count whose estimate is stable enough (batch-to-batch spread small relative to the win-probability
scale) for V1. This is diagnostic only -- it does not touch engine mechanics, and its own results
are not tuned after being produced; `N_SIMULATIONS` is chosen once from this output and then fixed.
"""
import statistics
import sys
import time

import historical_game_snapshot as hgs
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault

STUDY_GAMES = [
    "0022300061",  # Denver Nuggets vs LA Lakers, 2023-10-24 (opening night)
    "0022300225",  # Charlotte Hornets vs Washington Wizards, 2023-11-22
    "0022300230",  # Indiana Pacers vs Toronto Raptors, 2023-11-22 (contested/moderate favorite range)
]
SEASON = "2023-24"
CANDIDATE_COUNTS = [100, 250, 500, 1000]
N_BATCHES_PER_COUNT = 3  # independent seed batches per count, to measure batch-to-batch spread


def _seed(game_id: str, batch: int, i: int) -> int:
    import hashlib
    h = hashlib.sha256(f"stability-study|{game_id}|batch{batch}|{i}".encode()).hexdigest()
    return int(h[:16], 16)


def run_batch(snapshot, n_sims: int, batch: int):
    home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snapshot)
    home_scores, away_scores = [], []
    for i in range(n_sims):
        try:
            result = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles,
                                             rng_seed=_seed(snapshot.game_id, batch, i))
        except DetailedGameSimulationFault:
            continue
        if result.final_home_score == result.final_away_score:
            continue
        home_scores.append(result.final_home_score)
        away_scores.append(result.final_away_score)
    n = len(home_scores)
    home_wins = sum(1 for h, a in zip(home_scores, away_scores) if h > a)
    margins = [h - a for h, a in zip(home_scores, away_scores)]
    return {
        "n_valid": n,
        "home_win_prob": home_wins / n if n else None,
        "mean_margin": statistics.mean(margins) if margins else None,
    }


def main():
    ALL_SEASONS = ["2021-22", "2022-23", "2023-24"]  # sufficient for this diagnostic study
    for game_id in STUDY_GAMES:
        print(f"\n=== {game_id} ===")
        t0 = time.time()
        snapshot = hgs.build_historical_game_snapshot(game_id, SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        print(f"snapshot build: {time.time()-t0:.2f}s ({snapshot.home_team} vs {snapshot.away_team})")
        for n in CANDIDATE_COUNTS:
            t0 = time.time()
            batch_results = [run_batch(snapshot, n, b) for b in range(N_BATCHES_PER_COUNT)]
            elapsed = time.time() - t0
            probs = [b["home_win_prob"] for b in batch_results if b["home_win_prob"] is not None]
            margins = [b["mean_margin"] for b in batch_results if b["mean_margin"] is not None]
            prob_spread = max(probs) - min(probs) if probs else None
            margin_spread = max(margins) - min(margins) if margins else None
            print(f"  n={n:5d} time={elapsed:6.2f}s  "
                  f"home_win_prob batches={['%.3f' % p for p in probs]} spread={prob_spread:.3f}  "
                  f"mean_margin batches={['%.2f' % m for m in margins]} spread={margin_spread:.2f}")


if __name__ == "__main__":
    main()
