"""
Historical Pregame Game Snapshot V1.

The goal: for one real historical game, using ONLY the already-existing, already-tested truth/
belief pipelines, compose a single deterministic object that can run through the frozen detailed
engine. This module BUILDS NOTHING NEW at the estimator level -- it is pure composition/orchestration
over already-completed truth tracks, reused by import only:

    scoring        -> player_scoring_truth_temporal.build_scoring_truth_profile_as_of_date
                       + player_scoring_truth.apply_scoring_truth_to_simulation_profile
    playmaking     -> player_playmaking_truth.build_playmaking_truth_profile_as_of_date
                       + player_playmaking_truth.apply_playmaking_truth_to_simulation_profile
    rebounding     -> player_rebounding_truth.build_rebounding_truth_profile_as_of_date
                       + player_rebounding_truth.apply_rebounding_truth_to_simulation_profile (no-op,
                         per that phase's own documented scale mismatch)
                       + rebound_engine_adapter.apply_rebounding_truth_via_adapter (the real overlay)
    defense        -> player_defensive_truth.build_defensive_truth_profile_as_of_date
                       + player_defensive_truth.apply_defensive_truth_to_simulation_profile (poa +
                         rim, direct)
                       + defensive_engine_adapter.apply_defensive_playmaking_via_adapter (the
                         defensive_playmaking bridge; foul_discipline stays un-overlaid, by design)
    role           -> player_role_truth.build_role_truth_profile_as_of_date
                       + player_role_truth.apply_role_truth_to_simulation_profile
    team/roster    -> player_team_stints.team_roster_as_of_date / team_as_of_date
    rotation       -> player_rotation_truth.build_pregame_rotation / build_oracle_rotation
    primary five   -> rotation_engine_adapter.primary_five (the ONLY real engine-lineup hook, since
                       the frozen engine has no substitution mechanism -- see that module's own
                       docstring, unchanged here)
    identity       -> player_identity.resolve_id_to_name
    game metadata  -> game_metadata.get_game_metadata (new this phase, additive, reads the
                       already-cached real schedule.json)

============================ MODES (kept structurally distinct) ============================
`MODE_PREGAME_EXPECTED`: roster + rotation come from `build_pregame_rotation` (real, date-safe,
zero target-game evidence). This is the future predictor mode.

`MODE_ORACLE_PARTICIPANTS`: roster + rotation come from `build_oracle_rotation` (real target-game
box-score participants/minutes) -- but EVERY player's ability/tendency/role truth is STILL built
with the exact same pregame-safe, `as_of_date`-cutoff calls as PREGAME_EXPECTED. Oracle mode never
lets target-game outcomes leak into player TRUTH -- only WHO PLAYED and HOW MANY MINUTES THEY
ACTUALLY GOT are oracle. This isolates "how good is the engine with the right participants" from
"how good is our participant-forecasting."

============================ ENGINE CONSTRAINT (documented again, not re-litigated) ============================
The frozen detailed engine has NO substitution mechanism (established in the Rotations V1 phase,
unchanged here) -- `simulate_detailed_game` takes exactly one fixed five-man lineup per side.
`primary_five()` is therefore the ONLY way rotation information reaches the engine: expected/oracle
minutes determine WHO is in the fixed five, not in-game court time. This is stated again here,
prominently, because it directly bounds what this phase's "composition" can mean.
"""
import functools
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import player_identity as pid
import player_scoring_truth as pst
import player_scoring_truth_temporal as psst
import player_playmaking_truth as ppt
import player_rebounding_truth as prt_reb
import player_defensive_truth as pdt
import player_role_truth as prt_role
import player_rotation_truth as prt_rot
import rebound_engine_adapter as rebound_adapter
import defensive_engine_adapter as defense_adapter
import rotation_engine_adapter as rotation_adapter
from game_metadata import GameMetadata, get_game_metadata
from player_team_stints import team_roster_as_of_date
from possession_orchestrator import PlayerSimulationProfile

SCHEMA_VERSION = "0.1.0-historical-snapshot"
MODEL_VERSION = "v1"

