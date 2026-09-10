"""
Phase 10 -- POA Containment: the empirical investigation. Entirely
offline, read-only. Reuses existing caches: `loader.load_teams`/
`player_advanced.json` (box stats, usg_pct, reb_pct), Phase 6's
`foul_ingestion.py` cache (foul-discipline cross-check), and Phase 7's
`rim_protection_ingestion.load_team_opp_rim_frequency` (team-scheme
cross-check) -- only `poa_containment_ingestion.py`'s matchup aggregates
are genuinely new.

============================ THE CANDIDATE (kept to one, per task's "small set") ============================
`containment_rate = (total_expected_fgm - total_matchup_fgm) / expected_fga_covered`

Real, opponent-quality-adjusted FG% suppression, PRIMARY-MATCHUP ONLY
(structurally excludes help defense -- see ingestion module docstring).
Higher = better (opponent shoots WORSE than their own real season rate
when this player is the primary defender). Deliberately excludes
`MATCHUP_TOV` from the numerator (that's `defensive_playmaking`'s
domain, see `steals_overlap_report`) and `SFL` (that's `foul_discipline`'s
domain, see `foul_interaction_report`).
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import poa_containment_ingestion as pci
from loader import load_teams, load_player_advanced_stats

MIN_EXPOSURE_DEFAULT = 100.0  # real expected_fga_covered floor


@dataclass
class PlayerContainmentRow:
    player_id: str
    player_name: str
    season: str
    total_partial_poss: float
    total_matchup_fga: float
    total_matchup_fgm: float
    total_expected_fgm: float
    expected_fga_covered: float
    total_matchup_tov: float
    total_sfl: float
    total_matchup_time_sec: float
    n_distinct_opponents: int
    minutes: Optional[float]
    usg_pct: Optional[float]
    reb_pct: Optional[float]
    stl_per36: Optional[float]
    gp: Optional[int]


def build_player_containment_rows(season: str) -> List[PlayerContainmentRow]:
    poa_data = pci.load_poa_containment(season)
    if not poa_data:
        return []
    adv = load_player_advanced_stats(season)
    teams = load_teams(season)
    box_by_name = {p.name: p for team in teams.values() for p in team.players}

    rows = []
    for pid, prow in poa_data.items():
        name = prow["player_name"]
        adv_row = adv.get(name, {})
        box_player = box_by_name.get(name)
        gp = adv_row.get("gp")
        minutes = (adv_row.get("mpg", 0) * gp) if gp else None
        stl_per36 = None
        if box_player is not None and box_player.min:
            stl_per36 = box_player.stl / box_player.min * 36.0
        rows.append(PlayerContainmentRow(
            player_id=pid, player_name=name, season=season,
            total_partial_poss=prow["total_partial_poss"], total_matchup_fga=prow["total_matchup_fga"],
            total_matchup_fgm=prow["total_matchup_fgm"], total_expected_fgm=prow["total_expected_fgm"],
            expected_fga_covered=prow["expected_fga_covered"], total_matchup_tov=prow["total_matchup_tov"],
            total_sfl=prow["total_sfl"], total_matchup_time_sec=prow["total_matchup_time_sec"],
            n_distinct_opponents=prow["n_distinct_opponents"],
            minutes=minutes, usg_pct=adv_row.get("usg_pct"), reb_pct=adv_row.get("reb_pct"),
            stl_per36=stl_per36, gp=gp,
        ))
    return rows


def containment_rate(row: PlayerContainmentRow, min_exposure: float = MIN_EXPOSURE_DEFAULT) -> Optional[float]:
    if row.expected_fga_covered < min_exposure:
        return None
    return (row.total_expected_fgm - row.total_matchup_fgm) / row.expected_fga_covered


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


def combined_diagnostic_pass(rows_by_season: Dict[str, List[PlayerContainmentRow]], check_season: str,
                              min_exposure: float = MIN_EXPOSURE_DEFAULT) -> dict:
    seasons = sorted(rows_by_season.keys())
    rows = rows_by_season[check_season]

    # stability
    idx = seasons.index(check_season)
    stability = None
    if idx + 1 < len(seasons):
        next_by_id = {r.player_id: r for r in rows_by_season[seasons[idx + 1]]}
        now_r, next_r = [], []
        for r in rows:
            rn = containment_rate(r, min_exposure)
            r_next = next_by_id.get(r.player_id)
            if rn is None or r_next is None:
                continue
            rx = containment_rate(r_next, min_exposure)
            if rx is None:
                continue
            now_r.append(rn)
            next_r.append(rx)
        stability = {"n": len(now_r), "rank_corr": _spearman(now_r, next_r)}

    # role/contamination + steals overlap, one pass
    xs = {"usg_pct": [], "minutes": [], "reb_pct": [], "stl_per36": []}
    ys = {k: [] for k in xs}
    for r in rows:
        rate = containment_rate(r, min_exposure)
        if rate is None:
            continue
        for key, val in (("usg_pct", r.usg_pct), ("minutes", r.minutes), ("reb_pct", r.reb_pct), ("stl_per36", r.stl_per36)):
            if val is not None:
                xs[key].append(val)
                ys[key].append(rate)
    contamination = {f"corr_vs_{k}": _pearson(xs[k], ys[k]) for k in xs} | {"n": len(xs["usg_pct"])}

    return {"season": check_season, "stability": stability, "contamination_and_overlap": contamination}


def foul_interaction_report(rows: List[PlayerContainmentRow], min_exposure: float = MIN_EXPOSURE_DEFAULT) -> dict:
    """Real correlation between containment rate and real SFL (shooting-
    fouls-related) rate PER matchup possession -- checks whether apparent
    containment is partly achieved by illegal contact."""
    xs, ys = [], []
    for r in rows:
        rate = containment_rate(r, min_exposure)
        if rate is None or r.total_partial_poss <= 0:
            continue
        xs.append(r.total_sfl / r.total_partial_poss)
        ys.append(rate)
    return {"n": len(xs), "corr_containment_vs_sfl_rate": _pearson(xs, ys)}


def team_scheme_bias_report(rows: List[PlayerContainmentRow], season: str, min_exposure: float = MIN_EXPOSURE_DEFAULT) -> dict:
    """Real correlation with team-level context: team defensive FG%
    allowed (loader's existing team_defense.json) and team rim-protection
    quality (Phase 7's rim_protection_ingestion team opponent-rim-freq
    cache, reused directly)."""
    import json
    from pathlib import Path
    import rim_protection_ingestion as rpi
    from loader import load_teams

    teams = load_teams(season)
    name_to_team = {p.name: tname for tname, team in teams.items() for p in team.players}
    team_def_path = Path("cache") / season / "team_defense.json"
    team_def = json.load(open(team_def_path))["teams"] if team_def_path.exists() else {}
    team_opp_rim = rpi.load_team_opp_rim_frequency(season)

    xs_def, ys_def, xs_rim, ys_rim = [], [], [], []
    for r in rows:
        rate = containment_rate(r, min_exposure)
        if rate is None:
            continue
        tname = name_to_team.get(r.player_name)
        if tname is None:
            continue
        td = team_def.get(tname)
        if td is not None and td.get("opp_fga"):
            xs_def.append(td["opp_fgm"] / td["opp_fga"])
            ys_def.append(rate)
        opp_rim = team_opp_rim.get(tname)
        if opp_rim is not None:
            xs_rim.append(opp_rim)
            ys_rim.append(rate)
    return {
        "n_vs_team_def_fgpct": len(xs_def), "corr_vs_team_def_fgpct": _pearson(xs_def, ys_def),
        "n_vs_team_opp_rim_freq": len(xs_rim), "corr_vs_team_opp_rim_freq": _pearson(xs_rim, ys_rim),
    }
