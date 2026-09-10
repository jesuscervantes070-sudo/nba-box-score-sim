"""
Phase 23A -- Autonomous Single-Possession Kernel (DETAILED POSSESSION-
ENGINE research track).

============================ DUAL-ENGINE DOCTRINE (read this first) ============================
This project runs an intentional ASYMMETRIC DUAL-ENGINE architecture:
  1. The DETAILED POSSESSION ENGINE (Phases 15-23A, this file included)
     -- an event-driven, authoritative basketball-CAUSALITY model. This
     is the future source for watched games, tactical simulation,
     play-by-play, lineup interactions, and generative-world basketball.
  2. The FAST AGGREGATE ENGINE (`game_engine.py`, untouched, not
     imported anywhere in this file) -- the existing, supported
     approximation for fast season/multi-season simulation, legacy
     compatibility, and a benchmark/reference engine. NOT event-emergent.
Both are intentional and currently COEXIST. They share PLAYER TRUTH
(identity/abilities/tendencies/roles/physical evidence/provenance/
temporal cutoffs -- `PlayerSimulationProfile` is an ADAPTER over that
shared truth for THIS engine, never a second, competing identity store)
and are expected to eventually emit a compatible FINAL box-score output
boundary -- accuracy/calibration claims remain separate per engine.

`simulate_possession(...)` is Phase 23A's one-possession integration
infrastructure for the DETAILED engine ONLY. It is NOT yet routed into
`main.py`/`season.py`/`playoffs.py`/`db.py`, and it does NOT replace or
modify the legacy aggregate engine (confirmed: no reference to
`possession_orchestrator`/`simulate_possession` exists anywhere in
those files, and this module never imports `game_engine.py`). It is
explicitly NOT described here as "the repository's canonical public
game API" -- a future, DELIBERATE, separate game-level integration/
migration checkpoint is required before any product code routes through
it. Phase 23A's own scope ends at ONE possession finishing correctly.

============================ SOURCE-OF-TRUTH HIERARCHY (reconciliation, see docs/PHASE23A_*) ============================
Three DISTINCT truths, never conflated:
  - LIVE BASKETBALL STATE: `engine.state` (Phase 15, authoritative,
    unchanged) + `PossessionWorld` (structural context around it, Sec.
    "PossessionWorld" below) -- what is true RIGHT NOW.
  - EVENT-STREAM ACCOUNTING: `engine.log.events` -- the ordered,
    replayable record Phase 15 established as the accounting/source-of-
    truth DIRECTION for this engine. See `derive_stat_deltas_from_events`
    below for exactly what CAN be reconstructed from it today, and
    `docs/PHASE23A_AUTONOMOUS_POSSESSION_KERNEL_REPORT.md`'s
    reconciliation section for the exact, itemized gap list.
  - CONTROL-FLOW / ORCHESTRATION TRUTH: `PossessionTerminalResult` --
    whether the possession ended, why, and the resulting context for a
    future Phase 23B to continue from. This is NOT statistical
    accounting and callers should not need to parse events to get it.
`StatDeltas` (mutated directly by dispatch code as each action
resolves) is a PROVISIONAL, per-possession CONVENIENCE projection, NOT
a second authoritative accounting source -- it does not persist beyond
one `PossessionTerminalResult`, and every field it can currently
express IS cross-checked against `derive_stat_deltas_from_events` in
this phase's own test suite. Fields the current event schema cannot yet
support (points, FGA/FGM, 3PA/3PM, FTA/FTM -- see the itemized gap list)
are honestly flagged as NOT YET EVENT-DERIVABLE rather than silently
trusted as equivalent to a hypothetical future event-derived value.

The CENTRAL CONTROLLER tying together Phases 15-22A into ONE callable,
`simulate_possession(...)`, that runs a complete possession autonomously
from an initial 5v5 configuration to a typed terminal result, without a
test caller manually chaining opportunity generation -> perception ->
selection -> resolver-specific context construction -> clock bookkeeping
-> rebound/foul handoff. This module is GLUE -- it invents no new
basketball mechanic; every actual outcome (drive leverage, pass
disruption, shot make%, contact/whistle, block, rebound, floor-foul
administration) is resolved by the SAME existing resolver each of those
phases already built and tested.

============================ SUPPORTED VS. CAPABILITY-GATED ACTIONS ============================
`SUPPORTED_ACTION_TYPES` = DRIVE, PULL_UP, CATCH_AND_SHOOT, SWING_PASS,
KICKOUT, RESET_PASS, POCKET_PASS -- every action family with a coherent,
already-built resolver this module can route into without inventing new
mechanics. `CAPABILITY_GATED_ACTION_TYPES` = ISOLATION_ATTACK,
CLOSEOUT_ATTACK, TRANSITION_PUSH, OUTLET_PASS, RECOVER_LOOSE_BALL -- NONE
of these have a coherent existing resolver this module can call (there
is no isolation-specific resolver distinct from DRIVE's own leverage
model, no closeout-specific resolver, no transition-push resolver wired
here, and OUTLET_PASS needs `transition_offense.py`'s own transition-
specific opportunity/geometry this module does not build). Gating
happens TWICE: perceived opportunities are filtered to
`SUPPORTED_ACTION_TYPES` BEFORE `SelectionPolicy.select()` ever sees
them (so an unsupported action can never even be SCORED, let alone
chosen), and `dispatch_action` independently raises on anything outside
that set as a structural, defense-in-depth guard. `RECOVER_LOOSE_BALL`
is gated for a different reason: a `BallState.LOOSE` possession is
intercepted at the TOP of the possession loop, before
`generate_opportunities` is even called, and routed to
`_resolve_generic_loose_ball` directly -- it never reaches ordinary
Phase 16 selection at all, so scoring "which of 10 players recovers a
loose ball" via role/tendency weights (which have no real bearing on
that decision) is never attempted.

============================ CLOCK OWNERSHIP (READ THIS BEFORE ADDING A NEW DISPATCH PATH) ============================
`pass_resolution.resolve_pass` ALREADY decrements
`shot_clock_remaining`/`game_clock_remaining` internally by its own real
`FLIGHT_DURATION_SECONDS[family]` (confirmed by direct source read) --
this orchestrator NEVER calls `_charge_time` after a pass dispatch, to
avoid double-charging the same elapsed time. Every OTHER resolver used
here (`drive_resolution`, `shot_resolution`, `interior_shot_resolution`,
`rebound_resolution`, `foul_resolution`, `on_ball_pressure_resolution`)
passes `dt=0.0` everywhere and touches neither clock field at all
(confirmed by direct source read of every one of them) -- for those,
THIS module is the sole clock owner, via `_charge_time`, using a real,
EXPLICITLY UNCALIBRATED, hand-set positive per-action-type duration
(`PossessionConfig`'s `*_action_seconds` fields). No player-specific
speed/pace latent exists anywhere in this file.
"""
import random
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from action_intent import ActionIntent, ActionType, PASS_ACTIONS
from action_opportunity import INTERIOR_ZONES, PERIMETER_ZONES, StructuralContext, generate_opportunities
from action_perception import perceive
from action_selection import ClockContext, RoleContext, SelectionPolicy, TendencyContext
from drive_resolution import DriveResolutionContext, DriveOutcome, resolve_drive
from floor_foul_administration import (
    DEFENSIVE_FLOOR_FOUL,
    OFFENSIVE_CHARGE,
    FoulAdministrationState,
    administer_floor_foul,
)
from foul_resolution import (
    ContactContext,
    FoulEligibleDefender,
    FreeThrowSequence,
    apply_free_throw_attempt_to_engine,
    awarded_free_throws,
    resolve_contact_and_whistle,
    resolve_shooting_foul_shot,
)
from interior_shot_resolution import (
    InteriorDefenderContext,
    InteriorShotContext,
    InteriorShotFamily,
    InteriorShotOutcome,
    apply_interior_shot_to_engine,
    unblocked_make_probability,
)
from on_ball_pressure_resolution import (
    OnBallContactOutcome,
    OnBallPressureContext,
    apply_on_ball_pressure_to_engine,
)
from pass_resolution import DefenderCandidate, PassOutcome, PassResolutionContext, resolve_pass
from possession_engine import PossessionEngine
from possession_rules import EraRules
from possession_events import Event, EventType
from possession_state import (
    BallState, DefensivePosture, DribbleState, PossessionPhase, PossessionState, SpatialZone,
    ball_side,
)
from rebound_resolution import ReboundCandidate, ReboundOpportunity, ReboundOutcome, ReboundSource, apply_rebound_to_engine
from shot_resolution import ContestBucket, ReleaseMode, ShotFamily, ShotOutcome, ShotResolutionContext, apply_shot_resolution_to_engine, shot_make_probability

PerimeterShotFamily = ShotFamily  # alias: foul_resolution.ContactContext.shot_family is a plain string key,
# not tied to any specific enum -- this project's convention (Phase 18A/18B/18C all use plain string family
# keys, never a shared locked Enum) -- reusing shot_resolution.ShotFamily's own THREE_POINT constant by name.

# ---------------------------------------------------------------------
# Supported / capability-gated action sets. See module docstring.
# ---------------------------------------------------------------------
SUPPORTED_ACTION_TYPES = frozenset({
    ActionType.DRIVE, ActionType.PULL_UP, ActionType.CATCH_AND_SHOOT,
    ActionType.SWING_PASS, ActionType.KICKOUT, ActionType.RESET_PASS, ActionType.POCKET_PASS,
})
CAPABILITY_GATED_ACTION_TYPES = frozenset({
    ActionType.ISOLATION_ATTACK, ActionType.CLOSEOUT_ATTACK, ActionType.TRANSITION_PUSH,
    ActionType.OUTLET_PASS, ActionType.RECOVER_LOOSE_BALL,
})


class UnsupportedActionError(ValueError):
    """Raised if an unsupported `ActionType` ever reaches `dispatch_action`
    -- a defense-in-depth structural guard; the perception-stage filter
    (Sec. above) is supposed to make this unreachable in practice."""


class PossessionSimulationFault(RuntimeError):
    """A BUG/FAULT guard, NOT basketball logic -- raised when a
    possession exceeds `PossessionConfig.max_steps_per_possession`
    non-terminal steps. Never silently converted into a turnover or any
    other normal terminal reason; a caller must catch this explicitly.
    Carries diagnostic state so the fault is debuggable, not just a bare
    message."""
    def __init__(self, message: str, steps: int, state: PossessionState, events: Tuple[Event, ...]):
        super().__init__(message)
        self.steps = steps
        self.state = state
        self.events = events


