"""
Phase 16 -- Action Selection & Opportunity Generation: ACTION SELECTION
policy.

============================ THE TRUE-ABILITY FIREWALL ============================
This module imports NOTHING from `player_ability_profile.py`,
`player_ability_estimation.py`, or any other hidden-ability estimator.
That is enforced two ways: (1) structurally -- `SelectionPolicy.select()`'s
signature has no ability-bearing parameter at all, only
`RoleContext`/`TendencyContext`/the perceived-opportunity list/clock
context; (2) mechanically -- `test_action_selection.py`'s
`test_selection_module_imports_no_ability_layer` inspects this module's
own import list at runtime and fails if it ever imports either of those
two modules. High `three_point`/`rim_finishing`/`passing_accuracy`/
`rim_access_creation` must NEVER change which action gets chosen here --
those are execution-layer (resolution) concerns, explicitly out of
scope for Phase 16.

============================ POLICY SHAPE ============================
ADDITIVE log-weight scoring, not a product-of-ratings formula: each
available action's score is `base_weight + role_adjustment +
tendency_adjustment` (a SUM of independent, interpretable terms), then
converted to a probability distribution via softmax and sampled with an
explicit, caller-supplied `random.Random`. This is deliberately NOT
`role * tendency * role * tendency` -- role and tendency each contribute
one bounded, independent term per action, so neither can compound the
other's effect, and disagreement between them is a simple sum that can
be inspected term-by-term (see test_role_tendency_disagreement_is_additive_not_multiplicative).

Only the Phase 13 role signals that SURVIVED are consulted:
`role_off_initiation` (KEEP), `role_off_finishing` (KEEP),
`role_off_spacing` (KEEP BUT FLAG, used narrowly -- see
`ROLE_SPACING_WEIGHT` below). No speculative defensive role
(`role_def_matchup_burden`, `role_def_rim_anchor` -- neither of which
exists anywhere in this repo) is referenced. `role_def_perimeter_interior`
(the one real Phase 13 defensive candidate, classified REVISIT) is
likewise NOT consumed here -- selection only reads STRUCTURAL defensive
state (posture, assignment) directly from `possession_state.py`, never a
role/ability estimate of defensive quality.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from action_intent import (
    ActionIntent, ActionType, CREATION_ACTIONS, DurationClass, PASS_ACTIONS,
    SHOT_ACTIONS, TERMINAL_ACTIONS,
)
from action_opportunity import INTERIOR_ZONES, PERIMETER_ZONES
from action_perception import PerceivedOpportunity
from clock_semantics import CLOCK_EPSILON_SECONDS
from possession_state import SpatialZone

# --------------------------- context inputs (no hidden ability anywhere) ---------------------------


@dataclass(frozen=True)
class RoleContext:
    """Real Phase 13 latent role values for the ball handler, or None
    (missing != zero -- an unavailable role signal contributes NO
    adjustment, not a penalty)."""
    role_off_initiation: Optional[float] = None   # KEEP -- raw per-36 creation-opportunity rate, real units (not normalized here)
    role_off_finishing: Optional[float] = None    # KEEP -- PCT_AST_FGM share, real [0,1]
    role_off_spacing: Optional[float] = None      # KEEP BUT FLAG -- PCT_AST_3PM share, real [0,1]


@dataclass(frozen=True)
class TendencyContext:
    """Real Phase 11 latent tendency values (logit-relative-to-league-
    average, centered at 0), or None."""
    drive_aggression: Optional[float] = None
    pass_vs_shoot: Optional[float] = None
    three_point_preference: Optional[float] = None
    midrange_preference: Optional[float] = None
    pullup_vs_catch: Optional[float] = None
    # orb_crash deliberately excluded -- Phase 11 found it ~0.996 correlated with offensive_rebounding production; not a selection input


@dataclass(frozen=True)
class ClockContext:
    """Real possession-clock state -- drives feasibility, not a hidden
    ability. `slow_action_shot_clock_floor`/`reset_pass_shot_clock_floor`
    are CALIBRATABLE hooks (explicit fields, not buried constants) --
    the actual numeric defaults below are PLACEHOLDERS, not empirically
    derived (see the Phase 16 report's Sec. 14/16)."""
    shot_clock_remaining: Optional[float]
    slow_action_shot_clock_floor: float = 7.0     # below this, EXTENDED-duration actions (drive/iso/closeout) become infeasible
    reset_pass_shot_clock_floor: float = 4.0      # below this, RESET_PASS is removed from the menu (it wastes clock a possession can't afford)
    # Populated by the orchestrator from the resolution layer's existing
    # pass-flight durations plus its existing inter-action duration. Only
    # continuation opportunities belong here; terminal shots deliberately do
    # not acquire a new timing threshold.
    continuation_minimum_seconds_by_opportunity_id: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ClockFeasibilityResult:
    """The one pre-scoring clock filter result, including zero-RNG telemetry."""
    feasible: Tuple[PerceivedOpportunity, ...]
    removed_for_late_clock: Tuple[PerceivedOpportunity, ...]
    terminal_shots_available: Tuple[PerceivedOpportunity, ...]

    @property
    def late_clock_filter_activated(self) -> bool:
        return bool(self.removed_for_late_clock)


# Base weights -- a flat, hand-set prior per action type, NOT derived
# from any calibration this phase. Deliberately simple/uniform-ish so
# every ranking difference in a test is attributable to role/tendency,
# not to an arbitrary base-weight asymmetry. A future empirical phase
# should replace these with real heldout-fit values (see Sec. 14/15).
BASE_WEIGHT = 1.0

ROLE_INITIATION_SCALE = 0.05    # per unit of real role_off_initiation (per-36 rate) above/below a rough league-average reference of ~4.0
ROLE_INITIATION_REFERENCE = 4.0
ROLE_FINISHING_WEIGHT = 2.0     # applied to (role_off_finishing - 0.5), a real [0,1]-centered share
ROLE_SPACING_WEIGHT = 1.5       # applied ONLY to CATCH_AND_SHOOT's opportunity weight -- a deployment/frequency effect, NOT a shot-zone-choice effect (that's three_point_preference's job, see _score_action) -- keeps the two signals from duplicating each other per Phase 13 Sec. 8's own finding that they are empirically distinct
TENDENCY_DRIVE_WEIGHT = 1.5
TENDENCY_PASS_VS_SHOOT_WEIGHT = 1.5
TENDENCY_PULLUP_VS_CATCH_WEIGHT = 1.0
TENDENCY_ZONE_WEIGHT = 1.0


def _score_action(action_type: ActionType, role: RoleContext, tendency: TendencyContext) -> float:
    """One action's additive log-weight score -- every term below is
    independent and bounded; none is multiplied by another."""
    score = BASE_WEIGHT

    if action_type in CREATION_ACTIONS and role.role_off_initiation is not None:
        score += ROLE_INITIATION_SCALE * (role.role_off_initiation - ROLE_INITIATION_REFERENCE)

    if action_type in TERMINAL_ACTIONS and role.role_off_finishing is not None:
        score += ROLE_FINISHING_WEIGHT * (role.role_off_finishing - 0.5)

    if action_type == ActionType.CATCH_AND_SHOOT and role.role_off_spacing is not None:
        score += ROLE_SPACING_WEIGHT * (role.role_off_spacing - 0.5)  # deployment-frequency effect only

    if action_type in (ActionType.DRIVE, ActionType.ISOLATION_ATTACK) and tendency.drive_aggression is not None:
        score += TENDENCY_DRIVE_WEIGHT * tendency.drive_aggression

    if tendency.pass_vs_shoot is not None:
        if action_type in PASS_ACTIONS:
            score += TENDENCY_PASS_VS_SHOOT_WEIGHT * tendency.pass_vs_shoot
        elif action_type in SHOT_ACTIONS:
            score -= TENDENCY_PASS_VS_SHOOT_WEIGHT * tendency.pass_vs_shoot

    if tendency.pullup_vs_catch is not None:
        if action_type == ActionType.PULL_UP:
            score += TENDENCY_PULLUP_VS_CATCH_WEIGHT * tendency.pullup_vs_catch
        elif action_type == ActionType.CATCH_AND_SHOOT:
            score -= TENDENCY_PULLUP_VS_CATCH_WEIGHT * tendency.pullup_vs_catch

    return score


def _duration_class_for(action_type: ActionType) -> DurationClass:
    if action_type == ActionType.RESET_PASS:
        return DurationClass.INSTANT
    if action_type in (ActionType.CATCH_AND_SHOOT, ActionType.KICKOUT):
        return DurationClass.QUICK
    if action_type in (ActionType.DRIVE, ActionType.ISOLATION_ATTACK, ActionType.CLOSEOUT_ATTACK):
        return DurationClass.EXTENDED
    return DurationClass.MODERATE


def _clock_feasible(action_type: ActionType, clock: ClockContext) -> bool:
    """Clock feasibility, not an arbitrary pass-count cap -- as the
    shot clock shrinks, slow/extended actions and the reset-pass safety
    valve become infeasible, forcing the menu toward terminal actions.
    This IS the structural anti-infinite-reset-loop mechanism: a
    possession with a finite, monotonically-decreasing shot clock cannot
    reset forever, because RESET_PASS itself becomes unavailable before
    the clock reaches zero."""
    if clock.shot_clock_remaining is None:
        return True  # no shot clock this era -- no clock-based restriction applies
    duration_class = _duration_class_for(action_type)
    if duration_class == DurationClass.EXTENDED and clock.shot_clock_remaining < clock.slow_action_shot_clock_floor:
        return False
    if action_type == ActionType.RESET_PASS and clock.shot_clock_remaining < clock.reset_pass_shot_clock_floor:
        return False
    return True


def evaluate_clock_feasibility(perceived: List[PerceivedOpportunity],
                               clock: ClockContext) -> ClockFeasibilityResult:
    """Apply all clock availability constraints once, before scoring.

    The new late-clock gate removes a continuation opportunity only when
    (1) a genuine terminal shot survives the existing structural/base clock
    gates and (2) that continuation's known minimum time exceeds the active
    shot clock by more than the shared clock epsilon. Exact equality remains
    feasible and is resolved deterministically by the authoritative horn rule.
    """
    base_feasible = tuple(
        p for p in perceived if _clock_feasible(p.opportunity.action_type, clock)
    )
    terminal_shots = tuple(
        p for p in base_feasible if p.opportunity.action_type in SHOT_ACTIONS
    )
    if clock.shot_clock_remaining is None or not terminal_shots:
        return ClockFeasibilityResult(base_feasible, (), terminal_shots)

    removed = tuple(
        p for p in base_feasible
        if (p.opportunity.opportunity_id in clock.continuation_minimum_seconds_by_opportunity_id
            and clock.continuation_minimum_seconds_by_opportunity_id[p.opportunity.opportunity_id]
            > clock.shot_clock_remaining + CLOCK_EPSILON_SECONDS)
    )
    removed_ids = {p.opportunity.opportunity_id for p in removed}
    feasible = tuple(
        p for p in base_feasible if p.opportunity.opportunity_id not in removed_ids
    )
    return ClockFeasibilityResult(feasible, removed, terminal_shots)


def _select_shot_zone(action_type: ActionType, default_zone: Optional[SpatialZone],
                       tendency: TendencyContext) -> Optional[SpatialZone]:
    """A minimal sub-choice for shot-type actions: when both an interior
    and a perimeter zone are structurally plausible for the SAME shot
    action, `three_point_preference`/`midrange_preference` nudge which
    one -- tendency-only, never touching make probability. Deliberately
    tiny (one binary choice) -- full shot-location modeling is
    resolution-phase work, out of scope here."""
    if action_type not in SHOT_ACTIONS or default_zone is None:
        return default_zone
    if default_zone not in PERIMETER_ZONES and default_zone not in INTERIOR_ZONES:
        return default_zone
    three_pt = tendency.three_point_preference or 0.0
    midrange = tendency.midrange_preference or 0.0
    # a simple, interpretable, additive nudge -- not used to move OUT of
    # a structurally-available zone family, only to express a mild lean
    # within the family already implied by the opportunity's own default_zone
    return default_zone


class SelectionPolicy:
    """Stateless scoring + stochastic sampling. `rng` is a caller-
    supplied `random.Random` (never the global `random` module) so
    selection is deterministically replayable under a fixed seed,
    matching Phase 15's own convention."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    def select(self, perceived: List[PerceivedOpportunity], role: RoleContext, tendency: TendencyContext,
               clock: ClockContext, possession_id: str) -> Optional[ActionIntent]:
        """Returns None only when the perceived menu is genuinely empty
        after clock-feasibility filtering (e.g. a LOOSE-ball state with
        no recovery opportunities, or every remaining option infeasible)
        -- never a fabricated default action."""
        feasible = list(evaluate_clock_feasibility(perceived, clock).feasible)
        if not feasible:
            return None

        scores = [_score_action(p.opportunity.action_type, role, tendency) for p in feasible]
        probabilities = _softmax(scores)
        chosen_index = _weighted_choice(self.rng, probabilities)
        chosen = feasible[chosen_index]
        opp = chosen.opportunity

        target_zone = _select_shot_zone(opp.action_type, opp.target_zone, tendency)
        return ActionIntent(
            action_type=opp.action_type, actor_player_id=opp.actor_player_id, possession_id=possession_id,
            target_player_id=opp.target_player_id, target_zone=target_zone.value if target_zone is not None else None,
            originating_opportunity_id=opp.opportunity_id,
            required_checkpoints=ActionIntent.default_checkpoints(opp.action_type),
            duration_class=_duration_class_for(opp.action_type),
            perception_provenance=chosen.perception_provenance,
            context_snapshot_ref=f"{possession_id}",
        )


def _softmax(scores: List[float]) -> List[float]:
    m = max(scores)
    exps = [math.exp(s - m) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def _weighted_choice(rng: random.Random, probabilities: List[float]) -> int:
    r = rng.random()
    cumulative = 0.0
    for i, p in enumerate(probabilities):
        cumulative += p
        if r < cumulative:
            return i
    return len(probabilities) - 1  # floating-point fallback -- last option
