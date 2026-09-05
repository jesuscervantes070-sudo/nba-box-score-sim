"""
Tunes game_engine's constants against EVERY backtested season at once,
instead of one season at a time.

Why this exists
---------------
Every tuned constant in game_engine.py was chosen honestly -- by testing
against real data rather than guessing -- but almost all of them were
tested against ONE season, 2025-26. DEFENSE_AMPLIFICATION is the clearest
case: it was picked by sweeping 1x-8x against 2025-26's real standings.
That answers "what fits 2025-26 best," which is not the same question as
"what fits real NBA basketball best," and the 30-season backtest set now
makes the difference measurable.

It matters, too. Comparing the 29 saved backtest snapshots against real
standings turned up a bias that a single season could never have shown,
because it is invisible in any one season's headline number:

  - The sim spreads win totals ~37% WIDER than real basketball does
    (simulated st-dev 16.6 wins vs. a real 12.1), in all 29 seasons --
    not one exception, in either direction.
  - Good defenses get over-predicted and good offenses under-predicted,
    by nearly equal and opposite amounts (correlation of a team's real
    defensive quality with the sim's win error, after the width above is
    factored out: +0.33; the same for offensive quality: -0.31; again
    with the same sign in all 29 seasons).

Both are the same root cause: a team's own offense enters a simulated
game at its literal real strength, while the opponent's defense enters
amplified 5x, so defense decides far too much of who wins. See
game_engine's DEFENSE_AMPLIFICATION comment for how that 5 was arrived
at, and the module docstring's FIFTH DEFENSE fix for why amplifying at
all is still the right idea.

Fitting honestly, not curve-fitting
-----------------------------------
Thirty seasons and a handful of knobs is more than enough rope to overfit
-- to find numbers that flatter this specific set of past seasons and
generalize to nothing. So seasons are SPLIT, and the split is by time,
never shuffled: constants get fit on the older TRAIN seasons and scored
on the newer HOLDOUT ones the fit never saw. A setting only counts as a
real improvement if it improves BOTH. A setting that improves train while
holdout gets worse is the signature of overfitting, and this file prints
the two side by side specifically so that is impossible to miss.

What it measures (in the user's stated priority order)
------------------------------------------------------
Standings accuracy is the target; player stat bias is secondary, tracked
so a constant that quietly wrecks it can't slip through unnoticed.

  mae          -- mean absolute error in team win totals. THE headline.
  correlation  -- real vs. simulated win totals. Ranking quality.
  spread_ratio -- simulated win st-dev / real win st-dev. 1.0 is perfect;
                  today's ~1.37 is the over-spread above. Worth watching
                  separately from mae because it is the one thing a
                  correlation can't see: multiplying every simulated win
                  total's distance from .500 by a constant leaves
                  correlation mathematically UNCHANGED while making mae
                  much worse. A sim can rank all 30 teams essentially
                  right and still miss every win total badly.
  fg_pct_bias  -- league-wide simulated minus real player FG%. Secondary.

Usage:
    python3 sweep_constants.py --constant DEFENSE_AMPLIFICATION \\
                               --values 1,1.5,2,2.5,3,4,5
    python3 sweep_constants.py --constant DEFENSE_AMPLIFICATION \\
                               --values 2,3 --runs 10 --label defense_amp
"""
import argparse
import json
import statistics
import time
from itertools import product
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import game_engine
from benchmark_accuracy import run_accuracy_benchmark, _git_commit
from data_source import fetch_real_standings

CACHE_DIR = Path(__file__).parent / "cache"
SWEEPS_DIR = Path(__file__).parent / "sweeps"

# Every file loader.py needs before a season can be simulated at all. A
# season's cache folder can exist while still being empty or partial
# (1995-96's does -- it's the probe that confirmed the stats API has
# nothing before 1996-97), so presence of the folder proves nothing.
REQUIRED_CACHE_FILES = (
    "rosters.json",
    "schedule.json",
    "team_defense.json",
    "team_conferences.json",
    "injuries.json",
    "roster_membership.json",
)

# The first HOLDOUT season: everything before it trains, it and
# everything after is scored but never fit against. Chosen to put
# roughly a third of the seasons in holdout while keeping the split on a
# plain calendar boundary rather than one picked to make some result look
# good. Seasons sort correctly as plain strings here ("1996-97" <
# "2016-17") because they're all zero-padded and same-length.
HOLDOUT_FIRST_SEASON = "2016-17"

# Real final standings never change for a long-finished season, but
# they're deliberately not cached to disk (see
# data_source.fetch_real_standings). A sweep re-simulates every season
# once per candidate setting, so without this each season's identical
# standings would be re-fetched over the network dozens of times per
# sweep. Kept only for the life of one sweep -- nothing is written to
# disk, so the "not cached" decision that file made still stands.
_standings_cache: Dict[str, Dict[str, int]] = {}


