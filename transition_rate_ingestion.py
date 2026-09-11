"""
Calibrate source-conditioned transition routing -- real 2025-26 (and reusable 2024-25/2023-24)
empirical extraction of P(transition | possession source).

============================ DATA-SOURCE AUDIT (verified directly this phase) ============================
`playbyplayv3` (nba_api), the SAME endpoint/schema already validated in `block_rate_ingestion.py`.
Possession-CHANGE events are identified directly from real, already-audited row shapes (no
legacy `EVENTMSGTYPE`/`PCTIMESTRING` fields anywhere -- this module reads only `actionType`,
`subType`, `description`, `teamId`, `personId`, `clock`, `period`, exactly as the v3 schema
returns them):
  - `actionType == "Rebound"`, `description` containing `"Def:1"` -> a real DEFENSIVE_REBOUND.
    (`"Off:1"` rebounds are offensive -- excluded; not a possession-change source.)
  - `actionType == "Turnover"` whose `actionNumber` is immediately paired with a companion row
    (empty `actionType`, description matching `"<Name> STEAL (N STL)"`, SAME real pairing
    mechanism already verified for blocks) -> LIVE_STEAL (subType NOT "Bad Pass") or
    LIVE_BAD_PASS_INTERCEPTION (subType "Bad Pass").
  - `actionType == "Turnover"`, `subType` containing "Out of Bounds"/"Offensive Foul"/"Shot
    Clock"/"Kicked Ball" (no STEAL companion row) -> DEAD_BALL_TURNOVER.
  - `actionType == "Made Shot"` -> MADE_BASKET_INBOUND.
  - `actionType == "Turnover"`, `subType == "Lost Ball"` (no STEAL companion) -> a real
    LOOSE_BALL_RECOVERY precursor, but WHO recovers it (offense retains vs. defense recovers) is
    a SEPARATE, later event this extraction does NOT resolve -- excluded from this sample,
    honestly (see `LOOSE_BALL_RECOVERY` coverage note below), not guessed.

For each identified source event, this module finds the elapsed GAME-CLOCK time (from the
event's own `clock` field, parsed from the real `"PTxxMxx.xxS"` format) to the next event, BY THE
TEAM THAT JUST GAINED THE BALL, whose `actionType` is `Made Shot`/`Missed Shot`/`Turnover`/`Foul`
-- the real first offensive action of the new possession. Events that would cross a period
boundary are dropped (cannot be measured cleanly; never guessed).

============================ COVERAGE NOTE: LOOSE_BALL_RECOVERY ============================
This extraction does NOT resolve LOOSE_BALL_RECOVERY timing -- doing so correctly requires
tracking which team recovers a live loose ball (a SEPARATE subsequent event from the "Lost Ball"
turnover row itself), which this module does not yet implement. `SOURCE_TRANSITION_PROFILES`
(`possession_orchestrator`/`transition_state.py`) therefore leaves `LOOSE_BALL_RECOVERY`'s
routing UNCHANGED (still deterministically `LIVE_TRANSITION`, its pre-existing behavior) rather
than guessing a rate for it -- MISSING != ZERO, and never fabricated from a similar-looking
source's own rate.

============================ WHY "<8 SECONDS" IS NOT A FRESH ARBITRARY THRESHOLD ============================
This module reports each source's elapsed-time distribution (mean/median/<6s/<8s/<12s shares) --
the SAME "<8 second" framing this project's own prior transition-diagnostic work already used
(the Phase 20 architecture audit's own report: "~34% of possessions are under 8s... 98.7% of
under-8s possessions originate from LIVE_TRANSITION"). `SOURCE_TRANSITION_PROFILES` uses each
source's own measured <8s SHARE directly as its `live_transition_probability` -- continuing this
project's own prior analysis convention, not inventing a new cutoff this phase.
"""
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from data_source import _season_cache_dir

TRANSITION_RATE_CACHE_VERSION = 1

_CLOCK_RE = re.compile(r"PT(\d+)M([\d.]+)S")
_OFFENSIVE_EVENT_TYPES = frozenset({"Made Shot", "Missed Shot", "Turnover", "Foul"})


def _clock_to_seconds(clock_str: Optional[str]) -> Optional[float]:
    if not clock_str:
        return None
    m = _CLOCK_RE.match(clock_str)
    if not m:
        return None
    return int(m.group(1)) * 60 + float(m.group(2))


def _transition_rate_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "transition_rate_sample.json"


