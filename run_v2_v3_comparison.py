"""Paired V2-vs-V3 analysis over the frozen holdout. Read-only over backtest_v1/v2/v3 game files.
Reuses backtest_v1_v2_comparison's paired functions, treating V2 as the baseline slot and V3 as the
new slot, then relabels keys. Writes backtests/backtest_v2_v3_comparison.json.
    python3 run_v2_v3_comparison.py
"""
import json
import statistics
from pathlib import Path

import backtest_v1_v2_comparison as cmp
import historical_predictive_backtest as hpb

BACKTESTS_DIR = Path("backtests")


def relabel(record):
    """cmp's baseline/new slots are keyed v1_/v2_; here they mean V2/V3."""
    out = {}
    for k, v in record.items():
        if k.startswith("v1_"):
            out["v2_" + k[3:]] = v
        elif k.startswith("v2_"):
            out["v3_" + k[3:]] = v
        else:
            out[k] = v
    return out


def headline(games, label):
    pairs = [(g["predicted_home_win_prob"], 1 if g["actual_winner"] == g["home_team"] else 0) for g in games]
    pm = [g["predicted_mean_margin"] for g in games]
    am = [g["actual_margin"] for g in games]
    hs = [g["mean_simulated_home_score"] for g in games]
    as_ = [g["mean_simulated_away_score"] for g in games]
    ah = [g["actual_home_score"] for g in games]
    aa = [g["actual_away_score"] for g in games]
    bins = hpb.calibration_table(pairs)
    return {
        "accuracy": hpb.winner_accuracy(pairs), "brier_score": hpb.brier_score(pairs), "log_loss": hpb.log_loss(pairs),
        "margin_mae": hpb.margin_mae(pm, am), "margin_rmse": hpb.margin_rmse(pm, am),
        "mean_signed_margin_error": hpb.mean_signed_margin_error(pm, am),
        "home_score_mae": hpb.score_mae(hs, ah), "away_score_mae": hpb.score_mae(as_, aa),
        "combined_score_mae": (sum(abs(p - a) for p, a in zip(hs, ah)) + sum(abs(p - a) for p, a in zip(as_, aa))) / (2 * len(games)),
        "expected_calibration_error": hpb.expected_calibration_error(bins, len(games)),
        "mean_simulated_total_score": statistics.mean(h + a for h, a in zip(hs, as_)),
        "mean_simulated_team_score": statistics.mean(hs + as_),
        "simulated_team_score_sd_across_games": statistics.pstdev(hs + as_),
        "mean_actual_total_score": statistics.mean(h + a for h, a in zip(ah, aa)),
    }, bins


def bucket_table(v1, v2, v3):
    def bucket(g):
        diff = abs(hpb._team_net_rating_as_of(g["home_team"], g["date"], hpb.BACKTEST_SEASON)
                   - hpb._team_net_rating_as_of(g["away_team"], g["date"], hpb.BACKTEST_SEASON))
        return ("near_equal (<3)" if diff < 3 else "modest (3-8)" if diff < 8 else "large (8-15)" if diff < 15 else "extreme (>=15)")
    b1, b2, b3 = ({g["game_id"]: g for g in gs} for gs in (v1, v2, v3))
    groups = {}
    for gid in sorted(set(b1) & set(b2) & set(b3)):
        groups.setdefault(bucket(b2[gid]), []).append(gid)
    out = []
    for name in ("near_equal (<3)", "modest (3-8)", "large (8-15)", "extreme (>=15)"):
        ids = groups.get(name, [])
        if not ids:
            out.append({"bucket": name, "n_games": 0})
            continue
        out.append({"bucket": name, "n_games": len(ids),
                    "actual_abs_margin": statistics.mean(abs(b2[i]["actual_margin"]) for i in ids),
                    "v1_abs_predicted_margin": statistics.mean(abs(b1[i]["predicted_mean_margin"]) for i in ids),
                    "v2_abs_predicted_margin": statistics.mean(abs(b2[i]["predicted_mean_margin"]) for i in ids),
                    "v3_abs_predicted_margin": statistics.mean(abs(b3[i]["predicted_mean_margin"]) for i in ids)})
    return out


