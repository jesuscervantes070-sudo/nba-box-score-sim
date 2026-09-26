"""STRENGTH-NEUTRAL OFFENSIVE STYLE V1 -- metrics for a re-simulated configuration
(backtests/_sn_resim_<config>.json.gz from style_neutrality_eval.py). Development-sample metrics only; the frozen
83-game holdout is not in the dataset. Report-only targets are never used as model inputs.

python3 style_neutrality_report.py <config> [<config> ...]  -> backtests/_sn_metrics_<config>.json
"""
import gzip
import json
import os
import sys
from collections import defaultdict

import numpy as np

import historical_predictive_backtest as hpb
import offensive_translation_analysis as oa
import player_input_team_strength_dataset as ds
import player_input_team_strength_diagnostic as d
import run_offensive_translation_diagnostic as rd

PRED_PATH = "backtests/_sn_oof_offense_pred.npy"
SEASONS = ds.SEASONS[1:]


def load_common():
    with gzip.open(ds.DATASET_PATH, "rt") as f:
        records = json.load(f)["records"]
    records.sort(key=lambda r: (r["date"], r["game_id"]))
    names, home_f, away_f = d.game_matrix(records)
    if os.path.exists(PRED_PATH):
        pred = np.load(PRED_PATH)
    else:
        pred = rd.oof_offense_predictions(records, names, home_f, away_f)
        np.save(PRED_PATH, pred)
    real_idx = {}
    real = [oa.real_components(r, real_idx) for r in records]
    season_ppg = {}
    for season in SEASONS:
        pts = defaultdict(list)
        for o in hpb._all_game_outcomes(season):
            pts[o.home_team].append(o.home_score)
            pts[o.away_team].append(o.away_score)
        season_ppg[season] = {t: float(np.mean(v)) for t, v in pts.items()}
    return records, pred, real, season_ppg, oa.real_season_box(SEASONS)


def pct(x):
    return {"mean": float(np.mean(x)), "sd": float(np.std(x)), "p10": float(np.percentile(x, 10)), "p50": float(np.percentile(x, 50)), "p90": float(np.percentile(x, 90))}


