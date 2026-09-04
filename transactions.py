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
    average stat line, it doesn't get split per team, same as every
    other real/derived-stat rule in this project.

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

        existing_names = {p.name for p in team.players}
        for entry in membership.get(team_name, []):
            name = entry["name"]
            if name not in existing_names:
                source = all_players_by_name.get(name)
                if source is None:
                    continue  # no real stat line anywhere -- can't simulate them
                # A new Player entry for THIS team, same real per-game
                # stat line, just attributed to this team for box-score
                # purposes (dataclasses.replace makes a copy -- doesn't
                # touch the original team's Player object).
                team.players.append(replace(source, team=team_name))
                existing_names.add(name)

            first_idx = game_index.get(entry["first_game_id"])
            last_idx = game_index.get(entry["last_game_id"])
            if first_idx is None or last_idx is None:
                continue  # shouldn't happen -- membership game_ids come from this same schedule

            for i, gid in enumerate(team_game_ids):
                if i < first_idx or i > last_idx:
                    unavailable.add((name, gid))

    return unavailable


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
