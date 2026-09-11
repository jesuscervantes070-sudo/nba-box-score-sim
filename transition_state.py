"""
Phase 20A -- Possession Change, Floor Balance & Transition State
Generation.

WHEN POSSESSION CHANGES -> WHAT SPATIAL/STRUCTURAL INFORMATION
SURVIVES -> WHO HAS THE BALL -> WHO IS AHEAD/BEHIND/COMPROMISED ->
WHETHER THE NEW OFFENSE BEGINS IN TRANSITION-LIKE STRUCTURAL ASYMMETRY.
Does NOT execute the transition attack (no outlet pass, no advance
dribble, no transition shot selection/resolution -- Phase 20B).

============================ CENTRAL DOCTRINE ============================
OLD OFFENSIVE ADVANTAGE != NEW TRANSITION ADVANTAGE. Every function in
this module that produces a new `TransitionState` either sets
`advantage=None` or derives a BRAND NEW `AdvantageModel` instance from
the CURRENT geometry it was given -- none of them ever read or copy
`engine.advantage` from before the possession change. Verified directly
by test: two possession changes with identical physical floor
geometry but different OLD `AdvantageState` values produce byte-for-byte
identical new `TransitionState` output.
"""
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from possession_advantage import AdvantageModel
from possession_engine import PossessionEngine
from possession_events import EventType
from possession_state import (
    BallState, DefensivePosture, PossessionPhase, SpatialZone, _assert_player_id,
)

# ---------------------------------------------------------------------
# Possession-change source taxonomy + live/dead classification --
# audited directly against this repo's existing possession-change
# pathways (Phases 15/17B/18B/18C/19). No source is assumed
# transition-capable by default; each is classified individually.
# ---------------------------------------------------------------------


class PossessionChangeSource:
    DEFENSIVE_REBOUND = "DEFENSIVE_REBOUND"                    # Phase 19 secure_defensive_rebound_from_loose
    LIVE_STEAL = "LIVE_STEAL"                                  # Phase 17B CLEAN_INTERCEPTION
    LIVE_BAD_PASS_INTERCEPTION = "LIVE_BAD_PASS_INTERCEPTION"  # Phase 17B BAD_PASS_TO_DEFENDER (a live-ball turnover to a defender)
    LOOSE_BALL_RECOVERY = "LOOSE_BALL_RECOVERY"                # Phase 15/17B/19 -- a scramble that resolves to defensive control
    DEAD_BALL_TURNOVER = "DEAD_BALL_TURNOVER"                  # Phase 15 dead_ball_turnover / Phase 17B BAD_PASS_OUT_OF_BOUNDS
    MADE_BASKET_INBOUND = "MADE_BASKET_INBOUND"                # Phase 18A/18B resolve_shot_made
    BLOCK_RECOVERY_DEFENSE = "BLOCK_RECOVERY_DEFENSE"          # Phase 19 rebound of an UNRESOLVED_BLOCK source
    PERIOD_START = "PERIOD_START"                              # start of a new period -- no residue of any kind


LIVE_TRANSITION_CAPABLE = "LIVE_TRANSITION_CAPABLE"
DEAD_BALL_INBOUND = "DEAD_BALL_INBOUND"
CONTEXT_DEPENDENT = "CONTEXT_DEPENDENT"

# Real, audited classification -- see report Sec. 4/5 for the reasoning
# behind each. LIVE sources may preserve real spatial imbalance (the
# defense had no time to organize); DEAD_BALL sources generally permit
# defensive organization and begin structurally neutral; CONTEXT_DEPENDENT
# sources (only block recovery) may involve real, unusual, but NOT
# artificially-bonused geometry (a blocker can be airborne/out of
# position) -- handled the same as LIVE for state-derivation purposes,
# but explicitly flagged as a distinct real case, not silently folded
# into an ordinary steal/DREB.
SOURCE_CLASSIFICATION = {
    PossessionChangeSource.DEFENSIVE_REBOUND: LIVE_TRANSITION_CAPABLE,
    PossessionChangeSource.LIVE_STEAL: LIVE_TRANSITION_CAPABLE,
    PossessionChangeSource.LIVE_BAD_PASS_INTERCEPTION: LIVE_TRANSITION_CAPABLE,
    PossessionChangeSource.LOOSE_BALL_RECOVERY: LIVE_TRANSITION_CAPABLE,
    PossessionChangeSource.DEAD_BALL_TURNOVER: DEAD_BALL_INBOUND,
    PossessionChangeSource.MADE_BASKET_INBOUND: DEAD_BALL_INBOUND,
    PossessionChangeSource.BLOCK_RECOVERY_DEFENSE: CONTEXT_DEPENDENT,
    PossessionChangeSource.PERIOD_START: DEAD_BALL_INBOUND,
}


