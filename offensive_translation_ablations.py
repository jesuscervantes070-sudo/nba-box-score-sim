"""OFFENSIVE TRANSLATION FAILURE DIAGNOSTIC V1: family-ablation audit. For each family, every simulated player's
fields in that family are set to the league-median value (both teams), the sampled games are re-simulated, and the
team-season offence ranking is compared with the report-only stable target (season points per game).
Measurement only -- nothing here changes the engine.

python3 offensive_translation_ablations.py   -> backtests/_ot_ablations.json
"""
import dataclasses
import gzip
import json
import multiprocessing as mp
from collections import defaultdict

import numpy as np

import historical_predictive_backtest as hpb
import offensive_translation_analysis as oa
import offensive_translation_capture as otc
import offensive_translation_experiments as ex
import player_input_team_strength_dataset as ds
from detailed_game import DetailedGameSimulationFault

N_SIMS = 30
ABLATIONS = {
    "baseline": (),
    "shooting_to_median": ex.FAMILIES["shooting"],
    "rim_creation_to_median": ex.FAMILIES["rim_creation"],
    "passing_to_median": ex.FAMILIES["passing"],
    "ball_security_to_median": ex.FAMILIES["ball_security"],
    "offensive_rebounding_to_median": ex.FAMILIES["offensive_rebounding"],
    "finishing_role_to_median": ex.FAMILIES["finishing_role"],
    "spacing_role_to_median": ex.FAMILIES["spacing_role"],
    "initiation_role_to_median": ex.FAMILIES["initiation_role"],
    "all_roles_to_median": ex.FAMILIES["finishing_role"] + ex.FAMILIES["spacing_role"] + ex.FAMILIES["initiation_role"],
    "three_point_preference_to_median": ex.FAMILIES["three_point_preference"],
    "other_tendencies_to_median": ("drive_aggression", "pass_vs_shoot", "midrange_preference", "pullup_vs_catch"),
    "all_tendencies_to_median": ("drive_aggression", "pass_vs_shoot", "midrange_preference", "pullup_vs_catch", "three_point_preference"),
    "volume_drivers_to_median": ex.FAMILIES["offensive_rebounding"] + ex.FAMILIES["ball_security"] + ex.FAMILIES["finishing_role"]
                                + ("drive_aggression", "pass_vs_shoot", "midrange_preference", "pullup_vs_catch"),
    "oreb_and_ball_security_to_median": ex.FAMILIES["offensive_rebounding"] + ex.FAMILIES["ball_security"],
}
ONLY = None  # set by main(argv) to run a subset
_MEDIANS = {}


def _init(medians):
    _MEDIANS.update(medians)


def _task(args):
    record, name, n = args
    fields = ABLATIONS[name]
    h, a, profiles = otc.build_game(record)
    if fields:
        profiles = {pid: dataclasses.replace(p, **{f: _MEDIANS[f] for f in fields if _MEDIANS.get(f) is not None}) for pid, p in profiles.items()}
    pts = {"HOME": [], "AWAY": []}
    poss = {"HOME": [], "AWAY": []}
    att = {"HOME": [], "AWAY": []}
    for i in range(n):
        try:
            totals, _, _ = otc.simulate_once("HOME", "AWAY", h, a, profiles, otc.sim_seed(record["game_id"], i, "ot-abl"))
        except DetailedGameSimulationFault:
            continue
        for side in pts:
            pts[side].append(totals[side]["points"]); poss[side].append(totals[side]["poss"])
            att[side].append(totals[side]["fga"] + 0.44 * totals[side]["fta"])
    return (name, record["game_id"], {s: float(np.mean(v)) for s, v in pts.items()}, {s: float(np.mean(v)) for s, v in poss.items()},
            {s: float(np.mean(v)) for s, v in att.items()})


