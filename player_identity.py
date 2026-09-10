"""
Phase 14 -- Stable Player Identity Integration.

Canonical identity going forward: real, stable NBA `player_id`. Name is
METADATA ONLY (display, reports, diagnostics, historical lookup
assistance) -- never the authoritative relational key once a real ID
exists for a player.

This module is an ADAPTER/RESOLUTION layer, not a rewrite of the
existing estimators. Every ability/tendency estimator built in Phases
1-11 (`estimate_attribute`, `estimate_tendency`, `estimate_playmaking_vision`,
`estimate_rim_protection`, `estimate_poa_containment`, `estimate_foul_drawing`,
`estimate_foul_discipline`, `estimate_rim_access_creation`,
`estimate_perimeter_space_creation`, `estimate_ball_security`) takes a
player NAME and is left completely untouched -- their validated math is
not touched by this phase (explicit instruction). Instead, this module
resolves a real `player_id` to the canonical name those functions
expect, calls the existing function unmodified, and returns the result
tagged with the resolved `player_id` and how confidently it was
resolved. Phase 12A (`anthropometrics_estimation`) and Phase 13
(`role_off_estimation`) already take `player_id` directly and need no
adapter.

============================ IDENTITY SOURCES VERIFIED ============================
`nba_api.stats.static.players.get_players()` -- the real, authoritative,
ALL-TIME NBA player index (5,103 real entries as of this phase), each
with a real, stable `id` and `full_name`. This is the single source of
truth for id<->name resolution in this module. Real, confirmed finding:
**38 real full-name collisions exist in this list** (e.g. "Dee Brown" =
[244, 200793], "Patrick Ewing" = [121, 201607] -- two genuinely
different real people, not a data error) -- name is measurably NOT a
safe canonical key, confirmed directly rather than assumed.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

RESOLVED = "RESOLVED"                    # exactly one real player_id matches
AMBIGUOUS = "AMBIGUOUS"                  # 2+ real player_ids share this name -- never silently picked
UNRESOLVED = "UNRESOLVED"                # no real player_id found for this name/query at all
NON_NBA_ONLY = "NON_NBA_ONLY"            # a real id exists (e.g. combine) but it has no NBA static-index record (never played)
LOOKUP_FAILED = "LOOKUP_FAILED"          # the lookup itself errored (network/API) -- NOT the same as UNRESOLVED

RESOLUTION_STATES = (RESOLVED, AMBIGUOUS, UNRESOLVED, NON_NBA_ONLY, LOOKUP_FAILED)


@dataclass(frozen=True)
class IdentityResolution:
    """Result of one resolution attempt. `player_id`/`canonical_name`
    are populated ONLY when state == RESOLVED -- every other state
    leaves them None rather than guessing. `candidates` carries the
    real alternative player_ids when state == AMBIGUOUS, so a caller can
    make an informed, explicit choice (or surface the ambiguity to a
    person) instead of this layer picking one silently."""
    query: str
    state: str
    player_id: Optional[str] = None
    canonical_name: Optional[str] = None
    candidates: Tuple[str, ...] = ()  # real player_ids, populated for AMBIGUOUS
    provenance: str = "nba_api.stats.static.players"
    note: Optional[str] = None

    def __post_init__(self):
        if self.state not in RESOLUTION_STATES:
            raise ValueError(f"Unknown resolution state {self.state!r} -- must be one of {RESOLUTION_STATES}")
        if self.state == RESOLVED and (self.player_id is None or self.canonical_name is None):
            raise ValueError("RESOLVED must carry both player_id and canonical_name")
        if self.state != RESOLVED and self.player_id is not None:
            raise ValueError(f"{self.state} must not carry a player_id -- resolution failed/ambiguous, no ID is authoritative")


@lru_cache(maxsize=1)
def _static_index() -> Tuple[Dict[str, str], Dict[str, Tuple[str, ...]]]:
    """(id_to_name, name_to_ids) built once from the real, local
    nba_api static player list -- no network call (this list ships with
    the nba_api package itself)."""
    from nba_api.stats.static import players as sp
    id_to_name: Dict[str, str] = {}
    name_to_ids: Dict[str, List[str]] = {}
    for p in sp.get_players():
        pid, name = str(p["id"]), p["full_name"]
        id_to_name[pid] = name
        name_to_ids.setdefault(name, []).append(pid)
    return id_to_name, {k: tuple(v) for k, v in name_to_ids.items()}


def duplicate_name_collisions() -> Dict[str, Tuple[str, ...]]:
    """Real, direct list of every full name in the static index that
    resolves to more than one real player_id -- the concrete evidence
    behind "name is not a safe canonical key" (38 real cases as of this
    phase; see docs/PHASE14_IDENTITY_INTEGRATION_REPORT.md Sec. 1)."""
    _, name_to_ids = _static_index()
    return {name: ids for name, ids in name_to_ids.items() if len(ids) > 1}


def resolve_id_to_name(player_id: str) -> IdentityResolution:
    """The SAFE direction: a real player_id maps to exactly one
    canonical name by construction (an ID is never shared by two real
    people in the static index) -- used by the ability/tendency adapter
    functions below to go from a caller's real `player_id` to the name
    those older estimators expect."""
    id_to_name, _ = _static_index()
    name = id_to_name.get(player_id)
    if name is not None:
        return IdentityResolution(query=player_id, state=RESOLVED, player_id=player_id, canonical_name=name)
    return IdentityResolution(query=player_id, state=NON_NBA_ONLY,
                               note="player_id not found in the real NBA static player index -- either a non-NBA "
                                    "combine-only id (see Phase 12A Sec. 4), a genuinely bad id, or a lookup this "
                                    "module doesn't cover yet. NOT assumed to be a lookup failure -- no network call was made.")


def resolve_name_to_id(name: str, season_hint: Optional[str] = None) -> IdentityResolution:
    """The UNSAFE direction (name -> id): real collisions exist (see
    duplicate_name_collisions). If exactly one real id matches, RESOLVED.
    If 0 match, UNRESOLVED (no fabricated id). If 2+ match, attempts a
    real, evidence-based disambiguation using `season_hint` (does this
    name+season combination have real evidence for exactly one of the
    candidate ids in this repo's own already-cached, id-keyed data --
    role_off/shot_zone/physical caches) -- if that still doesn't narrow
    to one, returns AMBIGUOUS with every real candidate id, never a
    silent first-match pick."""
    _, name_to_ids = _static_index()
    ids = name_to_ids.get(name)
    if not ids:
        return IdentityResolution(query=name, state=UNRESOLVED, note="no real player_id in the static index matches this name")
    if len(ids) == 1:
        return IdentityResolution(query=name, state=RESOLVED, player_id=ids[0], canonical_name=name)

    if season_hint is not None:
        narrowed = _disambiguate_by_season_evidence(ids, season_hint)
        if len(narrowed) == 1:
            return IdentityResolution(query=name, state=RESOLVED, player_id=narrowed[0], canonical_name=name,
                                       note=f"disambiguated among {len(ids)} real same-name candidates using {season_hint} evidence")
        ids = narrowed if narrowed else ids

    return IdentityResolution(query=name, state=AMBIGUOUS, candidates=tuple(ids),
                               note=f"{len(ids)} real, distinct NBA players share this exact name -- "
                                    "no automatic choice made; caller must supply more context or ask a person")


def _disambiguate_by_season_evidence(candidate_ids: Tuple[str, ...], season: str) -> Tuple[str, ...]:
    """Checks this repo's own real, already-cached id-keyed data
    (shot_zone, role_off) for which of the candidate ids has real
    evidence of playing in `season` -- a real, cheap, no-new-API-call
    disambiguation signal. Returns the subset of candidate_ids with real
    evidence; if that subset has exactly one id, the caller above treats
    it as resolved."""
    try:
        import shot_zone_ingestion as sz
        zones = sz.load_shot_zones(season)
    except Exception:
        zones = {}
    try:
        import role_off_ingestion as roi
        role_rows = roi.load_role_off(season)
    except Exception:
        role_rows = {}
    present = {cid for cid in candidate_ids if cid in zones or cid in role_rows}
    return tuple(present) if present else candidate_ids


# --------------------------- ability/tendency adapters (id -> name -> existing estimator) ---------------------------

def estimate_attribute_by_id(player_id: str, as_of_season: str, attribute: str, all_seasons: List[str]):
    """Adapter over the UNMODIFIED player_ability_estimation.estimate_attribute.
    Returns (IdentityResolution, AttributeEstimate_or_None) -- the caller
    can inspect resolution.state before trusting the estimate. Real
    math/calibration inside estimate_attribute is untouched."""
    import player_ability_estimation as pae
    resolution = resolve_id_to_name(player_id)
    if resolution.state != RESOLVED:
        return resolution, None
    return resolution, pae.estimate_attribute(resolution.canonical_name, as_of_season, attribute, all_seasons)


def estimate_tendency_by_id(player_id: str, as_of_season: str, all_seasons: List[str], tendency: str):
    """Adapter over the UNMODIFIED player_tendencies_estimation.estimate_tendency.
    The returned PlayerTendencyEstimate has its (new, additive,
    backward-compatible) `player_id` field filled in via `replace()` --
    the estimator's own math/fields are otherwise untouched."""
    import player_tendencies_estimation as pte
    from dataclasses import replace
    resolution = resolve_id_to_name(player_id)
    if resolution.state != RESOLVED:
        return resolution, None
    result = pte.estimate_tendency(resolution.canonical_name, as_of_season, all_seasons, tendency)
    return resolution, replace(result, player_id=player_id)


