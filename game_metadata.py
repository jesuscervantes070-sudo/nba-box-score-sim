"""
Historical Pregame Game Snapshot V1 -- smallest additive date-safe game-metadata reader.

Reuses the existing, real, already-cached `cache/<season>/schedule.json` (real game_id/date/
home_team/away_team, 1230 rows/season, no live dependency) -- no new ingestion. This module ONLY
reads it and answers "what real game was this" (never inferred from input-argument ORDER -- the
task's own explicit concern: home/away must come from real official metadata, not from whichever
positional argument a caller happened to pass first).
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class GameMetadata:
    game_id: str
    game_date: str
    season: str
    home_team: str
    away_team: str


@lru_cache(maxsize=None)
def _schedule_by_game_id(season: str) -> dict:
    path = Path("cache") / season / "schedule.json"
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    return {g["game_id"]: g for g in data.get("games", [])}


def get_game_metadata(game_id: str, season: str) -> Optional[GameMetadata]:
    """Real, official game metadata for `game_id` -- None if not found in this season's real
    cached schedule (never guessed from caller-supplied team order)."""
    row = _schedule_by_game_id(season).get(game_id)
    if row is None:
        return None
    return GameMetadata(
        game_id=row["game_id"], game_date=row["date"], season=season,
        home_team=row["home_team"], away_team=row["away_team"],
    )
