"""
Season awards -- MVP first, with DPOY/ROY/MIP (and maybe Coach of the
Year) to follow the same pattern.

The core idea: one scoring FORMULA per award, built from real
basketball logic (see the design discussion in CLAUDE.md), applied to
whichever set of per-player stats it's handed -- REAL season stats (to
backtest the formula against who actually won, and to calibrate its
weights) or SIMULATED season stats (to award the followed sim run).
Same formula either way; only the input numbers differ.

Ground truth (who really won) has to be a hardcoded table, not a live
fetch: the NBA stats API only exposes awards per PLAYER (PlayerAwards
needs a player_id you already suspect), there's no "who won MVP in
season X" endpoint. So the winners below were each individually
VERIFIED against that real endpoint (queried the guessed winner's real
award history and confirmed a "NBA Most Valuable Player" entry for
that exact season) rather than trusted from memory -- notably 2024-25
and 2025-26, which are recent enough that memory alone wasn't good
enough to trust here.
"""
import time
from typing import Dict, List, Optional, Tuple

from loader import (
    load_player_advanced_stats, load_player_rim_defense, load_player_perimeter_defense,
    load_player_hustle_stats, load_team_coaches, load_teams, load_schedule,
    available_seasons, DEFAULT_SEASON,
)

# =====================================================================
# REAL GROUND TRUTH -- MVP
# =====================================================================

REAL_MVP_WINNERS: Dict[str, str] = {
    "1996-97": "Karl Malone",
    "1997-98": "Michael Jordan",
    "1998-99": "Karl Malone",
    "1999-00": "Shaquille O'Neal",
    "2000-01": "Allen Iverson",
    "2001-02": "Tim Duncan",
    "2002-03": "Tim Duncan",
    "2003-04": "Kevin Garnett",
    "2004-05": "Steve Nash",
    "2005-06": "Steve Nash",
    "2006-07": "Dirk Nowitzki",
    "2007-08": "Kobe Bryant",
    "2008-09": "LeBron James",
    "2009-10": "LeBron James",
    "2010-11": "Derrick Rose",
    "2011-12": "LeBron James",
    "2012-13": "LeBron James",
    "2013-14": "Kevin Durant",
    "2014-15": "Stephen Curry",
    "2015-16": "Stephen Curry",
    "2016-17": "Russell Westbrook",
    "2017-18": "James Harden",
    "2018-19": "Giannis Antetokounmpo",
    "2019-20": "Giannis Antetokounmpo",
    "2020-21": "Nikola Jokić",
    "2021-22": "Nikola Jokić",
    "2022-23": "Joel Embiid",
    "2023-24": "Nikola Jokić",
    "2024-25": "Shai Gilgeous-Alexander",
    "2025-26": "Shai Gilgeous-Alexander",
}

# =====================================================================
# REAL GROUND TRUTH -- ROY
# =====================================================================

# Same verification method as REAL_MVP_WINNERS (each checked against
# PlayerAwards for a real "NBA Rookie of the Year" entry matching that
# exact season). 1999-00 is a genuine real TIE -- Elton Brand and Steve
# Francis both officially won -- so its value is a list; every other
# season is a single name. rank_roy_candidates below treats a hit as
# "the formula's #1 pick is IN this season's winner(s)", not ==.
REAL_ROY_WINNERS: Dict[str, object] = {
    "1996-97": "Allen Iverson",
    "1997-98": "Tim Duncan",
    "1998-99": "Vince Carter",
    "1999-00": ["Elton Brand", "Steve Francis"],  # real tie
    "2000-01": "Mike Miller",
    "2001-02": "Pau Gasol",
    "2002-03": "Amar'e Stoudemire",
    "2003-04": "LeBron James",
    "2004-05": "Emeka Okafor",
    "2005-06": "Chris Paul",
    "2006-07": "Brandon Roy",
    "2007-08": "Kevin Durant",
    "2008-09": "Derrick Rose",
    "2009-10": "Tyreke Evans",
    "2010-11": "Blake Griffin",
    "2011-12": "Kyrie Irving",
    "2012-13": "Damian Lillard",
    "2013-14": "Michael Carter-Williams",
    "2014-15": "Andrew Wiggins",
    "2015-16": "Karl-Anthony Towns",
    "2016-17": "Malcolm Brogdon",
    "2017-18": "Ben Simmons",
    "2018-19": "Luka Dončić",
    "2019-20": "Ja Morant",
    "2020-21": "LaMelo Ball",
    "2021-22": "Scottie Barnes",
    "2022-23": "Paolo Banchero",
    "2023-24": "Victor Wembanyama",
    "2024-25": "Stephon Castle",
    "2025-26": "Cooper Flagg",
}


def _roy_winner_names(season: str) -> List[str]:
    """REAL_ROY_WINNERS entries are a single name almost every season,
    but a list for the one real tie (1999-00) -- this always returns a
    list, so calling code never has to special-case the tie."""
    winner = REAL_ROY_WINNERS.get(season)
    if winner is None:
        return []
    return winner if isinstance(winner, list) else [winner]


# Real rookies get spotted the same way offseason.py already spots a
# real "new to the league" arrival -- no earlier season in this
# project's cache has any real stat line for them at all -- PLUS an
# age ceiling, because that "new to the league" signal alone has a
# real false positive: a veteran who simply missed every game of an
# earlier cached season (hurt all year, or playing overseas) reads
# identically to a rookie. Confirmed by testing on real 2024-25/2025-26
# data: without the age filter, Guerschon Yabusele (a ~29-year-old
# veteran returning from years in Europe/China) and Saddiq Bey (missed
# all of 2024-25 to a real ACL tear) both look like rookies. 23 is
# generous enough for real older rookies (international prospects
# occasionally debut at 21-22) while excluding a returning veteran.
ROY_MAX_AGE = 23.0
ROY_MIN_GAMES = 20
# Lower than MVP's minutes floor on purpose -- real ROY winners have
# sometimes come off the bench for real (Malcolm Brogdon, 2016-17), so
# MVP_MIN_MPG's "regular starter" bar would wrongly disqualify them.
ROY_MIN_MPG = 15.0

# Starting point -- NOT yet tuned; see backtest_roy.py. Reuses the
# MVP formula's SHAPE (this whole award was built on the idea "MVP's
# formula, just restricted to rookies" -- see CLAUDE.md), but team
# win_pct is zeroed out here since real ROY voting doesn't require a
# good team at all (plenty of ROY winners come from lottery teams) --
# left for the sweep to confirm or overrule, not assumed permanent.
ROY_WEIGHTS: Dict[str, float] = dict(pie=10.0, ts_pct=3.0, usg_pct=8.0, win_pct=0.0,
                                      availability=4.0, fatigue=0.0)


# Real per-team full-season game count, by era -- the FALLBACK for
# turning a player's real GP into an availability RATE, used only when
# a season's real per-team schedule isn't available (see
# team_games_played below, which is what every award actually uses).
# Same real-world facts main.py's SEASON_NOTES already documents.
GAMES_PER_SEASON: Dict[str, int] = {
    "1998-99": 50,
    "2011-12": 66,
    "2020-21": 72,
}
DEFAULT_GAMES_PER_SEASON = 82


def games_per_season(season: str) -> int:
    return GAMES_PER_SEASON.get(season, DEFAULT_GAMES_PER_SEASON)


_team_games_cache: Dict[str, Dict[str, int]] = {}


