"""Read-only shot-CONVERSION decomposition diagnostics ("Calibrate clean shot conversion" phase).

Distinguishes, per shot family, exactly what the calibration task requires:
    A. shot BLOCKED (interior families only -- RIM/FLOATER)
    B. a SHOOTING-FOUL event (whistled before block/make resolution -- see
       `possession_orchestrator._dispatch_shot`'s own `contact_result.whistled` branch)
    C. a CLEAN (unblocked, unwhistled) field-goal resolution
    D. MAKE/MISS within that clean resolution

This module never resolves or influences a shot -- it re-reads `world.trace`/
`world.shot_attempt_log`/`world.action_log`, all already-computed, already-structured
values -- and consumes zero RNG (verified by `test_shot_conversion_diagnostics.py`'s own
`test_diagnostics_add_zero_rng` guardrail).

FGA/FGM ACCOUNTING (see `possession_orchestrator._dispatch_shot`/`_dispatch_shooting_foul`'s
own comments, reused as-is here, not reinvented):
  - Any UNWHISTLED dispatch ALWAYS counts as one raw FGA, made, missed, OR BLOCKED (a block is
    still a real field-goal attempt/miss in the real NBA box score -- never excluded).
  - A missed, WHISTLED (shooting-foul) attempt counts as ZERO raw FGA/FGM (real NBA box-score
    convention, already the engine's own rule).
  - A made, WHISTLED (and-one) attempt counts as one raw FGA AND one raw FGM.
So: raw_fga (`final_recorded_attempts`) = attempts + and_one_makes (`attempts` already includes
blocked); raw_fgm (`final_recorded_makes`) = clean_makes + and_one_makes.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Tuple

from detailed_engine_benchmark import BenchmarkGame
from detailed_game import DetailedGameResult
from interior_shot_resolution import InteriorShotFamily, InteriorShotOutcome
from shot_resolution import ShotFamily

FAMILIES: Tuple[str, ...] = (
    InteriorShotFamily.RIM, InteriorShotFamily.FLOATER, ShotFamily.MIDRANGE, ShotFamily.THREE_POINT,
)
_SHOT_TRACE_ACTIONS = frozenset({"PULL_UP", "CATCH_AND_SHOOT"})
_BLOCKED_OUTCOMES = frozenset({InteriorShotOutcome.BLOCKED_RETAINED_OFFENSE, InteriorShotOutcome.BLOCKED_SECURED_DEFENSE})
# The real, non-terminal dispatched actions that can immediately precede a shot dispatch --
# mirrors `shot_family_diagnostics.py`'s own `action_immediately_after_drive` convention rather
# than inventing a second way to tag "origin". A shot NOT immediately preceded by one of these
# (the possession's own first action, or one following a completed pass/rebound reset) is ORDINARY.
_ORIGIN_ACTIONS = frozenset({"DRIVE", "ON_BALL_SCREEN", "TRANSITION_PUSH", "INTERIOR_CUT", "INTERIOR_SEAL"})


@dataclass(frozen=True)
class FamilyConversionStats:
    attempts: int                 # real CLEAN-OR-BLOCKED dispatches (excludes whistled attempts)
    blocked: int
    shooting_fouls: int
    clean_attempts: int
    clean_makes: int
    clean_misses: int
    and_one_makes: int            # whistled AND made -- the only whistled outcome counted as raw FGA/FGM
    final_recorded_attempts: int  # raw box-score FGA for this family
    final_recorded_makes: int     # raw box-score FGM for this family
    by_release_action: Dict[str, "FamilyConversionStats"]
    by_origin: Dict[str, "FamilyConversionStats"]

    @property
    def clean_make_pct(self) -> float:
        return self.clean_makes / self.clean_attempts if self.clean_attempts else 0.0

    @property
    def raw_fg_pct(self) -> float:
        return self.final_recorded_makes / self.final_recorded_attempts if self.final_recorded_attempts else 0.0


@dataclass(frozen=True)
class FreeThrowConversionStats:
    attempts: int
    makes: int

    @property
    def ft_pct(self) -> float:
        return self.makes / self.attempts if self.attempts else 0.0


@dataclass(frozen=True)
class ShotConversionDiagnosis:
    games: int
    possessions: int
    by_family: Dict[str, FamilyConversionStats]
    free_throws: FreeThrowConversionStats
    fga_from_stat_deltas: int
    fgm_from_stat_deltas: int
    fta_from_stat_deltas: int
    ftm_from_stat_deltas: int
    accounting_mismatches: Tuple[str, ...]


def _results(items: Sequence[object]) -> Iterable[DetailedGameResult]:
    for item in items:
        yield item.result if isinstance(item, BenchmarkGame) else item


class _Bucket:
    """Plain, mutable accumulator -- converted to the frozen public dataclass at the end."""
    __slots__ = ("attempts", "blocked", "shooting_fouls", "clean_makes", "and_one_makes")

    def __init__(self):
        self.attempts = 0
        self.blocked = 0
        self.shooting_fouls = 0
        self.clean_makes = 0
        self.and_one_makes = 0

    def freeze(self, sub_by_release=None, sub_by_origin=None) -> FamilyConversionStats:
        clean_attempts = self.attempts - self.blocked
        clean_misses = clean_attempts - self.clean_makes
        # A BLOCKED attempt is still a real, UNWHISTLED clean-resolution dispatch -- real NBA
        # box-score rule (and this engine's own existing `world.stats.fga += 1`, charged
        # unconditionally in the interior branch regardless of block outcome): a block counts as
        # one raw FGA/missed FG, never zero. Only a WHISTLED-AND-MISSED attempt is excluded from
        # raw FGA (the engine's pre-existing shooting-foul accounting rule, reused as-is). So raw
        # FGA = ALL non-whistled dispatches (blocked or not) + made-and-one attempts.
        return FamilyConversionStats(
            attempts=self.attempts, blocked=self.blocked, shooting_fouls=self.shooting_fouls,
            clean_attempts=clean_attempts, clean_makes=self.clean_makes, clean_misses=clean_misses,
            and_one_makes=self.and_one_makes,
            final_recorded_attempts=self.attempts + self.and_one_makes,
            final_recorded_makes=self.clean_makes + self.and_one_makes,
            by_release_action={k: v.freeze() for k, v in (sub_by_release or {}).items()},
            by_origin={k: v.freeze() for k, v in (sub_by_origin or {}).items()},
        )


def diagnose_shot_conversion(items: Sequence[object]) -> ShotConversionDiagnosis:
    results = tuple(_results(items))
    records = [record for result in results for record in result.possessions]

    top = {family: _Bucket() for family in FAMILIES}
    by_release = {family: defaultdict(_Bucket) for family in FAMILIES}
    by_origin = {family: defaultdict(_Bucket) for family in FAMILIES}
    ft_attempts = 0
    ft_makes = 0
    mismatches = []

    fga_stat = fgm_stat = fta_stat = ftm_stat = 0

    for record in records:
        world = record.terminal_result.world
        fga_stat += record.provisional_deltas.fga
        fgm_stat += record.provisional_deltas.fgm
        fta_stat += record.provisional_deltas.fta
        ftm_stat += record.provisional_deltas.ftm

        # origin lookup: for each dispatched-shot step, what was the immediately PRECEDING
        # dispatched action (if any)? Mirrors `shot_family_diagnostics.action_immediately_after_drive`.
        actions = world.action_log
        origin_by_step = {}
        for index, action in enumerate(actions):
            if index == 0:
                continue
            prev_type = actions[index - 1]["action_type"]
            origin_by_step[action["step"]] = prev_type if prev_type in _ORIGIN_ACTIONS else "ORDINARY"
        # a shot at the possession's own FIRST dispatched action has no preceding action at all.
        if actions:
            origin_by_step.setdefault(actions[0]["step"], "ORDINARY")

        for row in world.trace:
            action = row.get("action")
            family = row.get("shot_family")
            if action in _SHOT_TRACE_ACTIONS and family in top:
                outcome = row.get("outcome")
                step = row.get("step")
                origin = origin_by_step.get(step, "ORDINARY")
                for bucket in (top[family], by_release[family][action], by_origin[family][origin]):
                    bucket.attempts += 1
                    if outcome == "MADE":
                        bucket.clean_makes += 1
                    elif outcome in _BLOCKED_OUTCOMES:
                        bucket.blocked += 1
                    elif outcome not in ("MISSED_UNBLOCKED", "MISSED"):
                        mismatches.append(f"{record.possession_id} step {step}: unexpected shot outcome {outcome!r}")
            elif action == "SHOOTING_FOUL" and family in top:
                step = row.get("step")
                origin = origin_by_step.get(step, "ORDINARY")
                for bucket in (top[family], by_origin[family][origin]):
                    bucket.shooting_fouls += 1
                    if row.get("made"):
                        bucket.and_one_makes += 1
            elif action == "BONUS_FLOOR_FOUL_FREE_THROWS":
                pass  # not a shooting-family event -- owned by foul diagnostics, not this module

        ft_attempts += record.provisional_deltas.fta
        ft_makes += record.provisional_deltas.ftm

    by_family = {
        family: top[family].freeze(sub_by_release=by_release[family], sub_by_origin=by_origin[family])
        for family in FAMILIES
    }

    total_fga = sum(stats.final_recorded_attempts for stats in by_family.values())
    total_fgm = sum(stats.final_recorded_makes for stats in by_family.values())
    if total_fga != fga_stat:
        mismatches.append(f"reconstructed FGA {total_fga} != StatDeltas FGA {fga_stat}")
    if total_fgm != fgm_stat:
        mismatches.append(f"reconstructed FGM {total_fgm} != StatDeltas FGM {fgm_stat}")
    if ft_attempts != fta_stat:
        mismatches.append(f"reconstructed FTA {ft_attempts} != StatDeltas FTA {fta_stat}")
    if ft_makes != ftm_stat:
        mismatches.append(f"reconstructed FTM {ft_makes} != StatDeltas FTM {ftm_stat}")
    for family, stats in by_family.items():
        if stats.clean_makes + stats.clean_misses != stats.clean_attempts:
            mismatches.append(f"{family}: clean makes+misses != clean attempts")
        if stats.blocked + stats.clean_attempts != stats.attempts:
            mismatches.append(f"{family}: blocked+clean != attempts")

    return ShotConversionDiagnosis(
        games=len(results), possessions=len(records), by_family=by_family,
        free_throws=FreeThrowConversionStats(attempts=ft_attempts, makes=ft_makes),
        fga_from_stat_deltas=fga_stat, fgm_from_stat_deltas=fgm_stat,
        fta_from_stat_deltas=fta_stat, ftm_from_stat_deltas=ftm_stat,
        accounting_mismatches=tuple(mismatches),
    )


def assert_shot_conversion_reconciliation(diagnosis: ShotConversionDiagnosis) -> None:
    if diagnosis.accounting_mismatches:
        raise AssertionError(f"shot-conversion diagnostics failed to reconcile: {diagnosis.accounting_mismatches}")
