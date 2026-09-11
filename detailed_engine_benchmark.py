"""
First 100-Game League-Level Benchmark instrumentation (measurement only).

============================ DOCTRINE FIREWALL ============================
Same posture as `detailed_engine_diagnostics.py` (see that module's own
docstring): this module OBSERVES already-structured, already-produced
`DetailedGameResult`/`PossessionRecord` state. It NEVER makes a simulation
decision, never consumes RNG, never mutates simulation state, and is never
imported by `possession_orchestrator.py`, `detailed_game_orchestrator.py`,
or `detailed_game.py`. It exists to MEASURE how far the current detailed
engine is from real 2024-25 NBA league averages -- it does not (and must
not) change anything to make that measurement look better.

============================ ACCOUNTING-AUTHORITY WARNING ============================
The detailed engine's event schema is NOT yet fully authoritative for every
box-score field (see `possession_orchestrator.py`'s own
`derive_stat_deltas_from_events` docstring for the itemized, up-to-date gap
list). Every metric this module reports is tagged with one of three
authority levels -- EVENT-AUTHORITATIVE, PROVISIONAL STATDELTA, or
PARTIALLY EVENT-DERIVED -- via `ACCOUNTING_AUTHORITY` below. Re-derived
from THIS module's own inspection of the current repo, not copied blindly
from a prior phase's report.

============================ TEAM-GAME ATTRIBUTION ============================
`PossessionRecord.provisional_deltas` (a `StatDeltas`) is populated by
`possession_orchestrator.py`'s dispatch code under a fixed, real convention
this module reuses (verified by direct source read, same methodology
`derive_stat_deltas_from_events` already established):
  - points/fga/fgm/fg3a/fg3m/fta/ftm/oreb/team turnovers -> the POSSESSION'S OWN
    OFFENSE team (`PossessionRecord.offense_team_id`) -- these are all
    either the offense's own shot/FT attempts or their own second-chance
    rebound/giveaway.
  - dreb/steals/blocks -> the POSSESSION'S OWN DEFENSE team
    (`PossessionRecord.defense_team_id`) -- the defense secured the miss,
    disrupted a pass, or blocked a shot.
  - personal_fouls (a `{player_id: count}` dict) -> resolved PER PLAYER via
    a fixed player_id->team_id map (no in-game substitutions exist in this
    V0 engine, so one map built from the two starting fives is valid for
    the whole game) -- an offensive foul is the offense's own player, a
    defensive/shooting foul is the defense's own player.
No new simulation behavior; this is a read-only re-attribution of fields
this module did not itself compute.
"""
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from detailed_game import DetailedGameConfig, DetailedGameResult, simulate_detailed_game
from possession_orchestrator import PlayerSimulationProfile, PossessionConfig

# ---------------------------------------------------------------------
# 1. Accounting-authority classification -- see module docstring.
# ---------------------------------------------------------------------
EVENT_AUTHORITATIVE = "EVENT-AUTHORITATIVE"
PROVISIONAL_STATDELTA = "PROVISIONAL STATDELTA"
PARTIALLY_EVENT_DERIVED = "PARTIALLY EVENT-DERIVED"

# Verified by direct inspection of `derive_stat_deltas_from_events` (possession_orchestrator.py) as of
# this benchmark -- re-check that function's own docstring before trusting this table in a future phase;
# it is deliberately NOT assumed to be static.
ACCOUNTING_AUTHORITY: Dict[str, str] = {
    "oreb": EVENT_AUTHORITATIVE,
    "dreb": EVENT_AUTHORITATIVE,
    "turnovers": EVENT_AUTHORITATIVE,  # backward-compatible player-charged total
    "team_turnovers": EVENT_AUTHORITATIVE,
    "player_turnovers": EVENT_AUTHORITATIVE,
    "steals": EVENT_AUTHORITATIVE,
    "blocks": EVENT_AUTHORITATIVE,
    "personal_fouls": EVENT_AUTHORITATIVE,
    "pf": EVENT_AUTHORITATIVE,
    # points/FGA/FGM/3PA/3PM: SHOT_RESOLVED carries only metadata["made"] -- no shot_family/point-value
    # field exists in the Phase 15 event schema, and the whistled-and-one path does not log a
    # SHOT_RESOLVED event for its own underlying shot at all. StatDeltas is the only place these live.
    "points": PROVISIONAL_STATDELTA,
    "fga": PROVISIONAL_STATDELTA,
    "fgm": PROVISIONAL_STATDELTA,
    "fg3a": PROVISIONAL_STATDELTA,
    "fg3m": PROVISIONAL_STATDELTA,
    # FTA/FTM: no free-throw EventType exists anywhere in the repository; `administer_floor_foul`'s own
    # bonus FT loop performs a bare dataclasses.replace() with no _log call at all.
    "fta": PROVISIONAL_STATDELTA,
    "ftm": PROVISIONAL_STATDELTA,
    # derived ratios inherit the authority of their least-authoritative input.
    "fg_pct": PROVISIONAL_STATDELTA,
    "fg3_pct": PROVISIONAL_STATDELTA,
    "ft_pct": PROVISIONAL_STATDELTA,
    "fta_per_fga": PROVISIONAL_STATDELTA,
    "fg3a_rate": PROVISIONAL_STATDELTA,
    "oreb_pct": EVENT_AUTHORITATIVE,
    "ortg": PARTIALLY_EVENT_DERIVED,  # possessions (TRUE, structural) are authoritative; PTS is not
    "possessions": EVENT_AUTHORITATIVE,  # TRUE simulator possession boundaries -- PossessionRecord count
    "possessions_est": PARTIALLY_EVENT_DERIVED,  # box-score estimate formula built from mixed-authority inputs
    "ast": PROVISIONAL_STATDELTA,  # not populated by StatDeltas at all in the current engine -- see note below
}
# NOTE: `StatDeltas` has NO `ast` field at all (verified: no assists concept exists anywhere in
# `possession_orchestrator.py`'s dispatch code) -- AST is reported as `NOT_YET_MODELED`, never a
# fabricated zero, everywhere below.


