"""
Phase 8 -- Playmaking Vision: the diagnostic estimator. PARALLEL to
player_ability_estimation.py -- NOT wired into its ATTRIBUTE_EXTRACTORS.
Reuses rim_protection_calibration.py's generic shrinkage math directly
(`_shrunk_rate`) -- no new calibration machinery.

Real target: POTENTIAL_AST/touch, residualized against real usage/
drives-per-touch/time-of-possession-per-touch (frozen TRAIN-fit
coefficients, see playmaking_vision_analysis.py) -- NOT raw AST, NOT raw
POTENTIAL_AST/touch (see report for why the raw version was rejected:
0.85-0.91 real correlation with the existing `passing_accuracy`
attribute, too high to call it distinct).

No historical (pre-2013-14) mode -- same real tracking floor as every
`leaguedashpt*`-derived attribute this codebase has (Phase 4B/5/7/8).
Returns UNESTIMATED for a pre-floor season, never a fabricated rate.

============================ PROFILE WIRING KEY ============================
`player_ability_profile.SKILL_ATTRIBUTES` does not have a literal
`"playmaking_vision"` entry -- it already has `"creation_for_others"` in
the exact same PLAYMAKING_ATTRIBUTES slot this phase's concept occupies
(recognizing/generating passing opportunities for others, as distinct
from `"passing"` = accuracy and `"ball_security"` = handling). Per this
phase's own "no schema changes unless absolutely necessary" instruction,
this estimator's results are wired into `PlayerAbilityProfile` under the
EXISTING `"creation_for_others"` key rather than adding a 19th skill
attribute -- the Python module/function names keep this phase's own
`playmaking_vision` terminology throughout for clarity.
"""

PROFILE_ATTRIBUTE_KEY = "creation_for_others"  # see module docstring's "PROFILE WIRING KEY" note
from dataclasses import dataclass
from typing import List, Optional

import playmaking_vision_analysis as pva
import playmaking_vision_calibration as pvc
import passing_tracking_ingestion as pti
from player_ability_profile import AttributeEstimate

RATING_MIN, RATING_MAX = 0.0, 99.0
PROVISIONAL_LAMBDA = 0.6
PROVISIONAL_M = 200.0


@dataclass
class PlaymakingVisionReport:
    player_name: str
    season: str
    mode: str = "INSUFFICIENT"
    touches: Optional[float] = None
    potential_ast: Optional[float] = None
    raw_rate: Optional[float] = None       # real POTENTIAL_AST/touch, unadjusted
    residual_rate: Optional[float] = None  # role-adjusted (the actual estimator input)
    shrunk_rate: Optional[float] = None
    rating_0_99: Optional[float] = None
    confidence: str = "none"
    param_source: str = "provisional"
    coverage_note: str = ""


def _resolve_params():
    calibrated = pvc.get_calibrated_params()
    if calibrated is not None:
        return calibrated.lambda_, calibrated.M, "calibrated"
    return PROVISIONAL_LAMBDA, PROVISIONAL_M, "provisional"


def _percentile(value: float, reference: List[float]) -> float:
    if not reference:
        return 50.0
    import bisect
    sorted_ref = sorted(reference)
    rank = bisect.bisect_left(sorted_ref, value)
    return round(max(RATING_MIN, min(RATING_MAX, 100.0 * rank / len(sorted_ref))), 1)


def estimate_playmaking_vision(player_name: str, as_of_season: str, all_seasons: List[str],
                                min_touches: float = 200.0) -> PlaymakingVisionReport:
    report = PlaymakingVisionReport(player_name=player_name, season=as_of_season)

    if as_of_season < pti.PASSING_TRACKING_FIRST_SEASON:
        report.coverage_note = (f"Before the real passing-tracking floor ({pti.PASSING_TRACKING_FIRST_SEASON}) -- "
                                 f"no MODERN_TRACKING evidence exists; no historical fallback is built this phase.")
        return report

    lam, M, param_source = _resolve_params()
    report.param_source = param_source
    report.mode = "MODERN_TRACKING"

    seasons_through = [s for s in all_seasons if s <= as_of_season and s >= pti.PASSING_TRACKING_FIRST_SEASON]
    rows_by_season = {s: pva.build_player_vision_rows(s) for s in seasons_through}
    current_rows = rows_by_season.get(as_of_season, [])
    player_row = next((r for r in current_rows if r.player_name == player_name), None)
    if player_row is None:
        report.coverage_note = "No real passing-tracking evidence for this player-season."
        return report

    report.touches = player_row.touches
    report.potential_ast = player_row.potential_ast
    report.raw_rate = pva.vision_rate(player_row, "touches", min_touches)

    residual = pva.residual_vision_rate(player_row, min_touches=min_touches)
    if residual is None:
        report.confidence = "low"
        report.coverage_note = (f"Real touches ({player_row.touches}) below the {min_touches:.0f}-touch floor, or "
                                 f"missing role inputs -- insufficient sample for a real rate, NOT evidence of poor "
                                 f"vision. Reporting low confidence, not a fabricated rating.")
        return report
    report.residual_rate = residual

    history = []
    for s in sorted(seasons_through):
        row = next((r for r in rows_by_season[s] if r.player_name == player_name), None)
        if row is None:
            continue
        r = pva.residual_vision_rate(row, min_touches=min_touches)
        if r is None:
            continue
        history.append((s, r, row.touches))

    all_residuals = [r for r in (pva.residual_vision_rate(row, min_touches=min_touches) for row in current_rows) if r is not None]
    league_avg = sum(all_residuals) / len(all_residuals) if all_residuals else None

    if league_avg is None:
        report.shrunk_rate = residual
    else:
        shrunk = pvc._shrunk_rate(history, int(as_of_season[:4]), lam, M, league_avg)
        report.shrunk_rate = shrunk if shrunk is not None else residual

    report.rating_0_99 = _percentile(report.shrunk_rate, all_residuals)
    total_weight = sum(w for _, _, w in history)
    report.confidence = "high" if (param_source == "calibrated" and total_weight >= 5 * M) else "medium"
    report.coverage_note = f"{len(history)} season(s) of real evidence, {total_weight:.0f} total real touches."
    return report


def result_to_attribute_estimate(report: PlaymakingVisionReport) -> AttributeEstimate:
    if report.rating_0_99 is None:
        return AttributeEstimate()
    confidence = {"high": 0.8, "medium": 0.5, "low": 0.2}.get(report.confidence, 0.2)
    sample_size = int(round(report.touches)) if report.touches else None
    return AttributeEstimate(value=report.rating_0_99, confidence=confidence, sample_size=sample_size)
