"""
First offline ATTRIBUTE ESTIMATION prototype for PlayerAbilityProfile.

Produces a small subset of SKILL_ATTRIBUTES (see player_ability_profile.py)
from real, multi-year historical evidence -- NOT single-season production,
and NOT contaminated by team success (no win%, no PIE, no net_rating, no
awards.mvp_score/dpoy_score, no ratings.py Overall). This file is entirely
OFFLINE: nothing here is imported by season.py, game_engine.py, awards.py,
transactions.py, models.py, or ratings.py, and nothing here writes to any
cache/*.json file or a database.

============================ DATA INSPECTED FIRST ============================
Checked directly, before writing any formula (per this phase's own
requirement not to assume a stat exists):

- loader.load_teams -> real per-game Player fields (min, fgm, fga, fg3m,
  fg3a, ftm, fta, reb, oreb, ast, stl, blk, tov, pf) -- every cached season,
  1996-97 to 2025-26.
- player_advanced.json -> pie, ts_pct, usg_pct, off/def/net_rating, ast_pct,
  reb_pct (COMBINED offensive+defensive, no separate split), gp, mpg, age --
  same season range.
- player_hustle.json -> deflections, charges_drawn -- ONLY 2016-17+ (real
  hustle-stat camera-tracking floor, same one ratings.py already documents).
- player_rim_defense.json / player_perimeter_defense.json -> BOTH are
  DEFENSIVE metrics only (real opponent shooting AGAINST this player when he
  defends) -- 2013-14+. Checked directly: there is no offensive shot-zone /
  shot-location fetcher anywhere in data_source.py at all. RIM_FINISHING IS
  THEREFORE LEFT UNESTIMATED IN THIS PROTOTYPE -- no real evidence exists in
  this codebase for a player's own rim-finishing percentage, and inventing
  one from generic FG% would not be an honest per-zone estimate.

============================ WHAT ISN'T HERE YET ============================
Per this phase's explicit stop conditions: no OVR, no role classification,
no simulation integration, no expansion to all 18 attributes. Only:
three_point, free_throw, passing, ball_security, offensive_rebounding,
defensive_rebounding, defensive_playmaking.
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from loader import load_teams, load_player_advanced_stats, load_player_rebound_splits
from player_ability_profile import AttributeEstimate

# =====================================================================
# PROVISIONAL CONFIGURATION -- explicitly not empirically calibrated.
# Nothing else in this codebase has already derived "the right" values
# for cross-season recency decay or shrinkage strength for THESE
# specific rates (unlike, say, ratings.py's SHRINK_MINUTES=1000, which
# was tuned against a real, checked failure -- Moritz Wagner). These
# are conservative, documented starting points only. Recalibrating
# them against a real backtest (e.g. "does this attribute predict next
# season's own rate better than raw single-season rate does") is
# explicitly named as future work in the final report, not done here.
# =====================================================================

# Per season of AGE (as_of_year - that_season's_year), how much a
# season's weight decays. 0.6 means a season 1 year old counts 60% as
# much as the current one, 2 years old 36%, etc. -- a real, standard
# sports-analytics shape (recent form matters more), not derived from
# this project's own data.
RECENCY_DECAY = 0.6

# Real total plays behind an attribute's own league-average, used as
# the shrinkage prior's weight (same "regression toward the mean"
# formula as ratings.py's _shrink, just attribute-specific rather than
# reusing ratings.py's SHRINK_MINUTES for everything). Chosen as a
# round, conservative number for each attribute's own typical sample
# scale -- NOT swept against a real backtest. Provisional.
SHRINKAGE_PRIOR_STRENGTH: Dict[str, float] = {
    "three_point": 200.0,   # real 3PA -- three-point volume is often thin for non-shooters
    "free_throw": 125.0,    # real FTA -- FT% stabilizes faster than 3PT%, per the task's own guidance
    "passing": 1500.0,      # real total minutes -- ast_pct is already a rate, shrink on playing time
    "ball_security": 1500.0,  # real estimated plays
    "offensive_rebounding": 1500.0,  # real total minutes
    "defensive_rebounding": 1500.0,
    "defensive_playmaking": 1500.0,  # real total minutes
}

RATING_MIN, RATING_MAX = 0.0, 99.0


def resolve_params(attribute: str) -> Tuple[float, float, str]:
    """
    (recency_decay, prior_strength, source) for `attribute` -- prefers
    the empirically calibrated value (see
    player_ability_calibration.py) when one exists, falls back to this
    module's own provisional constants otherwise. `source` is
    "calibrated" or "provisional", reported by estimate_attribute so
    callers/tests can see exactly which one was actually used.

    ball_security is deliberately NEVER calibrated this phase (no
    entry exists in the calibration artifact for it) -- always
    resolves to "provisional" until a future phase replaces its
    total-TOV proxy with real turnover-subtype evidence.
    """
    from player_ability_calibration import get_calibrated_params
    calibrated = get_calibrated_params(attribute)
    if calibrated is not None:
        return calibrated.lambda_, calibrated.M, "calibrated"
    return RECENCY_DECAY, SHRINKAGE_PRIOR_STRENGTH[attribute], "provisional"


@dataclass
class SeasonEvidence:
    """One season's raw rate + real sample size for one attribute --
    the un-weighted, un-shrunk input. Kept around so the diagnostic
    report can show its work (raw evidence -> weighted -> shrunk)."""
    season: str
    rate: float
    sample: float  # real attempts/plays/minutes behind `rate` -- unit varies by attribute, documented per function
    # "true" (real OREB_PCT/DREB_PCT), "fallback" (combined REB% split),
    # or "n/a" (attributes with no such distinction) -- see
    # _extract_off_rebounding/_extract_def_rebounding. Reported per
    # season so the diagnostic can show exactly which seasons used real
    # data vs the approximation, not just an aggregate label.
    mode: str = "n/a"


@dataclass
class EstimationResult:
    attribute: str
    seasons_used: List[SeasonEvidence]
    weighted_raw_rate: Optional[float]   # None if literally zero real evidence across every season
    shrunk_rate: Optional[float]
    league_avg_rate: Optional[float]
    percentile_rating: Optional[float]   # 0-99 scale, None if weighted_raw_rate is None
    total_weight: float
    param_source: str = "provisional"   # "calibrated" or "provisional" -- see resolve_params
    age_adjusted: bool = False   # True if a real v2 age correction was actually applied (opt-in -- see estimate_attribute)

    @property
    def evidence_mode(self) -> str:
        """One-word summary for the diagnostic report: TRUE if every
        season used real OREB_PCT/DREB_PCT, FALLBACK if any season had
        to use the combined-reb_pct-split approximation, MISSING if no
        evidence was found at all, N/A for attributes with no such
        distinction (see ATTRIBUTES_NEEDING_REBOUND_SPLITS)."""
        if self.attribute not in ATTRIBUTES_NEEDING_REBOUND_SPLITS:
            return "N/A"
        if not self.seasons_used:
            return "MISSING"
        modes = {ev.mode for ev in self.seasons_used}
        if modes == {"true"}:
            return "TRUE"
        if "fallback" in modes:
            return "FALLBACK"
        return "MIXED"


def _season_year(season: str) -> int:
    return int(season[:4])


def _seasons_through_cutoff(as_of_season: str, all_seasons: List[str]) -> List[str]:
    """Every real cached season up to and including `as_of_season` --
    the ONE place leakage is prevented. Every caller in this file goes
    through this function rather than filtering ad hoc."""
    cutoff_year = _season_year(as_of_season)
    return [s for s in all_seasons if _season_year(s) <= cutoff_year]


def _weighted_shrunk_estimate(
    seasons_evidence: List[SeasonEvidence], as_of_season: str, prior_strength: float,
    league_avg_rate: Optional[float], recency_decay: float = RECENCY_DECAY,
) -> Tuple[Optional[float], Optional[float], float]:
    """
    Core multi-year estimator, shared by every attribute below:
    recency-weight each season by its own real sample size, combine,
    then shrink toward `league_avg_rate` using `prior_strength` as the
    prior's own weight (same Bayesian-shrinkage shape as ratings.py's
    _shrink, generalized to real multi-season pooling).

    `recency_decay` defaults to the module's universal provisional
    constant but is attribute-specific once empirically calibrated --
    see resolve_params() and player_ability_calibration.py.

    Returns (weighted_raw_rate, shrunk_rate, total_weight). Both rates
    are None if total_weight is 0 (no real evidence at all -- NEVER
    silently returns 0.0, matching AttributeEstimate's own contract).
    """
    as_of_year = _season_year(as_of_season)
    total_weight = 0.0
    weighted_sum = 0.0
    for ev in seasons_evidence:
        if ev.sample <= 0:
            continue  # zero real attempts/plays that season -- contributes no evidence, not a 0.0 rate
        age = as_of_year - _season_year(ev.season)
        recency_w = recency_decay ** max(age, 0)
        w = recency_w * ev.sample
        weighted_sum += ev.rate * w
        total_weight += w

    if total_weight <= 0:
        return None, None, 0.0

    weighted_raw_rate = weighted_sum / total_weight

    if league_avg_rate is None:
        return weighted_raw_rate, weighted_raw_rate, total_weight  # nothing to shrink toward -- report raw as-is

    shrunk = (weighted_sum + prior_strength * league_avg_rate) / (total_weight + prior_strength)
    return weighted_raw_rate, shrunk, total_weight


def _percentile_rating(value: float, reference_values: List[float]) -> float:
    """Maps `value` onto a 0-99 scale by its real percentile within
    `reference_values` -- a real historical distribution, NOT an
    invented formula, and (see callers) built ONLY from seasons up to
    the same as_of_season cutoff, so this never leaks future-season
    shape into a past `as_of_season` profile. 50 = exactly league
    average by construction. Bounded, ordering-preserving (a strictly
    higher rate always maps to a >= rating)."""
    if not reference_values:
        return 50.0  # no reference population at all (e.g. the very first cached season) -- neutral, documented
    sorted_ref = sorted(reference_values)
    n = len(sorted_ref)
    import bisect
    rank = bisect.bisect_left(sorted_ref, value)
    percentile = 100.0 * rank / n
    return round(max(RATING_MIN, min(RATING_MAX, percentile)), 1)


# =====================================================================
# Per-attribute raw evidence extraction. Each returns a SeasonEvidence
# per real cached season a player appears in (only seasons with a real
# stat line -- rosters.json's own "no honest answer" rule).
# =====================================================================

def _player_evidence_by_season(name: str, seasons: List[str],
                                extract: Callable[[object, dict, Optional[dict]], Optional[Tuple[float, float, str]]],
                                needs_rebound_splits: bool = False) -> List[SeasonEvidence]:
    """Shared loop: for each real season, load real teams + advanced
    stats ONCE, find `name`'s real Player + advanced-stats row, and
    call `extract(player, advanced_row, rebound_row) -> (rate, sample, mode) or None`.
    `rebound_row` (real TRUE OREB_PCT/DREB_PCT for this season, or None)
    is only loaded/passed when `needs_rebound_splits` -- every other
    attribute's extractor ignores the third argument entirely."""
    evidence = []
    for s in seasons:
        try:
            teams = load_teams(s)
        except FileNotFoundError:
            continue
        advanced = load_player_advanced_stats(s)
        player = None
        for team in teams.values():
            player = team.get_player(name)
            if player:
                break
        if player is None:
            continue
        adv_row = advanced.get(name, {})
        rebound_row = None
        if needs_rebound_splits:
            rebound_row = load_player_rebound_splits(s).get(name)
        result = extract(player, adv_row, rebound_row)
        if result is None:
            continue
        rate, sample, mode = result
        if sample > 0:
            evidence.append(SeasonEvidence(season=s, rate=rate, sample=sample, mode=mode))
    return evidence


def _extract_three_point(player, adv_row, rebound_row=None):
    if player.fg3a <= 0:
        return None
    gp = adv_row.get("gp", 0)
    return player.fg3_pct, player.fg3a * gp, "n/a"  # real total season attempts


def _extract_free_throw(player, adv_row, rebound_row=None):
    if player.fta <= 0:
        return None
    gp = adv_row.get("gp", 0)
    return player.ft_pct, player.fta * gp, "n/a"


def _extract_passing(player, adv_row, rebound_row=None):
    ast_pct = adv_row.get("ast_pct")
    if ast_pct is None:
        return None
    total_min = adv_row.get("mpg", 0) * adv_row.get("gp", 0)
    return ast_pct, total_min, "n/a"


def _extract_ball_security(player, adv_row, rebound_row=None):
    """TOV% approximation using the standard real formula this
    project's own data already supports: TOV / (FGA + 0.44*FTA + AST +
    TOV) -- 0.44 is the real, standard "how many FTA-equivalent
    possessions one FT trip represents" constant already used
    elsewhere in basketball analytics (and matches this project's own
    real-pace formula in game_engine.py). LIMITATION, stated plainly:
    this is box-score-derived, not real touches/dribbles data -- this
    project has no touch-tracking, so it cannot fully separate "poor
    ball security" from "high defensive pressure faced" or "role
    requiring more live-ball touches." Lower tov_rate = better ball
    security, so the RATE stored here is INVERTED (1 - tov_rate) so
    that, like every other attribute, higher = better.

    See player_ability_turnover_prototype.py for the investigation
    into a TRUE handling-error-only numerator (Lost Ball/Traveling,
    excluding Bad Pass/Offensive Foul) -- confirmed real in the NBA's
    own play-by-play text, NOT swapped in here yet: it requires real
    per-game play-by-play parsing across the whole season range, which
    this phase's own scope explicitly excludes ("avoid giant data/
    research jobs"). This box-score proxy remains the production path
    until that's built out as a real, season-covering ingestion job."""
    plays = player.fga + 0.44 * player.fta + player.ast + player.tov
    if plays <= 0:
        return None
    gp = adv_row.get("gp")  # plays above is a per-game figure; scale to season total using real gp
    if not gp:
        return None
    tov_rate = player.tov / plays
    return 1.0 - tov_rate, plays * gp, "n/a"


def _extract_off_rebounding(player, adv_row, rebound_row=None):
    """Prefers TRUE real OREB_PCT (rebound_row, from
    data_source.build_and_cache_player_rebound_splits) when available
    -- mode="true". Falls back to the combined-reb_pct-split
    APPROXIMATION only when the true value is missing for this season
    -- mode="fallback", never silent. See the module docstring for
    where true OREB_PCT/DREB_PCT come from and their real season
    coverage."""
    total_min = adv_row.get("mpg", 0) * adv_row.get("gp", 0)
    if rebound_row is not None and rebound_row.get("oreb_pct") is not None:
        return rebound_row["oreb_pct"], total_min, "true"
    reb_pct = adv_row.get("reb_pct")
    if reb_pct is None or player.reb <= 0:
        return None
    # FALLBACK APPROXIMATION, stated plainly: splits the one combined
    # reb_pct by this player's own real oreb/total-reb share. Assumes
    # his real O/D rebound MIX is a fair way to divide his one real
    # opportunity-normalized rate; not a real separately-measured ORB%.
    oreb_share = player.oreb / player.reb
    return reb_pct * oreb_share, total_min, "fallback"


def _extract_def_rebounding(player, adv_row, rebound_row=None):
    """Mirror of _extract_off_rebounding -- see its docstring."""
    total_min = adv_row.get("mpg", 0) * adv_row.get("gp", 0)
    if rebound_row is not None and rebound_row.get("dreb_pct") is not None:
        return rebound_row["dreb_pct"], total_min, "true"
    reb_pct = adv_row.get("reb_pct")
    if reb_pct is None or player.reb <= 0:
        return None
    dreb_share = player.dreb / player.reb
    return reb_pct * dreb_share, total_min, "fallback"


def _extract_def_playmaking(player, adv_row, rebound_row=None):
    """STL+BLK per-36 -- disruptive defensive EVENTS only, per this
    phase's explicit scope ("do not treat this as complete overall
    defense"). Deflections deliberately EXCLUDED: checked and fixed
    once already this project (ratings.py's Defense category) --
    deflections only exist 2016-17+, and letting them into a pooled
    multi-era signal creates a real, measured era jump with no actual
    change in ability. Same lesson, applied here before it could
    become the same bug."""
    if player.min <= 0:
        return None
    per36 = (player.stl + player.blk) / player.min * 36.0
    total_min = adv_row.get("mpg", 0) * adv_row.get("gp", 0)
    if total_min <= 0:
        return None
    return per36, total_min, "n/a"


ATTRIBUTE_EXTRACTORS: Dict[str, Callable] = {
    "three_point": _extract_three_point,
    "free_throw": _extract_free_throw,
    "passing": _extract_passing,
    "ball_security": _extract_ball_security,
    "offensive_rebounding": _extract_off_rebounding,
    "defensive_rebounding": _extract_def_rebounding,
    "defensive_playmaking": _extract_def_playmaking,
}

# Attributes whose extractor needs real TRUE OREB_PCT/DREB_PCT looked
# up per season (see _extract_off_rebounding/_extract_def_rebounding).
# Every other attribute skips that extra real per-season file read.
ATTRIBUTES_NEEDING_REBOUND_SPLITS = frozenset({"offensive_rebounding", "defensive_rebounding"})


def _build_reference_population(attribute: str, as_of_season: str, all_seasons: List[str],
                                 min_gp: int = 20, min_mpg: float = 12.0) -> Tuple[List[float], Optional[float]]:
    """
    Real single-season rates for every qualifying player-season THROUGH
    `as_of_season` ONLY -- the leak-free reference distribution this
    attribute's percentile mapping and shrinkage-prior league average
    are both built from. Same RATING_MIN_GAMES/MPG floor ratings.py
    already uses, for consistency, not re-derived.

    NOTE on cost: this re-walks real cached seasons up to the cutoff
    every call -- acceptable for this prototype's small diagnostic
    sample (per this phase's "avoid giant data/research jobs"
    instruction), not something this file tries to cache/optimize.
    """
    extractor = ATTRIBUTE_EXTRACTORS[attribute]
    needs_splits = attribute in ATTRIBUTES_NEEDING_REBOUND_SPLITS
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    values = []
    for s in seasons:
        try:
            teams = load_teams(s)
        except FileNotFoundError:
            continue
        advanced = load_player_advanced_stats(s)
        splits = load_player_rebound_splits(s) if needs_splits else {}
        for team in teams.values():
            for player in team.players:
                adv_row = advanced.get(player.name)
                if not adv_row or adv_row.get("gp", 0) < min_gp or adv_row.get("mpg", 0) < min_mpg:
                    continue
                rebound_row = splits.get(player.name) if needs_splits else None
                result = extractor(player, adv_row, rebound_row)
                if result is None:
                    continue
                rate, sample, _mode = result
                if sample > 0:
                    values.append(rate)
    league_avg = sum(values) / len(values) if values else None
    return values, league_avg


def estimate_attribute(name: str, as_of_season: str, attribute: str, all_seasons: List[str],
                        apply_age_adjustment: bool = False) -> EstimationResult:
    """
    The one public entry point: a single attribute, for a single
    player, as of a single season -- strictly no evidence from any
    season after `as_of_season` anywhere in this function or anything
    it calls (see _seasons_through_cutoff).

    `apply_age_adjustment` defaults to False -- OPT IN only. When
    True, applies the real, held-out-validated v2 age correction (see
    player_ability_calibration.py's apply_age_adjustment and
    player_ability_calibration_v2.json) for the four attributes it
    actually helped; the other two (both rebounding attributes) have
    no adjustment on file (a real, tested null result), so passing
    True for those is always a safe no-op. Default stays False so
    every existing caller's behavior is UNCHANGED unless it explicitly
    opts in -- this phase validates the age model, it does not silently
    turn it on everywhere.
    """
    if attribute not in ATTRIBUTE_EXTRACTORS:
        raise ValueError(f"No estimator implemented for {attribute!r} in this prototype")
    extractor = ATTRIBUTE_EXTRACTORS[attribute]
    seasons = _seasons_through_cutoff(as_of_season, all_seasons)
    seasons_evidence = _player_evidence_by_season(
        name, seasons, extractor, needs_rebound_splits=attribute in ATTRIBUTES_NEEDING_REBOUND_SPLITS,
    )

    reference_values, league_avg = _build_reference_population(attribute, as_of_season, all_seasons)
    recency_decay, prior_strength, param_source = resolve_params(attribute)

    weighted_raw, shrunk, total_weight = _weighted_shrunk_estimate(
        seasons_evidence, as_of_season, prior_strength, league_avg, recency_decay=recency_decay,
    )

    age_adjusted = False
    if apply_age_adjustment and shrunk is not None:
        from player_ability_calibration import apply_age_adjustment as _apply_age_adj
        player_age = load_player_advanced_stats(as_of_season).get(name, {}).get("age")
        adjusted = _apply_age_adj(attribute, shrunk, player_age)
        if adjusted != shrunk:
            age_adjusted = True
        shrunk = adjusted

    rating = _percentile_rating(shrunk, reference_values) if shrunk is not None else None

    return EstimationResult(
        attribute=attribute, seasons_used=seasons_evidence,
        weighted_raw_rate=weighted_raw, shrunk_rate=shrunk,
        league_avg_rate=league_avg, percentile_rating=rating, total_weight=total_weight,
        param_source=param_source, age_adjusted=age_adjusted,
    )


def result_to_attribute_estimate(result: EstimationResult) -> AttributeEstimate:
    """Bridges an EstimationResult into the foundational
    player_ability_profile.AttributeEstimate shape."""
    if result.percentile_rating is None:
        return AttributeEstimate()  # UNESTIMATED -- no real evidence found, never a guessed 0.0
    # Confidence is a simple, provisional, DOCUMENTED-AS-SUCH function
    # of real total weight relative to this attribute's own shrinkage
    # prior -- more real evidence relative to the prior => more
    # confidence. Not empirically validated against anything; a real
    # confidence model is future work (see the final report).
    _, prior, _ = resolve_params(result.attribute)
    confidence = round(result.total_weight / (result.total_weight + prior), 3)
    return AttributeEstimate(
        value=result.percentile_rating, confidence=confidence,
        sample_size=int(round(result.total_weight)),
    )
