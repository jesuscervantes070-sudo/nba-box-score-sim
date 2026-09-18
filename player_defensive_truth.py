"""
Defensive Truth V1 -- truth-to-simulation-profile bridge.

Mirrors `player_scoring_truth.py`/`player_playmaking_truth.py`/`player_rebounding_truth.py`'s exact
architecture and conventions (same PLAYER TRUTH vs MODEL BELIEF contract, same MISSING != ZERO
discipline, same provenance vocabulary reused from `player_scoring_truth_temporal.py`) for 4
targets: `poa_containment`, `rim_protection`, `defensive_playmaking`, `foul_discipline`.

============================ EXISTING ARCHITECTURE AUDIT (done BEFORE writing anything new) ============================
Three of the four targets ALREADY EXIST as mature, complete, real, opportunity-normalized
estimators from earlier phases -- none rebuilt here, all reused by IMPORT via new
`player_identity.py` id-adapters (this phase's only addition to that file):

  - `poa_containment` (Phase 10, `poa_containment_estimation.py`): real MATCHUP tracking
    (`player_poa_containment.json`, id-keyed cache, name-keyed estimator) --
    `containment_rate = (expected_fgm - actual_fgm) / expected_fga_covered`, where `expected_fgm`
    is the REAL, opponent-quality-adjusted expected makes for the exact shots this player's
    matchup assignment covered. This GENUINELY isolates on-ball/matchup defense (not generic
    opponent FG%, not steals, not team defensive rating) -- confirmed by direct source read, not
    assumed. `player_perimeter_defense.json` (the older, cruder, name-keyed `perimeter_deterrence`
    proxy -- a bare "shot frequency near this defender minus expected" number, NOT opportunity-
    normalized against real matchup coverage) is explicitly NOT used as the truth source here;
    labeled honestly as a weaker proxy, left untouched. Real floor: 2017-18.

  - `rim_protection` (Phase 7, `rim_protection_estimation.py`): real rim-defense tracking
    (`player_rim_protection.json`) -- `rim_suppression_plusminus` = the NBA's OWN computed real
    opponent FG% suppression on Restricted-Area attempts (expected - actual), conditional on real
    `rim_fga_defended` (a real opportunity denominator, not a raw attempt/game count). Kept
    SEPARATE from blocks by construction (`blk_per36` is reported only as auxiliary diagnostic
    context, never part of the raw rate). `player_rim_defense.json` (the older, cruder
    `rim_deterrence` proxy, same shape as `player_perimeter_defense.json`) is NOT used here for the
    same reason. Real floor: 2013-14.

  - `foul_discipline` (Phase 6, `foul_estimation.py`): real PBP-derived foul-subtype evidence
    (`player_foul_events.json`) -- `(shooting_foul_committed + nonshooting_def_foul_committed) /
    exposure` (default exposure = real minutes; a `def_possessions_proxy` denominator is also
    implemented and used automatically once calibrated). Offensive fouls are tracked as a SEPARATE,
    diagnostic-only field (`offensive_foul_committed_diagnostic_only`) and never enter the rate.
    Real floor: none (the PBP foul-subtype classification works across the full cached 1996-97+
    range; only the alternate `def_possessions_proxy` denominator has its own real-pace-data floor).

`defensive_playmaking` had NO adequate existing construct -- `player_ability_estimation.py`'s
same-named attribute is a crude, unnormalized STL+BLK-per-36 count (real, but explicitly NOT
opportunity-normalized beyond minutes, and missing deflections entirely). Built FRESH this phase
in `defensive_playmaking_estimation.py` -- see that module's own docstring for the construct
definition. Two real, honestly-distinct definitions coexist under one name in two separate
modules (same posture as `ball_security` in Phase 8, `offensive_rebounding`/`defensive_rebounding`
in the Rebounding Truth phase) -- documented, not papered over.

============================ ENGINE CONSUMPTION AUDIT (done BEFORE choosing overlays) ============================
Checked directly against `possession_orchestrator.PlayerSimulationProfile`'s own module docstring
and dispatch call sites:

  - `poa_containment_shrunk_rate` (default 0.0): LIVE, passed straight through to
    `drive_resolution.py`'s own `DriveResolutionContext` with NO adapter-side conversion -- the
    module's own comment states "drive_resolution.py performs its OWN standardization against its
    own embedded population constants." This is EXACTLY `poa_containment_estimation.py`'s own
    native `containment_rate` scale (real plus-minus, centered near 0). **DIRECTLY COMPATIBLE.**

  - `rim_protection_suppression_rate` (default 0.00988 = the real, documented
    RIM_PROTECTION_POPULATION_MEAN): LIVE, "suppresses UNBLOCKED conversion only; never generates a
    block itself." This is EXACTLY `rim_protection_estimation.py`'s own native
    `rim_suppression_plusminus` scale. **DIRECTLY COMPATIBLE.**

  - `defensive_playmaking_per36` (default 1.5 = the OLD crude construct's own real population
    mean): LIVE, but the module's own comment explicitly restricts it to "a small, flagged weight
    for on-ball strips... Never broadened into a generic defensive-IQ signal." This phase's real,
    richer construct has a real, measured population mean/SD of 4.13/1.43 vs. the old construct's
    1.89/0.75 (both measured directly, 2024-25 cutoff) -- a genuine, ~2.2x scale gap (driven mainly
    by adding deflections). **NEEDS EXPLICIT ADAPTER** (see `defensive_engine_adapter.py`).

  - `foul_discipline_shrunk_rate`: **ALREADY DISABLED by the engine's own prior documentation**
    (`possession_orchestrator.py`'s own module comment, Phase 23A): "the existing foul resolvers
    expect a CENTERED, modifier-like value, while these estimators produce native rates on a
    different scale (and foul_discipline's own native direction... is the OPPOSITE sign)... no
    validated conversion exists." This phase does NOT dispute or attempt to fix that finding --
    **CLASSIFIED D: SEMANTIC MISMATCH -- DO NOT OVERLAY**, consistent with the engine's own existing
    posture. `foul_discipline` truth is still built and reported honestly; it is simply not wired.

============================ TEMPORAL STATUS ============================
All four targets are **PRIOR_SEASON_ONLY**. `player_poa_containment.json`/`player_rim_protection.json`
are real season-aggregate matchup/tracking caches (Classification B); `player_foul_events.json` is
real PBP-derived but the underlying per-game ingestion is not persisted as a per-game prefix this
phase (same proportionate-scope reasoning as the Playmaking phase's own turnover-subtype finding);
`defensive_playmaking`'s DEFLECTIONS component is a real season-aggregate-only tracking source
(`leaguehustlestatsplayer`), which caps the WHOLE composite at season granularity even though
STL/BLK are technically per-game-available. No current-season date-safe path is built for any of
the four this phase -- an honest, safe V1 outcome, not a shortfall silently papered over.
"""
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import player_identity as pid
import player_scoring_truth_temporal as psst  # reused for the SAME provenance vocabulary, not redefined
from possession_orchestrator import PlayerSimulationProfile

