"""
Real Player / Team Truth phase -- first production-quality bridge from the real, per-attribute/
per-tendency estimator layer to the detailed engine's own consumption shape
(`possession_orchestrator.PlayerSimulationProfile`).

SCOPE THIS PASS (explicit): SCORING ABILITY (`rim_finishing`, `floater_short_mid`, `midrange`,
`three_point`, `free_throw`) + BASIC SCORING TENDENCIES (`three_point_preference`,
`midrange_preference`, `drive_aggression`) ONLY. Defense, passing, rebounding, role clustering,
injuries, team tactics, and fast-engine (`game_engine.py`) synchronization are explicitly NOT
built here -- every other `PlayerSimulationProfile` field keeps using
`PlayerSimulationProfile.synthetic()`'s own existing league-average default, clearly labeled as
such (never silently presented as real evidence). This module establishes the GENERAL PATTERN
later attribute passes (defense, passing, rebounding) will reuse -- one `ScoringTruthEstimate` per
target field, one join function, one profile-construction function.

============================ PLAYER TRUTH vs MODEL BELIEF ============================
PLAYER TRUTH is the latent basketball state we believe exists (a real player's true underlying
conversion rate or shot-selection propensity) -- never directly observed. MODEL BELIEF is this
project's own POSTERIOR estimate of that truth given evidence available as of `as_of_season`
(`player_ability_estimation.py`'s `_weighted_shrunk_estimate` / `player_tendencies_estimation.py`'s
Bayesian shrinkage). `ScoringTruthEstimate` below is the belief-layer's own record for ONE target
field -- posterior VALUE, CONFIDENCE, EFFECTIVE SAMPLE SIZE, AS-OF-SEASON, and SOURCE/PARAM
PROVENANCE -- kept alongside, not discarded when, the bare scalar is extracted for the engine.
`PlayerSimulationProfile` (the detailed engine's own dataclass, unchanged, unmodified) has no
uncertainty field today -- the engine only ever consumes the posterior MEAN. This is a real,
ACKNOWLEDGED simplification, not an architectural dead end: `ScoringTruthProfile` retains full
posterior/uncertainty/provenance for every field, so a future simulation layer that wants to
sample from (rather than point-estimate) a real posterior has everything it needs without
re-deriving it -- uncertainty is not architecturally impossible to add later, it is simply not
yet READ by the engine.

MISSING != ZERO: a target field with no real evidence (the underlying estimator's own shrunk
value is `None` -- e.g. a rookie season, or a player who never appears in a cached season) is left
`None` in `ScoringTruthEstimate.value`, in `ScoringTruthProfile`'s own convenience accessor, and in
the resulting `PlayerSimulationProfile` field. This module NEVER substitutes 0.0 or a synthetic
default for one of the 8 TARGET fields -- whether an engine run can proceed with a `None` target
field is the ENGINE's own existing, pre-existing decision (`_require`/`_require_ft_rate` already
raise explicitly there rather than silently defaulting); this module adds no hidden fallback for
them. (Non-target fields, explicitly out of scope this pass, DO use the synthetic default -- see
`build_partial_simulation_profile`'s own docstring for why that is a different, honestly-labeled
case.)

NO TEMPORAL LEAKAGE: every value here comes from an estimator (`player_ability_estimation.py`,
`shot_zone_estimation.py`, `player_tendencies_estimation.py`) that already enforces its own
`as_of_season` cutoff (`_seasons_through_cutoff` / `TENDENCY_FIRST_SEASON` gating). This module
performs NO season filtering of its own -- it passes `as_of_season`/`all_seasons` straight through
to each sub-call, unmodified, adding no new leakage surface (the same discipline
`player_identity.get_unified_player_context` already documents for its own fan-out).

TRUE != STATIC: every function here takes `as_of_season` explicitly; a profile for the same
player at two different `as_of_season` values is two different, independently-computed
`ScoringTruthProfile` objects, never one mutated in place.
"""
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import player_identity as pid
from possession_orchestrator import PlayerSimulationProfile

# ---------------------------------------------------------------------
# Target fields this phase covers. Each maps to (estimator_kind, PlayerSimulationProfile field
# name) -- the SINGLE source of truth for "what this module builds", reused by every function
# below rather than re-listing the 8 names ad hoc.
# ---------------------------------------------------------------------
ABILITY_TARGETS: Tuple[str, ...] = ("rim_finishing", "floater_short_mid", "midrange", "three_point", "free_throw")
TENDENCY_TARGETS: Tuple[str, ...] = ("three_point_preference", "midrange_preference", "drive_aggression")

_SHOT_ZONE_ABILITIES = frozenset({"rim_finishing", "floater_short_mid", "midrange"})

