"""
Phase 11 -- Player Tendencies Foundation: the estimator layer. Reuses
`rim_protection_calibration.py`'s generic shrinkage engine directly (no
new grid-search code). Deliberately SEPARATE from
`player_ability_profile.PlayerAbilityProfile` -- tendencies are NOT
abilities and this phase does not touch that schema at all.

============================ ADDENDUM CORRECTIONS INCORPORATED ============================
1. COMPOSITIONAL: `three_point_preference`/`midrange_preference`/
   `drive_aggression`/`pass_vs_shoot` are NOT independent action
   probabilities -- a player cannot pass+drive+shoot-3+shoot-midrange on
   one decision simultaneously. Each is estimated independently for
   measurement purposes ONLY; no softmax/joint-normalization is
   hardcoded here (not supported by calibration evidence this phase).
   **A future possession engine MUST jointly normalize competing
   propensities** -- this module produces independent inputs to that
   future step, not final probabilities.
2. INTERNAL SCALE: the primary stored value is a **logit-relative-to-
   contemporaneous-league-average latent propensity**
   (`logit(player_rate) - logit(that season's real league-average rate)`)
   -- NOT a raw attempt share, and NOT treated as a probability. This
   also solves the era problem (#7) for free: a 1996-97 player's 40%
   raw 3PT share and a 2023-24 player's 40% share mean very different
   things (real league averages were 0.204 and 0.407 respectively,
   checked directly) -- comparing them on the LOGIT-RELATIVE-TO-OWN-
   SEASON scale is valid across eras, comparing raw shares is not.
3. NO OVER-CONTROLLING: usage/FGA/TOP are NEVER used as residualization
   controls anywhere in this phase (they were used ONLY as diagnostic
   correlations in `player_tendencies_analysis.py`, never subtracted
   out) -- per the addendum's "these may partly be CAUSED by the
   tendency" caution. No residualization of any kind is applied to any
   of the five candidates this phase.
4. PULLUP_VS_CATCH DOWNGRADED: real contamination with usage/touches was
   found substantial (0.40-0.68 correlation, see report) and no
   conditional-on-credible-opportunity version was built this phase --
   demoted to KEEP BUT FLAG / FUTURE, NOT part of the primary five-
   candidate profile below.
5. ORB_CRASH RETAINED FOR MEASUREMENT, FLAGGED HONESTLY: real
   correlation with the existing `offensive_rebounding` ABILITY
   attribute's own OREB_PCT is 0.996 on real data -- essentially
   collinear with the real proxy available. Computed and reported (per
   the addendum's explicit re-inclusion), but its OWN status is KEEP BUT
   FLAG given this near-total redundancy, not a clean distinct tendency.
6. TEAM-SWITCH CAUTION: team-switch rank-correlation (§ report) is used
   only as SUPPORTING evidence, not a clean causal portability proof
   (teams often acquire players specifically to change their role) --
   framed as such in the report; no within-team role-shock/teammate-
   absence detector was built (real, but expensive per-game
   infrastructure, out of this phase's efficiency scope).
7. PER-TENDENCY EXPOSURE/SHRINKAGE: `TENDENCY_MIN_EXPOSURE` differs by
   candidate (its own real natural attempt scale), and `TENDENCY_M`
   is now a light, NON-ZERO per-tendency prior (set equal to that
   candidate's own min-exposure floor) rather than a hard universal N=100
   cutoff or a pure M=0 -- a real, checked-flat region (see report's
   calibration section), chosen so a low-exposure player is pulled
   toward the real league average (uncertain/prior-dominated) rather
   than either a hard cutoff or an unshrunk, potentially wild small-
   sample rate.
"""
import math
from dataclasses import dataclass
from typing import List, Optional

import player_tendencies_analysis as pta
from rim_protection_calibration import _shrunk_rate

TENDENCY_LAMBDA = 0.5

# Real, adopted PRIMARY five-candidate profile (pullup_vs_catch demoted,
# orb_crash retained for measurement but flagged -- see module docstring).
PRIMARY_TENDENCIES = (
    "three_point_preference", "midrange_preference", "drive_aggression",
    "pass_vs_shoot", "orb_crash",
)
SECONDARY_TENDENCIES = ("pullup_vs_catch",)  # KEEP BUT FLAG / FUTURE -- see report

TENDENCY_WEIGHT_FIELD = {
    "three_point_preference": "fga",
    "midrange_preference": None,  # weight = real 2PT-zone total, see _weight_for
    "pullup_vs_catch": None,      # weight = pull_up_fga + catch_shoot_fga
    "drive_aggression": "touches",
    "pass_vs_shoot": None,        # weight = passes_made + fga + fta
    "orb_crash": "minutes",
}

