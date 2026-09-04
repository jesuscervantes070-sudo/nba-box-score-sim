"""
Playoffs: seeds each conference off the regular-season standings, runs
the real current-NBA play-in tournament for the 7-10 seeds, then a
standard fixed 8-team bracket (no reseeding between rounds -- that
matches how the real playoffs actually work) all the way to the Finals.

Like season.py, this file has no simulation logic of its own -- every
game is still produced by game_engine.simulate_game(). This file only
decides WHO plays WHOM and WHERE (home court), then reads who won.

Kept deliberately "easy" per the project's current priorities:
  - Standings ties break by win count, then team name alphabetically.
    Real NBA tiebreakers (head-to-head, division record, etc.) are a
    much deeper rabbit hole -- not worth it for this pass.
  - Nothing here gets written to season.db. It's simulate-and-print,
    same spirit as a single exhibition game in main.py's option 1.
"""
from typing import Dict, List, Tuple

from models import Team
from game_engine import simulate_game, GameResult, LeagueAverages
import db

# A playoff seed is carried around as (seed_number, team_name) so that
# later rounds -- where the two teams came from different first-round
# matchups -- can still tell which team is the better seed (and
# therefore gets home-court advantage) without looking anything up.
Seed = Tuple[int, str]

# Real NBA best-of-7 home/away pattern: the better seed hosts games
# 1, 2, 5, and 7; the other team hosts games 3, 4, and 6. A series
# that ends early just never reaches the later entries.
HOME_PATTERN = [True, True, False, False, True, False, True]


def seed_conference(standings: List[dict], teams: Dict[str, Team], conference: str) -> List[str]:
    """
    Top 10 teams in one conference, ranked 1-10, off the regular-season
    standings. `standings` is already win-sorted (see db.get_standings),
    but ties in win count aren't broken there -- broken here instead,
    by team name, just so seeding is deterministic and reproducible.
    """
    conf_teams = [row for row in standings if teams[row["team"]].conference == conference]
    conf_teams.sort(key=lambda row: (-row["W"], row["team"]))
    return [row["team"] for row in conf_teams[:10]]


def _play_one_game(home: str, away: str, teams: Dict[str, Team], league_avg: LeagueAverages) -> Tuple[str, str]:
    """Simulates one game, returns (winner, loser) by name."""
    result = simulate_game(teams[home], teams[away], league_avg)
    if result.home_score > result.away_score:
        return home, away
    return away, home


def run_play_in(seeded10: List[str], teams: Dict[str, Team], league_avg: LeagueAverages) -> dict:
    """
    The real current-NBA play-in tournament for one conference's 7-10
    seeds:
      Game 1: 7 vs 8, better record (7) hosts -- winner takes the 7 seed.
      Game 2: 9 vs 10, better record (9) hosts -- loser is eliminated.
      Game 3: loser of Game 1 vs winner of Game 2, hosted by the
              7-seed team (still the better regular-season record of
              the two) -- winner takes the 8 seed.

    Returns the final 7 and 8 seeds plus a plain-text log of what
    happened, so main.py can print it without re-deriving any of this.
    """
    seed7, seed8, seed9, seed10 = seeded10[6], seeded10[7], seeded10[8], seeded10[9]
    log = []

    g1_winner, g1_loser = _play_one_game(seed7, seed8, teams, league_avg)
    log.append(f"  Game 1 (7 vs 8): {seed7} vs {seed8} -> {g1_winner} wins, becomes the 7 seed")

    g2_winner, g2_loser = _play_one_game(seed9, seed10, teams, league_avg)
    log.append(f"  Game 2 (9 vs 10): {seed9} vs {seed10} -> {g2_winner} wins, advances; {g2_loser} eliminated")

    g3_winner, g3_loser = _play_one_game(g1_loser, g2_winner, teams, league_avg)
    log.append(f"  Game 3 ({g1_loser} vs {g2_winner}): {g3_winner} wins, becomes the 8 seed; {g3_loser} eliminated")

    return {"seed_7": g1_winner, "seed_8": g3_winner, "log": log}


