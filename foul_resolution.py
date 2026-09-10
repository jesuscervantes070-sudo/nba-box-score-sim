"""
Phase 18C -- Contact, Shooting Fouls, And-Ones & Free Throw Administration.

SHOT/DRIVE-TO-SHOT CONTEXT -> CONTACT OPPORTUNITY -> CONTACT STATE ->
WHISTLE DECISION -> SHOT OUTCOME/AND-ONE OR NO-FGA BRANCH -> FREE THROW
ADMINISTRATION -> POSSESSION/REBOUND HANDOFF. Operates BEFORE Phase
18A/18B's own block/trajectory resolution -- if a shot is whistled as a
shooting foul, it never enters the block-eligibility branch at all
(Sec. "sequencing" below), which is the concrete mechanism preventing
an impossible "clean block + shooting foul + ordinary miss" combination.

============================ CONSTRUCT AUDITS (see docs/PHASE18C_FOUL_RESOLUTION_REPORT.md) ============================
`foul_drawing` (foul_estimation.py/foul_analysis.py, Phase 6, KEEP BUT
FLAG): real, `drives`-denominator-normalized shooting-foul-drawn rate
(NOT raw FTA/game) -- confirmed by direct source read
(`CANDIDATE_DRAW_DENOMINATORS`, real chosen denominator `drives`, with
an explicit historical pre-tracking-era fallback). A real, exposure-
normalized rate, but Phase 6's own original report already documented a
real ~50-57% drawer-attribution coverage gap -- classified here as a
**real but incomplete-coverage contextual signal**, not a clean,
fully-identified foul-drawing skill. Used ONLY in the WHISTLE stage,
with a deliberately small weight.

`foul_discipline` (same module, Phase 6, KEEP BUT FLAG): real,
exposure-normalized (`def_possessions_proxy`, not raw fouls/minute)
personal-foul rate -- confirmed by direct source read. Phase 6's own
report already documented a real, unresolved "bigs foul more per
minute" bias (a role/burden confound never corrected). Used ONLY in the
WHISTLE stage (defender side), also with a deliberately small weight,
and NEVER as a rim-protection or POA-suppression proxy.

`free_throw` (player_ability_estimation.py, Phase 1-3, KEEP/STRONG):
real, whole-season box `FT_PCT`, shrunk via the generic λ/M engine --
execution-only by construction (no contact/foul context anywhere in its
own estimation). Used EXCLUSIVELY for the FT-attempt roll; nothing else
in this module reads it.
"""
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from possession_state import BallState, DefensivePosture, SpatialZone, _assert_player_id

# ---------------------------------------------------------------------
# Real, hand-set, EXPLICITLY FLAGGED placeholder base rates. No public
# per-shot contact/whistle ground truth exists (same honest limitation
# as every prior resolution phase) -- see the report's Sec. 7 for what
# WAS verified (real, cached shooting-fouls-drawn and and-ones counts,
# Phase 6's own turnover/foul-event cache) and what was not (event-level
# contact/geometry labels, which are proprietary/unavailable).
# ---------------------------------------------------------------------

# Real, cached 2023-24 evidence (`cache/2023-24/player_foul_events.json`,
# a real, partial-season sample already ingested by Phase 6, scaled
# consistently since numerator and denominator share the same sample):
# 5,471 real shooting fouls drawn, 928 real and-ones among them --
# P(and-one | shooting foul) = 16.96%. This is NOT used as a separate
# and-one probability (see module docstring's "preferred hypothesis" --
# and-one = ordinary make roll under contact) -- it is reported here
# purely as a real, independent sanity check that the resolver's
# emergent and-one rate should land in this real ballpark once wired to
# real shooter/defender values (Sec. 33 of the report).
REAL_AND_ONE_GIVEN_FOUL_RATE_2023_24 = 0.1696  # reporting/validation anchor only -- not a resolver input

