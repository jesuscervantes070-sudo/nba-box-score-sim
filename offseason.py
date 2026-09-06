"""
What changed between two consecutive real NBA seasons -- the "offseason
bridge" that connects one simulated season to the next.

The point: this project can simulate any of 30 real seasons, but ran
each one in isolation. Playing 1996-97 straight through to 2025-26 needs
something between the seasons saying what actually happened in between:
who arrived, who left, who changed teams.

IMPORTANT about what this is and isn't. Every season uses its OWN real
rosters, so the real offseason already happened -- this module REPORTS
that change, it does not simulate one. Nothing here invents a draft or
decides free agency. What that buys is that every season stays as
accurate as it was on its own; what it costs is that a simulated 1997
champion can't change who's on which roster in 1998. Simulating the
offseason itself (progression, contracts, AI decisions) is a much
bigger, separate project -- see CLAUDE.md's Deferred list.

Everything here is DERIVED from the two seasons' real cached rosters,
including which teams are the same franchise after a rename -- see
franchise_map below, which needs no hardcoded table at all.
"""
from typing import Dict, List, Optional

from loader import load_teams


# A player who shows up on a roster for the first time is usually a
# rookie, but not always -- somebody who missed a whole season injured
# reads exactly the same way (Alonzo Mourning, out all of 2002-03 with
# kidney disease, "arrives" in 2003-04). The data records who played,
# never why they didn't, so these are labelled "arrived" and "left"
# rather than "drafted" and "retired", which would be a guess.
def _rosters_by_team(season: str) -> Dict[str, set]:
    """{team name -> set of player names} for one real season."""
    return {t.name: {p.name for p in t.players} for t in load_teams(season).values()}


def _minutes_by_player(season: str) -> Dict[str, float]:
    """Each player's real minutes per game -- used only to rank who
    matters, so a report can lead with rotation players instead of
    burying them among 90 fringe names."""
    return {p.name: p.min for t in load_teams(season).values() for p in t.players}


def franchise_map(season_a: str, season_b: str) -> Dict[str, str]:
    """
    Team name in `season_a` -> the same franchise's name in `season_b`,
    for every team that carried over.

    Franchises really do rename and relocate mid-history (Seattle
    SuperSonics -> Oklahoma City Thunder, New Jersey -> Brooklyn Nets,
    Vancouver -> Memphis Grizzlies, Charlotte Bobcats -> Hornets,
    Washington Bullets -> Wizards, New Orleans Hornets -> Pelicans), and
    a multi-season run has to follow YOUR team through that instead of
    losing it.

    Worked out from ROSTER OVERLAP rather than a hardcoded rename table:
    a renamed franchise keeps most of its players, so the disappeared
    name matches the new name it shares the most players with. Checked
    against all six real renames in this project's range -- each shares
    3 to 8 players with its true successor and only 1 or 2 with the
    nearest unrelated team, so the signal is not close.

    A brand-new EXPANSION team (the 2004-05 Bobcats) simply has no
    disappeared team to match, and is left out rather than forced onto
    one.
    """
    a, b = _rosters_by_team(season_a), _rosters_by_team(season_b)
    mapping = {name: name for name in a if name in b}

    gone = [name for name in a if name not in b]
    new = [name for name in b if name not in a]
    for old_name in gone:
        if not new:
            continue
        successor = max(new, key=lambda n: len(a[old_name] & b[n]))
        # Require SOME shared roster -- otherwise two unrelated things
        # (a team folding and an expansion team arriving) would get
        # linked just for happening in the same summer.
        if a[old_name] & b[successor]:
            mapping[old_name] = successor
    return mapping


def diff_seasons(season_a: str, season_b: str) -> dict:
    """
    Everything that changed between two consecutive real seasons.

    Returns:
      arrived   [{player, team, min}]      -- not in season_a at all
      left      [{player, team, min}]      -- not in season_b at all
      moved     [{player, from, to, min}]  -- in both, different team
      renamed   [{from, to}]               -- franchise kept, name changed
    Each player list is sorted by how much they really played (in
    whichever season they're being described from), so a caller can show
    the ten that matter instead of ninety that don't.
    """
    a_rosters, b_rosters = _rosters_by_team(season_a), _rosters_by_team(season_b)
    a_min, b_min = _minutes_by_player(season_a), _minutes_by_player(season_b)
    team_of_a = {p: t for t, players in a_rosters.items() for p in players}
    team_of_b = {p: t for t, players in b_rosters.items() for p in players}
    mapping = franchise_map(season_a, season_b)

    arrived, left, moved = [], [], []
    for player, team in team_of_b.items():
        if player not in team_of_a:
            arrived.append({"player": player, "team": team, "min": b_min.get(player, 0.0)})
        # Compared through the franchise map, so a player who stayed put
        # while his team was renamed is NOT reported as having moved.
        elif mapping.get(team_of_a[player]) != team:
            moved.append({"player": player, "from": team_of_a[player], "to": team,
                          "min": b_min.get(player, 0.0)})
    for player, team in team_of_a.items():
        if player not in team_of_b:
            left.append({"player": player, "team": team, "min": a_min.get(player, 0.0)})

    renamed = [{"from": old, "to": new} for old, new in mapping.items() if old != new]

    for group in (arrived, left, moved):
        group.sort(key=lambda r: -r["min"])
    return {"from_season": season_a, "to_season": season_b,
            "arrived": arrived, "left": left, "moved": moved, "renamed": renamed}


def team_changes(diff: dict, team_name: str) -> dict:
    """
    The same diff narrowed to ONE team -- who it gained, who it lost, and
    who arrived new. `team_name` is the team's name in the LATER season
    (that's the roster the user is about to play with).
    """
    return {
        "arrived": [r for r in diff["arrived"] if r["team"] == team_name],
        "gained": [r for r in diff["moved"] if r["to"] == team_name],
        "lost": [r for r in diff["moved"] if r["from"] == team_name],
        "left_league": [r for r in diff["left"] if r["team"] == team_name],
    }