SCHEMA_VERSION = "0.1.0-defensive-truth"

DEFENSIVE_ATTRIBUTES = ("poa_containment", "rim_protection", "defensive_playmaking", "foul_discipline")

# Real floors, per each underlying estimator's own module docstring (see this module's own audit
# above). Attributes with no meaningful floor read as "1996-97" (this project's own cached start).
_FIRST_SEASON = {
    "poa_containment": "2017-18",
    "rim_protection": "2013-14",
    "defensive_playmaking": "2016-17",
    "foul_discipline": "1996-97",
}

_CONFIDENCE_STRING_TO_FLOAT = {"high": 0.8, "medium": 0.5, "low": 0.2, "none": None}


@dataclass(frozen=True)
class DefensiveTruthEstimate:
    """Same shape/contract as `ScoringTruthEstimate`/`PlaymakingTruthEstimate`/
    `ReboundingTruthEstimate`. `value` is each attribute's own NATIVE scale (NOT a common [0,1] or
    percentile scale -- see each estimator's own module docstring; this phase's own instruction is
    explicit that "Do not make them all percentile ratings merely for symmetry")."""
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

    def __post_init__(self):
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0] or None -- got {self.confidence!r}")
        valid_provenance = {"CURRENT_SEASON_PREGAME", "PRIOR_SEASON_ONLY", "MULTI_SEASON_THROUGH_CUTOFF", "MISSING"}
        if self.provenance not in valid_provenance:
            raise ValueError(f"provenance must be one of {valid_provenance} -- got {self.provenance!r}")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "player_id": self.player_id, "as_of_season": self.as_of_season,
            "value": self.value, "confidence": self.confidence, "sample_size": self.sample_size,
            "source": self.source, "param_source": self.param_source,
            "coverage_note": self.coverage_note, "provenance": self.provenance,
        }

    @staticmethod
    def from_dict(d: dict) -> "DefensiveTruthEstimate":
        return DefensiveTruthEstimate(
            name=d["name"], player_id=d["player_id"], as_of_season=d["as_of_season"],
            value=d.get("value"), confidence=d.get("confidence"), sample_size=d.get("sample_size"),
            source=d["source"], param_source=d.get("param_source"),
            coverage_note=d.get("coverage_note", ""),
            provenance=d.get("provenance", "MULTI_SEASON_THROUGH_CUTOFF"),
        )


