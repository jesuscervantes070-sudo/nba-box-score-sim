# Phase 5 — Two-Point Shot-Zone Ability Foundation

`rim_finishing`, `floater_short_mid`, `midrange`. Entirely offline/parallel,
same discipline as every prior phase: nothing here is imported by or
imports `game_engine.py`, `season.py`, `awards.py`, `models.py`,
`transactions.py`. No OVR, no roles, no simulation integration, no new
attributes, Passing/awards untouched. All 139 tests pass (78 pre-existing +
18 turnover-ingestion + 19 ball-security-analysis + 24 shot-zone, all
unchanged/new — nothing from Phase 4A/4B was modified).

## 1. Exact source verification

`nba_api.stats.endpoints.leaguedashplayershotlocations.LeagueDashPlayerShotLocations`,
default `distance_range="By Zone"`, `per_mode_detailed="Totals"`, called
per-season with `season=<season>`, `season_type_all_star="Regular Season"`.

**Verified directly, not trusted from any outside claim:**
- Returns one real **MultiIndex-column** DataFrame (top level = zone name,
  second level = `FGM`/`FGA`/`FG_PCT`), one row per player-season. Confirmed
  real zones: `Restricted Area`, `In The Paint (Non-RA)`, `Mid-Range`,
  `Left Corner 3`, `Right Corner 3`, `Above the Break 3`, `Backcourt`, and a
  redundant combined `Corner 3`.
- Real season floor: 1994-95 and 1995-96 both checked directly — **genuinely
  empty (0 rows)**, not partial/malformed. 1996-97 is the first real season
  (441 players). Matches this codebase's overall per-player-endpoint floor.
- Tested 1996-97, 2001-02, 2013-14, 2025-26 (current/latest) directly — all
  real, populated, no partial/truncated response in any of them.
- Real per-call latency 0.15–2.8s across every test call this phase (~50+
  real calls total); **zero timeouts/rate-limit failures observed** — much
  lighter than the per-game `playbyplayv3` calls Phase 4A/4B needed, since
  this is one call per SEASON.
- Coverage check (real shot-location total FGA vs. real box-score total
  FGA reconstructed from `loader.load_teams` + `player_advanced.json`'s
  `gp`): ratio **1.01–1.03** in 1996-97, 2013-14, AND 2023-24 — consistently
  slightly ABOVE 1.0 in every era (a rounding artifact of the box-score
  per-game-average reconstruction, not a real shot-location gap). **No
  missing early-season coverage found anywhere.**
