"""
Phase 21B -- Floor Foul Administration (personal/team fouls, bonus, FTs).

FOUL DETECTION/CLASSIFICATION != FOUL ADMINISTRATION. Phase 21A (and any
future foul-producing mechanic -- rebound/loose-ball fouls, etc.)
DETECTS that a pre-shot floor foul happened and classifies it
(`OFFENSIVE_CHARGE` / `DEFENSIVE_FLOOR_FOUL`). This module ADMINISTERS
the consequences of an already-detected foul: personal-foul recording,
CATEGORY-AWARE team-foul recording, era-aware regulation/OT bonus
evaluation, and (only when the rules say so) real free-throw
administration. It never rerolls, reclassifies, or independently
decides whether a foul happened -- there is no RNG here for "did a foul
occur," only the real, existing, already-reused FT execution roll
(`foul_resolution.resolve_free_throw_attempt`) for consequences the
rules say ARE stochastic downstream.

============================ REAL RULE: OFFENSIVE FOULS DO NOT CHARGE A TEAM FOUL ============================
Per current NBA Rule 12 Section VII: an ordinary offensive foul (a
charge is exactly this case) records a personal foul on the offender,
does NOT charge the offending team with a team foul, and awards no
penalty free throws -- only personal-foul bookkeeping + a turnover.
(Punching/flagrant offensive fouls are a separate, unmodeled exception,
explicitly out of scope -- Sec. deferred mechanics.) This module's
`OFFENSIVE_CHARGE` branch therefore NEVER increments `team_fouls` and
NEVER reaches the bonus-check/FT-award code at all -- category-aware,
not "every personal foul is also a team foul" (verified:
`test_charge_never_increments_team_fouls`,
`test_charge_source_never_calls_bonus_check_or_ft_award`).

============================ OWNERSHIP BOUNDARY ============================
Phase 18C owns shooting-foul detection, contact/whistle resolution, and
its OWN FT administration for shooting fouls (`foul_resolution.py`) --
untouched by this module, and not re-entered by it (no shot-family/
contact/whistle concept exists anywhere here). Phase 21A owns pre-shot
contact/collision DETECTION and classification (`on_ball_pressure_resolution.py`)
-- untouched by this module, and not re-derived here (this module never
rolls whether a foul occurred; it consumes an already-classified
`foul_class`). This module is the FIRST piece of GENERIC floor-foul
ADMINISTRATION -- built so a future rebound/loose-ball-foul detector can
emit into the exact same `administer_floor_foul` entry point without
duplicating any of this bonus/team-foul/FT-format logic. A future
typed special foul (flagrant/technical/transition take foul/clear
path/away-from-the-play/defensive three seconds) may be ADMINISTERED
through an extension of this layer later; this module does not detect
or classify any of them now (deferred, Sec. report).

`possession_consequence_already_applied` is the explicit seam handling
the one real overlap risk: Phase 21A's own `apply_on_ball_pressure_to_engine`
ALREADY calls `engine.non_shooting_foul`/`engine.dead_ball_turnover`
(Phase 15's real, existing state-transition methods) to make the DEAD-ball/
turnover consequence correct at DETECTION time (see Phase 21A Sec. 24-26).
A caller driving this module from a Phase 21A `FoulPacket` passes
`possession_consequence_already_applied=True` so this module does NOT
call those same engine methods a second time (which would double-log the
event and double-apply state that is already correct). A FUTURE detector
that has NOT already applied that consequence passes `False`, and this
module applies it via the exact SAME real Phase 15 engine methods --
reusing the identical rule logic either way, never a second copy of it.

============================ REGULATION VS. OVERTIME ============================
Real current-NBA team-foul limits are NOT the same number reset every
period: regulation allows the first 4 team fouls in a period without
penalty (the 5th+ qualifying common foul is a penalty); overtime allows
only the first 3, with the 4th+ a penalty. `EraRules.overtime_bonus_foul_threshold`
(Phase 21B addition, `possession_rules.py`) represents this as a real,
DISTINCT, rules-supplied number rather than "the same regulation
threshold with the count merely reset" -- `is_overtime` is a
caller-supplied STRUCTURAL fact (this module has no period/clock state
of its own to derive it from, same convention as Phase 21A's
caller-supplied `contact_established`). Every era constant in
`possession_rules.py` now sets a real, distinct OT value (4).

============================ FINAL-TWO-MINUTE EXCEPTION (Phase 21B correction) ============================
A real, explicit state machine, not a naive `team_fouls >= quota - 1`
clock check: `FoulAdministrationState.final_two_minute_exception_used`
is a per-team, per-PERIOD flag (reset at the same period boundary as
`team_fouls` -- see `reset_team_fouls`). `is_team_in_penalty` is the
ONE shared definition of "is this team in the penalty right now,"
consulted by BOTH this module's own `administer_floor_foul` (passed the
POST-increment state, for "does THIS just-committed foul draw bonus
FTs") and `detailed_game_orchestrator.DetailedGameState.in_bonus` (passed
its own current, unmodified state, for a general query) -- never
duplicated. Semantics, evaluated using team_id's CURRENT qualifying-foul
count against the applicable (regulation/OT) threshold:
  - count already >= threshold: ALWAYS in penalty, clock irrelevant
    (the ordinary quota was already exceeded before the final two
    minutes even began, or independent of them).
  - count < threshold AND the game clock is NOT inside the final two
    minutes (`FINAL_TWO_MINUTES_SECONDS`): NOT in penalty (the ordinary
    rule).
  - count < threshold AND the clock IS inside the final two minutes:
    the FIRST such qualifying foul in this window is forgiven (NOT in
    penalty) -- this sets `final_two_minute_exception_used` for that
    team for the rest of the period; every SUBSEQUENT qualifying foul in
    that period, even though the ordinary numeric quota may still not be
    technically exhausted, IS a penalty from then on (the "one allowed
    foul" is consumed). This is a real, explicit per-period exception
    flag -- not a second, looser quota.
"""
import random
from dataclasses import dataclass, field, replace
from typing import Dict, FrozenSet, Optional, Tuple