# Contact-probability base rates by shot family -- REAL, ROUGH order-
# of-magnitude anchors, NOT independently fit to a per-shot dataset
# (none exists). RIM anchored loosely to Phase 17A's own real,
# previously-computed `DRIVE_PF_PCT` leaguewide mean (~6.65%, most rim
# shooting fouls arise from drives) via the base rate below; perimeter
# rates are real-world-plausible but NOT independently verified via a
# live call this phase -- explicitly flagged lower-confidence than the
# rim anchor.
BASE_CONTACT_PROBABILITY = {
    "RIM": 0.14,        # rough real anchor -- most rim attempts involve SOME real physical contact, only a fraction is whistled
    "FLOATER": 0.08,
    "MIDRANGE": 0.03,
    "THREE_POINT": 0.025,
}
# Given contact occurred, base probability it is WHISTLED as a shooting
# foul -- calibrated so BASE_CONTACT_PROBABILITY x BASE_WHISTLE_GIVEN_CONTACT
# lands roughly near real, well-known NBA shooting-foul-rate orders of
# magnitude per family (rim ~6-7% of attempts, three ~2% of attempts) --
# explicitly a real, hand-tuned placeholder PAIR, not two independently
# validated numbers.
BASE_WHISTLE_GIVEN_CONTACT = {
    "RIM": 0.48,
    "FLOATER": 0.35,
    "MIDRANGE": 0.25,
    "THREE_POINT": 0.60,   # perimeter contact that DOES occur (e.g. a real closeout collision) is whistled at a real, higher conditional rate than a marginal rim bump
}

FOUL_DRAWING_WEIGHT = 0.20      # deliberately small -- real coverage-gap-flagged signal (see module docstring)
FOUL_DISCIPLINE_WEIGHT = -0.20  # deliberately small, opposite sign (higher discipline -> fewer whistles on this defender)

# Real, historical rule/environment fact: the NBA's 2004-05 hand-check
# rule enforcement change is a well-documented real increase in real
# shooting-foul/freedom-of-movement whistle rates -- an ENVIRONMENT
# change, not a player-ability change. Modeled as a real, coarse era
# hook (two eras), analogous to possession_rules.py's own era pattern --
# NOT re-verified via a live per-season foul-rate pull this phase
# (flagged), but the underlying rule-change FACT is real, well-known
# NBA history, not invented.
PRE_HANDCHECK_ERA_WHISTLE_LOGIT_DELTA = -0.15
MODERN_ERA_WHISTLE_LOGIT_DELTA = 0.0
HANDCHECK_ERA_CUTOFF_SEASON = "2004-05"


def era_whistle_logit_delta(season: str) -> float:
    """Environment, not ability -- keyed by season string only, never by
    player_id. Real, coarse two-era hook; a future phase could add finer
    real eras (landing-space emphasis, etc.) without changing this
    function's contract."""
    return PRE_HANDCHECK_ERA_WHISTLE_LOGIT_DELTA if season < HANDCHECK_ERA_CUTOFF_SEASON else MODERN_ERA_WHISTLE_LOGIT_DELTA


def _logit(p: float) -> float:
    import math
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))


_PROB_EPSILON = 0.005


@dataclass
class FoulEligibleDefender:
    """One structurally-eligible-to-be-the-fouler defender -- built by
    the caller from real Phase 15/18B state, never inferred by this
    module from "highest historical foul volume" or any such proxy.
    `is_primary`/`is_helper` mirror Phase 18B's own primary/secondary
    distinction exactly (reused, not reinvented)."""
    defender_id: str
    is_primary: bool
    posture: DefensivePosture
    foul_discipline: Optional[float] = None  # real Phase 6 estimate for THIS defender, or None


@dataclass
class ContactContext:
    """The smallest useful EPHEMERAL contact representation -- never a
    permanent stored 'contact rating'. Built fresh per shot attempt."""
    shot_family: str                          # "RIM" | "FLOATER" | "MIDRANGE" | "THREE_POINT"
    shooter_foul_drawing: Optional[float] = None   # real Phase 6 estimate for the SHOOTER, or None
    eligible_defenders: List[FoulEligibleDefender] = field(default_factory=list)
    season: str = "2023-24"                   # routes era_whistle_logit_delta -- environment, not ability


def contact_probability(context: ContactContext) -> float:
    """STAGE A: was there meaningful contact at all? Structural/geometric
    only -- no foul_drawing/foul_discipline enters here (per the
    required causal separation: contact is a structural/geometric fact,
    whistling it is a separate, later decision)."""
    return BASE_CONTACT_PROBABILITY.get(context.shot_family, 0.05)


def _primary_fouler(context: ContactContext) -> Optional[FoulEligibleDefender]:
    for d in context.eligible_defenders:
        if d.is_primary:
            return d
    return context.eligible_defenders[0] if context.eligible_defenders else None


