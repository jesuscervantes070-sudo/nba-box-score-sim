"""FIRST HISTORICAL PREDICTIVE BACKTEST V1.

A clean evaluation layer, separate from every engine/estimator module: selects a deterministic
holdout of real historical games, builds `HistoricalGameSnapshot`s (reused by import from
`historical_game_snapshot.py`, unmodified), runs Monte Carlo batches through the frozen
`detailed_game.simulate_detailed_game` (unmodified), joins real reconstructed outcomes (via
`historical_game_outcome.py`, new this phase but itself pure composition over already-cached real
data), computes probabilistic/margin/score/calibration metrics, and compares against transparent,
leak-safe baselines. NO estimator math, shrinkage, priors, engine mechanics, or temporal cutoffs
are touched here -- this module is read-only over everything upstream of it.

============================ HOLDOUT SELECTION (locked BEFORE any prediction is run) ============================
`select_holdout_games()` is a fully mechanical, deterministic rule: every real 2023-24 regular
season game_id (from the already-cached `schedule.json`) is included iff
`int(sha256(game_id).hexdigest(), 16) % HOLDOUT_SELECTION_MOD == 0`. This is NOT sensitive to
schedule ordering (a fixed-stride sample over date-sorted games could accidentally correlate with
day-of-week or back-to-back position; a hash-based sample does not), reproduces byte-identically on
every call, and was computed and written to `backtests/backtest_v1_holdout_game_ids.json` BEFORE
this module ever built a snapshot or ran a simulation -- the selection rule and its resulting game
list are never revisited after seeing prediction results (this is BACKTEST_V1_HOLDOUT).

============================ SIMULATION COUNT / SEED POLICY ============================
`N_SIMULATIONS` (see `simulation_stability_study.py` for the empirical count-stability study this
value was chosen from) is fixed BEFORE the holdout run and never changed mid-backtest. Seeds are
fully deterministic: `_seed_for(game_id, mode, sim_index)` hashes
`(MODEL_VERSION, mode, game_id, sim_index)` -- the SAME triple always reproduces the SAME seed, so
a repeated backtest run against the same commit/config reproduces byte-identical raw results. A
single `rng_seed` drives `simulate_detailed_game`'s own internal RNG stream for the whole game
(both teams share that one stream, exactly as every other phase's own engine tests already do --
never a team-specific seed, so no seed ever structurally favors one side).

============================ PREGAME_EXPECTED IS THE HONEST PREDICTOR ============================
`ORACLE_PARTICIPANTS` is evaluated ONLY as an upper-bound/diagnostic comparison (see module
docstring on `historical_game_snapshot.py`'s own mode doctrine) -- it uses real target-game
participants/minutes and is NEVER a real predictor. All primary conclusions in this phase's report
use `PREGAME_EXPECTED`.
"""
import functools
import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import historical_game_snapshot as hgs
import historical_game_outcome as hgo
import player_team_stints as pts
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault

MODEL_VERSION = "backtest-v1"
BACKTEST_SEASON = "2023-24"
HOLDOUT_SELECTION_MOD = 16  # int(sha256(game_id),16) % this == 0 -- see module docstring

# Chosen from simulation_stability_study.py's own empirical count-stability comparison (100/250/
# 500/1000, 3 independent seed batches each, on 3 real 2023-24 games spanning a close/toss-up
# favorite, a moderate favorite, and a strong favorite). Real measured batch-to-batch spread in
# home win probability: n=100 -> 0.060-0.070; n=250 -> 0.028-0.108 (one game's n=250 batches were
# noisier than its own n=100 batches -- a real artifact of only 3 batches per count, not evidence
# n=250 is worse than n=100 in general); n=500 -> 0.032-0.074; n=1000 -> 0.014-0.045. n=1000 was
# also measured to cost up to 526s for a single game's batch (vs ~40s at n=250, ~78s at n=500) --
# a >10x-versus-n=250 runtime cost for a further ~0.01-0.03 reduction in spread. n=250 is the
# smallest tested count with real, if noisy, stability improvement over n=100 and a runtime that
# keeps an ~80-game, two-mode (PREGAME + ORACLE) holdout practical in one session; V1 win
# probabilities should be read with an honest ~+/-0.03-0.10 Monte Carlo noise band, not
# over-interpreted at the single-game level. Fixed before the holdout run; never changed
# mid-backtest.
N_SIMULATIONS = 250

BACKTESTS_DIR = Path("backtests")


# =====================================================================
# Holdout selection
# =====================================================================
def select_holdout_games(season: str = BACKTEST_SEASON, mod: int = HOLDOUT_SELECTION_MOD) -> List[str]:
    """The exact, documented, mechanical selection rule (see module docstring). Deterministic --
    calling this twice always returns the identical, identically-ordered list."""
    path = Path("cache") / season / "schedule.json"
    with open(path) as f:
        games = json.load(f)["games"]
    selected = [
        g["game_id"] for g in games
        if int(hashlib.sha256(g["game_id"].encode()).hexdigest(), 16) % mod == 0
    ]
    return sorted(selected)


def load_or_create_holdout(season: str = BACKTEST_SEASON) -> List[str]:
    """The persisted BACKTEST_V1_HOLDOUT game-id list. If a file already exists, that EXACT
    (already-locked) list is used regardless of what `select_holdout_games()` would currently
    compute -- selection is locked once, at first creation, never silently recomputed on a later
    run (that would be a form of post-hoc resampling)."""
    BACKTESTS_DIR.mkdir(exist_ok=True)
    path = BACKTESTS_DIR / "backtest_v1_holdout_game_ids.json"
    if path.exists():
        with open(path) as f:
            data = json.load(f)
        return data["game_ids"]
    game_ids = select_holdout_games(season)
    with open(path, "w") as f:
        json.dump({
            "season": season, "selection_rule": f"sha256(game_id) % {HOLDOUT_SELECTION_MOD} == 0",
            "n_games": len(game_ids), "game_ids": game_ids,
        }, f, indent=2, sort_keys=True)
    return game_ids


