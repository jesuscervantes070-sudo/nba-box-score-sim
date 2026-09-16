"""Runner for MARGIN COMPRESSION DIAGNOSTIC V1. Diagnostic/measurement only -- writes
backtests/margin_compression_diagnostic_v1.json. Never touches BACKTEST_V1_HOLDOUT or any
production parameter. Run directly: python3 run_margin_compression_diagnostic_v1.py
"""
import json
import time
from pathlib import Path

import margin_compression_diagnostic as mcd
import historical_game_snapshot as hgs

BACKTESTS_DIR = Path("backtests")


def pick_sim_subset(records, n_per_bucket=4):
    """Deterministic stratified subset by |strength_diff| quartile-ish buckets, for the expensive
    simulation-based diagnostics. Selection is based on REAL, pregame-known net-rating strength
    differential (never on simulated results)."""
    by_game = {}
    for r in records:
        by_game.setdefault(r["game_id"], []).append(r)
    game_diffs = []
    for gid, sides in by_game.items():
        diff = abs(sides[0]["strength_diff"])
        game_diffs.append((gid, diff))
    buckets = {"near_equal": [], "modest": [], "large": [], "extreme": []}
    for gid, diff in sorted(game_diffs):  # sorted by game_id for determinism
        bucket = "near_equal" if diff < 3 else "modest" if diff < 8 else "large" if diff < 15 else "extreme"
        if len(buckets[bucket]) < n_per_bucket:
            buckets[bucket].append(gid)
    subset = sorted(set(g for lst in buckets.values() for g in lst))
    return subset, buckets


