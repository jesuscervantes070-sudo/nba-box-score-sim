"""
Backtests and calibrates awards.DPOY_WEIGHTS against who REALLY won
Defensive Player of the Year -- same train/holdout-by-time methodology
as backtest_mvp.py/backtest_roy.py (see backtest_mvp.py's docstring).

No live network fetch needed here (unlike MVP/ROY's team win_pct) --
dpoy_features_for_season is built entirely from already-cached data
(rosters.json + team_defense.json + player_advanced.json), so this
runs fast, no per-season API calls or sleeps.

Usage:
    python3 backtest_dpoy.py                  # score the current DPOY_WEIGHTS
    python3 backtest_dpoy.py --sweep          # grid-sweep weights, report best on holdout
"""
import argparse

from loader import available_seasons
from awards import REAL_DPOY_WINNERS, DPOY_WEIGHTS, dpoy_features_for_season, rank_dpoy_candidates

HOLDOUT_FIRST_SEASON = "2016-17"


def _build_all_features() -> dict:
    seasons = [s for s in available_seasons() if s in REAL_DPOY_WINNERS]
    return {season: dpoy_features_for_season(season) for season in seasons}


def score_weights(all_features: dict, weights: dict) -> dict:
    def _run(seasons):
        hits, top3, misses = 0, 0, []
        for season in seasons:
            features = all_features[season]
            if not features:
                continue
            ranked = rank_dpoy_candidates(features, season, weights)
            names = [name for name, _ in ranked]
            real = REAL_DPOY_WINNERS[season]
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
    """
    Grid over stl/blk/team_def_strength -- the three weights most
    likely to trade against each other (a shot-blocking big vs. a
    ball-hawking guard vs. pure team context).
    """
    stl_values = [8.0, 15.0, 22.0]
    blk_values = [6.0, 10.0, 14.0]
    team_values = [10.0, 20.0, 30.0]

    results = []
    for stl in stl_values:
        for blk in blk_values:
            for team in team_values:
                weights = dict(DPOY_WEIGHTS, stl=stl, blk=blk, team_def_strength=team)
                result = score_weights(all_features, weights)
                results.append((weights, result))

    results.sort(key=lambda r: (-r[1]["holdout"]["hit_rate"], -r[1]["holdout"]["top3_rate"]))
    print(f"{'stl':>6} {'blk':>6} {'team':>6} {'train hit':>10} {'holdout hit':>12} {'holdout top3':>13}")
    for weights, result in results[:15]:
        t, h = result["train"], result["holdout"]
        print(f"{weights['stl']:>6.1f} {weights['blk']:>6.1f} {weights['team_def_strength']:>6.1f} "
              f"{t['hit_rate']:>9.0%} {h['hit_rate']:>11.0%} {h['top3_rate']:>12.0%}")

    best_weights, best_result = results[0]
    print()
    _print_result(f"BEST ON HOLDOUT: stl={best_weights['stl']}, blk={best_weights['blk']}, "
                   f"team_def_strength={best_weights['team_def_strength']}", best_result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", action="store_true", help="grid-sweep stl/blk/team_def_strength weights")
    args = parser.parse_args()

    all_features = _build_all_features()

    if args.sweep:
        sweep(all_features)
    else:
        result = score_weights(all_features, DPOY_WEIGHTS)
        _print_result(f"CURRENT DPOY_WEIGHTS: {DPOY_WEIGHTS}", result)
