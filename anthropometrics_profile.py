"""
Phase 12A -- Player Anthropometrics Foundation: the minimal physical-state
representation.

PHYSICAL MEASUREMENTS ARE NOT BASKETBALL SKILLS. This module is
STRUCTURALLY SEPARATE from `player_ability_profile.py` (PlayerAbilityProfile)
and `player_tendencies_analysis.py` -- nothing here is imported by, or
imports, either of those. No skill estimator is read or modified by any
code in this file.

Real units only, everywhere: inches (height_in / standing_reach_in /
wingspan_in), pounds (mass_lbs). Never a 0-99 rating.

Keyed by the real, stable NBA `player_id` (confirmed in
anthropometrics_analysis.combine_id_vs_static_player_id: modern combine
PLAYER_ID values ARE real NBA player_ids for players who went on to
appear in an NBA game -- see docs/PHASE12A_ANTHROPOMETRICS_REPORT.md
Sec. 4 for the real match-rate-by-year finding and its caveats), NOT by
name.

`value=None` / `evidence_mode=UNAVAILABLE` is a first-class state --
missing is always preferable to a fabricated number, same convention as
`AttributeEstimate` in player_ability_profile.py.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

SCHEMA_VERSION = "0.1.0-phase12a"

# Evidence-mode taxonomy -- kept separate from any notion of "confidence"
# (a MEASURED_COMBINE value could still be an outlier/parser error; an
# INFERRED_REGRESSION value could still be tight if the model is good).
MEASURED_COMBINE = "MEASURED_COMBINE"      # official NBA combine anthropometric testing
MEASURED_ROSTER = "MEASURED_ROSTER"        # team-reported roster HEIGHT/WEIGHT (LISTED, not measured)
INFERRED_REGRESSION = "INFERRED_REGRESSION"  # predicted from other real anthropometrics via a validated model
PRIOR_ONLY = "PRIOR_ONLY"                  # no player-specific evidence at all -- population prior only (not implemented in V1; reserved)
UNAVAILABLE = "UNAVAILABLE"                # no honest value can be produced

EVIDENCE_MODES = (MEASURED_COMBINE, MEASURED_ROSTER, INFERRED_REGRESSION, PRIOR_ONLY, UNAVAILABLE)


@dataclass(frozen=True)
class PhysicalObservation:
    """One real, dated, sourced observation of one physical trait. NEVER
    silently averaged with another observation of the same trait --
    a profile keeps the full observation list where a trait may
    legitimately vary (mass) and keeps only the SELECTED best-tier
    observation where inspection has shown a single point-value is the
    right representation (height/wingspan/standing_reach -- see
    PlayerPhysicalProfile docstring), but always retains provenance to
    say which tier and observation it is.
    """
    value: Optional[float]              # real unit (inches or lbs); None iff evidence_mode == UNAVAILABLE
    unit: str                           # "inches" or "lbs"
    evidence_mode: str
    source: str                         # e.g. "draftcombineplayeranthro", "commonteamroster"
    as_of: str                          # draft year ("2018") or season ("2018-19") the observation is dated to
    raw_value: Optional[float] = None   # unconverted source value, if a conversion was applied (none currently needed -- reserved)
    model_version: Optional[str] = None  # set iff evidence_mode == INFERRED_REGRESSION
    note: Optional[str] = None          # free-text caveat (e.g. "measurement convention unspecified")

    def __post_init__(self):
        if self.evidence_mode not in EVIDENCE_MODES:
            raise ValueError(f"Unknown evidence_mode {self.evidence_mode!r} -- must be one of {EVIDENCE_MODES}")
        if self.evidence_mode == UNAVAILABLE and self.value is not None:
            raise ValueError("UNAVAILABLE observation must not carry a value")
        if self.evidence_mode != UNAVAILABLE and self.value is None:
            raise ValueError(f"{self.evidence_mode} observation must carry a value")
        if self.evidence_mode == INFERRED_REGRESSION and self.model_version is None:
            raise ValueError("INFERRED_REGRESSION observation must carry model_version")

    def to_dict(self) -> dict:
        return {"value": self.value, "unit": self.unit, "evidence_mode": self.evidence_mode,
                "source": self.source, "as_of": self.as_of, "raw_value": self.raw_value,
                "model_version": self.model_version, "note": self.note}

    @staticmethod
    def from_dict(d: dict) -> "PhysicalObservation":
        return PhysicalObservation(**d)


UNAVAILABLE_OBSERVATION = PhysicalObservation(value=None, unit="", evidence_mode=UNAVAILABLE, source="none", as_of="")


@dataclass(frozen=True)
class PlayerPhysicalProfile:
    """
    Minimal V1 physical-state object -- height, standing_reach, wingspan,
    mass ONLY. No first_step_burst/lateral_agility/vertical_pop/
    workload_capacity field exists here; adding one is an explicit,
    separate future decision (see docs/PHASE12A_ANTHROPOMETRICS_REPORT.md
    Sec. 11/12), not an oversight.

    height_in / standing_reach_in / wingspan_in are each a SINGLE selected
    PhysicalObservation (source-hierarchy winner: MEASURED_COMBINE >
    MEASURED_ROSTER > INFERRED_REGRESSION > UNAVAILABLE for height;
    MEASURED_COMBINE > INFERRED_REGRESSION > UNAVAILABLE for
    wingspan/standing_reach, since no roster-listed source exists for
    those two) -- these three traits are treated by this project's own
    empirical inspection as effectively static post-maturity (no
    biological growth model is implemented; this is a representational
    choice, not a claim that height literally never changes).

    mass_observations is a LIST -- mass is explicitly time-varying and
    must never be collapsed to one number (see
    anthropometrics_analysis.mass_longitudinal_diagnostic /
    adjacent_season_mass_staleness for the real evidence this choice is
    based on).
    """
    player_id: str
    schema_version: str = SCHEMA_VERSION
    height_in: PhysicalObservation = UNAVAILABLE_OBSERVATION
    standing_reach_in: PhysicalObservation = UNAVAILABLE_OBSERVATION
    wingspan_in: PhysicalObservation = UNAVAILABLE_OBSERVATION
    mass_observations: Tuple[PhysicalObservation, ...] = field(default_factory=tuple)

    @property
    def latest_mass(self) -> PhysicalObservation:
        """Most recent real mass observation by `as_of`, or UNAVAILABLE.
        Does NOT average across observations."""
        dated = [o for o in self.mass_observations if o.evidence_mode != UNAVAILABLE]
        if not dated:
            return UNAVAILABLE_OBSERVATION
        return max(dated, key=lambda o: o.as_of)

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id, "schema_version": self.schema_version,
            "height_in": self.height_in.to_dict(), "standing_reach_in": self.standing_reach_in.to_dict(),
            "wingspan_in": self.wingspan_in.to_dict(),
            "mass_observations": [o.to_dict() for o in self.mass_observations],
        }

    @staticmethod
    def from_dict(d: dict) -> "PlayerPhysicalProfile":
        return PlayerPhysicalProfile(
            player_id=d["player_id"], schema_version=d.get("schema_version", SCHEMA_VERSION),
            height_in=PhysicalObservation.from_dict(d["height_in"]),
            standing_reach_in=PhysicalObservation.from_dict(d["standing_reach_in"]),
            wingspan_in=PhysicalObservation.from_dict(d["wingspan_in"]),
            mass_observations=tuple(PhysicalObservation.from_dict(o) for o in d.get("mass_observations", [])),
        )