class TransitionRestartType:
    """Canonical possession-boundary restart vocabulary.

    These are structural outcomes, not calibrated transition probabilities.
    ``CONTROLLED_ADVANCE`` is intentionally represented for the future split
    between a live change and a genuine fast break, but no V1 source routes to
    it until trustworthy source-conditioned evidence exists.
    """
    DEAD_BALL_INBOUND = "DEAD_BALL_INBOUND"
    LIVE_TRANSITION = "LIVE_TRANSITION"
    CONTROLLED_ADVANCE = "CONTROLLED_ADVANCE"


def decide_restart_type(source: str, source_taxonomy: str) -> str:
    """Return today's deterministic restart for one concrete source.

    ``source_taxonomy`` must be the canonical Phase 20 classification for
    ``source``.  Requiring both makes the source -> taxonomy -> restart seam
    explicit while preventing a caller from silently overriding taxonomy.

    V1 deliberately preserves the pre-existing all-live behavior for every
    ``LIVE_TRANSITION_CAPABLE`` source.  ``CONTEXT_DEPENDENT`` likewise maps
    to live transition to preserve the current blocked-shot recovery behavior,
    which orchestration presently observes under the defensive-rebound label.
    Neither branch is presented as an empirical probability.  This function
    consumes no RNG, clock, geometry, pace target, or future state.
    """
    canonical_taxonomy = classify_source(source)
    if source_taxonomy != canonical_taxonomy:
        raise ValueError(
            f"source taxonomy mismatch for {source!r}: "
            f"expected {canonical_taxonomy!r}, got {source_taxonomy!r}"
        )
    if source_taxonomy == DEAD_BALL_INBOUND:
        return TransitionRestartType.DEAD_BALL_INBOUND
    if source_taxonomy in (LIVE_TRANSITION_CAPABLE, CONTEXT_DEPENDENT):
        return TransitionRestartType.LIVE_TRANSITION
    raise ValueError(f"unsupported possession-change taxonomy {source_taxonomy!r}")


# =========================================================================
# "Calibrate source-conditioned transition routing" (Phase 20 correction).
# =========================================================================
#
# ============================ EMPIRICAL SOURCE (verified directly, not guessed) ============================
# `transition_rate_ingestion.py` -- real nba_api `playbyplayv3` extraction, 2025-26 regular
# season, 112-game stratified sample (14,819 possession-change events; see that module's own
# docstring for the exact, audited event-pairing rules). For each LIVE_TRANSITION_CAPABLE source,
# `live_transition_probability` below is that source's own measured SHARE of real possessions
# whose first offensive action (shot/turnover/foul) occurred within 8 seconds of the
# rebound/steal/interception -- the SAME "<8 second" framing this project's own prior Phase 20
# architecture audit already used (not a freshly-invented cutoff this phase):
#   DEFENSIVE_REBOUND:          n=2,709   <8s share = 0.512   (mean 9.60s, median 7.00s)
#   LIVE_STEAL:                 n=738     <8s share = 0.623   (mean 9.51s, median 6.00s)
#   LIVE_BAD_PASS_INTERCEPTION: n=1,087   <8s share = 0.611   (mean 9.65s, median 6.00s)
# DEAD_BALL_INBOUND sources were ALSO measured and confirm the existing deterministic behavior
# rather than overriding it: MADE_BASKET_INBOUND (n=9,206) <8s share = 0.091, mean 17.38s;
# DEAD_BALL_TURNOVER (n=1,079) <8s share = 0.087, mean 15.79s -- both overwhelmingly settled,
# so they are NOT given a `TransitionSourceProfile` below and remain unconditionally
# `DEAD_BALL_INBOUND` (zero RNG consumed), matching the task's own expectation.
#
# ============================ LOOSE_BALL_RECOVERY -- NOT COVERED (missing != zero) ============================
# This extraction does NOT resolve `LOOSE_BALL_RECOVERY` timing (see
# `transition_rate_ingestion.py`'s own "COVERAGE NOTE" -- who recovers a live loose ball is a
# separate event this extraction does not yet track). `LOOSE_BALL_RECOVERY` therefore has NO
# profile below and keeps its pre-existing deterministic `LIVE_TRANSITION` behavior, UNCHANGED --
# never guessed from a similar-looking source's own rate.
#
# ============================ WHAT "CONTROLLED_ADVANCE" MEANS HERE ============================
# For a profiled source, `1 - live_transition_probability` is NOT "settled/dead-ball" (the ball
# never went dead) -- it is `TransitionRestartType.CONTROLLED_ADVANCE`: a live change of
# possession that must still be advanced/organized, distinct from both a genuine fast break and a
# dead-ball inbound (see `possession_orchestrator.PossessionStage.CONTROLLED_ADVANCE_ENTRY`,
# activated by this same phase).
@dataclass(frozen=True)
class TransitionSourceProfile:
    """`live_transition_probability` + `controlled_advance_probability` sum to exactly 1.0 for
    every profiled source -- there is no third "settled" option for a LIVE source (see module
    comment above: the ball never went dead, so `DEAD_BALL_INBOUND` is never a valid outcome
    here)."""
    live_transition_probability: float
    controlled_advance_probability: float

    def __post_init__(self) -> None:
        total = self.live_transition_probability + self.controlled_advance_probability
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"TransitionSourceProfile probabilities must sum to 1.0, got {total}")


