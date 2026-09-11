"""
Pace / FGA Root-Cause Diagnostic (observational only).

============================ DOCTRINE FIREWALL ============================
Same posture as `detailed_engine_diagnostics.py` and `detailed_engine_benchmark.py`
(see those modules' own docstrings): this module OBSERVES already-structured,
already-produced `PossessionRecord`/`PossessionWorld` state. It NEVER makes a
simulation decision, never consumes RNG, never mutates simulation state, and
is never imported by `possession_orchestrator.py`, `detailed_game_orchestrator.py`,
or `detailed_game.py`. It exists to explain WHY the engine currently produces
too many alternating possessions/game and too much FGA/team-game -- it does
not change anything to fix that.

Every field this module reports reads a value `possession_orchestrator.py`'s
own dispatch code ALREADY computed and logged into one of these diagnostic-only
structures (verified by direct source read, same methodology
`detailed_engine_diagnostics.py`'s own docstring already established):
  - `PossessionRecord.restart_context` -- how THIS possession began
    (`RestartType.DEAD_BALL_INBOUND`/`LIVE_TRANSITION`, `PossessionChangeSource`).
  - `world.stage_timing_log` -- entry/second-chance timing charges.
  - `world.inter_action_log` -- inter-action timing charges.
  - `world.clock_charge_log` -- the COMPLETE clock-charge ledger, one entry per
    real clock mutation, tagged by `timing_category`
    (HALFCOURT_ENTRY/TRANSITION_ENTRY/SECOND_CHANCE_RESET/INTER_ACTION/
    PASS_FLIGHT/DRIVE_EXECUTION/SHOT_EXECUTION/LOOSE_BALL_RECOVERY).
  - `world.decision_log` -- the exact perceived/feasible menu at every
    perception/selection decision boundary, including late-clock-filter
    activation.
  - `world.action_log` -- one entry per real dispatched `ActionIntent`.
  - `world.shot_attempt_log` -- one entry per real FGA.
  - `world.shot_clock_violation_log` -- one causal summary per terminal
    SHOT_CLOCK_VIOLATION.
  - `world.rebound_opportunity_log` -- one entry per real rebound opportunity,
    including its `source` (`ReboundSource.MISSED_FG`/`UNRESOLVED_BLOCK`/
    `FINAL_MISSED_FT`).
No new logging hook is added to `possession_orchestrator.py` by this module --
every one of the structures above already existed before this diagnostic task.
"""
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from detailed_engine_benchmark import BenchmarkGame, run_benchmark_sample
from detailed_engine_diagnostics import SHOT_CLOCK_BINS, _classify_turnover_subtype, _shot_clock_bin
from detailed_game import DetailedGameConfig
from detailed_game_orchestrator import PossessionRecord, RestartType
from possession_orchestrator import PossessionTerminalReason

# ---------------------------------------------------------------------
# 1. Per-possession extraction -- pure re-attribution, no new computation
# beyond arithmetic already implied by the logged values.
# ---------------------------------------------------------------------
DURATION_BUCKETS: Tuple[Tuple[str, float, float], ...] = (
    ("<3s", 0.0, 3.0),
    ("3-5s", 3.0, 5.0),
    ("5-8s", 5.0, 8.0),
    ("8-10s", 8.0, 10.0),
    ("10-12s", 10.0, 12.0),
    ("12-15s", 12.0, 15.0),
    ("15-18s", 15.0, 18.0),
    ("18-21s", 18.0, 21.0),
    ("21-24s", 21.0, 24.0),
    (">=24s", 24.0, float("inf")),
)


def _duration_bucket(seconds: float) -> str:
    for label, low, high in DURATION_BUCKETS:
        if (low <= seconds < high) or (high == float("inf") and seconds >= low):
            return label
    return "OUT_OF_RANGE"  # should never occur -- elapsed is always >= 0


CLOCK_CATEGORIES: Tuple[str, ...] = (
    "HALFCOURT_ENTRY", "TRANSITION_ENTRY", "SECOND_CHANCE_RESET", "INTER_ACTION",
    "PASS_FLIGHT", "DRIVE_EXECUTION", "SHOT_EXECUTION", "LOOSE_BALL_RECOVERY",
)


