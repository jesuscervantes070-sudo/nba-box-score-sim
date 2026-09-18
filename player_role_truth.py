"""
Roles + Team Context Truth V1 -- truth-to-simulation-profile bridge.

============================ EXISTING ARCHITECTURE AUDIT (done BEFORE writing anything new) ============================
`role_off_profile.py`/`role_off_estimation.py`/`role_off_analysis.py` (Phase 13) ALREADY implement
exactly the V1 role dimensions this phase is asked to build, real and complete:

    role_off_initiation = POTENTIAL_AST per 36 real minutes (real creation-opportunity RATE, not a
        share -- deliberately NOT residualized against usage/touches; raw responsibility, not skill
        left over after removing it. NOTE: shares the same POTENTIAL_AST field family as the
        Playmaking Truth phase's `playmaking_vision` [potential_ast / passes_made] -- a genuinely
        DIFFERENT denominator/concept (per-minute volume/role vs per-pass quality/skill), verified
        empirically distinct in this phase's own diagnostics, not assumed.)
    role_off_finishing  = PCT_AST_FGM (real share of a player's OWN made shots that were assisted --
        high = fed shots (finisher), low = self-creates -- deployment, not shot-making SKILL).
    role_off_spacing    = PCT_AST_3PM (real share of a player's OWN made 3s that were assisted --
        empirically NOT the same signal as the tendency layer's `three_point_preference`, real
        Pearson r=0.42/Spearman r=0.11 on 2023-24 data per that module's own docstring).

Source: `player_role_off.json` (real `leaguedashptstats`+`leaguedashplayerstats` season-aggregate
tracking, id-keyed, real floor 2013-14 -- `role_off_ingestion.ROLE_TRACKING_FIRST_SEASON`; this
phase backfilled the cache from the previously-populated 2022-23/2023-24-only snapshot to the full
2013-14+ range, no new ingestion module needed). Already id-keyed directly -- no name-resolution
adapter needed.

REAL, DISCOVERED INTEGRATION DEFECT (found and fixed during the Historical Pregame Game Snapshot
phase's composition work, documented here since this is the module it lives in): this module
originally called `role_off_estimation.build_role_profile(player_id, as_of_season)`, which ALSO
unconditionally computes `role_def_perimeter_interior` via
`role_off_analysis.defensive_deployment_axis` -> `poa_containment_ingestion.fetch_matchup_data` --
a genuinely LIVE, UNCACHED `leagueseasonmatchups` network call (90s timeout, 4 retries) that this
module's own three V1 dimensions never read. A real composition pass (many players x one season)
was therefore paying for a slow live network round-trip it never needed. Fixed by calling the
cheaper `role_off_analysis.role_off_initiation/finishing/spacing` extractors directly against the
same already-cached `role_off_ingestion.load_role_off` row -- byte-identical real values, zero
network calls, `role_off_estimation.py`/`role_off_analysis.py`'s own math untouched and reused by
import, not duplicated.

None of that estimator math is touched, duplicated, or re-derived here. This module ONLY: (1) adapts
`PlayerRoleProfile`'s `RoleObservation` shape into this project's now-standard value/confidence/
sample_size/provenance/source/as_of truth contract (consistent with scoring/playmaking/rebounding/
defensive truth), (2) adds a date-safe, team-stint-aware pregame entry point, and (3) overlays the
three real values directly into `PlayerSimulationProfile`'s ALREADY-LIVE `role_off_initiation`/
`role_off_finishing`/`role_off_spacing` fields -- see the engine-consumption audit below.

============================ ENGINE CONSUMPTION AUDIT ============================
Checked directly against `action_selection.py`'s own module docstring and dispatch code (NOT
touched by this phase): `role_off_initiation` is "KEEP" -- LIVE, consumed for every
CREATION_ACTIONS score via `ROLE_INITIATION_SCALE * (role_off_initiation - ROLE_INITIATION_REFERENCE)`.
`role_off_finishing` is "KEEP" -- LIVE, consumed for TERMINAL_ACTIONS scoring AND (in
`possession_orchestrator.py`) for "nearest finishing role" seal-receiver selection.
`role_off_spacing` is "KEEP BUT FLAG, used narrowly" -- LIVE but only affects CATCH_AND_SHOOT
scoring. All three engine fields' native units are EXACTLY `role_off_estimation.py`'s own real
units (a per-36 rate for initiation, real [0,1] shares for finishing/spacing) -- confirmed by
`PlayerSimulationProfile.synthetic()`'s own defaults (`role_off_initiation=4.0`,
`role_off_finishing=0.5`, `role_off_spacing=0.5`) matching the real population centers documented in
`role_off_analysis.py`. **ALL THREE ARE DIRECTLY COMPATIBLE -- no adapter needed, unlike
`defensive_playmaking` in the prior phase.**

============================ TEAM-STINT CONTEXT ============================
`player_team_stints.py` (new this phase) inverts the existing, real `roster_membership.json` (built
for `transactions.py`'s own in-season-trade handling) into a date-safe, player_id-keyed stint list,
with a documented static-roster fallback for the ~5/30 teams missing from that cache's current
snapshot (e.g. Golden State Warriors, 2023-24) -- verified directly, not assumed complete. ROLE
values themselves are NOT split by stint this phase (the real `player_role_off.json` source is a
SEASON-AGGREGATE, not stint-specific) -- the season-aggregate role value is reported for whichever
team the player is on as of a given date, honestly labeled as a season-blended value in
`coverage_note` when a trade occurred, per this phase's own "if evidence is only season aggregate
and a player changed teams, document the approximation" instruction.

============================ TEMPORAL STATUS ============================
`player_role_off.json` is Classification B (season aggregate only) -- the same real tracking-cache
shape as passing/handling/rebound-chance data. No per-game touches/potential-assist prefix is built
this phase (same proportionate-scope reasoning as prior phases). All three role dimensions are
**PRIOR_SEASON_ONLY** for pregame use.
"""
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import player_scoring_truth_temporal as psst  # reused for the SAME provenance vocabulary, not redefined
import role_off_analysis as roa
import role_off_ingestion as roi
from player_team_stints import team_as_of_date, current_team, was_traded, team_stints_for_player
from possession_orchestrator import PlayerSimulationProfile
from role_off_profile import MEASURED_TRACKING

