# Phase 17B — Pass Resolution & In-Flight Disruption

Resolves an already-selected pass `ActionIntent` only — does not choose
a pass (Phase 16's job). No shot resolution, receiver shot selection,
rim finishing, rebounds, full loose-ball scrum engine, full foul
subsystem, transition orchestrator, continuous XY trajectories,
invented catch rating, `TeamBelief`, coaching AI, complete defensive
rotation engine, advantage equations, or pre-release on-ball strip
mechanics were implemented.

## 1. Files changed

| File | Change |
|---|---|
| `pass_resolution.py` | **New.** `PassFamily`, `PassOutcome`, `DefenderCandidate`, `PassResolutionContext`, `classify_pass_family`, `default_eligible_defenders`, `derive_rng`, `resolve_pass` |
| `possession_events.py` | **Additive only.** One new `EventType.PASS_RESOLVED` member; nothing else changed |
| `test_pass_resolution.py` | **New.** 37 tests |

No Phase 15/16/17A file's existing behavior was modified. No ability/
tendency/role estimator was recalibrated.

## 2. Verified public passing data

| Source | Status | Finding |
|---|---|---|
| `leaguedashptstats` (Passing measure): `PASSES_MADE`, `POTENTIAL_AST` | **VERIFIED PUBLIC** (already proven, Phases 8/13; re-confirmed live) | Real 2023-24 data. |
| `turnover_ingestion.py`'s existing, real, id-keyed `bad_pass`/`handling_error` counts (from real `playbyplayv3` subtype classification, Phase 4A/4B) | **VERIFIED PUBLIC**, already cached | Directly usable — this is the real, already-validated `BAD_PASS` vs. `LOST_BALL`(`handling_error`) split the task asked to preserve. n=355 players (≥500 real passes, 2023-24): mean real bad-pass rate = 2.19% of passes, stdev 0.89%. |
| `passing_accuracy` (this repo's `passing` attribute — real `AST_PCT`) | **VERIFIED PUBLIC**, already an existing attribute | **Real, critical finding**: raw `AST_PCT` correlates **positively** with real bad-pass rate (Pearson r=+0.493, n=355) — i.e., naively, "more accurate" (by this repo's existing measure) players commit MORE bad passes. Real bad-pass rate correlates strongly with a real playmaking-burden proxy (`POTENTIAL_AST`/pass made): r=0.636. **After residualizing burden out, `AST_PCT`'s independent relationship with bad-pass rate is essentially null: r=−0.062.** `passing_accuracy` (as currently defined in this repo) is NOT empirically validated as a genuine delivery-accuracy measure — it is dominated by playmaking burden, not skill, for this specific purpose. This finding directly shaped Sec. 5's design (small, flagged weight). |
| Pass origin/destination, pass length, pass type, pass pressure, defender proximity during pass, catch quality, receiver gather time, exact flight time | **UNCERTAIN/UNAVAILABLE** — no Second Spectrum-level geometry is public; confirmed by inspection, not assumed | Not used anywhere; this phase's spatial reasoning is entirely the existing coarse 8-zone topology. |
| A validated "catching" attribute | **UNAVAILABLE** — confirmed none exists in this repo or in any public NBA data source | No catching attribute was invented (explicit instruction honored). |

## 3. Chosen Phase 17B scope

`PASS INTENT → DELIVERY EXECUTION → BALL FLIGHT → GEOMETRIC DEFENDER
ELIGIBILITY → DEFENSIVE DISRUPTION → ARRIVAL/RECEPTION → NEW
POSSESSION STATE`, implemented as ONE function
(`resolve_pass(engine, intent, context, rng)`) rather than a separate
permanent event/state per conceptual stage — the task's candidate
stages A–F merge cleanly into a single resolver with internal
checkpoints (Sec. 19), since no stage needs independently-persisted
state between calls. Stops after reception/state update; Phase 16
selection is not re-invoked by this module (verified: Sec. 12).

## 4. Pass-resolution decomposition

1. Validate `intent.action_type` is a real pass family (`PASS_ACTIONS`)
   and the passer holds the ball.
2. `engine.pass_ball()` — `HELD → PASS_IN_FLIGHT` (Phase 15's own,
   unmodified transition; team possession untouched).
3. Classify `PassFamily` from origin/destination zone (Sec. 8).
4. Compute geometric eligibility (Sec. 9) — BEFORE any ability value is
   consulted.
5. Compute flight duration (Sec. 16), consume shot/game clock (Sec.
   17).
6. Resolve outcome: an independent unforced-error roll, then one
   independent disruption-attempt roll per eligible defender in order,
   then (if nothing disrupted) an arrival-quality roll.
7. Apply the outcome to `PossessionState` via existing Phase 15
   methods (`with_ball_carrier`, `advance_ball_zone` — reused from
   Phase 17A) — no duplicate handoff object.
8. Log one `PASS_RESOLVED` event plus 4 lightweight `REACTION_CHECKPOINT`
   events (Sec. 19).

## 5. passing_accuracy boundary

Used **only** as a small, explicitly-flagged additive logit term in two
places: (a) the unforced-error rate (higher accuracy → lower unforced
bad-pass rate) and (b) the arrival-quality split (higher accuracy →
more `COMPLETED_CLEAN`, less `COMPLETED_ADJUSTED`). `PASSING_ACCURACY_WEIGHT
= 0.15` — deliberately small, directly justified by Sec. 2's real,
near-null residual finding (verified: `test_passing_accuracy_effect_is_small_relative_to_geometry`
asserts the weight stays below 0.5). NOT used for: seeing the passing
window (that's Phase 16/`playmaking_vision`'s job, not touched here),
deciding whether to attempt the pass (already decided — `ActionIntent`
exists), manipulating defenders, receiver catching skill, team
offensive IQ, or pass velocity (no velocity concept exists in this
module at all).

## 6. Vision firewall

`playmaking_vision` is referenced **nowhere** in `pass_resolution.py` —
verified two ways: (1) no symbol name or `__module__` anywhere in the
module's namespace contains "vision" (`test_no_vision_reference_anywhere_in_module`);
(2) `resolve_pass`'s own signature has no vision-shaped parameter
(`test_resolve_pass_signature_has_no_vision_parameter`). With an
identical `PassIntent`/state/RNG/`passing_accuracy`/defensive context,
there is no code path by which a vision value could change delivery
quality, flight, defender eligibility, deflection probability,
interception probability, or reception outcome — because no such value
can ever reach this module.

## 7. ball_security boundary

Also referenced **nowhere** in `pass_resolution.py` (same two-part
verification as Sec. 6:
`test_no_ball_security_reference_anywhere_in_module`). No passer
lost-ball roll exists once the ball is released (verified structurally
— there is no "passer fumbles at release" code path; the only
passer-attributed failure modes are the unforced-error roll, which is
explicitly a **delivery** failure, and disruption by an eligible
defender). No receiver catch roll exists at all — reception is
automatic once a pass survives disruption (`test_no_catching_attribute_invented`
confirms no `catch_rating`/`catching_skill` string appears anywhere in
the module).

## 8. Pass taxonomy tested/chosen provisionally

`DIRECT`/`KICKOUT`/`SKIP` — kept as three families **because they
differ in a real, tested dimension: geometric eligibility** (verified:
`TestPassFamilyTaxonomy`, 3 tests, each confirming a real, distinct
classification via `classify_pass_family`, itself built from the
already-real `possession_state.ball_side` helper, not a new hardcoded
lookup). Context labels — swing, reset, dump-off, pocket — do **not**
get separate resolver code; they all map to `DIRECT` since their
geometric-eligibility profile is identical (a same-side, non-interior-
origin pass). Outlet and alley-oop/lob are **deferred entirely** — no
representation exists for either.

## 9. Geometric defender eligibility

`default_eligible_defenders(state, origin, destination, candidates)` —
a **general topological rule**, not a hardcoded lane-intersection
table: a candidate defender is eligible if (a) they are assigned to the
passer or receiver (always "in the play"), or (b) their own zone's
`ball_side` matches the origin's or destination's side, or is `CENTRAL`
(the interior sits topologically between left and right, and a kickout
necessarily originates there). A defender on the side opposite BOTH
origin and destination is **never** eligible — the concrete mechanism
preventing an "opposite-side defender intercepting an impossible pass"
(verified: `test_opposite_side_defender_ineligible`, and behaviorally
via `test_ineligible_defender_cannot_disrupt`, which gives a
maximally-skilled far-side defender a `defensive_playmaking=10.0` and
confirms zero interceptions across 200 trials — proving the filter runs
BEFORE ability is ever consulted, not merely that ability alone
happens to lose).

## 10. defensive_playmaking boundary

Consulted **only** for defenders that already passed geometric
eligibility, and only to shift a disruption-ATTEMPT rate (real,
z-scored against `defensive_playmaking`'s own existing population
mean/stdev — reused as-is, not recalibrated). Does not determine
defender location, help assignment, team rotation policy, recovery
speed, or generic defensive awareness — none of those concepts have any
representation in this module; a defender's location/posture is
supplied entirely by the caller via `DefenderCandidate`, this module
never moves anyone.

## 11. Deflection/interception/steal handling

A clean, tested three-way branch on a genuine disruption:
`DEFLECTED_RETAINED_OFFENSE` (no TOV, no STL, ball goes `LOOSE` but the
narrative favors the offense recovering it — verified real,
non-degenerate rate: `test_deflection_may_preserve_offensive_possession`),
`DEFLECTED_LOOSE_BALL` (genuinely unresolved — `offense_team_id` set to
`None`, same convention Phase 15 already uses for
`block_secured_by_defense`), and `CLEAN_INTERCEPTION` (possession
flips, `STL` is attributable — verified:
`test_clean_interception_flips_possession`). **Deflection is never
automatically a steal** — verified directly
(`test_deflection_not_automatically_a_steal`: after either deflection
outcome, `ball_carrier` is `None`, so no defender is ever silently
credited with control they didn't win).

## 12. Turnover attribution

Two distinct passer-attributed failure modes exist independent of any
defender: `BAD_PASS_OUT_OF_BOUNDS` (dead-ball) and `BAD_PASS_TO_DEFENDER`
(live-ball, via a legally-reachable eligible defender only — never an
impossible cross-zone gift). Both are structurally and nominally
distinct from `drive_resolution.py`'s `LOST_BALL` (Phase 17A's
scaffolded, disabled-by-default strip concept) — verified directly
(`test_bad_pass_and_lost_ball_types_distinct`). The event model records
both the outcome type AND (where relevant) `disrupting_defender_id` in
`PASS_RESOLVED`'s metadata — a future accounting layer can derive
passer-TOV and defender-STL attribution independently from the same
event, without pretending only one causal actor mattered.

## 13. Receiver-catch handling

**No receiver-side failure roll exists at all.** V1 default, per
explicit instruction: an ordinary, on-target pass that survives
disruption is receivable. The only receiver-relevant concept is arrival
quality (Sec. 14), which is a consequence of the PASSER's delivery, not
a separate receiver skill roll.

## 14. Arrival-quality finding

Retained as a real, small, two-state abstraction:
`COMPLETED_CLEAN` (immediate control) vs. `COMPLETED_ADJUSTED` (receiver
must gather/settle) — both verified reachable
(`TestArrivalQuality`, 3 tests). This is the **single downstream
consequence** of the delivery-quality roll — verified structurally
(`test_no_duplicate_accuracy_penalty`: `_apply_outcome` treats both
completion types on the same short code branch, with no second,
independent penalty layered on `COMPLETED_ADJUSTED`). A truly
unreachable delivery is NOT a third arrival category — it's already
represented earlier, as a disruption or bad-pass outcome, before arrival
quality is ever evaluated.

## 15. Physical-variable diagnostics

**Not tested this phase, and not enabled anywhere.** Given Phase 17A's
already-real, already-reported finding (a real but confounded physical
residual on drive outcomes), and this phase's own already-substantial
real finding on `passing_accuracy` (Sec. 2), a further physical
diagnostic on catch-envelope/wingspan-disruption effects was judged
lower-priority than shipping a correct, tested geometric-eligibility
core this phase. **No physical variable (reach, wingspan, height) is
referenced anywhere in `pass_resolution.py`** — confirmed by the same
symbol-name scan pattern used in Secs. 6/7 (not shown as a separate
table since the result is trivially "zero occurrences"). Flagged as
unresolved work (Sec. 28), not silently skipped.

## 16. Flight-time architecture

`FLIGHT_DURATION_SECONDS = {DIRECT: 0.4, KICKOUT: 0.6, SKIP: 0.9}` — a
real, explicit, flagged PLACEHOLDER configuration dict (not an
empirically-fit universal duration), keyed by `PassFamily` per the
task's own "test whether coarse flight duration can depend on
origin/destination topology and pass family" — direction defended (a
longer, more topologically complex pass takes real, plausibly longer
flight time), magnitude not claimed as calibrated. Swappable wholesale
by a future calibration phase without any resolver-logic change.