def whistle_probability(context: ContactContext, contact_occurred: bool) -> float:
    """STAGE B: given contact, is it whistled? `foul_drawing`
    (shooter) and `foul_discipline` (the STRUCTURALLY-RESPONSIBLE
    defender, never an arbitrary/nearest/highest-ability pick) enter
    HERE ONLY, each with a deliberately small weight given their real,
    documented coverage/contamination limitations (module docstring)."""
    if not contact_occurred:
        return 0.0  # no contact -- structurally cannot be whistled as a shooting foul
    base = BASE_WHISTLE_GIVEN_CONTACT.get(context.shot_family, 0.3)
    logit = _logit(base)
    if context.shooter_foul_drawing is not None:
        logit += FOUL_DRAWING_WEIGHT * context.shooter_foul_drawing
    fouler = _primary_fouler(context)
    if fouler is not None and fouler.foul_discipline is not None:
        logit += FOUL_DISCIPLINE_WEIGHT * fouler.foul_discipline
    logit += era_whistle_logit_delta(context.season)
    return min(max(_sigmoid(logit), _PROB_EPSILON), 1.0 - _PROB_EPSILON)


@dataclass
class ShootingFoulResult:
    contact_occurred: bool
    whistled: bool
    fouler_id: Optional[str] = None


def resolve_contact_and_whistle(context: ContactContext, rng: random.Random) -> ShootingFoulResult:
    """The two independent, sequential rolls -- contact, then (only if
    contact occurred) whistle. A counterfactual that changes only
    whistle-relevant inputs (foul_drawing/foul_discipline/era) never
    reshuffles the contact roll, and vice versa."""
    contact = rng.random() < contact_probability(context)
    if not contact:
        return ShootingFoulResult(contact_occurred=False, whistled=False)
    whistled = rng.random() < whistle_probability(context, contact_occurred=True)
    fouler = _primary_fouler(context) if whistled else None
    return ShootingFoulResult(contact_occurred=True, whistled=whistled,
                               fouler_id=fouler.defender_id if fouler else None)


# --------------------------- FT count rules (real, modern NBA rule; era-hook-ready) ---------------------------

def awarded_free_throws(shot_family: str, made: bool) -> int:
    """Real modern-NBA FT-count rule: a made basket + shooting foul (an
    and-one) awards exactly 1 FT regardless of shot family; a missed 2PT
    shooting foul awards 2 FTs; a missed 3PT shooting foul awards 3 FTs.
    Kept as a plain function (not a stored constant table) so a future
    era hook can branch on `season` cleanly without restructuring
    callers -- not yet parameterized by era this phase (no real
    historical FT-count rule CHANGE was identified to model; the 1/2/3
    FT structure itself is treated as a real, stable modern rule)."""
    if made:
        return 1
    return 3 if shot_family == "THREE_POINT" else 2


# --------------------------- free throw administration ---------------------------

@dataclass
class FreeThrowSequence:
    shooter_id: str
    awarded_attempts: int
    source_foul_type: str        # "AND_ONE" | "MISSED_SHOOTING_FOUL"
    attempt_index: int = 0       # 0-based index of the NEXT attempt to resolve
    makes: int = 0

    @property
    def attempts_remaining(self) -> int:
        return self.awarded_attempts - self.attempt_index

    @property
    def is_final_attempt(self) -> bool:
        return self.attempt_index == self.awarded_attempts - 1

    @property
    def is_complete(self) -> bool:
        return self.attempt_index >= self.awarded_attempts


def resolve_free_throw_attempt(shooter_id: str, free_throw_rate: float, rng: random.Random) -> bool:
    """Execution-only -- `free_throw_rate` is the shooter's own,
    already-resolved real `free_throw` estimate; nothing else (no
    foul_drawing, no rim_finishing, no three_point, no contest, no
    defender attribute, no clutch/fatigue trait) is read here."""
    _assert_player_id(shooter_id)
    p = min(max(free_throw_rate, _PROB_EPSILON), 1.0 - _PROB_EPSILON)
    return rng.random() < p


def advance_free_throw_sequence(sequence: FreeThrowSequence, made: bool) -> FreeThrowSequence:
    from dataclasses import replace
    return replace(sequence, attempt_index=sequence.attempt_index + 1, makes=sequence.makes + (1 if made else 0))