from foul_resolution import (
    FreeThrowSequence,
    PersonalFoulTracker,
    apply_free_throw_attempt_to_engine,
    resolve_free_throw_attempt,
)
from possession_engine import PossessionEngine
from possession_rules import EraRules
from possession_state import BallState, _assert_player_id

# The real, current-NBA "final two minutes" window -- a clock value, not a possession/action count.
FINAL_TWO_MINUTES_SECONDS = 120.0

OFFENSIVE_CHARGE = "OFFENSIVE_CHARGE"
DEFENSIVE_FLOOR_FOUL = "DEFENSIVE_FLOOR_FOUL"
_VALID_FOUL_CLASSES = (OFFENSIVE_CHARGE, DEFENSIVE_FLOOR_FOUL)

TWO_SHOT = "TWO_SHOT"
THREE_TO_MAKE_TWO = "THREE_TO_MAKE_TWO"
_VALID_BONUS_FORMATS = (TWO_SHOT, THREE_TO_MAKE_TWO)


def effective_bonus_foul_threshold(era_rules: EraRules, is_overtime: bool) -> Optional[int]:
    """Regulation always uses `era_rules.bonus_foul_threshold` -- the
    SAME real field Phase 15's own `non_shooting_foul` and Phase 18C's
    own `shooting_foul_team_bonus_check` already read, unchanged.
    Overtime uses `era_rules.overtime_bonus_foul_threshold` when the era
    configures a real, distinct value; `None` there means "no distinct
    OT threshold configured for this era" and falls back to the
    regulation number explicitly (missing != zero -- never silently 0)."""
    if not is_overtime:
        return era_rules.bonus_foul_threshold
    if era_rules.overtime_bonus_foul_threshold is not None:
        return era_rules.overtime_bonus_foul_threshold
    return era_rules.bonus_foul_threshold


