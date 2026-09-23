"""THREE-POINT SHOT RESOLUTION diagnostic helpers.

Read-only measurement over the frozen engine: the wrapper installed by `capture_three_point_attempts`
only records what `apply_perimeter_shot_to_engine` is already called with and returns its result
unchanged, so no probability math and no RNG consumption is altered.
"""
import contextlib
import hashlib
import math
from typing import Dict, List

import margin_compression_diagnostic as mcd
import historical_game_snapshot as hgs
import historical_predictive_backtest as hpb
import possession_orchestrator as po
import shot_resolution as sr
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault

SEASON = mcd.SEASON
ALL_SEASONS = mcd.ALL_SEASONS
_mean = mcd._mean
_sd = mcd._sd
_pearson = mcd._pearson


def make_probability(base_rate: float, release_mode=sr.ReleaseMode.CATCH_AND_SHOOT,
                     contest=sr.ContestBucket.OPEN, posture=po.DefensivePosture.SQUARE) -> float:
    return sr.shot_make_probability(sr.ShotResolutionContext(
        shot_family=sr.ShotFamily.THREE_POINT, shooter_base_rate=base_rate, contest_bucket=contest,
        release_mode=release_mode, defender_posture=posture))


def response_curve(rates=(0.28, 0.32, 0.36, 0.40, 0.44), **ctx) -> List[dict]:
    rows = []
    for r in rates:
        p = make_probability(r, **ctx)
        eps = 0.005
        slope = (make_probability(r + eps, **ctx) - make_probability(r - eps, **ctx)) / (2 * eps)
        rows.append({"base_rate": r, "make_probability": p, "dp_d_base": slope})
    return rows


def quantiles(values: List[float], qs=(0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)) -> dict:
    xs = sorted(values)
    out = {}
    for q in qs:
        idx = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
        out[f"p{int(q * 100):02d}"] = xs[idx]
    out["sd"] = _sd(xs)
    out["mean"] = _mean(xs)
    out["n"] = len(xs)
    return out


@contextlib.contextmanager
def capture_three_point_attempts(sink: list):
    """Records (shooter_id, offense_side, release_mode, posture, probability, made) for every
    THREE_POINT attempt while active; behavior is otherwise identical to the unwrapped call."""
    original = po.apply_perimeter_shot_to_engine

    def wrapper(engine, shooter_id, shot_context, block_context, blocker_id, rng, zone=None, assisted_by=None):
        result = original(engine, shooter_id, shot_context, block_context, blocker_id, rng,
                          zone=zone, assisted_by=assisted_by)
        if shot_context.shot_family == sr.ShotFamily.THREE_POINT:
            sink.append({"shooter": shooter_id, "release": shot_context.release_mode,
                         "posture": shot_context.defender_posture, "prob": sr.shot_make_probability(shot_context),
                         "base": shot_context.shooter_base_rate, "made": result.outcome == sr.ShotOutcome.MADE,
                         "outcome": result.outcome})
        return result

    po.apply_perimeter_shot_to_engine = wrapper
    try:
        yield
    finally:
        po.apply_perimeter_shot_to_engine = original


def simulate_attempts(home_id, away_id, home_five, away_five, profiles, tag: str, n_sims: int) -> List[dict]:
    sink: List[dict] = []
    with capture_three_point_attempts(sink):
        for i in range(n_sims):
            seed = int(hashlib.sha256(f"3pt-diag|{tag}|{i}".encode()).hexdigest()[:16], 16)
            try:
                simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
            except DetailedGameSimulationFault:
                continue
    return sink


def side_summary(attempts: List[dict], five, profiles) -> dict:
    mine = [a for a in attempts if a["shooter"] in five]
    if not mine:
        return {"attempts": 0}
    made = sum(1 for a in mine if a["made"])
    shots_by_player: Dict[str, int] = {}
    for a in mine:
        shots_by_player[a["shooter"]] = shots_by_player.get(a["shooter"], 0) + 1
    ranked = sorted(five, key=lambda p: -(profiles[p].three_point_shrunk_rate or 0))
    total = len(mine)
    return {
        "attempts": total, "pct": made / total, "mean_make_probability": _mean([a["prob"] for a in mine]),
        "catch_and_shoot_share": sum(1 for a in mine if a["release"] == sr.ReleaseMode.CATCH_AND_SHOOT) / total,
        "share_by_shooter_rank": [shots_by_player.get(p, 0) / total for p in ranked],
        "primary_five_mean_rate": _mean([profiles[p].three_point_shrunk_rate for p in five]),
        "attempt_weighted_input_rate": _mean([a["base"] for a in mine]),
    }


def real_game_three_point_pct(game_id: str) -> Dict[str, float]:
    """{team_name: real 3P%} for one real game, from the cached per-player game log."""
    from player_game_log_ingestion import load_player_game_log
    from player_team_stints import team_as_of_date
    from game_metadata import get_game_metadata
    meta = get_game_metadata(game_id, SEASON)
    totals: Dict[str, List[int]] = {meta.home_team: [0, 0], meta.away_team: [0, 0]}
    for pid, rows in load_player_game_log(SEASON).items():
        for row in rows:
            if row["game_id"] != game_id:
                continue
            team = team_as_of_date(pid, meta.game_date, SEASON)
            if team in totals:
                totals[team][0] += row.get("fg3m", 0) or 0
                totals[team][1] += row.get("fg3a", 0) or 0
    return {t: (m / a if a else None) for t, (m, a) in totals.items()}


def three_point_stacking(three_point_rate: float, levels=(0, 1, 2, 3, 4, 5), n_sims: int = 150) -> List[dict]:
    """Team A (5 average) vs a team with k shooters at `three_point_rate` and everything else identical.
    Reports the stack team's 3PA/sim, 3P%, mean make probability and mean margin."""
    from possession_orchestrator import PlayerSimulationProfile
    avg_ids = tuple(str(930001 + i) for i in range(5))
    stack_ids = tuple(str(930011 + i) for i in range(5))
    rows = []
    for k in levels:
        profiles = {pid: PlayerSimulationProfile.synthetic(pid, "AWAY") for pid in avg_ids}
        for i, pid in enumerate(stack_ids):
            extra = {"three_point_shrunk_rate": three_point_rate} if i < k else {}
            profiles[pid] = PlayerSimulationProfile.synthetic(pid, "HOME", **extra)
        sink: List[dict] = []
        margins = []
        with capture_three_point_attempts(sink):
            for i in range(n_sims):
                seed = int(hashlib.sha256(f"3pt-stack|{three_point_rate}|{k}|{i}".encode()).hexdigest()[:16], 16)
                try:
                    r = simulate_detailed_game("HOME", "AWAY", stack_ids, avg_ids, profiles, rng_seed=seed)
                except DetailedGameSimulationFault:
                    continue
                margins.append(r.final_home_score - r.final_away_score)
        mine = [a for a in sink if a["shooter"] in stack_ids]
        rows.append({"n_shooters": k, "team_3pa_per_sim": len(mine) / max(1, len(margins)),
                     "team_3pt_pct": (sum(a["made"] for a in mine) / len(mine)) if mine else None,
                     "mean_make_probability": _mean([a["prob"] for a in mine]),
                     "mean_margin": _mean(margins), "n_sims": len(margins)})
    return rows
