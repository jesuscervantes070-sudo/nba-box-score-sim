"""
Drive Floor-Foul Resolution -- Observable-Outcome Redesign.

============================ WHY THIS MODULE EXISTS ============================
`on_ball_pressure_resolution.py`'s `OnBallPressureContext.contact_established` was the prior
design's intended PRODUCTION calibration target for reaching Phase 21A/21B's floor-foul/bonus
machinery: "is a collision geometrically occurring at all?" gated a SEPARATE collision-
classification branch (`_resolve_collision`) that then rolled charge/defensive-foul/no-call.
New empirical research established that `P(physical contact | drive)` is itself a LATENT
quantity that cannot be defensibly calibrated from public NBA data -- unwhistled body
contact/collisions are not publicly labeled anywhere. Calibrating a hidden "did contact happen"
gate was never going to be possible; keeping it as the production entry point would have meant
either leaving the whole floor-foul pipeline permanently unreachable (the prior, honest state)
or eventually inventing an unsourced number for something that cannot be observed.

This module REPLACES that two-stage latent-contact design with a single, directly OBSERVABLE
per-drive outcome classification:

    DRIVE -> possible OFFENSIVE_CHARGE -> possible DEFENSIVE_FLOOR_FOUL -> otherwise NO_FLOOR_FOUL

Both `OFFENSIVE_CHARGE` and `DEFENSIVE_FLOOR_FOUL` are real, publicly countable per-drive
outcomes (a charge call, a shooting-adjacent/floor-foul whistle) -- unlike "was there contact,"
these are exactly the things a future calibration pass COULD source real per-drive rates for
from play-by-play-derived drive counts, even though no such rates exist yet.

============================ DOCTRINE ============================
CONTACT != FOUL: this module makes NO claim about whether physical contact occurred on a given
drive -- it only classifies which of the three real, observable outcomes resulted. It does not
resolve, reuse, or import anything from `on_ball_pressure_resolution.py`'s own contact/pressure
model (that module, and its `contact_established` gate, are UNCHANGED by this phase -- see
`possession_orchestrator.py`'s own comment at its now-legacy call site).

FLOOR FOUL != SHOOTING FOUL: this module NEVER evaluates, and structurally cannot produce, a
shooting foul. A drive classified `NO_FLOOR_FOUL` simply continues to ordinary drive resolution
(`drive_resolution.py`, unchanged) -- if that drive later creates a shot, the EXISTING and ONLY
shooting-foul resolver (`possession_orchestrator.py`'s `_dispatch_shot`/`_dispatch_shooting_foul`,
via `foul_resolution.resolve_contact_and_whistle`) evaluates it, exactly once, completely
independently of this module. See `possession_orchestrator.py`'s own docstring on
`_dispatch_shooting_foul` ("Phase 21A never administers a shooting foul... Phase 21B never
detects one") -- this module inherits that same firewall by construction: it has no shot-family
concept, no make-probability input, and never calls `resolve_contact_and_whistle` or
`_dispatch_shooting_foul`.

OFFENSIVE_CHARGE != DEFENSIVE_FLOOR_FOUL: kept as two distinct outcome values (reusing
`floor_foul_administration`'s own `OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL` string constants
verbatim, not redefined here) so the one real foul-class vocabulary never forks into two.
`administer_floor_foul` (Phase 21B, UNCHANGED by this module) still owns every rule difference
between the two: only a defensive floor foul charges the team-foul/bonus ledger; an offensive
charge never does (see that module's own docstring).

FOUL DRAWING != RIM ACCESS, FOUL DISCIPLINE != RAW PF: `DriveFloorFoulContext.foul_drawing`/
`foul_discipline` are represented (same forward-looking-but-inert pattern
`OnBallPressureContext` already uses for the identical two attributes) but NOT YET consulted by
`resolve_drive_floor_foul_outcome` -- no validated conversion from either estimator's native
scale to a hazard adjustment exists yet (see the module-level classification table below).
Missing != zero: leaving them `None` here means "not yet wired," never a silent zero-weight
claim about their true effect.

============================ MISSING != ZERO / NO UNCALIBRATED NUMBERS ============================
`charge_hazard_per_drive` and `defensive_floor_foul_hazard_per_drive` are the two REAL,
OBSERVABLE per-drive quantities this architecture is designed to be calibrated against once
NBA-Tracking-derived charge-per-drive and floor-foul-per-drive rates are sourced (see this
phase's own report for the exact data requirement). Both default to `None` (UNCALIBRATED, not
zero) everywhere in this codebase today -- `resolve_drive_floor_foul_outcome` treats a `None`
hazard as an explicit, honest 0.0, and when BOTH hazards are `None`/`0.0` it returns
`NO_FLOOR_FOUL` WITHOUT consuming any `rng` draw at all. This is a stronger guarantee than
"statistically inert": leaving both hazards unconfigured makes this stage byte-for-byte
indistinguishable from not existing at all (verified: the canonical 100-game benchmark digest is
unchanged from checkpoint 282a096).

============================ CANDIDATE FUTURE INPUTS -- CLASSIFIED, NONE ACTIVATED ============================
  - offensive `foul_drawing`        -- STRUCTURALLY APPROPRIATE (a real, already-estimated Phase 6
    attribute); CALIBRATION REQUIRED before any weight is assigned (no validated conversion from
    its native scale to a hazard multiplier exists).
  - defensive `foul_discipline`     -- same posture as `foul_drawing` above.
  - `defender_posture`              -- STRUCTURALLY APPROPRIATE (already a real, already-used-
    elsewhere-for-whistle-adjacent-decisions signal, e.g. `on_ball_pressure_resolution.py`'s own
    `_posture_disruption_delta`); CALIBRATION REQUIRED for a floor-foul-specific delta.
  - drive/advantage/transition state -- STRUCTURALLY APPROPRIATE as FUTURE context (a genuine
    defensive-scramble state could plausibly change floor-foul hazard); CALIBRATION REQUIRED, and
    deliberately NOT represented as a field yet -- no evidence-backed direction has been
    established for it, so adding an unused field now would invite an unjustified default weight
    later. REJECTED for this phase, not merely deferred.
  - assignment/matchup context beyond `defender_posture` -- REJECTED: no additional real signal
    beyond who the primary defender is (already used to select `defender_posture`/attributes)
    has been identified.
"""
import random
from dataclasses import dataclass
from typing import Optional

