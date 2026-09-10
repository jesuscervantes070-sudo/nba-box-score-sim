"""
Foundational representation for the player-ability architecture.

This file implements ONLY the first stage of the project's stated pipeline:

    Raw Statistical Evidence
      -> Stabilized PlayerAbilityProfile   <-- THIS FILE
      -> PlayerSeasonContext
      -> PlayerSimulationProfile
      -> Existing Game Engine

FOUNDATIONAL ONLY -- no attribute is calculated here, no rating is
produced, and nothing in this file is imported by main.py, season.py,
game_engine.py, ratings.py, awards.py, transactions.py, or
counterfactual.py. It exists so future attribute-calculation work has
a real, typed, versioned place to write into, per the project's own
explicit scoping for this phase.

WHY A SEPARATE REPRESENTATION, NOT models.Player: Player currently
conflates four different concepts into one real per-game stat line --
intrinsic ability, this season's role/usage, this season's workload,
and this season's realized performance. That conflation is exactly
right for the historical replay path (a real season's box score IS
all four of those at once, for that one real season) and this file
does not touch or replace it. A PORTABLE ability estimate is a
different question -- "what does this player carry with him" -- which
needs its own representation, evidenced from MULTIPLE seasons, kept
separate from any one season's role/context. See PlayerSeasonContext
and PlayerSimulationProfile (later pipeline stages, NOT built yet) for
where role/usage/workload will eventually live.

STABLE IDENTITY (investigated before writing this file): the project's
per-season stat files (rosters.json, player_advanced.json, and every
loader.py function built on them) key EVERYTHING by player NAME --
no numeric ID anywhere in that path. A real, stable NBA player_id DOES
exist elsewhere in this codebase's own cache -- data_source.py's real
transaction feed (cache/<season>/transactions.json) carries the NBA's
own PLAYER_ID per row -- but that feed only starts 2015-16 (see
data_source.fetch_player_transactions's own docstring), and nothing
currently builds a name -> player_id crosswalk from it. Per this
phase's explicit instruction not to force a disruptive migration:
`player_id` below is Optional[str] and simply unset for now. `name` is
the real, load-bearing key today, same as everywhere else in this
project -- documented as a known limitation (see the module-level
KNOWN_LIMITATIONS note near the bottom), not silently worked around.

GUARDRAILS FOR WHOEVER CALCULATES ATTRIBUTES NEXT (not enforced by
this file, since it does no calculation -- recorded here because this
is the natural place a future implementer will read first): do not
derive any SKILL_ATTRIBUTES or ABILITY_OUTPUTS value from team win%,
PIE, net_rating, awards.mvp_score, awards.dpoy_score, or any other
context-heavy metric already shown (see ratings.py's own validation
history) to encode team success rather than intrinsic individual
skill. Those are legitimate inputs for a SEASON-CONTEXT or AWARD-VALUE
concept, not for a portable ability estimate.
"""
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Tuple

# Schema/methodology version for this representation. Bump this
# whenever the SHAPE of PlayerAbilityProfile or AttributeEstimate
# changes (new fields, changed meaning of an existing field) -- NOT
# when the underlying VALUES change (a new season's estimates are
# just a new profile with the same model_version). Kept as a plain
# module constant, not computed, so it's visible at a glance.
SCHEMA_VERSION = "0.1.0-foundation"