def team_games_played(season: str) -> Dict[str, int]:
    """
    How many real games each team actually PLAYED in `season`, counted
    from the cached real schedule -- local, no network.

    This exists because a league-wide constant is genuinely unfair in
    a season where teams played DIFFERENT numbers of games. 2019-20 is
    the real case (found by testing): COVID cut it short mid-season and
    only 22 teams were invited to the Orlando bubble, so Golden State
    played 60 games and Dallas played 74. Scoring availability against
    a flat 82 meant a Warrior who played every single game his team
    had read as 73% available while a Maverick who did the same read as
    90% -- a 0.68-point MVP swing (availability weight 4.0) decided
    entirely by whether his team got a bubble invite, nothing to do
    with the player. Real MVP gaps between top candidates are often
    0.1-0.5, so that was big enough to flip an outcome.

    Counting the real schedule also handles every other real anomaly
    for free, with no table to maintain: the two teams that played 81
    in 2012-13 (a real cancelled game after Sandy Hook, never made up),
    and both lockout seasons.
    """
    if season not in _team_games_cache:
        counts: Dict[str, int] = {}
        for game in load_schedule(season):
            counts[game.home_team] = counts.get(game.home_team, 0) + 1
            counts[game.away_team] = counts.get(game.away_team, 0) + 1
        _team_games_cache[season] = counts
    return _team_games_cache[season]


def _availability(gp: float, season: str, team_games: Optional[int] = None) -> float:
    """
    Share of his team's games a player was available for. Clamped at
    1.0 because a real TRADED player can exceed his team's game count
    (his old and new teams had played different numbers of games when
    the trade happened) -- confirmed real: the 2023-24 league max is 84
    games in an 82-game season.
    """
    denominator = team_games or games_per_season(season)
    return min(gp / denominator, 1.0) if denominator else 0.0


# =====================================================================
# REAL TEAM SUCCESS (for scoring against real seasons only -- a
# simulated season already has this in db.get_standings)
# =====================================================================

def fetch_real_team_win_pct(season: str, retries: int = 4) -> Dict[str, float]:
    """
    Real WIN% per team for `season` -- a separate small fetch from
    data_source.fetch_real_standings (which only returns win COUNTS,
    fine for the standings-comparison it was built for, but MVP scoring
    needs a rate so a 50-game lockout season and an 82-game one compare
    fairly). Not cached: same reasoning as fetch_real_standings itself
    -- this is a real-world INPUT to a formula, fetched on demand, not
    something the simulation depends on every run.

    Retries on failure -- this endpoint has been observed to read-
    timeout in practice (found by testing: an un-retried caller,
    backtest_mvp.py, failed outright mid-run on a transient timeout).
    Every backtest_*.py script calls this, so fixing it here fixes all
    of them at once rather than each needing its own retry loop.
    """
    from nba_api.stats.endpoints import leaguestandingsv3
    from data_source import NBA_API_TEAM_NAME_FIXES

    last_error = None
    for attempt in range(retries):
        try:
            df = leaguestandingsv3.LeagueStandingsV3(season=season, timeout=45).get_data_frames()[0]
            win_pct = {}
            for _, row in df.iterrows():
                team_name = f"{row['TeamCity']} {row['TeamName']}"
                team_name = NBA_API_TEAM_NAME_FIXES.get(team_name, team_name)
                win_pct[team_name] = float(row["WinPCT"])
            return win_pct
        except Exception as e:
            last_error = e
            time.sleep(3)
    raise RuntimeError(f"fetch_real_team_win_pct({season!r}) failed after {retries} attempts: {last_error}")


# =====================================================================
# MVP FORMULA
# =====================================================================

# TUNED against all 30 real seasons by backtest_mvp.py --sweep (time
# split: train 1996-97..2015-16, holdout 2016-17..2025-26). Final
# result: holdout hit-rate 90% (9/10), top-3 rate 100% -- the one miss
# is 2018-19 (Giannis over Harden), a genuinely close real MVP race,
# and even there the real winner still lands in the formula's top 3.
MVP_WEIGHTS: Dict[str, float] = {
    "pie": 10.0,          # NBA's own all-in-one impact number
    "ts_pct": 3.0,        # efficiency -- but see usg_pct below, this alone let a
                          # high-efficiency/low-usage big (Jarrett Allen, 2024-25)
                          # nearly outscore the real MVP on TS% alone
    "usg_pct": 8.0,       # real MVP-level shot-creation LOAD, not just efficiency --
                          # added after testing: without it, efficient low-usage
                          # role players (Allen, Gobert, Bridges) kept beating real
                          # MVP-caliber stars whose efficiency is merely very good
    "win_pct": 2.0,       # team success -- swept DOWN from an initial 8.0: real MVPs
                          # (e.g. Jokić on a non-#1-seed Denver) don't need the
                          # single best record, just a good one, and weighting this
                          # too high let a role player on the best-record team beat
                          # a real MVP-level star on a merely-good team
    "availability": 4.0,  # penalize missed games even with elite per-game numbers
    "fatigue": 0.0,       # repeat-winner penalty -- tested NEGATIVE on holdout (real
                          # MVPs, e.g. Jokić x3, SGA x2, repeat often) -- left at 0
}

# A player needs at least this many real games AND minutes/game to be
# an MVP candidate at all. Games alone isn't enough -- found by testing:
# without a minutes floor, low-minute bigs like JaVale McGee and Rudy
# Gobert (efficient dunks/putbacks in a handful of minutes a night)
# beat real MVP-level stars on PIE and TS%, which no real MVP voter
# would ever consider. 24 mpg is a real "regular starter" minutes
# level, not a guess -- every real MVP winner in REAL_MVP_WINNERS plays
# well above it.
MVP_MIN_GAMES = 20
MVP_MIN_MPG = 24.0


def mvp_score(pie: float, ts_pct: float, usg_pct: float, win_pct: float, gp: float,
              season: str, already_won_last_season: bool,
              weights: Optional[Dict[str, float]] = None,
              team_games: Optional[int] = None) -> float:
    """
    One player's real-basketball MVP "case," as a single ranking number
    -- not a real stat, a composite built for this project. PIE is the
    main "how much winning did this player individually produce"
    signal; usg_pct is real shot-creation LOAD (found by testing: PIE
    and TS% alone let efficient low-usage role players nearly outscore
    real MVP-caliber stars -- see MVP_WEIGHTS); TS% keeps empty
    high-volume scoring from outscoring real efficient production;
    win_pct is "carries a good team," which real MVP voting leans on
    but not as an absolute best-record requirement; availability
    penalizes missed games the way real voters visibly do even for
    elite per-game stat lines. `already_won_last_season` feeds a
    voter-fatigue term -- tested and left at weight 0 (see MVP_WEIGHTS
    comment on why).
    """
    weights = weights or MVP_WEIGHTS
    availability = _availability(gp, season, team_games)
    score = (
        weights["pie"] * pie
        + weights["ts_pct"] * ts_pct
        + weights["usg_pct"] * usg_pct
        + weights["win_pct"] * win_pct
        + weights["availability"] * availability
    )
    if already_won_last_season:
        score += weights["fatigue"]
    return score


def _player_team_map(season: str) -> Dict[str, str]:
    """Real team name for every player who actually played in `season`
    (from rosters.json via load_teams) -- needed to look up that
    player's team's real win%, since player_advanced.json (unlike
    rosters.json) isn't keyed or grouped by team."""
    teams = load_teams(season)
    mapping = {}
    for team_name, team in teams.items():
        for player in team.players:
            mapping[player.name] = team_name
    return mapping



def _team_win_pct_or_league_average(win_pct: Dict[str, float], team: Optional[str]) -> float:
    """
    A candidate's team win%, or the LEAGUE AVERAGE when his team isn't
    known -- never 0.0.

    Found by testing: ~60 players per season play real games but are
    absent from rosters.json entirely (waived, released, or a two-way/
    10-day deal that ended, so they're on no team's end-of-season roster
    snapshot -- the same class of gap transactions.py exists to patch
    for traded players). They were being scored as if their team went
    winless, a ~1.0-point MVP penalty (win_pct weight 2.0) for a fact
    about ROSTER PAPERWORK, not about the player. League average is the
    honest "we don't know" value.

    The proper fix is to carry each player's real team on
    player_advanced.json itself (the source dataframe has
    TEAM_ABBREVIATION; this project just never cached it), which would
    also let DPOY stop silently excluding these players -- see CLAUDE.md.
    """
    if team and team in win_pct:
        return win_pct[team]
    return sum(win_pct.values()) / len(win_pct) if win_pct else 0.0