@dataclass(frozen=True)
class DefensiveTruthProfile:
    """Same shape/contract as `ScoringTruthProfile`/`PlaymakingTruthProfile`/`ReboundingTruthProfile`."""
    player_id: str
    canonical_name: Optional[str]
    as_of_season: str
    identity_state: str
    estimates: Dict[str, DefensiveTruthEstimate] = field(default_factory=dict)
    as_of_date: Optional[str] = None

    def value(self, name: str) -> Optional[float]:
        est = self.estimates.get(name)
        return est.value if est is not None else None

    def has_real_evidence(self, name: str) -> bool:
        return self.value(name) is not None

    def coverage_summary(self) -> Dict[str, bool]:
        return {name: self.has_real_evidence(name) for name in DEFENSIVE_ATTRIBUTES}

    @property
    def conceptual_key(self) -> Tuple[str, str, str]:
        return (self.player_id, self.as_of_date or self.as_of_season, SCHEMA_VERSION)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "player_id": self.player_id,
            "canonical_name": self.canonical_name,
            "as_of_season": self.as_of_season,
            "as_of_date": self.as_of_date,
            "identity_state": self.identity_state,
            "estimates": {name: est.to_dict() for name, est in sorted(self.estimates.items())},
        }

    @staticmethod
    def from_dict(d: dict) -> "DefensiveTruthProfile":
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"DefensiveTruthProfile.from_dict: schema_version mismatch -- stored "
                f"{d.get('schema_version')!r}, this code expects {SCHEMA_VERSION!r}."
            )
        return DefensiveTruthProfile(
            player_id=d["player_id"], canonical_name=d.get("canonical_name"),
            as_of_season=d["as_of_season"], identity_state=d["identity_state"],
            estimates={name: DefensiveTruthEstimate.from_dict(v) for name, v in d.get("estimates", {}).items()},
            as_of_date=d.get("as_of_date"),
        )


def _missing(attribute: str, player_id: str, as_of_season: str, source: str, note: str,
             provenance: str = "MISSING") -> DefensiveTruthEstimate:
    return DefensiveTruthEstimate(
        name=attribute, player_id=player_id, as_of_season=as_of_season, value=None, confidence=None,
        sample_size=None, source=source, param_source=None, coverage_note=note, provenance=provenance,
    )


def _estimate_poa_containment(player_id: str, as_of_season: str, all_seasons: List[str]) -> DefensiveTruthEstimate:
    source = "poa_containment_estimation.estimate_poa_containment"
    resolution, report = pid.estimate_poa_containment_by_id(player_id, as_of_season, all_seasons)
    if report is None or report.shrunk_rate is None:
        return _missing("poa_containment", player_id, as_of_season, source,
                         (report.coverage_note if report else "identity not resolved"))
    confidence = _CONFIDENCE_STRING_TO_FLOAT.get(report.confidence)
    return DefensiveTruthEstimate(
        name="poa_containment", player_id=player_id, as_of_season=as_of_season, value=report.shrunk_rate,
        confidence=confidence, sample_size=report.expected_fga_covered, source=source,
        param_source=report.param_source, coverage_note=report.coverage_note,
        provenance=psst.MULTI_SEASON_THROUGH_CUTOFF,
    )


def _estimate_rim_protection(player_id: str, as_of_season: str, all_seasons: List[str]) -> DefensiveTruthEstimate:
    source = "rim_protection_estimation.estimate_rim_protection"
    resolution, report = pid.estimate_rim_protection_by_id(player_id, as_of_season, all_seasons)
    if report is None or report.shrunk_rate is None:
        return _missing("rim_protection", player_id, as_of_season, source,
                         (report.coverage_note if report else "identity not resolved"))
    confidence = _CONFIDENCE_STRING_TO_FLOAT.get(report.confidence)
    return DefensiveTruthEstimate(
        name="rim_protection", player_id=player_id, as_of_season=as_of_season, value=report.shrunk_rate,
        confidence=confidence, sample_size=report.rim_fga_defended, source=source,
        param_source=report.param_source, coverage_note=report.coverage_note,
        provenance=psst.MULTI_SEASON_THROUGH_CUTOFF,
    )


