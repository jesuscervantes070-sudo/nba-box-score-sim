"""
Turns each player's REAL absence pattern this season (cache/injuries.json,
via loader.load_player_injuries) into a randomized injury calendar for one
SIMULATED season: which of a player's team's games they're unavailable for.

Design: reuse the real COUNT and LENGTH of a player's real absence stints
(how often they went down, how long each time), but randomize WHERE those
stints land in the simulated schedule. That keeps a simulated season
"accurate to this season" -- someone who was really hurt a lot stays hurt
a lot, an iron man stays available -- without literally replaying the
exact same real dates every single simulated run.

This does NOT try to guess WHY a real absence happened (injury vs. rest
vs. a trade) -- see data_source.py's fetch_player_absence_stints for that
caveat. It just decides which games a player sits, and reports it as a
generic "injury" absence, matching what was asked for: no specific injury
types, just "this player was out for N games."
"""
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import numpy as np

from models import Team, ScheduledGame

# Real single-game absences are almost always a coach's-decision rest day
# or something similarly minor, not a real injury -- tested against the
# real 2025-26 data: counting every missed game flagged 425 of 522
# players (81%) as having had 3+ separate "injuries" in a season, which
# drowns out the real, meaningful absences in noise. Requiring at least 2
# CONSECUTIVE missed games before it counts as a simulated injury cuts
# that noise out while keeping real short bumps and bruises.
MIN_INJURY_STINT_LENGTH = 2

_rng = np.random.default_rng()  # matches game_engine.py's own module-level rng


@dataclass
class InjurySpan:
    """One simulated absence: a player is OUT for a contiguous block of
    their team's games. Kept as its own record (rather than just a flat
    set of missed game_ids per player) so a player with TWO separate
    real absences this season -- e.g. Kawhi Leonard's real [10, 3] --
    gets reported as two distinct injuries, not one merged blob."""
    player_name: str
    team: str
    game_ids: List[str]  # chronological order, this team's games only


def _team_game_ids(team_name: str, schedule: List[ScheduledGame]) -> List[str]:
    """This team's own games, in real chronological order (schedule is
    already sorted -- see data_source.fetch_schedule)."""
    return [g.game_id for g in schedule if g.home_team == team_name or g.away_team == team_name]


def _place_stints(num_games: int, stint_lengths: List[int]) -> List[List[int]]:
    """
    Randomly assigns each stint length its own non-overlapping block of
    game INDICES (0..num_games-1). Returns one index list per stint that
    could actually be placed.

    Longer stints are placed FIRST -- they have fewer valid spots to fit
    into, so giving them first pick of a still-mostly-open season avoids
    a real season-long injury failing to fit just because several short
    stints already grabbed the room for it.
    """
    # Free space as a list of (start, end) index ranges, end EXCLUSIVE --
    # starts as the whole season, and shrinks as stints claim pieces of it.
    free_ranges = [(0, num_games)]
    placements = []

    for length in sorted(stint_lengths, reverse=True):
        candidates = [
            s
            for (start, end) in free_ranges
            for s in range(start, end - length + 1)
        ]
        if not candidates:
            # No room left to fit this stint (only happens if a player's
            # real stints add up to more games than this simulated
            # season has, or overlap after clipping). Skip it rather
            # than crash -- losing one absence is a far smaller problem
            # than the whole season sim failing.
            continue

        chosen_start = int(_rng.choice(candidates))
        chosen_end = chosen_start + length
        placements.append(list(range(chosen_start, chosen_end)))

        # Rebuild free_ranges with this new block carved out of it.
        new_free = []
        for (start, end) in free_ranges:
            if chosen_end <= start or chosen_start >= end:
                new_free.append((start, end))  # untouched by this placement
                continue
            if start < chosen_start:
                new_free.append((start, chosen_start))
            if chosen_end < end:
                new_free.append((chosen_end, end))
        free_ranges = new_free

    return placements


def build_season_injuries(
    teams: Dict[str, Team],
    schedule: List[ScheduledGame],
    real_injuries: Dict[str, dict],
) -> List[InjurySpan]:
    """
    Builds one simulated season's injuries: a list of InjurySpan, one per
    stint (so a player with multiple real absences gets multiple separate
    spans, not one merged blob). season.py turns this into a fast
    per-game lookup via `missed_lookup()` below; db.py stores it as-is so
    main.py can report on it later.
    """
    spans: List[InjurySpan] = []

    for team_name, team in teams.items():
        team_game_ids = _team_game_ids(team_name, schedule)
        if not team_game_ids:
            continue

        for player in team.players:
            real = real_injuries.get(player.name)
            if not real:
                continue  # no real absence data for this player -- treat as fully available

            stint_lengths = [n for n in real["stints"] if n >= MIN_INJURY_STINT_LENGTH]
            if not stint_lengths:
                continue  # iron man, or only ever had noise-level single-game misses

            # A real stint longer than this simulated season's actual
            # game count can't fit as-is (e.g. a shortened schedule) --
            # clip it down rather than drop it, so a real season-long
            # injury still reads as "out all year" instead of vanishing.
            stint_lengths = [min(n, len(team_game_ids)) for n in stint_lengths]

            for indices in _place_stints(len(team_game_ids), stint_lengths):
                spans.append(InjurySpan(
                    player_name=player.name,
                    team=team_name,
                    game_ids=[team_game_ids[i] for i in indices],
                ))

    return spans


def missed_lookup(spans: List[InjurySpan]) -> Set[Tuple[str, str]]:
    """
    Flattens injury spans into a fast set of (player_name, game_id) pairs
    -- what season.py actually needs to check, once per player per game,
    when deciding who's available for a given scheduled game.
    """
    return {(span.player_name, gid) for span in spans for gid in span.game_ids}


if __name__ == "__main__":
    # Quick manual sanity check: build one calendar and print a handful
    # of real, previously-spot-checked players so a human can eyeball
    # that the stint COUNT/LENGTH still matches their real pattern, just
    # placed on different (random) games.
    from loader import load_teams, load_schedule, load_player_injuries

    teams = load_teams()
    schedule = load_schedule()
    real_injuries = load_player_injuries()

    spans = build_season_injuries(teams, schedule, real_injuries)

    check_names = ["Jayson Tatum", "Kawhi Leonard", "Nikola Jokić", "Luka Dončić"]
    for name in check_names:
        real = real_injuries.get(name, {})
        player_spans = [s.game_ids for s in spans if s.player_name == name]
        total_missed = sum(len(g) for g in player_spans)
        print(f"{name}: real stints {real.get('stints')} -> simulated spans "
              f"{[len(g) for g in player_spans]} ({total_missed} games total)")
