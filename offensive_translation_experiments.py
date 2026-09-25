"""OFFENSIVE TRANSLATION FAILURE DIAGNOSTIC V1: controlled experiments on the detailed engine (and the old
aggregate engine).  Everything here is measurement; no production behaviour is changed.

python3 offensive_translation_experiments.py   -> backtests/_ot_experiments.json
"""
import copy
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import sys
import textwrap
from typing import Dict, List

import numpy as np

import offensive_translation_capture as otc
import player_input_team_strength_dataset as ds
from detailed_game import DetailedGameSimulationFault, simulate_detailed_game
from possession_orchestrator import PlayerSimulationProfile

DATASET = "backtests/player_input_team_strength_dataset_v1.json.gz"
OUT = os.environ.get("OT_EXP", "backtests/_ot_experiments.json")
N_SCALE = float(os.environ.get("OT_SCALE", "1"))  # <1 only for debugging the pipeline


def scaled(n: int) -> int:
    return max(4, int(n * N_SCALE))

FAMILIES = {
    "shooting": ("rim_finishing_shrunk_rate", "floater_short_mid_shrunk_rate", "midrange_shrunk_rate",
                 "three_point_shrunk_rate", "free_throw_shrunk_rate"),
    "rim_creation": ("rim_access_creation_shrunk_rate",),
    "passing": ("passing_accuracy_ast_pct", "playmaking_vision_shrunk_rate"),
    "ball_security": ("ball_security_error_rate",),
    "offensive_rebounding": ("offensive_rebounding_shrunk_rate",),
    "finishing_role": ("role_off_finishing",),
    "spacing_role": ("role_off_spacing",),
    "initiation_role": ("role_off_initiation",),
    "three_point_preference": ("three_point_preference",),
}
LOWER_IS_BETTER = {"ball_security_error_rate"}
LEVEL_PERCENTILES = {"weak": 10, "average": 50, "strong": 90, "elite": 99}


def load_records() -> List[dict]:
    with gzip.open(DATASET, "rt") as f:
        return json.load(f)["records"]


def field_distributions(records) -> Dict[str, np.ndarray]:
    vals = {n: [] for n in ds.PLAYER_FIELDS}
    for r in records:
        for side in ("home", "away"):
            for p in r[side]:
                if p["primary"]:
                    for n, v in zip(ds.PLAYER_FIELDS, p["f"]):
                        if v is not None:
                            vals[n].append(v)
    return {n: np.array(v) for n, v in vals.items() if v}


def percentile_of(dists, name, q, lower_is_better=False):
    if name not in dists:
        return None
    return float(np.percentile(dists[name], 100 - q if lower_is_better else q))


def median_fields(dists) -> dict:
    return {n: float(np.median(dists[n])) if n in dists else None for n in ds.PLAYER_FIELDS}


def five(fields: dict, side: str, base: int):
    ids = tuple(str(base + i) for i in range(5))
    return ids, {pid: PlayerSimulationProfile.synthetic(pid, side, **fields) for pid in ids}


def summarize(sums: Dict[str, float], n: int) -> dict:
    s = sums
    poss = s["poss"] or 1.0
    fga = s["fga"] or 1.0
    return {
        "ortg": s["points"] / poss * 100, "points": s["points"] / n, "poss": poss / n,
        "efg": (s["fgm"] + 0.5 * s["fg3m"]) / fga, "tov_per_100": s["tov"] / poss * 100, "orb_per_100": s["oreb"] / poss * 100,
        "ft_rate": s["fta"] / fga, "three_rate": s["fg3a"] / fga, "ftm_per_100": s["ftm"] / poss * 100,
        "exp_pps_open": s["exp_fg_pts_unblocked"] / max(1.0, s["open_fga"] - s["blocked"]),
        "real_pps_open": s["real_fg_pts"] / max(1.0, s["open_fga"] - s["blocked"]),
        "block_rate": s["blocked"] / max(1.0, s["open_fga"]), "fga_per_100": fga / poss * 100,
    }