SOURCE_TRANSITION_PROFILES: Dict[str, TransitionSourceProfile] = {
    PossessionChangeSource.DEFENSIVE_REBOUND: TransitionSourceProfile(0.512, 0.488),
    PossessionChangeSource.LIVE_STEAL: TransitionSourceProfile(0.623, 0.377),
    PossessionChangeSource.LIVE_BAD_PASS_INTERCEPTION: TransitionSourceProfile(0.611, 0.389),
    # PossessionChangeSource.LOOSE_BALL_RECOVERY: deliberately absent -- see module comment above.
}


def decide_restart_type_stochastic(source: str, source_taxonomy: str, rng: random.Random) -> str:
    """Real-evidence-driven replacement for `decide_restart_type` above, used by production
    (`detailed_game_orchestrator.next_restart_context`). `decide_restart_type` itself is left
    completely UNCHANGED (still the deterministic Phase 20 baseline, still independently
    tested/importable) -- this is a separate, additive function, not a rewrite in place.

    Consumes EXACTLY ONE `rng.random()` draw for a profiled LIVE_TRANSITION_CAPABLE source, and
    ZERO draws for every other case (DEAD_BALL_INBOUND, CONTEXT_DEPENDENT, or an unprofiled live
    source like LOOSE_BALL_RECOVERY) -- missing evidence must never silently cost an RNG draw
    nor silently default to one particular outcome."""
    canonical_taxonomy = classify_source(source)
    if source_taxonomy != canonical_taxonomy:
        raise ValueError(
            f"source taxonomy mismatch for {source!r}: "
            f"expected {canonical_taxonomy!r}, got {source_taxonomy!r}"
        )
    if source_taxonomy == DEAD_BALL_INBOUND:
        return TransitionRestartType.DEAD_BALL_INBOUND
    if source_taxonomy == CONTEXT_DEPENDENT:
        return TransitionRestartType.LIVE_TRANSITION  # unchanged -- BLOCK_RECOVERY_DEFENSE not yet profiled
    if source_taxonomy != LIVE_TRANSITION_CAPABLE:
        raise ValueError(f"unsupported possession-change taxonomy {source_taxonomy!r}")

    profile = SOURCE_TRANSITION_PROFILES.get(source)
    if profile is None:
        return TransitionRestartType.LIVE_TRANSITION  # unprofiled live source (e.g. LOOSE_BALL_RECOVERY) -- unchanged, zero RNG
    return (TransitionRestartType.LIVE_TRANSITION if rng.random() < profile.live_transition_probability
            else TransitionRestartType.CONTROLLED_ADVANCE)