# ---------------------------------------------------------------------
# The ~18 skill attributes the project's design calls for, grouped
# exactly as specified. This tuple is the single source of truth for
# "what attributes exist" -- PlayerAbilityProfile validates against it
# rather than accepting any string key, so a typo'd attribute name
# fails loudly instead of silently creating a new, never-read field.
# ---------------------------------------------------------------------
SCORING_ATTRIBUTES: Tuple[str, ...] = (
    "rim_finishing", "floater_short_mid", "midrange", "three_point",
    "free_throw", "shot_creation", "foul_drawing",
)
PLAYMAKING_ATTRIBUTES: Tuple[str, ...] = (
    "passing", "creation_for_others", "ball_security",
)
DEFENSE_ATTRIBUTES: Tuple[str, ...] = (
    "perimeter_defense", "interior_defense", "rim_protection",
    "defensive_playmaking", "defensive_versatility", "foul_discipline",
)
REBOUNDING_ATTRIBUTES: Tuple[str, ...] = (
    "offensive_rebounding", "defensive_rebounding",
)
SKILL_ATTRIBUTES: Tuple[str, ...] = (
    SCORING_ATTRIBUTES + PLAYMAKING_ATTRIBUTES + DEFENSE_ATTRIBUTES + REBOUNDING_ATTRIBUTES
)  # 7 + 3 + 6 + 2 = 18, matching the project's "approximately 18 skill attributes"

# Composite OUTPUTS, not raw skills -- kept in a separate namespace
# from SKILL_ATTRIBUTES so a future formula can't accidentally treat
# "overall_ability" as one more input alongside the skills it's
# derived FROM. No formula for these is designed or implemented here.
ABILITY_OUTPUTS: Tuple[str, ...] = (
    "offensive_ability", "defensive_ability", "overall_ability",
)

# Candidate role archetypes named in the project's design -- kept as
# plain string keys (not an enum) so a future phase can add archetypes
# without a schema migration. Deliberately a SEPARATE structure
# (RoleProfile) from PlayerAbilityProfile's skill attributes -- role is
# "how a team plausibly uses this player," ability is "what he can do
# regardless of usage," and conflating them was the exact problem this
# whole redesign exists to undo.
ROLE_ARCHETYPES: Tuple[str, ...] = (
    "primary_offensive_engine", "off_ball_spacer", "versatile_connector",
    "rim_runner", "defensive_anchor",
)

_MIN_RATING, _MAX_RATING = 0.0, 99.0  # same 0-99 scale ratings.py already uses elsewhere in this project


@dataclass(frozen=True)
class AttributeEstimate:
    """
    One attribute's estimated value, ALWAYS paired with how much to
    trust it -- never a bare number. `value=None` is a first-class,
    explicit state ("not yet estimated"), distinct from `value=0.0`
    ("estimated to be the worst possible") -- the project's own
    requirement, and a real, checked failure mode elsewhere in this
    project's history (an unmeasured signal silently defaulting to a
    real, misleading number) is exactly what this guards against.

    Frozen (immutable) -- an estimate is a snapshot of evidence as of
    when it was computed, not something later code should mutate in
    place; a REVISED estimate is a new AttributeEstimate, not an edit.
    """
    value: Optional[float] = None
    # 0.0-1.0 confidence in `value`, or None if no confidence model
    # exists yet for this attribute (which is the honest default right
    # now -- nothing calculates these yet). NOT the same axis as
    # sample_size below: a huge sample can still be low-confidence if
    # the underlying proxy stat is a weak signal for this attribute.
    confidence: Optional[float] = None
    # Real count (games, possessions, shot attempts -- whatever unit
    # this specific attribute's eventual evidence uses) behind `value`,
    # or None if unknown/not tracked. Kept as a plain int rather than
    # folded into `confidence` so a future implementer can see WHY
    # confidence is low (thin sample vs. a genuinely noisy proxy).
    sample_size: Optional[int] = None

    def __post_init__(self):
        if self.value is not None and not (_MIN_RATING <= self.value <= _MAX_RATING):
            raise ValueError(
                f"AttributeEstimate.value must be within [{_MIN_RATING}, {_MAX_RATING}] "
                f"or None (not yet estimated) -- got {self.value!r}"
            )
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"AttributeEstimate.confidence must be within [0.0, 1.0] or None -- got {self.confidence!r}")
        if self.sample_size is not None and self.sample_size < 0:
            raise ValueError(f"AttributeEstimate.sample_size must be >= 0 or None -- got {self.sample_size!r}")

    @property
    def is_estimated(self) -> bool:
        """False means 'no honest answer yet' -- callers should treat
        this the same way ratings.py's own None-returning ratings are
        treated: display '--' or skip, never substitute a guessed
        number."""
        return self.value is not None

    def to_dict(self) -> dict:
        return {"value": self.value, "confidence": self.confidence, "sample_size": self.sample_size}

    @staticmethod
    def from_dict(d: dict) -> "AttributeEstimate":
        return AttributeEstimate(value=d.get("value"), confidence=d.get("confidence"), sample_size=d.get("sample_size"))


