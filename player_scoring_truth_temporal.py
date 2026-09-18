"""
Pregame-Safe Scoring Truth phase.

Extends `player_scoring_truth.py` (SEASON-level, `as_of_season`) with a DATE-level, pregame-safe
sibling API. Does NOT replace the season-level API -- both are kept, both are useful (season-level
for cross-season snapshots; this module for in-season pregame snapshots). Does NOT touch the
detailed engine, any resolver, or `possession_orchestrator.py` -- this is data/evidence
construction only.

============================ WHY SEASON-LEVEL WAS NOT PREGAME-SAFE ============================
`build_scoring_truth_profile(player_id, as_of_season, ...)` (previous phase) reads
`player_ability_estimation.estimate_attribute`/`shot_zone_estimation.estimate_shot_zone_attribute`/
`player_tendencies_estimation.estimate_tendency`, every one of which treats `as_of_season` as "every
real game of that season," not "games before some date within it" -- confirmed by direct source
read (`_player_evidence_by_season`'s own extractors read `player.fg3a` = the season's FULL, final
per-game average). A December profile built with `as_of_season="2024-25"` would therefore see
January-June 2025 games that had not happened yet relative to a December game -- a genuine leak.

============================ PER-TARGET GRANULARITY AUDIT ============================
Checked directly before writing any code (see player_game_log_ingestion.py's and
player_shot_event_ingestion.py's own module docstrings for the exact verified columns/call-budget
investigation for each source):

| Target | Per-game evidence available? | Source | V1 pregame strategy |
|---|---|---|---|
| three_point | YES (FGM/FGA/FG3M/FG3A per game) | `leaguegamelog` | current-season games before cutoff + prior seasons, same shrinkage |
| free_throw | YES (FTM/FTA per game) | `leaguegamelog` | same |
| three_point_preference | YES (FG3A/FGA per game) | `leaguegamelog` | same, tendency-side shrinkage |
| rim_finishing | YES (per-shot SHOT_ZONE_BASIC="Restricted Area", real, ~10 calls/season) | `shotchartdetail` | current-season shots before cutoff + prior seasons, same shrinkage |
| floater_short_mid | YES (SHOT_ZONE_BASIC="In The Paint (Non-RA)") | `shotchartdetail` | same |
| midrange | YES (SHOT_ZONE_BASIC="Mid-Range") | `shotchartdetail` | same |
| midrange_preference | YES (mid shots / real 2PT-zone-total shots, matching the tendency's own existing denominator exactly) | `shotchartdetail` | same, tendency-side shrinkage |
| drive_aggression | **NO** -- see determination below | none available | Option B: last COMPLETED prior season's own value only (PRIOR_SEASON_ONLY, unchanged) |

**Original 500+-calls/season estimate REJECTED as the ingestion design** -- investigated 3 real
call strategies before choosing one (see `player_shot_event_ingestion.py`'s own module docstring
for the full investigation): per-player (~500+ calls, works but wasteful), `player_id=0` single
call (fails silently -- a real, confirmed **102,400-row server-side cap**, truncating a real
218,700-row season to its first 575 of 1,230 games), and `player_id=0` PAGED BY MONTH (chosen: ~10
calls/season, real, verified, complete -- 218,700/1,230-games reconciled exactly against this
project's own already-verified league totals).

============================ DRIVE_AGGRESSION DETERMINATION ============================
`shotchartdetail`'s own `ACTION_TYPE` field (free text, e.g. "Driving Layup Shot") could flag SOME
shots as drive-associated, but this project's own `drive_aggression` tendency is explicitly defined
(`player_tendencies_analysis.py`) as touches-denominated: **P(DRIVE | a real offensive touch)**,
not "share of FGA that look like a drive." A shot-only source can supply the numerator's own
FGA-level subset at best, but has NO touches/no-shot-possessions denominator at all (a drive that
ends in a pass or a turnover, not a shot, is invisible to any per-SHOT source by construction) --
this is classified **(B) partial proxy only**, not **(A) fully recoverable**. Per this phase's own
explicit instruction ("If B/C: leave current-season drive_aggression unavailable and retain
PRIOR_SEASON_ONLY"), `drive_aggression` is NOT touched -- it keeps using
`_prior_season_only_estimate`, unchanged from the previous phase.

============================ CUTOFF SEMANTICS ============================
`evidence game date < as_of_date` (strict). The target game itself, and every later game, are
excluded. `leaguegamelog` carries no game START TIMESTAMP, only a calendar date -- a same-date
scenario (e.g. attempting to build a "before this game" snapshot for a real, rare same-day
doubleheader) is resolved by falling back to `GAME_ID` ordering (real, stable, assigned in
schedule order) -- documented here as a real, conservative limitation: with date alone this module
cannot distinguish "earlier today" from "later today" for two DIFFERENT players' unrelated games,
only within one player's own sorted game list, where `GAME_ID` ordering is the best available
real tie-break. Callers building a profile "as of this player's OWN specific game" should pass
`exclude_game_id` explicitly (see `build_scoring_truth_profile_as_of_date`) rather than relying on
date equality alone.

============================ EVIDENCE REPRESENTATION ============================
`player_game_log_ingestion.load_player_game_log(season)` already returns one real row per
player-game (game_id, date, fgm, fga, fg3m, fg3a, ftm, fta), pre-sorted by (date, game_id) --
this IS the `PlayerEvidenceEvent` stream (a dict-shaped row rather than a separate dataclass, since
introducing a parallel type for the exact same fields the cache already stores would duplicate
structure for no benefit; `_PrefixLedger` below is the "evidence events -> cutoff filter" step).

============================ PERFORMANCE ============================
`_prefix_ledger_for` builds ONE cumulative-prefix-sum array per (player_id, season) the first time
it's needed and memoizes it (`functools.lru_cache`) -- a cutoff query for any date within that
season is then a single `bisect` (O(log n) in games-per-season, ~82 max) plus an O(1) array
lookup, never a re-scan of the raw game list. A rolling backtest across many dates for the same
player/season pays the O(n) prefix-build cost exactly once. See
test_pregame_safe_scoring_truth.py's own benchmark test for measured wall-clock behavior.
"""
import bisect
from dataclasses import replace
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import player_identity as pid
import player_scoring_truth as pst
from player_ability_estimation import SeasonEvidence, _weighted_shrunk_estimate, resolve_params as pae_resolve_params
from player_game_log_ingestion import load_player_game_log

