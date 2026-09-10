"""
Phase 10 -- POA Containment: ingestion. Entirely offline/parallel.

============================ SOURCE VERIFIED DIRECTLY ============================
`nba_api.stats.endpoints.leagueseasonmatchups` -- a REAL, first-party
player-vs-player MATCHUP dataset, ONE CALL for the ENTIRE LEAGUE per
season (checked directly: 2023-24 returns 137,763 real
offense/defense-player-pair rows in 0.8s -- genuinely cheap, not a
per-team or per-player loop). Real, verified columns: `OFF_PLAYER_ID`,
`OFF_PLAYER_NAME`, `DEF_PLAYER_ID`, `DEF_PLAYER_NAME`, `MATCHUP_MIN`
(string "mm:ss" -- NOT used, see `MATCHUP_TIME_SEC` below),
`PARTIAL_POSS` (real fractional matchup-possession count),
`MATCHUP_FGM/FGA/FG_PCT` (opponent shooting specifically while THIS
defender was the PRIMARY matchup -- excludes help situations, which are
separately reported as `HELP_FGM/FGA/PERC`), `MATCHUP_AST`,
`MATCHUP_TOV`, `MATCHUP_BLK`, `MATCHUP_FG3M/FG3A/FG_PCT`,
`MATCHUP_FTM/FTA`, `SFL` (shooting-fouls-related, real, used only for
the foul-discipline cross-check), `MATCHUP_TIME_SEC` (real, numeric
seconds -- used instead of parsing the string `MATCHUP_MIN`).

Real floor, checked directly: 2015-16 and earlier return 0 rows; 2016-17
returns only 3,515 rows (a real PARTIAL rollout, same pattern as this
codebase's own documented hustle-stats partial-rollout season) vs.
2017-18's full 132,489 -- **the real, reliable floor is 2017-18**;
2016-17 exists but is flagged unreliable (too sparse to trust), same
honest treatment Phase 4B gave hustle stats' own partial-rollout year.

`MATCHUP_FGM/FGA/FG_PCT` (primary-matchup-only shooting) is critically
DIFFERENT from -- and better than -- the DFG%-style metric the task
warned against: it structurally excludes HELP situations already (a
separate, real `HELP_FGM/FGA` field exists on the same row), so team/
scheme contamination from help defenders is NOT baked into this specific
number the way blended opponent-FG%/DFG% would be.

============================ OPPONENT-QUALITY ADJUSTMENT (real, not hand-written) ============================
At ingestion time, for every real matchup row, this player's REAL
overall season FG% (from already-cached box stats, `loader.load_teams`)
is looked up as the "expected" shooting baseline for that specific
offensive player -- NOT a league average, NOT a hand-written star-player
discount. `(expected_fg_pct - MATCHUP_FG_PCT)`, weighted by real
`MATCHUP_FGA`, is the per-row containment contribution, aggregated into
one compact PER-DEFENDER cache (not the raw ~130K-row matchup table,
which is discarded after aggregation -- keeps the cache small,
~500-600 rows/season like every other player-level cache in this
project).
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from data_source import _season_cache_dir

POA_CACHE_VERSION = 1
POA_FIRST_SEASON = "2017-18"  # real, reliable floor -- see module docstring
POA_PARTIAL_SEASON = "2016-17"  # real but too sparse (3,515 rows vs ~130K) -- not used


def _poa_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_poa_containment.json"


def fetch_matchup_data(season: str, retries: int = 4, timeout: int = 90):
    from nba_api.stats.endpoints import leagueseasonmatchups
    last_err = None
    for attempt in range(retries):
        try:
            return leagueseasonmatchups.LeagueSeasonMatchups(season=season, timeout=timeout).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"leagueseasonmatchups({season}) failed after {retries} attempts: {last_err}")


def _real_season_fg_pct_by_name(season: str) -> Dict[str, float]:
    """Real per-player overall season FG% (fgm/fga from the existing,
    already-cached box stats) -- the real "expected" baseline for the
    opponent-quality adjustment. NOT a new API call."""
    from loader import load_teams
    teams = load_teams(season)
    result = {}
    for team in teams.values():
        for p in team.players:
            if p.fga > 0:
                result[p.name] = p.fgm / p.fga
    return result


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_and_cache_poa_containment(season: str, force: bool = False) -> Optional[dict]:
    cache_path = _poa_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)
    if season < POA_FIRST_SEASON:
        print(f"{season} is before the real reliable matchup floor ({POA_FIRST_SEASON}) -- nothing cached.")
        return None

    print(f"Fetching {season} real league-wide matchup data...")
    df = fetch_matchup_data(season)
    if df is None or len(df) == 0:
        print(f"{season} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    fg_pct_by_name = _real_season_fg_pct_by_name(season)

    defenders: Dict[str, dict] = {}
    for _, row in df.iterrows():
        def_id = str(int(row["DEF_PLAYER_ID"]))
        off_name = row["OFF_PLAYER_NAME"]
        matchup_fga = float(row["MATCHUP_FGA"])
        matchup_fgm = float(row["MATCHUP_FGM"])
        partial_poss = float(row["PARTIAL_POSS"])
        matchup_tov = float(row["MATCHUP_TOV"])
        sfl = float(row["SFL"])
        matchup_time_sec = float(row["MATCHUP_TIME_SEC"])

        d = defenders.setdefault(def_id, {
            "player_name": row["DEF_PLAYER_NAME"], "total_partial_poss": 0.0,
            "total_matchup_fga": 0.0, "total_matchup_fgm": 0.0,
            "total_expected_fgm": 0.0,  # Sum(expected_fg_pct_of_opponent * matchup_fga) -- real opponent-quality baseline
            "expected_fga_covered": 0.0,  # real FGA subset with a known opponent expected-FG% -- the correct
            # denominator for total_expected_fgm (NOT total_matchup_fga, which would silently dilute the
            # expected-rate average toward 0 for any opponent missing a real box-stat match).
            "total_matchup_tov": 0.0, "total_sfl": 0.0, "total_matchup_time_sec": 0.0,
            "n_distinct_opponents": 0,
        })
        d["total_partial_poss"] += partial_poss
        d["total_matchup_fga"] += matchup_fga
        d["total_matchup_fgm"] += matchup_fgm
        d["total_matchup_tov"] += matchup_tov
        d["total_sfl"] += sfl
        d["total_matchup_time_sec"] += matchup_time_sec
        d["n_distinct_opponents"] += 1
        expected_pct = fg_pct_by_name.get(off_name)
        if expected_pct is not None and matchup_fga > 0:
            d["total_expected_fgm"] += expected_pct * matchup_fga
            d["expected_fga_covered"] += matchup_fga

    payload = {"season": season, "source": "leagueseasonmatchups", "schema_version": POA_CACHE_VERSION,
               "provenance": "nba_api.stats.endpoints.leagueseasonmatchups", "players": defenders}
    _atomic_write(cache_path, payload)
    print(f"Cached real POA-containment matchup data for {len(defenders)} defenders -> {cache_path}")
    return payload


def load_poa_containment(season: str) -> Dict[str, dict]:
    cache_path = _poa_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def build_and_cache_poa_containment_range(seasons, force: bool = False) -> dict:
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_poa_containment(season, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