_ABILITY_TO_PROFILE_FIELD: Dict[str, str] = {
    "rim_finishing": "rim_finishing_shrunk_rate",
    "floater_short_mid": "floater_short_mid_shrunk_rate",
    "midrange": "midrange_shrunk_rate",
    "three_point": "three_point_shrunk_rate",
    "free_throw": "free_throw_shrunk_rate",
}
_TENDENCY_TO_PROFILE_FIELD: Dict[str, str] = {
    "three_point_preference": "three_point_preference",
    "midrange_preference": "midrange_preference",
    "drive_aggression": "drive_aggression",
}


@dataclass(frozen=True)
class ScoringTruthEstimate:
    """One target field's MODEL BELIEF as of one season -- the posterior mean the engine will
    consume, paired with how much to trust it. Mirrors `player_ability_profile.AttributeEstimate`'s
    own "never a bare number" contract, but on the engine's native [rate | logit-deviation] scale
    rather than a 0-99 display percentile (that percentile mapping is a SEPARATE, display-layer
    concept `AttributeEstimate`/`result_to_attribute_estimate` already own; this module never
    recomputes or duplicates it)."""
    name: str                       # one of ABILITY_TARGETS / TENDENCY_TARGETS
    kind: str                       # "ability" or "tendency"
    player_id: str
    as_of_season: str
    value: Optional[float]          # shrunk_rate (ability, real [0,1]) or latent_propensity (tendency,
                                     # logit-relative-to-contemporaneous-league-average) -- None = no real evidence
    confidence: Optional[float]     # 0-1, shrinkage-weight-based; None if value is None
    sample_size: Optional[float]    # real effective evidence weight (total_weight / exposure) behind value
    source: str                     # which estimator module/function produced this
    param_source: Optional[str]     # "calibrated" / "provisional", where the underlying estimator reports one
    coverage_note: str = ""         # human-readable reason when value is None (thin evidence, before data floor, etc.)

    def __post_init__(self):
        if self.kind not in ("ability", "tendency"):
            raise ValueError(f"kind must be 'ability' or 'tendency', got {self.kind!r}")
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0] or None -- got {self.confidence!r}")


@dataclass(frozen=True)
class ScoringTruthProfile:
    """The full, real-evidence-only scoring-truth record for ONE player_id as of ONE
    as_of_season -- the object later attribute passes (defense/passing/rebounding) should mirror
    the shape of, not this module's own private detail. `estimates` is keyed by target name
    (ABILITY_TARGETS + TENDENCY_TARGETS); a name absent from it (rather than present with
    value=None) never happens -- every target always gets an entry, real evidence or not, so a
    caller can always ask "what did we find, and why" for any of the 8 fields."""
    player_id: str
    canonical_name: Optional[str]
    as_of_season: str
    identity_state: str
    estimates: Dict[str, ScoringTruthEstimate] = field(default_factory=dict)

    def value(self, name: str) -> Optional[float]:
        """The engine-ready posterior mean for `name`, or None if unestimated -- the ONE
        convenience accessor callers should use rather than reaching into `estimates` directly."""
        est = self.estimates.get(name)
        return est.value if est is not None else None

    def has_real_evidence(self, name: str) -> bool:
        return self.value(name) is not None

    def coverage_summary(self) -> Dict[str, bool]:
        """One line per target: whether real evidence exists. A quick, honest "how complete is
        this profile" report -- never used to decide whether to fabricate a missing value."""
        return {name: self.has_real_evidence(name) for name in (*ABILITY_TARGETS, *TENDENCY_TARGETS)}


def _ability_estimate(player_id: str, as_of_season: str, attribute: str,
                       all_seasons: List[str]) -> ScoringTruthEstimate:
    if attribute in _SHOT_ZONE_ABILITIES:
        resolution, result = pid.estimate_shot_zone_attribute_by_id(player_id, as_of_season, attribute, all_seasons)
        source = "shot_zone_estimation.estimate_shot_zone_attribute"
    else:
        resolution, result = pid.estimate_attribute_by_id(player_id, as_of_season, attribute, all_seasons)
        source = "player_ability_estimation.estimate_attribute"

    if resolution.state != pid.RESOLVED:
        return ScoringTruthEstimate(
            name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note=f"identity not resolved ({resolution.state}): {resolution.note or ''}".strip(),
        )
    if result is None or result.shrunk_rate is None:
        return ScoringTruthEstimate(
            name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=(result.total_weight if result is not None else None),
            source=source, param_source=(result.param_source if result is not None else None),
            coverage_note="no real evidence for this player-season through the as-of cutoff",
        )
    # Confidence mirrors shot_zone_estimation.result_to_attribute_estimate's own shrinkage-weight
    # definition (total real evidence weight / (that weight + the prior's own weight)) -- reused,
    # not reinvented, so ability confidence is comparable across the display-percentile path and
    # this engine-scale path.
    if attribute in _SHOT_ZONE_ABILITIES:
        import shot_zone_estimation as sze
        _, prior_strength, _ = sze.resolve_params(attribute)
    else:
        import player_ability_estimation as pae
        _, prior_strength, _ = pae.resolve_params(attribute)
    confidence = round(result.total_weight / (result.total_weight + prior_strength), 3) if prior_strength else None
    return ScoringTruthEstimate(
        name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
        value=result.shrunk_rate, confidence=confidence, sample_size=result.total_weight,
        source=source, param_source=result.param_source,
    )