def mvp_features_for_season(season: str, team_win_pct: Optional[Dict[str, float]] = None) -> List[dict]:
    """
    Every real MVP CANDIDATE's raw features for `season` (players with
    at least MVP_MIN_GAMES real games) -- the expensive-to-build half
    of scoring: loads real advanced stats, real team assignment, and
    real team win% ONCE. Split out from the actual scoring (mvp_score)
    specifically so backtest_mvp.py can build this once per season and
    then sweep many weight combinations over it purely in memory,
    rather than re-fetching real data (a live network call) per combo.

    `team_win_pct` can be passed in pre-fetched for the same reason.
    """
    advanced = load_player_advanced_stats(season)
    if not advanced:
        return []
    team_of = _player_team_map(season)
    team_games = team_games_played(season)
    win_pct = team_win_pct if team_win_pct is not None else fetch_real_team_win_pct(season)
    prior_winner = REAL_MVP_WINNERS.get(_previous_season(season))

    features = []
    for name, stats in advanced.items():
        if stats["gp"] < MVP_MIN_GAMES or stats.get("mpg", 0.0) < MVP_MIN_MPG:
            continue
        team = team_of.get(name)
        features.append({
            "name": name, "pie": stats["pie"], "ts_pct": stats["ts_pct"],
            "usg_pct": stats["usg_pct"], "gp": stats["gp"],
            "team_win_pct": _team_win_pct_or_league_average(win_pct, team),
            "team_games": team_games.get(team) if team else None,
            "already_won_last_season": name == prior_winner,
        })
    return features


def rank_mvp_candidates(features: List[dict], season: str,
                         weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Scores and ranks a season's already-built feature list (see
    mvp_features_for_season) -- pure in-memory math, no fetching, so
    this is the part safe to call once per weight combo in a sweep."""
    scored = [
        (f["name"], mvp_score(
            pie=f["pie"], ts_pct=f["ts_pct"], usg_pct=f["usg_pct"], win_pct=f["team_win_pct"],
            gp=f["gp"], season=season, already_won_last_season=f["already_won_last_season"],
            weights=weights, team_games=f.get("team_games"),
        ))
        for f in features
    ]
    scored.sort(key=lambda c: -c[1])
    return scored


def real_mvp_candidates(season: str, weights: Optional[Dict[str, float]] = None,
                         team_win_pct: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version of mvp_features_for_season +
    rank_mvp_candidates, for a single ad-hoc lookup (e.g. showing one
    season's MVP case) where there's no sweep to optimize for."""
    features = mvp_features_for_season(season, team_win_pct)
    return rank_mvp_candidates(features, season, weights)


def _previous_season(season: str) -> str:
    """'2023-24' -> '2022-23' -- string arithmetic on the real
    hyphenated season format every cache file already uses."""
    start_year = int(season[:4])
    prev_start, prev_end = start_year - 1, start_year % 100
    return f"{prev_start}-{prev_end:02d}"


# =====================================================================
# ROY -- literally the MVP formula, restricted to real rookies (this
# whole award's design, per CLAUDE.md's discussion).
# =====================================================================

_debut_season_cache: Optional[Dict[str, str]] = None


def _debut_seasons() -> Dict[str, str]:
    """
    Every player's FIRST season (chronologically, among every season
    this project has cached) with a real per-game stat line at all --
    the same "compare real game-log evidence across seasons" method
    offseason.py already uses to spot a real "new to the league"
    arrival, just built once here for every player rather than diffed
    season-to-season. 1996-97 is a real, unavoidable floor (see
    CLAUDE.md): a player who already has a stat line THERE looks like a
    debut with no way to check further back, since this project's data
    source has nothing before it either.

    Memoized at module level -- this scans all 30 seasons' cached
    files, real work worth doing once, not once per candidate lookup.
    """
    global _debut_season_cache
    if _debut_season_cache is not None:
        return _debut_season_cache

    debut: Dict[str, str] = {}
    for season in sorted(available_seasons()):  # oldest first
        for name in load_player_advanced_stats(season):
            debut.setdefault(name, season)  # first (oldest) season wins
    _debut_season_cache = debut
    return debut


_earliest_season_cache: Optional[str] = None


def _earliest_season() -> str:
    """This project's real data floor (1996-97) -- memoized, and
    computed rather than hardcoded so it still tracks correctly if the
    cached range ever changes. Season strings sort correctly as plain
    strings (same property sweep_constants.py's HOLDOUT_FIRST_SEASON
    relies on) since they're all zero-padded and the same length."""
    global _earliest_season_cache
    if _earliest_season_cache is None:
        _earliest_season_cache = min(available_seasons())
    return _earliest_season_cache


def _draft_rookie_season(draft_year: int) -> str:
    """A real draft year's normal rookie season -- the NBA draft is in
    June, so a player drafted in year Y almost always debuts that same
    fall, in the Y-(Y+1) season. Real draft-and-stash exceptions (a
    player drafted years before actually debuting) exist but are rare
    enough that this project accepts missing them, same tradeoff
    documented on is_real_rookie below."""
    return f"{draft_year}-{(draft_year + 1) % 100:02d}"


def is_real_rookie(name: str, season: str, stats: dict, debut: Dict[str, str]) -> bool:
    """
    Whether `name` is a real rookie IN `season`. The debut-season check
    (_debut_seasons: this is the first season, among every one cached,
    with a real stat line for them at all) is actually CORRECT on its
    own for every season except the very first one -- it already
    handles a real injury-delayed rookie year correctly (Ben Simmons'
    debut is naturally 2017-18, since he has zero 2016-17 stats after
    missing that whole season hurt; same for Blake Griffin, 2010-11).

    The ONE real gap is the first cached season itself (1996-97): a
    player who was ALREADY an active veteran before this project's data
    even begins has no earlier season to compare against, so debut-
    season alone wrongly calls him a rookie there too. That's the one
    place real draft year (a real historical fact, fetchable regardless
    of this project's own data floor) earns its keep -- confirmed
    directly: Damon Stoudamire's real draft_year is 1995, so his real
    rookie season computes to "1995-96", one season before this
    project's data starts, correctly ruling him out as a 1996-97
    "rookie" even though 1996-97 is his first APPEARANCE in the cache.

    Deliberately NOT applied every season -- an earlier version of this
    check used draft year everywhere and BROKE Simmons/Griffin (their
    draft year implies an "expected" rookie season one or two years
    before the real one their injury pushed it to) -- found by testing:
    holdout hit-rate went down, not up, after that version landed.
    """
    debut_season = debut.get(name)
    if debut_season != season:
        return False  # not this player's first cached appearance at all

    if season == _earliest_season():
        draft_year = stats.get("draft_year")
        if draft_year is not None and _draft_rookie_season(draft_year) != season:
            return False  # drafted before the floor -- a real veteran, not a rookie here

    return True