from floor_foul_administration import DEFENSIVE_FLOOR_FOUL, OFFENSIVE_CHARGE
from possession_state import DefensivePosture


class DriveFloorFoulOutcome:
    """Not a locked Enum, per this project's established convention (Phases 17A/18B/21A) for
    representations that may need real-evidence-driven additions later. `OFFENSIVE_CHARGE`/
    `DEFENSIVE_FLOOR_FOUL` are the EXACT SAME string values `floor_foul_administration.py` and
    `on_ball_pressure_resolution.OnBallContactOutcome` already use -- reused verbatim, not
    redefined, so there is only ever one real foul-class vocabulary in this codebase."""
    NO_FLOOR_FOUL = "NO_FLOOR_FOUL"
    OFFENSIVE_CHARGE = OFFENSIVE_CHARGE
    DEFENSIVE_FLOOR_FOUL = DEFENSIVE_FLOOR_FOUL


@dataclass
class DriveFloorFoulContext:
    """Every ability/hazard field is Optional and independently gate-able -- missing != zero, it
    means 'no adjustment/no hazard configured,' never a fabricated zero-effect claim. See this
    module's own docstring for the full classification of which fields are structurally
    appropriate-but-inert today versus rejected outright."""
    foul_drawing: Optional[float] = None              # NOT YET consulted -- see module docstring
    foul_discipline: Optional[float] = None           # NOT YET consulted -- see module docstring
    defender_posture: DefensivePosture = DefensivePosture.SQUARE  # NOT YET consulted -- see module docstring
    charge_hazard_per_drive: Optional[float] = None                     # UNCALIBRATED -- see module docstring
    defensive_floor_foul_hazard_per_drive: Optional[float] = None       # UNCALIBRATED -- see module docstring
    force_outcome: Optional[str] = None  # TEST-ONLY deterministic override -- bypasses RNG entirely; production
    # config construction never sets this (see `PossessionConfig.force_drive_floor_foul_outcome`'s own docstring).


def resolve_drive_floor_foul_outcome(context: DriveFloorFoulContext, rng: random.Random) -> str:
    """Pure resolution function -- no engine mutation, no RNG beyond what's passed in. Returns
    exactly one of `DriveFloorFoulOutcome.{NO_FLOOR_FOUL, OFFENSIVE_CHARGE, DEFENSIVE_FLOOR_FOUL}`.

    `force_outcome` (test-only) short-circuits everything below it, consuming zero RNG -- used by
    focused tests to prove downstream dispatch/administration behavior deterministically, per this
    phase's own "tests must not rely on random sampling" requirement.

    With both hazards at their honest default (`None`/0.0), this function returns
    `NO_FLOOR_FOUL` WITHOUT calling `rng.random()` at all -- see the module docstring's own
    "byte-for-byte indistinguishable from not existing" guarantee."""
    if context.force_outcome is not None:
        return context.force_outcome

    charge_hazard = context.charge_hazard_per_drive or 0.0
    floor_foul_hazard = context.defensive_floor_foul_hazard_per_drive or 0.0
    if charge_hazard <= 0.0 and floor_foul_hazard <= 0.0:
        return DriveFloorFoulOutcome.NO_FLOOR_FOUL

    roll = rng.random()
    if roll < charge_hazard:
        return DriveFloorFoulOutcome.OFFENSIVE_CHARGE
    if roll < charge_hazard + floor_foul_hazard:
        return DriveFloorFoulOutcome.DEFENSIVE_FLOOR_FOUL
    return DriveFloorFoulOutcome.NO_FLOOR_FOUL
