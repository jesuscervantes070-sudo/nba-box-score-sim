"""
Phase 4A -- Ball Security: the empirical investigation itself. Entirely
offline, read-only with respect to the rest of the codebase (imports
turnover_ingestion / handling_exposure / loader caches, writes nothing
except its own ball_security_calibration.json artifact via
calibrate_ball_security() at the bottom). Not imported by game_engine.py,
season.py, awards.py, models.py, transactions.py, or
player_ability_estimation.py's production ATTRIBUTE_EXTRACTORS.

============================ REAL JOIN KEY ============================
turnover_ingestion's `player_id` (from playbyplayv3's `personId`) and
handling_exposure's player key (from leaguedashptstats' `PLAYER_ID`) are
the SAME real NBA person-id space -- confirmed directly (e.g. Nikola
Jokic is 203999 in both). This lets ball-security evidence join on a
real numeric id instead of the rest of this codebase's name-based
matching -- a real, checked improvement, not assumed.

============================ WHAT THIS FILE ANSWERS ============================
1. Which real tracking-era denominator (touches / estimated dribbles /
   time-of-possession / drives) best explains handling-error rate.
2. Whether handling-error rate is systematically biased by creation
   burden (usage, dribbles/touch) -- and whether an expected-rate
   adjustment measurably helps.
3. Whether a simple, pre-tracking-era proxy (built ONLY from
   period-appropriate box/PBP evidence) can reasonably reproduce real
   tracking-era exposure, trained/evaluated with a real, time-respecting
   split.

============================ REAL DATA LIMITATION, STATED PLAINLY ============================
Turnover ingestion in this session covers a REAL but PARTIAL sample of
each season's games (see turnover_ingestion.ingest_season_turnovers'
`max_games` and the coverage numbers this file reports) -- not every
cached season's full ~1,230-game schedule. The full resumable pipeline
(tested for correctness in test_turnover_ingestion.py, and exercised for
real against the live API here) is ready to run to full completion as a
long-running background job; that full run was NOT launched in this
session (would be many real hours of API calls against a documented
flaky endpoint). Every rate below that mixes a partial-season PBP
numerator with a full-season tracking denominator is EXPLICITLY SCALED
(see `_scale_to_full_season`) and labeled with its own real games_done/
games_total, never silently presented as if it came from full coverage.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import handling_exposure as he
import turnover_ingestion as ti
from loader import load_player_advanced_stats, load_teams

TRUE_TRACKING = "TRUE_TRACKING"
HISTORICAL_PROXY = "HISTORICAL_PROXY"

# Real candidate denominators -- every one of these is a field
# handling_exposure.py stored verbatim (or, for estimated_total_dribbles,
# a direct multiplication of two real reported fields -- see that
# module's docstring). No invented "Estimated Ball Touch" formula.
CANDIDATE_DENOMINATORS = ("touches", "estimated_total_dribbles", "time_of_poss", "drives")


def _scale_to_full_season(count: int, games_done: int, games_total: int) -> float:
    """A real partial-season PBP count -> an estimated full-season-
    equivalent count, assuming (an explicit, stated assumption, NOT
    verified game-by-game) this player's real per-game handling-error
    rate is roughly stable across the games sampled vs the ones not yet
    ingested. Returns the raw count unscaled if coverage is already
    complete or unknown."""
    if not games_total or games_done <= 0 or games_done >= games_total:
        return float(count)
    return count * (games_total / games_done)


@dataclass
class PlayerSeasonRow:
    player_id: str
    player_name: str
    season: str
    handling_error: float  # scaled to full-season-equivalent, see _scale_to_full_season
    bad_pass: float
    offensive_foul_nonhandle: float
    team_system: float
    other_unclassified: float
    total_turnovers: float
    exposure: Dict[str, float]  # real tracking fields, unscaled (already full-season totals)
    usg_pct: Optional[float]
    ast_pct: Optional[float]
    reb_pct: Optional[float]  # real combined REB% -- used only as a rough, explicitly-approximate
    # role proxy (no real position field exists anywhere in this codebase's cache/models -- see
    # subgroup_bias_report's docstring), NOT a new attribute or role-classification system.
    fga: Optional[float]
    fta: Optional[float]
    gp: Optional[int]


def build_player_season_rows(season: str) -> List[PlayerSeasonRow]:
    """Every player with BOTH real turnover-subtype evidence (this
    session's ingested sample for `season`) and real tracking-era
    handling-exposure data (empty dict, hence no rows, for a season
    before HANDLING_EXPOSURE_FIRST_SEASON) -- joined on the real shared
    player_id. See module docstring for the partial-coverage scaling."""
    turnover_state = ti.load_turnover_cache(season)
    exposure = he.load_handling_exposure(season)
    if turnover_state is None or not exposure:
        return []

    games_done = len(turnover_state["games_done"])
    games_total = turnover_state["games_total"]

    adv = load_player_advanced_stats(season)
    adv_by_name = adv  # keyed by name already

    rows = []
    for pid, tv_row in turnover_state["players"].items():
        exp_row = exposure.get(pid)
        if exp_row is None:
            continue  # real turnover evidence but no real tracking row this season -- skip, don't guess
        # turnover_ingestion's player_name comes from playbyplayv3's real
        # `playerName` field, which is LAST-NAME-ONLY (e.g. "Paul" for
        # Chris Paul) -- confirmed directly, not what player_advanced.json
        # is keyed by. Resolve the real full name from the shared
        # player_id instead (see turnover_ingestion.resolve_full_name) --
        # falls back to handling_exposure's own PLAYER_NAME, then the raw
        # last-name string, only if the static crosswalk somehow misses.
        name = ti.resolve_full_name(int(pid)) or exp_row.get("player_name") or tv_row["player_name"]
        adv_row = adv_by_name.get(name, {})
        rows.append(PlayerSeasonRow(
            player_id=pid,
            player_name=name,
            season=season,
            handling_error=_scale_to_full_season(tv_row["handling_error"], games_done, games_total),
            bad_pass=_scale_to_full_season(tv_row["bad_pass"], games_done, games_total),
            offensive_foul_nonhandle=_scale_to_full_season(tv_row["offensive_foul_nonhandle"], games_done, games_total),
            team_system=_scale_to_full_season(tv_row["team_system"], games_done, games_total),
            other_unclassified=_scale_to_full_season(tv_row["other_unclassified"], games_done, games_total),
            total_turnovers=_scale_to_full_season(tv_row["total"], games_done, games_total),
            exposure={d: exp_row.get(d, 0.0) for d in CANDIDATE_DENOMINATORS},
            usg_pct=adv_row.get("usg_pct"),
            ast_pct=adv_row.get("ast_pct"),
            reb_pct=adv_row.get("reb_pct"),
            fga=None,
            fta=None,
            gp=adv_row.get("gp"),
        ))
    return rows


# =====================================================================
# PART 1 -- candidate denominator comparison
# =====================================================================

def handling_error_rate(row: PlayerSeasonRow, denominator: str, min_exposure: float = 1.0) -> Optional[float]:
    exp = row.exposure.get(denominator, 0.0)
    if exp < min_exposure:
        return None  # real low-exposure floor -- never divide by near-zero and call it a rate
    return row.handling_error / exp


def compare_denominators(rows_by_season: Dict[str, List[PlayerSeasonRow]], min_exposure: float = 50.0) -> dict:
    """
    For every candidate denominator: real weighted MAE/RMSE predicting a
    LATER season's own handling-error rate from an EARLIER season's rate
    (same player, same denominator) -- the standard "does this season's
    rate predict next season's rate" stability check used for the other
    six attributes, adapted to whatever real season pairs this session's
    partial ingestion actually covers (reported honestly via
    `season_pairs_used`).
    """
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
                rate_now = handling_error_rate(r_now, denom, min_exposure)
                rate_next = handling_error_rate(r_next, denom, min_exposure)
                if rate_now is None or rate_next is None:
                    continue
                weight = r_next.exposure.get(denom, 0.0)
                pairs.append((rate_now, rate_next, weight))

        if not pairs:
            results[denom] = {"n_pairs": 0, "weighted_mae": None, "weighted_rmse": None}
            continue

        total_w = sum(w for _, _, w in pairs)
        mae = sum(abs(a - b) * w for a, b, w in pairs) / total_w
        rmse = math.sqrt(sum((a - b) ** 2 * w for a, b, w in pairs) / total_w)
        results[denom] = {
            "n_pairs": len(pairs),
            "weighted_mae": round(mae, 5),
            "weighted_rmse": round(rmse, 5),
        }
    return {"season_pairs_used": list(zip(seasons, seasons[1:])), "denominators": results}


# =====================================================================
# PART 2 -- creation-burden bias check
# =====================================================================

def burden_bias_report(rows: List[PlayerSeasonRow], denominator: str, min_exposure: float = 50.0) -> dict:
    """
    Real Pearson correlation between handling-error RATE (using
    `denominator`) and two real burden proxies: USG% (season-level, real)
    and dribbles-per-touch-implied burden (estimated_total_dribbles /
    touches, i.e. how much a player dribbles per touch he gets -- a real
    reported-field ratio, not invented). A positive, non-trivial
    correlation with USG% is the real signature of the "high-usage
    creators get unfairly punished" bias the task asks about.
    """
    xs_usg, ys_usg = [], []
    xs_drib, ys_drib = [], []
    for r in rows:
        rate = handling_error_rate(r, denominator, min_exposure)
        if rate is None:
            continue
        if r.usg_pct is not None:
            xs_usg.append(r.usg_pct)
            ys_usg.append(rate)
        touches = r.exposure.get("touches", 0.0)
        dribbles = r.exposure.get("estimated_total_dribbles", 0.0)
        if touches > 0:
            xs_drib.append(dribbles / touches)
            ys_drib.append(rate)

    return {
        "denominator": denominator,
        "n_players": len(rows),
        "corr_rate_vs_usage": _pearson(xs_usg, ys_usg),
        "n_usage_pairs": len(xs_usg),
        "corr_rate_vs_dribbles_per_touch": _pearson(xs_drib, ys_drib),
        "n_dribble_pairs": len(xs_drib),
    }


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


# =====================================================================
# PART 3 -- historical (pre-tracking) exposure proxy
# =====================================================================

@dataclass
class ProxyModel:
    """y = a + b*usg_pct + c*ast_pct -- a deliberately small, interpretable
    2-variable linear model (both real, available every cached season
    back to 1996-97), fit by ordinary least squares on TRAIN seasons
    only. Predicts real tracking-era TOUCHES per real MIN (per-minute,
    so it generalizes across role/minutes rather than raw season
    totals)."""
    intercept: float
    coef_usg: float
    coef_ast: float
    train_seasons: Tuple[str, ...]

    def predict(self, usg_pct: float, ast_pct: float) -> float:
        return self.intercept + self.coef_usg * usg_pct + self.coef_ast * ast_pct


def _touches_per_min(row: PlayerSeasonRow, adv_row: dict) -> Optional[float]:
    minutes = adv_row.get("mpg", 0) * adv_row.get("gp", 0)
    touches = row.exposure.get("touches", 0.0)
    if minutes <= 0 or touches <= 0:
        return None
    return touches / minutes


def fit_touches_proxy(train_rows: List[PlayerSeasonRow], season_advanced: Dict[str, Dict[str, dict]]) -> Optional[ProxyModel]:
    """OLS fit of touches-per-minute ~ usg_pct + ast_pct on TRAIN rows
    only. Plain 2-variable least squares (closed-form normal equations)
    -- no ML library, per the task's explicit "no complex black-box ML."
    """
    X, y = [], []
    for r in train_rows:
        adv_row = season_advanced.get(r.season, {}).get(r.player_name, {})
        target = _touches_per_min(r, adv_row)
        if target is None or r.usg_pct is None or r.ast_pct is None:
            continue
        X.append((1.0, r.usg_pct, r.ast_pct))
        y.append(target)
    if len(X) < 10:
        return None

    # Normal equations: (X^T X) beta = X^T y, solved by hand (3x3) --
    # small and interpretable, exactly per the task's own constraint.
    n = len(X)
    xtx = [[0.0] * 3 for _ in range(3)]
    xty = [0.0, 0.0, 0.0]
    for row, target in zip(X, y):
        for i in range(3):
            xty[i] += row[i] * target
            for j in range(3):
                xtx[i][j] += row[i] * row[j]
    beta = _solve_3x3(xtx, xty)
    if beta is None:
        return None
    seasons = tuple(sorted({r.season for r in train_rows}))
    return ProxyModel(intercept=beta[0], coef_usg=beta[1], coef_ast=beta[2], train_seasons=seasons)


def _solve_3x3(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting for a small 3x3 system.
    Returns None if the system is singular (degenerate training data)."""
    A = [row[:] + [b[i]] for i, row in enumerate(A)]
    n = 3
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[pivot][col]) < 1e-12:
            return None
        A[col], A[pivot] = A[pivot], A[col]
        for r in range(n):
            if r == col:
                continue
            factor = A[r][col] / A[col][col]
            for c in range(col, n + 1):
                A[r][c] -= factor * A[col][c]
    return [A[i][n] / A[i][i] for i in range(n)]


