"""Creation Truth V1 -- truth-to-simulation-profile bridge for `rim_access_creation_shrunk_rate`.

============================ ZERO-VARIANCE AUDIT (CURRENT-SEASON DEFENSE + ROLE REFRESH V1) ============================
`PlayerSimulationProfile.rim_access_creation_shrunk_rate` (real consumer: `drive_resolution.py`'s
own `leverage` term, `(value - RIM_ACCESS_POPULATION_MEAN) / RIM_ACCESS_POPULATION_STDEV`) was
PERMANENTLY frozen at its synthetic default for EVERY real player in this project until this
phase -- NOT because no real construct exists, but because a real, already-built, already-
calibrated estimator (`shot_creation_estimation.estimate_rim_access_creation`, Phase 9: real
"P(create a productive rim-access outcome | drive)" rate, `(drive_fga + drive_fta + drive_ast -
drive_tov) / drives`, real shrinkage via the same generic `rim_protection_calibration._shrunk_rate`
engine every other Phase 7-10 estimator reuses) was simply never wired to an id-based truth
profile or overlaid into the engine field. Confirmed by direct grep before writing anything here:
`estimate_rim_access_creation` had zero real-player call sites anywhere in this project.

This module is the wiring -- no estimator math is touched, duplicated, or re-derived. See
`possession_orchestrator.PlayerSimulationProfile.synthetic()`'s own docstring for a SEPARATE, real
discrepancy found alongside this phase (the synthetic default is 0.5, not the engine's own
documented `RIM_ACCESS_POPULATION_MEAN` of 0.622) -- documented there, deliberately left UNCHANGED
this phase (fixing it shifted several pre-existing, out-of-scope possession-mechanics baseline
tests), flagged for a dedicated future calibration-safety phase.

============================ TEMPORAL STATUS ============================
`player_shot_creation_tracking.json` (the real, already-cached source `shot_creation_ingestion.py`
builds) is Classification B: season-aggregate only, no per-game or date-range extraction path
exists for it (not investigated this phase -- out of the two PRIMARY refresh targets; per this
phase's own instruction, "the primary goal is to remove permanent zero variance, not necessarily
make this one current-season fresh"). **PRIOR_SEASON_ONLY**, same Option-B fallback as every other
season-aggregate-only truth track in this project.
"""
from dataclasses import dataclass, field, replace
from typing import Dict, Optional

import player_identity as pid
import player_scoring_truth_temporal as psst
from possession_orchestrator import PlayerSimulationProfile

SCHEMA_VERSION = "0.1.0-creation-truth"
CREATION_ATTRIBUTES = ("rim_access_creation",)

_CONFIDENCE_STRING_TO_FLOAT = {"high": 0.8, "medium": 0.5, "low": 0.2, "none": 0.0}


@dataclass(frozen=True)
class CreationTruthEstimate:
    name: str
    player_id: str
    as_of_season: str
    value: Optional[float]
    confidence: Optional[float]
    sample_size: Optional[float]
    source: str
    param_source: Optional[str]
    coverage_note: str = ""
    provenance: str = "MULTI_SEASON_THROUGH_CUTOFF"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "player_id": self.player_id, "as_of_season": self.as_of_season,
            "value": self.value, "confidence": self.confidence, "sample_size": self.sample_size,
            "source": self.source, "param_source": self.param_source,
            "coverage_note": self.coverage_note, "provenance": self.provenance,
        }


@dataclass(frozen=True)
class CreationTruthProfile:
    player_id: str
    canonical_name: Optional[str]
    as_of_season: str
    identity_state: str
    estimates: Dict[str, CreationTruthEstimate] = field(default_factory=dict)
    as_of_date: Optional[str] = None

    def value(self, name: str) -> Optional[float]:
        est = self.estimates.get(name)
        return est.value if est is not None else None

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION, "player_id": self.player_id,
            "canonical_name": self.canonical_name, "as_of_season": self.as_of_season,
            "as_of_date": self.as_of_date, "identity_state": self.identity_state,
            "estimates": {name: est.to_dict() for name, est in sorted(self.estimates.items())},
        }


def _missing(player_id: str, as_of_season: str, note: str) -> CreationTruthEstimate:
    return CreationTruthEstimate(
        name="rim_access_creation", player_id=player_id, as_of_season=as_of_season, value=None,
        confidence=None, sample_size=None, source="shot_creation_estimation.estimate_rim_access_creation",
        param_source=None, coverage_note=note, provenance=psst.MISSING,
    )


def _estimate(player_id: str, as_of_season: str, all_seasons) -> CreationTruthEstimate:
    source = "shot_creation_estimation.estimate_rim_access_creation"
    resolution, report = pid.estimate_rim_access_creation_by_id(player_id, as_of_season, list(all_seasons))
    if report is None or report.shrunk_rate is None:
        return _missing(player_id, as_of_season, (report.coverage_note if report else "identity not resolved"))
    confidence = _CONFIDENCE_STRING_TO_FLOAT.get(report.confidence, 0.0)
    return CreationTruthEstimate(
        name="rim_access_creation", player_id=player_id, as_of_season=as_of_season, value=report.shrunk_rate,
        confidence=confidence, sample_size=report.exposure, source=source,
        param_source=report.param_source, coverage_note=report.coverage_note,
        provenance=psst.MULTI_SEASON_THROUGH_CUTOFF,
    )


def build_creation_truth_profile_as_of_date(player_id: str, as_of_date: str, as_of_season: str,
                                             all_seasons) -> CreationTruthProfile:
    """Pregame-safe entry point. PRIOR_SEASON_ONLY (see module docstring's temporal-status audit)
    -- built from the last FULLY COMPLETED season only, frozen for the whole current season,
    exactly like every other season-aggregate-only truth track's own Option-B fallback."""
    resolution = pid.resolve_id_to_name(player_id)
    reference_season = psst._season_before(as_of_season)
    reference_all_seasons = [s for s in all_seasons if s <= reference_season]

    if not reference_all_seasons:
        estimate = _missing(player_id, as_of_season, "no fully-completed prior season exists yet (rookie / first tracked season)")
    else:
        base = _estimate(player_id, reference_season, reference_all_seasons)
        provenance = psst.MISSING if base.value is None else psst.PRIOR_SEASON_ONLY
        estimate = replace(base, as_of_season=as_of_season, provenance=provenance)

    return CreationTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates={"rim_access_creation": estimate}, as_of_date=as_of_date,
    )


def apply_creation_truth_to_simulation_profile(profile: PlayerSimulationProfile,
                                                truth: CreationTruthProfile) -> PlayerSimulationProfile:
    """Overlays ONLY `rim_access_creation_shrunk_rate`, DIRECTLY -- confirmed scale-compatible:
    `shot_creation_estimation.estimate_rim_access_creation`'s own `shrunk_rate` is on the exact
    same real, native `(drive_fga+drive_fta+drive_ast-drive_tov)/drives` scale (~0.15-1.1 real
    range, centered near the engine's own real `RIM_ACCESS_POPULATION_MEAN=0.622`) that
    `drive_resolution.py` already standardizes internally -- no adapter needed, same posture as
    `player_role_truth.py`'s own three role fields. A missing estimate leaves the field completely
    untouched (keeps the engine's own real population-mean default -- see
    `PlayerSimulationProfile.synthetic()`'s bug-fix docstring -- never fabricates a value)."""
    value = truth.value("rim_access_creation")
    if value is None:
        return profile
    return replace(profile, rim_access_creation_shrunk_rate=value)