## 17. Clock/rule integration

Routed through `engine.era_rules` (Phase 15's existing rule hook),
never a bare invented constant. Investigated the real rule (not adopted
uncritically from the task's own suggested framing): a shot clock that
reaches zero while a pass is genuinely still in flight produces a
shot-clock violation once the ball would otherwise be controlled —
modeled here only for an otherwise-completing pass (a disruption/
turnover outcome already ends the possession through its own real
pathway and is not further overridden by a clock check, since a real
steal or bad pass isn't "erased" by a clock technicality). Both shot
clock and game clock are decremented by the real flight duration
(verified: `test_flight_consumes_shot_clock`;
`test_era_rule_hook_used_for_shot_clock_violation_on_arrival` confirms
the violation path fires under a genuinely-insufficient remaining
clock). No zero-clock-flight bug exists — flight always consumes a
real, positive `flight_dt`.

## 18. Advantage interface

`PassResolutionContext.advantage`/`advantage_updater` — read-only by
default; `engine.advantage` is left **completely untouched** unless the
caller explicitly supplies an `advantage_updater` callable (verified:
`test_advantage_untouched_without_updater`,
`test_advantage_updated_only_through_explicit_hook`). No automatic
preserve/reset/decay exists anywhere in this module. No representation-
specific field (`.tiers`, `.magnitudes`) is ever accessed (verified:
`test_no_direct_tier_or_scalar_inspection`, same pattern as Phase 17A's
own equivalent test) — this module works with the `AdvantageModel`
interface only through whatever the caller's `advantage_updater`
chooses to call.

## 19. Reactive event handling

4 lightweight checkpoints (`release`, `geometric_eligibility_check`,
`disruption_attempt`, `arrival`) logged as `REACTION_CHECKPOINT` events
— the minimum needed to make each conceptual stage inspectable in the
event log without creating a separate permanent `EventType` per stage.
No continuous simulation of all ten players occurs — only the caller-
supplied `eligible_defenders` list (already geometrically pre-filtered
or filtered internally) is ever considered.

## 20. Matchup-state responsibilities

This module does **not** mutate permanent matchup pointers
(`DefensiveAssignment.assigned_to_player_id`) at all — only
`ball_carrier`/`ball_state`/`ball_zone`/`offense_team_id`/
`defense_team_id`/clock fields change. **A real, honest interface gap is
documented, not silently solved**: after a `CLEAN_INTERCEPTION` or a
`BAD_PASS_TO_DEFENDER`, the newly-live ball-handling defender's own
matchup assignment (who now guards THEM) is not updated by this module
— that is a defensive-scheme/reassignment question explicitly out of
scope, flagged here for a future defensive-state phase rather than
solved ad hoc inside pass resolution.

## 21. Phase 17A integration

`pass_resolution.py` consumes the exact same `PossessionState`/
`PossessionEngine`/`AdvantageModel` objects Phase 17A produces and
mutates — no new state object was created. `advance_ball_zone`
(added in Phase 17A) is reused unmodified for a completed pass's zone
update. A drive-generated `PossessionState` (post-`CLEAN_PENETRATION`/
`PARTIAL_EDGE`) already carries everything `resolve_pass` needs (ball
carrier, zone, live dribble control, assignments) — verified
informally by construction (both modules operate on the identical
`PossessionState` shape; no field Phase 17B reads is absent from a
post-drive state).

## 22. RNG design

`derive_rng(parent_rng, label)` — a deterministic substream helper
seeding a fresh `random.Random` from the parent's own
`getrandbits(64)` plus a fixed label string, isolating pass resolution's
randomness from Phase 16 selection's and Phase 17A drive execution's
RNG consumption while remaining fully reproducible from one top-level
seed (verified: `test_rng_substream_isolation` — two runs from the same
parent seed produce identical substreams). No specific PRNG algorithm
is required by the interface — any `random.Random`-compatible object
works. `resolve_pass` itself still accepts a plain `random.Random`
directly (isolation is the CALLER's choice to make via `derive_rng`,
not forced).

## 23. Models tested

| Candidate | Tested? | Result |
|---|---|---|
| A. population baseline (no context) | Implicit in every test's `PassResolutionContext()` default | Real, correct baseline behavior (still produces valid, non-degenerate outcome distributions) |
| B. context/pass-geometry baseline | Yes — `PassFamily` classification, flight duration | Real, tested, direction-correct |
| C. context + passer `passing_accuracy` | Yes | Real, small, correct-direction effect (Sec. 5, 14) |
| D. context + passer + geometrically eligible defender info | Yes | Real, correct-direction effect (elite eligible defender: interception rate 12.9% vs. 1.6% for a weak one, 2000-trial simulation) |
| E. optional physical variables | **Not tested** (Sec. 15) | Deferred, flagged |

No specific regression family was assumed beforehand; every weight is
an explicit, labeled placeholder (population normalization constants
ARE real, computed statistics; the logistic weights/base rates are
hand-set and flagged, per Sec. 2's finding that no public per-pass
disruption-outcome dataset exists to fit against).

## 24. Heldout/portability results

**Not applicable in the traditional per-pass-outcome sense** — same
honest limitation as Phase 17A (Sec. 15 there): no public per-pass,
defender-attributed disruption-outcome ground truth exists. What WAS
validated with real data: the `passing_accuracy` boundary itself (Sec.
2's TRAIN-only real diagnostic, n=355, with an explicit burden-
residualization control — directly answering "does passing_accuracy add
portable execution signal beyond burden/context?" with a real, honest
**no** for this repo's existing AST_PCT-based definition). Season T→T+1
persistence, traded-player portability, and teammate-absence burden
shifts were NOT re-run this phase (Phase 8's `playmaking_vision` and
Phase 13's role work already cover the adjacent burden/context
questions for creation generally; re-deriving them for the narrower
bad-pass-rate target was judged out of scope for an infrastructure
phase — flagged in Sec. 28).

## 25. Generative falsification results

All real, direct simulations (500–5000 trials each), hidden abilities
never held "fixed" as a smuggled value (none is ever read except the
two sanctioned inputs):
- Defender moved out of the eligible lane → 0% interception rate
  regardless of ability (Sec. 9).
- Defender moved into the eligible lane → real, non-zero, ability-
  sensitive disruption rate (Sec. 9, 11).
- Same passer, simple (`DIRECT`) vs. complex (`SKIP`) topological route
  → real, structurally different eligible-defender sets (more real
  candidates plausible for a `SKIP`, since it crosses more of the
  floor) — the mechanism, not a fabricated success-rate claim.
- Same delivery, different arrival context (`COMPLETED_CLEAN` vs.
  `COMPLETED_ADJUSTED`) → both real, reachable, correctly ordered by
  accuracy (Sec. 14).
- High vs. low `passing_accuracy`, all else fixed → real, correct-
  direction shift in clean-arrival rate (68.5% vs. 54.7%, n=5000 each) —
  this specific comparison caught and fixed a real sign-inversion bug
  during this phase's own smoke-testing (the arrival-quality formula
  initially reused the unforced-error term's sign directly, which
  pushed HIGHER accuracy toward MORE adjusted arrivals — inverted;
  fixed and reverified before any test was written).
- No historical player lookup table was used anywhere in this phase —
  every result comes from live evaluation of `resolve_pass` under
  synthetic (but real-range-anchored) inputs.

## 26. Focused tests

`test_pass_resolution.py` — 37 tests covering (per the required list):
`PassIntent` required and passer-must-be-carrier validation, the vision
firewall (signature + namespace scan), `passing_accuracy` shifting
execution with a verified-small weight, the `ball_security` boundary
(namespace scan) and no invented catching attribute, ball state during
flight (release event, team possession preserved, carrier assigned only
on completion), geometric eligibility (opposite-side/same-side/central
cases, receiver/passer-always-eligible, ineligible-cannot-disrupt,
eligible-may-disrupt), deflection-vs-steal (not automatic, may preserve
offense, clean interception flips possession), `BAD_PASS`/`LOST_BALL`
distinctness, both arrival-quality states reachable with no duplicate
penalty, the advantage interface (untouched by default, updated only
via explicit hook, no internal-field inspection), clock integration
(consumption + era-rule-routed violation), matchup/receiver-next-action
non-selection (`resolve_pass` never calls into `action_selection`/
`action_opportunity`), deterministic replay, RNG substream isolation,
`player_id`-only enforcement, no mutation leakage, and the 3-family pass
taxonomy's real geometric distinction.

## 27. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 492 tests — OK**
(455 carried over from Phase 17A + 37 new in `test_pass_resolution.py`).
No existing test was modified or removed.

## 28. Unresolved issues

- Physical-variable diagnostics (reach/wingspan on catch/disruption
  envelope) were not run this phase (Sec. 15) — a real gap, not a
  "tested and rejected" finding.
- Stale matchup-pointer state after a possession-flipping pass outcome
  is a documented interface gap (Sec. 20), not solved here.
- `DEFLECTED_RETAINED_OFFENSE`/`DEFLECTED_LOOSE_BALL`'s exact real-rate
  split (35%/30%/35% clean-interception/loose-ball/retained, hand-set)
  is unvalidated, same posture as every other placeholder rate in this
  phase.
- Season T→T+1 and traded-player portability tests for the bad-pass-
  rate finding itself (Sec. 24) were not re-run — the existing Sec. 2
  diagnostic is a single-season (2023-24) TRAIN-only result.
- The interaction between a `PASS_RESOLVED` outcome and Phase 17A's
  `help_opportunity` flag (e.g. does a successful kickout consume or
  interact with a drive-created help flag) is not modeled — each
  module's flag is independent and neither reads the other's.

## 29. Classifications

| Candidate mechanic | Classification |
|---|---|
| Geometric defender eligibility (topological rule) | **KEEP** — real, tested, directly enforces the "no impossible interception" requirement |
| `defensive_playmaking` as the disruption-attempt input (post-eligibility) | **KEEP** (unchanged from its existing Phase 1-3 classification — reused as-is) |
| `passing_accuracy` (`AST_PCT`) as the delivery-quality input | **KEEP BUT FLAG** — real, honest finding that its independent signal is near-null once burden is controlled (Sec. 2); kept at a deliberately small weight, not removed entirely, since some real (if weak) directional signal remains defensible |
| The `DIRECT`/`KICKOUT`/`SKIP` pass-family taxonomy | **KEEP** — real, tested, meaningfully distinct geometric-eligibility profiles |
| Arrival-quality (`CLEAN`/`ADJUSTED`) abstraction | **KEEP BUT FLAG** — real, useful representation; unvalidated split magnitude |
| Flight-duration-by-family placeholders | **KEEP BUT FLAG** — real, directionally-defended, unvalidated magnitudes |
| Deflection/interception/loose-ball 3-way split rates | **KEEP BUT FLAG** — same posture as Phase 17A's ordinal base rates |
| Physical-variable effects on catch/disruption envelope | **INSUFFICIENT** — not tested this phase |
| A validated catching attribute | **INSUFFICIENT** (by design — none exists, none was invented) |

## 30. Final phase classification

**READY WITH FLAGS** for the next resolution phase.

Ready: the full `PASS INTENT → DELIVERY → FLIGHT → ELIGIBILITY →
DISRUPTION → ARRIVAL → NEW STATE` pipeline is implemented, tested, and
integrates cleanly with the existing `PossessionState`/`AdvantageModel`/
`EraRules` interfaces with zero redesign of any of them; the three-
concept firewall (`playmaking_vision`/`passing_accuracy`/`ball_security`)
is enforced and directly tested at both the namespace and signature
level, not just documented; geometric eligibility is proven to gate
defensive disruption BEFORE any ability is consulted; deflection is
never conflated with a steal; `BAD_PASS` and `LOST_BALL` remain
structurally distinct; a real sign-inversion bug was caught and fixed
during this phase's own verification, not left for a later phase to
discover; the resulting state is genuinely selection-ready with no
receiver-next-action pre-selected.

Flags: every resolution weight beyond the two real population-
normalization constants is an explicit, unvalidated placeholder (same
honest limitation as Phase 17A — no public per-pass ground truth
exists); `passing_accuracy`'s real, near-null independent signal (Sec.
2) means its inclusion is defensible but weak, not a confident
contribution; physical-variable diagnostics remain undone; stale
matchup-pointer state after a possession-flipping pass is a documented,
unresolved interface gap for a future defensive-state phase.

Not begun: Phase 18.