def simulate_series(favored: Seed, underdog: Seed, teams: Dict[str, Team], league_avg: LeagueAverages) -> dict:
    """
    Best-of-7 between two seeds, first to 4 wins, real 2-2-1-1-1 home
    court favoring `favored` (the better seed, or -- in the Finals --
    the better regular-season record; see run_finals below).

    `game_log` holds the full GameResult for every game played (not
    just the final score) -- nothing here writes to season.db (see
    this module's docstring), so this is the only record of what
    actually happened, and it's what compute_series_player_averages
    below reads to build a series' player stat lines.
    """
    favored_seed, favored_name = favored
    underdog_seed, underdog_name = underdog

    wins = {favored_name: 0, underdog_name: 0}
    game_log: List[GameResult] = []

    for game_num in range(7):
        if wins[favored_name] == 4 or wins[underdog_name] == 4:
            break
        favored_hosts = HOME_PATTERN[game_num]
        home = favored_name if favored_hosts else underdog_name
        away = underdog_name if favored_hosts else favored_name

        result = simulate_game(teams[home], teams[away], league_avg)
        winner = home if result.home_score > result.away_score else away
        wins[winner] += 1
        game_log.append(result)

    winner = favored_name if wins[favored_name] == 4 else underdog_name
    loser = underdog_name if winner == favored_name else favored_name
    winner_seed = favored_seed if winner == favored_name else underdog_seed

    return {
        "winner": winner, "winner_seed": winner_seed, "loser": loser,
        "wins": dict(wins), "game_log": game_log,
    }


def compute_series_player_averages(game_log: List[GameResult]) -> Dict[str, dict]:
    """
    Per-player averages across a series' games (e.g. the Finals) --
    same "derive, don't duplicate" rule as db.get_player_season_averages:
    counting stats are averaged directly, but PTS/percentages are
    recomputed from the summed makes/attempts, never averaged on their
    own. This is the in-memory equivalent of that function for a
    series that was never written to season.db (see this module's
    docstring on why) -- reuses db.STAT_COLS so the two can't quietly
    drift onto different column lists.

    A DNP (0 minutes) in a given game doesn't count toward that
    player's games_played, same reasoning as db.insert_game skipping
    DNP rows: a season/series average is "per game PLAYED."
    """
    totals: Dict[str, dict] = {}
    for game in game_log:
        for player in game.home_players + game.away_players:
            if player.min == 0:
                continue
            totals.setdefault(player.name, {"team": player.team, "games": 0,
                                              **{c: 0.0 for c in db.STAT_COLS}})
            totals[player.name]["games"] += 1
            for col in db.STAT_COLS:
                totals[player.name][col] += getattr(player, col)

    averages: Dict[str, dict] = {}
    for name, t in totals.items():
        g = t["games"]
        averages[name] = {
            "player": name, "team": t["team"], "games_played": g,
            **{c: t[c] / g for c in db.STAT_COLS},
            "pts": (2 * (t["fgm"] - t["fg3m"]) + 3 * t["fg3m"] + t["ftm"]) / g,
            "fg_pct": (t["fgm"] / t["fga"]) if t["fga"] else 0.0,
            "fg3_pct": (t["fg3m"] / t["fg3a"]) if t["fg3a"] else 0.0,
            "ft_pct": (t["ftm"] / t["fta"]) if t["fta"] else 0.0,
        }
    return averages


def _series_line(matchup_label: str, result: dict) -> str:
    w, l = result["winner"], result["loser"]
    return f"  {matchup_label}: {w} def. {l}, {result['wins'][w]}-{result['wins'][l]}"


def _order_by_seed(a: dict, b: dict) -> Tuple[Seed, Seed]:
    """
    Given two simulate_series results, returns (favored, underdog) as
    Seed tuples -- whichever winner has the better (lower) seed number
    goes first. Used to figure out home-court advantage once two
    bracket "halves" that started from different seeds meet up.
    """
    seed_a: Seed = (a["winner_seed"], a["winner"])
    seed_b: Seed = (b["winner_seed"], b["winner"])
    return (seed_a, seed_b) if seed_a[0] < seed_b[0] else (seed_b, seed_a)


