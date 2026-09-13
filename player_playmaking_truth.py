"""
Playmaking + Ball Security V1 -- truth-to-simulation-profile bridge.

Mirrors `player_scoring_truth.py`'s exact architecture and conventions (same PLAYER TRUTH vs MODEL
BELIEF contract, same MISSING != ZERO discipline, same provenance vocabulary reused from
`player_scoring_truth_temporal.py`) for the 3 new targets: `passing_accuracy`, `playmaking_vision`,
`ball_security`. Deliberately NOT a copy-pasted parallel type family with different field names --
`PlaymakingTruthEstimate`/`PlaymakingTruthProfile` below have the SAME shape as
`ScoringTruthEstimate`/`ScoringTruthProfile` on purpose, so a future joiner (or a future
generalization of both into one shared type) is a mechanical exercise, not a redesign.

============================ TEMPORAL STATUS (checked directly before building) ============================
All three targets' real evidence sources (`player_passing_tracking.json`,
`player_handling_exposure.json` -- both real SportVU/Second Spectrum SEASON-AGGREGATE camera
tracking; `player_turnover_subtypes.json` -- real PBP-derived, but the CACHE ITSELF stores only a
SEASON-AGGREGATE per player, never a per-game breakdown, confirmed by direct inspection of its own
schema) are **Classification B: season aggregate only**. `player_turnover_subtypes.json`'s own
underlying INGESTION PIPELINE (`turnover_ingestion.py`) fetches real play-by-play PER GAME (one
call per real game_id) and therefore COULD in principle support a per-game/date-filterable
prefix -- but building that would mean re-deriving and re-caching per-game turnover-subtype
evidence for every cached season (~1,230 real PBP calls/season, a MUCH larger, genuinely new
ingestion job than the ~10-call/season shot-chart win from the prior phase) -- explicitly out of
this pass's proportionate scope ("avoid giant data/research jobs"; "Do not launch massive
ingestion until call budget is understood"). No current-season date-safe path is built for any of
the 3 targets this phase -- all three are **PRIOR_SEASON_ONLY** (safe: rests entirely on a season
strictly before `as_of_season`; never leaks the in-progress season's own full-season totals).
This is an ACCEPTABLE, EXPLICITLY-SANCTIONED V1 outcome per this phase's own instruction ("Do not
chase perfect current-season freshness if the available evidence cannot support it without
leakage") -- not a shortfall to silently paper over, but the one honest, safe choice available
without a materially larger, separately-scoped ingestion phase.

============================ OVERLAY SCALE AUDIT (real, discovered, documented) ============================
Checked directly against `PlayerSimulationProfile`'s own existing 3 fields before overlaying
anything:
  - `ball_security_error_rate` (default 0.0086, HIGHER = WORSE, ALREADY CONSUMED by
    `possession_orchestrator.py`'s drive dispatch): real population median handling_error/touches
    is 0.0111 (measured directly, 2023-24) -- the SAME order of magnitude as the existing default.
    SCALE-COMPATIBLE. Overlaid as `1 - value` (this module's own `value` is ability-scale,
    higher=better; the engine field wants the raw, higher=worse error rate).
  - `playmaking_vision_shrunk_rate` (default None, DORMANT -- confirmed by direct source read:
    never referenced anywhere outside its own dataclass field definition and the `perceive()` call
    that always passes `None` for it). SCALE-COMPATIBLE BY CONSTRUCTION (no existing consumer to
    conflict with). Overlaid directly.
  - `passing_accuracy_ast_pct` (default 0.18, ACTIVELY CONSUMED by `pass_resolution.py`, whose own
    module docstring explicitly documents this field as "AST_PCT-based," with a small, flagged-weak
    `PASSING_ACCURACY_WEIGHT` calibrated around that ~0.10-0.30 real AST_PCT range). This module's
    OWN `passing_accuracy` construct (bad-pass-avoidance rate) has a real, measured population
    range of ~0.96-0.99 (median 0.98, 2023-24) -- a COMPLETELY DIFFERENT absolute scale from the
    field's own documented AST_PCT semantics and already-tuned weight. **REAL, DISCOVERED SCALE
    MISMATCH -- NOT overlaid this phase.** Forcing it in would silently change the calibrated
    behavior of an already-tested resolver formula, which this phase's own explicit instruction
    forbids ("Do not change the engine formulas themselves unless an actual mismatch in expected
    scale is discovered" -- discovered here, and the correct response is NOT to force the overlay,
    not to redesign `pass_resolution.py`). Classified SCALE_INCOMPATIBLE below, flagged for a
    dedicated future decision (either a new field, or a documented rescale/recalibration of
    `PASSING_ACCURACY_WEIGHT` against this construct's own real range).
"""
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import player_identity as pid
import player_scoring_truth_temporal as psst  # reused for the SAME provenance vocabulary, not redefined
from playmaking_estimation import PLAYMAKING_ATTRIBUTES, estimate_playmaking_attribute, resolve_params
from possession_orchestrator import PlayerSimulationProfile