@dataclass
class PossessionRow:
    """One possession's diagnostic row -- every field is read straight off an
    already-existing structure, never re-derived from prose/strings."""
    game_seed: int
    sequence_index: int  # 0-based order within the game (across periods)
    possession_id: str
    offense_team_id: str
    start_game_clock: float
    end_game_clock: float
    elapsed_seconds: float
    restart_type: str            # RestartType.DEAD_BALL_INBOUND | LIVE_TRANSITION | "PERIOD_OPENER"
    restart_source: str          # PossessionChangeSource value, or "PERIOD_OPENER" for period 1/OT openers
    initial_stage: str           # stage_timing_log[0]["stage"], or "UNKNOWN" if the entry charge alone exhausted the clock's own already-zero remainder (never happens with a positive placeholder, but defensive)
    terminal_reason: str
    terminal_subtype: Optional[str]  # turnover subtype / rebound-source subtype, see _terminal_subtype
    action_count: int
    action_types: Tuple[str, ...]
    fga_count: int
    fga_made_count: int
    oreb_count: int
    had_late_clock_filter: bool
    first_decision_shot_clock: Optional[float]
    terminal_shot_clock_remaining: Optional[float]
    clock_by_category: Dict[str, float]  # timing_category -> summed actual_elapsed_seconds this possession


def _rebound_terminal_subtype(record: PossessionRecord) -> Optional[str]:
    world = record.terminal_result.world
    if not world.rebound_opportunity_log:
        return None
    last = world.rebound_opportunity_log[-1]
    source = last.get("source")
    if source == "FINAL_MISSED_FT":
        return "DEFENSIVE_REBOUND_AFTER_FINAL_MISSED_FT"
    if source == "UNRESOLVED_BLOCK":
        return "DEFENSIVE_REBOUND_AFTER_BLOCKED_FG"
    return "DEFENSIVE_REBOUND_AFTER_MISSED_FG"


def _terminal_subtype(record: PossessionRecord) -> Optional[str]:
    reason = record.terminal_result.reason
    if reason == PossessionTerminalReason.DEFENSIVE_REBOUND:
        return _rebound_terminal_subtype(record)
    if reason in (PossessionTerminalReason.TURNOVER, PossessionTerminalReason.OFFENSIVE_FOUL_TURNOVER):
        return _classify_turnover_subtype(record)
    return None


def _clock_by_category(world) -> Dict[str, float]:
    totals: Dict[str, float] = {c: 0.0 for c in CLOCK_CATEGORIES}
    for entry in world.clock_charge_log:
        category = entry.get("timing_category")
        if category not in totals:
            totals[category] = 0.0
        actual = entry.get("actual_elapsed_seconds")
        totals[category] += actual if actual is not None else (entry.get("elapsed_game_clock_seconds") or 0.0)
    return totals


def possession_row(record: PossessionRecord, game_seed: int, sequence_index: int,
                    is_period_opener: bool) -> PossessionRow:
    world = record.terminal_result.world
    stage_log = world.stage_timing_log
    initial_stage = stage_log[0]["stage"] if stage_log else "UNKNOWN"
    fga_entries = world.shot_attempt_log
    decision_log = world.decision_log

    if is_period_opener:
        restart_type, restart_source = "PERIOD_OPENER", "PERIOD_OPENER"
    else:
        restart_type = record.restart_context.restart_type
        restart_source = record.restart_context.source

    return PossessionRow(
        game_seed=game_seed, sequence_index=sequence_index, possession_id=record.possession_id,
        offense_team_id=record.offense_team_id,
        start_game_clock=record.start_game_clock, end_game_clock=record.end_game_clock,
        elapsed_seconds=record.start_game_clock - record.end_game_clock,
        restart_type=restart_type, restart_source=restart_source, initial_stage=initial_stage,
        terminal_reason=record.terminal_result.reason, terminal_subtype=_terminal_subtype(record),
        action_count=len(world.action_log),
        action_types=tuple(a["action_type"] for a in world.action_log),
        fga_count=len(fga_entries), fga_made_count=sum(1 for e in fga_entries if e.get("made")),
        oreb_count=record.provisional_deltas.oreb,
        had_late_clock_filter=any(d.get("late_clock_filter_activated") for d in decision_log),
        first_decision_shot_clock=(decision_log[0]["shot_clock_remaining"] if decision_log else None),
        terminal_shot_clock_remaining=record.terminal_result.engine_state.shot_clock_remaining,
        clock_by_category=_clock_by_category(world),
    )


