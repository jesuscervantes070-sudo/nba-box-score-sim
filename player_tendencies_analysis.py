"""
Phase 11 -- Player Tendencies Foundation. Entirely offline, read-only.
ZERO new API calls this phase -- every field needed for all 6 required
candidates is ALREADY cached by prior phases:
  - `loader.load_teams` (box FGA/FG3A/OREB/REB) -- every cached season, 1996-97+.
  - `shot_zone_ingestion.py` (Phase 5, midrange/restricted-area/paint FGA) -- 1996-97+.
  - `handling_exposure.py` (Phase 4B, touches/drives) -- 2013-14+.
  - `passing_tracking_ingestion.py` (Phase 8, PASSES_MADE) -- 2013-14+.
  - `shot_creation_ingestion.py` (Phase 9, PULL_UP_FGA/CATCH_SHOOT_FGA) -- 2013-14+.

============================ ABILITY vs TENDENCY vs ROLE (per task) ============================
ABILITY = can (existing SKILL_ATTRIBUTES, e.g. `three_point`, `midrange`,
`rim_access_creation`, `playmaking_vision`, `offensive_rebounding` --
ALL already built in prior phases, NOT rebuilt here).
TENDENCY (this phase) = prefers/attempts, given a real, observable action-
frequency rate -- deliberately NOT reusing an ability's own numerator as
the tendency's target (e.g. `drive_aggression` uses real DRIVES volume,
NEVER `rim_access_creation`'s per-drive outcome rate).
ROLE/TEAM CONTEXT = real usage/touches/team-style contamination, measured
(not silently corrected) per candidate.

============================ SIX REQUIRED CANDIDATES ============================
1. three_point_preference = real FG3A / FGA (box stats, 1996-97+)
2. midrange_preference    = real midrange_fga / (restricted_area_fga +
                             paint_non_ra_fga + midrange_fga) -- share of
                             real NON-RIM... actually share of all real
                             2PT-zone-classified shots that are midrange
                             (Phase 5 shot-zone cache, 1996-97+)
3. pullup_vs_catch        = real PULL_UP_FGA / (PULL_UP_FGA + CATCH_SHOOT_FGA)
                             (Phase 9 cache, 2013-14+)
4. drive_aggression       = real DRIVES / touches (Phase 4B cache, 2013-14+)
5. pass_vs_shoot          = real PASSES_MADE / (PASSES_MADE + FGA + FTA)
                             (Phase 8 passing cache + box FGA/FTA, 2013-14+)
6. orb_crash              = real OREB / (OREB + DREB-of-teammates-implied...)
                             -- see module's own `orb_crash_rate` docstring
                             for the real, honest limitation here.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import shot_zone_ingestion as szi
import handling_exposure as he
import passing_tracking_ingestion as pti
import shot_creation_ingestion as sci
from loader import load_teams, load_player_advanced_stats

TENDENCY_FIRST_SEASON_TRACKING = "2013-14"  # pullup_vs_catch, drive_aggression, pass_vs_shoot
TENDENCY_FIRST_SEASON_BOX = "1996-97"       # three_point_preference, midrange_preference, orb_crash (real box-stat floor of this codebase)


@dataclass
class PlayerTendencyRow:
    player_name: str
    season: str
    team_name: Optional[str]
    fga: float
    fg3a: float
    oreb: float
    reb: float
    minutes: Optional[float]
    usg_pct: Optional[float]
    # Phase 5 shot-zone (may be None pre-1996-97 floor -- never happens in this codebase's range, but real fields absent if uncached)
    restricted_area_fga: Optional[float]
    paint_non_ra_fga: Optional[float]
    midrange_fga: Optional[float]
    # Phase 9 / Phase 4B / Phase 8 tracking-era fields (2013-14+ only)
    pull_up_fga: Optional[float]
    catch_shoot_fga: Optional[float]
    drives: Optional[float]
    touches: Optional[float]
    passes_made: Optional[float]
    fta: Optional[float]

    @property
    def player_id(self) -> str:
        """Real load-bearing key is `player_name` here (same convention
        `player_ability_estimation.py` and every box-stat-based module in
        this codebase already uses) -- aliased as `player_id` so this
        row is drop-in compatible with `rim_protection_calibration.py`'s
        generic engine (which groups history by `.player_id`), without
        rebuilding that engine for a name-keyed source."""
        return self.player_name


def build_player_tendency_rows(season: str) -> List[PlayerTendencyRow]:
    teams = load_teams(season)
    adv = load_player_advanced_stats(season)
    shot_zones = szi.load_shot_zones(season)  # keyed by player_id
    exposure = he.load_handling_exposure(season)  # keyed by player_id
    creation = sci.load_shot_creation_tracking(season)  # keyed by player_id
    passing = pti.load_passing_tracking(season)  # keyed by player_id

    # Build name -> tracking-era row via player_id -> name resolution already
    # established as reliable in these caches (all real PLAYER_NAME fields,
    # full display names -- NOT the playbyplayv3 last-name-only quirk from
    # Phase 4B/6, which doesn't apply to any of these endpoints).
    sz_by_name = {r["player_name"]: r for r in shot_zones.values()}
    exp_by_name = {r["player_name"]: r for r in exposure.values()}
    creation_by_name = {r["player_name"]: r for r in creation.values()}
    passing_by_name = {r["player_name"]: r for r in passing.values()}

    rows = []
    for team_name, team in teams.items():
        for p in team.players:
            adv_row = adv.get(p.name, {})
            gp = adv_row.get("gp")
            minutes = (adv_row.get("mpg", 0) * gp) if gp else None
            sz = sz_by_name.get(p.name)
            exp = exp_by_name.get(p.name)
            cr = creation_by_name.get(p.name)
            ps = passing_by_name.get(p.name)
            gp_box = gp or 0
            rows.append(PlayerTendencyRow(
                player_name=p.name, season=season, team_name=team_name,
                fga=p.fga * gp_box, fg3a=p.fg3a * gp_box, oreb=p.oreb * gp_box, reb=p.reb * gp_box,
                minutes=minutes, usg_pct=adv_row.get("usg_pct"),
                restricted_area_fga=sz.get("restricted_area_fga") if sz else None,
                paint_non_ra_fga=sz.get("paint_non_ra_fga") if sz else None,
                midrange_fga=sz.get("midrange_fga") if sz else None,
                pull_up_fga=cr.get("pull_up_fga") if cr else None,
                catch_shoot_fga=cr.get("catch_shoot_fga") if cr else None,
                drives=exp.get("drives") if exp else None,
                touches=exp.get("touches") if exp else None,
                passes_made=ps.get("passes_made") if ps else None,
                fta=p.fta * gp_box,
            ))
    return rows


# =====================================================================
# Candidate rate functions -- each returns None below its own real
# exposure floor (never a fabricated precise tendency from a tiny sample).
# =====================================================================

def three_point_preference(row: PlayerTendencyRow, min_fga: float = 100.0) -> Optional[float]:
    if row.fga < min_fga:
        return None
    return row.fg3a / row.fga


def midrange_preference(row: PlayerTendencyRow, min_zone_fga: float = 100.0) -> Optional[float]:
    if row.midrange_fga is None or row.restricted_area_fga is None or row.paint_non_ra_fga is None:
        return None
    total_2pt_zone = row.restricted_area_fga + row.paint_non_ra_fga + row.midrange_fga
    if total_2pt_zone < min_zone_fga:
        return None
    return row.midrange_fga / total_2pt_zone


def pullup_vs_catch(row: PlayerTendencyRow, min_jumpers: float = 50.0) -> Optional[float]:
    if row.pull_up_fga is None or row.catch_shoot_fga is None:
        return None
    total = row.pull_up_fga + row.catch_shoot_fga
    if total < min_jumpers:
        return None
    return row.pull_up_fga / total


def drive_aggression(row: PlayerTendencyRow, min_touches: float = 200.0) -> Optional[float]:
    if row.drives is None or row.touches is None or row.touches < min_touches:
        return None
    return row.drives / row.touches


def pass_vs_shoot(row: PlayerTendencyRow, min_actions: float = 200.0) -> Optional[float]:
    if row.passes_made is None or row.fta is None:
        return None
    total_actions = row.passes_made + row.fga + row.fta
    if total_actions < min_actions:
        return None
    return row.passes_made / total_actions


def orb_crash_rate(row: PlayerTendencyRow, min_minutes: float = 500.0) -> Optional[float]:
    """
    REAL, HONEST LIMITATION: no real "offensive rebound CHANCE" field
    (a shot went up while this player was in position to crash) exists
    anywhere in this codebase's real cache -- that would need real
    per-possession on-court/location data, not built this phase (see
    report). This is a coarse real proxy only: real OREB volume per real
    minute, NOT opportunity-normalized the way the existing
    `offensive_rebounding` ABILITY attribute's OREB_PCT already is. Kept
    deliberately separate from that attribute's real, opportunity-
    normalized numerator (see module docstring) -- but reported with
    honest, lower confidence in the final classification, not hidden.
    """
    if row.minutes is None or row.minutes < min_minutes:
        return None
    return row.oreb / row.minutes * 36.0  # real OREB per-36, a raw-volume tendency proxy


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


CANDIDATES = {
    "three_point_preference": three_point_preference,
    "midrange_preference": midrange_preference,
    "pullup_vs_catch": pullup_vs_catch,
    "drive_aggression": drive_aggression,
    "pass_vs_shoot": pass_vs_shoot,
    "orb_crash": orb_crash_rate,
}


def combined_diagnostic_pass(rows_by_season: Dict[str, List[PlayerTendencyRow]], check_season: str) -> dict:
    """ONE combined pass, all six candidates: T->T+1 stability + real
    usage/touches contamination, per this phase's own efficiency rule."""
    seasons = sorted(rows_by_season.keys())
    rows = rows_by_season[check_season]
    idx = seasons.index(check_season)

    result = {}
    for name, fn in CANDIDATES.items():
        stability = None
        if idx + 1 < len(seasons):
            next_by_name = {r.player_name: r for r in rows_by_season[seasons[idx + 1]]}
            now_r, next_r = [], []
            for r in rows:
                rn = fn(r)
                r_next = next_by_name.get(r.player_name)
                if rn is None or r_next is None:
                    continue
                rx = fn(r_next)
                if rx is None:
                    continue
                now_r.append(rn)
                next_r.append(rx)
            stability = {"n": len(now_r), "rank_corr": _spearman(now_r, next_r)}

        xs_usg, ys_usg, xs_touch, ys_touch = [], [], [], []
        for r in rows:
            rate = fn(r)
            if rate is None:
                continue
            if r.usg_pct is not None:
                xs_usg.append(r.usg_pct)
                ys_usg.append(rate)
            if r.touches is not None:
                xs_touch.append(r.touches)
                ys_touch.append(rate)
        result[name] = {
            "stability": stability,
            "corr_vs_usg_pct": _pearson(xs_usg, ys_usg), "n_usg": len(xs_usg),
            "corr_vs_touches": _pearson(xs_touch, ys_touch), "n_touches": len(xs_touch),
        }
    return result