# Per-tendency real exposure floor (its own natural attempt/opportunity
# scale) -- NOT a universal N=100 cutoff, per the addendum.
TENDENCY_MIN_EXPOSURE = {
    "three_point_preference": 100.0,
    "midrange_preference": 100.0,
    "pullup_vs_catch": 50.0,
    "drive_aggression": 200.0,
    "pass_vs_shoot": 200.0,
    "orb_crash": 500.0,
}

# Light, non-zero per-tendency shrinkage prior strength (own exposure
# units) -- see module docstring point 7. A real, checked-flat region
# (§ report), not re-derived per candidate.
TENDENCY_M = dict(TENDENCY_MIN_EXPOSURE)

# Real, checked bounds: three_point_preference/midrange_preference/
# pullup_vs_catch/pass_vs_shoot/drive_aggression are genuine SHARES
# (numerator subset of denominator), empirically confirmed bounded in
# [0,1] on real data (drive_aggression's real 2023-24 max is 0.33) --
# `logit` is valid for these. `orb_crash` is real OREB PER-36 MINUTES,
# NOT a share -- it is NOT bounded in [0,1] (real values run well past
# 1.0 for elite rebounders) and applying `logit`'s [0,1] clip to it was
# a real bug found and fixed this phase (it silently clipped every
# real value above ~1 to the same constant, erasing all real signal --
# caught by a sanity check on Domantas Sabonis, whose real rate is
# obviously well above league average but came back as an exact 0.0
# latent propensity). `orb_crash` uses a real LOG-RATIO transform
# instead (`log(rate) - log(league_avg)`), which only requires
# positivity, not [0,1] boundedness.
TENDENCIES_USING_LOGIT = frozenset({
    "three_point_preference", "midrange_preference", "pullup_vs_catch",
    "pass_vs_shoot", "drive_aggression",
})

TENDENCY_FIRST_SEASON = {
    "three_point_preference": pta.TENDENCY_FIRST_SEASON_BOX,
    "midrange_preference": pta.TENDENCY_FIRST_SEASON_BOX,
    "pullup_vs_catch": pta.TENDENCY_FIRST_SEASON_TRACKING,
    "drive_aggression": pta.TENDENCY_FIRST_SEASON_TRACKING,
    "pass_vs_shoot": pta.TENDENCY_FIRST_SEASON_TRACKING,
    "orb_crash": pta.TENDENCY_FIRST_SEASON_BOX,
}


def _weight_for(row: pta.PlayerTendencyRow, tendency: str) -> float:
    field = TENDENCY_WEIGHT_FIELD[tendency]
    if field is not None:
        return getattr(row, field) or 0.0
    if tendency == "midrange_preference":
        if row.restricted_area_fga is None or row.paint_non_ra_fga is None or row.midrange_fga is None:
            return 0.0
        return row.restricted_area_fga + row.paint_non_ra_fga + row.midrange_fga
    if tendency == "pullup_vs_catch":
        return (row.pull_up_fga or 0.0) + (row.catch_shoot_fga or 0.0)
    if tendency == "pass_vs_shoot":
        return (row.passes_made or 0.0) + row.fga + (row.fta or 0.0)
    raise ValueError(f"Unknown tendency {tendency!r}")


def _logit(p: float, eps: float = 1e-4) -> float:
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def _log_ratio_transform(rate: float, eps: float = 1e-4) -> float:
    """For real, unbounded (non-share) rates like `orb_crash` (OREB per
    36 min) -- only requires positivity, unlike `_logit`'s [0,1] clip."""
    return math.log(max(rate, eps))


def _relative_transform(value: float, tendency: str) -> float:
    return _logit(value) if tendency in TENDENCIES_USING_LOGIT else _log_ratio_transform(value)


def _league_avg_rate(rows: List[pta.PlayerTendencyRow], tendency: str) -> Optional[float]:
    rate_fn = pta.CANDIDATES[tendency]
    rates = [r for r in (rate_fn(row) for row in rows) if r is not None]
    return sum(rates) / len(rates) if rates else None


