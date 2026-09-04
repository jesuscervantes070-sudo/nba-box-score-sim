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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import game_engine
from loader import load_teams, load_schedule
from game_engine import simulate_game, compute_league_averages
from data_source import fetch_real_standings

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


def run_accuracy_benchmark(season: str = "2025-26", n_runs: int = 30) -> dict:
    """
    Simulates `n_runs` independent full seasons (same real schedule
    every time, different random outcomes) and returns a structured
    accuracy report: per-team real-vs-simulated win totals (every run,
    not just the mean, so future comparisons can look at spread too),
    league-wide standings accuracy (MAE, correlation), and league-wide
    player shooting/scoring bias (sim minus real, aggregated the same
    "derive from summed makes/attempts" way db.py always does).
    """
    teams = load_teams()
    schedule = load_schedule()
    league_avg = compute_league_averages(teams)
    real_standings = fetch_real_standings(season)

    real_players = {p.name: p for t in teams.values() for p in t.players}

    sim_wins_by_team: Dict[str, List[int]] = {name: [] for name in teams}
    # Aggregated across every simulated game, every run -- this is what
    # player-level bias gets computed from, same reasoning as
    # playoffs.compute_series_player_averages (derive PTS/FG% from
    # summed makes/attempts, never average a per-game % directly).
    player_totals: Dict[str, dict] = {}

    t0 = time.time()
    for _ in range(n_runs):
        wins = {name: 0 for name in teams}
        for g in schedule:
            home, away = teams[g.home_team], teams[g.away_team]
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
    parser.add_argument("--label", default="pre_injury_baseline",
                         help="output filename (without .json) under benchmarks/")
    args = parser.parse_args()

    report = run_accuracy_benchmark(season=args.season, n_runs=args.runs)
    print_summary(report)

    BENCHMARKS_DIR.mkdir(exist_ok=True)
    out_path = BENCHMARKS_DIR / f"{args.label}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nSaved to {out_path}")
