"""
Turns each player's REAL absence pattern this season (cache/injuries.json,
via loader.load_player_injuries) into a randomized injury calendar for one
SIMULATED season: which of a player's team's games they're unavailable for.

Design: reuse the real COUNT of a player's real absence stints (how often
they went down) and ANCHOR each one to roughly WHEN it really started
(e.g. a player hurt before the season even began, like a real preseason
Achilles tear, stays hurt from the literal start of a simulated season
too -- not scattered to a random point in the middle regardless of when
it really happened). The LENGTH gets randomized around the real one
(+/- INJURY_LENGTH_JITTER_GAMES) instead of being copied exactly, since
recovery time realistically isn't a fixed, identical number of games
every single run -- any player could plausibly take a bit more or less
time to actually recover. (Earlier version of this file did the reverse
-- fixed length, fully random placement -- which is backwards: it let a
day-one injury land in February, and made every run identical for anyone
whose real absence was long enough to only fit one place in the season.)

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

# How much a simulated injury's LENGTH can randomly differ from the real
# one, in games -- "plus or minus about a week" per feedback. An NBA team
# plays roughly 3-4 games a week (82 games / ~26 weeks), so 3 games is
# that same "about a week" in the unit this whole file actually works in
# (games, not calendar days -- the sim has no calendar of its own).
INJURY_LENGTH_JITTER_GAMES = 3

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
    # True when this span's last missed game IS the team's literal last
    # regular-season game -- i.e. the player never got a "return game"
    # before the season ran out. Since a span's END is now real-length
    # (jittered) rather than randomly placed, this happens whenever that
    # end lands on or past the season's last game (see
    # build_season_injuries's `end` clipping) -- most reliably for a
    # real injury that was already still ongoing when the real season's
    # own data was captured.
    still_out_at_season_end: bool = False


def _team_game_ids(team_name: str, schedule: List[ScheduledGame]) -> List[str]:
    """This team's own games, in real chronological order (schedule is
    already sorted -- see data_source.fetch_schedule)."""
    return [g.game_id for g in schedule if g.home_team == team_name or g.away_team == team_name]


def build_season_injuries(
    teams: Dict[str, Team],
    schedule: List[ScheduledGame],
    real_injuries: Dict[str, dict],
) -> List[InjurySpan]:
    """
    Builds one simulated season's injuries: a list of InjurySpan, one per
    real stint (so a player with multiple real absences gets multiple
    separate spans, not one merged blob) -- each one ANCHORED to its real
    start index (data_source.fetch_player_absence_stints), with its
    LENGTH jittered +/- INJURY_LENGTH_JITTER_GAMES around the real one.
    season.py turns this into a fast per-game lookup via missed_lookup()
    below; db.py stores it as-is so main.py can report on it later.
    """
    spans: List[InjurySpan] = []

    for team_name, team in teams.items():
        team_game_ids = _team_game_ids(team_name, schedule)
        num_games = len(team_game_ids)
        if not num_games:
            continue

        for player in team.players:
            real = real_injuries.get(player.name)
            if not real:
                continue  # no real absence data for this player -- treat as fully available

            # Sorted by real start -- lets each stint's (possibly jitter-
            # lengthened) end get clipped against the NEXT one's own real
            # start below, so two real, separately-anchored absences can
            # never grow into overlapping each other.
            stints = sorted(
                (s for s in real["stints"] if s["length"] >= MIN_INJURY_STINT_LENGTH),
                key=lambda s: s["start"],
            )
            if not stints:
                continue  # iron man, or only ever had noise-level single-game misses

            for i, stint in enumerate(stints):
                # Clip -- only matters if this simulated season's game
                # count ever differs from the real one it was measured
                # against (e.g. a shortened schedule); same defensive
                # spirit as the length clip below.
                start = min(stint["start"], num_games - 1)

                jitter = int(_rng.integers(-INJURY_LENGTH_JITTER_GAMES, INJURY_LENGTH_JITTER_GAMES + 1))
                length = max(MIN_INJURY_STINT_LENGTH, stint["length"] + jitter)

                next_start = stints[i + 1]["start"] if i + 1 < len(stints) else num_games
                end = min(start + length, num_games, next_start)
                if end <= start:
                    continue  # jitter/clipping left no room (rare -- two real stints back to back)

                indices = list(range(start, end))
                spans.append(InjurySpan(
                    player_name=player.name,
                    team=team_name,
                    game_ids=[team_game_ids[i] for i in indices],
                    still_out_at_season_end=(end == num_games),
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
    # that each simulated span's START is close to its real one (the
    # anchor), and its LENGTH is close but not identical (the jitter).
    from loader import load_teams, load_schedule, load_player_injuries

    teams = load_teams()
    schedule = load_schedule()
    real_injuries = load_player_injuries()

    spans = build_season_injuries(teams, schedule, real_injuries)

    check_names = ["Jayson Tatum", "Kawhi Leonard", "Nikola Jokić", "Luka Dončić"]
    for name in check_names:
        real = real_injuries.get(name, {})
        real_stints = [(s["start"], s["length"]) for s in real.get("stints", [])]
        player_spans = [s.game_ids for s in spans if s.player_name == name]
        total_missed = sum(len(g) for g in player_spans)
        print(f"{name}: real (start, length) {real_stints} -> simulated span lengths "
              f"{[len(g) for g in player_spans]} ({total_missed} games total)")
