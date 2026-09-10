"""
Phase 17A -- Drive Resolution & Interior Penetration.

DRIVE INTENT -> POINT-OF-ATTACK INTERACTION -> PARTIAL/CLEAN/FAILED
PENETRATION STATE -> UPDATED POSSESSION CONTEXT. Output is a new,
selection-ready `PossessionState` -- NOT a shot, NOT a pass, NOT a
turnover resolution. Phase 16 selection runs again from whatever this
module produces.

============================ THE THREE-SKILL BOUNDARY ============================
`rim_access_creation` (Phase 9, KEEP BUT FLAG) -- the ONLY driver-side
input. Used ONLY to shift the coarse leverage distribution toward
CLEAN/PARTIAL outcomes. Never touches shot resolution (there is no shot
in this module) and never touches rim-protector contest quality (there
is no rim protector interaction in this module).

`poa_containment` (Phase 10, KEEP BUT FLAG) -- the ONLY defender-side
input. Used ONLY to shift the coarse leverage distribution toward
CONTAINED/FORCED_PICKUP outcomes. Never acts as rim protection, screen
navigation, or generic perimeter IQ -- this module has no screen state
and no rim-protection state to act on.

`rim_finishing` (Phase 5, LOCK V1) -- NOT IMPORTED, NOT REFERENCED
ANYWHERE in this file. A drive outcome never implies or computes a shot
make probability.

Physical variables (height/mass/wingspan/standing_reach, Phase 12A) --
tested empirically this phase (see docs/PHASE17A_DRIVE_RESOLUTION_REPORT.md
Sec. 8) and found to carry a real but confounded (likely role-driven,
not cleanly attributable) residual signal beyond `rim_access_creation`.
NOT wired into the resolver by default -- `DriveResolutionContext.enable_physical_adjustment`
defaults to False and must be explicitly set True by a caller to have
any effect at all, and even then only via an explicitly-supplied,
caller-computed adjustment value (this module does not fetch physical
data itself).
"""
import math
import random
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from possession_advantage import AdvantageModel
from possession_engine import PossessionEngine
from possession_events import EventType
from possession_state import DefensivePosture, DribbleState, SpatialZone, _assert_player_id

# ---------------------------------------------------------------------
# Real, population-level normalization constants -- computed THIS PHASE
# from real 2023-24 data (leaguedashptstats Drives measure's own
# rim_access_creation formula output; leagueseasonmatchups-derived
# poa_containment output), n=329 and n=423 players respectively (>=
# each estimator's own real exposure floor). NOT re-derived per season/
# era -- a real, explicit placeholder, flagged here and in the report.
# Used ONLY to put two differently-scaled real estimates on a common
# footing before combining them additively (the same scale-normalization
# lesson this project has applied since Phase 6/7).
# ---------------------------------------------------------------------
RIM_ACCESS_POPULATION_MEAN = 0.622
RIM_ACCESS_POPULATION_STDEV = 0.125
POA_CONTAINMENT_POPULATION_MEAN = -0.0147
POA_CONTAINMENT_POPULATION_STDEV = 0.0329

# Posture modifiers -- HAND-SET placeholders (not fit to any heldout
# target; no public per-drive posture-labeled dataset exists to fit
# against). Direction only is defended: a defender who has already lost
# leverage (TRAILING/RECOVERING) or is elsewhere (HELPING) should never
# be treated identically to a SQUARE defender.
POSTURE_LEVERAGE_BONUS = {
    DefensivePosture.SQUARE: 0.0,
    DefensivePosture.RECOVERING: 0.4,
    DefensivePosture.TRAILING: 0.7,
    DefensivePosture.HELPING: 0.6,  # defender is not even engaged on this driver -- a real, structural advantage for the offense
}

# Ordinal outcome thresholds (cumulative, worst-for-offense -> best),
# expressed as HAND-SET placeholder base rates -- loosely order-of-
# magnitude-anchored by the real 2023-24 leaguewide DRIVE_TOV_PCT mean
# (~6.5%, Sec. 2/9 of the report) for the single worst bucket, but NOT
# independently fit (no public data separates "forced pickup" from
# "contained-but-still-in-control" at the granularity this taxonomy
# needs). Flagged KEEP BUT FLAG for this reason.
CUMULATIVE_BASE_RATES = {
    "FORCED_PICKUP": 0.10,
    "CONTAINED": 0.45,
    "PARTIAL_EDGE": 0.80,
    "CLEAN_PENETRATION": 1.00,
}

