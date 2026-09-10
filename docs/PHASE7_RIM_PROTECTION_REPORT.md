# Phase 7 — Rim Protection

`rim_protection`. Entirely offline/parallel: nothing here is imported by or
imports `game_engine.py`, `season.py`, `awards.py`, `models.py`,
`transactions.py`. No POA/perimeter defense, defensive versatility,
playmaking vision, shot creation, physical ratings, development, OVR, or
simulation integration. Phase 6 (`foul_drawing`/`foul_discipline`, both
KEEP BUT FLAG) is unchanged — not revisited. All 206 tests pass (177
pre-existing from Phases 4A-6 + 7 rim-ingestion + 13 rim-analysis + 9
rim-estimation).

## 1. Source verification

**`leaguedashptdefend`** (`defense_category="Less Than 6Ft"`,
`per_mode_simple="Totals"`) — the SAME real endpoint `data_source.py`
already uses for `ratings.py`'s DPOY formula (there, only as a `PerGame`
rate with no raw counts cached). Re-fetched this phase at `Totals` for
real sample-size weight. Real, verified columns: `CLOSE_DEF_PERSON_ID`,
`PLAYER_NAME`, `GP`, `FREQ`, `FGM_LT_06`, `FGA_LT_06`, `LT_06_PCT`,
`NS_LT_06_PCT`, `PLUSMINUS`. Real floor: checked directly, 2012-13
returns 0 rows, 2013-14 returns real populated data (471 players) — same
floor as every other `leaguedashpt*` endpoint this codebase already uses.
**Cheap**: 13 real tracking-era seasons (2013-14 through 2025-26) fully
ingested in 10.4 seconds, one call each — a genuinely trivial full
historical backfill within its real floor, unlike Phase 4A/4B/6's
per-game PBP pipelines.

`NS_LT_06_PCT` ("Normal Shooting") is the NBA's own real, first-party
expected FG% for this exact shot type — the closest real analogue this
codebase has access to for "expected FG% if exposed." `PLUSMINUS` =
`LT_06_PCT − NS_LT_06_PCT`, a real, NBA-computed residual (NOT computed
by this project). **This satisfies the task's own request to use a real
expected-FG residual rather than fabricating one** — sign-flipped in
this phase's cache so higher = better, this project's universal
convention.

**Checked and NOT built**: a per-defender opponent-rim-ATTEMPT-frequency
on/off signal (mechanism A, attempt deterrence) has no real field
anywhere in this codebase's API access — would require a genuine
per-player on/off query (a materially larger, uncontrolled job). A
coarse, real TEAM-level proxy (`leaguedashteamshotlocations`, Opponent
measure type, `Restricted Area_OPP_FGA`) was fetched instead (cheap, one
call/season) for the scheme-bias check (§9) only — **documented as
FUTURE ONLY** for a real per-player deterrence signal. Second-Spectrum-
level "expected FG conditional on this specific opponent's shooting
profile" is real but proprietary — not accessible through this
codebase's real API access, **documented as FUTURE ONLY**, not
fabricated.

## 2. Data coverage

| Season | Players | Season | Players |
|---|---|---|---|
| 2013-14 | 471 | 2020-21 | 535 |
| 2014-15 | 485 | 2021-22 | 581 |
| 2015-16 | 470 | 2022-23 | 529 |
| 2016-17 | 481 | 2023-24 | 562 |
| 2017-18 | 524 | 2024-25 | 562 |
| 2018-19 | 517 | 2025-26 | 576 |
| 2019-20 | 524 | | |

