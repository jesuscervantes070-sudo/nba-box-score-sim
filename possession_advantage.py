"""
Phase 15 -- Possession State & Event Kernel: advantage-state ABSTRACTION.

ADVANTAGE IS POSSESSION CONTEXT, NOT PLAYER ABILITY -- it lives on
`PossessionState`, never on a player profile. No advantage MATH is
locked this phase (no decay coefficient, no magnitude-to-probability
mapping) -- this file defines an interface two structurally different
future representations can both satisfy, plus two real (but empirically
unvalidated) implementations of it, to prove the interface doesn't
secretly assume one of them.

Candidates named in the task:
  A. spatial magnitude + compromised opportunity/location (continuous)
  B. coarse discrete tiers (NEUTRAL/TILTED/COLLAPSED/SCRAMBLE) as a benchmark

Neither is chosen as final. Both exist here ONLY to prove the shared
`AdvantageModel` interface (persist, decay, transfer, compound, and
support MULTIPLE simultaneous compromised areas) is representation-
agnostic -- see test_possession_kernel.py's
`test_advantage_abstraction_survives_multiple_representations`, which
runs the identical sequence of operations against both and asserts only
the interface-level invariants, never a specific numeric outcome.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Dict, List, Tuple

from possession_state import SpatialZone


@dataclass(frozen=True)
class CompromisedArea:
    """One objectively-compromised defensive area -- a real spatial
    location plus a magnitude in the implementation's own units (a
    discrete-tier model uses a small integer tier index; a continuous
    model uses a 0.0-1.0-ish float -- deliberately NOT normalized to a
    single shared scale here, since forcing that would itself be a
    premature lock on the math). Existence of a CompromisedArea does
    NOT imply any player has perceived it -- see the module docstring's
    WORLD OPPORTUNITY -> PERCEPTION -> POLICY -> EXECUTION pipeline;
    perception is a separate, future concept, not modeled here."""
    zone: SpatialZone
    magnitude: float
    source: str = ""  # free-text: what created this (e.g. "drive_beat_poa", "help_rotation_gap") -- diagnostic only


class AdvantageModel(ABC):
    """Shared interface. Concrete state is held by the implementation
    (immutable, `replace()`-style like the rest of this kernel) --
    `PossessionState` holds a reference to whichever `AdvantageModel`
    instance is active for that possession, so the possession-state
    layer never needs to know which representation it's carrying."""

    @abstractmethod
    def compromised_areas(self) -> Tuple[CompromisedArea, ...]:
        """Zero or more simultaneously-compromised areas -- NEVER
        collapsed to a single global scalar. An empty tuple is a valid,
        real state (fully neutral defense), not an error."""

    @abstractmethod
    def decay(self, dt: float) -> "AdvantageModel":
        """Returns a NEW model with advantage reduced by elapsed time
        `dt` -- no specific decay function is prescribed by the
        interface; each implementation defines its own real or
        placeholder curve."""

    @abstractmethod
    def transfer(self, from_zone: SpatialZone, to_zone: SpatialZone) -> "AdvantageModel":
        """Returns a NEW model with (some or all of) the advantage at
        `from_zone` moved to `to_zone` -- e.g. a ball reversal moving a
        help-created advantage to the opposite side. No magnitude-
        preservation guarantee is part of the interface contract."""

    @abstractmethod
    def compound(self, other: "AdvantageModel") -> "AdvantageModel":
        """Returns a NEW model combining `self` with `other` (e.g. a
        second, independent action creating a further advantage in a
        possession that already had one) -- must accept another
        instance of the SAME concrete type; combining two different
        representations is not part of this phase's scope."""


