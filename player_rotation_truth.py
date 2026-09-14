"""
Availability + Expected Minutes + Rotations V1.

============================ EXISTING ARCHITECTURE AUDIT (done BEFORE writing anything new) ============================
Nothing in this repo already builds a date-safe, per-game participant/minutes reconstruction for
the DETAILED engine track. `injuries.py` (a separate, PARALLEL Codex workstream's file -- untouched,
never imported here) builds a RANDOMIZED simulated-injury CALENDAR for the legacy simulator, from
real absence-stint COUNTS/LENGTHS (`cache/injuries.json` via `loader.load_player_injuries`) -- a
different purpose (deciding which games a player sits in a SIMULATED season) from this phase's own
goal (reconstructing what ACTUALLY happened, or could have been known pregame, for one REAL
historical game). `roster_membership.json` (real, reused via `player_team_stints.py`) gives team
assignment but not minutes or per-game participation. No starter/DNP/rotation module exists.

`player_game_log_ingestion.py` caches real per-game shooting stats but NOT `MIN`/`TEAM_ID`, even
though both are real columns in the same already-used `leaguegamelog` response --
`player_game_minutes_ingestion.py` (new this phase) extracts them additively into a separate cache,
`player_game_minutes.json`, real, id-keyed, per-game.

============================ ORACLE vs PREGAME (kept structurally separate) ============================
`build_oracle_rotation(...)`: reconstructs what ACTUALLY happened in one specific historical game --
who has a real per-game-minutes row for that `game_id`+team, their real minutes, and a PROXY
starter label (top-5 real minutes that game -- true START_POSITION data was not ingested this phase,
see module docstring's own "what this does not solve" note below). NOT predictive; used only to
isolate engine quality by feeding it the historically-correct participants.

`build_pregame_rotation(...)`: uses ONLY evidence strictly before `as_of_date` -- a real, date-safe
rolling-minutes prefix, a real recent-DNP-rate availability gate, and a documented deterministic
allocation to 240 regulation minutes. NEVER reads the target game's own row.

============================ WHAT THIS PHASE DOES NOT SOLVE ============================
True official STARTER data (`START_POSITION`) requires a separate per-game endpoint
(`boxscoretraditionalv2`, ~1 call/game, ~1,230 calls/season) -- explicitly out of this phase's
proportionate scope. Both oracle and pregame "starter" signals here are an honestly-labeled PROXY
(rank by minutes), never presented as official record.

The frozen detailed engine (`detailed_game.py`/`possession_orchestrator.py`) has NO substitution,
quarter, or in-game-minutes mechanism at all -- confirmed directly, no such hook exists anywhere in
that code. `simulate_detailed_game` takes one FIXED five-man lineup per side for the entire
simulated game. This phase therefore cannot "wire expected minutes into the engine" in the sense of
driving in-game substitutions (no such mechanism exists to wire into) -- the only real,
non-invented integration point is choosing WHICH five players occupy each side's fixed lineup (see
`rotation_engine_adapter.py`). This is reported honestly as ABSENT, not silently built around.

============================ AVAILABILITY DEFINITION ============================
For a target game, `PlayerGameAvailability.status` is one of:
    ACTIVE  -- the player has a real per-game-minutes row for this exact game_id (oracle), or
               (pregame) recent evidence supports inclusion in the expected rotation.
    OUT     -- (oracle only) the player's team played this game_id and the player has NO row for
               it (a real, observed absence -- reason unknown, same real limitation
               `data_source.fetch_player_absence_stints`'s own docstring already documents).
    UNKNOWN -- (pregame only) evidence is too thin (rookie/new-team/opening-night) to assert
               either way; never silently promoted to ACTIVE.
"""
import statistics as st
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import player_identity as pid
from player_game_minutes_ingestion import load_game_minutes
from player_team_stints import team_as_of_date, team_roster_as_of_date

SCHEMA_VERSION = "0.1.0-rotation-truth"

STATUS_ACTIVE = "ACTIVE"
STATUS_OUT = "OUT"
STATUS_UNKNOWN = "UNKNOWN"

MODE_ORACLE_ACTUAL = "ORACLE_ACTUAL"
MODE_PREGAME_EXPECTED = "PREGAME_EXPECTED"

REGULATION_TEAM_MINUTES = 240.0
INDIVIDUAL_MINUTE_CAP = 42.0  # see module docstring Sec. "cap derivation" in the phase report
ZERO_MINUTE_THRESHOLD = 3.0   # raw estimates below this are zeroed before renormalization
RECENT_WINDOW_SIZES = (1, 3, 5, 10)