def _real_standings(season: str) -> Dict[str, int]:
    if season not in _standings_cache:
        _standings_cache[season] = fetch_real_standings(season)
    return _standings_cache[season]


def available_seasons() -> List[str]:
    """Every season whose cache is COMPLETE enough to simulate, oldest
    first. Derived by looking at what's actually on disk rather than
    hardcoding a list, so fetching another season makes it sweepable
    with no edit here."""
    seasons = []
    for path in sorted(CACHE_DIR.iterdir()):
        if not path.is_dir():
            continue
        if all((path / f).exists() for f in REQUIRED_CACHE_FILES):
            seasons.append(path.name)
    return seasons


def _season_metrics(report: dict) -> dict:
    """The four numbers this file compares settings on, pulled out of one
    season's full benchmark report -- see the module docstring for what
    each means and why spread_ratio is tracked separately from mae."""
    teams = report["teams"].values()
    real_wins = [t["real_wins"] for t in teams]
    sim_wins = [t["sim_wins_mean"] for t in teams]
    sd_real = statistics.pstdev(real_wins)
    sd_sim = statistics.pstdev(sim_wins)
    return {
        "mae": report["standings_accuracy"]["single_run_mae_mean"],
        "correlation": report["standings_accuracy"]["correlation_real_vs_sim"],
        # A real season with zero spread in win totals is impossible, but
        # dividing by it would crash a whole sweep -- guard anyway.
        "spread_ratio": sd_sim / sd_real if sd_real else float("nan"),
        "fg_pct_bias_pp": report["player_bias"]["fg_pct_bias_pp"],
    }


def evaluate_setting(setting: Dict[str, float], seasons: List[str],
                     n_runs: int, use_injuries: bool = True,
                     use_real_moves: bool = True) -> dict:
    """
    Simulates every season in `seasons` with game_engine's constants set
    to `setting` ({constant_name: value}), and returns that setting's
    per-season and averaged metrics.

    The constants are set by assigning onto the game_engine MODULE rather
    than editing the file. That works -- and keeps a sweep from having to
    rewrite source code mid-run -- because every one of them is read by
    name inside the function that uses it, each time it's called, not
    captured once when the module is imported. The original values are
    always restored afterwards, including if a run raises, so a sweep can
    never leave the module quietly mutated for whatever runs next.

    Injuries and real trades default ON: they're what a real simulated
    season actually uses, and every saved backtest snapshot this is meant
    to be comparable against was produced with both enabled.
    """
    previous = {name: getattr(game_engine, name) for name in setting}
    for name, value in setting.items():
        setattr(game_engine, name, value)

    per_season = {}
    try:
        for season in seasons:
            report = run_accuracy_benchmark(
                season=season, n_runs=n_runs,
                use_injuries=use_injuries, use_real_moves=use_real_moves,
                real_standings=_real_standings(season),
            )
            per_season[season] = _season_metrics(report)
    finally:
        for name, value in previous.items():
            setattr(game_engine, name, value)

    # Split into the two groups scored separately -- see the module
    # docstring on why a fit is only believable if both move together.
    train = [s for s in per_season if s < HOLDOUT_FIRST_SEASON]
    holdout = [s for s in per_season if s >= HOLDOUT_FIRST_SEASON]

    def summarize(group: List[str]) -> dict:
        if not group:
            return {}
        return {
            key: statistics.mean(per_season[s][key] for s in group)
            for key in ("mae", "correlation", "spread_ratio", "fg_pct_bias_pp")
        }

    return {
        "setting": setting,
        "train": summarize(train),
        "holdout": summarize(holdout),
        "all": summarize(list(per_season)),
        "per_season": per_season,
    }


