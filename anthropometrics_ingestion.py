"""
Phase 12A -- Player Anthropometrics Foundation: ingestion. Entirely
offline/parallel. Geometry/body-state only -- NOT a basketball skill,
NOT wired into `PlayerAbilityProfile`, NOT residualized against any
existing skill estimator this phase.

============================ SOURCE VERIFIED DIRECTLY ============================
**Tier A -- MEASURED_COMBINE** (highest quality, preferred):
`nba_api.stats.endpoints.draftcombineplayeranthro`, `season_year=<draft
class year>`. Real, verified columns: `PLAYER_ID`, `PLAYER_NAME`,
`POSITION`, `HEIGHT_WO_SHOES` (real, BAREFOOT height in inches --
decimal, e.g. 75.25), `HEIGHT_W_SHOES`, `WEIGHT` (lbs), `WINGSPAN`
(inches), `STANDING_REACH` (inches), `BODY_FAT_PCT`, `HAND_LENGTH/WIDTH`.

Real floor, checked directly: 1990/1995/1998/1999 all return 0 rows;
**2000 is the first real draft class with combine anthro data** (65
real rows). Cheap: one call per draft-class year, 26 real years
(2000-2025) fully covered in well under a minute.

**Real, confirmed data-quality finding**: `HEIGHT_W_SHOES` (shoes-on
height) is NOT consistently measured across the whole combine era --
checked directly: 2000 has ZERO `HEIGHT_W_SHOES` rows (65/65 barefoot
only), 2010-2020 has BOTH fully populated, 2023+ has ZERO `HEIGHT_W_SHOES`
again (the NBA stopped recording shoes-on height in recent combines).
**`HEIGHT_WO_SHOES` (barefoot) is the only field with continuous real
coverage across the entire 2000-2025 range** -- adopted as the primary
real height field, per the task's own "prefer official combine barefoot
height" guidance, now empirically confirmed necessary (not just
preferred) for coverage continuity. NO fixed shoe-adjustment constant is
applied anywhere in this module -- `HEIGHT_W_SHOES` is preserved as raw
auxiliary evidence only where it exists, never synthesized.

Real, confirmed non-random missingness even WITHIN a combine year:
2023's real anthro table has 81 total invited players but only 67 real
`HEIGHT_WO_SHOES` measurements -- some combine attendees skip
anthropometric testing. Never imputed.

**Tier B -- MEASURED_ROSTER**: `nba_api.stats.endpoints.commonteamroster`
-- the SAME real endpoint `data_source.fetch_team_rosters` already calls
for `cache/<season>/rosters.json` (which currently extracts ONLY player
names, discarding the real `HEIGHT`/`WEIGHT`/`PLAYER_ID` columns already
present on every response). This module re-fetches the same real,
already-proven endpoint to also extract those real fields -- NOT a new
source, and `rosters.json` itself is untouched (this writes its own new
cache file). Real `HEIGHT` is a string like `"6-5"` (feet-inches,
LISTED, not measured) -- parsed to real inches. Given the real cost (30
team calls per season, ~0.6s throttle each, same as the existing roster
fetch), this phase ingests a representative SAMPLE of seasons (not a
full 30-season backfill) -- see report for exactly which, and why a
full historical backfill is deferred, not silently skipped.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from data_source import CACHE_DIR, _season_cache_dir, _historical_team_name

ANTHRO_CACHE_VERSION = 1
COMBINE_FIRST_DRAFT_YEAR = 2000  # real floor, checked directly

MEASURED_COMBINE = "MEASURED_COMBINE"
MEASURED_ROSTER = "MEASURED_ROSTER"


def _combine_cache_path(draft_year: int) -> Path:
    d = CACHE_DIR / "anthropometrics"
    d.mkdir(exist_ok=True)
    return d / f"combine_{draft_year}.json"


def _roster_physical_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_physical_roster.json"


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def fetch_combine_anthro(draft_year: int, retries: int = 4, timeout: int = 30):
    from nba_api.stats.endpoints import draftcombineplayeranthro
    last_err = None
    for attempt in range(retries):
        try:
            return draftcombineplayeranthro.DraftCombinePlayerAnthro(season_year=draft_year, timeout=timeout).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"draftcombineplayeranthro({draft_year}) failed after {retries} attempts: {last_err}")


def build_and_cache_combine_anthro(draft_year: int, force: bool = False) -> Optional[dict]:
    cache_path = _combine_cache_path(draft_year)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)
    if draft_year < COMBINE_FIRST_DRAFT_YEAR:
        print(f"{draft_year} is before the real combine-anthro floor ({COMBINE_FIRST_DRAFT_YEAR}) -- nothing cached.")
        return None

    print(f"Fetching {draft_year} real draft combine anthropometrics...")
    df = fetch_combine_anthro(draft_year)
    if df is None or len(df) == 0:
        print(f"{draft_year} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    def _clean(v):
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None  # real, confirmed quirk: some rows carry an empty string instead of NaN/None (e.g. 2016 WEIGHT)
        try:
            if v != v:  # real NaN check
                return None
        except TypeError:
            pass
        return float(v)

    players = {}
    for _, row in df.iterrows():
        pid = str(int(row["PLAYER_ID"]))
        players[pid] = {
            "player_name": row["PLAYER_NAME"],
            "position": row.get("POSITION"),
            "height_wo_shoes_in": _clean(row.get("HEIGHT_WO_SHOES")),
            "height_w_shoes_in": _clean(row.get("HEIGHT_W_SHOES")),  # real auxiliary only, era-inconsistent coverage
            "wingspan_in": _clean(row.get("WINGSPAN")),
            "standing_reach_in": _clean(row.get("STANDING_REACH")),
            "weight_lbs": _clean(row.get("WEIGHT")),
        }
    payload = {"draft_year": draft_year, "source": "draftcombineplayeranthro",
               "schema_version": ANTHRO_CACHE_VERSION, "provenance": "nba_api.stats.endpoints.draftcombineplayeranthro",
               "players": players}
    _atomic_write(cache_path, payload)
    print(f"Cached real combine anthropometrics for {len(players)} players (draft {draft_year}) -> {cache_path}")
    return payload


def load_combine_anthro(draft_year: int) -> Dict[str, dict]:
    cache_path = _combine_cache_path(draft_year)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_combine_anthro_range(draft_years, force: bool = False) -> dict:
    results = {}
    for y in draft_years:
        try:
            results[y] = build_and_cache_combine_anthro(y, force=force)
        except Exception as e:
            print(f"{y}: FAILED ({e}) -- other years' caches are unaffected.")
            results[y] = None
    return results


def _parse_listed_height(height_str: Optional[str]) -> Optional[float]:
    """Real roster `HEIGHT` field is a string like "6-5" (feet-inches,
    LISTED not measured). Returns real inches, or None if unparseable."""
    if not height_str or "-" not in str(height_str):
        return None
    try:
        feet, inches = str(height_str).split("-")
        return float(feet) * 12.0 + float(inches)
    except (ValueError, TypeError):
        return None


def fetch_roster_physicals(season: str, retries: int = 4, timeout: int = 30):
    """Real per-team roster call -- SAME endpoint as
    `data_source.fetch_team_rosters`, re-fetched here to also keep the
    real HEIGHT/WEIGHT/PLAYER_ID columns that fetch already receives but
    discards. Real cost: one call per real team (~30), same throttle."""
    from nba_api.stats.static import teams as static_teams
    from nba_api.stats.endpoints import commonteamroster

    players = {}
    for t in static_teams.get_teams():
        df = None
        last_err = None
        for attempt in range(retries):
            try:
                df = commonteamroster.CommonTeamRoster(team_id=t["id"], season=season, timeout=timeout).get_data_frames()[0]
                break
            except Exception as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        if df is None:
            print(f"  {t['full_name']} ({season}): FAILED ({last_err}) -- skipped, other teams unaffected.")
            continue
        team_name = _historical_team_name(t["id"], season, t["full_name"])
        for _, row in df.iterrows():
            pid = row.get("PLAYER_ID")
            if pid is None:
                continue
            players[str(int(pid))] = {
                "player_name": row["PLAYER"], "team_name": team_name, "position": row.get("POSITION"),
                "listed_height_in": _parse_listed_height(row.get("HEIGHT")),
                "listed_weight_lbs": float(row["WEIGHT"]) if row.get("WEIGHT") not in (None, "") else None,
            }
        time.sleep(0.6)
    return players


def build_and_cache_roster_physicals(season: str, force: bool = False) -> Optional[dict]:
    cache_path = _roster_physical_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)

    print(f"Fetching {season} real roster-listed physicals (commonteamroster, all 30 teams)...")
    players = fetch_roster_physicals(season)
    if not players:
        print(f"{season} returned no real roster-physical data -- nothing cached.")
        return None
    payload = {"season": season, "source": "commonteamroster", "schema_version": ANTHRO_CACHE_VERSION,
               "provenance": "nba_api.stats.endpoints.commonteamroster", "players": players}
    _atomic_write(cache_path, payload)
    print(f"Cached real roster-listed physicals for {len(players)} players ({season}) -> {cache_path}")
    return payload


def load_roster_physicals(season: str) -> Dict[str, dict]:
    cache_path = _roster_physical_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_roster_physicals_range(seasons, force: bool = False) -> dict:
    results = {}
    for s in seasons:
        try:
            results[s] = build_and_cache_roster_physicals(s, force=force)
        except Exception as e:
            print(f"{s}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[s] = None
    return results