def main():
    t_start = time.time()
    BACKTESTS_DIR.mkdir(exist_ok=True)
    dev_games = mcd.load_or_create_development_sample()
    print(f"Development sample: {len(dev_games)} games")

    print("\n--- Building dev team records (snapshot-only, all dev games) ---")
    t0 = time.time()
    records = mcd.build_dev_team_records(dev_games)
    print(f"  {len(records)} team-records built in {time.time()-t0:.1f}s")

    print("\n--- Correlating engine inputs with real team strength ---")
    correlations = mcd.correlate_fields_with_strength(records)

    print("\n--- Strong vs weak quartile comparison ---")
    quartiles = mcd.strong_vs_weak_quartiles(records)

    print("\n--- Year-over-year truth lag ---")
    yoy = mcd.year_over_year_truth_lag(records)

    print("\n--- Raw -> truth -> engine variance transfer (three_point) ---")
    variance_transfer = mcd.raw_truth_engine_variance_transfer(records)

    print("\n--- Home-court contribution estimate ---")
    home_court = mcd.home_court_contribution_estimate(records)

    print("\n--- Fixed-five / bench information loss (ORACLE mode, all dev games) ---")
    t0 = time.time()
    bench_rows = mcd.fixed_five_information_loss(dev_games)
    print(f"  {len(bench_rows)} rows in {time.time()-t0:.1f}s")
    rotation_vs_top5 = mcd.rotation_weighted_vs_top5_correlation(bench_rows)

    print("\n--- Adapter compression check ---")
    sample_triples = []
    seen = set()
    for r in records:
        for group in (r["primary_five_summary"],):
            pass
    # draw real (player_id, date, season) triples from the primary-five of a sample of dev games
    triples = []
    for gid in dev_games[:20]:
        try:
            snap = hgs.build_historical_game_snapshot(gid, mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        for team_snap in (snap.home_team_snapshot, snap.away_team_snapshot):
            for p in team_snap.players:
                if p.is_primary_five and p.player_id not in seen:
                    seen.add(p.player_id)
                    triples.append((p.player_id, snap.game_date, mcd.SEASON))
    adapter_check = mcd.adapter_compression_check(triples)

    print("\n--- Role amplification diagnostic (isolated synthetic) ---")
    role_amp = mcd.role_amplification_diagnostic(n_sims=200)

    print("\n--- Selecting SIM subset for simulation-based diagnostics ---")
    sim_subset, buckets = pick_sim_subset(records, n_per_bucket=4)
    print(f"  SIM subset: {len(sim_subset)} games -> {buckets}")

    print("\n--- Matchup-strength compression curve ---")
    t0 = time.time()
    compression_curve = mcd.matchup_strength_compression_curve(sim_subset, n_sims=50)
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- Simulated vs real team shooting splits ---")
    t0 = time.time()
    real_splits = mcd.real_team_shooting_splits(sim_subset)
    sim_splits = mcd.simulated_team_shooting_splits(sim_subset, n_sims=50)
    print(f"  done in {time.time()-t0:.1f}s")
    common_teams = sorted(set(real_splits) & set(sim_splits))
    import statistics as _st
    offense_variance = {}
    for stat in ("two_pt_pct", "three_pt_pct", "ft_rate"):
        real_vals = [real_splits[t][stat] for t in common_teams if real_splits[t][stat] is not None]
        sim_vals = [sim_splits[t][stat] for t in common_teams if sim_splits[t].get(stat) is not None]
        offense_variance[stat] = {
            "n_teams": len(common_teams),
            "real_sd": (_st.pstdev(real_vals) if len(real_vals) > 1 else None),
            "simulated_sd": (_st.pstdev(sim_vals) if len(sim_vals) > 1 else None),
        }

    print("\n--- Simulated possessions (pace) variance ---")
    t0 = time.time()
    pace_variance = mcd.simulated_possessions_variance(sim_subset, n_sims=30)
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- Monte Carlo noise check (n=250 vs n=1000, 3 games) ---")
    t0 = time.time()
    mc_noise = mcd.monte_carlo_noise_check(sim_subset[:3], counts=(250, 1000))
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- Star / weak-player replacement counterfactuals ---")
    t0 = time.time()
    counterfactuals = []
    # pick 3 STRONG-team games: replace their best primary-five scorer with league-average
    strong_games = sorted(records, key=lambda r: -r["net_rating"])[:6]
    seen_games = set()
    for r in strong_games:
        if r["game_id"] in seen_games or len(counterfactuals) >= 3:
            continue
        seen_games.add(r["game_id"])
        try:
            snap = hgs.build_historical_game_snapshot(r["game_id"], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        team_snap = snap.home_team_snapshot if snap.home_team == r["team"] else snap.away_team_snapshot
        p5 = [p for p in team_snap.players if p.is_primary_five]
        best = max(p5, key=lambda p: (p.simulation_profile.three_point_shrunk_rate or 0) + (p.simulation_profile.rim_finishing_shrunk_rate or 0))
        side = team_snap.side
        result = mcd.player_replacement_counterfactual(r["game_id"], side, best.player_id, n_sims=150)
        result["experiment"] = "replace_best_scorer_on_STRONG_team_with_average"
        result["team"] = r["team"]
        counterfactuals.append(result)

    weak_games = sorted(records, key=lambda r: r["net_rating"])[:6]
    seen_games2 = set()
    for r in weak_games:
        if r["game_id"] in seen_games2 or len([c for c in counterfactuals if c["experiment"].endswith("WEAK_team_with_average")]) >= 3:
            continue
        seen_games2.add(r["game_id"])
        try:
            snap = hgs.build_historical_game_snapshot(r["game_id"], mcd.SEASON, mcd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        team_snap = snap.home_team_snapshot if snap.home_team == r["team"] else snap.away_team_snapshot
        p5 = [p for p in team_snap.players if p.is_primary_five]
        worst = min(p5, key=lambda p: (p.simulation_profile.three_point_shrunk_rate or 0) + (p.simulation_profile.rim_finishing_shrunk_rate or 0))
        side = team_snap.side
        result = mcd.player_replacement_counterfactual(r["game_id"], side, worst.player_id, n_sims=150)
        result["experiment"] = "replace_worst_scorer_on_WEAK_team_with_average"
        result["team"] = r["team"]
        counterfactuals.append(result)
    print(f"  {len(counterfactuals)} counterfactual experiments done in {time.time()-t0:.1f}s")

    total_time = time.time() - t_start
    print(f"\n=== TOTAL DIAGNOSTIC RUNTIME: {total_time:.1f}s ===")

    output = {
        "development_sample": {"n_games": len(dev_games), "game_ids": dev_games},
        "sim_subset": {"n_games": len(sim_subset), "game_ids": sim_subset, "buckets": buckets},
        "team_strength_correlations": correlations,
        "strong_vs_weak_quartiles": quartiles,
        "year_over_year_truth_lag": yoy,
        "raw_truth_engine_variance_transfer": variance_transfer,
        "home_court_contribution_estimate": home_court,
        "fixed_five_bench_rows_sample": bench_rows[:10],
        "rotation_weighted_vs_top5_correlation": rotation_vs_top5,
        "bench_minute_share_summary": {
            "mean_bench_minute_share": rotation_vs_top5["mean_bench_minute_share"],
        },
        "adapter_compression_check": adapter_check,
        "role_amplification_diagnostic": role_amp,
        "matchup_strength_compression_curve": compression_curve,
        "offense_output_variance (real_sd vs simulated_sd)": offense_variance,
        "pace_variance": pace_variance,
        "monte_carlo_noise_check": mc_noise,
        "player_replacement_counterfactuals": counterfactuals,
        "runtime_seconds": total_time,
    }
    with open(BACKTESTS_DIR / "margin_compression_diagnostic_v1.json", "w") as f:
        json.dump(output, f, indent=2, sort_keys=True, default=str)
    print("\nWROTE backtests/margin_compression_diagnostic_v1.json")


if __name__ == "__main__":
    main()
