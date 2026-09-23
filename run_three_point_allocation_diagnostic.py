"""Runner: python3 run_three_point_allocation_diagnostic.py <label> [n_sims]
Writes backtests/three_point_allocation_<label>.json. Development games only."""
import dataclasses
import json
import sys
import time
from pathlib import Path

import three_point_allocation_diagnostic as tad
import three_point_resolution_diagnostic as tpd
import margin_compression_diagnostic as mcd
import historical_game_snapshot as hgs
import shot_zone_ingestion as szi
from loader import load_player_advanced_stats

SEASON = tad.SEASON

ROLE_SETS = {
    "A_all_identical": [{}] * 5,
    "C_spacing_only": [{"role_off_spacing": v} for v in (0.7, 0.55, 0.4, 0.25, 0.1)],
    "D_initiation_only": [{"role_off_initiation": v} for v in (9.0, 6.0, 4.0, 2.0, 1.0)],
    "E_finishing_only": [{"role_off_finishing": v} for v in (0.2, 0.35, 0.5, 0.65, 0.8)],
    "F_three_point_preference_only": [{"three_point_preference": v} for v in (1.0, 0.5, 0.0, -0.5, -1.0)],
    "B_realistic_roles": [
        {"role_off_initiation": 9.0, "role_off_spacing": 0.5, "role_off_finishing": 0.3},
        {"role_off_initiation": 5.0, "role_off_spacing": 0.6, "role_off_finishing": 0.4},
        {"role_off_initiation": 3.0, "role_off_spacing": 0.55, "role_off_finishing": 0.5},
        {"role_off_initiation": 2.0, "role_off_spacing": 0.3, "role_off_finishing": 0.6},
        {"role_off_initiation": 1.0, "role_off_spacing": 0.05, "role_off_finishing": 0.85},
    ],
}


def real_three_point_by_name():
    out = {}
    for row in szi.load_shot_zones(SEASON).values():
        fga = sum(row.get(k, 0) or 0 for k in ("above_break3_fga", "left_corner3_fga", "right_corner3_fga"))
        fgm = sum(row.get(k, 0) or 0 for k in ("above_break3_fgm", "left_corner3_fgm", "right_corner3_fgm"))
        out[row["player_name"]] = (fgm, fga)
    return out


def pick_team_games(game_ids):
    """Mechanical greedy cover: walk sorted dev games, keep a game if it adds an uncovered team."""
    covered, picked = set(), []
    import game_metadata
    for gid in sorted(game_ids):
        meta = game_metadata.get_game_metadata(gid, SEASON)
        new = {meta.home_team, meta.away_team} - covered
        if new:
            picked.append(gid)
            covered |= {meta.home_team, meta.away_team}
    return picked


