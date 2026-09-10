"""
Phase 9 -- Shot Creation Internals: the empirical investigation. Entirely
offline, read-only. Reuses Phase 4B's `handling_exposure.py` (touches,
drives already cached there) and existing `player_advanced.json`
(usg_pct, reb_pct) -- only the NEW fields from `shot_creation_ingestion.py`
are genuinely new.

============================ TWO CANDIDATES (small set, per task) ============================
A. RIM ACCESS CREATION -- "given a drive was initiated, how often did it
   reach a real scoring conclusion (own shot/foul) or create one for a
   teammate (assist), net of turnovers?" Real, unweighted composite,
   exactly the task's own suggested formula (no invented weights):

       rim_access_rate = (DRIVE_FGA + DRIVE_FTA + DRIVE_AST - DRIVE_TOV) / DRIVES

   Conditioned on real DRIVES (the initiation count) -- NOT raw drive
   volume, which would just be tendency/aggression.

B. PERIMETER SPACE CREATION -- real PULL_UP_FGA is BY DEFINITION a
   self-created (unassisted, off-the-dribble) jumper -- no separate
   "unassisted" field needed. Raw volume is tendency, not ability, so
   it's conditioned on real handling exposure (touches):

       perimeter_rate = PULL_UP_FGA / touches

   `CATCH_SHOOT_FGA` is used ONLY as a real contrast/validation signal
   (§7 in the report), never as part of the candidate itself.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import shot_creation_ingestion as sci
import handling_exposure as he
from loader import load_player_advanced_stats


@dataclass
class PlayerCreationRow:
    player_id: str
    player_name: str
    season: str
    drives: float
    drive_fga: float
    drive_fta: float
    drive_ast: float
    drive_tov: float
    pull_up_fga: Optional[float]
    catch_shoot_fga: Optional[float]
    touches: Optional[float]
    time_of_poss: Optional[float]
    usg_pct: Optional[float]
    reb_pct: Optional[float]
    ast_pct: Optional[float]
    gp: Optional[int]


def build_player_creation_rows(season: str) -> List[PlayerCreationRow]:
    creation_data = sci.load_shot_creation_tracking(season)
    if not creation_data:
        return []
    exposure = he.load_handling_exposure(season)
    adv = load_player_advanced_stats(season)

    rows = []
    for pid, crow in creation_data.items():
        name = crow["player_name"]
        adv_row = adv.get(name, {})
        exp_row = exposure.get(pid)
        rows.append(PlayerCreationRow(
            player_id=pid, player_name=name, season=season,
            drives=crow["drives"], drive_fga=crow["drive_fga"], drive_fta=crow["drive_fta"],
            drive_ast=crow["drive_ast"], drive_tov=crow["drive_tov"],
            pull_up_fga=crow.get("pull_up_fga"), catch_shoot_fga=crow.get("catch_shoot_fga"),
            touches=exp_row.get("touches") if exp_row else None,
            time_of_poss=exp_row.get("time_of_poss") if exp_row else None,
            usg_pct=adv_row.get("usg_pct"), reb_pct=adv_row.get("reb_pct"), ast_pct=adv_row.get("ast_pct"),
            gp=adv_row.get("gp"),
        ))
    return rows


def rim_access_rate(row: PlayerCreationRow, min_drives: float = 50.0) -> Optional[float]:
    if row.drives < min_drives:
        return None
    return (row.drive_fga + row.drive_fta + row.drive_ast - row.drive_tov) / row.drives


def perimeter_space_rate(row: PlayerCreationRow, min_touches: float = 200.0) -> Optional[float]:
    if row.pull_up_fga is None or row.touches is None or row.touches < min_touches:
        return None
    return row.pull_up_fga / row.touches


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


def combined_diagnostic_pass(rows_by_season: Dict[str, List[PlayerCreationRow]], check_season: str) -> dict:
    """ONE combined pass: stability (T->T+1), role bias, scoring-volume
    overlap, contamination, component correlation -- per the task's own
    "run one combined diagnostic pass" efficiency instruction."""
    seasons = sorted(rows_by_season.keys())
    rows = rows_by_season[check_season]

    # --- stability (T -> T+1), both candidates ---
    idx = seasons.index(check_season)
    stability = {}
    if idx + 1 < len(seasons):
        nxt = rows_by_season[seasons[idx + 1]]
        next_by_id = {r.player_id: r for r in nxt}
        for label, fn in (("rim_access", rim_access_rate), ("perimeter_space", perimeter_space_rate)):
            now_r, next_r = [], []
            for r in rows:
                rn = fn(r)
                r_next = next_by_id.get(r.player_id)
                if rn is None or r_next is None:
                    continue
                rx = fn(r_next)
                if rx is None:
                    continue
                now_r.append(rn)
                next_r.append(rx)
            stability[label] = {"n": len(now_r), "rank_corr": _spearman(now_r, next_r)}

    # --- role bias + scoring-volume + contamination, both candidates ---
    contamination = {}
    for label, fn in (("rim_access", rim_access_rate), ("perimeter_space", perimeter_space_rate)):
        xs = {"usg_pct": [], "touches": [], "time_of_poss": [], "reb_pct": [], "drives": [], "drive_fga_vol": []}
        ys = {k: [] for k in xs}
        for r in rows:
            rate = fn(r)
            if rate is None:
                continue
            for key, val in (("usg_pct", r.usg_pct), ("touches", r.touches), ("time_of_poss", r.time_of_poss),
                              ("reb_pct", r.reb_pct), ("drives", r.drives), ("drive_fga_vol", r.drive_fga)):
                if val is not None:
                    xs[key].append(val)
                    ys[key].append(rate)
        contamination[label] = {f"corr_vs_{k}": _pearson(xs[k], ys[k]) for k in xs} | {"n": len(xs["usg_pct"])}

    # --- component correlation (rim access vs perimeter space, same players) ---
    ra_vals, ps_vals = [], []
    for r in rows:
        ra = rim_access_rate(r)
        ps = perimeter_space_rate(r)
        if ra is not None and ps is not None:
            ra_vals.append(ra)
            ps_vals.append(ps)
    component_corr = {"n": len(ra_vals), "corr": _pearson(ra_vals, ps_vals), "rank_corr": _spearman(ra_vals, ps_vals)}

    return {"season": check_season, "stability": stability, "contamination": contamination, "component_correlation": component_corr}
