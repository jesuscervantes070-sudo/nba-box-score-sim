"""
Runs a batch of full simulated seasons and saves a structured accuracy
snapshot to benchmarks/<label>.json -- a permanent, comparable record
of "how close was the sim to real standings/player stats" at a given
point in the project, rather than throwaway numbers that vanish at the
end of a terminal session.

The direct reason this exists: injuries aren't built yet. Right now,
every simulated player plays a full healthy season even if their real
counterpart missed real games to a real injury -- so today's accuracy
numbers are really "how close is the sim to a fully-healthy hypothetical
season," not "how close is the sim to what actually happened." That's
an intentional, expected gap, not sim error (see the project's saved
"sim accuracy vs. injuries" note). Once an injury system exists, the
real test is whether TURNING IT ON moves these same numbers closer to
real standings -- which only means something if there's an honest
"before injuries" snapshot to compare against. That's what this file
produces.

Usage:
    python3 benchmark_accuracy.py --runs 30 --label pre_injury_baseline
"""
import argparse
import json
import statistics
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import game_engine
from loader import load_teams, load_schedule, load_player_injuries, load_roster_membership, load_league_pace_variation
from game_engine import simulate_game, compute_league_averages
from data_source import fetch_real_standings
from injuries import build_season_injuries, missed_lookup, enforce_minimum_roster
from transactions import expand_rosters_with_real_moves

BENCHMARKS_DIR = Path(__file__).parent / "benchmarks"