def _estimate_defensive_playmaking(player_id: str, as_of_season: str, all_seasons: List[str]) -> DefensiveTruthEstimate:
    source = "defensive_playmaking_estimation.estimate_defensive_playmaking"
    resolution, result = pid.estimate_defensive_playmaking_by_id(player_id, as_of_season, all_seasons)
    if result is None or result.shrunk_rate is None:
        return _missing("defensive_playmaking", player_id, as_of_season, source, "no real evidence through cutoff")
    import defensive_playmaking_estimation as dpe
    _, prior_strength, param_source = dpe.resolve_params()
    confidence = round(result.total_weight / (result.total_weight + prior_strength), 3)
    return DefensiveTruthEstimate(
        name="defensive_playmaking", player_id=player_id, as_of_season=as_of_season, value=result.shrunk_rate,
        confidence=confidence, sample_size=result.total_weight, source=source, param_source=param_source,
        provenance=psst.MULTI_SEASON_THROUGH_CUTOFF,
    )


def _estimate_foul_discipline(player_id: str, as_of_season: str, all_seasons: List[str]) -> DefensiveTruthEstimate:
    source = "foul_estimation.estimate_foul_discipline"
    resolution, report = pid.estimate_foul_discipline_by_id(player_id, as_of_season, all_seasons)
    if report is None or report.shrunk_rate is None:
        return _missing("foul_discipline", player_id, as_of_season, source,
                         (report.coverage_note if report else "identity not resolved"))
    confidence = _CONFIDENCE_STRING_TO_FLOAT.get(report.confidence)
    return DefensiveTruthEstimate(
        name="foul_discipline", player_id=player_id, as_of_season=as_of_season, value=report.shrunk_rate,
        confidence=confidence, sample_size=report.exposure, source=source,
        param_source=report.param_source, coverage_note=report.coverage_note,
        provenance=psst.MULTI_SEASON_THROUGH_CUTOFF,
    )


_ESTIMATE_FN = {
    "poa_containment": _estimate_poa_containment,
    "rim_protection": _estimate_rim_protection,
    "defensive_playmaking": _estimate_defensive_playmaking,
    "foul_discipline": _estimate_foul_discipline,
}


def _estimate_rim_protection_as_of_date(player_id: str, as_of_date: str, as_of_season: str,
                                         all_seasons: List[str]) -> DefensiveTruthEstimate:
    """CURRENT-SEASON DEFENSE + ROLE REFRESH V1: rim_protection's own date-safe path -- real
    current-season-through-cutoff evidence (see `rim_protection_estimation.
    estimate_rim_protection_as_of_date`'s own docstring for the full leakage/shrinkage-history
    argument), never the full in-progress season. `poa_containment` has NO such path (see module
    docstring's updated temporal-status audit) -- `leagueseasonmatchups`, the only real source for
    it, was directly confirmed this phase (`inspect.signature`) to expose no date-range parameter
    at all, so it remains PRIOR_SEASON_ONLY, an investigated-and-confirmed limitation, not an
    oversight."""
    source = "rim_protection_estimation.estimate_rim_protection_as_of_date"
    month_cutoff = psst.month_cutoff_for_date(as_of_date)
    resolution, report = pid.estimate_rim_protection_as_of_date_by_id(
        player_id, as_of_date, as_of_season, all_seasons, month_cutoff)
    if report is None or report.shrunk_rate is None:
        return _missing("rim_protection", player_id, as_of_season, source,
                         (report.coverage_note if report else "identity not resolved"))
    confidence = _CONFIDENCE_STRING_TO_FLOAT.get(report.confidence)
    provenance = psst.CURRENT_SEASON_PREGAME if report.used_current_season_evidence else psst.PRIOR_SEASON_ONLY
    return DefensiveTruthEstimate(
        name="rim_protection", player_id=player_id, as_of_season=as_of_season, value=report.shrunk_rate,
        confidence=confidence, sample_size=report.rim_fga_defended, source=source,
        param_source=report.param_source, coverage_note=report.coverage_note, provenance=provenance,
    )


