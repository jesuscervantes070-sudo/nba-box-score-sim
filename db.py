"""
SQLite storage for simulated games. Every game a season simulation
produces gets written here, so standings and real-vs-simulated
accuracy checks can be computed from real stored history, instead of
re-running an entire season every time they're needed.

Design rule, same as everywhere else in this project: a game's score
and every team-level number are always DERIVED by summing that game's
player rows -- never entered or stored independently. That's what
makes it structurally impossible for a stored total to disagree with
the players it's supposed to be summing.

Important: DNP (0-minute) players are NOT stored here at all -- see
insert_game()'s docstring for why. A player's stored rows are only the
games they actually played, which is what makes their averaged stats
directly comparable to a real "per game" stat (points / games played,
not points / team's total games).
"""
import sqlite3
from pathlib import Path
from typing import List, Optional

from models import Player, ScheduledGame
from game_engine import GameResult
from injuries import InjurySpan

DB_PATH = Path(__file__).parent / "cache" / "season.db"

# Matches Player's own field names exactly, so a player row can be
# unpacked straight into a Player object (or into a Player's fields)
# without a separate name-mapping table.
STAT_COLS = [
    "min", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "reb", "oreb", "ast", "stl", "blk", "tov", "pf",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    season TEXT NOT NULL,
    date TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    -- How many extra 5-minute periods this game needed (0 = decided in
    -- regulation) -- see GameResult.overtime_periods in game_engine.py.
    -- home_score/away_score already have any OT stats folded in; this
    -- is purely a display flag (e.g. main.py showing "F/OT").
    overtime_periods INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS player_game_stats (
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT NOT NULL,
    {', '.join(f'{c} REAL' for c in STAT_COLS)},
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS injuries (
    season TEXT NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT NOT NULL,
    start_game_id TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_game_id TEXT NOT NULL,
    end_date TEXT NOT NULL,
    games_missed INTEGER NOT NULL,
    -- Whether this span's last missed game was the team's literal last
    -- regular-season game -- see injuries.InjurySpan.still_out_at_season_end.
    still_out_at_season_end INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pgs_game ON player_game_stats(game_id);
CREATE INDEX IF NOT EXISTS idx_pgs_player ON player_game_stats(player_name);
CREATE INDEX IF NOT EXISTS idx_games_season ON games(season);
CREATE INDEX IF NOT EXISTS idx_injuries_season ON injuries(season);
"""


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Creates the database file and tables if they don't exist yet,
    and returns an open connection to it."""
    path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_game(conn: sqlite3.Connection, season: str, scheduled_game: ScheduledGame, result: GameResult) -> None:
    """
    Writes one simulated game -- the game record itself, plus every
    player's stat line. home_score/away_score are read directly from
    `result` (which computes them by summing its own player rows --
    see GameResult.home_score in game_engine.py), never recomputed or
    entered separately here, so they can never disagree with the
    player rows being stored in the same call.

    DNP (0-minute) players are skipped entirely -- a player who didn't
    play produced no real stat line, and storing a 0-stat row for them
    would silently drag down their averaged stats later (a season
    average is supposed to be "per game PLAYED", not "per team game").
    """
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO games (game_id, season, date, home_team, away_team, home_score, away_score, "
        "overtime_periods) VALUES (?,?,?,?,?,?,?,?)",
        (scheduled_game.game_id, season, scheduled_game.date,
         result.home_team, result.away_team, result.home_score, result.away_score, result.overtime_periods),
    )

    col_list = ", ".join(STAT_COLS)
    placeholders = ", ".join(["?"] * len(STAT_COLS))
    for team_name, players in [(result.home_team, result.home_players), (result.away_team, result.away_players)]:
        for p in players:
            if p.min == 0:
                continue  # DNP -- see docstring above
            cur.execute(
                f"INSERT INTO player_game_stats (game_id, player_name, team, {col_list}) "
                f"VALUES (?,?,?,{placeholders})",
                (scheduled_game.game_id, p.name, team_name, *[getattr(p, c) for c in STAT_COLS]),
            )

    conn.commit()


def insert_injuries(conn: sqlite3.Connection, season: str, schedule_by_id: dict, spans: List[InjurySpan]) -> None:
    """
    Stores one row per simulated injury span (see injuries.py) -- who,
    which team, and the real start/end game + date. Computed all at once
    up front (unlike games, which stream in one at a time as they're
    simulated), so this is called once per season run rather than
    growing incrementally.
    """
    cur = conn.cursor()
    for span in spans:
        # span.game_ids is already this team's real chronological order
        # (see injuries.build_season_injuries), so first/last are the
        # real start/end.
        start_game = schedule_by_id[span.game_ids[0]]
        end_game = schedule_by_id[span.game_ids[-1]]
        cur.execute(
            "INSERT INTO injuries (season, player_name, team, start_game_id, start_date, "
            "end_game_id, end_date, games_missed, still_out_at_season_end) VALUES (?,?,?,?,?,?,?,?,?)",
            (season, span.player_name, span.team, start_game.game_id, start_game.date,
             end_game.game_id, end_game.date, len(span.game_ids), int(span.still_out_at_season_end)),
        )
    conn.commit()


def get_injuries(conn: sqlite3.Connection, season: str) -> list:
    """
    Every simulated injury for a season, longest first -- the ones most
    worth knowing about first when browsing.
    """
    rows = conn.execute(
        "SELECT player_name, team, start_date, end_date, games_missed, still_out_at_season_end "
        "FROM injuries WHERE season = ? ORDER BY games_missed DESC",
        (season,),
    ).fetchall()
    return [
        {"player": r[0], "team": r[1], "start_date": r[2], "end_date": r[3],
         "games_missed": r[4], "still_out_at_season_end": bool(r[5])}
        for r in rows
    ]


def get_standings(conn: sqlite3.Connection, season: str) -> list:
    """
    Wins/losses derived straight from stored game results -- never
    tracked as a separate running counter, so a team's record can't
    ever drift out of sync with its actual game-by-game results.
    """
    rows = conn.execute(
        "SELECT home_team, away_team, home_score, away_score FROM games WHERE season = ?",
        (season,),
    ).fetchall()

    records: dict = {}
    for home, away, home_score, away_score in rows:
        records.setdefault(home, {"W": 0, "L": 0})
        records.setdefault(away, {"W": 0, "L": 0})
        if home_score > away_score:
            records[home]["W"] += 1
            records[away]["L"] += 1
        else:
            records[away]["W"] += 1
            records[home]["L"] += 1

    standings = [{"team": t, **rec} for t, rec in records.items()]
    standings.sort(key=lambda r: -r["W"])
    return standings


def get_team_games(conn: sqlite3.Connection, season: str, team: str) -> list:
    """
    One team's real per-game results this season, as (opponent, my_score,
    opp_score) tuples -- the shared building block for every playoff
    tiebreaker in playoffs.py (head-to-head, division/conference record,
    point differential, record-vs-playoff-pool are all just filtering or
    summing this same list), instead of five near-duplicate queries.
    """
    rows = conn.execute(
        "SELECT home_team, away_team, home_score, away_score FROM games "
        "WHERE season = ? AND (home_team = ? OR away_team = ?)",
        (season, team, team),
    ).fetchall()

    games = []
    for home, away, home_score, away_score in rows:
        if home == team:
            games.append((away, home_score, away_score))
        else:
            games.append((home, away_score, home_score))
    return games


def get_team_game_log(conn: sqlite3.Connection, season: str, team: str) -> list:
    """
    One team's stored games this season in real chronological order --
    game_id, date, opponent, home/away, and the final score. Built for
    main.py's game-by-game replay: unlike get_team_games (which just
    hands playoffs.py's tiebreakers an unordered bag of (opponent,
    mine, theirs) tuples), this needs to walk a team's actual schedule
    in order, one game at a time.
    """
    rows = conn.execute(
        "SELECT game_id, date, home_team, away_team, home_score, away_score, overtime_periods "
        "FROM games WHERE season = ? AND (home_team = ? OR away_team = ?) "
        "ORDER BY date, game_id",
        (season, team, team),
    ).fetchall()

    log = []
    for game_id, date, home, away, home_score, away_score, overtime_periods in rows:
        is_home = home == team
        log.append({
            "game_id": game_id,
            "date": date,
            "opponent": away if is_home else home,
            "is_home": is_home,
            "my_score": home_score if is_home else away_score,
            "opp_score": away_score if is_home else home_score,
            "overtime_periods": overtime_periods,
        })
    return log


def get_game_box_score(conn: sqlite3.Connection, game_id: str) -> Optional[GameResult]:
    """
    Rebuilds one already-stored game's full box score as a real
    GameResult, straight from its stored player rows -- so it can be
    handed to main.py's print_box_score() unchanged (the same function
    a freshly-simulated single game already uses), instead of that
    function needing a second, parallel "print a game from the
    database" version. Nothing is re-simulated here: GameResult.
    home_score/away_score still get computed by summing these same
    Player rows (see game_engine.GameResult), so a replayed score can
    never disagree with the box score it's replayed alongside.
    """
    game_row = conn.execute(
        "SELECT home_team, away_team, overtime_periods FROM games WHERE game_id = ?", (game_id,)
    ).fetchone()
    if not game_row:
        return None
    home_team, away_team, overtime_periods = game_row

    col_list = ", ".join(STAT_COLS)
    rows = conn.execute(
        f"SELECT player_name, team, {col_list} FROM player_game_stats WHERE game_id = ?",
        (game_id,),
    ).fetchall()

    home_players, away_players = [], []
    for player_name, team, *stat_values in rows:
        player = Player(name=player_name, team=team, **dict(zip(STAT_COLS, stat_values)))
        (home_players if team == home_team else away_players).append(player)

    return GameResult(home_team=home_team, away_team=away_team,
                       home_players=home_players, away_players=away_players,
                       overtime_periods=overtime_periods)


def get_player_season_averages(conn: sqlite3.Connection, player_name: str, season: Optional[str] = None) -> dict:
    """
    Averages a player's SIMULATED games this season (only games they
    actually played -- see insert_game) -- this is the number to diff
    against their real season average to check how accurate the sim is.
    """
    q = (
        f"SELECT COUNT(*), {', '.join(f'AVG({c})' for c in STAT_COLS)}, "
        f"SUM(fgm), SUM(fga), SUM(fg3m), SUM(fg3a), SUM(ftm), SUM(fta) "
        "FROM player_game_stats p JOIN games g ON p.game_id = g.game_id "
        "WHERE p.player_name = ?"
    )
    params: List = [player_name]
    if season:
        q += " AND g.season = ?"
        params.append(season)

    row = conn.execute(q, params).fetchone()
    games_played = row[0]
    if not games_played:
        return {}

    averages = dict(zip(STAT_COLS, row[1:1 + len(STAT_COLS)]))
    sfgm, sfga, sfg3m, sfg3a, sftm, sfta = row[1 + len(STAT_COLS):]

    return {
        "player": player_name,
        "games_played": games_played,
        **averages,
        # PTS/percentages computed from the summed makes/attempts, not
        # averaged directly -- same "derive, don't duplicate" rule as
        # the Player class itself.
        "pts": (2 * (sfgm - sfg3m) + 3 * sfg3m + sftm) / games_played,
        "fg_pct": (sfgm / sfga) if sfga else 0.0,
        "fg3_pct": (sfg3m / sfg3a) if sfg3a else 0.0,
        "ft_pct": (sftm / sfta) if sfta else 0.0,
    }
