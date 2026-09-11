# Detailed Engine — First Diagnostic Report

**Not a phase report.** Diagnostic-only instrumentation added on top of
the accepted, uncommitted Phase 23C detailed-engine work, to explain
*why* the current simulator produces unrealistic pace/rebounding/
turnover output — before any calibration is attempted.

## A. Instrumentation added

One new module, `detailed_engine_diagnostics.py` — `PossessionDiagnostics`
(per-possession), `GameDiagnostics` (per-game aggregation), `MultiGameDiagnostics`
(multi-game aggregation), plus `diagnose_possession`/`diagnose_game`/
`diagnose_games`. All three read EXCLUSIVELY from already-produced,
already-structured objects (`PossessionRecord`, `PossessionTerminalResult`,
`record.events`, `PossessionWorld.trace`/`.action_log`) — no re-simulation,
no RNG, no new simulation decision.

Two tiny, additive hooks were added inside the ALREADY-OPEN
`possession_orchestrator.py` (the only file this diagnostic pass modified
beyond adding new files) to expose data that existed nowhere at all
before:

1. **`PossessionWorld.action_log`** (new field) — `simulate_possession`'s
   own loop now reads `engine.state.game_clock_remaining` immediately
   before and after each `dispatch_action` call and appends
   `{"step", "action_type", "elapsed_game_clock_seconds"}`. This is the
   ONLY way to get real, per-dispatched-action clock consumption without
   moving clock ownership (which the task explicitly forbade) — the hook
   observes clock state the orchestrator was already computing, at
   exactly one call site.
2. **A `REBOUND_OPPORTUNITY` trace entry** inside `_dispatch_rebound`
   (which previously logged NOTHING diagnostic at all) — records
   `source`, `shot_family`, `eligible_count`, `outcome`, `rebounder`,
   all read directly off `rebound_resolution.ReboundResult`'s own typed
   fields.

Both hooks are pure observation of state the code already computes;
neither changes any probability, duration, or control-flow branch.
Verified: `test_no_faults_and_deterministic_output_unaffected_by_telemetry`
confirms an identical seed reproduces an identical score/possession count
whether or not diagnostics are ever computed.

## B. Source-of-truth / firewall statement

```
LIVE STATE            -> engine.state / PossessionWorld
EVENT STREAM          -> engine.log.events (accounting-truth direction)
TYPED RESULTS         -> PossessionTerminalResult / DetailedGameResult
DIAGNOSTIC TELEMETRY  -> detailed_engine_diagnostics.py (OBSERVABILITY ONLY)
```
`detailed_engine_diagnostics.py` is never imported by `possession_orchestrator.py`,
`detailed_game_orchestrator.py`, or `detailed_game.py` (verified:
`test_diagnostics_never_imported_by_simulation_modules`), imports no RNG
and no `SelectionPolicy` (verified via a namespace-name scan, the same
methodology this project already uses for its ability/attribute
firewalls: `test_diagnostics_module_never_imports_rng_or_selection`), and
never calls `simulate_possession`/`simulate_detailed_game` itself.

## C. Test counts

- New targeted: `test_detailed_engine_diagnostics.py` → **16/16 OK**.
- Focused re-run: `test_possession_orchestrator.py` (43) + `test_detailed_game.py` (24) + the new 16 = **83/83 OK**.
- Full suite: `python3 -m unittest discover -p "test_*.py"` → **892/892 OK** (876 baseline + 16 new). Zero regressions. The two `TEST-RP-BAD`/`TEST-SZ-BAD` lines are the same pre-existing, expected simulated-failure log lines present in every prior baseline run.

## D/G. Seed 23024 diagnostic

Reproduced exactly, from the real, unmodified simulation output, via `diagnose_game`:

| Metric | Value |
|---|---|
| Final score | HOME 463 – 400 AWAY |
| Total possessions | 681 |
| Mean / median possession seconds | 4.229 / 2.700 |
| p10 / p90 possession seconds | 1.0 / 9.2 |
| Mean / median actions per possession | 3.696 / 2 |
| Max actions in one possession | 31 |
| Max step count (loop iterations, includes clock/loose-ball checks) | 31 |
| FGA / FGM / misses | 857 / 300 / 557 |
| Rebound opportunities | 552 |
| OREB / DREB | 353 / 199 |
| OREB share (OREB / (OREB+DREB)) | **0.639** |
| OREB-count distribution (0/1/2/3+ per possession) | 459 / 134 / 61 / 27 |
| Turnovers (turnover-ending rate) | 174 (25.6%) |
| Turnover subtypes | CLEAN_INTERCEPTION 91, LOOSE_BALL_DEFENSE_RECOVERED 58, BAD_PASS_OUT_OF_BOUNDS 16, BAD_PASS_TO_DEFENDER 9 |
| Personal fouls (shooting / floor) | 18 (18 / 0) |
| FTA / FTM | 32 / 24 |
| Terminal-reason distribution | MADE_FG 300, DEFENSIVE_REBOUND 199, TURNOVER 174, FINAL_FT_MADE 5, PERIOD_END 3 |

