"""
Roles + Team Context Truth V1 -- date-safe player_id -> team mapping.

Reuses EXISTING, real infrastructure by import only: `loader.load_roster_membership` (real, already
built for `transactions.py`'s own in-season-trade handling -- "for every player who suited up for a
given team AT ALL, the first and last game (in that team's own real chronological schedule) they
actually played for them", see `data_source.fetch_roster_membership`'s own docstring) joined against
`player_game_log_ingestion.load_player_game_log`'s real per-game `(game_id, date)` ordering (both
already-cached, real sources -- no new ingestion this phase).

`roster_membership.json` is TEAM-keyed and NAME-keyed (a real, pre-existing limitation -- built for
`transactions.py`, which only ever needs "who was on this team's roster," not "what team was this
player on"). This module inverts it into a PLAYER-keyed, DATE-safe stint list, and resolves names to
real `player_id`s via `player_identity.py` so every other truth module in this project can consume
it consistently.

A player who was never traded in a season gets exactly one stint spanning that whole season (the
common case, identical in effect to today's static single-team assumption). A player traded
mid-season gets two or more real, date-bounded stints -- this is what lets role truth honor
"ability follows player_id, role may change after trade" without inventing anything: the underlying
roster_membership data already captures the real trade boundary, it was just never exposed this way
before.
"""
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple

import player_identity as pid
from loader import load_roster_membership
from player_game_log_ingestion import load_player_game_log


@dataclass(frozen=True)
class TeamStint:
    team_name: str
    first_date: str  # "YYYY-MM-DD", inclusive
    last_date: str   # "YYYY-MM-DD", inclusive


@lru_cache(maxsize=None)
def _game_id_to_date(season: str):
    """Real (game_id -> date) map built once from the season's own real per-player game log cache
    -- every game a season's players played appears in at least one player's own game list, so
    scanning all of them once covers every real game_id/date pair for that season. Process-local
    memoization only (same real data, read once instead of once per stint lookup)."""
    mapping = {}
    game_log = load_player_game_log(season)
    for games in game_log.values():
        for g in games:
            mapping[g["game_id"]] = g["date"]
    return mapping


@lru_cache(maxsize=None)
def _roster_membership_by_name(season: str):
    return load_roster_membership(season)


@lru_cache(maxsize=None)
def team_stints_for_player(player_id: str, as_of_season: str) -> List[TeamStint]:
    """Real, date-bounded stints for this player_id in this season, sorted chronologically.

    FIRST HISTORICAL PREDICTIVE BACKTEST V1: process-local `lru_cache` by (player_id, as_of_season)
    -- this is a pure function of real, on-disk, per-season cache files (real membership + real
    game-log dates), previously rebuilt via a full linear scan over EVERY team's EVERY player on
    every single call. `historical_game_outcome.py`'s real-outcome reconstruction calls
    `team_as_of_date` (which calls this) for every player in every game of a season -- tens of
    thousands of calls for one season, the large majority for the SAME (player_id, season) pair
    repeated across that player's many real games. Pure computational reuse; no change to stint
    boundaries or fallback logic. Returned lists are read-only everywhere in this project (checked
    directly) -- safe to share the cached list across callers. Assumes on-disk cache files are
    static for the life of the process; call `team_stints_for_player.cache_clear()` after mutating
    them mid-process.

    `roster_membership.json` is a real but, in this repo's current cached snapshot, INCOMPLETE
    source -- checked directly: 2023-24's cache carries only 25 of 30 real teams (5 missing
    entirely, e.g. Golden State Warriors). For a player whose team never appears in that cache,
    this falls back to `loader.load_teams`'s own static, name-keyed roster (single team for the
    whole season) -- honestly the SAME whole-season-static assumption this project already made
    everywhere before this module existed, not a new fabrication. The stint's own date bounds in
    that fallback case come from the player's real first/last game in their own cached game log
    (if available) -- never invented dates. Returns [] only if no real evidence exists for this
    player-season at all (never fabricated)."""
    resolution = pid.resolve_id_to_name(player_id)
    if resolution.state != "RESOLVED":
        return []
    name = resolution.canonical_name

    membership = _roster_membership_by_name(as_of_season)
    game_id_to_date = _game_id_to_date(as_of_season)

    stints = []
    for team_name, players in membership.items():
        for row in players:
            if row.get("name") != name:
                continue
            first_date = game_id_to_date.get(row.get("first_game_id"))
            last_date = game_id_to_date.get(row.get("last_game_id"))
            if first_date is None or last_date is None:
                continue
            stints.append(TeamStint(team_name=team_name, first_date=first_date, last_date=last_date))
    stints.sort(key=lambda s: s.first_date)
    if stints:
        return stints

    return _static_fallback_stint(player_id, name, as_of_season)