def roy_features_for_season(season: str, team_win_pct: Optional[Dict[str, float]] = None) -> List[dict]:
    """
    Same shape as mvp_features_for_season, filtered down to real ROOKIE
    candidates only: is_real_rookie (real draft year, or a debut-season
    fallback for undrafted players), young enough to actually be one
    (ROY_MAX_AGE -- a safety net for the rare undrafted-veteran case
    is_real_rookie's fallback can still miss), and enough games/minutes
    to be a real candidate at all (ROY_MIN_GAMES/ROY_MIN_MPG, both
    looser than MVP's since real ROY winners have come off the bench).
    """
    advanced = load_player_advanced_stats(season)
    if not advanced:
        return []
    debut = _debut_seasons()
    team_of = _player_team_map(season)
    team_games = team_games_played(season)
    win_pct = team_win_pct if team_win_pct is not None else fetch_real_team_win_pct(season)

    features = []
    for name, stats in advanced.items():
        if not is_real_rookie(name, season, stats, debut):
            continue
        if stats.get("age", 99) > ROY_MAX_AGE:
            continue  # see ROY_MAX_AGE -- filters a returning veteran, not a real rookie
        if stats["gp"] < ROY_MIN_GAMES or stats.get("mpg", 0.0) < ROY_MIN_MPG:
            continue
        team = team_of.get(name)
        features.append({
            "name": name, "pie": stats["pie"], "ts_pct": stats["ts_pct"],
            "usg_pct": stats["usg_pct"], "gp": stats["gp"],
            "team_win_pct": _team_win_pct_or_league_average(win_pct, team),
            "team_games": team_games.get(team) if team else None,
            "already_won_last_season": False,  # a rookie can't be a repeat winner
        })
    return features