Action-time decomposition (count / total seconds / mean seconds):

| Action | Count | Total sec | Mean sec |
|---|---|---|---|
| DRIVE | 477 | 1191.5 | 2.498 |
| PULL_UP | 547 | 819.6 | 1.498 |
| CATCH_AND_SHOOT | 310 | 309.7 | 0.999 |
| SWING_PASS | 607 | 242.8 | 0.400 |
| RESET_PASS | 576 | 230.4 | 0.400 |

Every mean matches its `PossessionConfig`/`pass_resolution.FLIGHT_DURATION_SECONDS`
placeholder value almost exactly (2.5 / 1.5 / 1.0 / 0.4 / 0.4) — confirming clock
ownership is behaving exactly as designed, with no double-charging or
drift (this is expected/correct, not a bug).

## H. 10-game aggregate (seeds 23024–23033)

| Metric | Mean across 10 games |
|---|---|
| Total possessions | 672.3 |
| OREB / DREB | 327.3 / 196.7 |
| OREB share | 0.624 |
| Personal fouls | 16.3 |
| FTA | 34.4 |
| Turnovers (rate) | 162.0 (24.0%) |

All 10 games ran to a valid typed termination with zero
`DetailedGameSimulationFault`/`PossessionSimulationFault` raised.

## E/F/I. CLOCK — exact reasons pace is ~3.4× too fast