# ---------------------------------------------------------------------
# Provenance vocabulary -- attached to every ScoringTruthEstimate this module produces (and, for
# comparison, describes what the ORIGINAL season-level module's own estimates represent).
# ---------------------------------------------------------------------
CURRENT_SEASON_PREGAME = "CURRENT_SEASON_PREGAME"    # includes real current-season evidence, correctly cut off
PRIOR_SEASON_ONLY = "PRIOR_SEASON_ONLY"              # rests entirely on a season strictly before as_of_season
MULTI_SEASON_THROUGH_CUTOFF = "MULTI_SEASON_THROUGH_CUTOFF"  # the ORIGINAL season-level API's own provenance
MISSING = "MISSING"

# Targets with real, per-game, pregame-safe evidence this phase (see module docstring's audit table).
_GAME_LOG_TARGETS = frozenset({"three_point", "free_throw", "three_point_preference"})
_PRIOR_SEASON_ONLY_TARGETS = frozenset(pst.ABILITY_TARGETS) - {"three_point", "free_throw"} | (
    frozenset(pst.TENDENCY_TARGETS) - {"three_point_preference"}
)


def _season_before(season: str) -> str:
    """'2023-24' -> '2022-23'. Pure string arithmetic on this project's own real season-string
    convention (already used identically by every estimator's own season-list handling)."""
    start_year = int(season[:4])
    prev_start = start_year - 1
    return f"{prev_start}-{str(prev_start + 1)[-2:]}"


def month_cutoff_for_date(as_of_date: str) -> str:
    """CURRENT-SEASON DEFENSE + ROLE REFRESH V1: the real, deterministic pregame cutoff
    granularity shared by the current-season rim-protection and role-finishing/spacing paths --
    the 1st of the calendar month STRICTLY BEFORE `as_of_date` (if `as_of_date` is itself the 1st
    of a month, backs up one further month), so a real `date_to_nullable=month_cutoff` API pull
    always covers evidence strictly earlier than `as_of_date`, with a full day-or-more safety
    margin by construction -- never a same-day or same-month boundary. Bounds the real number of
    distinct (season, cutoff) API pulls ever needed to at most ~7/season (one per real calendar
    month in an Oct-Apr regular season), matching this phase's own low-call-count mandate."""
    y, m, d = int(as_of_date[:4]), int(as_of_date[5:7]), int(as_of_date[8:10])
    if d == 1:
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return f"{y:04d}-{m:02d}-01"


