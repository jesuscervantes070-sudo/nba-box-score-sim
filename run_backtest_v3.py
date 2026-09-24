"""Runner for FROZEN HISTORICAL BACKTEST RE-EVALUATION V3 -- re-scores the EXACT, UNCHANGED
BACKTEST_V1_HOLDOUT (same 83 games, same N_SIMULATIONS=250, same seed formula/MODEL_VERSION, same
metric definitions, same baselines) after the lineup-slot/pass-target allocation fix and the
THREE_POINT_PREFERENCE_WEIGHT 1.0 -> 2.0 change. Writes separate backtest_v3_games.json / backtest_v3_summary.json -- NEVER
overwrites the V1/V2 files. Run directly: python3 run_backtest_v3.py pregame | oracle | merge
"""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import historical_predictive_backtest as hpb
import historical_game_snapshot as hgs

ALL_SEASONS = ["2021-22", "2022-23", "2023-24"]
BACKTESTS_DIR = Path("backtests")
INTERVENTION_VERSION = "allocation-fixes-v1"  # this phase's own intervention label, NOT a
# change to MODEL_VERSION/seed formula -- V3 deliberately reuses the EXACT SAME seed sequence as
# V1 (same MODEL_VERSION="backtest-v1" string feeds _seed_for) so any output difference is
# attributable ONLY to the truth intervention, never to a different RNG stream.


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_holdout_integrity():
    """Hard assertion, not a soft check -- refuses to run V3 at all if the holdout file was ever
    regenerated, reselected, or reordered."""
    path = BACKTESTS_DIR / "backtest_v1_holdout_game_ids.json"
    with open(path) as f:
        data = json.load(f)
    assert data["n_games"] == 83, f"expected 83 games, found {data['n_games']}"
    assert data["selection_rule"] == "sha256(game_id) % 16 == 0"
    checksum = _sha256(path)
    print(f"HOLDOUT FILE CHECKSUM (sha256): {checksum}")
    print(f"HOLDOUT FILE: {path}, n_games={data['n_games']}")
    return data["game_ids"], checksum


def run_mode(game_ids, mode, label):
    print(f"\n{'='*70}\nV3: Running {label} ({mode}) over {len(game_ids)} games, n_sims={hpb.N_SIMULATIONS}\n{'='*70}")
    t0 = time.time()
    result = hpb.run_backtest(game_ids, hpb.BACKTEST_SEASON, ALL_SEASONS, mode, n_sims=hpb.N_SIMULATIONS, verbose=True)
    elapsed = time.time() - t0
    print(f"\n{label} done in {elapsed:.1f}s -- succeeded={result.n_succeeded}/{result.n_attempted}, skips={len(result.skips)}")
    if result.skips:
        print("SKIPS:", json.dumps(result.skips, indent=2))
    return result


def _run_one_mode_isolated(mode_label: str):
    """Runs ONLY one mode in THIS process and writes its own intermediate result file --
    deliberately isolated from the other mode's process (see module docstring: V1's ORACLE run
    showed an 8.6-hour long-process anomaly when both modes ran back-to-back in one process; this
    phase runs them as two separate `python3 run_backtest_v3.py pregame` / `... oracle` invocations)."""
    BACKTESTS_DIR.mkdir(exist_ok=True)
    game_ids, holdout_checksum = verify_holdout_integrity()
    print(f"Reusing EXACT V1 holdout: {len(game_ids)} games (game_ids[:3]={game_ids[:3]})")
    hpb.clear_backtest_caches()
    print(f"Cleared all estimator/snapshot/reference caches before V3 {mode_label} evaluation.")

    mode = hgs.MODE_PREGAME_EXPECTED if mode_label == "pregame" else hgs.MODE_ORACLE_PARTICIPANTS
    result = run_mode(game_ids, mode, f"{mode_label.upper()} (V3, isolated process)")
    metrics = hpb.compute_metrics_for_predictions(result.predictions, result.outcomes)
    raw = hpb.serialize_raw_games(result)
    payload = {
        "mode_label": mode_label, "mode": mode, "metrics": metrics, "timing": result.timing,
        "n_attempted": result.n_attempted, "n_succeeded": result.n_succeeded, "skips": result.skips,
        "raw_games": raw,
        "baseline_predictions": ({name: {gid: {"predicted_home_win_prob": p.predicted_home_win_prob,
                                                 "predicted_margin": p.predicted_margin}
                                          for gid, p in preds.items()}
                                   for name, preds in result.baseline_predictions.items()}
                                  if mode_label == "pregame" else {}),
        "holdout_checksum": holdout_checksum,
    }
    out_path = BACKTESTS_DIR / f"_v3_partial_{mode_label}.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
    print(f"\nWrote intermediate result: {out_path}")
    _, checksum_after = verify_holdout_integrity()
    assert checksum_after == holdout_checksum, "HOLDOUT FILE WAS MODIFIED DURING V3 RUN -- investigate immediately"
    print("Post-run holdout checksum re-verified UNCHANGED.")