**Measurement-artifact check performed and RULED OUT first**: it was
hypothesized that an OREB might spawn a brand-new `PossessionRecord`
(inflating the raw 681-possession count relative to the real "one trip =
one possession" NBA statistic). Verified directly: `_dispatch_rebound`
returns `None` (non-terminal) on `SECURED_OFFENSE`, so Phase 23A's own
possession loop **continues inside the SAME `simulate_possession` call and
the SAME `PossessionRecord`** — an OREB never creates an extra record.
Confirmed empirically: grouping the 681 raw records by real
offense-team alternation yields **678** true alternating-team
possessions — essentially identical to the raw count (the 3-record gap
is exactly the 3 `PERIOD_END` boundaries where the same team keeps the
ball into the next period). **The 3.4× pace gap is real, not a counting
definition mismatch.**

With that ruled out, the measured cause is a combination of two factors,
both directly evidenced by the action-count/time telemetry above:

1. **Too few actions per possession is the dominant mechanism.** Median
   actions per possession is **2**, and **232 of 681 possessions (34%)
   terminate on their very FIRST dispatched action.** There is no
   modeled "possession development" cost at all — the very first
   Phase 16 selection after inbound is already shot/drive-eligible with
   zero prior cost, unlike a real NBA possession which typically
   involves an advance-the-ball phase before the first live decision.
2. **Individual action durations are short by explicit V0 placeholder
   design**, and passes in particular are cheap: `SWING_PASS`/`RESET_PASS`
   average exactly 0.4s (the `DIRECT`-family `FLIGHT_DURATION_SECONDS`
   placeholder), the shortest of any action type, while still being
   among the most frequently dispatched (607 + 576 = 1,183 dispatches,
   more than DRIVE + both shot types combined). A possession heavy in
   passes barely dents the clock per exchange.
3. **Restart type (transition vs. dead-ball) is NOT a material driver** —
   measured mean possession length is 4.41s for `DEAD_BALL_INBOUND` vs.
   4.06s for `LIVE_TRANSITION`, a small, directionally-unremarkable
   difference, not the dominant mechanism.
4. **The shot clock is essentially never a binding constraint** — with a
   median possession of 2.7s against a 24s shot clock, `SHOT_CLOCK_VIOLATION`
   is a comparatively rare terminal reason (not observed at all in the
   681-possession seed-23024 sample) — pace is governed entirely by how
   quickly the action-selection/dispatch loop reaches a terminal
   outcome, not by clock-expiry pressure forcing quick shots.

**Answer to "which mechanism":** primarily (1) too few actions per
possession, secondarily (2) short per-action durations (especially
cheap passes) — NOT missing setup time as a separate line item (there
genuinely is none — that absence IS finding (1)) and NOT transition/
restart behavior.

## J. REBOUNDING — exact reasons OREB is exploding

- **True simulator OREB share: 0.639** (seed 23024) / **0.624** (10-game mean) — both roughly 2.3–2.5× the real ~0.25–0.28 NBA OREB rate. Reported here as `OREB / (OREB+DREB)`, never called "OREB%" (that name is reserved for the real, opportunity-normalized NBA stat this raw ratio is not).
- **Misses: 557** (seed 23024); **rebound opportunities: 552** → **0.991 opportunities per miss** — confirms **ONE** rebound opportunity is generated per miss, essentially never duplicated (the small gap is the 5 `FINAL_FT_MADE` sequences that never reach a miss/rebound at all). This rules out mechanism (B) from the task's own list (duplicate opportunity generation) as a material cause.
- **OREB-count distribution**: 459 possessions with 0 OREBs, 134 with exactly 1, 61 with 2, **27 with 3+** — repeated second-chance loops (mechanism D) are real and non-trivial (88/681 ≈ 13% of possessions had 2+ offensive rebounds in the SAME trip) but are a magnifier, not the root cause (see below).
- **The dominant, directly source-confirmed mechanism is (a hybrid of B/C): a literal geometry/world-state bug that structurally excludes the defense from interior-zone rebound competition.** Direct inspection of `possession_orchestrator.py` shows defender zones (`world.player_zones[defender_id]`) are set exactly ONCE, at possession start (`_mirror_defender_zones`), and are **never updated again for the rest of the possession** — every place a player's zone is subsequently updated (`_dispatch_drive`, `_dispatch_pass`, `_dispatch_floor_foul`, `resolve_generic_loose_ball`, `_dispatch_rebound`'s own rebounder assignment) updates only the OFFENSIVE participant (driver/receiver/fouled player/loose-ball winner/rebounder), never that player's defender. Empirically confirmed via telemetry cross-referenced against `world.player_zones` at possession end: **across 369 possessions that reached a rebound opportunity, ZERO had any defender positioned in the interior zone (RESTRICTED_RIM/PAINT)**, while offense had 1+ player there in 51/369 (≈14%) of them. Directly consistent with the rebound-outcome breakdown by shot family: **RIM/FLOATER (interior) missed shots were secured by the OFFENSE in 79 of 79 cases (100%) — the defense won ZERO interior rebounds in this entire game.** All 199 real defensive rebounds in this game came exclusively from `THREE_POINT` misses (274 offense / 199 defense, 57.9% offense share there too — still elevated versus real NBA rates, but not the total exclusion seen on interior misses).
- This is a real, structural, DEMONSTRATED bug — not a resolver-outcome/skill-weighting issue. `rebound_resolution.py`'s own real "opportunity eligibility BEFORE any skill value is read" gate (Phase 19's own central design principle) is doing exactly what it was built to do; it is being fed a **corrupted input** (defenders whose recorded position has gone stale relative to where the actual play developed).
- A secondary, NOT-yet-fully-diagnosed effect remains even on `THREE_POINT` misses (offense still wins 57.9% despite the synthetic profile's defense having a HIGHER default rebounding rate, 0.15, than offense's 0.08) — flagged as unproven (Sec. O), not attributed to a specific mechanism yet.

**Is the OREB explosion opportunity-generation or resolver-outcome?** Neither in the way the task's own dichotomy framed it — it is a **world-state input-quality problem feeding a correctly-functioning opportunity/eligibility gate and resolver**: one real opportunity per miss (not duplicated), but that opportunity's own eligible-candidate list is built from stale defender geometry that, for interior shots specifically, contains no real defender at all.

## K. TURNOVERS — dominant source

Subtype counts (seed 23024): `CLEAN_INTERCEPTION` 91 (52%), `LOOSE_BALL_DEFENSE_RECOVERED` 58 (33%), `BAD_PASS_OUT_OF_BOUNDS` 16 (9%), `BAD_PASS_TO_DEFENDER` 9 (5%). Two resolvers dominate together:

1. **`pass_resolution.py`'s own disruption/interception model** (`CLEAN_INTERCEPTION`) is the single largest subtype at 52% of all turnovers — directly downstream of how frequently passes are dispatched (1,183 pass dispatches in this one game) combined with that resolver's own per-eligible-defender disruption-attempt roll.
2. **This diagnostic pass's own `resolve_generic_loose_ball`** (`LOOSE_BALL_DEFENSE_RECOVERED`) is the second-largest subtype at 33% — notably, this outcome fires even for `DEFLECTED_RETAINED_OFFENSE`-sourced loose balls, where the offense is given only a small, explicit 2× weight (Sec. `resolve_generic_loose_ball`'s own docstring) — that weight is evidently not strongly protective; a meaningful share of "offense favored" loose balls still flip to the defense.