def team_switch_portability(rows_by_season: Dict[str, List[PlayerTendencyRow]], s_now: str, s_next: str) -> dict:
    """Real team-switch validation (cheap, per instruction -- reuses
    already-built rows, no new infrastructure): for players whose real
    team changed between s_now and s_next, compare each candidate's T->T+1
    rank stability against ONLY-switched players vs. the full population.
    A real, persistent player tendency should show partial (not total)
    persistence even across a real team change."""
    now_by_name = {r.player_name: r for r in rows_by_season[s_now]}
    next_by_name = {r.player_name: r for r in rows_by_season[s_next]}
    switched_names = {name for name, r in now_by_name.items()
                       if name in next_by_name and next_by_name[name].team_name != r.team_name}

    result = {}
    for name, fn in CANDIDATES.items():
        all_now, all_next, sw_now, sw_next = [], [], [], []
        for pname, r in now_by_name.items():
            r_next = next_by_name.get(pname)
            if r_next is None:
                continue
            rn, rx = fn(r), fn(r_next)
            if rn is None or rx is None:
                continue
            all_now.append(rn)
            all_next.append(rx)
            if pname in switched_names:
                sw_now.append(rn)
                sw_next.append(rx)
        result[name] = {
            "n_all": len(all_now), "rank_corr_all": _spearman(all_now, all_next),
            "n_switched": len(sw_now), "rank_corr_switched": _spearman(sw_now, sw_next),
        }
    return result