def evaluate(config: str, common) -> dict:
    records, pred, real, season_ppg, real_box = common
    N = len(records)
    with gzip.open(f"backtests/_sn_resim_{config}.json.gz", "rt") as f:
        blob = json.load(f)
    games = {g["game_id"]: g for g in blob["games"]}
    rows = []
    for i, r in enumerate(records):
        g = games.get(r["game_id"])
        if g is None or real[i] is None:
            continue
        for k, side in enumerate(("home", "away")):
            s = g["side"][side.upper()]
            fga = max(1e-9, s["fga"])
            rows.append({"i": i, "side": side, "season": r["season"], "team": r["context_only"][f"{side}_team"], "pred": pred[i + k * N],
                         "points": s["points"], "poss": s["poss"], "ortg": s["points"] / s["poss"] * 100, "three_rate": s["fg3a"] / fga,
                         "fga": s["fga"], "fta": s["fta"], "fg3a": s["fg3a"], "oreb": s["oreb"], "tov": s["tov"],
                         "efg": (s["fgm"] + 0.5 * s["fg3m"]) / fga, "real_three_rate": real[i][side]["three_rate"], "real_points": real[i][side]["points"]})
    out = {"config": config, "n_sims": blob["n_sims"], "settings": blob["settings"], "n_team_rows": len(rows)}
    sim3 = np.array([r["three_rate"] for r in rows])
    out["team_game_three_point_attempt_rate"] = {"sim": pct(sim3), "real": pct(np.array([r["real_three_rate"] for r in rows]))}
    out["ortg"] = pct(np.array([r["ortg"] for r in rows]))
    # team-season aggregates (the stable anchors)
    per = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for k in ("points", "three_rate", "fga", "fta", "fg3a", "oreb", "tov", "poss", "pred", "ortg"):
            if not (k == "pred" and np.isnan(r["pred"])):
                per[(r["season"], r["team"])][k].append(r[k])
    ts = {k: {kk: float(np.mean(v)) for kk, v in dd.items()} for k, dd in per.items() if len(dd["points"]) >= 5}
    keys = [k for k in ts if k in real_box and k[0] in SEASONS]
    sim_ts3 = np.array([ts[k]["fg3a"] / ts[k]["fga"] for k in keys])
    real_ts3 = np.array([real_box[k]["three_rate"] for k in keys])
    out["team_season_three_point_attempt_rate"] = {"sim": pct(sim_ts3), "real": pct(real_ts3), "corr": oa.pearson(sim_ts3, real_ts3),
                                                   "mae_of_centered": float(np.mean(np.abs((sim_ts3 - sim_ts3.mean()) - (real_ts3 - real_ts3.mean())))),
                                                   "sd_ratio_sim_over_real": float(sim_ts3.std() / real_ts3.std())}
    out["team_season_offense_spread_sd_points"] = float(np.std([ts[k]["points"] for k in keys]))
    out["team_season_ortg_spread_sd"] = float(np.std([ts[k]["ortg"] for k in keys]))
    # stable ranking vs season points per game
    rank = {}
    zs_real, zs_sim, zs_pred, zs_eff = [], [], [], []
    for season in SEASONS:
        ks = [k for k in keys if k[0] == season and k[1] in season_ppg[season]]
        real_v = np.array([season_ppg[season][k[1]] for k in ks])
        sim_v = np.array([ts[k]["points"] for k in ks])
        rank[season] = {"n_teams": len(ks), "sim_pearson": oa.pearson(real_v, sim_v), "sim_spearman": oa.spearman(real_v, sim_v),
                        "sim_ortg_pearson": oa.pearson(real_v, np.array([ts[k]["ortg"] for k in ks]))}
        zs_real.append(oa.zscore(real_v)); zs_sim.append(oa.zscore(sim_v))
    out["stable_ranking"] = {"by_season": rank, "pooled_within_season_z_pearson": oa.pearson(np.concatenate(zs_real), np.concatenate(zs_sim))}
    # player-input -> sim offense transfer
    tested = np.array([not np.isnan(r["pred"]) for r in rows])
    pr = np.array([r["pred"] for r in rows])[tested]
    for key in ("points", "ortg"):
        y = np.array([r[key] for r in rows])[tested]
        out[f"transfer_{key}_on_predicted_offense"] = {"slope": float(np.polyfit(pr, y, 1)[0]), "corr": oa.pearson(pr, y), "n": int(tested.sum())}
    # game-level margin metrics (real games, sim mean margin / win fraction)
    real_m, sim_m, pw, ok = [], [], [], []
    pred_m = []
    for i, r in enumerate(records):
        g = games.get(r["game_id"])
        if g is None:
            continue
        real_m.append(r["target"]["home_score"] - r["target"]["away_score"]); sim_m.append(g["margin"]); pw.append(g["home_win_frac"])
        pred_m.append(pred[i] - pred[i + N])
    real_m, sim_m, pw, pred_m = map(np.array, (real_m, sim_m, pw, pred_m))
    win = (real_m > 0).astype(float)
    out["game_margin"] = {"n": len(real_m), "margin_r": oa.pearson(sim_m, real_m), "margin_mae": float(np.mean(np.abs(sim_m - real_m))),
                          "winner_accuracy": float(np.mean((sim_m > 0) == (real_m > 0))), "brier_win_frac": float(np.mean((pw - win) ** 2)),
                          "mean_sim_margin": float(sim_m.mean()), "sd_sim_margin": float(sim_m.std())}
    good = ~np.isnan(pred_m)
    def r2(X, y):
        X1 = np.column_stack([np.ones(len(y)), X])
        beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
        return float(1 - ((y - X1 @ beta) ** 2).sum() / ((y - y.mean()) ** 2).sum())
    out["incremental_information"] = {"n": int(good.sum()), "r2_player_input_offense_diff": r2(pred_m[good, None], real_m[good]),
                                      "r2_sim_margin_only": r2(sim_m[good, None], real_m[good]),
                                      "r2_player_input_plus_sim": r2(np.column_stack([pred_m[good], sim_m[good]]), real_m[good])}
    # variance drivers of simulated team-season points
    pts = np.array([ts[k]["points"] for k in keys])
    out["volume_side_effects"] = {
        "sd_team_season_fga": float(np.std([ts[k]["fga"] for k in keys])), "sd_team_season_oreb": float(np.std([ts[k]["oreb"] for k in keys])),
        "sd_team_season_tov": float(np.std([ts[k]["tov"] for k in keys])), "sd_team_season_poss": float(np.std([ts[k]["poss"] for k in keys])),
        "corr_points_fga": oa.pearson(pts, np.array([ts[k]["fga"] for k in keys])), "corr_points_oreb": oa.pearson(pts, np.array([ts[k]["oreb"] for k in keys])),
        "corr_points_tov": oa.pearson(pts, np.array([ts[k]["tov"] for k in keys])), "mean_oreb": float(np.mean([ts[k]["oreb"] for k in keys])),
        "mean_tov": float(np.mean([ts[k]["tov"] for k in keys]))}
    json.dump(out, open(f"backtests/_sn_metrics_{config}.json", "w"), indent=1)
    return out


if __name__ == "__main__":
    common = load_common()
    for c in sys.argv[1:]:
        m = evaluate(c, common)
        print(c, "3PA-rate team-season sd", round(m["team_season_three_point_attempt_rate"]["sim"]["sd"], 4),
              "rank", {s: round(v["sim_pearson"], 3) for s, v in m["stable_ranking"]["by_season"].items()},
              "slope", round(m["transfer_points_on_predicted_offense"]["slope"], 3))
