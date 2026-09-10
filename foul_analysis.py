"""
Phase 6 -- Foul Drawing + Foul Discipline: the empirical investigation.
Entirely offline, read-only with respect to the rest of the codebase
(imports foul_ingestion / shot_zone_ingestion / handling_exposure / loader
caches only). Not imported by game_engine.py/season.py/awards.py/models.py/
transactions.py/player_ability_estimation.py's production extractors.

============================ REAL JOIN KEY ============================
foul_ingestion's committer/drawer keys and shot_zone_ingestion's/
handling_exposure's player keys are all the same real NBA person-id
space (same fact already confirmed in Phase 4B) -- turnover_ingestion's
`resolve_full_name` (a real, era-spanning static id->name crosswalk,
verified in Phase 4B) is reused here rather than duplicated, since
foul_ingestion's own `playerName`-equivalent data was never stored (only
raw ids) -- the row itself never carries a text name at all, sidestepping
the last-name-only quirk entirely.

============================ CANDIDATE DENOMINATORS ============================
Real, verified fields only -- no invented formula except the explicitly
task-specified ESA (tested, not assumed):
  A. FGA (real box FGA * real gp)
  B. FGA + shooting fouls drawn (a rough "scoring-attempt" count including
     trips that ended in a foul instead of a shot attempt)
  C. "ESA" = FGA + shooting_foul_drawn - and_ones (the task's own candidate
     -- an and-1 is BOTH a counted FGA (the make) and a shooting foul
     drawn, so naively summing A+shooting-fouls double-counts every real
     and-1; ESA is the arithmetic correction for that -- tested, not
     assumed correct)
  D. Drives (real leaguedashptstats field, 2013-14+ only, from
     handling_exposure.py's already-cached data)
  E. Rim + Paint attempts (real restricted_area_fga + paint_non_ra_fga,
     from shot_zone_ingestion.py's already-cached Phase 5 data -- zero
     new API calls)
  F. Touches (real leaguedashptstats field, 2013-14+, already cached)
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import foul_ingestion as fli
import handling_exposure as he
import shot_zone_ingestion as szi
from loader import load_teams, load_player_advanced_stats
from turnover_ingestion import resolve_full_name

CANDIDATE_DRAW_DENOMINATORS = ("fga", "fga_plus_sfd", "esa", "drives", "rim_paint_fga", "touches")
# "drives_faced_proxy" (a candidate the task named) was investigated and
# DROPPED before implementation: leaguedashptstats' real DRIVES field is
# the player's OWN offensive drives, not drives he defended against --
# there is no real per-defender "drives faced" field anywhere in this
# codebase's cache, and inventing one would violate this phase's own
# "do not build the methodology around a field you have not verified"
# rule. Reported as a real, investigated-and-rejected candidate, not
# silently omitted.
CANDIDATE_DISC_DENOMINATORS = ("minutes", "def_possessions_proxy")


@dataclass
class PlayerFoulRow:
    player_id: str
    player_name: str
    season: str
    # Attacker side (real, this session's ingested sample -- see
    # foul_ingestion's own coverage caveats).
    shooting_foul_drawn: float
    nonshooting_def_foul_drawn: float
    and_ones: float
    total_drawn: float
    # Defender side.
    shooting_foul_committed: float
    nonshooting_def_foul_committed: float
    offensive_foul_committed: float
    total_committed: float
    # Real box/tracking/shot-zone denominator ingredients.
    fga: Optional[float]
    fta: Optional[float]
    minutes: Optional[float]
    rim_paint_fga: Optional[float]
    touches: Optional[float]
    drives: Optional[float]
    usg_pct: Optional[float]
    reb_pct: Optional[float]
    ast_pct: Optional[float]
    gp: Optional[int]


def _scale_to_full_season(count: float, games_done: int, games_total: int) -> float:
    """Real, PARTIAL-season foul-event counts (from this session's own
    PBP ingestion coverage) are joined against FULL-season denominators
    (real box FGA/minutes/FTA, and real full-season touches/drives/shot-
    zone data from Phase 4B/5's own complete season-long API calls) --
    without this scaling, every rate would be systematically deflated by
    the ingestion coverage fraction, and that fraction DIFFERS by season
    (e.g. 300/1230 vs 200/1189), which would silently contaminate every
    cross-season T->T+1 comparison with a spurious season effect. Same
    explicit, stated-assumption scaling as
    ball_security_analysis._scale_to_full_season (Phase 4B) -- assumes a
    roughly stable per-game rate across the games sampled vs. not yet
    ingested, not verified game-by-game."""
    if not games_total or games_done <= 0 or games_done >= games_total:
        return float(count)
    return count * (games_total / games_done)


def build_player_foul_rows(season: str) -> List[PlayerFoulRow]:
    """Every player with real foul-event evidence (drawn AND/OR committed)
    for `season`, joined against real box/shot-zone/tracking data by the
    shared real player_id. A player present in the foul cache but with no
    box-stat match is skipped (rare; matches this codebase's existing
    name-matching risk, not introduced here). Foul-event counts are
    scaled to a full-season-equivalent (see _scale_to_full_season) so
    they stay comparable to the full-season denominators they're joined
    against."""
    foul_state = fli.load_foul_cache(season)
    if foul_state is None:
        return []
    games_done = len(foul_state["games_done"])
    games_total = foul_state["games_total"]

    adv = load_player_advanced_stats(season)
    teams = load_teams(season)
    box_by_name = {}
    for team in teams.values():
        for p in team.players:
            box_by_name[p.name] = p
    shot_zones = szi.load_shot_zones(season)
    exposure = he.load_handling_exposure(season)

    all_ids = set(foul_state["committers"]) | set(foul_state["drawers"])
    rows = []
    for pid in all_ids:
        name = resolve_full_name(int(pid))
        if name is None:
            continue
        crow = foul_state["committers"].get(pid, fli._empty_committer_row(int(pid)))
        drow = foul_state["drawers"].get(pid, fli._empty_drawer_row(int(pid)))
        adv_row = adv.get(name, {})
        box_player = box_by_name.get(name)
        gp = adv_row.get("gp")
        minutes = (adv_row.get("mpg", 0) * gp) if gp else None
        fga = (box_player.fga * gp) if (box_player and gp) else None
        fta = (box_player.fta * gp) if (box_player and gp) else None
        sz_row = shot_zones.get(pid)
        rim_paint_fga = None
        if sz_row is not None:
            rim_paint_fga = sz_row.get("restricted_area_fga", 0.0) + sz_row.get("paint_non_ra_fga", 0.0)
        exp_row = exposure.get(pid)
        touches = exp_row.get("touches") if exp_row else None
        drives = exp_row.get("drives") if exp_row else None

        def scale(x):
            return _scale_to_full_season(x, games_done, games_total)

        rows.append(PlayerFoulRow(
            player_id=pid, player_name=name, season=season,
            shooting_foul_drawn=scale(drow["shooting_foul_drawn"]),
            nonshooting_def_foul_drawn=scale(drow["nonshooting_def_foul_drawn"]),
            and_ones=scale(drow["and_ones"]), total_drawn=scale(drow["total_drawn"]),
            shooting_foul_committed=scale(crow["shooting_foul"]),
            nonshooting_def_foul_committed=scale(crow["nonshooting_def_foul"]),
            offensive_foul_committed=scale(crow["offensive_foul"]),
            total_committed=scale(crow["shooting_foul"] + crow["nonshooting_def_foul"]),
            fga=fga, fta=fta, minutes=minutes, rim_paint_fga=rim_paint_fga,
            touches=touches, drives=drives,
            usg_pct=adv_row.get("usg_pct"), reb_pct=adv_row.get("reb_pct"),
            ast_pct=adv_row.get("ast_pct"), gp=gp,
        ))
    return rows


# =====================================================================
# FOUL DRAWING -- rate + denominator comparison
# =====================================================================

def draw_denominator_value(row: PlayerFoulRow, denominator: str) -> Optional[float]:
    if denominator == "fga":
        return row.fga
    if denominator == "fga_plus_sfd":
        return (row.fga + row.shooting_foul_drawn) if row.fga is not None else None
    if denominator == "esa":
        return (row.fga + row.shooting_foul_drawn - row.and_ones) if row.fga is not None else None
    if denominator == "drives":
        return row.drives
    if denominator == "rim_paint_fga":
        return row.rim_paint_fga
    if denominator == "touches":
        return row.touches
    raise ValueError(f"Unknown draw denominator {denominator!r}")


def foul_drawing_rate(row: PlayerFoulRow, denominator: str, min_exposure: float = 30.0) -> Optional[float]:
    """Shooting-fouls-drawn per unit of `denominator` -- the primary
    FOUL_DRAWING numerator (nonshooting/bonus fouls drawn are a real but
    much rarer, noisier signal -- kept as auxiliary evidence, see
    module docstring / report, not the headline rate)."""
    exp = draw_denominator_value(row, denominator)
    if exp is None or exp < min_exposure:
        return None
    return row.shooting_foul_drawn / exp


def compare_draw_denominators(rows_by_season: Dict[str, List[PlayerFoulRow]], min_exposure: float = 30.0) -> dict:
    seasons = sorted(rows_by_season.keys())
    results = {}
    for denom in CANDIDATE_DRAW_DENOMINATORS:
        pairs = []
        for i in range(len(seasons) - 1):
            s_now, s_next = seasons[i], seasons[i + 1]
            now_by_id = {r.player_id: r for r in rows_by_season[s_now]}
            next_by_id = {r.player_id: r for r in rows_by_season[s_next]}
            for pid, r_now in now_by_id.items():
                r_next = next_by_id.get(pid)
                if r_next is None:
                    continue
                rate_now = foul_drawing_rate(r_now, denom, min_exposure)
                rate_next = foul_drawing_rate(r_next, denom, min_exposure)
                if rate_now is None or rate_next is None:
                    continue
                weight = draw_denominator_value(r_next, denom) or 0.0
                pairs.append((rate_now, rate_next, weight))
        if not pairs:
            results[denom] = {"n_pairs": 0, "weighted_mae": None, "weighted_rmse": None}
            continue
        total_w = sum(w for _, _, w in pairs)
        mae = sum(abs(a - b) * w for a, b, w in pairs) / total_w
        rmse = math.sqrt(sum((a - b) ** 2 * w for a, b, w in pairs) / total_w)
        results[denom] = {"n_pairs": len(pairs), "weighted_mae": round(mae, 6), "weighted_rmse": round(rmse, 6)}
    return {"season_pairs_used": list(zip(seasons, seasons[1:])), "denominators": results}


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


def draw_role_bias_report(rows: List[PlayerFoulRow], denominator: str, min_exposure: float = 30.0) -> dict:
    xs_usg, ys = [], []
    xs_reb, ys2 = [], []
    for r in rows:
        rate = foul_drawing_rate(r, denominator, min_exposure)
        if rate is None:
            continue
        if r.usg_pct is not None:
            xs_usg.append(r.usg_pct)
            ys.append(rate)
        if r.reb_pct is not None:
            xs_reb.append(r.reb_pct)
            ys2.append(rate)
    return {
        "denominator": denominator,
        "corr_rate_vs_usage": _pearson(xs_usg, ys), "n_usage": len(xs_usg),
        "corr_rate_vs_reb_pct": _pearson(xs_reb, ys2), "n_reb": len(xs_reb),
    }


# =====================================================================
# FOUL DISCIPLINE -- rate + denominator comparison
# =====================================================================

def disc_denominator_value(row: PlayerFoulRow, denominator: str, league_mean_possessions: Optional[float] = None) -> Optional[float]:
    if denominator == "minutes":
        return row.minutes
    if denominator == "def_possessions_proxy":
        # Real, simple derivation: real league-wide mean possessions/game
        # (data_source.fetch_league_pace_variation -> league_pace.json,
        # already cached) times this player's real minutes share of a
        # real 48-minute game -- approximates "possessions defended" as
        # "team plays league-average pace while he's on the floor." A
        # real, grounded estimate, not a per-team-specific precise figure
        # (no per-team pace-while-this-player-on-court field exists).
        if row.minutes is None or league_mean_possessions is None:
            return None
        return (row.minutes / 48.0) * league_mean_possessions
    raise ValueError(f"Unknown/unimplemented discipline denominator {denominator!r} this phase")


def foul_discipline_rate(row: PlayerFoulRow, denominator: str, min_exposure: float = 100.0,
                          league_mean_possessions: Optional[float] = None) -> Optional[float]:
    """Defensive fouls COMMITTED per unit of `denominator` -- lower is
    worse (more foul-prone); inverted to "higher = better" only at the
    final rating stage (see foul_estimation.py), matching every other
    attribute's convention."""
    exp = disc_denominator_value(row, denominator, league_mean_possessions)
    if exp is None or exp < min_exposure:
        return None
    return row.total_committed / exp


def _load_league_mean_possessions(season: str) -> Optional[float]:
    import json
    from pathlib import Path
    path = Path("cache") / season / "league_pace.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f).get("mean_possessions")


def compare_disc_denominators(rows_by_season: Dict[str, List[PlayerFoulRow]], min_exposure: float = 100.0) -> dict:
    seasons = sorted(rows_by_season.keys())
    results = {}
    for denom in CANDIDATE_DISC_DENOMINATORS:
        pairs = []
        for i in range(len(seasons) - 1):
            s_now, s_next = seasons[i], seasons[i + 1]
            lmp_now = _load_league_mean_possessions(s_now)
            lmp_next = _load_league_mean_possessions(s_next)
            now_by_id = {r.player_id: r for r in rows_by_season[s_now]}
            next_by_id = {r.player_id: r for r in rows_by_season[s_next]}
            for pid, r_now in now_by_id.items():
                r_next = next_by_id.get(pid)
                if r_next is None:
                    continue
                rate_now = foul_discipline_rate(r_now, denom, min_exposure, lmp_now)
                rate_next = foul_discipline_rate(r_next, denom, min_exposure, lmp_next)
                if rate_now is None or rate_next is None:
                    continue
                weight = disc_denominator_value(r_next, denom, lmp_next) or 0.0
                pairs.append((rate_now, rate_next, weight))
        if not pairs:
            results[denom] = {"n_pairs": 0, "weighted_mae": None, "weighted_rmse": None}
            continue
        total_w = sum(w for _, _, w in pairs)
        mae = sum(abs(a - b) * w for a, b, w in pairs) / total_w
        rmse = math.sqrt(sum((a - b) ** 2 * w for a, b, w in pairs) / total_w)
        results[denom] = {"n_pairs": len(pairs), "weighted_mae": round(mae, 6), "weighted_rmse": round(rmse, 6)}
    return {"season_pairs_used": list(zip(seasons, seasons[1:])), "denominators": results}


def disc_role_bias_report(rows: List[PlayerFoulRow], denominator: str, min_exposure: float = 100.0,
                           league_mean_possessions: Optional[float] = None) -> dict:
    xs_usg, ys = [], []
    xs_reb, ys2 = [], []
    for r in rows:
        rate = foul_discipline_rate(r, denominator, min_exposure, league_mean_possessions)
        if rate is None:
            continue
        if r.usg_pct is not None:
            xs_usg.append(r.usg_pct)
            ys.append(rate)
        if r.reb_pct is not None:
            xs_reb.append(r.reb_pct)
            ys2.append(rate)
    return {
        "denominator": denominator,
        "corr_rate_vs_usage": _pearson(xs_usg, ys), "n_usage": len(xs_usg),
        "corr_rate_vs_reb_pct (rough big-vs-perimeter proxy)": _pearson(xs_reb, ys2), "n_reb": len(xs_reb),
    }