def _tendency_estimate(player_id: str, as_of_season: str, tendency: str,
                        all_seasons: List[str]) -> ScoringTruthEstimate:
    resolution, result = pid.estimate_tendency_by_id(player_id, as_of_season, all_seasons, tendency)
    source = "player_tendencies_estimation.estimate_tendency"
    if resolution.state != pid.RESOLVED:
        return ScoringTruthEstimate(
            name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note=f"identity not resolved ({resolution.state}): {resolution.note or ''}".strip(),
        )
    if result is None or result.latent_propensity is None:
        return ScoringTruthEstimate(
            name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=(result.exposure if result is not None else None),
            source=source, param_source=None,
            coverage_note=(result.coverage_note if result is not None else "unresolved identity"),
        )
    # Tendency confidence has no single shrinkage-prior scalar exposed the way ability estimators
    # do -- `PlayerTendencyEstimate.confidence` is already a real, estimator-owned label
    # ("high"/"medium"/"low"/"none"); mapped onto [0,1] here ONLY as a coarse, documented ordinal
    # so `ScoringTruthEstimate.confidence` stays on one consistent numeric scale across abilities
    # and tendencies -- never treated as a probability.
    confidence_map = {"high": 0.9, "medium": 0.6, "low": 0.3, "none": 0.0}
    return ScoringTruthEstimate(
        name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
        value=result.latent_propensity, confidence=confidence_map.get(result.confidence),
        sample_size=result.exposure, source=source, param_source=None,
        coverage_note=result.coverage_note,
    )


def build_scoring_truth_profile(player_id: str, as_of_season: str, all_seasons: List[str]) -> ScoringTruthProfile:
    """The one public entry point. Real evidence only, real identity resolution only, no
    fabricated values -- see module docstring for the full MISSING != ZERO / NO TEMPORAL LEAKAGE
    contract. Every one of the 8 target fields is always represented in the returned
    `estimates` dict (with `value=None` when unestimated), never silently omitted."""
    resolution = pid.resolve_id_to_name(player_id)
    estimates: Dict[str, ScoringTruthEstimate] = {}
    for attribute in ABILITY_TARGETS:
        estimates[attribute] = _ability_estimate(player_id, as_of_season, attribute, all_seasons)
    for tendency in TENDENCY_TARGETS:
        estimates[tendency] = _tendency_estimate(player_id, as_of_season, tendency, all_seasons)
    return ScoringTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates,
    )


def apply_scoring_truth_to_simulation_profile(profile: PlayerSimulationProfile,
                                               truth: ScoringTruthProfile) -> PlayerSimulationProfile:
    """Overlays ONLY the 8 target fields from `truth` onto an existing `PlayerSimulationProfile`
    (via `dataclasses.replace` -- `PlayerSimulationProfile` is frozen, this never mutates the
    input) -- a field with `truth.value(name) is None` is left completely UNTOUCHED on `profile`
    (not set to None, not zeroed) so a caller can layer real evidence over an existing baseline
    (e.g. `PlayerSimulationProfile.synthetic()`'s own league-average defaults for every non-target
    field, and for a target field this player genuinely has no real evidence for) without ever
    overwriting a good value with a missing one."""
    overrides = {}
    for ability, field_name in _ABILITY_TO_PROFILE_FIELD.items():
        value = truth.value(ability)
        if value is not None:
            overrides[field_name] = value
    for tendency, field_name in _TENDENCY_TO_PROFILE_FIELD.items():
        value = truth.value(tendency)
        if value is not None:
            overrides[field_name] = value
    return replace(profile, **overrides) if overrides else profile


def build_partial_simulation_profile(player_id: str, team_id: str, as_of_season: str,
                                      all_seasons: List[str]) -> Tuple[ScoringTruthProfile, PlayerSimulationProfile]:
    """Convenience wrapper for a caller that wants an ENGINE-READY `PlayerSimulationProfile`
    directly: builds the real `ScoringTruthProfile`, then overlays it onto
    `PlayerSimulationProfile.synthetic()`'s own existing league-average baseline (used here ONLY
    as the explicitly-labeled, out-of-scope-this-pass fallback for the fields this phase does not
    estimate -- defense, passing, rebounding, remaining tendencies/role -- never presented as real
    evidence; see `ScoringTruthProfile.coverage_summary()` to see exactly which of the 8 target
    fields are real vs still falling back to that same synthetic default because this specific
    player-season has no real evidence). Returns BOTH objects -- the truth layer is never
    discarded, so a caller can inspect confidence/coverage even after using the engine-ready
    profile."""
    truth = build_scoring_truth_profile(player_id, as_of_season, all_seasons)
    baseline = PlayerSimulationProfile.synthetic(player_id, team_id)
    return truth, apply_scoring_truth_to_simulation_profile(baseline, truth)
