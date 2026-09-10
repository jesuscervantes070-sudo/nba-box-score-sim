"""
Phase 7 -- Rim Protection: the empirical investigation. Entirely offline,
read-only with respect to the rest of the codebase. Not imported by
game_engine.py/season.py/awards.py/models.py/transactions.py/
player_ability_estimation.py's production extractors.

============================ TAXONOMY (formalized, per task) ============================
A. RIM PROTECTION ABILITY  -- technique/timing/positioning/suppression
   when actually contesting -- the thing this phase estimates.
B. RIM-PROTECTION OPPORTUNITY -- how often placed to protect the rim
   (`rim_fga_defended`, real, from rim_protection_ingestion.py).
C. PHYSICAL TRAITS -- standing reach/wingspan/vertical -- NOT modeled
   this phase (no real per-player physical-measurement field exists
   anywhere in this codebase's cache).
D. TEAM SCHEME -- drop/switch/zone/help -- investigated as a real
   CONTAMINATION risk (see `team_scheme_bias_report`), never corrected.
E. DEFENSIVE PLAYMAKING -- block/steal EVENT generation, already a real,
   separate attribute (`defensive_playmaking`, STL+BLK per-36). Overlap
   quantified (see `blocks_overlap_report`), not re-estimated here.
F. FOUL DISCIPLINE -- legal-contest ability, already a real, separate
   Phase 6 attribute. Cross-referenced (see `foul_interaction_report`),
   not re-estimated here.

============================ SUPPRESSION vs DETERRENCE (mechanisms A/B) ============================
- RIM CONVERSION SUPPRESSION (task's mechanism B: "whether the attempted
  shot is made") -- real, direct signal: `rim_suppression_plusminus`
  (NBA's own real, first-party residual: this defender's real opponent
  FG% on Restricted-Area attempts vs. the real "normal"/expected rate for
  that exact shot type). This IS estimable with real, existing data.
- RIM ATTEMPT DETERRENCE (mechanism A: "whether opponents attempt a rim
  shot at all") -- investigated, NOT built as a per-player signal: no
  real per-defender on/off opponent-rim-attempt-frequency field exists
  anywhere in this codebase's real API access; building one would need a
  real per-player on/off query (a materially larger, uncontrolled API
  job, explicitly out of this phase's scope per "avoid uncontrolled
  parallel NBA API calls"). A real, coarse, TEAM-level proxy check is
  done instead (see `team_scheme_bias_report`) -- confirms deterrence is
  a real, plausible phenomenon at the team level but is NOT isolated to
  one player's individual skill with data available this phase. Reported
  as FUTURE ONLY (would need a genuine, larger on/off ingestion effort).
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import rim_protection_ingestion as rpi
import foul_ingestion as fli
from loader import load_teams, load_player_advanced_stats
from turnover_ingestion import resolve_full_name

CANDIDATE_WEIGHTS = ("rim_fga_defended", "minutes", "def_possessions_proxy")


@dataclass
class PlayerRimRow:
    player_id: str
    player_name: str
    season: str
    team_name: Optional[str]
    rim_fga_defended: float
    rim_fgm_allowed: float
    rim_fg_pct_allowed: Optional[float]
    rim_expected_fg_pct: Optional[float]
    rim_suppression_plusminus: Optional[float]  # real, NBA-computed, higher = better
    rim_freq_of_own_defended_shots: Optional[float]
    blk_per36: Optional[float]
    minutes: Optional[float]
    reb_pct: Optional[float]
    usg_pct: Optional[float]
    shooting_foul_committed: Optional[float]  # real, from Phase 6 cache, whichever seasons overlap
    gp: Optional[int]


def build_player_rim_rows(season: str) -> List[PlayerRimRow]:
    rim_data = rpi.load_rim_protection(season)
    if not rim_data:
        return []
    adv = load_player_advanced_stats(season)
    teams = load_teams(season)
    box_by_name = {}
    team_of_name = {}
    for team_name, team in teams.items():
        for p in team.players:
            box_by_name[p.name] = p
            team_of_name[p.name] = team_name

    foul_state = fli.load_foul_cache(season)
    foul_committed_by_name = {}
    if foul_state is not None:
        games_done = len(foul_state["games_done"])
        games_total = foul_state["games_total"]
        scale = (games_total / games_done) if (games_done and games_done < games_total) else 1.0
        for row in foul_state["committers"].values():
            name = resolve_full_name(row["player_id"])
            if name is None:
                continue
            foul_committed_by_name[name] = (row["shooting_foul"] + row["nonshooting_def_foul"]) * scale

    rows = []
    for pid, rrow in rim_data.items():
        name = rrow["player_name"]
        adv_row = adv.get(name, {})
        box_player = box_by_name.get(name)
        gp = adv_row.get("gp")
        minutes = (adv_row.get("mpg", 0) * gp) if gp else None
        blk_per36 = None
        if box_player is not None and box_player.min:
            blk_per36 = box_player.blk / box_player.min * 36.0
        rows.append(PlayerRimRow(
            player_id=pid, player_name=name, season=season,
            team_name=team_of_name.get(name),
            rim_fga_defended=rrow["rim_fga_defended"], rim_fgm_allowed=rrow["rim_fgm_allowed"],
            rim_fg_pct_allowed=rrow.get("rim_fg_pct_allowed"), rim_expected_fg_pct=rrow.get("rim_expected_fg_pct"),
            rim_suppression_plusminus=rrow.get("rim_suppression_plusminus"),
            rim_freq_of_own_defended_shots=rrow.get("rim_freq_of_own_defended_shots"),
            blk_per36=blk_per36, minutes=minutes,
            reb_pct=adv_row.get("reb_pct"), usg_pct=adv_row.get("usg_pct"),
            shooting_foul_committed=foul_committed_by_name.get(name), gp=gp,
        ))
    return rows


# =====================================================================
# Candidate metrics
# =====================================================================

def suppression_rate(row: PlayerRimRow, min_exposure: float = 30.0) -> Optional[float]:
    """The primary rim-protection-ability signal: real opponent FG%
    suppression on Restricted-Area attempts, already computed by the
    NBA itself (see module docstring). `min_exposure` guards against a
    near-zero-attempt sample masquerading as a real rate."""
    if row.rim_fga_defended < min_exposure or row.rim_suppression_plusminus is None:
        return None
    return row.rim_suppression_plusminus


def weight_value(row: PlayerRimRow, weight: str, league_mean_possessions: Optional[float] = None) -> Optional[float]:
    if weight == "rim_fga_defended":
        return row.rim_fga_defended
    if weight == "minutes":
        return row.minutes
    if weight == "def_possessions_proxy":
        if row.minutes is None or league_mean_possessions is None:
            return None
        return (row.minutes / 48.0) * league_mean_possessions
    raise ValueError(f"Unknown weight {weight!r}")


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


# =====================================================================
# Blocks overlap
# =====================================================================

def blocks_overlap_report(rows: List[PlayerRimRow], min_exposure: float = 30.0) -> dict:
    """Real correlation between rim-suppression rate and real BLK/36 --
    if this is near 1.0, rim protection would just be rediscovering
    defensive_playmaking's existing BLK component (a REVISIT signal)."""
    xs, ys = [], []
    for r in rows:
        rate = suppression_rate(r, min_exposure)
        if rate is None or r.blk_per36 is None:
            continue
        xs.append(r.blk_per36)
        ys.append(rate)
    return {"n": len(xs), "corr_suppression_vs_blk_per36": _pearson(xs, ys),
            "rank_corr_suppression_vs_blk_per36": _spearman(xs, ys)}