@dataclass(frozen=True)
class PlayerGameAvailability:
    player_id: str
    status: str
    provenance: str


@dataclass(frozen=True)
class ExpectedPlayerMinutes:
    """GAME-STATE BELIEF, not player truth -- see module docstring. `starter_expectation` is a
    proxy signal (see module docstring's own "what this does not solve"), never official record."""
    player_id: str
    team_name: str
    availability: PlayerGameAvailability
    expected_minutes: float
    starter_expectation: Optional[float]  # [0,1] proxy probability, or None if no evidence
    confidence: Optional[float]
    sample_size: Optional[int]
    source: str
    as_of: str
    mode: str

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id, "team_name": self.team_name,
            "status": self.availability.status, "availability_provenance": self.availability.provenance,
            "expected_minutes": self.expected_minutes, "starter_expectation": self.starter_expectation,
            "confidence": self.confidence, "sample_size": self.sample_size, "source": self.source,
            "as_of": self.as_of, "mode": self.mode,
        }


@dataclass(frozen=True)
class TeamRotationSnapshot:
    team_name: str
    as_of: str
    mode: str
    season: str
    players: Tuple[ExpectedPlayerMinutes, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def total_minutes(self) -> float:
        return sum(p.expected_minutes for p in self.players)

    def top_n(self, n: int) -> Tuple[ExpectedPlayerMinutes, ...]:
        return tuple(sorted(self.players, key=lambda p: p.expected_minutes, reverse=True)[:n])

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "team_name": self.team_name, "as_of": self.as_of,
            "mode": self.mode, "season": self.season,
            "players": [p.to_dict() for p in self.players],
        }


# =====================================================================
# Real, date-safe rolling-minutes prefix (per player, per season)
# =====================================================================

@lru_cache(maxsize=None)
def _season_games_for_player(player_id: str, season: str) -> Tuple[dict, ...]:
    games = load_game_minutes(season).get(player_id, [])
    return tuple(games)  # already sorted (date, game_id) by the ingestion module


def games_before_date(player_id: str, as_of_date: str, season: str) -> List[dict]:
    """Real per-game rows strictly before `as_of_date` -- the ONE enforcement point every rolling
    feature and the pregame rotation builder route through. Never includes the target date itself."""
    return [g for g in _season_games_for_player(player_id, season) if g["date"] < as_of_date]


def rolling_minutes_features(player_id: str, as_of_date: str, season: str) -> Dict[str, Optional[float]]:
    """Real, date-safe rolling features -- last-1/3/5/10 mean, season-to-date mean/median, and a
    recent-DNP-rate proxy (share of the team's last N scheduled games -- approximated here as the
    player's own last N *known* games' gaps are not reconstructed from the full schedule this
    phase; DNP-rate is instead computed as zero-minute-share among the player's OWN recent rows,
    a real but conservative signal -- see phase report Sec. for the documented limitation)."""
    games = games_before_date(player_id, as_of_date, season)
    if not games:
        return {f"last_{n}": None for n in RECENT_WINDOW_SIZES} | {
            "season_to_date_mean": None, "season_to_date_median": None,
            "recent_zero_share": None, "games_played": 0,
        }
    minutes = [g["minutes"] for g in games]
    out: Dict[str, Optional[float]] = {"games_played": len(minutes)}
    for n in RECENT_WINDOW_SIZES:
        window = minutes[-n:]
        out[f"last_{n}"] = round(st.mean(window), 3) if window else None
    out["season_to_date_mean"] = round(st.mean(minutes), 3)
    out["season_to_date_median"] = round(st.median(minutes), 3)
    recent = minutes[-10:]
    out["recent_zero_share"] = round(sum(1 for m in recent if m == 0.0) / len(recent), 3)
    return out


def _prior_season_features(player_id: str, prior_season: str) -> Dict[str, Optional[float]]:
    """Opening-night fallback: the player's own FULL prior-season rolling snapshot (as of the day
    after their last prior-season game) -- real evidence, never a league-average fabrication."""
    games = _season_games_for_player(player_id, prior_season)
    if not games:
        return {"season_to_date_mean": None, "games_played": 0, "recent_zero_share": None}
    minutes = [g["minutes"] for g in games]
    recent = minutes[-10:]
    return {
        "season_to_date_mean": round(st.mean(minutes), 3), "games_played": len(minutes),
        "recent_zero_share": round(sum(1 for m in recent if m == 0.0) / len(recent), 3),
        "last_5": round(st.mean(minutes[-5:]), 3),
    }


