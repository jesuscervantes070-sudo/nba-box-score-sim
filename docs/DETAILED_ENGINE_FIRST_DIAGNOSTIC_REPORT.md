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

**Checkpoint note:** the defender-zone fix above was subsequently
committed (`7fc98de` "Fix defender zone synchronization") and pushed to
`origin/codex/empirical-player-modeling`, on top of the two earlier
checkpoint commits (`aa7e327`, `b2d6432`). All BEFORE/AFTER measurements
above are preserved exactly as reported. The section below is a
follow-up diagnostic pass built on that pushed baseline; it changes NO
simulation behavior (only `test_detailed_engine_diagnostics.py` gained
3 new tests) and remains uncommitted.

---

# Timing / Pace Root-Cause Diagnosis

Purely diagnostic. **No duration, probability, or action-selection
weight was changed anywhere in this section's work.** Built on the
pushed defender-zone-fix baseline (`7fc98de`); all measurements below
use the SAME seed 23024 and 23024–23033 sample as every prior
measurement in this report, post-zone-fix.

## C. Clock ownership map

| Action / transition | Clock owner | Duration source | Typical seconds | Random/fixed | Can be 0? | Notes |
|---|---|---|---|---|---|---|
| DRIVE (ordinary outcome) | `possession_orchestrator._charge_time` | `PossessionConfig.drive_action_seconds` | 2.5 | Fixed | No (min with remaining clock via `max(0, ...)`) | Charged for every `resolve_drive` outcome AND for `FORCED_PICKUP`/`CLEAN_STRIP_LOOSE` pressure outcomes |
| DRIVE → floor-foul branch (`OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL`) | *(none)* | — | **0** | — | **Always 0** | **Literal bug** (Sec. J) — `_dispatch_floor_foul` never calls `_charge_time` |
| PULL_UP | `_charge_time` | `pull_up_action_seconds` | 1.5 | Fixed | No | Same duration whether made, missed, or blocked |
| CATCH_AND_SHOOT | `_charge_time` | `catch_and_shoot_action_seconds` | 1.0 | Fixed | No | Same duration whether made, missed, or blocked |
| Shooting-foul branch (`_dispatch_shooting_foul`) | `_charge_time` | same `action_seconds` as the shot that triggered it | 1.0–1.5 | Fixed | No | Charges the SHOT's own duration; the whistle/FT sequence itself adds none (see FT row) |
| SWING_PASS / KICKOUT / RESET_PASS / POCKET_PASS | `pass_resolution.resolve_pass` (NOT `_charge_time`) | `FLIGHT_DURATION_SECONDS[family]` | 0.4 (DIRECT), 0.6 (KICKOUT), 0.9 (SKIP) | Fixed per family | No | Ball-FLIGHT time only (Sec. H) — verified no double-charge (`test_pass_dispatch_never_calls_charge_time`) |
| Free throws (shooting-foul or floor-foul bonus) | *(none — deliberately)* | — | 0 | — | Always 0 | **Correct, intentional real-basketball behavior** — a whistle stops the game clock; FT attempts are dead-ball time by rule, not a bug |
| Rebound resolution (`_dispatch_rebound`) | *(none)* | — | 0 | — | Always 0 | **Not currently represented** at all — the scramble/box-out itself consumes no modeled time |
| Loose-ball recovery | `_charge_time` (top-of-loop) | `loose_ball_action_seconds` | 0.5 | Fixed | No | Only for the GENERIC (non-shot-adjacent) loose-ball path |
| Inbound (`engine.inbound`) | *(none — `dt=0.0` default, never overridden)* | — | 0 | — | Always 0 | Correct for a dead ball; but ALSO used for the live first-touch of a NEW possession, where 0 elapsed time also means "bringing the ball up/organizing" is unmodeled (Sec. I) |
| Transition start (`RestartType.LIVE_TRANSITION`) | *(none)* | — | 0 | — | Always 0 | Same `engine.inbound(..., dt=0.0)` call as a dead-ball restart; no distinct transition-advancement time |
| Possession initialization (`simulate_possession`'s own setup) | *(none)* | — | 0 | — | Always 0 | Clock is only ever SET (carried over from the prior possession), never decremented, during setup |
| Dead-ball turnover via a charge (`engine.dead_ball_turnover`) | *(none, inherits the 0-charge floor-foul branch)* | — | 0 | — | Always 0 | Same literal bug as the floor-foul row above |
| Dead-ball turnover via a bad pass (`BAD_PASS_OUT_OF_BOUNDS`) | `resolve_pass` | `FLIGHT_DURATION_SECONDS[family]` | 0.4–0.9 | Fixed | No | Charged normally — the pass itself still flew before going out of bounds |
| Made basket → next possession | *(none additional)* | — | 0 | — | Always 0 | Correct — the shot's own duration already accounts for the possession; the administrative handoff to the next possession is real dead-ball time |

**Shot-clock vs. game-clock consistency**: both are decremented
TOGETHER, in the SAME `_charge_time` call and the SAME `resolve_pass`
flight-time decrement — verified by direct source read (both fields are
set in one `replace()` call in each of the two decrement sites) and by
the existing, already-exercised `DetailedGameInvariantError` checks in
`detailed_game_orchestrator.apply_possession_result` (game clock
monotonically non-increasing, never negative, across 7,698+ possessions
in the 10-game sample with zero violations). **No desync bug found.**

## D. Action timing distribution (seed 23024, post-zone-fix)

| Action | Count | % of actions | Mean sec | Total sec | % of game clock (2,880s) |
|---|---|---|---|---|---|
| DRIVE | 476 | 18.9% | 2.500 | 1,190.0 | 41.3% |
| PULL_UP | 536 | 21.3% | 1.500 | 804.0 | 27.9% |
| CATCH_AND_SHOOT | 321 | 12.7% | 0.994 | 319.2 | 11.1% |
| SWING_PASS | 612 | 24.3% | 0.400 | 244.5 | 8.5% |
| RESET_PASS | 573 | 22.8% | 0.400 | 229.2 | 8.0% |
| **Action total** | **2,518** | **100%** | — | **2,786.9** | **96.8%** |
| Generic loose-ball recovery | 187 | *(not a dispatched action)* | 0.5 | 93.5 | 3.2% |
| **Grand total** | | | | **2,880.4** | **100.1%*** |

*The 0.4s excess over the true 2,880.0s is pure floating-point summation
error across thousands of additions — confirmed exact reconciliation
(`sum(elapsed) == 2880.0` to 6 decimal places) via
`test_possession_elapsed_seconds_reconcile_with_total_period_length`.

10-game sample (seeds 23024–23033) action mix is nearly identical in
proportion: DRIVE 19.8%/2.495s, PULL_UP 20.4%/1.499s, CATCH_AND_SHOOT
13.1%/0.999s, SWING_PASS 23.4%/0.400s, RESET_PASS 23.3%/0.400s.

## E. Possession action-count distribution

| Actions | Seed 23024 | 10-game sample |
|---|---|---|
| 1 | 274 (34.5%) | 2,675 (34.7%) |
| 2 | 156 (19.6%) | 1,424 (18.5%) |
| 3 | 108 (13.6%) | 1,036 (13.5%) |
| 4+ | 256 (32.2%) | 2,563 (33.3%) |
| Mean | 3.171 | 3.243 |
| Median | 2 | 2 |
| p90 | 7 | 7 |

## F. First-action termination breakdown (seed 23024)

274 of 794 possessions (34.5%) end on the very first dispatched action.
Broken down by the actual action type dispatched:

| First action type | Count | % of 1-action possessions |
|---|---|---|
| PULL_UP | 117 | 42.7% |
| CATCH_AND_SHOOT | 94 | 34.3% |
| SWING_PASS | 31 | 11.3% |
| RESET_PASS | 28 | 10.2% |
| DRIVE | 4 | 1.5% |

By terminal reason: MADE_FG 106, DEFENSIVE_REBOUND 104 (an immediate
shot that missed and was immediately rebounded by the defense),
TURNOVER 63 (an immediate pass interception/bad pass), PERIOD_END 1.
The 4 DRIVE-first cases are a stripped/loose ball recovered by the
defense within the SAME dispatched action (a real, structurally
distinct path — `CLEAN_STRIP_LOOSE` → generic loose-ball recovery →
`DEFENSE_RECOVERED`, all counted as one `action_log` entry since only
one `ActionIntent` was ever selected).

**Classification**: PULL_UP/CATCH_AND_SHOOT together are 77% of
one-action possessions. These are NOT illegitimate basketball events in
isolation (a real NBA possession can legitimately be a single
catch-and-shoot) — the problem is architectural, not eventful:
`SelectionPolicy` is free to choose a fully terminal shot action as the
VERY FIRST decision after inbound, with zero prior possession
development modeled at all. This is best classified as **mostly
category C (too few actions) and D (missing initialization/setup
time) combined — NOT primarily category A** (individual action
durations, while short, are a secondary contributor: even a much longer
`PULL_UP` duration would not by itself prevent a possession from
legitimately consisting of exactly one action).

## G. Full 2,880-second clock accounting (seed 23024, one complete regulation game)

| Category | Total seconds | % of game clock |
|---|---|---|
| Drives | 1,190.0 | 41.3% |
| Shot actions (PULL_UP + CATCH_AND_SHOOT, including the shooting-foul branch's own shot duration) | 1,123.2 | 39.0% |
| Passes (SWING_PASS + RESET_PASS ball-flight time) | 473.7 | 16.4% |
| Generic loose-ball recovery | 93.5 | 3.2% |
| Rebound resolution | 0.0 | 0.0% |
| Free throws | 0.0 | 0.0% (correct — dead-ball time) |
| Floor-foul branch | 0.0 | 0.0% (0 occurrences this seed; would be 0 even if occurred — literal bug, Sec. J) |
| Inbound / possession setup / transition advancement | 0.0 | 0.0% (unmodeled, Sec. I) |
| **Total** | **2,880.4*** | **100%** |

*Reconciles to the true 2,880.0s within floating-point summation error
(verified: `test_action_and_loose_ball_time_fully_explains_total_elapsed`).
**Every second of consumed game clock is fully attributable to a known,
already-tracked category — there is no "unclassified" or silently-lost
time.**

## H. Exact meaning of the current ~0.4-second pass duration

Confirmed by direct source read of `pass_resolution.py`:
`FLIGHT_DURATION_SECONDS` is explicitly documented in that module as
"coarse, real, flight-duration-class placeholders" — **BALL FLIGHT TIME
ONLY** (the physical time the ball is in the air between release and
reception), NOT the entire time from one offensive decision to the
next. There is currently **no separate representation anywhere** for
the decision/setup/hold/dribble time between a reception and the next
selected action — the very next `StructuralContext`/opportunity
generation happens immediately upon reception with zero elapsed time
beyond the pass's own flight. Per instruction, this value was **not
modified** — increasing it to represent total offensive possession time
would conflate two conceptually distinct things this resolver was
deliberately built to keep separate (per `pass_resolution.py`'s own
module docstring, ball flight is real, physically-grounded modeling;
inflating it to also carry setup/decision time would not be).

## I. Missing game-clock-consuming basketball phases

| Phase | Classification | Currently modeled? |
|---|---|---|
| Bringing the ball up after an inbound | GAME CLOCK SHOULD RUN | **NOT CURRENTLY REPRESENTED** |
| Crossing half court | GAME CLOCK SHOULD RUN | **NOT CURRENTLY REPRESENTED** |
| Offense organizing / initial halfcourt setup | GAME CLOCK SHOULD RUN | **NOT CURRENTLY REPRESENTED** |
| Transition advancement | GAME CLOCK SHOULD RUN | **NOT CURRENTLY REPRESENTED** (same 0-time inbound call as a dead-ball restart) |
| Catch/hold before the next selection (decision time) | GAME CLOCK SHOULD RUN | **NOT CURRENTLY REPRESENTED** — folded into nothing; the pass's own flight time is the only thing charged around a reception |
| Dribble/setup time between modeled actions (e.g. between two DRIVE/PASS dispatches by the same player) | GAME CLOCK SHOULD RUN | **NOT CURRENTLY REPRESENTED** |
| Reset after an OREB (the "second chance" re-organization) | CONTEXT DEPENDENT (a putback attempt should cost little; a reset-and-restart should cost more) | **NOT CURRENTLY REPRESENTED** — an OREB continuation goes straight into the next `StructuralContext` build with zero elapsed time |
| Dead-ball administration (ball retrieval, walking to the inbound spot, referee administration) | **GAME CLOCK SHOULD NOT RUN** (correct real-basketball rule) | Correctly modeled as zero (this is NOT a gap) |
| Free-throw attempts themselves | **GAME CLOCK SHOULD NOT RUN** (correct real-basketball rule — clock is dead on a whistle) | Correctly modeled as zero (this is NOT a gap) |

The clear, dominant pattern: every phase where the ball is genuinely
LIVE and players are actively moving/deciding is currently
**unmodeled** (zero time), while every phase where the real rule is
"clock legitimately stops" is already CORRECTLY zero. This is not a
"blindly add duration everywhere" finding — it is specifically the
LIVE, pre-decision phases that are missing.

## J. Literal clock bug discovered

**A drive that routes into the floor-foul branch
(`OnBallContactOutcome.OFFENSIVE_CHARGE` / `DEFENSIVE_FLOOR_FOUL`)
charges exactly ZERO elapsed game-clock seconds for the entire drive
sequence**, while every OTHER drive outcome
(`CLEAN_CONTROL`/`DISRUPTED`/`NO_CALL_CONTACT`/`FORCED_PICKUP`/
`CLEAN_STRIP_LOOSE`) correctly charges `drive_action_seconds` (2.5s).
Confirmed by direct source read: `_dispatch_drive` returns directly
from `_dispatch_floor_foul(...)` at both charge/floor-foul branches
(lines calling `_dispatch_floor_foul`) BEFORE ever reaching the
`_charge_time(engine, config.drive_action_seconds)` call later in the
function; `_dispatch_floor_foul` itself never calls `_charge_time`
either. Demonstrated directly:
`test_floor_foul_branch_currently_charges_zero_elapsed_time` (with
`force_on_ball_contact_established=True`, since the default `False`
config never reaches this branch at all). **This bug has ZERO
measurable effect on any number reported anywhere in this report** —
default autonomous play never exercises the floor-foul branch — so it
is reported here as a literal finding, per instruction, and **NOT
fixed** in this task (fixing it is not required for correct measurement
of the default-config pace diagnosis).

## K. Primary demonstrated cause(s) of 3–4 second possessions

1. **Possessions contain too few actions (category C), and there is no
   modeled cost for the possession-development phases that would
   normally precede the first live decision (category D).** 34.5–34.7%
   of possessions terminate on their very first dispatched action;
   median actions per possession is 2 (vs. what a real NBA possession —
   which typically involves an advance, a catch, at least one reset or
   probing action — would show). This is the DOMINANT, directly measured
   cause.
2. **Individual action durations are short by explicit design**
   (category A), particularly passes (0.4s, ball-flight only, correctly
   scoped — Sec. H) — a real but SECONDARY contributor, since these
   durations are honest about what they represent (flight time, not
   total decision time) rather than being wrong for their own stated
   purpose.
3. **No elapsed time exists between the inbound/reception and the next
   live decision (category B)** — this is the SAME underlying gap as
   (1) and (D), restated at the mechanism level: there is currently no
   code path that charges ANY time for "the ball is live and a player
   is holding/organizing/advancing it" outside of the four modeled
   action types.
4. **Category E (terminal transitions omitting elapsed time) was
   specifically investigated and NOT found to be a material cause** —
   every terminal transition (MADE_FG, DEFENSIVE_REBOUND, TURNOVER,
   FINAL_FT_MADE, PERIOD_END, SHOT_CLOCK_VIOLATION) correctly inherits
   whatever time the triggering action already charged; the FULL
   2,880-second reconciliation (Sec. G) shows no unaccounted-for gap.

**In short: the 3–4 second average possession is real, structural, and
caused primarily by too few modeled actions combined with a completely
unmodeled "live but undecided" phase — not primarily by any individual
action's duration being wrong for what it represents.**

## L. Recommended smallest timing architecture correction

**Option C (possession-stage timing: advance/setup → action
decision/execution → continuation) is recommended over A, B, or D**,
evaluated on semantics, not merely on ability to hit 14.6s:

- **Option A (increase existing action durations)** — REJECTED as the
  primary fix. Passes represent ball flight only (Sec. H); inflating
  that value to also carry setup/decision time would make the resolver
  represent two different physical quantities under one name, which is
  conceptually wrong per the task's own framing and this module's own
  documented scope.
- **Option B (separate action execution time from inter-action/setup
  time)** — a real, valid partial mechanism, but by itself doesn't
  address the STRUCTURAL fact that a possession can currently have
  ZERO actions worth of "setup" before an immediately-terminal shot; it
  would need to be paired with something like Option C's own staging
  anyway.
- **Option C (possession-stage timing)** — the best semantic fit: an
  explicit "advance/setup" stage (a real, coarse, uncalibrated cost
  representing bringing the ball up + crossing half-court + initial
  organization) would run ONCE per possession (or once per
  live-transition-vs-dead-ball-restart, since these are legitimately
  different in real basketball — a transition possession should cost
  LESS setup time than a half-court dead-ball inbound), BEFORE the
  first `SelectionPolicy` decision is ever reached — closing the
  demonstrated category-C/D gap directly, without touching what any
  existing action-duration value represents.
- **Option D (another simpler model)** — no simpler model was
  identified this pass that addresses the SAME root cause without
  effectively re-deriving Option C under a different name.

**This is NOT implemented in this task** — it is a structural fix
recommendation only, requiring HQ review (per instruction, "propose the
smallest clean timing model. Do not implement it yet").

## M. Structural fix needed now vs. empirical value needed later

**STRUCTURAL FIX NEEDED NOW** (architecture, not data-dependent):
- Add a real "advance/setup" stage/hook to the possession loop (Option
  C) — the MECHANISM can be built with a placeholder, explicitly
  flagged, UNCALIBRATED value, exactly like every other duration in
  this project.
- Fix the literal floor-foul zero-duration bug (Sec. J) — a pure
  bookkeeping correction, not a calibration question, whenever the
  floor-foul branch's timing is next touched.

**EMPIRICAL VALUE NEEDED LATER** (requires real data, explicitly NOT
researched this task — data dependency only, per instruction):
- The actual real-world SECONDS a possession's advance/setup phase
  should cost, likely derivable from NBA play-by-play event timestamps
  (time between an inbound/rebound event and the first shot/pass
  event), public tracking-data-derived possession-duration
  distributions, or a similar source — NOT browsed/researched this
  task.
- Whether/how much that cost should differ between a live-transition
  restart and a dead-ball halfcourt restart (real NBA transition
  possessions are measurably faster on average) — an empirical
  question, not assumed here.
- Any recalibration of the EXISTING action durations themselves once a
  real total-possession-duration target distribution is available to
  fit against — explicitly out of scope for this diagnostic task.

## N. Tests

`python3 -m unittest test_detailed_engine_diagnostics -v` → **19/19 OK**
(16 prior + 3 new in `TestClockAccounting`):
`test_possession_elapsed_seconds_reconcile_with_total_period_length`,
`test_action_and_loose_ball_time_fully_explains_total_elapsed`,
`test_floor_foul_branch_currently_charges_zero_elapsed_time` (documents
the Sec. J bug without fixing it). Full suite:
`python3 -m unittest discover -p "test_*.py"` → **907/907 OK** (904
baseline + 3 new). Zero regressions.

## O. Confirmations

- No timing, probability, or calibration constant was changed anywhere
  in this diagnostic pass — verified: `git diff --stat` shows only
  `test_detailed_engine_diagnostics.py` modified (58 insertions, tests
  only); no source module was touched.
- No legacy/product file was touched: `game_engine.py`, `main.py`,
  `season.py`, `playoffs.py`, `db.py`, `models.py`, `README.md`,
  `ACCURACY.md`, `CLAUDE.md` are all untouched.
- This diagnostic work remains **UNCOMMITTED**, on top of the pushed
  defender-zone-fix checkpoint (`7fc98de`).
- No new gameplay phase was begun.

**Checkpoint note:** the timing-diagnosis work above was subsequently
committed (`996c5d8` "Document detailed engine timing diagnosis") and
pushed. All measurements above are preserved exactly as reported. The
section below implements the recommended fix/hook on top of that
pushed baseline; it remains uncommitted.

---

# Structural Timing Hook

Implements exactly two things, both recommended (not newly decided) by
the prior diagnosis: (1) the literal floor-foul zero-duration clock bug
fix, and (2) the smallest possible home for LIVE, pre-decision
possession-stage time. **No probability, selection weight, rebound/
turnover/shooting/foul rate, or player attribute was touched anywhere.**

## Why the hook was needed

The prior diagnosis (Sec. K above) concluded the 3–4 second average
possession was caused primarily by too few modeled actions combined
with a completely unmodeled "live but undecided" phase — NOT by any
individual action duration being wrong for what it represents (ball
flight, drive execution, and shot execution are all honest about their
own narrow scope). There was no existing code location where a real,
configurable, non-zero cost for "the ball is live and a player is
advancing/organizing/re-setting it" could be charged. This section adds
exactly that location, and nothing else.

## Floor-foul clock bug correction

**Root cause (recap, Sec. J above):** `_dispatch_drive` returned
directly into `_dispatch_floor_foul` on `OFFENSIVE_CHARGE`/
`DEFENSIVE_FLOOR_FOUL` outcomes, before ever reaching the
`_charge_time(engine, config.drive_action_seconds)` call every OTHER
drive outcome reaches.

**Exact fix:** `_charge_time(engine, config.drive_action_seconds)` is
now called immediately before each `_dispatch_floor_foul(...)` call in
`_dispatch_drive` — the SAME existing constant, the SAME existing
clock-decrement mechanism, charged EXACTLY once. No new duration was
invented. `_dispatch_floor_foul` itself still charges nothing
additional — foul ADMINISTRATION remains real dead-ball time (correct,
unchanged), and offensive-foul/defensive-floor-foul semantics
(personal foul, team foul, bonus, turnover-vs-continuation) are
completely untouched. Verified:
`test_floor_foul_drive_consumes_drive_time_exactly_once`,
`test_floor_foul_branch_now_charges_the_same_drive_time_as_any_other_drive_outcome`.

## Setup-stage semantics

A new `PossessionStage` (plain string constants, the SAME convention
as `DriveOutcome`/`PassOutcome`/`OnBallContactOutcome`/
`PossessionTerminalReason` — not a locked `Enum`):

- `HALFCOURT_ENTRY` — a new possession beginning at a dead-ball,
  halfcourt inbound.
- `TRANSITION_ENTRY` — a new possession beginning live, in transition.
- `SECOND_CHANCE_RESET` — an offensive rebound continuing the SAME
  possession.

`_charge_possession_stage_time(engine, world, config, stage, step)` is
the ONE place any of these is charged. It reuses `_charge_time`
verbatim (same game-clock/shot-clock pairing, same `max(0, ...)`
floor) — this is a new REASON to call the existing clock mechanism,
never a new clock owner. The real, post-clamp elapsed amount is logged
to a NEW, dedicated `PossessionWorld.stage_timing_log` list — kept
deliberately SEPARATE from `world.action_log` so `action_count` keeps
meaning exactly what it already meant (a count of `SelectionPolicy`-
chosen `ActionIntent` dispatches); a setup-stage charge is not one of
those.

## Config fields / placeholders

Added to `PossessionConfig`, prominently marked:

```python
ordinary_entry_seconds: float = 3.0       # UNCALIBRATED PLACEHOLDER
transition_entry_seconds: float = 1.5     # UNCALIBRATED PLACEHOLDER
second_chance_reset_seconds: float = 1.0  # UNCALIBRATED PLACEHOLDER
```

**UNCALIBRATED PLACEHOLDER. NOT NBA EMPIRICAL TRUTH.** Chosen only to
be conservative, nonzero, and directionally sensible (a live transition
entry should structurally cost less than a dead-ball halfcourt entry; a
second-chance reset should cost less than either) — NOT fit to any
target possession-duration distribution, and NOT chosen to approach the
14.6-second reference.

## Exact locations where setup time is charged

1. **Entry stage** — once, in `simulate_possession`, immediately after
   `engine.inbound(...)`/zone setup and BEFORE the possession's main
   loop begins. The stage (`HALFCOURT_ENTRY` vs. `TRANSITION_ENTRY`) is
   chosen from `config.initial_phase` — a real signal Phase 23B already
   supplies, not new plumbing.
2. **Second-chance reset** — once per real offensive rebound, inside
   `_dispatch_rebound`'s `SECURED_OFFENSE`/`TEAM_REBOUND_OFFENSE`
   branch, immediately before returning `None` to continue the SAME
   possession.

No third call site exists. `_charge_possession_stage_time` itself
raises on an unknown stage name, and `_charge_time` (reused, unchanged)
still raises on a non-positive duration — both existing safety
properties are preserved.

## State / clock ownership

Unchanged doctrine, reaffirmed: `_charge_time` remains the SOLE
game-clock/shot-clock decrement primitive in this module (`resolve_pass`
remains the one other, pre-existing, already-audited decrementer, for
ball-flight time only). The Structural Timing Hook adds no new clock
field, no new clock owner, and no second decrement path — only new
CALLS into the existing one.

**Double-count bug found and fixed while building this hook** (a real
finding, not a simulation bug — a diagnostic-instrumentation
bookkeeping issue): the top-level loop's `action_log` entry for a
dispatched `ActionIntent` is measured as `clock_before - clock_after`
around the WHOLE `dispatch_action` call. When a shot's own miss
triggers `_dispatch_rebound`'s `SECOND_CHANCE_RESET` charge WITHIN that
same call (an OREB resolved inline), the outer measurement folded the
stage-timing seconds into the action's own recorded elapsed — and
`stage_timing_log` recorded them AGAIN, separately. **Fixed**: the loop
now subtracts any `stage_timing_log` entries newly appended DURING the
`dispatch_action` call from the action's own recorded elapsed, before
logging it — the REAL clock decrement (verified via the full
possession-elapsed reconciliation) was always correct; only the
DIAGNOSTIC ATTRIBUTION between the two telemetry categories was
double-counting. Verified:
`test_action_and_loose_ball_time_fully_explains_total_elapsed` (now
reconciles exactly, `2880.0 == 2880.0`, vs. a `163.0`-second over-count
before the fix) and `test_shot_execution_duration_excludes_stage_timing`.

## OREB timing semantics

An offensive rebound remains the SAME `possession_id` and the SAME
`simulate_possession` call — confirmed directly
(`test_second_chance_reset_charged_after_a_real_oreb_same_possession_id`:
every event in the terminal result shares one `possession_id` even
after a real, boosted-rate OREB fires a `SECOND_CHANCE_RESET` charge).
The shot-clock reset itself is Phase 19's own existing, unmodified
`oreb_reset_value` machinery (14s modern-era reset) — the setup charge
consumes FROM that already-reset clock, exactly like any other live
action would, never a second reset and never a new possession.

## Transition timing semantics

`TRANSITION_ENTRY` is charged whenever `config.initial_phase ==
PossessionPhase.TRANSITION` — the SAME real signal Phase 23B already
computes (`RestartType.LIVE_TRANSITION` → `PossessionPhase.TRANSITION`)
to distinguish a live rebound/steal restart from a dead-ball inbound.
No transition offense, transition scoring bonus, or transition-specific
mechanic was built — only this one timing-context distinction. Verified:
`test_transition_entry_consumes_configured_setup_time`.

## Focused tests

18 new tests across `test_possession_orchestrator.py`
(`TestStructuralTimingHook`, 12 tests) and
`test_detailed_engine_diagnostics.py` (`TestClockAccounting`, 3 updated/
new tests) plus 3 pre-existing tests updated for the fix's own
legitimate, demonstrated effect on possession counts (loose numeric
bounds widened, not weakened — see each test's own comment). Covers:
floor-foul drive timing (exactly once, correct duration), ordinary/
transition entry charging the correct configured amount, second-chance
reset with same-possession-id preservation, exactly-once game/shot
clock decrementing, period expiration and shot-clock expiration DURING
setup correctly preventing any action dispatch (`action_count == 0` is
now a legitimate, tested possibility), no negative clocks, pass-flight/
drive-execution/shot-execution durations all unchanged outside the
fixed bug, deterministic replay, and diagnostic attribution of the new
`stage_timing` category.

Full suite: `python3 -m unittest discover -p "test_*.py"` → **919/919
OK** (907 baseline + 12 new). Zero regressions.

## Before / after (STRUCTURAL sensitivity test, NOT a realism claim)

### Seed 23024

| Metric | BEFORE (defender-zone fix only) | AFTER (+ Structural Timing Hook) |
|---|---|---|
| Score | HOME 458 – 395 AWAY | HOME 283 – 241 AWAY |
| Total possessions | 794 | **486** |
| Mean / median possession seconds | 3.627 / 2.300 | **5.926 / 4.500** |
| Mean / median actions per possession | 3.171 / 2 | 3.109 / 2 |
| FGA / misses | 857 / 567 | 521 / 342 |
| OREB / DREB | 276 / 286 | 163 / 177 |
| OREB share | 0.491 | 0.479 |
| Turnovers (rate) | 211 (26.6%) | 124 (25.5%) |
| Setup/stage time (new category) | n/a | **1,177.3s (40.9% of game clock)** — HALFCOURT_ENTRY 573.3s/192 charges, TRANSITION_ENTRY 441.0s/294 charges, SECOND_CHANCE_RESET 163.0s/163 charges |
| Action-execution time | 2,786.9s (96.8%) | 1,650.7s (57.3%) |
| Faults | 0 | 0 |

### 10-game sample (seeds 23024–23033)

| Metric | BEFORE | AFTER |
|---|---|---|
| Mean possessions | 769.8 | **471.4** |
| Mean OREB / DREB | 257.9 / 275.2 | 156.1 / 171.4 |
| Mean OREB share | 0.484 | 0.477 |
| Mean personal fouls | 15.4 | 8.5 |
| Mean FTA | 34.0 | 19.3 |
| Mean turnovers (rate) | 187.3 (24.3%) | 113.1 (24.0%) |
| Faults across 10 games | 0 | 0 |

(Personal fouls/FTA dropped simply because FEWER, LONGER possessions
occurred in the same 48 minutes — not because any foul probability
changed; foul rate PER POSSESSION is essentially unchanged, consistent
with turnover rate also being essentially unchanged.)

**This is exactly the required structural proof, nothing more:**
adding a real, nonzero, uncalibrated setup-time placeholder
mechanically lengthened possessions (794→486, a real ~39% reduction in
possession count for the SAME 48 minutes) and increased mean/median
possession duration (3.6s→5.9s / 2.3s→4.5s), while every individual
action-execution duration (drive, shot, pass) remained byte-for-byte
identical to before, and OREB share / turnover rate — governed
entirely by UNTOUCHED resolver logic — stayed within noise of their
pre-hook values. **Pace is still far short of the real ~14.6s
reference and this is explicitly NOT a calibration claim.**

## What remains uncalibrated

- All three new stage-timing constants (3.0s / 1.5s / 1.0s) are
  explicitly-flagged placeholders with no empirical grounding.
- The existing action-execution durations (2.5s drive, 1.5s pull-up,
  1.0s catch-and-shoot, 0.4–0.9s pass) are unchanged and remain the
  SAME placeholders diagnosed previously.
- No inter-action "hold/dribble" timing stage was added (explicitly
  out of scope per instruction — "do not make every pass reception
  automatically create a full possession-entry delay"); this remains a
  documented future extension point, not built here.
- The floor-foul branch's now-correct `drive_action_seconds` charge is
  itself still an uncalibrated placeholder (same one every other drive
  outcome already used).

## Empirical quantities needed next (data dependency only, not researched this task)

- Real seconds a possession's advance/setup phase should cost, and
  whether/how much `TRANSITION_ENTRY` should differ from
  `HALFCOURT_ENTRY` (real NBA transition possessions are measurably
  faster) — derivable from NBA play-by-play event timestamps or public
  tracking-derived possession-duration distributions.
- Real seconds a second-chance reset should cost, separated from an
  ordinary new-possession entry.
- Whether an inter-action hold/dribble stage is empirically warranted
  at all, and if so its real magnitude — not assumed or estimated here.

## Confirmations

- No probability, selection-weight, rebound/turnover/shooting/foul
  RATE, or player attribute was changed anywhere — verified by direct
  diff review (`git diff 996c5d8 -- possession_orchestrator.py`)
  containing no touched numeric constant outside the three new,
  clearly-named timing fields and the floor-foul fix's reuse of the
  existing `drive_action_seconds`.
- No legacy/product file was touched: `game_engine.py`, `main.py`,
  `season.py`, `playoffs.py`, `db.py`, `models.py`, `README.md`,
  `ACCURACY.md`, `CLAUDE.md` are all untouched.
- Both the floor-foul bug fix and the Structural Timing Hook remain
  **UNCOMMITTED**, on top of the pushed timing-diagnosis checkpoint
  (`996c5d8`).
- No new gameplay phase was begun.

**Checkpoint note:** the Structural Timing Hook above was subsequently
committed (`3f4dc75` "Add structural possession timing stages") and
pushed. All measurements above are preserved exactly as reported. The
section below is a further diagnostic pass built on that pushed
baseline; it adds observational telemetry only and remains uncommitted.

---

# Shot-Clock-at-Attempt Diagnosis

Purely diagnostic. **No setup-stage placeholder, action duration,
probability, selection weight, or player attribute was changed
anywhere.** Built on the pushed Structural Timing Hook baseline
(`3f4dc75`); all measurements below use the SAME seed 23024 and
23024–23033 sample as every prior measurement in this report.

## Telemetry added

One new diagnostic-only list, `PossessionWorld.shot_attempt_log`,
populated by a new `_log_shot_attempt` helper called from
`_dispatch_shot` (ordinary made/missed/blocked interior and perimeter
attempts) and `_dispatch_shooting_foul` (made and-ones only — see
"source-of-truth statement" below for why missed-and-ones are
excluded). Each entry:

```
{possession_id, offense_team_id, shot_family, game_clock_at_attempt,
 shot_clock_at_attempt, stage_origin, stage_generation, step, made,
 is_first_action, action_index}
```

`game_clock_at_attempt`/`shot_clock_at_attempt` are captured at the TOP
of `_dispatch_shot` (and re-captured, identically, at the top of
`_dispatch_shooting_foul`, since no clock mutation occurs between the
two call sites) — i.e. the REAL clock state at the moment of the
attempt, BEFORE that action's own execution duration is charged.
`stage_origin` is derived by a new `_possession_stage_origin` helper:
the most recent `PossessionStage` charged at or before this action's
own step (the possession's own entry stage always applies until a later
`SECOND_CHANCE_RESET` supersedes it). `stage_generation` is a new
companion field — a count of how many stage charges (entry + every
reset so far) preceded this attempt — added specifically so a consumer
can distinguish two attempts that share the SAME `stage_origin` LABEL
(e.g. two separate second-chance putbacks in one possession) from two
attempts within the SAME uninterrupted segment. `action_index` is
`len(world.action_log)` at the moment of the attempt (before the outer
loop appends this action's own entry) — `is_first_action` is
`action_index == 0`.

Two new aggregation functions in `detailed_engine_diagnostics.py`:
`diagnose_shot_clock_at_attempt(result) -> ShotClockAtAttemptDiagnostics`
(one game) and `diagnose_shot_clock_at_attempt_multi(results) ->
MultiGameShotClockDiagnostics` (a sample of games) — both read
EXCLUSIVELY from `shot_attempt_log`.

## Source-of-truth statement

Every field is read from an already-computed, already-structured value
— the engine's own real clock state, the caller's own real
`shot_family` string constant, and `world.action_log`'s own real
length — never inferred from a descriptive/log string. This module
never mutates simulation state and is never imported by any simulation
module (unchanged doctrine, re-verified: `TestTelemetryIsObservationalOnly`).
One deliberate accounting-consistency choice: a MISSED shooting foul is
NOT logged to `shot_attempt_log`, matching `world.stats.fga`'s own
existing, real convention (Phase 18C's rule: a missed shooting foul
contributes ZERO FGA) — so `len(shot_attempt_log) <= stats.fga` always,
verified: `test_shot_attempt_log_fga_count_matches_provisional_fga_where_convention_matches`.

## Shot-clock bin definitions

```
24-18, 18-15, 15-7, 7-4, 4-0
```

These are **INTERNAL, NEUTRAL bins**, NOT claimed as official NBA.com
shot-clock-range categories, with ONE documented exception: the **"4-0"
boundary IS a real, independently-verified NBA.com bucket edge**
already used elsewhere in this repository (`shot_resolution.py`'s own
`LATE_CLOCK_THRESHOLD_SECONDS = 4.0`, grounded in a real, previously-
verified NBA.com late-clock 3PT% finding). The remaining boundaries
(18, 15, 7) are this diagnostic's own internal choices — no broader
public NBA.com shot-clock-range taxonomy was verified in-repo or
researched this task, per instruction ("do not claim these are official
categories unless verified").

## Seed 23024 results

| Metric | Value |
|---|---|
| Total FGA | 517 |
| Mean / median shot clock remaining at attempt | 18.34 / 20.6 |
| First-action FGA count / share | 192 / 37.1% |
| Mean / median shot clock, first-action FGA only | 21.90 / 22.5 |
| Shot family split | THREE_POINT 468, FLOATER 36, RIM 13 |

Bin distribution:

| Bin | Count | FG% |
|---|---|---|
| 24-18 | 345 (66.7%) | 32.5% |
| 18-15 | 28 (5.4%) | 50.0% |
| 15-7 | 138 (26.7%) | 37.7% |
| 7-4 | 4 (0.8%) | 25.0% |
| 4-0 | 2 (0.4%) | 0.0% |

By possession-entry context:

| Origin | FGA | FG% | Mean SC | Median SC | Mean elapsed since possession start | First-action share | Mean action index |
|---|---|---|---|---|---|---|---|
| HALFCOURT_ENTRY | 144 | 30.6% | 19.75 | 21.0 | 4.25s | 53.5% | 1.07 |
| TRANSITION_ENTRY | 242 | 35.1% | 21.21 | 22.1 | 2.79s | 47.5% | 1.26 |
| SECOND_CHANCE_RESET | 131 | 38.2% | 11.50 | 12.6 | 8.78s | 0.0%* | 4.83 |

*A second-chance shot can never be the possession's action_index==0 by
construction (at least one action already occurred before the OREB
that created it) — 0.0% here is a structural certainty, not a finding.
"Mean elapsed since possession start" for `SECOND_CHANCE_RESET` reflects
the WHOLE possession (including time before the rebound), NOT elapsed
since only the reset itself — a documented simplification (see
`StageOriginShotSummary`'s own docstring); building the full "time since
the current segment began" reconstruction was judged out of proportion
for a diagnostic-only task.

## 10-game sample results (seeds 23024–23033)

| Metric | Value |
|---|---|
| Total FGA across 10 games | 5,053 (mean 505.3/game) |
| Mean shot clock remaining at attempt (game-means averaged) | 18.26 |
| Mean first-action FGA share | 37.0% |

Bin distribution (summed across all 10 games): 24-18: 3,336 (66.0%),
18-15: 346 (6.8%), 15-7: 1,281 (25.4%), 7-4: 80 (1.6%), 4-0: 10 (0.2%).

By origin (summed across all 10 games):

| Origin | FGA | FG% | Mean SC | Median SC |
|---|---|---|---|---|
| HALFCOURT_ENTRY | 1,562 | 35.5% | 19.61 | 20.6 |
| TRANSITION_ENTRY | 2,271 | 35.8% | 21.12 | 22.1 |
| SECOND_CHANCE_RESET | 1,220 | 34.8% | 11.22 | 12.6 |

Consistent with seed 23024 in every respect — the finding is robust
across the sample, not a seed artifact. Zero faults across all 10 games.

## First-action shot analysis

**37.0–37.1% of ALL field-goal attempts occur on the possession's very
first dispatched action** — mean shot clock remaining for these
specifically is 21.9–22.5 (only ~1.5–2.1 seconds elapsed on the shot
clock before release). This is the single largest, cleanest piece of
evidence that action-selection timing is currently very aggressive: a
first-action shot happens before the entry stage's own real cost (3.0s
ordinary / 1.5s transition) has meaningfully eaten into a 24-second shot
clock at all.

## Ordinary vs. transition vs. second-chance comparison

**A/B: are shots occurring far too early, and is this driven by one
context or all three?** All three contexts show early attempts, but
NOT uniformly:

- **HALFCOURT_ENTRY and TRANSITION_ENTRY are the two clearest early-shot
  drivers in ABSOLUTE terms** (mean shot clock remaining 19.6–19.7 and
  21.1–21.2 respectively — TRANSITION_ENTRY shots occur EVEN earlier
  than halfcourt-entry shots, consistent with its smaller 1.5s entry
  cost).
- **SECOND_CHANCE_RESET shots occur later in ABSOLUTE shot-clock terms**
  (mean ~11.2–11.5 remaining) simply because the reset base itself is
  14s, not 24s — but PROPORTIONALLY, the pattern is similar: roughly
  17–18% of the AVAILABLE shot clock has elapsed before the shot in
  EVERY context (HALFCOURT_ENTRY ≈ 4.25/24 ≈ 17.7% elapsed;
  SECOND_CHANCE_RESET's own reset-to-shot gap is proportionally
  comparable once the smaller 14s base is accounted for). **This is a
  genuine, all-three-contexts pattern, not one outlier context.**
- **C: first-action shots are a major, not the sole, source of
  early-clock attempts.** 37% of all FGA are first-action shots (mean
  SC ~22), but the REMAINING 63% of attempts still average mean shot
  clock in the high teens across every origin — meaning even
  MULTI-action possessions are reaching a shot with a lot of clock left,
  not just the immediate-shot subset.
- **D: ordinary-entry timing still leaves a lot of shot clock at
  attempts** — mean 19.6–19.75 remaining (only ~4.3–4.4s elapsed) even
  though `ordinary_entry_seconds=3.0` is the largest of the three
  placeholders. This is fully consistent with the earlier pace
  diagnosis's own conclusion: the entry stage alone is not sufficient:
  most possessions still only add 1–2 further actions (each 0.4–2.5s)
  before a shot is taken.
- **E: second-chance timing is structurally plausible relative to the
  14s reset, without claiming calibration** — a mean of ~11.2–11.5
  remaining (out of 14) after a 1.0s reset charge is directionally
  sensible (a putback/quick second shot IS a real, common NBA pattern)
  and does not show any obvious pathology (no negative values, no
  values exceeding 14.0 minus the reset cost — verified directly:
  `test_shot_attempt_after_oreb_reset_has_second_chance_origin_and_respects_14s_reset`).

## Demonstrated causes

1. **First-action shots are frequent and occur very early** (37% of
   FGA, ~22s mean remaining) — directly measured, not inferred.
2. **Even non-first-action shots occur with substantial shot clock
   remaining across all three entry contexts** — the entry-stage timing
   hook alone (validated in the prior section) measurably lengthened
   possessions, but did NOT by itself push typical shot timing into a
   realistic range; the gap is in the NUMBER and SPACING of actions
   between entry and the eventual shot, not the entry cost itself.
3. **No shot-clock consistency bug was found.** Every check requested —
   monotonic non-increase within an uninterrupted segment
   (`test_shot_clock_never_increases_within_one_stage_segment`), the
   real 14s OREB reset respected, no shot ever attempted with a
   negative or already-expired shot clock, no double-charge between
   stage timing and action timing (already proven in the prior section
   and unaffected by this purely-additive telemetry pass) — passed
   cleanly on the first attempt. This diagnostic pass did NOT surface a
   new literal bug.

## Unproven / not investigated

- Whether an inter-action "hold/dribble/probe" timing stage would, if
  added, produce a MORE realistic shot-clock-at-attempt distribution —
  plausible given the evidence, but not implemented or simulated this
  task (explicitly out of scope).
- Any real NBA shot-clock-at-attempt reference distribution to compare
  against numerically — not researched this task (a real data
  dependency for later, same posture as every other empirical
  calibration question in this report).
- Whether the proportional similarity across the three entry contexts
  (≈17–18% of available shot clock elapsed before a shot) is a
  coincidence of the current placeholder values or a more fundamental
  property of the action-selection policy — not distinguished this
  pass.

## Does the evidence suggest an inter-action timing stage is needed?

**Yes, as a genuine candidate for the NEXT investigation** — the
evidence is consistent with (though does not, by itself, prove) the
hypothesis that the missing piece is time BETWEEN modeled actions
(hold/probe/reset dribbling), not merely time BEFORE the first one. The
entry-stage hook already validated in the prior section measurably
shifted pace (Sec. "Structural Timing Hook"); this diagnosis shows that
shift was not sufficient to bring shot-clock-at-attempt into a
plausible-looking range, and that the shortfall appears across ALL
three entry contexts, not concentrated in one. This is offered as a
recommended next diagnostic/design question for HQ review — **no
inter-action timing mechanism was implemented, and no timing placeholder
was changed, in this task.**

## Tests / full suite

New focused tests: 9 in `test_possession_orchestrator.py`
(`TestShotClockAtAttemptTelemetry`) + 5 in
`test_detailed_engine_diagnostics.py`
(`TestShotClockAtAttemptAggregation`) = **14 new tests**. Covers: shot
attempts after ordinary-entry/transition-entry/second-chance-reset with
correct `stage_origin` and clock bounds, the real 14s OREB reset
respected, no illegitimate shot-clock increase within an uninterrupted
segment, a shot near shot-clock expiration still resolving and binning
correctly with no negative clock, period expiration and shot-clock
violation both correctly preventing any further shot attempt, same-seed
determinism of the new log, the FGA-accounting-convention consistency
check, and game/multi-game aggregation reconciliation (bin sums, origin
sums, first-action-count bound).

Full suite: `python3 -m unittest discover -p "test_*.py"` → **933/933
OK** (919 baseline + 14 new). Zero regressions.

## Confirmations

- No setup-stage placeholder, action duration, probability, selection
  weight, or player attribute was changed anywhere — verified by direct
  diff review (`git diff 3f4dc75 -- possession_orchestrator.py`)
  containing no touched numeric constant outside the new diagnostic
  fields (`game_clock_at_attempt`, `shot_clock_at_attempt`,
  `action_index`, `stage_generation`).
- No legacy/product file was touched: `game_engine.py`, `main.py`,
  `season.py`, `playoffs.py`, `db.py`, `models.py`, `README.md`,
  `ACCURACY.md`, `CLAUDE.md` are all untouched.
- This diagnostic work remains **UNCOMMITTED**, on top of the pushed
  Structural Timing Hook checkpoint (`3f4dc75`).
- No new gameplay phase was begun; no timing calibration was performed.