No single resolver is responsible for "most" of the ~25% turnover-ending rate in isolation — it is the SUM of pass-interception risk (dominant) plus generic loose-ball recovery variance (secondary) plus a smaller unforced-error/out-of-bounds tail.

## M/L. FOULS — reachability, and the one other real gap noticed

Shooting fouls are **naturally, structurally reachable** in the unchanged diagnostic — 18 occurred in seed 23024, awarding 32 FTA and 24 FTM, all with zero forced configuration. **Floor fouls (`OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL`) were NOT observed at all (0 of 18 fouls)** — this is the ALREADY-DOCUMENTED Phase 23A/23C behavior (`PossessionConfig.force_on_ball_contact_established` defaults to `False`, since no real per-drive contact-occurrence rate has ever been derived in this project) — not a new finding, and not a bug; simply reconfirmed here via structured telemetry rather than re-asserted from memory.

## N. Top three demonstrated causes of current unrealistic output

1. **Defender-zone staleness structurally excludes the defense from interior-zone rebound competition** (100% offense on RIM/FLOATER misses, 0/79) — the single most severe, most directly source-confirmed finding, and very likely the largest individual contributor to the 0.63–0.64 OREB share.
2. **Too few actions per possession** (median 2; 34% of possessions end on the first dispatched action) is the dominant, directly measured driver of the ~2.7–4.2 second median/mean possession length, against a real ~14.6s reference.
3. **Pass-interception (`CLEAN_INTERCEPTION`) and generic loose-ball-to-defense recovery together account for 85% of all turnovers** — both plausible, real resolver behaviors, but their COMBINED magnitude (25.6% possession-ending rate) is a real, measured outcome of how often passes are dispatched and how permissively `pass_resolution.py`'s disruption-attempt roll and this diagnostic pass's own loose-ball weighting currently resolve.

## O. Issues NOT yet demonstrated (do not tune these)

- The secondary, unexplained offense-favored skew on `THREE_POINT`-miss rebounds (57.9% offense despite a HIGHER synthetic defensive rebounding default) — a real, measured anomaly with no confirmed root cause yet; could be eligible-candidate-count asymmetry from the V0 zone-cycling policy, softmax weighting behavior, or something else. Not diagnosed further this pass.
- Whether `pass_resolution.py`'s own base disruption-attempt rate (`BASE_RATE_ANY_DISRUPTION_ATTEMPT`) is itself "too high" in an absolute sense — this diagnostic only shows it is the largest turnover subtype in volume terms, not that its underlying probability is wrong; no calibration judgment is made.
- Whether `resolve_generic_loose_ball`'s 2× favored-team weight is "too weak" in any calibrated sense — only that it empirically allows a meaningful defense-recovery share.
- Any claim about SHOT-MAKE percentages, scoring realism, or FT-rate realism — explicitly out of scope per instruction; the inflated score is a downstream consequence of possession-count inflation, not independently diagnosed here.
- Whether the same defender-zone-staleness mechanism materially affects PASS-disruption eligibility or DRIVE containment realism (this diagnostic only measured its effect on rebound eligibility) — plausible given it is the same root data structure, but not measured this pass.

## P. Exact next engineering investigation/fix recommended

**Investigate and fix defender-zone tracking staleness first** (Finding
N.1) — specifically, decide whether/how a defender's `world.player_zones`
entry should update when their assignment moves during a possession
(e.g., mirror the offensive assignment's current zone at each decision
point, or introduce an explicit "defender recovery" model) — this is a
world-state/orchestration correctness question, not a probability-
tuning question, and per instruction should go to HQ for review before
any code change, since it is exactly the kind of "redesign the rebound
system" decision the task said not to make unilaterally. Recommended
SECOND investigation: quantify how much of the 3.4× pace gap would
remain if a possession had ANY explicit non-zero pre-decision setup cost
(a measurement/design question, not an unauthorized tuning change).

## Confirmations

- No legacy/product files were touched: `git status --short` shows only
  `detailed_game_orchestrator.py` (unchanged from before this task — a
  prior, unrelated 2-line OT-threading diff already present),
  `possession_orchestrator.py` (this task's two tiny diagnostic hooks),
  and the new `detailed_engine_diagnostics.py` /
  `test_detailed_engine_diagnostics.py` / this report. `game_engine.py`,
  `main.py`, `season.py`, `playoffs.py`, `db.py`, `models.py`,
  `README.md`, `ACCURACY.md`, `CLAUDE.md` are untouched.
- Nothing was committed or pushed this task, and no new gameplay phase
  was begun. Phase 23C plus this diagnostic instrumentation remain
  available locally, uncommitted, for HQ review.