# =====================================================================
# Deterministic seed policy
# =====================================================================
def _seed_for(game_id: str, mode: str, sim_index: int) -> int:
    h = hashlib.sha256(f"{MODEL_VERSION}|{mode}|{game_id}|{sim_index}".encode()).hexdigest()
    return int(h[:16], 16)  # 64-bit deterministic seed


# =====================================================================
# Simulation batch
# =====================================================================
@dataclass(frozen=True)
class SimulationBatchResult:
    game_id: str
    mode: str
    n_requested: int
    n_valid: int
    home_scores: Tuple[int, ...]
    away_scores: Tuple[int, ...]
    fault_reasons: Tuple[str, ...]  # one entry per faulted simulation, never silently dropped


def run_simulation_batch(snapshot: hgs.HistoricalGameSnapshot, n_sims: int = N_SIMULATIONS) -> SimulationBatchResult:
    home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snapshot)
    home_scores = []
    away_scores = []
    faults = []
    for i in range(n_sims):
        seed = _seed_for(snapshot.game_id, snapshot.mode, i)
        try:
            result = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
        except DetailedGameSimulationFault as e:
            faults.append(f"{e.code}: {e}")
            continue
        if result.final_home_score == result.final_away_score:
            # structurally should not happen (engine plays overtime until resolved) -- if it ever
            # does, this is a genuine invalid simulation, never coin-flipped.
            faults.append(f"UNRESOLVED_TIE: home={result.final_home_score} away={result.final_away_score}")
            continue
        home_scores.append(result.final_home_score)
        away_scores.append(result.final_away_score)
    return SimulationBatchResult(
        game_id=snapshot.game_id, mode=snapshot.mode, n_requested=n_sims,
        n_valid=len(home_scores), home_scores=tuple(home_scores), away_scores=tuple(away_scores),
        fault_reasons=tuple(faults),
    )


# =====================================================================
# Percentiles (no numpy dependency -- simple, deterministic, documented)
# =====================================================================
def _percentiles(values: Sequence[float], pcts=(10, 25, 50, 75, 90)) -> Dict[str, float]:
    if not values:
        return {f"p{p}": None for p in pcts}
    ordered = sorted(values)
    n = len(ordered)
    out = {}
    for p in pcts:
        if n == 1:
            out[f"p{p}"] = float(ordered[0])
            continue
        rank = (p / 100.0) * (n - 1)
        lo = int(math.floor(rank))
        hi = int(math.ceil(rank))
        if lo == hi:
            out[f"p{p}"] = float(ordered[lo])
        else:
            frac = rank - lo
            out[f"p{p}"] = float(ordered[lo] * (1 - frac) + ordered[hi] * frac)
    return out


def _summarize_snapshot_provenance(snapshot: hgs.HistoricalGameSnapshot) -> Dict[str, Dict[str, int]]:
    """Machine-readable provenance summary over the players actually feeding the engine (both
    primary fives) -- {truth_group: {provenance_label: count}}."""
    summary: Dict[str, Dict[str, int]] = {}
    for team_snap in (snapshot.home_team_snapshot, snapshot.away_team_snapshot):
        for player in team_snap.players:
            if not player.is_primary_five:
                continue
            for group, label in player.truth_provenance.items():
                summary.setdefault(group, {})
                summary[group][label] = summary[group].get(label, 0) + 1
    return summary


# =====================================================================
# Game prediction
# =====================================================================
@dataclass(frozen=True)
class GamePrediction:
    game_id: str
    date: str
    home_team: str
    away_team: str
    mode: str
    n_requested: int
    n_valid: int
    fault_reasons: Tuple[str, ...]
    predicted_home_win_prob: Optional[float]
    predicted_away_win_prob: Optional[float]
    mean_sim_home_score: Optional[float]
    mean_sim_away_score: Optional[float]
    predicted_mean_margin: Optional[float]
    median_margin: Optional[float]
    margin_percentiles: Dict[str, float]
    home_score_percentiles: Dict[str, float]
    away_score_percentiles: Dict[str, float]
    margin_sd: Optional[float]
    snapshot_provenance: Dict[str, Dict[str, int]]

    def to_dict(self) -> dict:
        return asdict(self)


