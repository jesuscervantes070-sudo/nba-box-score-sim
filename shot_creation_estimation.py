"""
Phase 9 -- Shot Creation Internals: diagnostic estimators for
`rim_access_creation` and `perimeter_space_creation`. PARALLEL to
player_ability_estimation.py -- NOT wired into its ATTRIBUTE_EXTRACTORS.
Reuses rim_protection_calibration.py's generic shrinkage math directly.

============================ PROFILE WIRING DECISION (Option B, per task) ============================
`player_ability_profile.SKILL_ATTRIBUTES` has a `"shot_creation"` slot
but NOT separate `rim_access_creation`/`perimeter_space_creation` slots.
Neither internal component reached LOCK V1 this phase (both KEEP BUT
FLAG -- see report), and combining them into one display number would
require a validated combination method this phase explicitly did not
build (no "invent weights," no hand-set 50/50). Per the task's own
"prefer not to lose two-component information" instruction, this phase
does NOT call `with_attribute("shot_creation", ...)` at all -- the
existing `shot_creation` slot is left UNWIRED, and both internal
components are exposed here as their own `AttributeEstimate`-compatible
results, ready for a FUTURE phase to combine once a real, validated
combination method exists.

No historical (pre-2013-14) mode -- same real tracking floor as every
`leaguedashpt*`-derived attribute in this codebase.
"""
from dataclasses import dataclass
from typing import List, Optional

import shot_creation_analysis as sca
import shot_creation_calibration as scc
import shot_creation_ingestion as sci
from rim_protection_calibration import _shrunk_rate
from player_ability_profile import AttributeEstimate

RATING_MIN, RATING_MAX = 0.0, 99.0
PROVISIONAL_LAMBDA = 0.6
PROVISIONAL_M = 200.0

# Frozen TRAIN-only (2013-14 through 2019-20) OLS coefficients for the
# perimeter-space residual (intercept, usg_pct, time_of_poss/touch) --
# see shot_creation_analysis.py / report for how these were fit. NEVER
# refit per season (would leak future-season information backward).
PERIMETER_RESIDUAL_COEFFICIENTS = (-0.035744879515455144, 0.27659289885978294, 0.6543760467797481)


@dataclass
class CreationComponentReport:
    player_name: str
    season: str
    component: str  # "rim_access_creation" or "perimeter_space_creation"
    mode: str = "INSUFFICIENT"
    exposure: Optional[float] = None  # real drives (rim access) or touches (perimeter)
    raw_rate: Optional[float] = None
    shrunk_rate: Optional[float] = None
    rating_0_99: Optional[float] = None
    confidence: str = "none"
    param_source: str = "provisional"
    coverage_note: str = ""


def _percentile(value: float, reference: List[float]) -> float:
    if not reference:
        return 50.0
    import bisect
    sorted_ref = sorted(reference)
    rank = bisect.bisect_left(sorted_ref, value)
    return round(max(RATING_MIN, min(RATING_MAX, 100.0 * rank / len(sorted_ref))), 1)


def perimeter_residual(row, min_touches: float = 200.0) -> Optional[float]:
    rate = sca.perimeter_space_rate(row, min_touches)
    if rate is None or row.usg_pct is None or row.time_of_poss is None or not row.touches:
        return None
    x = (1.0, row.usg_pct, row.time_of_poss / row.touches)
    pred = sum(b * xi for b, xi in zip(PERIMETER_RESIDUAL_COEFFICIENTS, x))
    return rate - pred


def _estimate(player_name: str, as_of_season: str, all_seasons: List[str], component: str,
              rate_fn, weight_fn, min_exposure: float) -> CreationComponentReport:
    report = CreationComponentReport(player_name=player_name, season=as_of_season, component=component)

    if as_of_season < sci.SHOT_CREATION_FIRST_SEASON:
        report.coverage_note = f"Before the real tracking floor ({sci.SHOT_CREATION_FIRST_SEASON}) -- no historical fallback built this phase."
        return report

    calib = scc.get_calibrated_params(component)
    if calib is not None:
        lam, M, report.param_source = calib["lambda"], calib["M"], "calibrated"
    else:
        lam, M, report.param_source = PROVISIONAL_LAMBDA, PROVISIONAL_M, "provisional"
    report.mode = "MODERN_TRACKING"

    seasons_through = [s for s in all_seasons if s <= as_of_season and s >= sci.SHOT_CREATION_FIRST_SEASON]
    rows_by_season = {s: sca.build_player_creation_rows(s) for s in seasons_through}
    current_rows = rows_by_season.get(as_of_season, [])
    player_row = next((r for r in current_rows if r.player_name == player_name), None)
    if player_row is None:
        report.coverage_note = "No real tracking evidence for this player-season."
        return report

    report.exposure = weight_fn(player_row)
    raw = rate_fn(player_row)
    if raw is None:
        report.confidence = "low"
        report.coverage_note = f"Real exposure below the {min_exposure:.0f} floor -- insufficient sample, NOT evidence of poor {component}."
        return report
    report.raw_rate = raw

    history = []
    for s in sorted(seasons_through):
        row = next((r for r in rows_by_season[s] if r.player_name == player_name), None)
        if row is None:
            continue
        r = rate_fn(row)
        if r is None:
            continue
        history.append((s, r, weight_fn(row)))

    all_rates = [r for r in (rate_fn(row) for row in current_rows) if r is not None]
    league_avg = sum(all_rates) / len(all_rates) if all_rates else None
    if league_avg is None:
        report.shrunk_rate = raw
    else:
        shrunk = _shrunk_rate(history, int(as_of_season[:4]), lam, M, league_avg)
        report.shrunk_rate = shrunk if shrunk is not None else raw

    report.rating_0_99 = _percentile(report.shrunk_rate, all_rates)
    total_weight = sum(w for _, _, w in history)
    report.confidence = "high" if (report.param_source == "calibrated" and total_weight >= 5 * M) else "medium"
    report.coverage_note = f"{len(history)} season(s) of real evidence, {total_weight:.0f} total real exposure units."
    return report


def estimate_rim_access_creation(player_name: str, as_of_season: str, all_seasons: List[str],
                                  min_drives: float = 50.0) -> CreationComponentReport:
    return _estimate(player_name, as_of_season, all_seasons, "rim_access_creation",
                      rate_fn=lambda r: sca.rim_access_rate(r, min_drives),
                      weight_fn=lambda r: r.drives or 0.0, min_exposure=min_drives)


def estimate_perimeter_space_creation(player_name: str, as_of_season: str, all_seasons: List[str],
                                       min_touches: float = 200.0) -> CreationComponentReport:
    return _estimate(player_name, as_of_season, all_seasons, "perimeter_space_creation",
                      rate_fn=lambda r: perimeter_residual(r, min_touches),
                      weight_fn=lambda r: r.touches or 0.0, min_exposure=min_touches)


def result_to_attribute_estimate(report: CreationComponentReport) -> AttributeEstimate:
    if report.rating_0_99 is None:
        return AttributeEstimate()
    confidence = {"high": 0.8, "medium": 0.5, "low": 0.2}.get(report.confidence, 0.2)
    sample_size = int(round(report.exposure)) if report.exposure else None
    return AttributeEstimate(value=report.rating_0_99, confidence=confidence, sample_size=sample_size)