def _git_commit() -> str:
    """The exact commit this benchmark was run against -- so a saved
    snapshot can always be traced back to the code that produced it,
    even if the code changes again later."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, text=True,
        ).strip()
    except Exception:
        return "unknown"


def run_accuracy_benchmark(season: str = "2025-26", n_runs: int = 30,
                            use_injuries: bool = False, use_real_moves: bool = False,
                            real_standings: Dict[str, int] = None) -> dict:
    """
    Simulates `n_runs` independent full seasons (same real schedule
    every time, different random outcomes) and returns a structured
    accuracy report: per-team real-vs-simulated win totals (every run,
    not just the mean, so future comparisons can look at spread too),
    league-wide standings accuracy (MAE, correlation), and league-wide
    player shooting/scoring bias (sim minus real, aggregated the same
    "derive from summed makes/attempts" way db.py always does).

    `use_injuries=True` benches injuries.py's roster-filtering into
    every run -- a FRESH injury calendar per run (placement is
    randomized per call, same as a real season.py run), not the same
    one reused across all n_runs.

    `use_real_moves=True` benches transactions.py's real in-season
    trades in -- unlike injuries, this is deterministic (no randomness
    in WHICH games a trade affects, only in what happens to the ball
    once real rosters are set), so it's applied ONCE before the loop,
    not re-rolled every run.

    Both are independent toggles, on top of the pre-existing "before
    injuries" baseline -- this is the "after" side of the comparison
    this whole file exists for, see the module docstring.

    `real_standings` is an optional escape hatch for callers that
    benchmark the SAME season many times over (sweep_constants.py runs
    every season once per candidate constant setting). Real standings
    are deliberately not cached to disk -- see
    data_source.fetch_real_standings for why -- so without this, a
    sweep would re-fetch the identical never-changing final standings
    of a long-finished season over the network dozens of times. Left
    as None (every pre-existing caller), it fetches exactly as before.
    """
    teams = load_teams(season)
    schedule = load_schedule(season)
    # See season.py -- the season's own real pace swing, not a constant.
    league_avg = compute_league_averages(teams, load_league_pace_variation(season))
    if real_standings is None:
        real_standings = fetch_real_standings(season)
    real_player_injuries = load_player_injuries(season) if use_injuries else None

    real_players = {p.name: p for t in teams.values() for p in t.players}

    # A snapshot of each team's ORIGINAL (pre-trade-expansion) player
    # list, by name -- injuries.build_season_injuries has to run
    # against this unexpanded view every single run, even though trade
    # expansion itself (below) only needs to happen once. Each player's
    # real absence data was measured against their one real FINAL team
    # (see data_source.fetch_player_absence_stints); reapplying it to a
    # team added later by trade expansion would be meaningless.
    original_players_by_team = (
        {name: list(t.players) for name, t in teams.items()} if use_injuries else None
    )

    # Real in-season trades: deterministic, so computed once, not
    # per-run. Mutates `teams` in place (see transactions.py) --
    # everything below this point sees the trade-expanded rosters.
    trade_unavailable = set()
    if use_real_moves:
        membership = load_roster_membership(season)
        trade_unavailable = expand_rosters_with_real_moves(teams, schedule, membership)

    sim_wins_by_team: Dict[str, List[int]] = {name: [] for name in teams}
    # Aggregated across every simulated game, every run -- this is what
    # player-level bias gets computed from, same reasoning as
    # playoffs.compute_series_player_averages (derive PTS/FG% from
    # summed makes/attempts, never average a per-game % directly).
    player_totals: Dict[str, dict] = {}

    t0 = time.time()
    for _ in range(n_runs):
        wins = {name: 0 for name in teams}
        out_lookup = set(trade_unavailable)
        if use_injuries:
            # Built against the pre-expansion snapshot -- see comment above.
            original_view = {
                name: replace(t, players=original_players_by_team[name]) for name, t in teams.items()
            }
            spans = build_season_injuries(original_view, schedule, real_player_injuries)
            out_lookup |= missed_lookup(spans)
        for g in schedule:
            home, away = teams[g.home_team], teams[g.away_team]
            if out_lookup:
                # Same roster-filtering season.py does per game -- see
                # its comment for why dataclasses.replace (a new Team,
                # not a mutated shared one) matters here.
                home = replace(home, players=enforce_minimum_roster(
                    [p for p in home.players if (p.name, g.game_id) not in out_lookup],
                    home.players))
                away = replace(away, players=enforce_minimum_roster(
                    [p for p in away.players if (p.name, g.game_id) not in out_lookup],
                    away.players))
            result = simulate_game(home, away, league_avg)
            if result.home_score > result.away_score:
                wins[g.home_team] += 1
            else:
                wins[g.away_team] += 1
            for p in result.home_players + result.away_players:
                if p.min == 0:
                    continue
                t = player_totals.setdefault(
                    p.name, {"fgm": 0.0, "fga": 0.0, "pts": 0.0, "games": 0},
                )
                t["fgm"] += p.fgm
                t["fga"] += p.fga
                t["pts"] += p.pts
                t["games"] += 1
        for name, w in wins.items():
            sim_wins_by_team[name].append(w)
    elapsed = time.time() - t0

    # -- Team-level standings accuracy --------------------------------
    teams_report = {}
    for name in teams:
        if name not in real_standings:
            continue
        runs = sim_wins_by_team[name]
        teams_report[name] = {
            "real_wins": real_standings[name],
            "sim_wins_by_run": runs,
            "sim_wins_mean": statistics.mean(runs),
            "sim_wins_stdev": statistics.pstdev(runs),
            "mean_error": statistics.mean(runs) - real_standings[name],
        }

    single_run_maes = [
        statistics.mean(abs(sim_wins_by_team[n][run] - real_standings[n])
                         for n in teams if n in real_standings)
        for run in range(n_runs)
    ]
    pairs = [(real_standings[n], teams_report[n]["sim_wins_mean"]) for n in teams_report]
    correlation = statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs])

    # -- League-wide player shooting/scoring bias ----------------------
    fg_diffs, fga_diffs, pts_diffs = [], [], []
    for name, t in player_totals.items():
        real_p = real_players.get(name)
        if real_p is None or t["fga"] == 0 or real_p.fga < 3:
            continue  # skip tiny real-volume players -- too noisy to compare meaningfully
        fg_diffs.append(t["fgm"] / t["fga"] - real_p.fg_pct)
        fga_diffs.append(t["fga"] / t["games"] - real_p.fga)
        pts_diffs.append(t["pts"] / t["games"] - real_p.pts)

    return {
        "meta": {
            "season": season,
            "n_runs": n_runs,
            "elapsed_seconds": round(elapsed, 1),
            "git_commit": _git_commit(),
            "defense_amplification": game_engine.DEFENSE_AMPLIFICATION,
            # Recorded alongside the defensive one because the two are
            # tuned as a PAIR (see game_engine.OFFENSE_AMPLIFICATION) --
            # a snapshot naming only one of them can't be reproduced.
            "offense_amplification": game_engine.OFFENSE_AMPLIFICATION,
            "injuries_enabled": use_injuries,
            "real_moves_enabled": use_real_moves,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "standings_accuracy": {
            "single_run_mae_mean": statistics.mean(single_run_maes),
            "single_run_mae_min": min(single_run_maes),
            "single_run_mae_max": max(single_run_maes),
            "correlation_real_vs_sim": correlation,
        },
        "player_bias": {
            "fg_pct_bias_pp": statistics.mean(fg_diffs) * 100,
            "fga_bias_per_game": statistics.mean(fga_diffs),
            "pts_bias_per_game": statistics.mean(pts_diffs),
            "n_players_compared": len(fg_diffs),
        },
        "teams": teams_report,
    }


def print_summary(report: dict) -> None:
    meta = report["meta"]
    acc = report["standings_accuracy"]
    bias = report["player_bias"]
    print(f"Benchmark: {meta['n_runs']} seasons simulated in {meta['elapsed_seconds']}s "
          f"(commit {meta['git_commit'][:8]}, DEFENSE_AMPLIFICATION={meta['defense_amplification']})")
    print(f"Standings: single-run MAE mean={acc['single_run_mae_mean']:.2f} "
          f"(min={acc['single_run_mae_min']:.1f}, max={acc['single_run_mae_max']:.1f}), "
          f"correlation={acc['correlation_real_vs_sim']:.3f}")
    print(f"Player bias: FG% {bias['fg_pct_bias_pp']:+.2f}pp, "
          f"FGA {bias['fga_bias_per_game']:+.2f}/game, "
          f"PTS {bias['pts_bias_per_game']:+.2f}/game "
          f"(n={bias['n_players_compared']} players)")

    rows = sorted(report["teams"].items(), key=lambda kv: -abs(kv[1]["mean_error"]))
    print()
    print("Largest team-level gaps (real wins vs. mean simulated wins):")
    print(f"  {'TEAM':<28}{'REAL W':>8}{'AVG SIM W':>11}{'ERROR':>8}{'STDEV':>8}")
    for name, t in rows[:8]:
        print(f"  {name:<28}{t['real_wins']:>8}{t['sim_wins_mean']:>11.1f}"
              f"{t['mean_error']:>+8.1f}{t['sim_wins_stdev']:>8.1f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--with-injuries", action="store_true",
                         help="bench injuries.py's roster-filtering into every run")
    parser.add_argument("--with-trades", action="store_true",
                         help="bench transactions.py's real in-season trades into every run")
    parser.add_argument("--label", default=None,
                         help="output filename (without .json) under benchmarks/ -- "
                              "defaults based on which of --with-injuries/--with-trades are set")
    parser.add_argument("--overwrite", action="store_true",
                         help="allow replacing an existing snapshot of the same name "
                              "(refused by default -- see below)")
    args = parser.parse_args()
    # Separate defaults on purpose -- so running with a new flag combo
    # without remembering to also pass --label can't silently overwrite
    # an existing snapshot (like pre_injury_baseline.json) this whole
    # comparison depends on.
    if args.label:
        label = args.label
    elif args.with_injuries and args.with_trades:
        label = "post_injury_and_trades"
    elif args.with_trades:
        label = "post_trades_only"
    elif args.with_injuries:
        label = "post_injury"
    else:
        label = "pre_injury_baseline"

    report = run_accuracy_benchmark(season=args.season, n_runs=args.runs,
                                     use_injuries=args.with_injuries, use_real_moves=args.with_trades)
    print_summary(report)

    BENCHMARKS_DIR.mkdir(exist_ok=True)
    out_path = BENCHMARKS_DIR / f"{label}.json"

    # A saved snapshot's whole value is being a PERMANENT record of how
    # accurate the sim was at one point in its history -- the "before"
    # half of every before/after comparison this file exists to make.
    # Overwriting one silently destroys that, and it's an easy mistake:
    # the flag-combo defaults above guard the four names they generate,
    # but an explicit --label sails straight past them (which is exactly
    # how two backtest snapshots got clobbered once and had to be
    # restored from git). So refuse by default and make the caller say
    # so out loud, rather than trusting whoever's running it to
    # remember which of ~60 filenames are already taken.
    if out_path.exists() and not args.overwrite:
        raise SystemExit(
            f"\n{out_path} already exists.\n"
            f"Saved benchmarks are permanent records -- replacing one destroys the\n"
            f"'before' side of a comparison. Pick a different --label, or pass\n"
            f"--overwrite if you really do mean to replace it.")

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nSaved to {out_path}")