@dataclass(frozen=True)
class DiscreteTierAdvantage(AdvantageModel):
    """Candidate B -- coarse discrete tiers per zone. `NEUTRAL` is
    represented by a zone's absence from `tiers` (missing != zero
    disadvantage AND != zero advantage -- it's simply not tracked as
    compromised), not by an explicit NEUTRAL entry, so
    `compromised_areas()` only ever reports genuinely non-neutral zones."""
    TIER_NAMES: Tuple[str, ...] = ("TILTED", "COLLAPSED", "SCRAMBLE")  # ordering = increasing severity; NOT locked as final or exhaustive
    tiers: Dict[SpatialZone, str] = field(default_factory=dict)

    def compromised_areas(self) -> Tuple[CompromisedArea, ...]:
        return tuple(
            CompromisedArea(zone=z, magnitude=float(self.TIER_NAMES.index(tier)) + 1.0, source=tier)
            for z, tier in self.tiers.items()
        )

    def decay(self, dt: float) -> "DiscreteTierAdvantage":
        """Placeholder-only decay: any tier drops one severity level per
        call regardless of `dt`'s real value -- NOT an empirically
        validated decay curve, purely enough behavior to prove the
        interface's `decay()` contract (state changes, zones can clear)."""
        new_tiers = {}
        for z, tier in self.tiers.items():
            idx = self.TIER_NAMES.index(tier) - 1
            if idx >= 0:
                new_tiers[z] = self.TIER_NAMES[idx]
        return replace(self, tiers=new_tiers)

    def transfer(self, from_zone: SpatialZone, to_zone: SpatialZone) -> "DiscreteTierAdvantage":
        if from_zone not in self.tiers:
            return self
        new_tiers = dict(self.tiers)
        tier = new_tiers.pop(from_zone)
        new_tiers[to_zone] = tier
        return replace(self, tiers=new_tiers)

    def compound(self, other: "DiscreteTierAdvantage") -> "DiscreteTierAdvantage":
        if not isinstance(other, DiscreteTierAdvantage):
            raise TypeError("DiscreteTierAdvantage.compound() requires another DiscreteTierAdvantage")
        new_tiers = dict(self.tiers)
        for z, tier in other.tiers.items():
            existing = new_tiers.get(z)
            if existing is None or self.TIER_NAMES.index(tier) > self.TIER_NAMES.index(existing):
                new_tiers[z] = tier  # worse-for-defense tier wins -- placeholder rule, not empirically derived
        return replace(self, tiers=new_tiers)


@dataclass(frozen=True)
class SpatialMagnitudeAdvantage(AdvantageModel):
    """Candidate A -- continuous magnitude per compromised zone."""
    magnitudes: Dict[SpatialZone, float] = field(default_factory=dict)

    def compromised_areas(self) -> Tuple[CompromisedArea, ...]:
        return tuple(CompromisedArea(zone=z, magnitude=m) for z, m in self.magnitudes.items() if m > 0.0)

    def decay(self, dt: float) -> "SpatialMagnitudeAdvantage":
        """Placeholder-only exponential-ish decay -- NOT an empirically
        validated coefficient (there is no calibrated half-life yet)."""
        factor = max(0.0, 1.0 - 0.1 * dt)
        new_mags = {z: m * factor for z, m in self.magnitudes.items() if m * factor > 1e-6}
        return replace(self, magnitudes=new_mags)

    def transfer(self, from_zone: SpatialZone, to_zone: SpatialZone) -> "SpatialMagnitudeAdvantage":
        if from_zone not in self.magnitudes:
            return self
        new_mags = dict(self.magnitudes)
        mag = new_mags.pop(from_zone)
        new_mags[to_zone] = new_mags.get(to_zone, 0.0) + mag
        return replace(self, magnitudes=new_mags)

    def compound(self, other: "SpatialMagnitudeAdvantage") -> "SpatialMagnitudeAdvantage":
        if not isinstance(other, SpatialMagnitudeAdvantage):
            raise TypeError("SpatialMagnitudeAdvantage.compound() requires another SpatialMagnitudeAdvantage")
        new_mags = dict(self.magnitudes)
        for z, m in other.magnitudes.items():
            new_mags[z] = new_mags.get(z, 0.0) + m
        return replace(self, magnitudes=new_mags)


NEUTRAL_DISCRETE = DiscreteTierAdvantage()
NEUTRAL_SPATIAL = SpatialMagnitudeAdvantage()
