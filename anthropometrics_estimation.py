"""
Phase 12A -- Player Anthropometrics Foundation: the estimator.

Builds a `PlayerPhysicalProfile` for one real, stable NBA `player_id` as
of a given season, applying:
  - the source hierarchy (MEASURED_COMBINE > MEASURED_ROSTER >
    INFERRED_REGRESSION > UNAVAILABLE for height; MEASURED_COMBINE >
    INFERRED_REGRESSION > UNAVAILABLE for wingspan/standing_reach --
    no roster-listed source exists for those two),
  - strict temporal-leakage prevention (no observation dated after
    `as_of_season` is ever used),
  - the validated wingspan~height / standing_reach~height+wingspan
    regressions from anthropometrics_analysis.py, ONLY as a fallback
    when a real MEASURED_COMBINE value is absent, and ONLY using
    coefficients fit on real combine data (no basketball-production
    predictor is used anywhere in this file).

Does NOT touch PlayerAbilityProfile, player_tendencies_*, or any skill
estimator.
"""
from typing import Optional, Tuple

import anthropometrics_analysis as aa
import anthropometrics_ingestion as ai
from anthropometrics_profile import (
    INFERRED_REGRESSION, MEASURED_COMBINE, MEASURED_ROSTER, UNAVAILABLE,
    UNAVAILABLE_OBSERVATION, PhysicalObservation, PlayerPhysicalProfile,
)

INFERENCE_MODEL_VERSION = "ols-v1"


def _season_start_year(season: str) -> int:
    return int(season[:4])


def _find_combine_row(player_id: str, draft_years=aa.COMBINE_YEARS):
    """A player appears in exactly one draft-class combine file (if
    any). Returns (draft_year, row) or (None, None)."""
    for y in draft_years:
        row = ai.load_combine_anthro(y).get(player_id)
        if row is not None:
            return y, row
    return None, None


def _find_roster_observations(player_id: str, seasons, as_of_year: int):
    """All real roster-physical observations for this player at or
    before `as_of_year`, oldest-first excluded -- caller picks what it
    needs. Only seasons actually present in the cache are considered
    (this project ingested a representative sample, not every season --
    see docs/PHASE12A_ANTHROPOMETRICS_REPORT.md Sec. 3)."""
    obs = []
    for s in seasons:
        if _season_start_year(s) > as_of_year:
            continue  # strict temporal cutoff -- never use a future roster snapshot
        row = ai.load_roster_physicals(s).get(player_id)
        if row is not None:
            obs.append((s, row))
    return obs


_CACHED_ROSTER_SEASONS = ("1996-97", "2005-06", "2013-14", "2018-19", "2022-23", "2023-24")

# Fit ONCE on all real combine data (2000-2025) for production inference.
# Kept separate from anthropometrics_analysis.backtest_regression's
# TRAIN-only fit, which exists purely to validate generalization -- this
# is the "final" model used once validation already passed.
_WINGSPAN_FIT = None
_REACH_FIT = None


def _fit_production_models():
    global _WINGSPAN_FIT, _REACH_FIT
    if _WINGSPAN_FIT is None:
        X, y = aa._collect_pairs(aa.COMBINE_YEARS, ("height_wo_shoes_in",), "wingspan_in")
        _WINGSPAN_FIT = aa._solve_normal_equations(X, y)
    if _REACH_FIT is None:
        X, y = aa._collect_pairs(aa.COMBINE_YEARS, ("height_wo_shoes_in", "wingspan_in"), "standing_reach_in")
        _REACH_FIT = aa._solve_normal_equations(X, y)
    return _WINGSPAN_FIT, _REACH_FIT


