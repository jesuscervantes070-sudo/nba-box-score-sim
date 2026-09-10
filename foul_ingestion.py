"""
Phase 6 -- Foul Drawing + Foul Discipline: resumable, per-game foul-event
ingestion. Same resumable/interruption-safe/deterministic discipline as
turnover_ingestion.py (Phase 4A/4B) -- imports `load_schedule` and
`data_source._season_cache_dir` only; writes its own new cache file,
never touches game_engine.py/season.py/awards.py/models.py/transactions.py.

============================ SOURCE VERIFIED DIRECTLY ============================
`playbyplayv3` real `actionType == "Foul"` rows carry a real, structured
`subType` -- confirmed across 1996-97, 2005-06, 2013-14, 2023-24. Real
subtypes observed (union across all four eras):
  "Shooting", "Shooting Block" -- a real defensive shooting foul (the
      "Block" suffix means it happened during a block attempt, same real
      foul type).
  "Personal", "Loose Ball", "Personal Block" -- real non-shooting
      defensive fouls (contact/positioning during live-ball action).
  "Offensive", "Offensive Charge" -- real OFFENSIVE fouls (committed BY
      the ball-handler, not a defender) -- excluded from both attributes.
  "Technical", "Double Technical", "Delay Technical" -- excluded (not
      player-attributable action; "Delay Technical" isn't even always a
      specific player -- confirmed team-level descriptions like "LAKERS
      Foul").
  "Flagrant Type 1" (checked directly, 2013-14+) -- excluded: excessive/
      illegal contact, not representative of normal legitimate defense.
  "Transition Take", "Personal Take" -- real TAKE FOULS (intentional fouls
      to stop a fast break) -- excluded per this phase's explicit scope.
  "Flopping" -- a real penalty ON THE FLOPPER, not a defensive foul at
      all -- excluded from both.
  "Defense 3 Second" -- a real procedural violation (illegal defense),
      independent of any attacker action or physical contact -- excluded
      from both (fits neither definition).
  "Away From Play" -- a real foul away from the ball (rebounding scrums,
      end-game hack strategy) -- excluded per this phase's explicit scope.
Any subtype NOT in this map is preserved as `other_unclassified` with a
real example description -- never silently dropped or forced into another
bucket.

============================ WHO DREW / WHO COMMITTED ============================
The Foul row's own `personId` is the FOULER (defender, for every real
defensive-foul subtype above) -- directly usable for FOUL_DISCIPLINE's
numerator (fouls committed), no extra lookup needed, and NOT gated on
whether the foul produced free throws.

The row does NOT identify who was FOULED. Verified directly: every real
Shooting foul, and every real non-shooting foul called while the fouled
team is in the bonus (its description carries a real "PN" penalty
marker), is immediately followed by one or more real Free Throw events
whose `personId` IS the fouled player -- confirmed across dozens of real
examples this session. This is the FOUL_DRAWING attribution mechanism:
match each in-scope Foul event to its immediately-following Free Throw
event(s) and credit that shooter. A non-bonus common foul (no following
free throws) has NO reliable drawer-attribution signal in this data --
excluded from FOUL_DRAWING's numerator this phase, a real, stated
coverage limitation (not silently guessed).

============================ AND-1 DE-DUPLICATION (verified directly) ============================
A made shot immediately followed by a Shooting-foul row is NOT
automatically an and-1 -- checked directly and found REAL FALSE POSITIVES
(a made basket that ends one possession, followed immediately by an
unrelated foul on the very next possession, credits a DIFFERENT player's
free throws). The real and-1 signature, verified directly: the made
shot's shooter's `personId` EQUALS the immediately-following free-throw
event's `personId`, AND that free throw is a real "1 of 1" (a normal,
non-and-1 shooting-foul-on-a-miss produces "1 of 2"/"2 of 2" instead).
This module counts the SHOOTING FOUL EVENT ITSELF exactly once per real
occurrence (and-1 or not) -- the made shot's own evidence belongs
entirely to `shot_zone_ingestion.py`'s independent FGM/FGA pipeline
(Phase 5), a completely separate data source. No event is ever counted
toward both an and-1 flag AND an extra foul-drawing credit; `and_one`
below is a diagnostic-only flag, never a second numerator contribution.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from data_source import _season_cache_dir

FOUL_CACHE_VERSION = 1
_TEAM_ID_PREFIX = "1610612"

# Real subType -> category. See module docstring for how this was built.
CATEGORY_SHOOTING_FOUL = "shooting_foul"           # eligible for BOTH attributes
CATEGORY_NONSHOOTING_DEF_FOUL = "nonshooting_def_foul"  # eligible for BOTH (drawing gated on bonus/FT)
CATEGORY_OFFENSIVE_FOUL = "offensive_foul"         # excluded from both -- diagnostic only
CATEGORY_EXCLUDED_OTHER = "excluded_other"         # technical/flagrant/take/flop/3sec/away-from-play
CATEGORY_UNKNOWN = "other_unclassified"

SUBTYPE_CATEGORY_MAP: Dict[str, str] = {
    "Shooting": CATEGORY_SHOOTING_FOUL,
    "Shooting Block": CATEGORY_SHOOTING_FOUL,
    "Personal": CATEGORY_NONSHOOTING_DEF_FOUL,
    "Loose Ball": CATEGORY_NONSHOOTING_DEF_FOUL,
    "Personal Block": CATEGORY_NONSHOOTING_DEF_FOUL,
    "Offensive": CATEGORY_OFFENSIVE_FOUL,
    "Offensive Charge": CATEGORY_OFFENSIVE_FOUL,
    "Technical": CATEGORY_EXCLUDED_OTHER,
    "Double Technical": CATEGORY_EXCLUDED_OTHER,
    "Delay Technical": CATEGORY_EXCLUDED_OTHER,
    "Flagrant Type 1": CATEGORY_EXCLUDED_OTHER,
    "Flagrant Type 2": CATEGORY_EXCLUDED_OTHER,
    "Transition Take": CATEGORY_EXCLUDED_OTHER,
    "Personal Take": CATEGORY_EXCLUDED_OTHER,
    "Flopping": CATEGORY_EXCLUDED_OTHER,
    "Defense 3 Second": CATEGORY_EXCLUDED_OTHER,
    "Away From Play": CATEGORY_EXCLUDED_OTHER,
}


def classify_foul_subtype(subtype: Optional[str]) -> str:
    if not subtype:
        return CATEGORY_UNKNOWN
    return SUBTYPE_CATEGORY_MAP.get(subtype, CATEGORY_UNKNOWN)


def _is_team_event(person_id) -> bool:
    if person_id is None:
        return False
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return False
    return str(pid).startswith(_TEAM_ID_PREFIX)


def _foul_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_foul_events.json"


def fetch_game_foul_events(game_id: str, retries: int = 4, timeout: int = 20) -> List[dict]:
    """
    Every real in-scope foul event in one real game:
    [{"drawer_player_id", "committer_player_id", "category", "subtype",
      "and_one" (diagnostic only), "description"}].

    `committer_player_id` is always the Foul row's own real `personId`
    (skipped if it's a team-level event). `drawer_player_id` is filled in
    ONLY when a real, immediately-following Free Throw event identifies
    the fouled shooter (see module docstring); otherwise None -- a real,
    honest "cannot attribute a drawer" case for a non-bonus common foul.
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

    df = df.reset_index(drop=True)
    events = []
    foul_rows = df.index[df["actionType"] == "Foul"].tolist()
    for idx in foul_rows:
        row = df.loc[idx]
        committer_id = row.get("personId")
        if committer_id is None or _is_team_event(committer_id):
            continue
        subtype = row.get("subType") or ""
        category = classify_foul_subtype(subtype)

        # Real and-1 check (diagnostic flag only -- see module docstring;
        # never contributes a second numerator credit).
        and_one = False
        if category == CATEGORY_SHOOTING_FOUL and idx > 0:
            prev = df.loc[idx - 1]
            if prev.get("actionType") == "Made Shot":
                nxt_ft = df.loc[idx + 1] if idx + 1 < len(df) else None
                if (nxt_ft is not None and nxt_ft.get("actionType") == "Free Throw"
                        and nxt_ft.get("personId") == prev.get("personId")
                        and "1 of 1" in (nxt_ft.get("description") or "")):
                    and_one = True

        # Real drawer attribution: the immediately-following Free Throw
        # event's real personId, for shooting fouls (always) and
        # bonus-triggering non-shooting fouls (only when one actually
        # follows -- a non-bonus common foul has none, real coverage gap).
        drawer_id = None
        if category in (CATEGORY_SHOOTING_FOUL, CATEGORY_NONSHOOTING_DEF_FOUL) and idx + 1 < len(df):
            nxt = df.loc[idx + 1]
            if nxt.get("actionType") == "Free Throw":
                drawer_id = nxt.get("personId")
            # else: no FT immediately follows -- a real non-bonus common
            # foul, no reliable drawer signal in this data (see docstring).

        events.append({
            "committer_player_id": int(committer_id),
            "drawer_player_id": int(drawer_id) if drawer_id is not None else None,
            "category": category,
            "subtype": subtype,
            "and_one": and_one,
            "description": row.get("description") or "",
        })
    return events


def _new_state(season: str) -> dict:
    return {
        "season": season,
        "source": "playbyplayv3",
        "cache_version": FOUL_CACHE_VERSION,
        "games_total": 0,
        "games_done": [],
        "games_failed": [],
        "committers": {},  # player_id -> {shooting_foul, nonshooting_def_foul, offensive_foul, excluded_other, other_unclassified, total_committed}
        "drawers": {},     # player_id -> {shooting_foul_drawn, nonshooting_def_foul_drawn, and_ones, total_drawn}
        "unknown_subtypes": {},
        "last_updated": None,
    }


def _empty_committer_row(pid: int) -> dict:
    return {"player_id": pid, "shooting_foul": 0, "nonshooting_def_foul": 0,
            "offensive_foul": 0, "excluded_other": 0, "other_unclassified": 0, "total_committed": 0}


def _empty_drawer_row(pid: int) -> dict:
    return {"player_id": pid, "shooting_foul_drawn": 0, "nonshooting_def_foul_drawn": 0,
            "and_ones": 0, "total_drawn": 0}


def _apply_events(state: dict, events: List[dict]) -> None:
    """Deterministic, order-independent aggregation -- same discipline as
    turnover_ingestion._apply_events."""
    for ev in events:
        cid = str(ev["committer_player_id"])
        crow = state["committers"].setdefault(cid, _empty_committer_row(ev["committer_player_id"]))
        cat = ev["category"]
        crow[cat] += 1
        crow["total_committed"] += 1
        if cat == CATEGORY_UNKNOWN:
            subtype = ev["subtype"] or "(empty)"
            bucket = state["unknown_subtypes"].setdefault(subtype, {"count": 0, "example": ev["description"]})
            bucket["count"] += 1

        if ev["drawer_player_id"] is not None and cat in (CATEGORY_SHOOTING_FOUL, CATEGORY_NONSHOOTING_DEF_FOUL):
            did = str(ev["drawer_player_id"])
            drow = state["drawers"].setdefault(did, _empty_drawer_row(ev["drawer_player_id"]))
            if cat == CATEGORY_SHOOTING_FOUL:
                drow["shooting_foul_drawn"] += 1
            else:
                drow["nonshooting_def_foul_drawn"] += 1
            drow["total_drawn"] += 1
            if ev["and_one"]:
                drow["and_ones"] += 1


def _atomic_write(path: Path, state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def load_foul_cache(season: str) -> Optional[dict]:
    path = _foul_cache_path(season)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def ingest_season_fouls(
    season: str, force: bool = False, max_games: Optional[int] = None,
    save_every: int = 25, sleep_between: float = 0.3, timeout: int = 20, retries: int = 4,
) -> dict:
    """Resumable, interruption-safe, deterministic -- identical shape to
    turnover_ingestion.ingest_season_turnovers (see its docstring for the
    exact guarantees; not re-derived here)."""
    from loader import load_schedule

    cache_path = _foul_cache_path(season)
    if force or not cache_path.exists():
        state = _new_state(season)
    else:
        state = load_foul_cache(season)
        if state.get("cache_version") != FOUL_CACHE_VERSION:
            raise RuntimeError(
                f"{cache_path} was built with cache_version={state.get('cache_version')}, "
                f"this code expects {FOUL_CACHE_VERSION}. Re-run with force=True to rebuild."
            )

    games = load_schedule(season)
    all_game_ids = [g.game_id for g in games]
    # `games_total` ALWAYS reflects the real, full season length --
    # NEVER the `max_games` cap for this particular call. A real bug,
    # found and fixed this phase: if `games_total` were set to the
    # capped attempt count (e.g. 300), a season deliberately sampled at
    # max_games=300 would look "100% complete" (300 done / 300 "total")
    # to any downstream scaling logic (see foul_analysis.py's
    # `_scale_to_full_season`), silently skipping the real, needed
    # partial-coverage scaling and corrupting cross-season comparisons.
    # `max_games` only limits how many games THIS CALL attempts.
    state["games_total"] = len(all_game_ids)
    game_ids = all_game_ids if max_games is None else all_game_ids[:max_games]

    done = set(state["games_done"])
    remaining = [gid for gid in game_ids if gid not in done]

    since_save = 0
    for gid in remaining:
        try:
            events = fetch_game_foul_events(gid, retries=retries, timeout=timeout)
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
    total_committed = sum(row["total_committed"] for row in state["committers"].values())
    unknown = sum(row["other_unclassified"] for row in state["committers"].values())
    shooting = sum(row["shooting_foul"] for row in state["committers"].values())
    nonshooting = sum(row["nonshooting_def_foul"] for row in state["committers"].values())
    offensive = sum(row["offensive_foul"] for row in state["committers"].values())
    excluded = sum(row["excluded_other"] for row in state["committers"].values())
    total_drawn_attributed = sum(row["total_drawn"] for row in state["drawers"].values())
    top_unknown = sorted(state["unknown_subtypes"].items(), key=lambda kv: -kv[1]["count"])[:10]
    return {
        "season": state["season"],
        "games_total": state["games_total"],
        "games_done": len(state["games_done"]),
        "games_failed": len(state["games_failed"]),
        "total_foul_events": total_committed,
        "shooting_foul_pct": round(100.0 * shooting / total_committed, 2) if total_committed else None,
        "nonshooting_def_foul_pct": round(100.0 * nonshooting / total_committed, 2) if total_committed else None,
        "offensive_foul_pct": round(100.0 * offensive / total_committed, 2) if total_committed else None,
        "excluded_other_pct": round(100.0 * excluded / total_committed, 2) if total_committed else None,
        "unknown_pct": round(100.0 * unknown / total_committed, 2) if total_committed else None,
        "total_drawn_events_attributed": total_drawn_attributed,
        "drawn_attribution_rate": round(100.0 * total_drawn_attributed / (shooting + nonshooting), 2) if (shooting + nonshooting) else None,
        "top_unknown_subtypes": top_unknown,
    }
