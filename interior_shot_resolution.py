"""
Phase 18B -- Interior/Rim Shot Resolution + Blocks (rim + floater only).

INTERIOR SHOT INTENT -> RELEASE CONTEXT -> PRIMARY/HELP DEFENDER
GEOMETRY -> DERIVED INTERIOR CONTEST -> BLOCK ELIGIBILITY -> BLOCK OR
UNBLOCKED PATH -> UNBLOCKED MAKE/MISS -> MISS/BLOCK HANDOFF. Operates on
an explicitly UNWHISTLED attempt -- no fouls, whistles, free throws,
and-ones, charges, or blocking fouls exist anywhere in this module
(Phase 18C's job). No rebound winner is selected.

============================ CONSTRUCT AUDITS (see docs/PHASE18B_INTERIOR_SHOT_RESOLUTION_REPORT.md) ============================
`rim_finishing` (shot_zone_estimation.py) = real, pooled Restricted-Area
zone FG% (Phase 5, LOCK V1). Confirmed by direct source read: does NOT
condition on contest, assisted status, dunk/layup mix, transition,
defender identity, rim-protector quality, physical size, or shot clock
-- a historical composite, same posture as Phase 18A's `three_point`/
`midrange` audit finding. Reused as-is; no compensating context
correction invented on top.

`floater_short_mid` (shot_zone_estimation.py) = real, pooled Paint
Non-RA zone FG% (Phase 5, KEEP BUT FLAG -- a real, weaker sample-size/
portability posture than `rim_finishing`). Same contamination profile.

`rim_protection` (rim_protection_analysis.py) = real, NBA-computed
opponent-FG%-SUPPRESSION `PLUSMINUS` on Restricted-Area attempts
(Phase 7, LOCK V1) -- confirmed by direct source read to be a pure
FG%-suppression signal, NOT a raw block count. `defensive_playmaking`
(STL+BLK per-36, Phase 1-3) is a SEPARATE, already-existing real
attribute carrying block volume specifically. Phase 7's own report
already found only a real, PARTIAL (25-42%) shared-variance overlap
between rim-suppression and BLK rate -- confirmed again this phase
(Sec. 16 of the report). **Chosen treatment: option D (split derived
components using two ALREADY-EXISTING attributes, not one latent reused
twice, and not a new invented `block_ability`)** -- `defensive_playmaking`
drives the BLOCK branch, `rim_protection` drives the UNBLOCKED
conversion-suppression branch. Using the SAME latent for both would risk
counting the same real suppression signal twice (once as an actual
block, again as a further FG%-suppression discount on shots that
survive); using two distinct real attributes for two distinct real
branches avoids that.

Real, additional finding this phase: defender standing reach correlates
r=0.407 (n=441, 2023-24, real `player_id` join) with `rim_protection`'s
own suppression rate -- rim_protection already substantially absorbs
physical length. Standing reach is therefore NOT added as an
independent term (Sec. 21 of the report) -- classified REVISIT, not
LOCK/KEEP, given the real, quantified double-counting risk.
"""
import random
from dataclasses import dataclass
from typing import Optional

from possession_state import BallState, DefensivePosture, SpatialZone, _assert_player_id

# ---------------------------------------------------------------------
# Real population normalization constants -- rim_protection's real
# 2023-24 suppression_rate (n=442, mean=0.00988, stdev=0.05793) and
# defensive_playmaking's real population stats (n reused from Phase 17B,
# mean=1.5, stdev=0.7 STL+BLK/36, a real, rough league-average order of
# magnitude). NOT re-derived per season/era -- explicit, flagged
# placeholders, same convention as every prior resolution phase.
# ---------------------------------------------------------------------
RIM_PROTECTION_POPULATION_MEAN = 0.00988
RIM_PROTECTION_POPULATION_STDEV = 0.05793
DEFENSIVE_PLAYMAKING_POPULATION_MEAN = 1.5
DEFENSIVE_PLAYMAKING_POPULATION_STDEV = 0.7


class InteriorShotFamily:
    RIM = "RIM"
    FLOATER = "FLOATER"