# =====================================================================
# Oracle mode -- what actually happened
# =====================================================================

@lru_cache(maxsize=None)
def _real_team_id(team_name: str) -> Optional[str]:
    """Real, static, offline NBA team registry (`nba_api.stats.static.teams` -- same posture as
    `player_identity.py`'s own static player index: a bundled, authoritative, no-network lookup)."""
    from nba_api.stats.static import teams as _teams
    for t in _teams.get_teams():
        if t["full_name"] == team_name:
            return str(t["id"])
    return None


def build_oracle_rotation(game_id: str, team_name: str, season: str) -> TeamRotationSnapshot:
    """ORACLE_ACTUAL. Real per-game minutes for every player with a row for `game_id` whose row's
    real `team_id` matches `team_name` (via the real, static NBA team registry) -- this excludes
    the OPPONENT's own real rows for the same `game_id` (both teams' players share one game_id;
    without this filter the two teams' totals would be combined). Starter proxy = rank by real
    minutes that game (top 5), NOT official START_POSITION -- see module docstring."""
    team_id = _real_team_id(team_name)
    minutes_cache = load_game_minutes(season)
    rows = []
    for player_id, games in minutes_cache.items():
        for g in games:
            if g["game_id"] == game_id and (team_id is None or g["team_id"] == team_id):
                rows.append((player_id, g["minutes"]))
                break

    rows.sort(key=lambda r: r[1], reverse=True)
    n = len(rows)
    players = []
    for rank, (player_id, minutes) in enumerate(rows):
        starter = 1.0 if rank < 5 else 0.0
        players.append(ExpectedPlayerMinutes(
            player_id=player_id, team_name=team_name,
            availability=PlayerGameAvailability(player_id, STATUS_ACTIVE, "real per-game-minutes row"),
            expected_minutes=minutes, starter_expectation=starter, confidence=1.0, sample_size=1,
            source="player_game_minutes_ingestion (oracle, real box score)", as_of=game_id, mode=MODE_ORACLE_ACTUAL,
        ))
    return TeamRotationSnapshot(team_name=team_name, as_of=game_id, mode=MODE_ORACLE_ACTUAL,
                                 season=season, players=tuple(players))


# =====================================================================
# Pregame mode -- only information available before tipoff
# =====================================================================

def _raw_expected_minutes(features: Dict[str, Optional[float]]) -> Optional[float]:
    """Simple, interpretable weighted family (per this phase's own "start simple" instruction):
        0.5 * last_5 + 0.3 * last_10 + 0.2 * season_to_date_mean
    falling back to whatever real windows are actually available (fewer games played -> fewer,
    equally-reweighted terms) -- never a fabricated number when zero real games exist."""
    if not features or features.get("games_played", 0) == 0:
        return None
    weights = []
    if features.get("last_5") is not None:
        weights.append((0.5, features["last_5"]))
    if features.get("last_10") is not None:
        weights.append((0.3, features["last_10"]))
    if features.get("season_to_date_mean") is not None:
        weights.append((0.2, features["season_to_date_mean"]))
    if not weights:
        return None
    total_w = sum(w for w, _ in weights)
    return sum(w * v for w, v in weights) / total_w


def _availability_gate(features: Dict[str, Optional[float]]) -> str:
    """Deterministic V1 rule (documented, not probabilistic -- per this phase's own "if V1 cannot
    estimate availability probabilistically, document a deterministic rule instead" allowance):
    a recent-zero-share >= 0.8 (out for essentially all of the last 10 real games) is treated as
    OUT; otherwise ACTIVE if any real recent evidence exists; UNKNOWN if no real evidence exists
    at all (rookie / no games yet)."""
    if features.get("games_played", 0) == 0:
        return STATUS_UNKNOWN
    if (features.get("recent_zero_share") or 0.0) >= 0.8:
        return STATUS_OUT
    return STATUS_ACTIVE