# A real, coarse "distance from the defended rim" ranking over the
# existing coarse-zone topology -- reused, not replaced. Used ONLY to derive
# the relational ahead/behind-ball tags below; no continuous coordinate
# is introduced.
_RIM_DISTANCE_RANK = {
    SpatialZone.RESTRICTED_RIM: 0,
    SpatialZone.PAINT: 1,
    SpatialZone.MIDRANGE: 2,
    SpatialZone.LEFT_CORNER: 2, SpatialZone.RIGHT_CORNER: 2,
    SpatialZone.LEFT_WING: 2, SpatialZone.RIGHT_WING: 2,
    SpatialZone.TOP_OF_KEY: 3,
    SpatialZone.BACKCOURT: 4,
}

AHEAD_OF_BALL = "AHEAD_OF_BALL"    # closer to the (new) defended rim than the ball -- already retreating/set
BEHIND_BALL = "BEHIND_BALL"        # farther from the defended rim than the ball -- caught upcourt (e.g. a crashing rebounder)
NEAR_BALL = "NEAR_BALL"            # same coarse rank as the ball

# IMPORTANT CALLER CONVENTION (a real, non-obvious point -- get this
# wrong and every relational tag inverts): zone labels in this module,
# same as everywhere else in this project, are relative to the NEW
# OFFENSE's OWN attacking rim, not to "wherever the ball physically
# changed hands." For a defensive rebound secured at the SHOOTING
# team's basket, the new offense's own `ball_zone` should normally be
# supplied as `BACKCOURT` (far from THEIR attacking rim, which is the
# other end) -- NOT `RESTRICTED_RIM` (that would describe the ball as
# already being at the new offense's attacking basket, which is
# physically wrong immediately after a rebound at the other end).

# ---------------------------------------------------------------------
# COURT-FLIP / ZONE-REORIENTATION TRANSFORM (Task: "Court-Flip / Zone-
# Relabeling Transform"). Reinterprets a zone that was tracked RELATIVE
# TO THE OLD OFFENSE'S OWN ATTACKING RIM (e.g. `world.player_zones` as
# tracked by `possession_orchestrator.py` for the possession that JUST
# ENDED) as the equivalent zone RELATIVE TO THE NEW OFFENSE'S OWN
# ATTACKING RIM (this module's own established convention, see the
# caller-convention comment immediately above).
#
# ============================ WHY THIS IS NOT A SIMPLE BIJECTION (source-audited, not assumed) ============================
# This project's 9-zone topology (`SpatialZone`) is DELIBERATELY
# ASYMMETRIC, not two mirrored half-courts: 8 zones
# (RESTRICTED_RIM/PAINT/MIDRANGE/LEFT_WING/RIGHT_WING/LEFT_CORNER/
# RIGHT_CORNER/TOP_OF_KEY) describe VARYING DEPTH WITHIN the currently-
# attacking team's OWN frontcourt, and exactly ONE zone (`BACKCOURT`)
# is the sole, single-resolution catch-all for "not yet in my
# frontcourt at all" (confirmed by direct source read: `INTERIOR_ZONES`
# `|` `PERIMETER_ZONES` in `action_opportunity.py` together cover
# exactly those same 8 zones, and `BACKCOURT` is a member of neither;
# `default_v0_zone_placement`'s own `_V0_PERIMETER_CYCLE` never assigns
# it to any player either -- confirmed no current gameplay path ever
# places a real player/ball at `BACKCOURT` today). A physical spot deep
# in the OLD offense's frontcourt is, for the NEW offense, deep in
# THEIR OWN defensive end -- but the topology has no finer resolution
# there than the single `BACKCOURT` label (there is no "how deep in my
# own end" distinction to preserve, because the model never built one
# for that side of the floor). The FORWARD direction (any of the 8
# reachable zones -> the new offense's frame) is therefore well-defined
# and UNAMBIGUOUS: it is always `BACKCOURT` -- this matches this
# module's OWN pre-existing documented example immediately above
# ("the new offense's own ball_zone should normally be supplied as
# BACKCOURT... NOT RESTRICTED_RIM").
#
# The REVERSE direction (`BACKCOURT` -> a specific new-offense-relative
# frontcourt zone) is GENUINELY AMBIGUOUS and is deliberately NOT
# invented: "the old offense's own backcourt" carries no information
# about where in the new offense's frontcourt that physical spot falls.
# Per this task's own instruction to report rather than force a
# fabricated answer here, `flip(BACKCOURT)` returns `BACKCOURT` itself
# -- a conservative "no re-derivable information, stay at maximum
# uncertainty" fixed point, never a guessed frontcourt zone. This means
# `flip` is a many-to-one PROJECTION, not a true involution:
# `flip(flip(zone)) == zone` holds ONLY for `zone == BACKCOURT`
# (trivially, since it is a fixed point) -- it does NOT hold for the
# other 8 zones, because they are deliberately, honestly collapsed to
# `BACKCOURT` and cannot be recovered. `flip(flip(zone)) == flip(zone)`
# (idempotence) DOES hold for every zone -- see the focused tests.
#
# ============================ LEFT/RIGHT AXIS (deliberately NOT transformed) ============================
# Whether "left"/"right" zone labels are camera/scorer's-table-relative
# (fixed) or attack-direction-relative (would swap when the attacking
# team's own facing direction reverses) is NOT resolved anywhere else
# in this codebase -- no existing code exercises a left/right swap
# today to establish a precedent either way, and `ball_side()`'s own
# docstring only documents it as deriving STRONG/WEAK side relative to
# THAT SAME possession's own ball zone, never as a claim about a fixed
# external camera frame. This transform deliberately does NOT guess:
# every rank-2 zone (MIDRANGE/LEFT_WING/RIGHT_WING/LEFT_CORNER/
# RIGHT_CORNER) collapses to `BACKCOURT` under the same forward rule as
# every other non-BACKCOURT zone (see above) -- so this ambiguity is
# CURRENTLY INERT for the transform's own output (there is no rank-2-
# to-rank-2 identity mapping in this design for the ambiguity to even
# apply to). Flagged for a FUTURE phase that might need a genuine
# rank-preserving (not collapsing) transform, not resolved here.
_UNORIENTED_ZONE = SpatialZone.BACKCOURT  # the one zone this transform cannot re-derive information for