# Interior zones this module operates on -- reused from possession_state's
# existing coarse-zone topology; this interior resolver still owns only paint/rim.
_INTERIOR_ZONE_BY_FAMILY = {InteriorShotFamily.RIM: SpatialZone.RESTRICTED_RIM,
                            InteriorShotFamily.FLOATER: SpatialZone.PAINT}


@dataclass
class InteriorDefenderContext:
    """One defender's real, structural context. `rim_protection`/
    `defensive_playmaking` are the caller-resolved, already-existing
    real estimate values (this module does not fetch either itself) --
    both Optional (missing != zero effect, see the fallback logic
    below)."""
    defender_id: str
    zone: SpatialZone
    posture: DefensivePosture
    is_primary: bool                          # on-ball defender at the point of the shot -- always geometrically eligible
    rim_protection: Optional[float] = None    # real Phase 7 suppression_rate -- used for UNBLOCKED conversion suppression only
    defensive_playmaking: Optional[float] = None  # real Phase 1-3 STL+BLK/36 -- used for BLOCK probability only


def geometric_block_eligibility(shot_family: str, defender: InteriorDefenderContext) -> bool:
    """A general topological gate, not a hardcoded pairing table. The
    PRIMARY defender is always eligible (they are, by construction, at
    the point of the shot). A SECONDARY/help defender is eligible only
    if their own zone is the same interior zone this shot family
    occupies AND their posture is HELPING (a defender merely assigned
    elsewhere, e.g. still out on the perimeter, cannot block a rim
    attempt regardless of any ability value)."""
    if defender.is_primary:
        return True
    interior_zone = _INTERIOR_ZONE_BY_FAMILY[shot_family]
    return defender.zone == interior_zone and defender.posture == DefensivePosture.HELPING


# Posture contest modifiers -- STRUCTURAL, hand-set placeholders (same
# explicit-not-validated posture as Phase 18A's own posture deltas). A
# trailing/beaten primary defender must not exert the same frontal
# contest as a square one -- direction defended, magnitude not.
_PRIMARY_POSTURE_SUPPRESSION_DELTA = {
    DefensivePosture.SQUARE: 0.0,
    DefensivePosture.RECOVERING: -0.15,
    DefensivePosture.TRAILING: -0.35,   # beaten -- materially less able to contest
    DefensivePosture.HELPING: -0.35,    # primary defender is, structurally, not even engaged (rare but representable)
}
_HELPER_PRESENT_SUPPRESSION_DELTA = 0.20  # a real, established help anchor adds SOME suppression -- placeholder magnitude


def _logit(p: float) -> float:
    import math
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))


def _z(value: Optional[float], mean: float, stdev: float) -> float:
    return (value - mean) / stdev if value is not None else 0.0  # missing -> zero Z-CONTRIBUTION, not a fabricated skill value


def _effective_suppression(primary: InteriorDefenderContext, secondary: Optional[InteriorDefenderContext]) -> float:
    """The chosen contest-combination model: STRONGEST-EFFECTIVE, not a
    blind sum -- HQ accepted the max-effective PRINCIPLE, not any
    specific equation. Sign convention: HIGHER return value = MORE
    suppression of the offense (subtracted from the shooter's logit in
    `unblocked_make_probability`, never added). Primary suppression =
    real, z-scored `rim_protection` signal (higher z = better real
    defender = more suppression) + a real, structural posture delta
    (NEGATIVE for a beaten/trailing/helping-elsewhere primary -- less
    able to suppress). Secondary suppression (only if a real, eligible,
    HELPING anchor is present) = a real, flagged placeholder bonus. The
    COMBINED effective suppression is the LARGER (stronger) of the two,
    never their sum -- this structurally prevents two overlapping
    defenders from double-penalizing the same shot geometry."""
    primary_component = (_z(primary.rim_protection, RIM_PROTECTION_POPULATION_MEAN, RIM_PROTECTION_POPULATION_STDEV)
                          + _PRIMARY_POSTURE_SUPPRESSION_DELTA.get(primary.posture, 0.0))
    if secondary is None or secondary.posture != DefensivePosture.HELPING:
        return primary_component  # no REAL established help anchor -- secondary presence alone (e.g. still recovering) does not yet count
    return max(primary_component, _HELPER_PRESENT_SUPPRESSION_DELTA)