SCHEMA_VERSION = "0.1.0-playmaking-truth"

_TARGET_TO_PROFILE_FIELD: Dict[str, Optional[str]] = {
    "passing_accuracy": None,  # SCALE_INCOMPATIBLE -- see module docstring; never overlaid
    "playmaking_vision": "playmaking_vision_shrunk_rate",
    "ball_security": "ball_security_error_rate",
}
# Fields whose engine convention is HIGHER = WORSE (a raw error rate) -- the overlay inverts this
# module's own ability-scale value (higher = better, matching every other truth estimate in this
# project) when writing to them. Never touches the estimate's own stored `value`.
_INVERTED_ON_OVERLAY = frozenset({"ball_security"})


@dataclass(frozen=True)
class PlaymakingTruthEstimate:
    """Same shape/contract as `player_scoring_truth.ScoringTruthEstimate` -- see that class's own
    docstring for the full PLAYER TRUTH vs MODEL BELIEF rationale, reused verbatim here rather than
    re-explained."""
    name: str
    player_id: str
    as_of_season: str
    value: Optional[float]       # ability-scale, real [0,1], higher = better, for ALL THREE targets
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
    def from_dict(d: dict) -> "PlaymakingTruthEstimate":
        return PlaymakingTruthEstimate(
            name=d["name"], player_id=d["player_id"], as_of_season=d["as_of_season"],
            value=d.get("value"), confidence=d.get("confidence"), sample_size=d.get("sample_size"),
            source=d["source"], param_source=d.get("param_source"),
            coverage_note=d.get("coverage_note", ""),
            provenance=d.get("provenance", "MULTI_SEASON_THROUGH_CUTOFF"),
        )


@dataclass(frozen=True)
class PlaymakingTruthProfile:
    """Same shape/contract as `player_scoring_truth.ScoringTruthProfile`."""
    player_id: str
    canonical_name: Optional[str]
    as_of_season: str
    identity_state: str
    estimates: Dict[str, PlaymakingTruthEstimate] = field(default_factory=dict)
    as_of_date: Optional[str] = None

    def value(self, name: str) -> Optional[float]:
        est = self.estimates.get(name)
        return est.value if est is not None else None

    def has_real_evidence(self, name: str) -> bool:
        return self.value(name) is not None

    def coverage_summary(self) -> Dict[str, bool]:
        return {name: self.has_real_evidence(name) for name in PLAYMAKING_ATTRIBUTES}

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
    def from_dict(d: dict) -> "PlaymakingTruthProfile":
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"PlaymakingTruthProfile.from_dict: schema_version mismatch -- stored "
                f"{d.get('schema_version')!r}, this code expects {SCHEMA_VERSION!r}."
            )
        return PlaymakingTruthProfile(
            player_id=d["player_id"], canonical_name=d.get("canonical_name"),
            as_of_season=d["as_of_season"], identity_state=d["identity_state"],
            estimates={name: PlaymakingTruthEstimate.from_dict(v) for name, v in d.get("estimates", {}).items()},
            as_of_date=d.get("as_of_date"),
        )