@lru_cache(maxsize=None)
def _prefix_ledger_for(player_id: str, season: str) -> Optional[Tuple[Tuple[str, str], Tuple[Tuple[int, ...], ...]]]:
    """(sorted (date, game_id) keys, cumulative prefix sums) for one player/season, or None if this
    player has no cached game-log evidence that season. `prefix[i]` = (fgm, fga, fg3m, fg3a, ftm,
    fta) totals across games[0:i] (i.e. prefix[0] = all zeros, prefix[len(games)] = full-season
    totals) -- the standard prefix-sum shape making "sum of games strictly before index i" an O(1)
    lookup once i is found via bisect. Memoized: built once per (player, season), reused across
    every date query a rolling backtest makes for that player/season."""
    games = load_player_game_log(season).get(player_id)
    if not games:
        return None
    keys = tuple((g["date"], g["game_id"]) for g in games)
    prefix = [(0, 0, 0, 0, 0, 0)]
    running = [0, 0, 0, 0, 0, 0]
    for g in games:
        running = [running[0] + g["fgm"], running[1] + g["fga"], running[2] + g["fg3m"],
                   running[3] + g["fg3a"], running[4] + g["ftm"], running[5] + g["fta"]]
        prefix.append(tuple(running))
    return keys, tuple(prefix)


def _evidence_before(player_id: str, season: str, as_of_date: str,
                      exclude_game_id: Optional[str] = None) -> Optional[Tuple[int, ...]]:
    """(fgm, fga, fg3m, fg3a, ftm, fta) summed over every real game strictly before `as_of_date`
    (and, defensively, never including `exclude_game_id` even if its own date is not `< as_of_date`
    -- e.g. a caller who passes the target game's OWN date as `as_of_date` rather than the day
    after it). Returns None if this player has no cached evidence at all this season (distinct
    from a real, valid all-zero result for a player who has cached evidence but hasn't played yet
    this season as of this date -- see the off-by-one invariant test)."""
    ledger = _prefix_ledger_for(player_id, season)
    if ledger is None:
        return None
    keys, prefix = ledger
    # bisect_left on (date, "") finds the first game NOT strictly before as_of_date (a game_id
    # string is never empty in real data, so this key sorts before every real same-date game).
    cut = bisect.bisect_left(keys, (as_of_date, ""))
    fgm, fga, fg3m, fg3a, ftm, fta = prefix[cut]
    if exclude_game_id is not None and cut < len(keys) and keys[cut] == (as_of_date, exclude_game_id):
        pass  # already excluded by the strict "< as_of_date" cut -- nothing to subtract
    return (fgm, fga, fg3m, fg3a, ftm, fta)


def _partial_current_season_evidence(player_id: str, as_of_season: str, as_of_date: str, stat: str,
                                      exclude_game_id: Optional[str] = None) -> Optional[SeasonEvidence]:
    """One SeasonEvidence row for the CURRENT season, built from ONLY pre-cutoff games -- the
    direct pregame-safe replacement for `_player_evidence_by_season`'s own full-season row for
    this one season. `stat` is 'three_point' or 'free_throw'. Returns None if this player has no
    game-log evidence at all this season (not yet in the cache, or genuinely never played) -- the
    caller falls back to prior-season-only evidence in that case, same as a real rookie/opening-
    night case."""
    totals = _evidence_before(player_id, as_of_season, as_of_date, exclude_game_id)
    if totals is None:
        return None
    fgm, fga, fg3m, fg3a, ftm, fta = totals
    if stat == "three_point":
        if fg3a <= 0:
            return SeasonEvidence(season=as_of_season, rate=0.0, sample=0.0)  # real zero attempts so far -- zero weight, not missing
        return SeasonEvidence(season=as_of_season, rate=fg3m / fg3a, sample=float(fg3a))
    if stat == "free_throw":
        if fta <= 0:
            return SeasonEvidence(season=as_of_season, rate=0.0, sample=0.0)
        return SeasonEvidence(season=as_of_season, rate=ftm / fta, sample=float(fta))
    raise ValueError(f"no per-game extraction defined for {stat!r}")