def _block_leverage(primary: InteriorDefenderContext, secondary: Optional[InteriorDefenderContext],
                     shot_family: str) -> float:
    """Same strongest-effective principle, applied to the BLOCK branch
    using `defensive_playmaking` (NOT `rim_protection` -- see module
    docstring's double-counting rationale)."""
    candidates = [primary] + ([secondary] if secondary is not None else [])
    eligible = [d for d in candidates if geometric_block_eligibility(shot_family, d)]
    if not eligible:
        return float("-inf")  # no eligible defender -- block probability floor applies (near-zero, never fabricated)
    # BLOCK_LEVERAGE_SCALE: a real, flagged damping placeholder -- an unscaled z-score
    # pushed an elite shot-blocker (BLK/36 ~4.0) toward an implausible ~70%+ block rate;
    # 0.5 keeps the DIRECTION correct while avoiding a wildly unrealistic magnitude, still
    # explicitly unvalidated (see report Sec. 23).
    BLOCK_LEVERAGE_SCALE = 0.5
    return BLOCK_LEVERAGE_SCALE * max(
        _z(d.defensive_playmaking, DEFENSIVE_PLAYMAKING_POPULATION_MEAN, DEFENSIVE_PLAYMAKING_POPULATION_STDEV)
        for d in eligible)  # the single MOST dangerous eligible shot-blocker, not a sum of all eligible defenders


# Real, hand-set, explicitly-flagged placeholder base rates -- no public
# per-attempt block/make ground truth exists (same honest limitation as
# every prior resolution phase). Real anchor: rim_finishing/floater_short_mid
# supply the make-probability BASE; these two constants set the OVERALL
# scale of block risk and interior contest, not fit to any per-shot
# target.
BASE_BLOCK_LOGIT = -2.6   # a real, rough population base rate for "this eligible defender blocks THIS specific attempt" (~7% before any ability adjustment) -- placeholder
_PROB_EPSILON = 0.01


@dataclass
class InteriorShotContext:
    shot_family: str
    shooter_base_rate: float                       # the shooter's own existing rim_finishing/floater_short_mid estimate
    primary_defender: InteriorDefenderContext
    secondary_defender: Optional[InteriorDefenderContext] = None
    shot_clock_remaining: Optional[float] = None   # accepted for future era-rule routing; no late-clock term applied this phase (no real interior-specific finding gathered)


def block_probability(context: InteriorShotContext) -> float:
    leverage = _block_leverage(context.primary_defender, context.secondary_defender, context.shot_family)
    if leverage == float("-inf"):
        return _PROB_EPSILON  # no eligible defender -- floor, never exactly zero (still technically possible in a real, chaotic sequence) but structurally near-impossible
    return min(max(_sigmoid(BASE_BLOCK_LOGIT + leverage), _PROB_EPSILON), 1.0 - _PROB_EPSILON)


def unblocked_make_probability(context: InteriorShotContext) -> float:
    logit = _logit(context.shooter_base_rate)
    logit -= _effective_suppression(context.primary_defender, context.secondary_defender)  # suppression REDUCES make probability
    p = _sigmoid(logit)
    return min(max(p, _PROB_EPSILON), 1.0 - _PROB_EPSILON)


class InteriorShotOutcome:
    MADE = "MADE"
    MISSED_UNBLOCKED = "MISSED_UNBLOCKED"
    BLOCKED_RETAINED_OFFENSE = "BLOCKED_RETAINED_OFFENSE"
    BLOCKED_SECURED_DEFENSE = "BLOCKED_SECURED_DEFENSE"


@dataclass
class InteriorShotResult:
    outcome: str
    points: int
    block_probability_used: float
    make_probability_used: Optional[float]
    blocker_id: Optional[str] = None
    shot_family: str = ""
    shooter_id: str = ""