MODE_PREGAME_EXPECTED = "PREGAME_EXPECTED"
MODE_ORACLE_PARTICIPANTS = "ORACLE_PARTICIPANTS"
_VALID_MODES = (MODE_PREGAME_EXPECTED, MODE_ORACLE_PARTICIPANTS)


# Process-local memoization ONLY, at THIS composition layer -- a real, measured performance
# necessity (a single team's ~15-18 players each independently trigger their own real reference-
# population rebuild inside each of 5 upstream, UNMODIFIED estimator modules; a naive composition
# pass over a full roster is genuinely slow). This caches the deterministic, immutable per-player
# TRUTH PROFILE objects (never the final PlayerSimulationProfile overlay, and never anything
# oracle/participation-related) keyed on exactly the same real, hashable arguments the underlying
# builder itself uses -- so a repeated build, or the SAME player appearing in both a
# PREGAME_EXPECTED and an ORACLE_PARTICIPANTS snapshot for the same game (which always use the
# identical as_of_date cutoff, see module docstring), reuses the identical real result instead of
# recomputing it. Does NOT touch, wrap, or change any upstream estimator's own math or caching.
_build_scoring_truth_cached = functools.lru_cache(maxsize=4096)(
    lambda player_id, as_of_date, as_of_season, all_seasons, exclude_game_id:
    psst.build_scoring_truth_profile_as_of_date(player_id, as_of_date, as_of_season, list(all_seasons), exclude_game_id=exclude_game_id))
_build_playmaking_truth_cached = functools.lru_cache(maxsize=4096)(
    lambda player_id, as_of_date, as_of_season, all_seasons:
    ppt.build_playmaking_truth_profile_as_of_date(player_id, as_of_date, as_of_season, list(all_seasons)))
_build_rebounding_truth_cached = functools.lru_cache(maxsize=4096)(
    lambda player_id, as_of_date, as_of_season, all_seasons:
    prt_reb.build_rebounding_truth_profile_as_of_date(player_id, as_of_date, as_of_season, list(all_seasons)))
_build_defensive_truth_cached = functools.lru_cache(maxsize=4096)(
    lambda player_id, as_of_date, as_of_season, all_seasons:
    pdt.build_defensive_truth_profile_as_of_date(player_id, as_of_date, as_of_season, list(all_seasons)))
_build_role_truth_cached = functools.lru_cache(maxsize=4096)(
    lambda player_id, as_of_date, as_of_season:
    prt_role.build_role_truth_profile_as_of_date(player_id, as_of_date, as_of_season))


def _summarize_provenance(profile) -> str:
    """One concise, machine-readable label per truth-attribute-GROUP (not per individual
    attribute) -- the single provenance value if every attribute in the group agrees, "MIXED" if
    they genuinely differ (e.g. one scoring target is CURRENT_SEASON_PREGAME while another is
    PRIOR_SEASON_ONLY -- a real, honest state, not an error), or MISSING if the player has no real
    evidence anywhere in that group."""
    values = {est.provenance for est in profile.estimates.values()}
    if not values:
        return "MISSING"
    if values == {"MISSING"}:
        return "MISSING"
    if len(values) == 1:
        return next(iter(values))
    return "MIXED"


@dataclass(frozen=True)
class PlayerSnapshotEntry:
    player_id: str
    canonical_name: Optional[str]
    team_name: str
    side: str  # "HOME" or "AWAY" -- the engine's own team_id convention
    expected_minutes: float
    availability_status: str
    is_primary_five: bool
    simulation_profile: PlayerSimulationProfile
    truth_provenance: Dict[str, str] = field(default_factory=dict)  # {"scoring": ..., "playmaking": ..., ...}
    rotation_mode: str = MODE_PREGAME_EXPECTED

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id, "canonical_name": self.canonical_name,
            "team_name": self.team_name, "side": self.side,
            "expected_minutes": self.expected_minutes, "availability_status": self.availability_status,
            "is_primary_five": self.is_primary_five, "truth_provenance": dict(sorted(self.truth_provenance.items())),
            "rotation_mode": self.rotation_mode,
        }


