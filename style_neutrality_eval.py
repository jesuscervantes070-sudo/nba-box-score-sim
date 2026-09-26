"""STRENGTH-NEUTRAL OFFENSIVE STYLE V1 -- evaluation harness.

Re-simulates the (non-holdout) player-input dataset sample under a named engine configuration, and measures
team shot-diet dispersion, stable-ranking correlation, player-input -> sim-offense transfer, and synthetic
identical-ability tendency sweeps. Configurations only set the documented module constants in
`action_selection`; nothing here touches player truth.

python3 style_neutrality_eval.py resim <config> [n_sims]     -> backtests/_sn_resim_<config>.json.gz
python3 style_neutrality_eval.py sweep <config> [n_sims]     -> backtests/_sn_sweep_<config>.json
"""
import gzip
import json
import multiprocessing as mp
import sys
from collections import defaultdict

import numpy as np

import action_selection as acs
import offensive_translation_capture as otc
import offensive_translation_experiments as ex
from detailed_game import DetailedGameSimulationFault

# Team-level centering / capping candidates (TEAM_STYLE_CENTERING 0.5/0.75/1.0, PREFERENCE_CAP 1.0) were run against a
# temporary, since-reverted production knob; their stored results live in backtests/_sn_metrics_center50.json and
# backtests/_sn_sweep_center*.json / _sn_sweep_cap1.0.json. Only configs expressible with existing constants remain here.
CONFIGS = {
    "baseline": {},
    "w1.0": {"THREE_POINT_PREFERENCE_WEIGHT": 1.0},
    "w1.4": {"THREE_POINT_PREFERENCE_WEIGHT": 1.4},
    # report-only ceiling: every zone/action tendency contributes nothing (NOT a production candidate)
    "all_zero": {"THREE_POINT_PREFERENCE_WEIGHT": 0.0, "MIDRANGE_PREFERENCE_WEIGHT": 0.0, "TENDENCY_DRIVE_WEIGHT": 0.0},
    "w1.0_mid0.5_drive0.75": {"THREE_POINT_PREFERENCE_WEIGHT": 1.0, "MIDRANGE_PREFERENCE_WEIGHT": 0.5, "TENDENCY_DRIVE_WEIGHT": 0.75},
    "w1.0_drive0.75": {"THREE_POINT_PREFERENCE_WEIGHT": 1.0, "TENDENCY_DRIVE_WEIGHT": 0.75},
}


def apply_config(cfg: dict) -> None:
    for k, v in cfg.items():
        assert hasattr(acs, k), k
        setattr(acs, k, v)


def _init(cfg):
    apply_config(cfg)


def _game_task(args):
    record, n = args
    g = otc.simulate_game_record(record, n, tag="sn-sim")
    out = {"game_id": g["game_id"], "season": g["season"], "n_valid": g["n_valid"], "side": {}}
    for side in ("HOME", "AWAY"):
        out["side"][side] = {k: float(np.mean(v)) for k, v in g["arrays"][side].items()}
    h, a = np.array(g["arrays"]["HOME"]["points"]), np.array(g["arrays"]["AWAY"]["points"])
    out["home_win_frac"] = float(np.mean(h > a) + 0.5 * np.mean(h == a))
    out["margin"] = float(np.mean(h - a))
    return out


def resim(name: str, n: int = 30, workers: int = 8) -> str:
    records = ex.load_records()
    with mp.Pool(workers, initializer=_init, initargs=(CONFIGS[name],)) as pool:
        games = list(pool.imap(_game_task, [(r, n) for r in records], chunksize=2))
    path = f"backtests/_sn_resim_{name}.json.gz"
    with gzip.open(path, "wt") as f:
        json.dump({"config": name, "settings": CONFIGS[name], "n_sims": n, "games": games}, f)
    return path


# ---------------------------------------------------------------- synthetic identical-ability sweeps
def _sweep_task(args):
    fields_json, label, n = args
    base = ex.median_fields(ex.field_distributions(ex.load_records()))
    home = {**base, **json.loads(fields_json)}
    res = ex.run_matchup(home, base, n, f"sn-sweep|{label}")
    return label, res


def sweep_specs(dists) -> dict:
    """Same-ability sweeps: one tendency at a time, all five home players; plus skill x preference grid."""
    specs = {}
    for tend in ("three_point_preference", "midrange_preference", "drive_aggression"):
        for q in (1, 10, 50, 90, 99):
            specs[f"{tend}|p{q}"] = {tend: float(np.percentile(dists[tend], q))}
    for q3 in (10, 50, 90):
        for qp in (10, 50, 90):
            specs[f"skill3_p{q3}|pref3_p{qp}"] = {"three_point_shrunk_rate": float(np.percentile(dists["three_point_shrunk_rate"], q3)),
                                                  "three_point_preference": float(np.percentile(dists["three_point_preference"], qp))}
            specs[f"skillmid_p{q3}|prefmid_p{qp}"] = {"midrange_shrunk_rate": float(np.percentile(dists["midrange_shrunk_rate"], q3)),
                                                      "midrange_preference": float(np.percentile(dists["midrange_preference"], qp))}
    return specs


def sweep(name: str, n: int = 300, workers: int = 8) -> str:
    dists = ex.field_distributions(ex.load_records())
    specs = sweep_specs(dists)
    with mp.Pool(workers, initializer=_init, initargs=(CONFIGS[name],)) as pool:
        out = dict(pool.imap(_sweep_task, [(json.dumps(v), k, n) for k, v in specs.items()]))
    path = f"backtests/_sn_sweep_{name}.json"
    json.dump({"config": name, "settings": CONFIGS[name], "n": n, "specs": specs, "results": out}, open(path, "w"), indent=1)
    return path


if __name__ == "__main__":
    mode, name = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else None
    print(resim(name, n or 30) if mode == "resim" else sweep(name, n or 300))