# ---------------------------------------------------------------------
# 1. PlayerSimulationProfile -- the smallest typed adapter over existing
# empirical outputs. NOT a new empirical model, NOT an OVR, NOT a
# combination of attributes into one score. Every field is Optional
# (missing != zero -- see the capability-gating raised in dispatch when
# a SUPPORTED action needs a field this profile doesn't have). No
# physical field exists here at all -- V0's supported action set (drive/
# shot/pass/rebound/foul) needs none of them: `DriveResolutionContext`'s
# own physical adjustment is explicit opt-in and defaults off,
# `InteriorDefenderContext` has no physical field, and no other
# supported resolver reads one either (confirmed by direct source read
# of every resolver imported above).
#
# ============================ REAL-ADAPTER CONSTRUCTION CONTRACT (audited, verified this phase) ============================
# This dataclass is filled by SYNTHETIC/test values in Phase 23A (see
# `PlayerSimulationProfile.synthetic` below) -- real ingestion from
# `player_ability_estimation.py`/`role_off_profile.py`/tendency
# estimators is explicitly NOT built this phase (per instruction: "we do
# NOT need perfect real-player ingestion before testing orchestration").
# A FUTURE real-ingestion constructor MUST follow this binding contract,
# verified against the estimator/resolver source this phase:
#   1. Every field is the estimator's own NATIVE, ALREADY-SHRUNK rate
#      (e.g. `EstimationResult.shrunk_rate`, a tendency's own
#      `latent_propensity`) -- NEVER `rating_0_99`, a display percentile,
#      or any other display-abstraction conversion, which can discard
#      scale/provenance information a resolver depends on.
#   2. Every source call contributing to ONE profile instance must share
#      the SAME `as_of_season` -- never mixed cutoffs across fields.
#   3. Any HISTORICAL ball-security proxy path must filter its own
#      available-seasons list to `season <= as_of_season` BEFORE use, or
#      remain disabled if that filtering cannot be proven safe (the
#      historical proxy can otherwise inspect seasons after the
#      requested cutoff -- a real, audited temporal-leakage risk).
#   4. Evidence/provenance/exposure/confidence/cutoff metadata from the
#      canonical estimator objects should be RETAINED alongside (not
#      discarded by) whatever thin adapter produces these fields -- this
#      dataclass itself is intentionally numeric-only for resolver
#      consumption, but a real constructor should keep the canonical
#      `AttributeEstimate`/report objects available for provenance
#      auditing, not silently drop them.
#   5. A missing/insufficient estimate must produce `None` here --
#      never 0, 0.5, 50, or a league-average substitute chosen by the
#      ADAPTER (a resolver's OWN documented missing-evidence policy, if
#      any, is a separate and legitimate thing -- see the rebound
#      gating below for a case where this module does NOT trust a
#      resolver's own fallback).
#
# ============================ DISABLED-BY-DEFAULT FIELDS (verified, not silently wired) ============================
# `foul_drawing_shrunk_rate`/`foul_discipline_shrunk_rate`: PRESENT as
# optional evidence, but NEVER read by any dispatch path in this module
# (verified: `test_foul_drawing_and_discipline_never_reach_a_resolver`)
# -- the existing foul resolvers expect a CENTERED, modifier-like value,
# while these estimators produce native rates on a different scale (and
# `foul_discipline`'s own native direction -- higher shrunk rate = WORSE
# discipline -- is the OPPOSITE sign some resolver call sites' naming
# suggests), and no validated conversion exists. Phase 23A passes `None`
# for both at every call site, which is each resolver's own documented
# neutral/no-adjustment behavior -- NOT a fabricated conversion.
# `playmaking_vision_shrunk_rate`: PRESENT as optional evidence, NEVER
# read by `perceive()` in this module -- `estimate_playmaking_vision()`'s
# own shrunk rate is not on the same scale `perceive()`'s
# `vision_latent_propensity` expects, and no validated conversion exists.
# `perceive(..., None, rng)` is called unconditionally, which is that
# function's own documented neutral (league-average) behavior.
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class PlayerSimulationProfile:
    player_id: str
    team_id: str
    # shot-family execution (shooter-side) -- Phase 1-3/5 estimators' own native shrunk_rate, real [0,1] make rates.
    # Used DIRECTLY as ShotResolutionContext.shooter_base_rate / InteriorShotContext.shooter_base_rate -- no
    # adapter-side rating conversion, no additional z-score.
    three_point_shrunk_rate: Optional[float] = None
    rim_finishing_shrunk_rate: Optional[float] = None
    floater_short_mid_shrunk_rate: Optional[float] = None
    free_throw_shrunk_rate: Optional[float] = None
    # shooting-foul-adjacent (Phase 6) -- DISABLED THIS PHASE, see module docstring above. Present as evidence
    # only; never read by any dispatch path (verified by test).
    foul_drawing_shrunk_rate: Optional[float] = None
    foul_discipline_shrunk_rate: Optional[float] = None
    # drive-adjacent (Phase 9/10) -- native shrunk rates, used EXACTLY ONCE per drive dispatch, passed straight
    # through to DriveResolutionContext; drive_resolution.py performs its OWN standardization against its own
    # embedded population constants -- this adapter does not z-score them a second time.
    rim_access_creation_shrunk_rate: Optional[float] = None
    poa_containment_shrunk_rate: Optional[float] = None
    # rim-protection (Phase 7, defender-side) -- suppresses UNBLOCKED conversion only; never generates a block
    # itself (defensive_playmaking_per36 owns block probability -- see below). Native suppression_rate.
    rim_protection_suppression_rate: Optional[float] = None
    # passing -- the generic passing estimator's shrunk AST_PCT proxy. AST_PCT != a direct completion
    # probability; fed ONLY into pass_resolution.py's own small, already-tested delivery-quality modifier
    # (PassResolutionContext.passing_accuracy), never treated as an overall pass-success probability.
    passing_accuracy_ast_pct: Optional[float] = None
    # ball-security -- the SPECIALIZED handling-error estimator (handling errors per touch, excluding bad-pass
    # and offensive-foul categories). HIGHER = WORSE security. NOT inverted here -- on_ball_pressure_resolution.py
    # already interprets this native direction correctly (higher error rate -> more disruption risk); the
    # `ball_security` dataclass field comment in that module claiming "lower = worse" is itself erroneous and is
    # NOT followed here -- estimator + resolver behavior are the semantic truth, not that stray comment.
    ball_security_error_rate: Optional[float] = None
    # STL+BLK per-36 (Phase 1-3) -- owns pass-disruption likelihood AND block probability; ONLY a small, flagged
    # weight for on-ball strips (Phase 21A's own KEEP-BUT-FLAG posture, unchanged). Never broadened into a
    # generic defensive-IQ signal anywhere in this module.
    defensive_playmaking_per36: Optional[float] = None
    # vision -- DISABLED THIS PHASE, see module docstring above. Present as evidence only.
    playmaking_vision_shrunk_rate: Optional[float] = None
    # rebounding (Phase 1-3) -- native, ALREADY side-specific (OREB_PCT/DREB_PCT) shrunk rates. A candidate
    # missing the side-specific value for their own side is EXCLUDED from rebound competition entirely by this
    # module (see `_dispatch_rebound`) -- this module does NOT rely on rebound_resolution.py's own internal
    # `None -> 0.5` fallback (verified: `test_rebound_excludes_players_with_missing_estimate`).
    offensive_rebounding_shrunk_rate: Optional[float] = None
    defensive_rebounding_shrunk_rate: Optional[float] = None
    # tendencies (Phase 11) -- each estimator's own `latent_propensity`, NOT a raw rate or display percentile.
    # Selection-layer only (action_selection.py's own firewall, unchanged).
    drive_aggression: Optional[float] = None
    pass_vs_shoot: Optional[float] = None
    three_point_preference: Optional[float] = None
    midrange_preference: Optional[float] = None
    pullup_vs_catch: Optional[float] = None
    # role (Phase 13) -- PlayerRoleProfile's own real per-36/share values (POTENTIAL_AST per-36 for initiation,
    # PCT_AST_FGM share for finishing, PCT_AST_3PM share for spacing), `.value` extracted only when evidence is
    # available (missing -> None, never 0). Deployment/opportunity context only -- never converted into skill.
    role_off_initiation: Optional[float] = None
    role_off_finishing: Optional[float] = None
    role_off_spacing: Optional[float] = None

    @staticmethod
    def synthetic(player_id: str, team_id: str, **overrides) -> "PlayerSimulationProfile":
        """Deterministic, clearly-synthetic V0 test profile -- explicit,
        real-world-plausible placeholder values, not empirically derived.
        Used by this phase's own tests/traces; a real profile is built by
        a FUTURE adapter following the construction contract above, not
        by this helper."""
        defaults = dict(
            three_point_shrunk_rate=0.36, rim_finishing_shrunk_rate=0.62, floater_short_mid_shrunk_rate=0.40,
            free_throw_shrunk_rate=0.78, rim_access_creation_shrunk_rate=0.5, poa_containment_shrunk_rate=0.0,
            rim_protection_suppression_rate=0.0, passing_accuracy_ast_pct=0.18, ball_security_error_rate=0.0086,
            defensive_playmaking_per36=1.5, offensive_rebounding_shrunk_rate=0.08, defensive_rebounding_shrunk_rate=0.15,
            drive_aggression=0.0, pass_vs_shoot=0.0, three_point_preference=0.0, midrange_preference=0.0,
            pullup_vs_catch=0.0, role_off_initiation=4.0, role_off_finishing=0.5, role_off_spacing=0.5,
        )
        defaults.update(overrides)
        return PlayerSimulationProfile(player_id=player_id, team_id=team_id, **defaults)


def _role_context(profile: PlayerSimulationProfile) -> RoleContext:
    return RoleContext(role_off_initiation=profile.role_off_initiation, role_off_finishing=profile.role_off_finishing,
                        role_off_spacing=profile.role_off_spacing)


def _tendency_context(profile: PlayerSimulationProfile) -> TendencyContext:
    return TendencyContext(drive_aggression=profile.drive_aggression, pass_vs_shoot=profile.pass_vs_shoot,
                            three_point_preference=profile.three_point_preference,
                            midrange_preference=profile.midrange_preference, pullup_vs_catch=profile.pullup_vs_catch)


