"""
Phase 4A -- Ball Security: the final, diagnostic estimator built from real
turnover-subtype evidence + real tracking-era exposure (or, pre-2013-14, a
validated linear proxy for that exposure). PARALLEL to
player_ability_estimation.py -- NOT wired into its ATTRIBUTE_EXTRACTORS /
ball_security slot in this phase. That module's `_extract_ball_security`
(the box-score TOV-rate proxy) is left completely untouched; this file is
an independent, standalone diagnostic estimator callable on its own, exactly
like player_ability_turnover_prototype.py was for the smaller investigation
before it.

WHY NOT WIRE IT IN YET, stated plainly: this session's real ingestion is a
partial sample of each season (see turnover_ingestion.py's own docstring
and ball_security_analysis.py's stated coverage limitation), not the full
historical corpus the production attribute would need. The task's own
instructions are explicit here: "If the proxy is weak... use lower
confidence... or explicitly separate proxy mode," and "Do NOT force LOCK
V1." Report classification (see docs/PHASE4A_BALL_SECURITY_REPORT.md)
reflects this honestly.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import ball_security_analysis as bsa
import ball_security_calibration as bsc
from loader import load_player_advanced_stats, load_teams
from turnover_ingestion import load_turnover_cache, resolve_full_name
from handling_exposure import load_handling_exposure, HANDLING_EXPOSURE_FIRST_SEASON


def _find_turnover_row_by_full_name(turnover_state: dict, player_name: str) -> Optional[dict]:
    """turnover_state['players'] is keyed by player_id with a LAST-NAME-
    ONLY `player_name` field (see ball_security_analysis.py's docstring
    on why) -- resolve each id's real full name via the static id->name
    crosswalk before comparing, rather than matching on the raw
    last-name string (which would silently mismatch or collide)."""
    for row in turnover_state["players"].values():
        if resolve_full_name(row["player_id"]) == player_name:
            return row
    return None

RATING_MIN, RATING_MAX = 0.0, 99.0

# Provisional fallback if ball_security_calibration.json is missing or
# INSUFFICIENT_EVIDENCE -- deliberately conservative (heavier shrinkage
# than the grid search's own candidates), same role as
# player_ability_estimation.SHRINKAGE_PRIOR_STRENGTH's per-attribute
# defaults. NEVER presented as "calibrated."
PROVISIONAL_LAMBDA = 0.5
PROVISIONAL_M = 400.0
DEFAULT_DENOMINATOR = "touches"


@dataclass
class BallSecurityReport:
    player_name: str
    season: str
    mode: str  # TRUE_TRACKING or HISTORICAL_PROXY
    total_turnovers: Optional[float] = None
    lost_ball_and_handling: Optional[float] = None  # handling_error category total
    bad_pass: Optional[float] = None
    offensive_foul_nonhandle: Optional[float] = None
    team_system: Optional[float] = None
    unknown: Optional[float] = None
    exposure: Optional[float] = None
    exposure_denominator: str = DEFAULT_DENOMINATOR
    raw_rate: Optional[float] = None  # handling errors per unit exposure (lower = worse, NOT yet inverted)
    shrunk_rate: Optional[float] = None
    rating_0_99: Optional[float] = None
    confidence: str = "none"  # "high" (TRUE_TRACKING, calibrated, ample sample), "medium", "low"
    param_source: str = "provisional"
    proxy_r2: Optional[float] = None  # only set when mode == HISTORICAL_PROXY
    old_provisional_rating: Optional[float] = None
    coverage_note: str = ""


def _resolve_params():
    calibrated = bsc.get_ball_security_params()
    if calibrated is not None and calibrated.status != "INSUFFICIENT_EVIDENCE":
        return calibrated.lambda_, calibrated.M, calibrated.denominator, "calibrated"
    return PROVISIONAL_LAMBDA, PROVISIONAL_M, DEFAULT_DENOMINATOR, "provisional"


def _league_avg_rate(rows: List["bsa.PlayerSeasonRow"], denominator: str, min_exposure: float = 50.0) -> Optional[float]:
    rates = [r for r in (bsa.handling_error_rate(row, denominator, min_exposure) for row in rows) if r is not None]
    return sum(rates) / len(rates) if rates else None


def _percentile(value: float, reference: List[float]) -> float:
    if not reference:
        return 50.0
    sorted_ref = sorted(reference)
    import bisect
    rank = bisect.bisect_left(sorted_ref, value)
    return round(max(RATING_MIN, min(RATING_MAX, 100.0 * rank / len(sorted_ref))), 1)


def estimate_ball_security(player_name: str, as_of_season: str, all_seasons: List[str],
                            min_exposure: float = 50.0) -> BallSecurityReport:
    """
    Real, offline diagnostic estimate for one player-season. Prefers
    TRUE_TRACKING (real handling_exposure + turnover-subtype evidence
    jointly available for `as_of_season`, i.e. as_of_season >=
    HANDLING_EXPOSURE_FIRST_SEASON and both caches exist) and falls back
    to HISTORICAL_PROXY (predicted exposure from a proxy model fit on
    tracking-era seasons) otherwise. Missing evidence stays missing
    (None fields), never a silent 0.0/50.0 default beyond the neutral
    percentile fallback `_percentile` already documents.
    """
    lam, M, denominator, param_source = _resolve_params()
    report = BallSecurityReport(player_name=player_name, season=as_of_season, mode="", exposure_denominator=denominator)

    turnover_state = load_turnover_cache(as_of_season)
    exposure_map = load_handling_exposure(as_of_season) if as_of_season >= HANDLING_EXPOSURE_FIRST_SEASON else {}
    true_tracking_available = bool(exposure_map) and turnover_state is not None

    if true_tracking_available:
        rows = bsa.build_player_season_rows(as_of_season)
        report.mode = bsa.TRUE_TRACKING
        player_row = next((r for r in rows if r.player_name == player_name), None)
        if player_row is None:
            report.coverage_note = "No joined real turnover+tracking evidence for this player-season (may be outside this session's partial ingestion sample)."
            return report
        exposure_val = player_row.exposure.get(denominator, 0.0)
        report.total_turnovers = player_row.total_turnovers
        report.lost_ball_and_handling = player_row.handling_error
        report.bad_pass = player_row.bad_pass
        report.offensive_foul_nonhandle = player_row.offensive_foul_nonhandle
        report.team_system = player_row.team_system
        report.unknown = player_row.other_unclassified
        report.exposure = exposure_val
        games_done = len(turnover_state["games_done"])
        games_total = turnover_state["games_total"]
        report.coverage_note = f"turnover ingestion coverage this session: {games_done}/{games_total} games"

        raw_rate = bsa.handling_error_rate(player_row, denominator, min_exposure)
        if raw_rate is None:
            report.confidence = "low"
            report.coverage_note += "; exposure below min_exposure floor -- rate not computed, never defaulted to elite."
            return report
        report.raw_rate = raw_rate

        league_avg = _league_avg_rate(rows, denominator, min_exposure)
        if league_avg is None:
            report.shrunk_rate = raw_rate
        else:
            report.shrunk_rate = (raw_rate * exposure_val + M * league_avg) / (exposure_val + M)

        reference = [r for r in (bsa.handling_error_rate(row, denominator, min_exposure) for row in rows) if r is not None]
        # Ball security = FEWER handling errors is better -> invert before percentile-ranking, same
        # "higher = better" convention as every other attribute in player_ability_estimation.py.
        inverted_value = -report.shrunk_rate
        inverted_reference = [-r for r in reference]
        report.rating_0_99 = _percentile(inverted_value, inverted_reference)
        report.confidence = "high" if (param_source == "calibrated" and exposure_val >= 5 * M) else "medium"
        report.param_source = param_source
    else:
        proxy_result = _historical_proxy_estimate(player_name, as_of_season, all_seasons, denominator, lam, M, min_exposure)
        if proxy_result is None:
            report.mode = bsa.HISTORICAL_PROXY
            report.coverage_note = "No real turnover-subtype evidence ingested for this player-season yet, or proxy model unavailable."
            return report
        report = proxy_result
        report.param_source = param_source

    # OLD provisional rating, for direct comparison -- reuses the
    # existing, unmodified player_ability_estimation.py estimator.
    try:
        from player_ability_estimation import estimate_attribute
        old = estimate_attribute(player_name, as_of_season, "ball_security", all_seasons)
        report.old_provisional_rating = old.percentile_rating
    except Exception:
        report.old_provisional_rating = None

    return report


def _historical_proxy_estimate(player_name: str, as_of_season: str, all_seasons: List[str],
                                denominator: str, lam: float, M: float, min_exposure: float) -> Optional[BallSecurityReport]:
    """
    Pre-tracking-era path: predict this player-season's real exposure
    (touches-per-minute) from a proxy model TRAINED ONLY on tracking-era
    seasons (see ball_security_analysis.fit_touches_proxy), then divide
    this session's real ingested handling-error count for that season by
    the PREDICTED exposure. Returns None if no turnover evidence exists
    for this player-season, or if too few tracking-era seasons are
    available to fit/trust a proxy.
    """
    turnover_state = load_turnover_cache(as_of_season)
    if turnover_state is None:
        return None
    tv_row = _find_turnover_row_by_full_name(turnover_state, player_name)
    if tv_row is None:
        return None

    tracking_seasons = [s for s in all_seasons if s >= HANDLING_EXPOSURE_FIRST_SEASON and load_handling_exposure(s)]
    if len(tracking_seasons) < 2:
        return None  # not enough real tracking-era seasons in this session's cache to fit+validate a proxy

    train_seasons = tracking_seasons[:-1]  # last real tracking season held out for validation, never for fitting
    all_rows = {s: bsa.build_player_season_rows(s) for s in tracking_seasons}
    advanced = {s: load_player_advanced_stats(s) for s in tracking_seasons + [as_of_season]}
    train_rows = [r for s in train_seasons for r in all_rows[s]]
    model = bsa.fit_touches_proxy(train_rows, advanced)
    if model is None:
        return None

    test_rows = all_rows[tracking_seasons[-1]]
    validation = bsa.evaluate_touches_proxy(model, test_rows, advanced)

    adv_row = advanced[as_of_season].get(player_name, {})
    usg_pct, ast_pct = adv_row.get("usg_pct"), adv_row.get("ast_pct")
    minutes = adv_row.get("mpg", 0) * adv_row.get("gp", 0)
    if usg_pct is None or ast_pct is None or minutes <= 0:
        return None

    predicted_touches_per_min = max(model.predict(usg_pct, ast_pct), 0.0)
    predicted_exposure = predicted_touches_per_min * minutes
    games_done = len(turnover_state["games_done"])
    games_total = turnover_state["games_total"]
    handling_error_scaled = bsa._scale_to_full_season(tv_row["handling_error"], games_done, games_total)

    report = BallSecurityReport(
        player_name=player_name, season=as_of_season, mode=bsa.HISTORICAL_PROXY,
        exposure_denominator=denominator + "_PREDICTED",
        total_turnovers=bsa._scale_to_full_season(tv_row["total"], games_done, games_total),
        lost_ball_and_handling=handling_error_scaled,
        bad_pass=bsa._scale_to_full_season(tv_row["bad_pass"], games_done, games_total),
        offensive_foul_nonhandle=bsa._scale_to_full_season(tv_row["offensive_foul_nonhandle"], games_done, games_total),
        team_system=bsa._scale_to_full_season(tv_row["team_system"], games_done, games_total),
        unknown=bsa._scale_to_full_season(tv_row["other_unclassified"], games_done, games_total),
        exposure=predicted_exposure,
        proxy_r2=validation["r2"],
        coverage_note=(f"turnover ingestion coverage this session: {games_done}/{games_total} games; "
                       f"exposure PREDICTED by a proxy trained on {train_seasons}, validated on "
                       f"{tracking_seasons[-1]} (held-out R^2={validation['r2']})"),
    )
    if predicted_exposure < min_exposure:
        report.confidence = "low"
        return report
    report.raw_rate = handling_error_scaled / predicted_exposure
    report.shrunk_rate = (report.raw_rate * predicted_exposure + M * report.raw_rate) / (predicted_exposure + M) \
        if predicted_exposure > 0 else report.raw_rate  # no real cross-player league-avg available in proxy mode this session -- degrades to raw rate, documented, not invented
    report.rating_0_99 = None  # deliberately NOT percentile-ranked in proxy mode this session -- no real
    # cross-player historical-proxy reference population was built (would need the proxy applied to
    # every player that season, out of scope for this diagnostic pass) -- reported as a rate only, per
    # the task's own "use lower confidence... or explicitly separate proxy mode" guidance.
    report.confidence = "low" if (validation["r2"] is None or validation["r2"] < 0.3) else "medium"
    return report