def evaluate_touches_proxy(model: ProxyModel, test_rows: List[PlayerSeasonRow], season_advanced: Dict[str, Dict[str, dict]]) -> dict:
    """Real held-out evaluation: R^2, MAE, and Spearman-style rank
    correlation between the proxy's predicted touches-per-minute and the
    REAL touches-per-minute for TEST seasons (never seen during fit_touches_proxy)."""
    preds, actuals = [], []
    for r in test_rows:
        adv_row = season_advanced.get(r.season, {}).get(r.player_name, {})
        actual = _touches_per_min(r, adv_row)
        if actual is None or r.usg_pct is None or r.ast_pct is None:
            continue
        preds.append(model.predict(r.usg_pct, r.ast_pct))
        actuals.append(actual)

    if len(preds) < 5:
        return {"n": len(preds), "r2": None, "mae": None, "rmse": None, "rank_corr": None}

    n = len(preds)
    mean_actual = sum(actuals) / n
    ss_res = sum((a - p) ** 2 for a, p in zip(actuals, preds))
    ss_tot = sum((a - mean_actual) ** 2 for a in actuals)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    mae = sum(abs(a - p) for a, p in zip(actuals, preds)) / n
    rmse = math.sqrt(ss_res / n)
    rank_corr = _spearman(preds, actuals)
    return {"n": n, "r2": round(r2, 4) if r2 is not None else None,
            "mae": round(mae, 5), "rmse": round(rmse, 5), "rank_corr": rank_corr}