# ---------------------------------------------------------------------
# 2. Lineups / matchup initializer -- the smallest safe 5v5 structure.
# ---------------------------------------------------------------------
def validate_lineups(team_a_five: Tuple[str, ...], team_b_five: Tuple[str, ...]) -> None:
    """Deterministic validation: exactly 5 unique ids per side, and no
    player on both. Raises ValueError immediately on any violation --
    never silently truncates/dedupes."""
    if len(team_a_five) != 5 or len(set(team_a_five)) != 5:
        raise ValueError(f"team_a_five must be exactly 5 unique player_ids, got {team_a_five!r}")
    if len(team_b_five) != 5 or len(set(team_b_five)) != 5:
        raise ValueError(f"team_b_five must be exactly 5 unique player_ids, got {team_b_five!r}")
    overlap = set(team_a_five) & set(team_b_five)
    if overlap:
        raise ValueError(f"a player cannot appear on both teams -- overlap: {overlap!r}")


def build_matchup_assignments(offensive_five: Tuple[str, ...], defensive_five: Tuple[str, ...],
                               matchup_pairs: Optional[List[Tuple[str, str]]] = None) -> Dict[str, "DefensiveAssignment"]:
    """The smallest valid 5v5 defensive-assignment initializer -- also
    reusable to REBUILD assignments after a possession change (a future
    caller passes the new offensive/defensive fives in their new roles).
    `matchup_pairs`, if given, is `[(defender_id, offensive_target_id), ...]`
    -- validated for a real bijection; otherwise deterministic lineup-
    order pairing (`defensive_five[i]` guards `offensive_five[i]`).
    Postures always initialize to SQUARE. No optimizer, no AI -- a plain,
    explicit, deterministic pairing."""
    from possession_state import DefensiveAssignment
    validate_lineups(offensive_five, defensive_five)
    if matchup_pairs is None:
        pairs = list(zip(defensive_five, offensive_five))
    else:
        pairs = list(matchup_pairs)
    defenders = [d for d, _ in pairs]
    targets = [o for _, o in pairs]
    if len(pairs) != 5:
        raise ValueError(f"matchup_pairs must contain exactly 5 pairs, got {len(pairs)}")
    if len(set(defenders)) != 5:
        raise ValueError(f"matchup_pairs defenders must be 5 unique ids, got {defenders!r}")
    if len(set(targets)) != 5:
        raise ValueError(f"matchup_pairs offensive targets must be 5 unique ids, got {targets!r} -- no duplicate assignment")
    if set(defenders) != set(defensive_five) or set(targets) != set(offensive_five):
        raise ValueError("matchup_pairs must exactly cover the supplied offensive/defensive fives -- no uncovered player")
    return {d: DefensiveAssignment(defender_id=d, assigned_to_player_id=o, posture=DefensivePosture.SQUARE) for d, o in pairs}


def apply_matchup_assignments(engine: PossessionEngine, offensive_five: Tuple[str, ...], defensive_five: Tuple[str, ...],
                               matchup_pairs: Optional[List[Tuple[str, str]]] = None) -> None:
    """Applies `build_matchup_assignments` to `engine.state` in ONE
    atomic `replace()` -- same "compute the whole dict, then one
    transition" posture as Phase 22A's own atomic switch, for the same
    reason (no intermediate, partially-covered assignment state should
    ever be observable)."""
    assignments = build_matchup_assignments(offensive_five, defensive_five, matchup_pairs)
    engine.state = replace(engine.state, assignments=assignments)


# ---------------------------------------------------------------------
# 3. PossessionWorld -- structural context AROUND the authoritative
# engine state, never a second mutable copy of anything PossessionState
# already owns. OWNERSHIP:
#   - engine.state owns: ball_carrier/ball_state/ball_control/ball_zone,
#     offense_team_id/defense_team_id (WHOEVER currently has the ball --
#     this FLIPS during the possession), assignments (defender->offender
#     pointer + posture), shot_clock/game_clock. This module never keeps
#     a second copy of any of these.
#   - world owns: the STATIC lineup<->team_id binding (team_a_id/
#     team_a_five, team_b_id/team_b_five -- these do NOT flip when
#     possession changes; "current offense five" is DERIVED by comparing
#     `engine.state.offense_team_id` against team_a_id/team_b_id, never
#     stored as its own mutable field), the per-player coarse
#     `SpatialZone` for all ten players (PossessionState has no
#     non-ball-related positional concept at all -- this is genuinely
#     new information, not duplicated authority), the one-shot
#     `just_caught_pass_player_id` transient (Phase 16 established this
#     as a purely caller-supplied fact; no Phase 15 engine method sets
#     it), the profile lookup, the running `FoulAdministrationState`
#     (shared across both floor-foul and shooting-foul bookkeeping this
#     possession, since a personal/team foul is a personal/team foul
#     regardless of type in real accounting), stat deltas, and a
#     diagnostic trace.
# ---------------------------------------------------------------------
@dataclass
class StatDeltas:
    """PROVISIONAL, per-possession CONVENIENCE projection -- NOT a
    second authoritative accounting source. See the module docstring's
    "SOURCE-OF-TRUTH HIERARCHY" section. `EVENT STREAM ACCOUNTING`
    (`engine.log.events`) is the real, ordered, replayable accounting
    truth Phase 15 established; `StatDeltas` is mutated directly by
    dispatch code as a cheap, immediate convenience for a caller (a
    test, a diagnostic trace) that doesn't want to re-parse events every
    time. Every field this class CAN currently express is cross-checked
    against `derive_stat_deltas_from_events` in this phase's own test
    suite (`test_possession_orchestrator.py`'s `TestEventStatConsistency`)
    -- `oreb`, `dreb`, `turnovers`, `steals`, `blocks`, and
    `personal_fouls` all currently have full event-stream parity.
    `points`/`fga`/`fgm`/`fg3a`/`fg3m`/`fta`/`ftm` do NOT yet have
    event-stream parity (see `derive_stat_deltas_from_events`'s own
    docstring for the exact missing event-schema fields) -- this class
    still tracks them (so a caller has SOMETHING today), but they are
    honestly NOT claimed to be independently re-derivable from the event
    stream yet, and no test asserts they are. This class holds NO
    state beyond one possession -- it is never a persistent, cross-
    possession accounting store."""
    points: int = 0
    fga: int = 0
    fgm: int = 0
    fg3a: int = 0
    fg3m: int = 0
    fta: int = 0
    ftm: int = 0
    oreb: int = 0
    dreb: int = 0
    turnovers: int = 0
    steals: int = 0
    blocks: int = 0
    personal_fouls: Dict[str, int] = field(default_factory=dict)  # player_id -> count, this possession only

    def add_personal_foul(self, player_id: str) -> None:
        self.personal_fouls[player_id] = self.personal_fouls.get(player_id, 0) + 1


@dataclass
class EventDerivedStats:
    """The stats CURRENTLY reconstructable from `engine.log.events`
    ALONE -- structured event `event_type`/`primary_player_id`/
    `secondary_player_id`/`metadata` fields only, NEVER prose/log-string
    interpretation. See `derive_stat_deltas_from_events` for exactly
    which event(s) back each field. Deliberately has NO `points`/`fga`/
    `fgm`/`fg3a`/`fg3m`/`fta`/`ftm` fields at all -- their absence here
    IS the honest classification (NOT DERIVABLE NOW), not an oversight;
    see that function's docstring for the exact missing event-schema
    fields a future phase would need to add them."""
    oreb: int = 0
    dreb: int = 0
    turnovers: int = 0
    steals: int = 0
    blocks: int = 0
    personal_fouls: Dict[str, int] = field(default_factory=dict)


