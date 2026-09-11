"""Read-only detailed-engine BLOCK diagnostics.

Companion to `detailed_engine_foul_diagnostics.py` (same posture): this module consumes
completed `DetailedGameResult` objects. It does not simulate, mutate state, consume RNG, or
supply any value to production code.

============================ WHY A SEPARATE MODULE ============================
Blocks are resolved entirely inside `interior_shot_resolution.py`'s `resolve_interior_shot` --
RIM and FLOATER shots only (confirmed by direct source read of that module's own docstring:
"Phase 18B -- Interior/Rim Shot Resolution + Blocks (rim + floater only)"). `shot_resolution.py`
(MIDRANGE/THREE_POINT) has no block concept at all (confirmed: its own module docstring states
it resolves "... without blocks, shooting fouls, free throws, putbacks, or rebounds" for the
perimeter family, and direct source read finds zero occurrences of "block"/"BLOCK" in that file).
This funnel is therefore structurally family-specific in a way the shooting-foul funnel (which
runs identically across all four families) is not -- a dedicated module keeps that asymmetry
visible rather than folding it into the foul diagnostics' own per-family tables.

============================ ORDERING WITH THE SHOOTING-FOUL CHECK ============================
`possession_orchestrator._dispatch_shot` evaluates `resolve_contact_and_whistle` (the shooting-
foul check) BEFORE ever constructing block-relevant context or calling
`apply_interior_shot_to_engine`/`block_probability` -- confirmed by direct source read: a
whistled shot (`contact_result.whistled`) returns immediately via `_dispatch_shooting_foul` and
`apply_interior_shot_to_engine` is never reached for that attempt at all. The two paths are
therefore mutually exclusive, not sequential within one shot: a given attempt is EITHER a
shooting foul OR block-eligible, never both, and never double-resolved. This module reports
`shooting_foul_attempts_by_family` alongside the block funnel so the denominator relationship is
explicit -- every RIM/FLOATER attempt is either whistled (skips block check entirely) or reaches
`block_probability` (this module's own `block_checks_by_family`), never neither, never both.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Counter as CounterType, Dict, List, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from detailed_game import DetailedGameResult

SHOT_ACTIONS = frozenset({"PULL_UP", "CATCH_AND_SHOOT"})
# Block-capable shot families, per interior_shot_resolution.py's own documented scope --
# MIDRANGE/THREE_POINT are deliberately absent, not an oversight (see module docstring).
BLOCK_CAPABLE_FAMILIES = frozenset({"RIM", "FLOATER"})
BLOCKED_OUTCOMES = frozenset({"BLOCKED_RETAINED_OFFENSE", "BLOCKED_SECURED_DEFENSE"})


@dataclass
class BlockDiagnostics:
    game_count: int = 0
    # Every dispatched shot attempt, by family -- includes whistled attempts (which never reach
    # a block check at all; see `shooting_foul_attempts_by_family` below).
    dispatched_shots_by_family: CounterType[str] = field(default_factory=Counter)
    # Attempts whistled as a shooting foul BEFORE any block-relevant context existed -- these
    # structurally never reach `block_probability` (see module docstring's ordering section).
    shooting_foul_attempts_by_family: CounterType[str] = field(default_factory=Counter)
    # = dispatched_shots_by_family - shooting_foul_attempts_by_family, restricted to
    # BLOCK_CAPABLE_FAMILIES -- every one of these reaches `block_probability` exactly once.
    block_checks_by_family: CounterType[str] = field(default_factory=Counter)
    blocks_by_family: CounterType[str] = field(default_factory=Counter)
    blocked_retained_offense_by_family: CounterType[str] = field(default_factory=Counter)
    blocked_secured_defense_by_family: CounterType[str] = field(default_factory=Counter)
    made_by_family: CounterType[str] = field(default_factory=Counter)
    missed_unblocked_by_family: CounterType[str] = field(default_factory=Counter)
    blocks_from_stat_deltas: int = 0
    blocks_from_events: int = 0

    @property
    def total_dispatched_shots(self) -> int:
        return sum(self.dispatched_shots_by_family.values())

    @property
    def total_block_eligible_attempts(self) -> int:
        """Attempts that structurally COULD reach a block check -- every dispatched RIM/FLOATER
        attempt, whistled or not. This is the honest "opportunity" denominator; most of it is
        NOT actually checked (see `total_block_checks`) because a whistled attempt skips the
        check entirely."""
        return sum(self.dispatched_shots_by_family.get(f, 0) for f in BLOCK_CAPABLE_FAMILIES)

    @property
    def total_block_checks(self) -> int:
        return sum(self.block_checks_by_family.values())

    @property
    def total_blocks(self) -> int:
        return sum(self.blocks_by_family.values())

    def block_rate_per_check(self, family: str) -> float:
        checks = self.block_checks_by_family.get(family, 0)
        return (self.blocks_by_family.get(family, 0) / checks) if checks else float("nan")


def _trace_rows_for_step(world, step: int) -> List[dict]:
    return [row for row in world.trace if row.get("step") == step]


def diagnose_blocks(results: Sequence["DetailedGameResult"]) -> BlockDiagnostics:
    """Aggregate the complete block-check/credit funnel without re-simulation."""
    diagnosis = BlockDiagnostics(game_count=len(results))

    for result in results:
        for record in result.possessions:
            world = record.terminal_result.world
            deltas = record.provisional_deltas
            diagnosis.blocks_from_stat_deltas += deltas.blocks

            for action in world.action_log:
                action_type = action["action_type"]
                if action_type not in SHOT_ACTIONS:
                    continue
                rows = _trace_rows_for_step(world, action["step"])
                shot_row = next((r for r in rows if r.get("action") == action_type and r.get("shot_family") is not None), None)
                foul_row = next((r for r in rows if r.get("action") == "SHOOTING_FOUL"), None)
                if foul_row is not None:
                    family = foul_row.get("shot_family", "UNKNOWN")
                    diagnosis.dispatched_shots_by_family[family] += 1
                    diagnosis.shooting_foul_attempts_by_family[family] += 1
                    continue
                if shot_row is None:
                    continue
                family = shot_row.get("shot_family", "UNKNOWN")
                diagnosis.dispatched_shots_by_family[family] += 1
                if family in BLOCK_CAPABLE_FAMILIES:
                    diagnosis.block_checks_by_family[family] += 1
                    outcome = shot_row.get("outcome")
                    if outcome == "MADE":
                        diagnosis.made_by_family[family] += 1
                    elif outcome == "MISSED_UNBLOCKED":
                        diagnosis.missed_unblocked_by_family[family] += 1
                    elif outcome in BLOCKED_OUTCOMES:
                        diagnosis.blocks_by_family[family] += 1
                        if outcome == "BLOCKED_RETAINED_OFFENSE":
                            diagnosis.blocked_retained_offense_by_family[family] += 1
                        else:
                            diagnosis.blocked_secured_defense_by_family[family] += 1
                else:
                    outcome = shot_row.get("outcome")
                    if outcome == "MADE":
                        diagnosis.made_by_family[family] += 1

            for event in record.events:
                if event.event_type.name in ("BLOCK_RETAINED_BY_OFFENSE", "BLOCK_SECURED_BY_DEFENSE"):
                    diagnosis.blocks_from_events += 1

    return diagnosis


def assert_block_reconciliation(diagnosis: BlockDiagnostics) -> None:
    """Raise when existing event/trace/stat projections disagree."""
    if diagnosis.total_blocks != diagnosis.blocks_from_stat_deltas:
        raise AssertionError("trace-derived blocks do not match StatDeltas.blocks")
    if diagnosis.blocks_from_events != diagnosis.blocks_from_stat_deltas:
        raise AssertionError("event-derived blocks do not match StatDeltas.blocks")
    # every block-capable dispatch is EITHER whistled OR block-checked, never neither/both.
    for family in BLOCK_CAPABLE_FAMILIES:
        dispatched = diagnosis.dispatched_shots_by_family.get(family, 0)
        whistled = diagnosis.shooting_foul_attempts_by_family.get(family, 0)
        checked = diagnosis.block_checks_by_family.get(family, 0)
        if dispatched != whistled + checked:
            raise AssertionError(
                f"{family}: dispatched ({dispatched}) != whistled ({whistled}) + block-checked ({checked})"
            )