def real_season_components(seasons):
    import historical_game_outcome as hgo
    from game_metadata import get_game_metadata
    from player_team_stints import team_as_of_date
    ts, att = {}, {}
    for season in seasons:
        tot = defaultdict(lambda: defaultdict(float))
        games = defaultdict(int)
        for gid, rows in hgo._game_log_index(season).items():
            meta = get_game_metadata(gid, season)
            if meta is None:
                continue
            seen = set()
            for pid, row in rows:
                team = team_as_of_date(pid, meta.game_date, season)
                if team in (meta.home_team, meta.away_team):
                    seen.add(team)
                    for k in ("fgm", "fga", "fg3m", "ftm", "fta"):
                        tot[team][k] += row.get(k, 0) or 0
            for team in seen:
                games[team] += 1
        for team, t in tot.items():
            pts = 2 * t["fgm"] + t["fg3m"] + t["ftm"]
            ts[(season, team)] = pts / (2 * (t["fga"] + 0.44 * t["fta"]))
            att[(season, team)] = (t["fga"] + 0.44 * t["fta"]) / games[team]
    return ts, att


def real_ts_arr(keys, real_ts):
    return np.array([real_ts[k] for k in keys])


def real_att_arr(keys, real_att):
    return np.array([real_att[k] for k in keys])


def main(only=None):
    real_ts, real_att = real_season_components(ds.SEASONS[1:])
    with gzip.open(ds.DATASET_PATH, "rt") as f:
        records = json.load(f)["records"]
    records.sort(key=lambda r: (r["date"], r["game_id"]))
    dists = ex.field_distributions(records)
    medians = ex.median_fields(dists)
    subset = [r for r in records if r["season"] != ds.SEASONS[0]][::2]
    ppg = {}
    for s in ds.SEASONS[1:]:
        pts = defaultdict(list)
        for o in hpb._all_game_outcomes(s):
            pts[o.home_team].append(o.home_score); pts[o.away_team].append(o.away_score)
        ppg[s] = {t: float(np.mean(v)) for t, v in pts.items()}
    by_id = {r["game_id"]: r for r in subset}
    results = {}
    with mp.Pool(8, initializer=_init, initargs=(medians,)) as pool:
        for name in ABLATIONS:
            if only and name not in only:
                continue
            per_game = {}
            for _, gid, pts, poss, att in pool.imap_unordered(_task, [(r, name, N_SIMS) for r in subset], chunksize=4):
                per_game[gid] = (pts, poss, att)
            team = defaultdict(list)
            for gid, (pts, poss, att) in per_game.items():
                r = by_id[gid]
                for side in ("home", "away"):
                    team[(r["season"], r["context_only"][f"{side}_team"])].append((pts[side.upper()], pts[side.upper()] / poss[side.upper()] * 100,
                                                                                  att[side.upper()], pts[side.upper()] / (2 * att[side.upper()])))
            keys = [k for k, v in team.items() if len(v) >= 5]
            sim = np.array([np.mean([x[0] for x in team[k]]) for k in keys])
            ortg = np.array([np.mean([x[1] for x in team[k]]) for k in keys])
            real = np.array([ppg[k[0]][k[1]] for k in keys])
            attempts = np.array([np.mean([x[2] for x in team[k]]) for k in keys])
            ts = np.array([np.mean([x[3] for x in team[k]]) for k in keys])
            results[name] = {"attempts_sd": float(attempts.std()), "ts_pearson_with_real_ts": oa.pearson(real_ts_arr(keys, real_ts), ts) if real_ts else None,
                             "attempts_pearson_with_real_attempts": oa.pearson(real_att_arr(keys, real_att), attempts) if real_att else None,
                             "n_team_seasons": len(keys), "pearson_with_real_season_ppg": oa.pearson(real, sim), "spearman_with_real_season_ppg": oa.spearman(real, sim),
                             "pearson_ortg_with_real_season_ppg": oa.pearson(real, ortg), "team_season_sim_points_sd": float(sim.std()), "mean_sim_points": float(sim.mean()),
                             "mean_sim_ortg": float(ortg.mean()),
                             "per_season": {s: {"pearson": oa.pearson(real[[i for i, k in enumerate(keys) if k[0] == s]], sim[[i for i, k in enumerate(keys) if k[0] == s]])}
                                            for s in ds.SEASONS[1:]}}
            print(name, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in results[name].items() if k != "per_season"}, flush=True)
    out_path = "backtests/_ot_ablations.json" if not only else "backtests/_ot_ablations_volume.json"
    with open(out_path, "w") as f:
        json.dump({"n_sims": N_SIMS, "n_games": len(subset), "results": results}, f, indent=1)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:] or None)