def derive_stat_deltas_from_events(events: Tuple[Event, ...]) -> EventDerivedStats:
    """Reconstructs DERIVABLE-NOW stats purely from the ordered event
    stream -- the EVENT STREAM ACCOUNTING side of the source-of-truth
    hierarchy (module docstring). Classification, verified against every
    resolver's own `_log`/event-emitting source this phase touches:

    DERIVABLE NOW:
      - OREB: count of `EventType.OFFENSIVE_REBOUND` events.
      - DREB: count of `EventType.DEFENSIVE_REBOUND` events.
      - turnovers: `EventType.DEAD_BALL_TURNOVER` + `EventType.LIVE_BALL_TURNOVER`
        events, PLUS `PASS_RESOLVED` events whose `metadata["outcome"]`
        is `pass_resolution.py`'s own `PassOutcome.BAD_PASS_OUT_OF_BOUNDS`
        (always), `.CLEAN_INTERCEPTION` (always), or
        `.BAD_PASS_TO_DEFENDER` ONLY when `metadata["disrupting_defender_id"]`
        is not `None` (per `pass_resolution._apply_outcome`'s own real
        rule: a `None` target means the ball went LOOSE instead, which
        the offense may recover -- NOT, by itself, a turnover; these are
        `pass_resolution.py`'s own structured `PassOutcome` string
        constants, never re-detected, just recognized, and never free
        prose), PLUS this module's own supplementary `REACTION_CHECKPOINT`
        checkpoint `"generic_loose_ball_recovered"` when `recovery ==
        "DEFENSE_RECOVERED"` (Sec. `resolve_generic_loose_ball`).
      - steals: `PASS_RESOLVED` events with `metadata["outcome"] ==
        "CLEAN_INTERCEPTION"` and a real `metadata["disrupting_defender_id"]`.
      - blocks: count of `EventType.BLOCK_RETAINED_BY_OFFENSE` +
        `EventType.BLOCK_SECURED_BY_DEFENSE` events.
      - personal_fouls: `EventType.SHOOTING_FOUL` events
        (`primary_player_id` = fouler) + `EventType.NON_SHOOTING_FOUL`
        events (`primary_player_id` = fouler) + this module's own
        supplementary `REACTION_CHECKPOINT` checkpoint
        `"floor_foul_administered"` (covers the `OFFENSIVE_CHARGE` case,
        whose own underlying `DEAD_BALL_TURNOVER` event is otherwise
        indistinguishable from any other dead-ball turnover).

    NOT DERIVABLE NOW (see the report's reconciliation section for the
    exact missing fields):
      - points/FGA/FGM/3PA/3PM: `EventType.SHOT_RESOLVED` carries only
        `metadata["made"]` -- no `shot_family`/point-value field exists
        anywhere in the Phase 15 event schema, and this module's own
        whistled-and-one path (`_dispatch_shooting_foul`) does not log a
        `SHOT_RESOLVED` event for the underlying shot AT ALL (it calls
        `engine.begin_shot`/`engine.shooting_foul` directly, bypassing
        `resolve_shot_made`/`resolve_shot_missed_pending_rebound`
        entirely). Fixing this requires adding a `shot_family`/points
        field to `SHOT_RESOLVED`'s metadata (a Phase 15 `possession_engine.py`
        change) and/or this module logging its own supplementary
        shot-resolution event for the and-one case -- NOT done this
        phase (not tiny: it touches a foundational, already-tested,
        cross-phase file).
      - FTA/FTM: NO free-throw event type or logging exists ANYWHERE in
        this repository (`foul_resolution.apply_free_throw_attempt_to_engine`
        performs a bare `dataclasses.replace()` on `engine.state` with no
        `_log` call at all, confirmed by direct source read). A future
        Phase 23B needs either a new `EventType` for a free-throw
        attempt, or a caller-side (this module's) supplementary
        checkpoint logged per attempt -- NOT done this phase for the
        SAME reason (the floor-foul-administration FT loop lives inside
        Phase 21B's own `administer_floor_foul`, outside this module's
        per-attempt control, so only a HALF-fix -- enriching the
        shooting-foul FT loop but not the floor-foul bonus FT loop --
        would be achievable without touching an existing phase's file;
        an inconsistent half-fix was judged worse than an honest gap).
    """
    result = EventDerivedStats()
    for event in events:
        if event.event_type == EventType.OFFENSIVE_REBOUND:
            result.oreb += 1
        elif event.event_type == EventType.DEFENSIVE_REBOUND:
            result.dreb += 1
        elif event.event_type in (EventType.DEAD_BALL_TURNOVER, EventType.LIVE_BALL_TURNOVER):
            result.turnovers += 1
        elif event.event_type in (EventType.BLOCK_RETAINED_BY_OFFENSE, EventType.BLOCK_SECURED_BY_DEFENSE):
            result.blocks += 1
        elif event.event_type == EventType.SHOOTING_FOUL:
            if event.primary_player_id is not None:
                result.personal_fouls[event.primary_player_id] = result.personal_fouls.get(event.primary_player_id, 0) + 1
        elif event.event_type == EventType.NON_SHOOTING_FOUL:
            if event.primary_player_id is not None:
                result.personal_fouls[event.primary_player_id] = result.personal_fouls.get(event.primary_player_id, 0) + 1
        elif event.event_type == EventType.PASS_RESOLVED:
            outcome = event.metadata.get("outcome")
            disrupting_defender_id = event.metadata.get("disrupting_defender_id")
            if outcome == "BAD_PASS_OUT_OF_BOUNDS":
                result.turnovers += 1  # always a real dead-ball turnover, regardless of a known defender id
            elif outcome == "BAD_PASS_TO_DEFENDER" and disrupting_defender_id is not None:
                # per pass_resolution.py's own `_apply_outcome`: only a REAL turnover when a specific
                # defender actually secured it -- a `None` target means the ball went LOOSE instead (the
                # offense may recover it), which is NOT, by itself, a turnover.
                result.turnovers += 1
            elif outcome == "CLEAN_INTERCEPTION":
                result.turnovers += 1
                if disrupting_defender_id is not None:
                    result.steals += 1
        elif event.event_type == EventType.REACTION_CHECKPOINT:
            checkpoint = event.metadata.get("checkpoint")
            if checkpoint == "generic_loose_ball_recovered" and event.metadata.get("recovery") == "DEFENSE_RECOVERED":
                result.turnovers += 1
            elif checkpoint == "floor_foul_administered" and event.metadata.get("foul_class") == OFFENSIVE_CHARGE \
                    and event.primary_player_id is not None:
                # ONLY the OFFENSIVE_CHARGE case -- a DEFENSIVE_FLOOR_FOUL's personal foul is already counted
                # via its own real `NON_SHOOTING_FOUL` event above; counting this checkpoint for THAT class
                # too would double-count the same real foul.
                result.personal_fouls[event.primary_player_id] = result.personal_fouls.get(event.primary_player_id, 0) + 1
    return result


@dataclass
class PossessionWorld:
    team_a_id: str
    team_b_id: str
    team_a_five: Tuple[str, ...]
    team_b_five: Tuple[str, ...]
    profiles: Dict[str, PlayerSimulationProfile]
    player_zones: Dict[str, SpatialZone] = field(default_factory=dict)
    just_caught_pass_player_id: Optional[str] = None
    loose_ball_favored_team_id: Optional[str] = None
    foul_state: FoulAdministrationState = field(default_factory=FoulAdministrationState)
    stats: StatDeltas = field(default_factory=StatDeltas)
    trace: List[dict] = field(default_factory=list)

    def team_id_for(self, player_id: str) -> str:
        if player_id in self.team_a_five:
            return self.team_a_id
        if player_id in self.team_b_five:
            return self.team_b_id
        raise ValueError(f"{player_id!r} is not on either lineup")

    def five_for_team(self, team_id: str) -> Tuple[str, ...]:
        if team_id == self.team_a_id:
            return self.team_a_five
        if team_id == self.team_b_id:
            return self.team_b_five
        raise ValueError(f"{team_id!r} is not one of this world's two teams")

    def offense_five(self, engine: PossessionEngine) -> Tuple[str, ...]:
        return self.five_for_team(engine.state.offense_team_id)

    def defense_five(self, engine: PossessionEngine) -> Tuple[str, ...]:
        return self.five_for_team(engine.state.defense_team_id)

    def teammates_of(self, engine: PossessionEngine, player_id: str) -> Tuple[str, ...]:
        five = self.five_for_team(self.team_id_for(player_id))
        return tuple(p for p in five if p != player_id)

    def all_ten(self) -> Tuple[str, ...]:
        return self.team_a_five + self.team_b_five

    def log_trace(self, **kwargs) -> None:
        self.trace.append(kwargs)


def _primary_defender(engine: PossessionEngine, offensive_player_id: str) -> Optional[str]:
    for defender_id, assignment in engine.state.assignments.items():
        if assignment.assigned_to_player_id == offensive_player_id:
            return defender_id
    return None


# ---------------------------------------------------------------------
# 4. Deterministic V0 zone placement -- ORCHESTRATION SCAFFOLDING, NOT
# calibrated player behavior/tendency-informed positioning. A future
# phase may replace this with something real; this exists only so
# contexts (perimeter receivers, nearest teammate, defender zones for
# pass/rebound eligibility) have SOMETHING coarse and deterministic to
# read at possession start.
# ---------------------------------------------------------------------
_V0_PERIMETER_CYCLE: Tuple[SpatialZone, ...] = (
    SpatialZone.LEFT_WING, SpatialZone.RIGHT_WING, SpatialZone.LEFT_CORNER,
    SpatialZone.RIGHT_CORNER, SpatialZone.TOP_OF_KEY,
)


def default_v0_zone_placement(offensive_five: Tuple[str, ...], ball_handler_id: str,
                               ball_zone: SpatialZone) -> Dict[str, SpatialZone]:
    """Ball handler at `ball_zone`; the other four offensive players
    cycle deterministically through the five coarse perimeter zones in
    lineup order (skipping duplication of the ball handler's own zone
    only incidentally -- this is a placement CYCLE, not a spacing
    optimizer). Explicit and swappable: a caller may instead supply its
    own `player_zones` entirely (see `simulate_possession`'s
    `initial_player_zones` parameter)."""
    zones = {ball_handler_id: ball_zone}
    others = [p for p in offensive_five if p != ball_handler_id]
    for i, pid in enumerate(others):
        zones[pid] = _V0_PERIMETER_CYCLE[i % len(_V0_PERIMETER_CYCLE)]
    return zones


def _mirror_defender_zones(zones: Dict[str, SpatialZone], assignments) -> None:
    """Each defender starts in the SAME coarse zone as their assignment
    -- the simplest structurally-coherent man-to-man starting geometry,
    not a claim about real defensive positioning tendencies."""
    for defender_id, assignment in assignments.items():
        zones[defender_id] = zones.get(assignment.assigned_to_player_id, SpatialZone.TOP_OF_KEY)


# ---------------------------------------------------------------------
# 5. StructuralContext derivation -- built automatically from
# authoritative engine.state + world, per the required field-by-field
# policy (module docstring's own table, reproduced in each helper's
# docstring below).
# ---------------------------------------------------------------------
def _nearest_teammate_id(engine: PossessionEngine, world: PossessionWorld, carrier_id: str) -> Optional[str]:
    """Deterministic COARSE zone/topology proximity -- NOT Euclidean
    distance (no continuous coordinates exist). A teammate whose zone
    shares the carrier's `ball_side()` classification (LEFT/RIGHT/
    CENTRAL) is considered "nearest"; ties broken by lineup order. Falls
    back to the first teammate in lineup order if none share a side."""
    teammates = world.teammates_of(engine, carrier_id)
    if not teammates:
        return None
    carrier_side = ball_side(world.player_zones.get(carrier_id, engine.state.ball_zone))
    for pid in teammates:
        if ball_side(world.player_zones.get(pid, engine.state.ball_zone)) == carrier_side:
            return pid
    return teammates[0]


def build_structural_context(engine: PossessionEngine, world: PossessionWorld) -> StructuralContext:
    """Derives every `StructuralContext` field automatically:
      teammate_ids            -> world.offense_five(engine) minus carrier
      perimeter_receiver_ids  -> teammates whose world.player_zones entry is a PERIMETER_ZONE
      ball_handler_defender_id-> engine.state.assignments (whoever guards the carrier)
      just_caught_pass        -> world.just_caught_pass_player_id == carrier (consumed once by the caller)
      roller_id / screen_active -> always None / False -- Phase 22A's off-ball screen primitive remains
                                     caller-triggered; V0 never fabricates a live screen (per explicit instruction)
      nearest_teammate_id     -> _nearest_teammate_id (coarse zone topology, see above)
    """
    carrier = engine.state.ball_carrier
    teammates = tuple(p for p in world.offense_five(engine) if p != carrier)
    perimeter_receivers = {p: world.player_zones[p] for p in teammates
                            if world.player_zones.get(p) in PERIMETER_ZONES}
    defender_id = _primary_defender(engine, carrier) if carrier else None
    just_caught = world.just_caught_pass_player_id == carrier
    return StructuralContext(
        teammate_ids=list(teammates), perimeter_receiver_ids=perimeter_receivers,
        roller_id=None, screen_active=False,
        nearest_teammate_id=_nearest_teammate_id(engine, world, carrier) if carrier else None,
        just_caught_pass=just_caught, ball_handler_defender_id=defender_id,
    )


# ---------------------------------------------------------------------
# 6. Clock ownership -- see module docstring.
# ---------------------------------------------------------------------
def _charge_time(engine: PossessionEngine, dt: float) -> None:
    if dt <= 0.0:
        raise ValueError("every dispatched LIVE action must consume dt > 0 -- got a non-positive duration")
    new_shot = None if engine.state.shot_clock_remaining is None else max(0.0, engine.state.shot_clock_remaining - dt)
    new_game = None if engine.state.game_clock_remaining is None else max(0.0, engine.state.game_clock_remaining - dt)
    engine.state = replace(engine.state, shot_clock_remaining=new_shot, game_clock_remaining=new_game)


