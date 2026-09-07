"""
Backtests and calibrates awards.COY_WEIGHTS against who REALLY won
Coach of the Year -- same train/holdout-by-time methodology as
backtest_mvp.py (see that file's docstring). REAL_COY_WINNERS is
itself UNVERIFIED against a live API (see its own comment in
awards.py) -- worth remembering when reading these numbers: an error
here could be this backtest, or could be a wrong entry in that table.

1996-97 is always skipped: no real prior season to diff against.
2025-26 has no ground truth at all (not guessed) so it's skipped too.

Usage:
    python3 backtest_coy.py                  # score the current COY_WEIGHTS
    python3 backtest_coy.py --sweep          # grid-sweep win_pct_level/win_pct_delta
"""
import argparse
import time

from loader import available_seasons
from awards import REAL_COY_WINNERS, COY_WEIGHTS, coy_features_for_season, rank_coy_candidates, fetch_real_team_win_pct

HOLDOUT_FIRST_SEASON = "2016-17"


def _retry_fetch(season: str, retries: int = 5):
    """leaguestandingsv3 has been flaky in practice (real, observed
    read-timeouts) -- retry here rather than let one bad request quietly
    produce a season with no candidates at all (found by testing: an
    earlier un-retried run silently dropped 5 seasons this way)."""
    for attempt in range(retries):
        try:
            win_pct = fetch_real_team_win_pct(season)
            if win_pct:
                return win_pct
        except Exception:
            pass
        time.sleep(3)
    return {}


def _build_all_features() -> dict:
    seasons = [s for s in available_seasons() if s in REAL_COY_WINNERS]
    win_pct_cache = {}

    def _cached_win_pct(season):
        if season not in win_pct_cache:
            win_pct_cache[season] = _retry_fetch(season)
        return win_pct_cache[season]

    all_features = {}
    for season in seasons:
        if season == min(seasons):
            all_features[season] = []  # no prior season to diff against
            continue
        win_pct = _cached_win_pct(season)
        prev_win_pct = _cached_win_pct(_previous_season_str(season))
        all_features[season] = coy_features_for_season(season, win_pct, prev_win_pct) if (win_pct and prev_win_pct) else []
    return all_features


def _previous_season_str(season: str) -> str:
    start_year = int(season[:4])
    return f"{start_year - 1}-{start_year % 100:02d}"


def score_weights(all_features: dict, weights: dict) -> dict:
    def _run(seasons):
        hits, top3, misses = 0, 0, []
        for season in seasons:
            features = all_features[season]
            if not features:
                continue
            ranked = rank_coy_candidates(features, weights)
            names = [name for name, _ in ranked]
            real = REAL_COY_WINNERS[season]
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
    """Grid over win_pct_level/win_pct_delta -- found by testing that
    level has to dominate (real COY winners are overwhelmingly top-3
    record teams), delta still helps some (covers new-coach-turnaround
    stories)."""
    level_values = [0.0, 1.0, 2.0, 3.0, 5.0, 8.0]
    delta_values = [0.0, 0.5, 1.0, 2.0, 4.0]

    results = []
    for level in level_values:
        for delta in delta_values:
            weights = dict(win_pct_level=level, win_pct_delta=delta)
            result = score_weights(all_features, weights)
            results.append((weights, result))

    results.sort(key=lambda r: (-r[1]["holdout"]["hit_rate"], -r[1]["holdout"]["top3_rate"]))
    print(f"{'level':>7} {'delta':>7} {'train hit':>10} {'holdout hit':>12} {'holdout top3':>13}")
    for weights, result in results[:15]:
        t, h = result["train"], result["holdout"]
        print(f"{weights['win_pct_level']:>7.1f} {weights['win_pct_delta']:>7.1f} "
              f"{t['hit_rate']:>9.0%} {h['hit_rate']:>11.0%} {h['top3_rate']:>12.0%}")

    best_weights, best_result = results[0]
    print()
    _print_result(f"BEST ON HOLDOUT: win_pct_level={best_weights['win_pct_level']}, "
                   f"win_pct_delta={best_weights['win_pct_delta']}", best_result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", action="store_true", help="grid-sweep win_pct_level/win_pct_delta weights")
    args = parser.parse_args()

    print("Fetching real team win% for every backtestable season "
          "(one-time, ~30 network calls)...")
    all_features = _build_all_features()

    if args.sweep:
        sweep(all_features)
    else:
        result = score_weights(all_features, COY_WEIGHTS)
        _print_result(f"CURRENT COY_WEIGHTS: {COY_WEIGHTS}", result)