# --------------------------- unified, temporally-safe lookup ---------------------------

def attach_player_id_to_ability_profile(profile, season_hint: Optional[str] = None):
    """Metadata-only helper: resolves `profile.name` to a real
    `player_id` and returns a NEW PlayerAbilityProfile (via `replace()`,
    matching this dataclass's own "replace, don't mutate" convention)
    with that field filled in. Does not touch `attributes`,
    `ability_outputs`, or any estimate math. If resolution is AMBIGUOUS
    or UNRESOLVED, `player_id` is left as-is (None) rather than guessed
    -- the caller can inspect the returned IdentityResolution to see why."""
    from dataclasses import replace
    resolution = resolve_name_to_id(profile.name, season_hint=season_hint or profile.as_of_season)
    if resolution.state == RESOLVED:
        return resolution, replace(profile, player_id=resolution.player_id)
    return resolution, profile


def get_unified_player_context(player_id: str, as_of_season: str, all_seasons: List[str],
                                attributes: Tuple[str, ...] = (), tendencies: Tuple[str, ...] = ()) -> dict:
    """Joins ability (name-keyed, via adapter), tendency (name-keyed, via
    adapter), physical (Phase 12A, id-keyed), and role (Phase 13,
    id-keyed) evidence for ONE canonical player_id, ALL constrained to
    the same real `as_of_season` -- never a mix of season snapshots.
    Every sub-call already enforces its own temporal cutoff (verified in
    docs/PHASE14_IDENTITY_INTEGRATION_REPORT.md Sec. 12); this function
    adds no new leakage surface, it only fans out to already-safe calls
    with the SAME as_of_season passed through unchanged."""
    import anthropometrics_estimation as ae
    import role_off_estimation as roe

    resolution = resolve_id_to_name(player_id)
    result = {
        "player_id": player_id, "as_of_season": as_of_season,
        "identity": {"state": resolution.state, "canonical_name": resolution.canonical_name, "note": resolution.note},
        "abilities": {}, "tendencies": {}, "physical": None, "role": None,
    }

    if resolution.state == RESOLVED:
        from dataclasses import asdict
        for attr in attributes:
            _, est = estimate_attribute_by_id(player_id, as_of_season, attr, all_seasons)
            result["abilities"][attr] = asdict(est) if est is not None else None
        for tendency in tendencies:
            _, est = estimate_tendency_by_id(player_id, as_of_season, all_seasons, tendency)
            result["tendencies"][tendency] = est if est is None else {
                "mode": est.mode, "latent_propensity": est.latent_propensity, "confidence": est.confidence,
            }

    result["physical"] = ae.build_physical_profile(player_id, as_of_season).to_dict()
    result["role"] = roe.build_role_profile(player_id, as_of_season).to_dict()
    return result