# A single, shared "not yet estimated" sentinel -- every attribute
# starts here, not at value=0.0. Reused rather than constructed fresh
# each time since AttributeEstimate is frozen/immutable.
UNESTIMATED = AttributeEstimate()


@dataclass(frozen=True)
class RoleProfile:
    """
    Future role/archetype probabilities or scores -- kept structurally
    separate from PlayerAbilityProfile's skill attributes (see
    ROLE_ARCHETYPES's comment for why). Empty by default: no role
    calculation exists yet, this is only the shape it will eventually
    fill.
    """
    as_of_season: str
    scores: Dict[str, float] = field(default_factory=dict)  # archetype name -> 0.0-1.0, keys checked against ROLE_ARCHETYPES

    def __post_init__(self):
        for key, val in self.scores.items():
            if key not in ROLE_ARCHETYPES:
                raise ValueError(f"Unknown role archetype {key!r} -- must be one of {ROLE_ARCHETYPES}")
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"Role score for {key!r} must be within [0.0, 1.0] -- got {val!r}")

    def to_dict(self) -> dict:
        return {"as_of_season": self.as_of_season, "scores": dict(self.scores)}

    @staticmethod
    def from_dict(d: dict) -> "RoleProfile":
        return RoleProfile(as_of_season=d["as_of_season"], scores=dict(d.get("scores", {})))