@dataclass
class PossessionConfig:
    """Explicit, UNCALIBRATED V0 knobs -- every duration is a real,
    hand-set positive placeholder whose only job is loop correctness
    (every live action progresses time), not realism. No player-specific
    speed/pace latent is read anywhere. `force_on_ball_contact_established`
    is a TEST-ONLY override (see `_dispatch_drive`'s docstring) -- False
    is the honest V0 default because no real per-drive contact-occurrence
    rate has ever been derived in this project."""
    season: str = "2023-24"
    initial_ball_zone: SpatialZone = SpatialZone.TOP_OF_KEY
    drive_action_seconds: float = 2.5
    pull_up_action_seconds: float = 1.5
    catch_and_shoot_action_seconds: float = 1.0
    loose_ball_action_seconds: float = 0.5
    max_steps_per_possession: int = 100
    force_on_ball_contact_established: bool = False
    default_free_throw_rate_if_missing: Optional[float] = None  # None = fail explicitly (see _require_ft_rate); no silent placeholder unless a caller opts in
    era_rules: Optional["EraRules"] = None  # overrides `season`-derived era rules when set -- e.g. a test constructing a short game clock to reach PERIOD_END quickly
    # Phase 23B additive orchestration inputs. `None` preserves Phase 23A's
    # original behavior (a fresh period clock and HALFCOURT start).
    initial_game_clock_seconds: Optional[float] = None
    initial_phase: PossessionPhase = PossessionPhase.HALFCOURT


def _require(value: Optional[float], what: str) -> float:
    if value is None:
        raise ValueError(f"missing required player-model estimate for {what} -- capability-gate this action/path "
                          f"or supply a synthetic PlayerSimulationProfile value; this module never silently treats "
                          f"missing evidence as zero")
    return value


def _require_ft_rate(profile: PlayerSimulationProfile, config: PossessionConfig) -> float:
    if profile.free_throw_shrunk_rate is not None:
        return profile.free_throw_shrunk_rate
    if config.default_free_throw_rate_if_missing is not None:
        return config.default_free_throw_rate_if_missing  # explicit caller opt-in only -- documented, not a silent default
    raise ValueError(f"player {profile.player_id!r} has no free_throw_shrunk_rate estimate and no "
                      f"default_free_throw_rate_if_missing was configured -- cannot administer free throws")


# ---------------------------------------------------------------------
# 7. Terminal result.
# ---------------------------------------------------------------------
class PossessionTerminalReason:
    MADE_FG = "MADE_FG"
    FINAL_FT_MADE = "FINAL_FT_MADE"                    # points scored via FT(s) alone (missed shooting foul, or bonus non-shooting foul) -- ball to opponent
    DEFENSIVE_REBOUND = "DEFENSIVE_REBOUND"
    TURNOVER = "TURNOVER"                              # bad pass / interception / defense wins a loose ball
    OFFENSIVE_FOUL_TURNOVER = "OFFENSIVE_FOUL_TURNOVER"  # a charge -- distinct per the task's own suggested vocabulary
    SHOT_CLOCK_VIOLATION = "SHOT_CLOCK_VIOLATION"
    PERIOD_END = "PERIOD_END"


@dataclass
class PossessionTerminalResult:
    """The ONE machine-readable outcome -- a caller never has to inspect
    event strings or resolver-specific metadata to know the possession
    ended, or who has the ball next. `resulting_offense_team_id`/
    `resulting_defense_team_id` are set EXPLICITLY by this module for
    every reason (not merely read off `engine.state`, since some
    Phase-15 terminal primitives -- e.g. `dead_ball_turnover` -- do not
    themselves flip team ids, by their own documented design, leaving
    that to "the caller starts a new PossessionEngine"; this module IS
    that caller, and always resolves the real next-possession team ids
    here)."""
    reason: str
    resulting_offense_team_id: Optional[str]
    resulting_defense_team_id: Optional[str]
    stats: StatDeltas
    steps_taken: int
    engine_state: PossessionState
    events: Tuple[Event, ...]
    world: PossessionWorld


def _terminal(reason: str, engine: PossessionEngine, world: PossessionWorld, steps: int,
              offense_team_id: Optional[str] = None, defense_team_id: Optional[str] = None) -> PossessionTerminalResult:
    # Phase 23B reconciliation: a typed terminal result owns next-possession
    # control flow, so every possession-ending flip must be explicit here.
    # Several Phase 15 dead-ball transitions deliberately leave the OLD team
    # ids on engine.state for a caller to replace; returning those old ids from
    # Phase 23A made the supposedly typed handoff incorrect.
    if offense_team_id is None and defense_team_id is None:
        must_flip = reason in (
            PossessionTerminalReason.MADE_FG,
            PossessionTerminalReason.FINAL_FT_MADE,
            PossessionTerminalReason.SHOT_CLOCK_VIOLATION,
        ) or (reason == PossessionTerminalReason.TURNOVER and engine.state.phase == PossessionPhase.DEAD_BALL)
        if must_flip:
            # `world.team_a_id` is the offense at THIS possession's
            # initialization and never mutates. It remains trustworthy even
            # when a final missed/made FT temporarily clears team ownership
            # on live state; deriving from `engine.state` here was the Phase
            # 23A chaining bug this reconciliation closes.
            offense_team_id, defense_team_id = world.team_b_id, world.team_a_id
    return PossessionTerminalResult(
        reason=reason,
        resulting_offense_team_id=offense_team_id if offense_team_id is not None else engine.state.offense_team_id,
        resulting_defense_team_id=defense_team_id if defense_team_id is not None else engine.state.defense_team_id,
        stats=world.stats, steps_taken=steps, engine_state=engine.state, events=engine.log.events, world=world,
    )


# ---------------------------------------------------------------------
# 8. Generic loose-ball recovery -- the smallest structural closure
# mechanism for a LOOSE ball NOT produced by a shot/block/FT miss (those
# are already closed by Phase 19's own `rebound_resolution.py`, which
# this module reuses as-is -- see `_dispatch_rebound`). This resolver
# exists ONLY for pass deflections (`DEFLECTED_LOOSE_BALL`) and on-ball
# strips (`CLEAN_STRIP_LOOSE`), which are NOT shot-adjacent and do not
# fit rebound_resolution's own carom-zone framing.
# ---------------------------------------------------------------------
def resolve_generic_loose_ball(engine: PossessionEngine, world: PossessionWorld, rng: random.Random,
                                favored_team_id: Optional[str] = None) -> str:
    """Eligible players = whoever (from either team) is currently in the
    SAME coarse zone as the loose ball (`engine.state.ball_zone`); falls
    back to all ten players if the zone bookkeeping happens to have
    nobody there (never crashes, never fabricates a phantom carrier). NO
    new loose-ball skill latent is read -- a UNIFORM choice among
    eligible players, with a small, explicit, UNCALIBRATED 2x weight for
    `favored_team_id`'s eligible players when the calling resolver
    already signaled a real structural reason one side is favored (e.g.
    Phase 17B's own `DEFLECTED_RETAINED_OFFENSE`/`CLEAN_STRIP_LOOSE`-vs-
    `CLEAN_INTERCEPTION` distinction -- reused as a real signal, not
    invented here). Applies the winning player via the EXISTING
    `PossessionEngine.secure_loose_ball` (Phase 15, reused verbatim).
    Returns `"OFFENSE_RECOVERED"` or `"DEFENSE_RECOVERED"` (relative to
    the CURRENT offense at the moment this is called)."""
    zone = engine.state.ball_zone
    eligible = [pid for pid in world.all_ten() if world.player_zones.get(pid) == zone]
    if not eligible:
        eligible = list(world.all_ten())  # documented fallback -- never crashes, never leaves the ball unresolved

    weights = []
    for pid in eligible:
        w = 1.0
        if favored_team_id is not None and world.team_id_for(pid) == favored_team_id:
            w = 2.0
        weights.append(w)
    total = sum(weights)
    roll = rng.random() * total
    cumulative = 0.0
    winner = eligible[-1]
    for pid, w in zip(eligible, weights):
        cumulative += w
        if roll < cumulative:
            winner = pid
            break

    winner_team = world.team_id_for(winner)
    # A pass deflection/strip may deliberately set live team possession to
    # None while the ball is unresolved. `world.team_a_id` is the immutable
    # offense at this possession's start, so it is the only sound reference
    # for classifying the recovery as retained offense vs. turnover.
    current_offense = world.team_a_id
    other_team = world.team_b_id if winner_team == world.team_a_id else world.team_a_id
    engine.secure_loose_ball(winner, winner_team, other_team)
    world.player_zones[winner] = zone
    recovery = "OFFENSE_RECOVERED" if winner_team == current_offense else "DEFENSE_RECOVERED"
    # Reconciliation fix: `PossessionEngine.secure_loose_ball` itself logs NO event at all (confirmed by
    # direct source read) -- a generic loose-ball recovery would otherwise leave ZERO trace in the event
    # stream, undermining "events = accounting truth." This reuses the EXISTING `REACTION_CHECKPOINT` event
    # type (no new vocabulary, same convention Phase 21A/22A already established) with structured,
    # non-prose metadata -- `derive_stat_deltas_from_events` below reads this exact field to recognize a
    # defense-recovered loose ball as a turnover.
    engine._log(EventType.REACTION_CHECKPOINT, 0.0, primary=winner,
                meta={"checkpoint": "generic_loose_ball_recovered", "recovering_team_id": winner_team,
                      "recovery": recovery})
    return recovery


