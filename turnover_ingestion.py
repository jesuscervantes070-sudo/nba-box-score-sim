"""
Phase 4A -- Ball Security: resumable, per-game turnover-subtype ingestion.

READ-ONLY with respect to everything else in this codebase: this module
imports `load_schedule` (for real game ids) and `data_source._season_cache_dir`
(for the existing cache/<season>/ convention) but writes only its OWN new
cache file, `cache/<season>/player_turnover_subtypes.json`. Nothing here is
imported by game_engine.py, season.py, awards.py, models.py, or
transactions.py.

============================ WHAT'S REAL HERE ============================
`playbyplayv3` (confirmed across 1996-97 through the present, see
player_ability_turnover_prototype.py's original prototype) returns a real,
structured `actionType`/`subType` pair for every turnover event -- NOT just
free text to keyword-match. `actionType == "Turnover"` isolates every
turnover event in a game; `subType` is the NBA's own real classification
("Bad Pass", "Lost Ball", "Traveling", "Offensive Foul Turnover", "Shot
Clock Turnover", ...) -- confirmed present and populated across every era
this project caches (checked directly on 1996-97, 2005-06, 2013-14 games).
This is a materially better signal than the original prototype's raw-text
keyword search, which is kept in player_ability_turnover_prototype.py
unmodified as the original, still-valid small-sample demonstration.

Team-level turnovers (e.g. "NUGGETS Turnover: Shot Clock") report the
TEAM's id in `personId` (a 1610612xxx franchise id, not a real player id)
with `teamId == 0` on that same row -- confirmed directly. Those are
skipped entirely: they are not attributable to any one player's handling
at all, per the task's own scope ("TEAM/SYSTEM" bucket exists for the
season-level record, but a team-level event has no real individual to
attribute it to).

============================ CATEGORY MAP ============================
Built from real `subType` values observed directly across a real,
multi-era sample (1996-97, 2000-01, 2005-06, 2009-10, 2013-14, 2018-19,
2023-24) -- not guessed. Any `subType` NOT in this map falls through to
`other_unclassified`, preserved with its own real example description and
count -- never silently forced into another bucket (per the task's
explicit "do not silently discard unknown turnover descriptions").
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from data_source import _season_cache_dir  # reuse the existing cache/<season>/ convention

TURNOVER_CACHE_VERSION = 1

# personId values for team-level (not player-level) turnover events are
# real NBA franchise ids, e.g. 1610612743 -- always exactly this prefix,
# always 10 digits. A real player id is much shorter (currently up to 7
# digits). Checked directly against every team-level turnover row in the
# samples above -- never a false positive against a real player id.
_TEAM_ID_PREFIX = "1610612"

CATEGORY_HANDLING = "handling_error"
CATEGORY_BAD_PASS = "bad_pass"
CATEGORY_OFFENSIVE_FOUL = "offensive_foul_nonhandle"
CATEGORY_TEAM_SYSTEM = "team_system"
CATEGORY_UNKNOWN = "other_unclassified"

ALL_CATEGORIES = (
    CATEGORY_HANDLING, CATEGORY_BAD_PASS, CATEGORY_OFFENSIVE_FOUL,
    CATEGORY_TEAM_SYSTEM, CATEGORY_UNKNOWN,
)

# Real subType -> category. See module docstring for how this was built.
SUBTYPE_CATEGORY_MAP: Dict[str, str] = {
    # HANDLING -- a real live-ball ball-control failure while the player
    # himself has/had the ball. This, and only this, is the intended
    # future ball_security numerator (see BALL SECURITY task scope).
    "Lost Ball": CATEGORY_HANDLING,
    "Traveling": CATEGORY_HANDLING,
    "Out of Bounds Lost Ball Turnover": CATEGORY_HANDLING,
    "Poss Lost Ball Turnover": CATEGORY_HANDLING,
    "Step Out of Bounds Turnover": CATEGORY_HANDLING,
    "Discontinue Dribble": CATEGORY_HANDLING,
    "Palming Turnover": CATEGORY_HANDLING,
    "Double Dribble": CATEGORY_HANDLING,
    "Backcourt Turnover": CATEGORY_HANDLING,
    # PASSING -- a decision/execution error, NEVER counted toward ball
    # security (belongs to the existing Passing attribute's domain).
    "Bad Pass": CATEGORY_BAD_PASS,
    "Out of Bounds - Bad Pass Turnover": CATEGORY_BAD_PASS,
    "Inbound Turnover": CATEGORY_BAD_PASS,  # real subtype: a bad inbounds pass, same passing-error family
    # OFFENSIVE FOUL / NON-HANDLE -- contact/positioning, NEVER handling.
    "Foul": CATEGORY_OFFENSIVE_FOUL,  # real subtype text for an offensive-foul turnover in older seasons
    "Offensive Foul Turnover": CATEGORY_OFFENSIVE_FOUL,
    "Illegal Pick": CATEGORY_OFFENSIVE_FOUL,  # illegal screen
    "Illegal Screen Turnover": CATEGORY_OFFENSIVE_FOUL,  # real subtype text seen in a later-era sample, same real infraction as "Illegal Pick"
    # TEAM / SYSTEM -- clock/procedural violations, not a handling failure.
    "Shot Clock Turnover": CATEGORY_TEAM_SYSTEM,
    "3 Second Violation": CATEGORY_TEAM_SYSTEM,
    "5 Second Violation": CATEGORY_TEAM_SYSTEM,
    "8 Second Violation": CATEGORY_TEAM_SYSTEM,
    "Jump Ball Violation": CATEGORY_TEAM_SYSTEM,
    "Team Turnover": CATEGORY_TEAM_SYSTEM,
}
# Deliberately left OUT of the map (falls to other_unclassified, not
# forced into HANDLING even though they sound related -- see docstring):
#   "Out Of Bounds" (bare) -- ambiguous who/what caused it; the task says
#       to only classify OOB as handling "when classification is
#       defensible" -- a bare, unqualified OOB turnover is NOT.
#   "Offensive Goaltending", "Kicked Ball Violation", "Lane Violation" --
#       real rule violations, but not ball-control failures.


def classify_subtype(subtype: Optional[str]) -> str:
    """Real subType string -> one of ALL_CATEGORIES. Unknown/empty ->
    other_unclassified, never silently dropped."""
    if not subtype:
        return CATEGORY_UNKNOWN
    return SUBTYPE_CATEGORY_MAP.get(subtype, CATEGORY_UNKNOWN)


def _is_reviewed_not_a_turnover(description: str) -> bool:
    """
    Real, structured-field-based fix (Phase 4A expansion): a small but
    real number of `actionType == "Turnover"` rows have an EMPTY subType
    and a description reading literally "<Name> No Turnover (...)" --
    confirmed directly (2018-19 game 0021800015, "Smith No Turnover").
    This is a replay-review overturn that the NBA's own feed never
    reclassified out of the Turnover actionType -- it is not a real
    turnover of ANY kind, and must not be counted in `total`,
    `handling_error`, or even `other_unclassified` (which would
    over-count real turnover volume, not just misclassify its type).
    Deliberately narrow (`"No Turnover" in description`) -- this is a
    literal, structured signal from the NBA's own text, not a fuzzy
    guess, and does not fire on any real turnover subtype description
    checked so far.
    """
    return "No Turnover" in (description or "")


def _is_team_event(person_id) -> bool:
    if person_id is None:
        return False
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return False
    return str(pid).startswith(_TEAM_ID_PREFIX)


def _turnover_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_turnover_subtypes.json"


def resolve_full_name(player_id: int) -> Optional[str]:
    """
    Real player_id -> real full display name, via nba_api's bundled
    static player list (nba_api.stats.static.players) -- covers EVERY
    era (checked directly: both Michael Jordan, id 893, and Nikola
    Jokic, id 203999, resolve correctly), no network call needed.

    This is the correct crosswalk for turnover_ingestion's own
    `player_name` field, which comes from playbyplayv3's real
    `playerName` column -- confirmed to be LAST-NAME-ONLY (e.g. "Paul"
    for Chris Paul, "Jordan" for Michael Jordan), NOT a full display
    name, and therefore NOT safely joinable against
    player_advanced.json's full-name keys on its own. Returns None if
    the id isn't in the static list (should not happen for a real NBA
    player id, but never silently guessed).
    """
    from nba_api.stats.static import players
    info = players.find_player_by_id(player_id)
    return info["full_name"] if info else None


def fetch_game_turnovers(game_id: str, retries: int = 4, timeout: int = 20) -> List[dict]:
    """
    Every real player-attributed turnover event in one real game:
    [{"player_id", "player_name", "category", "subtype", "description"}].
    Team-level events (see _is_team_event) are skipped -- not attributable
    to a player. Retries with backoff on real, observed transient
    read-timeouts from stats.nba.com (same flakiness data_source.py's
    leaguedashptdefend fetcher already documents and retries around).
    """
    from nba_api.stats.endpoints import playbyplayv3
    last_err = None
    df = None
    for attempt in range(retries):
        try:
            df = playbyplayv3.PlayByPlayV3(game_id=game_id, timeout=timeout).get_data_frames()[0]
            break
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    if df is None:
        raise RuntimeError(f"playbyplayv3 failed for game {game_id} after {retries} attempts: {last_err}")

    events = []
    turnovers = df[df["actionType"] == "Turnover"]
    for _, row in turnovers.iterrows():
        person_id = row.get("personId")
        if _is_team_event(person_id):
            continue
        if person_id is None:
            continue
        description = row.get("description") or ""
        if _is_reviewed_not_a_turnover(description):
            continue  # real replay-overturned call -- never a real turnover, see _is_reviewed_not_a_turnover
        subtype = row.get("subType") or ""
        events.append({
            "player_id": int(person_id),
            "player_name": row.get("playerName") or row.get("playerNameI") or "",
            "category": classify_subtype(subtype),
            "subtype": subtype,
            "description": row.get("description") or "",
        })
    return events


def _new_state(season: str) -> dict:
    return {
        "season": season,
        "source": "playbyplayv3",
        "cache_version": TURNOVER_CACHE_VERSION,
        "games_total": 0,
        "games_done": [],
        "games_failed": [],
        "players": {},
        "unknown_subtypes": {},
        "last_updated": None,
    }


def _empty_player_row(player_id: int, player_name: str) -> dict:
    row = {"player_id": player_id, "player_name": player_name, "total": 0}
    for cat in ALL_CATEGORIES:
        row[cat] = 0
    return row


def _apply_events(state: dict, events: List[dict]) -> None:
    """Deterministic aggregation: pure addition, order-independent. Same
    input events always produce the same resulting counts regardless of
    what order games/events were processed in (no season-level average,
    no running max, nothing order-sensitive)."""
    for ev in events:
        pid = str(ev["player_id"])
        row = state["players"].setdefault(pid, _empty_player_row(ev["player_id"], ev["player_name"]))
        # A player's real display name can't change retroactively within
        # one season's data -- keep the first one seen rather than
        # rewriting it every event (cheap, deterministic either way).
        cat = ev["category"]
        row[cat] += 1
        row["total"] += 1
        if cat == CATEGORY_UNKNOWN:
            subtype = ev["subtype"] or "(empty)"
            bucket = state["unknown_subtypes"].setdefault(subtype, {"count": 0, "example": ev["description"]})
            bucket["count"] += 1


def _atomic_write(path: Path, state: dict) -> None:
    """Write-to-temp-then-rename so a killed process (Ctrl-C, crash,
    session interruption) can NEVER leave a half-written/corrupt cache
    file -- the file at `path` is always either the previous complete
    state or the new complete state, never a partial write."""
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def load_turnover_cache(season: str) -> Optional[dict]:
    path = _turnover_cache_path(season)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def ingest_season_turnovers(
    season: str,
    force: bool = False,
    max_games: Optional[int] = None,
    save_every: int = 20,
    sleep_between: float = 0.3,
    timeout: int = 20,
    retries: int = 4,
) -> dict:
    """
    Resumable ingestion of one season's real per-game turnover events.

    - Resumable: reads the existing cache (unless force=True) and only
      fetches games NOT already in `games_done`.
    - Interruption-safe: the cache file is rewritten atomically every
      `save_every` completed games (and once more at the end) -- killing
      this process mid-run loses at most the last partial batch, never
      corrupts what was already saved.
    - Deterministic: `load_schedule(season)` returns a fixed, already-
      chronologically-sorted real game list; aggregation is pure
      addition (see _apply_events) -- re-running to completion from any
      partial state produces the identical final totals.
    - A game that fails after all retries is recorded in `games_failed`
      (and NOT in `games_done`) so a later run retries it automatically,
      without silently losing track of it or re-fetching already-done
      games.
    """
    from loader import load_schedule  # local import: avoids a hard dependency for callers that only read the cache

    cache_path = _turnover_cache_path(season)
    if force or not cache_path.exists():
        state = _new_state(season)
    else:
        state = load_turnover_cache(season)
        if state.get("cache_version") != TURNOVER_CACHE_VERSION:
            raise RuntimeError(
                f"{cache_path} was built with cache_version={state.get('cache_version')}, "
                f"this code expects {TURNOVER_CACHE_VERSION}. Re-run with force=True to rebuild."
            )

    games = load_schedule(season)
    game_ids = [g.game_id for g in games]
    if max_games is not None:
        game_ids = game_ids[:max_games]
    state["games_total"] = len(game_ids)

    done = set(state["games_done"])
    remaining = [gid for gid in game_ids if gid not in done]

    since_save = 0
    for gid in remaining:
        try:
            events = fetch_game_turnovers(gid, retries=retries, timeout=timeout)
        except Exception:
            if gid not in state["games_failed"]:
                state["games_failed"].append(gid)
            _atomic_write(cache_path, state)
            since_save = 0
            continue

        _apply_events(state, events)
        state["games_done"].append(gid)
        if gid in state["games_failed"]:
            state["games_failed"].remove(gid)
        since_save += 1
        if since_save >= save_every:
            _atomic_write(cache_path, state)
            since_save = 0
        if sleep_between:
            time.sleep(sleep_between)

    _atomic_write(cache_path, state)
    return state


def summarize_coverage(state: dict) -> dict:
    """Real, honest coverage numbers for one season's cache -- classified
    %, unknown %, most frequent unknown subtypes."""
    total_events = sum(row["total"] for row in state["players"].values())
    unknown_events = sum(row[CATEGORY_UNKNOWN] for row in state["players"].values())
    classified = total_events - unknown_events
    top_unknown = sorted(state["unknown_subtypes"].items(), key=lambda kv: -kv[1]["count"])[:10]
    return {
        "season": state["season"],
        "games_total": state["games_total"],
        "games_done": len(state["games_done"]),
        "games_failed": len(state["games_failed"]),
        "total_turnover_events": total_events,
        "classified_pct": round(100.0 * classified / total_events, 2) if total_events else None,
        "unknown_pct": round(100.0 * unknown_events / total_events, 2) if total_events else None,
        "top_unknown_subtypes": top_unknown,
    }


def validate_against_box_score(season: str, min_gp: int = 1) -> dict:
    """
    Compares this season's cached PBP-derived total turnovers per player
    against the same season's real box-score `tov` total (loader.load_teams)
    -- matched by name, the same load-bearing key the rest of this
    codebase already uses (see PLAYER_ABILITY_HANDOFF.md Part 8 on the
    real, inherited name-matching risk -- not introduced here).

    Only meaningful for games actually ingested so far (`games_done` vs
    `games_total`) -- a season ingested at, say, 30/1230 games will show
    real per-player box totals well above real PBP totals for players
    whose games mostly weren't in that partial sample yet. Reports
    `games_done`/`games_total` alongside every number so that partial
    coverage is never mistaken for a real discrepancy.
    """
    from loader import load_teams, load_player_advanced_stats

    state = load_turnover_cache(season)
    if state is None:
        raise FileNotFoundError(f"No turnover cache for {season} -- run ingest_season_turnovers first.")

    teams = load_teams(season)
    adv = load_player_advanced_stats(season)  # real per-player "gp" -- Player itself only stores per-game averages
    box_tov_by_name: Dict[str, float] = {}
    for team in teams.values():
        for p in team.players:
            gp = adv.get(p.name, {}).get("gp", 0)
            if gp < min_gp:
                continue
            box_tov_by_name[p.name] = box_tov_by_name.get(p.name, 0.0) + p.tov * gp

    pbp_tov_by_name: Dict[str, int] = {}
    for row in state["players"].values():
        pbp_tov_by_name[row["player_name"]] = pbp_tov_by_name.get(row["player_name"], 0) + row["total"]

    diffs = []
    for name, pbp_total in pbp_tov_by_name.items():
        box_total = box_tov_by_name.get(name)
        if box_total is None:
            continue
        diffs.append({"name": name, "pbp_total": pbp_total, "box_total_scaled": round(box_total, 1)})

    return {
        "season": season,
        "games_done": len(state["games_done"]),
        "games_total": state["games_total"],
        "players_compared": len(diffs),
        "sample": sorted(diffs, key=lambda d: -d["pbp_total"])[:15],
    }