def build_physical_profile(player_id: str, as_of_season: str,
                            roster_seasons=_CACHED_ROSTER_SEASONS) -> PlayerPhysicalProfile:
    as_of_year = _season_start_year(as_of_season)
    draft_year, combine_row = _find_combine_row(player_id)
    combine_usable = combine_row is not None and draft_year <= as_of_year  # temporal cutoff: combine predates rookie season

    roster_obs = _find_roster_observations(player_id, roster_seasons, as_of_year)

    # ---- height ----
    if combine_usable and combine_row.get("height_wo_shoes_in") is not None:
        height_obs = PhysicalObservation(
            value=combine_row["height_wo_shoes_in"], unit="inches", evidence_mode=MEASURED_COMBINE,
            source="draftcombineplayeranthro", as_of=str(draft_year),
            note="barefoot (HEIGHT_WO_SHOES) -- the only combine height field with continuous 2000-2025 coverage",
        )
    elif roster_obs:
        season, row = roster_obs[-1]  # most recent usable roster snapshot
        if row.get("listed_height_in") is not None:
            height_obs = PhysicalObservation(
                value=row["listed_height_in"], unit="inches", evidence_mode=MEASURED_ROSTER,
                source="commonteamroster", as_of=season,
                note="team-LISTED height, not measured; empirically ~+1.0in above real combine barefoot height on average (n=723, stdev 0.67in) -- NOT corrected here, see report Sec. 5",
            )
        else:
            height_obs = UNAVAILABLE_OBSERVATION
    else:
        height_obs = UNAVAILABLE_OBSERVATION

    # ---- wingspan (combine only; inferred from height as fallback) ----
    if combine_usable and combine_row.get("wingspan_in") is not None:
        wingspan_obs = PhysicalObservation(
            value=combine_row["wingspan_in"], unit="inches", evidence_mode=MEASURED_COMBINE,
            source="draftcombineplayeranthro", as_of=str(draft_year),
        )
    elif height_obs.evidence_mode != UNAVAILABLE:
        wingspan_fit, _ = _fit_production_models()
        pred = wingspan_fit[0] + wingspan_fit[1] * height_obs.value
        wingspan_obs = PhysicalObservation(
            value=pred, unit="inches", evidence_mode=INFERRED_REGRESSION, source="wingspan~height (OLS)",
            as_of=as_of_season, model_version=INFERENCE_MODEL_VERSION,
            note="heldout MAE 1.73in (n=538, 2018-2025 draft classes) -- moderate precision only, see report Sec. 8",
        )
    else:
        wingspan_obs = UNAVAILABLE_OBSERVATION

    # ---- standing_reach (combine only; inferred from height+wingspan as fallback) ----
    if combine_usable and combine_row.get("standing_reach_in") is not None:
        reach_obs = PhysicalObservation(
            value=combine_row["standing_reach_in"], unit="inches", evidence_mode=MEASURED_COMBINE,
            source="draftcombineplayeranthro", as_of=str(draft_year),
        )
    elif height_obs.evidence_mode != UNAVAILABLE and wingspan_obs.evidence_mode != UNAVAILABLE:
        _, reach_fit = _fit_production_models()
        pred = reach_fit[0] + reach_fit[1] * height_obs.value + reach_fit[2] * wingspan_obs.value
        reach_obs = PhysicalObservation(
            value=pred, unit="inches", evidence_mode=INFERRED_REGRESSION, source="standing_reach~height+wingspan (OLS)",
            as_of=as_of_season, model_version=INFERENCE_MODEL_VERSION,
            note="heldout MAE 1.09in (n=538, 2018-2025 draft classes)",
        )
    else:
        reach_obs = UNAVAILABLE_OBSERVATION

    # ---- mass (time series -- combine snapshot + every usable roster snapshot, never collapsed) ----
    mass_list = []
    if combine_usable and combine_row.get("weight_lbs") is not None:
        mass_list.append(PhysicalObservation(
            value=combine_row["weight_lbs"], unit="lbs", evidence_mode=MEASURED_COMBINE,
            source="draftcombineplayeranthro", as_of=str(draft_year),
            note="pre-draft combine measurement -- a single point-in-time snapshot, not representative of later-career mass",
        ))
    for season, row in roster_obs:
        if row.get("listed_weight_lbs") is not None:
            mass_list.append(PhysicalObservation(
                value=row["listed_weight_lbs"], unit="lbs", evidence_mode=MEASURED_ROSTER,
                source="commonteamroster", as_of=season,
                note="team-LISTED weight; empirically ~96% identical to the prior season's listed weight (real, adjacent-season check, n=425) -- may reflect real stability or roster-page staleness, not distinguished here",
            ))

    return PlayerPhysicalProfile(
        player_id=player_id, height_in=height_obs, standing_reach_in=reach_obs,
        wingspan_in=wingspan_obs, mass_observations=tuple(mass_list),
    )
