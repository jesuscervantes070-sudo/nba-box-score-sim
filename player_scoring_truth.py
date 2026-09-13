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

============================ TEMPORAL GRANULARITY -- HONEST LIMITATION ============================
Every one of the 8 target estimators is SEASON-LEVEL ONLY: `as_of_season="2024-25"` means "every
real game of the 2024-25 season that is cached," not "every real game through some specific date
within 2024-25." There is no game-log-level or date-filterable evidence source wired into any of
these estimators today (`loader.load_teams`/`load_player_advanced_stats`, `shot_zone_ingestion`,
`player_tendencies_analysis` all return one aggregated row per player-SEASON, never per-game).
Consequently: a `ScoringTruthProfile` built with `as_of_season="2024-25"` is SAFE for a
CROSS-SEASON forecast (predicting a 2025-26 game using only 2024-25-and-earlier evidence) but IS
NOT SAFE for an IN-SEASON PREGAME forecast of any 2024-25 game itself -- it would leak that same
game's own future-within-season evidence (and every other game played after it that season) into
the "belief as of" that game. This module does not solve that problem (no date-filterable source
exists yet to solve it with) -- it is flagged here explicitly as a real, current limitation for
the next phase (a game-log-level or PBP-date-level evidence source would be required), not
silently assumed away. `conceptual_key`/`to_dict` use `as_of_season` (never a date) for exactly
this reason -- adding a `as_of_date` field to the key today would imply a precision this module
cannot actually honor.
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

# Schema/methodology version for ScoringTruthProfile's serialized form -- bumped when the SHAPE
# changes (new/removed field, changed meaning of an existing field), never when only the
# underlying VALUES change (a new as_of_season is just a new profile at the same schema version).
# Same convention as player_ability_profile.SCHEMA_VERSION, kept as its own independent constant
# since this module's serialized shape is not that one's (engine-scale values, not 0-99 percentiles).
SCHEMA_VERSION = "0.1.0-scoring-truth"

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
    # "Pregame-Safe Scoring Truth" phase -- freshness/provenance label. One of
    # player_scoring_truth_temporal.{CURRENT_SEASON_PREGAME, PRIOR_SEASON_ONLY, MISSING} for a
    # date-level (pregame) estimate, or MULTI_SEASON_THROUGH_CUTOFF for the original season-level
    # `build_scoring_truth_profile`'s own estimates (kept as the default so every pre-existing
    # caller/serialized snapshot is unaffected -- this field is additive, not a breaking change).
    # Never left ambiguous: an estimate's freshness must always be inspectable, not inferred.
    provenance: str = "MULTI_SEASON_THROUGH_CUTOFF"

    def __post_init__(self):
        if self.kind not in ("ability", "tendency"):
            raise ValueError(f"kind must be 'ability' or 'tendency', got {self.kind!r}")
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0] or None -- got {self.confidence!r}")
        valid_provenance = {"CURRENT_SEASON_PREGAME", "PRIOR_SEASON_ONLY", "MULTI_SEASON_THROUGH_CUTOFF", "MISSING"}
        if self.provenance not in valid_provenance:
            raise ValueError(f"provenance must be one of {valid_provenance} -- got {self.provenance!r}")

    def to_dict(self) -> dict:
        """Deterministic, plain-JSON-serializable representation -- every field is already a
        str/float/None, no custom types, so `json.dumps(sort_keys=True)` of this dict is stable
        across processes/runs for identical input (see test_player_scoring_truth.py's own
        serialization-determinism guardrail)."""
        return {
            "name": self.name, "kind": self.kind, "player_id": self.player_id,
            "as_of_season": self.as_of_season, "value": self.value, "confidence": self.confidence,
            "sample_size": self.sample_size, "source": self.source, "param_source": self.param_source,
            "coverage_note": self.coverage_note, "provenance": self.provenance,
        }

    @staticmethod
    def from_dict(d: dict) -> "ScoringTruthEstimate":
        return ScoringTruthEstimate(
            name=d["name"], kind=d["kind"], player_id=d["player_id"], as_of_season=d["as_of_season"],
            value=d.get("value"), confidence=d.get("confidence"), sample_size=d.get("sample_size"),
            source=d["source"], param_source=d.get("param_source"), coverage_note=d.get("coverage_note", ""),
            provenance=d.get("provenance", "MULTI_SEASON_THROUGH_CUTOFF"),
        )


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
    # "Pregame-Safe Scoring Truth" phase -- additive, optional. None for every profile built by
    # `build_scoring_truth_profile` (season-level, unchanged) -- a real "YYYY-MM-DD" for one built
    # by `player_scoring_truth_temporal.build_scoring_truth_profile_as_of_date`. Deliberately NOT
    # required: this module never fabricates date-level precision for a season-level profile.
    as_of_date: Optional[str] = None

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

    @property
    def conceptual_key(self) -> Tuple[str, str, str]:
        """The stable identity of ONE profile: (player_id, as_of_date_or_season, model_version).
        Uses `as_of_date` when present (a pregame snapshot -- date-level precision is real and
        meaningful there) and falls back to `as_of_season` otherwise (a season-level snapshot --
        see module docstring's "TEMPORAL GRANULARITY" section for why this module never fabricates
        date precision it doesn't have). Two profiles with the same key are expected to be
        value-identical (same real inputs, same estimator code); this is the natural
        cache/snapshot key a future pregame-snapshot store should use."""
        return (self.player_id, self.as_of_date or self.as_of_season, SCHEMA_VERSION)

    def to_dict(self) -> dict:
        """Deterministic, plain-JSON-serializable representation. `schema_version` and the
        conceptual key's three components are all present explicitly (not just derivable) so a
        stored snapshot is self-describing even without importing this module. Field order is
        fixed (a plain dict literal); combined with `json.dumps(..., sort_keys=True)` at the
        call site, two calls on equal input produce byte-identical output."""
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
    def from_dict(d: dict) -> "ScoringTruthProfile":
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"ScoringTruthProfile.from_dict: schema_version mismatch -- stored "
                f"{d.get('schema_version')!r}, this code expects {SCHEMA_VERSION!r}. A schema "
                f"migration (not a silent reinterpretation) is required before loading this snapshot."
            )
        return ScoringTruthProfile(
            player_id=d["player_id"], canonical_name=d.get("canonical_name"),
            as_of_season=d["as_of_season"], identity_state=d["identity_state"],
            estimates={name: ScoringTruthEstimate.from_dict(v) for name, v in d.get("estimates", {}).items()},
            as_of_date=d.get("as_of_date"),
        )


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