SCHEMA_VERSION = "0.1.0-role-truth"

ROLE_ATTRIBUTES = ("role_off_initiation", "role_off_finishing", "role_off_spacing")

_FIELD_NAME = {
    "role_off_initiation": "role_off_initiation",
    "role_off_finishing": "role_off_finishing",
    "role_off_spacing": "role_off_spacing",
}

# Confidence ramps with real minutes exposure -- NOT the ability-track's Bayesian shrinkage (role
# doesn't need a shrunk VALUE, per this phase's own instruction: "Role estimates do not necessarily
# need the same Bayesian shrinkage used for ability. But low exposure must not generate false
# certainty.") -- a simple, transparent exposure-based confidence ramp instead.
_CONFIDENCE_FULL_MINUTES = 1000.0


def _confidence_from_minutes(minutes: Optional[int]) -> Optional[float]:
    if minutes is None:
        return None
    return round(min(1.0, minutes / _CONFIDENCE_FULL_MINUTES), 3)


@dataclass(frozen=True)
class RoleTruthEstimate:
    """Same shape/contract as `DefensiveTruthEstimate`/`ReboundingTruthEstimate`, plus one
    additive field (`team_name`) since role, unlike ability, is meaningfully team-contextual."""
    name: str
    player_id: str
    as_of_season: str
    value: Optional[float]
    confidence: Optional[float]
    sample_size: Optional[float]
    source: str
    param_source: Optional[str]
    team_name: Optional[str] = None
    coverage_note: str = ""
    provenance: str = "MULTI_SEASON_THROUGH_CUTOFF"

    def __post_init__(self):
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0] or None -- got {self.confidence!r}")
        valid_provenance = {"CURRENT_SEASON_PREGAME", "PRIOR_SEASON_ONLY", "MULTI_SEASON_THROUGH_CUTOFF", "MISSING"}
        if self.provenance not in valid_provenance:
            raise ValueError(f"provenance must be one of {valid_provenance} -- got {self.provenance!r}")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "player_id": self.player_id, "as_of_season": self.as_of_season,
            "value": self.value, "confidence": self.confidence, "sample_size": self.sample_size,
            "source": self.source, "param_source": self.param_source, "team_name": self.team_name,
            "coverage_note": self.coverage_note, "provenance": self.provenance,
        }

    @staticmethod
    def from_dict(d: dict) -> "RoleTruthEstimate":
        return RoleTruthEstimate(
            name=d["name"], player_id=d["player_id"], as_of_season=d["as_of_season"],
            value=d.get("value"), confidence=d.get("confidence"), sample_size=d.get("sample_size"),
            source=d["source"], param_source=d.get("param_source"), team_name=d.get("team_name"),
            coverage_note=d.get("coverage_note", ""),
            provenance=d.get("provenance", "MULTI_SEASON_THROUGH_CUTOFF"),
        )


