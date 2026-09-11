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