@dataclass(frozen=True)
class HistoricalTeamSnapshot:
    team_name: str
    side: str
    eligible_roster: Tuple[str, ...]
    primary_five: Tuple[str, ...]
    players: Tuple[PlayerSnapshotEntry, ...]

    def to_dict(self) -> dict:
        return {
            "team_name": self.team_name, "side": self.side,
            "eligible_roster": sorted(self.eligible_roster), "primary_five": sorted(self.primary_five),
            "players": [p.to_dict() for p in sorted(self.players, key=lambda p: p.player_id)],
        }


@dataclass(frozen=True)
class HistoricalGameSnapshot:
    game_id: str
    game_date: str
    season: str
    home_team: str
    away_team: str
    mode: str
    as_of: str
    home_team_snapshot: HistoricalTeamSnapshot
    away_team_snapshot: HistoricalTeamSnapshot
    schema_version: str = SCHEMA_VERSION
    model_version: str = MODEL_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "model_version": self.model_version,
            "game_id": self.game_id, "game_date": self.game_date, "season": self.season,
            "home_team": self.home_team, "away_team": self.away_team, "mode": self.mode,
            "as_of": self.as_of,
            "home_team_snapshot": self.home_team_snapshot.to_dict(),
            "away_team_snapshot": self.away_team_snapshot.to_dict(),
        }


def _build_player_entry(player_id: str, team_name: str, side: str, as_of_date: str, as_of_season: str,
                         all_seasons: List[str], expected_minutes: float, availability_status: str,
                         is_primary: bool, mode: str, exclude_game_id: Optional[str]) -> PlayerSnapshotEntry:
    """The one per-player composition routine -- ALWAYS uses the pregame-safe, as_of_date-cutoff
    truth builders regardless of `mode` (see module docstring: only rotation/participation differs
    between modes, never player truth)."""
    resolution = pid.resolve_id_to_name(player_id)
    baseline = PlayerSimulationProfile.synthetic(player_id, side)
    all_seasons_key = tuple(all_seasons)

    scoring_truth = _build_scoring_truth_cached(player_id, as_of_date, as_of_season, all_seasons_key, exclude_game_id)
    profile = pst.apply_scoring_truth_to_simulation_profile(baseline, scoring_truth)

    playmaking_truth = _build_playmaking_truth_cached(player_id, as_of_date, as_of_season, all_seasons_key)
    profile = ppt.apply_playmaking_truth_to_simulation_profile(profile, playmaking_truth)

    rebounding_truth = _build_rebounding_truth_cached(player_id, as_of_date, as_of_season, all_seasons_key)
    profile = prt_reb.apply_rebounding_truth_to_simulation_profile(profile, rebounding_truth)  # documented no-op
    profile = rebound_adapter.apply_rebounding_truth_via_adapter(profile, rebounding_truth)     # the real overlay

    defensive_truth = _build_defensive_truth_cached(player_id, as_of_date, as_of_season, all_seasons_key)
    profile = pdt.apply_defensive_truth_to_simulation_profile(profile, defensive_truth)          # poa + rim, direct
    profile = defense_adapter.apply_defensive_playmaking_via_adapter(profile, defensive_truth)   # defensive_playmaking bridge

    role_truth = _build_role_truth_cached(player_id, as_of_date, as_of_season)
    profile = prt_role.apply_role_truth_to_simulation_profile(profile, role_truth)

    provenance = {
        "scoring": _summarize_provenance(scoring_truth),
        "playmaking": _summarize_provenance(playmaking_truth),
        "rebounding": _summarize_provenance(rebounding_truth),
        "defense": _summarize_provenance(defensive_truth),
        "role": _summarize_provenance(role_truth),
    }

    return PlayerSnapshotEntry(
        player_id=player_id, canonical_name=resolution.canonical_name, team_name=team_name, side=side,
        expected_minutes=expected_minutes, availability_status=availability_status, is_primary_five=is_primary,
        simulation_profile=profile, truth_provenance=provenance, rotation_mode=mode,
    )