@dataclass(frozen=True)
class RoleTruthProfile:
    """Same shape/contract as `DefensiveTruthProfile`/`ReboundingTruthProfile`."""
    player_id: str
    canonical_name: Optional[str]
    as_of_season: str
    identity_state: str
    estimates: Dict[str, RoleTruthEstimate] = field(default_factory=dict)
    as_of_date: Optional[str] = None
    team_name: Optional[str] = None

    def value(self, name: str) -> Optional[float]:
        est = self.estimates.get(name)
        return est.value if est is not None else None

    def has_real_evidence(self, name: str) -> bool:
        return self.value(name) is not None

    def coverage_summary(self) -> Dict[str, bool]:
        return {name: self.has_real_evidence(name) for name in ROLE_ATTRIBUTES}

    @property
    def conceptual_key(self) -> Tuple[str, str, str]:
        return (self.player_id, self.as_of_date or self.as_of_season, SCHEMA_VERSION)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "player_id": self.player_id,
            "canonical_name": self.canonical_name,
            "as_of_season": self.as_of_season,
            "as_of_date": self.as_of_date,
            "identity_state": self.identity_state,
            "team_name": self.team_name,
            "estimates": {name: est.to_dict() for name, est in sorted(self.estimates.items())},
        }

    @staticmethod
    def from_dict(d: dict) -> "RoleTruthProfile":
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"RoleTruthProfile.from_dict: schema_version mismatch -- stored "
                f"{d.get('schema_version')!r}, this code expects {SCHEMA_VERSION!r}."
            )
        return RoleTruthProfile(
            player_id=d["player_id"], canonical_name=d.get("canonical_name"),
            as_of_season=d["as_of_season"], identity_state=d["identity_state"],
            estimates={name: RoleTruthEstimate.from_dict(v) for name, v in d.get("estimates", {}).items()},
            as_of_date=d.get("as_of_date"), team_name=d.get("team_name"),
        )


_ROLE_ANALYSIS_FN = {
    "role_off_initiation": roa.role_off_initiation,
    "role_off_finishing": roa.role_off_finishing,
    "role_off_spacing": roa.role_off_spacing,
}