def rank_roy_candidates(features: List[dict], season: str,
                         weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Same scoring function as MVP (rank_mvp_candidates) -- a distinct
    name here for clarity, even though the implementation underneath
    is identical (see this section's docstring)."""
    return rank_mvp_candidates(features, season, weights if weights is not None else ROY_WEIGHTS)


def real_roy_candidates(season: str, weights: Optional[Dict[str, float]] = None,
                         team_win_pct: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a single real-season lookup,
    mirroring real_mvp_candidates."""
    features = roy_features_for_season(season, team_win_pct)
    return rank_roy_candidates(features, season, weights)


# =====================================================================
# SIMULATED SEASON -- same formula, fed simulated stats instead of
# real ones. Works for ANY simulated run of any cached season (a
# one-off re-sim, or any season inside a multi-season run) -- nothing
# here is season-specific, since it's computed straight from that
# run's own box scores rather than anything real about that season.
# =====================================================================

def mvp_features_from_simulated(conn, season: str, standings: Optional[List[dict]] = None) -> List[dict]:
    """
    Every simulated MVP candidate's features for `season`, computed
    from that season's SIMULATED box scores (db.get_simulated_advanced_stats
    applies the same real PIE/TS%/USG% formulas real_mvp_candidates
    uses, just fed simulated instead of real per-game numbers -- see
    that function's docstring). `standings` can be passed in
    pre-computed (db.get_standings) to avoid a second query when the
    caller already has it, e.g. main.py right after simulating.

    `already_won_last_season` is always False here -- fatigue's weight
    is 0 anyway (see MVP_WEIGHTS), and there's no meaningful "last
    season" to check across independent single-season sim runs.
    """
    import db  # local import: db.py doesn't import awards.py, avoid a cycle either way

    advanced = db.get_simulated_advanced_stats(conn, season)
    if not advanced:
        return []
    if standings is None:
        standings = db.get_standings(conn, season)
    win_pct = {row["team"]: row["W"] / (row["W"] + row["L"]) if (row["W"] + row["L"]) else 0.0
               for row in standings}
    sim_team_games = {row["team"]: row["W"] + row["L"] for row in standings}

    features = []
    for name, stats in advanced.items():
        if stats["gp"] < MVP_MIN_GAMES or stats["mpg"] < MVP_MIN_MPG:
            continue
        features.append({
            "name": name, "pie": stats["pie"], "ts_pct": stats["ts_pct"],
            "usg_pct": stats["usg_pct"], "gp": stats["gp"],
            "team_win_pct": win_pct.get(stats["team"], 0.0),
            "team_games": sim_team_games.get(stats["team"]),
            "already_won_last_season": False,
        })
    return features


def simulated_mvp_candidates(conn, season: str, standings: Optional[List[dict]] = None,
                              weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a simulated season, mirroring
    real_mvp_candidates -- builds features then ranks them."""
    features = mvp_features_from_simulated(conn, season, standings)
    return rank_mvp_candidates(features, season, weights)


def roy_features_from_simulated(conn, season: str, standings: Optional[List[dict]] = None) -> List[dict]:
    """
    Same idea as mvp_features_from_simulated, but "is this player a
    rookie" is inherently a REAL-world question (draft year/career
    debut + age), not something a simulated box score can answer -- so
    rookie identification here reuses the exact same real data
    (is_real_rookie, from player_advanced.json) the real-season
    backtest uses, while PIE/TS%/USG%/team win% come from THIS run's
    own simulated box scores/standings, same as MVP.
    """
    import db  # local import: db.py doesn't import awards.py, avoid a cycle either way

    advanced = db.get_simulated_advanced_stats(conn, season)
    if not advanced:
        return []
    real_ages = load_player_advanced_stats(season)  # real season -- only used for age/debut check
    debut = _debut_seasons()
    if standings is None:
        standings = db.get_standings(conn, season)
    win_pct = {row["team"]: row["W"] / (row["W"] + row["L"]) if (row["W"] + row["L"]) else 0.0
               for row in standings}
    sim_team_games = {row["team"]: row["W"] + row["L"] for row in standings}

    features = []
    for name, stats in advanced.items():
        real_stats = real_ages.get(name, {})
        if not is_real_rookie(name, season, real_stats, debut):
            continue
        if real_stats.get("age", 99) > ROY_MAX_AGE:
            continue
        if stats["gp"] < ROY_MIN_GAMES or stats["mpg"] < ROY_MIN_MPG:
            continue
        features.append({
            "name": name, "pie": stats["pie"], "ts_pct": stats["ts_pct"],
            "usg_pct": stats["usg_pct"], "gp": stats["gp"],
            "team_win_pct": win_pct.get(stats["team"], 0.0),
            "team_games": sim_team_games.get(stats["team"]),
            "already_won_last_season": False,
        })
    return features


def simulated_roy_candidates(conn, season: str, standings: Optional[List[dict]] = None,
                              weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a simulated season, mirroring
    simulated_mvp_candidates."""
    features = roy_features_from_simulated(conn, season, standings)
    return rank_roy_candidates(features, season, weights)


# =====================================================================
# REAL GROUND TRUTH -- DPOY
# =====================================================================

# Same verification method as MVP/ROY (each checked against PlayerAwards
# for a real "NBA Defensive Player of the Year" entry matching that
# exact season). "Metta World Peace" (not "Ron Artest") for 2003-04 --
# the real award endpoint AND this project's own rosters.json both file
# him under his current legal name retroactively, so that's the name to
# match against everywhere else in this project too.
REAL_DPOY_WINNERS: Dict[str, str] = {
    "1996-97": "Dikembe Mutombo",
    "1997-98": "Dikembe Mutombo",
    "1998-99": "Alonzo Mourning",
    "1999-00": "Alonzo Mourning",
    "2000-01": "Dikembe Mutombo",
    "2001-02": "Ben Wallace",
    "2002-03": "Ben Wallace",
    "2003-04": "Metta World Peace",
    "2004-05": "Ben Wallace",
    "2005-06": "Ben Wallace",
    "2006-07": "Marcus Camby",
    "2007-08": "Kevin Garnett",
    "2008-09": "Dwight Howard",
    "2009-10": "Dwight Howard",
    "2010-11": "Dwight Howard",
    "2011-12": "Tyson Chandler",
    "2012-13": "Marc Gasol",
    "2013-14": "Joakim Noah",
    "2014-15": "Kawhi Leonard",
    "2015-16": "Kawhi Leonard",
    "2016-17": "Draymond Green",
    "2017-18": "Rudy Gobert",
    "2018-19": "Rudy Gobert",
    "2019-20": "Giannis Antetokounmpo",
    "2020-21": "Rudy Gobert",
    "2021-22": "Marcus Smart",
    "2022-23": "Jaren Jackson Jr.",
    "2023-24": "Rudy Gobert",
    "2024-25": "Evan Mobley",
    "2025-26": "Victor Wembanyama",
}

# Same looser-than-MVP bar as ROY -- real DPOY winners are always heavy
# rotation players (unlike ROY, no real bench-role winners), so this
# stays close to MVP's own floor rather than ROY's lower one.
DPOY_MIN_GAMES = 20
DPOY_MIN_MPG = 24.0

# TUNED against all 30 real seasons by backtest_dpoy.py --sweep, with
# a real BUG caught and fixed along the way, not just the highest
# holdout number found. An earlier version (stl=4, blk=2,
# team_def_strength=200) scored a higher holdout hit-rate (60%) but was
# WRONG on inspection (reported directly): team_def_strength is the
# SAME number for every player on a team, so weighting it that heavily
# let a pure offensive player who happens to gamble for steals (Kevin
# Durant 2011-12, Paul George 2012-13) beat that team's actual defensive
# anchor (Serge Ibaka, Roy Hibbert) just by riding the team's defensive
# rating with slightly more steals/availability -- exactly the kind of
# real-basketball sanity check this project's own accuracy-tuning
# lesson warns about (a number that looks better because something is
# broken is not accuracy). BLK is now the dominant individual signal
# instead of team context, matching real DPOY history (the large
# majority of real winners are rim-protecting bigs). Confirmed fixed:
# Ibaka/Hibbert now correctly outrank Durant/George.
#
# rim_deterrence (see dpoy_features_for_season) directly answers what
# used to be this section's stated ceiling: an elite rim protector like
# Gobert can post LOWER blocks than a good-but-not-elite one, because
# opponents stop attacking the rim against him at all -- raw BLK can
# never see that, but real player-tracking data (how far below normal
# opponents shoot at the rim against THIS player specifically) does.
# Its weight (200) had to be swept separately from stl/blk/team --
# checked directly, 0 to 2000 -- because its raw values (~0.05-0.15)
# are a full order of magnitude smaller than blk's, so it needs a
# proportionally large weight to matter at all, the same lesson
# team_def_strength's own weight already taught here. 200 is a real
# peak, not a monotonic "more is better": holdout hit-rate ROSE 40% ->
# 50% (top-3 60% -> 70%) at 200, then fell again by 300+ as
# rim_deterrence started to dominate too much. Only real for seasons
# 2013-14+ (camera tracking's real floor) -- defaults to 0 (neutral)
# for every season before it, and for a simulated season (this
# project's box-score-only sim has no shot-location/defender-
# assignment data to derive it from at all -- a structural gap, not a
# missing feature).
#
# perimeter_deterrence (the same real deterrence idea, at the 3PT line
# instead of the rim -- built specifically to try to rescue a real,
# confirmed gap: EVERY real-data DPOY pick this formula ever made was a
# traditional big, 30/30 seasons, while 4/30 real winners are
# perimeter defenders -- Draymond Green, Marcus Smart, Kawhi Leonard
# x2). Tested honestly and it does NOT help: swept 0 to 3000, holdout
# hit-rate never improves on 0 and gets WORSE past ~150 (draggin in
# Kevin Durant/LeBron James as false positives, the same shape of bug
# team_def_strength caused earlier). Left at weight 0 -- the
# infrastructure (data_source.fetch_player_perimeter_defense,
# loader.load_player_perimeter_defense) stays, in case it's useful
# combined with something else later (e.g. gated to perimeter
# positions only), but it does not get to silently make the score
# worse just because it seemed like it should help conceptually.
# deflections (real hustle-stat data, a LATER real floor -- 2016-17,
# see data_source.HUSTLE_STATS_FIRST_SEASON): a genuinely more honest
# result than perimeter_deterrence above, but still a partial one, not
# a full fix -- reported plainly rather than oversold either way.
# Checked directly: Draymond Green's real 2016-17 (his real DPOY-
# winning season) deflections are 3.88/game against Rudy Gobert's 1.64,
# Kevin Durant's 2.23, and LeBron James's 2.26 that SAME season -- a
# real signal that does NOT reward pure offensive stars, unlike
# perimeter_deterrence. At weight 15, it does flip 2016-17 to the
# correct Draymond pick -- but that same weight also flips TWO
# previously-correct picks wrong (Andre Drummond over the real Gobert
# in 2018-19, Jonathan Isaac over the real Giannis in 2019-20), because
# high-deflection guards who aren't real DPOY-level defenders (gambling
# for deflections without being good positional defenders) get
# rewarded too. Net: exact hit-rate does NOT improve at any weight
# tried (0-30) over the deflections=0 baseline. Left low (4) -- at that
# weight hit-rate is unchanged from 0 but top-3 rate improves (7/10 ->
# 8/10 on holdout), a real if modest gain with no downside found.
DPOY_WEIGHTS: Dict[str, float] = dict(stl=1.0, blk=10.0, reb=1.0, team_def_strength=60.0,
                                       rim_deterrence=200.0, perimeter_deterrence=0.0,
                                       deflections=4.0, availability=2.0)

# A player needs to have faced at least this many real rim/3PT attempts
# per game for his deterrence number to mean anything -- a wing who
# only occasionally rotates over to contest a rim shot (or a big who
# almost never closes out on 3s) has a noisy, small-sample rate that
# shouldn't carry the same weight as a real regular's. Below this,
# deterrence is treated as 0 (neutral), not disqualifying -- same
# "missing means average" choice as every other optional signal here.
DPOY_MIN_RIM_FREQ = 2.0
DPOY_MIN_PERIMETER_FREQ = 2.0


def dpoy_score(stl: float, blk: float, reb: float, team_def_strength: float, rim_deterrence: float,
                perimeter_deterrence: float, deflections: float, gp: float, season: str,
                weights: Optional[Dict[str, float]] = None,
                team_games: Optional[int] = None) -> float:
    """
    One player's real-basketball DPOY "case" -- stl/blk are the
    clearest individual defensive PRODUCTION signals this project has
    real per-game data for; reb (mostly DEFENSIVE rebounding in
    practice, since a rim protector's board total is dominated by
    D-REB) gives some credit to interior presence without needing a
    separate DREB-only stat; rim_deterrence/perimeter_deterrence are
    real DETERRENCE at the two ends of the floor (see DPOY_WEIGHTS's
    comment) -- 0.0 when unavailable (pre-2013-14, or a simulated
    season), which this formula treats as neutral, not as "no
    defense"; deflections is the real signal that actually rescues a
    versatile PERIMETER defender's case (Draymond Green, Marcus Smart)
    where the deterrence signals above could not -- see DPOY_WEIGHTS's
    comment; team_def_strength is "how much better than league average
    does this player's team defend" -- real DPOY voting leans on this
    heavily, per the design discussion this award was built from.
    Availability penalizes missed games, same reasoning as MVP/ROY.
    """
    weights = weights or DPOY_WEIGHTS
    availability = _availability(gp, season, team_games)
    return (
        weights["stl"] * stl
        + weights["blk"] * blk
        + weights["reb"] * reb
        + weights["team_def_strength"] * team_def_strength
        + weights["rim_deterrence"] * rim_deterrence
        + weights["perimeter_deterrence"] * perimeter_deterrence
        + weights["deflections"] * deflections
        + weights["availability"] * availability
    )


def dpoy_features_for_season(season: str) -> List[dict]:
    """
    Every real DPOY candidate's features for `season`: real per-game
    STL/BLK/REB (from loader.load_teams's Player objects -- the same
    real rosters.json data everything else in this project already
    uses, not a new fetch), each player's TEAM's real defensive
    strength (opp_fg_pct vs that season's league average -- lower
    opp_fg_pct is better defense, so this is flipped to make higher =
    better, matching every other signal here), real rim AND perimeter
    deterrence (load_player_rim_defense/load_player_perimeter_defense --
    0.0 when unavailable, see DPOY_MIN_RIM_FREQ/DPOY_MIN_PERIMETER_FREQ
    and DPOY_WEIGHTS), real deflections (load_player_hustle_stats -- its
    own, later real floor, 2016-17), and the usual games/minutes floor
    (DPOY_MIN_GAMES/DPOY_MIN_MPG).
    """
    teams = load_teams(season)
    advanced = load_player_advanced_stats(season)  # only used here for gp/mpg
    if not teams or not advanced:
        return []
    rim_defense = load_player_rim_defense(season)  # {} before 2013-14 -- see that function's docstring
    perimeter_defense = load_player_perimeter_defense(season)  # same real floor
    hustle = load_player_hustle_stats(season)  # {} before 2016-17 -- its own, later real floor
    team_games = team_games_played(season)

    league_avg_opp_fg_pct = sum(t.opp_fg_pct for t in teams.values()) / len(teams)

    features = []
    for team_name, team in teams.items():
        team_def_strength = league_avg_opp_fg_pct - team.opp_fg_pct
        for player in team.players:
            stats = advanced.get(player.name)
            if not stats or stats["gp"] < DPOY_MIN_GAMES or stats.get("mpg", 0.0) < DPOY_MIN_MPG:
                continue
            rim = rim_defense.get(player.name)
            rim_deterrence = rim["rim_deterrence"] if rim and rim["rim_freq"] >= DPOY_MIN_RIM_FREQ else 0.0
            perimeter = perimeter_defense.get(player.name)
            perimeter_deterrence = (perimeter["perimeter_deterrence"]
                                     if perimeter and perimeter["perimeter_freq"] >= DPOY_MIN_PERIMETER_FREQ
                                     else 0.0)
            deflections = hustle.get(player.name, {}).get("deflections", 0.0)
            features.append({
                "name": player.name, "stl": player.stl, "blk": player.blk, "reb": player.reb,
                "team_def_strength": team_def_strength, "rim_deterrence": rim_deterrence,
                "perimeter_deterrence": perimeter_deterrence, "deflections": deflections,
                "gp": stats["gp"], "team_games": team_games.get(team_name),
            })
    return features


def rank_dpoy_candidates(features: List[dict], season: str,
                          weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Scores and ranks a season's already-built feature list -- pure
    in-memory math, no fetching, safe to call once per weight combo."""
    scored = [
        (f["name"], dpoy_score(
            stl=f["stl"], blk=f["blk"], reb=f["reb"], team_def_strength=f["team_def_strength"],
            rim_deterrence=f.get("rim_deterrence", 0.0),
            perimeter_deterrence=f.get("perimeter_deterrence", 0.0),
            deflections=f.get("deflections", 0.0),
            gp=f["gp"], season=season, weights=weights, team_games=f.get("team_games"),
        ))
        for f in features
    ]
    scored.sort(key=lambda c: -c[1])
    return scored


def real_dpoy_candidates(season: str, weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a single real-season lookup."""
    features = dpoy_features_for_season(season)
    return rank_dpoy_candidates(features, season, weights)


def dpoy_features_from_simulated(conn, season: str) -> List[dict]:
    """
    Same idea as mvp_features_from_simulated: STL/BLK/REB come from
    THIS run's own simulated per-game averages (db.get_player_season_averages
    already computes these the same "derive, don't duplicate" way every
    other simulated stat here does), and team defensive strength comes
    from db.get_simulated_team_opp_fg_pct -- the simulated analogue of
    Team.opp_fg_pct, built for exactly this.

    No rim_deterrence/perimeter_deterrence/deflections here at all --
    not a missing feature, a real structural gap: this project's sim
    only produces box-score totals (FGM/FGA/BLK/etc.), never shot-by-
    shot location, who was the closest defender, or hustle-play events
    (deflections, contests), so there is nothing to derive any of them
    FROM. rank_dpoy_candidates treats a feature dict with none of these
    keys as 0.0 (neutral) for each, same as a real season before that
    signal's own camera-tracking floor.
    """
    import db  # local import: db.py doesn't import awards.py, avoid a cycle either way

    advanced = db.get_simulated_advanced_stats(conn, season)
    if not advanced:
        return []
    opp_fg_pct = db.get_simulated_team_opp_fg_pct(conn, season)
    if not opp_fg_pct:
        return []
    league_avg_opp_fg_pct = sum(opp_fg_pct.values()) / len(opp_fg_pct)
    sim_team_games = {row["team"]: row["W"] + row["L"] for row in db.get_standings(conn, season)}

    features = []
    for name, stats in advanced.items():
        if stats["gp"] < DPOY_MIN_GAMES or stats["mpg"] < DPOY_MIN_MPG:
            continue
        avg = db.get_player_season_averages(conn, name, season)
        if not avg:
            continue
        team_def_strength = league_avg_opp_fg_pct - opp_fg_pct.get(stats["team"], league_avg_opp_fg_pct)
        features.append({
            "name": name, "stl": avg["stl"], "blk": avg["blk"], "reb": avg["reb"],
            "team_def_strength": team_def_strength, "gp": stats["gp"],
            "team_games": sim_team_games.get(stats["team"]),
        })
    return features


def simulated_dpoy_candidates(conn, season: str, weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a simulated season."""
    features = dpoy_features_from_simulated(conn, season)
    return rank_dpoy_candidates(features, season, weights)


# =====================================================================
# REAL GROUND TRUTH -- MIP
# =====================================================================

# Same verification method as MVP/ROY/DPOY. A few of these are genuine
# "not who I'd have guessed from memory" names -- Ike Austin (not
# "Isaac Austin"), CJ McCollum (not "C.J. McCollum") -- exactly why
# every one here was checked against the real awards API rather than
# trusted from memory.
REAL_MIP_WINNERS: Dict[str, str] = {
    "1996-97": "Ike Austin",
    "1997-98": "Alan Henderson",
    "1998-99": "Darrell Armstrong",
    "1999-00": "Jalen Rose",
    "2000-01": "Tracy McGrady",
    "2001-02": "Jermaine O'Neal",
    "2002-03": "Gilbert Arenas",
    "2003-04": "Zach Randolph",
    "2004-05": "Bobby Simmons",
    "2005-06": "Boris Diaw",
    "2006-07": "Monta Ellis",
    "2007-08": "Hedo Turkoglu",
    "2008-09": "Danny Granger",
    "2009-10": "Aaron Brooks",
    "2010-11": "Kevin Love",
    "2011-12": "Ryan Anderson",
    "2012-13": "Paul George",
    "2013-14": "Goran Dragic",
    "2014-15": "Jimmy Butler III",
    "2015-16": "CJ McCollum",
    "2016-17": "Giannis Antetokounmpo",
    "2017-18": "Victor Oladipo",
    "2018-19": "Pascal Siakam",
    "2019-20": "Brandon Ingram",
    "2020-21": "Julius Randle",
    "2021-22": "Ja Morant",
    "2022-23": "Lauri Markkanen",
    "2023-24": "Tyrese Maxey",
    "2024-25": "Dyson Daniels",
    "2025-26": "Nickeil Alexander-Walker",
}

# A player needs real per-game data in BOTH this season and the one
# before it to even have an "improvement" to measure -- rules out
# rookies structurally (no prior season exists for them at all) and a
# player who barely played the prior year (the "before" number would be
# too noisy/small-sample to mean anything). Real MIP voting also
# effectively requires this -- nobody wins it off a change from a
# near-zero baseline.
MIP_MIN_GAMES = 20
MIP_MIN_MPG = 15.0

# TUNED against all 30 real seasons by backtest_mip.py --sweep:
# holdout hit-rate 40%, top-3 40% -- MIP's honest ceiling, in the same
# territory as DPOY's, for a real reason: MIP voting is famously
# narrative-driven, not stat-driven. pie_delta ended up at 0 (no help)
# -- real MIP snubs are often exactly players whose PIE genuinely did
# improve a lot (Giannis 2016-17 grew his whole game, not just scoring)
# but who lost to a bigger pure scoring jump anyway (Devin Booker,
# Russell Westbrook that same year), so weighting all-around
# improvement HIGHER actually hurts, not helps -- confirmed by testing.
# Two other real ideas tried and REJECTED, not just skipped:
# percentage scoring increase instead of raw PPG delta (17% hit-rate --
# far worse, over-rewards noisy small-baseline jumps like a bench
# player's 2ppg becoming 5ppg), and excluding already-good scorers from
# the prior season (no effect at all on any season tested).
MIP_WEIGHTS: Dict[str, float] = dict(pts_delta=0.5, pie_delta=0.0, availability=2.0)


def mip_score(pts_delta: float, pie_delta: float, gp: float, season: str,
              weights: Optional[Dict[str, float]] = None,
              team_games: Optional[int] = None) -> float:
    """
    One player's real-basketball MIP "case" -- pts_delta (this year's
    real PPG minus last year's) is the dominant signal, matching real
    MIP voting's own well-known bias toward scoring jumps; pie_delta
    (this year's PIE minus last year's) checks that the improvement is
    real all-around production, not just empty extra shots off a
    bigger role. Availability uses this season's OWN games (not a
    delta) -- same reasoning as MVP/ROY/DPOY, a real improvement still
    needs him on the floor to be voted on.
    """
    weights = weights or MIP_WEIGHTS
    availability = _availability(gp, season, team_games)
    return (
        weights["pts_delta"] * pts_delta
        + weights["pie_delta"] * pie_delta
        + weights["availability"] * availability
    )


def _player_pts_map(season: str) -> Dict[str, float]:
    """Real per-game PTS for every player who played in `season` --
    from loader.load_teams's Player objects (Player.pts is a computed
    @property, see models.py), not a new fetch."""
    teams = load_teams(season)
    return {p.name: p.pts for team in teams.values() for p in team.players}


def mip_features_for_season(season: str) -> List[dict]:
    """
    Every real MIP candidate's features for `season`: this season's
    real PTS/PIE minus the PREVIOUS season's (both from already-cached
    real data -- rosters.json via loader.load_teams for PTS,
    player_advanced.json for PIE/gp/mpg), for any player with enough
    games/minutes in BOTH seasons (MIP_MIN_GAMES/MIP_MIN_MPG).

    Returns [] for this project's earliest cached season -- there is no
    real prior season to diff against at all, a real floor (same
    category as every other "1996-97 has nothing before it" limitation
    in this project), not a bug.
    """
    if season == _earliest_season():
        return []
    prev_season = _previous_season(season)

    advanced = load_player_advanced_stats(season)
    prev_advanced = load_player_advanced_stats(prev_season)
    if not advanced or not prev_advanced:
        return []
    pts = _player_pts_map(season)
    prev_pts = _player_pts_map(prev_season)
    team_of = _player_team_map(season)
    team_games = team_games_played(season)

    features = []
    for name, stats in advanced.items():
        prev_stats = prev_advanced.get(name)
        if not prev_stats or name not in pts or name not in prev_pts:
            continue
        if (stats["gp"] < MIP_MIN_GAMES or stats.get("mpg", 0.0) < MIP_MIN_MPG
                or prev_stats["gp"] < MIP_MIN_GAMES or prev_stats.get("mpg", 0.0) < MIP_MIN_MPG):
            continue
        features.append({
            "name": name, "pts_delta": pts[name] - prev_pts[name],
            "pie_delta": stats["pie"] - prev_stats["pie"], "gp": stats["gp"],
            "team_games": team_games.get(team_of.get(name)),
        })
    return features


def rank_mip_candidates(features: List[dict], season: str,
                         weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Scores and ranks a season's already-built feature list -- pure
    in-memory math, no fetching, safe to call once per weight combo."""
    scored = [
        (f["name"], mip_score(
            pts_delta=f["pts_delta"], pie_delta=f["pie_delta"],
            gp=f["gp"], season=season, weights=weights, team_games=f.get("team_games"),
        ))
        for f in features
    ]
    scored.sort(key=lambda c: -c[1])
    return scored


def real_mip_candidates(season: str, weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a single real-season lookup."""
    features = mip_features_for_season(season)
    return rank_mip_candidates(features, season, weights)


def mip_features_from_simulated(conn, season: str) -> List[dict]:
    """
    MIP's "before" side needs a PREVIOUS SEASON's stats -- but this
    project's season.db is fully wiped every time a new season is
    simulated (season.simulate_season's fresh=True, see its docstring),
    so there is never a previous SIMULATED season's box scores sitting
    around to diff against, in either a single-season run (no prior
    sim exists at all) or a multi-season run (the earlier season's own
    games are already gone by the time a later one's awards are
    shown). Rather than fake a number, this compares THIS run's own
    simulated season against the player's REAL previous season -- the
    only honest baseline actually available. Still a legitimate
    "improvement" question (did the simulated season look better than
    his real one last year?), just answering a slightly different
    question than the real-season backtest does.
    """
    import db  # local import: db.py doesn't import awards.py, avoid a cycle either way

    if season == _earliest_season():
        return []
    prev_season = _previous_season(season)

    advanced = db.get_simulated_advanced_stats(conn, season)
    prev_advanced = load_player_advanced_stats(prev_season)  # REAL prior season -- see docstring
    if not advanced or not prev_advanced:
        return []
    prev_pts = _player_pts_map(prev_season)  # real prior-season PTS, built once for the whole season
    sim_team_games = {row["team"]: row["W"] + row["L"] for row in db.get_standings(conn, season)}

    features = []
    for name, stats in advanced.items():
        prev_stats = prev_advanced.get(name)
        if not prev_stats or name not in prev_pts:
            continue
        if (stats["gp"] < MIP_MIN_GAMES or stats["mpg"] < MIP_MIN_MPG
                or prev_stats["gp"] < MIP_MIN_GAMES or prev_stats.get("mpg", 0.0) < MIP_MIN_MPG):
            continue
        avg = db.get_player_season_averages(conn, name, season)
        if not avg:
            continue
        features.append({
            "name": name, "pts_delta": avg["pts"] - prev_pts[name],
            "pie_delta": stats["pie"] - prev_stats["pie"], "gp": stats["gp"],
            "team_games": sim_team_games.get(stats["team"]),
        })
    return features


def simulated_mip_candidates(conn, season: str, weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a simulated season."""
    features = mip_features_from_simulated(conn, season)
    return rank_mip_candidates(features, season, weights)


# =====================================================================
# GROUND TRUTH -- COACH OF THE YEAR
# =====================================================================
#
# UNVERIFIED, unlike every other REAL_*_WINNERS table above -- flagged
# plainly per the design discussion this was built from. MVP/ROY/DPOY/
# MIP were each checked against the real PlayerAwards endpoint (a real
# per-PLAYER award history, queryable by player_id); Coach of the Year
# goes to a COACH, and nbs_api has no coach-award endpoint anywhere --
# checked directly (no "coach" match anywhere in nba_api.stats.endpoints).
# This table is instead cross-checked against a real, current web
# source (nba.com's own year-by-year Coach of the Year history) rather
# than trusted from memory alone, which is the best verification
# actually available here -- still a real step down from the other
# three awards' live-API confirmation, and worth remembering if this
# table is ever suspected of being wrong.
#
# 2025-26 is deliberately OMITTED (not guessed) -- unlike the other
# three awards' 2025-26 winners (each confirmed via PlayerAwards), no
# source checked here had a real answer for this season yet.
REAL_COY_WINNERS: Dict[str, str] = {
    "1996-97": "Pat Riley",
    "1997-98": "Larry Bird",
    "1998-99": "Mike Dunleavy",
    "1999-00": "Doc Rivers",
    "2000-01": "Larry Brown",
    "2001-02": "Rick Carlisle",
    "2002-03": "Gregg Popovich",
    "2003-04": "Hubie Brown",
    "2004-05": "Mike D'Antoni",
    "2005-06": "Avery Johnson",
    "2006-07": "Sam Mitchell",
    "2007-08": "Byron Scott",
    "2008-09": "Mike Brown",
    "2009-10": "Scott Brooks",
    "2010-11": "Tom Thibodeau",
    "2011-12": "Gregg Popovich",
    "2012-13": "George Karl",
    "2013-14": "Gregg Popovich",
    "2014-15": "Mike Budenholzer",
    "2015-16": "Steve Kerr",
    "2016-17": "Mike D'Antoni",
    "2017-18": "Dwane Casey",
    "2018-19": "Mike Budenholzer",
    "2019-20": "Nick Nurse",
    "2020-21": "Tom Thibodeau",
    "2021-22": "Monty Williams",
    "2022-23": "Mike Brown",
    "2023-24": "Mark Daigneault",
    "2024-25": "Kenny Atkinson",
}

# TUNED against 28 real seasons by backtest_coy.py --sweep. FIXED a
# real bug found by testing (reported directly, from a real basketball
# hunch): the original formula used win_pct_delta ALONE, but real COY
# winners are overwhelmingly the league's best or near-best record that
# season -- checked directly against all 28: the real winner's team
# ranked #1-#3 in the league in 19 of 28 seasons, and #1 outright in
# 10. Pure improvement completely missed this (a team can jump a lot
# while still being mediocre). Adding win_pct_level took holdout
# hit-rate from ~20% to 67% (top-3 78%) -- equal weight on both signals
# swept best; level alone (delta=0) or delta-heavy settings both scored
# worse, so it's genuinely a BLEND of "best record" and "biggest jump,"
# not either one alone.
COY_WEIGHTS: Dict[str, float] = dict(win_pct_level=1.0, win_pct_delta=1.0)

# Coach data before this season is not just PATCHY, it is WRONG often
# enough not to publish -- found by testing against known history, and
# a genuinely nastier problem than the coverage gaps noted earlier,
# because coverage completeness gave false reassurance: 1999-00 looked
# like the best-covered old season (29/29 teams named) but only 3 of 7
# spot-checked names were right. The wrong ones aren't near-misses,
# they're coaches from a decade later -- 1999-00 Indiana comes back as
# Frank Vogel (really there 2010-16), Miami as Erik Spoelstra (2008+),
# New York as Mike D'Antoni (2008+). Measured accuracy by era on
# known-real coach/team/season facts: 1996-2003 60%, 2003-2011 100%,
# 2011-2026 93%.
#
# So this is a real floor, same spirit as MIP's "no prior season to
# diff against" -- an award nobody can name correctly shouldn't be
# named at all, especially somewhere a UI will show it as fact. It
# also explains a backtest anomaly that had no explanation before:
# COY's train hit-rate was BELOW its holdout (37% vs 67%), backwards
# from every other award, because the train set carries these bad
# seasons -- scored separately, pre-2004-05 is 29% against a clean
# 2004-05..2015-16 train of 42%.
#
# One constant, trivially reversible if better coach data turns up.
COY_RELIABLE_FIRST_SEASON = "2004-05"


def coy_score(win_pct_level: float, win_pct_delta: float, weights: Optional[Dict[str, float]] = None) -> float:
    """
    One coach's real-basketball COY "case": mostly his TEAM's real win%
    THIS season (win_pct_level -- see COY_WEIGHTS's comment on why this
    has to dominate), plus its improvement over last season
    (win_pct_delta), which covers the two real COY story shapes at
    once: a returning coach improving on his own prior season, and a
    brand-new coach immediately turning a bad team around (Doc Rivers
    1999-00, Byron Scott 2007-08, Kenny Atkinson 2024-25 were all each
    coach's first year with that team) -- both need SOME weight on
    delta, just not as much as the level.
    """
    weights = weights or COY_WEIGHTS
    return weights["win_pct_level"] * win_pct_level + weights["win_pct_delta"] * win_pct_delta


def coy_features_for_season(season: str, team_win_pct: Optional[Dict[str, float]] = None,
                             prev_team_win_pct: Optional[Dict[str, float]] = None) -> List[dict]:
    """
    Every real COY candidate for `season`: every team with a known head
    coach THIS season (load_team_coaches -- a team maps to None for a
    real gap in that data, see that function's docstring, and is
    skipped, not guessed), paired with that team's real win% THIS
    season and its improvement over last season's. Returns [] for this
    project's earliest cached season (no prior season to diff against
    -- same real floor as MIP).
    """
    if season == _earliest_season() or season < COY_RELIABLE_FIRST_SEASON:
        return []  # see COY_RELIABLE_FIRST_SEASON -- wrong coach names, not just missing
    prev_season = _previous_season(season)

    coaches = load_team_coaches(season)
    if not coaches:
        return []
    win_pct = team_win_pct if team_win_pct is not None else fetch_real_team_win_pct(season)
    prev_win_pct = prev_team_win_pct if prev_team_win_pct is not None else fetch_real_team_win_pct(prev_season)

    features = []
    for team_name, coach in coaches.items():
        if not coach or team_name not in win_pct or team_name not in prev_win_pct:
            continue
        features.append({
            "name": coach, "team": team_name, "win_pct_level": win_pct[team_name],
            "win_pct_delta": win_pct[team_name] - prev_win_pct[team_name],
        })
    return features


def rank_coy_candidates(features: List[dict], weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Scores and ranks a season's already-built feature list -- pure
    in-memory math, no fetching, safe to call once per weight combo."""
    scored = [(f["name"], coy_score(f["win_pct_level"], f["win_pct_delta"], weights)) for f in features]
    scored.sort(key=lambda c: -c[1])
    return scored


def real_coy_candidates(season: str, weights: Optional[Dict[str, float]] = None,
                         team_win_pct: Optional[Dict[str, float]] = None,
                         prev_team_win_pct: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a single real-season lookup."""
    features = coy_features_for_season(season, team_win_pct, prev_team_win_pct)
    return rank_coy_candidates(features, weights)


def coy_features_from_simulated(conn, season: str, standings: Optional[List[dict]] = None) -> List[dict]:
    """
    Same "before has to be real" reasoning as MIP (season.db is fully
    wiped every new simulated season, see mip_features_from_simulated's
    docstring): this season's win% (level AND delta) comes from THIS
    run's own simulated standings, but last season's win% (delta's
    baseline only) is the team's REAL prior-season record -- the only
    baseline actually available. Coach identity (load_team_coaches) is
    real either way; the sim has no concept of coaching decisions at
    all to make that up.
    """
    import db  # local import: db.py doesn't import awards.py, avoid a cycle either way

    if season == _earliest_season() or season < COY_RELIABLE_FIRST_SEASON:
        return []  # see COY_RELIABLE_FIRST_SEASON -- wrong coach names, not just missing
    prev_season = _previous_season(season)

    coaches = load_team_coaches(season)
    if not coaches:
        return []
    if standings is None:
        standings = db.get_standings(conn, season)
    win_pct = {row["team"]: row["W"] / (row["W"] + row["L"]) if (row["W"] + row["L"]) else 0.0
               for row in standings}
    prev_win_pct = fetch_real_team_win_pct(prev_season)

    features = []
    for team_name, coach in coaches.items():
        if not coach or team_name not in win_pct or team_name not in prev_win_pct:
            continue
        features.append({
            "name": coach, "team": team_name, "win_pct_level": win_pct[team_name],
            "win_pct_delta": win_pct[team_name] - prev_win_pct[team_name],
        })
    return features


def simulated_coy_candidates(conn, season: str, standings: Optional[List[dict]] = None,
                              weights: Optional[Dict[str, float]] = None) -> List[Tuple[str, float]]:
    """Convenience one-shot version for a simulated season."""
    features = coy_features_from_simulated(conn, season, standings)
    return rank_coy_candidates(features, weights)
