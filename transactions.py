"""
Real player movement during a simulated season -- for now, just real
in-season TRADES (see the module-level plan: signings/free agency, and
eventually user-driven moves, are natural extensions of this same idea,
not built yet).

rosters.json only ever files a player under ONE team -- whichever
team's roster call happened to return them, in practice their real
FINAL team that season. That means, without this file, a player who
was traded mid-season is simulated as if they'd been on their new team
the whole time, and never existed on their old team at all -- even for
the real games they actually played there. This uses
cache/roster_membership.json (data_source.fetch_roster_membership,
built from real game-by-game data) to fix both sides: add the player
back to their OLD team's pool for their real pre-trade games, and
restrict their NEW team's pool to only their real post-trade games.

For the vast majority of players (never traded this season), this
changes nothing -- their membership window is just "team's first game"
to "team's last game," same as today.
"""
from dataclasses import replace
from typing import Dict, List, Set, Tuple

from models import Player, Team, ScheduledGame


def _team_game_ids(team_name: str, schedule: List[ScheduledGame]) -> List[str]:
    """This team's own games, in real chronological order (schedule is
    already sorted -- see data_source.fetch_schedule)."""
    return [g.game_id for g in schedule if g.home_team == team_name or g.away_team == team_name]


def expand_rosters_with_real_moves(
    teams: Dict[str, Team],
    schedule: List[ScheduledGame],
    membership: Dict[str, list],
) -> Set[Tuple[str, str]]:
    """
    Mutates each Team's `players` list IN PLACE to include every player
    who really played for them at any point this season (not just
    whoever rosters.json currently files them under), pulling each
    added player's real per-game stat line from whichever team's
    roster they already exist under -- one player has one real season-
    average stat line for shooting/passing/etc, it doesn't get split
    per team, same as every other real/derived-stat rule in this
    project. MINUTES is the one deliberate exception -- see the
    real_min handling below -- because it isn't just a descriptive
    stat here, it's the weight/denominator the whole sim is built on.

    IMPORTANT: call this AFTER game_engine.compute_league_averages(teams),
    not before -- that function sums real per-player stats across
    `team.players` to build the league-wide baseline, and a traded
    player added to a SECOND team before that runs would get counted
    twice.

    Returns the "not on this roster yet/anymore" lookup -- a set of
    (player_name, game_id) pairs season.py should treat as unavailable
    for that specific team's game, symmetric with injuries.missed_lookup
    and meant to be combined with it the same way.
    """
    # One name -> Player lookup across every team's roster as loaded, so
    # a traded player's real stat line can be reused when adding them
    # to a team they aren't already listed under.
    all_players_by_name: Dict[str, Player] = {
        p.name: p for team in teams.values() for p in team.players
    }

    unavailable: Set[Tuple[str, str]] = set()

    for team_name, team in teams.items():
        team_game_ids = _team_game_ids(team_name, schedule)
        if not team_game_ids:
            continue
        game_index = {gid: i for i, gid in enumerate(team_game_ids)}

        # Index (not just a name set) so an ALREADY-listed player's
        # real_min can be corrected in place below, not just checked.
        index_by_name = {p.name: i for i, p in enumerate(team.players)}

        for entry in membership.get(team_name, []):
            name = entry["name"]

            # Sentinel for "this player never actually played a single
            # real game for this team at all" (see
            # data_source.fetch_roster_membership's misattributed-
            # player handling, e.g. a trade that happened after this
            # team's real season was already over) -- mark every one
            # of this team's games unavailable for them, rather than
            # trying to compute a first/last-game window that has no
            # real game to anchor to. No need to add them to
            # team.players either -- rosters.json already (incorrectly)
            # lists them here, which is exactly the problem being fixed.
            if entry["first_game_id"] is None:
                unavailable |= {(name, gid) for gid in team_game_ids}
                continue

            # This team-STINT's real minutes, not the season-blended
            # average rosters.json's Player object carries -- see
            # fetch_roster_membership's real_min comment for why this
            # matters (confirmed cause of trades hurting accuracy).
            # Applies whether the player is already listed here
            # (rosters.json's side -- their existing min gets
            # corrected) or being newly added from the other side.
            real_min = entry.get("real_min")

            if name in index_by_name:
                if real_min is not None:
                    i = index_by_name[name]
                    team.players[i] = replace(team.players[i], min=real_min)
            else:
                source = all_players_by_name.get(name)
                if source is None:
                    continue  # no real stat line anywhere -- can't simulate them
                # A new Player entry for THIS team, same real per-game
                # shooting/passing/etc stat line, just attributed to
                # this team for box-score purposes (dataclasses.replace
                # makes a copy -- doesn't touch the original team's
                # Player object) -- WITH minutes corrected to this
                # stint's real value, falling back to the blended one
                # only if a stint-specific value somehow isn't available.
                new_min = real_min if real_min is not None else source.min
                team.players.append(replace(source, team=team_name, min=new_min))
                index_by_name[name] = len(team.players) - 1

            first_idx = game_index.get(entry["first_game_id"])
            last_idx = game_index.get(entry["last_game_id"])
            if first_idx is None or last_idx is None:
                continue  # shouldn't happen -- membership game_ids come from this same schedule

            for i, gid in enumerate(team_game_ids):
                if i < first_idx or i > last_idx:
                    unavailable.add((name, gid))

    return unavailable