def predict_game(game_id: str, season: str, all_seasons: List[str], mode: str,
                  n_sims: int = N_SIMULATIONS) -> Tuple[Optional[GamePrediction], Optional[str]]:
    """Returns (prediction, failure_reason). failure_reason is None iff prediction succeeded --
    snapshot-build failures and simulation-batch failures are NEVER silently swallowed."""
    try:
        snapshot = hgs.build_historical_game_snapshot(game_id, season, all_seasons, mode=mode)
    except Exception as e:
        return None, f"SNAPSHOT_BUILD_FAILED: {type(e).__name__}: {e}"

    try:
        hgs.audit_snapshot_temporal_safety(snapshot)
    except hgs.SnapshotTemporalSafetyError as e:
        return None, f"TEMPORAL_SAFETY_AUDIT_FAILED: {e}"

    batch = run_simulation_batch(snapshot, n_sims=n_sims)
    if batch.n_valid == 0:
        return None, f"ALL_SIMULATIONS_FAULTED: {batch.fault_reasons[:3]}"

    home_scores = batch.home_scores
    away_scores = batch.away_scores
    margins = tuple(h - a for h, a in zip(home_scores, away_scores))
    home_wins = sum(1 for m in margins if m > 0)

    prediction = GamePrediction(
        game_id=game_id, date=snapshot.game_date, home_team=snapshot.home_team,
        away_team=snapshot.away_team, mode=mode,
        n_requested=batch.n_requested, n_valid=batch.n_valid, fault_reasons=batch.fault_reasons,
        predicted_home_win_prob=home_wins / batch.n_valid,
        predicted_away_win_prob=1.0 - home_wins / batch.n_valid,
        mean_sim_home_score=statistics.mean(home_scores),
        mean_sim_away_score=statistics.mean(away_scores),
        predicted_mean_margin=statistics.mean(margins),
        median_margin=statistics.median(margins),
        margin_percentiles=_percentiles(margins),
        home_score_percentiles=_percentiles(home_scores),
        away_score_percentiles=_percentiles(away_scores),
        margin_sd=statistics.pstdev(margins) if len(margins) > 1 else 0.0,
        snapshot_provenance=_summarize_snapshot_provenance(snapshot),
    )
    return prediction, None


# =====================================================================
# Metrics
# =====================================================================
def brier_score(pairs: List[Tuple[float, int]]) -> Optional[float]:
    """pairs: [(predicted_home_win_prob, actual_home_win_as_0_or_1), ...]"""
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: List[Tuple[float, int]], eps: float = 1e-6) -> Optional[float]:
    if not pairs:
        return None
    total = 0.0
    for p, y in pairs:
        p_clipped = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p_clipped) + (1 - y) * math.log(1 - p_clipped))
    return total / len(pairs)


def winner_accuracy(pairs: List[Tuple[float, int]]) -> Optional[float]:
    """p>=0.5 -> predict home win. Documented tie-break: p==0.5 counts as a home pick."""
    if not pairs:
        return None
    correct = sum(1 for p, y in pairs if (1 if p >= 0.5 else 0) == y)
    return correct / len(pairs)


def margin_mae(pred_margins: List[float], actual_margins: List[float]) -> Optional[float]:
    if not pred_margins:
        return None
    return sum(abs(p - a) for p, a in zip(pred_margins, actual_margins)) / len(pred_margins)


def margin_rmse(pred_margins: List[float], actual_margins: List[float]) -> Optional[float]:
    if not pred_margins:
        return None
    return math.sqrt(sum((p - a) ** 2 for p, a in zip(pred_margins, actual_margins)) / len(pred_margins))


def mean_signed_margin_error(pred_margins: List[float], actual_margins: List[float]) -> Optional[float]:
    if not pred_margins:
        return None
    return sum(p - a for p, a in zip(pred_margins, actual_margins)) / len(pred_margins)


def score_mae(pred_scores: List[float], actual_scores: List[int]) -> Optional[float]:
    if not pred_scores:
        return None
    return sum(abs(p - a) for p, a in zip(pred_scores, actual_scores)) / len(pred_scores)


def calibration_table(pairs: List[Tuple[float, int]], bin_edges=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)) -> List[dict]:
    """Confidence-calibration bins over `max(p_home, 1-p_home)` (the predicted side's own
    confidence) vs. whether the predicted side actually won -- the standard "how well-calibrated
    is the model's confidence" table, matching the task's 50-60/60-70/.../90-100 bin spec
    regardless of whether the predicted side is home or away."""
    bins = []
    for lo, hi in zip(bin_edges[:-1], bin_edges[1:]):
        bin_pairs = []
        for p, y in pairs:
            conf = max(p, 1 - p)
            predicted_home = p >= 0.5
            hit = (predicted_home and y == 1) or (not predicted_home and y == 0)
            if lo <= conf < hi:
                bin_pairs.append((conf, hit))
        n = len(bin_pairs)
        bins.append({
            "range": f"{int(lo*100)}-{int(min(hi,1.0)*100)}%",
            "n_games": n,
            "mean_predicted_confidence": (sum(c for c, _ in bin_pairs) / n) if n else None,
            "actual_hit_rate": (sum(1 for _, h in bin_pairs if h) / n) if n else None,
        })
    return bins


def expected_calibration_error(bins: List[dict], total_n: int) -> Optional[float]:
    if not total_n:
        return None
    ece = 0.0
    for b in bins:
        if b["n_games"] == 0:
            continue
        ece += (b["n_games"] / total_n) * abs(b["mean_predicted_confidence"] - b["actual_hit_rate"])
    return ece


# =====================================================================
# Leak-safe baselines
# =====================================================================
PRIOR_SEASON = "2022-23"  # the ONE season immediately before BACKTEST_SEASON -- fully completed,
# real, never touches 2023-24 target-game evidence at all.
NET_RATING_LOGISTIC_SCALE = 11.0  # a fixed, well-known, documented NBA point-margin-to-win-
# probability scale (Elo/net-rating literature commonly uses ~10-11 points per "one full win-
# probability logistic unit") -- NOT fit on this backtest's own holdout results, never touched
# after selection, per this phase's own "no post-hoc tuning" rule.
MIN_IN_SEASON_GAMES_FOR_NET_RATING = 5  # below this, fall back entirely to the PRIOR season's
# real, final average point differential rather than a noisy few-game in-season average.


@functools.lru_cache(maxsize=1)
def _all_game_outcomes(season: str) -> Tuple[hgo.GameOutcome, ...]:
    path = Path("cache") / season / "schedule.json"
    with open(path) as f:
        games = json.load(f)["games"]
    outcomes = []
    for g in games:
        o = hgo.get_game_outcome(g["game_id"], season)
        if o is not None:
            outcomes.append(o)
    return tuple(outcomes)


