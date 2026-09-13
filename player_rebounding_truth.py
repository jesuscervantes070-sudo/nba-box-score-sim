"""
Rebounding Truth V1 -- truth-to-simulation-profile bridge.

Mirrors `player_scoring_truth.py`/`player_playmaking_truth.py`'s exact architecture and conventions
(same PLAYER TRUTH vs MODEL BELIEF contract, same MISSING != ZERO discipline, same provenance
vocabulary reused from `player_scoring_truth_temporal.py`) for the 2 new targets:
`offensive_rebounding`, `defensive_rebounding`. Deliberately NOT a copy-pasted parallel type family
with different field names -- `ReboundingTruthEstimate`/`ReboundingTruthProfile` below have the
SAME shape as `ScoringTruthEstimate`/`PlaymakingTruthEstimate` on purpose.

============================ TEMPORAL STATUS (checked directly before building) ============================
Both targets' real evidence source (`player_rebound_chances.json` -- real SportVU/Second Spectrum
`leaguedashptstats(Rebounding)` tracking, see `rebound_chance_ingestion.py`) is
**Classification B: season aggregate only** -- the same real endpoint family as
`player_passing_tracking.json`/`player_handling_exposure.json`, with the same real per-season
(not per-game/date-filterable) shape. No per-game breakdown of rebound CHANCES is cached or
reconstructable from any existing cheap source in this repo. Both targets are therefore
**PRIOR_SEASON_ONLY** (safe: rests entirely on a season strictly before `as_of_season`; never leaks
the in-progress season's own full-season totals) -- the same, explicitly-sanctioned V1 outcome as
every one of this project's other season-aggregate-only tracking sources ("Freshness is optional.
Leakage safety is mandatory.").

============================ OVERLAY SCALE AUDIT (real, discovered, documented) ============================
Checked directly against `PlayerSimulationProfile`'s own 2 existing fields, and against
`rebound_resolution.py`'s own calibrated reference constants, before overlaying anything:

  - `offensive_rebounding_shrunk_rate` / `defensive_rebounding_shrunk_rate` (defaults 0.08 / 0.15 in
    `PlayerSimulationProfile.synthetic()`; ALREADY LIVE -- consulted by `rebound_resolution.py`'s
    `_dispatch_rebound`/`_candidate_log_weight` for every real rebound opportunity, confirmed by
    direct source read). These fields' calibrated CENTERING constants
    (`rebound_resolution.OFFENSIVE_REBOUND_RATE_REFERENCE = 0.0482`,
    `DEFENSIVE_REBOUND_RATE_REFERENCE = 0.1313`) confirm the engine was tuned against
    `player_ability_estimation.py`'s EXISTING Phase 1-3 `offensive_rebounding`/`defensive_rebounding`
    attributes -- real OREB_PCT/DREB_PCT, a TEAM-CONTEXT share of rebounds available while the
    player is on the floor (a real, league-average-~4.8%/~13.1% scale).

    This module's OWN construct (real, individually-tracked OREB_CHANCE_PCT/DREB_CHANCE_PCT --
    see `rebounding_estimation.py`'s own module docstring for why this is the PREFERRED, stronger
    denominator per this phase's own explicit ranking) has a real, measured league-average scale of
    ~0.38-0.42 (offensive) and ~0.59-0.61 (defensive) -- roughly **7.8x** and **4.5x** the engine's
    calibrated reference constants respectively (both measured directly, 2023-24 reference
    population, `min_chances>=30`). **REAL, DISCOVERED SCALE MISMATCH for BOTH targets -- NEITHER
    is overlaid this phase.** Forcing either in would silently and substantially change the
    calibrated softmax competition `_candidate_log_weight` already tunes (a materially different
    centered-logit magnitude for essentially every real player), which this phase's own explicit
    instruction forbids ("Before overlaying anything, inspect what the frozen detailed engine
    expects... If the scale is incompatible: do NOT force an overlay. Report the mismatch
    honestly"). Classified SCALE_INCOMPATIBLE below for both targets.

    Note, for completeness: as of this phase, NOTHING in this repository writes real per-player
    values into `offensive_rebounding_shrunk_rate`/`defensive_rebounding_shrunk_rate` at all
    (confirmed by direct grep -- only `possession_orchestrator.py`'s own dataclass definition and
    test files reference these field names; no existing overlay module). This phase's non-overlay
    decision therefore does not remove any pre-existing real coverage -- it declines to ADD a
    scale-incompatible one. A real, engine-scale-compatible overlay is possible in a future phase
    by reusing `player_ability_estimation.py`'s EXISTING, already-tested
    `offensive_rebounding`/`defensive_rebounding` attributes (via `player_identity.estimate_attribute_by_id`)
    -- see this phase's report Sec. Z for the explicit recommendation; deliberately NOT done in this
    pass, to avoid quietly mixing two different real constructs under one "truth" value without a
    dedicated, explicit decision to do so.
"""
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import player_identity as pid
import player_scoring_truth_temporal as psst  # reused for the SAME provenance vocabulary, not redefined
from rebounding_estimation import REBOUNDING_ATTRIBUTES, estimate_rebounding_attribute, resolve_params
from possession_orchestrator import PlayerSimulationProfile