All 13 real tracking-era seasons FULLY complete (not partial-sampled —
this endpoint's real cost made a full backfill trivial, unlike prior
phases' per-game pipelines). 12 real consecutive-year (T,T+1) transitions
available — the densest corpus of any phase so far.

## 3. Semantic definition (formalized taxonomy)

| Concept | Definition | This phase's treatment |
|---|---|---|
| A. Rim Protection Ability | Technique/timing/positioning/suppression when actually contesting | **What this phase estimates** |
| B. Rim-Protection Opportunity | How often placed to protect the rim | `rim_fga_defended` (real, tracked separately) |
| C. Physical Traits | Reach/wingspan/vertical/mass | Not modeled (no real per-player field exists) |
| D. Team Scheme | Drop/switch/zone/help | Investigated as contamination risk (§9), not corrected |
| E. Defensive Playmaking | Block/steal event generation | Existing, separate attribute; overlap quantified (§7) |
| F. Foul Discipline | Legal-contest ability | Existing Phase 6 attribute; cross-referenced (§8) |

## 4. Candidate metrics — what each actually measures

| Metric | What it measures | Used how |
|---|---|---|
| Block rate (BLK/36) | Real EVENT generation (a swat) | Cross-referenced, NOT re-estimated (belongs to `defensive_playmaking`) |
| `rim_suppression_plusminus` (real NBA residual) | Real FG% ALTERATION when contesting, already shot-type-normalized | **Primary signal this phase uses** |
| Raw `LT_06_PCT` (un-residualized) | Contaminated by opponent quality/shot difficulty | Rejected — `NS_LT_06_PCT` normalization already does this better |
| `rim_fga_defended` | Real OPPORTUNITY count | Denominator/weight, not an ability signal |

## 5. Denominator (weight) comparison

`suppression_rate` is already a real percentage-point RATE (not itself
divided by a chosen denominator) — what's compared here is which real
quantity should serve as the shrinkage-prior SAMPLE-SIZE WEIGHT (all
three keep the rate in the same units, so no Phase-6-style scale
artifact applies, but the comparison was still run to avoid assuming):

| Weight | n pairs | weighted MAE |
|---|---|---|
| **rim_fga_defended** | 4,106 | **0.04300** |
| minutes | 4,099 | 0.04517 |
| def_possessions_proxy | 4,099 | 0.04514 |

`rim_fga_defended` wins (real, direct exposure measure) — adopted.

## 6. Suppression vs. deterrence analysis

**Conversion suppression** (mechanism B): real, direct, estimable —
`rim_suppression_plusminus`, the primary signal this phase uses.

**Attempt deterrence** (mechanism A): investigated at the real TEAM level
only (§1) — correlation between individual suppression/opportunity and
real team-level opponent rim-attempt frequency is **near-zero**
(−0.10 to +0.09 across 2018-19/2022-23/2023-24, see §9) — no detectable
team-level deterrence signature strong enough to isolate a per-player
component from data available this phase. **Not built as a second
internal component** — a real per-player on/off ingestion job would be
needed, explicitly deferred (§17).

## 7. Blocks overlap (defensive_playmaking)

Real correlation, rim-suppression rate vs. real BLK/36, all four
cross-check seasons:

| Season | corr | rank corr | R² (variance explained by blocks) |
|---|---|---|---|
| 2013-14 | 0.646 | 0.683 | 42% |
| 2018-19 | 0.525 | 0.563 | 28% |
| 2022-23 | 0.502 | 0.492 | 25% |
| 2023-24 | 0.517 | 0.559 | 27% |

Real, moderate overlap (25-42% of variance) but **58-75% of variance in
rim-suppression is NOT explained by block rate** — confirmed incremental
signal, not a rediscovery. Concrete example (§13): Giannis Antetokounmpo
2023-24 has modest BLK/36 (1.13) but real elite suppression (94.6 rating)
— exactly the "mobile defender, fewer blocks, effective contests" case
the task described.

## 8. Foul interaction

Real correlation, rim-suppression rate vs. real (Phase 6, OVERALL
shooting-foul-committed rate — NOT rim-specific, a stated limitation)
foul rate:

| Season | corr |
|---|---|
| 2013-14 | 0.243 |
| 2018-19 | 0.137 |
| 2022-23 | 0.134 |
| 2023-24 | 0.080 |

Small, real, and DECREASING over time — no evidence that apparent
suppression is achieved by fouling (if it were, this correlation would
be large and negative, since fouling more should reduce a defender's
clean-contest opportunities, or the metric would be inflated by removing
tough shots via whistle — neither pattern appears). Real, modest,
positive correlation is consistent with physical/aggressive defenders
both contesting well AND fouling somewhat more — not a contamination
concern at this magnitude.

## 9. Team/scheme bias

Real correlation, individual suppression vs. real TEAM-level context:

| Signal | 2018-19 | 2022-23 | 2023-24 |
|---|---|---|---|
| corr(suppression, team opponent rim-FGA freq) | −0.102 | +0.085 | −0.065 |
| corr(opportunity, team opponent rim-FGA freq) | −0.021 | +0.070 | −0.023 |
| corr(suppression, team opponent FG%) | −0.215 | −0.113 | −0.183 |

Team opponent-rim-frequency contamination is **negligible** (all
|corr| < 0.11) — a real, reassuring finding. Team overall defensive
quality (opponent FG%) shows a real but modest correlation (−0.11 to
−0.22) — playing on a better defense correlates with somewhat higher
individual suppression, a real, non-trivial but not overwhelming context
effect, reported rather than corrected (per the task's explicit "do not
correct with arbitrary scheme multipliers").

## 10. Role bias

Real correlation, suppression vs. real REB% (rough big-vs-perimeter
proxy — no true position field exists anywhere in this codebase):

| Season | corr |
|---|---|
| 2013-14 | 0.558 |
| 2018-19 | 0.468 |
| 2022-23 | 0.408 |
| 2023-24 | 0.371 |

Real, moderate, and **decreasing over time** — bigs do suppress rim shots
better on average (physically plausible: reach/positioning genuinely
help), but the correlation is well short of 1.0 and has weakened across
seasons (2013-14's 0.56 → 2023-24's 0.37), consistent with the modern
NBA's real shift toward switchable, less traditionally-positional rim
defense. **Not corrected** — same rationale as Phase 5/6's precedent.

## 11. Calibration

Grid search, λ∈{0.3..0.9}, M∈{0,25,...,3000}, weight = `rim_fga_defended`.
**Stricter discipline than every prior phase**: parameters selected on a
real TRAIN split only (2013-14→2019-20, 6 transitions), evaluated on a
completely separate HELDOUT split (2019-20→2025-26, 6 transitions) never
touched during grid search — the task's own explicit requirement, a
genuine train/test split rather than nested walk-forward.

| Split | Best λ, M | Weighted MAE | n |
|---|---|---|---|
| TRAIN | λ=0.5, M=100 | 0.03622 | 2,010 |
| **HELDOUT (train params applied)** | (same) | **0.03524** | **2,232** |

**HELDOUT MAE is essentially IDENTICAL to (even marginally better than)
TRAIN MAE** — strong, real evidence the selected parameters are not
overfit. Heldout rank correlation: 0.455.

**Age correction tested, REJECTED**: a real, small, monotonic-ish decline
in mean suppression with age (peak near 22-24, declining ~1-1.5
percentage points by the mid-30s) was measured, and a simple linear
age-correction was fit on TRAIN and applied to HELDOUT. Result: HELDOUT
MAE got marginally WORSE (0.03537 vs. 0.03524 without it) — **not
adopted**, per the task's explicit "do NOT force one."

## 12. Held-out results (OLD-naive vs. NEW calibrated)

All evaluated on the SAME held-out pairs (2019-20→2025-26, never touched
during parameter selection):

| Method | Weighted MAE |
|---|---|
| A. raw previous-season rate | 0.04301 |
| B. no-shrink multi-year (λ=0.5) | 0.03772 |
| C. generic provisional (λ=0.6, M=200) | 0.03479 |
| **D. tuned (λ=0.5, M=100)** | **0.03524** |
| **Improvement, D vs. A** | **18.1%** |

Honest disclosure: C (generic provisional) is marginally BETTER than D on
this specific heldout set (0.0348 vs. 0.0352, ~1% relative) — a real,
flat region around M=100-200, λ=0.5-0.6, not a sharp single optimum. Both
D and C clearly beat A/B (raw-previous/no-shrink) by a wide margin. The
TRAIN-selected D is reported as final per the phase's own leak-free
methodology, with this flatness stated plainly rather than hidden.

## 13. Representative players (2023-24, real full-season data)

| Player | Archetype | FGA defended | Suppression (raw) | BLK/36 | Rating |
|---|---|---|---|---|---|
| Rudy Gobert | Elite drop rim protector | 591 | +0.134 | 2.22 | **96.4** |
| Victor Wembanyama | Elite young shot blocker | 587 | +0.107 | 4.36 | **92.5** |
| Jaren Jackson Jr. | High-block big | 395 | +0.081 | 1.79 | **92.3** |
| Giannis Antetokounmpo | Help-side forward shot blocker | 358 | +0.094 | 1.13 | **94.6** |
| Draymond Green | Mobile switching helper, low blocks | 246 | +0.029 | 1.20 | 83.9 |
| Domantas Sabonis | Strong rebounder, not elite rim protector | 621 | +0.053 | 0.61 | 72.9 |
| Nikola Jokić | Big, average/weak suppression despite reputation | 657 | −0.004 | 0.94 | **45.0** |
| Scoot Henderson | Guard, REAL substantial exposure (299 FGA), genuinely poor | 299 | −0.112 | 0.25 | **5.4** |
| Onuralp Bitim | Low-opportunity wing | 24 | n/a | 0.31 | **null (confidence: low)** |

Key validations: **Giannis** (94.6) shows real incremental value beyond
blocks — elite suppression despite modest BLK/36. **Jokić** (45.0, near
league-average) is an honest, non-flattering finding — a great overall
player who is NOT an elite rim suppressor. **Scoot Henderson** (5.4) is
correctly identified as genuinely poor with a REAL, substantial sample
(299 rim attempts defended, not a small-sample fluke — checked directly:
the worst-suppression list at 2023-24 is uniformly guards with real
200-400+ attempt samples, a real, defensible signal, not noise). **Bitim**
(24 attempts, below the 30-attempt floor) correctly returns no rating and
low confidence rather than a fabricated bad score — exactly the
"few opportunities ≠ bad rim protector" distinction the task required.

## 14. Historical fallback

**Investigated, NOT built as a production path.** Real candidate
historical (pre-2013-14) proxy inputs exist (BLK, DREB%, minutes, real
Phase 6 foul rate) but a BOX_PROXY combining them would need real
validation against the modern tracking-era signal (the same
overlap-training methodology used for Ball Security's historical proxy in
Phase 4B) — not attempted this phase, given time constraints and this
phase's own priority on getting the MODERN signal right first. Every
pre-2013-14 estimate returns **`MODE_INSUFFICIENT`, no forced rating** —
consistent with "no fabricated precision." Building a real, validated
BOX_PROXY mode is named explicitly as future work (§17).

## 15. Final estimator

`shrunk_rate = (Σ(rate_s · λ^age_s · FGA_defended_s) + M · league_avg) / (Σ(λ^age_s · FGA_defended_s) + M)`
with λ=0.5, M=100 (in `rim_fga_defended` units), `rate` = real NBA
`PLUSMINUS`-derived suppression (sign-flipped, higher = better), percentile-
ranked against the real same-season population. A player below the
30-attempt exposure floor returns `confidence="low"`, no rating — never a
fabricated precise score.

## 16. Limitations

- Attempt deterrence (mechanism A) is real at the team level but not
  isolated per-player — a genuine per-player on/off ingestion job is
  needed, deferred as future work.
- Foul-interaction check uses OVERALL (not rim-specific) Phase 6 shooting-
  foul rate — a real, stated approximation.
- No physical (reach/wingspan/vertical) data exists in this codebase to
  separate physical advantage from technique/timing.
- Historical (pre-2013-14) coverage is zero — `MODE_INSUFFICIENT` only,
  no BOX_PROXY built this phase.
- The calibration optimum (§11-12) is real but flat (C and D nearly tied
  on heldout) — λ/M are defensible, not razor-sharp.
- Real, moderate team-defensive-quality correlation (−0.11 to −0.22,
  §9) is a genuine, unaddressed context effect.

## 17. Final classification: **LOCK V1**

Meets every stated LOCK V1 requirement with real, verified evidence:
- **Strong predictive stability**: TRAIN MAE (0.0362) ≈ HELDOUT MAE
  (0.0352) — a genuine train/test split, not just nested walk-forward,
  the strictest validation any phase in this project has used.
- **Heldout parameter stability**: confirmed directly (§11-12), not
  merely nested.
- **Acceptable role/scheme bias**: team-scheme contamination is
  negligible (§9); role (reb_pct) correlation is real but moderate and
  DECREASING over time, not overwhelming (§10).
- **Clear incremental value beyond blocks**: 58-75% of suppression-rate
  variance is unexplained by BLK/36 alone (§7), demonstrated concretely
  by Giannis's real divergence (§13).
- **Robust evidence coverage**: 13 full (not partial-sampled) real
  tracking-era seasons, 12 real transitions, ~470-580 players/season, a
  real first-party NBA-computed residual metric (not a custom PBP
  derivation).

Not locked blindly "because it looks right" — the age-correction test was
run and honestly rejected, the flat calibration region was disclosed
rather than hidden, and the team-defensive-quality correlation (§9) is
reported as a real, unresolved limitation.

## 18. Future recommendations

1. Build a real per-player on/off opponent-rim-ATTEMPT-frequency ingestion
   (mechanism A, attempt deterrence) — the one real gap in this phase's
   mechanism coverage.
2. Build and validate a real historical (pre-2013-14) BOX_PROXY, same
   overlap-training methodology as Ball Security's Phase 4B proxy.
3. Revisit the flat calibration region (§12) once a real physical
   (reach/wingspan) data source is found, which may sharpen the
   role-bias picture (§10).
4. Consider whether a future phase should split `rim_protection` into
   internal `rim_deterrence`/`rim_contest_effectiveness` components once
   real per-player attempt-deterrence data exists — NOT done this phase
   (insufficient evidence for the deterrence half specifically), single
   display attribute retained.

Wired into `PlayerAbilityProfile` via `with_attribute` (no schema changes
— `rim_protection` was already in `SKILL_ATTRIBUTES`). Not touched:
simulation, OVR, roles, Shot Creation, Playmaking Vision, physical traits.

Per direction: **stopping here.** No new attribute phase begun.
