"""
Detailed-engine FIRST DIAGNOSTIC instrumentation (observability only).

============================ DOCTRINE FIREWALL ============================
This module OBSERVES already-structured, already-produced state/results/
events from Phases 15-23C. It NEVER makes a simulation decision, never
consumes RNG, never mutates `PossessionRecord`/`DetailedGameResult`/
`PossessionWorld`/`engine.state`, and is never imported by any resolver,
`possession_orchestrator.py`, `detailed_game_orchestrator.py`, or
`detailed_game.py`. It is a FOURTH, purely observational layer alongside
this project's existing three:
  LIVE STATE            -> `engine.state` / `PossessionWorld`
  EVENT STREAM          -> `engine.log.events` (accounting-truth direction)
  TYPED RESULTS         -> `PossessionTerminalResult` / `DetailedGameResult`
  DIAGNOSTIC TELEMETRY  -> THIS MODULE (observability only, never authority)

Every field this module reports is read from an EXISTING typed/structured
source: `PossessionRecord`'s own fields, `PossessionTerminalResult`'s own
fields, `record.events`' structured `event_type`/`metadata`, or
`PossessionWorld.trace`/`PossessionWorld.action_log` (both diagnostic
lists ALREADY populated by `possession_orchestrator.py`'s own dispatch
code with structured dict fields -- e.g. `outcome`, `shot_family`,
`eligible_count` -- never free prose). No shot type, turnover subtype, or
any other classification is ever inferred from a descriptive string; every
classification below reads a resolver's own already-established string
CONSTANT (`ShotOutcome.MADE`, `PassOutcome.BAD_PASS_TO_DEFENDER`,
`DriveOutcome.FORCED_PICKUP`, etc.) via structured dict/attribute access,
exactly the same methodology `derive_stat_deltas_from_events` already
uses in `possession_orchestrator.py`.

Nothing here calibrates, tunes, or recommends a parameter change -- it
only measures and reports.
"""
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from detailed_game_orchestrator import PossessionRecord, RestartType
from detailed_game import DetailedGameResult
from possession_events import EventType

# ---------------------------------------------------------------------
# 1. Per-possession telemetry.
# ---------------------------------------------------------------------

_SHOT_ACTION_TYPES = frozenset({"PULL_UP", "CATCH_AND_SHOOT"})
_PASS_ACTION_TYPES = frozenset({"SWING_PASS", "KICKOUT", "RESET_PASS", "POCKET_PASS", "OUTLET_PASS"})
_MISSED_OUTCOMES = frozenset({"MISSED", "MISSED_UNBLOCKED", "BLOCKED_RETAINED_OFFENSE", "BLOCKED_SECURED_DEFENSE"})
_MADE_OUTCOMES = frozenset({"MADE"})
_BLOCKED_OUTCOMES = frozenset({"BLOCKED_RETAINED_OFFENSE", "BLOCKED_SECURED_DEFENSE"})


@dataclass
class ActionTelemetry:
    """One action type's aggregate clock consumption within a scope
    (one possession or a whole game)."""
    action_type: str
    count: int = 0
    total_seconds: float = 0.0

    @property
    def mean_seconds(self) -> float:
        return self.total_seconds / self.count if self.count else 0.0

    def add(self, elapsed_seconds: Optional[float]) -> None:
        self.count += 1
        if elapsed_seconds is not None:
            self.total_seconds += elapsed_seconds


def _accumulate_action_telemetry(entries: Sequence[dict], into: Dict[str, ActionTelemetry], key: str = "action_type") -> None:
    """Generic accumulator over any diagnostic log whose entries carry a
    string key (`"action_type"` for `world.action_log`, `"stage"` for
    `world.stage_timing_log`) plus `"elapsed_game_clock_seconds"` --
    reused, not duplicated, for the Structural Timing Hook's own setup-
    stage telemetry (Sec. below)."""
    for entry in entries:
        name = entry.get(key)
        if name is None:
            continue
        bucket = into.setdefault(name, ActionTelemetry(action_type=name))
        bucket.add(entry.get("elapsed_game_clock_seconds"))


def _shot_trace_entries(trace: Sequence[dict]) -> List[dict]:
    return [e for e in trace if e.get("action") in _SHOT_ACTION_TYPES or e.get("action") == "SHOOTING_FOUL"]