def flip_zone_to_new_offense_frame(zone: SpatialZone) -> SpatialZone:
    """Reinterprets `zone` (tracked relative to the OLD offense's own
    attacking rim) as the equivalent zone relative to the NEW offense's
    own attacking rim. Deterministic, no RNG, total (never raises) --
    see the module-level comment block immediately above for the full
    derivation and the documented, deliberate non-bijection. Every one
    of the 9 `SpatialZone` members is covered explicitly, not via a
    fallback/default branch, so a future new zone value fails loudly
    (`KeyError`) rather than being silently misclassified."""
    return _COURT_FLIP_MAP[zone]


_COURT_FLIP_MAP: Dict[SpatialZone, SpatialZone] = {
    SpatialZone.RESTRICTED_RIM: _UNORIENTED_ZONE,
    SpatialZone.PAINT: _UNORIENTED_ZONE,
    SpatialZone.MIDRANGE: _UNORIENTED_ZONE,
    SpatialZone.LEFT_WING: _UNORIENTED_ZONE,
    SpatialZone.RIGHT_WING: _UNORIENTED_ZONE,
    SpatialZone.LEFT_CORNER: _UNORIENTED_ZONE,
    SpatialZone.RIGHT_CORNER: _UNORIENTED_ZONE,
    SpatialZone.TOP_OF_KEY: _UNORIENTED_ZONE,
    SpatialZone.BACKCOURT: _UNORIENTED_ZONE,  # the one genuine fixed point -- see module comment
}


def relational_tag(player_zone: SpatialZone, ball_zone: SpatialZone) -> str:
    player_rank = _RIM_DISTANCE_RANK.get(player_zone, 3)
    ball_rank = _RIM_DISTANCE_RANK.get(ball_zone, 3)
    if player_rank < ball_rank:
        return AHEAD_OF_BALL
    if player_rank > ball_rank:
        return BEHIND_BALL
    return NEAR_BALL


@dataclass
class FloorPlayer:
    """One real player's coarse position at the moment of possession
    change, plus which team they're now on (POST-flip -- 'DEFENSE' means
    they are now defending the NEW offense). Caller-supplied; this
    module never invents a position."""
    player_id: str
    zone: SpatialZone
    side: str  # "OFFENSE" | "DEFENSE" -- relative to the NEW possession