# ---------------------------------------------------------------------
# 2. Per-team-game aggregation.
# ---------------------------------------------------------------------
@dataclass
class TeamGameStats:
    """One team's box-score-shaped totals for ONE game. Field authority
    is documented once, in `ACCOUNTING_AUTHORITY` above -- not repeated
    per-field here."""
    team_id: str
    opponent_team_id: str
    points: int = 0
    fga: int = 0
    fgm: int = 0
    fg3a: int = 0
    fg3m: int = 0
    fta: int = 0
    ftm: int = 0
    oreb: int = 0
    dreb: int = 0
    turnovers: int = 0
    player_turnovers: int = 0
    steals: int = 0
    blocks: int = 0
    personal_fouls: int = 0
    true_possessions: int = 0  # count of PossessionRecord where THIS team was offense -- TRUE alternating-possession count
    opponent_dreb: int = 0  # filled in a second pass -- needed for OREB% denominator


def _lineup_team_map(home_five: Tuple[str, ...], away_five: Tuple[str, ...],
                      home_team_id: str, away_team_id: str) -> Dict[str, str]:
    mapping = {pid: home_team_id for pid in home_five}
    mapping.update({pid: away_team_id for pid in away_five})
    return mapping


def aggregate_team_game_stats(result: DetailedGameResult, home_five: Tuple[str, ...],
                               away_five: Tuple[str, ...]) -> Tuple[TeamGameStats, TeamGameStats]:
    """Builds (home, away) `TeamGameStats` for ONE `DetailedGameResult`,
    per the attribution convention in this module's own docstring. Pure
    read-only aggregation over already-produced `PossessionRecord`s --
    no RNG, no simulation-state mutation."""
    home_id, away_id = result.home_team_id, result.away_team_id
    player_team = _lineup_team_map(home_five, away_five, home_id, away_id)
    stats = {home_id: TeamGameStats(team_id=home_id, opponent_team_id=away_id),
              away_id: TeamGameStats(team_id=away_id, opponent_team_id=home_id)}

    for record in result.possessions:
        d = record.provisional_deltas
        off = stats[record.offense_team_id]
        deff = stats[record.defense_team_id]

        off.points += d.points
        off.fga += d.fga
        off.fgm += d.fgm
        off.fg3a += d.fg3a
        off.fg3m += d.fg3m
        off.fta += d.fta
        off.ftm += d.ftm
        off.oreb += d.oreb
        # Public/NBA team TOV comparison uses the event-derivable TEAM total,
        # including team-only shot-clock violations. Player-charged TOV remain
        # separately visible and are never inferred by subtraction.
        off.turnovers += d.team_turnovers
        off.player_turnovers += sum(d.player_turnovers.values())
        off.true_possessions += 1

        deff.dreb += d.dreb
        deff.steals += d.steals
        deff.blocks += d.blocks

        for player_id, count in d.personal_fouls.items():
            team_id = player_team.get(player_id)
            if team_id is not None:
                stats[team_id].personal_fouls += count

    home_stats, away_stats = stats[home_id], stats[away_id]
    home_stats.opponent_dreb = away_stats.dreb
    away_stats.opponent_dreb = home_stats.dreb
    return home_stats, away_stats