def _estimate(player_id: str, as_of_season: str, attribute: str, all_seasons: List[str]) -> PlaymakingTruthEstimate:
    source = "playmaking_estimation.estimate_playmaking_attribute"
    result = estimate_playmaking_attribute(player_id, as_of_season, attribute, all_seasons)
    if result.shrunk_rate is None:
        return PlaymakingTruthEstimate(
            name=attribute, player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=result.param_source,
            coverage_note="no real evidence (no season with real tracking data for this player through cutoff)",
            provenance=psst.MISSING,
        )
    _, prior_strength, _ = resolve_params(attribute)
    confidence = round(result.total_weight / (result.total_weight + prior_strength), 3) if prior_strength else None
    return PlaymakingTruthEstimate(
        name=attribute, player_id=player_id, as_of_season=as_of_season,
        value=result.shrunk_rate, confidence=confidence, sample_size=result.total_weight,
        source=source, param_source=result.param_source, provenance=psst.MULTI_SEASON_THROUGH_CUTOFF,
    )


def build_playmaking_truth_profile(player_id: str, as_of_season: str, all_seasons: List[str]) -> PlaymakingTruthProfile:
    """Season-level entry point -- direct id-keyed evidence (no name-resolution adapter needed;
    see playmaking_estimation.py's own module docstring), mirroring
    `player_scoring_truth.build_scoring_truth_profile`'s exact contract."""
    resolution = pid.resolve_id_to_name(player_id)
    estimates = {attr: _estimate(player_id, as_of_season, attr, all_seasons) for attr in PLAYMAKING_ATTRIBUTES}
    return PlaymakingTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates,
    )


def build_playmaking_truth_profile_as_of_date(player_id: str, as_of_date: str, as_of_season: str,
                                               all_seasons: List[str]) -> PlaymakingTruthProfile:
    """Pregame-safe entry point. All 3 targets are PRIOR_SEASON_ONLY this phase (see module
    docstring's temporal-status audit) -- built from the last FULLY COMPLETED season only, frozen
    for the whole current season, exactly like `player_scoring_truth_temporal.drive_aggression`'s
    own existing Option-B fallback. Safe (never leaks the in-progress season), honestly labeled
    (never presented as date-fresh)."""
    resolution = pid.resolve_id_to_name(player_id)
    reference_season = psst._season_before(as_of_season)
    reference_all_seasons = [s for s in all_seasons if s <= reference_season]

    estimates: Dict[str, PlaymakingTruthEstimate] = {}
    for attribute in PLAYMAKING_ATTRIBUTES:
        if not reference_all_seasons:
            estimates[attribute] = PlaymakingTruthEstimate(
                name=attribute, player_id=player_id, as_of_season=as_of_season,
                value=None, confidence=None, sample_size=None,
                source="player_playmaking_truth (no completed prior season)", param_source=None,
                coverage_note="no fully-completed prior season exists yet (rookie / first tracked season)",
                provenance=psst.MISSING,
            )
            continue
        base = _estimate(player_id, reference_season, attribute, reference_all_seasons)
        provenance = psst.MISSING if base.value is None else psst.PRIOR_SEASON_ONLY
        estimates[attribute] = replace(base, as_of_season=as_of_season, provenance=provenance)

    return PlaymakingTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates, as_of_date=as_of_date,
    )


def apply_playmaking_truth_to_simulation_profile(profile: PlayerSimulationProfile,
                                                  truth: PlaymakingTruthProfile) -> PlayerSimulationProfile:
    """Overlays ONLY `playmaking_vision_shrunk_rate` and `ball_security_error_rate` -- NEVER
    `passing_accuracy_ast_pct` (see module docstring's overlay scale audit: a real, discovered
    scale mismatch, deliberately not forced). A missing estimate leaves the corresponding field
    completely untouched (same "replace() only what's real" contract as
    `player_scoring_truth.apply_scoring_truth_to_simulation_profile`)."""
    overrides = {}
    for attribute, field_name in _TARGET_TO_PROFILE_FIELD.items():
        if field_name is None:
            continue
        value = truth.value(attribute)
        if value is None:
            continue
        overrides[field_name] = (1.0 - value) if attribute in _INVERTED_ON_OVERLAY else value
    return replace(profile, **overrides) if overrides else profile
