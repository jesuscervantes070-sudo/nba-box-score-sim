"""OFFENSIVE TRANSLATION FAILURE DIAGNOSTIC V1: instrumented detailed-engine simulation.

Read-only measurement. Engine inputs are reconstructed from the stored pregame player state
(primary five per side, synthetic player ids), so no snapshot rebuild is needed and player identity
cannot matter. The shot wrappers only record what the engine already computed and return its result
unchanged.
"""
import contextlib
import gzip
import hashlib
import json
import multiprocessing as mp
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np

import player_input_team_strength_dataset as ds
import possession_orchestrator as po
import shot_resolution as sr
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault
from possession_orchestrator import PlayerSimulationProfile

CACHE_PATH = "backtests/offensive_translation_sim_cache_v1.json.gz"
N_SIMS = 100
ARRAY_KEYS = ("points", "poss", "fga", "fg3a", "fgm", "fg3m", "fta", "ftm", "tov", "oreb", "exp_fg_pts", "real_fg_pts",
              "blocked", "open_fga", "fouled_shots", "exp_fg_pts_unblocked")


def build_side(rows: List[dict], side: str, base_id: int) -> Tuple[Tuple[str, ...], Dict[str, PlayerSimulationProfile]]:
    ids, profiles = [], {}
    for i, p in enumerate([p for p in rows if p["primary"]]):
        pid = str(base_id + i)
        ids.append(pid)
        profiles[pid] = PlayerSimulationProfile.synthetic(pid, side, **dict(zip(ds.PLAYER_FIELDS, p["f"])))
    return tuple(ids), profiles


def build_game(record: dict, home_base: int = 700001, away_base: int = 700101, home_order=None, away_order=None):
    home_rows = record["home"] if home_order is None else [record["home"][i] for i in home_order]
    away_rows = record["away"] if away_order is None else [record["away"][i] for i in away_order]
    h, hp = build_side(home_rows, "HOME", home_base)
    a, ap = build_side(away_rows, "AWAY", away_base)
    return h, a, {**hp, **ap}


@contextlib.contextmanager
def capture_shots(sink: List[dict]):
    """Records, for every resolved (unwhistled) field-goal attempt, the offense side, a shot label, the engine's
    own make probability and the realized outcome. Whistled shots are counted separately from the trace."""
    orig_perimeter, orig_interior = po.apply_perimeter_shot_to_engine, po.apply_interior_shot_to_engine

    def perimeter(engine, shooter_id, shot_context, *args, **kwargs):
        prob = sr.shot_make_probability(shot_context)
        offense = engine.state.offense_team_id  # a miss clears it, so read it before resolving
        result = orig_perimeter(engine, shooter_id, shot_context, *args, **kwargs)
        pts = 3 if shot_context.shot_family == sr.ShotFamily.THREE_POINT else 2
        sink.append({"side": None, "label": f"{shot_context.release_mode}:{shot_context.shot_family}", "prob": prob, "pts": pts,
                     "made": result.outcome == "MADE", "blocked": str(result.outcome).startswith("BLOCK"),
                     "offense": offense})
        return result

    def interior(engine, shooter_id, interior_ctx, *args, **kwargs):
        prob = po.unblocked_make_probability(interior_ctx)
        offense = engine.state.offense_team_id
        result = orig_interior(engine, shooter_id, interior_ctx, *args, **kwargs)
        sink.append({"side": None, "label": str(interior_ctx.shot_family), "prob": prob, "pts": 2,
                     "made": result.outcome == "MADE", "blocked": str(result.outcome).startswith("BLOCK"),
                     "offense": offense})
        return result

    po.apply_perimeter_shot_to_engine, po.apply_interior_shot_to_engine = perimeter, interior
    try:
        yield
    finally:
        po.apply_perimeter_shot_to_engine, po.apply_interior_shot_to_engine = orig_perimeter, orig_interior


def new_side_totals() -> dict:
    return {k: 0.0 for k in ARRAY_KEYS}