def _estimate(player_id: str, as_of_season: str, attribute: str, team_name: Optional[str],
              coverage_note: str, provenance: str) -> RoleTruthEstimate:
    """Deliberately does NOT call `role_off_estimation.build_role_profile` -- a REAL, discovered
    integration defect: that function unconditionally also computes
    `role_def_perimeter_interior` (`role_off_analysis.defensive_deployment_axis` ->
    `poa_containment_ingestion.fetch_matchup_data`), a genuinely LIVE, UNCACHED
    `leagueseasonmatchups` network call (90s timeout, 4 retries) -- this project's V1 role
    dimensions here (initiation/finishing/spacing) never read that field at all, so a real
    per-player composition pass was paying for a slow live network call for a value it never
    uses. This module (owned by this project's own Roles phase, not a frozen prior-phase
    estimator) calls the cheaper underlying `role_off_analysis.role_off_initiation/finishing/
    spacing` extractors directly against the already-cached `role_off_ingestion.load_role_off`
    row -- byte-identical real values, zero network calls, and none of
    `role_off_estimation.py`/`role_off_analysis.py`'s own math is modified or duplicated (the
    exact same extractor functions are reused by import)."""
    source = "role_off_analysis (direct, bypassing build_role_profile's unused defensive-axis network call)"
    if as_of_season < roi.ROLE_TRACKING_FIRST_SEASON:
        return RoleTruthEstimate(
            name=attribute, player_id=player_id, as_of_season=as_of_season, value=None, confidence=None,
            sample_size=None, source=source, param_source=None, team_name=team_name,
            coverage_note=f"before the real role-tracking floor ({roi.ROLE_TRACKING_FIRST_SEASON})",
            provenance=psst.MISSING,
        )
    row = roi.load_role_off(as_of_season).get(player_id)
    value = _ROLE_ANALYSIS_FN[attribute](row) if row is not None else None
    if row is None or value is None:
        return RoleTruthEstimate(
            name=attribute, player_id=player_id, as_of_season=as_of_season, value=None, confidence=None,
            sample_size=None, source=source, param_source=None, team_name=team_name,
            coverage_note="no real role-tracking evidence for this player-season", provenance=psst.MISSING,
        )
    sample_size = int(row.get("MIN") or 0)
    return RoleTruthEstimate(
        name=attribute, player_id=player_id, as_of_season=as_of_season, value=value,
        confidence=_confidence_from_minutes(sample_size), sample_size=sample_size,
        source=source, param_source="none (exposure-based confidence, no shrinkage -- see module docstring)",
        team_name=team_name, coverage_note=coverage_note, provenance=provenance,
    )


_ROLE_SCORING_FIELD = {"role_off_finishing": "PCT_AST_FGM", "role_off_spacing": "PCT_AST_3PM"}
_CURRENT_SEASON_MIN_MINUTES = roa.MIN_MINUTES  # SAME real exposure floor role_off_analysis's own
# role_off_finishing/role_off_spacing extractors already use -- not a new, separately-tuned floor.


def _estimate_current_season_scoring_role(player_id: str, as_of_date: str, as_of_season: str,
                                           attribute: str, team_name: Optional[str]) -> Optional[RoleTruthEstimate]:
    """CURRENT-SEASON DEFENSE + ROLE REFRESH V1: `role_off_finishing`/`role_off_spacing` ONLY (NOT
    `role_off_initiation` -- out of this phase's scope). Returns None (never a fabricated value)
    if no real current-season-through-cutoff evidence exists yet or exposure is below the real
    floor -- the caller falls back to the existing PRIOR_SEASON_ONLY path in that case. Uses a
    real `leaguedashplayerstats(Scoring, date_to=month_cutoff)` cumulative pull (see
    `role_off_ingestion.build_and_cache_role_scoring_through_date`'s own docstring for the real
    date-safety argument) -- PCT_AST_FGM/PCT_AST_3PM are the EXACT SAME real fields/semantics the
    existing PRIOR_SEASON_ONLY path already uses, just filtered to real evidence strictly before
    `as_of_date` instead of the full completed prior season. No new shrinkage math is introduced
    (role fields use exposure-based confidence, not Bayesian shrinkage, per this module's own
    existing convention) -- the real current-season MIN is used directly as `sample_size`."""
    stints_so_far = [s for s in team_stints_for_player(player_id, as_of_season) if s.first_date <= as_of_date]
    if len(stints_so_far) > 1:
        # A real, deliberate safety choice (per this phase's own trade-handling instruction): a
        # single league-wide cumulative-through-cutoff pull cannot distinguish which TEAM a
        # player's minutes/PCT_AST_* accrued under -- for a player with more than one real stint
        # ALREADY VISIBLE as of `as_of_date` (a trade that has genuinely already happened pregame),
        # using it would silently BLEND old-team and new-team role into one number. Falling back to
        # the existing PRIOR_SEASON_ONLY path is the safe choice. Deliberately checks stints
        # up to `as_of_date` ONLY (not `player_team_stints.was_traded`'s whole-season check) --
        # a real, discovered leakage risk: the whole-season check would flip this player's
        # provenance based on a trade that has NOT happened yet from `as_of_date`'s own pregame
        # perspective, which a target/future-game poison test would (correctly) catch as leakage.
        return None
    field = _ROLE_SCORING_FIELD[attribute]
    month_cutoff = psst.month_cutoff_for_date(as_of_date)
    rows = roi.load_role_scoring_through_date(as_of_season, month_cutoff)
    row = rows.get(player_id)
    if row is None:
        return None
    minutes = row.get("MIN")
    value = row.get(field)
    if minutes is None or value is None or minutes < _CURRENT_SEASON_MIN_MINUTES:
        return None
    return RoleTruthEstimate(
        name=attribute, player_id=player_id, as_of_season=as_of_season, value=value,
        confidence=_confidence_from_minutes(int(minutes)), sample_size=int(minutes),
        source=f"role_off_ingestion.load_role_scoring_through_date (real, through {month_cutoff})",
        param_source="none (exposure-based confidence, no shrinkage)", team_name=team_name,
        coverage_note=f"real current-season evidence through {month_cutoff}", provenance=psst.CURRENT_SEASON_PREGAME,
    )


