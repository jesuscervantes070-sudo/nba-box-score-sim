"""Authoritative elapsed-time semantics for competing live clocks."""
from dataclasses import dataclass
from typing import Optional


# The detailed-engine timing tests and diagnostics already use 1e-9 as
# their small floating-point comparison tolerance. Reuse that convention
# so meaningless positive residue cannot authorize another live segment.
CLOCK_EPSILON_SECONDS = 1e-9


class ClockTerminalCause:
    NONE = "NONE"
    SHOT_CLOCK = "SHOT_CLOCK"
    PERIOD = "PERIOD"


@dataclass(frozen=True)
class LiveClockAdvance:
    requested_seconds: float
    actual_elapsed_seconds: float
    shot_clock_after: Optional[float]
    game_clock_after: Optional[float]
    terminal_cause: str
    truncated_by_shot_clock_seconds: float
    truncated_by_period_clock_seconds: float


def _normalize(clock: Optional[float]) -> Optional[float]:
    if clock is None:
        return None
    if clock < -CLOCK_EPSILON_SECONDS:
        raise ValueError("clock cannot be negative")
    return 0.0 if clock <= CLOCK_EPSILON_SECONDS else clock


def advance_live_clocks(requested_seconds: float,
                        shot_clock_remaining: Optional[float],
                        game_clock_remaining: Optional[float],
                        *, shot_clock_stops_segment: bool = True) -> LiveClockAdvance:
    """Return the physical elapsed time and both post-segment clocks.

    A live segment ends at the earliest of its nominal duration, an active
    shot-clock horn, or the period horn. Exact/simultaneous shot and period
    boundaries preserve the engine's established shot-clock-first precedence.
    A legally released shot opts out of the shot-clock stop condition while
    still decrementing/clamping that display clock.
    """
    if requested_seconds <= 0.0:
        raise ValueError("requested live duration must be positive")
    shot_before = _normalize(shot_clock_remaining)
    game_before = _normalize(game_clock_remaining)

    shot_limit = shot_before if shot_clock_stops_segment and shot_before is not None else None
    limits = [requested_seconds]
    if shot_limit is not None:
        limits.append(shot_limit)
    if game_before is not None:
        limits.append(game_before)
    actual = min(limits)

    shot_reached = (shot_limit is not None
                    and shot_limit <= requested_seconds + CLOCK_EPSILON_SECONDS
                    and (game_before is None or shot_limit <= game_before + CLOCK_EPSILON_SECONDS))
    period_reached = (game_before is not None
                      and game_before <= requested_seconds + CLOCK_EPSILON_SECONDS
                      and (shot_limit is None or game_before < shot_limit - CLOCK_EPSILON_SECONDS))
    if shot_reached:
        cause = ClockTerminalCause.SHOT_CLOCK
    elif period_reached:
        cause = ClockTerminalCause.PERIOD
    else:
        cause = ClockTerminalCause.NONE

    shot_after = None if shot_before is None else _normalize(max(0.0, shot_before - actual))
    game_after = None if game_before is None else _normalize(max(0.0, game_before - actual))
    truncated = max(0.0, requested_seconds - actual)
    return LiveClockAdvance(
        requested_seconds=requested_seconds,
        actual_elapsed_seconds=actual,
        shot_clock_after=shot_after,
        game_clock_after=game_after,
        terminal_cause=cause,
        truncated_by_shot_clock_seconds=(truncated if cause == ClockTerminalCause.SHOT_CLOCK else 0.0),
        truncated_by_period_clock_seconds=(truncated if cause == ClockTerminalCause.PERIOD else 0.0),
    )