def simulate_once(home_id, away_id, home_five, away_five, profiles, seed: int):
    """One captured game -> (per-side totals, {(side, label): [attempts, makes, exp_pts, real_pts, blocked]}, result)."""
    sink: List[dict] = []
    with capture_shots(sink):
        result = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
    totals = {"HOME": new_side_totals(), "AWAY": new_side_totals()}
    families: Dict[Tuple[str, str], List[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    for rec in result.possessions:
        side = "HOME" if rec.offense_team_id == home_id else "AWAY"
        d = rec.provisional_deltas
        t = totals[side]
        t["points"] += d.points; t["poss"] += 1; t["fga"] += d.fga; t["fg3a"] += d.fg3a; t["fgm"] += d.fgm
        t["fg3m"] += d.fg3m; t["fta"] += d.fta; t["ftm"] += d.ftm; t["oreb"] += d.oreb
        t["tov"] += d.turnovers + d.team_turnovers
        for e in rec.terminal_result.world.trace:
            if e.get("action") == "SHOOTING_FOUL":
                t["fouled_shots"] += 1
    for cap in sink:
        side = "HOME" if cap["offense"] == home_id else "AWAY"
        f = families[(side, cap["label"])]
        f[0] += 1; f[1] += 1 if cap["made"] else 0; f[2] += cap["prob"] * cap["pts"]
        f[3] += cap["pts"] if cap["made"] else 0.0; f[4] += 1 if cap["blocked"] else 0
        totals[side]["exp_fg_pts"] += cap["prob"] * cap["pts"]
        totals[side]["real_fg_pts"] += cap["pts"] if cap["made"] else 0.0
        totals[side]["blocked"] += 1 if cap["blocked"] else 0
        totals[side]["exp_fg_pts_unblocked"] += 0.0 if cap["blocked"] else cap["prob"] * cap["pts"]
        totals[side]["open_fga"] += 1
    return totals, families, result


def sim_seed(game_id: str, i: int, tag: str = "ot-sim") -> int:
    return int(hashlib.sha256(f"{tag}|{game_id}|{i}".encode()).hexdigest()[:16], 16)


def simulate_game_record(record: dict, n_sims: int = N_SIMS, tag: str = "ot-sim") -> dict:
    home_five, away_five, profiles = build_game(record)
    arrays = {side: {k: [] for k in ARRAY_KEYS} for side in ("HOME", "AWAY")}
    fam_totals: Dict[str, List[float]] = defaultdict(lambda: [0.0] * 5)
    n_valid = 0
    for i in range(n_sims):
        try:
            totals, families, _ = simulate_once("HOME", "AWAY", home_five, away_five, profiles, sim_seed(record["game_id"], i, tag))
        except DetailedGameSimulationFault:
            continue
        n_valid += 1
        for side in totals:
            for k in ARRAY_KEYS:
                arrays[side][k].append(round(totals[side][k], 3))
        for (side, label), v in families.items():
            fam_totals[f"{side}|{label}"] = [a + b for a, b in zip(fam_totals[f"{side}|{label}"], v)]
    return {"game_id": record["game_id"], "season": record["season"], "n_valid": n_valid,
            "arrays": arrays, "families": dict(fam_totals)}


def _worker(args):
    record, n_sims = args
    return simulate_game_record(record, n_sims)


def run_all(records: List[dict], n_sims: int = N_SIMS, workers: int = 8, path: str = CACHE_PATH) -> None:
    out = []
    with mp.Pool(workers) as pool:
        for i, res in enumerate(pool.imap(_worker, [(r, n_sims) for r in records], chunksize=2)):
            out.append(res)
            if i % 25 == 0:
                print("done", i, len(records), flush=True)
    with gzip.open(path, "wt") as f:
        json.dump({"n_sims": n_sims, "config": "reconstructed primary-five profiles, synthetic ids, seeds sha256(ot-sim|game|i)", "games": out}, f)
    print("wrote", path, len(out), flush=True)


def load_cache(path: str = CACHE_PATH) -> Dict[str, dict]:
    with gzip.open(path, "rt") as f:
        return {g["game_id"]: g for g in json.load(f)["games"]}