# ---------------------------------------------------------------------
# 9. Rebound handoff (Phase 19 reuse) -- shared by every miss/block/
# final-missed-FT path.
# ---------------------------------------------------------------------
def _dispatch_rebound(engine: PossessionEngine, world: PossessionWorld, rng: random.Random,
                       source: str, shot_family: str, steps: int,
                       offense_team_id: str, defense_team_id: str) -> Optional[PossessionTerminalResult]:
    """`offense_team_id`/`defense_team_id` are the team ids AS OF THE
    MOMENT THE SHOT/FT WAS ATTEMPTED -- captured explicitly by the
    caller BEFORE dispatching, never re-read from `engine.state` here.
    This matters because the very miss/final-missed-FT that produces
    this rebound opportunity ALREADY clears `engine.state.offense_team_id`
    to `None` (Phase 15/18C's own "genuinely unresolved until secured"
    convention, reused, not worked around) -- reading it again at this
    point would silently lose which team was on offense.

    A player MISSING their own side's estimate is EXCLUDED from
    `candidates` entirely -- `rebound_resolution.py`'s own
    `_candidate_log_weight` silently substitutes `0.5` for a `None`
    skill value, which this module does NOT trust for Phase 23A (a real,
    audited "missing != zero/0.5" gap): a candidate is only entered into
    competition when this project already has a real side-specific
    estimate for them. If that leaves zero eligible candidates,
    `resolve_rebound`'s own existing, real "no eligible candidate"
    behavior applies (a team rebound credited to the DEFENSE by
    default) -- a well-defined, already-tested fallback, not a crash and
    not a fabricated individual rebounder."""
    candidates = []
    for pid in world.all_ten():
        side = "OFFENSE" if world.team_id_for(pid) == offense_team_id else "DEFENSE"
        profile = world.profiles[pid]
        side_rate = profile.offensive_rebounding_shrunk_rate if side == "OFFENSE" else profile.defensive_rebounding_shrunk_rate
        if side_rate is None:
            continue  # missing != 0.5 -- excluded from competition rather than silently defaulted
        candidates.append(ReboundCandidate(
            player_id=pid, side=side, zone=world.player_zones.get(pid, engine.state.ball_zone),
            offensive_rebounding=side_rate if side == "OFFENSE" else None,
            defensive_rebounding=side_rate if side == "DEFENSE" else None,
        ))
    opportunity = ReboundOpportunity(source=source, shot_family=shot_family, rebound_zone=engine.state.ball_zone,
                                      candidates=candidates, advantage=engine.advantage)
    result = apply_rebound_to_engine(
        engine, opportunity, rng,
        new_offense_team_id=defense_team_id, new_defense_team_id=offense_team_id,
        original_offense_team_id=offense_team_id,
    )
    if result.outcome in (ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.TEAM_REBOUND_OFFENSE):
        world.stats.oreb += 1
        if result.rebounder_id is not None:
            world.player_zones[result.rebounder_id] = engine.state.ball_zone
        return None  # continue orchestration -- SECOND_CHANCE
    world.stats.dreb += 1
    return _terminal(PossessionTerminalReason.DEFENSIVE_REBOUND, engine, world, steps)


# ---------------------------------------------------------------------
# 10. Floor-foul handoff (Phase 21A detection -> Phase 21B administration).
# ---------------------------------------------------------------------
def _dispatch_floor_foul(engine: PossessionEngine, world: PossessionWorld, rng: random.Random,
                          steps: int, on_ball_outcome: str, offender_id: str, fouled_player_id: str) -> Optional[PossessionTerminalResult]:
    """`on_ball_outcome` is a Phase 21A `OnBallContactOutcome` string
    (`OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL`) already classified by
    `apply_on_ball_pressure_to_engine`, which has ALREADY applied the
    real possession consequence (`dead_ball_turnover`/`non_shooting_foul`)
    -- this function does NOT re-detect the foul, only administers it via
    Phase 21B's existing `administer_floor_foul`, with
    `possession_consequence_already_applied=True` (the exact seam Phase
    21B's own report documents for this exact caller)."""
    foul_event_id = f"{engine.state.possession_id}:foul:{len(engine.log.events)}"
    offender_team_id = world.team_id_for(offender_id)
    fouled_team_id = world.team_id_for(fouled_player_id)
    free_throw_rate = None
    if on_ball_outcome == DEFENSIVE_FLOOR_FOUL:
        # only fetched/required if the bonus actually applies -- administer_floor_foul itself raises
        # explicitly if it turns out to be needed and is missing (Sec. missing/invalid context).
        fouled_profile = world.profiles[fouled_player_id]
        free_throw_rate = fouled_profile.free_throw_shrunk_rate

    world.foul_state, result = administer_floor_foul(
        engine, world.foul_state, foul_event_id, offender_id=offender_id, fouled_player_id=fouled_player_id,
        foul_class=on_ball_outcome, offender_team_id=offender_team_id, fouled_team_id=fouled_team_id,
        rng=rng, possession_consequence_already_applied=True, free_throw_rate=free_throw_rate,
    )
    world.stats.add_personal_foul(offender_id)
    # Reconciliation fix: `administer_floor_foul` (Phase 21B) itself logs no event -- an OFFENSIVE_CHARGE's
    # personal-foul aspect would otherwise be INDISTINGUISHABLE in the event stream from any other
    # DEAD_BALL_TURNOVER (travel, generic OOB, etc.). A single, structured, non-prose REACTION_CHECKPOINT
    # (same existing event type, no vocabulary bloat) makes "a personal foul of exactly this class was
    # administered, on this player" machine-readable for BOTH foul classes, closing the personal-foul
    # event-derivability gap without touching floor_foul_administration.py or on_ball_pressure_resolution.py.
    engine._log(EventType.REACTION_CHECKPOINT, 0.0, primary=offender_id, secondary=fouled_player_id,
                meta={"checkpoint": "floor_foul_administered", "foul_class": on_ball_outcome,
                      "foul_event_id": foul_event_id})

    if on_ball_outcome == OFFENSIVE_CHARGE:
        world.stats.turnovers += 1
        return _terminal(PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER, engine, world, steps,
                          offense_team_id=fouled_team_id, defense_team_id=offender_team_id)

    # DEFENSIVE_FLOOR_FOUL
    if result.free_throw_sequence is not None:
        seq = result.free_throw_sequence
        world.stats.fta += seq.awarded_attempts
        world.stats.ftm += seq.makes
        world.stats.points += seq.makes
        if engine.state.ball_state == BallState.LOOSE:
            return _dispatch_rebound(engine, world, rng, ReboundSource.FINAL_MISSED_FT, "FREE_THROW", steps,
                                      offense_team_id=fouled_team_id, defense_team_id=offender_team_id)
        # a made final bonus FT -> dead ball, possession flips to the fouling team
        return _terminal(PossessionTerminalReason.FINAL_FT_MADE, engine, world, steps)

    # not in the bonus -- offense retains the ball (already applied); re-inbound the fouled player
    # at the same zone (the smallest honest V0 choice -- no inbound-playcalling is modeled).
    engine.inbound(fouled_player_id, engine.state.ball_zone, PossessionPhase.HALFCOURT)
    world.player_zones[fouled_player_id] = engine.state.ball_zone
    return None  # continue orchestration


# ---------------------------------------------------------------------
# 11. Drive dispatch.
# ---------------------------------------------------------------------
def _dispatch_drive(engine: PossessionEngine, world: PossessionWorld, intent: ActionIntent, config: PossessionConfig,
                     rng: random.Random, steps: int) -> Optional[PossessionTerminalResult]:
    driver_id = intent.actor_player_id
    driver_profile = world.profiles[driver_id]
    defender_id = _primary_defender(engine, driver_id)
    defender_profile = world.profiles.get(defender_id) if defender_id else None
    posture = engine.state.assignments[defender_id].posture if defender_id else DefensivePosture.SQUARE

    # Phase 21A ordering decision (drives occur during LIVE_DRIBBLE, exactly Phase 21A's own owned
    # precondition): checked FIRST, exactly once, per drive dispatch. `contact_established` defaults to
    # False (module docstring's PossessionConfig) -- no real per-drive contact-occurrence rate exists in
    # this project, so V0 does not fabricate one; this keeps ordinary drives on Phase 17A's own leverage
    # model, while still making the real 21A->21B floor-foul pipeline reachable for a caller that opts in
    # (`force_on_ball_contact_established=True`), satisfying "do not silently double-resolve the same
    # contact" -- the SAME contact is never seen by both this pre-check and drive_resolution.py, because a
    # collision outcome here (charge/D-foul/no-call) never falls through into `resolve_drive` at all.
    if defender_id is not None:
        pressure_ctx = OnBallPressureContext(
            ball_security=driver_profile.ball_security_error_rate,
            defensive_playmaking=defender_profile.defensive_playmaking_per36 if defender_profile else None,
            defender_posture=posture,
            # foul_discipline/foul_drawing are DISABLED this phase (see PlayerSimulationProfile's own
            # docstring) -- no validated conversion exists from these estimators' native scale to the
            # centered modifier value this resolver expects; None is each resolver's own documented
            # neutral/no-adjustment behavior, not a fabricated conversion.
            foul_discipline=None,
            foul_drawing=None,
            contact_established=config.force_on_ball_contact_established,
        )
        pressure_outcome = apply_on_ball_pressure_to_engine(engine, driver_id, defender_id, pressure_ctx, rng)
        world.log_trace(step=steps, action="ON_BALL_PRESSURE", outcome=pressure_outcome, driver=driver_id, defender=defender_id)

        if pressure_outcome == OnBallContactOutcome.OFFENSIVE_CHARGE:
            return _dispatch_floor_foul(engine, world, rng, steps, OFFENSIVE_CHARGE, driver_id, defender_id)
        if pressure_outcome == OnBallContactOutcome.DEFENSIVE_FLOOR_FOUL:
            return _dispatch_floor_foul(engine, world, rng, steps, DEFENSIVE_FLOOR_FOUL, defender_id, driver_id)
        if pressure_outcome == OnBallContactOutcome.FORCED_PICKUP:
            _charge_time(engine, config.drive_action_seconds)
            return None  # dribble is now DEAD_DRIBBLE -- selection re-runs, DRIVE no longer offered
        if pressure_outcome == OnBallContactOutcome.CLEAN_STRIP_LOOSE:
            _charge_time(engine, config.drive_action_seconds)
            world.loose_ball_favored_team_id = None  # genuinely unresolved -- no favored side
            return None  # top-of-loop LOOSE check handles it next iteration
        # CLEAN_CONTROL / DISRUPTED / NO_CALL_CONTACT -> fall through to the ordinary drive leverage roll

    drive_ctx = DriveResolutionContext(
        rim_access_creation=driver_profile.rim_access_creation_shrunk_rate,
        poa_containment=defender_profile.poa_containment_shrunk_rate if defender_profile else None,
        defender_posture=posture, advantage=engine.advantage,
        # both explicit opt-ins, left at their honest defaults -- physical adjustment and lost-ball
        # scaffolding stay DISABLED this phase (enable_physical_adjustment=False, enable_lost_ball=False).
    )
    outcome = resolve_drive(engine, driver_id, defender_id, drive_ctx, rng)
    _charge_time(engine, config.drive_action_seconds)
    world.player_zones[driver_id] = engine.state.ball_zone
    world.log_trace(step=steps, action="DRIVE", outcome=outcome, driver=driver_id, defender=defender_id,
                     zone=engine.state.ball_zone.value)
    return None  # a drive is never terminal by itself in V0 -- selection re-runs from the new structure


