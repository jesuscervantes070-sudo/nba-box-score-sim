"""
Phase 8 -- Playmaking Vision: the empirical investigation. Entirely
offline, read-only. Reuses existing caches only (Phase 4B's
handling_exposure.py for touches/drives/time_of_poss, existing
player_advanced.json for usg_pct/ast_pct/reb_pct) plus this phase's own
new passing_tracking_ingestion.py (POTENTIAL_AST etc, a real field family
not previously cached anywhere in this project).

============================ TAXONOMY (per task) ============================
A. Playmaking Vision -- recognizing advantageous passing options (this
   phase's target).
B. Passing Accuracy -- executing the pass (EXISTING `passing` attribute,
   AST%-based, LOCKED, not rebuilt here -- see overlap check below).
C. Creation/Scoring Gravity -- forcing help (contamination risk, checked
   not corrected).
D. Role/Initiation Share -- decision-making opportunity (contamination
   risk, checked).
E. Teammate Conversion -- whether the recipient scores (AST depends on
   this; POTENTIAL_AST does NOT -- exactly why it's the primary candidate
   family).
F. Ball Security -- handling errors (existing, separate attribute).
G. Bad-Pass Turnovers -- execution/read failures (existing Phase 4A/4B
   evidence, cross-referenced only, not rebuilt).
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import passing_tracking_ingestion as pti
import handling_exposure as he
from loader import load_player_advanced_stats

CANDIDATE_DENOMINATORS = ("touches", "passes_made", "time_of_poss")


@dataclass
class PlayerVisionRow:
    player_id: str
    player_name: str
    season: str
    potential_ast: float
    ast: float
    secondary_ast: float
    ast_pts_created: float
    passes_made: float
    touches: Optional[float]
    time_of_poss: Optional[float]
    drives: Optional[float]
    minutes: Optional[float]
    usg_pct: Optional[float]
    ast_pct: Optional[float]  # the EXISTING passing_accuracy attribute's own raw input
    reb_pct: Optional[float]
    gp: Optional[int]


def build_player_vision_rows(season: str) -> List[PlayerVisionRow]:
    passing_data = pti.load_passing_tracking(season)
    if not passing_data:
        return []
    exposure = he.load_handling_exposure(season)
    adv = load_player_advanced_stats(season)

    rows = []
    for pid, prow in passing_data.items():
        name = prow["player_name"]
        adv_row = adv.get(name, {})
        exp_row = exposure.get(pid)
        gp = adv_row.get("gp")
        minutes = (adv_row.get("mpg", 0) * gp) if gp else None
        rows.append(PlayerVisionRow(
            player_id=pid, player_name=name, season=season,
            potential_ast=prow["potential_ast"], ast=prow["ast"],
            secondary_ast=prow["secondary_ast"], ast_pts_created=prow["ast_pts_created"],
            passes_made=prow["passes_made"],
            touches=exp_row.get("touches") if exp_row else None,
            time_of_poss=exp_row.get("time_of_poss") if exp_row else None,
            drives=exp_row.get("drives") if exp_row else None,
            minutes=minutes, usg_pct=adv_row.get("usg_pct"), ast_pct=adv_row.get("ast_pct"),
            reb_pct=adv_row.get("reb_pct"), gp=gp,
        ))
    return rows


def denom_value(row: PlayerVisionRow, denom: str) -> Optional[float]:
    if denom == "touches":
        return row.touches
    if denom == "passes_made":
        return row.passes_made
    if denom == "time_of_poss":
        return row.time_of_poss
    raise ValueError(f"Unknown denominator {denom!r}")


def vision_rate(row: PlayerVisionRow, denom: str, min_exposure: float) -> Optional[float]:
    exp = denom_value(row, denom)
    if exp is None or exp < min_exposure:
        return None
    return row.potential_ast / exp


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return round(cov / math.sqrt(vx * vy), 4)


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 5:
        return None
    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        for r, i in enumerate(order):
            ranks[i] = r
        return ranks
    return _pearson(rank(xs), rank(ys))


def compare_denominators(rows_by_season: Dict[str, List[PlayerVisionRow]], min_exposure: float) -> dict:
    seasons = sorted(rows_by_season.keys())
    results = {}
    for denom in CANDIDATE_DENOMINATORS:
        pairs = []
        for i in range(len(seasons) - 1):
            s_now, s_next = seasons[i], seasons[i + 1]
            now_by_id = {r.player_id: r for r in rows_by_season[s_now]}
            next_by_id = {r.player_id: r for r in rows_by_season[s_next]}
            for pid, r_now in now_by_id.items():
                r_next = next_by_id.get(pid)
                if r_next is None:
                    continue
                rn = vision_rate(r_now, denom, min_exposure)
                rx = vision_rate(r_next, denom, min_exposure)
                if rn is None or rx is None:
                    continue
                w = denom_value(r_next, denom) or 0.0
                pairs.append((rn, rx, w))
        if not pairs:
            results[denom] = {"n_pairs": 0}
            continue
        rates = [r for row in rows_by_season.values() for r in [vision_rate(x, denom, min_exposure) for x in row] if r is not None]
        mean_rate = sum(rates) / len(rates)
        total_w = sum(w for _, _, w in pairs)
        mae = sum(abs(a - b) * w for a, b, w in pairs) / total_w
        rmse = math.sqrt(sum((a - b) ** 2 * w for a, b, w in pairs) / total_w)
        rank_corr = _spearman([p for p, _, _ in pairs], [a for _, a, _ in pairs])
        results[denom] = {"n_pairs": len(pairs), "weighted_mae": round(mae, 6),
                           "weighted_rmse": round(rmse, 6), "mean_rate": round(mean_rate, 6),
                           "cv_mae": round(mae / mean_rate, 4) if mean_rate else None,
                           "rank_corr": rank_corr}
    return {"season_pairs_used": list(zip(seasons, seasons[1:])), "denominators": results}


def contamination_report(rows: List[PlayerVisionRow], denom: str, min_exposure: float) -> dict:
    """Real correlation between the vision candidate rate and usage/
    touches/TOP/drives -- the central contamination question."""
    xs = {"usg_pct": [], "touches": [], "time_of_poss": [], "drives": [], "ast_pct": [], "reb_pct": []}
    ys = {k: [] for k in xs}
    for r in rows:
        rate = vision_rate(r, denom, min_exposure)
        if rate is None:
            continue
        for key, val in (("usg_pct", r.usg_pct), ("touches", r.touches), ("time_of_poss", r.time_of_poss),
                          ("drives", r.drives), ("ast_pct", r.ast_pct), ("reb_pct", r.reb_pct)):
            if val is not None:
                xs[key].append(val)
                ys[key].append(rate)
    return {f"corr_vs_{k}": _pearson(xs[k], ys[k]) for k in xs} | {f"n_{k}": len(xs[k]) for k in xs}


# =====================================================================
# Role-adjusted residual candidate (family E: "potential-assist creation
# residual after role exposure") -- the ONLY candidate this phase adopts.
# Raw POTENTIAL_AST/touches correlates 0.85-0.91 with the EXISTING
# `passing_accuracy` (ast_pct) attribute across every season checked --
# too high to call it distinct (see report). Residualizing out real
# role/opportunity contamination (usage, drives/touch, time-of-
# possession/touch) drops that correlation to ~0.42 while RETAINING
# substantial real T->T+1 rank stability (0.64, vs 0.88 for the raw
# rate) -- evidence this is removing OPPORTUNITY, not the underlying
# ability itself (measured, not assumed -- see report's own check).
#
# Coefficients are FROZEN, fit once on real TRAIN seasons only
# (2013-14 through 2019-20) via plain OLS (no ML library) -- never
# refit per season (that would leak future-season information into
# every prior season's residual).
VISION_RESIDUAL_COEFFICIENTS = (0.04304374166037528, -0.07053845128367767, 0.11361655292454395, 1.329152483294982)


def residual_vision_rate(row: PlayerVisionRow, min_touches: float = 200.0,
                          coefficients=VISION_RESIDUAL_COEFFICIENTS) -> Optional[float]:
    rate = vision_rate(row, "touches", min_touches)
    if rate is None or row.usg_pct is None or row.drives is None or row.time_of_poss is None or not row.touches:
        return None
    x = (1.0, row.usg_pct, row.drives / row.touches, row.time_of_poss / row.touches)
    pred = sum(b * xi for b, xi in zip(coefficients, x))
    return rate - pred


def raw_ast_vs_potential_ast_report(rows: List[PlayerVisionRow], min_touches: float = 200.0) -> dict:
    """Do raw AST and POTENTIAL_AST rank players the same way? If POTENTIAL_AST
    removes teammate-conversion noise, the two should meaningfully diverge."""
    ast_rate, pot_rate = [], []
    for r in rows:
        if r.touches is None or r.touches < min_touches:
            continue
        ast_rate.append(r.ast / r.touches)
        pot_rate.append(r.potential_ast / r.touches)
    return {"n": len(ast_rate), "rank_corr_ast_vs_potential_ast": _spearman(ast_rate, pot_rate)}