def run_matchup(home_fields: dict, away_fields: dict, n: int, tag: str) -> dict:
    h, hp = five(home_fields, "HOME", 800001)
    a, ap = five(away_fields, "AWAY", 800101)
    profiles = {**hp, **ap}
    tot = {"HOME": otc.new_side_totals(), "AWAY": otc.new_side_totals()}
    fams = {}
    valid = 0
    margins = []
    for i in range(n):
        try:
            totals, families, result = otc.simulate_once("HOME", "AWAY", h, a, profiles, otc.sim_seed(tag, i, "ot-exp"))
        except DetailedGameSimulationFault:
            continue
        valid += 1
        margins.append(result.final_home_score - result.final_away_score)
        for side in tot:
            for k in otc.ARRAY_KEYS:
                tot[side][k] += totals[side][k]
    return {"n": valid, "home": summarize(tot["HOME"], valid), "away": summarize(tot["AWAY"], valid),
            "mean_margin": float(np.mean(margins)), "margin_sd": float(np.std(margins))}


# ---------------------------------------------------------------- workers
def _sweep_task(args):
    family, level, fields_json, base_json, n = args
    base = json.loads(base_json)
    fields = json.loads(fields_json)
    return family, level, run_matchup({**base, **fields}, base, n, f"sweep|{family}|{level}")


def _team_task(args):
    level, fields_json, base_json, n = args
    base = json.loads(base_json)
    return level, run_matchup({**base, **json.loads(fields_json)}, base, n, f"team|{level}")


def _convergence_task(args):
    record, n = args
    h, a, profiles = otc.build_game(record)
    pts_h, pts_a, poss = [], [], []
    for i in range(n):
        try:
            totals, _, result = otc.simulate_once("HOME", "AWAY", h, a, profiles, otc.sim_seed(record["game_id"], i, "ot-conv"))
        except DetailedGameSimulationFault:
            continue
        pts_h.append(result.final_home_score); pts_a.append(result.final_away_score)
        poss.append((totals["HOME"]["poss"], totals["AWAY"]["poss"]))
    return record["game_id"], pts_h, pts_a, poss


def _path_task(args):
    record, n = args
    h, a, profiles = otc.build_game(record)
    rows = []
    for i in range(n):
        try:
            sink = []
            with otc.capture_shots(sink):
                result = simulate_detailed_game("HOME", "AWAY", h, a, profiles, rng_seed=otc.sim_seed(record["game_id"], i, "ot-path"))
        except DetailedGameSimulationFault:
            continue
        first = result.possessions[0]
        initiator = None
        for e in first.events:
            if e.event_type.value == "POSSESSION_START":
                initiator = e.primary_player_id
                break
        side_first = "HOME" if first.offense_team_id == "HOME" else "AWAY"
        first_scorer = None
        first_tov = None
        for rec in result.possessions[:30]:
            side = "HOME" if rec.offense_team_id == "HOME" else "AWAY"
            if first_scorer is None and rec.provisional_deltas.points > 0:
                first_scorer = side
            if first_tov is None and (rec.provisional_deltas.turnovers + rec.provisional_deltas.team_turnovers) > 0:
                first_tov = side
        q1 = result.periods[0]
        rows.append({"initiator_index": (h + a).index(initiator) if initiator in (h + a) else -1, "first_offense": side_first,
                     "first_scorer": first_scorer, "first_turnover_side": first_tov, "q1_margin": q1.home_points - q1.away_points,
                     "final_margin": result.final_home_score - result.final_away_score})
    return record["game_id"], rows


def _perm_task(args):
    record, home_order, away_order, n, tag = args
    h, a, profiles = otc.build_game(record, home_order=home_order, away_order=away_order)
    tot = {"HOME": otc.new_side_totals(), "AWAY": otc.new_side_totals()}
    valid = 0
    for i in range(n):
        try:
            totals, _, _ = otc.simulate_once("HOME", "AWAY", h, a, profiles, otc.sim_seed(record["game_id"], i, "ot-perm"))
        except DetailedGameSimulationFault:
            continue
        valid += 1
        for side in tot:
            for k in otc.ARRAY_KEYS:
                tot[side][k] += totals[side][k]
    return record["game_id"], home_order, away_order, summarize(tot["HOME"], valid), summarize(tot["AWAY"], valid)