# ---------------------------------------------------------------------
# 12. Shot dispatch (perimeter/interior + Phase 18C shooting-foul integration).
# ---------------------------------------------------------------------
def _dispatch_shot(engine: PossessionEngine, world: PossessionWorld, intent: ActionIntent, config: PossessionConfig,
                    rng: random.Random, steps: int) -> Optional[PossessionTerminalResult]:
    shooter_id = intent.actor_player_id
    shooter_profile = world.profiles[shooter_id]
    zone = SpatialZone(intent.target_zone) if intent.target_zone else engine.state.ball_zone
    # captured BEFORE any resolver mutates engine.state -- a miss/final-missed-FT clears
    # engine.state.offense_team_id to None (Sec. `_dispatch_rebound`'s own docstring).
    offense_team_id, defense_team_id = engine.state.offense_team_id, engine.state.defense_team_id
    defender_id = _primary_defender(engine, shooter_id)
    defender_profile = world.profiles.get(defender_id) if defender_id else None
    posture = engine.state.assignments[defender_id].posture if defender_id else DefensivePosture.SQUARE
    action_seconds = config.catch_and_shoot_action_seconds if intent.action_type == ActionType.CATCH_AND_SHOOT else config.pull_up_action_seconds

    is_interior = zone in INTERIOR_ZONES
    # V0 simplification, explicitly documented (Sec. report): ALL perimeter-zone shots are dispatched as
    # THREE_POINT (this project's 8-zone topology does not distinguish a mid-range release point from a
    # beyond-the-arc one within the same coarse PERIMETER_ZONES set) -- `midrange`/`midrange_preference`
    # remain real, unused-by-V0-dispatch profile fields, not deleted, for a future finer-grained phase.
    shot_family_interior = InteriorShotFamily.RIM if zone == SpatialZone.RESTRICTED_RIM else InteriorShotFamily.FLOATER
    shot_family = shot_family_interior if is_interior else PerimeterShotFamily.THREE_POINT

    eligible_defenders = []
    if defender_id is not None:
        eligible_defenders.append(FoulEligibleDefender(
            defender_id=defender_id, is_primary=True, posture=posture,
            foul_discipline=None,  # DISABLED this phase -- see PlayerSimulationProfile's own docstring
        ))
    contact_ctx = ContactContext(shot_family=shot_family, shooter_foul_drawing=None,  # DISABLED this phase
                                  eligible_defenders=eligible_defenders, season=config.season)
    contact_result = resolve_contact_and_whistle(contact_ctx, rng)

    if is_interior:
        base_rate = shooter_profile.rim_finishing_shrunk_rate if shot_family == InteriorShotFamily.RIM else shooter_profile.floater_short_mid_shrunk_rate
        base_rate = _require(base_rate, f"{shooter_id}'s rim_finishing_shrunk_rate/floater_short_mid_shrunk_rate ({shot_family})")
        interior_ctx = InteriorShotContext(
            shot_family=shot_family, shooter_base_rate=base_rate,
            primary_defender=InteriorDefenderContext(
                defender_id=defender_id or shooter_id, zone=world.player_zones.get(defender_id, zone) if defender_id else zone,
                posture=posture, is_primary=True,
                rim_protection=defender_profile.rim_protection_suppression_rate if defender_profile else None,
                defensive_playmaking=defender_profile.defensive_playmaking_per36 if defender_profile else None,
            ),
            shot_clock_remaining=engine.state.shot_clock_remaining,
        )
        make_probability = unblocked_make_probability(interior_ctx)
    else:
        base_rate = _require(shooter_profile.three_point_shrunk_rate, f"{shooter_id}'s three_point_shrunk_rate")
        release_mode = ReleaseMode.CATCH_AND_SHOOT if intent.action_type == ActionType.CATCH_AND_SHOOT else ReleaseMode.PULL_UP
        perimeter_ctx = ShotResolutionContext(shot_family=ShotFamily.THREE_POINT, shooter_base_rate=base_rate,
                                               contest_bucket=ContestBucket.OPEN, release_mode=release_mode,
                                               defender_posture=posture, shot_clock_remaining=engine.state.shot_clock_remaining)
        make_probability = shot_make_probability(perimeter_ctx)

    if contact_result.whistled:
        return _dispatch_shooting_foul(engine, world, config, rng, steps, shooter_id, contact_result.fouler_id,
                                        shot_family, make_probability, zone, action_seconds,
                                        offense_team_id, defense_team_id)

    if is_interior:
        result = apply_interior_shot_to_engine(engine, shooter_id, interior_ctx, rng, zone=zone.value)
        _charge_time(engine, action_seconds)
        world.stats.fga += 1
        world.log_trace(step=steps, action=intent.action_type.value, shot_family=shot_family, outcome=result.outcome,
                         shooter=shooter_id, zone=zone.value)
        if result.outcome == InteriorShotOutcome.MADE:
            world.stats.fgm += 1
            world.stats.points += result.points
            return _terminal(PossessionTerminalReason.MADE_FG, engine, world, steps)
        if result.blocker_id is not None and result.outcome in (InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE, InteriorShotOutcome.BLOCKED_SECURED_DEFENSE):
            world.stats.blocks += 1
        return _dispatch_rebound(engine, world, rng, ReboundSource.MISSED_FG if result.outcome == InteriorShotOutcome.MISSED_UNBLOCKED
                                  else ReboundSource.UNRESOLVED_BLOCK, shot_family, steps,
                                  offense_team_id=offense_team_id, defense_team_id=defense_team_id)
    else:
        result = apply_shot_resolution_to_engine(engine, shooter_id, perimeter_ctx, rng, zone=zone.value)
        _charge_time(engine, action_seconds)
        world.stats.fga += 1
        world.stats.fg3a += 1
        world.log_trace(step=steps, action=intent.action_type.value, shot_family=shot_family, outcome=result.outcome,
                         shooter=shooter_id, zone=zone.value)
        if result.outcome == ShotOutcome.MADE:
            world.stats.fgm += 1
            world.stats.fg3m += 1
            world.stats.points += result.points
            return _terminal(PossessionTerminalReason.MADE_FG, engine, world, steps)
        return _dispatch_rebound(engine, world, rng, ReboundSource.MISSED_FG, shot_family, steps,
                                  offense_team_id=offense_team_id, defense_team_id=defense_team_id)


def _dispatch_shooting_foul(engine: PossessionEngine, world: PossessionWorld, config: PossessionConfig,
                             rng: random.Random, steps: int, shooter_id: str, fouler_id: Optional[str],
                             shot_family: str, make_probability: float, zone: SpatialZone,
                             action_seconds: float, offense_team_id: str, defense_team_id: str) -> Optional[PossessionTerminalResult]:
    """Phase 18C's OWN shooting-foul machinery, reused verbatim -- Phase
    21A never administers a shooting foul, and Phase 21B never detects
    one (it has no shot-family/contact/whistle concept at all, confirmed
    by direct inspection of `floor_foul_administration.py`'s imports).
    Personal/team foul bookkeeping is recorded on the SAME shared
    `world.foul_state` a floor foul would use (one real running count per
    player/team, regardless of foul TYPE), but via a direct
    `PersonalFoulTracker`/team-dict increment here -- NOT via
    `administer_floor_foul` (whose `foul_class` vocabulary is
    `OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL` only and does not, and
    should not, grow a shooting-foul class just to reuse one increment)."""
    engine.begin_shot(zone, dt=0.0)
    made, points, awarded_fts = resolve_shooting_foul_shot(shooter_id, shot_family, make_probability, rng)
    engine.shooting_foul(shooter_id, fouler_id, dt=0.0)
    _charge_time(engine, action_seconds)

    if fouler_id is not None:
        world.foul_state = replace(world.foul_state, personal_fouls=world.foul_state.personal_fouls.increment(fouler_id))
        fouler_team = world.team_id_for(fouler_id)
        world.foul_state = world.foul_state._with_team_foul_incremented(fouler_team)
        world.stats.add_personal_foul(fouler_id)

    if made:
        world.stats.fga += 1
        world.stats.fgm += 1
        if shot_family == PerimeterShotFamily.THREE_POINT:
            world.stats.fg3a += 1
            world.stats.fg3m += 1
        world.stats.points += points
    # a MISSED shooting foul contributes ZERO FGA/FGM -- Phase 18C's own real accounting rule, reused as-is.

    ft_rate = _require_ft_rate(world.profiles[shooter_id], config)
    sequence = FreeThrowSequence(shooter_id=shooter_id, awarded_attempts=awarded_fts,
                                  source_foul_type="AND_ONE" if made else "MISSED_SHOOTING_FOUL")
    while not sequence.is_complete:
        sequence, ft_made = apply_free_throw_attempt_to_engine(engine, sequence, ft_rate, rng)
        world.stats.fta += 1
        if ft_made:
            world.stats.ftm += 1
            world.stats.points += 1

    world.log_trace(step=steps, action="SHOOTING_FOUL", shot_family=shot_family, made=made, fouler=fouler_id,
                     shooter=shooter_id, awarded_fts=awarded_fts, ft_makes=sequence.makes)

    if made:
        return _terminal(PossessionTerminalReason.MADE_FG, engine, world, steps)
    if engine.state.ball_state == BallState.LOOSE:
        return _dispatch_rebound(engine, world, rng, ReboundSource.FINAL_MISSED_FT, shot_family, steps,
                                  offense_team_id=offense_team_id, defense_team_id=defense_team_id)
    return _terminal(PossessionTerminalReason.FINAL_FT_MADE, engine, world, steps)