@dataclass
class PlayerAbilityProfile:
    """
    A stabilized, multi-year estimate of a player's intrinsic ability --
    NOT tied to any one season's role, usage, or team context. See the
    module docstring for the full design rationale and what this
    deliberately does not do yet (no attribute calculation, no
    integration with the existing sim).

    Every SKILL_ATTRIBUTES / ABILITY_OUTPUTS entry is an
    AttributeEstimate, not a bare float -- a missing key (or a key
    present with value=None) means "not yet estimated," and callers
    MUST treat that as distinct from a real, low estimate. See
    get_attribute/set_attribute for the one supported way to read/
    write these dicts, rather than mutating `attributes` directly.
    """
    # ---- Identity / provenance ----
    name: str  # the real, load-bearing key today -- see the module docstring's STABLE IDENTITY section
    as_of_season: str  # last real season's evidence folded into this profile, e.g. "2024-25"
    model_version: str = SCHEMA_VERSION
    player_id: Optional[str] = None  # real NBA player_id when a future crosswalk fills it in; see module docstring
    source_cutoff: Optional[str] = None  # latest real date/game this evidence includes, if tracked
    evidence_strength: Optional[float] = None  # None = unknown; a future overall "how much real data backs this profile" signal, not yet defined
    uncertainty_note: Optional[str] = None  # free-text -- no formal uncertainty model exists yet

    attributes: Dict[str, AttributeEstimate] = field(default_factory=dict)
    ability_outputs: Dict[str, AttributeEstimate] = field(default_factory=dict)
    role: Optional[RoleProfile] = None

    def __post_init__(self):
        for key in self.attributes:
            if key not in SKILL_ATTRIBUTES:
                raise ValueError(f"Unknown skill attribute {key!r} -- must be one of SKILL_ATTRIBUTES")
        for key in self.ability_outputs:
            if key not in ABILITY_OUTPUTS:
                raise ValueError(f"Unknown ability output {key!r} -- must be one of ABILITY_OUTPUTS")

    # ---- Reading ----
    def get_attribute(self, name: str) -> AttributeEstimate:
        """Always returns an AttributeEstimate -- UNESTIMATED (value=None)
        if `name` was never set, never a bare 0.0. Raises on an unknown
        attribute name (a typo), the same "fail loud, don't silently
        invent a field" rule __post_init__ already enforces."""
        if name not in SKILL_ATTRIBUTES:
            raise ValueError(f"Unknown skill attribute {name!r}")
        return self.attributes.get(name, UNESTIMATED)

    def get_ability_output(self, name: str) -> AttributeEstimate:
        if name not in ABILITY_OUTPUTS:
            raise ValueError(f"Unknown ability output {name!r}")
        return self.ability_outputs.get(name, UNESTIMATED)

    # ---- Writing (returns a NEW profile -- this dataclass's nested
    # dicts are mutable for practicality, but the supported pattern is
    # "replace, don't mutate," matching AttributeEstimate's own
    # immutability and this project's existing "never store what can
    # be derived, and never mutate a snapshot in place" convention) ----
    def with_attribute(self, name: str, estimate: AttributeEstimate) -> "PlayerAbilityProfile":
        if name not in SKILL_ATTRIBUTES:
            raise ValueError(f"Unknown skill attribute {name!r}")
        new_attrs = dict(self.attributes)
        new_attrs[name] = estimate
        return replace(self, attributes=new_attrs)

    def with_ability_output(self, name: str, estimate: AttributeEstimate) -> "PlayerAbilityProfile":
        if name not in ABILITY_OUTPUTS:
            raise ValueError(f"Unknown ability output {name!r}")
        new_outputs = dict(self.ability_outputs)
        new_outputs[name] = estimate
        return replace(self, ability_outputs=new_outputs)

    @property
    def estimated_attribute_count(self) -> int:
        """How many of the 18 skill attributes have a real (non-None)
        value -- a quick, honest completeness signal for a profile
        that's still being filled in over time."""
        return sum(1 for est in self.attributes.values() if est.is_estimated)

    # ---- Serialization ----
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "as_of_season": self.as_of_season,
            "model_version": self.model_version,
            "player_id": self.player_id,
            "source_cutoff": self.source_cutoff,
            "evidence_strength": self.evidence_strength,
            "uncertainty_note": self.uncertainty_note,
            "attributes": {k: v.to_dict() for k, v in self.attributes.items()},
            "ability_outputs": {k: v.to_dict() for k, v in self.ability_outputs.items()},
            "role": self.role.to_dict() if self.role is not None else None,
        }

    @staticmethod
    def from_dict(d: dict) -> "PlayerAbilityProfile":
        return PlayerAbilityProfile(
            name=d["name"],
            as_of_season=d["as_of_season"],
            model_version=d.get("model_version", SCHEMA_VERSION),
            player_id=d.get("player_id"),
            source_cutoff=d.get("source_cutoff"),
            evidence_strength=d.get("evidence_strength"),
            uncertainty_note=d.get("uncertainty_note"),
            attributes={k: AttributeEstimate.from_dict(v) for k, v in d.get("attributes", {}).items()},
            ability_outputs={k: AttributeEstimate.from_dict(v) for k, v in d.get("ability_outputs", {}).items()},
            role=RoleProfile.from_dict(d["role"]) if d.get("role") is not None else None,
        )


# =====================================================================
# KNOWN LIMITATIONS (read before building the next phase on top of this)
# =====================================================================
# 1. No stable numeric player_id crosswalk exists yet. `name` is the
#    real key, same as every other file in this project -- a real,
#    same-named-different-person collision is a known, unaddressed
#    risk (shared with the rest of the codebase, not introduced here).
# 2. `player_id` exists in this schema but is never populated by
#    anything yet -- see the module docstring for where a real ID
#    could eventually be sourced from (cache/<season>/transactions.json,
#    2015-16+ only).
# 3. No attribute is calculated anywhere in this file. Every
#    PlayerAbilityProfile a caller builds today has to be populated
#    by hand (see the tests for exactly that pattern).
# 4. `evidence_strength` and `uncertainty_note` are placeholders --
#    no real uncertainty MODEL exists yet, just a place to eventually
#    put one.
