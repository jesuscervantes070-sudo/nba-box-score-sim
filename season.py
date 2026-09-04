"""
Runs a full simulated NBA season: every real scheduled game gets
simulated (game_engine.simulate_game) and stored (db.py), in real
chronological order.

This file has no simulation logic of its own -- it just orchestrates
pieces that already exist and are already tested on their own:
loader.py for real data, game_engine.py for what happens in a game,
db.py for how a result gets stored.

Usage:
    python season.py                    # simulate the season, store it
    python season.py --fresh            # wipe any existing season.db first
"""
import argparse
import time
from dataclasses import replace

from loader import load_teams, load_schedule, load_player_injuries, load_roster_membership
from game_engine import simulate_game, compute_league_averages
from injuries import build_season_injuries, missed_lookup
from transactions import expand_rosters_with_real_moves
import db


def simulate_season(season: str = "2025-26", fresh: bool = False,
                     use_injuries: bool = True, use_real_moves: bool = True, verbose: bool = True) -> None:
    """
    Simulates every scheduled game for `season`, in real chronological
    order, and stores each result via db.py.

    `game_id` is the database's primary key, so running this twice
    without `fresh=True` will fail loudly (a SQLite "UNIQUE constraint"
    error) rather than silently double-counting every game -- that's
    intentional. Pass fresh=True to start over from an empty database.

    `use_injuries` / `use_real_moves` are independent toggles (both
    default on) -- mainly for benchmark_accuracy.py to isolate each
    effect on real-vs-simulated accuracy, but also handy here if either
    one is ever suspected of causing a regression.

    `verbose` (on by default -- this is what `python season.py` and
    benchmark_accuracy.py both still see) prints the "Simulated N games
    in X.XXs" summary below. main.py's game-by-game flow turns it off:
    that line gives away that the whole season is already fully
    computed right before asking "want to watch it game by game?",
    which undercuts the point (reported directly) -- main.py has its
    own standings/box-score views to show the results with instead.
    """
    if fresh and db.DB_PATH.exists():
        db.DB_PATH.unlink()

    teams = load_teams()
    schedule = load_schedule()
    conn = db.init_db()

    # Real, league-wide baselines (what's an average defense, an
    # average steal/block rate) -- computed ONCE here, not per game,
    # since every game needs the same league-wide numbers to judge
    # "is this specific defense tougher or easier than average." Must
    # run BEFORE expand_rosters_with_real_moves below -- see that
    # function's docstring for why (double-counting a traded player).
    league_avg = compute_league_averages(teams)

    out_lookup = set()  # (player_name, game_id) pairs unavailable for that team's game

    # This season's injuries -- who's out, and for which real games --
    # built ONCE up front (not per game) since it needs the whole
    # season's schedule to decide where each absence lands. Computed
    # on the ORIGINAL (not yet trade-expanded) rosters on purpose: each
    # player's real absence data was measured against their one real
    # FINAL team (see data_source.fetch_player_absence_stints), so it
    # would be meaningless if reapplied to a team added later below.
    if use_injuries:
        real_injuries = load_player_injuries()
        injury_spans = build_season_injuries(teams, schedule, real_injuries)
        out_lookup |= missed_lookup(injury_spans)
        db.insert_injuries(conn, season, {g.game_id: g for g in schedule}, injury_spans)

    # Real in-season trades -- adds a traded player back to their OLD
    # team's pool for their real pre-trade games too (rosters.json only
    # ever lists their FINAL team), and restricts their NEW team's pool
    # to only their real post-trade games. See transactions.py.
    if use_real_moves:
        membership = load_roster_membership()
        out_lookup |= expand_rosters_with_real_moves(teams, schedule, membership)

    start = time.time()
    for scheduled_game in schedule:
        home = teams[scheduled_game.home_team]
        away = teams[scheduled_game.away_team]

        # Swap in an "available roster" for tonight -- anyone in
        # out_lookup for this exact game sits out. dataclasses.replace
        # makes a new Team with a filtered players list rather than
        # mutating the shared `teams` dict, so an injured player is
        # correctly back available for their team's OTHER games.
        home_available = replace(home, players=[
            p for p in home.players if (p.name, scheduled_game.game_id) not in out_lookup
        ])
        away_available = replace(away, players=[
            p for p in away.players if (p.name, scheduled_game.game_id) not in out_lookup
        ])

        result = simulate_game(home_available, away_available, league_avg)
        db.insert_game(conn, season, scheduled_game, result)
    elapsed = time.time() - start

    if verbose:
        print(f"Simulated {len(schedule)} games for the {season} season in {elapsed:.2f}s.")
        print(f"Stored in {db.DB_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="2025-26", help="season to simulate, e.g. 2025-26")
    parser.add_argument("--fresh", action="store_true", help="wipe any existing season.db before simulating")
    parser.add_argument("--no-injuries", action="store_true", help="disable real-injury-pattern simulation")
    parser.add_argument("--no-real-moves", action="store_true", help="disable real in-season trades")
    args = parser.parse_args()
    simulate_season(season=args.season, fresh=args.fresh,
                     use_injuries=not args.no_injuries, use_real_moves=not args.no_real_moves)