def apply_free_throw_attempt_to_engine(engine, sequence: FreeThrowSequence, free_throw_rate: float,
                                        rng: random.Random) -> Tuple[FreeThrowSequence, bool]:
    """Resolves ONE attempt in an already-started FT sequence and
    applies only the state changes this specific attempt legally
    requires. Game clock is deliberately left untouched (frozen during
    ordinary FT attempts, per instruction) -- no method here reads or
    writes `engine.state.game_clock_remaining`. FT attempts are NOT
    modeled as an ordinary `ShotIntent`/`begin_shot` call -- this is
    FT's own administrative/execution state, per explicit instruction."""
    from dataclasses import replace as _replace
    made = resolve_free_throw_attempt(sequence.shooter_id, free_throw_rate, rng)
    new_sequence = advance_free_throw_sequence(sequence, made)

    if made and new_sequence.is_complete:
        # Real rule: a made final FT is a dead ball, opponent inbounds.
        old_offense, old_defense = engine.state.offense_team_id, engine.state.defense_team_id
        engine.state = _replace(engine.state, ball_state=BallState.DEAD, ball_carrier=None, ball_control=None,
                                 offense_team_id=old_defense, defense_team_id=old_offense)
    elif not made and new_sequence.is_complete:
        # Only a legally live FINAL missed FT creates a rebound opportunity -- a non-final miss (more
        # attempts remain) does NOT reach this branch at all (see the sequence.is_complete guard).
        engine.state = _replace(engine.state, ball_state=BallState.LOOSE, ball_carrier=None,
                                 ball_control=None, offense_team_id=None)
    # else: made-but-not-final, or missed-but-not-final -- the sequence simply continues;
    # NO possession-state change happens here at all (correctly excludes non-final misses from any rebound handoff).
    return new_sequence, made


# --------------------------- and-one / missed-shooting-foul shot resolution ---------------------------

def resolve_shooting_foul_shot(shooter_id: str, shot_family: str, unblocked_make_probability: float,
                                rng: random.Random) -> Tuple[bool, int, int]:
    """Given a shot has been WHISTLED as a shooting foul, resolves
    whether the underlying shot ALSO went in (an and-one) using the
    SAME real execution probability Phase 18A/18B's own resolvers would
    have used for this shot (`unblocked_make_probability` -- caller-
    computed via `shot_resolution.shot_make_probability` or
    `interior_shot_resolution.unblocked_make_probability`, NOT
    recomputed here). This is the concrete mechanism implementing
    "and-one = ordinary shot execution + contact + foul_drawing having
    already done its (whistle-only) work" -- `foul_drawing` never
    enters this function, so it cannot ALSO inflate the make roll.

    Returns (made, points, awarded_fts) -- `points` here is the BASKET's
    own points only (0 or 2/3), matching real NBA accounting: a missed
    shooting foul contributes ZERO FGA/FGM (the miss never counted as
    an attempt), while a made and-one contributes exactly 1 real FGA/FGM
    for the basket, with FT resolution handled separately by
    `FreeThrowSequence`."""
    made = rng.random() < min(max(unblocked_make_probability, _PROB_EPSILON), 1.0 - _PROB_EPSILON)
    points = 0
    if made:
        points = 3 if shot_family == "THREE_POINT" else 2
    awarded = awarded_free_throws(shot_family, made)
    return made, points, awarded


@dataclass
class PersonalFoulTracker:
    """The smallest useful persistent foul-count interface -- NOT a
    substitution/foul-out engine (explicitly Phase 21B's job). Just a
    real, per-player running personal-foul count, keyed by real
    `player_id`."""
    counts: dict = field(default_factory=dict)

    def increment(self, player_id: str) -> "PersonalFoulTracker":
        from dataclasses import replace
        new_counts = dict(self.counts)
        new_counts[player_id] = new_counts.get(player_id, 0) + 1
        return replace(self, counts=new_counts)

    def count_for(self, player_id: str) -> int:
        return self.counts.get(player_id, 0)


def shooting_foul_team_bonus_check(engine, team_foul_count: int) -> bool:
    """Reuses `engine.era_rules.bonus_foul_threshold` exactly the same
    way Phase 15's own `non_shooting_foul` already does -- a shooting
    foul's own FT count (Sec. `awarded_free_throws`) is independent of
    bonus status (a shooting foul always awards its own FTs regardless
    of bonus), but this hook exists so a future generic floor-foul
    phase (21B) can share the same bonus-threshold logic rather than
    reimplementing it."""
    return (engine.era_rules.bonus_foul_threshold is not None
            and team_foul_count >= engine.era_rules.bonus_foul_threshold)
