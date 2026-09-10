# Phase 14 — Stable Player Identity Integration

Identity infrastructure only. No estimator math, calibration, or Phase
13 role formula was changed. No gameplay/possession logic. No OVR.
Codex's counterfactual workstream was not touched.

## 1. Identity audit before migration

Classification per the scheme given (A=canonical `player_id`, B=name-only,
C=mixed, D=derived/fallback, E=historical record with no NBA ID
available):

| Component | Class | Evidence |
|---|---|---|
| `PlayerAbilityProfile` | **C (mixed)** | Already has `player_id: Optional[str] = None` alongside the load-bearing `name: str` (per its own module docstring's "STABLE IDENTITY" note) — field exists, was never populated by any estimator. |
| `AttributeEstimate` | **A, trivially** | Carries no identity at all (`value`/`confidence`/`sample_size` only) — identity lives one level up, in `PlayerAbilityProfile`. Not a gap. |
| `RoleProfile` (existing, archetype-based) | **B (name-only), and effectively dead** | Grep-confirmed: never constructed or populated anywhere outside its own definition/serialization plumbing — a real, unused placeholder, not an active identity path. |
| `player_ability_estimation.estimate_attribute` | **B (name-only)** | `def estimate_attribute(name: str, ...)` — first parameter is a name, unchanged since Phase 1-3. |
| `player_tendencies_estimation.estimate_tendency` / `PlayerTendencyEstimate` | **B (name-only)** | Same pattern; `PlayerTendencyEstimate` had no `player_id` field at all before this phase. |
| Phases 4A/4B/6/7/8/9/10 estimators (`estimate_ball_security`, `estimate_foul_drawing`, `estimate_foul_discipline`, `estimate_rim_protection`, `estimate_playmaking_vision`, `estimate_rim_access_creation`, `estimate_perimeter_space_creation`, `estimate_poa_containment`) | **B (name-only)** | Confirmed directly: every one takes `player_name: str`/`name: str` as its first parameter. |
| Phase 12A (`anthropometrics_profile.PlayerPhysicalProfile`, `anthropometrics_estimation.build_physical_profile`) | **A (canonical `player_id`)** | Already correct, built that way from the start. |
| Phase 13 (`role_off_profile.PlayerRoleProfile`, `role_off_estimation.build_role_profile`) | **A (canonical `player_id`)** | Already correct. |
| `data_source.py`'s legacy caches (`rosters.json`, `player_advanced.json`, `player_rebound_splits.json`, `player_rim_defense.json`, `player_perimeter_defense.json`, `player_hustle.json`) | **B (name-only)** | Confirmed directly: `player_advanced.json`'s `players` dict is keyed by real display name strings (e.g. `"A.J. Lawson"`), not id. |
| Newer per-season ingestion caches (`player_turnover_subtypes.json`, `player_passing_tracking.json`, `player_poa_containment.json`, `player_rim_protection.json`, `player_shot_zones.json`, `player_role_off.json`, `combine_<year>.json`, `player_physical_roster.json`) | **A (canonical `player_id`)** | Confirmed directly: all keyed by real numeric `player_id` strings (e.g. `"101108"`) — a real, favorable finding: most of the RAW EVIDENCE was already id-safe; name-keying is introduced one layer up, in each phase's own `build_player_*_rows` analysis function, which re-keys id-indexed raw data into name-indexed `Row` objects for the (name-keyed) estimator to consume. |
| `legacy Player` (`models.py`, the box-score replay simulator) | **B (name-only)**, out of scope | Confirmed to be the pre-existing, name-keyed legacy representation this whole research track has always run parallel to, not touched this phase (or any prior phase). |
| `loader.py` | **B (name-only)** | Reads the same name-keyed legacy caches above; not touched this phase. |
| Calibration JSON artifacts (all 9: `ball_security_calibration.json`, `foul_calibration.json`, `player_ability_calibration.json`(+`_v2`), `playmaking_vision_calibration.json`, `poa_containment_calibration.json`, `rim_protection_calibration.json`, `shot_creation_calibration.json`, `shot_zone_calibration.json`) | **N/A — population-level, no identity at all** | Confirmed directly (programmatic scan): none contain a per-player name or id field of any kind — every one stores only λ/M/thresholds/status/methodology at the attribute level. Not an identity path. |
| Tests (`test_*.py`) | **B/C, appropriately** | Existing ability/tendency tests use hardcoded names (matching the estimators they test) — correct as-is, not part of this migration's scope. |
| Historical players with no combine/roster-cache match | **E** | Real, confirmed: many pre-2000 (pre-combine) and low-profile players have zero real anthropometric evidence and, in some cases, no reliable static-index entry either (a genuine "no stable ID resolvable" case, distinct from an API error). |

## 2. Files changed

| File | Change |
|---|---|
| `player_identity.py` | **New.** The identity-resolution and adapter layer (this phase's core deliverable). |
| `player_tendencies_estimation.py` | **Additive only.** Added `player_id: Optional[str] = None` to `PlayerTendencyEstimate` (a new dataclass field with a default — no existing call site changed, no existing test broken). `estimate_tendency`'s own logic is byte-for-byte unchanged. |
| `test_player_identity.py` | **New.** 16 tests. |
| `docs/PHASE14_IDENTITY_INTEGRATION_REPORT.md` | **New.** This report. |

No other file was modified. `player_ability_estimation.py`, all Phase
4A-11 estimator/analysis/calibration files, `anthropometrics_*.py`,
`role_off_*.py`, `data_source.py`, `loader.py`, `models.py`, and every
Codex counterfactual-workstream file are byte-for-byte unchanged.

## 3. Canonical identity schema

```
IdentityResolution:
    query: str                    # what was looked up (a name or an id)
    state: str                    # RESOLVED | AMBIGUOUS | UNRESOLVED | NON_NBA_ONLY | LOOKUP_FAILED
    player_id: Optional[str]      # set ONLY when state == RESOLVED
    canonical_name: Optional[str] # set ONLY when state == RESOLVED
    candidates: Tuple[str, ...]   # real alternative ids, set ONLY when state == AMBIGUOUS
    provenance: str               # "nba_api.stats.static.players"
    note: Optional[str]
```

ONE PLAYER → ONE canonical `player_id` → many season/team/context
observations is enforced structurally, not just by convention: neither
`IdentityResolution` nor any Phase 12A/13/14 object encodes season or
team into the identity fields at all — season/team only ever appear as
separate parameters to lookup functions (`as_of_season`, `team_name`),
never concatenated into an id or name string.

## 4. ID resolution rules

- **`resolve_id_to_name(player_id)`** — the SAFE direction. A real
  `player_id` maps to exactly one name in the real, authoritative
  `nba_api.stats.static.players` index by construction (an id is never
  shared by two people). Used by every adapter in this phase.
- **`resolve_name_to_id(name, season_hint=None)`** — the UNSAFE
  direction, because real name collisions exist (Sec. 5). Exactly one
  match → `RESOLVED`. Zero matches → `UNRESOLVED`. Two or more matches →
  attempts `season_hint`-based disambiguation using this repo's own
  already-cached, real, id-keyed evidence (`shot_zone`/`role_off`
  caches: does exactly one candidate id have real evidence for that
  season?) before falling back to `AMBIGUOUS` with every real candidate
  id listed, never a silent first-match pick.
- No fuzzy/approximate string matching was implemented — every
  resolution this phase is either an exact real static-index match or
  an explicit `AMBIGUOUS`/`UNRESOLVED` state. A future phase could add a
  genuine fuzzy-alias layer, but it would need its own explicit
  confidence/provenance path per this phase's own instruction — not
  built here.

## 5. Ambiguity policy

**Real, confirmed evidence, not a hypothetical**: `nba_api`'s static
player index contains **38 real full-name collisions** (e.g. "Dee Brown"
→ [244, 200793], "Patrick Ewing" → [121, 201607], "Mike Dunleavy" →
[2399, 76616] — genuinely different people, not a data error; the full
list of 38 is reproducible via `player_identity.duplicate_name_collisions()`).
Policy: an ambiguous name resolution is NEVER silently collapsed to one
candidate. `resolve_name_to_id` either narrows using real, independent
season evidence (Sec. 4) or returns `AMBIGUOUS` with the full real
candidate-id list — verified directly by test
(`test_ambiguous_name_never_silently_resolved`,
`test_ambiguous_disambiguated_by_real_season_evidence`,
`test_ambiguous_stays_ambiguous_if_season_evidence_does_not_narrow`).

## 6. Historical/missing-ID policy

Five explicit, distinguished states (no sixth silently-invented state,
no fabricated ID anywhere in this module):

| State | Meaning |
|---|---|
| `RESOLVED` | Exactly one real NBA `player_id` confirmed |
| `AMBIGUOUS` | 2+ real distinct NBA players share the queried name |
| `UNRESOLVED` | No real match found for a name query — legacy/historical player with no static-index entry, or a genuine typo |
| `NON_NBA_ONLY` | A real id was supplied but has no NBA static-index record — e.g. a real combine invitee (Phase 12A Sec. 4 already found 477 of these) who never played an NBA game |
| `LOOKUP_FAILED` | Reserved for a genuine network/API error path — kept structurally distinct from `UNRESOLVED` so "checked and found nothing" is never conflated with "couldn't check" (this module's current lookups are all local/no-network, so this state is not exercised by real traffic yet, but is a real, tested, distinct value in `RESOLUTION_STATES`). |

No surrogate/internal ID scheme was introduced this phase — not
genuinely necessary yet (every id-keyed source in this repo already
uses real NBA ids). If one becomes necessary later, it must be
namespace-prefixed (e.g. `"internal:..."`) so it can never be confused
with a real NBA id — noted as a requirement for that future work, not
built now.

## 7. Ability migration

**Adapter, not rewrite** (`estimate_attribute_by_id` in `player_identity.py`):
resolves `player_id → canonical_name` via `resolve_id_to_name`, then
calls the completely unmodified `player_ability_estimation.estimate_attribute`.
Verified by test (`test_ability_adapter_returns_none_without_calling_estimator_when_unresolved`)
that the real estimator is never even called when identity resolution
fails — no risk of it silently running on a wrong/guessed name.
`PlayerAbilityProfile.player_id` (already present, previously unused) can
now be filled in via the new `attach_player_id_to_ability_profile`
helper, which uses `dataclasses.replace()` — the profile's `attributes`/
`ability_outputs` dicts are never touched by it.

## 8. Tendency migration

Same adapter pattern (`estimate_tendency_by_id`). `PlayerTendencyEstimate`
gained one additive field, `player_id: Optional[str] = None` (Sec. 2) —
verified by test that the underlying `estimate_tendency` call receives
exactly the resolved name and unchanged arguments
(`test_tendency_adapter_calls_unmodified_estimator_with_resolved_name`),
and that only the new field differs in the returned object.

## 9. Physical integration

No migration needed — Phase 12A was already `player_id`-keyed
end-to-end. Verified working in the unified join (Sec. 13).

## 10. Role integration

No migration needed — Phase 13 was already `player_id`-keyed end-to-end.
Verified working in the unified join (Sec. 13), including confirming
`role_off_estimation.build_role_profile`'s own real 2013-14 temporal
floor is respected when called through the unified layer.

## 11. Calibration/config migration decisions

**No calibration JSON artifact needs any identity-key migration.**
Confirmed directly (programmatic scan of all 9 files): every one is
strictly population-level (λ, M, thresholds, methodology, status per
*attribute*, never per *player*). This is the "no player identity
needed at all" case named explicitly in the phase instructions — no
artifact was mechanically rewritten, because none needed it.

## 12. Temporal-leakage checks

`get_unified_player_context(player_id, as_of_season, ...)` fans out to
four already-temporally-safe calls (ability/tendency adapters pass
`as_of_season` straight through to the unmodified Phase 1-11
estimators, which already enforce their own real leakage checks per
prior phases' reports; physical/role calls pass it straight through to
Phase 12A/13's own already-tested temporal cutoffs) — **verified by a
direct test** (`test_unified_context_passes_the_same_as_of_season_to_every_sub_call`)
that every sub-call receives EXACTLY the requested season, never
"latest available," using Pascal Siakam's real `player_id` (1627783) at
a season (2018-19) years before his real 2024 trade — confirming a
shared canonical identity cannot, by construction, pull a future team
affiliation, role estimate, ability estimate, or physical observation
into an earlier `as_of_season` query. No new leakage surface was
introduced — this layer adds fan-out, not new data access.

## 13. Representative joins

Real, live join for Tyrese Maxey (`player_id=1630178`, `as_of_season="2023-24"`):
ability (`three_point`, percentile 75.4, calibrated), tendency
(`drive_aggression`, latent propensity 0.394, high confidence), physical
(height 74.0in `MEASURED_ROSTER` — real, confirmed: Maxey's 2020 draft
class combine was COVID-disrupted and he has no `MEASURED_COMBINE`
record, correctly falling back per Phase 12A's existing hierarchy;
wingspan/reach `INFERRED_REGRESSION`), and role
(`role_off_initiation`/`role_off_finishing`/`role_off_spacing`/
`role_def_perimeter_interior` all populated) — all four layers joined
under the single canonical id, all agreeing on the same `as_of_season`,
zero collisions. No missing→zero anywhere in the joined output (every
absent field is `None`/`UNAVAILABLE`, never a fabricated number).

## 14. Failures/unresolved edge cases

- Ambiguous name ("Patrick Ewing", "Gerald Henderson" un-narrowed) →
  `AMBIGUOUS`, real candidate list returned, ability/tendency adapters
  correctly refuse to guess (verified by test).
- Unresolved name (typo/fully fictional) → `UNRESOLVED`, no fabricated id.
- Non-NBA id (a real combine-only id, Phase 12A's 477 non-NBA combine
  invitees) → `NON_NBA_ONLY`, distinguished from a lookup failure.
- `get_unified_player_context` with an unresolvable id still correctly
  returns physical/role data (already id-native, no name dependency) —
  verified by test — while leaving `abilities`/`tendencies` empty rather
  than crashing or guessing.

## 15. Tests

`test_player_identity.py` — 16 tests, covering (per the required list):
identical names / different entities (Patrick Ewing collision, both
confirmed-ambiguous and season-disambiguated cases), unresolved names,
a combine-participant-who-never-reached-the-NBA case (`NON_NBA_ONLY`),
failed-lookup-is-a-distinct-state-from-unresolved, `player_id` joins
across all four profile layers, temporal as-of isolation (Siakam
pre-trade), duplicate-id/name detection (the real 38-collision list),
missing≠zero (every non-resolved state carries `None`, never a
fabricated id), and that the ability/tendency adapters call the
existing, unmodified estimators with unchanged arguments (proving no
estimator math was altered). "Player changing teams" / "same player
across seasons" are covered indirectly via the Siakam
temporal-isolation test (same `player_id`, different `as_of_season`,
correct real season-scoped results — team change itself is Phase 13's
concern, already tested there).

## 16. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 331 tests — OK**
(315 carried over from Phase 13 + 16 new in `test_player_identity.py`).
No existing test was modified or removed.

## 17. Possession-engine-integration readiness

**READY WITH FLAGS.**

Why not unqualified READY: (1) the original six Phase 1-3 ability
attributes and all of Phases 4A-11 are reachable by `player_id` only
through this phase's adapter layer, not natively — a future possession
engine must consistently call the `_by_id` adapters rather than the raw
`estimate_*` functions, and nothing currently enforces that at the
type level (a careless future call could still bypass the adapter and
use a raw name); (2) `resolve_name_to_id`'s season-hint disambiguation
is real but narrow (only checks `shot_zone`/`role_off` cache presence,
not a general evidence search) — a genuinely ambiguous case outside
those two caches' coverage will correctly surface as `AMBIGUOUS` rather
than silently resolving, which is safe but means some real players may
need manual disambiguation before integration; (3) the legacy
`Player`/`loader.py`/`models.py` path remains fully name-keyed and
entirely outside this phase's scope, so a possession engine that needs
to bridge to the LEGACY simulator (rather than only this research
track) still has no id-based bridge to that side.

Why not NOT READY: every genuinely new (Phase 12A/13/14) interface is
already `player_id`-native; the ambiguity/collision risk is real but is
now *detected and surfaced*, never silently mishandled; temporal
isolation across a shared identity is verified directly; and the
calibration layer needs no changes at all.

**Recommended immediate next step** (not started, awaiting approval):
require future possession-engine-facing call sites to go through
`player_identity.py`'s adapters exclusively (a lint/convention rule, not
a code change to the adapters themselves), and extend
`_disambiguate_by_season_evidence` to check a wider set of already-cached
id-keyed sources before Phase 15 begins.
