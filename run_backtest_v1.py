"""Runner for FIRST HISTORICAL PREDICTIVE BACKTEST V1 -- executes the frozen BACKTEST_V1_HOLDOUT
under both PREGAME_EXPECTED and ORACLE_PARTICIPANTS, computes all metrics/baselines/diagnostics,
and persists raw + summary results under backtests/. Not a test file; run directly:
    python3 run_backtest_v1.py
"""
import json
import sys
import time
from pathlib import Path

import historical_predictive_backtest as hpb
import historical_game_snapshot as hgs

ALL_SEASONS = ["2021-22", "2022-23", "2023-24"]  # sufficient real coverage for this season's backtest
BACKTESTS_DIR = Path("backtests")


def run_mode(game_ids, mode, label):
    print(f"\n{'='*70}\nRunning {label} ({mode}) over {len(game_ids)} games, n_sims={hpb.N_SIMULATIONS}\n{'='*70}")
    t0 = time.time()
    result = hpb.run_backtest(game_ids, hpb.BACKTEST_SEASON, ALL_SEASONS, mode, n_sims=hpb.N_SIMULATIONS, verbose=True)
    elapsed = time.time() - t0
    print(f"\n{label} done in {elapsed:.1f}s -- succeeded={result.n_succeeded}/{result.n_attempted}, skips={len(result.skips)}")
    if result.skips:
        print("SKIPS:", json.dumps(result.skips, indent=2))
    return result


def main():
    BACKTESTS_DIR.mkdir(exist_ok=True)
    game_ids = hpb.load_or_create_holdout()
    print(f"BACKTEST_V1_HOLDOUT: {len(game_ids)} games, season {hpb.BACKTEST_SEASON}")

    pregame_result = run_mode(game_ids, hgs.MODE_PREGAME_EXPECTED, "PREGAME_EXPECTED (primary/honest predictor)")
    oracle_result = run_mode(game_ids, hgs.MODE_ORACLE_PARTICIPANTS, "ORACLE_PARTICIPANTS (diagnostic upper bound)")

    pregame_metrics = hpb.compute_metrics_for_predictions(pregame_result.predictions, pregame_result.outcomes)
    oracle_metrics = hpb.compute_metrics_for_predictions(oracle_result.predictions, oracle_result.outcomes)

    common_game_ids = sorted(set(pregame_result.outcomes.keys()) & set(oracle_result.outcomes.keys()))
    baseline_metrics = []
    for name in hpb.BASELINES:
        baseline_metrics.append(hpb.compute_metrics_for_baseline(
            name, common_game_ids, pregame_result.baseline_predictions[name], pregame_result.outcomes))

    extra_pregame = {
        "score_and_pace_bias": hpb.score_and_pace_bias(pregame_result.predictions, pregame_result.outcomes),
        "margin_variance_bias": hpb.margin_variance_bias(pregame_result.predictions, pregame_result.outcomes),
        "home_away_bias": hpb.home_away_bias(pregame_result.predictions, pregame_result.outcomes),
        "team_level_bias": hpb.team_level_bias(pregame_result.predictions, pregame_result.outcomes),
        "confidence_group_breakdown": hpb.confidence_group_breakdown(pregame_result.predictions, pregame_result.outcomes),
        "upset_analysis": hpb.upset_analysis(pregame_result.predictions, pregame_result.outcomes),
        "worst_and_best_games": hpb.worst_and_best_games(pregame_result.predictions, pregame_result.outcomes),
        "provenance_vs_error": hpb.provenance_vs_error(pregame_result.predictions, pregame_result.outcomes),
        "period_breakdown": hpb.period_breakdown(pregame_result.predictions, pregame_result.outcomes),
    }

    pregame_by_gid = {p.game_id: p for p in pregame_result.predictions}
    rotation_rows = hpb.rotation_overlap_and_minutes_concentration(
        common_game_ids, hpb.BACKTEST_SEASON, ALL_SEASONS, pregame_by_gid, pregame_result.outcomes)
    extra_pregame["rotation_overlap_and_minutes_concentration"] = rotation_rows

    # PREGAME vs ORACLE delta (on the games both succeeded for)
    both_ids = sorted(set(p.game_id for p in pregame_result.predictions) & set(p.game_id for p in oracle_result.predictions))
    pregame_common = [p for p in pregame_result.predictions if p.game_id in both_ids]
    oracle_common = [p for p in oracle_result.predictions if p.game_id in both_ids]
    pregame_common_metrics = hpb.compute_metrics_for_predictions(pregame_common, pregame_result.outcomes)
    oracle_common_metrics = hpb.compute_metrics_for_predictions(oracle_common, oracle_result.outcomes)
    delta = {
        "n_common_games": len(both_ids),
        "pregame_on_common": pregame_common_metrics,
        "oracle_on_common": oracle_common_metrics,
        "delta_brier (oracle-pregame, negative=oracle better)": (
            oracle_common_metrics["brier_score"] - pregame_common_metrics["brier_score"]
            if oracle_common_metrics["brier_score"] is not None and pregame_common_metrics["brier_score"] is not None else None),
        "delta_accuracy (oracle-pregame)": (
            oracle_common_metrics["winner_accuracy"] - pregame_common_metrics["winner_accuracy"]
            if oracle_common_metrics["winner_accuracy"] is not None and pregame_common_metrics["winner_accuracy"] is not None else None),
        "delta_margin_mae (oracle-pregame, negative=oracle better)": (
            oracle_common_metrics["margin_mae"] - pregame_common_metrics["margin_mae"]
            if oracle_common_metrics["margin_mae"] is not None and pregame_common_metrics["margin_mae"] is not None else None),
    }
    extra_pregame["pregame_vs_oracle_delta"] = delta

    # Persist raw games (PREGAME primary, ORACLE separately)
    raw_pregame = hpb.serialize_raw_games(pregame_result)
    raw_oracle = hpb.serialize_raw_games(oracle_result)
    with open(BACKTESTS_DIR / "backtest_v1_games.json", "w") as f:
        json.dump({"pregame_expected": raw_pregame, "oracle_participants": raw_oracle}, f, indent=2, sort_keys=True, default=str)

    summary = hpb.serialize_summary(pregame_result, pregame_metrics, baseline_metrics, extra_sections=extra_pregame)
    summary["oracle_metrics"] = oracle_metrics
    summary["oracle_timing"] = oracle_result.timing
    summary["oracle_n_succeeded"] = oracle_result.n_succeeded
    summary["oracle_skips"] = oracle_result.skips
    with open(BACKTESTS_DIR / "backtest_v1_summary.json", "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)

    print("\n\n=== WROTE backtests/backtest_v1_games.json and backtests/backtest_v1_summary.json ===")
    print(json.dumps({"pregame_metrics": pregame_metrics, "oracle_metrics": oracle_metrics,
                       "baseline_metrics": baseline_metrics, "delta": delta}, indent=2, default=str))


if __name__ == "__main__":
    main()
