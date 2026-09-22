"""Runner for LARGE / EXTREME MATCHUP COMPRESSION DIAGNOSTIC V1. Diagnostic/measurement only --
writes backtests/large_extreme_matchup_diagnostic_v1.json. Never touches BACKTEST_V1_HOLDOUT or any
production parameter. Run directly: python3 run_large_extreme_matchup_diagnostic_v1.py
"""
import json
import time
from pathlib import Path

import large_extreme_matchup_diagnostic as led
import margin_compression_diagnostic as mcd

BACKTESTS_DIR = Path("backtests")


def main():
    t_start = time.time()
    BACKTESTS_DIR.mkdir(exist_ok=True)

    sample = led.load_or_create_large_extreme_sample(n_per_bucket=8)
    game_ids = sample["game_ids"]
    print(f"Large/extreme sample: {sample['n_games']} games "
          f"({len(sample['buckets']['large'])} large, {len(sample['buckets']['extreme'])} extreme)")

    print("\n--- 1. Reproduce compression on development data (large/extreme only) ---")
    t0 = time.time()
    compression_curve = mcd.matchup_strength_compression_curve(game_ids, n_sims=80)
    print(f"  done in {time.time()-t0:.1f}s: {compression_curve}")

    print("\n--- 2. Engine-input category gaps (scoring/playmaking/rebounding/defense/role) ---")
    t0 = time.time()
    category_gaps = led.category_gap_report(game_ids)
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- 3. Top-five vs full-rotation scoring-composite gap ---")
    t0 = time.time()
    composite_rows = led.scoring_composite_gap_rows(game_ids, n_sims=80)
    composite_corr = led.scoring_composite_correlations(composite_rows)
    print(f"  done in {time.time()-t0:.1f}s: {composite_corr}")

    print("\n--- 4. Bench quality: strong vs weak ---")
    t0 = time.time()
    bench_quality = led.bench_quality_strong_vs_weak(game_ids)
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- 5. Elite scoring stacking progression (synthetic) ---")
    t0 = time.time()
    elite_scoring_stack = led.stacking_progression(led.ELITE_SCORING_KWARGS, "elite_scoring", n_sims=150)
    print(f"  done in {time.time()-t0:.1f}s: {[p['mean_margin_stack_perspective'] for p in elite_scoring_stack['progression']]}")

    print("\n--- 6. Weak scoring stacking progression (synthetic, mirror) ---")
    t0 = time.time()
    weak_scoring_stack = led.stacking_progression(led.WEAK_SCORING_KWARGS, "weak_scoring", n_sims=150)
    print(f"  done in {time.time()-t0:.1f}s: {[p['mean_margin_stack_perspective'] for p in weak_scoring_stack['progression']]}")

    print("\n--- 7. Elite defense stacking progression (synthetic) ---")
    t0 = time.time()
    elite_defense_stack = led.stacking_progression(led.ELITE_DEFENSE_KWARGS, "elite_defense", n_sims=150)
    print(f"  done in {time.time()-t0:.1f}s: {[p['mean_margin_stack_perspective'] for p in elite_defense_stack['progression']]}")

    print("\n--- 8. Elite rebounding stacking progression (synthetic, OREB/DREB counts) ---")
    t0 = time.time()
    rebound_stack = led.rebounding_stacking_progression(n_sims=80)
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- 9. Sequential real-player replacement (saturation check, real data) ---")
    t0 = time.time()
    # deterministic pick: first extreme game for STRONG-side replacement, second for WEAK-side
    extreme_games = sample["buckets"]["extreme"]
    import historical_game_snapshot as hgs
    seq_results = []
    for gid, label in ((extreme_games[0], "strong_side"), (extreme_games[1], "weak_side_mirror")):
        snap = hgs.build_historical_game_snapshot(gid, led.SEASON, led.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        home_id, away_id, _, _, _ = hgs.snapshot_to_engine_input(snap)
        import historical_predictive_backtest as hpb
        home_nr = hpb._team_net_rating_as_of(snap.home_team, snap.game_date, led.SEASON)
        away_nr = hpb._team_net_rating_as_of(snap.away_team, snap.game_date, led.SEASON)
        stronger_side = home_id if home_nr >= away_nr else away_id
        weaker_side = away_id if stronger_side == home_id else home_id
        side_to_replace = stronger_side if label == "strong_side" else weaker_side
        result = led.sequential_replacement_progression(gid, side_to_replace, n_sims=100)
        result["experiment"] = label
        seq_results.append(result)
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- 10. Full-team average replacement (player-quality-explained margin) ---")
    t0 = time.time()
    full_avg_results = [led.full_team_average_replacement(gid, n_sims=100) for gid in extreme_games[:4]]
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- 11. Output-rate decomposition (offense vs defense/hustle) ---")
    t0 = time.time()
    output_decomposition = led.output_rate_strong_weak_decomposition(game_ids, n_sims=50)
    print(f"  done in {time.time()-t0:.1f}s")

    print("\n--- 12. Pace / possession-count variance (reused) ---")
    t0 = time.time()
    pace_variance = mcd.simulated_possessions_variance(game_ids, n_sims=30)
    print(f"  done in {time.time()-t0:.1f}s")

    total_time = time.time() - t_start
    print(f"\n=== TOTAL DIAGNOSTIC RUNTIME: {total_time:.1f}s ===")

    output = {
        "large_extreme_sample": sample,
        "compression_curve_reproduction": compression_curve,
        "category_gap_report": category_gaps,
        "scoring_composite_gap_rows": composite_rows,
        "scoring_composite_correlations": composite_corr,
        "bench_quality_strong_vs_weak": bench_quality,
        "elite_scoring_stacking": elite_scoring_stack,
        "weak_scoring_stacking": weak_scoring_stack,
        "elite_defense_stacking": elite_defense_stack,
        "elite_rebounding_stacking": rebound_stack,
        "sequential_real_player_replacement": seq_results,
        "full_team_average_replacement": full_avg_results,
        "output_rate_decomposition": output_decomposition,
        "pace_variance": pace_variance,
        "runtime_seconds": total_time,
    }
    with open(BACKTESTS_DIR / "large_extreme_matchup_diagnostic_v1.json", "w") as f:
        json.dump(output, f, indent=2, sort_keys=True, default=str)
    print("\nWROTE backtests/large_extreme_matchup_diagnostic_v1.json")


if __name__ == "__main__":
    main()