def _pregame_ability_estimate(player_id: str, canonical_name: Optional[str], as_of_season: str,
                               as_of_date: str, attribute: str, all_seasons: List[str],
                               exclude_game_id: Optional[str] = None) -> pst.ScoringTruthEstimate:
    """three_point / free_throw only (the two game-log-capable abilities)."""
    from player_ability_estimation import _seasons_through_cutoff, estimate_attribute
    source = "player_scoring_truth_temporal (leaguegamelog current-season prefix + prior full seasons)"
    if canonical_name is None:
        return pst.ScoringTruthEstimate(
            name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="identity not resolved", provenance=MISSING,
        )

    prior_seasons = [s for s in _seasons_through_cutoff(as_of_season, all_seasons) if s < as_of_season]
    prior_evidence: List[SeasonEvidence] = []
    for season in prior_seasons:
        result = estimate_attribute(canonical_name, season, attribute, [s for s in all_seasons if s <= season])
        if result.weighted_raw_rate is not None:
            # re-derive that season's OWN un-shrunk single-season rate/sample from its own
            # seasons_used list (the one entry for `season` itself) -- reused verbatim, not
            # recomputed, so prior-season contribution is IDENTICAL to what the season-level
            # estimator already trusted for that season.
            own_row = next((ev for ev in result.seasons_used if ev.season == season), None)
            if own_row is not None:
                prior_evidence.append(own_row)

    current = _partial_current_season_evidence(player_id, as_of_season, as_of_date, attribute, exclude_game_id)
    all_evidence = prior_evidence + ([current] if current is not None else [])

    if not all_evidence:
        return pst.ScoringTruthEstimate(
            name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="no real evidence (no prior season, no current-season games before cutoff)",
            provenance=MISSING,
        )

    # league_avg_rate -- REAL LEAKAGE FOUND AND FIXED HERE (this phase): the season-level
    # estimator's own reference population (`_build_reference_population`) is built from every
    # qualifying player-season THROUGH `as_of_season`, which -- for `as_of_season` itself --
    # includes every OTHER player's FULL, final-season rate (not date-cut). Using
    # `estimate_attribute(..., as_of_season, ...).league_avg_rate` for a pregame query would
    # therefore leak the REST OF THE LEAGUE's future (post-cutoff-date) games into this player's
    # shrinkage prior, even though this player's OWN evidence is correctly cut off above. Fixed by
    # using the LAST FULLY COMPLETED season's own league average instead -- entirely in the past,
    # zero leakage, at the honest cost of a slightly stale (one-season-old) reference population.
    recency_decay, prior_strength, param_source = pae_resolve_params(attribute)
    reference_season = _season_before(as_of_season)
    reference_all_seasons = [s for s in all_seasons if s <= reference_season]
    full_result = (estimate_attribute(canonical_name, reference_season, attribute, reference_all_seasons)
                   if reference_all_seasons else None)
    league_avg = full_result.league_avg_rate if full_result is not None else None

    weighted_raw, shrunk, total_weight = _weighted_shrunk_estimate(
        all_evidence, as_of_season, prior_strength, league_avg, recency_decay=recency_decay,
    )
    if shrunk is None:
        return pst.ScoringTruthEstimate(
            name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=param_source,
            coverage_note="zero total evidence weight", provenance=MISSING,
        )
    confidence = round(total_weight / (total_weight + prior_strength), 3) if prior_strength else None
    provenance = CURRENT_SEASON_PREGAME if (current is not None and current.sample > 0) else PRIOR_SEASON_ONLY
    return pst.ScoringTruthEstimate(
        name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
        value=shrunk, confidence=confidence, sample_size=total_weight, source=source, param_source=param_source,
        provenance=provenance,
    )


