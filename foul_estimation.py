"""
Phase 6 -- Foul Drawing + Foul Discipline: the diagnostic estimators.
PARALLEL to player_ability_estimation.py -- NOT wired into its
ATTRIBUTE_EXTRACTORS in this phase (that dict is untouched). Standalone,
callable estimator, same role as ball_security_estimation.py in Phase 4B.

============================ NO BOX_PROXY MODE FOR EVENT CLASSIFICATION -- BUT ONE REAL DENOMINATOR FLOOR ============================
Unlike Phase 4B/5's tracking-era floor (2013-14), real playbyplayv3 foul-
subtype detail (Shooting/Personal/Offensive/etc.) is available across this
entire project's cached range, 1996-97 onward (verified directly in
foul_ingestion.py's own docstring) -- the EVENT CLASSIFICATION methodology
never changes by era. The one real era-dependent piece is
`foul_drawing`'s CALIBRATED DENOMINATOR (`drives`, from
handling_exposure.py's tracking-era-only data, 2013-14+) -- there is no
real per-player drive count before that floor. `estimate_foul_drawing`
falls back to the historical `fga_plus_sfd` denominator (the best-
performing non-tracking candidate found in this phase's own comparison --
see `docs/PHASE6_FOUL_ATTRIBUTES_REPORT.md`) for any season before
`handling_exposure.HANDLING_EXPOSURE_FIRST_SEASON`, and reports which
denominator was actually used on every result. `foul_discipline`'s
denominator (`minutes`) has no such floor -- real minutes exist for every
cached season.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

import foul_analysis as fa
import foul_calibration as fc
import foul_ingestion as fli
from player_ability_profile import AttributeEstimate

RATING_MIN, RATING_MAX = 0.0, 99.0

# Provisional fallbacks (used only when foul_calibration.json has no
# entry / is INSUFFICIENT_EVIDENCE for that attribute) -- deliberately
# conservative, same role as every other attribute's own provisional
# constants before calibration.
PROVISIONAL_LAMBDA = 0.5
PROVISIONAL_M_DRAWING = 200.0
PROVISIONAL_M_DISCIPLINE = 400.0
DEFAULT_DRAW_DENOMINATOR = "fga"
DEFAULT_DISC_DENOMINATOR = "minutes"
# Best-performing NON-tracking-era candidate found in this phase's own
# denominator comparison (see docs/PHASE6_FOUL_ATTRIBUTES_REPORT.md) --
# used automatically for any season before the real tracking floor,
# since the calibrated `drives` denominator has no data at all there.
HISTORICAL_DRAW_DENOMINATOR = "fga_plus_sfd"


@dataclass
class FoulDrawingReport:
    player_name: str
    season: str
    shooting_foul_drawn: Optional[float] = None
    nonshooting_def_foul_drawn: Optional[float] = None
    and_ones: Optional[float] = None
    exposure: Optional[float] = None
    exposure_denominator: str = DEFAULT_DRAW_DENOMINATOR
    raw_rate: Optional[float] = None
    shrunk_rate: Optional[float] = None
    rating_0_99: Optional[float] = None
    confidence: str = "none"
    param_source: str = "provisional"
    coverage_note: str = ""


@dataclass
class FoulDisciplineReport:
    player_name: str
    season: str
    shooting_foul_committed: Optional[float] = None
    nonshooting_def_foul_committed: Optional[float] = None
    offensive_foul_committed_diagnostic_only: Optional[float] = None
    exposure: Optional[float] = None
    exposure_denominator: str = DEFAULT_DISC_DENOMINATOR
    raw_rate: Optional[float] = None  # fouls per unit exposure -- LOWER is better (not yet inverted)
    shrunk_rate: Optional[float] = None
    rating_0_99: Optional[float] = None
    confidence: str = "none"
    param_source: str = "provisional"
    coverage_note: str = ""


def _resolve_draw_params():
    calibrated = fc.get_calibrated_params("foul_drawing")
    if calibrated is not None and calibrated.status != "INSUFFICIENT_EVIDENCE":
        return calibrated.lambda_, calibrated.M, calibrated.denominator, "calibrated"
    return PROVISIONAL_LAMBDA, PROVISIONAL_M_DRAWING, DEFAULT_DRAW_DENOMINATOR, "provisional"


def _resolve_disc_params():
    calibrated = fc.get_calibrated_params("foul_discipline")
    if calibrated is not None and calibrated.status != "INSUFFICIENT_EVIDENCE":
        return calibrated.lambda_, calibrated.M, calibrated.denominator, "calibrated"
    return PROVISIONAL_LAMBDA, PROVISIONAL_M_DISCIPLINE, DEFAULT_DISC_DENOMINATOR, "provisional"


def _percentile(value: float, reference: List[float]) -> float:
    if not reference:
        return 50.0
    import bisect
    sorted_ref = sorted(reference)
    rank = bisect.bisect_left(sorted_ref, value)
    return round(max(RATING_MIN, min(RATING_MAX, 100.0 * rank / len(sorted_ref))), 1)


def estimate_foul_drawing(player_name: str, as_of_season: str, all_seasons: List[str],
                           min_exposure: float = 30.0) -> FoulDrawingReport:
    from handling_exposure import HANDLING_EXPOSURE_FIRST_SEASON
    lam, M, denominator, param_source = _resolve_draw_params()
    if denominator == "drives" and as_of_season < HANDLING_EXPOSURE_FIRST_SEASON:
        # Real tracking floor -- no `drives` data exists this far back.
        # Falls back to the best historical candidate, NOT a silent
        # zero/guess (see HISTORICAL_DRAW_DENOMINATOR's own docstring).
        denominator = HISTORICAL_DRAW_DENOMINATOR
    report = FoulDrawingReport(player_name=player_name, season=as_of_season, exposure_denominator=denominator, param_source=param_source)

    foul_state = fli.load_foul_cache(as_of_season)
    if foul_state is None:
        report.coverage_note = "No foul-event cache for this season."
        return report

    rows = fa.build_player_foul_rows(as_of_season)
    player_row = next((r for r in rows if r.player_name == player_name), None)
    if player_row is None:
        report.coverage_note = "No joined real foul-drawing evidence for this player-season (may be outside this session's partial ingestion sample)."
        return report

    exposure_val = fa.draw_denominator_value(player_row, denominator)
    report.shooting_foul_drawn = player_row.shooting_foul_drawn
    report.nonshooting_def_foul_drawn = player_row.nonshooting_def_foul_drawn
    report.and_ones = player_row.and_ones
    report.exposure = exposure_val
    games_done = len(foul_state["games_done"])
    games_total = foul_state["games_total"]
    report.coverage_note = f"foul ingestion coverage this session: {games_done}/{games_total} games"

    raw_rate = fa.foul_drawing_rate(player_row, denominator, min_exposure)
    if raw_rate is None:
        report.confidence = "low"
        report.coverage_note += "; exposure below floor -- rate not computed."
        return report
    report.raw_rate = raw_rate

    rates = [r for r in (fa.foul_drawing_rate(row, denominator, min_exposure) for row in rows) if r is not None]
    league_avg = sum(rates) / len(rates) if rates else None
    if league_avg is None:
        report.shrunk_rate = raw_rate
    else:
        report.shrunk_rate = (raw_rate * exposure_val + M * league_avg) / (exposure_val + M)

    report.rating_0_99 = _percentile(report.shrunk_rate, rates)  # higher rate = more fouls drawn = better, no inversion needed
    if denominator == HISTORICAL_DRAW_DENOMINATOR and param_source == "calibrated":
        # Real, stated limitation: the calibrated lambda/M were tuned on
        # the tracking-era `drives` denominator, not re-tuned for this
        # historical fallback -- never presented at "high" confidence.
        report.confidence = "low"
        report.coverage_note += "; using historical fallback denominator with tracking-era-tuned shrinkage (not separately calibrated) -- low confidence by design."
    else:
        report.confidence = "high" if (param_source == "calibrated" and exposure_val >= 5 * M) else "medium"
    return report


def estimate_foul_discipline(player_name: str, as_of_season: str, all_seasons: List[str],
                              min_exposure: float = 100.0) -> FoulDisciplineReport:
    lam, M, denominator, param_source = _resolve_disc_params()
    report = FoulDisciplineReport(player_name=player_name, season=as_of_season, exposure_denominator=denominator, param_source=param_source)

    foul_state = fli.load_foul_cache(as_of_season)
    if foul_state is None:
        report.coverage_note = "No foul-event cache for this season."
        return report

    rows = fa.build_player_foul_rows(as_of_season)
    player_row = next((r for r in rows if r.player_name == player_name), None)
    if player_row is None:
        report.coverage_note = "No joined real foul-discipline evidence for this player-season."
        return report

    league_mean_poss = fa._load_league_mean_possessions(as_of_season) if denominator == "def_possessions_proxy" else None
    exposure_val = fa.disc_denominator_value(player_row, denominator, league_mean_poss)
    report.shooting_foul_committed = player_row.shooting_foul_committed
    report.nonshooting_def_foul_committed = player_row.nonshooting_def_foul_committed
    report.offensive_foul_committed_diagnostic_only = player_row.offensive_foul_committed
    report.exposure = exposure_val
    games_done = len(foul_state["games_done"])
    games_total = foul_state["games_total"]
    report.coverage_note = f"foul ingestion coverage this session: {games_done}/{games_total} games"

    raw_rate = fa.foul_discipline_rate(player_row, denominator, min_exposure, league_mean_poss)
    if raw_rate is None:
        report.confidence = "low"
        report.coverage_note += "; exposure below floor -- rate not computed."
        return report
    report.raw_rate = raw_rate

    rates = [r for r in (fa.foul_discipline_rate(row, denominator, min_exposure, league_mean_poss) for row in rows) if r is not None]
    league_avg = sum(rates) / len(rates) if rates else None
    if league_avg is None:
        report.shrunk_rate = raw_rate
    else:
        report.shrunk_rate = (raw_rate * exposure_val + M * league_avg) / (exposure_val + M)

    # Foul discipline = FEWER fouls is better -> invert before percentile-ranking.
    inverted_value = -report.shrunk_rate
    inverted_reference = [-r for r in rates]
    report.rating_0_99 = _percentile(inverted_value, inverted_reference)
    report.confidence = "high" if (param_source == "calibrated" and exposure_val >= 5 * M) else "medium"
    return report


def result_to_attribute_estimate_drawing(report: FoulDrawingReport) -> AttributeEstimate:
    if report.rating_0_99 is None:
        return AttributeEstimate()
    confidence = 0.5 if report.confidence == "medium" else (0.8 if report.confidence == "high" else 0.2)
    return AttributeEstimate(value=report.rating_0_99, confidence=confidence,
                              sample_size=int(round(report.exposure)) if report.exposure else None)


def result_to_attribute_estimate_discipline(report: FoulDisciplineReport) -> AttributeEstimate:
    if report.rating_0_99 is None:
        return AttributeEstimate()
    confidence = 0.5 if report.confidence == "medium" else (0.8 if report.confidence == "high" else 0.2)
    return AttributeEstimate(value=report.rating_0_99, confidence=confidence,
                              sample_size=int(round(report.exposure)) if report.exposure else None)
