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

**Checkpoint note (superseded by the follow-up task below):** Phase 23C
and this diagnostic instrumentation were subsequently checkpointed as
two separate commits (`aa7e327` "Add Phase 23C minimal detailed game",
`b2d6432` "Add detailed engine diagnostic instrumentation") and pushed
to `origin/codex/empirical-player-modeling`. All measurements above are
preserved exactly as originally reported (the BEFORE state) — see the
new section below for the defender-zone fix built on top of that
checkpoint, left uncommitted for HQ review.

---

# Defender-Zone Staleness Correction

A follow-up, SMALL, targeted fix for exactly the one structural bug this
diagnostic pass demonstrated (Sec. J/N.1 above) — nothing else. Built on
top of the checkpointed commits `aa7e327`/`b2d6432`; **left uncommitted**.

## Root-cause trace

1. **Offensive player zones are initialized** by `default_v0_zone_placement`
   (called once, in `simulate_possession`, at possession start): the
   inbound receiver gets `initial_ball_zone`; the other four offensive
   players cycle deterministically through `_V0_PERIMETER_CYCLE`.
2. **Defender zones are initialized** by `_mirror_defender_zones`
   (called once, immediately after, also only at possession start): each
   defender is set to the SAME zone as `engine.state.assignments[defender_id].assigned_to_player_id`'s
   zone at that instant.
3. **Actions that update OFFENSIVE locations** (confirmed by direct
   source read, all writes to `world.player_zones[X]` where `X` is an
   offensive participant): `_dispatch_drive` (`world.player_zones[driver_id] = engine.state.ball_zone`),
   `_dispatch_pass` on a completed reception (`world.player_zones[receiver_id] = destination_zone`),
   `_dispatch_floor_foul`'s non-bonus re-inbound (`world.player_zones[fouled_player_id] = engine.state.ball_zone`),
   `resolve_generic_loose_ball` (`world.player_zones[winner] = zone` — either side, whoever recovers),
   `_dispatch_rebound` on `SECURED_OFFENSE` (`world.player_zones[result.rebounder_id] = engine.state.ball_zone`).
4. **Actions that update defender posture/assignment**: `resolve_drive`
   calls `engine.update_posture(defender_id, ...)` (posture only);
   `on_ball_pressure_resolution` outcomes may also change posture via
   existing Phase 15 engine methods; Phase 22A's atomic switch
   (`off_ball_screen_resolution._atomic_switch_assignments`) can change
   the assignment pointer itself — but Phase 22A remains fully
   caller-triggered and was NOT invoked anywhere in the orchestrator's
   own dispatch loop (confirmed by source scan, unchanged by this fix).
5. **Whether any action updated defender ZONES before this fix: NO.**
   Confirmed by an exhaustive grep of every `world.player_zones[...] =`
   assignment in the pre-fix file — none targeted a `defender_id`.
