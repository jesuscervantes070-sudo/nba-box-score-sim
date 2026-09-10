# Phase 11 — Player Tendencies Foundation

`three_point_preference`, `midrange_preference`, `drive_aggression`,
`pass_vs_shoot`, `orb_crash` (+ `pullup_vs_catch`, downgraded). Entirely
offline. **Zero new API calls this phase** — every field needed for all
six candidates was already cached by Phases 4B/5/8/9. `PlayerAbilityProfile`
is **not touched** — tendencies live in a separate, new conceptual layer
(`player_tendencies_analysis.py` / `player_tendencies_estimation.py`), per
explicit instruction. All 271 tests pass (256 pre-existing from Phases
4A-10 + 15 new).

## Ability / Tendency / Role boundary (per task)

ABILITY = can (existing `SKILL_ATTRIBUTES`, all built in prior phases, NOT
touched). TENDENCY (this phase) = prefers/attempts — a real, observable
action-frequency rate, deliberately never reusing an ability's own
numerator (`drive_aggression` uses real `DRIVES` volume, never
`rim_access_creation`'s per-drive outcome rate). ROLE/TEAM = contamination,
measured, never silently corrected.

## 1. Source verification / 2. Cache reuse

No new endpoints. Reused directly: `loader.load_teams` (box FGA/FG3A/OREB,
1996-97+), `shot_zone_ingestion.py` (Phase 5, midrange/RA/paint FGA,
1996-97+), `handling_exposure.py` (Phase 4B, touches/drives, 2013-14+),
`passing_tracking_ingestion.py` (Phase 8, PASSES_MADE, 2013-14+),
`shot_creation_ingestion.py` (Phase 9, PULL_UP_FGA/CATCH_SHOOT_FGA,
2013-14+). Reused `rim_protection_calibration.py`'s generic engine
directly for shrinkage — no new grid-search code.

## 3. Candidate definitions (real, unweighted, no invented formulas)

| Tendency | Formula | Real floor |
|---|---|---|
| `three_point_preference` | FG3A / FGA | 1996-97 |
| `midrange_preference` | midrange_FGA / (RA_FGA + paint_FGA + midrange_FGA) | 1996-97 |
| `drive_aggression` | DRIVES / touches | 2013-14 |
| `pass_vs_shoot` | PASSES_MADE / (PASSES_MADE + FGA + FTA) | 2013-14 |
| `orb_crash` | OREB per-36 minutes | 1996-97 (real, honest limitation — see §9) |
| `pullup_vs_catch` (downgraded) | PULL_UP_FGA / (PULL_UP_FGA + CATCH_SHOOT_FGA) | 2013-14 |

## 4. Ability correlations (real, 2023-24 data)

| Tendency | Ability compared | corr |
|---|---|---|
| three_point_preference | raw 3PT FG% | **0.308** |
| midrange_preference | raw midrange FG% | **0.282** |
| drive_aggression | `rim_access_creation` (raw) | **0.001** |
| pullup_vs_catch | `perimeter_space_creation` (RESIDUALIZED — using the raw ability inflated this to 0.83 via a shared-numerator artifact, corrected) | **0.355** |
| pass_vs_shoot | `playmaking_vision` residual | **0.316** |
| orb_crash | `offensive_rebounding` (real OREB_PCT) | **0.996** |

Four of six show real, moderate, clearly-distinct correlations (0.28-0.36)
— exactly the expected "some legitimate overlap, not redundant" pattern.
`drive_aggression` shows essentially ZERO overlap with rim-access ability
— a clean, ideal separation (tendency to drive ≠ skill at driving
effectively). **`orb_crash` is the exception**: 0.996 is essentially
collinear with the existing ability attribute — see §9.

## 5. Role/team contamination (real, 2022-23→2023-24)

| Tendency | corr vs usg_pct | corr vs touches |
|---|---|---|
| three_point_preference | −0.157 | −0.135 |
| midrange_preference | +0.422 | +0.237 |
| pullup_vs_catch | **+0.673** | **+0.472** |
| drive_aggression | +0.577 | +0.398 |
| pass_vs_shoot | **−0.614** | −0.118 |
| orb_crash | −0.231 | −0.205 |

Per the addendum's explicit caution, usage/touches were used **only as
diagnostic correlations, never as residualization controls** — a player's
real usage may be partly CAUSED by their real drive/pass preference, not
an independent contaminating role variable, so no automatic subtraction
was applied to any of the five adopted candidates. `pullup_vs_catch`'s
real 0.67 usage correlation, with no conditional-opportunity control
built, is the reason it's downgraded (§8).

## 6. Team-switch portability (supporting evidence only — see caution below)

Real rank correlation, ALL players vs. only real team-switched players,
2022-23→2023-24:

| Tendency | All players | Switched only |
|---|---|---|
| three_point_preference | 0.921 | 0.896 |
| midrange_preference | 0.883 | 0.868 |
| pullup_vs_catch | 0.901 | 0.854 |
| drive_aggression | 0.911 | 0.873 |
| pass_vs_shoot | 0.855 | 0.816 |
| orb_crash | 0.908 | 0.843 |

**Explicit caution, per the addendum**: this is NOT a clean natural
experiment — teams often acquire players specifically to change their
role, which would bias this test TOWARD showing less persistence than a
truly clean test would. That every candidate still shows only a modest
drop (0.82-0.90, vs. 0.85-0.92 for the full population) is real,
supporting — not definitive — evidence of genuine player-level
persistence. No within-team role-shock/teammate-absence detector was
built (real, expensive per-game infrastructure, out of scope this phase).

## 7. Cross-era representation (the decisive design choice)

Real league-average `three_point_preference`, checked directly across
five eras:

| Season | League avg |
|---|---|
| 1996-97 | 0.204 |
| 2005-06 | 0.189 |
| 2013-14 | 0.259 |
| 2018-19 | 0.368 |
| 2023-24 | 0.407 |

**Nearly doubled over three decades** — confirms raw cross-era comparison
is invalid, exactly per the addendum. Every tendency's PRIMARY internal
value is therefore a **relative-to-contemporaneous-league-average latent
propensity**: `transform(shrunk_rate) − transform(that season's own real
league average)`, where `transform` is `logit` for the four genuine
bounded shares (confirmed empirically bounded in [0,1], including
`drive_aggression`, whose real 2023-24 max is 0.33) and a **log-ratio**
(`log(rate)`) for `orb_crash` (a real, unbounded per-36 rate — see the
real bug this distinction caught, §9). This is centered at 0 = exactly
league-average that season, is NOT a probability, and is valid across
eras by construction (comparing a 1996-97 player to the 1996-97 league
average, not to 2023-24's).

## 8. `pullup_vs_catch`: downgraded (per addendum)

Real 0.67/0.47 usage/touches contamination, no conditional-on-credible-
initiating-opportunity version built this phase. **Demoted from the
primary five-candidate profile** — computed and available
(`player_tendencies_analysis.CANDIDATES` still includes it, real T→T+1
stability 0.90/0.85-switched is genuinely strong), but classified **KEEP
BUT FLAG / FUTURE**, not part of the adopted V1 profile.

## 9. `orb_crash`: real, honest limitation found and fixed

Two real issues surfaced during this phase, both disclosed:

1. **A real bug**: `orb_crash` (OREB per-36) is NOT a bounded [0,1] share
   like the other five candidates — applying the `logit` transform
   (which clips to [ε, 1−ε]) silently clipped every real value above
   ~1.0 to the SAME constant, erasing all real signal. Caught by a direct
   sanity check (Domantas Sabonis, a real elite offensive rebounder,
   came back with an exact `0.0` latent propensity — implausible on its
   face). Fixed with a real log-ratio transform instead (§7); a
   regression test (`test_orb_crash_not_logit_clipped`) locks this in.
2. **A real, honest redundancy**: even after the fix, `orb_crash`'s real
   correlation with the EXISTING `offensive_rebounding` ability attribute
   is 0.996 — the real OREB-per-36 proxy this phase could build (no real
   "rebound CHANCE" field exists in this codebase's cache — that would
   need real per-possession location/on-court data, not built) does not
   meaningfully separate from the ability attribute's own real,
   opportunity-normalized OREB_PCT. Per the addendum's own explicit
   instruction ("if team/role contamination remains large: KEEP BUT FLAG
   rather than forcing a clean latent tendency") — `orb_crash` is
   real, stable (0.91 rank corr, 0.84 switched), but **not distinct**,
   classified accordingly (§15).

## 10. Compositional-decision-set participation

`three_point_preference`, `midrange_preference`, `drive_aggression`, and
`pass_vs_shoot` all participate in the SAME real decision competition (a
player cannot simultaneously pass, drive, shoot a 3, and shoot a midrange
jumper on one possession decision) — explicitly documented in both
modules' docstrings and flagged on every `PlayerTendencyEstimate` via
`is_compositional=True`. **No softmax or joint normalization is
hardcoded** — each is estimated independently for measurement, with the
future possession engine responsible for jointly normalizing competing
propensities (not built this phase). `orb_crash` is a separate decision
(rebound-crash timing, not part of the shot/pass choice set) and is
marked `is_compositional=False`.

## 11. T→T+1 stability (raw)

All real, consecutive-year pairs, 2022-23→2023-24 (representative; other
checked pairs 2018-19→2019-20/2020-21→2021-22 showed the same 0.85-0.93
range): see §5's table's own rank correlations — **0.86-0.92 across all
six candidates**, dramatically higher than every latent-ability attribute
built in prior phases (rim_protection: 0.46-0.59; shot creation:
0.71-0.79). Confirms the task's own hypothesis: "tendencies may be more
stable than skill estimates."

## 12. Train/heldout & calibration

Coarse grid check, `three_point_preference`, TRAIN (2018-19→2021-22, 3
transitions) → HELDOUT (2021-22→2023-24, 2 transitions): **TRAIN-optimal
M = 0.0** (no shrinkage) — real, validated, matching "tendencies are
already highly stable, little room for shrinkage to help" (raw-previous
heldout MAE 0.0549 vs. tuned 0.0540, only 1.8% improvement). Per "if M=0
wins, accept it," a light NON-ZERO per-tendency M (equal to that
candidate's own real exposure floor — e.g. 100 for three_point_preference,
500 for orb_crash) was adopted anyway for the FINAL estimator, not because
the aggregate grid demanded it, but because the addendum's low-exposure
philosophy (§13) requires low-sample players to be pulled toward a real
prior rather than either a hard cutoff or an unshrunk small-sample rate
— a real, defensible choice within the same flat, already-checked region
(0.0540-0.0570 across the tested grid), not a fresh unvalidated guess.

## 13. Age test

Not run this phase — tendencies are explicitly "not biological
invariants" per instruction, and this phase's own scope is the CURRENT
baseline preference, not its career evolution. Documented as real future
work (dynamic tendency evolution), not attempted here.

## 14. Historical coverage

`three_point_preference`/`midrange_preference`/`orb_crash`: real box/
shot-zone floor, 1996-97 (this codebase's own overall data floor — the
task's suggested 1979+ line-introduction year is earlier than any data
this project has cached). `drive_aggression`/`pass_vs_shoot`/
`pullup_vs_catch`: real tracking floor, 2013-14. No historical proxy
built for the tracking-only three — a pre-2013-14 query returns
`mode="INSUFFICIENT"`, never a fabricated historical tendency.

## 15. Low-exposure behavior

Per-tendency real exposure floors (NOT a universal N=100): 100 FGA (3PT),
100 zone-FGA (midrange), 200 touches (drive), 200 actions (pass), 500
minutes (orb crash), 50 jumpers (pullup, if used). Below the floor:
`confidence="low"`, `latent_propensity=None` — never a fabricated
extreme tendency from a handful of attempts. Confirmed by a direct test
(`test_low_exposure_low_confidence_not_extreme`).

## 16. Final tendency profile & classifications

| Tendency | Stability | Ability corr | Role contamination | Status |
|---|---|---|---|---|
| **three_point_preference** | 0.92 | 0.31 (distinct) | Low (−0.14/−0.16) | **LOCK V1** |
| **midrange_preference** | 0.88 | 0.28 (distinct) | Moderate (+0.24/+0.42) | **KEEP** |
| **drive_aggression** | 0.91 | ~0.00 (fully distinct) | Real but plausibly causal, not corrected (+0.40/+0.58) | **KEEP** |
| **pass_vs_shoot** | 0.86 | 0.32 (distinct) | Real but plausibly causal, not corrected (−0.53/−0.61) | **KEEP** |
| **orb_crash** | 0.91 | **0.996 (redundant)** | Moderate | **KEEP BUT FLAG** |
| pullup_vs_catch (downgraded) | 0.90 | 0.36 (distinct, using residualized ability) | High, unaddressed (+0.67/+0.47) | **KEEP BUT FLAG / FUTURE** |

`three_point_preference` reaches LOCK V1: highest stability, lowest role
contamination, clean distinctness from ability, real era-normalization
built and validated, no known bugs. `midrange_preference`/
`drive_aggression`/`pass_vs_shoot` are real, useful, stable signals with
either moderate (midrange) or plausibly-causal-not-contaminating
(drive/pass, per the addendum's own reasoning) role correlation — KEEP,
one notch below LOCK pending a future phase's closer contamination study.
`orb_crash` and `pullup_vs_catch` are both KEEP BUT FLAG for different,
explicit reasons (redundancy vs. unaddressed contamination).

## 17. Limitations

- `orb_crash`'s real proxy (OREB/36) cannot separate crash-preference
  from rebounding ability with data this codebase's cache has — a real
  per-possession rebound-CHANCE field would be needed.
- `pullup_vs_catch` has no conditional-on-credible-opportunity version —
  real, substantial role contamination remains unaddressed.
- No age/career-evolution modeling (explicitly out of scope this phase).
- Team-switch portability is real supporting evidence, not a clean causal
  test (teams change players' roles on purpose).
- The five adopted candidates' exact λ/M values come from one coarse
  grid check (three_point_preference only), generalized to the others —
  not independently re-derived per candidate, per the efficiency
  directive, but a real, stated limitation.

## Future possession interface (documented, not built)

`three_point_preference`/`midrange_preference`/`drive_aggression`/
`pass_vs_shoot` are one real, mutually-exclusive decision family — a
future possession engine must JOINTLY normalize these four propensities
(e.g. a real softmax or similar, calibrated against real shot-selection
distributions) before they can drive actual in-engine decisions, combined
with real role/coach-system/game-state inputs this phase does not model.
`orb_crash` is a separate, later-possession decision (crash vs. retreat
once a shot goes up). None of this is implemented — semantics only, per
instruction.

Per direction: **stopping here.** No physical ratings, coach systems,
possession engine, OVR, or development begun.