DRIVE_CHECKPOINTS: Tuple[str, ...] = ("drive_begins", "poa_interaction", "leverage_outcome",
                                       "inward_movement_or_cutoff", "help_opportunity_handoff")


class DriveOutcome:
    """Plain string constants, not an Enum -- kept deliberately loose
    (a string, not a locked type) since the task explicitly allows
    flexible naming and a future phase may add outcomes (e.g. a real,
    separately-calibrated LOST_BALL) without a breaking type change.
    Each outcome describes a POSSESSION-GEOMETRY/LEVERAGE change, never
    a scoring outcome."""
    CLEAN_PENETRATION = "CLEAN_PENETRATION"
    PARTIAL_EDGE = "PARTIAL_EDGE"
    CONTAINED = "CONTAINED"
    FORCED_PICKUP = "FORCED_PICKUP"
    LOST_BALL = "LOST_BALL"  # SCAFFOLDED ONLY -- see DriveResolutionContext.enable_lost_ball; classified REVISIT (Sec. 11)

    ORDERED = (FORCED_PICKUP, CONTAINED, PARTIAL_EDGE, CLEAN_PENETRATION)


@dataclass
class DriveResolutionContext:
    """Every empirical input is Optional and independently gate-able --
    missing != zero-effect, it means "no adjustment from this source."""
    rim_access_creation: Optional[float] = None    # driver's real Phase 9 raw rate (~0.15-1.1 real range, 2023-24)
    poa_containment: Optional[float] = None        # defender's real Phase 10 containment_rate (~-0.13 to +0.09 real range, 2023-24)
    defender_posture: DefensivePosture = DefensivePosture.SQUARE
    enable_physical_adjustment: bool = False       # explicit opt-in required -- see module docstring
    physical_adjustment: Optional[float] = None    # caller-supplied, pre-computed; this module does not fetch physical data
    enable_lost_ball: bool = False                 # explicit opt-in -- scaffolded, unvalidated (Sec. 11); False means LOST_BALL can never be sampled
    lost_ball_rate: float = 0.0                    # only consulted if enable_lost_ball is True
    advantage: Optional[AdvantageModel] = None     # queried read-only for context; never mutated by this module (no representation-specific write)


def _net_leverage(context: DriveResolutionContext) -> float:
    """A single, additive, interpretable real number -- higher favors
    the offense (more clean penetration), lower favors the defense.
    Every term is independent; none is multiplied by another (same
    convention as Phase 16's action-selection scoring)."""
    leverage = 0.0
    if context.rim_access_creation is not None:
        leverage += (context.rim_access_creation - RIM_ACCESS_POPULATION_MEAN) / RIM_ACCESS_POPULATION_STDEV
    if context.poa_containment is not None:
        leverage -= (context.poa_containment - POA_CONTAINMENT_POPULATION_MEAN) / POA_CONTAINMENT_POPULATION_STDEV
    leverage += POSTURE_LEVERAGE_BONUS.get(context.defender_posture, 0.0)
    if context.enable_physical_adjustment and context.physical_adjustment is not None:
        leverage += context.physical_adjustment
    return leverage


def _sample_outcome(context: DriveResolutionContext, rng: random.Random) -> str:
    """Ordinal sampling via a logistic shift of the real, hand-set
    cumulative base rates by `_net_leverage`. `rng` is caller-supplied
    (never the global `random` module) -- deterministic replay."""
    net = _net_leverage(context)

    if context.enable_lost_ball and context.lost_ball_rate > 0.0 and rng.random() < context.lost_ball_rate:
        return DriveOutcome.LOST_BALL

    def shifted(base_rate: float) -> float:
        # logit-shift: push the cumulative-probability-of-a-BAD-outcome
        # threshold by `-net` -- a positive net (offense favored) must
        # LOWER the cumulative probability of landing at or below a bad
        # (FORCED_PICKUP/CONTAINED) outcome, standard proportional-odds
        # direction. A real, bounded logistic transform -- not a raw
        # additive probability (which could leave [0,1]).
        if base_rate <= 0.0:
            return 0.0
        if base_rate >= 1.0:
            return 1.0
        logit = math.log(base_rate / (1.0 - base_rate)) - net
        return 1.0 / (1.0 + math.exp(-logit))

    roll = rng.random()
    cumulative = 0.0
    for outcome in DriveOutcome.ORDERED:
        cumulative = shifted(CUMULATIVE_BASE_RATES[outcome])
        if roll < cumulative:
            return outcome
    return DriveOutcome.CLEAN_PENETRATION  # floating-point fallback


