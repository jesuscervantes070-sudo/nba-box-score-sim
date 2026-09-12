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
from action_opportunity import INTERIOR_ZONES, MIDRANGE_ZONES, PERIMETER_ZONES
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


@dataclass(frozen=True)
class ShotFamilySelectionContext:
    """Era/environment prior, separate from player tendency and skill.

    The baseline is an additive THREE-vs-MIDRANGE log weight. A future
    era adapter can vary it without rewriting player identity.
    """
    three_point_baseline_log_weight: float = 0.0


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


DRIVE_FOLLOWUP_LOG_WEIGHT: Dict[str, float] = {
    "CLEAN_PENETRATION": 3.2,
    "PARTIAL_EDGE": 1.8,
    "CONTAINED": 0.6,
    "FORCED_PICKUP": 0.0,  # dead dribble -- DRIVE/ISOLATION_ATTACK/PULL_UP are never live-dribble-gated
    # opportunities after this outcome anyway (`resolve_drive` calls `engine.dead_dribble()`), so a
    # shot-vs-pass reallocation would never even see a shot-side option to bias toward here.
}
# "Calibrate drive follow-up decisions" phase -- MODEL-IMPOSED structure, not an observed within-
# outcome split (no public per-outcome drive->shot/pass rate exists; see module docstring for the
# aggregate constraint this satisfies). Preserves the required monotonic ordering
# (CLEAN_PENETRATION > PARTIAL_EDGE > CONTAINED > FORCED_PICKUP, by construction of the table
# itself). CALIBRATED, as a SET (one shared scale factor over a fixed 3.2:1.8:0.6 ratio -- not each
# value fit independently, which this project has no real per-outcome data to do honestly), against
# the real AGGREGATE 2025-26 drive->FGA (~41.0%) rate: a small scale grid on TRAIN seeds
# 25000-25049 found scale=2.0 (over the same 1.6:0.9:0.3 base ratio) reaches drive->shot%=40.09 on
# TRAIN and 41.03 on HELDOUT seeds 25050-25099, with pace/FGA/3PA STABLE or slightly IMPROVED (not
# regressed) at every scale tested -- unlike `drive_selection_log_weight`, this lever does not trade
# off against pace, because it only reallocates the SAME already-live decision between an existing
# shot vs. an existing pass action, it does not change how many decisions a possession takes.


def _score_action(action_type: ActionType, role: RoleContext, tendency: TendencyContext,
                   drive_selection_log_weight: float = 0.0,
                   post_drive_outcome: Optional[str] = None) -> float:
    """One action's additive log-weight score -- every term below is
    independent and bounded; none is multiplied by another.

    `drive_selection_log_weight` ("Calibrate drive follow-up decisions" phase) is a real,
    STRUCTURAL environment/era prior -- same additive-log-weight-space convention as
    `ShotFamilySelectionContext.three_point_baseline_log_weight` -- NOT a player tendency (it is
    NEVER read from any `TendencyContext`/`drive_aggression`, which stays exactly what it always
    was: one specific player's own real relative drive preference). It exists because
    `BASE_WEIGHT` is a single flat, hand-set prior shared by every action type (this module's own
    documented limitation, Sec. "Base weights" above) -- direct measurement (see
    `docs/PROJECT_STATE.md`'s "Expand interior scoring opportunities"/"Calibrate drive follow-up
    decisions" phase notes) found DRIVE is selected far less often, PER LIVE-DRIBBLE OPPORTUNITY,
    than real 2025-26 drives-per-100-possessions tracking implies -- a genuine selection-level gap,
    not a pace artifact (drives/100 stayed low even after pace normalized close to the real
    reference). Defaults to 0.0 (no effect, byte-for-byte identical to this term not existing)
    until a caller opts in via `PossessionConfig.drive_selection_log_weight`."""
    score = BASE_WEIGHT
    if action_type == ActionType.DRIVE:
        score += drive_selection_log_weight

    if post_drive_outcome is not None:
        bias = DRIVE_FOLLOWUP_LOG_WEIGHT.get(post_drive_outcome, 0.0)
        # SAME additive, opposite-sign-for-shot-vs-pass convention `tendency.pass_vs_shoot` already
        # uses below -- a real DRIVE outcome shifting THIS ONE decision's shot/pass balance, kept
        # fully independent of (added on top of, never multiplying) the player's own tendency term.
        if action_type in SHOT_ACTIONS:
            score += bias
        elif action_type in PASS_ACTIONS:
            score -= bias

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