@dataclass
class TransitionState:
    """The minimal new-possession packet. Every field was challenged
    against the task's own candidate list -- fields NOT included
    (numerical-advantage TIER enum, transition_eligible boolean,
    outlet-receiver assignment) were deliberately rejected as either
    redundant with derivable state or out of this phase's scope (see
    report Sec. 6/9/23)."""
    source: str
    new_offense_team_id: str
    new_defense_team_id: str
    ball_carrier_id: str
    ball_zone: SpatialZone
    player_zones: Tuple[FloorPlayer, ...] = ()   # empty if no real spatial info was supplied (graceful degradation, Sec. 34)
    shot_clock_remaining: Optional[float] = None
    advantage: Optional[AdvantageModel] = None    # freshly derived or None -- NEVER copied from the old offense

    @property
    def relational_tags(self) -> Dict[str, str]:
        """DERIVED, not stored -- computed fresh from `player_zones`/
        `ball_zone` every time, so it can never drift out of sync or be
        accidentally carried over from a prior state."""
        return {p.player_id: relational_tag(p.zone, self.ball_zone) for p in self.player_zones}

    @property
    def defenders_back_count(self) -> int:
        return sum(1 for p in self.player_zones if p.side == "DEFENSE" and relational_tag(p.zone, self.ball_zone) == AHEAD_OF_BALL)

    @property
    def offense_ahead_count(self) -> int:
        """Real, raw COUNT (Candidate B) -- not a discrete tier enum
        (Gemini's "defense set/offense edge/scrambled" tiers were
        explicitly NOT locked, per instruction). Counts real offensive
        players who are, structurally, closer to the rim than the
        ball -- i.e. already pushing ahead of it."""
        return sum(1 for p in self.player_zones if p.side == "OFFENSE" and relational_tag(p.zone, self.ball_zone) == AHEAD_OF_BALL)


def classify_source(source: str) -> str:
    return SOURCE_CLASSIFICATION.get(source, DEAD_BALL_INBOUND)  # an unrecognized source defaults to the SAFER (neutral) classification, never assumed transition-capable


def _derive_fresh_advantage(player_zones: Tuple[FloorPlayer, ...], ball_zone: SpatialZone) -> Optional[AdvantageModel]:
    """Freshly derives a NEW `SpatialMagnitudeAdvantage` from the
    CURRENT geometry only (a real, defensible default representation --
    NOT locked as the only future one, matching Phase 15's own
    representation-agnostic posture) -- compromised regions come ONLY
    from defenders who are structurally BEHIND the ball (caught out of
    position), never copied or inferred from the prior possession's own
    advantage state. Returns None if no real spatial info exists at all
    (graceful degradation, not a fabricated neutral state)."""
    if not player_zones:
        return None
    from possession_advantage import SpatialMagnitudeAdvantage
    compromised: Dict[SpatialZone, float] = {}
    for p in player_zones:
        if p.side == "DEFENSE" and relational_tag(p.zone, ball_zone) == BEHIND_BALL:
            # a real, hand-set, explicitly flagged placeholder magnitude per out-of-position defender --
            # direction only is defended (more caught-behind defenders -> more real compromise), magnitude unvalidated.
            compromised[p.zone] = compromised.get(p.zone, 0.0) + 0.3
    return SpatialMagnitudeAdvantage(magnitudes=compromised) if compromised else SpatialMagnitudeAdvantage()