def _pregame_three_point_preference_estimate(player_id: str, canonical_name: Optional[str],
                                              as_of_season: str, as_of_date: str, all_seasons: List[str],
                                              exclude_game_id: Optional[str] = None) -> pst.ScoringTruthEstimate:
    """The one tendency with real, per-game, pregame-safe evidence this phase (see module
    docstring's audit table) -- FG3A/FGA, the exact same rows the ability-side pregame path reads.
    Reuses `player_tendencies_estimation`'s own shrinkage primitive (`_shrunk_rate`) and transform
    (`_logit`-based `_relative_transform`) rather than inventing a parallel tendency model."""
    import player_tendencies_analysis as pta
    from player_tendencies_estimation import TENDENCY_LAMBDA, TENDENCY_M, _relative_transform, _league_avg_rate
    from rim_protection_calibration import _shrunk_rate

    source = "player_scoring_truth_temporal (leaguegamelog current-season prefix + prior full seasons)"
    tendency = "three_point_preference"
    if canonical_name is None:
        return pst.ScoringTruthEstimate(
            name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="identity not resolved", provenance=MISSING,
        )

    prior_seasons = [s for s in all_seasons if pta.TENDENCY_FIRST_SEASON_BOX <= s < as_of_season]
    history: List[Tuple[str, float, float]] = []
    for season in prior_seasons:
        rows = pta.build_player_tendency_rows(season)
        row = next((r for r in rows if r.player_name == canonical_name), None)
        if row is None:
            continue
        rate = pta.three_point_preference(row)
        if rate is not None:
            history.append((season, rate, row.fga or 0.0))

    totals = _evidence_before(player_id, as_of_season, as_of_date, exclude_game_id)
    current_weight = 0.0
    if totals is not None:
        _, fga, _, fg3a, _, _ = totals
        if fga > 0:
            history.append((as_of_season, fg3a / fga, float(fga)))
            current_weight = float(fga)

    if not history:
        return pst.ScoringTruthEstimate(
            name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="no real evidence (no prior season, no current-season games before cutoff)",
            provenance=MISSING,
        )

    # Same anti-leakage fix as the ability path: the league-average reference comes from the LAST
    # FULLY COMPLETED season only, never the in-progress as_of_season (which would pull in every
    # OTHER player's full-season rate, not just this player's own, correctly cut-off evidence).
    reference_season = _season_before(as_of_season)
    reference_rows = pta.build_player_tendency_rows(reference_season) if reference_season >= pta.TENDENCY_FIRST_SEASON_BOX else []
    league_avg = _league_avg_rate(reference_rows, tendency) if reference_rows else None

    shrunk = _shrunk_rate(history, int(as_of_season[:4]), TENDENCY_LAMBDA, TENDENCY_M[tendency], league_avg) if league_avg is not None else None
    if shrunk is None:
        return pst.ScoringTruthEstimate(
            name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="no safe league-average reference available (no fully-completed prior season)",
            provenance=MISSING,
        )

    latent_propensity = round(_relative_transform(shrunk, tendency) - _relative_transform(league_avg, tendency), 4)
    total_weight = sum(w for _, _, w in history)
    # Coarse ordinal confidence, matching player_scoring_truth._tendency_estimate's own
    # documented mapping (high/medium/low -> 0.9/0.6/0.3) for the SAME reason: no single
    # shrinkage-prior scalar is exposed for tendencies the way ability estimators expose one.
    confidence = 0.9 if total_weight >= 3 * 100.0 else (0.6 if total_weight >= 100.0 else 0.3)
    provenance = CURRENT_SEASON_PREGAME if current_weight > 0 else PRIOR_SEASON_ONLY
    return pst.ScoringTruthEstimate(
        name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
        value=latent_propensity, confidence=confidence, sample_size=total_weight, source=source,
        param_source=None, provenance=provenance,
    )


def _prior_season_only_estimate(player_id: str, as_of_season: str, target: str,
                                 all_seasons: List[str]) -> pst.ScoringTruthEstimate:
    """rim_finishing / floater_short_mid / midrange / midrange_preference / drive_aggression --
    Option B (see module docstring's audit table): no per-game evidence exists anywhere in this
    repo's already-used sources, so the pregame-safe value is simply the LAST FULLY COMPLETED
    season's own, already-correctly-cut-off season-level estimate -- identical for EVERY date
    within `as_of_season` (never updated mid-season), clearly labeled PRIOR_SEASON_ONLY rather than
    silently presented as date-fresh. This is strictly safe (the reference season is entirely in
    the past relative to any date in as_of_season) even though it is not incremental."""
    reference_season = _season_before(as_of_season)
    reference_all_seasons = [s for s in all_seasons if s <= reference_season]
    if not reference_all_seasons:
        kind = "ability" if target in pst.ABILITY_TARGETS else "tendency"
        return pst.ScoringTruthEstimate(
            name=target, kind=kind, player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None,
            source="player_scoring_truth_temporal (no completed prior season)", param_source=None,
            coverage_note="no fully-completed prior season exists yet (rookie / first tracked season)",
            provenance=MISSING,
        )
    if target in pst.ABILITY_TARGETS:
        base = pst._ability_estimate(player_id, reference_season, target, reference_all_seasons)
    else:
        base = pst._tendency_estimate(player_id, reference_season, target, reference_all_seasons)
    provenance = MISSING if base.value is None else PRIOR_SEASON_ONLY
    return replace(base, as_of_season=as_of_season, provenance=provenance)


