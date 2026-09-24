"""Pure helpers for the season-picker menus (no I/O, so they can be tested without a terminal).

The list of playable seasons itself comes from loader.available_seasons(); nothing here keeps a
second copy of it.
"""
from dataclasses import dataclass
from typing import List, Optional, Sequence

BACK_WORDS = ("b", "back", "q", "quit")


@dataclass(frozen=True)
class Choice:
    """kind is "season" (index set), "default" (index = the caller's default), "back" or "invalid"."""
    kind: str
    index: Optional[int] = None


def parse_season_choice(raw: str, seasons: Sequence[str], default_index: Optional[int] = None) -> Choice:
    """Parse one answer to a season prompt: a 1-based number, the season text itself, Enter for the
    default (only when the caller has one), or a back word. Never raises."""
    text = raw.strip().lower()
    if text in BACK_WORDS:
        return Choice("back")
    if text == "":
        return Choice("default", default_index) if default_index is not None else Choice("invalid")
    if text.isdigit():
        number = int(text)
        return Choice("season", number - 1) if 1 <= number <= len(seasons) else Choice("invalid")
    for i, season in enumerate(seasons):
        if season.lower() == text:
            return Choice("season", i)
    return Choice("invalid")


def season_span(seasons: Sequence[str]) -> str:
    """'1996-97 through 2025-26' for any ordering of `seasons`."""
    ordered = sorted(seasons)
    return ordered[0] if len(ordered) == 1 else f"{ordered[0]} through {ordered[-1]}"


def run_label(run: Sequence[str]) -> str:
    if len(run) == 1:
        return f"{run[0]} NBA Season"
    return f"{run[0]} through {run[-1]} ({len(run)} seasons)"


def team_count_text(counts: Sequence[int]) -> str:
    low, high = min(counts), max(counts)
    return f"{low} teams" if low == high else f"{low} to {high} teams"