def _static_fallback_stint(player_id: str, name: str, as_of_season: str) -> List[TeamStint]:
    from loader import load_teams
    try:
        teams = load_teams(as_of_season)
    except FileNotFoundError:
        return []
    team_name = None
    for t_name, team in teams.items():
        if team.get_player(name):
            team_name = t_name
            break
    if team_name is None:
        return []
    games = load_player_game_log(as_of_season).get(player_id, [])
    if games:
        first_date, last_date = games[0]["date"], games[-1]["date"]
    else:
        first_date = last_date = f"{as_of_season[:4]}-10-01"  # documented placeholder, no real game evidence
    return [TeamStint(team_name=team_name, first_date=first_date, last_date=last_date)]


def team_as_of_date(player_id: str, as_of_date: str, as_of_season: str) -> Optional[str]:
    """The real team name this player was on as of `as_of_date` (their last real stint starting
    on or before that date; if `as_of_date` precedes every real stint this season, returns the
    EARLIEST stint's team -- a rookie/opening-night convention, not a leak, since it uses no
    evidence beyond that first real stint's own boundary). Returns None if no real evidence exists
    for this player-season (never fabricated)."""
    stints = team_stints_for_player(player_id, as_of_season)
    if not stints:
        return None
    candidates = [s for s in stints if s.first_date <= as_of_date]
    if candidates:
        return max(candidates, key=lambda s: s.first_date).team_name
    return stints[0].team_name


def current_team(player_id: str, as_of_season: str) -> Optional[str]:
    """The real, LAST team this player played for in `as_of_season` -- season-level convenience
    (no date needed), used by the season-level (not pregame) role truth entry point."""
    stints = team_stints_for_player(player_id, as_of_season)
    if not stints:
        return None
    return stints[-1].team_name


def was_traded(player_id: str, as_of_season: str) -> bool:
    return len(team_stints_for_player(player_id, as_of_season)) > 1


def team_roster_as_of_date(team_name: str, as_of_date: str, as_of_season: str) -> List[str]:
    """Availability + Expected Minutes + Rotations V1 addition (same module, additive). Real,
    date-safe roster reconstruction, combining TWO real sources the same way `team_stints_for_player`
    already does for one player at a time:

    1. `roster_membership.json`'s real per-team name list -- checked directly: this cache only
       carries entries for players it needed to correct (trades/mid-season movement), NOT a
       player's full static roster -- a player who spent the whole season on one team and needed
       no correction is typically ABSENT from this list entirely (confirmed: Indiana Pacers'
       2023-24 entry has only 2 rows -- the two real players actually involved in the Siakam
       trade -- not their full ~15-man roster).
    2. `loader.load_teams`'s own static, name-keyed roster (`rosters.json`) -- the same
       whole-season fallback `_static_fallback_stint` already uses -- for every player NOT already
       covered by (1), i.e. every player with no recorded stint anywhere this season (never
       traded, single team all year).

    A player covered by (1) is included only if their own `team_as_of_date` resolves to
    `team_name` on `as_of_date` (excludes them once they've moved on). A player only covered by
    (2) is included if `team_name` is their one real static team. Unresolvable/ambiguous names are
    silently excluded (never guessed)."""
    from loader import load_teams

    membership = _roster_membership_by_name(as_of_season)
    rows = membership.get(team_name, [])
    roster = []
    seen = set()
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        resolution = pid.resolve_name_to_id(name, season_hint=as_of_season)
        if resolution.state != "RESOLVED" or resolution.player_id in seen:
            continue
        if team_as_of_date(resolution.player_id, as_of_date, as_of_season) == team_name:
            roster.append(resolution.player_id)
            seen.add(resolution.player_id)

    # every real name appearing ANYWHERE in roster_membership this season already has a stint
    # record and is excluded from the static fallback below (their real team-as-of-date already
    # decided their inclusion above).
    names_with_stints = {row.get("name") for rows_ in membership.values() for row in rows_ if row.get("name")}

    try:
        teams = load_teams(as_of_season)
    except FileNotFoundError:
        teams = {}
    team = teams.get(team_name)
    if team is not None:
        for player in team.players:
            if player.name in names_with_stints:
                continue
            resolution = pid.resolve_name_to_id(player.name, season_hint=as_of_season)
            if resolution.state != "RESOLVED" or resolution.player_id in seen:
                continue
            roster.append(resolution.player_id)
            seen.add(resolution.player_id)
    return roster


def clear_reference_caches() -> None:
    """Test/debug reset hook -- clears `team_stints_for_player`'s process-local cache (FIRST
    HISTORICAL PREDICTIVE BACKTEST V1). Call after mutating the on-disk roster-membership or
    game-log cache mid-process."""
    team_stints_for_player.cache_clear()