def print_sweep(results: List[dict]) -> None:
    """One row per setting, train and holdout side by side. Sorted by
    HOLDOUT mae -- the number that says whether a setting generalizes,
    rather than train mae, which a sufficiently overfit setting can
    always drive down."""
    label_width = max(len(_setting_label(r["setting"])) for r in results)
    label_width = max(label_width, len("SETTING"))

    print()
    print(f"{'':{label_width}}   {'---------- TRAIN ----------':^27}   "
          f"{'--------- HOLDOUT ---------':^27}")
    print(f"{'SETTING':<{label_width}}   {'MAE':>6}{'CORR':>7}{'SPREAD':>8}{'FGBIAS':>7}   "
          f"{'MAE':>6}{'CORR':>7}{'SPREAD':>8}{'FGBIAS':>7}")

    for r in sorted(results, key=lambda r: r["holdout"]["mae"]):
        t, h = r["train"], r["holdout"]
        print(f"{_setting_label(r['setting']):<{label_width}}   "
              f"{t['mae']:>6.2f}{t['correlation']:>7.3f}{t['spread_ratio']:>8.3f}"
              f"{t['fg_pct_bias_pp']:>+7.2f}   "
              f"{h['mae']:>6.2f}{h['correlation']:>7.3f}{h['spread_ratio']:>8.3f}"
              f"{h['fg_pct_bias_pp']:>+7.2f}")

    best = min(results, key=lambda r: r["holdout"]["mae"])
    print()
    print(f"Best on holdout MAE: {_setting_label(best['setting'])} "
          f"({best['holdout']['mae']:.2f} wins, spread ratio "
          f"{best['holdout']['spread_ratio']:.3f} where 1.000 is real)")


def _setting_label(setting: Dict[str, float]) -> str:
    """"DEFENSE_AMPLIFICATION=2" -- or several joined, once more than one
    constant is being swept at a time."""
    return " ".join(f"{name}={value:g}" for name, value in setting.items())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--constant", required=True,
                        help="game_engine constant to sweep, e.g. DEFENSE_AMPLIFICATION. "
                             "Comma-separate two or more to sweep them TOGETHER, e.g. "
                             "DEFENSE_AMPLIFICATION,OFFENSE_AMPLIFICATION")
    parser.add_argument("--values", required=True,
                        help="comma-separated values to try, e.g. 1,2,3,4,5. When sweeping "
                             "several constants, separate each one's values with a semicolon "
                             "(e.g. '1.5,2,2.5;0,0.5,1') and every combination is tried. "
                             "Constants that interact have to be swept jointly like this: "
                             "tuning one and then the other finds whatever the first pass "
                             "happened to leave behind, not the best pair.")
    parser.add_argument("--runs", type=int, default=10,
                        help="simulated seasons per season per setting (default 10 -- "
                             "lower than benchmark_accuracy.py's 30 because a sweep "
                             "averages over ~30 seasons as well, so there's far more "
                             "total signal behind each row than one season's 30 runs)")
    parser.add_argument("--seasons", default=None,
                        help="comma-separated seasons to sweep (default: every complete "
                             "cached season)")
    parser.add_argument("--label", default=None,
                        help="output filename (without .json) under sweeps/")
    args = parser.parse_args()

    constants = args.constant.split(",")
    for name in constants:
        if not hasattr(game_engine, name):
            raise SystemExit(f"game_engine has no constant named {name!r}")

    value_lists = [[float(v) for v in group.split(",")] for group in args.values.split(";")]
    if len(value_lists) != len(constants):
        raise SystemExit(
            f"got {len(constants)} constants but {len(value_lists)} semicolon-separated "
            f"value groups -- they have to match one-to-one")

    # Every combination of every constant's values. With one constant
    # this is just that constant's list, so the single-knob case reads
    # exactly as before.
    settings = [dict(zip(constants, combo)) for combo in product(*value_lists)]

    seasons = args.seasons.split(",") if args.seasons else available_seasons()
    n_train = sum(1 for s in seasons if s < HOLDOUT_FIRST_SEASON)
    print(f"Sweeping {' x '.join(constants)} -- {len(settings)} combinations")
    print(f"{len(seasons)} seasons ({n_train} train / {len(seasons) - n_train} holdout "
          f"from {HOLDOUT_FIRST_SEASON}), {args.runs} runs each")

    t0 = time.time()
    results = []
    for setting in settings:
        started = time.time()
        result = evaluate_setting(setting, seasons, args.runs)
        results.append(result)
        print(f"  {_setting_label(setting)}: train MAE {result['train']['mae']:.2f}, "
              f"holdout MAE {result['holdout']['mae']:.2f}  ({time.time() - started:.0f}s)",
              flush=True)

    print_sweep(results)

    # Joined with "_x_" rather than the comma the CLI takes, so a joint
    # sweep's default filename stays a clean filename.
    label = args.label or "sweep_" + "_x_".join(c.lower() for c in constants)
    SWEEPS_DIR.mkdir(exist_ok=True)
    out_path = SWEEPS_DIR / f"{label}.json"
    with open(out_path, "w") as f:
        json.dump({
            "meta": {
                "constants": constants,
                "values": value_lists,
                "seasons": seasons,
                "holdout_first_season": HOLDOUT_FIRST_SEASON,
                "n_runs": args.runs,
                "elapsed_seconds": round(time.time() - t0, 1),
                "git_commit": _git_commit(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "results": results,
        }, f, indent=2)
    print(f"\nSaved to {out_path}")
