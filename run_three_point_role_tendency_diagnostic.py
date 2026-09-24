"""Runner: candidate weightings vs the fixed 29-team real allocation sample (development games only).
python3 run_three_point_role_tendency_diagnostic.py [n_sims]"""
import json
import sys
import time

import three_point_allocation_diagnostic as tad
import three_point_role_tendency_diagnostic as rtd
import three_point_resolution_diagnostic as tpd
import margin_compression_diagnostic as mcd
import historical_game_snapshot as hgs
import run_three_point_allocation_diagnostic as base
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault

OUT = "backtests/three_point_role_tendency_candidates.json"
CANDIDATES = {
    "A_current": {},
    "F0_finishing_off": {"finishing_weight": 0.0},
    "F1_finishing_half": {"finishing_weight": 1.0},
    "P15_pref_x1.5": {"pref_scale": 1.5},
    "P2_pref_x2": {"pref_scale": 2.0},
    "P25_pref_x2.5": {"pref_scale": 2.5},
    "P3_pref_x3": {"pref_scale": 3.0},
    "R1_receiver_tendency_1": {"receiver_weights": {"tendency": 1.0}},
    "R2_receiver_tendency_2": {"receiver_weights": {"tendency": 2.0}},
    "RS_receiver_tendency_spacing": {"receiver_weights": {"tendency": 1.0, "spacing": 1.5}},
    "RF_receiver_finishing": {"receiver_weights": {"finishing": 2.0}},
    "F0_R1": {"finishing_weight": 0.0, "receiver_weights": {"tendency": 1.0}},
    "F0_P2": {"finishing_weight": 0.0, "pref_scale": 2.0},
    "F0_R1_P2": {"finishing_weight": 0.0, "pref_scale": 2.0, "receiver_weights": {"tendency": 1.0}},
}


def main(n_sims: int, names=None):
    t0 = time.time()
    ref = json.load(open("backtests/three_point_allocation_after.json"))["player_rows"]
    ref_by_team = {}
    for r in ref:
        ref_by_team.setdefault(r["team"], {})[r["player_id"]] = r
    games = base.pick_team_games(mcd.load_or_create_development_sample())
    cache = []
    for gid in games:
        snap = hgs.build_historical_game_snapshot(gid, tad.SEASON, tad.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        _, _, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        cache.append((gid, snap.home_team, snap.away_team, home_five, away_five, profiles))
    print("snapshots cached", len(cache), round(time.time() - t0), flush=True)
    results = {}
    for name, kw in CANDIDATES.items():
        if names and name not in names:
            continue
        t1 = time.time()
        rows, team_points, team_3pa = [], [], []
        with rtd.patched(**kw):
            for gid, home, away, home_five, away_five, profiles in cache:
                sink = []
                pts = []
                with tpd.capture_three_point_attempts(sink):
                    for i in range(n_sims):
                        try:
                            r = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=tad._seed(gid + name, i))
                        except DetailedGameSimulationFault:
                            continue
                        pts.append((r.final_home_score, r.final_away_score))
                n = max(1, len(pts))
                for team, five in ((home, home_five), (away, away_five)):
                    if team not in ref_by_team:
                        continue
                    trs = [ref_by_team[team][p] for p in five if p in ref_by_team[team]]
                    if len(trs) != 5 or (gid != trs[0]["game_id"]):
                        continue
                    counts = [sum(1 for a in sink if a["shooter"] == p) for p in five]
                    shares = tad.shares(counts)
                    for tr, c, s in zip(trs, counts, shares):
                        rows.append({**{k: tr[k] for k in ("team", "player_id", "real_share", "tendency", "spacing", "finishing",
                                                             "initiation", "ability", "expected_minutes", "slot")},
                                     "sim_share": s, "sim_3pa_per_game": c / n})
                    team_3pa.append(sum(counts) / n)
                team_points.append(_mean_pts(pts))
        dev = [r for r in rows if rtd.team_split(r["team"]) == "dev"]
        val = [r for r in rows if rtd.team_split(r["team"]) == "validation"]
        preds = ["tendency", "spacing", "finishing", "initiation", "ability", "expected_minutes"]
        results[name] = {
            "kwargs": kw, "all": rtd.allocation_metrics(rows, "sim_share"), "dev": rtd.allocation_metrics(dev, "sim_share"),
            "validation": rtd.allocation_metrics(val, "sim_share"),
            "sim_standardized_betas": tad.within_team_r2(rows, preds, "sim_share"),
            "team_3pa_per_game": sum(team_3pa) / len(team_3pa), "team_points_per_game": sum(team_points) / len(team_points),
            "mean_3pa_per_game_by_tendency_tercile": _terciles(rows), "runtime": time.time() - t1,
        }
        print(name, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in results[name]["all"].items()},
              "val", round(results[name]["validation"]["pearson"], 3), round(results[name]["validation"]["mae"], 3),
              "3pa", round(results[name]["team_3pa_per_game"], 1), round(time.time() - t1), flush=True)
        import os
        prior = json.load(open(OUT)) if os.path.exists(OUT) else {}
        prior.update(results)
        with open(OUT, "w") as f:
            json.dump(prior, f, indent=1, sort_keys=True, default=str)
    print("done", round(time.time() - t0))


def _mean_pts(pts):
    return sum(h + a for h, a in pts) / (2 * max(1, len(pts)))


def _terciles(rows):
    xs = sorted(rows, key=lambda r: r["tendency"])
    k = len(xs) // 3
    return {"low_preference": sum(r["sim_3pa_per_game"] for r in xs[:k]) / k,
            "mid_preference": sum(r["sim_3pa_per_game"] for r in xs[k:2 * k]) / k,
            "high_preference": sum(r["sim_3pa_per_game"] for r in xs[2 * k:]) / len(xs[2 * k:])}


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30, sys.argv[2:] or None)