@dataclass
class FoulAdministrationState:
    """The one persistent, immutable administration state this module
    owns -- mirrors Phase 18C's own `PersonalFoulTracker` convention
    (real, per-`player_id` running count, replace()-based updates)
    rather than inventing a new mutation style. `team_fouls` is a real,
    per-`team_id` running PERIOD foul count -- counted directly on this
    state object (not derived from event-log replay), matching the
    existing `PersonalFoulTracker`'s own direct-counting convention
    rather than inventing a parallel derivation mechanism. Only
    CATEGORY-QUALIFYING fouls increment it (an ordinary offensive
    foul/charge never does -- see module docstring). A future
    period-boundary orchestration layer resets it (`reset_team_fouls`);
    this module does not itself know when a period ends.
    `final_two_minute_exception_used` is the real, per-team, per-PERIOD
    flag for the final-two-minute exception (see module docstring) --
    reset at the SAME period boundary as `team_fouls` (same
    `reset_team_fouls` call), since it is definitionally scoped to "this
    period's" final two minutes, never carried across a period boundary.
    `administered_event_ids` is the idempotence ledger (see
    `administer_floor_foul`). Player personal fouls (`personal_fouls`)
    are DELIBERATELY NEVER touched by `reset_team_fouls` -- a real NBA
    personal foul carries across quarters/OT (foul-out tracking), unlike
    the team-foul/bonus ledger, which is strictly per-period."""
    personal_fouls: PersonalFoulTracker = field(default_factory=PersonalFoulTracker)
    team_fouls: Dict[str, int] = field(default_factory=dict)
    final_two_minute_exception_used: FrozenSet[str] = field(default_factory=frozenset)
    administered_event_ids: FrozenSet[str] = field(default_factory=frozenset)

    def team_foul_count(self, team_id: str) -> int:
        return self.team_fouls.get(team_id, 0)

    def _with_team_foul_incremented(self, team_id: str) -> "FoulAdministrationState":
        new_team_fouls = dict(self.team_fouls)
        new_team_fouls[team_id] = new_team_fouls.get(team_id, 0) + 1
        return replace(self, team_fouls=new_team_fouls)

    def _with_final_two_minute_exception_used(self, team_id: str) -> "FoulAdministrationState":
        return replace(self, final_two_minute_exception_used=self.final_two_minute_exception_used | {team_id})

    def reset_team_fouls(self) -> "FoulAdministrationState":
        """A real, minimal period-boundary hook -- NOT invoked anywhere
        in this module itself (this module has no concept of when a
        period ends); exists so a future game-orchestration layer has
        somewhere real to put "team fouls reset each period" without
        this module inventing period-tracking of its own. Also resets
        `final_two_minute_exception_used` (same per-period scope, see
        class docstring) -- player `personal_fouls` are UNCHANGED (carry
        across periods/OT, a real, separate NBA rule)."""
        return replace(self, team_fouls={}, final_two_minute_exception_used=frozenset())


def is_team_in_penalty(state: "FoulAdministrationState", team_id: str, clock_remaining_seconds: Optional[float],
                        era_rules: EraRules, is_overtime: bool) -> bool:
    """THE single shared definition of "is `team_id` in the penalty right now" -- consulted by
    both `administer_floor_foul` (passed the POST-increment state, to decide whether the
    just-committed foul itself draws bonus free throws) and
    `detailed_game_orchestrator.DetailedGameState.in_bonus` (passed its own current, unmodified
    state, for a general query) -- never duplicated. See module docstring's own
    "FINAL-TWO-MINUTE EXCEPTION" section for the exact real-rule semantics this implements.
    `clock_remaining_seconds=None` (missing != zero) is treated as "cannot determine whether the
    final two minutes have started" -- conservatively falls back to the ordinary, clock-independent
    quota check, never guessing a clock value."""
    threshold = effective_bonus_foul_threshold(era_rules, is_overtime)
    if threshold is None:
        return False  # era has no bonus rule configured at all -- missing != zero, never a fabricated penalty
    count = state.team_foul_count(team_id)
    if count >= threshold:
        return True  # the ordinary quota is already exceeded -- always in penalty, clock irrelevant
    if clock_remaining_seconds is None or clock_remaining_seconds > FINAL_TWO_MINUTES_SECONDS:
        return False  # below quota, and not (or not verifiably) inside the final two minutes -- ordinary rule
    # below the ordinary quota AND inside the final two minutes: the one allowed exception foul is
    # forgiven exactly once per period -- every qualifying foul after that is a penalty from here on.
    return team_id in state.final_two_minute_exception_used