# =====================================================================
# Foul interaction
# =====================================================================

def foul_interaction_report(rows: List[PlayerRimRow], min_exposure: float = 30.0) -> dict:
    """Real correlation between rim-suppression rate and real (Phase 6,
    OVERALL not rim-specific -- a stated limitation) shooting-foul-
    committed rate -- checks whether apparent suppression is partly
    achieved by fouling (which should NOT be rewarded as clean defense)."""
    xs, ys = [], []
    for r in rows:
        rate = suppression_rate(r, min_exposure)
        if rate is None or r.shooting_foul_committed is None or not r.minutes:
            continue
        xs.append(r.shooting_foul_committed / r.minutes)
        ys.append(rate)
    return {"n": len(xs), "corr_suppression_vs_foul_rate": _pearson(xs, ys)}


# =====================================================================
# Team / scheme bias
# =====================================================================

def team_scheme_bias_report(rows: List[PlayerRimRow], season: str, min_exposure: float = 30.0) -> dict:
    """Real correlation between individual rim-protection signals and
    real TEAM-level context (opponent rim-attempt frequency, real team
    defensive rating via loader.load_teams' cached defense fields) --
    quantifies contamination, per the task's explicit instruction, rather
    than assuming or correcting for it blindly."""
    team_opp_rim = rpi.load_team_opp_rim_frequency(season)
    xs_freq, ys_freq = [], []
    xs_fga, ys_oppfga = [], []
    for r in rows:
        rate = suppression_rate(r, min_exposure)
        if rate is None or r.team_name is None:
            continue
        opp_rim_fga = team_opp_rim.get(r.team_name)
        if opp_rim_fga is not None:
            xs_freq.append(opp_rim_fga)
            ys_freq.append(rate)
        xs_fga.append(r.rim_fga_defended)
        if opp_rim_fga is not None:
            ys_oppfga.append(opp_rim_fga)
    return {
        "n_suppression_vs_team_opp_rim_freq": len(xs_freq),
        "corr_suppression_vs_team_opp_rim_freq": _pearson(xs_freq, ys_freq),
        "n_opportunity_vs_team_opp_rim_freq": min(len(xs_fga), len(ys_oppfga)),
        "corr_opportunity_vs_team_opp_rim_freq": _pearson(xs_fga[:len(ys_oppfga)], ys_oppfga) if ys_oppfga else None,
    }