HASH_SCRIPT = textwrap.dedent('''
    import gzip, json, sys
    import offensive_translation_capture as otc
    recs = json.load(gzip.open("backtests/player_input_team_strength_dataset_v1.json.gz", "rt"))["records"]
    r = [x for x in recs if x.get("detailed_sim")][3]
    base = int(sys.argv[1])
    h, a, pr = otc.build_game(r, home_base=base, away_base=base + 100)
    out = []
    for i in range(8):
        res = otc.simulate_detailed_game("HOME", "AWAY", h, a, pr, rng_seed=1000 + i)
        out.append((res.final_home_score, res.final_away_score, res.total_possessions))
    print(json.dumps(out))
''')


def hash_order_check() -> dict:
    results = {}
    for label, seed, base in (("hashseed1_ids700001", "1", 700001), ("hashseed2_ids700001", "2", 700001), ("hashseed3_ids123456", "3", 123456),
                              ("hashseed4_ids900001", "4", 900001)):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", HASH_SCRIPT, str(base)], capture_output=True, text=True, env=env, cwd=".")
        results[label] = json.loads(out.stdout.strip().splitlines()[-1]) if out.returncode == 0 else out.stderr[-300:]
    first = next(iter(results.values()))
    return {"runs": results, "all_identical": all(v == first for v in results.values())}


# ---------------------------------------------------------------- aggregate engine
def aggregate_engine_response(season="2023-24", n_games=None) -> dict:
    import game_engine as ge
    from loader import load_teams, load_league_pace_variation
    n_games = n_games or scaled(4000)
    teams = load_teams(season)
    avg = ge.compute_league_averages(teams, load_league_pace_variation(season))
    ppg = {t: sum(p.pts for p in team.players) for t, team in teams.items()}
    med = float(np.median(list(ppg.values())))
    ordered = sorted(teams, key=lambda t: abs(ppg[t] - med))
    base_name, opp_name = ordered[0], ordered[1]

    def modified(kind):
        team = copy.deepcopy(teams[base_name])
        for p in team.players:
            if kind == "three_point_plus_0.04":
                p.fg3m += 0.04 * p.fg3a; p.fgm += 0.04 * p.fg3a
            elif kind == "two_point_plus_0.04":
                p.fgm += 0.04 * (p.fga - p.fg3a)
            elif kind == "free_throw_plus_0.06":
                p.ftm += 0.06 * p.fta
            elif kind == "offensive_rebounding_x1.4":
                p.oreb *= 1.4; p.reb += 0.4 * p.oreb / 1.4
            elif kind == "turnovers_x0.85":
                p.tov *= 0.85
        return team

    out = {"base_team": base_name, "opponent": opp_name}
    for kind in ("baseline", "three_point_plus_0.04", "two_point_plus_0.04", "free_throw_plus_0.06",
                 "offensive_rebounding_x1.4", "turnovers_x0.85"):
        team = teams[base_name] if kind == "baseline" else modified(kind)
        ge._rng = np.random.default_rng(int(hashlib.sha256(f"agg-resp|{kind}".encode()).hexdigest()[:8], 16))
        pts = [ge.simulate_game(team, teams[opp_name], avg).home_score for _ in range(n_games)]
        out[kind] = {"mean_points": float(np.mean(pts)), "se": float(np.std(pts) / np.sqrt(n_games))}
    for kind in list(out):
        if isinstance(out[kind], dict) and kind != "baseline":
            out[kind]["delta_points"] = out[kind]["mean_points"] - out["baseline"]["mean_points"]
    return out


def detailed_equivalent_response(dists, n=None) -> dict:
    n = n or scaled(600)
    base = median_fields(dists)
    changes = {
        "three_point_plus_0.04": {"three_point_shrunk_rate": base["three_point_shrunk_rate"] + 0.04},
        "two_point_plus_0.04": {k: base[k] + 0.04 for k in ("rim_finishing_shrunk_rate", "floater_short_mid_shrunk_rate", "midrange_shrunk_rate")},
        "free_throw_plus_0.06": {"free_throw_shrunk_rate": base["free_throw_shrunk_rate"] + 0.06},
        "offensive_rebounding_x1.4": {"offensive_rebounding_shrunk_rate": base["offensive_rebounding_shrunk_rate"] * 1.4},
        "turnovers_x0.85": {"ball_security_error_rate": base["ball_security_error_rate"] * 0.85},
    }
    out = {"baseline": run_matchup(base, base, n, "eq|baseline")}
    for kind, kw in changes.items():
        res = run_matchup({**base, **kw}, base, n, f"eq|{kind}")
        res["delta_points"] = res["home"]["points"] - out["baseline"]["home"]["points"]
        res["delta_ortg"] = res["home"]["ortg"] - out["baseline"]["home"]["ortg"]
        out[kind] = res
    return out


