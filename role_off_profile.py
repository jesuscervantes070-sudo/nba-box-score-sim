"""
Phase 13 -- Lineup / Player Role Inference: the minimal role-state
representation.

ROLE != ABILITY, ROLE != TENDENCY. Structurally separate from
`player_ability_profile.PlayerAbilityProfile`/`RoleProfile` (which uses a
fixed set of discrete, mutually-exclusive ARCHETYPES -- a different
concept from this phase's continuous, independently-scored deployment
DIMENSIONS) and from `player_tendencies_analysis`'s
`PlayerTendencyEstimate`. Nothing here is imported by, or imports,
either of those, and no skill/tendency estimator is read or modified by
any code in this file.

No compositional/softmax constraint -- each dimension is scored
independently and is NOT required to sum to anything. `role_off_spacing`
is not forced to equal `1 - role_off_initiation - role_off_finishing` or
any such relation; a player can be high or low on any combination.

Keyed by real, stable NBA `player_id`. Modern-era only (2013-14+
tracking floor) -- `value=None`/`evidence_mode=UNAVAILABLE` for anything
outside that, same missing-never-zero convention as every other phase.
The schema is deliberately shaped so a LATER phase can add a
`HISTORICAL_PROXY` evidence mode for pre-2013-14 seasons without a
schema change -- not built this phase (see report Sec. 4).
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple

SCHEMA_VERSION = "0.1.0-phase13"

MEASURED_TRACKING = "MEASURED_TRACKING"  # real leaguedashptstats/leaguedashplayerstats season aggregate
UNAVAILABLE = "UNAVAILABLE"

EVIDENCE_MODES = (MEASURED_TRACKING, UNAVAILABLE)

ROLE_DIMENSIONS = ("role_off_initiation", "role_off_finishing", "role_off_spacing", "role_def_perimeter_interior")


@dataclass(frozen=True)
class RoleObservation:
    """One dimension's real, dated, sourced value -- never forced into a
    0-1 or compositional range (each dimension's own natural units are
    kept: a per-36 rate for initiation, a real share in [0,1] for
    finishing/spacing, a signed share-difference in [-1,1] for the
    defensive axis)."""
    value: Optional[float]
    evidence_mode: str
    source: str
    as_of_season: str
    sample_size: Optional[int] = None  # unit varies by dimension (minutes, matchup-minutes) -- see `note`
    note: Optional[str] = None

    def __post_init__(self):
        if self.evidence_mode not in EVIDENCE_MODES:
            raise ValueError(f"Unknown evidence_mode {self.evidence_mode!r} -- must be one of {EVIDENCE_MODES}")
        if self.evidence_mode == UNAVAILABLE and self.value is not None:
            raise ValueError("UNAVAILABLE observation must not carry a value")
        if self.evidence_mode != UNAVAILABLE and self.value is None:
            raise ValueError(f"{self.evidence_mode} observation must carry a value")

    def to_dict(self) -> dict:
        return {"value": self.value, "evidence_mode": self.evidence_mode, "source": self.source,
                "as_of_season": self.as_of_season, "sample_size": self.sample_size, "note": self.note}

    @staticmethod
    def from_dict(d: dict) -> "RoleObservation":
        return RoleObservation(**d)


UNAVAILABLE_OBSERVATION = RoleObservation(value=None, evidence_mode=UNAVAILABLE, source="none", as_of_season="")


@dataclass(frozen=True)
class PlayerRoleProfile:
    """V1: the three offensive deployment dimensions + one coarse
    defensive deployment signal. No archetype, no possession-engine
    hook, no gameplay effect -- a read-only, diagnostic-first
    representation, same posture as Phase 12A's PlayerPhysicalProfile."""
    player_id: str
    as_of_season: str
    schema_version: str = SCHEMA_VERSION
    role_off_initiation: RoleObservation = UNAVAILABLE_OBSERVATION
    role_off_finishing: RoleObservation = UNAVAILABLE_OBSERVATION
    role_off_spacing: RoleObservation = UNAVAILABLE_OBSERVATION
    role_def_perimeter_interior: RoleObservation = UNAVAILABLE_OBSERVATION

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id, "as_of_season": self.as_of_season, "schema_version": self.schema_version,
            "role_off_initiation": self.role_off_initiation.to_dict(),
            "role_off_finishing": self.role_off_finishing.to_dict(),
            "role_off_spacing": self.role_off_spacing.to_dict(),
            "role_def_perimeter_interior": self.role_def_perimeter_interior.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> "PlayerRoleProfile":
        return PlayerRoleProfile(
            player_id=d["player_id"], as_of_season=d["as_of_season"], schema_version=d.get("schema_version", SCHEMA_VERSION),
            role_off_initiation=RoleObservation.from_dict(d["role_off_initiation"]),
            role_off_finishing=RoleObservation.from_dict(d["role_off_finishing"]),
            role_off_spacing=RoleObservation.from_dict(d["role_off_spacing"]),
            role_def_perimeter_interior=RoleObservation.from_dict(d["role_def_perimeter_interior"]),
        )
