"""
Phase 15 -- Possession State & Event Kernel: era-aware rules interface.

Rules change by ERA; player identity/ability never does. This file lets
`possession_engine.py` ask "what are the rules right now" without ever
hardcoding modern-NBA numbers into engine logic -- the engine takes an
`EraRules` instance as a parameter, it never imports a constant shot
clock value itself.

Exact historical rule tables are explicitly NOT completed this phase
(per instruction) -- the two real eras below are included to prove the
lookup-by-season mechanism works with more than one entry, using real,
verifiable NBA rule-change facts (not invented for convenience):
  - the shot clock did not exist before the 1954-55 season;
  - the offensive-rebound shot-clock reset changed from a full 24s to a
    real 14s reset starting the 2018-19 season.
Bonus-foul thresholds are included as a real interface hook with a
single placeholder value, NOT an empirically verified per-era table --
flagged as such below and in the report.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EraRules:
    """Everything possession-timing-related that the engine needs to
    ask the environment for, rather than assume. `None` fields mean
    "this rule doesn't exist in this era" (missing != zero -- a pre-
    shot-clock era has `shot_clock_seconds=None`, not `0`, since a 0
    would look like an already-expired clock)."""
    era_name: str
    shot_clock_seconds: Optional[float]
    oreb_shot_clock_reset_seconds: Optional[float]  # None = full reset to shot_clock_seconds (or no shot clock at all)
    bonus_foul_threshold: Optional[int]  # team fouls in a period that trigger bonus free throws -- PLACEHOLDER, not empirically verified per era
    period_length_seconds: float
    periods_per_game: int


# Real, verified rule-change facts (see module docstring). Bonus
# threshold is a single placeholder value shared by both real eras
# below -- explicitly NOT claimed as historically accurate per-era; a
# future phase should verify it before relying on it for anything
# beyond an interface smoke test.
PRE_SHOT_CLOCK_ERA = EraRules(
    era_name="pre_shot_clock (before 1954-55)",
    shot_clock_seconds=None, oreb_shot_clock_reset_seconds=None,
    bonus_foul_threshold=5, period_length_seconds=12 * 60.0, periods_per_game=4,
)
CLASSIC_24_RESET_ERA = EraRules(
    era_name="classic_24s_full_reset (1954-55 through 2017-18)",
    shot_clock_seconds=24.0, oreb_shot_clock_reset_seconds=None,  # None -> full reset to 24.0, the real rule for this whole span
    bonus_foul_threshold=5, period_length_seconds=12 * 60.0, periods_per_game=4,
)
MODERN_14_RESET_ERA = EraRules(
    era_name="modern_14s_oreb_reset (2018-19+)",
    shot_clock_seconds=24.0, oreb_shot_clock_reset_seconds=14.0,  # real rule change, verified: only on a live-ball OREB with >14s not already remaining
    bonus_foul_threshold=5, period_length_seconds=12 * 60.0, periods_per_game=4,
)


def get_era_rules(season: str) -> EraRules:
    """Real season-string lookup -- `season` is the same `"YYYY-YY"`
    format used everywhere else in this project. Exact boundary seasons
    are approximate placeholders for the two real rule changes named
    above; not re-verified game-by-game this phase."""
    year = int(season[:4])
    if year < 1954:
        return PRE_SHOT_CLOCK_ERA
    if year < 2018:
        return CLASSIC_24_RESET_ERA
    return MODERN_14_RESET_ERA


def oreb_reset_value(rules: EraRules, shot_clock_remaining_before_oreb: Optional[float]) -> Optional[float]:
    """Returns the shot-clock value to reset to after a live-ball
    offensive rebound: `oreb_shot_clock_reset_seconds` if the era has a
    shortened reset (real modern rule: 14.0), else a full reset to
    `shot_clock_seconds`, else None (no shot clock this era).
    `shot_clock_remaining_before_oreb` is accepted for a future,
    unimplemented refinement (the real rule has an edge-case interaction
    with backcourt-violation timing not modeled here) -- currently
    unused, kept as an explicit parameter rather than silently dropped
    so a caller's intent is visible in the call site."""
    if rules.shot_clock_seconds is None:
        return None
    if rules.oreb_shot_clock_reset_seconds is None:
        return rules.shot_clock_seconds  # full reset era
    return rules.oreb_shot_clock_reset_seconds