SCHEMA_VERSION = "0.1.0-rebounding-truth"

# Both targets are SCALE_INCOMPATIBLE with the engine's current calibrated fields -- see module
# docstring's overlay scale audit. Neither is overlaid this phase.
_TARGET_TO_PROFILE_FIELD: Dict[str, Optional[str]] = {
    "offensive_rebounding": None,
    "defensive_rebounding": None,
}


@dataclass(frozen=True)
class ReboundingTruthEstimate:
    """Same shape/contract as `player_scoring_truth.ScoringTruthEstimate` /
    `player_playmaking_truth.PlaymakingTruthEstimate` -- see those classes' own docstrings for the
    full PLAYER TRUTH vs MODEL BELIEF rationale, reused verbatim here rather than re-explained."""
    name: str
    player_id: str
    as_of_season: str
    value: Optional[float]       # ability-scale, real [0,1] chance-conversion rate, higher = better
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
    def from_dict(d: dict) -> "ReboundingTruthEstimate":
        return ReboundingTruthEstimate(
            name=d["name"], player_id=d["player_id"], as_of_season=d["as_of_season"],
            value=d.get("value"), confidence=d.get("confidence"), sample_size=d.get("sample_size"),
            source=d["source"], param_source=d.get("param_source"),
            coverage_note=d.get("coverage_note", ""),
            provenance=d.get("provenance", "MULTI_SEASON_THROUGH_CUTOFF"),
        )


@dataclass(frozen=True)
class ReboundingTruthProfile:
    """Same shape/contract as `player_scoring_truth.ScoringTruthProfile` /
    `player_playmaking_truth.PlaymakingTruthProfile`."""
    player_id: str
    canonical_name: Optional[str]
    as_of_season: str
    identity_state: str
    estimates: Dict[str, ReboundingTruthEstimate] = field(default_factory=dict)
    as_of_date: Optional[str] = None

    def value(self, name: str) -> Optional[float]:
        est = self.estimates.get(name)
        return est.value if est is not None else None

    def has_real_evidence(self, name: str) -> bool:
        return self.value(name) is not None

    def coverage_summary(self) -> Dict[str, bool]:
        return {name: self.has_real_evidence(name) for name in REBOUNDING_ATTRIBUTES}

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
    def from_dict(d: dict) -> "ReboundingTruthProfile":
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"ReboundingTruthProfile.from_dict: schema_version mismatch -- stored "
                f"{d.get('schema_version')!r}, this code expects {SCHEMA_VERSION!r}."
            )
        return ReboundingTruthProfile(
            player_id=d["player_id"], canonical_name=d.get("canonical_name"),
            as_of_season=d["as_of_season"], identity_state=d["identity_state"],
            estimates={name: ReboundingTruthEstimate.from_dict(v) for name, v in d.get("estimates", {}).items()},
            as_of_date=d.get("as_of_date"),
        )


