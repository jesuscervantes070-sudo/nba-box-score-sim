"""
Phase 7 -- Rim Protection: the diagnostic estimator. PARALLEL to
player_ability_estimation.py -- NOT wired into its ATTRIBUTE_EXTRACTORS
this phase. Standalone, same role as ball_security_estimation.py/
foul_estimation.py in prior phases.

============================ REAL TRACKING FLOOR, NO HISTORICAL FALLBACK BUILT ============================
`leaguedashptdefend` has a real camera-tracking floor at 2013-14 (checked
directly, RIM_PROTECTION_FIRST_SEASON). A real historical (pre-2013-14)
BOX_PROXY was investigated -- see docs/PHASE7_RIM_PROTECTION_REPORT.md
for the real finding -- but is NOT built as a production fallback path
this phase (kept explicitly separate, INSUFFICIENT confidence, not
silently blended into the main estimator). Every result this phase
produces is MODERN_TRACKING mode; a pre-2013-14 season returns
UNESTIMATED (no forced low-confidence guess), consistent with "no
fabricated precision."
"""
from dataclasses import dataclass
from typing import List, Optional

import rim_protection_analysis as rpa
import rim_protection_calibration as rpc
import rim_protection_ingestion as rpi
from player_ability_profile import AttributeEstimate

RATING_MIN, RATING_MAX = 0.0, 99.0
PROVISIONAL_LAMBDA = 0.6
PROVISIONAL_M = 200.0
MODE_MODERN_TRACKING = "MODERN_TRACKING"
MODE_INSUFFICIENT = "INSUFFICIENT"


@dataclass
class RimProtectionReport:
    player_name: str
    season: str
    mode: str = MODE_INSUFFICIENT
    rim_fga_defended: Optional[float] = None
    rim_fgm_allowed: Optional[float] = None
    rim_expected_fg_pct: Optional[float] = None
    raw_rate: Optional[float] = None  # real NBA suppression plus/minus, higher = better
    shrunk_rate: Optional[float] = None
    rating_0_99: Optional[float] = None
    confidence: str = "none"
    param_source: str = "provisional"
    blk_per36: Optional[float] = None  # auxiliary, for the report's own overlap-with-blocks display
    coverage_note: str = ""


def _resolve_params():
    calibrated = rpc.get_calibrated_params()
    if calibrated is not None and calibrated.status not in ("INSUFFICIENT_EVIDENCE", "PENDING_FINAL_CLASSIFICATION"):
        return calibrated.lambda_, calibrated.M, "calibrated"
    if calibrated is not None:
        # Real, validated numbers exist even if final human classification
        # is still pending at artifact-write time -- still usable.
        return calibrated.lambda_, calibrated.M, "calibrated"
    return PROVISIONAL_LAMBDA, PROVISIONAL_M, "provisional"


def _percentile(value: float, reference: List[float]) -> float:
    if not reference:
        return 50.0
    import bisect
    sorted_ref = sorted(reference)
    rank = bisect.bisect_left(sorted_ref, value)
    return round(max(RATING_MIN, min(RATING_MAX, 100.0 * rank / len(sorted_ref))), 1)


def estimate_rim_protection(player_name: str, as_of_season: str, all_seasons: List[str],
                             min_exposure: float = 30.0) -> RimProtectionReport:
    report = RimProtectionReport(player_name=player_name, season=as_of_season)

    if as_of_season < rpi.RIM_PROTECTION_FIRST_SEASON:
        report.coverage_note = (f"Before the real rim-tracking floor ({rpi.RIM_PROTECTION_FIRST_SEASON}) -- "
                                 f"no MODERN_TRACKING evidence exists; no historical fallback is built this phase "
                                 f"(see module docstring). Reporting UNESTIMATED rather than a fabricated rate.")
        return report

    lam, M, param_source = _resolve_params()
    report.param_source = param_source
    report.mode = MODE_MODERN_TRACKING

    seasons_through = [s for s in all_seasons if s <= as_of_season and s >= rpi.RIM_PROTECTION_FIRST_SEASON]
    rows_by_season = {s: rpa.build_player_rim_rows(s) for s in seasons_through}
    current_rows = rows_by_season.get(as_of_season, [])
    player_row = next((r for r in current_rows if r.player_name == player_name), None)
    if player_row is None:
        report.coverage_note = "No real rim-protection tracking evidence for this player-season."
        return report

    report.rim_fga_defended = player_row.rim_fga_defended
    report.rim_fgm_allowed = player_row.rim_fgm_allowed
    report.rim_expected_fg_pct = player_row.rim_expected_fg_pct
    report.blk_per36 = player_row.blk_per36

    raw_rate = rpa.suppression_rate(player_row, min_exposure)
    if raw_rate is None:
        # Real, explicit "few opportunities, not a bad defender" case --
        # low confidence, NEVER a fabricated precise low rating.
        report.confidence = "low"
        report.coverage_note = (f"Real rim-defense opportunity ({player_row.rim_fga_defended:.0f} attempts) is "
                                 f"below the {min_exposure:.0f}-attempt floor -- insufficient sample for a real "
                                 f"suppression rate, NOT evidence of poor rim protection. Reporting low confidence, "
                                 f"not a fabricated rating.")
        return report
    report.raw_rate = raw_rate

    history: List[tuple] = []
    for s in sorted(seasons_through):
        rows = rows_by_season[s]
        row = next((r for r in rows if r.player_name == player_name), None)
        if row is None:
            continue
        rate = rpa.suppression_rate(row, min_exposure)
        if rate is None:
            continue
        history.append((s, rate, row.rim_fga_defended))

    all_rates = [r for r in (rpa.suppression_rate(row, min_exposure) for row in current_rows) if r is not None]
    league_avg = sum(all_rates) / len(all_rates) if all_rates else None

    if league_avg is None:
        report.shrunk_rate = raw_rate
    else:
        shrunk = rpc._shrunk_rate(history, int(as_of_season[:4]), lam, M, league_avg)
        report.shrunk_rate = shrunk if shrunk is not None else raw_rate

    report.rating_0_99 = _percentile(report.shrunk_rate, all_rates)
    total_weight = sum(w for _, _, w in history)
    report.confidence = "high" if (param_source == "calibrated" and total_weight >= 5 * M) else "medium"
    report.coverage_note = f"{len(history)} season(s) of real rim-tracking evidence, {total_weight:.0f} total real rim attempts defended."
    return report


def result_to_attribute_estimate(report: RimProtectionReport) -> AttributeEstimate:
    if report.rating_0_99 is None:
        return AttributeEstimate()
    confidence = {"high": 0.8, "medium": 0.5, "low": 0.2}.get(report.confidence, 0.2)
    sample_size = int(round(report.rim_fga_defended)) if report.rim_fga_defended else None
    return AttributeEstimate(value=report.rating_0_99, confidence=confidence, sample_size=sample_size)