def extract_transition_rate_sample(season: str, sample_size: int = 120, force: bool = False) -> dict:
    """Fetches a real, stratified (every-Nth-game-by-date) sample of `season`'s regular-season
    play-by-play and extracts, for every identified possession-change SOURCE event, the elapsed
    time to the new offense's own first real action. Requires a live, reachable `stats.nba.com`
    connection -- raises `ConnectionError` explicitly rather than returning fabricated/stale data
    (same posture as `block_rate_ingestion.extract_block_rate_sample`).

    Idempotent/cacheable: returns the existing cache unless `force=True`."""
    cache_path = _transition_rate_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)

    try:
        from nba_api.stats.endpoints import leaguegamefinder, playbyplayv3
    except ImportError as e:
        raise ConnectionError(f"nba_api is not installed -- cannot extract real transition data: {e}")

    try:
        gf = leaguegamefinder.LeagueGameFinder(season_nullable=season, league_id_nullable="00",
                                                season_type_nullable="Regular Season", timeout=30)
        schedule_df = gf.get_data_frames()[0]
    except Exception as e:
        raise ConnectionError(
            f"Could not reach stats.nba.com to fetch the {season} schedule -- real extraction "
            f"requires a live connection; no fallback/guessed data is substituted: {e}"
        )

    games = schedule_df.drop_duplicates(subset="GAME_ID").sort_values("GAME_DATE")["GAME_ID"].tolist()
    step = max(1, len(games) // sample_size)
    sample_ids = games[::step][:sample_size]

    events: List[dict] = []
    failed: List[str] = []

    for game_id in sample_ids:
        df = None
        for attempt in range(3):
            try:
                df = playbyplayv3.PlayByPlayV3(game_id=game_id, timeout=20).get_data_frames()[0]
                break
            except Exception:
                time.sleep(1)
        if df is None:
            failed.append(game_id)
            continue

        rows = df.to_dict("records")
        n = len(rows)
        steal_by_action = {
            row["actionNumber"]: row for row in rows
            if row.get("actionType") == "" and "STEAL" in str(row.get("description", ""))
        }

        for idx, row in enumerate(rows):
            at = row.get("actionType")
            desc = str(row.get("description", ""))
            subtype = str(row.get("subType", ""))
            source = None
            new_team = None

            if at == "Rebound":
                if "Def:1" not in desc:
                    continue
                source = "DEFENSIVE_REBOUND"
                new_team = row.get("teamId")
            elif at == "Turnover":
                action_num = row.get("actionNumber")
                if action_num in steal_by_action:
                    source = "LIVE_BAD_PASS_INTERCEPTION" if "Bad Pass" in subtype else "LIVE_STEAL"
                    new_team = steal_by_action[action_num].get("teamId")
                elif ("Out of Bounds" in subtype or "Offensive Foul" in subtype
                      or "Shot Clock" in subtype or "Kicked Ball" in subtype):
                    source = "DEAD_BALL_TURNOVER"
                    for j in range(idx + 1, n):
                        if rows[j].get("teamId") and rows[j].get("teamId") != row.get("teamId"):
                            new_team = rows[j].get("teamId")
                            break
                else:
                    continue  # "Lost Ball" (loose-ball precursor) -- see module docstring's coverage note
            elif at == "Made Shot":
                source = "MADE_BASKET_INBOUND"
                for j in range(idx + 1, n):
                    if rows[j].get("teamId") and rows[j].get("teamId") != row.get("teamId"):
                        new_team = rows[j].get("teamId")
                        break
            else:
                continue

            if new_team is None:
                continue
            start_clock = _clock_to_seconds(row.get("clock"))
            start_period = row.get("period")
            if start_clock is None:
                continue

            elapsed = None
            for j in range(idx + 1, n):
                nxt = rows[j]
                if nxt.get("period") != start_period:
                    break
                if nxt.get("teamId") == new_team and nxt.get("actionType") in _OFFENSIVE_EVENT_TYPES:
                    nxt_clock = _clock_to_seconds(nxt.get("clock"))
                    if nxt_clock is not None:
                        elapsed = start_clock - nxt_clock
                    break
            if elapsed is not None and elapsed >= 0:
                events.append({"source": source, "elapsed_seconds": elapsed, "game_id": game_id})

    payload = {
        "cache_version": TRANSITION_RATE_CACHE_VERSION,
        "season": season,
        "season_type": "Regular Season",
        "source": "nba_api.stats.endpoints.playbyplayv3",
        "total_games_in_season": len(games),
        "sample_game_ids": sample_ids,
        "sample_size": len(sample_ids),
        "failed_game_ids": failed,
        "events": events,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(payload, f)
    return payload


@dataclass
class SourceTimingSummary:
    source: str
    n: int = 0
    mean_seconds: float = 0.0
    median_seconds: float = 0.0
    share_under_6s: float = 0.0
    share_under_8s: float = 0.0
    share_under_12s: float = 0.0


def summarize_transition_rate_sample(season: str) -> Dict[str, SourceTimingSummary]:
    """Reads the cached sample (call `extract_transition_rate_sample` first) and returns the
    per-source elapsed-time distribution."""
    import statistics
    cache_path = _transition_rate_cache_path(season)
    if not cache_path.exists():
        raise FileNotFoundError(
            f"no cached transition-rate sample for {season} -- call extract_transition_rate_sample({season!r}) first"
        )
    with open(cache_path) as f:
        payload = json.load(f)

    by_source: Dict[str, List[float]] = {}
    for e in payload["events"]:
        by_source.setdefault(e["source"], []).append(e["elapsed_seconds"])

    summaries: Dict[str, SourceTimingSummary] = {}
    for source, elapsed in by_source.items():
        n = len(elapsed)
        summaries[source] = SourceTimingSummary(
            source=source, n=n,
            mean_seconds=statistics.mean(elapsed), median_seconds=statistics.median(elapsed),
            share_under_6s=sum(1 for x in elapsed if x < 6) / n,
            share_under_8s=sum(1 for x in elapsed if x < 8) / n,
            share_under_12s=sum(1 for x in elapsed if x < 12) / n,
        )
    return summaries