def _rebound_trace_entries(trace: Sequence[dict]) -> List[dict]:
    return [e for e in trace if e.get("action") == "REBOUND_OPPORTUNITY"]


def _classify_turnover_subtype(record: PossessionRecord) -> Optional[str]:
    """Reads `record.terminal_result.reason` + the possession's own
    structured events -- never a string-parsed inference. Returns
    `None` when the possession did not end in a turnover at all."""
    reason = record.terminal_result.reason
    if reason == "OFFENSIVE_FOUL_TURNOVER":
        return "OFFENSIVE_CHARGE"
    if reason != "TURNOVER":
        return None
    for event in reversed(record.events):
        if event.event_type == EventType.PASS_RESOLVED:
            outcome = event.metadata.get("outcome")
            if outcome == "BAD_PASS_OUT_OF_BOUNDS":
                return "BAD_PASS_OUT_OF_BOUNDS"
            if outcome == "BAD_PASS_TO_DEFENDER" and event.metadata.get("disrupting_defender_id") is not None:
                return "BAD_PASS_TO_DEFENDER"
            if outcome == "CLEAN_INTERCEPTION":
                return "CLEAN_INTERCEPTION"
        elif event.event_type == EventType.REACTION_CHECKPOINT:
            if event.metadata.get("checkpoint") == "generic_loose_ball_recovered" \
                    and event.metadata.get("recovery") == "DEFENSE_RECOVERED":
                return "LOOSE_BALL_DEFENSE_RECOVERED"
        elif event.event_type == EventType.DEAD_BALL_TURNOVER:
            return "OTHER_DEAD_BALL_TURNOVER"
        elif event.event_type == EventType.LIVE_BALL_TURNOVER:
            return "OTHER_LIVE_BALL_TURNOVER"
    return "OTHER_TURNOVER"  # a real, structurally-reached TURNOVER with no recognized subtype signal


@dataclass
class PossessionDiagnostics:
    possession_id: str
    offense_team_id: str
    start_game_clock: float
    end_game_clock: float
    elapsed_game_clock_seconds: float
    step_count: int              # `PossessionTerminalResult.steps_taken` -- loop iterations, INCLUDES top-of-loop
                                  # clock checks and LOOSE-ball iterations that are NOT a dispatched action.
    action_count: int            # len(action_log) -- one entry per real SelectionPolicy-chosen ActionIntent dispatch.
    terminal_reason: str
    restart_type: str            # RestartType.DEAD_BALL_INBOUND | LIVE_TRANSITION (from the possession's own start)
    action_telemetry: Dict[str, ActionTelemetry] = field(default_factory=dict)
    # Structural Timing Hook (see docs/DETAILED_ENGINE_FIRST_DIAGNOSTIC_REPORT.md) -- keyed by
    # `PossessionStage` value (`HALFCOURT_ENTRY`/`TRANSITION_ENTRY`/`SECOND_CHANCE_RESET`), read from
    # `world.stage_timing_log`. Kept SEPARATE from `action_telemetry` on purpose -- setup time is not a
    # dispatched `ActionIntent`.
    stage_timing: Dict[str, ActionTelemetry] = field(default_factory=dict)
    # Inter-Action Timing Structure (see docs/DETAILED_ENGINE_FIRST_DIAGNOSTIC_REPORT.md) -- keyed by
    # `ContinuationStage` value (`INTER_ACTION`), read from `world.inter_action_log`. Kept SEPARATE
    # from `stage_timing` on purpose -- an inter-action charge answers a structurally different
    # question ("what happened LIVE between two decisions already inside this possession") than an
    # entry/reset charge ("how did this possession/second-chance BEGIN"); see `ContinuationStage`'s
    # own docstring in `possession_orchestrator.py`.
    inter_action_timing: Dict[str, ActionTelemetry] = field(default_factory=dict)
    fga: int = 0
    fgm: int = 0
    misses: int = 0
    fg3a: int = 0
    fg3m: int = 0
    blocked: int = 0
    shot_family_counts: Dict[str, int] = field(default_factory=dict)
    rebound_opportunities: int = 0
    oreb: int = 0
    dreb: int = 0
    second_chance_count: int = 0  # every OREB in this possession is, by construction, a second chance
    turnover_subtype: Optional[str] = None
    personal_fouls: int = 0
    shooting_fouls: int = 0
    floor_fouls: int = 0
    fta: int = 0
    ftm: int = 0
    has_whistle: bool = False