def run_conference_bracket(conference: str, seeded10: List[str], teams: Dict[str, Team],
                            league_avg: LeagueAverages) -> dict:
    """
    Full run for one conference: play-in, then the fixed 8-team
    bracket (1v8/4v5/3v6/2v7, no reseeding between rounds -- matching
    the real current NBA playoff format) up to a conference champion.
    """
    play_in = run_play_in(seeded10, teams, league_avg)

    # Seeds 1-6 go straight in; 7 and 8 come from the play-in above.
    bracket: Dict[int, str] = {i: seeded10[i - 1] for i in range(1, 7)}
    bracket[7] = play_in["seed_7"]
    bracket[8] = play_in["seed_8"]

    round_logs = []

    # Round 1: fixed pairings by seed, not reseeded.
    r1_pairs = [(1, 8), (4, 5), (3, 6), (2, 7)]
    r1_results = {}
    lines = [f"-- {conference} Conference First Round --"]
    for hi, lo in r1_pairs:
        favored, underdog = (hi, bracket[hi]), (lo, bracket[lo])
        result = simulate_series(favored, underdog, teams, league_avg)
        r1_results[(hi, lo)] = result
        lines.append(_series_line(f"({hi}) {bracket[hi]} vs ({lo}) {bracket[lo]}", result))
    round_logs.append(lines)

    # Round 2 (conference semis): winner(1v8) vs winner(4v5), and
    # winner(2v7) vs winner(3v6) -- the two "halves" of the bracket.
    w_1_8 = r1_results[(1, 8)]
    w_4_5 = r1_results[(4, 5)]
    w_2_7 = r1_results[(2, 7)]
    w_3_6 = r1_results[(3, 6)]

    half_a = simulate_series(*_order_by_seed(w_1_8, w_4_5), teams, league_avg)
    half_b = simulate_series(*_order_by_seed(w_2_7, w_3_6), teams, league_avg)
    lines = [
        f"-- {conference} Conference Semifinals --",
        _series_line(f"({w_1_8['winner_seed']}) {w_1_8['winner']} vs ({w_4_5['winner_seed']}) {w_4_5['winner']}", half_a),
        _series_line(f"({w_2_7['winner_seed']}) {w_2_7['winner']} vs ({w_3_6['winner_seed']}) {w_3_6['winner']}", half_b),
    ]
    round_logs.append(lines)

    # Round 3: conference finals.
    favored, underdog = _order_by_seed(half_a, half_b)
    conf_finals = simulate_series(favored, underdog, teams, league_avg)
    lines = [
        f"-- {conference} Conference Finals --",
        _series_line(f"({favored[0]}) {favored[1]} vs ({underdog[0]}) {underdog[1]}", conf_finals),
    ]
    round_logs.append(lines)

    # Structured (not pre-formatted-text) view of the same bracket, for
    # main.py's ASCII bracket diagram. Leaves are listed in the order
    # [1, 8, 4, 5, 2, 7, 3, 6] -- NOT seed order -- because that's the
    # order a bracket diagram actually draws in: each adjacent pair is
    # a real matchup (1v8, 4v5, 2v7, 3v6), and each adjacent PAIR of
    # pairs is exactly who meets in the next round (half_a = winners of
    # 1v8 & 4v5; half_b = winners of 2v7 & 3v6) -- so a simple "connect
    # adjacent nodes, recurse" renderer draws the correct tree with no
    # extra bracket-structure logic of its own.
    tree = {
        "leaves": [(seed, bracket[seed]) for seed in (1, 8, 4, 5, 2, 7, 3, 6)],
        "round1": [r1_results[(1, 8)], r1_results[(4, 5)], r1_results[(2, 7)], r1_results[(3, 6)]],
        "round2": [half_a, half_b],
        "round3": conf_finals,
    }

    return {
        "conference": conference,
        "play_in_log": play_in["log"],
        "round_logs": round_logs,
        "tree": tree,
        "champion": conf_finals["winner"],
        "champion_seed": conf_finals["winner_seed"],
    }


def run_finals(east_champ: str, east_seed: int, west_champ: str, west_seed: int,
                standings: List[dict], teams: Dict[str, Team], league_avg: LeagueAverages) -> dict:
    """
    NBA Finals: East champion vs West champion. Seed numbers aren't
    comparable across conferences, so home-court advantage instead
    goes to whichever finalist won more regular-season games (the real
    rule) -- falling back to the better (lower) conference seed only
    if regular-season wins are exactly tied.
    """
    wins_by_team = {row["team"]: row["W"] for row in standings}
    east_wins, west_wins = wins_by_team[east_champ], wins_by_team[west_champ]

    if east_wins != west_wins:
        favored_name, underdog_name = (east_champ, west_champ) if east_wins > west_wins else (west_champ, east_champ)
    else:
        favored_name, underdog_name = (east_champ, west_champ) if east_seed <= west_seed else (west_champ, east_champ)

    # Seed numbers no longer mean anything cross-conference at this
    # point -- 1/2 here are just "favored"/"underdog" markers so
    # simulate_series's home-court logic still works unchanged.
    result = simulate_series((1, favored_name), (2, underdog_name), teams, league_avg)
    return result


def run_playoffs(teams: Dict[str, Team], standings: List[dict], league_avg: LeagueAverages) -> dict:
    """
    Full playoffs: seed both conferences, run each conference's play-in
    and bracket, then the Finals. Returns everything main.py needs to
    print -- this function itself does no printing, same "logic and
    display stay separate" rule as the rest of the project.
    """
    east_seeded = seed_conference(standings, teams, "East")
    west_seeded = seed_conference(standings, teams, "West")

    east = run_conference_bracket("East", east_seeded, teams, league_avg)
    west = run_conference_bracket("West", west_seeded, teams, league_avg)

    finals = run_finals(
        east["champion"], east["champion_seed"],
        west["champion"], west["champion_seed"],
        standings, teams, league_avg,
    )

    return {"east": east, "west": west, "finals": finals, "champion": finals["winner"]}