def _is_period_opener(record: PossessionRecord, index_in_period: int) -> bool:
    """The FIRST possession of a period, for EITHER team, is a period
    opener -- `next_restart_context` returns `None` at a period boundary
    (see `detailed_game_orchestrator.py`'s own docstring), so the next
    possession's `restart_context` is freshly constructed by
    `initialize_next_possession` from the period's own deterministic
    opener policy, not from the PRIOR possession's terminal outcome.
    Identified structurally (first possession within its period's own
    sequence), never by string-matching a prose reason."""
    return index_in_period == 0


def game_possession_rows(game: BenchmarkGame) -> List[PossessionRow]:
    """One row per possession, in real game order, across all periods."""
    rows: List[PossessionRow] = []
    period_ranges = {p.period: (p.first_possession_sequence, p.last_possession_sequence)
                      for p in game.result.periods}
    for idx, record in enumerate(game.result.possessions):
        # possession_id format is opaque; use the PeriodRecord's own sequence range
        # (already-computed by detailed_game_orchestrator.py) to identify period openers
        # structurally rather than parsing possession_id text.
        period = next((p for p, (lo, hi) in period_ranges.items() if lo <= idx + 1 <= hi), None)
        is_opener = period is not None and period_ranges[period][0] == idx + 1
        rows.append(possession_row(record, game.seed, idx, is_opener))
    return rows


def all_possession_rows(games: Sequence[BenchmarkGame]) -> List[PossessionRow]:
    rows: List[PossessionRow] = []
    for g in games:
        rows.extend(game_possession_rows(g))
    return rows


# ---------------------------------------------------------------------
# 2. Percentile/distribution helper (same nearest-rank convention already
# used by `detailed_engine_benchmark.distribution`; duplicated here in a
# minimal float-only form to avoid importing a dataclass shape that
# doesn't match this module's own reporting needs -- reuse the same
# ALGORITHM, not a second one).
# ---------------------------------------------------------------------
def percentiles(values: Sequence[float], pcts: Sequence[float]) -> Dict[float, float]:
    if not values:
        raise ValueError("cannot compute percentiles of an empty sequence")
    s = sorted(values)
    out = {}
    for pct in pcts:
        idx = min(len(s) - 1, max(0, int(round(pct * (len(s) - 1)))))
        out[pct] = s[idx]
    return out


# ---------------------------------------------------------------------
# 3. Reconciliation checks -- pure arithmetic over already-logged values,
# used both by this module's own report-building AND by the focused test
# suite (`test_detailed_engine_pace_diagnosis.py`) to PROVE the ledgers
# are self-consistent, never to change behavior.
# ---------------------------------------------------------------------
def clock_ledger_reconciliation_gap(row: PossessionRow) -> float:
    """`elapsed_seconds - sum(clock_by_category.values())` -- should be
    ~0.0 (floating-point tolerance) for every possession if every real
    clock decrement is captured by exactly one `clock_charge_log` entry.
    A nonzero gap here is a genuine accounting finding, not a test bug."""
    return row.elapsed_seconds - sum(row.clock_by_category.values())


def true_possession_count(games: Sequence[BenchmarkGame]) -> int:
    """TRUE alternating possessions across the whole sample -- literally
    `len(result.possessions)` summed, the same count
    `DetailedGameResult.total_possessions`/`detailed_engine_benchmark.TeamGameStats.true_possessions`
    already use. Duplicated here as a one-line named function so the
    diagnostic report has one obvious place documenting WHICH possession
    definition it is using (Task 1's own semantic-audit requirement)."""
    return sum(g.result.total_possessions for g in games)


def run_pace_diagnosis_sample(seeds: Sequence[int],
                              config: Optional[DetailedGameConfig] = None) -> Tuple[BenchmarkGame, ...]:
    """Thin, intentional re-export of `run_benchmark_sample` -- the pace
    diagnostic MUST use the exact same execution path as the benchmark
    (same profiles, same default config) so its numbers are directly
    comparable to `docs/DETAILED_ENGINE_FIRST_100_GAME_BENCHMARK.md`,
    never a second, subtly-different runner."""
    return run_benchmark_sample(seeds=seeds, config=config)