def diagnose_possession(record: PossessionRecord) -> PossessionDiagnostics:
    """Builds one possession's diagnostics purely from `record`'s own
    already-structured fields -- no re-simulation, no RNG, no mutation."""
    terminal = record.terminal_result
    world = terminal.world
    trace = world.trace
    deltas = record.provisional_deltas

    elapsed = record.start_game_clock - record.end_game_clock
    if elapsed < 0.0:
        raise ValueError(f"possession {record.possession_id!r} has negative elapsed game clock "
                          f"({record.start_game_clock} -> {record.end_game_clock})")
    if elapsed > record.start_game_clock + 1e-9:
        raise ValueError(f"possession {record.possession_id!r} elapsed more time than its start clock had")

    action_telemetry: Dict[str, ActionTelemetry] = {}
    _accumulate_action_telemetry(world.action_log, action_telemetry)
    stage_timing: Dict[str, ActionTelemetry] = {}
    _accumulate_action_telemetry(world.stage_timing_log, stage_timing, key="stage")
    inter_action_timing: Dict[str, ActionTelemetry] = {}
    _accumulate_action_telemetry(world.inter_action_log, inter_action_timing, key="stage")

    shot_entries = _shot_trace_entries(trace)
    fga = fgm = fg3a = fg3m = blocked = 0
    shot_family_counts: Dict[str, int] = {}
    for entry in shot_entries:
        family = entry.get("shot_family")
        if family:
            shot_family_counts[family] = shot_family_counts.get(family, 0) + 1
        if entry.get("action") == "SHOOTING_FOUL":
            made = entry.get("made")
            fga += 1
            if made:
                fgm += 1
                if family == "THREE_POINT":
                    fg3a += 1
                    fg3m += 1
            elif family == "THREE_POINT":
                pass  # a missed-and-one still counted an FGA above; no separate 3PA double-count
            continue
        outcome = entry.get("outcome")
        fga += 1
        if outcome in _BLOCKED_OUTCOMES:
            blocked += 1
        if family == "THREE_POINT":
            fg3a += 1
        if outcome in _MADE_OUTCOMES:
            fgm += 1
            if family == "THREE_POINT":
                fg3m += 1
    misses = fga - fgm

    rebound_entries = _rebound_trace_entries(trace)

    return PossessionDiagnostics(
        possession_id=record.possession_id, offense_team_id=record.offense_team_id,
        start_game_clock=record.start_game_clock, end_game_clock=record.end_game_clock,
        elapsed_game_clock_seconds=elapsed, step_count=terminal.steps_taken,
        action_count=len(world.action_log), terminal_reason=terminal.reason,
        restart_type=record.restart_context.restart_type,
        action_telemetry=action_telemetry, stage_timing=stage_timing, inter_action_timing=inter_action_timing,
        fga=fga, fgm=fgm, misses=misses, fg3a=fg3a, fg3m=fg3m,
        blocked=blocked, shot_family_counts=shot_family_counts,
        rebound_opportunities=len(rebound_entries), oreb=deltas.oreb, dreb=deltas.dreb,
        second_chance_count=deltas.oreb, turnover_subtype=_classify_turnover_subtype(record),
        personal_fouls=sum(deltas.personal_fouls.values()),
        shooting_fouls=sum(1 for e in trace if e.get("action") == "SHOOTING_FOUL"),
        floor_fouls=sum(1 for e in trace if e.get("action") == "ON_BALL_PRESSURE"
                        and e.get("outcome") in ("OFFENSIVE_CHARGE", "DEFENSIVE_FLOOR_FOUL")),
        fta=deltas.fta, ftm=deltas.ftm, has_whistle=deltas.fta > 0 or sum(deltas.personal_fouls.values()) > 0,
    )


# ---------------------------------------------------------------------
# 2. Game-level aggregation.
# ---------------------------------------------------------------------

def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(round(pct * (len(sorted_values) - 1)))))
    return sorted_values[idx]