def build_defensive_truth_profile(player_id: str, as_of_season: str, all_seasons: List[str]) -> DefensiveTruthProfile:
    """Season-level entry point. Each attribute goes through its own id-adapter (added to
    player_identity.py this phase) over its own pre-existing (or, for defensive_playmaking, newly
    built) estimator -- no math is duplicated here."""
    resolution = pid.resolve_id_to_name(player_id)
    estimates = {attr: _ESTIMATE_FN[attr](player_id, as_of_season, all_seasons) for attr in DEFENSIVE_ATTRIBUTES}
    return DefensiveTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates,
    )


def build_defensive_truth_profile_as_of_date(player_id: str, as_of_date: str, as_of_season: str,
                                              all_seasons: List[str]) -> DefensiveTruthProfile:
    """Pregame-safe entry point. `rim_protection` tries a real CURRENT_SEASON_PREGAME path first
    (CURRENT-SEASON DEFENSE + ROLE REFRESH V1 -- see `_estimate_rim_protection_as_of_date`),
    falling back to PRIOR_SEASON_ONLY when no real current-season-through-cutoff evidence exists
    yet (opening month, rookie, etc.). `poa_containment`/`defensive_playmaking`/`foul_discipline`
    remain PRIOR_SEASON_ONLY -- built from the last FULLY COMPLETED season only, frozen for the
    whole current season (poa_containment's own source has no real date-range parameter at all,
    confirmed this phase; defensive_playmaking/foul_discipline were out of this phase's scope)."""
    resolution = pid.resolve_id_to_name(player_id)
    reference_season = psst._season_before(as_of_season)
    reference_all_seasons = [s for s in all_seasons if s <= reference_season]

    estimates: Dict[str, DefensiveTruthEstimate] = {}

    # rim_protection: real current-season-pregame path first.
    rim_estimate = _estimate_rim_protection_as_of_date(player_id, as_of_date, as_of_season, all_seasons)
    if rim_estimate.provenance == psst.MISSING and reference_all_seasons:
        # no current-season evidence at all (e.g. before the season's own real month-1 cutoff) --
        # fall back to the ordinary PRIOR_SEASON_ONLY path, exactly like every other attribute.
        base = _ESTIMATE_FN["rim_protection"](player_id, reference_season, reference_all_seasons)
        provenance = psst.MISSING if base.value is None else psst.PRIOR_SEASON_ONLY
        rim_estimate = replace(base, as_of_season=as_of_season, provenance=provenance)
    estimates["rim_protection"] = rim_estimate

    for attribute in DEFENSIVE_ATTRIBUTES:
        if attribute == "rim_protection":
            continue
        if not reference_all_seasons:
            estimates[attribute] = _missing(
                attribute, player_id, as_of_season,
                f"player_defensive_truth (no completed prior season)",
                "no fully-completed prior season exists yet (rookie / first tracked season)",
            )
            continue
        base = _ESTIMATE_FN[attribute](player_id, reference_season, reference_all_seasons)
        provenance = psst.MISSING if base.value is None else psst.PRIOR_SEASON_ONLY
        estimates[attribute] = replace(base, as_of_season=as_of_season, provenance=provenance)

    return DefensiveTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates, as_of_date=as_of_date,
    )


def apply_defensive_truth_to_simulation_profile(profile: PlayerSimulationProfile,
                                                 truth: DefensiveTruthProfile) -> PlayerSimulationProfile:
    """Overlays ONLY `poa_containment_shrunk_rate` and `rim_protection_suppression_rate` DIRECTLY
    (both confirmed scale-compatible with the engine -- see module docstring's engine-consumption
    audit). Does NOT touch `defensive_playmaking_per36` (needs an explicit adapter -- see
    `defensive_engine_adapter.py`, a SEPARATE module, matching the Rebounding Truth phase's own
    TRUTH != ENGINE ADAPTER precedent) or `foul_discipline_shrunk_rate` (confirmed
    SEMANTIC_MISMATCH by the engine's own prior documentation -- never overlaid). A missing
    estimate leaves the corresponding field completely untouched."""
    overrides = {}
    value = truth.value("poa_containment")
    if value is not None:
        overrides["poa_containment_shrunk_rate"] = value
    value = truth.value("rim_protection")
    if value is not None:
        overrides["rim_protection_suppression_rate"] = value
    return replace(profile, **overrides) if overrides else profile
