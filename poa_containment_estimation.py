"""
Phase 10 -- POA Containment: the diagnostic estimator. PARALLEL to
player_ability_estimation.py -- NOT wired into its ATTRIBUTE_EXTRACTORS.
Reuses rim_protection_calibration.py's generic shrinkage math directly.

Wired (if justified -- see report) into the EXISTING `perimeter_defense`
SKILL_ATTRIBUTES slot, not a new schema entry, per the task's own "use
existing slot if semantics are compatible" instruction.

No historical mode -- real matchup data floor is 2017-18 (checked
directly; 2016-17 exists but is a real, too-sparse partial rollout, not
used). Returns UNESTIMATED before the floor, never a fabricated rate.
"""
from dataclasses import dataclass
from typing import List, Optional

import poa_containment_analysis as pca
import poa_containment_calibration as pcc
import poa_containment_ingestion as pci
from rim_protection_calibration import _shrunk_rate
from player_ability_profile import AttributeEstimate

RATING_MIN, RATING_MAX = 0.0, 99.0
PROVISIONAL_LAMBDA = 0.6
PROVISIONAL_M = 200.0
PROFILE_ATTRIBUTE_KEY = "perimeter_defense"


@dataclass
class PoaContainmentReport:
    player_name: str
    season: str
    mode: str = "INSUFFICIENT"
    expected_fga_covered: Optional[float] = None
    n_distinct_opponents: Optional[int] = None
    raw_rate: Optional[float] = None
    shrunk_rate: Optional[float] = None
    rating_0_99: Optional[float] = None
    confidence: str = "none"
    param_source: str = "provisional"
    coverage_note: str = ""


def _resolve_params():
    calib = pcc.get_calibrated_params()
    if calib is not None:
        return calib["lambda"], calib["M"], "calibrated"
    return PROVISIONAL_LAMBDA, PROVISIONAL_M, "provisional"


def _percentile(value: float, reference: List[float]) -> float:
    if not reference:
        return 50.0
    import bisect
    sorted_ref = sorted(reference)
    rank = bisect.bisect_left(sorted_ref, value)
    return round(max(RATING_MIN, min(RATING_MAX, 100.0 * rank / len(sorted_ref))), 1)


def estimate_poa_containment(player_name: str, as_of_season: str, all_seasons: List[str],
                              min_exposure: float = 100.0) -> PoaContainmentReport:
    report = PoaContainmentReport(player_name=player_name, season=as_of_season)

    if as_of_season < pci.POA_FIRST_SEASON:
        report.coverage_note = f"Before the real reliable matchup floor ({pci.POA_FIRST_SEASON}) -- no historical fallback built this phase."
        return report

    lam, M, report.param_source = _resolve_params()
    report.mode = "MODERN_MATCHUP"

    seasons_through = [s for s in all_seasons if s <= as_of_season and s >= pci.POA_FIRST_SEASON]
    rows_by_season = {s: pca.build_player_containment_rows(s) for s in seasons_through}
    current_rows = rows_by_season.get(as_of_season, [])
    player_row = next((r for r in current_rows if r.player_name == player_name), None)
    if player_row is None:
        report.coverage_note = "No real matchup evidence for this player-season."
        return report

    report.expected_fga_covered = player_row.expected_fga_covered
    report.n_distinct_opponents = player_row.n_distinct_opponents
    raw = pca.containment_rate(player_row, min_exposure)
    if raw is None:
        report.confidence = "low"
        report.coverage_note = (f"Real opponent-quality-covered matchup FGA ({player_row.expected_fga_covered:.0f}) "
                                 f"below the {min_exposure:.0f} floor -- insufficient sample, NOT evidence of poor containment.")
        return report
    report.raw_rate = raw

    history = []
    for s in sorted(seasons_through):
        row = next((r for r in rows_by_season[s] if r.player_name == player_name), None)
        if row is None:
            continue
        r = pca.containment_rate(row, min_exposure)
        if r is None:
            continue
        history.append((s, r, row.expected_fga_covered))

    all_rates = [r for r in (pca.containment_rate(row, min_exposure) for row in current_rows) if r is not None]
    league_avg = sum(all_rates) / len(all_rates) if all_rates else None
    if league_avg is None:
        report.shrunk_rate = raw
    else:
        shrunk = _shrunk_rate(history, int(as_of_season[:4]), lam, M, league_avg)
        report.shrunk_rate = shrunk if shrunk is not None else raw

    report.rating_0_99 = _percentile(report.shrunk_rate, all_rates)
    total_weight = sum(w for _, _, w in history)
    report.confidence = "high" if (report.param_source == "calibrated" and total_weight >= 5 * M) else "medium"
    report.coverage_note = f"{len(history)} season(s), {total_weight:.0f} total real opponent-quality-covered matchup FGA."
    return report


def result_to_attribute_estimate(report: PoaContainmentReport) -> AttributeEstimate:
    if report.rating_0_99 is None:
        return AttributeEstimate()
    confidence = {"high": 0.8, "medium": 0.5, "low": 0.2}.get(report.confidence, 0.2)
    sample_size = int(round(report.expected_fga_covered)) if report.expected_fga_covered else None
    return AttributeEstimate(value=report.rating_0_99, confidence=confidence, sample_size=sample_size)