def build_role_truth_profile(player_id: str, as_of_season: str) -> RoleTruthProfile:
    """Season-level entry point -- id-keyed directly (role_off_estimation.build_role_profile
    already takes a player_id). Reports the current (last-stint) team for context; a mid-season
    trade is noted in each estimate's own coverage_note (the real season-aggregate value is NOT
    split by stint -- see module docstring)."""
    import player_identity as pid
    resolution = pid.resolve_id_to_name(player_id)
    team_name = current_team(player_id, as_of_season)
    traded = was_traded(player_id, as_of_season)
    note = ("season-aggregate value spans a real mid-season trade -- not stint-specific"
            if traded else "")
    estimates = {
        attr: _estimate(player_id, as_of_season, attr, team_name, note, psst.MULTI_SEASON_THROUGH_CUTOFF)
        for attr in ROLE_ATTRIBUTES
    }
    return RoleTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates, team_name=team_name,
    )


def build_role_truth_profile_as_of_date(player_id: str, as_of_date: str, as_of_season: str) -> RoleTruthProfile:
    """Pregame-safe entry point. Role is PRIOR_SEASON_ONLY this phase (season-aggregate-only
    source) -- built from the last FULLY COMPLETED season only, frozen for the whole current
    season, exactly like every other truth track's own Option-B fallback. Team context is
    reported AS OF `as_of_date` in the CURRENT season (using the real, date-safe team-stint
    mapping), even though the ROLE VALUE itself comes from the prior season -- team-as-of-date
    and role-evidence-season are honestly two different things when a player was traded between
    the reference season and now."""
    import player_identity as pid
    resolution = pid.resolve_id_to_name(player_id)
    reference_season = psst._season_before(as_of_season)
    team_name = team_as_of_date(player_id, as_of_date, as_of_season) or current_team(player_id, reference_season)

    estimates: Dict[str, RoleTruthEstimate] = {}
    for attribute in ROLE_ATTRIBUTES:
        current = (_estimate_current_season_scoring_role(player_id, as_of_date, as_of_season, attribute, team_name)
                   if attribute in _ROLE_SCORING_FIELD else None)
        if current is not None:
            estimates[attribute] = current
            continue
        base = _estimate(player_id, reference_season, attribute, team_name,
                          "pregame: value from last completed season", psst.PRIOR_SEASON_ONLY)
        if base.value is None:
            base = replace(base, provenance=psst.MISSING)
        estimates[attribute] = replace(base, as_of_season=as_of_season)

    return RoleTruthProfile(
        player_id=player_id, canonical_name=resolution.canonical_name, as_of_season=as_of_season,
        identity_state=resolution.state, estimates=estimates, as_of_date=as_of_date, team_name=team_name,
    )


def apply_role_truth_to_simulation_profile(profile: PlayerSimulationProfile,
                                            truth: RoleTruthProfile) -> PlayerSimulationProfile:
    """Overlays ALL THREE role fields DIRECTLY (confirmed scale-compatible with the engine -- see
    module docstring's engine-consumption audit; no adapter needed). A missing estimate leaves the
    corresponding field completely untouched -- never fabricates a league-average stand-in."""
    overrides = {}
    for attribute, field_name in _FIELD_NAME.items():
        value = truth.value(attribute)
        if value is not None:
            overrides[field_name] = value
    return replace(profile, **overrides) if overrides else profile