# Coarse, real, NOT-automatic zone advancement -- a clean/partial drive
# does NOT automatically reach RESTRICTED_RIM; it moves toward the
# interior with a real, hand-set (placeholder) split between PAINT and
# RESTRICTED_RIM, honoring "a partial or clean edge may terminate in
# different coarse spatial contexts."
_CLEAN_ZONE_WEIGHTS = ((SpatialZone.RESTRICTED_RIM, 0.55), (SpatialZone.PAINT, 0.45))
_PARTIAL_ZONE_WEIGHTS = ((SpatialZone.PAINT, 0.7), (SpatialZone.RESTRICTED_RIM, 0.3))


def _advance_zone(outcome: str, current_zone: SpatialZone, rng: random.Random) -> SpatialZone:
    if outcome == DriveOutcome.CLEAN_PENETRATION:
        weights = _CLEAN_ZONE_WEIGHTS
    elif outcome == DriveOutcome.PARTIAL_EDGE:
        weights = _PARTIAL_ZONE_WEIGHTS
    else:
        return current_zone  # CONTAINED/FORCED_PICKUP/LOST_BALL -- no forward geometric progress
    roll = rng.random()
    cumulative = 0.0
    for zone, weight in weights:
        cumulative += weight
        if roll < cumulative:
            return zone
    return weights[-1][0]


_POSTURE_AFTER_OUTCOME = {
    DriveOutcome.CLEAN_PENETRATION: DefensivePosture.TRAILING,   # defender materially lost leverage
    DriveOutcome.PARTIAL_EDGE: DefensivePosture.RECOVERING,      # defender still attached, working back
    DriveOutcome.CONTAINED: DefensivePosture.SQUARE,             # defender preserved/re-established leverage
    DriveOutcome.FORCED_PICKUP: DefensivePosture.SQUARE,
    DriveOutcome.LOST_BALL: DefensivePosture.SQUARE,
}

_HELP_RELEVANT_OUTCOMES = frozenset({DriveOutcome.CLEAN_PENETRATION, DriveOutcome.PARTIAL_EDGE})


def resolve_drive(engine: PossessionEngine, driver_id: str, defender_id: Optional[str],
                   context: DriveResolutionContext, rng: random.Random,
                   reaction_fn: Optional[Callable[[str, object], Optional[str]]] = None) -> str:
    """The single entry point. Mutates `engine.state` via its existing,
    Phase-15-provided replace-based methods only (`advance_ball_zone`,
    `dead_dribble`, `update_posture`) -- no duplicate handoff object is
    created; `PossessionState` already carries everything a Phase 17B
    pass-resolution phase will need (ball carrier, zone, dribble state,
    assignments, `engine.advantage`).

    Returns the sampled `DriveOutcome` string. Does NOT choose or
    execute a shot/pass/reset -- that is Phase 16 selection's job, to
    be re-run by the caller against the returned, updated state.
    """
    _assert_player_id(driver_id)
    _assert_player_id(defender_id)
    if engine.state.ball_carrier != driver_id:
        raise ValueError("resolve_drive requires the driver to be the current ball carrier")
    if engine.state.ball_control is None or engine.state.ball_control.state != DribbleState.LIVE_DRIBBLE:
        raise ValueError("resolve_drive requires a live dribble -- a dead/gathered dribble cannot drive again")

    def default_reaction(checkpoint: str, state) -> Optional[str]:
        return None

    last_checkpoint, interrupted = engine.run_checkpointed_action(
        list(DRIVE_CHECKPOINTS), reaction_fn or default_reaction, dt_per_checkpoint=0.0)

    outcome = _sample_outcome(context, rng)
    new_zone = _advance_zone(outcome, engine.state.ball_zone, rng)
    engine.advance_ball_zone(new_zone)

    if outcome == DriveOutcome.FORCED_PICKUP:
        engine.dead_dribble()
    # CONTAINED: dribble remains whatever it already was (still live, per instruction -- "dribble may remain
    # alive depending on actual state") -- no forced transition.
    # CLEAN_PENETRATION/PARTIAL_EDGE: dribble remains live -- selection may continue, pull up, or pass.

    if defender_id is not None and defender_id in engine.state.assignments:
        engine.update_posture(defender_id, _POSTURE_AFTER_OUTCOME[outcome])

    help_relevant = outcome in _HELP_RELEVANT_OUTCOMES
    engine._log(EventType.DRIVE_RESOLVED, 0.0, primary=driver_id, secondary=defender_id, zone=new_zone,
                meta={"outcome": outcome, "help_opportunity": help_relevant, "interrupted_at": last_checkpoint if interrupted else None})
    return outcome
