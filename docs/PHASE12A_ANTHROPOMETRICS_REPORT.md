# Phase 12A — Player Anthropometrics Foundation

Scope: real, verified `height`, `standing_reach`, `wingspan`, `mass` only.
Real units throughout (inches, lbs), never a 0-99 rating. Physical
measurements are NOT basketball skills — no skill estimator is read or
modified anywhere in this phase's code.

## 1. Repository changes

| File | Status | Purpose |
|---|---|---|
| `anthropometrics_ingestion.py` | Built (previous session) | Real `draftcombineplayeranthro` + `commonteamroster` fetch/cache, atomic writes |
| `anthropometrics_analysis.py` | Built (this session) | Identity linkage, coverage tables, mass-longitudinal diagnostics, hand-rolled OLS + TRAIN/HELDOUT backtest |
| `anthropometrics_profile.py` | Built (this session) | `PhysicalObservation` + `PlayerPhysicalProfile` — the minimal physical-state object, keyed by real `player_id` |
| `anthropometrics_estimation.py` | Built (this session) | `build_physical_profile(player_id, as_of_season)` — source hierarchy, temporal-leakage prevention, regression fallback |
| `test_anthropometrics_ingestion.py` | Built (previous session) | 9 tests |
| `test_anthropometrics_profile.py` | Built (this session) | 15 tests |

None of `player_ability_profile.py`, `player_ability_estimation.py`, or
`player_tendencies_*.py` were imported, read, or modified. No new schema
slot was added to `PlayerAbilityProfile` or `RoleProfile`.

Architecture actually implemented: `PlayerPhysicalProfile` (as
anticipated), keyed by the real stable NBA `player_id` (confirmed usable
directly — see Sec. 4), holding one selected `PhysicalObservation` each
for height/wingspan/standing_reach and a **list** of `PhysicalObservation`
for mass (never collapsed to one number — see Sec. 6).

## 2. Data source verification

| Source | Fields observed (real) | Years/coverage tested | Result |
|---|---|---|---|
| `draftcombineplayeranthro` | `PLAYER_ID`, `PLAYER_NAME`, `POSITION`, `HEIGHT_WO_SHOES`, `HEIGHT_W_SHOES`, `WINGSPAN`, `STANDING_REACH`, `WEIGHT` | 2000-2025 (26 calls; 2001 = real 0-row year) | Real floor confirmed 2000. `HEIGHT_W_SHOES` is NOT continuously populated (present ~2010-2020, absent 2000 and 2023+) — `HEIGHT_WO_SHOES` (barefoot) is the only field with continuous 2000-2025 coverage, adopted as primary. |
| `commonteamroster` | `PLAYER_ID`, `PLAYER`, `HEIGHT` ("6-5" string, LISTED), `WEIGHT` (lbs, LISTED), `POSITION` | 6 representative seasons: 1996-97, 2005-06, 2013-14, 2018-19, 2022-23, 2023-24 (all 30 real teams each) | Same endpoint `data_source.fetch_team_rosters` already calls; that path discards HEIGHT/WEIGHT/PLAYER_ID — this module re-extracts them into a separate cache file, `rosters.json` untouched. A representative (not full 30-season) sample was ingested deliberately for cost reasons; 2022-23 was added this session specifically to get one genuinely adjacent-season pair for the mass-staleness check (Sec. 6). |
| `commonplayerinfo` | `PLAYER_ID`, `DISPLAY_FIRST_LAST`, `HEIGHT`, `WEIGHT`, `POSITION`, `BIRTHDATE` | Spot-checked (Grayson Allen, Stephen Curry, plus several non-NBA combine invitees) | Real, confirmed: returns exactly one static listed HEIGHT/WEIGHT snapshot per player (no per-season history), so it is NOT useful for the mass time-series goal — `commonteamroster` already covers that need per-season. Confirmed it FAILS LOUDLY (raises, does not silently return empty) for a `player_id` with no real NBA game record — used as an identity-linkage check in Sec. 4, not adopted as an ingestion source. |

No API failure was silently treated as a missing observation anywhere —
`fetch_combine_anthro`/`fetch_roster_physicals` both raise after retries
exhausted; `build_and_cache_*` prints and returns `None` only for a
genuinely empty (0-row) real response, never for a real failure.

## 3. Coverage table

