"""Real historical game outcome reconstruction -- FIRST HISTORICAL PREDICTIVE BACKTEST V1.

There is no single cached "final score" record per game in this repo's cache (`schedule.json`
carries only game_id/date/home_team/away_team). The real, already-cached, per-player
`player_game_log.json` (fgm/fg3m/ftm per player per real game, via
`player_game_log_ingestion.load_player_game_log`) is the leak-free, real source this module sums
into real team final scores -- reused by import only, no new ingestion, no new API calls.

Real point arithmetic: a made 3-pointer is counted in BOTH `fgm` and `fg3m` in this cache's own
convention (confirmed against `player_scoring_truth_temporal.py`'s own use of the same field), so
points = 2*fgm + fg3m + ftm (2-point makes = fgm - fg3m, worth 2 each; 3-point makes = fg3m, worth
an extra 1 each on top of the 2 already counted; free throws are 1 each).

Team attribution per player-game uses the SAME real, trade-aware `player_team_stints.team_as_of_date`
already used everywhere else in this project -- never inferred from a static, non-trade-aware
season roster. A player whose `team_as_of_date` on the real game date matches neither the home nor
away team (a genuine, rare data edge case -- e.g. a stint-boundary mismatch) is excluded from both
sums and counted, never silently guessed into either side.

This is PAST, already-final information only -- never used to build a PREGAME snapshot's player
truth or rotation (see `historical_game_snapshot.py`'s own leakage doctrine); it is used ONLY to
score predictions AFTER a snapshot/simulation has already been built, in `historical_predictive_backtest.py`.
"""
import functools
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from game_metadata import get_game_metadata
from player_game_log_ingestion import load_player_game_log
from player_team_stints import team_as_of_date


@dataclass(frozen=True)
class GameOutcome:
    game_id: str
    game_date: str
    season: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    home_win: bool
    margin: int  # home_score - away_score, real, signed
    n_home_players_counted: int
    n_away_players_counted: int
    n_players_excluded: int  # real edge case: team_as_of_date matched neither side


@functools.lru_cache(maxsize=None)
def _game_log_index(season: str) -> Dict[str, Tuple[Tuple[str, dict], ...]]:
    """{game_id: ((player_id, row), ...)} -- built ONCE per season from the real, already-cached
    per-player game log (reused-by-import, no new ingestion). Process-local `lru_cache` by season;
    see `clear_reference_caches()` for the test/debug reset hook (this module follows the exact
    same convention HISTORICAL SNAPSHOT PERFORMANCE V1 already established for every other
    per-season reference builder in this project)."""
    log = load_player_game_log(season)
    index = defaultdict(list)
    for player_id, rows in log.items():
        for row in rows:
            index[row["game_id"]].append((player_id, row))
    return {gid: tuple(rows) for gid, rows in index.items()}


def clear_reference_caches() -> None:
    """Test/debug reset hook -- clears `_game_log_index`'s process-local per-season cache."""
    _game_log_index.cache_clear()


def get_game_outcome(game_id: str, season: str) -> Optional[GameOutcome]:
    """Real final score/winner/margin for one real historical game, reconstructed from real,
    already-cached per-player game-log rows -- or None if the game isn't in the cached schedule or
    has no game-log evidence at all (real MISSING, never a fabricated 0-0)."""
    metadata = get_game_metadata(game_id, season)
    if metadata is None:
        return None
    rows = _game_log_index(season).get(game_id, ())
    if not rows:
        return None

    home_score = 0
    away_score = 0
    n_home = 0
    n_away = 0
    n_excluded = 0
    for player_id, row in rows:
        pts = 2 * row["fgm"] + row["fg3m"] + row["ftm"]
        team = team_as_of_date(player_id, metadata.game_date, season)
        if team == metadata.home_team:
            home_score += pts
            n_home += 1
        elif team == metadata.away_team:
            away_score += pts
            n_away += 1
        else:
            n_excluded += 1

    if n_home == 0 or n_away == 0:
        return None  # structurally unusable -- could not attribute either side at all

    return GameOutcome(
        game_id=game_id, game_date=metadata.game_date, season=season,
        home_team=metadata.home_team, away_team=metadata.away_team,
        home_score=home_score, away_score=away_score, home_win=home_score > away_score,
        margin=home_score - away_score,
        n_home_players_counted=n_home, n_away_players_counted=n_away,
        n_players_excluded=n_excluded,
    )
