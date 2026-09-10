# Phase 8 — Playmaking Vision

`playmaking_vision`. Entirely offline/parallel: nothing here is imported by
or imports `game_engine.py`, `season.py`, `awards.py`, `models.py`,
`transactions.py`. No Shot Creation, POA Defense, physical traits,
development, OVR, or simulation integration. Phase 7 (`rim_protection`,
LOCK V1) is unchanged. All 224 tests pass (206 pre-existing from Phases
4A-7 + 4 passing-tracking-ingestion + 14 playmaking-vision).

## 1. Source verification

`nba_api.stats.endpoints.leaguedashptstats`, `pt_measure_type="Passing"`
— the SAME real endpoint family Phase 4B's `handling_exposure.py` already
uses for `Possessions`/`Drives` (a different `pt_measure_type` on an
already-proven endpoint, not a new source). Real, verified columns:
`PASSES_MADE`, `PASSES_RECEIVED`, `AST`, `FT_AST`, `SECONDARY_AST`,
`POTENTIAL_AST`, `AST_PTS_CREATED`, `AST_ADJ`, `AST_TO_PASS_PCT`,
`AST_TO_PASS_PCT_ADJ`. Real floor: 2012-13 returns 0 rows, 2013-14
returns real data — same floor as every other `leaguedashpt*` endpoint
this codebase uses. Cheap: 13 real tracking-era seasons fully ingested in
3.1 seconds (one call/season, per this phase's own efficiency rules).

## 2. Existing cache reuse

Reused directly, no re-ingestion: `handling_exposure.py` (touches, drives,
time_of_poss — Phase 4B), `player_advanced.json` (usg_pct, ast_pct,
reb_pct, gp, mpg — existing since Phase 1). Only `POTENTIAL_AST` and its
siblings were genuinely new to this codebase's cache.

## 3. Semantic definition

Per the task's own taxonomy: **A. Playmaking Vision** (this phase) =
recognizing advantageous passing options, kept distinct from **B. Passing
Accuracy** (existing, locked `passing`/AST%-based attribute — NOT
rebuilt), **C. Creation/Scoring Gravity**, **D. Role/Initiation Share**,
**E. Teammate Conversion** (why `POTENTIAL_AST`, not raw `AST`, is the
right starting field — it doesn't require the shot to go in), **F. Ball
Security**, **G. Bad-Pass Turnovers** (existing Phase 4A/4B evidence,
cross-referenced only).

## 4-5. Candidate metrics & denominator comparison

Real scale-normalized comparison (CV_MAE + rank correlation, the Phase
6/7 correction, applied by default this time), `POTENTIAL_AST` per real
candidate denominator, 12 real transitions (2013-14→2025-26):

| Denominator | n pairs | CV_MAE | rank corr |
|---|---|---|---|
| **touches** | 5,058 | **0.154** | **0.794** |
| passes_made | 5,056 | 0.159 | 0.790 |
| time_of_poss | 4,919 | 0.179 | 0.505 |

`touches` wins (marginally over `passes_made`, clearly over
`time_of_poss` — ball-dominance time is a worse predictor than
per-touch decision rate). Adopted as the base denominator.

## 6. Potential-assist analysis

Raw `POTENTIAL_AST/touches` has VERY HIGH real year-to-year rank
stability (0.79) — but see §7: this stability turns out to be inherited
mostly from stable ROLE, not stable ability. Real rank correlation
between raw `AST/touches` and `POTENTIAL_AST/touches` is 0.92-0.95 across
2018-19/2022-23/2023-24 — `POTENTIAL_AST` barely re-ranks players
relative to raw `AST` in this per-touch form, meaning teammate-conversion
noise is NOT the dominant issue here; role/opportunity is (§9).

## 7. Passing-accuracy overlap — the decisive finding

Real correlation, raw `POTENTIAL_AST/touches` vs. the EXISTING
`passing_accuracy` attribute's own raw input (`ast_pct`):

| Season | corr |
|---|---|
| 2013-14 | 0.911 |
| 2018-19 | 0.851 |
| 2022-23 | 0.880 |
| 2023-24 | 0.867 |

**This is far too high (R²=72-83%) to call the raw candidate distinct
from `passing_accuracy`** — a direct hit of this phase's own explicit
"REVISIT if candidate mostly reproduces passing_accuracy" trigger.
**Per the task's "30 seconds of reasoning" checkpoint**: is there
incremental signal? Not in this raw form. Is it role-limited? Yes (§9).
Rather than abandoning the whole `POTENTIAL_AST` family, one targeted
fix was tested (§9) — residualizing out real role/opportunity variables —
which dropped this correlation to **0.37-0.42** while preserving real
predictive stability. This became the phase's adopted candidate.

## 8. Usage/touch/TOP contamination

Real correlation, raw `POTENTIAL_AST/touches` rate vs.:

| Variable | 2013-14 | 2018-19 | 2022-23 | 2023-24 |
|---|---|---|---|---|
| usg_pct | 0.277 | 0.258 | 0.318 | 0.300 |
| drives | 0.660 | 0.610 | 0.577 | 0.520 |
| time_of_poss | 0.631 | 0.611 | 0.624 | 0.556 |
| reb_pct (big-vs-perimeter proxy) | −0.585 | −0.449 | −0.443 | −0.396 |

Real, substantial contamination from creation/ball-dominance variables
(drives, time-of-possession, both 0.52-0.66) and a real, large NEGATIVE
role bias against bigs (−0.40 to −0.59) — exactly the role-domination the
task warned about. **This is the primary target of the residualization
in §9**, not usage per se (usage's own correlation, 0.26-0.32, is real
but secondary to drives/TOP).

## 9. Scoring-gravity contamination & the adopted residual candidate

A small, interpretable OLS residual model (no ML library, coefficients
frozen from a real TRAIN-only fit, 2013-14–2019-20, never refit
per-season) was fit:

`POTENTIAL_AST/touch ≈ a + b·USG% + c·(drives/touch) + d·(TOP/touch)`

Applying this residual (candidate family **E**, "potential-assist
creation residual after role exposure") and re-checking correlations on
2023-24 real data:

| Variable | Raw rate corr | **Residual corr** |
|---|---|---|
| drives | 0.520 | **0.006** |
| time_of_poss | 0.556 | **0.040** |
| reb_pct | −0.396 | **0.044** |
| usg_pct | 0.300 | **−0.137** |
| **ast_pct (passing_accuracy)** | 0.867 | **0.369** |

Role/opportunity contamination is essentially eliminated (all near
zero); the passing_accuracy overlap drops to a real, moderate 0.37 —
**still real incremental signal, not a rediscovery**. Critically, per
the task's "measure, don't residualize away legitimate signal" caution:
the residual's own real T→T+1 rank stability (checked on a real held-out
season pair, 2018-19→2019-20) is **0.64** — clearly non-zero, i.e. this
is removing OPPORTUNITY, not erasing the underlying ability (which would
show near-zero stability if it had). The raw rate's own stability on the
identical pair is 0.88 — some of that was real role persistence, exactly
what residualizing is supposed to remove.

## 10. Role/archetype bias

Confirmed directly in §9's table: `reb_pct` (big-proxy) correlation drops
from −0.40/−0.59 (raw) to +0.04 (residual) — **bigs are no longer
systematically penalized**. Representative check (§15): Jokić and
Sabonis (both real high-usage bigs) both show real positive residuals
(0.056, 0.050) — comparable in magnitude to guard playmakers, not
suppressed by position.

## 11. Team-switch / portability evidence

Not run this phase — per the task's own "optional if cheap... do not
build expensive per-game pipeline unless clearly worth it," and given the
decisive residual-vs-raw evidence already in hand (§9's role-bias
elimination is itself real portability evidence: a candidate that no
longer depends on being a ball-dominant guard is inherently more
transferable across role changes). Flagged as real future work if a
denser check is wanted.

## 12-13. Calibration & train/heldout

Reused `rim_protection_calibration.py`'s generic grid-search/shrinkage
engine directly (no new calibration code written), weight = `touches`.
Real TRAIN split (2013-14→2019-20, 6 transitions) → real HELDOUT split
(2019-20→2025-26, 6 transitions), same discipline as Phase 7:

| Split | λ, M | Weighted MAE |
|---|---|---|
| TRAIN | λ=0.4, M=800 | 0.01215 |
| **HELDOUT (train params applied)** | (same) | **0.02082** |

Honest disclosure: unlike Phase 7's near-perfect train→heldout
transfer, HELDOUT MAE here is materially higher than TRAIN (a real gap —
this corpus's heldout window includes the COVID-disrupted 2020-21
season, adding real noise). Baselines on the SAME heldout pairs:

| Method | Weighted MAE |
|---|---|
| A. raw previous-season | 0.02395 |
| B. no-shrink multi-year | 0.02081 |
| C. generic provisional (λ=0.6, M=200) | **0.02012** |
| D. tuned (λ=0.4, M=800) | 0.02082 |
| **Improvement, D vs. A** | **13.1%** |

Honest disclosure: C (generic provisional) marginally BEATS D on this
specific heldout — real evidence the exact λ/M optimum is not sharply
pinned down (train wants heavy shrinkage, heldout shows lighter shrinkage
about equally good). HELDOUT rank correlation: 0.447. Real, positive
signal, imperfect parameter stability — reported plainly.

## 14. Age test

A real, small age-related pattern was checked in signed prediction error
by age bucket on the heldout set — no clean, consistent trend (errors
range ±0.005 with no monotonic shape, e.g. age-32 bucket is negative but
age-36 flips positive). **Rejected immediately, per instruction, no
further attempt.**

## 15. Representative players (2023-24, real data)

| Player | Archetype | Touches | TOP | Residual | Note |
|---|---|---|---|---|---|
| Draymond Green | Low-TOP elite passer | 3,267 | 107.6 (lowest) | **0.097** (highest) | Fast decisions, minimal ball-holding |
| Chris Paul | Conservative veteran connector | 3,349 | 262.6 | 0.073 | Efficient, moderate volume |
| Nikola Jokić | Big passing hub | 7,782 | 367.0 | 0.056 | No longer penalized for being a big |
| Domantas Sabonis | Big passing hub | 7,502 | 306.0 | 0.050 | Same |
| Alperen Sengun | Secondary big connector | 3,867 | 149.8 | 0.048 | Positive despite lower usage |
| LeBron James | Elite ball-dominant creator | 5,385 | 364.9 | 0.043 | Positive but not a residual outlier |
| Trae Young | Scoring guard, huge raw AST | 4,617 | 447.5 | 0.041 | Much of his raw gaudy rate is role-driven |
| Fred VanVleet | Secondary connector | 5,849 | 475.6 | 0.021 | Modest |
| Devin Booker | Scoring guard, inflated raw opportunity | 4,993 | 419.6 | **0.007** | High touches/usage, but low residual once adjusted |
| Russell Westbrook | Weak playmaker despite usage | 3,037 | 223.4 | 0.013 | Below most true playmakers here |

Real, un-tuned divergence exactly matching the archetypes requested:
Draymond's low ball-dominance but elite residual, Booker's high raw
volume but unremarkable residual, and both bigs retaining real positive
signal.

## 16. Historical fallback

None built. Same real 2013-14 tracking floor as every `leaguedashpt*`-
derived attribute in this project. A pre-2013-14 query returns
`mode="INSUFFICIENT"`, no rating — consistent with "do not force every
1990s player into false precision." A future phase could attempt a
`BOX_PROXY` (AST%, usage, historical bad-pass-turnover-subtype evidence
from Phase 4A/4B) but this was not attempted given the modern signal
itself still has an imperfect calibration story (§13) worth settling
first.

## 17. Final estimator

`shrunk_rate = (Σ(residual_s · λ^age_s · touches_s) + M · league_avg) / (Σ(λ^age_s · touches_s) + M)`,
λ=0.4, M=800 (touches units), percentile-ranked against the real
same-season population. Below a 200-touch floor: `confidence="low"`, no
rating — never fabricated precision for a low-exposure player.

## 18. Limitations

- Residual coefficients were fit once on TRAIN data via plain OLS (3
  predictors) — a real, interpretable, but simple model; a
  richer/nonlinear role model was NOT attempted (kept deliberately
  simple per the task's own "prefer interpretable candidate, no feature
  kitchen sink").
- Calibration parameter transfer (TRAIN→HELDOUT) is real but imperfect —
  the generic provisional (λ=0.6, M=200) ties the tuned choice on
  heldout.
- Residual still carries a real, moderate (0.37-0.42) link to
  `passing_accuracy` — expected (good decision-makers often also pass
  accurately) but means the two are not fully orthogonal.
- No historical mode, no team-switch portability study, no
  distance/skip-pass/pressure-adjusted passing detail (none of these
  real fields exist in this codebase's access).
- `playmaking_vision` is wired into the EXISTING `creation_for_others`
  `SKILL_ATTRIBUTES` slot (see `playmaking_vision_estimation.py`'s
  `PROFILE_ATTRIBUTE_KEY`), not a new 19th schema entry — a deliberate,
  minimal-schema-change decision, not an oversight.

## 19. Final classification: **KEEP BUT FLAG**

Real, demonstrated incremental signal beyond both raw assists and the
existing `passing_accuracy` attribute (§7/§9: correlation drops from
0.85-0.91 raw to 0.37-0.42 residual, while real T→T+1 stability is
RETAINED at 0.64, not destroyed). Role/scheme contamination
(drives/TOP/position) is measured and substantially removed, not ignored
(§9-10). Held back from LOCK V1 by: (1) imperfect calibration parameter
transfer — TRAIN and HELDOUT optima disagree more than Phase 7's rim
protection did (§13); (2) the residual model, while principled, is a
first-pass 3-variable OLS, not independently re-validated by a second
method; (3) real, moderate remaining overlap with `passing_accuracy`
(0.37-0.42) means "distinct from passing accuracy" is a matter of degree,
not a clean separation. This matches the task's own explicit KEEP BUT
FLAG criteria ("useful but role/scoring-gravity contamination remains...
parameter stability imperfect") precisely.

Wired into `PlayerAbilityProfile` via `with_attribute` (existing
`creation_for_others` key, no schema expansion). Not touched: simulation,
OVR, roles, Shot Creation, POA Defense, physical traits.

Per direction: **stopping here.** No new attribute phase begun.