def baseline_reproduction(s2, s3):
    """Baselines must reproduce exactly (evaluation-drift guard)."""
    m2 = {b["baseline"]: b for b in s2["baseline_metrics"]}
    m3 = {b["baseline"]: b for b in s3["baseline_metrics"]}
    report = {}
    for name in m2:
        report[name] = {"identical": all(m2[name][k] == m3[name][k] for k in ("brier_score", "log_loss", "winner_accuracy",
                                                                             "margin_mae", "margin_rmse")),
                        "v2": {k: m2[name][k] for k in ("brier_score", "margin_mae")},
                        "v3": {k: m3[name][k] for k in ("brier_score", "margin_mae")}}
    return report


def main():
    v1 = cmp.load_games(BACKTESTS_DIR / "backtest_v1_games.json")
    v2 = cmp.load_games(BACKTESTS_DIR / "backtest_v2_games.json")
    v3 = cmp.load_games(BACKTESTS_DIR / "backtest_v3_games.json")
    s2 = json.load(open(BACKTESTS_DIR / "backtest_v2_summary.json"))
    s3 = json.load(open(BACKTESTS_DIR / "backtest_v3_summary.json"))
    g1, g2, g3 = (x["pregame_expected"]["games"] for x in (v1, v2, v3))
    assert [g["game_id"] for g in g2] == [g["game_id"] for g in g3], "V2/V3 game order drifted"
    assert s3["holdout_file_sha256"] == s2["holdout_file_sha256"], "holdout checksum differs between V2 and V3"

    raw_records = cmp.paired_game_records(g2, g3)
    records = [relabel(r) for r in raw_records]
    bootstrap = cmp.paired_bootstrap_ci(raw_records)
    heads, bins = {}, {}
    for label, games in (("v1", g1), ("v2", g2), ("v3", g3)):
        heads[label], bins[label] = headline(games, label)
    table = {}
    for k in heads["v2"]:
        table[k] = {"v1": heads["v1"][k], "v2": heads["v2"][k], "v3": heads["v3"][k], "delta_v3_minus_v2": heads["v3"][k] - heads["v2"][k]}

    compression = {label: cmp.margin_compression_summary(g) for label, g in (("v1", g1), ("v2", g2), ("v3", g3))}
    strength = {label: cmp.strength_margin_correlation(g) for label, g in (("v1", g1), ("v2", g2), ("v3", g3))}
    oracle = cmp.oracle_comparison(v2["oracle_participants"]["games"], v3["oracle_participants"]["games"])
    out = {
        "headline_metrics_table": table, "paired_deltas_summary": cmp.paired_deltas_summary(raw_records),
        "bootstrap_confidence_intervals": bootstrap, "margin_compression": compression,
        "strength_to_margin_correlation": strength, "matchup_strength_buckets": bucket_table(g1, g2, g3),
        "calibration_bins": {"v2": bins["v2"], "v3": bins["v3"]},
        "baseline_reproduction": baseline_reproduction(s2, s3),
        "oracle_v2_vs_v3": {"v2_oracle": oracle["v1_oracle"], "v3_oracle": oracle["v2_oracle"],
                            "delta_brier": oracle["delta_brier"], "delta_accuracy": oracle["delta_accuracy"],
                            "delta_margin_mae": oracle["delta_margin_mae"]},
        "period_comparison": cmp.period_comparison(raw_records),
        "team_level_comparison": cmp.team_level_comparison(raw_records),
        "top_improvements_and_regressions": {k: [relabel(r) if False else r for r in v]
                                             for k, v in cmp.top_improvements_and_regressions(raw_records).items()},
        "slot_note": "in top_improvements_and_regressions rows, v1_* fields are V2 and v2_* fields are V3",
    }
    with open(BACKTESTS_DIR / "backtest_v2_v3_comparison.json", "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(json.dumps({"table": table, "bootstrap": bootstrap, "compression": compression, "strength": strength,
                      "buckets": out["matchup_strength_buckets"], "baseline_reproduction": out["baseline_reproduction"],
                      "oracle": out["oracle_v2_vs_v3"], "period": out["period_comparison"]}, indent=1, default=str))


if __name__ == "__main__":
    main()