@dataclass
class GameDiagnostics:
    total_possessions: int
    possessions_by_team: Dict[str, int]
    mean_possession_seconds: float
    median_possession_seconds: float
    p10_possession_seconds: float
    p90_possession_seconds: float
    mean_actions_per_possession: float
    median_actions_per_possession: float
    max_actions_per_possession: int
    max_step_count: int
    action_telemetry: Dict[str, ActionTelemetry]
    stage_timing: Dict[str, ActionTelemetry]  # Structural Timing Hook -- HALFCOURT_ENTRY/TRANSITION_ENTRY/SECOND_CHANCE_RESET
    inter_action_timing: Dict[str, ActionTelemetry]  # Inter-Action Timing Structure -- INTER_ACTION
    fga: int
    fgm: int
    misses: int
    fg3a: int
    fg3m: int
    blocked: int
    shot_family_counts: Dict[str, int]
    rebound_opportunities: int
    oreb: int
    dreb: int
    oreb_share: Optional[float]           # OREB / (OREB + DREB); None if zero rebounds occurred at all
    rebound_opportunities_per_miss: Optional[float]
    oreb_per_missed_shot: Optional[float]
    dreb_per_missed_shot: Optional[float]
    second_chance_total: int
    oreb_count_distribution: Dict[str, int]   # possessions with exactly 0 / 1 / 2 / "3+" OREBs
    turnovers: int
    turnover_ending_rate: float
    turnover_subtype_counts: Dict[str, int]
    personal_fouls: int
    shooting_fouls: int
    floor_fouls: int
    fta: int
    ftm: int
    possessions_with_whistle: int
    terminal_reason_distribution: Dict[str, int]
    possession_diagnostics: Tuple[PossessionDiagnostics, ...]