@dataclass
class FoulAdministrationResult:
    """What actually happened when `administer_floor_foul` was called --
    a real, inspectable record, not just a side effect."""
    foul_event_id: str
    foul_class: str
    offender_id: str
    duplicate: bool  # True -- this event_id was already administered; nothing below was applied THIS call
    personal_foul_recorded: bool
    team_foul_team_id: Optional[str]  # None for OFFENSIVE_CHARGE -- no team foul is ever charged (see module docstring)
    team_foul_count_after: Optional[int]
    in_bonus: bool
    free_throw_sequence: Optional[FreeThrowSequence]
    bonus_free_throw_format: Optional[str]
    possession_consequence_applied_here: bool  # True if THIS call invoked the Phase 15 engine transition (False if the caller already had, per the seam above)


def _apply_missed_final_bonus_ft(engine: PossessionEngine) -> None:
    """The real, missed-final-FT rebound handoff -- the SAME rule Phase
    18C's own `apply_free_throw_attempt_to_engine` applies on a final
    miss (`BallState.LOOSE`, `ball_carrier=None`, `offense_team_id=None`).
    A small, local application (not a call into `foul_resolution.py`)
    ONLY because that module's own `apply_free_throw_attempt_to_engine`
    couples the roll with the apply step -- re-invoking it here to reach
    this same branch would re-roll a shot this module already resolved,
    double-counting one real free throw. Same real rule, applied once."""
    engine.state = replace(engine.state, ball_state=BallState.LOOSE, ball_carrier=None,
                            ball_control=None, offense_team_id=None)


def _apply_made_final_bonus_ft(engine: PossessionEngine) -> None:
    """The real, made-final-FT handoff -- the SAME rule Phase 18C's own
    `apply_free_throw_attempt_to_engine` applies on a made final attempt
    (dead ball, possession swaps to the opponent). Same local-application
    rationale as `_apply_missed_final_bonus_ft` above."""
    old_offense, old_defense = engine.state.offense_team_id, engine.state.defense_team_id
    engine.state = replace(engine.state, ball_state=BallState.DEAD, ball_carrier=None, ball_control=None,
                            offense_team_id=old_defense, defense_team_id=old_offense)


def _administer_three_to_make_two(engine: PossessionEngine, shooter_id: str, free_throw_rate: float,
                                   rng: random.Random) -> FreeThrowSequence:
    """Real, documented historical NBA bonus format (confirmed abolished
    by 1981-82, module docstring): up to 3 attempts, stopping as soon as
    2 are made. NOT the same thing as an NCAA-style 1-and-1. Each
    individual attempt is resolved via the SAME execution-only function
    Phase 18C's own FT machinery uses (`resolve_free_throw_attempt`) --
    not re-derived. The terminal engine-state transition (made -> dead
    ball/opponent, missed -> LOOSE rebound) is applied exactly once, only
    on the actual final attempt of THIS real sequence -- intermediate
    attempts (e.g. attempt 1 of a make-miss-make sequence) leave engine
    state untouched, matching Phase 18C's own non-final-miss convention."""
    _assert_player_id(shooter_id)
    attempts_taken = 0
    makes = 0
    made = False
    while True:
        made = resolve_free_throw_attempt(shooter_id, free_throw_rate, rng)
        attempts_taken += 1
        makes += 1 if made else 0
        if makes == 2 or attempts_taken == 3:
            break
    if made:
        _apply_made_final_bonus_ft(engine)
    else:
        _apply_missed_final_bonus_ft(engine)
    return FreeThrowSequence(shooter_id=shooter_id, awarded_attempts=attempts_taken,
                              source_foul_type="BONUS_THREE_TO_MAKE_TWO",
                              attempt_index=attempts_taken, makes=makes)