def _build_team_snapshot(team_name: str, side: str, game_id: str, game_date: str, as_of_season: str,
                          all_seasons: List[str], mode: str) -> HistoricalTeamSnapshot:
    if mode == MODE_PREGAME_EXPECTED:
        rotation = prt_rot.build_pregame_rotation(team_name, game_date, as_of_season, all_seasons)
        roster = tuple(team_roster_as_of_date(team_name, game_date, as_of_season))
        exclude_game_id = game_id  # belt-and-suspenders even though as_of_date already excludes it
    elif mode == MODE_ORACLE_PARTICIPANTS:
        rotation = prt_rot.build_oracle_rotation(game_id, team_name, as_of_season)
        roster = tuple(p.player_id for p in rotation.players)
        exclude_game_id = None  # oracle mode's PARTICIPATION intentionally uses this game; truth still excludes it below
    else:
        raise ValueError(f"Unknown mode {mode!r} -- must be one of {_VALID_MODES}")

    primary = set(rotation_adapter.primary_five(rotation))
    entries = []
    for p in rotation.players:
        # truth is ALWAYS built pregame-safe (excluding the target game itself), even in oracle
        # participation mode -- see module docstring.
        entries.append(_build_player_entry(
            p.player_id, team_name, side, game_date, as_of_season, all_seasons,
            expected_minutes=p.expected_minutes, availability_status=p.availability.status,
            is_primary=(p.player_id in primary), mode=mode, exclude_game_id=game_id,
        ))

    return HistoricalTeamSnapshot(
        team_name=team_name, side=side, eligible_roster=roster,
        primary_five=tuple(sorted(primary)), players=tuple(entries),
    )


def build_historical_game_snapshot(game_id: str, season: str, all_seasons: List[str],
                                    mode: str = MODE_PREGAME_EXPECTED) -> HistoricalGameSnapshot:
    """The one public entry point. Real game metadata (never inferred from argument order) decides
    home/away; `mode` decides ONLY how rotation/participation is built (see module docstring)."""
    if mode not in _VALID_MODES:
        raise ValueError(f"Unknown mode {mode!r} -- must be one of {_VALID_MODES}")
    metadata = get_game_metadata(game_id, season)
    if metadata is None:
        raise ValueError(f"No real cached schedule metadata for game_id={game_id!r}, season={season!r}")

    home_snapshot = _build_team_snapshot(metadata.home_team, "HOME", game_id, metadata.game_date,
                                          season, all_seasons, mode)
    away_snapshot = _build_team_snapshot(metadata.away_team, "AWAY", game_id, metadata.game_date,
                                          season, all_seasons, mode)

    return HistoricalGameSnapshot(
        game_id=game_id, game_date=metadata.game_date, season=season,
        home_team=metadata.home_team, away_team=metadata.away_team, mode=mode, as_of=metadata.game_date,
        home_team_snapshot=home_snapshot, away_team_snapshot=away_snapshot,
    )


class SnapshotTemporalSafetyError(AssertionError):
    """Raised by `audit_snapshot_temporal_safety` -- a STRUCTURAL leakage/composition violation.
    Deliberately a hard failure (per this phase's own "fail loudly, do not merely log warnings for
    genuine leakage" instruction), never a logged warning."""