def initialize_transition_state(engine: PossessionEngine, source: str, new_offense_team_id: str,
                                  new_defense_team_id: str, ball_carrier_id: str, ball_zone: SpatialZone,
                                  player_zones: Optional[List[FloorPlayer]] = None, dt: float = 0.0) -> TransitionState:
    """The single entry point. Deterministic -- no RNG is used anywhere
    in this function; the new state is derived entirely from the real,
    caller-supplied inputs. Applies the result to `engine` via existing
    Phase 15 state fields only (no parallel state system): clears
    `engine.state.assignments` (Candidate A -- clear all stale matchups,
    chosen as the simplest, safest V1 option; downstream defense
    reconstructs real assignments from the new geometry, not from stale
    pointers), resets `engine.advantage` to the freshly-derived value
    (never the old one), and initializes the shot clock via
    `engine.era_rules` (never a bare hardcoded 24)."""
    _assert_player_id(ball_carrier_id)
    player_zones = tuple(player_zones or ())
    for p in player_zones:
        _assert_player_id(p.player_id)

    classification = classify_source(source)
    if classification == DEAD_BALL_INBOUND:
        # Dead-ball sources begin structurally neutral -- no live-transition residue is preserved,
        # regardless of what player_zones the caller happened to supply (a real, deliberate choice:
        # "do not preserve live transition overload through a dead ball without evidence").
        effective_zones: Tuple[FloorPlayer, ...] = ()
        advantage = None
    else:
        effective_zones = player_zones
        advantage = _derive_fresh_advantage(player_zones, ball_zone)

    shot_clock = engine.era_rules.shot_clock_seconds  # a real, full reset via era rules -- never a bare literal

    new_state = TransitionState(
        source=source, new_offense_team_id=new_offense_team_id, new_defense_team_id=new_defense_team_id,
        ball_carrier_id=ball_carrier_id, ball_zone=ball_zone, player_zones=effective_zones,
        shot_clock_remaining=shot_clock, advantage=advantage,
    )

    from dataclasses import replace
    engine.state = replace(
        engine.state, offense_team_id=new_offense_team_id, defense_team_id=new_defense_team_id,
        ball_carrier=ball_carrier_id, ball_state=BallState.HELD,
        ball_control=engine.state.with_ball_carrier(ball_carrier_id, BallState.HELD).ball_control,
        ball_zone=ball_zone, assignments={}, shot_clock_remaining=shot_clock,
        phase=PossessionPhase.TRANSITION if classification == LIVE_TRANSITION_CAPABLE else PossessionPhase.HALFCOURT,
    )
    engine.advantage = advantage
    engine._log(EventType.POSSESSION_START, dt, primary=ball_carrier_id, zone=ball_zone,
                meta={"source": source, "classification": classification,
                      "defenders_back": new_state.defenders_back_count, "offense_ahead": new_state.offense_ahead_count})
    return new_state


# ---------------------------------------------------------------------
# OBSERVATIONAL TRANSITION CLASSIFIER (diagnostic only -- see the task's
# own "Build An Observational Transition Classifier" section). Reports
# what structural information ALREADY EXISTS at a live possession
# change, using ONLY this module's own existing, already-audited
# machinery (`classify_source`, `TransitionState`'s own
# `offense_ahead_count`/`defenders_back_count`/`relational_tags`
# properties, `_derive_fresh_advantage`) plus the court-flip transform
# above. NEVER consulted by any simulation decision: no caller in
# `detailed_game_orchestrator.py`/`possession_orchestrator.py` reads
# `TransitionDiagnostic` to choose `restart_type`, `PossessionStage`,
# timing, the action menu, assignments, or any probability -- it is
# attached to `RestartContext` purely for later analysis (Phases 7/8 of
# the task), same "diagnostic-only, never consulted" convention every
# other diagnostic log in this project already follows
# (`world.decision_log`, `world.clock_charge_log`, etc.). Consumes ZERO
# RNG -- every input is already-computed, already-logged state.
# ---------------------------------------------------------------------
from action_opportunity import INTERIOR_ZONES, PERIMETER_ZONES


@dataclass(frozen=True)
class TransitionDiagnostic:
    """DIAGNOSTIC ONLY. One instance per LIVE (or CONTEXT_DEPENDENT)
    possession-change source -- `None` for DEAD_BALL_INBOUND sources
    (no live geometry exists to inspect for those). Every field is
    read from, or derived via already-existing functions over, state
    `possession_orchestrator.py`'s dispatch code already computed
    (`world.player_zones`, `world.rebound_opportunity_log`) -- nothing
    here is a new simulation input."""
    source: str
    source_taxonomy: str  # classify_source(source): LIVE_TRANSITION_CAPABLE | DEAD_BALL_INBOUND | CONTEXT_DEPENDENT
    old_offense_team_id: str
    new_offense_team_id: str
    ball_carrier_id: str
    prior_zones: Tuple[FloorPlayer, ...]                  # old-offense-relative, as tracked at possession end; side is OFFENSE/DEFENSE relative to the OLD (ending) possession
    new_offense_relative_zones: Tuple[FloorPlayer, ...]   # same players, zone flipped via flip_zone_to_new_offense_frame, side relative to the NEW possession
    old_offense_interior_count: int
    old_offense_perimeter_count: int
    offense_ahead_count: int
    defenders_back_count: int
    fresh_advantage_would_be_present: bool
    fresh_compromised_zones: Tuple[SpatialZone, ...]
    prior_shot_family: Optional[str]
    prior_release_zone: Optional[SpatialZone]


