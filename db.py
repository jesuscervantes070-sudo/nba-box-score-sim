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
from typing import Dict, List, Optional

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


def get_simulated_advanced_stats(conn: sqlite3.Connection, season: str) -> dict:
    """
    Real-formula PIE / TS% / USG% computed from this SEASON'S SIMULATED
    box scores -- built for awards.py's MVP formula, which needs the
    same three numbers whether it's scoring a real season (from
    player_advanced.json) or a simulated one (from here). Nothing here
    is a new simulated INPUT: every piece (PTS, FGM, FGA, FTM, FTA,
    REB, OREB, AST, STL, BLK, TOV, PF, MIN) is already stored per game
    in player_game_stats -- this just applies the NBA's own real,
    public formulas to numbers that already add up.

    PIE is fundamentally a PER-GAME share (each player's raw production
    divided by the combined production of all 20-ish players who played
    in that one game, both teams), so it has to be computed game by
    game and then averaged -- not from season totals like TS%/USG%
    below, which the real stat itself defines as ratios of season sums.
    """
    rows = conn.execute(
        "SELECT p.game_id, p.player_name, p.team, p.min, p.fgm, p.fga, p.fg3m, p.fg3a, "
        "p.ftm, p.fta, p.reb, p.oreb, p.ast, p.stl, p.blk, p.tov, p.pf "
        "FROM player_game_stats p JOIN games g ON p.game_id = g.game_id "
        "WHERE g.season = ?",
        (season,),
    ).fetchall()
    if not rows:
        return {}

    # First pass: group every row by game, so PIE's per-game shared
    # denominator and USG%'s per-game TEAM totals can both be computed
    # before touching any individual player's season totals.
    games: Dict[str, list] = {}
    for r in rows:
        games.setdefault(r[0], []).append(r)

    # Season-long accumulators per player -- gp/mpg and the season SUMS
    # TS%/USG% are real ratios of, plus a running list of this player's
    # own per-game PIE shares and USG% values to average at the end.
    totals: Dict[str, dict] = {}

    for game_id, game_rows in games.items():
        # PIE numerator for every row in this one game, and their sum
        # -- the real formula's shared denominator.
        pie_nums = {}
        for r in game_rows:
            (_, name, team, mins, fgm, fga, fg3m, fg3a, ftm, fta,
             reb, oreb, ast, stl, blk, tov, pf) = r
            dreb = reb - oreb
            pie_nums[name] = (
                (2 * (fgm - fg3m) + 3 * fg3m + ftm)  # PTS
                + fgm + ftm - fga - fta + dreb + 0.5 * oreb
                + ast + stl + 0.5 * blk - pf - tov
            )
        pie_denom = sum(pie_nums.values())

        # Real team totals for THIS game, needed by USG%'s "share of
        # team's plays used while on the floor" formula.
        team_totals: Dict[str, dict] = {}
        for r in game_rows:
            team = r[2]
            t = team_totals.setdefault(team, {"min": 0.0, "fga": 0.0, "fta": 0.0, "tov": 0.0})
            t["min"] += r[3]; t["fga"] += r[5]; t["fta"] += r[9]; t["tov"] += r[15]

        for r in game_rows:
            (_, name, team, mins, fgm, fga, fg3m, fg3a, ftm, fta,
             reb, oreb, ast, stl, blk, tov, pf) = r
            t = totals.setdefault(name, {
                "team": team, "gp": 0, "min_sum": 0.0, "pts_sum": 0.0,
                "fga_sum": 0.0, "fta_sum": 0.0, "pie_shares": [], "usg_values": [],
            })
            t["team"] = team  # keep the MOST RECENT team (matters for a mid-season trade)
            t["gp"] += 1
            t["min_sum"] += mins
            t["pts_sum"] += 2 * (fgm - fg3m) + 3 * fg3m + ftm
            t["fga_sum"] += fga
            t["fta_sum"] += fta
            t["pie_shares"].append(pie_nums[name] / pie_denom if pie_denom else 0.0)

            tm = team_totals[team]
            tm_min_per5 = tm["min"] / 5  # real team minutes are always 5x this game's played minutes
            tm_plays = tm["fga"] + 0.44 * tm["fta"] + tm["tov"]
            if mins and tm_plays:
                # Fractional form (0.336, not 33.6) -- matches
                # player_advanced.json's real usg_pct scale directly,
                # so awards.py never has to know which source a
                # feature dict came from.
                usg = ((fga + 0.44 * fta + tov) * tm_min_per5) / (mins * tm_plays)
                t["usg_values"].append(usg)

    advanced = {}
    for name, t in totals.items():
        gp = t["gp"]
        ts_denom = 2 * (t["fga_sum"] + 0.44 * t["fta_sum"])
        advanced[name] = {
            "team": t["team"],
            "gp": gp,
            "mpg": t["min_sum"] / gp,
            "pie": sum(t["pie_shares"]) / gp,
            "ts_pct": (t["pts_sum"] / ts_denom) if ts_denom else 0.0,
            "usg_pct": (sum(t["usg_values"]) / len(t["usg_values"])) if t["usg_values"] else 0.0,
        }
    return advanced


def get_simulated_team_opp_fg_pct(conn: sqlite3.Connection, season: str) -> Dict[str, float]:
    """
    This SIMULATED season's opponent-FG%-allowed per team -- the exact
    same real stat data_source.fetch_team_defense measures for a REAL
    season (Team.opp_fg_pct: "how well do teams shoot when they play
    against this team," a direct measure of that team's own defense),
    just derived from this run's own simulated box scores instead.
    Built for awards.py's DPOY formula, which needs the same "team
    defense" signal whether scoring a real season (Team.opp_fg_pct,
    already loaded by loader.load_teams) or a simulated one (from here).

    Nothing new is simulated here either -- a game's home/away FGM/FGA
    already add up (see insert_game); this just credits each team with
    its OPPONENT's shooting from every game it played.
    """
    rows = conn.execute(
        "SELECT g.home_team, g.away_team, p.team, p.fgm, p.fga "
        "FROM player_game_stats p JOIN games g ON p.game_id = g.game_id "
        "WHERE g.season = ?",
        (season,),
    ).fetchall()
    if not rows:
        return {}

    opp_totals: Dict[str, dict] = {}
    for home, away, team, fgm, fga in rows:
        # Whichever team this ROW's stats belong to, the OTHER team in
        # that same game is who was playing defense against it.
        defense_team = away if team == home else home
        t = opp_totals.setdefault(defense_team, {"fgm": 0.0, "fga": 0.0})
        t["fgm"] += fgm
        t["fga"] += fga

    return {team: (t["fgm"] / t["fga"] if t["fga"] else 0.0) for team, t in opp_totals.items()}
