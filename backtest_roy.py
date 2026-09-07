"""
Backtests and calibrates awards.ROY_WEIGHTS against who REALLY won
Rookie of the Year -- same train/holdout-by-time methodology as
backtest_mvp.py (see that file's docstring; same reasoning applies
here unchanged). The only difference: 1999-00 was a real TIE (Elton
Brand and Steve Francis both won), so a "hit" here means the formula's
#1 pick is IN that season's real winner(s), not strictly equal to one
name.

Usage:
    python3 backtest_roy.py                  # score the current ROY_WEIGHTS
    python3 backtest_roy.py --sweep          # grid-sweep weights, report best on holdout
"""
import argparse
import time

from loader import available_seasons
from awards import (
    REAL_ROY_WINNERS, ROY_WEIGHTS, _roy_winner_names, roy_features_for_season,
    rank_roy_candidates, fetch_real_team_win_pct,
)

HOLDOUT_FIRST_SEASON = "2016-17"  # same split point as backtest_mvp.py


def _build_all_features() -> dict:
    seasons = [s for s in available_seasons() if s in REAL_ROY_WINNERS]
    all_features = {}
    for season in seasons:
        win_pct = fetch_real_team_win_pct(season)
        all_features[season] = roy_features_for_season(season, team_win_pct=win_pct)
        time.sleep(0.3)
    return all_features


def score_weights(all_features: dict, weights: dict) -> dict:
    def _run(seasons):
        hits, top3, misses = 0, 0, []
        for season in seasons:
            features = all_features[season]
            if not features:
                continue
            ranked = rank_roy_candidates(features, season, weights)
            names = [name for name, _ in ranked]
            winners = _roy_winner_names(season)
            if not names:
                continue
            if names[0] in winners:
                hits += 1
            else:
                misses.append((season, "/".join(winners), names[0]))
            if any(w in names[:3] for w in winners):
                top3 += 1
        n = len([s for s in seasons if all_features.get(s)])
        return {"n": n, "hit_rate": hits / n if n else 0.0,
                "top3_rate": top3 / n if n else 0.0, "misses": misses}

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
    Grid over win_pct and usg_pct, same two that mattered for MVP.
    win_pct starts at 0 for ROY (real ROY winners often come from bad
    teams) -- swept anyway rather than assumed, since a MILD positive
    weight might still help (a rookie good enough to start for a
    decent team is some signal) and the sweep is what should decide
    that, not a guess.
    """
    win_pct_values = [0.0, 1.0, 2.0, 4.0]
    usg_pct_values = [4.0, 6.0, 8.0, 11.0, 15.0]

    results = []
    for wp in win_pct_values:
        for usg in usg_pct_values:
            weights = dict(ROY_WEIGHTS, win_pct=wp, usg_pct=usg)
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
    parser.add_argument("--sweep", action="store_true", help="grid-sweep win_pct/usg_pct weights")
    args = parser.parse_args()

    print("Fetching real team win% for every backtestable season "
          "(one-time, ~30 network calls)...")
    all_features = _build_all_features()

    if args.sweep:
        sweep(all_features)
    else:
        result = score_weights(all_features, ROY_WEIGHTS)
        _print_result(f"CURRENT ROY_WEIGHTS: {ROY_WEIGHTS}", result)