CATCH_AND_SHOOT_THREE_BASELINE_LOG_WEIGHT = 0.7
# "Model action-specific jump-shot selection" phase -- CATCH_AND_SHOOT's OWN structural prior,
# separate from `PULL_UP_THREE_BASELINE_LOG_WEIGHT`/`context.three_point_baseline_log_weight`
# below. ROOT CAUSE this phase fixed (see docs): both CATCH_AND_SHOOT and PULL_UP previously
# shared ONE generic {current-zone, MIDRANGE} candidate set and one shared, NEGATIVE
# `three_point_baseline_log_weight` (-0.2) -- meaning a league-average-tendency player's
# catch-and-shoot from the perimeter scored WORSE for a three than for a midrange (0.0 for
# MIDRANGE's un-weighted score vs -0.2+deviation for the perimeter zone), even though a catch-
# and-shoot's real basketball meaning ("receive the ball in a shooting-ready context and
# immediately shoot") is overwhelmingly a spot-up three in a perimeter location in the modern
# game. This is a REAL, STRUCTURAL prior (this action's own basketball semantics), not a player
# tendency and not a shooting-ABILITY input -- `tendency.three_point_preference` is still added on
# top, so player differentiation is preserved (see tests F/H). CALIBRATED (a joint grid with
# `PULLUP_THREE_BASELINE_LOG_WEIGHT` below, TRAIN seeds 25000-25049, validated on HELDOUT
# 25050-25099) as the SMALLEST joint pair landing overall THREE share close to the real 2025-26
# reference (~41.2%: TRAIN 41.2%, HELDOUT 40.6%) while reducing MIDRANGE materially (47.5% -> ~43-44%
# overall) -- NOT chasing MIDRANGE dramatically lower here, because with RIM+FLOATER's combined
# share still ~15% at this point (unchanged by this phase), any weight aggressive enough to push
# MIDRANGE below ~30% overall pushes THREE well past 50% (measured directly: e.g.
# catch=1.2/pullup=0.3 reaches THREE=46.1%/MIDRANGE=38.5%, catch=0.9/pullup=1.5 reaches
# THREE=56.2%/MIDRANGE=28.2%) -- exactly the "3PT share explodes above plausible modern range"
# regression this task explicitly rejects. Closing the rest of the MIDRANGE gap honestly requires
# RIM/FLOATER's own combined share to rise (a separate, later phase), not a further push on this
# knob alone.
PULLUP_THREE_BASELINE_LOG_WEIGHT = 0.2
# PULL_UP's own baseline -- a pull-up jumper is a genuinely different, more contested shot context
# than a catch-and-shoot; real NBA offense still runs meaningful pull-up-midrange volume (unlike a
# spot-up catch), so this stays far closer to neutral than CATCH_AND_SHOOT's own weight above,
# preserving a real, still-live competition between pull-up three and pull-up midrange rather than
# defaulting either family. `context.three_point_baseline_log_weight` (-0.2, UNCHANGED,
# `PossessionConfig.three_point_family_log_weight`) is NOT replaced -- it is still added on top,
# ADDITIVELY, as the era/environment adjustment it always was; this constant is the NEW, separate
# action-specific term layered alongside it, not instead of it.
MIDRANGE_PREFERENCE_WEIGHT = 1.0
# ACTIVATED this phase -- `tendency.midrange_preference`'s first-ever read anywhere in this
# codebase (previously "deliberately excluded" -- see this function's OLD docstring, preserved
# below in the module history). Applied ONLY to the MIDRANGE candidate's own score, and ONLY in a
# context where MIDRANGE is already a live candidate (PULL_UP's {perimeter-zone, MIDRANGE} or
# {TOP_OF_KEY, MIDRANGE} menu, or CATCH_AND_SHOOT's identical menu) -- never a standalone "global
# probability of a midrange shot" term, per the task's own explicit constraint. A player with a
# real, elevated midrange_preference now measurably shifts THIS competition toward midrange more
# than a league-average player would, without ever fabricating a midrange opportunity from a state
# where none of the existing structural gates (`_shot_zone_options`) would have offered one at all.
LATE_CLOCK_MIDRANGE_LOG_WEIGHT = 1.2
LATE_CLOCK_SHOT_CLOCK_THRESHOLD_SECONDS = 6.0
# ACTIVATED this phase -- `ClockContext.shot_clock_remaining` is REAL, already-reliable live state
# (already consulted by `evaluate_clock_feasibility`'s own late-clock gate; not fabricated here).
# A genuinely late shot clock is a REAL, well-known basketball reason a difficult pull-up/midrange
# attempt becomes more likely than it would be with a full shot clock -- this is a LEGITIMATE
# additional MIDRANGE source, not the false generic default this phase removes elsewhere. Applied
# identically to CATCH_AND_SHOOT and PULL_UP (a late-clock catch-and-shoot forced attempt is just as real
# as a late-clock pull-up) -- both still read the SAME real `TendencyContext`/family-eligibility
# gates on top, this is purely an ADDITIVE context term.