def diagnose_game(result: DetailedGameResult) -> GameDiagnostics:
    diagnostics = tuple(diagnose_possession(r) for r in result.possessions)

    possessions_by_team: Dict[str, int] = {}
    elapsed_seconds: List[float] = []
    action_counts: List[int] = []
    step_counts: List[int] = []
    action_telemetry: Dict[str, ActionTelemetry] = {}
    stage_timing: Dict[str, ActionTelemetry] = {}
    inter_action_timing: Dict[str, ActionTelemetry] = {}
    fga = fgm = fg3a = fg3m = blocked = 0
    shot_family_counts: Dict[str, int] = {}
    rebound_opportunities = oreb = dreb = 0
    second_chance_total = 0
    oreb_dist = {"0": 0, "1": 0, "2": 0, "3+": 0}
    turnover_subtype_counts: Dict[str, int] = {}
    turnovers = 0
    personal_fouls = shooting_fouls = floor_fouls = 0
    fta = ftm = 0
    possessions_with_whistle = 0
    terminal_reason_distribution: Dict[str, int] = {}

    for d in diagnostics:
        possessions_by_team[d.offense_team_id] = possessions_by_team.get(d.offense_team_id, 0) + 1
        elapsed_seconds.append(d.elapsed_game_clock_seconds)
        action_counts.append(d.action_count)
        step_counts.append(d.step_count)
        for name, telem in d.action_telemetry.items():
            bucket = action_telemetry.setdefault(name, ActionTelemetry(action_type=name))
            bucket.count += telem.count
            bucket.total_seconds += telem.total_seconds
        for name, telem in d.stage_timing.items():
            bucket = stage_timing.setdefault(name, ActionTelemetry(action_type=name))
            bucket.count += telem.count
            bucket.total_seconds += telem.total_seconds
        for name, telem in d.inter_action_timing.items():
            bucket = inter_action_timing.setdefault(name, ActionTelemetry(action_type=name))
            bucket.count += telem.count
            bucket.total_seconds += telem.total_seconds
        fga += d.fga
        fgm += d.fgm
        fg3a += d.fg3a
        fg3m += d.fg3m
        blocked += d.blocked
        for fam, cnt in d.shot_family_counts.items():
            shot_family_counts[fam] = shot_family_counts.get(fam, 0) + cnt
        rebound_opportunities += d.rebound_opportunities
        oreb += d.oreb
        dreb += d.dreb
        second_chance_total += d.second_chance_count
        bucket_key = str(d.oreb) if d.oreb < 3 else "3+"
        oreb_dist[bucket_key] += 1
        if d.turnover_subtype is not None:
            turnovers += 1
            turnover_subtype_counts[d.turnover_subtype] = turnover_subtype_counts.get(d.turnover_subtype, 0) + 1
        personal_fouls += d.personal_fouls
        shooting_fouls += d.shooting_fouls
        floor_fouls += d.floor_fouls
        fta += d.fta
        ftm += d.ftm
        if d.has_whistle:
            possessions_with_whistle += 1
        terminal_reason_distribution[d.terminal_reason] = terminal_reason_distribution.get(d.terminal_reason, 0) + 1

    misses = fga - fgm
    sorted_elapsed = sorted(elapsed_seconds)
    total_reb = oreb + dreb

    return GameDiagnostics(
        total_possessions=len(diagnostics), possessions_by_team=possessions_by_team,
        mean_possession_seconds=statistics.fmean(elapsed_seconds) if elapsed_seconds else 0.0,
        median_possession_seconds=statistics.median(elapsed_seconds) if elapsed_seconds else 0.0,
        p10_possession_seconds=_percentile(sorted_elapsed, 0.10), p90_possession_seconds=_percentile(sorted_elapsed, 0.90),
        mean_actions_per_possession=statistics.fmean(action_counts) if action_counts else 0.0,
        median_actions_per_possession=statistics.median(action_counts) if action_counts else 0.0,
        max_actions_per_possession=max(action_counts) if action_counts else 0,
        max_step_count=max(step_counts) if step_counts else 0,
        action_telemetry=action_telemetry, stage_timing=stage_timing, inter_action_timing=inter_action_timing,
        fga=fga, fgm=fgm, misses=misses, fg3a=fg3a, fg3m=fg3m, blocked=blocked,
        shot_family_counts=shot_family_counts, rebound_opportunities=rebound_opportunities, oreb=oreb, dreb=dreb,
        oreb_share=(oreb / total_reb) if total_reb > 0 else None,
        rebound_opportunities_per_miss=(rebound_opportunities / misses) if misses > 0 else None,
        oreb_per_missed_shot=(oreb / misses) if misses > 0 else None,
        dreb_per_missed_shot=(dreb / misses) if misses > 0 else None,
        second_chance_total=second_chance_total, oreb_count_distribution=oreb_dist,
        turnovers=turnovers, turnover_ending_rate=(turnovers / len(diagnostics)) if diagnostics else 0.0,
        turnover_subtype_counts=turnover_subtype_counts, personal_fouls=personal_fouls,
        shooting_fouls=shooting_fouls, floor_fouls=floor_fouls, fta=fta, ftm=ftm,
        possessions_with_whistle=possessions_with_whistle,
        terminal_reason_distribution=terminal_reason_distribution, possession_diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------
# 3. Multi-game aggregation (for the 10-game sample).
# ---------------------------------------------------------------------

@dataclass
class MultiGameDiagnostics:
    game_count: int
    mean_total_possessions: float
    mean_oreb: float
    mean_dreb: float
    mean_oreb_share: Optional[float]
    mean_personal_fouls: float
    mean_fta: float
    mean_turnovers: float
    mean_turnover_ending_rate: float
    per_game: Tuple[GameDiagnostics, ...]


def diagnose_games(results: Sequence[DetailedGameResult]) -> MultiGameDiagnostics:
    per_game = tuple(diagnose_game(r) for r in results)
    n = len(per_game)
    oreb_shares = [g.oreb_share for g in per_game if g.oreb_share is not None]
    return MultiGameDiagnostics(
        game_count=n,
        mean_total_possessions=statistics.fmean(g.total_possessions for g in per_game) if n else 0.0,
        mean_oreb=statistics.fmean(g.oreb for g in per_game) if n else 0.0,
        mean_dreb=statistics.fmean(g.dreb for g in per_game) if n else 0.0,
        mean_oreb_share=(statistics.fmean(oreb_shares) if oreb_shares else None),
        mean_personal_fouls=statistics.fmean(g.personal_fouls for g in per_game) if n else 0.0,
        mean_fta=statistics.fmean(g.fta for g in per_game) if n else 0.0,
        mean_turnovers=statistics.fmean(g.turnovers for g in per_game) if n else 0.0,
        mean_turnover_ending_rate=statistics.fmean(g.turnover_ending_rate for g in per_game) if n else 0.0,
        per_game=per_game,
    )


# ---------------------------------------------------------------------
# 4. Shot-Clock-at-Attempt Diagnosis (see
# docs/DETAILED_ENGINE_FIRST_DIAGNOSTIC_REPORT.md's own section). Reads
# ONLY `PossessionWorld.shot_attempt_log` (a diagnostic-only list
# `possession_orchestrator.py` populates from already-computed,
# already-structured clock/shot-family/outcome values -- never inferred
# from a descriptive string). Purely observational; never used by any
# simulation decision.
# ---------------------------------------------------------------------

# INTERNAL, NEUTRAL bins -- NOT claimed as official NBA.com shot-clock-range categories, with ONE
# exception: the "4-0" boundary IS a real, independently-verified NBA.com bucket edge already used
# elsewhere in this repository (`shot_resolution.py`'s own `LATE_CLOCK_THRESHOLD_SECONDS = 4.0`,
# grounded in a real NBA.com late-clock 3PT% finding). The remaining boundaries (18/15/7) are this
# diagnostic's own internal, neutral choices, not verified public categories.
SHOT_CLOCK_BINS: Tuple[Tuple[str, float, float], ...] = (
    ("24-18", 18.0, 24.0),
    ("18-15", 15.0, 18.0),
    ("15-7", 7.0, 15.0),
    ("7-4", 4.0, 7.0),
    ("4-0", 0.0, 4.0),
)


def _shot_clock_bin(value: Optional[float]) -> str:
    if value is None:
        return "NO_SHOT_CLOCK"
    for label, low, high in SHOT_CLOCK_BINS:
        if low <= value <= high:
            return label
    return "OUT_OF_RANGE"  # >24 or negative -- should never occur; a real anomaly if it does


@dataclass
class ShotClockBinStats:
    label: str
    count: int = 0
    makes: int = 0

    @property
    def fg_pct(self) -> Optional[float]:
        return (self.makes / self.count) if self.count else None


@dataclass
class StageOriginShotSummary:
    """Per-`PossessionStage`-origin shot-attempt summary. `elapsed_since_possession_start` is
    `record.start_game_clock - game_clock_at_attempt` for EVERY origin, including SECOND_CHANCE --
    for a second-chance shot this is elapsed since the WHOLE possession began (including time
    before the offensive rebound), NOT elapsed since only the reset itself; a documented
    simplification (see the report), not a fabricated "time since reset" reconstruction."""
    stage_origin: str
    fga: int = 0
    makes: int = 0
    first_action_fga: int = 0
    shot_clock_values: List[float] = field(default_factory=list)
    elapsed_since_possession_start_values: List[float] = field(default_factory=list)
    action_index_values: List[int] = field(default_factory=list)

    @property
    def fg_pct(self) -> Optional[float]:
        return (self.makes / self.fga) if self.fga else None

    @property
    def mean_shot_clock(self) -> Optional[float]:
        return statistics.fmean(self.shot_clock_values) if self.shot_clock_values else None

    @property
    def median_shot_clock(self) -> Optional[float]:
        return statistics.median(self.shot_clock_values) if self.shot_clock_values else None

    @property
    def mean_elapsed_since_possession_start(self) -> Optional[float]:
        return statistics.fmean(self.elapsed_since_possession_start_values) if self.elapsed_since_possession_start_values else None

    @property
    def first_action_share(self) -> Optional[float]:
        return (self.first_action_fga / self.fga) if self.fga else None

    @property
    def mean_action_index(self) -> Optional[float]:
        return statistics.fmean(self.action_index_values) if self.action_index_values else None


@dataclass
class ShotClockAtAttemptDiagnostics:
    total_fga: int
    bin_stats: Dict[str, ShotClockBinStats]
    mean_shot_clock_remaining: Optional[float]
    median_shot_clock_remaining: Optional[float]
    first_action_fga_count: int
    first_action_fga_share: Optional[float]
    mean_shot_clock_first_action: Optional[float]
    median_shot_clock_first_action: Optional[float]
    by_stage_origin: Dict[str, StageOriginShotSummary]
    shot_family_counts: Dict[str, int]


def diagnose_shot_clock_at_attempt(result: DetailedGameResult) -> ShotClockAtAttemptDiagnostics:
    bin_stats: Dict[str, ShotClockBinStats] = {label: ShotClockBinStats(label=label) for label, _, _ in SHOT_CLOCK_BINS}
    shot_clock_values: List[float] = []
    first_action_shot_clock_values: List[float] = []
    first_action_fga_count = 0
    total_fga = 0
    by_stage_origin: Dict[str, StageOriginShotSummary] = {}
    shot_family_counts: Dict[str, int] = {}

    for record in result.possessions:
        for entry in record.terminal_result.world.shot_attempt_log:
            total_fga += 1
            shot_clock_at_attempt = entry.get("shot_clock_at_attempt")
            made = bool(entry.get("made"))
            family = entry.get("shot_family")
            if family:
                shot_family_counts[family] = shot_family_counts.get(family, 0) + 1

            if shot_clock_at_attempt is not None:
                shot_clock_values.append(shot_clock_at_attempt)
                bucket = bin_stats.setdefault(_shot_clock_bin(shot_clock_at_attempt),
                                               ShotClockBinStats(label=_shot_clock_bin(shot_clock_at_attempt)))
                bucket.count += 1
                if made:
                    bucket.makes += 1

            is_first_action = bool(entry.get("is_first_action"))
            if is_first_action:
                first_action_fga_count += 1
                if shot_clock_at_attempt is not None:
                    first_action_shot_clock_values.append(shot_clock_at_attempt)

            origin = entry.get("stage_origin") or "UNKNOWN"
            summary = by_stage_origin.setdefault(origin, StageOriginShotSummary(stage_origin=origin))
            summary.fga += 1
            if made:
                summary.makes += 1
            if is_first_action:
                summary.first_action_fga += 1
            if shot_clock_at_attempt is not None:
                summary.shot_clock_values.append(shot_clock_at_attempt)
            game_clock_at_attempt = entry.get("game_clock_at_attempt")
            if game_clock_at_attempt is not None:
                summary.elapsed_since_possession_start_values.append(record.start_game_clock - game_clock_at_attempt)
            summary.action_index_values.append(entry.get("action_index", 0))

    return ShotClockAtAttemptDiagnostics(
        total_fga=total_fga, bin_stats=bin_stats,
        mean_shot_clock_remaining=statistics.fmean(shot_clock_values) if shot_clock_values else None,
        median_shot_clock_remaining=statistics.median(shot_clock_values) if shot_clock_values else None,
        first_action_fga_count=first_action_fga_count,
        first_action_fga_share=(first_action_fga_count / total_fga) if total_fga else None,
        mean_shot_clock_first_action=statistics.fmean(first_action_shot_clock_values) if first_action_shot_clock_values else None,
        median_shot_clock_first_action=statistics.median(first_action_shot_clock_values) if first_action_shot_clock_values else None,
        by_stage_origin=by_stage_origin, shot_family_counts=shot_family_counts,
    )


@dataclass
class MultiGameShotClockDiagnostics:
    game_count: int
    mean_total_fga: float
    mean_shot_clock_remaining: Optional[float]
    mean_first_action_fga_share: Optional[float]
    per_game: Tuple[ShotClockAtAttemptDiagnostics, ...]


def diagnose_shot_clock_at_attempt_multi(results: Sequence[DetailedGameResult]) -> MultiGameShotClockDiagnostics:
    per_game = tuple(diagnose_shot_clock_at_attempt(r) for r in results)
    n = len(per_game)
    means = [g.mean_shot_clock_remaining for g in per_game if g.mean_shot_clock_remaining is not None]
    shares = [g.first_action_fga_share for g in per_game if g.first_action_fga_share is not None]
    return MultiGameShotClockDiagnostics(
        game_count=n,
        mean_total_fga=statistics.fmean(g.total_fga for g in per_game) if n else 0.0,
        mean_shot_clock_remaining=(statistics.fmean(means) if means else None),
        mean_first_action_fga_share=(statistics.fmean(shares) if shares else None),
        per_game=per_game,
    )
