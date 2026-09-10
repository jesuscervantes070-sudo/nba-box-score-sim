"""
Phase 16 -- Action Selection & Opportunity Generation: PLAYER PERCEPTION
of objective opportunities.

WORLD OPPORTUNITY -> PLAYER PERCEPTION. `playmaking_vision` is used ONLY
here, ONLY to gate whether a genuinely-harder-to-see opportunity enters
the ball handler's perceived menu -- never to adjust pass accuracy,
never as generic offensive intelligence, and never inside
action_selection.py's policy math. An obvious opportunity (safety
reset, nearby swing, an uncontested shot right in front of the ball
handler) is NEVER vision-gated -- it always passes straight through,
because a real NBA ball-handler does not need above-average vision to
notice the man standing next to him.
"""
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

from action_intent import ActionType
from action_opportunity import ObjectiveOpportunity

# Opportunity types that plausibly require above-baseline vision to
# perceive at all, per the task's own worked examples (weak-side skip,
# pocket pass, backdoor cutter, secondary kickout, help-generated
# dump-off). Everything else always passes through ungated.
VISION_GATED_ACTIONS = frozenset({ActionType.KICKOUT, ActionType.POCKET_PASS})

NO_GATE_PROVENANCE = "OBJECTIVE_NO_GATE"
VISION_GATE_PROVENANCE_PREFIX = "VISION_GATED:playmaking_vision"


@dataclass(frozen=True)
class PerceivedOpportunity:
    """An ObjectiveOpportunity that made it into the ball handler's
    actual perceived menu, tagged with WHY (provenance) -- purely
    diagnostic, never fed back into any probability."""
    opportunity: ObjectiveOpportunity
    perception_provenance: str


def perceive(opportunities: List[ObjectiveOpportunity], vision_latent_propensity: Optional[float],
             rng: random.Random) -> List[PerceivedOpportunity]:
    """`vision_latent_propensity` is the ball handler's real
    `playmaking_vision` latent value (Phase 8's KEEP-BUT-FLAG estimate,
    logit/percentile-style, roughly centered at 0 = league average) --
    or None if unavailable (missing != zero: a missing vision estimate
    means every vision-gated opportunity is judged at a neutral,
    league-average perception rate, NOT silently perceived 100% of the
    time and NOT silently suppressed to 0%).

    Deterministic given `rng`'s state -- no hidden global randomness."""
    result: List[PerceivedOpportunity] = []
    for opp in opportunities:
        if opp.action_type not in VISION_GATED_ACTIONS:
            result.append(PerceivedOpportunity(opp, NO_GATE_PROVENANCE))
            continue
        perceive_probability = _vision_to_perception_probability(vision_latent_propensity)
        if rng.random() < perceive_probability:
            result.append(PerceivedOpportunity(opp, f"{VISION_GATE_PROVENANCE_PREFIX}(p={perceive_probability:.2f})"))
        # else: a REAL objective opportunity existed and was never perceived -- correctly absent from the menu, not logged as a "failure"
    return result


def _vision_to_perception_probability(vision_latent_propensity: Optional[float]) -> float:
    """A single, explicit, calibratable mapping from a real latent
    vision estimate to a perception probability -- a placeholder LOGISTIC
    curve (NOT an empirically fit one; no calibration experiment backs
    this specific shape yet, see the Phase 16 report's Sec. 14). Centered
    at 0.6 for a league-average player (not 0.5) because even an average
    NBA rotation player perceives a genuinely live kickout/pocket-pass
    window a clear majority of the time -- this single constant is the
    one deliberately-placeholder number in this module, called out
    explicitly rather than hidden."""
    if vision_latent_propensity is None:
        return 0.6  # missing evidence -> neutral, league-average assumption, not 0 and not 1
    import math
    steepness = 0.5  # placeholder, not calibrated
    baseline_logit = math.log(0.6 / 0.4)
    return 1.0 / (1.0 + math.exp(-(baseline_logit + steepness * vision_latent_propensity)))