def _estimate(player_id: str, as_of_season: str, attribute: str, all_seasons: List[str]) -> ReboundingTruthEstimate:
    source = "rebounding_estimation.estimate_rebounding_attribute"
    result = estimate_rebounding_attribute(player_id, as_of_season, attribute, all_seasons)
    if result.shrunk_rate is None:
        return ReboundingTruthEstimate(
            name=attribute, player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=result.param_source,
            coverage_note="no real evidence (no season with real rebound-chance tracking for this player through cutoff)",
            provenance=psst.MISSING,
        )
    _, prior_strength, _ = resolve_params(attribute)
    confidence = round(result.total_weight / (result.total_weight + prior_strength), 3) if prior_strength else None
    return ReboundingTruthEstimate(
        name=attribute, player_id=player_id, as_of_season=as_of_season,
        value=result.shrunk_rate, confidence=confidence, sample_size=result.total_weight,
        source=source, param_source=result.param_source, provenance=psst.MULTI_SEASON_THROUGH_CUTOFF,
    )


def build_rebounding_truth_profile(player_id: str, as_of_season: str, all_seasons: List[str]) -> ReboundingTruthProfile:
    """Season-level entry point -- direct id-keyed evidence (no name-resolution adapter needed; see
    rebounding_estimation.py's own module docstring), mirroring
    `player_playmaking_truth.build_playmaking_truth_profile`'s exact contract."""
    resolution = pid.resolve_id_to_name(player_id)
    estimates = {attr: _estimate(player_id, as_of_season, attr, all_seasons) for attr in REBOUNDING_ATTRIBUTES}
    return ReboundingTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates,
    )


def build_rebounding_truth_profile_as_of_date(player_id: str, as_of_date: str, as_of_season: str,
                                               all_seasons: List[str]) -> ReboundingTruthProfile:
    """Pregame-safe entry point. Both targets are PRIOR_SEASON_ONLY this phase (see module
    docstring's temporal-status audit) -- built from the last FULLY COMPLETED season only, frozen
    for the whole current season, exactly like `player_playmaking_truth_profile_as_of_date`'s own
    Option-B fallback. Safe (never leaks the in-progress season), honestly labeled (never presented
    as date-fresh)."""
    resolution = pid.resolve_id_to_name(player_id)
    reference_season = psst._season_before(as_of_season)
    reference_all_seasons = [s for s in all_seasons if s <= reference_season]

    estimates: Dict[str, ReboundingTruthEstimate] = {}
    for attribute in REBOUNDING_ATTRIBUTES:
        if not reference_all_seasons:
            estimates[attribute] = ReboundingTruthEstimate(
                name=attribute, player_id=player_id, as_of_season=as_of_season,
                value=None, confidence=None, sample_size=None,
                source="player_rebounding_truth (no completed prior season)", param_source=None,
                coverage_note="no fully-completed prior season exists yet (rookie / first tracked season)",
                provenance=psst.MISSING,
            )
            continue
        base = _estimate(player_id, reference_season, attribute, reference_all_seasons)
        provenance = psst.MISSING if base.value is None else psst.PRIOR_SEASON_ONLY
        estimates[attribute] = replace(base, as_of_season=as_of_season, provenance=provenance)

    return ReboundingTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates, as_of_date=as_of_date,
    )


def apply_rebounding_truth_to_simulation_profile(profile: PlayerSimulationProfile,
                                                  truth: ReboundingTruthProfile) -> PlayerSimulationProfile:
    """Overlays NEITHER `offensive_rebounding_shrunk_rate` NOR `defensive_rebounding_shrunk_rate`
    this phase (see module docstring's overlay scale audit: a real, discovered scale mismatch for
    both targets, deliberately not forced). Kept as a real function (rather than omitted entirely)
    for interface consistency with `apply_scoring_truth_to_simulation_profile`/
    `apply_playmaking_truth_to_simulation_profile`, and so a future phase that resolves the scale
    question only needs to populate `_TARGET_TO_PROFILE_FIELD`, not add a new call site."""
    overrides = {}
    for attribute, field_name in _TARGET_TO_PROFILE_FIELD.items():
        if field_name is None:
            continue
        value = truth.value(attribute)
        if value is None:
            continue
        overrides[field_name] = value
    return replace(profile, **overrides) if overrides else profile