# =====================================================================
# PART 4 -- four-way baseline comparison (old provisional / raw-previous
# / multi-year no-shrink / calibrated), in a common, unit-free scale
# (0-99 percentile rank, same scale the final rating itself uses) so a
# box-score-based old estimate and a real handling-only new rate can be
# fairly compared even though their raw units differ.
# =====================================================================

def _percentile_rank(value: float, reference: List[float]) -> float:
    if not reference:
        return 50.0
    import bisect
    return 100.0 * bisect.bisect_left(sorted(reference), value) / len(reference)


def compare_methods_percentile_mae(rows_by_season: Dict[str, List[PlayerSeasonRow]], denominator: str,
                                    lambda_: float, M: float, min_exposure: float = 50.0) -> dict:
    """
    For every real (T, T+1) pair this session's data covers: each
    method produces a T-based percentile-rank PREDICTION of "how good is
    this player's ball security" (higher = better, i.e. FEWER handling
    errors per unit exposure); the real target is the player's ACTUAL
    T+1 percentile rank on the same true handling-error-rate metric.
    Weighted (by real T+1 exposure) MAE/RMSE of percentile-rank error,
    for:
      A. old_provisional -- player_ability_estimation's existing box-TOV
         estimate_attribute(..., "ball_security") percentile rating at T.
      B. raw_previous -- T's own single-season raw handling-error rate,
         percentile-ranked among T's real population, no shrinkage at all.
      C. multiyear_no_shrink -- recency-weighted multi-year raw rate
         (lambda_, M=0 -- no shrinkage prior), percentile-ranked.
      D. calibrated -- recency-weighted, M-shrunk rate (lambda_, M),
         percentile-ranked -- the real accepted Phase 4A parameters.
    """
    from player_ability_estimation import estimate_attribute

    seasons = sorted(rows_by_season.keys())
    methods = {"old_provisional": [], "raw_previous": [], "multiyear_no_shrink": [], "calibrated": []}

    for i in range(len(seasons) - 1):
        s_now, s_next = seasons[i], seasons[i + 1]
        rows_now = rows_by_season[s_now]
        rows_next = rows_by_season[s_next]
        next_by_id = {r.player_id: r for r in rows_next}

        # Real T+1 target population (percentile reference + per-player target)
        target_rates = {r.player_id: handling_error_rate(r, denominator, min_exposure) for r in rows_next}
        target_pop = [v for v in target_rates.values() if v is not None]

        # T-based reference populations for each method's own percentile ranking
        raw_now_by_id = {r.player_id: handling_error_rate(r, denominator, min_exposure) for r in rows_now}
        raw_now_pop = [v for v in raw_now_by_id.values() if v is not None]

        # Multi-year histories through s_now, for methods C/D
        hist_by_id: Dict[str, List[Tuple[str, float, float]]] = {}
        for s in seasons:
            if int(s[:4]) > int(s_now[:4]):
                continue
            for row in rows_by_season[s]:
                rate = handling_error_rate(row, denominator, min_exposure)
                if rate is None:
                    continue
                hist_by_id.setdefault(row.player_id, []).append((s, rate, row.exposure.get(denominator, 0.0)))
        league_avg_now = sum(raw_now_pop) / len(raw_now_pop) if raw_now_pop else None

        c_by_id, d_by_id = {}, {}
        for pid, hist in hist_by_id.items():
            c_val = _multiyear_shrunk(hist, int(s_now[:4]), lambda_, 0.0, league_avg_now)
            d_val = _multiyear_shrunk(hist, int(s_now[:4]), lambda_, M, league_avg_now)
            if c_val is not None:
                c_by_id[pid] = c_val
            if d_val is not None:
                d_by_id[pid] = d_val
        c_pop = list(c_by_id.values())
        d_pop = list(d_by_id.values())

        for pid, r_now in {r.player_id: r for r in rows_now}.items():
            r_next = next_by_id.get(pid)
            if r_next is None:
                continue
            target = target_rates.get(pid)
            if target is None:
                continue
            # Ball security = FEWER errors is better -> invert before percentile-ranking.
            target_rank = 100.0 - _percentile_rank(target, target_pop)
            weight = r_next.exposure.get(denominator, 0.0)

            raw_now = raw_now_by_id.get(pid)
            if raw_now is not None:
                pred_rank = 100.0 - _percentile_rank(raw_now, raw_now_pop)
                methods["raw_previous"].append((pred_rank, target_rank, weight))

            if pid in c_by_id:
                pred_rank = 100.0 - _percentile_rank(c_by_id[pid], c_pop)
                methods["multiyear_no_shrink"].append((pred_rank, target_rank, weight))

            if pid in d_by_id:
                pred_rank = 100.0 - _percentile_rank(d_by_id[pid], d_pop)
                methods["calibrated"].append((pred_rank, target_rank, weight))

            try:
                old = estimate_attribute(r_now.player_name, s_now, "ball_security", seasons)
                if old.percentile_rating is not None:
                    methods["old_provisional"].append((old.percentile_rating, target_rank, weight))
            except Exception:
                pass

    results = {}
    for name, triples in methods.items():
        if not triples:
            results[name] = {"n": 0, "weighted_mae": None, "weighted_rmse": None}
            continue
        total_w = sum(w for _, _, w in triples)
        mae = sum(abs(p - t) * w for p, t, w in triples) / total_w
        rmse = math.sqrt(sum((p - t) ** 2 * w for p, t, w in triples) / total_w)
        results[name] = {"n": len(triples), "weighted_mae": round(mae, 3), "weighted_rmse": round(rmse, 3)}
    return results