def summarize_moves(membership: Dict[str, list]) -> List[dict]:
    """
    Every real in-season trade this season, as a chronological team
    sequence per player (a player traded twice this season -- rare,
    but it happens -- shows all three teams in order, not just the
    first and last).

    Built by inverting roster_membership.json from "team -> its traded
    players" to "player -> the teams they were on," then sorting each
    player's teams by first_game_id. game_id strings are fixed-width
    and zero-padded (see fetch_schedule), so they already sort into
    real chronological order with no separate date lookup needed.

    Only WHERE a player moved is available here, not WHY -- no real
    trade-details data (players/picks exchanged) is fetched anywhere
    in this project, just real game-log evidence of which team someone
    actually suited up for.
    """
    by_player: Dict[str, List[Tuple[str, str]]] = {}
    for team_name, entries in membership.items():
        for entry in entries:
            if entry["first_game_id"] is None:
                # Sentinel for "never actually played here at all" (see
                # data_source.fetch_roster_membership's misattributed-
                # player handling) -- not a real team in their history,
                # so it doesn't belong in a "moved from -> to" list, and
                # can't be chronologically sorted against a real game_id
                # anyway (None has no ordering against a string).
                continue
            by_player.setdefault(entry["name"], []).append((entry["first_game_id"], team_name))

    moves = []
    for name, team_windows in by_player.items():
        if len(team_windows) < 2:
            continue  # only one REAL team once the sentinel above is filtered -- not a move
        team_windows.sort()  # chronological -- see docstring on why this is safe
        moves.append({"player": name, "teams": [team for _, team in team_windows]})

    moves.sort(key=lambda m: m["player"])
    return moves


if __name__ == "__main__":
    # Quick manual sanity check: expand rosters, then report how many
    # real trades this actually found, and print one example (if any)
    # so a human can eyeball the before/after team assignment.
    from loader import load_teams, load_schedule, load_roster_membership
    from game_engine import compute_league_averages

    teams = load_teams()
    schedule = load_schedule()
    membership = load_roster_membership()

    before_counts = {name: len(t.players) for name, t in teams.items()}
    compute_league_averages(teams)  # must run before expansion -- see docstring
    unavailable = expand_rosters_with_real_moves(teams, schedule, membership)

    grown = {name: len(t.players) - before_counts[name] for name, t in teams.items() if len(t.players) > before_counts[name]}
    traded_players = {name for name, _ in unavailable}

    print(f"{len(traded_players)} players have a restricted (traded) window this season.")
    print(f"{sum(grown.values())} roster slots added back across {len(grown)} teams (players' old teams).")
    for team_name, added in list(grown.items())[:5]:
        print(f"  {team_name}: +{added}")
