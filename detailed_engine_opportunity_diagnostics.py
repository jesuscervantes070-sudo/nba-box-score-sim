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

    # ---- G. drive funnel ----
    drives_selected: int = 0
    drive_outcomes: CounterType[str] = field(default_factory=Counter)
    drive_floor_foul_checks: int = 0
    drive_then_next_action: CounterType[str] = field(default_factory=Counter)  # "<possession_ended>" is a real sentinel, not a family
    drive_then_shot_family: CounterType[str] = field(default_factory=Counter)

    # ---- I/J/K/L. shot-family selection ----
    shot_family_totals: CounterType[str] = field(default_factory=Counter)
    shot_family_by_origin_action: Dict[str, CounterType[str]] = field(default_factory=lambda: defaultdict(Counter))

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

            actions_list = world.action_log
            for i, action in enumerate(actions_list):
                at = action["action_type"]
                diag.selected_actions_by_type[at] += 1

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
    if diag.one_action_possessions != sum(diag.one_action_by_source.values()):
        raise AssertionError("one_action_by_source does not sum to one_action_possessions")
