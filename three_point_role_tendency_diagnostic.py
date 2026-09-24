"""3PA ROLE VS TENDENCY WEIGHTING diagnostic helpers.

Everything here is diagnostic. The counterfactual weightings are applied only inside the
`patched(...)` context manager, which restores the original module attributes on exit, so no
production behaviour changes unless a fix is separately committed.
"""
import contextlib
import dataclasses
import hashlib
import math
import statistics as st
from typing import Dict, List, Optional, Sequence

import action_selection as asel
import possession_orchestrator as po
import three_point_allocation_diagnostic as tad
import three_point_resolution_diagnostic as tpd
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault
from possession_orchestrator import PlayerSimulationProfile

_mean = tad._mean


@contextlib.contextmanager
def patched(finishing_weight: Optional[float] = None, spacing_weight: Optional[float] = None,
            pref_scale: Optional[float] = None, receiver_weights: Optional[dict] = None):
    """receiver_weights = {"tendency": a_t, "spacing": a_s, "finishing": a_f}: replaces the
    nearest-teammate pass target with a weighted draw over ALL teammates,
    w = exp(a_t*three_point_preference + a_s*(spacing-.5) + a_f*(finishing-.5)), using a
    state-keyed deterministic uniform (no RNG consumption)."""
    saved = (asel.ROLE_FINISHING_WEIGHT, asel.ROLE_SPACING_WEIGHT, asel.shot_zone_probabilities, po._nearest_teammate_id)
    original_zone = asel.shot_zone_probabilities
    original_receiver = po._nearest_teammate_id
    if finishing_weight is not None:
        asel.ROLE_FINISHING_WEIGHT = finishing_weight
    if spacing_weight is not None:
        asel.ROLE_SPACING_WEIGHT = spacing_weight
    if pref_scale is not None:
        def scaled(action_type, options, tendency, context, shot_clock_remaining=None):
            if tendency.three_point_preference is not None:
                tendency = dataclasses.replace(tendency, three_point_preference=tendency.three_point_preference * pref_scale)
            return original_zone(action_type, options, tendency, context, shot_clock_remaining)
        asel.shot_zone_probabilities = scaled
    if receiver_weights:
        a_t = receiver_weights.get("tendency", 0.0)
        a_s = receiver_weights.get("spacing", 0.0)
        a_f = receiver_weights.get("finishing", 0.0)

        def weighted_receiver(engine, world, carrier_id):
            teammates = world.teammates_of(engine, carrier_id)
            if not teammates:
                return None
            weights = []
            for pid in teammates:
                prof = world.profiles[pid]
                score = a_t * (prof.three_point_preference or 0.0) + a_s * ((prof.role_off_spacing or 0.5) - 0.5) \
                    + a_f * ((prof.role_off_finishing or 0.5) - 0.5)
                weights.append(math.exp(score))
            key = f"{engine.state.possession_id}|{carrier_id}|{len(engine.log.events)}"
            u = int(hashlib.sha256(key.encode()).hexdigest()[:12], 16) / float(16 ** 12) * sum(weights)
            acc = 0.0
            for pid, w in zip(teammates, weights):
                acc += w
                if u <= acc:
                    return pid
            return teammates[-1]
        po._nearest_teammate_id = weighted_receiver
    try:
        yield
    finally:
        asel.ROLE_FINISHING_WEIGHT, asel.ROLE_SPACING_WEIGHT = saved[0], saved[1]
        asel.shot_zone_probabilities = original_zone
        po._nearest_teammate_id = original_receiver


def sweep(field: str, values: Sequence[float], n_sims: int, tag: str) -> dict:
    """Five otherwise-identical players whose `field` takes `values[j]`; every cyclic lineup order is
    run so the slot cancels. Returns per-player means (by value index) of the decomposition
    touches -> shots -> threes."""
    ids, profiles = tad.identical_players([{field: v} for v in values])
    orders = [tuple((i + s) % 5 for i in range(5)) for s in range(5)]
    acc = {j: {"3pa": 0, "touches": 0, "fga": 0, "cs3": 0, "pu3": 0, "3pm": 0} for j in range(5)}
    team_3pa = []
    for order in orders:
        lineup = tuple(ids[j] for j in order)
        res = tad.simulate_side(lineup, profiles, f"{tag}|{order}", n_sims)
        team_3pa.append(res["team_3pa_per_sim"])
        for j in range(5):
            pp = res["per_player"][ids[j]]
            acc[j]["3pa"] += pp["3pa"]
            acc[j]["touches"] += pp["starts"] + pp["passes_received"]
            acc[j]["fga"] += pp["fga"]
            acc[j]["cs3"] += pp["catch_and_shoot_3pa"]
            acc[j]["pu3"] += pp["pull_up_3pa"]
            acc[j]["3pm"] += pp["3pm"]
    total_3pa = sum(a["3pa"] for a in acc.values())
    total_touches = sum(a["touches"] for a in acc.values())
    total_fga = sum(a["fga"] for a in acc.values())
    rows = []
    for j in range(5):
        a = acc[j]
        rows.append({"value": values[j], "share_3pa": a["3pa"] / total_3pa, "share_touches": a["touches"] / total_touches,
                     "share_fga": a["fga"] / total_fga if total_fga else None,
                     "fga_per_touch": a["fga"] / a["touches"] if a["touches"] else None,
                     "three_share_of_fga": a["3pa"] / a["fga"] if a["fga"] else None,
                     "cs_share_of_3pa": a["cs3"] / a["3pa"] if a["3pa"] else None,
                     "three_pt_pct": a["3pm"] / a["3pa"] if a["3pa"] else None})
    return {"field": field, "rows": rows, "team_3pa_per_sim": _mean(team_3pa)}


def allocation_metrics(player_rows: List[dict], share_key: str) -> dict:
    real = [r["real_share"] for r in player_rows]
    sim = [r[share_key] for r in player_rows]
    by_team: Dict[str, List[dict]] = {}
    for r in player_rows:
        by_team.setdefault(r["team"], []).append(r)
    top1 = _mean([max(r[share_key] for r in rows) for rows in by_team.values()])
    hhi = _mean([sum(r[share_key] ** 2 for r in rows) for rows in by_team.values()])
    rank_corrs = [tad.spearman([r["real_share"] for r in rows], [r[share_key] for r in rows]) for rows in by_team.values()]
    rank_corrs = [c for c in rank_corrs if c is not None]
    return {"n_players": len(player_rows), "n_teams": len(by_team), "pearson": tad._pearson(real, sim),
            "spearman": tad.spearman(real, sim), "mae": _mean([abs(a - b) for a, b in zip(real, sim)]),
            "top1": top1, "hhi": hhi, "mean_within_team_spearman": _mean(rank_corrs)}


def team_split(team: str) -> str:
    """Deterministic development/validation split of teams."""
    return "dev" if int(hashlib.sha256(team.encode()).hexdigest(), 16) % 2 == 0 else "validation"