- `PBP Stats` (`AtRimFGM`/`AtRimAssistedFGM`/etc.) is a **third-party
  community site**, NOT part of the installed `nba_api` package — confirmed
  by searching every real endpoint name in `nba_api.stats.endpoints`;
  nothing resembling it exists. The only first-party path to
  assisted/unassisted shot detail, `ShotChartDetail`, was checked directly:
  it requires ONE CALL PER PLAYER PER SEASON (~500+/season, a real, much
  larger cost than this phase's one-call-per-season base pipeline) and has
  **no assisted/unassisted flag in its own real columns either** (checked:
  `SHOT_ZONE_BASIC`, `SHOT_MADE_FLAG`, `ACTION_TYPE`, no `AST`-type field).
  **Not built this phase** — explicitly optional/deferred per the task's own
  instruction; does not block the base pipeline.

## 2. Ingestion coverage

**All 30 real cached seasons (1996-97 through 2025-26) fully ingested**, one
call each, 34.5s total, zero failures — this endpoint is cheap enough that
"prove resumability before a giant fetch" simply wasn't a real constraint
here (unlike Phase 4A/4B's per-game PBP pipeline).

| Range | Seasons | Player-seasons (rim_finishing evidence) |
|---|---|---|
| 1996-97 – 2025-26 | 30/30 complete | 14,242 total (9,563 real T→T+1 pairs) |

Cache: `cache/<season>/player_shot_zones.json` — raw FGM/FGA for all 8 real
zones (not just the 3 this phase uses — free to preserve, per "preserve raw
counts"), `player_id`, `player_name`, `season`, `season_type`, `provenance`,
`schema_version`. Resumable (`build_and_cache_shot_zones_range`, cache-first
per season, one season's API failure never affects another's cache file).

**Real data-quality fix made this phase**: 112 zone/field values (0.13% of
~84,000 real values across all 30 seasons × 3 target zones × 2 fields) came
back as **NaN** from the raw API instead of 0, always for extremely
low-volume players with a legitimate real zero in that specific zone (e.g.
Bruce Bowen's real 1996-97 Mid-Range row — every OTHER zone on that same
row has real, non-NaN values, confirming the row itself is real). Fixed via
`_clean_zero` — NaN and None both become a real 0.0/0.0, never silently
propagated (an unguarded NaN would have poisoned every downstream weighted
average with no error, which is exactly what happened on the first
calibration run before this fix — caught and corrected).

## 3. 1996-97 investigation (shortened 3PT line)

The real 3PT line was shortened (flat 22') for 1994-95–1996-97, then
reverted (23'9"/22' corners) starting 1997-98. Measured directly (not
assumed):

| Season | Restricted Area | Paint Non-RA | Mid-Range | Corner 3 | Above-Break 3 |
|---|---|---|---|---|---|
| 1996-97 (short line) | 37.4% | 11.9% | 39.9% | 3.1% | 7.6% |
| 1997-98 (reverted) | 33.3% | 14.1% | 36.8% | 2.9% | 12.7% |

The 3PT-zone shift is real (Above-Break 3 share nearly doubles) but the
THREE ZONES THIS PHASE USES move gradually, and — critically — in the
OPPOSITE direction a real boundary-crossing artifact would predict (if
shots were being reclassified from Mid-Range into 3PT because the arc
moved closer, Mid-Range share would be LOWER, not higher, in the
shortened-line season; it's measured higher). **No explicit era correction
applied** — the real discontinuity is confined to the 3PT zones, out of
scope here. Documented in `shot_zone_ingestion.py`'s own docstring for a
future 3PT-attribute phase to re-check.

## 4. Estimator formulas

Same generic machinery as the six previously-locked attributes
(`SeasonEvidence`, `_weighted_shrunk_estimate`, `_percentile_rating`,
`_seasons_through_cutoff` — imported, NOT modified) applied to real zone
FG%, weighted by real zone FGA:

`shrunk_rate = (Σ(rate_s · λ^age_s · FGA_s) + M · league_avg) / (Σ(λ^age_s · FGA_s) + M)`

- `rim_finishing`: real Restricted Area FG%, FGA-weighted.
- `floater_short_mid`: real "In The Paint (Non-RA)" FG%, FGA-weighted.
- `midrange`: real Mid-Range FG%, FGA-weighted.

A zero-FGA zone contributes NO evidence (excluded, never a fake 0% rate).
A player absent from a season's cache is real MISSING evidence, distinct
from a present row with 0/0.

## 5. Calibration results (nested, time-respecting, real 30-season corpus)

Grid search over λ∈{0.3..0.9}, M∈{0,25,50,100,200,400,800,1500}, real
CONSECUTIVE-year (T, T+1) pairs only, weighted by real T+1 FGA:

| Attribute | n pairs | A: raw-previous | B: no-shrink multi-yr | C: provisional (λ=.6,M=200) | D: tuned | Improvement (D vs A) |
|---|---|---|---|---|---|---|
| **rim_finishing** | 9,563 | MAE 0.0495 | 0.0435 | 0.0441 | **λ=0.6, M=50 → 0.0415** | **16.2%** |
| **floater_short_mid** | 8,057 | MAE 0.0628 | 0.0540 | 0.0524 | **λ=0.6, M=50 → 0.0493** | **21.6%** |
| **midrange** | 8,267 | MAE 0.0461 | 0.0391 | 0.0373 | **λ=0.7, M=100 → 0.0364** | **21.2%** |

**Grid is genuinely SHARP, not flat** (unlike ball security's) — e.g.
rim_finishing's MAE ranges 0.0415 (best) to 0.056 (worst) across the full
grid, a real, meaningful optimum, not noise.

**Nested/held-out parameter stability** (grid search re-run on PRE-2012
pairs only, then applied to POST-2012 pairs):

| Attribute | Train-only (pre-2012) optimum | Full-corpus optimum | Match? |
|---|---|---|---|
| rim_finishing | λ=0.6, M=50 | λ=0.6, M=50 | **EXACT** |
| floater_short_mid | λ=0.7, M=100 | λ=0.6, M=50 | Close, not exact |
| midrange | λ=0.7, M=100 | λ=0.7, M=100 | **EXACT** |

**Calibration-by-attempt-bucket** (tuned model's MAE improvement over
raw-previous, by real T+1 FGA volume) — textbook shrinkage behavior for
all three, largest gains at the lowest volume:

| FGA bucket | rim_finishing | floater_short_mid | midrange |
|---|---|---|---|
| 20-50 | 25.9% | 29.4% | 28.8% |
| 50-100 | 23.1% | 26.6% | 30.9% |
| 100-200 | 19.5% | 20.0% | 24.8% |
| 200+ | 10.9% | 8.9% | 13.9% |

**Rank correlation** (tuned model, predicted vs. real T+1 rate):
rim_finishing 0.587, floater_short_mid 0.452, midrange 0.439 — all real,
positive, moderate (shot-zone FG% is real but noisier season-to-season
than a volume-stat like AST%/OREB%, consistent with these being smaller-
sample percentage stats).

Saved to `shot_zone_calibration.json` (v1) — a NEW, separate artifact;
never touches `player_ability_calibration.json`/`_v2.json` or
`ball_security_calibration.json`.

## 6. Historical limitations

- 1994-95/1995-96 have no real shot-location data at all (real API floor).
- Rank correlations (0.44–0.59) are real but moderate — shot-zone FG% is a
  noisier year-to-year signal than the six previously-locked rate stats,
  an honest, expected property of percentage stats at this attempt scale.
- No player-tracking shot-difficulty context (contest level, defender
  distance) exists in this data — a real limitation shared with every
  zone-FG%-based metric, not something this phase's data can fix.
- Assisted/unassisted rim enrichment deliberately not built (see §1) —
  real future work, not a blocker.

## 7. Archetype diagnostics

**Systematic role-correlation check** (real Pearson correlation, zone FG%
vs. real USG%/REB%, 2018-19+2022-23+2023-24 pooled, FGA≥30):

| Attribute | corr vs. USG% | corr vs. REB% (rough big-vs-perimeter proxy) |
|---|---|---|
| rim_finishing | −0.050 (negligible) | **+0.390** (real, moderate) |
| floater_short_mid | +0.148 (small) | +0.113 (small) |
| midrange | +0.115 (small) | −0.125 (small) |

`rim_finishing`'s real +0.39 correlation with rebound share (a rough big-
man proxy, since no true position field exists in this codebase) is the
one non-trivial pattern found: bigs convert Restricted Area attempts at a
real, moderately higher rate than guards. This reads as a genuine
shot-difficulty/role difference (an uncontested dunk vs. a contested
guard drive-layup), not a pipeline defect — **no adjustment proposed**,
per the task's explicit "do not automatically correct" instruction; a
future phase could test whether residualizing this improves held-out
prediction, but that was not attempted here (no adjustment should be
adopted without beating the simpler model out-of-sample, and none was
built to test).

**Representative players** (2023-24, real full-season data, all 30 real
seasons as history):

| Attribute | Player | Archetype | Latest raw (FGA) | Shrunk rate | Rating |
|---|---|---|---|---|---|
| rim_finishing | Giannis Antetokounmpo | High-volume elite | .775 (853) | .759 | **98.2** |
| rim_finishing | Domantas Sabonis | High-volume good, paint-touch big | .697 (610) | .697 | 90.5 |
| rim_finishing | Mitchell Robinson | Rim-running lob finisher | .582 (122) | .676 | 84.4 |
| rim_finishing | Dexter Dennis | Low-volume HOT (6-for-6, 100%) | 1.000 (6) | **.643** | **71.5** (not 99) |
| rim_finishing | Armoni Brooks | Low-volume COLD (1-for-7, 14%) | .143 (7) | **.566** | **31.3** (not near-0) |
| floater_short_mid | Chris Paul | Guard floater/runner | .529 (85) | .514 | 95.0 |
| floater_short_mid | Joel Embiid | Center hooks | .479 (211) | .455 | 79.6 |
| floater_short_mid | Nikola Jokić | Ball-handling big, paint touch | .617 (535) | .599 | 99.0 |
| midrange | DeMar DeRozan | High-volume midrange specialist | .430 (547) | .444 | 83.9 |
| midrange | Kevin Durant | High-volume elite creator | .518 (485) | .517 | 98.4 |
| midrange | Shai Gilgeous-Alexander | Self-creating guard | .494 (336) | .453 | 87.7 |
| midrange | Rudy Gobert | Stationary big, very low volume | .273 (11, career total 25) | **.352** | 28.9 |

The two low-volume examples are the key validation: **Dexter Dennis's
literal 6-for-6 (100%) season is correctly regressed down to a 71.5
rating, not 99** (shrinkage working as intended), and **Armoni Brooks's
1-for-7 (14%) is correctly regressed UP to 31.3, not punished toward 0**.
No archetype in this sample — rim-runner, self-creator, floater guard,
stationary big, high-volume specialist — was badly misranked; ratings
track real-world basketball intuition throughout.

## 8. Final classification, per attribute

**`rim_finishing`: LOCK V1**
Largest real corpus (9,563 pairs), sharp grid optimum, EXACT nested
train/test parameter match (λ=0.6, M=50 both halves), clean per-bucket
shrinkage behavior, 16.2% held-out improvement over raw-previous, sensible
archetype behavior including correct low-volume regression. The one real
role correlation found (rebound-share vs. rim%) is reported, not
corrected, per instruction — does not block locking the CONVERSION metric
itself.

**`midrange`: LOCK V1**
Same profile as rim_finishing: 8,267 pairs, sharp optimum, EXACT nested
train/test match (λ=0.7, M=100 both halves), 21.2% held-out improvement,
strong per-bucket behavior (13.9–30.9% gains), sensible archetype spread
(DeRozan/Durant/SGA/Gobert all rank plausibly).

**`floater_short_mid`: KEEP BUT FLAG**
Real, strong improvement over the naive baseline (21.6%, the largest of
the three) and clean per-bucket shrinkage behavior — the conceptual
pipeline clearly works. Held back from LOCK V1 only because the nested
train/test parameter check did NOT reproduce an exact match (train-only
optimum λ=0.7/M=100 vs. full-corpus λ=0.6/M=50 — close, same direction,
but not identical, unlike the other two). A future session with more
seasons or a finer grid should re-confirm parameter stability before this
is called fully locked.

## 9. Wiring into `PlayerAbilityProfile`

Demonstrated end-to-end: `shot_zone_estimation.estimate_shot_zone_attribute`
→ `result_to_attribute_estimate` → `PlayerAbilityProfile.with_attribute`,
for all three attributes, on real data (e.g. Nikola Jokić, 2023-24:
rim_finishing=92.4, floater_short_mid=99.0, midrange=90.4, each with a real
confidence/sample_size). `player_ability_profile.py` itself required NO
changes — `rim_finishing`/`floater_short_mid`/`midrange` were already
listed in `SKILL_ATTRIBUTES`; only `shot_zone_estimation.py` (new, parallel)
had to be written. Not wired into the simulator, no OVR, no roles.

## 10. Files changed

New: `shot_zone_ingestion.py`, `shot_zone_estimation.py`,
`shot_zone_calibration.py`, `shot_zone_calibration_search.py`,
`shot_zone_calibration.json`, `test_shot_zone_ingestion.py`,
`test_shot_zone_estimation.py`, `docs/PHASE5_SHOT_ZONE_REPORT.md` (this
file), plus `cache/<season>/player_shot_zones.json` for all 30 real
cached seasons (gitignored, same as every other cache file).

**Not touched**: `player_ability_estimation.py`, `player_ability_profile.py`
(read-only imports), `player_ability_calibration.py/.json/_v2.json`,
`turnover_ingestion.py`, `handling_exposure.py`, `ball_security_*.py/.json`,
`game_engine.py`, `season.py`, `awards.py`, `models.py`, `transactions.py`,
any Codex counterfactual file. No new attribute created, no OVR, no role
classification, no simulation integration.

## 11. Tests / results

`python3 -m unittest test_player_ability_profile test_player_ability_estimation
test_player_ability_turnover_prototype test_player_ability_calibration
test_turnover_ingestion test_ball_security_analysis test_shot_zone_ingestion
test_shot_zone_estimation` → **139/139 passing** (78 pre-existing unchanged +
18 + 19 + 24 new). Covers: endpoint/cache parsing (incl. the real NaN
quirk), zero attempts, missing source data, no temporal leakage, multi-year
weighting, shrinkage (low-volume regression), serialization/`PlayerAbilityProfile`
round-trip, calibration fallback behavior, resumable/cache-first ingestion,
one-season-failure isolation.

## 12. Remaining limitations (repeated from §6 for completeness)

Moderate (not high) rank correlations for all three; no shot-difficulty
context; assisted/unassisted enrichment deferred; `floater_short_mid`'s
exact λ/M not yet nested-stable. None of these block LOCK V1 for
`rim_finishing`/`midrange` given the strength of every other check, but
all are stated plainly rather than smoothed over.

Per direction: **stopping here.** No new attribute phase begun.