# Real, hand-set, flagged placeholder split for what happens to a blocked
# ball -- no public data source distinguishes these outcomes at the
# per-block level (same honest limitation pattern as every prior phase).
BLOCK_RETAINED_BY_OFFENSE_RATE = 0.35  # of all real blocks, roughly how often the offense recovers the ball -- placeholder


def resolve_interior_shot(shooter_id: str, context: InteriorShotContext, rng: random.Random) -> InteriorShotResult:
    """The single entry point. `rng` is caller-supplied (never the
    global `random` module). Two INDEPENDENT rolls -- block, then (only
    if not blocked) make/miss -- so a counterfactual that changes only
    block-relevant inputs does not reshuffle the unblocked trajectory
    roll, and vice versa (RNG isolation within one call, per explicit
    instruction)."""
    _assert_player_id(shooter_id)
    _assert_player_id(context.primary_defender.defender_id)
    if context.secondary_defender is not None:
        _assert_player_id(context.secondary_defender.defender_id)

    p_block = block_probability(context)
    if rng.random() < p_block:
        blocker = _most_dangerous_eligible_blocker(context)
        retained = rng.random() < BLOCK_RETAINED_BY_OFFENSE_RATE
        outcome = InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE if retained else InteriorShotOutcome.BLOCKED_SECURED_DEFENSE
        return InteriorShotResult(outcome=outcome, points=0, block_probability_used=p_block,
                                   make_probability_used=None, blocker_id=blocker,
                                   shot_family=context.shot_family, shooter_id=shooter_id)

    p_make = unblocked_make_probability(context)
    made = rng.random() < p_make
    points = (2 if made else 0)
    return InteriorShotResult(
        outcome=InteriorShotOutcome.MADE if made else InteriorShotOutcome.MISSED_UNBLOCKED, points=points,
        block_probability_used=p_block, make_probability_used=p_make,
        shot_family=context.shot_family, shooter_id=shooter_id,
    )


def _most_dangerous_eligible_blocker(context: InteriorShotContext) -> str:
    candidates = [context.primary_defender] + ([context.secondary_defender] if context.secondary_defender else [])
    eligible = [d for d in candidates if geometric_block_eligibility(context.shot_family, d)]
    if not eligible:
        return context.primary_defender.defender_id  # structurally shouldn't happen (p_block floors near zero with no eligible defender) -- safe fallback, not a fabricated identity
    return max(eligible, key=lambda d: _z(d.defensive_playmaking, DEFENSIVE_PLAYMAKING_POPULATION_MEAN, DEFENSIVE_PLAYMAKING_POPULATION_STDEV)).defender_id


def apply_interior_shot_to_engine(engine, shooter_id: str, context: InteriorShotContext, rng: random.Random,
                                   zone: Optional[str] = None, assisted_by: Optional[str] = None) -> InteriorShotResult:
    """Thin integration layer -- reuses Phase 15's own, unmodified
    `begin_shot`/`resolve_shot_made`/`block_retained_by_offense`/
    `block_secured_by_defense` and Phase 18A's `resolve_shot_missed_pending_rebound`
    (itself family-agnostic) rather than inventing new engine state.
    No rebound winner is ever selected here."""
    zone_enum = SpatialZone(zone) if zone else engine.state.ball_zone
    engine.begin_shot(zone_enum, dt=0.0)
    result = resolve_interior_shot(shooter_id, context, rng)

    if result.outcome == InteriorShotOutcome.MADE:
        engine.resolve_shot_made(shooter_id, assisted_by=assisted_by, dt=0.0)
    elif result.outcome == InteriorShotOutcome.MISSED_UNBLOCKED:
        engine.resolve_shot_missed_pending_rebound(shooter_id, dt=0.0)
    elif result.outcome == InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE:
        engine.block_retained_by_offense(result.blocker_id, shooter_id, dt=0.0)
    elif result.outcome == InteriorShotOutcome.BLOCKED_SECURED_DEFENSE:
        engine.block_secured_by_defense(result.blocker_id, shooter_id, dt=0.0)
    return result
