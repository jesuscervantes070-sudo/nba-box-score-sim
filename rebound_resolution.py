"""
Phase 19 -- Rebound Opportunity + Rebound Resolution.

MISS/LIVE REBOUNDABLE BALL -> CAROM/REBOUND REGION -> REBOUND
OPPORTUNITY ELIGIBILITY -> BOX-OUT/LEVERAGE CONTEXT -> REBOUNDING SKILL
RESOLUTION -> SECURE/LOOSE/TEAM-REBOUND OUTCOME -> POSSESSION OR
SECOND-CHANCE HANDOFF. **THE CENTRAL REQUIREMENT**: a player must first
have a REAL, structural rebound OPPORTUNITY (same real zone as the
carom region, or explicitly supplied as eligible) before
`offensive_rebounding`/`defensive_rebounding` skill is consulted at
all -- see `eligible_rebound_candidates`, called BEFORE any skill value
is read anywhere in this module.

============================ CONSTRUCT AUDITS (see docs/PHASE19_REBOUND_RESOLUTION_REPORT.md) ============================
`offensive_rebounding`/`defensive_rebounding` (player_ability_estimation.py,
Phase 1-3, KEEP): real OREB_PCT/DREB_PCT (the NBA's own real,
opportunity-normalized "% of available rebounds this player grabbed
while on the floor," with a real historical fallback approximation when
true OREB_PCT/DREB_PCT isn't available) -- confirmed by direct source
read. A real, ALREADY opportunity-normalized rate, not a raw per-game
count -- but still a TEAM-CONTEXT-level normalization (rebounds
available while this player was on the floor), not an individual,
chance-by-chance acquisition rate. Real, decisive diagnostic this phase
(2023-24, n=394, real `leaguedashptstats` Rebounding-measure data):
`offensive_rebounding`'s own real OREB_PCT input correlates r=0.535
with the real, more granular `OREB_CHANCE_PCT` (rebounds secured out of
real, individually-tracked rebound CHANCES) -- moderate, not 1.0 and
not 0.0, confirming OREB_PCT carries real acquisition-skill signal
while still leaving real, substantial opportunity-driven variance
unexplained. This is exactly why this module enforces ELIGIBILITY
(a real, structural opportunity gate) BEFORE consulting either skill
value, rather than letting the skill value stand in for opportunity.

`orb_crash` was explicitly investigated and rejected in Phase 11
(~0.996 correlated with real offensive-rebounding production) --
**not revived here**, and no such concept (a persistent player
"crash propensity" trait) exists anywhere in this module.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from possession_advantage import AdvantageModel
from possession_engine import PossessionEngine
from possession_state import SpatialZone, _assert_player_id

# ---------------------------------------------------------------------
# Real, hand-set, EXPLICITLY FLAGGED placeholder default carom regions
# by shot family -- Candidate A/E chosen (shot-zone-only region, no
# shot-family x miss-type interaction), per the real, decisive finding
# (2023-24, leaguedashptstats Rebounding measure) that real leaguewide
# AVG_OREB_DIST (9.2 ft) and AVG_DREB_DIST (7.0 ft) are BOTH close to
# the rim REGARDLESS of shot family -- consistent with real basketball
# physics (most rebounds, even off perimeter misses, cluster near the
# interior) and NOT with a naive "long shot = long rebound" assumption,
# which was explicitly investigated and REJECTED for lack of a
# shot-family-specific public cross-tab to justify it (Sec. 8 of the
# report). A caller MAY override this default with a real,
# caller-determined zone (e.g. a genuinely long carom) via
# `ReboundOpportunity.rebound_zone` -- this module does not force it.
# ---------------------------------------------------------------------
_DEFAULT_CAROM_ZONE = SpatialZone.RESTRICTED_RIM


class ReboundSource:
    MISSED_FG = "MISSED_FG"
    UNRESOLVED_BLOCK = "UNRESOLVED_BLOCK"
    FINAL_MISSED_FT = "FINAL_MISSED_FT"


class BoxOutState:
    """Structural leverage context -- NOT a stored player rating. A
    real, coarse taxonomy (established box-out / contested / none),
    caller-supplied per rebound event, not derived by this module from
    any historical proxy."""
    ESTABLISHED_BOXOUT = "ESTABLISHED_BOXOUT"   # this defender has real inside leverage on a specific offensive candidate
    CONTESTED = "CONTESTED"                     # no clean box-out either way
    NONE = "NONE"                               # no meaningful leverage state at all (e.g. an uncontested long rebound)


@dataclass
class ReboundCandidate:
    """One structurally-eligible-to-compete player. `side` is
    `"OFFENSE"`/`"DEFENSE"` -- the SAME latent slot
    (`offensive_rebounding`/`defensive_rebounding`) is never swapped
    for the wrong side (verified by test). `box_out_state` describes
    THIS candidate's own leverage; `boxed_out_by` names the opposing
    candidate who has established position on them, if any (structural,
    not a rating)."""
    player_id: str
    side: str
    zone: SpatialZone
    box_out_state: str = BoxOutState.NONE
    boxed_out_by: Optional[str] = None
    offensive_rebounding: Optional[float] = None  # real Phase 1-3 estimate, consulted ONLY if side == "OFFENSE" and this candidate is eligible
    defensive_rebounding: Optional[float] = None  # real Phase 1-3 estimate, consulted ONLY if side == "DEFENSE" and this candidate is eligible


@dataclass
class ReboundOpportunity:
    source: str
    shot_family: str
    rebound_zone: Optional[SpatialZone] = None   # None -> use the real, flagged default (see module constant above)
    candidates: List[ReboundCandidate] = field(default_factory=list)
    advantage: Optional[AdvantageModel] = None   # read-only context; never mutated by this module directly

    @property
    def effective_zone(self) -> SpatialZone:
        return self.rebound_zone if self.rebound_zone is not None else _DEFAULT_CAROM_ZONE


def eligible_rebound_candidates(opportunity: ReboundOpportunity) -> List[ReboundCandidate]:
    """THE CENTRAL GATE: a candidate is eligible ONLY if their own zone
    matches the rebound's effective carom zone -- a player on the
    opposite side of the court cannot compete for this rebound
    regardless of any skill value, because this function runs, and
    filters, BEFORE any skill value is ever read anywhere in this
    module."""
    zone = opportunity.effective_zone
    return [c for c in opportunity.candidates if c.zone == zone]


# Box-out leverage deltas -- real, hand-set, EXPLICITLY UNVALIDATED
# placeholders (no public per-rebound box-out-outcome dataset exists to
# fit against -- same honest posture as every prior resolution phase).
# Direction only is defended: an established box-out meaningfully raises
# the boxing-out player's (and lowers the boxed-out player's) real
# acquisition weight; a merely-contested rebound has no leverage
# adjustment at all.
_BOXOUT_LEVERAGE_BONUS = 0.6
_BOXED_OUT_LEVERAGE_PENALTY = -0.6

# FIRST-PASS AGGREGATE ACQUISITION CALIBRATION. OREB_PCT and DREB_PCT
# are side-specific production shares with different opportunity
# denominators, not already-comparable acquisition log strengths. The
# two reference rates anchor each source statistic within its OWN
# population before the centered evidence enters a common contest.
# They are rounded 2024-25 player-population means from this project's
# cached leaguedashplayerstats(Advanced) OREB_PCT/DREB_PCT data.
OFFENSIVE_REBOUND_RATE_REFERENCE = 0.0482
DEFENSIVE_REBOUND_RATE_REFERENCE = 0.1313

# Temporary, explicit V0 home for the population-level defensive-side
# advantage while carom and boxout structure remain incomplete. This
# is an acquisition INTERCEPT, separate from player evidence and from
# the dormant event-level boxout terms. -1.5 is a deliberately round
# first-pass value selected against the aggregate 25.2% side-balance
# constraint, not a final rebound-physics claim or a post-draw OREB
# probability override.
OFFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT = -1.5
DEFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT = 0.0

# Numerical guard only. Real source data includes exact 0 and 1 values;
# clipping at the conversion boundary keeps logit finite without
# changing the stored player evidence.
REBOUND_RATE_LOGIT_EPSILON = 1e-6


def _bounded_logit(rate: float) -> float:
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"rebound rate evidence must be in [0, 1], got {rate!r}")
    bounded = min(1.0 - REBOUND_RATE_LOGIT_EPSILON, max(REBOUND_RATE_LOGIT_EPSILON, rate))
    return math.log(bounded / (1.0 - bounded))


def rebound_rate_to_acquisition_log_weight(rate: Optional[float], side: str) -> float:
    """Convert native empirical rate evidence to a contest log-weight.

    The centered logit carries only league-relative PLAYER evidence;
    the separately named side intercept carries the provisional V0
    population balance. Stored OREB_PCT/DREB_PCT values are unchanged.
    """
    if rate is None:
        raise ValueError("missing rebound-rate evidence cannot enter acquisition competition")
    if side == "OFFENSE":
        reference = OFFENSIVE_REBOUND_RATE_REFERENCE
        baseline = OFFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT
    elif side == "DEFENSE":
        reference = DEFENSIVE_REBOUND_RATE_REFERENCE
        baseline = DEFENSIVE_ACQUISITION_BASELINE_LOG_WEIGHT
    else:
        raise ValueError(f"rebound candidate side must be 'OFFENSE' or 'DEFENSE', got {side!r}")
    return baseline + _bounded_logit(rate) - _bounded_logit(reference)


def _candidate_log_weight(candidate: ReboundCandidate) -> float:
    """Additive log-weight -- combined SOFTMAX-style competition among
    ELIGIBLE candidates only (never a sum/product across sides, and
    never consulted for an ineligible candidate at all)."""
    skill = candidate.offensive_rebounding if candidate.side == "OFFENSE" else candidate.defensive_rebounding
    weight = rebound_rate_to_acquisition_log_weight(skill, candidate.side)
    if candidate.box_out_state == BoxOutState.ESTABLISHED_BOXOUT:
        weight += _BOXOUT_LEVERAGE_BONUS
    if candidate.boxed_out_by is not None:
        weight += _BOXED_OUT_LEVERAGE_PENALTY
    return weight


class ReboundOutcome:
    SECURED_OFFENSE = "SECURED_OFFENSE"
    SECURED_DEFENSE = "SECURED_DEFENSE"
    TEAM_REBOUND_OFFENSE = "TEAM_REBOUND_OFFENSE"
    TEAM_REBOUND_DEFENSE = "TEAM_REBOUND_DEFENSE"


@dataclass
class ReboundResult:
    outcome: str
    rebounder_id: Optional[str] = None
    eligible_count: int = 0


def _softmax_choice(rng: random.Random, weighted_candidates: List[Tuple[ReboundCandidate, float]]) -> ReboundCandidate:
    m = max(w for _, w in weighted_candidates)
    exps = [(c, math.exp(w - m)) for c, w in weighted_candidates]
    total = sum(e for _, e in exps)
    r = rng.random() * total
    cumulative = 0.0
    for c, e in exps:
        cumulative += e
        if r < cumulative:
            return c
    return exps[-1][0]


def resolve_rebound(opportunity: ReboundOpportunity, rng: random.Random,
                     no_eligible_candidate_default_side: str = "DEFENSE") -> ReboundResult:
    """The single entry point. Direct multi-player competition among
    ELIGIBLE candidates only (Candidate: eligibility-weighted individual
    model) -- NOT a team-first branch (P(OREB) vs P(DREB) decided
    first, then a player chosen within it). Side-specific OREB_PCT and
    DREB_PCT evidence is normalized against its own reference before
    this shared contest; the explicit V0 side intercept is part of each
    candidate's acquisition weight, not a second team-level draw.

    If no candidate is eligible (nobody from the caller-supplied roster
    happens to be in the carom zone -- a real, possible state, not an
    error), the ball is credited as a TEAM rebound to
    `no_eligible_candidate_default_side` -- never assigned to an
    arbitrary individual."""
    eligible = eligible_rebound_candidates(opportunity)
    if not eligible:
        outcome = ReboundOutcome.TEAM_REBOUND_OFFENSE if no_eligible_candidate_default_side == "OFFENSE" else ReboundOutcome.TEAM_REBOUND_DEFENSE
        return ReboundResult(outcome=outcome, eligible_count=0)

    weighted = [(c, _candidate_log_weight(c)) for c in eligible]
    winner = _softmax_choice(rng, weighted)
    outcome = ReboundOutcome.SECURED_OFFENSE if winner.side == "OFFENSE" else ReboundOutcome.SECURED_DEFENSE
    return ReboundResult(outcome=outcome, rebounder_id=winner.player_id, eligible_count=len(eligible))


def apply_rebound_to_engine(engine: PossessionEngine, opportunity: ReboundOpportunity, rng: random.Random,
                             new_offense_team_id: Optional[str] = None, new_defense_team_id: Optional[str] = None,
                             original_offense_team_id: Optional[str] = None) -> ReboundResult:
    """Thin integration layer -- reuses Phase 19's own new, additive
    engine methods (`secure_offensive_rebound_from_loose`,
    `secure_defensive_rebound_from_loose`, `credit_team_rebound`), which
    are themselves the SAME real logic as Phase 15's own pre-existing
    OREB/DREB methods, just re-guarded on the real `LOOSE` state Phase
    18A/18B/18C actually hands off. `new_offense_team_id`/
    `new_defense_team_id` are required only for a real defensive
    outcome (a possession flip needs real team ids this engine doesn't
    know on its own -- same convention as every other terminal method
    in `PossessionEngine`)."""
    result = resolve_rebound(opportunity, rng)
    if result.outcome == ReboundOutcome.SECURED_OFFENSE:
        engine.secure_offensive_rebound_from_loose(result.rebounder_id, offense_team_id=original_offense_team_id)
    elif result.outcome == ReboundOutcome.SECURED_DEFENSE:
        if new_offense_team_id is None or new_defense_team_id is None:
            raise ValueError("a defensive rebound requires new_offense_team_id/new_defense_team_id")
        engine.secure_defensive_rebound_from_loose(result.rebounder_id, new_offense_team_id, new_defense_team_id)
    elif result.outcome == ReboundOutcome.TEAM_REBOUND_OFFENSE:
        engine.credit_team_rebound("OFFENSE")
    else:
        if new_offense_team_id is None or new_defense_team_id is None:
            raise ValueError("a defensive team rebound requires new_offense_team_id/new_defense_team_id")
        engine.credit_team_rebound("DEFENSE", new_offense_team_id, new_defense_team_id)
    return result
