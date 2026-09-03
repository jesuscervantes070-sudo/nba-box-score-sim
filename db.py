"""
SQLite storage for simulated games. This is where every game the sim engine
produces gets written, so season-long accuracy checks and standings don't
require re-running anything.

Design principle: team_game_stats rows are DERIVED by summing that game's
player_game_stats rows, never entered independently. This is what makes it
structurally impossible for a team total to disagree with its own players.

Foul-out tracking (`fouled_out`) is included in the schema now even though
the sim engine that produces it doesn't exist yet -- when we build the game
engine, a player accumulating 6 PF mid-sim gets benched and any remaining
minutes get redistributed to other players on the roster. This table is
just where that outcome gets recorded.
"""
import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "cache" / "sim_history.db"

STAT_COLS = ["pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
             "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY AUTOINCREMENT,
    season TEXT NOT NULL,
    game_number INTEGER,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS team_game_stats (
    game_id INTEGER NOT NULL,
    team TEXT NOT NULL,
    {', '.join(f'{c} REAL' for c in STAT_COLS)},
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS player_game_stats (
    game_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT NOT NULL,
    min REAL,
    {', '.join(f'{c} REAL' for c in STAT_COLS)},
    fouled_out INTEGER DEFAULT 0,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_pgs_player ON player_game_stats(player_name);
CREATE INDEX IF NOT EXISTS idx_pgs_game ON player_game_stats(game_id);
CREATE INDEX IF NOT EXISTS idx_tgs_game ON team_game_stats(game_id);
"""


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_game(conn: sqlite3.Connection, season: str, home_team: str, away_team: str,
                 home_score: int, away_score: int, player_rows: list,
                 game_number: Optional[int] = None) -> int:
    """
    player_rows: list of dicts, one per player who appeared, each with keys:
        player_name, team, min, pts, reb, oreb, dreb, ast, stl, blk, tov, pf,
        fgm, fga, fg3m, fg3a, ftm, fta, fouled_out (optional bool)
    """
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO games (season, game_number, home_team, away_team, home_score, away_score) "
        "VALUES (?,?,?,?,?,?)",
        (season, game_number, home_team, away_team, home_score, away_score),
    )
    game_id = cur.lastrowid

    col_list = ", ".join(STAT_COLS)
    placeholders = ", ".join(["?"] * len(STAT_COLS))
    for row in player_rows:
        cur.execute(
            f"INSERT INTO player_game_stats (game_id, player_name, team, min, {col_list}, fouled_out) "
            f"VALUES (?,?,?,?,{placeholders},?)",
            (game_id, row["player_name"], row["team"], row.get("min", 0),
             *[row.get(c, 0) for c in STAT_COLS], int(row.get("fouled_out", False))),
        )

    # Team totals are always derived from the player rows just inserted --
    # never passed in separately -- so they cannot drift from each other.
    for team in (home_team, away_team):
        totals = {c: sum(r.get(c, 0) for r in player_rows if r["team"] == team) for c in STAT_COLS}
        cur.execute(
            f"INSERT INTO team_game_stats (game_id, team, {col_list}) "
            f"VALUES (?,?,{placeholders})",
            (game_id, team, *[totals[c] for c in STAT_COLS]),
        )

    conn.commit()
    return game_id


def get_season_player_averages(conn: sqlite3.Connection, player_name: str, season: Optional[str] = None) -> dict:
    """Averages a player's simulated games -- this is the number you diff
    against their real season average to check sim accuracy."""
    q = ("SELECT g.season, COUNT(*), AVG(min), AVG(pts), AVG(reb), AVG(ast), "
         "AVG(stl), AVG(blk), AVG(tov), AVG(pf), SUM(fgm), SUM(fga), SUM(fg3m), SUM(fg3a) "
         "FROM player_game_stats p JOIN games g ON p.game_id = g.game_id "
         "WHERE p.player_name = ?")
    params = [player_name]
    if season:
        q += " AND g.season = ?"
        params.append(season)

    row = conn.execute(q, params).fetchone()
    if row is None or row[1] == 0:
        return {}

    _, n, avg_min, avg_pts, avg_reb, avg_ast, avg_stl, avg_blk, avg_tov, avg_pf, sfgm, sfga, s3m, s3a = row
    return {
        "player": player_name, "games": n, "min": avg_min, "pts": avg_pts,
        "reb": avg_reb, "ast": avg_ast, "stl": avg_stl, "blk": avg_blk,
        "tov": avg_tov, "pf": avg_pf,
        "fg_pct": (sfgm / sfga) if sfga else 0.0,
        "fg3_pct": (s3m / s3a) if s3a else 0.0,
    }


def get_standings(conn: sqlite3.Connection, season: str) -> list:
    """Wins/losses derived straight from stored game results -- not tracked separately."""
    rows = conn.execute(
        "SELECT home_team, away_team, home_score, away_score FROM games WHERE season = ?",
        (season,),
    ).fetchall()

    records: dict = {}
    for home, away, hs, as_ in rows:
        records.setdefault(home, {"W": 0, "L": 0})
        records.setdefault(away, {"W": 0, "L": 0})
        if hs > as_:
            records[home]["W"] += 1
            records[away]["L"] += 1
        else:
            records[away]["W"] += 1
            records[home]["L"] += 1

    standings = [{"team": t, **rec} for t, rec in records.items()]
    standings.sort(key=lambda r: -r["W"])
    return standings
