"""Runner for the FROZEN HISTORICAL BACKTEST RE-EVALUATION V2 paired analysis. Requires
backtest_v1_games.json (untouched) and backtest_v2_games.json (from run_backtest_v2.py) to already
exist. Writes backtests/backtest_v1_v2_comparison.json. Run directly:
    python3 run_v1_v2_comparison.py
"""
import json
import time
from pathlib import Path

import backtest_v1_v2_comparison as cmp
import historical_predictive_backtest as hpb

BACKTESTS_DIR = Path("backtests")


def main():
    t0 = time.time()
    v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
    v2 = cmp.load_games(BACKTESTS_DIR / "backtest_v2_games.json")

    v1_pregame = v1["pregame_expected"]["games"]
    v2_pregame = v2["pregame_expected"]["games"]
    v1_oracle = v1["oracle_participants"]["games"]
    v2_oracle = v2["oracle_participants"]["games"]

    print(f"V1 pregame games: {len(v1_pregame)}, V2 pregame games: {len(v2_pregame)}")

    records = cmp.paired_game_records(v1_pregame, v2_pregame)
    print(f"Paired records: {len(records)}")

    deltas_summary = cmp.paired_deltas_summary(records)
    print("Computing bootstrap CIs...")
    bootstrap = cmp.paired_bootstrap_ci(records)

    v1_compression = cmp.margin_compression_summary(v1_pregame)
    v2_compression = cmp.margin_compression_summary(v2_pregame)

    print("Computing strength-margin correlations...")
    v1_strength_corr = cmp.strength_margin_correlation(v1_pregame)
    v2_strength_corr = cmp.strength_margin_correlation(v2_pregame)

    buckets = cmp.matchup_strength_buckets(v1_pregame, v2_pregame)
    calibration = cmp.calibration_comparison(v1_pregame, v2_pregame)
    oracle_cmp = cmp.oracle_comparison(v1_oracle, v2_oracle)
    period_cmp = cmp.period_comparison(records)
    team_cmp = cmp.team_level_comparison(records)
    top_bottom = cmp.top_improvements_and_regressions(records)

    print("Computing intervention exposure (rebuilds snapshots, reuses warm caches)...")
    game_ids = sorted(set(g["game_id"] for g in v2_pregame))
    exposure = cmp.intervention_exposure(game_ids)
    exposure_vs_improvement = cmp.exposure_vs_improvement(exposure.get("_rows", []), records)
    exposure.pop("_rows", None)

    # Accuracy point estimates (not just bootstrap) for the headline table.
    v1_pairs = [(g["predicted_home_win_prob"], 1 if g["actual_winner"] == g["home_team"] else 0) for g in v1_pregame]
    v2_pairs = [(g["predicted_home_win_prob"], 1 if g["actual_winner"] == g["home_team"] else 0) for g in v2_pregame]
    v1_pred_margins = [g["predicted_mean_margin"] for g in v1_pregame]
    v2_pred_margins = [g["predicted_mean_margin"] for g in v2_pregame]
    actual_margins = [g["actual_margin"] for g in v1_pregame]
    v1_home_scores = [g["mean_simulated_home_score"] for g in v1_pregame]
    v2_home_scores = [g["mean_simulated_home_score"] for g in v2_pregame]
    v1_away_scores = [g["mean_simulated_away_score"] for g in v1_pregame]
    v2_away_scores = [g["mean_simulated_away_score"] for g in v2_pregame]
    actual_home_scores = [g["actual_home_score"] for g in v1_pregame]
    actual_away_scores = [g["actual_away_score"] for g in v1_pregame]

    v1_bins, v2_bins = calibration["v1_bins"], calibration["v2_bins"]
    headline = {
        "accuracy": {"v1": hpb.winner_accuracy(v1_pairs), "v2": hpb.winner_accuracy(v2_pairs)},
        "brier_score": {"v1": hpb.brier_score(v1_pairs), "v2": hpb.brier_score(v2_pairs)},
        "log_loss": {"v1": hpb.log_loss(v1_pairs), "v2": hpb.log_loss(v2_pairs)},
        "margin_mae": {"v1": hpb.margin_mae(v1_pred_margins, actual_margins), "v2": hpb.margin_mae(v2_pred_margins, actual_margins)},
        "margin_rmse": {"v1": hpb.margin_rmse(v1_pred_margins, actual_margins), "v2": hpb.margin_rmse(v2_pred_margins, actual_margins)},
        "mean_signed_margin_error": {"v1": hpb.mean_signed_margin_error(v1_pred_margins, actual_margins),
                                      "v2": hpb.mean_signed_margin_error(v2_pred_margins, actual_margins)},
        "home_score_mae": {"v1": hpb.score_mae(v1_home_scores, actual_home_scores), "v2": hpb.score_mae(v2_home_scores, actual_home_scores)},
        "away_score_mae": {"v1": hpb.score_mae(v1_away_scores, actual_away_scores), "v2": hpb.score_mae(v2_away_scores, actual_away_scores)},
        "combined_score_mae": {
            "v1": (sum(abs(p - a) for p, a in zip(v1_home_scores, actual_home_scores)) + sum(abs(p - a) for p, a in zip(v1_away_scores, actual_away_scores))) / (2 * len(v1_pregame)),
            "v2": (sum(abs(p - a) for p, a in zip(v2_home_scores, actual_home_scores)) + sum(abs(p - a) for p, a in zip(v2_away_scores, actual_away_scores))) / (2 * len(v2_pregame)),
        },
        "expected_calibration_error": {
            "v1": hpb.expected_calibration_error(v1_bins, len(v1_pregame)),
            "v2": hpb.expected_calibration_error(v2_bins, len(v2_pregame)),
        },
    }
    for k, v in headline.items():
        if v["v1"] is not None and v["v2"] is not None:
            v["delta"] = v["v2"] - v["v1"]

    output = {
        "headline_metrics_table": headline,
        "paired_deltas_summary": deltas_summary,
        "bootstrap_confidence_intervals": bootstrap,
        "margin_compression": {"v1": v1_compression, "v2": v2_compression},
        "strength_to_margin_correlation": {"v1": v1_strength_corr, "v2": v2_strength_corr},
        "matchup_strength_buckets": buckets,
        "calibration_comparison": calibration,
        "oracle_v1_vs_v2": oracle_cmp,
        "period_comparison": period_cmp,
        "team_level_comparison": team_cmp,
        "intervention_exposure": exposure,
        "exposure_vs_improvement": exposure_vs_improvement,
        "top_improvements_and_regressions": top_bottom,
        "runtime_seconds": time.time() - t0,
    }
    with open(BACKTESTS_DIR / "backtest_v1_v2_comparison.json", "w") as f:
        json.dump(output, f, indent=2, sort_keys=True, default=str)
    print(f"\nWROTE backtests/backtest_v1_v2_comparison.json in {time.time()-t0:.1f}s")
    print(json.dumps(headline, indent=2, default=str))


if __name__ == "__main__":
    main()