def estimated_possessions(t: TeamGameStats) -> float:
    """The standard public box-score possession-ESTIMATE formula
    (FGA + 0.44*FTA - OREB + TOV), reported SEPARATELY from
    `t.true_possessions` (the engine's own real possession-boundary
    count) -- see module docstring's TEAM-GAME ATTRIBUTION section and
    the benchmark report's own "true vs. estimated" warning. Built from
    PROVISIONAL STATDELTA inputs (FGA/FTA) plus EVENT-AUTHORITATIVE
    inputs (OREB/TOV) -- PARTIALLY EVENT-DERIVED overall."""
    return t.fga + 0.44 * t.fta - t.oreb + t.turnovers


# ---------------------------------------------------------------------
# 3. Percentile / distribution helpers.
# ---------------------------------------------------------------------
@dataclass
class Distribution:
    mean: float
    stdev: float
    p10: float
    p25: float
    median: float
    p75: float
    p90: float
    minimum: float
    maximum: float
    n: int


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted sequence. `pct` in
    [0, 1]. Deterministic, no interpolation ambiguity -- same simple
    convention `detailed_engine_diagnostics.py`'s own `_percentile`
    already uses in this repo (reused, not reinvented)."""
    if not sorted_values:
        raise ValueError("cannot compute a percentile of an empty sequence")
    idx = min(len(sorted_values) - 1, max(0, int(round(pct * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def distribution(values: Sequence[float]) -> Distribution:
    if not values:
        raise ValueError("cannot summarize an empty sequence")
    s = sorted(values)
    return Distribution(
        mean=statistics.fmean(values), stdev=statistics.pstdev(values) if len(values) > 1 else 0.0,
        p10=_percentile(s, 0.10), p25=_percentile(s, 0.25), median=statistics.median(values),
        p75=_percentile(s, 0.75), p90=_percentile(s, 0.90),
        minimum=min(values), maximum=max(values), n=len(values),
    )


def pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Plain Pearson correlation coefficient. Returns `None` (never a
    fabricated 0.0) when fewer than 2 points or zero variance in either
    series. SIMULATOR BASELINE ONLY -- see module docstring; never
    compared against a real-league correlation matrix here."""
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0.0 or var_y == 0.0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def relative_error_pct(sim: float, real: float) -> float:
    """`(sim - real) / real * 100`, SIGN PRESERVED. Positive = simulator
    is HIGH; negative = simulator is LOW. Never `abs()`'d here -- a
    caller that wants a ranking by magnitude applies `abs()` itself,
    explicitly, at the point of ranking."""
    if real == 0:
        raise ValueError("real reference value cannot be zero for a relative-error calculation")
    return (sim - real) / real * 100.0


# ---------------------------------------------------------------------
# 4. Benchmark sample runner.
# ---------------------------------------------------------------------
@dataclass
class BenchmarkGame:
    seed: int
    result: DetailedGameResult
    home: TeamGameStats
    away: TeamGameStats


def _default_profiles(off_five: Tuple[str, ...], def_five: Tuple[str, ...]) -> Dict[str, PlayerSimulationProfile]:
    profiles = {}
    for p in off_five:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "HOME")
    for p in def_five:
        profiles[p] = PlayerSimulationProfile.synthetic(p, "AWAY")
    return profiles


def run_benchmark_sample(seeds: Sequence[int], config: Optional[DetailedGameConfig] = None,
                          home_five: Tuple[str, ...] = tuple(str(i) for i in range(1, 6)),
                          away_five: Tuple[str, ...] = tuple(str(i) for i in range(11, 16))
                          ) -> Tuple[BenchmarkGame, ...]:
    """Runs one `DetailedGameResult` per seed, using the SAME synthetic
    `PlayerSimulationProfile.synthetic` profiles and default
    `DetailedGameConfig` (which itself defaults to the current, already-
    calibrated `PossessionConfig()`) the existing diagnostics use --
    never a bespoke, favorable profile set for this benchmark. `config`
    may be supplied explicitly (e.g. by a test needing a smaller game)
    but the real 100-game benchmark run always uses the default."""
    profiles = _default_profiles(home_five, away_five)
    games = []
    for seed in seeds:
        result = simulate_detailed_game("HOME", "AWAY", home_five, away_five, profiles,
                                         rng_seed=seed, config=config)
        home_stats, away_stats = aggregate_team_game_stats(result, home_five, away_five)
        games.append(BenchmarkGame(seed=seed, result=result, home=home_stats, away=away_stats))
    return tuple(games)


def team_games(games: Sequence[BenchmarkGame]) -> Tuple[TeamGameStats, ...]:
    """Flattens a game sample into individual TEAM-GAMEs (2 per game) --
    the natural unit for the percentile/distribution tables and for
    comparison against real per-team-game NBA statistics."""
    out: List[TeamGameStats] = []
    for g in games:
        out.append(g.home)
        out.append(g.away)
    return tuple(out)
