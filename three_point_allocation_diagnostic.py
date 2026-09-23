"""THREE-POINT SHOT ALLOCATION DIAGNOSTIC V1 helpers.

Read-only measurement over the frozen engine. The only instrumentation is wrapping
`possession_orchestrator._nearest_teammate_id` to count which branch returned; the wrapper
returns the original value unchanged. Uses development games only (never the frozen holdout).
"""
import contextlib
import hashlib
import itertools
import statistics as st
from typing import Dict, List, Sequence, Tuple

import numpy as np

import margin_compression_diagnostic as mcd
import historical_game_snapshot as hgs
import possession_orchestrator as po
import three_point_resolution_diagnostic as tpd
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault
from possession_orchestrator import PlayerSimulationProfile

SEASON = mcd.SEASON
ALL_SEASONS = mcd.ALL_SEASONS
_mean = mcd._mean
_pearson = mcd._pearson


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------
def shares(counts: Sequence[float]) -> List[float]:
    total = sum(counts)
    return [c / total for c in counts] if total else [0.0] * len(counts)


def herfindahl(share_list: Sequence[float]) -> float:
    return sum(s * s for s in share_list)


def top_k_share(share_list: Sequence[float], k: int) -> float:
    return sum(sorted(share_list, reverse=True)[:k])


