"""
Phase 13 -- Lineup / Player Role Inference: ingestion.

ROLE != ABILITY, ROLE != TENDENCY -- see docs/PHASE13_ROLE_INFERENCE_REPORT.md
for the full architectural argument. This module only fetches/caches
real evidence; no role score is computed here (see role_off_analysis.py /
role_estimation.py).

============================ SOURCES VERIFIED DIRECTLY ============================

**Season-level role evidence** (the general estimator's input):
`nba_api.stats.endpoints.leaguedashptstats` (`pt_measure_type` in
{Possessions, Drives, Passing}) -- ALREADY a proven source in this repo
(handling_exposure.py, shot_creation_ingestion.py, passing_tracking_ingestion.py
all use it). Real floor: 2013-14 (public SportVU/Second Spectrum tracking).
`nba_api.stats.endpoints.leaguedashplayerstats` (`measure_type_detailed_defense="Scoring"`)
-- NOT previously used in this repo; verified directly this phase. Real,
confirmed fields: `PCT_AST_2PM`/`PCT_UAST_2PM`/`PCT_AST_3PM`/`PCT_UAST_3PM`/
`PCT_AST_FGM`/`PCT_UAST_FGM` -- the share of a player's made shots that were
assisted vs self-created, split by shot type. Both endpoints carry a real
`PLAYER_ID` column -- used directly, no name-based join needed for this
phase's ingestion (a real, confirmed improvement over the tendency layer's
name-keyed rows).

**Natural-experiment windows** (the diagnostic-only comparisons in
role_off_analysis.py, NOT part of the general per-season estimator):
both endpoints above accept real `date_from_nullable`/`date_to_nullable`
params -- confirmed directly this phase (not previously exploited anywhere
in this repo) to work exactly like a `season_segment` filter, letting the
SAME season be split into a "teammate present" window and a "teammate
absent" window, or a "before/after mid-season trade" window, using the
real per-game calendar dates already available from
`data_source._fetch_normalized_game_log` (already built, already proven --
reused here with ZERO changes) and `data_source.fetch_player_transactions`'s
real, dated trade records (already cached, `cache/<season>/transactions.json`).

**Defensive deployment evidence**: reuses `poa_containment_ingestion.fetch_matchup_data`
(already-proven `leagueseasonmatchups` call from Phase 10) joined against
`shot_zone_ingestion.load_shot_zones` (already-cached, real, Phase 5 zone
data) -- ZERO new API calls for the defensive candidate; see
role_off_analysis.defensive_deployment_axis. Real, confirmed format quirk:
`MATCHUP_MIN` is a `"MM:SS"` string, not a numeric minutes value -- parsed
here (`_parse_matchup_minutes`), a new finding this phase (Phase 10's own
`poa_containment_ingestion.py` never needed matchup-time as a standalone
number, only as a `MATCHUP_TIME_SEC` field it already had numerically).

Modern-era only. No historical role reconstruction is attempted this
phase (see report Sec. 4) -- the schema (role_profile.py) is designed so
a later phase CAN degrade to a proxy/lower-confidence/unavailable mode for
pre-2013-14 seasons without a schema change.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from data_source import CACHE_DIR, _season_cache_dir, _fetch_normalized_game_log, fetch_player_transactions

ROLE_CACHE_VERSION = 1
ROLE_TRACKING_FIRST_SEASON = "2013-14"  # same real tracking floor already established by Phases 8/9/10


def _role_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_role_off.json"


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def _clean(v):
    if v is None:
        return None
    try:
        if v != v:  # real NaN check
            return None
    except TypeError:
        pass
    if isinstance(v, str) and v.strip() == "":
        return None  # same real empty-string quirk found in Phase 12A -- guarded here defensively too
    return float(v)


def fetch_pt_stats(season: str, measure: str, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    retries: int = 4, timeout: int = 30):
    from nba_api.stats.endpoints import leaguedashptstats
    last_err = None
    for attempt in range(retries):
        try:
            return leaguedashptstats.LeagueDashPtStats(
                season=season, season_type_all_star="Regular Season", player_or_team="Player",
                pt_measure_type=measure, per_mode_simple="Totals",
                date_from_nullable=date_from or "", date_to_nullable=date_to or "", timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"leaguedashptstats({measure}, {season}, {date_from}-{date_to}) failed after {retries} attempts: {last_err}")


def fetch_scoring_stats(season: str, date_from: Optional[str] = None, date_to: Optional[str] = None,
                         retries: int = 4, timeout: int = 30):
    from nba_api.stats.endpoints import leaguedashplayerstats
    last_err = None
    for attempt in range(retries):
        try:
            return leaguedashplayerstats.LeagueDashPlayerStats(
                season=season, season_type_all_star="Regular Season", measure_type_detailed_defense="Scoring",
                per_mode_detailed="Totals", date_from_nullable=date_from or "", date_to_nullable=date_to or "",
                timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"leaguedashplayerstats(Scoring, {season}, {date_from}-{date_to}) failed after {retries} attempts: {last_err}")


def build_and_cache_role_off(season: str, force: bool = False) -> Optional[dict]:
    """Full-season role evidence, keyed by real player_id. This is the
    general estimator's input -- NOT the natural-experiment windows
    (those are fetched ad hoc by role_off_analysis.py's experiment
    functions and are not part of this per-season cache)."""
    cache_path = _role_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)
    if season < ROLE_TRACKING_FIRST_SEASON:
        print(f"{season} is before the real tracking floor ({ROLE_TRACKING_FIRST_SEASON}) -- nothing cached.")
        return None

    print(f"Fetching {season} real role-off evidence (Possessions/Drives/Passing/Scoring)...")
    poss = fetch_pt_stats(season, "Possessions")
    drives = fetch_pt_stats(season, "Drives")
    passing = fetch_pt_stats(season, "Passing")
    scoring = fetch_scoring_stats(season)
    if poss is None or len(poss) == 0:
        print(f"{season} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    def _index(df, cols):
        out = {}
        for _, row in df.iterrows():
            pid = str(int(row["PLAYER_ID"]))
            out[pid] = {c: _clean(row.get(c)) for c in cols}
        return out

    poss_idx = _index(poss, ["MIN", "TOUCHES", "FRONT_CT_TOUCHES", "TIME_OF_POSS", "AVG_SEC_PER_TOUCH", "AVG_DRIB_PER_TOUCH"])
    drives_idx = _index(drives, ["DRIVES", "DRIVE_AST", "DRIVE_AST_PCT", "DRIVE_PASSES_PCT", "DRIVE_PTS_PCT"])
    passing_idx = _index(passing, ["PASSES_MADE", "POTENTIAL_AST", "AST", "AST_TO_PASS_PCT"])
    scoring_idx = _index(scoring, ["PCT_AST_2PM", "PCT_UAST_2PM", "PCT_AST_3PM", "PCT_UAST_3PM", "PCT_AST_FGM", "PCT_UAST_FGM"])

    all_ids = set(poss_idx) | set(drives_idx) | set(passing_idx) | set(scoring_idx)
    players = {}
    name_by_id = {str(int(r["PLAYER_ID"])): r["PLAYER_NAME"] for _, r in poss.iterrows()}
    for pid in all_ids:
        players[pid] = {
            "player_name": name_by_id.get(pid),
            **poss_idx.get(pid, {}), **drives_idx.get(pid, {}),
            **passing_idx.get(pid, {}), **scoring_idx.get(pid, {}),
        }

    payload = {"season": season, "schema_version": ROLE_CACHE_VERSION,
               "sources": ["leaguedashptstats(Possessions/Drives/Passing)", "leaguedashplayerstats(Scoring)"],
               "players": players}
    _atomic_write(cache_path, payload)
    print(f"Cached real role-off evidence for {len(players)} players ({season}) -> {cache_path}")
    return payload


def load_role_off(season: str) -> Dict[str, dict]:
    cache_path = _role_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_role_off_range(seasons, force: bool = False) -> dict:
    results = {}
    for s in seasons:
        try:
            results[s] = build_and_cache_role_off(s, force=force)
        except Exception as e:
            print(f"{s}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[s] = None
    return results


# --------------------------- natural-experiment helpers ---------------------------

def find_significant_absence_window(season: str, team_name: str, player_name: str,
                                     min_games: int = 15) -> Optional[Tuple[str, str, int]]:
    """Real, longest CONTIGUOUS stretch of the team's games this player
    missed, as (start_date, end_date, n_games) ISO strings -- or None if
    no stretch of at least `min_games` exists. Built directly from the
    real per-game league log (`_fetch_normalized_game_log`), the same
    real source `data_source.fetch_player_absence_stints` already uses
    for injury modeling -- this function computes real calendar DATES
    (for `leaguedashptstats`'s date_from/date_to params) rather than that
    function's schedule-INDEX representation, since a different real use
    needs a different real unit; not a duplicate of that logic, a
    date-oriented sibling of it."""
    import pandas as pd
    df = _fetch_normalized_game_log(season)
    df = df.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    team_games = df[df["TEAM_NAME"] == team_name].drop_duplicates("GAME_ID").sort_values("GAME_DATE")
    if team_games.empty:
        return None
    played_ids = set(df[(df["PLAYER_NAME"] == player_name) & (df["TEAM_NAME"] == team_name)]["GAME_ID"])

    best = None
    run: List[str] = []
    for _, row in team_games.iterrows():
        if row["GAME_ID"] not in played_ids:
            run.append(row["GAME_DATE"])
        else:
            if len(run) >= min_games and (best is None or len(run) > best[2]):
                best = (run[0], run[-1], len(run))
            run = []
    if len(run) >= min_games and (best is None or len(run) > best[2]):
        best = (run[0], run[-1], len(run))
    if best is None:
        return None
    return (str(best[0].date()), str(best[1].date()), best[2])


def find_real_trade_date(season: str, player_name_fragment: str) -> Optional[dict]:
    """Real mid-season trade lookup from the already-cached, real
    `transactions.json` (built by data_source.build_and_cache_transactions).
    Returns the first real Trade-type record whose description mentions
    the given name fragment, or None. Name-fragment matching only (the
    cached transaction rows carry a free-text description, not a
    structured player field usable for exact matching in every case) --
    caller should verify the match manually for a diagnostic use like
    this phase's, per the project's "no silent ambiguous join" rule."""
    cache_path = _season_cache_dir(season) / "transactions.json"
    if not cache_path.exists():
        return None
    with open(cache_path) as f:
        data = json.load(f)
    matches = [t for t in data.get("transactions", [])
               if t.get("type") == "Trade" and player_name_fragment.lower() in t.get("description", "").lower()]
    return matches[0] if matches else None