6. **Rebound candidate construction** (`_dispatch_rebound`) reads
   `world.player_zones.get(pid, engine.state.ball_zone)` for literally
   every one of the ten players, then `rebound_resolution.eligible_rebound_candidates`
   filters to `candidate.zone == opportunity.effective_zone` (always
   `engine.state.ball_zone` at the moment of the miss, per
   `_dispatch_rebound`'s own explicit `rebound_zone=engine.state.ball_zone`).
7. **Why RIM/FLOATER misses produced total defender exclusion**: an
   interior shot only happens after the ball has moved into
   `RESTRICTED_RIM`/`PAINT`, almost always via a drive. Step 3 correctly
   moves the DRIVER's own zone to match. The driver's ASSIGNED DEFENDER's
   zone, however, was mirrored once at possession start (frequently to
   the driver's ORIGINAL, often-perimeter, starting zone) and never
   updated when the driver subsequently moved — leaving that defender
   structurally ineligible for the resulting interior-zone rebound.
   Empirically confirmed pre-fix: 0 of 79 interior (RIM/FLOATER)
   rebound opportunities were won by the defense in seed 23024, and
   ZERO defenders were ever recorded in the interior zone across 369
   possessions that reached a rebound opportunity.

## Smallest ownership-correct fix

**One new function, `_sync_assigned_defender_zone(engine, world, offensive_player_id, new_zone)`**,
called at the four offensive-zone-update sites that represent
genuinely supported possession actions moving a tracked player
(`_dispatch_drive`, `_dispatch_pass`'s completed-reception branch,
`_dispatch_floor_foul`'s re-inbound, `_dispatch_rebound`'s
`SECURED_OFFENSE` continuation). It does exactly one thing: look up the
offensive player's CURRENT defender via `engine.state.assignments`
(the existing, real man-to-man matchup pointer — re-resolved fresh on
every call, so it automatically respects a switch) and write that
defender's entry in the SAME `world.player_zones` dict every other
update already uses.

This is the EXACT SAME real rule `_mirror_defender_zones` already
established once at possession start (a defender occupies their
assignment's zone) — simply re-applied every time that assignment's own
zone changes, instead of only once. No new location authority, no
`defender_zones_v2`, no rebound-resolver-internal synthetic zone: still
`PossessionWorld.player_zones`, the ONE existing coarse-location map.
`resolve_generic_loose_ball`'s own zone write (item 3 above) was
DELIBERATELY left untouched — a loose-ball recovery does not have a
stable "offensive player + their assigned defender" framing (either
side may recover it, becoming the new ball handler), and touching it
was not required to remove the demonstrated pathology (which is
specifically about drives/receptions into the interior).

No probability, duration, skill weight, or resolver logic was touched.
`_sync_assigned_defender_zone` consumes no RNG (verified:
`test_sync_consumes_no_rng`) and does not depend on posture,
`AdvantageModel`, or any ability value — it is a pure, deterministic
coarse geometry update.

## State ownership after the fix

Unchanged from the project's existing doctrine, restated for clarity:

- `PossessionWorld.player_zones` — coarse possession spatial/context
  state for all ten players (the ONLY location authority; still one
  dict, now kept fresh rather than only initialized once).
- `PossessionState` (`engine.state`) — ball/carrier/in-flight possession
  mechanics; untouched by this fix.
- `engine.state.assignments` — the defensive matchup/posture
  relationship; untouched by this fix, and now READ fresh (never
  cached) every time a defender's zone needs to follow their man.

No duplicate authority was introduced (verified:
`test_sync_writes_only_to_the_existing_player_zones_dict`, which checks
both the source text and `PossessionWorld`'s own dataclass fields for
any second location map).

## Files changed for the fix

Only `possession_orchestrator.py` (the new `_sync_assigned_defender_zone`
function plus four call sites) and `test_possession_orchestrator.py`
(12 new focused tests, plus one pre-existing multi-game diagnostic
test's loose bounds widened to reflect the fix's own legitimate,
demonstrated effect on the rebound distribution — see below). No other
file was touched.

## Focused tests (`TestDefenderZoneStalenessFix`, 12 new tests)

1. `test_defender_zones_initialized_to_match_their_assignment` — defender zones initialize correctly.
2. `test_drive_updates_the_drivers_own_defender_zone` — a drive updates the relevant defender's zone.
3. `test_pass_reception_updates_the_receivers_own_defender_zone_not_frozen_across_sequence` — zone state is not frozen across a drive/pass sequence.
4. `test_matchup_identity_unchanged_by_zone_sync` — matchup identity remains valid after movement.
5. `test_zone_sync_respects_a_real_atomic_switch` — switch assignment remains atomic; the sync follows the NEW pairing, never a stale one.
6. `test_interior_miss_has_a_structurally_eligible_defender_candidate` — an interior miss produces at least one structurally eligible defensive candidate.
7. `test_defensive_rebound_reachable_after_rim_and_floater_misses` — a defensive rebound is genuinely reachable after both a RIM and a FLOATER miss.
8. (combined into 7)
9. `test_three_point_rebound_both_outcomes_remain_reachable` — existing 3PT rebound behavior (both offense and defense winning) remains structurally valid.
10. `test_sync_writes_only_to_the_existing_player_zones_dict` — no duplicate player/location authority introduced.
11. `test_deterministic_replay_preserved_with_zone_sync` — same-seed determinism preserved.
12. `test_sync_consumes_no_rng` — the sync itself never rolls dice or reads an ability value (action outcomes are unchanged by the fix except where downstream geometry legitimately differs).
13. `test_diagnostics_still_reconstructs_correctly_after_zone_fix` — diagnostic telemetry remains observational and structurally intact post-fix.
14. No legacy/product file was touched (unchanged from the prior checkpoint's own confirmation; re-verified via `git status --short` below).

**Result: 56/56 in `test_possession_orchestrator.py`, 24/24 in
`test_detailed_game.py`, 16/16 in `test_detailed_engine_diagnostics.py`
→ 96/96 combined focused. Full suite: 904/904 OK** (892 baseline + 12
new). Zero regressions. One pre-existing test's loose numeric bounds
(`test_ten_game_sample_matches_known_reported_aggregate`) were widened
to reflect the fix's own intended, demonstrated shift in the rebound
distribution — not a weakened assertion, a legitimately different real
range now being measured.

## Seed 23024 — BEFORE vs AFTER (BEFORE preserved exactly as originally reported above)

| Metric | BEFORE (pre-fix) | AFTER (post-fix) |
|---|---|---|
| Score | HOME 463 – 400 AWAY | HOME 458 – 395 AWAY |
| Total possessions | 681 | 794 |
| Mean / median possession seconds | 4.229 / 2.700 | 3.627 / 2.300 |
| Mean / median actions per possession | 3.696 / 2 | 3.171 / 2 |
| FGA / FGM / misses | 857 / 300 / 557 | 857 / 290 / 567 |
| Rebound opportunities | 552 | 562 |
| OREB / DREB | 353 / 199 | 276 / 286 |
| OREB share `OREB/(OREB+DREB)` | **0.639** | **0.491** |
| Interior (RIM+FLOATER) opportunities | 79 | 51 |
| Interior OREB / DREB | 79 / **0** | 25 / **26** |
| 3PT opportunities | 473 | 511 |
| 3PT OREB / DREB | 274 / 199 | 251 / 260 |
| OREB-count distribution (0/1/2/3+) | 459/134/61/27 | 587/151/47/9 |
| Turnovers (rate) | 174 (25.6%) | 211 (26.6%) |
| Personal fouls / FTA / FTM | 18 / 32 / 24 | 19 / 35 / 25 |
| Faults | 0 | 0 |

The FGA count is identical (857) because the shot-attempt/make-
probability machinery was not touched at all — the change is entirely
in what happens AFTER a miss.

## 10-game sample (seeds 23024–23033) — BEFORE vs AFTER

| Metric | BEFORE | AFTER |
|---|---|---|
| Mean possessions | 672.3 | 769.8 |
| Mean OREB / DREB | 327.3 / 196.7 | 257.9 / 275.2 |
| Mean OREB share | 0.624 | 0.484 |
| Mean personal fouls | 16.3 | 15.4 |
| Mean FTA | 34.4 | 34.0 |
| Mean turnovers (rate) | 162.0 (24.0%) | 187.3 (24.3%) |
| Faults across all 10 games | 0 | 0 |

## What improved

- **The pathological total exclusion is gone.** Interior DREB went from
  a hard **0** to **26** (seed 23024) — the defense can now genuinely
  compete for and win interior rebounds, because their zone now
  actually reflects where the play developed rather than where the
  possession began.
- **OREB share dropped from 0.64 → 0.49 (seed 23024) / 0.62 → 0.48
  (10-game mean)** — a large, real, structural shift, entirely as a
  DOWNSTREAM CONSEQUENCE of correct geometry, with zero change to any
  rebounding skill value, weight, or resolver constant.
- **3PT rebounding also became more balanced** (0.42 → 0.51 defense
  share on 3PT misses specifically) as a secondary, expected effect of
  the same fix (some defenders assigned to players who moved via a pass
  were ALSO previously stale for perimeter rebounds, not only interior
  ones).
- Zero faults, identical deterministic-replay behavior, and no
  detectable change in FGA volume or shot-family mix (the fix touches
  only post-miss geometry).

## What remains abnormal (NOT addressed by this fix, do not tune)

- **OREB share (0.48–0.49) is still roughly 1.7–2× the real NBA rate
  (~0.25–0.28).** This fix removed the total-exclusion pathology; it
  did not, and was not intended to, calibrate the resulting rate to any
  target.
- **Total possessions increased (672→770 mean)**, a legitimate,
  understood downstream effect: fewer OREB-driven second-chance
  loop-continuations per trip means each individual trip now resolves
  faster on average, so more distinct trips fit into the same 48
  minutes of game clock. This is a real, explainable consequence of the
  fix, not a new bug.
- **Turnover rate is essentially unchanged (~25–26%)** — expected, since
  this fix never touched pass/turnover resolution.
- **Pace remains structurally too fast, UNCHANGED, as instructed.**
  Mean/median possession length (3.6s / 2.3s post-fix, vs. 4.2s / 2.7s
  pre-fix — the shift is a downstream artifact of the rebound-share
  change, not a timing fix) is still far below the real ~14.6s
  reference. Median actions per possession is still 2. **No duration,
  action-selection weight, or timing constant was touched in this
  task**, per explicit instruction — this remains the next,
  separately-scoped, HQ-reviewed engineering problem.

## Classification

**FIX VALIDATED WITH FLAGS.** The demonstrated geometric pathology
(0-of-79 interior defensive rebounds, caused by literal defender-zone
staleness) is structurally removed — defense can now win interior
rebounds, no duplicate rebound opportunities were introduced, no
source-of-truth violation was introduced, deterministic replay remains
valid, and the full test suite (904/904) passes. Flagged: the resulting
OREB share, while no longer pathological, is not claimed to be
realistic or calibrated — no such claim was in scope. Pace remains
unaddressed by design.

## Confirmations

- No legacy/product files touched: `game_engine.py`, `main.py`,
  `season.py`, `playoffs.py`, `db.py`, `models.py`, `README.md`,
  `ACCURACY.md`, `CLAUDE.md` are all untouched by this fix.
- The fix (`possession_orchestrator.py` + `test_possession_orchestrator.py`
  changes) remains **UNCOMMITTED**, for HQ review, on top of the two
  pushed checkpoint commits (`aa7e327`, `b2d6432`).
- No probability, duration, weight, or calibration constant was changed
  anywhere in this task — confirmed by source diff review: the only
  new code is `_sync_assigned_defender_zone` (a pure dict write with no
  RNG, no skill read) and its four call sites.