| Trait | Source | Period tested | Sample size | Missingness |
|---|---|---|---|---|
| height (barefoot) | combine | 2000-2025 (25 real years, 2001 excluded) | 1,717 combine invitees | 1,659/1,717 have `HEIGHT_WO_SHOES` (96.6%) |
| height (listed) | roster | 6 representative seasons | 1,660 unique players (5 widely-spaced seasons) + 508 (2022-23) | See per-season table below |
| wingspan | combine only (no roster source) | 2000-2025 | 1,717 | 1,659/1,717 (96.6%) |
| standing_reach | combine only | 2000-2025 | 1,717 | 1,658/1,717 (96.6%) |
| mass (combine) | combine | 2000-2025 | 1,717 | 1,657/1,717 (96.5%) |
| mass (roster, per-season) | roster | 6 representative seasons | see below | see below |

Per-year combine coverage is essentially flat at ~95-100% within a given
year once the invited-player count is fixed (the ~3-5% gap is real,
non-random: some combine attendees skip individual anthropometric
stations — confirmed directly, e.g. 2023 has 81 invitees but only 67
real `HEIGHT_WO_SHOES` rows). Joint height+wingspan availability tracks
height availability almost exactly (1,658 vs 1,659) — wingspan is
essentially never missing when height is present.

## 4. Identity results

**Direct ID correspondence is real but incomplete, and improves sharply
over time** — this is a genuine finding, not the naive "IDs always
match" assumption:

| Draft-class era | Combine PLAYER_ID → real static `player_id` match rate |
|---|---|
| 2000-2008 | 38-53% |
| 2009-2017 | 76-91% |
| 2018-2025 | 71-92% |
| **Overall (2000-2025)** | **72.2%** (1,240 / 1,717) |

Investigated directly (not assumed): the unmatched 2000-2008 IDs are
**small integers** (e.g. `12017`, `12024`) that do not fall anywhere in
the real static player_id space (`min=2, max=1643141`, and specifically
no ids exist in the 12000-12100 range at all). A name-based fallback
match against the real static player list resolved only ~6% of a sampled
unmatched cohort (10/162 across 2000/2003/2006/2008); the remaining ~94%
are real combine invitees who **never appeared in an NBA game** (confirmed
independently: `commonplayerinfo` fails with no result set for these IDs
too, both for old small-integer IDs and for modern large-integer
unmatched IDs like `1628965`). **Conclusion: the low match rate for
early years is NOT a join bug — it reflects genuinely non-NBA combine
attendees, concentrated in earlier, less-selective combine classes.**
Players who WENT ON to play in the NBA are matched reliably by direct ID;
no separate crosswalk/fallback-identity system (name+birthdate+draft
year) was needed for that population. No ambiguous one-to-many name
collision was found in the sampled fallback check. This module therefore
uses **direct ID lookup only** — a name-based fallback was built and
tested for this diagnostic but is NOT wired into `anthropometrics_estimation.py`,
since the cases it would resolve are exactly the non-NBA-player rows the
simulator has no use for anyway.

Roster-cache identity is unambiguous by construction (real `PLAYER_ID`
column from `commonteamroster`, one row per real roster slot).

## 5. Height findings

- Barefoot combine height (`HEIGHT_WO_SHOES`) adopted as the primary,
  MEASURED_COMBINE-tier field — the only one with continuous era
  coverage (Sec. 2).
- Real, measured bias: roster-**listed** height runs **+1.014 inches**
  above real combine barefoot height on matched players (stdev 0.671,
  n=723, min -1.25 / max +3.25). This is consistent with (but does not
  prove) shoes-on listed height, rounding to the nearest half-inch by
  some teams, or simple team over-reporting.