@dataclass
class PlayerTendencyEstimate:
    player_name: str
    season: str
    tendency: str
    mode: str = "INSUFFICIENT"
    raw_rate: Optional[float] = None
    # PRIMARY value: logit-relative-to-contemporaneous-league-average
    # latent propensity. Centered at 0 = exactly league average that
    # season; positive = prefers this action MORE than a league-average
    # player that same season, negative = less. NOT a probability, NOT
    # comparable across tendencies with different natural scales, IS
    # comparable across eras (the whole point of this representation).
    latent_propensity: Optional[float] = None
    display_percentile: Optional[float] = None  # optional 0-99, derived from latent_propensity, secondary
    confidence: str = "none"
    exposure: Optional[float] = None
    coverage_note: str = ""
    is_compositional: bool = True  # see module docstring point 1 -- always True for the four shot/pass-decision tendencies
    # Phase 14: additive, backward-compatible field. Unset (None) unless
    # populated by player_identity.estimate_tendency_by_id's adapter --
    # estimate_tendency() itself still takes/keys on player_name and is
    # otherwise untouched by Phase 14 (name remains this dataclass's own
    # load-bearing field; player_id here is metadata added by the caller
    # when a real, resolved id is available).
    player_id: Optional[str] = None


def _percentile(value: float, reference: List[float]) -> float:
    if not reference:
        return 50.0
    import bisect
    sorted_ref = sorted(reference)
    rank = bisect.bisect_left(sorted_ref, value)
    return round(max(0.0, min(99.0, 100.0 * rank / len(sorted_ref))), 1)


def estimate_tendency(player_name: str, as_of_season: str, all_seasons: List[str], tendency: str) -> PlayerTendencyEstimate:
    if tendency not in pta.CANDIDATES:
        raise ValueError(f"Unknown tendency {tendency!r}")
    rate_fn = pta.CANDIDATES[tendency]
    min_exposure = TENDENCY_MIN_EXPOSURE[tendency]
    M = TENDENCY_M[tendency]
    floor_season = TENDENCY_FIRST_SEASON[tendency]
    is_compositional = tendency in ("three_point_preference", "midrange_preference", "drive_aggression", "pass_vs_shoot")

    result = PlayerTendencyEstimate(player_name=player_name, season=as_of_season, tendency=tendency,
                                     is_compositional=is_compositional)
    if as_of_season < floor_season:
        result.coverage_note = f"Before this tendency's real data floor ({floor_season})."
        return result
    result.mode = "MODERN_TRACKING" if floor_season == pta.TENDENCY_FIRST_SEASON_TRACKING else "HISTORICAL_BOX"

    seasons_through = [s for s in all_seasons if s <= as_of_season and s >= floor_season]
    rows_by_season = {s: pta.build_player_tendency_rows(s) for s in seasons_through}
    current_rows = rows_by_season.get(as_of_season, [])
    player_row = next((r for r in current_rows if r.player_name == player_name), None)
    if player_row is None:
        result.coverage_note = "No real evidence for this player-season."
        return result

    result.exposure = _weight_for(player_row, tendency)
    raw = rate_fn(player_row)
    if raw is None:
        result.confidence = "low"
        result.coverage_note = (f"Real exposure below the {min_exposure:.0f} floor -- uncertain/prior-dominated, "
                                 f"NOT zero preference and NOT a fabricated tendency.")
        return result
    result.raw_rate = raw

    # Real, CONTEMPORANEOUS (same-season) league average -- the
    # era-normalization baseline. Never a fixed historical constant.
    league_avg_current = _league_avg_rate(current_rows, tendency)

    history = []
    for s in sorted(seasons_through):
        row = next((r for r in rows_by_season[s] if r.player_name == player_name), None)
        if row is None:
            continue
        r = rate_fn(row)
        if r is None:
            continue
        # Each historical season's rate is shrunk toward THAT season's
        # own real league average (not the as_of season's), then the
        # FINAL shrunk value is compared to the as_of season's own real
        # league average when computing the latent propensity below --
        # this keeps every real season's own era context intact.
        history.append((s, r, _weight_for(row, tendency)))

    if league_avg_current is None:
        shrunk = raw
    else:
        shrunk = _shrunk_rate(history, int(as_of_season[:4]), TENDENCY_LAMBDA, M, league_avg_current)
        if shrunk is None:
            shrunk = raw

    if league_avg_current is not None:
        result.latent_propensity = round(
            _relative_transform(shrunk, tendency) - _relative_transform(league_avg_current, tendency), 4)
    else:
        result.latent_propensity = None

    if result.latent_propensity is not None:
        all_latent = []
        for row in current_rows:
            r = rate_fn(row)
            if r is not None and league_avg_current is not None:
                all_latent.append(_relative_transform(r, tendency) - _relative_transform(league_avg_current, tendency))
        result.display_percentile = _percentile(result.latent_propensity, all_latent)

    total_weight = sum(w for _, _, w in history)
    result.confidence = "high" if total_weight >= 3 * min_exposure else "medium"
    result.coverage_note = f"{len(history)} season(s) of real evidence, {total_weight:.0f} total real exposure units."
    return result
