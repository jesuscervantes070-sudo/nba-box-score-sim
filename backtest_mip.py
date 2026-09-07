"""
Backtests and calibrates awards.MIP_WEIGHTS against who REALLY won
Most Improved Player -- same train/holdout-by-time methodology as
backtest_mvp.py (see that file's docstring).

No live network fetch needed (unlike MVP/ROY's team win_pct) --
mip_features_for_season is built entirely from already-cached data, so
this runs fast. 1996-97 is always skipped: it's this project's
earliest cached season, so there is no real prior season to diff
against at all (see awards.mip_features_for_season's docstring).

Usage:
    python3 backtest_mip.py                  # score the current MIP_WEIGHTS
    python3 backtest_mip.py --sweep          # grid-sweep weights, report best on holdout
"""
import argparse

from loader import available_seasons
from awards import REAL_MIP_WINNERS, MIP_WEIGHTS, mip_features_for_season, rank_mip_candidates

HOLDOUT_FIRST_SEASON = "2016-17"


def _build_all_features() -> dict:
    seasons = [s for s in available_seasons() if s in REAL_MIP_WINNERS]
    return {season: mip_features_for_season(season) for season in seasons}


def score_weights(all_features: dict, weights: dict) -> dict:
    def _run(seasons):
        hits, top3, misses = 0, 0, []
        for season in seasons:
            features = all_features[season]
            if not features:
                continue
            ranked = rank_mip_candidates(features, season, weights)
            names = [name for name, _ in ranked]
            real = REAL_MIP_WINNERS[season]
            if not names:
                continue
            if names[0] == real:
                hits += 1
            else:
                misses.append((season, real, names[0]))
            if real in names[:3]:
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
    """Grid over pts_delta/pie_delta -- the two weights that trade
    against each other (pure scoring jump vs. all-around improvement)."""
    pts_values = [0.5, 1.0, 2.0, 4.0]
    pie_values = [0.0, 10.0, 20.0, 40.0, 80.0]

    results = []
    for pts in pts_values:
        for pie in pie_values:
            weights = dict(MIP_WEIGHTS, pts_delta=pts, pie_delta=pie)
            result = score_weights(all_features, weights)
            results.append((weights, result))

    results.sort(key=lambda r: (-r[1]["holdout"]["hit_rate"], -r[1]["holdout"]["top3_rate"]))
    print(f"{'pts_delta':>10} {'pie_delta':>10} {'train hit':>10} {'holdout hit':>12} {'holdout top3':>13}")
    for weights, result in results[:15]:
        t, h = result["train"], result["holdout"]
        print(f"{weights['pts_delta']:>10.1f} {weights['pie_delta']:>10.1f} "
              f"{t['hit_rate']:>9.0%} {h['hit_rate']:>11.0%} {h['top3_rate']:>12.0%}")

    best_weights, best_result = results[0]
    print()
    _print_result(f"BEST ON HOLDOUT: pts_delta={best_weights['pts_delta']}, pie_delta={best_weights['pie_delta']}",
                   best_result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", action="store_true", help="grid-sweep pts_delta/pie_delta weights")
    args = parser.parse_args()

    all_features = _build_all_features()

    if args.sweep:
        sweep(all_features)
    else:
        result = score_weights(all_features, MIP_WEIGHTS)
        _print_result(f"CURRENT MIP_WEIGHTS: {MIP_WEIGHTS}", result)