def build_transition_diagnostic(source: str, world, new_offense_team_id: str,
                                 new_defense_team_id: str, ball_carrier_id: str) -> Optional[TransitionDiagnostic]:
    """`world` is the ENDING possession's own `PossessionWorld` (duck-
    typed here, not imported at module level, to avoid any risk of a
    circular import with `possession_orchestrator.py` -- that module
    does not import this one today, and this function does not need to
    force a dependency either way). Returns `None` for a DEAD_BALL_INBOUND
    source -- consistent with `initialize_transition_state`'s own
    "dead-ball sources begin structurally neutral" rule; there is no
    live geometry to report for those. Consumes ZERO RNG; every value
    is read from, or derived deterministically via already-existing
    pure functions over, `world`'s own already-populated diagnostic
    state."""
    taxonomy = classify_source(source)
    if taxonomy == DEAD_BALL_INBOUND:
        return None

    old_offense_team_id = world.team_a_id
    old_offense_five = world.team_a_five

    prior_zones: List[FloorPlayer] = []
    new_offense_relative_zones: List[FloorPlayer] = []
    for pid in world.all_ten():
        zone = world.player_zones.get(pid, SpatialZone.TOP_OF_KEY)
        old_side = "OFFENSE" if pid in old_offense_five else "DEFENSE"
        prior_zones.append(FloorPlayer(player_id=pid, zone=zone, side=old_side))
        new_side = "DEFENSE" if old_side == "OFFENSE" else "OFFENSE"  # the old offense is now the new defense
        new_offense_relative_zones.append(
            FloorPlayer(player_id=pid, zone=flip_zone_to_new_offense_frame(zone), side=new_side)
        )
    prior_zones = tuple(prior_zones)
    new_offense_relative_zones = tuple(new_offense_relative_zones)

    old_offense_interior_count = sum(1 for p in prior_zones if p.side == "OFFENSE" and p.zone in INTERIOR_ZONES)
    old_offense_perimeter_count = sum(1 for p in prior_zones if p.side == "OFFENSE" and p.zone in PERIMETER_ZONES)

    # Reuses TransitionState's OWN existing offense_ahead_count/defenders_back_count properties
    # verbatim -- ball_zone=BACKCOURT matches this module's own documented convention (see the
    # court-flip transform's own comment block) for a live possession change originating at the
    # OLD offense's own attacking end.
    probe_state = TransitionState(
        source=source, new_offense_team_id=new_offense_team_id, new_defense_team_id=new_defense_team_id,
        ball_carrier_id=ball_carrier_id, ball_zone=SpatialZone.BACKCOURT,
        player_zones=new_offense_relative_zones,
    )
    fresh_advantage = _derive_fresh_advantage(new_offense_relative_zones, SpatialZone.BACKCOURT)
    fresh_compromised_zones = tuple(area.zone for area in fresh_advantage.compromised_areas()) if fresh_advantage is not None else ()

    prior_shot_family = None
    prior_release_zone = None
    rebound_log = getattr(world, "rebound_opportunity_log", ())
    if rebound_log:
        last_rebound = rebound_log[-1]
        prior_shot_family = last_rebound.get("shot_family")
        raw_zone = last_rebound.get("rebound_zone")
        prior_release_zone = SpatialZone(raw_zone) if raw_zone is not None else None

    return TransitionDiagnostic(
        source=source, source_taxonomy=taxonomy,
        old_offense_team_id=old_offense_team_id, new_offense_team_id=new_offense_team_id,
        ball_carrier_id=ball_carrier_id,
        prior_zones=prior_zones, new_offense_relative_zones=new_offense_relative_zones,
        old_offense_interior_count=old_offense_interior_count,
        old_offense_perimeter_count=old_offense_perimeter_count,
        offense_ahead_count=probe_state.offense_ahead_count,
        defenders_back_count=probe_state.defenders_back_count,
        fresh_advantage_would_be_present=fresh_advantage is not None,
        fresh_compromised_zones=fresh_compromised_zones,
        prior_shot_family=prior_shot_family, prior_release_zone=prior_release_zone,
    )