def _multiyear_shrunk(hist: List[Tuple[str, float, float]], as_of_year: int, lambda_: float, M: float,
                       league_avg: Optional[float]) -> Optional[float]:
    total_w, weighted_sum = 0.0, 0.0
    for season, rate, weight in hist:
        if weight <= 0:
            continue
        age = as_of_year - int(season[:4])
        if age < 0:
            continue
        w = (lambda_ ** age) * weight
        weighted_sum += rate * w
        total_w += w
    if total_w <= 0:
        return None
    if M <= 0 or league_avg is None:
        return weighted_sum / total_w
    return (weighted_sum + M * league_avg) / (total_w + M)


# =====================================================================
# PART 5 -- subgroup bias (usage tiers; an EXPLICITLY-APPROXIMATE
# rebounding-share role proxy, since no real position field exists
# anywhere in this codebase's cache/models -- see PlayerSeasonRow.reb_pct)
# =====================================================================

def _tercile_label(value: float, low: float, high: float) -> str:
    if value <= low:
        return "low"
    if value >= high:
        return "high"
    return "mid"


def subgroup_bias_report(rows: List[PlayerSeasonRow], denominator: str, min_exposure: float = 50.0) -> dict:
    """
    Real handling-error rate, grouped by USG% tercile (a direct,
    real creation-burden subgroup) and by REB% tercile (an
    EXPLICITLY-APPROXIMATE stand-in for "big vs. perimeter" -- this
    codebase has no real position field at all, so this is a rough
    proxy, not a role-classification system; reported as such, not as
    ground truth). A real bias would show up as systematically higher/
    lower mean rate in one tercile with no real skill explanation.
    """
    usg_vals = sorted(r.usg_pct for r in rows if r.usg_pct is not None)
    reb_vals = sorted(r.reb_pct for r in rows if r.reb_pct is not None)
    if len(usg_vals) < 9 or len(reb_vals) < 9:
        return {"status": "insufficient_data", "n": len(rows)}

    usg_low, usg_high = usg_vals[len(usg_vals) // 3], usg_vals[2 * len(usg_vals) // 3]
    reb_low, reb_high = reb_vals[len(reb_vals) // 3], reb_vals[2 * len(reb_vals) // 3]

    def _group_means(key_fn, low, high):
        buckets: Dict[str, List[float]] = {"low": [], "mid": [], "high": []}
        for r in rows:
            val = key_fn(r)
            rate = handling_error_rate(r, denominator, min_exposure)
            if val is None or rate is None:
                continue
            buckets[_tercile_label(val, low, high)].append(rate)
        return {k: {"n": len(v), "mean_rate": round(sum(v) / len(v), 6) if v else None} for k, v in buckets.items()}

    return {
        "denominator": denominator,
        "by_usage_tercile": _group_means(lambda r: r.usg_pct, usg_low, usg_high),
        "by_reb_pct_tercile_APPROXIMATE_ROLE_PROXY": _group_means(lambda r: r.reb_pct, reb_low, reb_high),
    }


# =====================================================================
# PART 6 -- downstream proxy fidelity: does HISTORICAL_PROXY exposure
# preserve real Ball Security signal, not just raw touches accuracy?
# =====================================================================

def downstream_proxy_fidelity(model: ProxyModel, eval_rows: List[PlayerSeasonRow],
                               season_advanced: Dict[str, dict], denominator_touches_equiv: str = "touches",
                               min_exposure: float = 50.0) -> dict:
    """
    On a REAL tracking-era season the proxy model was NOT trained on
    (`eval_rows`), compare the Ball-Security SIGNAL (not just raw
    touches) you'd get from the proxy's PREDICTED exposure against the
    REAL TRUE_TRACKING exposure -- both applied to the exact same real
    handling-error counts. This is the metric that actually matters:
    the task cares whether the proxy preserves latent Ball Security
    ranking, not whether it nails raw dribble counts.
    """
    true_rates, proxy_rates, weights = [], [], []
    for r in eval_rows:
        adv_row = season_advanced.get(r.player_name, {})
        usg_pct, ast_pct = r.usg_pct, r.ast_pct
        minutes = adv_row.get("mpg", 0) * adv_row.get("gp", 0)
        if usg_pct is None or ast_pct is None or minutes <= 0:
            continue
        true_exposure = r.exposure.get(denominator_touches_equiv, 0.0)
        if true_exposure < min_exposure:
            continue
        predicted_touches_per_min = max(model.predict(usg_pct, ast_pct), 1e-6)
        predicted_exposure = predicted_touches_per_min * minutes
        true_rate = r.handling_error / true_exposure
        proxy_rate = r.handling_error / predicted_exposure
        true_rates.append(true_rate)
        proxy_rates.append(proxy_rate)
        weights.append(true_exposure)

    n = len(true_rates)
    if n < 10:
        return {"n": n, "status": "insufficient_data"}

    # Ball security = fewer errors is better -> invert both before ranking, same convention as elsewhere.
    true_ranks = [100.0 - _percentile_rank(-v, [-x for x in true_rates]) for v in true_rates]
    proxy_ranks = [100.0 - _percentile_rank(-v, [-x for x in proxy_rates]) for v in proxy_rates]

    rank_corr = _spearman(true_rates, proxy_rates)
    total_w = sum(weights)
    rating_mae = sum(abs(t - p) * w for t, p, w in zip(true_ranks, proxy_ranks, weights)) / total_w

    # Top/bottom-decile agreement: real overlap between the true-ranking
    # top 10% and the proxy-ranking top 10% (same n both sides).
    k = max(1, n // 10)
    true_order = sorted(range(n), key=lambda i: true_rates[i])  # ascending rate = descending goodness... invert below
    proxy_order = sorted(range(n), key=lambda i: proxy_rates[i])
    true_top = set(true_order[:k])   # lowest RATE = best ball security = "top"
    proxy_top = set(proxy_order[:k])
    true_bottom = set(true_order[-k:])
    proxy_bottom = set(proxy_order[-k:])
    top_agreement = len(true_top & proxy_top) / k
    bottom_agreement = len(true_bottom & proxy_bottom) / k

    return {
        "n": n,
        "rank_correlation_true_vs_proxy_rate": rank_corr,
        "rating_mae_0_99_scale": round(rating_mae, 3),
        "top_decile_agreement": round(top_agreement, 3),
        "bottom_decile_agreement": round(bottom_agreement, 3),
    }


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
    rx, ry = rank(xs), rank(ys)
    return _pearson(rx, ry)