@functools.lru_cache(maxsize=1)
def _prior_season_league_home_win_rate(season: str = PRIOR_SEASON) -> Optional[float]:
    """Real, leak-safe: the league-wide home-win rate over an ENTIRE PRIOR, already-completed
    season -- never the test season's own (partial or final) home-win rate."""
    outcomes = _all_game_outcomes(season)
    if not outcomes:
        return None
    return sum(1 for o in outcomes if o.home_win) / len(outcomes)


@functools.lru_cache(maxsize=1)
def _prior_season_avg_margin(season: str = PRIOR_SEASON) -> Optional[float]:
    outcomes = _all_game_outcomes(season)
    if not outcomes:
        return None
    return sum(o.margin for o in outcomes) / len(outcomes)


@functools.lru_cache(maxsize=1)
def _prior_season_team_avg_diff(season: str = PRIOR_SEASON) -> Dict[str, float]:
    """Real per-team average point differential (their own margin, home and away games pooled)
    over one entire completed prior season -- the fallback prior for early-season net rating."""
    outcomes = _all_game_outcomes(season)
    totals: Dict[str, List[int]] = {}
    for o in outcomes:
        totals.setdefault(o.home_team, []).append(o.margin)
        totals.setdefault(o.away_team, []).append(-o.margin)
    return {team: sum(vals) / len(vals) for team, vals in totals.items()}


def _team_net_rating_as_of(team: str, as_of_date: str, season: str = BACKTEST_SEASON) -> float:
    """Real, date-safe: this team's own average point differential over real games STRICTLY
    BEFORE `as_of_date` within `season`; falls back to the real, completed PRIOR season's average
    for that team when too few in-season games exist yet (early season). Never uses `season`
    evidence on or after `as_of_date`."""
    in_season = [o.margin if o.home_team == team else -o.margin
                 for o in _all_game_outcomes(season)
                 if o.game_date < as_of_date and team in (o.home_team, o.away_team)]
    if len(in_season) >= MIN_IN_SEASON_GAMES_FOR_NET_RATING:
        return sum(in_season) / len(in_season)
    prior = _prior_season_team_avg_diff(PRIOR_SEASON)
    if team in prior:
        return prior[team]
    return sum(in_season) / len(in_season) if in_season else 0.0  # real MISSING fallback: no signal


def _logistic(margin: float, scale: float = NET_RATING_LOGISTIC_SCALE) -> float:
    return 1.0 / (1.0 + math.pow(10.0, -margin / scale))


@dataclass(frozen=True)
class BaselinePrediction:
    game_id: str
    predicted_home_win_prob: float
    predicted_margin: Optional[float]


def baseline_5050(game_id: str, home_team: str, away_team: str, game_date: str) -> BaselinePrediction:
    return BaselinePrediction(game_id=game_id, predicted_home_win_prob=0.5, predicted_margin=0.0)


def baseline_prior_home_rate(game_id: str, home_team: str, away_team: str, game_date: str) -> BaselinePrediction:
    rate = _prior_season_league_home_win_rate() or 0.5
    avg_margin = _prior_season_avg_margin() or 0.0
    return BaselinePrediction(game_id=game_id, predicted_home_win_prob=rate, predicted_margin=avg_margin)


def baseline_net_rating(game_id: str, home_team: str, away_team: str, game_date: str) -> BaselinePrediction:
    home_nr = _team_net_rating_as_of(home_team, game_date)
    away_nr = _team_net_rating_as_of(away_team, game_date)
    margin = home_nr - away_nr
    return BaselinePrediction(game_id=game_id, predicted_home_win_prob=_logistic(margin), predicted_margin=margin)


BASELINES = {
    "BASELINE_5050": baseline_5050,
    "BASELINE_PRIOR_SEASON_HOME_RATE": baseline_prior_home_rate,
    "BASELINE_NET_RATING": baseline_net_rating,
}


# =====================================================================
# Orchestration
# =====================================================================
@dataclass
class BacktestRunResult:
    mode: str
    n_attempted: int
    n_succeeded: int
    skips: List[dict]
    predictions: List[GamePrediction]
    outcomes: Dict[str, hgo.GameOutcome]
    baseline_predictions: Dict[str, Dict[str, BaselinePrediction]]  # {baseline_name: {game_id: pred}}
    timing: dict