def shot_zone_probabilities(action_type: ActionType, options: Tuple[SpatialZone, ...],
                            tendency: TendencyContext, context: ShotFamilySelectionContext,
                            shot_clock_remaining: Optional[float] = None) -> List[float]:
    """Return the hierarchical family-choice distribution for one shot action.

    ACTION-SPECIFIC by construction ("Model action-specific jump-shot selection" phase) --
    CATCH_AND_SHOOT and PULL_UP no longer share one generic perimeter-vs-MIDRANGE weight (see
    `CATCH_AND_SHOOT_THREE_BASELINE_LOG_WEIGHT`'s own docstring for the full root-cause history).
    `three_point_preference` is already a logit-relative-to-league-average player deviation for
    real 3PA/FGA, so its natural coefficient here is 1, applied identically for both actions (a
    player's real 3-point shot-selection preference does not change basketball meaning by action
    type). `midrange_preference` is now READ (see `MIDRANGE_PREFERENCE_WEIGHT`'s own docstring) --
    still only within this already-eligible two-way (or three-way, for a genuine interior PULL_UP
    menu) competition, never as a standalone global term."""
    scores = []
    player_three_deviation = tendency.three_point_preference or 0.0
    player_midrange_deviation = tendency.midrange_preference or 0.0
    late_clock = shot_clock_remaining is not None and shot_clock_remaining <= LATE_CLOCK_SHOT_CLOCK_THRESHOLD_SECONDS
    if action_type == ActionType.CATCH_AND_SHOOT:
        action_prior = CATCH_AND_SHOOT_THREE_BASELINE_LOG_WEIGHT
    elif action_type == ActionType.PULL_UP:
        action_prior = PULLUP_THREE_BASELINE_LOG_WEIGHT
    else:
        action_prior = 0.0  # unreached in production (SHOT_ACTIONS = {PULL_UP, CATCH_AND_SHOOT}
        # only) -- kept so a direct/test caller with any other action_type falls back to EXACTLY
        # the old, pre-this-phase behavior (`context.three_point_baseline_log_weight` alone).
    for zone in options:
        score = 0.0
        if zone in PERIMETER_ZONES or zone == SpatialZone.BACKCOURT:
            score += context.three_point_baseline_log_weight + action_prior + player_three_deviation
        elif zone in MIDRANGE_ZONES:
            score += MIDRANGE_PREFERENCE_WEIGHT * player_midrange_deviation
            if late_clock:
                score += LATE_CLOCK_MIDRANGE_LOG_WEIGHT
        scores.append(score)
    return _softmax(scores)


def _select_shot_zone(action_type: ActionType, default_zone: Optional[SpatialZone],
                       shot_zone_options: Tuple[SpatialZone, ...], tendency: TendencyContext,
                       context: ShotFamilySelectionContext, rng: random.Random,
                       shot_clock_remaining: Optional[float] = None) -> Optional[SpatialZone]:
    """Hierarchical family choice after action selection, before resolution."""
    if action_type not in SHOT_ACTIONS or default_zone is None:
        return default_zone
    options = shot_zone_options or (default_zone,)
    if len(options) == 1:
        return options[0]
    if any(zone not in PERIMETER_ZONES | MIDRANGE_ZONES | INTERIOR_ZONES
           and zone != SpatialZone.BACKCOURT for zone in options):
        return default_zone
    probabilities = shot_zone_probabilities(action_type, options, tendency, context, shot_clock_remaining)
    return options[_weighted_choice(rng, probabilities)]


class SelectionPolicy:
    """Stateless scoring + stochastic sampling. `rng` is a caller-
    supplied `random.Random` (never the global `random` module) so
    selection is deterministically replayable under a fixed seed,
    matching Phase 15's own convention."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    def select(self, perceived: List[PerceivedOpportunity], role: RoleContext, tendency: TendencyContext,
               clock: ClockContext, possession_id: str,
               shot_family_context: Optional[ShotFamilySelectionContext] = None,
               drive_selection_log_weight: float = 0.0,
               post_drive_outcome: Optional[str] = None) -> Optional[ActionIntent]:
        """Returns None only when the perceived menu is genuinely empty
        after clock-feasibility filtering (e.g. a LOOSE-ball state with
        no recovery opportunities, or every remaining option infeasible)
        -- never a fabricated default action."""
        feasible = list(evaluate_clock_feasibility(perceived, clock).feasible)
        if not feasible:
            return None

        scores = [_score_action(p.opportunity.action_type, role, tendency, drive_selection_log_weight,
                                 post_drive_outcome)
                  for p in feasible]
        probabilities = _softmax(scores)
        chosen_index = _weighted_choice(self.rng, probabilities)
        chosen = feasible[chosen_index]
        opp = chosen.opportunity

        family_context = shot_family_context or ShotFamilySelectionContext()
        target_zone = _select_shot_zone(
            opp.action_type, opp.target_zone, opp.shot_zone_options, tendency, family_context, self.rng,
            shot_clock_remaining=clock.shot_clock_remaining,
        )
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
