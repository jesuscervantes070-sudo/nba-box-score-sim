"""
Phase 7 -- Rim Protection: ingestion. Entirely offline/parallel, same
discipline as every prior phase -- imports `data_source._season_cache_dir`
only; writes its own new cache file, never touches
game_engine.py/season.py/awards.py/models.py/transactions.py.

============================ SOURCE VERIFIED DIRECTLY ============================
`nba_api.stats.endpoints.leaguedashptdefend`, `defense_category="Less Than
6Ft"`, `per_mode_simple="Totals"` -- ALREADY used elsewhere in this
codebase (`data_source.fetch_player_rim_defense`, PER-GAME rate only, for
`ratings.py`'s DPOY formula) but never cached as raw TOTALS with real
sample-size weight. This phase re-fetches the SAME real endpoint at
`per_mode_simple="Totals"` for real FGM/FGA counts.

Real, verified columns: `CLOSE_DEF_PERSON_ID`, `PLAYER_NAME`, `GP`, `FREQ`,
`FGM_LT_06`, `FGA_LT_06`, `LT_06_PCT`, `NS_LT_06_PCT`, `PLUSMINUS`.

- `FGA_LT_06` (real, TOTAL rim shots this player was the closest defender
  on all season) = the real RIM-PROTECTION OPPORTUNITY count (concept B
  in the phase's own taxonomy) -- NOT an ability measure by itself.
- `LT_06_PCT` = real opponent FG% on those attempts (contaminated by
  opponent shooter quality, shot difficulty, help, etc. -- see
  rim_protection_analysis.py for the contamination checks).
- `NS_LT_06_PCT` ("Normal Shooting") = the NBA's own real, first-party
  EXPECTED shooting percentage on this exact shot type -- NOT invented by
  this project; the closest real analogue this codebase has access to
  for "expected FG% if exposed" (task's own requested field). Documented
  NBA.com defensive-dashboard methodology: an average, shot-type-specific
  expectation, not the specific opponent's own season rate -- a real,
  first-party control for shot-type difficulty already baked in.
- `PLUSMINUS` = `LT_06_PCT - NS_LT_06_PCT` (real, NBA-computed residual,
  NOT computed by this project) -- POSITIVE means the defender allowed a
  HIGHER-than-expected FG% (bad defense); flipped in this cache (like the
  existing `rim_deterrence` field in `data_source.py`) so higher = better,
  consistent with this project's convention.
- Real floor: checked directly, 2012-13 returns 0 rows, 2013-14 returns
  real populated data -- same floor as every other `leaguedashpt*`
  endpoint this codebase already uses (`RIM_DEFENSE_FIRST_SEASON`).
- `FREQ` = real fraction of THIS PLAYER's own total defended shots (any
  distance) that are `<6Ft` -- a real POSITIONAL/ROLE proxy (a helpside
  wing who occasionally contests at the rim has a real, low FREQ; a
  drop-coverage center has a real, high FREQ), not an ability signal.
  Preserved as auxiliary evidence for role-bias analysis (see
  rim_protection_analysis.py), never used as the primary numerator or
  denominator.

============================ WHAT ISN'T HERE (documented, not silently skipped) ============================
- No real "expected FG% conditional on THIS SPECIFIC opponent's shooting
  profile" field exists in this codebase's real API access (that level
  of shot-quality modeling is Second Spectrum proprietary territory) --
  `NS_LT_06_PCT` is the best real, first-party approximation available,
  and is used as such, not replaced by an invented model.
- No real per-defender "opponent rim ATTEMPT frequency when this
  defender is on court vs off court" field exists anywhere in this
  codebase's real API access either -- real attempt-deterrence
  (mechanism A in the phase's own framing) is investigated in
  rim_protection_analysis.py using real TEAM-level opponent-FGA data
  cross-referenced with real per-player minutes share, not a fabricated
  per-defender on/off split. See that module's own docstring for the
  real result.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from data_source import _season_cache_dir

RIM_PROTECTION_CACHE_VERSION = 1
RIM_PROTECTION_FIRST_SEASON = "2013-14"  # real camera-tracking floor, matches RIM_DEFENSE_FIRST_SEASON


def _rim_protection_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "player_rim_protection.json"


def fetch_rim_protection_data(season: str, retries: int = 4, timeout: int = 60):
    """One real league-wide call (same real endpoint `data_source.py`
    already uses, at `per_mode_simple='Totals'` instead of `PerGame`).
    Real, observed flakiness on this specific endpoint (already
    documented in `data_source._fetch_ptdefend`) -- retried with backoff."""
    from nba_api.stats.endpoints import leaguedashptdefend
    last_err = None
    for attempt in range(retries):
        try:
            return leaguedashptdefend.LeagueDashPtDefend(
                season=season, defense_category="Less Than 6Ft", per_mode_simple="Totals", timeout=timeout,
            ).get_data_frames()[0]
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"leaguedashptdefend(Less Than 6Ft, Totals, {season}) failed after {retries} attempts: {last_err}")


def _atomic_write(path: Path, payload: dict) -> None:
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_and_cache_rim_protection(season: str, force: bool = False) -> Optional[dict]:
    """Cache-first, one-call-per-season -- same pattern as
    shot_zone_ingestion.py (cheap enough that a full historical backfill
    across every real tracking-era season is genuinely trivial, unlike
    the per-game PBP pipelines from Phase 4A/4B/6)."""
    cache_path = _rim_protection_cache_path(season)
    if cache_path.exists() and not force:
        print(f"Rim-protection cache already exists at {cache_path}. Use force=True to update.")
        with open(cache_path) as f:
            return json.load(f)

    if season < RIM_PROTECTION_FIRST_SEASON:
        print(f"{season} is before the real rim-tracking floor ({RIM_PROTECTION_FIRST_SEASON}) -- no data exists, nothing cached.")
        return None

    print(f"Fetching {season} real player rim-protection data (leaguedashptdefend, Less Than 6Ft, Totals)...")
    df = fetch_rim_protection_data(season)
    if df is None or len(df) == 0:
        print(f"{season} returned 0 rows -- genuinely no data, nothing cached.")
        return None

    players = {}
    for _, row in df.iterrows():
        pid = str(int(row["CLOSE_DEF_PERSON_ID"]))
        fga = float(row["FGA_LT_06"])
        fgm = float(row["FGM_LT_06"])
        ns_pct = row.get("NS_LT_06_PCT")
        lt06_pct = row.get("LT_06_PCT")
        plusminus = row.get("PLUSMINUS")
        players[pid] = {
            "player_name": row["PLAYER_NAME"],
            "gp": int(row["GP"]),
            "rim_fga_defended": fga,
            "rim_fgm_allowed": fgm,
            "rim_fg_pct_allowed": float(lt06_pct) if lt06_pct is not None else None,
            "rim_expected_fg_pct": float(ns_pct) if ns_pct is not None else None,  # real NBA-computed "normal shooting" baseline
            "rim_suppression_plusminus": -float(plusminus) if plusminus is not None else None,  # sign-flipped: higher = better defense
            "rim_freq_of_own_defended_shots": float(row["FREQ"]) if row.get("FREQ") is not None else None,
        }

    payload = {
        "season": season,
        "source": "leaguedashptdefend(Less Than 6Ft, Totals)",
        "schema_version": RIM_PROTECTION_CACHE_VERSION,
        "provenance": "nba_api.stats.endpoints.leaguedashptdefend",
        "players": players,
    }
    _atomic_write(cache_path, payload)
    print(f"Cached real rim-protection data for {len(players)} players -> {cache_path}")
    return payload


def load_rim_protection(season: str) -> Dict[str, dict]:
    """{player_id_str: {...}} for `season`, or {} if not cached / before
    the real tracking floor -- never a silent fallback."""
    cache_path = _rim_protection_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["players"]


def _team_opp_rim_cache_path(season: str) -> Path:
    return _season_cache_dir(season) / "team_opp_rim_frequency.json"


def build_and_cache_team_opp_rim_frequency(season: str, force: bool = False) -> Optional[dict]:
    """
    Real TEAM-level opponent Restricted-Area attempt volume
    (`leaguedashteamshotlocations`, `measure_type_simple="Opponent"`) --
    used ONLY for the team/scheme-bias check in
    rim_protection_analysis.py (does a team's real rim protector quality
    correlate with how many rim attempts opponents take against that
    team, a real, coarse, TEAM-level signature of scheme/context
    contamination). NOT a per-player on/off split (see this module's own
    top docstring on why that's deferred as future-only) -- one real
    number per team per season.
    """
    cache_path = _team_opp_rim_cache_path(season)
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            return json.load(f)
    from nba_api.stats.endpoints import leaguedashteamshotlocations
    last_err = None
    df = None
    for attempt in range(4):
        try:
            df = leaguedashteamshotlocations.LeagueDashTeamShotLocations(
                season=season, measure_type_simple="Opponent", timeout=30,
            ).get_data_frames()[0]
            break
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    if df is None:
        raise RuntimeError(f"leaguedashteamshotlocations({season}) failed: {last_err}")
    if len(df) == 0:
        return None
    df.columns = ["_".join([c for c in col if c]) for col in df.columns]
    teams = {row["TEAM_NAME"]: float(row["Restricted Area_OPP_FGA"]) for _, row in df.iterrows()}
    payload = {"season": season, "source": "leaguedashteamshotlocations(Opponent)", "teams": teams}
    _atomic_write(cache_path, payload)
    return payload


def load_team_opp_rim_frequency(season: str) -> Dict[str, float]:
    cache_path = _team_opp_rim_cache_path(season)
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        return json.load(f)["teams"]


def build_and_cache_rim_protection_range(seasons, force: bool = False) -> dict:
    """Resumable across a season list: cache-first per season, one
    season's failure never affects another's cache file."""
    results = {}
    for season in seasons:
        try:
            results[season] = build_and_cache_rim_protection(season, force=force)
        except Exception as e:
            print(f"{season}: FAILED ({e}) -- other seasons' caches are unaffected.")
            results[season] = None
    return results