def audit_snapshot_temporal_safety(snapshot: HistoricalGameSnapshot) -> None:
    """Structural invariant checks over an already-built snapshot. Does NOT re-derive each
    underlying truth layer's own temporal-cutoff math (that is each layer's OWN already-tested
    responsibility -- `test_player_scoring_truth.py`/`test_playmaking_truth.py`/etc. already prove
    per-attribute leakage safety; re-deriving it here would duplicate, not strengthen, that
    coverage). This audits the COMPOSITION layer's own structural contract instead:

      1. `mode` is one of the two valid, real modes.
      2. `as_of`/`game_date` are non-empty and identical (the snapshot's own belief cutoff must be
         the target game's real date, never some other date silently substituted).
      3. every player's `team_name` matches the team snapshot they're filed under (no cross-team
         player -- catches a real roster-construction bug, not a hypothetical one).
      4. no duplicate `player_id` within one team's roster.
      5. `primary_five` has at most 5 members, and every one is drawn from `eligible_roster`.
      6. In PREGAME_EXPECTED mode: no player with `availability_status == OUT` appears in
         `primary_five` (a real, checkable leakage-adjacent invariant -- an unavailable player
         must never anchor the fixed lineup the frozen engine will actually simulate).
    Raises `SnapshotTemporalSafetyError` on any violation -- never a silent log."""
    if snapshot.mode not in _VALID_MODES:
        raise SnapshotTemporalSafetyError(f"invalid mode {snapshot.mode!r}")
    if not snapshot.as_of or not snapshot.game_date or snapshot.as_of != snapshot.game_date:
        raise SnapshotTemporalSafetyError(
            f"as_of ({snapshot.as_of!r}) must equal the real game_date ({snapshot.game_date!r})")

    for team_snapshot in (snapshot.home_team_snapshot, snapshot.away_team_snapshot):
        seen_ids = set()
        for player in team_snapshot.players:
            if player.team_name != team_snapshot.team_name:
                raise SnapshotTemporalSafetyError(
                    f"player {player.player_id} filed under {team_snapshot.team_name!r} but "
                    f"carries team_name {player.team_name!r}")
            if player.player_id in seen_ids:
                raise SnapshotTemporalSafetyError(f"duplicate player_id {player.player_id} on {team_snapshot.team_name!r}")
            seen_ids.add(player.player_id)
        if len(team_snapshot.primary_five) > 5:
            raise SnapshotTemporalSafetyError(
                f"{team_snapshot.team_name!r} primary_five has {len(team_snapshot.primary_five)} members, expected <=5")
        for player_id in team_snapshot.primary_five:
            if player_id not in team_snapshot.eligible_roster:
                raise SnapshotTemporalSafetyError(
                    f"primary_five player {player_id} not in {team_snapshot.team_name!r}'s eligible_roster")
        if snapshot.mode == MODE_PREGAME_EXPECTED:
            for player in team_snapshot.players:
                if player.is_primary_five and player.availability_status == prt_rot.STATUS_OUT:
                    raise SnapshotTemporalSafetyError(
                        f"OUT player {player.player_id} appears in PREGAME_EXPECTED primary_five")


def snapshot_to_engine_input(snapshot: HistoricalGameSnapshot) -> Tuple[str, str, Tuple[str, ...], Tuple[str, ...],
                                                                          Dict[str, PlayerSimulationProfile]]:
    """The minimum adapter: `HistoricalGameSnapshot` -> exactly the 5 positional arguments
    `detailed_game.simulate_detailed_game` already expects (home_team_id, away_team_id, home_five,
    away_five, profiles) -- `rng_seed` stays the caller's own choice, not part of the snapshot.

    Returns the literal `"HOME"`/`"AWAY"` team-id strings, NOT the real team NAMES
    (`snapshot.home_team`/`away_team`) -- a real bug found and fixed during this phase's own
    end-to-end testing: every `PlayerSimulationProfile` in this snapshot was built via
    `PlayerSimulationProfile.synthetic(player_id, side)` with `side` already fixed to the literal
    `"HOME"`/`"AWAY"` engine convention every other phase in this project already uses (see
    `_build_team_snapshot`'s own call). `detailed_game_orchestrator.validate_detailed_game_inputs`
    checks `profile.team_id == home_team_id` -- passing the real team NAME there while every
    profile's own `team_id` is the literal `"HOME"`/`"AWAY"` string raised a real
    "identity/team mismatch" for every player. The real team names remain fully preserved
    elsewhere on the snapshot (`snapshot.home_team`/`away_team`) for identification/reporting;
    only the ENGINE's own internal team-id convention is used here. Does not modify possession
    mechanics; uses the existing detailed-engine API unchanged."""
    home_five = snapshot.home_team_snapshot.primary_five
    away_five = snapshot.away_team_snapshot.primary_five
    profiles: Dict[str, PlayerSimulationProfile] = {}
    for team_snapshot in (snapshot.home_team_snapshot, snapshot.away_team_snapshot):
        for player in team_snapshot.players:
            if player.is_primary_five:
                profiles[player.player_id] = player.simulation_profile
    return snapshot.home_team_snapshot.side, snapshot.away_team_snapshot.side, home_five, away_five, profiles