def run_backtest(game_ids: List[str], season: str, all_seasons: List[str], mode: str,
                  n_sims: int = N_SIMULATIONS, verbose: bool = False) -> BacktestRunResult:
    predictions: List[GamePrediction] = []
    outcomes: Dict[str, hgo.GameOutcome] = {}
    skips: List[dict] = []
    baseline_preds: Dict[str, Dict[str, BaselinePrediction]] = {name: {} for name in BASELINES}

    t_start = time.time()
    snapshot_time_total = 0.0
    sim_time_total = 0.0

    for game_id in game_ids:
        outcome = hgo.get_game_outcome(game_id, season)
        if outcome is None:
            skips.append({"game_id": game_id, "stage": "OUTCOME_JOIN", "reason": "no real reconstructable outcome"})
            continue

        t0 = time.time()
        try:
            snapshot = hgs.build_historical_game_snapshot(game_id, season, all_seasons, mode=mode)
        except Exception as e:
            skips.append({"game_id": game_id, "stage": "SNAPSHOT_BUILD", "reason": f"{type(e).__name__}: {e}"})
            continue
        try:
            hgs.audit_snapshot_temporal_safety(snapshot)
        except hgs.SnapshotTemporalSafetyError as e:
            skips.append({"game_id": game_id, "stage": "TEMPORAL_AUDIT", "reason": str(e)})
            continue
        snapshot_time_total += time.time() - t0

        t1 = time.time()
        batch = run_simulation_batch(snapshot, n_sims=n_sims)
        sim_time_total += time.time() - t1
        if batch.n_valid == 0:
            skips.append({"game_id": game_id, "stage": "SIMULATION", "reason": f"all faulted: {batch.fault_reasons[:3]}"})
            continue

        home_scores, away_scores = batch.home_scores, batch.away_scores
        margins = tuple(h - a for h, a in zip(home_scores, away_scores))
        home_wins = sum(1 for m in margins if m > 0)
        prediction = GamePrediction(
            game_id=game_id, date=snapshot.game_date, home_team=snapshot.home_team,
            away_team=snapshot.away_team, mode=mode,
            n_requested=batch.n_requested, n_valid=batch.n_valid, fault_reasons=batch.fault_reasons,
            predicted_home_win_prob=home_wins / batch.n_valid,
            predicted_away_win_prob=1.0 - home_wins / batch.n_valid,
            mean_sim_home_score=statistics.mean(home_scores),
            mean_sim_away_score=statistics.mean(away_scores),
            predicted_mean_margin=statistics.mean(margins),
            median_margin=statistics.median(margins),
            margin_percentiles=_percentiles(margins),
            home_score_percentiles=_percentiles(home_scores),
            away_score_percentiles=_percentiles(away_scores),
            margin_sd=statistics.pstdev(margins) if len(margins) > 1 else 0.0,
            snapshot_provenance=_summarize_snapshot_provenance(snapshot),
        )
        predictions.append(prediction)
        outcomes[game_id] = outcome
        for name, fn in BASELINES.items():
            baseline_preds[name][game_id] = fn(game_id, snapshot.home_team, snapshot.away_team, snapshot.game_date)
        if verbose:
            print(f"{game_id} {snapshot.home_team} vs {snapshot.away_team}: "
                  f"p_home={prediction.predicted_home_win_prob:.3f} actual_home_win={outcome.home_win}")

    total_time = time.time() - t_start
    n_attempted = len(game_ids)
    n_succeeded = len(predictions)
    timing = {
        "total_seconds": total_time,
        "snapshot_seconds_total": snapshot_time_total,
        "simulation_seconds_total": sim_time_total,
        "avg_seconds_per_successful_game": (total_time / n_succeeded) if n_succeeded else None,
    }
    return BacktestRunResult(
        mode=mode, n_attempted=n_attempted, n_succeeded=n_succeeded, skips=skips,
        predictions=predictions, outcomes=outcomes, baseline_predictions=baseline_preds, timing=timing,
    )


