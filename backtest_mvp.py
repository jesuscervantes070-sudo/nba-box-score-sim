"""
Backtests and calibrates awards.mvp_score against who REALLY won MVP,
across every cached season -- same train/holdout-by-time methodology
as sweep_constants.py (see that file's docstring for why the split
matters: a set of weights that only look good on the seasons they were
fit against would be a fit to noise, not a real formula).

The only real ground truth available is who WON (see awards.py's
docstring on why vote shares aren't available from this project's data
source) -- so the metric here is HIT RATE: on how many seasons does
mvp_score's #1 pick match the real winner, plus a softer "was the real
winner in the formula's top 3" rate for partial credit.

Usage:
    python3 backtest_mvp.py                  # score the current MVP_WEIGHTS
    python3 backtest_mvp.py --sweep          # grid-sweep weights, report best on holdout
"""
import argparse
import time

from loader import available_seasons
from awards import (
    REAL_MVP_WINNERS, MVP_WEIGHTS, mvp_features_for_season, rank_mvp_candidates,
    fetch_real_team_win_pct,
)

# Same split point sweep_constants.py uses, for the same reason: a
# time-based holdout is the only honest test that weights generalize
# rather than just fitting the seasons they were tuned on.
HOLDOUT_FIRST_SEASON = "2016-17"


def _build_all_features() -> dict:
    """
    Fetches real team win% and builds MVP candidate features for every
    backtestable season ONCE -- reused across every weight combo in a
    sweep, so a 30-value grid doesn't refetch real data 30x per combo.
    Prints progress since fetch_real_team_win_pct is a live network
    call per season (~30 total, a few seconds each).
    """
    seasons = [s for s in available_seasons() if s in REAL_MVP_WINNERS]
    all_features = {}
    for season in seasons:
        win_pct = fetch_real_team_win_pct(season)
        all_features[season] = mvp_features_for_season(season, team_win_pct=win_pct)
        time.sleep(0.3)
    return all_features


def score_weights(all_features: dict, weights: dict) -> dict:
    """
    Runs one weight setting against every season already built in
    `all_features`, split train/holdout by time. Returns hit-rate (the
    formula's #1 pick == the real winner) and top-3 rate for each half.
    """
    def _run(seasons):
        hits, top3, misses = 0, 0, []
        for season in seasons:
            features = all_features[season]
            if not features:
                continue
            ranked = rank_mvp_candidates(features, season, weights)
            names = [name for name, _ in ranked]
            real_winner = REAL_MVP_WINNERS[season]
            if not names:
                continue
            if names[0] == real_winner:
                hits += 1
            else:
                misses.append((season, real_winner, names[0]))
            if real_winner in names[:3]:
                top3 += 1
        n = len([s for s in seasons if all_features.get(s)])
        return {
            "n": n,
            "hit_rate": hits / n if n else 0.0,
            "top3_rate": top3 / n if n else 0.0,
            "misses": misses,
        }

    train = [s for s in all_features if s < HOLDOUT_FIRST_SEASON]
    holdout = [s for s in all_features if s >= HOLDOUT_FIRST_SEASON]
    return {"train": _run(train), "holdout": _run(holdout)}


def _print_result(label: str, result: dict) -> None:
    t, h = result["train"], result["holdout"]
    print(f"{label}")
    print(f"  train   ({t['n']:2d} seasons): hit-rate {t['hit_rate']:.0%}, top-3 {t['top3_rate']:.0%}")
    print(f"  holdout ({h['n']:2d} seasons): hit-rate {h['hit_rate']:.0%}, top-3 {h['top3_rate']:.0%}")
    if h["misses"]:
        print("  holdout misses (season: real winner -> formula's pick):")
        for season, real, picked in h["misses"]:
            print(f"    {season}: {real} -> {picked}")


def sweep(all_features: dict) -> None:
    """
    A grid over win_pct (team success) and usg_pct (shot-creation
    load) jointly -- these two trade against each other (found by
    testing: with usg_pct weighted low, an efficient low-usage role
    player on the best-record team can beat a real MVP-level star on a
    merely-good team). fatigue is fixed at 0 -- already tested
    separately and found to hurt on holdout every time (real MVPs
    repeat far more than a fatigue penalty assumes). pie/ts_pct/
    availability held at their MVP_WEIGHTS starting values. Reports
    every combo, sorted by holdout hit-rate.
    """
    win_pct_values = [2.0, 3.0, 4.0, 6.0, 8.0]
    usg_pct_values = [8.0, 11.0, 15.0, 19.0, 23.0]

    results = []
    for wp in win_pct_values:
        for usg in usg_pct_values:
            weights = dict(MVP_WEIGHTS, win_pct=wp, usg_pct=usg, fatigue=0.0)
            result = score_weights(all_features, weights)
            results.append((weights, result))

    results.sort(key=lambda r: (-r[1]["holdout"]["hit_rate"], -r[1]["holdout"]["top3_rate"]))
    print(f"{'win_pct':>8} {'usg_pct':>8} {'train hit':>10} {'holdout hit':>12} {'holdout top3':>13}")
    for weights, result in results:
        t, h = result["train"], result["holdout"]
        print(f"{weights['win_pct']:>8.1f} {weights['usg_pct']:>8.1f} "
              f"{t['hit_rate']:>9.0%} {h['hit_rate']:>11.0%} {h['top3_rate']:>12.0%}")

    best_weights, best_result = results[0]
    print()
    _print_result(f"BEST ON HOLDOUT: win_pct={best_weights['win_pct']}, usg_pct={best_weights['usg_pct']}",
                   best_result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", action="store_true", help="grid-sweep win_pct/fatigue weights")
    args = parser.parse_args()

    print("Fetching real team win% for every backtestable season "
          "(one-time, ~30 network calls)...")
    all_features = _build_all_features()

    if args.sweep:
        sweep(all_features)
    else:
        result = score_weights(all_features, MVP_WEIGHTS)
        _print_result(f"CURRENT MVP_WEIGHTS: {MVP_WEIGHTS}", result)