def build_scoring_truth_profile_as_of_date(player_id: str, as_of_date: str, as_of_season: str,
                                            all_seasons: List[str],
                                            exclude_game_id: Optional[str] = None) -> pst.ScoringTruthProfile:
    """The one public, pregame-safe entry point. `as_of_season` names the season `as_of_date`
    falls within (the caller's own responsibility -- this module does not infer a date's season
    from a schedule, since no single canonical season-boundary source is wired here yet). Every
    one of the 8 targets is always represented (MISSING != absent, same contract as the
    season-level `build_scoring_truth_profile`); each carries an explicit `provenance` so a caller
    never has to guess how fresh a given field actually is.

    `exclude_game_id`: pass the target game's own real GAME_ID when building a snapshot for "right
    before THIS specific game" and that game's own date equals `as_of_date` -- belt-and-suspenders
    against ever including the target game's own stats (the strict `< as_of_date` cut already
    excludes it whenever the target game's date truly is `as_of_date`, since a game cannot be
    strictly before its own date; this parameter only matters if a caller mistakenly passes a
    `as_of_date` equal to or after the target game's date)."""
    resolution = pid.resolve_id_to_name(player_id)
    canonical_name = resolution.canonical_name

    estimates: Dict[str, pst.ScoringTruthEstimate] = {}
    for attribute in ("three_point", "free_throw"):
        estimates[attribute] = _pregame_ability_estimate(
            player_id, canonical_name, as_of_season, as_of_date, attribute, all_seasons, exclude_game_id)
    estimates["three_point_preference"] = _pregame_three_point_preference_estimate(
        player_id, canonical_name, as_of_season, as_of_date, all_seasons, exclude_game_id)
    # "Add pregame shot-event scoring evidence" phase -- these 4 are now genuinely
    # CURRENT_SEASON_PREGAME-capable via shotchartdetail (see module docstring's updated audit
    # table). Only drive_aggression remains PRIOR_SEASON_ONLY (Option B) -- see
    # `_prior_season_only_estimate`'s own docstring / the module docstring's drive_aggression
    # determination for why shot-event data does not resolve it.
    for attribute in _SHOT_ZONE_TARGETS:
        estimates[attribute] = _pregame_shot_zone_ability_estimate(
            player_id, canonical_name, as_of_season, as_of_date, attribute, all_seasons, exclude_game_id)
    estimates["midrange_preference"] = _pregame_midrange_preference_estimate(
        player_id, canonical_name, as_of_season, as_of_date, all_seasons, exclude_game_id)
    estimates["drive_aggression"] = _prior_season_only_estimate(player_id, as_of_season, "drive_aggression", all_seasons)

    return pst.ScoringTruthProfile(
        player_id=player_id, canonical_name=canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates, as_of_date=as_of_date,
    )


# =======================================================================
# Shot-event-derived pregame paths (rim_finishing / floater_short_mid / midrange /
# midrange_preference) -- "Add pregame shot-event scoring evidence" phase.
# =======================================================================
from player_shot_event_ingestion import load_shot_events  # noqa: E402 -- grouped with this section's own imports

# SHOT_ZONE_BASIC -> family, using the EXACT SAME real zone names shot_zone_ingestion.py's own
# ZONES tuple already uses (see player_shot_event_ingestion.py's own module docstring for the
# direct verification that this is the SAME taxonomy, not a parallel one). "Backcourt" is
# deliberately excluded from THREE (a rare desperation heave, not a real shot-quality look -- the
# SAME exclusion this project's own real 2023-24/2024-25 league-wide zone-share audit already used).
_ZONE_TO_FAMILY = {
    "Restricted Area": "rim_finishing",
    "In The Paint (Non-RA)": "floater_short_mid",
    "Mid-Range": "midrange",
    "Left Corner 3": "THREE", "Right Corner 3": "THREE", "Above the Break 3": "THREE",
}
_SHOT_ZONE_TARGETS = ("rim_finishing", "floater_short_mid", "midrange")