def _ranks(xs: Sequence[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    a, b = zip(*pairs)
    return _pearson(_ranks(a), _ranks(b))


# ---------------------------------------------------------------------
# Instrumentation of the teammate-selection branch
# ---------------------------------------------------------------------
@contextlib.contextmanager
def count_nearest_teammate_branches(counter: dict):
    original = po._nearest_teammate_id

    def wrapper(engine, world, carrier_id):
        result = original(engine, world, carrier_id)
        if result is None:
            counter["none"] = counter.get("none", 0) + 1
        else:
            teammates = world.teammates_of(engine, carrier_id)
            carrier_side = po.ball_side(world.player_zones.get(carrier_id, engine.state.ball_zone))
            matched = any(po.ball_side(world.player_zones.get(p, engine.state.ball_zone)) == carrier_side
                          for p in teammates)
            key = "same_side_first_match" if matched else "fallback_first_teammate"
            counter[key] = counter.get(key, 0) + 1
            counter["by_lineup_index"] = counter.get("by_lineup_index", {})
            idx = teammates.index(result)
            counter["by_lineup_index"][idx] = counter["by_lineup_index"].get(idx, 0) + 1
        return result

    po._nearest_teammate_id = wrapper
    try:
        yield
    finally:
        po._nearest_teammate_id = original


# ---------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------
def _seed(tag: str, i: int) -> int:
    return int(hashlib.sha256(f"3pa-alloc|{tag}|{i}".encode()).hexdigest()[:16], 16)


def simulate_side(home_five: Tuple[str, ...], profiles: Dict[str, PlayerSimulationProfile], tag: str, n_sims: int,
                  away_five: Tuple[str, ...] = None) -> dict:
    """Simulate `home_five` (HOME side) vs `away_five` (default: five synthetic averages) and return
    per-player 3PA/3PM counts (all THREE_POINT attempts), release-mode split, and per-player
    possession-start/pass-receipt/shot counts from the event stream (HOME offense only)."""
    if away_five is None:
        away_five = tuple(str(950001 + i) for i in range(5))
        profiles = dict(profiles)
        for p in away_five:
            profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
    sink: List[dict] = []
    starts: Dict[str, int] = {p: 0 for p in home_five}
    received: Dict[str, int] = {p: 0 for p in home_five}
    fga: Dict[str, int] = {p: 0 for p in home_five}
    margins = []
    with tpd.capture_three_point_attempts(sink):
        for i in range(n_sims):
            try:
                r = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles, rng_seed=_seed(tag, i))
            except DetailedGameSimulationFault:
                continue
            margins.append(r.final_home_score - r.final_away_score)
            for rec in r.possessions:
                if rec.offense_team_id != "HOME":
                    continue
                for e in rec.events:
                    t = e.event_type.value
                    if t == "POSSESSION_START" and e.primary_player_id in starts:
                        starts[e.primary_player_id] += 1
                    elif t == "PASS_RESOLVED" and e.secondary_player_id in received:
                        received[e.secondary_player_id] += 1
                    elif t == "SHOT_RELEASED" and e.primary_player_id in fga:
                        fga[e.primary_player_id] += 1
    mine = [a for a in sink if a["shooter"] in home_five]
    per_player = {}
    for p in home_five:
        pa = [a for a in mine if a["shooter"] == p]
        per_player[p] = {"3pa": len(pa), "3pm": sum(a["made"] for a in pa),
                         "catch_and_shoot_3pa": sum(1 for a in pa if a["release"] == "CATCH_AND_SHOOT"),
                         "pull_up_3pa": sum(1 for a in pa if a["release"] == "PULL_UP"),
                         "starts": starts[p], "passes_received": received[p], "fga": fga[p]}
    return {"per_player": per_player, "n_sims": len(margins), "team_3pa_per_sim": len(mine) / max(1, len(margins)),
            "team_3pt_pct": (sum(a["made"] for a in mine) / len(mine)) if mine else None,
            "mean_margin": _mean(margins)}


def slot_shares(result: dict, order: Sequence[str], key: str = "3pa") -> List[float]:
    return shares([result["per_player"][p][key] for p in order])


def identical_players(role_kwargs_by_slot=None, tag_offset: int = 0):
    ids = tuple(str(960001 + tag_offset + i) for i in range(5))
    profiles = {}
    for i, p in enumerate(ids):
        extra = role_kwargs_by_slot[i] if role_kwargs_by_slot else {}
        profiles[p] = PlayerSimulationProfile.synthetic(p, "HOME", **extra)
    return ids, profiles


def permutation_test(role_kwargs_by_player: List[dict], n_sims: int, tag: str,
                     orders: Sequence[Sequence[int]] = None) -> dict:
    """The same five player objects placed in different lineup orders. `role_kwargs_by_player[j]` is
    player j's field overrides. Returns per-order slot shares and per-player shares so the caller can
    see whether volume follows the SLOT or the PLAYER."""
    ids, profiles = identical_players(role_kwargs_by_player)
    if orders is None:
        orders = [tuple((i + s) % 5 for i in range(5)) for s in range(5)]
    rows = []
    for order in orders:
        lineup = tuple(ids[j] for j in order)
        res = simulate_side(lineup, profiles, f"{tag}|{order}", n_sims)
        by_slot = slot_shares(res, lineup)
        rows.append({"order_of_player_indices": list(order), "share_by_slot": by_slot,
                     "share_by_player": [by_slot[order.index(j)] for j in range(5)]})
    slot_mean = [_mean([r["share_by_slot"][s] for r in rows]) for s in range(5)]
    player_mean = [_mean([r["share_by_player"][j] for r in rows]) for j in range(5)]
    slot_sd = st.pstdev(slot_mean)
    player_sd = st.pstdev(player_mean)
    return {"rows": rows, "mean_share_by_slot": slot_mean, "mean_share_by_player": player_mean,
            "sd_of_slot_means": slot_sd, "sd_of_player_means": player_sd}


ELITE = {"three_point_shrunk_rate": 0.42}
WEAK = {"three_point_shrunk_rate": 0.30}


def balanced_stacking(kwargs: dict, n_sims: int = 40, levels=(0, 1, 2, 3, 4, 5)) -> List[dict]:
    """k shooters with `kwargs`; ALL C(5,k) slot placements aggregated, removing slot confounding."""
    rows = []
    for k in levels:
        pooled_3pa = pooled_3pm = 0
        margins = []
        probs = []
        n_sims_total = 0
        for combo in itertools.combinations(range(5), k):
            ids = tuple(str(970001 + i) for i in range(5))
            profiles = {p: PlayerSimulationProfile.synthetic(p, "HOME", **(kwargs if i in combo else {}))
                        for i, p in enumerate(ids)}
            res = simulate_side(ids, profiles, f"bal|{kwargs}|{k}|{combo}", n_sims)
            pooled_3pa += sum(v["3pa"] for v in res["per_player"].values())
            pooled_3pm += sum(v["3pm"] for v in res["per_player"].values())
            margins.append(res["mean_margin"])
            n_sims_total += res["n_sims"]
        rows.append({"n_shooters": k, "team_3pa_per_sim": pooled_3pa / max(1, n_sims_total),
                     "team_3pt_pct": pooled_3pm / pooled_3pa if pooled_3pa else None,
                     "mean_margin": _mean(margins)})
    return rows


# ---------------------------------------------------------------------
# Transparent within-team regression
# ---------------------------------------------------------------------
def within_team_r2(rows: List[dict], predictors: List[str], target: str) -> dict:
    """Center target and predictors within team, standardize predictors, OLS. Returns R^2 and betas.
    Diagnostic only -- not a fitted production model."""
    by_team: Dict[str, List[dict]] = {}
    for r in rows:
        if r.get(target) is None or any(r.get(p) is None for p in predictors):
            continue
        by_team.setdefault(r["team"], []).append(r)
    X_rows, y_rows = [], []
    for team_rows in by_team.values():
        if len(team_rows) < 3:
            continue
        y_mean = _mean([r[target] for r in team_rows])
        means = {p: _mean([r[p] for r in team_rows]) for p in predictors}
        for r in team_rows:
            y_rows.append(r[target] - y_mean)
            X_rows.append([r[p] - means[p] for p in predictors])
    if not X_rows:
        return {"n": 0, "r2": None}
    X = np.array(X_rows)
    y = np.array(y_rows)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    Xs = X / sd
    beta, *_ = np.linalg.lstsq(Xs, y, rcond=None)
    pred = Xs @ beta
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float((y ** 2).sum())
    return {"n": len(y), "r2": 1 - ss_res / ss_tot if ss_tot else None,
            "standardized_betas": {p: float(b) for p, b in zip(predictors, beta)}}
