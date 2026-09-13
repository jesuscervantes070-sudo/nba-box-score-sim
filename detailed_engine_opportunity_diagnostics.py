"""Read-only detailed-engine OPPORTUNITY-GENERATION diagnostics.

Companion to `detailed_engine_foul_diagnostics.py` / `detailed_engine_block_diagnostics.py`
(same posture): this module consumes completed `DetailedGameResult` objects. It does not
simulate, mutate state, consume RNG, or supply any value to production code.

============================ WHY THIS MODULE EXISTS ============================
"Diagnose shot-opportunity generation": this repo's detailed engine produces a catastrophically
unrealistic shot-family distribution (canonical 100-game sample: ~12,900 MIDRANGE / ~10,400
THREE_POINT / ~690 FLOATER / ~400 RIM attempts) and an inflated pace (~123 possessions/team vs a
real ~99 NBA reference). This module instruments the CAUSAL CHAIN from possession start through
terminal shot family -- possession source -> restart/transition classification -> objective
opportunities -> selected action -> (for a DRIVE) outcome/next-action -> shot family -- so the
exact point(s) where the chain becomes unrealistic are directly measurable, not merely asserted.

============================ THE HEADLINE FINDING THIS MODULE MAKES MEASURABLE ============================
`action_opportunity.py`'s own `_shot_zone_options()` (inside `generate_opportunities`) NEVER
offers `RESTRICTED_RIM`/`PAINT` as a shot-zone candidate for an ordinary `PULL_UP`/
`CATCH_AND_SHOOT` opportunity generated from a perimeter or midrange ball position -- its own
docstring says so explicitly: "Interior access remains owned by drive geometry." An interior shot
can therefore ONLY happen when `state.ball_zone` already equals `RESTRICTED_RIM`/`PAINT` at the
moment the opportunity is generated -- which only happens via a `DRIVE` whose
`drive_resolution.py`-sampled outcome was `CLEAN_PENETRATION`/`PARTIAL_EDGE` (the only two
outcomes that move `ball_zone` forward at all) AND whose zone-advance roll landed on that
specific interior zone, AND the VERY NEXT selected action is a shot (not a pass or another
drive). `shot_family_by_origin_action`/`drive_then_next_action`/`drive_then_shot_family` below
make every stage of that conjunction directly countable.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Counter as CounterType, Dict, List, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from detailed_game import DetailedGameResult

SHOT_ACTIONS = frozenset({"PULL_UP", "CATCH_AND_SHOOT"})
# "Model action-specific jump-shot selection" phase -- the "originating context" labels
# `shot_family_by_originating_context` recognizes as something OTHER than "ORDINARY" (see that
# field's own docstring).
_ORIGINATING_CONTEXT_ACTIONS = frozenset({"DRIVE", "TRANSITION_PUSH", "INTERIOR_CUT", "INTERIOR_SEAL"})
_INTERIOR_PASS_ORIGINS = frozenset({"TRANSITION_PUSH", "INTERIOR_CUT", "INTERIOR_SEAL"})


@dataclass
class InteriorOriginFunnel:
    """Zero-RNG audit of one named pass-created interior path."""
    opportunities_offered: int = 0
    selected: int = 0
    pass_attempts: int = 0
    successful_passes: int = 0
    turnovers: int = 0
    rim_shots: int = 0
    floater_shots: int = 0
    midrange_shots: int = 0
    three_shots: int = 0
    fouls: int = 0


@dataclass
class ScreenOriginFunnel:
    """Zero-RNG audit of screen initiation, its one live response, and resulting release."""
    opportunities_offered: int = 0
    selected: int = 0
    rollers_assigned: int = 0
    pocket_passes: int = 0
    drives: int = 0
    pull_ups: int = 0
    passes_or_resets: int = 0
    turnovers: int = 0
    successful_pocket_passes: int = 0
    paint_destinations: int = 0
    rim_destinations: int = 0
    rim_shots: int = 0
    floater_shots: int = 0
    midrange_shots: int = 0
    three_shots: int = 0
    fouls: int = 0
    invalid_active_states: int = 0
    stale_terminal_states: int = 0
    duplicate_screen_ids: int = 0
    missing_clear_events: int = 0
    # A real, legitimate edge case: the shot clock can expire WHILE `ON_BALL_SCREEN` itself is
    # being charged (`_dispatch_on_ball_screen`'s own `EXPIRED_BEFORE_SCREEN` branch) -- the action
    # is genuinely "selected" (it appears in `world.action_log`) but never creates real screen
    # state, so it is never eligible for `rollers_assigned`. Counted separately so
    # `rollers_assigned + expired_before_screen == selected` remains an exact invariant.
    expired_before_screen: int = 0


@dataclass
class OpportunityDiagnostics:
    total_possessions: int = 0

    # ---- B. possession-source distribution ----
    possessions_by_source: CounterType[str] = field(default_factory=Counter)
    possessions_by_restart_type: CounterType[str] = field(default_factory=Counter)
    duration_seconds_by_source: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    action_count_by_source: Dict[str, List[int]] = field(default_factory=lambda: defaultdict(list))
    fga_by_source: CounterType[str] = field(default_factory=Counter)
    tov_by_source: CounterType[str] = field(default_factory=Counter)

    # ---- E. opportunity -> action funnel ----
    objective_opportunities_by_action: CounterType[str] = field(default_factory=Counter)
    selected_actions_by_type: CounterType[str] = field(default_factory=Counter)
    interior_origin_funnels: Dict[str, InteriorOriginFunnel] = field(
        default_factory=lambda: defaultdict(InteriorOriginFunnel)
    )
    screen_origin_funnel: ScreenOriginFunnel = field(default_factory=ScreenOriginFunnel)

    # ---- G. drive funnel ----
    drives_selected: int = 0
    drive_outcomes: CounterType[str] = field(default_factory=Counter)
    drive_floor_foul_checks: int = 0
    drive_then_next_action: CounterType[str] = field(default_factory=Counter)  # "<possession_ended>" is a real sentinel, not a family
    drive_then_shot_family: CounterType[str] = field(default_factory=Counter)

    # ---- I/J/K/L. shot-family selection ----
    shot_family_totals: CounterType[str] = field(default_factory=Counter)
    shot_family_by_origin_action: Dict[str, CounterType[str]] = field(default_factory=lambda: defaultdict(Counter))
    # "Model action-specific jump-shot selection" phase -- PERMANENT extension: shot family keyed
    # by the ORIGINATING CONTEXT (the immediately preceding dispatched action in the SAME
    # possession, if any) rather than by the release action itself (`shot_family_by_origin_action`
    # above already covers the PULL_UP-vs-CATCH_AND_SHOOT split). Keys: "DRIVE", "TRANSITION_PUSH",
    # "INTERIOR_CUT", "ORDINARY" (no immediately-preceding action of interest -- the shot was the
    # possession's first action, or was preceded by an ordinary pass like SWING_PASS/RESET_PASS/
    # OUTLET_PASS). Answers "where are the remaining MIDRANGE attempts actually coming from?" --
    # zero RNG, purely a re-read of `world.action_log`'s already-recorded step order.
    shot_family_by_originating_context: Dict[str, CounterType[str]] = field(default_factory=lambda: defaultdict(Counter))

    # ---- N. one-action possessions ----
    one_action_possessions: int = 0
    one_action_by_source: CounterType[str] = field(default_factory=Counter)
    one_action_by_restart_type: CounterType[str] = field(default_factory=Counter)
    one_action_by_shot_family: CounterType[str] = field(default_factory=Counter)

    # ---- M. possession endings ----
    endings_with_fga: int = 0
    endings_shooting_foul_no_fga: int = 0
    endings_turnover: int = 0
    endings_offensive_foul: int = 0
    endings_shot_clock_violation: int = 0
    endings_period_end: int = 0
    endings_other: CounterType[str] = field(default_factory=Counter)

    @property
    def total_fga(self) -> int:
        return sum(self.fga_by_source.values())

    @property
    def total_tov(self) -> int:
        return sum(self.tov_by_source.values())

    def mean_duration_for(self, source: str) -> float:
        durs = self.duration_seconds_by_source.get(source, [])
        return sum(durs) / len(durs) if durs else float("nan")

    def mean_actions_for(self, source: str) -> float:
        counts = self.action_count_by_source.get(source, [])
        return sum(counts) / len(counts) if counts else float("nan")


def _shot_row_and_family(rows: Sequence[dict], action_type: str) -> Tuple[dict, str]:
    """Returns (row, family) for a dispatched shot action -- `family` comes from the
    `SHOOTING_FOUL` trace row when the attempt was whistled (it never reaches the ordinary
    shot-dispatch trace row at all, per `_dispatch_shot`'s own whistled-shot early return) or from
    the ordinary shot trace row otherwise. `(None, None)` if neither exists (should not happen for
    a real dispatched shot action; never silently guessed)."""
    shot_row = next((r for r in rows if r.get("action") == action_type and r.get("shot_family") is not None), None)
    foul_row = next((r for r in rows if r.get("action") == "SHOOTING_FOUL"), None)
    if foul_row is not None:
        return foul_row, foul_row.get("shot_family")
    if shot_row is not None:
        return shot_row, shot_row.get("shot_family")
    return None, None


def diagnose_opportunities(results: Sequence["DetailedGameResult"]) -> OpportunityDiagnostics:
    """Aggregate the complete possession-source/transition/opportunity/action/drive/shot-family
    funnel without re-simulation."""
    diag = OpportunityDiagnostics()

    for result in results:
        for record in result.possessions:
            diag.total_possessions += 1
            world = record.terminal_result.world
            restart = record.restart_context
            source, restart_type = restart.source, restart.restart_type
            deltas = record.provisional_deltas

            diag.possessions_by_source[source] += 1
            diag.possessions_by_restart_type[restart_type] += 1
            diag.duration_seconds_by_source[source].append(record.start_game_clock - record.end_game_clock)
            n_actions = len(world.action_log)
            diag.action_count_by_source[source].append(n_actions)
            diag.fga_by_source[source] += deltas.fga
            diag.tov_by_source[source] += deltas.turnovers

            if n_actions == 1:
                diag.one_action_possessions += 1
                diag.one_action_by_source[source] += 1
                diag.one_action_by_restart_type[restart_type] += 1

            for decision in world.decision_log:
                for opp in decision.get("objective_opportunities", ()):
                    diag.objective_opportunities_by_action[opp["action_type"]] += 1
                    if opp["action_type"] == "ON_BALL_SCREEN":
                        diag.screen_origin_funnel.opportunities_offered += 1
                    if opp["action_type"] in _INTERIOR_PASS_ORIGINS:
                        diag.interior_origin_funnels[opp["action_type"]].opportunities_offered += 1

            actions_list = world.action_log
            screen_traces = [r for r in world.trace if r.get("action") == "ON_BALL_SCREEN"
                             and r.get("screen_id") is not None]
            clear_traces = [r for r in world.trace if r.get("action") == "SCREEN_CONTEXT_CLEARED"]
            screen_ids = [r["screen_id"] for r in screen_traces]
            diag.screen_origin_funnel.duplicate_screen_ids += len(screen_ids) - len(set(screen_ids))
            clear_ids = [r.get("screen_id") for r in clear_traces]
            diag.screen_origin_funnel.missing_clear_events += sum(clear_ids.count(sid) != 1 for sid in screen_ids)
            if (world.screen_active or world.roller_id is not None or world.active_screen_id is not None
                    or world.screen_ball_handler_id is not None):
                diag.screen_origin_funnel.stale_terminal_states += 1
            for i, action in enumerate(actions_list):
                at = action["action_type"]
                diag.selected_actions_by_type[at] += 1

                if at == "ON_BALL_SCREEN":
                    funnel = diag.screen_origin_funnel
                    funnel.selected += 1
                    screen_id = action.get("screen_context_id")
                    activation = next((r for r in screen_traces if r.get("screen_id") == screen_id), None)
                    if activation is not None and activation.get("roller") in world.team_a_five:
                        funnel.rollers_assigned += 1
                    elif activation is not None:
                        funnel.invalid_active_states += 1
                    elif screen_id is None:
                        # the real EXPIRED_BEFORE_SCREEN edge case (see `ScreenOriginFunnel.expired_before_screen`'s
                        # own docstring) -- no screen state was ever created, so no roller assignment
                        # is expected or missing here.
                        funnel.expired_before_screen += 1
                    else:
                        funnel.invalid_active_states += 1  # a screen_id was recorded but its activation row is missing entirely
                    if i + 1 < len(actions_list):
                        response = actions_list[i + 1]
                        response_type = response["action_type"]
                        if not response.get("screen_active_before_action") or response.get("screen_context_id") != screen_id:
                            funnel.invalid_active_states += 1
                        if response_type == "POCKET_PASS":
                            funnel.pocket_passes += 1
                            rows = [r for r in world.trace if r.get("step") == response["step"]]
                            pass_row = next((r for r in rows if r.get("action") == "POCKET_PASS"), None)
                            if pass_row is not None and pass_row.get("outcome") in ("COMPLETED_CLEAN", "COMPLETED_ADJUSTED"):
                                funnel.successful_pocket_passes += 1
                                if pass_row.get("zone") == "PAINT":
                                    funnel.paint_destinations += 1
                                elif pass_row.get("zone") == "RESTRICTED_RIM":
                                    funnel.rim_destinations += 1
                        elif response_type == "DRIVE":
                            funnel.drives += 1
                        elif response_type == "PULL_UP":
                            funnel.pull_ups += 1
                        elif response_type in ("SWING_PASS", "RESET_PASS", "KICKOUT", "INTERIOR_CUT", "INTERIOR_SEAL"):
                            funnel.passes_or_resets += 1
                        if (i + 1 == len(actions_list) - 1
                                and record.terminal_result.reason in ("TURNOVER", "OFFENSIVE_FOUL_TURNOVER",
                                                                      "SHOT_CLOCK_VIOLATION")):
                            funnel.turnovers += 1

                    # A screen-origin release is either the immediate ball-handler shot or the
                    # immediate shot after the one screen response (DRIVE or successful POCKET_PASS).
                    shot_index = None
                    if i + 1 < len(actions_list) and actions_list[i + 1]["action_type"] in SHOT_ACTIONS:
                        shot_index = i + 1
                    elif (i + 2 < len(actions_list)
                          and actions_list[i + 1]["action_type"] in ("DRIVE", "POCKET_PASS")
                          and actions_list[i + 2]["action_type"] in SHOT_ACTIONS):
                        shot_index = i + 2
                    if shot_index is not None:
                        shot_action = actions_list[shot_index]
                        rows = [r for r in world.trace if r.get("step") == shot_action["step"]]
                        shot_row, family = _shot_row_and_family(rows, shot_action["action_type"])
                        if family == "RIM": funnel.rim_shots += 1
                        elif family == "FLOATER": funnel.floater_shots += 1
                        elif family == "MIDRANGE": funnel.midrange_shots += 1
                        elif family == "THREE_POINT": funnel.three_shots += 1
                        if shot_row is not None and shot_row.get("action") == "SHOOTING_FOUL":
                            funnel.fouls += 1

                if at in _INTERIOR_PASS_ORIGINS:
                    funnel = diag.interior_origin_funnels[at]
                    funnel.selected += 1
                    funnel.pass_attempts += 1
                    rows = [r for r in world.trace if r.get("step") == action["step"]]
                    pass_row = next((r for r in rows if r.get("action") == at), None)
                    outcome = pass_row.get("outcome") if pass_row is not None else None
                    if outcome in ("COMPLETED_CLEAN", "COMPLETED_ADJUSTED"):
                        funnel.successful_passes += 1
                    elif outcome in ("CLEAN_INTERCEPTION", "BAD_PASS_OUT_OF_BOUNDS", "BAD_PASS_TO_DEFENDER"):
                        funnel.turnovers += 1
                    if i + 1 < len(actions_list) and actions_list[i + 1]["action_type"] in SHOT_ACTIONS:
                        nxt = actions_list[i + 1]
                        nxt_rows = [r for r in world.trace if r.get("step") == nxt["step"]]
                        shot_row, family = _shot_row_and_family(nxt_rows, nxt["action_type"])
                        if family == "RIM":
                            funnel.rim_shots += 1
                        elif family == "FLOATER":
                            funnel.floater_shots += 1
                        elif family == "MIDRANGE":
                            funnel.midrange_shots += 1
                        elif family == "THREE_POINT":
                            funnel.three_shots += 1
                        if shot_row is not None and shot_row.get("action") == "SHOOTING_FOUL":
                            funnel.fouls += 1

                if at == "DRIVE":
                    diag.drives_selected += 1
                    rows = [r for r in world.trace if r.get("step") == action["step"]]
                    drow = next((r for r in rows if r.get("action") == "DRIVE"), None)
                    if drow is not None:
                        diag.drive_outcomes[drow.get("outcome")] += 1
                    if any(r.get("action") == "DRIVE_FLOOR_FOUL_CHECK" for r in rows):
                        diag.drive_floor_foul_checks += 1
                    if i + 1 < len(actions_list):
                        nxt = actions_list[i + 1]
                        diag.drive_then_next_action[nxt["action_type"]] += 1
                        if nxt["action_type"] in SHOT_ACTIONS:
                            nxt_rows = [r for r in world.trace if r.get("step") == nxt["step"]]
                            _, family = _shot_row_and_family(nxt_rows, nxt["action_type"])
                            if family:
                                diag.drive_then_shot_family[family] += 1
                    else:
                        diag.drive_then_next_action["<possession_ended>"] += 1

                if at in SHOT_ACTIONS:
                    rows = [r for r in world.trace if r.get("step") == action["step"]]
                    _, family = _shot_row_and_family(rows, at)
                    if family:
                        diag.shot_family_totals[family] += 1
                        diag.shot_family_by_origin_action[at][family] += 1
                        if n_actions == 1:
                            diag.one_action_by_shot_family[family] += 1
                        preceding_type = actions_list[i - 1]["action_type"] if i > 0 else None
                        two_back = actions_list[i - 2]["action_type"] if i > 1 else None
                        if preceding_type == "POCKET_PASS" and two_back == "ON_BALL_SCREEN":
                            origin = "SCREEN_ROLL"
                        elif preceding_type == "ON_BALL_SCREEN" or (
                                preceding_type == "DRIVE" and two_back == "ON_BALL_SCREEN"):
                            origin = "SCREEN_BALL_HANDLER"
                        else:
                            origin = preceding_type if preceding_type in _ORIGINATING_CONTEXT_ACTIONS else "ORDINARY"
                        diag.shot_family_by_originating_context[origin][family] += 1

            reason = record.terminal_result.reason
            has_shooting_foul = any(r.get("action") == "SHOOTING_FOUL" for r in world.trace)
            if deltas.fga > 0:
                diag.endings_with_fga += 1
            elif has_shooting_foul:
                diag.endings_shooting_foul_no_fga += 1
            elif reason == "TURNOVER":
                diag.endings_turnover += 1
            elif reason == "OFFENSIVE_FOUL_TURNOVER":
                diag.endings_offensive_foul += 1
            elif reason == "SHOT_CLOCK_VIOLATION":
                diag.endings_shot_clock_violation += 1
            elif reason == "PERIOD_END":
                diag.endings_period_end += 1
            else:
                diag.endings_other[reason] += 1

    return diag


def assert_opportunity_reconciliation(diag: OpportunityDiagnostics) -> None:
    """Raise when the independently-tallied projections disagree."""
    if sum(diag.possessions_by_source.values()) != diag.total_possessions:
        raise AssertionError("possessions_by_source does not sum to total_possessions")
    if sum(diag.possessions_by_restart_type.values()) != diag.total_possessions:
        raise AssertionError("possessions_by_restart_type does not sum to total_possessions")
    endings_total = (diag.endings_with_fga + diag.endings_shooting_foul_no_fga + diag.endings_turnover
                      + diag.endings_offensive_foul + diag.endings_shot_clock_violation
                      + diag.endings_period_end + sum(diag.endings_other.values()))
    if endings_total != diag.total_possessions:
        raise AssertionError(f"ending-type counts ({endings_total}) do not sum to total_possessions ({diag.total_possessions})")
    if sum(diag.shot_family_totals.values()) != sum(c.total() for c in diag.shot_family_by_origin_action.values()):
        raise AssertionError("shot_family_totals does not match the sum of shot_family_by_origin_action")
    if sum(diag.shot_family_totals.values()) != sum(c.total() for c in diag.shot_family_by_originating_context.values()):
        raise AssertionError("shot_family_totals does not match the sum of shot_family_by_originating_context")
    if diag.one_action_possessions != sum(diag.one_action_by_source.values()):
        raise AssertionError("one_action_by_source does not sum to one_action_possessions")
    for origin, funnel in diag.interior_origin_funnels.items():
        if funnel.selected != funnel.pass_attempts:
            raise AssertionError(f"{origin} selected/pass-attempt counts do not reconcile")
        if funnel.selected != diag.selected_actions_by_type[origin]:
            raise AssertionError(f"{origin} funnel selection does not match selected-actions total")
        if funnel.opportunities_offered != diag.objective_opportunities_by_action[origin]:
            raise AssertionError(f"{origin} funnel opportunities do not match objective total")
    screen = diag.screen_origin_funnel
    if screen.selected != diag.selected_actions_by_type["ON_BALL_SCREEN"]:
        raise AssertionError("screen funnel selection does not match selected-actions total")
    if screen.opportunities_offered != diag.objective_opportunities_by_action["ON_BALL_SCREEN"]:
        raise AssertionError("screen funnel opportunities do not match objective total")
    if screen.rollers_assigned + screen.expired_before_screen != screen.selected:
        raise AssertionError("every selected screen must either assign one valid offensive roller "
                              "or be the real EXPIRED_BEFORE_SCREEN edge case")
    if screen.invalid_active_states or screen.stale_terminal_states or screen.duplicate_screen_ids or screen.missing_clear_events:
        raise AssertionError("screen-state lifecycle reconciliation failed")