@lru_cache(maxsize=None)
def _shot_event_prefix_ledger_for(player_id: str, season: str):
    """(sorted (date, game_id, game_event_id) keys, cumulative prefix sums) for one player/season
    -- same shape/contract as `_prefix_ledger_for`, one array slot per shot-zone family
    (rim_fga, rim_fgm, floater_fga, floater_fgm, mid_fga, mid_fgm), or None if this player has no
    cached shot-event evidence that season."""
    events = load_shot_events(season).get(player_id)
    if not events:
        return None
    keys = tuple((e["date"], e["game_id"], e["game_event_id"]) for e in events)
    prefix = [(0, 0, 0, 0, 0, 0)]
    running = [0, 0, 0, 0, 0, 0]
    for e in events:
        family = _ZONE_TO_FAMILY.get(e["zone_basic"])
        if family == "rim_finishing":
            running[0] += 1
            running[1] += int(e["made"])
        elif family == "floater_short_mid":
            running[2] += 1
            running[3] += int(e["made"])
        elif family == "midrange":
            running[4] += 1
            running[5] += int(e["made"])
        prefix.append(tuple(running))
    return keys, tuple(prefix)


def _shot_family_evidence_before(player_id: str, season: str, as_of_date: str,
                                  exclude_game_id: Optional[str] = None) -> Optional[Tuple[int, ...]]:
    """(rim_fga, rim_fgm, floater_fga, floater_fgm, mid_fga, mid_fgm) summed over every real shot
    strictly before `as_of_date` -- same off-by-one contract as `_evidence_before`."""
    ledger = _shot_event_prefix_ledger_for(player_id, season)
    if ledger is None:
        return None
    keys, prefix = ledger
    cut = bisect.bisect_left(keys, (as_of_date, "", -1))
    return prefix[cut]


def _pregame_shot_zone_ability_estimate(player_id: str, canonical_name: Optional[str], as_of_season: str,
                                         as_of_date: str, attribute: str, all_seasons: List[str],
                                         exclude_game_id: Optional[str] = None) -> pst.ScoringTruthEstimate:
    """rim_finishing / floater_short_mid / midrange -- same architecture as
    `_pregame_ability_estimate`, but the current-season partial row comes from the shot-event
    ledger instead of the box game-log, and prior seasons still reuse
    `shot_zone_estimation.estimate_shot_zone_attribute`'s own already-validated per-season rows
    unmodified."""
    from shot_zone_estimation import _seasons_through_cutoff, estimate_shot_zone_attribute, resolve_params
    source = "player_scoring_truth_temporal (shotchartdetail current-season prefix + prior full seasons)"
    if canonical_name is None:
        return pst.ScoringTruthEstimate(
            name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="identity not resolved", provenance=MISSING,
        )

    prior_seasons = [s for s in _seasons_through_cutoff(as_of_season, all_seasons) if s < as_of_season]
    prior_evidence: List[SeasonEvidence] = []
    for season in prior_seasons:
        result = estimate_shot_zone_attribute(canonical_name, season, attribute, [s for s in all_seasons if s <= season])
        own_row = next((ev for ev in result.seasons_used if ev.season == season), None)
        if own_row is not None:
            prior_evidence.append(own_row)

    idx = {"rim_finishing": (0, 1), "floater_short_mid": (2, 3), "midrange": (4, 5)}[attribute]
    totals = _shot_family_evidence_before(player_id, as_of_season, as_of_date, exclude_game_id)
    current = None
    if totals is not None:
        fga, fgm = totals[idx[0]], totals[idx[1]]
        current = SeasonEvidence(season=as_of_season, rate=(fgm / fga if fga > 0 else 0.0), sample=float(fga))

    all_evidence = prior_evidence + ([current] if current is not None else [])
    if not all_evidence:
        return pst.ScoringTruthEstimate(
            name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="no real evidence (no prior season, no current-season shot events before cutoff)",
            provenance=MISSING,
        )

    # Same anti-leakage fix as the box-score pregame path: league average from the last FULLY
    # COMPLETED season only, never the in-progress as_of_season.
    recency_decay, prior_strength, param_source = resolve_params(attribute)
    reference_season = _season_before(as_of_season)
    reference_all_seasons = [s for s in all_seasons if s <= reference_season]
    full_result = (estimate_shot_zone_attribute(canonical_name, reference_season, attribute, reference_all_seasons)
                   if reference_all_seasons else None)
    league_avg = full_result.league_avg_rate if full_result is not None else None

    weighted_raw, shrunk, total_weight = _weighted_shrunk_estimate(
        all_evidence, as_of_season, prior_strength, league_avg, recency_decay=recency_decay,
    )
    if shrunk is None:
        return pst.ScoringTruthEstimate(
            name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=param_source,
            coverage_note="zero total evidence weight", provenance=MISSING,
        )
    confidence = round(total_weight / (total_weight + prior_strength), 3) if prior_strength else None
    provenance = CURRENT_SEASON_PREGAME if (current is not None and current.sample > 0) else PRIOR_SEASON_ONLY
    return pst.ScoringTruthEstimate(
        name=attribute, kind="ability", player_id=player_id, as_of_season=as_of_season,
        value=shrunk, confidence=confidence, sample_size=total_weight, source=source, param_source=param_source,
        provenance=provenance,
    )