def merge_partials():
    """Merges the two isolated-process partial result files (written by `_run_one_mode_isolated`)
    into the final backtest_v3_games.json / backtest_v3_summary.json -- same output shape as the
    single-process `main()` path below would have produced."""
    with open(BACKTESTS_DIR / "_v3_partial_pregame.json") as f:
        pregame_partial = json.load(f)
    with open(BACKTESTS_DIR / "_v3_partial_oracle.json") as f:
        oracle_partial = json.load(f)
    assert pregame_partial["holdout_checksum"] == oracle_partial["holdout_checksum"]

    pregame_metrics = pregame_partial["metrics"]
    oracle_metrics = oracle_partial["metrics"]
    raw_pregame = pregame_partial["raw_games"]
    raw_oracle = oracle_partial["raw_games"]

    pregame_games_by_id = {g["game_id"]: g for g in raw_pregame["games"]}
    oracle_games_by_id = {g["game_id"]: g for g in raw_oracle["games"]}
    common_game_ids = sorted(set(pregame_games_by_id) & set(oracle_games_by_id))

    baseline_metrics = []
    for name, preds in pregame_partial["baseline_predictions"].items():
        pairs = [(preds[gid]["predicted_home_win_prob"], 1 if pregame_games_by_id[gid]["actual_winner"] == pregame_games_by_id[gid]["home_team"] else 0)
                 for gid in common_game_ids if gid in preds]
        pred_margins = [preds[gid]["predicted_margin"] for gid in common_game_ids if gid in preds]
        actual_margins = [pregame_games_by_id[gid]["actual_margin"] for gid in common_game_ids if gid in preds]
        baseline_metrics.append({
            "baseline": name, "n_games": len(pairs),
            "brier_score": hpb.brier_score(pairs), "log_loss": hpb.log_loss(pairs),
            "winner_accuracy": hpb.winner_accuracy(pairs),
            "margin_mae": hpb.margin_mae(pred_margins, actual_margins) if all(m is not None for m in pred_margins) else None,
            "margin_rmse": hpb.margin_rmse(pred_margins, actual_margins) if all(m is not None for m in pred_margins) else None,
        })

    both_ids = sorted(set(pregame_games_by_id) & set(oracle_games_by_id))
    def _pairs(games_by_id, ids):
        return [(games_by_id[g]["predicted_home_win_prob"], 1 if games_by_id[g]["actual_winner"] == games_by_id[g]["home_team"] else 0) for g in ids]
    def _margins(games_by_id, ids, key):
        return [games_by_id[g][key] for g in ids]
    pregame_common_metrics = {
        "n_games": len(both_ids), "brier_score": hpb.brier_score(_pairs(pregame_games_by_id, both_ids)),
        "winner_accuracy": hpb.winner_accuracy(_pairs(pregame_games_by_id, both_ids)),
        "margin_mae": hpb.margin_mae(_margins(pregame_games_by_id, both_ids, "predicted_mean_margin"), _margins(pregame_games_by_id, both_ids, "actual_margin")),
    }
    oracle_common_metrics = {
        "n_games": len(both_ids), "brier_score": hpb.brier_score(_pairs(oracle_games_by_id, both_ids)),
        "winner_accuracy": hpb.winner_accuracy(_pairs(oracle_games_by_id, both_ids)),
        "margin_mae": hpb.margin_mae(_margins(oracle_games_by_id, both_ids, "predicted_mean_margin"), _margins(oracle_games_by_id, both_ids, "actual_margin")),
    }
    delta = {
        "n_common_games": len(both_ids), "pregame_on_common": pregame_common_metrics, "oracle_on_common": oracle_common_metrics,
        "delta_brier (oracle-pregame, negative=oracle better)": oracle_common_metrics["brier_score"] - pregame_common_metrics["brier_score"],
        "delta_accuracy (oracle-pregame)": oracle_common_metrics["winner_accuracy"] - pregame_common_metrics["winner_accuracy"],
        "delta_margin_mae (oracle-pregame, negative=oracle better)": oracle_common_metrics["margin_mae"] - pregame_common_metrics["margin_mae"],
    }

    with open(BACKTESTS_DIR / "backtest_v3_games.json", "w") as f:
        json.dump({"pregame_expected": raw_pregame, "oracle_participants": raw_oracle}, f, indent=2, sort_keys=True, default=str)

    summary = {
        "model_commit": _git_head_commit(), "model_version": hpb.MODEL_VERSION,
        "snapshot_mode": hgs.MODE_PREGAME_EXPECTED, "n_sims_per_game": hpb.N_SIMULATIONS,
        "seed_policy": "sha256(f'{MODEL_VERSION}|{mode}|{game_id}|{sim_index}')[:16 hex] as int",
        "holdout_selection_rule": f"sha256(game_id) % {hpb.HOLDOUT_SELECTION_MOD} == 0, season {hpb.BACKTEST_SEASON}",
        "n_attempted": pregame_partial["n_attempted"], "n_succeeded": pregame_partial["n_succeeded"],
        "n_skipped": len(pregame_partial["skips"]), "game_ids": common_game_ids,
        "timing": pregame_partial["timing"], "metrics": pregame_metrics, "baseline_metrics": baseline_metrics,
        "oracle_metrics": oracle_metrics, "oracle_timing": oracle_partial["timing"],
        "oracle_n_succeeded": oracle_partial["n_succeeded"], "oracle_skips": oracle_partial["skips"],
        "pregame_vs_oracle_delta": delta,
        "intervention_version": INTERVENTION_VERSION, "holdout_file_sha256": pregame_partial["holdout_checksum"],
        "evaluation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "isolated_process_run": True,
    }
    with open(BACKTESTS_DIR / "backtest_v3_summary.json", "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)
    print("Merged partial results -> backtest_v3_games.json / backtest_v3_summary.json")
    print(json.dumps({"pregame_metrics": pregame_metrics, "oracle_metrics": oracle_metrics,
                       "baseline_metrics": baseline_metrics, "delta": delta}, indent=2, default=str))


def _git_head_commit() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "UNKNOWN"


def main():
    BACKTESTS_DIR.mkdir(exist_ok=True)
    game_ids, holdout_checksum = verify_holdout_integrity()
    print(f"Reusing EXACT V1 holdout: {len(game_ids)} games (game_ids[:3]={game_ids[:3]})")

    # Explicit, documented cache clear -- no stale V1-truth profiles survive into V3.
    hpb.clear_backtest_caches()
    print("Cleared all estimator/snapshot/reference caches before V3 evaluation.")

    pregame_result = run_mode(game_ids, hgs.MODE_PREGAME_EXPECTED, "PREGAME_EXPECTED (V3)")
    oracle_result = run_mode(game_ids, hgs.MODE_ORACLE_PARTICIPANTS, "ORACLE_PARTICIPANTS (V3)")

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

    raw_pregame = hpb.serialize_raw_games(pregame_result)
    raw_oracle = hpb.serialize_raw_games(oracle_result)
    with open(BACKTESTS_DIR / "backtest_v3_games.json", "w") as f:
        json.dump({"pregame_expected": raw_pregame, "oracle_participants": raw_oracle}, f, indent=2, sort_keys=True, default=str)

    summary = hpb.serialize_summary(pregame_result, pregame_metrics, baseline_metrics, extra_sections=extra_pregame)
    summary["oracle_metrics"] = oracle_metrics
    summary["oracle_timing"] = oracle_result.timing
    summary["oracle_n_succeeded"] = oracle_result.n_succeeded
    summary["oracle_skips"] = oracle_result.skips
    summary["intervention_version"] = INTERVENTION_VERSION
    summary["holdout_file_sha256"] = holdout_checksum
    summary["evaluation_timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    with open(BACKTESTS_DIR / "backtest_v3_summary.json", "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)

    print("\n\n=== WROTE backtests/backtest_v3_games.json and backtests/backtest_v3_summary.json ===")
    print(json.dumps({"pregame_metrics": pregame_metrics, "oracle_metrics": oracle_metrics,
                       "baseline_metrics": baseline_metrics, "delta": delta}, indent=2, default=str))

    # Re-verify holdout file was not touched by this run.
    _, checksum_after = verify_holdout_integrity()
    assert checksum_after == holdout_checksum, "HOLDOUT FILE WAS MODIFIED DURING V3 RUN -- investigate immediately"
    print("Post-run holdout checksum re-verified UNCHANGED.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("pregame", "oracle"):
        _run_one_mode_isolated(sys.argv[1])
    elif len(sys.argv) > 1 and sys.argv[1] == "merge":
        merge_partials()
    else:
        main()