# ---------------------------------------------------------------------
# 13. Pass dispatch.
# ---------------------------------------------------------------------
def _dispatch_pass(engine: PossessionEngine, world: PossessionWorld, intent: ActionIntent,
                    rng: random.Random, steps: int) -> Optional[PossessionTerminalResult]:
    passer_id = intent.actor_player_id
    receiver_id = intent.target_player_id
    passer_profile = world.profiles[passer_id]
    origin_zone = engine.state.ball_zone
    destination_zone = SpatialZone(intent.target_zone) if intent.target_zone else origin_zone

    candidates = []
    for defender_id, assignment in engine.state.assignments.items():
        defender_profile = world.profiles.get(defender_id)
        candidates.append(DefenderCandidate(
            defender_id=defender_id, zone=world.player_zones.get(defender_id, origin_zone),
            posture=assignment.posture,
            defensive_playmaking=defender_profile.defensive_playmaking_per36 if defender_profile else None,
            is_receiver_defender=(assignment.assigned_to_player_id == receiver_id),
            is_passer_defender=(assignment.assigned_to_player_id == passer_id),
        ))
    pass_ctx = PassResolutionContext(passing_accuracy=passer_profile.passing_accuracy_ast_pct, eligible_defenders=candidates,
                                      already_filtered=False, advantage=engine.advantage)
    outcome = resolve_pass(engine, intent, pass_ctx, rng)
    world.log_trace(step=steps, action=intent.action_type.value, outcome=outcome, passer=passer_id, receiver=receiver_id,
                     zone=destination_zone.value)

    if outcome == "SHOT_CLOCK_VIOLATION_ON_ARRIVAL":
        # engine.shot_clock_violation() was already called INSIDE resolve_pass -- not called again here.
        return _terminal(PossessionTerminalReason.SHOT_CLOCK_VIOLATION, engine, world, steps)

    if outcome in (PassOutcome.COMPLETED_CLEAN, PassOutcome.COMPLETED_ADJUSTED):
        world.player_zones[receiver_id] = destination_zone
        world.just_caught_pass_player_id = receiver_id
        return None

    if outcome == PassOutcome.CLEAN_INTERCEPTION:
        last_event = engine.log.events[-1]
        disrupting = last_event.metadata.get("disrupting_defender_id")
        if disrupting is not None:
            world.stats.steals += 1
        new_offense = world.team_id_for(disrupting) if disrupting else engine.state.offense_team_id
        new_defense = world.team_b_id if new_offense == world.team_a_id else world.team_a_id
        world.stats.turnovers += 1
        return _terminal(PossessionTerminalReason.TURNOVER, engine, world, steps,
                          offense_team_id=new_offense, defense_team_id=new_defense)

    if outcome == PassOutcome.BAD_PASS_OUT_OF_BOUNDS:
        world.stats.turnovers += 1
        return _terminal(PossessionTerminalReason.TURNOVER, engine, world, steps,
                          offense_team_id=engine.state.offense_team_id, defense_team_id=engine.state.defense_team_id)

    if outcome == PassOutcome.BAD_PASS_TO_DEFENDER:
        if engine.state.ball_state == BallState.LOOSE:
            recovery = resolve_generic_loose_ball(engine, world, rng, favored_team_id=None)
            return _loose_ball_continuation_or_terminal(engine, world, steps, recovery)
        world.stats.turnovers += 1
        return _terminal(PossessionTerminalReason.TURNOVER, engine, world, steps,
                          offense_team_id=engine.state.offense_team_id, defense_team_id=engine.state.defense_team_id)

    if outcome == PassOutcome.DEFLECTED_LOOSE_BALL:
        world.loose_ball_favored_team_id = None
        return None

    if outcome == PassOutcome.DEFLECTED_RETAINED_OFFENSE:
        world.loose_ball_favored_team_id = engine.state.offense_team_id
        return None

    raise ValueError(f"unhandled pass outcome reached the orchestrator: {outcome!r}")


def _loose_ball_continuation_or_terminal(engine: PossessionEngine, world: PossessionWorld, steps: int,
                                          recovery: str) -> Optional[PossessionTerminalResult]:
    if recovery == "DEFENSE_RECOVERED":
        world.stats.turnovers += 1
        return _terminal(PossessionTerminalReason.TURNOVER, engine, world, steps)
    return None


# ---------------------------------------------------------------------
# 14. Dispatcher -- the one authoritative seam.
# ---------------------------------------------------------------------
def dispatch_action(engine: PossessionEngine, world: PossessionWorld, intent: ActionIntent,
                     config: PossessionConfig, rng: random.Random, steps: int) -> Optional[PossessionTerminalResult]:
    if intent.action_type not in SUPPORTED_ACTION_TYPES:
        raise UnsupportedActionError(f"dispatch_action cannot execute {intent.action_type} -- "
                                      f"it is capability-gated (CAPABILITY_GATED_ACTION_TYPES) or unknown")
    if intent.action_type == ActionType.DRIVE:
        return _dispatch_drive(engine, world, intent, config, rng, steps)
    if intent.action_type in (ActionType.PULL_UP, ActionType.CATCH_AND_SHOOT):
        return _dispatch_shot(engine, world, intent, config, rng, steps)
    if intent.action_type in PASS_ACTIONS:
        return _dispatch_pass(engine, world, intent, rng, steps)
    raise UnsupportedActionError(f"dispatch_action has no route for {intent.action_type} despite it being "
                                  f"in SUPPORTED_ACTION_TYPES -- a real implementation gap, not a gate")


# ---------------------------------------------------------------------
# 15. The top-level autonomous possession loop.
# ---------------------------------------------------------------------
def simulate_possession(
    offense_team_id: str, defense_team_id: str,
    offensive_five: Tuple[str, ...], defensive_five: Tuple[str, ...],
    profiles: Dict[str, PlayerSimulationProfile],
    inbound_receiver_id: str,
    config: Optional[PossessionConfig] = None,
    matchup_pairs: Optional[List[Tuple[str, str]]] = None,
    initial_player_zones: Optional[Dict[str, SpatialZone]] = None,
    foul_state: Optional[FoulAdministrationState] = None,
    rng_seed: Optional[int] = None,
    possession_id: str = "p1",
) -> PossessionTerminalResult:
    """INITIALIZE -> BUILD WORLD -> BUILD MATCHUPS -> INBOUND -> [DERIVE
    STRUCTURAL CONTEXT -> GENERATE OPPORTUNITIES -> PERCEIVE -> SELECT ->
    DISPATCH -> RESOLVE -> UPDATE WORLD/ENGINE -> CONSUME CLOCK -> CONTINUE
    OR TERMINATE] -- the orchestrator, not the caller, owns this loop. No
    test caller manually constructs a `StructuralContext`,
    `PassResolutionContext`, `DriveResolutionContext`, `ContactContext`,
    `InteriorShotContext`, `ShotResolutionContext`, or `ReboundOpportunity`
    -- this function does, every step, from `profiles`/`world`/
    `engine.state` alone."""
    config = config or PossessionConfig()
    rng = random.Random(rng_seed)
    validate_lineups(offensive_five, defensive_five)

    engine = PossessionEngine(possession_id=possession_id, offense_team_id=offense_team_id,
                               defense_team_id=defense_team_id, era_rules=config.era_rules,
                               season=None if config.era_rules is not None else config.season,
                               rng_seed=rng.getrandbits(64))
    if config.initial_game_clock_seconds is not None:
        if config.initial_game_clock_seconds < 0.0:
            raise ValueError("initial_game_clock_seconds cannot be negative")
        if config.initial_game_clock_seconds > engine.era_rules.period_length_seconds:
            raise ValueError("initial_game_clock_seconds cannot exceed the configured period length")
        engine.state = replace(engine.state, game_clock_remaining=config.initial_game_clock_seconds)
    if config.initial_phase not in (PossessionPhase.HALFCOURT, PossessionPhase.TRANSITION):
        raise ValueError("initial_phase must be HALFCOURT or TRANSITION for a live possession start")
    apply_matchup_assignments(engine, offensive_five, defensive_five, matchup_pairs)

    world = PossessionWorld(team_a_id=offense_team_id, team_b_id=defense_team_id,
                             team_a_five=tuple(offensive_five), team_b_five=tuple(defensive_five),
                             profiles=dict(profiles), foul_state=foul_state or FoulAdministrationState())

    engine.inbound(inbound_receiver_id, config.initial_ball_zone, config.initial_phase)
    zones = initial_player_zones if initial_player_zones is not None else \
        default_v0_zone_placement(offensive_five, inbound_receiver_id, config.initial_ball_zone)
    world.player_zones = dict(zones)
    _mirror_defender_zones(world.player_zones, engine.state.assignments)
    world.just_caught_pass_player_id = inbound_receiver_id

    for step in range(config.max_steps_per_possession):
        if engine.state.shot_clock_remaining is not None and engine.state.shot_clock_remaining <= 0.0 \
                and engine.state.ball_state != BallState.LOOSE:
            engine.shot_clock_violation()
            return _terminal(PossessionTerminalReason.SHOT_CLOCK_VIOLATION, engine, world, step)
        if engine.state.game_clock_remaining is not None and engine.state.game_clock_remaining <= 0.0:
            engine.period_expiration()
            return _terminal(PossessionTerminalReason.PERIOD_END, engine, world, step)

        if engine.state.ball_state == BallState.LOOSE:
            recovery = resolve_generic_loose_ball(engine, world, rng, favored_team_id=world.loose_ball_favored_team_id)
            world.loose_ball_favored_team_id = None
            _charge_time(engine, config.loose_ball_action_seconds)
            world.log_trace(step=step, action="LOOSE_BALL_RECOVERY", recovery=recovery)
            terminal = _loose_ball_continuation_or_terminal(engine, world, step, recovery)
            if terminal is not None:
                return terminal
            continue

        ctx = build_structural_context(engine, world)
        carrier = engine.state.ball_carrier
        world.just_caught_pass_player_id = None  # consumed exactly once, regardless of whether it matched this carrier

        opportunities = generate_opportunities(engine.state, ctx, advantage=engine.advantage)
        carrier_profile = world.profiles[carrier]
        # vision modifier DISABLED this phase (see PlayerSimulationProfile's own docstring) --
        # estimate_playmaking_vision()'s native shrunk rate has no validated conversion to the scale
        # perceive() expects; None is perceive()'s own documented neutral (league-average) behavior.
        perceived = perceive(opportunities, None, rng)
        perceived = [p for p in perceived if p.opportunity.action_type in SUPPORTED_ACTION_TYPES]

        clock_ctx = ClockContext(shot_clock_remaining=engine.state.shot_clock_remaining)
        policy = SelectionPolicy(rng)
        intent = policy.select(perceived, _role_context(carrier_profile), _tendency_context(carrier_profile),
                                clock_ctx, engine.state.possession_id)
        if intent is None:
            # genuinely no feasible supported action (e.g. clock too low for anything but an
            # already-infeasible RESET_PASS) -- the real, structural analog of a shot-clock violation.
            engine.shot_clock_violation()
            return _terminal(PossessionTerminalReason.SHOT_CLOCK_VIOLATION, engine, world, step)

        terminal = dispatch_action(engine, world, intent, config, rng, step)
        if terminal is not None:
            return terminal

    raise PossessionSimulationFault(
        f"possession exceeded max_steps_per_possession={config.max_steps_per_possession} without a terminal result",
        steps=config.max_steps_per_possession, state=engine.state, events=engine.log.events,
    )