def main(label: str, n_sims: int):
    t0 = time.time()
    out = {"label": label, "n_sims": n_sims}

    print("--- symmetry / permutation ---")
    out["permutation_tests"] = {name: tad.permutation_test(kw, n_sims, name) for name, kw in ROLE_SETS.items()}
    for name, res in out["permutation_tests"].items():
        print(name, "slot means", [round(x, 2) for x in res["mean_share_by_slot"]],
              "player means", [round(x, 2) for x in res["mean_share_by_player"]])

    print("--- real teams: real vs simulated allocation ---")
    real3 = real_three_point_by_name()
    adv = load_player_advanced_stats(SEASON)
    games = pick_team_games(mcd.load_or_create_development_sample())
    player_rows, team_rows = [], []
    branch_counter = {}
    seen_teams = set()
    for gid in games:
        snap = hgs.build_historical_game_snapshot(gid, SEASON, tad.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        with tad.count_nearest_teammate_branches(branch_counter):
            res_home = tad.simulate_side(home_five, profiles, gid + "|H", n_sims, away_five=away_five)
        flipped = {pid: dataclasses.replace(prof, team_id=("HOME" if pid in away_five else "AWAY"))
                   for pid, prof in profiles.items()}
        with tad.count_nearest_teammate_branches(branch_counter):
            res_away = tad.simulate_side(away_five, flipped, gid + "|A", n_sims, away_five=home_five)
        for team_name, five, team_snap, res in ((snap.home_team, home_five, snap.home_team_snapshot, res_home),
                                                 (snap.away_team, away_five, snap.away_team_snapshot, res_away)):
            if team_name in seen_teams:
                continue
            seen_teams.add(team_name)
            if res is None:
                continue
            entries = {p.player_id: p for p in team_snap.players}
            real_3pa = []
            rows = []
            for slot, pid in enumerate(five):
                e = entries[pid]
                m, a = real3.get(e.canonical_name, (0, 0))
                a_stats = adv.get(e.canonical_name, {})
                prof = e.simulation_profile
                rows.append({
                    "team": team_name, "game_id": gid, "player_id": pid, "slot": slot,
                    "real_3pa": a, "real_3pct": (m / a) if a else None,
                    "real_3pa_per_min": (a / (a_stats.get("gp", 0) * a_stats.get("mpg", 0))) if a_stats.get("gp") and a_stats.get("mpg") else None,
                    "expected_minutes": e.expected_minutes,
                    "ability": prof.three_point_shrunk_rate, "tendency": prof.three_point_preference,
                    "spacing": prof.role_off_spacing, "initiation": prof.role_off_initiation, "finishing": prof.role_off_finishing,
                    "sim_3pa": res["per_player"][pid]["3pa"],
                })
            real_share = tad.shares([r["real_3pa"] for r in rows])
            sim_share = tad.shares([r["sim_3pa"] for r in rows])
            for r, rs, ss in zip(rows, real_share, sim_share):
                r["real_share"], r["sim_share"] = rs, ss
                player_rows.append(r)
            team_rows.append({"team": team_name, "game_id": gid,
                              "real_hhi": tad.herfindahl(real_share), "sim_hhi": tad.herfindahl(sim_share),
                              "real_top1": tad.top_k_share(real_share, 1), "sim_top1": tad.top_k_share(sim_share, 1),
                              "real_top2": tad.top_k_share(real_share, 2), "sim_top2": tad.top_k_share(sim_share, 2),
                              "real_share_by_slot": real_share, "sim_share_by_slot": sim_share,
                              "sim_team_3pa_per_sim": res["team_3pa_per_sim"]})
    real_sh = [r["real_share"] for r in player_rows]
    sim_sh = [r["sim_share"] for r in player_rows]
    out["real_vs_sim_allocation"] = {
        "n_players": len(player_rows), "n_teams": len(team_rows),
        "pearson": tad._pearson(real_sh, sim_sh), "spearman": tad.spearman(real_sh, sim_sh),
        "mae": tad._mean([abs(a - b) for a, b in zip(real_sh, sim_sh)]),
        "mean_slot_share_real": [tad._mean([r["real_share_by_slot"][s] for r in team_rows]) for s in range(5)],
        "mean_slot_share_sim": [tad._mean([r["sim_share_by_slot"][s] for r in team_rows]) for s in range(5)],
        "concentration": {k: tad._mean([t[k] for t in team_rows]) for k in
                          ("real_hhi", "sim_hhi", "real_top1", "sim_top1", "real_top2", "sim_top2")},
        "teams": team_rows,
    }
    predictors_sets = {
        "slot_only": [f"slot_{s}" for s in range(1, 5)],
        "B_tendency": ["tendency"], "C_spacing": ["spacing"], "D_tendency_role": ["tendency", "spacing", "initiation", "finishing"],
        "E_tendency_role_ability": ["tendency", "spacing", "initiation", "finishing", "ability"],
        "ability_only": ["ability"], "minutes_only": ["expected_minutes"],
    }
    for r in player_rows:
        for s in range(1, 5):
            r[f"slot_{s}"] = 1.0 if r["slot"] == s else 0.0
    out["real_explanatory"] = {name: tad.within_team_r2(player_rows, preds, "real_share") for name, preds in predictors_sets.items()}
    out["sim_explanatory"] = {name: tad.within_team_r2(player_rows, preds, "sim_share") for name, preds in predictors_sets.items()}
    out["sim_explanatory"]["slot_plus_fields"] = tad.within_team_r2(
        player_rows, predictors_sets["slot_only"] + predictors_sets["E_tendency_role_ability"], "sim_share")
    out["real_explanatory"]["slot_plus_fields"] = tad.within_team_r2(
        player_rows, predictors_sets["slot_only"] + predictors_sets["E_tendency_role_ability"], "real_share")
    out["real_correlations_with_real_share"] = {
        f: {"pearson": tad._pearson([r[f] for r in player_rows], real_sh),
            "spearman": tad.spearman([r[f] for r in player_rows], real_sh)}
        for f in ("real_3pct", "ability", "tendency", "spacing", "initiation", "finishing", "expected_minutes", "real_3pa_per_min")}
    out["sim_correlations_with_sim_share"] = {
        f: {"pearson": tad._pearson([r[f] for r in player_rows], sim_sh),
            "spearman": tad.spearman([r[f] for r in player_rows], sim_sh)}
        for f in ("ability", "tendency", "spacing", "initiation", "finishing", "expected_minutes", "slot")}
    out["nearest_teammate_branch_counts"] = branch_counter
    out["player_rows"] = player_rows

    print("--- slot-balanced stacking ---")
    out["balanced_stacking_elite"] = tad.balanced_stacking(tad.ELITE, n_sims=max(20, n_sims // 2))
    out["balanced_stacking_weak"] = tad.balanced_stacking(tad.WEAK, n_sims=max(20, n_sims // 2))
    out["runtime_seconds"] = time.time() - t0
    Path("backtests").mkdir(exist_ok=True)
    with open(f"backtests/three_point_allocation_{label}.json", "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print("done", out["runtime_seconds"])


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 60)