def compute_metrics_for_predictions(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    pairs = [(p.predicted_home_win_prob, 1 if outcomes[p.game_id].home_win else 0) for p in predictions]
    pred_margins = [p.predicted_mean_margin for p in predictions]
    actual_margins = [outcomes[p.game_id].margin for p in predictions]
    pred_home_scores = [p.mean_sim_home_score for p in predictions]
    actual_home_scores = [outcomes[p.game_id].home_score for p in predictions]
    pred_away_scores = [p.mean_sim_away_score for p in predictions]
    actual_away_scores = [outcomes[p.game_id].away_score for p in predictions]

    bins = calibration_table(pairs)
    home_mae = score_mae(pred_home_scores, actual_home_scores)
    away_mae = score_mae(pred_away_scores, actual_away_scores)
    combined_score_mae = (
        (sum(abs(p - a) for p, a in zip(pred_home_scores, actual_home_scores)) +
         sum(abs(p - a) for p, a in zip(pred_away_scores, actual_away_scores)))
        / (2 * len(predictions))
    ) if predictions else None

    return {
        "n_games": len(predictions),
        "brier_score": brier_score(pairs),
        "log_loss": log_loss(pairs),
        "winner_accuracy": winner_accuracy(pairs),
        "margin_mae": margin_mae(pred_margins, actual_margins),
        "margin_rmse": margin_rmse(pred_margins, actual_margins),
        "mean_signed_margin_error": mean_signed_margin_error(pred_margins, actual_margins),
        "home_score_mae": home_mae,
        "away_score_mae": away_mae,
        "combined_score_mae": combined_score_mae,
        "calibration_bins": bins,
        "expected_calibration_error": expected_calibration_error(bins, len(predictions)),
        "mean_predicted_home_prob": (sum(p for p, _ in pairs) / len(pairs)) if pairs else None,
        "actual_home_win_rate": (sum(y for _, y in pairs) / len(pairs)) if pairs else None,
    }


def compute_metrics_for_baseline(name: str, game_ids: List[str], baseline_preds: Dict[str, BaselinePrediction],
                                  outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    pairs = [(baseline_preds[gid].predicted_home_win_prob, 1 if outcomes[gid].home_win else 0) for gid in game_ids]
    pred_margins = [baseline_preds[gid].predicted_margin for gid in game_ids]
    actual_margins = [outcomes[gid].margin for gid in game_ids]
    return {
        "baseline": name,
        "n_games": len(game_ids),
        "brier_score": brier_score(pairs),
        "log_loss": log_loss(pairs),
        "winner_accuracy": winner_accuracy(pairs),
        "margin_mae": margin_mae(pred_margins, actual_margins) if all(m is not None for m in pred_margins) else None,
        "margin_rmse": margin_rmse(pred_margins, actual_margins) if all(m is not None for m in pred_margins) else None,
    }


# =====================================================================
# Diagnostic breakdowns (Z-report sections S-W)
# =====================================================================
def score_and_pace_bias(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    if not predictions:
        return {}
    avg_sim_home = statistics.mean(p.mean_sim_home_score for p in predictions)
    avg_sim_away = statistics.mean(p.mean_sim_away_score for p in predictions)
    avg_actual_home = statistics.mean(outcomes[p.game_id].home_score for p in predictions)
    avg_actual_away = statistics.mean(outcomes[p.game_id].away_score for p in predictions)
    return {
        "avg_simulated_home_score": avg_sim_home, "avg_actual_home_score": avg_actual_home,
        "avg_simulated_away_score": avg_sim_away, "avg_actual_away_score": avg_actual_away,
        "avg_simulated_total": avg_sim_home + avg_sim_away,
        "avg_actual_total": avg_actual_home + avg_actual_away,
        "signed_home_score_bias": avg_sim_home - avg_actual_home,
        "signed_away_score_bias": avg_sim_away - avg_actual_away,
        "signed_total_bias": (avg_sim_home + avg_sim_away) - (avg_actual_home + avg_actual_away),
    }


def margin_variance_bias(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    if not predictions:
        return {}
    pred_margins = [p.predicted_mean_margin for p in predictions]
    actual_margins = [outcomes[p.game_id].margin for p in predictions]
    avg_sim_margin_sd = statistics.mean(p.margin_sd for p in predictions)
    actual_margin_sd = statistics.pstdev(actual_margins) if len(actual_margins) > 1 else 0.0
    blowout_thresh = 20
    close_thresh = 5
    return {
        "mean_predicted_margin": statistics.mean(pred_margins),
        "mean_actual_margin": statistics.mean(actual_margins),
        "predicted_margin_sd_across_games": statistics.pstdev(pred_margins) if len(pred_margins) > 1 else 0.0,
        "actual_margin_sd_across_games": actual_margin_sd,
        "avg_within_game_simulated_margin_sd": avg_sim_margin_sd,
        "actual_blowout_rate (>=20pt)": sum(1 for m in actual_margins if abs(m) >= blowout_thresh) / len(actual_margins),
        "predicted_blowout_rate (>=20pt)": sum(1 for m in pred_margins if abs(m) >= blowout_thresh) / len(pred_margins),
        "actual_close_game_rate (<=5pt)": sum(1 for m in actual_margins if abs(m) <= close_thresh) / len(actual_margins),
        "predicted_close_game_rate (<=5pt)": sum(1 for m in pred_margins if abs(m) <= close_thresh) / len(pred_margins),
    }


def home_away_bias(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    pairs = [(p.predicted_home_win_prob, 1 if outcomes[p.game_id].home_win else 0) for p in predictions]
    return {
        "n_games": len(predictions),
        "mean_predicted_home_win_prob": (sum(p for p, _ in pairs) / len(pairs)) if pairs else None,
        "actual_home_win_rate": (sum(y for _, y in pairs) / len(pairs)) if pairs else None,
        "note": "the detailed engine models no explicit home-court effect (documented, unmodified "
                "this phase) -- a gap here between mean predicted home-win-prob and actual home-win "
                "rate is evidence FOR a missing home-court feature, not something this phase adds.",
    }


def team_level_bias(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> List[dict]:
    by_team: Dict[str, dict] = {}
    for p in predictions:
        outcome = outcomes[p.game_id]
        for team, is_home in ((p.home_team, True), (p.away_team, False)):
            row = by_team.setdefault(team, {"team": team, "games": 0, "wins": 0,
                                             "sum_pred_win_prob_for_team": 0.0,
                                             "sum_score_error": 0.0, "sum_margin_error": 0.0})
            row["games"] += 1
            team_pred_prob = p.predicted_home_win_prob if is_home else p.predicted_away_win_prob
            team_won = outcome.home_win if is_home else (not outcome.home_win)
            row["wins"] += 1 if team_won else 0
            row["sum_pred_win_prob_for_team"] += team_pred_prob
            pred_score = p.mean_sim_home_score if is_home else p.mean_sim_away_score
            actual_score = outcome.home_score if is_home else outcome.away_score
            row["sum_score_error"] += (pred_score - actual_score)
            team_margin_pred = p.predicted_mean_margin if is_home else -p.predicted_mean_margin
            team_margin_actual = outcome.margin if is_home else -outcome.margin
            row["sum_margin_error"] += (team_margin_pred - team_margin_actual)
    out = []
    for team, row in sorted(by_team.items()):
        g = row["games"]
        out.append({
            "team": team, "games": g,
            "predicted_win_prob_bias": row["sum_pred_win_prob_for_team"] / g - row["wins"] / g,
            "actual_win_rate": row["wins"] / g,
            "mean_predicted_win_prob": row["sum_pred_win_prob_for_team"] / g,
            "mean_signed_score_error": row["sum_score_error"] / g,
            "mean_signed_margin_error": row["sum_margin_error"] / g,
        })
    return out


def confidence_group_breakdown(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    groups = {"near_toss_up (50-60%)": [], "moderate_favorite (60-80%)": [], "strong_favorite (80-100%)": []}
    for p in predictions:
        conf = max(p.predicted_home_win_prob, 1 - p.predicted_home_win_prob)
        predicted_home = p.predicted_home_win_prob >= 0.5
        hit = predicted_home == outcomes[p.game_id].home_win
        if conf < 0.6:
            groups["near_toss_up (50-60%)"].append((conf, hit))
        elif conf < 0.8:
            groups["moderate_favorite (60-80%)"].append((conf, hit))
        else:
            groups["strong_favorite (80-100%)"].append((conf, hit))
    return {
        name: {
            "n_games": len(vals),
            "mean_confidence": (sum(c for c, _ in vals) / len(vals)) if vals else None,
            "actual_hit_rate": (sum(1 for _, h in vals if h) / len(vals)) if vals else None,
        }
        for name, vals in groups.items()
    }


def upset_analysis(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    """Games where the predicted favorite lost -- reported, never auto-labeled a model failure."""
    upsets = []
    for p in predictions:
        predicted_home = p.predicted_home_win_prob >= 0.5
        actual_home_won = outcomes[p.game_id].home_win
        if predicted_home != actual_home_won:
            conf = max(p.predicted_home_win_prob, 1 - p.predicted_home_win_prob)
            upsets.append({"game_id": p.game_id, "home_team": p.home_team, "away_team": p.away_team,
                            "predicted_home_win_prob": p.predicted_home_win_prob,
                            "predicted_favorite_confidence": conf,
                            "actual_winner": p.home_team if actual_home_won else p.away_team})
    confidences = [u["predicted_favorite_confidence"] for u in upsets]
    return {
        "n_upsets": len(upsets), "n_games": len(predictions),
        "upset_rate": (len(upsets) / len(predictions)) if predictions else None,
        "mean_favorite_confidence_in_upsets": (sum(confidences) / len(confidences)) if confidences else None,
        "upsets": upsets,
    }


def worst_and_best_games(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome], k: int = 10) -> dict:
    def log_loss_contribution(p: GamePrediction) -> float:
        y = 1 if outcomes[p.game_id].home_win else 0
        prob = min(max(p.predicted_home_win_prob, 1e-6), 1 - 1e-6)
        return -(y * math.log(prob) + (1 - y) * math.log(1 - prob))

    def row(p: GamePrediction) -> dict:
        o = outcomes[p.game_id]
        return {
            "game_id": p.game_id, "home_team": p.home_team, "away_team": p.away_team, "date": p.date,
            "predicted_home_win_prob": p.predicted_home_win_prob,
            "predicted_mean_margin": p.predicted_mean_margin,
            "actual_home_score": o.home_score, "actual_away_score": o.away_score, "actual_margin": o.margin,
            "margin_error": abs(p.predicted_mean_margin - o.margin),
            "log_loss_contribution": log_loss_contribution(p),
            "snapshot_provenance": p.snapshot_provenance,
        }

    by_margin_error = sorted(predictions, key=lambda p: abs(p.predicted_mean_margin - outcomes[p.game_id].margin), reverse=True)
    by_log_loss = sorted(predictions, key=log_loss_contribution, reverse=True)
    best_by_margin = sorted(predictions, key=lambda p: abs(p.predicted_mean_margin - outcomes[p.game_id].margin))
    return {
        "worst_by_margin_error": [row(p) for p in by_margin_error[:k]],
        "worst_by_log_loss": [row(p) for p in by_log_loss[:k]],
        "best_by_margin_error": [row(p) for p in best_by_margin[:k]],
    }


def provenance_vs_error(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    """Does more PRIOR_SEASON_ONLY/MISSING/synthetic-fallback provenance correlate with worse
    per-game margin error? Reported descriptively -- NOT inferred as causal from this sample size."""
    def staleness_fraction(p: GamePrediction) -> float:
        stale_labels = {"PRIOR_SEASON_ONLY", "MISSING"}
        total = 0
        stale = 0
        for group, counts in p.snapshot_provenance.items():
            for label, n in counts.items():
                total += n
                if label in stale_labels:
                    stale += n
        return (stale / total) if total else 0.0

    rows = []
    for p in predictions:
        frac = staleness_fraction(p)
        err = abs(p.predicted_mean_margin - outcomes[p.game_id].margin)
        rows.append((frac, err))
    if len(rows) < 3:
        return {"note": "sample too small for a meaningful correlation", "n_games": len(rows)}
    fracs = [r[0] for r in rows]
    errs = [r[1] for r in rows]
    mean_f, mean_e = sum(fracs) / len(fracs), sum(errs) / len(errs)
    cov = sum((f - mean_f) * (e - mean_e) for f, e in rows)
    var_f = sum((f - mean_f) ** 2 for f in fracs)
    var_e = sum((e - mean_e) ** 2 for e in errs)
    corr = (cov / math.sqrt(var_f * var_e)) if var_f > 0 and var_e > 0 else None
    return {
        "n_games": len(rows), "mean_stale_provenance_fraction": mean_f, "mean_margin_error": mean_e,
        "pearson_corr_staleness_vs_margin_error": corr,
        "caveat": "descriptive only -- sample size (n_games) is far too small to support a causal claim.",
    }


def rotation_overlap_and_minutes_concentration(game_ids: List[str], season: str, all_seasons: List[str],
                                                predictions_by_gid: Dict[str, GamePrediction],
                                                outcomes: Dict[str, hgo.GameOutcome]) -> List[dict]:
    """PREGAME expected primary five vs ORACLE (real target-game) primary five overlap, and real
    oracle-primary-five share of real total game minutes (240) -- how much real rotation exposure
    the engine's fixed-five constraint is structurally ignoring, and its relationship to error."""
    rows = []
    for game_id in game_ids:
        if game_id not in predictions_by_gid:
            continue
        try:
            pregame_snap = hgs.build_historical_game_snapshot(game_id, season, all_seasons, mode=hgs.MODE_PREGAME_EXPECTED)
            oracle_snap = hgs.build_historical_game_snapshot(game_id, season, all_seasons, mode=hgs.MODE_ORACLE_PARTICIPANTS)
        except Exception:
            continue
        overlaps = []
        oracle_minutes_fraction = []
        for pregame_team, oracle_team in ((pregame_snap.home_team_snapshot, oracle_snap.home_team_snapshot),
                                           (pregame_snap.away_team_snapshot, oracle_snap.away_team_snapshot)):
            overlap = len(set(pregame_team.primary_five) & set(oracle_team.primary_five))
            overlaps.append(overlap)
            oracle_five_minutes = sum(
                p.expected_minutes for p in oracle_team.players if p.is_primary_five
            )
            oracle_minutes_fraction.append(oracle_five_minutes / 240.0)
        pred = predictions_by_gid[game_id]
        outcome = outcomes.get(game_id)
        err = abs(pred.predicted_mean_margin - outcome.margin) if outcome else None
        rows.append({
            "game_id": game_id,
            "home_primary_five_overlap_pregame_vs_oracle": overlaps[0],
            "away_primary_five_overlap_pregame_vs_oracle": overlaps[1],
            "home_oracle_five_minutes_fraction_of_240": oracle_minutes_fraction[0],
            "away_oracle_five_minutes_fraction_of_240": oracle_minutes_fraction[1],
            "pregame_margin_error": err,
        })
    return rows


def _game_record(prediction: GamePrediction, outcome: hgo.GameOutcome) -> dict:
    return {
        "game_id": prediction.game_id, "date": prediction.date,
        "home_team": prediction.home_team, "away_team": prediction.away_team,
        "actual_home_score": outcome.home_score, "actual_away_score": outcome.away_score,
        "actual_winner": prediction.home_team if outcome.home_win else prediction.away_team,
        "actual_margin": outcome.margin,
        "predicted_home_win_prob": prediction.predicted_home_win_prob,
        "predicted_away_win_prob": prediction.predicted_away_win_prob,
        "mean_simulated_home_score": prediction.mean_sim_home_score,
        "mean_simulated_away_score": prediction.mean_sim_away_score,
        "predicted_mean_margin": prediction.predicted_mean_margin,
        "median_margin": prediction.median_margin,
        "margin_percentiles": prediction.margin_percentiles,
        "home_score_percentiles": prediction.home_score_percentiles,
        "away_score_percentiles": prediction.away_score_percentiles,
        "margin_sd": prediction.margin_sd,
        "simulation_count_valid": prediction.n_valid,
        "simulation_count_requested": prediction.n_requested,
        "fault_reasons": list(prediction.fault_reasons),
        "snapshot_mode": prediction.mode,
        "snapshot_provenance": prediction.snapshot_provenance,
    }


def serialize_raw_games(result: BacktestRunResult) -> dict:
    return {
        "mode": result.mode,
        "games": [_game_record(p, result.outcomes[p.game_id]) for p in result.predictions],
        "skips": result.skips,
    }


def serialize_summary(result: BacktestRunResult, metrics: dict, baseline_metrics: List[dict],
                       extra_sections: Optional[dict] = None) -> dict:
    return {
        "model_commit": _git_head_commit(),
        "model_version": MODEL_VERSION,
        "snapshot_mode": result.mode,
        "n_sims_per_game": N_SIMULATIONS,
        "seed_policy": "sha256(f'{MODEL_VERSION}|{mode}|{game_id}|{sim_index}')[:16 hex] as int",
        "holdout_selection_rule": f"sha256(game_id) % {HOLDOUT_SELECTION_MOD} == 0, season {BACKTEST_SEASON}",
        "n_attempted": result.n_attempted,
        "n_succeeded": result.n_succeeded,
        "n_skipped": len(result.skips),
        "game_ids": sorted(result.outcomes.keys()),
        "timing": result.timing,
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
        "extra_sections": extra_sections or {},
    }


def _git_head_commit() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "UNKNOWN"


def period_breakdown(predictions: List[GamePrediction], outcomes: Dict[str, hgo.GameOutcome]) -> dict:
    """Opening month / midseason / late season split -- PRIOR_SEASON_ONLY truth may be relatively
    staler later in a season (further from its own as-of evidence for CURRENT_SEASON_PREGAME
    tracks, though PRIOR_SEASON_ONLY tracks' OWN staleness relative to the target date doesn't
    change within a season -- what changes is how much CURRENT_SEASON_PREGAME evidence has
    accumulated by then)."""
    def period_of(date: str) -> str:
        if date < "2023-12-01":
            return "opening (Oct-Nov)"
        if date < "2024-02-15":
            return "midseason (Dec-mid Feb)"
        return "late season (mid Feb-Apr)"

    groups: Dict[str, List[GamePrediction]] = {}
    for p in predictions:
        groups.setdefault(period_of(p.date), []).append(p)
    return {
        period: compute_metrics_for_predictions(preds, outcomes)
        for period, preds in sorted(groups.items())
    }


def clear_backtest_caches() -> None:
    """Central test/debug reset hook for this whole evaluation layer -- clears every snapshot-
    layer cache (`historical_game_snapshot.clear_estimator_caches()`) plus this phase's own new
    caches (`historical_game_outcome`'s per-season game-log index, `player_team_stints`'s per-
    player stint cache, and this module's own per-season outcome/net-rating caches). Required for
    poison/leakage tests; assumes on-disk cache files are static for the life of the process
    otherwise."""
    hgs.clear_estimator_caches()
    hgo.clear_reference_caches()
    pts.clear_reference_caches()
    _all_game_outcomes.cache_clear()
    _prior_season_league_home_win_rate.cache_clear()
    _prior_season_avg_margin.cache_clear()
    _prior_season_team_avg_diff.cache_clear()
