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
from pathlib import Path

from loader import load_teams, load_schedule
from game_engine import simulate_game, compute_league_averages
import db


def simulate_season(season: str = "2025-26", fresh: bool = False) -> None:
    """
    Simulates every scheduled game for `season`, in real chronological
    order, and stores each result via db.py.

    `game_id` is the database's primary key, so running this twice
    without `fresh=True` will fail loudly (a SQLite "UNIQUE constraint"
    error) rather than silently double-counting every game -- that's
    intentional. Pass fresh=True to start over from an empty database.
    """
    if fresh and db.DB_PATH.exists():
        db.DB_PATH.unlink()

    teams = load_teams()
    schedule = load_schedule()
    conn = db.init_db()

    # Real, league-wide baselines (what's an average defense, an
    # average steal/block rate) -- computed ONCE here, not per game,
    # since every game needs the same league-wide numbers to judge
    # "is this specific defense tougher or easier than average."
    league_avg = compute_league_averages(teams)

    start = time.time()
    for scheduled_game in schedule:
        home = teams[scheduled_game.home_team]
        away = teams[scheduled_game.away_team]
        result = simulate_game(home, away, league_avg)
        db.insert_game(conn, season, scheduled_game, result)
    elapsed = time.time() - start

    print(f"Simulated {len(schedule)} games for the {season} season in {elapsed:.2f}s.")
    print(f"Stored in {db.DB_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="2025-26", help="season to simulate, e.g. 2025-26")
    parser.add_argument("--fresh", action="store_true", help="wipe any existing season.db before simulating")
    args = parser.parse_args()
    simulate_season(season=args.season, fresh=args.fresh)