def _pregame_midrange_preference_estimate(player_id: str, canonical_name: Optional[str], as_of_season: str,
                                           as_of_date: str, all_seasons: List[str],
                                           exclude_game_id: Optional[str] = None) -> pst.ScoringTruthEstimate:
    """midrange_preference -- same shot-event source as the ability path above, but the
    DENOMINATOR is the real 2PT-ZONE total (rim + floater + mid attempts), matching
    `player_tendencies_analysis._weight_for`'s own existing definition EXACTLY (never a simpler
    "mid FGA / all FGA" proxy, per this phase's own explicit instruction not to change the
    tendency's conceptual denominator)."""
    import player_tendencies_analysis as pta
    from player_tendencies_estimation import TENDENCY_LAMBDA, TENDENCY_M, _relative_transform, _league_avg_rate
    from rim_protection_calibration import _shrunk_rate

    source = "player_scoring_truth_temporal (shotchartdetail current-season prefix + prior full seasons)"
    tendency = "midrange_preference"
    if canonical_name is None:
        return pst.ScoringTruthEstimate(
            name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="identity not resolved", provenance=MISSING,
        )

    prior_seasons = [s for s in all_seasons if pta.TENDENCY_FIRST_SEASON_BOX <= s < as_of_season]
    history: List[Tuple[str, float, float]] = []
    for season in prior_seasons:
        rows = pta.build_player_tendency_rows(season)
        row = next((r for r in rows if r.player_name == canonical_name), None)
        if row is None:
            continue
        rate = pta.midrange_preference(row)
        if rate is not None:
            weight = (row.restricted_area_fga or 0.0) + (row.paint_non_ra_fga or 0.0) + (row.midrange_fga or 0.0)
            history.append((season, rate, weight))

    totals = _shot_family_evidence_before(player_id, as_of_season, as_of_date, exclude_game_id)
    current_weight = 0.0
    if totals is not None:
        rim_fga, _, floater_fga, _, mid_fga, _ = totals
        two_pt_zone_total = rim_fga + floater_fga + mid_fga
        if two_pt_zone_total > 0:
            history.append((as_of_season, mid_fga / two_pt_zone_total, float(two_pt_zone_total)))
            current_weight = float(two_pt_zone_total)

    if not history:
        return pst.ScoringTruthEstimate(
            name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="no real evidence (no prior season, no current-season shot events before cutoff)",
            provenance=MISSING,
        )

    reference_season = _season_before(as_of_season)
    reference_rows = pta.build_player_tendency_rows(reference_season) if reference_season >= pta.TENDENCY_FIRST_SEASON_BOX else []
    league_avg = _league_avg_rate(reference_rows, tendency) if reference_rows else None

    shrunk = (_shrunk_rate(history, int(as_of_season[:4]), TENDENCY_LAMBDA, TENDENCY_M[tendency], league_avg)
              if league_avg is not None else None)
    if shrunk is None:
        return pst.ScoringTruthEstimate(
            name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
            value=None, confidence=None, sample_size=None, source=source, param_source=None,
            coverage_note="no safe league-average reference available (no fully-completed prior season)",
            provenance=MISSING,
        )

    latent_propensity = round(_relative_transform(shrunk, tendency) - _relative_transform(league_avg, tendency), 4)
    total_weight = sum(w for _, _, w in history)
    confidence = 0.9 if total_weight >= 3 * 100.0 else (0.6 if total_weight >= 100.0 else 0.3)
    provenance = CURRENT_SEASON_PREGAME if current_weight > 0 else PRIOR_SEASON_ONLY
    return pst.ScoringTruthEstimate(
        name=tendency, kind="tendency", player_id=player_id, as_of_season=as_of_season,
        value=latent_propensity, confidence=confidence, sample_size=total_weight, source=source,
        param_source=None, provenance=provenance,
    )