- **No fixed shoe-adjustment constant is applied.** The measured
  variance (stdev 0.67") is judged too large relative to the mean bias
  (1.01") to justify a blanket point-correction for individual players —
  per explicit instruction not to invent a universal constant without
  strong real validation. `MEASURED_ROSTER` observations carry this
  finding as a `note`, unadjusted.
- No repeated-measurement height conflicts within a single tier were
  investigated further (a player has exactly one combine height record
  by construction); cross-tier conflict (combine vs. roster) is the one
  documented above.

## 6. Mass longitudinal diagnostic

Two real, separate checks were run (deliberately not conflated — a
multi-year-gap comparison and a genuine single-year-apart comparison
answer different questions):

**Wide-gap comparison** (players appearing in ≥2 of the 5 originally-cached,
widely-spaced seasons: 1996-97/2005-06/2013-14/2018-19/2023-24):
- 1,660 unique players total across the 5 seasons; 483 appear in ≥2.
- Of those 483: **202 (41.8%) show an IDENTICAL listed weight in every
  season they appear in**, despite gaps as large as ~9-27 years.
- Mean (max−min) change among the 483: 5.71 lbs; median 2.0 lbs; max
  38 lbs.

**True adjacent-season comparison** (2022-23 → 2023-24, added this
session specifically for this check):
- 425 players present with a real weight in both seasons.
- **409 (96.2%) show an IDENTICAL listed weight year-over-year.**
- Mean change: +0.28 lbs; stdev 2.49 lbs.

**This is treated as evidence, not proof, of roster-page staleness** —
per explicit instruction not to auto-label repeated values as stale.
The adjacent-season number (96.2% identical) is a much stronger signal
than the wide-gap number and is the one worth taking seriously: real
season-to-season physical mass change this small and this rare is
implausible for an entire league of professional athletes, so the most
defensible reading is that `commonteamroster`'s WEIGHT field is updated
infrequently for most players, not that NBA players' mass is
overwhelmingly static. This is recorded as a `note` on every
`MEASURED_ROSTER` mass observation, not as a `STALE` evidence mode (no
such mode was created — doing so would require confirming staleness
per-player, which this phase's data cannot do).

Architectural consequence: mass is stored as a **list** of dated
`PhysicalObservation`s (combine snapshot + one entry per usable roster
season), never averaged or collapsed — `PlayerPhysicalProfile.latest_mass`
returns the most recent entry only.

## 7. Wingspan / standing_reach measured-data diagnostics

Covered in Sec. 3's coverage table: 96.6% joint availability with height
across all 1,717 real combine rows, 2000-2025. No anomalous
out-of-physical-range values were found in a manual scan of the
extremes (min/max wingspan-height differences stayed within a
plausible ±8-10 inch band for the full dataset); no parser errors
detected (the earlier-session empty-string WEIGHT bug, already fixed in
`anthropometrics_ingestion.py`, was the only real parsing defect found in
either module across this project).

## 8. Inference backtest

Real chronological TRAIN (draft classes < 2018) / HELDOUT (≥ 2018) split
— genuine generalization test, not a random shuffle. Hand-rolled OLS
(Gaussian elimination on the normal equations, same no-ML-library
convention as `playmaking_vision_analysis.py`).

**Wingspan ~ height** (n_train=1,120, n_heldout=538):
| Model | MAE | RMSE | Bias | Max abs error |
|---|---|---|---|---|
| Mean-only baseline | 3.01 | 3.77 | -0.01 | 15.91 |
| height-only OLS | **1.73** | 2.15 | +0.15 | 6.51 |

Heldout MAE improvement over baseline: 1.27 inches — real, decisive.
Coefficients (fit on ALL real combine data for production use):
`wingspan = 4.49 + 1.003 × height`.

**Standing_reach ~ height + wingspan** (n_train=1,119, n_heldout=538):
| Model | MAE | RMSE | Bias | Max abs error |
|---|---|---|---|---|
| Mean-only baseline | 3.66 | 4.61 | -0.18 | 19.04 |
| height-only OLS | 1.42 | 1.78 | +0.04 | 5.54 |
| height + wingspan OLS | **1.09** | 1.36 | -0.04 | 4.37 |

Adding wingspan produces a real, material improvement over height-only
(1.42 → 1.09 MAE) — both predictors carry independent information.
Production coefficients: `standing_reach = -2.98 + 0.811 × height +
0.529 × wingspan`.

**Judgment**: standing_reach's inference (1.09" heldout MAE, ~1.4% of a
typical ~80" reach) is tight enough to be a defensible fallback estimate.
Wingspan's inference (1.73" heldout MAE on a typical ~7" wingspan-minus-height
gap) is real and beats baseline by a wide margin, but the residual is
large relative to the quantity being predicted (a real max heldout error
of 6.5"), so it should be read as a moderate-confidence fallback, not a
precise individual measurement — `anthropometrics_estimation.py` marks
every `INFERRED_REGRESSION` observation with its real heldout MAE in its
`note` field so downstream code is never misled about precision.
Uncertainty is NOT collapsed into a single confidence scalar; the report
number (heldout MAE) is the intended uncertainty signal for now.

## 9. Test results

`python3 -m unittest discover -p "test_*.py"` → **Ran 295 tests — OK**
(280 tests carried over from before this phase's estimation/profile work
+ 15 new tests in `test_anthropometrics_profile.py`, on top of the 9
already-passing tests in `test_anthropometrics_ingestion.py` counted in
the 280). Covered: `PhysicalObservation` invariants (UNAVAILABLE must
not carry a value, MEASURED must carry one, INFERRED must carry a model
version), round-trip serialization, mass list never collapsed,
`latest_mass` picks most-recent not average, source-hierarchy preference
(combine over roster), roster fallback when no combine, **two distinct
temporal-leakage tests** (a combine record dated after `as_of_season`;
a roster record dated after `as_of_season`), missing-player →
UNAVAILABLE (not zero), and wingspan-inferred-only-when-height-known
with the correct physical direction (wingspan > height).

## 10. Limitations / risks

- Roster-listed height/weight coverage is a 6-season sample, not a full
  30-season backfill — a player active only in an un-cached season has
  no `MEASURED_ROSTER` fallback available today (would need additional,
  cheap re-ingestion of more seasons via the existing
  `build_and_cache_roster_physicals_range`, deferred per this phase's
  own efficiency scope).
- The mass-staleness finding (Sec. 6) is suggestive, not proven per-player
  — no per-player "is this actually stale" signal exists; treating every
  identical-weight pair as literally unchanged mass would be a
  real, unverified assumption if a future phase makes that leap.
- Wingspan inference (Sec. 8) has a real, non-trivial residual tail
  (max heldout error 6.5") — not suitable for a use case that needs
  tight individual precision without further model work.
- Pre-2000 players have height only from whatever roster seasons happen
  to be cached, and no wingspan/standing_reach at all unless inferred
  from that roster-listed height (with the added, unquantified
  listed-vs-barefoot bias baked in, since the regression was fit on
  barefoot combine height, not listed height) — this is a real,
  documented mismatch: inferring wingspan from a *listed* height via a
  model fit on *barefoot* height silently imports the roster bias into
  the wingspan/reach estimate. This is a genuine limitation, not fixed
  this phase (would need a second regression fit on listed height, out
  of scope here).
- No biological growth/aging model exists for height (a real
  representational simplification, not a validated claim that height
  never changes).

## 11. Classification recommendation

| Trait | Classification | Basis |
|---|---|---|
| `height` | **LOCK V1** | Real, continuous 96.6% MEASURED_COMBINE coverage since 2000; well-understood, labeled roster-listed fallback with a measured (not invented) bias for earlier eras. |
| `mass` | **KEEP** | Real time-series representation with genuine per-season granularity where roster data exists; the staleness question (Sec. 6) is a real open issue but does not block usefulness as a physical-state field, since every observation is honestly dated and sourced. |
| `standing_reach` | **KEEP** | Real 96.6% MEASURED_COMBINE coverage since 2000 (same as height); real, tight (1.09" heldout MAE) fallback inference where combine data is unavailable. |
| `wingspan` | **KEEP BUT FLAG** | Real 96.6% MEASURED_COMBINE coverage, but its fallback inference has a materially wider real residual (1.73" heldout MAE, outliers to 6.5") than standing_reach's, and the listed-height-bias-import issue (Sec. 10) is unresolved for pre-combine-era players. |

(This does not force the expectation stated in the task prompt — height
came out LOCK V1 as expected, but standing_reach performed comparably
well to height rather than needing a flag, while wingspan is the one
flagged, based on the actual measured heldout numbers above rather than
intuition.)

## 12. Next step

Recommended immediate next step: **run the deferred, explicitly
diagnostic-only correlation pass** (standing_reach vs. `rim_protection`,
wingspan vs. `defensive_playmaking`, mass vs. `offensive_rebounding`/
`defensive_rebounding`, height vs. `rim_finishing`) as a small, separate,
read-only follow-up — report-only, must not adjust any skill estimator
(per explicit standing instruction). This was scoped as part of Phase
12A's original ask but hit a real, concrete blocker rather than being
skipped for time: every existing skill estimator
(`rim_protection_estimation.py`, `player_ability_estimation.py`, etc.)
is keyed by player **name** (a known, documented limitation — see
`player_ability_profile.py`'s own "STABLE IDENTITY" note), while this
phase's `PlayerPhysicalProfile` is keyed by real, stable `player_id` per
explicit instruction. No name↔player_id crosswalk currently exists in
this repo. Building one (even a small one, for the diagnostic pass only)
is a real, separate piece of work — not a redesign of either system, but
also not "free," so it was left as the next step rather than folded into
this phase silently. It is the natural, narrow completion of Phase 12A
rather than a new phase. **Do not begin Phase 13** (lineup/role
inference) or any physical trait beyond the four built here without
explicit approval.