def main():
    records = load_records()
    dists = field_distributions(records)
    base = median_fields(dists)
    base_json = json.dumps(base)
    out = {"field_percentiles": {n: {q: float(np.percentile(v, q)) for q in (1, 10, 50, 90, 99)} for n, v in dists.items()}}
    sim_games = sorted([r for r in records if r.get("detailed_sim")], key=lambda r: r["game_id"])
    with mp.Pool(8) as pool:
        # controlled single-family sweeps
        tasks = []
        for fam, fields in FAMILIES.items():
            for level, q in LEVEL_PERCENTILES.items():
                kw = {f: percentile_of(dists, f, q, f in LOWER_IS_BETTER) for f in fields if f in dists}
                tasks.append((fam, level, json.dumps(kw), base_json, scaled(300)))
        sweeps = {}
        for fam, level, res in pool.imap_unordered(_sweep_task, tasks):
            sweeps.setdefault(fam, {})[level] = res
        out["single_family_sweeps"] = sweeps
        print("sweeps done", flush=True)
        # realistic offensive teams: every offensive field at one percentile
        off_fields = [f for fam in ("shooting", "rim_creation", "passing", "ball_security", "offensive_rebounding", "finishing_role",
                                    "spacing_role", "initiation_role", "three_point_preference") for f in FAMILIES[fam]]
        team_tasks = []
        for level, q in LEVEL_PERCENTILES.items():
            kw = {f: percentile_of(dists, f, q, f in LOWER_IS_BETTER) for f in off_fields if f in dists}
            team_tasks.append((level, json.dumps(kw), base_json, scaled(500)))
        out["realistic_offensive_teams"] = {lvl: res for lvl, res in pool.imap_unordered(_team_task, team_tasks)}
        print("teams done", flush=True)
        # Monte Carlo convergence on 16 deterministic games
        conv_games = sim_games[::max(1, len(sim_games) // 16)][:16]
        conv = {}
        for gid, ph, pa, poss in pool.imap_unordered(_convergence_task, [(g, scaled(500)) for g in conv_games]):
            conv[gid] = {"home": ph, "away": pa, "poss": poss}
        by_id = {g["game_id"]: g for g in conv_games}
        out["mc_convergence_raw"] = {gid: {"home": v["home"], "away": v["away"], "poss": v["poss"]} for gid, v in conv.items()}
        out["mc_v1_reference"] = {gid: by_id[gid]["detailed_sim"] for gid in conv}
        print("convergence done", flush=True)
        # path dependence on 6 games x 400 sims
        path_games = sim_games[5::max(1, len(sim_games) // 6)][:6]
        out["path_dependence_raw"] = {gid: rows for gid, rows in pool.imap_unordered(_path_task, [(g, scaled(400)) for g in path_games])}
        print("path done", flush=True)
        # lineup permutation invariance: 4 games x 5 home orderings (+ 2 away) x 300 sims
        perm_tasks = []
        rng = np.random.default_rng(7)
        for g in sim_games[7::max(1, len(sim_games) // 4)][:4]:
            n_home, n_away = len(g["home"]), len(g["away"])
            for k in range(5):
                perm_tasks.append((g, list(rng.permutation(n_home)), None, scaled(300), f"h{k}"))
            for k in range(2):
                perm_tasks.append((g, None, list(rng.permutation(n_away)), scaled(300), f"a{k}"))
        perms = {}
        for gid, ho, ao, hs, as_ in pool.imap_unordered(_perm_task, perm_tasks):
            perms.setdefault(gid, []).append({"home_order": ho, "away_order": ao, "home": hs, "away": as_})
        out["permutation_invariance"] = perms
        # identical average teams: home/away symmetry
        sym = pool.starmap(run_matchup, [(base, base, scaled(250), f"sym|{k}") for k in range(8)])
        out["identical_teams_symmetry"] = {"chunks": sym}
    out["hash_and_id_order_check"] = hash_order_check()
    out["aggregate_engine_response"] = aggregate_engine_response()
    out["detailed_equivalent_response"] = detailed_equivalent_response(dists)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