def _administer_two_shot(engine: PossessionEngine, shooter_id: str, free_throw_rate: float,
                          rng: random.Random) -> FreeThrowSequence:
    """Real current-NBA bonus format: both attempts are always awarded --
    full, direct reuse of Phase 18C's own `FreeThrowSequence` +
    `apply_free_throw_attempt_to_engine`, exactly the same real machinery
    already administering a missed-2PT shooting foul's 2 FTs (Phase 18C
    Sec. 22/23), applied here unchanged for a different real trigger
    (a non-shooting foul in the bonus rather than a shooting foul)."""
    sequence = FreeThrowSequence(shooter_id=shooter_id, awarded_attempts=2, source_foul_type="BONUS_NON_SHOOTING")
    while not sequence.is_complete:
        sequence, _made = apply_free_throw_attempt_to_engine(engine, sequence, free_throw_rate, rng)
    return sequence


def administer_bonus_free_throws(engine: PossessionEngine, shooter_id: str, free_throw_rate: float,
                                  bonus_format: str, rng: random.Random) -> FreeThrowSequence:
    """Dispatches on the era-supplied format string ONLY -- no season,
    no player_id, nothing but the rules configuration decides which real
    branch runs (verified: `test_bonus_format_is_rules_driven_not_hardcoded`)."""
    if bonus_format == THREE_TO_MAKE_TWO:
        return _administer_three_to_make_two(engine, shooter_id, free_throw_rate, rng)
    if bonus_format == TWO_SHOT:
        return _administer_two_shot(engine, shooter_id, free_throw_rate, rng)
    raise ValueError(f"unknown bonus_free_throw_format {bonus_format!r} -- expected one of {_VALID_BONUS_FORMATS}")