def role_bias_report(rows: List[PlayerRimRow], min_exposure: float = 30.0) -> dict:
    xs_reb, ys = [], []
    for r in rows:
        rate = suppression_rate(r, min_exposure)
        if rate is None or r.reb_pct is None:
            continue
        xs_reb.append(r.reb_pct)
        ys.append(rate)
    return {"n": len(xs_reb), "corr_suppression_vs_reb_pct (big-proxy)": _pearson(xs_reb, ys)}


# =====================================================================
# Weight (denominator) comparison, scale-normalized (Phase 6's own
# correction, reused deliberately -- do not repeat that mistake).
# =====================================================================

def compare_weights(rows_by_season: Dict[str, List[PlayerRimRow]], min_exposure: float = 30.0) -> dict:
    """`suppression_rate` is already a real percentage-point RATE (not
    itself divided by any of these candidates) -- what's actually being
    compared here is which real quantity should serve as the SAMPLE-SIZE
    WEIGHT in recency/shrinkage calibration. Evaluated the same
    scale-normalized way as Phase 6 (CV_MAE + rank correlation), even
    though these three weights don't change the rate's own scale (only
    its shrinkage-prior strength) -- included for completeness/rigor."""
    seasons = sorted(rows_by_season.keys())
    results = {}
    for weight in CANDIDATE_WEIGHTS:
        pairs = []
        for i in range(len(seasons) - 1):
            s_now, s_next = seasons[i], seasons[i + 1]
            lmp_now = _load_lmp(s_now) if weight == "def_possessions_proxy" else None
            lmp_next = _load_lmp(s_next) if weight == "def_possessions_proxy" else None
            now_by_id = {r.player_id: r for r in rows_by_season[s_now]}
            next_by_id = {r.player_id: r for r in rows_by_season[s_next]}
            for pid, r_now in now_by_id.items():
                r_next = next_by_id.get(pid)
                if r_next is None:
                    continue
                rn = suppression_rate(r_now, min_exposure)
                rx = suppression_rate(r_next, min_exposure)
                if rn is None or rx is None:
                    continue
                w = weight_value(r_next, weight, lmp_next)
                if w is None or w <= 0:
                    continue
                pairs.append((rn, rx, w))
        if not pairs:
            results[weight] = {"n_pairs": 0, "weighted_mae": None}
            continue
        total_w = sum(w for _, _, w in pairs)
        mae = sum(abs(a - b) * w for a, b, w in pairs) / total_w
        rmse = math.sqrt(sum((a - b) ** 2 * w for a, b, w in pairs) / total_w)
        results[weight] = {"n_pairs": len(pairs), "weighted_mae": round(mae, 6), "weighted_rmse": round(rmse, 6)}
    return {"season_pairs_used": list(zip(seasons, seasons[1:])), "weights": results}


def _load_lmp(season: str) -> Optional[float]:
    import json
    from pathlib import Path
    path = Path("cache") / season / "league_pace.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f).get("mean_possessions")