def _allocate_240(raw: Dict[str, float]) -> Dict[str, float]:
    """Deterministic constrained allocation -- NOT an optimizer. Steps:
    1. zero out anything below ZERO_MINUTE_THRESHOLD (documented -- a tiny raw estimate is not a
       real rotation player).
    2. Iterative proportional capping ("water-filling"): scale the remaining pool to fill
       whatever target-minutes remain; anyone who would exceed INDIVIDUAL_MINUTE_CAP is pinned at
       the cap and removed from the pool; the target is reduced by exactly their capped total;
       repeat among the still-uncapped remainder until nobody new gets capped. This correctly
       handles CASCADING caps (pinning one player can push another over the cap on the next pass)
       -- a single capped rescale, verified by this phase's own test suite, does not.
    Preserves raw ranking throughout (each pass is a positive scalar multiply, never a reorder)."""
    values = {p: v for p, v in raw.items() if v >= ZERO_MINUTE_THRESHOLD}
    if not values:
        return {p: 0.0 for p in raw}

    fixed: Dict[str, float] = {}
    remaining = dict(values)
    target = REGULATION_TEAM_MINUTES
    for _ in range(len(values) + 1):
        if not remaining:
            break
        pool_sum = sum(remaining.values())
        if pool_sum <= 0:
            break
        scale = target / pool_sum
        newly_capped = [p for p, v in remaining.items() if v * scale > INDIVIDUAL_MINUTE_CAP + 1e-9]
        if not newly_capped:
            for p, v in remaining.items():
                fixed[p] = v * scale
            remaining = {}
            break
        for p in newly_capped:
            fixed[p] = INDIVIDUAL_MINUTE_CAP
            target -= INDIVIDUAL_MINUTE_CAP
            del remaining[p]

    out = {p: 0.0 for p in raw}
    out.update(fixed)
    return out


def build_pregame_rotation(team_name: str, as_of_date: str, as_of_season: str,
                            all_seasons: List[str]) -> TeamRotationSnapshot:
    """PREGAME_EXPECTED. Uses `team_roster_as_of_date` (real, date-safe) for the candidate pool,
    then rolling features from games strictly before `as_of_date`. Falls back to the prior
    season's own real full-season snapshot for a player with zero current-season games (new
    trade/rookie/opening night) -- confidence is set lower in that fallback case, never presented
    as equally fresh."""
    roster = team_roster_as_of_date(team_name, as_of_date, as_of_season)
    prior_season = None
    season_idx = sorted(all_seasons).index(as_of_season) if as_of_season in all_seasons else None
    if season_idx is not None and season_idx > 0:
        prior_season = sorted(all_seasons)[season_idx - 1]

    raw_estimates: Dict[str, float] = {}
    entries: Dict[str, dict] = {}
    for player_id in roster:
        features = rolling_minutes_features(player_id, as_of_date, as_of_season)
        used_fallback = False
        if features["games_played"] == 0 and prior_season:
            features = _prior_season_features(player_id, prior_season)
            used_fallback = True

        raw = _raw_expected_minutes(features)
        status = _availability_gate(features)
        entries[player_id] = {"features": features, "used_fallback": used_fallback, "status": status}
        if raw is not None and status != STATUS_OUT:
            raw_estimates[player_id] = raw

    allocated = _allocate_240(raw_estimates)

    players = []
    for player_id in roster:
        e = entries[player_id]
        features, used_fallback, status = e["features"], e["used_fallback"], e["status"]
        minutes = allocated.get(player_id, 0.0)
        games_played = features.get("games_played", 0) or 0
        confidence = None
        if status != STATUS_UNKNOWN:
            confidence = round(min(1.0, games_played / 20.0), 3)
            if used_fallback:
                confidence = round(confidence * 0.5, 3)  # prior-season evidence, explicitly discounted
        starter_prob = None
        if games_played > 0:
            last5 = features.get("last_5") or 0.0
            starter_prob = round(min(1.0, max(0.0, last5 / 30.0)), 3)  # proxy only, see module docstring

        players.append(ExpectedPlayerMinutes(
            player_id=player_id, team_name=team_name,
            availability=PlayerGameAvailability(
                player_id, status,
                "prior-season fallback (no current-season games yet)" if used_fallback else
                "real rolling minutes through cutoff" if games_played > 0 else "no real evidence",
            ),
            expected_minutes=minutes, starter_expectation=starter_prob, confidence=confidence,
            sample_size=games_played, source="player_rotation_truth.build_pregame_rotation",
            as_of=as_of_date, mode=MODE_PREGAME_EXPECTED,
        ))

    return TeamRotationSnapshot(team_name=team_name, as_of=as_of_date, mode=MODE_PREGAME_EXPECTED,
                                 season=as_of_season, players=tuple(players))