def administer_floor_foul(
    engine: PossessionEngine,
    state: FoulAdministrationState,
    foul_event_id: str,
    offender_id: str,
    fouled_player_id: Optional[str],
    foul_class: str,
    offender_team_id: str,
    fouled_team_id: str,
    rng: random.Random,
    possession_consequence_already_applied: bool = False,
    free_throw_rate: Optional[float] = None,
    is_overtime: bool = False,
    clock_remaining_seconds: Optional[float] = None,
) -> Tuple[FoulAdministrationState, FoulAdministrationResult]:
    """The single entry point. Consumes an ALREADY-DETECTED foul
    (`foul_class` -- typically a Phase 21A `FoulPacket.foul_class`,
    unchanged) and administers personal foul, CATEGORY-AWARE team foul,
    era-aware regulation/OT bonus (including the real final-two-minute
    exception, see module docstring), and (only in the bonus, only for a
    defensive floor foul) real free throws. Never rerolls whether a foul
    happened; never reads a shot family, contact/whistle concept, or
    anything else that would make this a second detection engine.

    `is_overtime` is a caller-supplied structural fact (this module has
    no period/clock state of its own) that routes bonus evaluation
    through `effective_bonus_foul_threshold` -- a real, distinct OT
    number when the era configures one, not merely the regulation
    threshold with the count reset (module docstring).

    `clock_remaining_seconds` is the real PERIOD clock remaining at the
    moment of THIS foul (`None` -- missing != zero -- means "unknown,"
    which conservatively falls back to the ordinary, clock-independent
    quota check; see `is_team_in_penalty`). Required for the
    final-two-minute exception to ever apply; omitting it never fabricates
    a clock value, it simply keeps ordinary quota-only behavior.

    IDEMPOTENCE: `foul_event_id` is the caller-supplied, per-real-foul
    unique key (e.g. an event/possession-scoped identifier the caller
    already tracks) -- if this exact id was already administered, this
    call is a real no-op: the returned state is IDENTICAL to the input
    state, and the result's `duplicate=True` (verified:
    `test_duplicate_administration_is_a_no_op`). This is the concrete
    mechanism preventing one real foul from incrementing foul totals or
    awarding free throws twice."""
    _assert_player_id(offender_id)
    _assert_player_id(fouled_player_id)
    if foul_class not in _VALID_FOUL_CLASSES:
        raise ValueError(f"unknown foul_class {foul_class!r} -- expected one of {_VALID_FOUL_CLASSES}")

    if foul_event_id in state.administered_event_ids:
        return state, FoulAdministrationResult(
            foul_event_id=foul_event_id, foul_class=foul_class, offender_id=offender_id, duplicate=True,
            personal_foul_recorded=False, team_foul_team_id=None, team_foul_count_after=None,
            in_bonus=False, free_throw_sequence=None, bonus_free_throw_format=None,
            possession_consequence_applied_here=False,
        )

    new_state = replace(state, personal_fouls=state.personal_fouls.increment(offender_id))
    applied_here = False
    in_bonus = False
    ft_sequence: Optional[FreeThrowSequence] = None
    bonus_format: Optional[str] = None
    team_foul_team_id: Optional[str] = None
    team_foul_count_after: Optional[int] = None

    if foul_class == DEFENSIVE_FLOOR_FOUL:
        # An ordinary defensive floor foul IS a real, category-qualifying "common foul" -- it charges a
        # team foul (unlike an offensive foul/charge, see module docstring).
        new_state = new_state._with_team_foul_incremented(offender_team_id)
        team_foul_team_id = offender_team_id
        team_foul_count_after = new_state.team_foul_count(offender_team_id)

        # ONE shared definition (regulation AND OT, including the real final-two-minute exception) --
        # see module docstring and `is_team_in_penalty`'s own docstring. Evaluated against `new_state`
        # (POST-increment for THIS foul) -- "does the foul just committed itself draw bonus FTs."
        in_bonus = is_team_in_penalty(new_state, offender_team_id, clock_remaining_seconds,
                                       engine.era_rules, is_overtime)
        if not in_bonus and clock_remaining_seconds is not None and clock_remaining_seconds <= FINAL_TWO_MINUTES_SECONDS:
            # This foul was forgiven INSIDE the final two minutes (the only way `is_team_in_penalty`
            # can return False while the clock is already <= FINAL_TWO_MINUTES_SECONDS, by its own
            # construction) -- consume the one-time exception for the rest of this period.
            new_state = new_state._with_final_two_minute_exception_used(offender_team_id)

        if not possession_consequence_already_applied:
            engine.non_shooting_foul(offender_id, fouled_player_id, team_foul_count_after)
            applied_here = True

        if in_bonus:
            if free_throw_rate is None:
                # MISSING != ZERO: a real bonus FT is required by the rules but no real shooter
                # free_throw estimate was supplied -- fail explicitly rather than silently
                # skipping the FTs or fabricating a rate.
                raise ValueError(
                    "team is in the bonus and a defensive floor foul was administered, but no "
                    "free_throw_rate was supplied for the fouled shooter -- cannot fabricate one")
            bonus_format = engine.era_rules.bonus_free_throw_format
            ft_sequence = administer_bonus_free_throws(engine, fouled_player_id, free_throw_rate, bonus_format, rng)

    else:  # OFFENSIVE_CHARGE -- an ordinary offensive foul: personal foul only, NEVER a team foul,
           # NEVER reaches a bonus check or FT award (real current-NBA Rule 12 Section VII; module docstring).
        if not possession_consequence_already_applied:
            engine.dead_ball_turnover(offender_id)
            applied_here = True

    new_state = replace(new_state, administered_event_ids=new_state.administered_event_ids | {foul_event_id})

    return new_state, FoulAdministrationResult(
        foul_event_id=foul_event_id, foul_class=foul_class, offender_id=offender_id, duplicate=False,
        personal_foul_recorded=True, team_foul_team_id=team_foul_team_id, team_foul_count_after=team_foul_count_after,
        in_bonus=in_bonus, free_throw_sequence=ft_sequence, bonus_free_throw_format=bonus_format,
        possession_consequence_applied_here=applied_here,
    )
