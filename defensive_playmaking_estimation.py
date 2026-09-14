"""
Defensive Truth V1 -- defensive_playmaking: the season-level estimator.

PARALLEL to player_ability_estimation.py -- reuses its generic shrinkage/percentile/cutoff
machinery by IMPORT only (same posture as rim_protection_estimation.py/poa_containment_estimation.py/
foul_estimation.py). Does not modify or duplicate the existing, cruder `defensive_playmaking`
attribute already defined there (`_extract_def_playmaking`, real STL+BLK per-36 minutes) -- that
attribute is EXPLICITLY still consumed as `PlayerSimulationProfile.defensive_playmaking_per36`
(the engine's own field), documented as a real "KEEP-BUT-FLAG" posture (Phase 21A), and is left
completely untouched here. This module builds a genuinely DIFFERENT, richer construct under the
SAME plain-English name -- documented, not papered over (same posture this project already took
for `ball_security` in Phase 8/Playmaking, and `offensive_rebounding`/`defensive_rebounding` in the
Rebounding Truth phase).

============================ CONSTRUCT DEFINITION ============================
`defensive_playmaking` = ability to generate disruptive defensive events (steals, blocks,
deflections), normalized by real defensive exposure (minutes), NOT a raw STL+BLK/game count:

    rate = (STL + BLK + DEFLECTIONS) per-36-minutes

Real, available components: `STL`/`BLK` (box score, per-game averages, every cached season) +
`DEFLECTIONS` (`player_hustle.json`, real `leaguehustlestatsplayer` tracking, floor 2016-17 --
see `data_source.HUSTLE_STATS_FIRST_SEASON`). Loose-ball recoveries were investigated (the real
`leaguehustlestatsplayer` endpoint also returns `LOOSE_BALLS_RECOVERED`/`DEF_LOOSE_BALLS_RECOVERED`)
but are NOT ingested this phase -- explicitly out of scope ("do not overcomplicate V1 unless the
data clearly supports it"; deflections alone already meaningfully separates event-generation
ability from raw STL+BLK, the exact real finding `player_hustle.json`'s own module docstring
already documents for Draymond Green's real 2016-17 DPOY case). Component weights are a SIMPLE,
UNWEIGHTED SUM of three real disruptive-event counts (steals a possession outright, blocks a shot
outright, deflects a pass/dribble -- each a real, discrete disruption of the opponent's
possession) -- not fit/tuned, a defensible and simple composite per this phase's own instruction.

Denominator is MINUTES (not games, not possessions) -- the same real, always-available exposure
measure `foul_discipline`'s own DEFAULT_DISC_DENOMINATOR already uses, and simpler than a
possessions-proxy derivation for an equivalent real signal (minutes on the floor is exactly the
real window during which a player can generate any of these three events). Effective floor is
2016-17 (`DEFENSIVE_PLAYMAKING_FIRST_SEASON`), the tighter DEFLECTIONS constraint -- STL/BLK exist
back to 1996-97, but a season with a missing deflections component would silently understate every
player's rate, so the whole construct is gated to the real hustle-tracking floor.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from data_source import HUSTLE_STATS_FIRST_SEASON
from loader import load_player_advanced_stats, load_player_hustle_stats, load_teams
from player_ability_estimation import (
    RATING_MIN, RATING_MAX, SeasonEvidence, _percentile_rating, _seasons_through_cutoff,
    _weighted_shrunk_estimate,
)
from player_ability_profile import AttributeEstimate

DEFENSIVE_PLAYMAKING_FIRST_SEASON = HUSTLE_STATS_FIRST_SEASON

PROVISIONAL_RECENCY_DECAY = 0.6
PROVISIONAL_SHRINKAGE_M = 500.0  # real total-minutes units, matches foul_discipline's own order of magnitude


def resolve_params() -> Tuple[float, float, str]:
    return PROVISIONAL_RECENCY_DECAY, PROVISIONAL_SHRINKAGE_M, "provisional"


@dataclass
class DefensivePlaymakingResult:
    seasons_used: List[SeasonEvidence]
    weighted_raw_rate: Optional[float]
    shrunk_rate: Optional[float]
    league_avg_rate: Optional[float]
    percentile_rating: Optional[float]
    total_weight: float
    param_source: str = "provisional"


def _player_season_evidence(name: str, season: str) -> Optional[Tuple[float, float]]:
    """(per-36 rate, real total minutes) for one real player-season, or None if no real evidence
    exists (a true 'no evidence', never a fabricated 0.0)."""
    try:
        teams = load_teams(season)
    except FileNotFoundError:
        return None
    player = None
    for team in teams.values():
        player = team.get_player(name)
        if player:
            break
    if player is None or player.min <= 0:
        return None
    adv_row = load_player_advanced_stats(season).get(name, {})
    total_min = adv_row.get("mpg", 0) * adv_row.get("gp", 0)
    if total_min <= 0:
        return None
    hustle_row = load_player_hustle_stats(season).get(name)
    deflections_per_game = hustle_row.get("deflections", 0.0) if hustle_row else 0.0
    per36 = (player.stl + player.blk + deflections_per_game) / player.min * 36.0
    return per36, total_min


def _player_evidence_by_season(name: str, seasons: List[str]) -> List[SeasonEvidence]:
    evidence = []
    for s in seasons:
        if s < DEFENSIVE_PLAYMAKING_FIRST_SEASON:
            continue
        result = _player_season_evidence(name, s)
        if result is None:
            continue
        rate, sample = result
        evidence.append(SeasonEvidence(season=s, rate=rate, sample=sample))
    return evidence


def _build_reference_population(as_of_season: str, all_seasons: List[str],
                                 min_gp: int = 20, min_mpg: float = 12.0) -> Tuple[List[float], Optional[float]]:
    """Real per-season rates for every qualifying real player-season THROUGH `as_of_season` ONLY --
    same `_seasons_through_cutoff` discipline and the same real min_gp/min_mpg floor
    `player_ability_estimation.py`'s own reference-population builder already uses, for
    consistency."""
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    values = []
    for s in seasons:
        if s < DEFENSIVE_PLAYMAKING_FIRST_SEASON:
            continue
        try:
            teams = load_teams(s)
        except FileNotFoundError:
            continue
        advanced = load_player_advanced_stats(s)
        hustle = load_player_hustle_stats(s)
        for team in teams.values():
            for player in team.players:
                adv_row = advanced.get(player.name)
                if not adv_row or adv_row.get("gp", 0) < min_gp or adv_row.get("mpg", 0) < min_mpg:
                    continue
                if player.min <= 0:
                    continue
                deflections_per_game = hustle.get(player.name, {}).get("deflections", 0.0) if hustle else 0.0
                per36 = (player.stl + player.blk + deflections_per_game) / player.min * 36.0
                values.append(per36)
    league_avg = sum(values) / len(values) if values else None
    return values, league_avg


def estimate_defensive_playmaking(name: str, as_of_season: str, all_seasons: List[str]) -> DefensivePlaymakingResult:
    """The one public entry point -- NAME-keyed (same as rim_protection/poa_containment/
    foul_discipline; an id-adapter is added separately in player_identity.py, matching precedent).
    Same no-future-leakage discipline as every other estimator in this project."""
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    seasons_evidence = _player_evidence_by_season(name, seasons)

    reference_values, league_avg = _build_reference_population(as_of_season, all_seasons)
    recency_decay, prior_strength, param_source = resolve_params()

    weighted_raw, shrunk, total_weight = _weighted_shrunk_estimate(
        seasons_evidence, as_of_season, prior_strength, league_avg, recency_decay=recency_decay,
    )
    rating = _percentile_rating(shrunk, reference_values) if shrunk is not None else None

    return DefensivePlaymakingResult(
        seasons_used=seasons_evidence, weighted_raw_rate=weighted_raw, shrunk_rate=shrunk,
        league_avg_rate=league_avg, percentile_rating=rating, total_weight=total_weight,
        param_source=param_source,
    )


def result_to_attribute_estimate(result: DefensivePlaymakingResult) -> AttributeEstimate:
    if result.percentile_rating is None:
        return AttributeEstimate()
    _, prior, _ = resolve_params()
    confidence = round(result.total_weight / (result.total_weight + prior), 3)
    return AttributeEstimate(
        value=result.percentile_rating, confidence=confidence, sample_size=int(round(result.total_weight)),
    )
