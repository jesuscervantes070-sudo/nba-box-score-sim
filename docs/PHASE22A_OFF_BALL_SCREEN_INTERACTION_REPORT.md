# Phase 22A — Off-Ball Screen Interaction

Implements one generic half-court off-ball screen interaction primitive
that resolves into real, existing Phase 15 state (defender posture, an
atomic two-defender assignment exchange, and an optional zone-scoped
`AdvantageModel` compromise) and then hands control straight back to the
ordinary Phase 16 pipeline. It creates WORLD STATE only — it never
awards a pass, a shot, an assist, a score, or a possession change.

## 1. Architecture implemented

One new resolver module following this project's established
two-layer resolver shape (pure resolution function + a separate
engine-applying function, matching `on_ball_pressure_resolution.py`/
`foul_resolution.py`):

- `OffBallScreenContext` — every field a real, caller-supplied
  structural fact (four real participant `player_id`s, origin/
  destination `SpatialZone`, both defenders' existing `DefensivePosture`,
  and an optional `coverage_instruction` string). No hidden ability
  value anywhere.
- `resolve_off_ball_screen(context, rng) -> str` — PURE. A recognized
  `coverage_instruction` (`"SWITCH"`) deterministically decides the
  outcome with no roll; otherwise a small, posture-only, explicitly
  flagged placeholder weight table is sampled.
- `_atomic_switch_assignments(engine, defender_a_id, defender_b_id, ...)`
  — the smallest safe atomic two-defender assignment transaction (Sec.
  4).
- `_compromise_zone(advantage, zone)` — generic reuse of
  `AdvantageModel.compound()` across whichever concrete representation
  the caller already chose.
- `apply_off_ball_screen_to_engine(engine, context, rng) -> str` —
  validates the context against real engine state FIRST (fail-fast,
  zero mutation on any invalid input), resolves the outcome, then
  applies only the state change that outcome licenses.

## 2. Files changed/added

| File | Change |
|---|---|
| `off_ball_screen_resolution.py` | **New.** `OffBallScreenContext`, `OffBallScreenOutcome`, `resolve_off_ball_screen`, `_atomic_switch_assignments`, `_compromise_zone`, `apply_off_ball_screen_to_engine` |
| `test_off_ball_screen_resolution.py` | **New.** 33 tests |

**Zero changes to any existing file** — reuses Phase 15's
`PossessionEngine.update_posture`/`_log`, `DefensiveAssignment.switch`,
`PossessionState.assignments`, Phase 15's `AdvantageModel.compound()`
interface (both concrete implementations), and Phase 16's
`generate_opportunities`/`StructuralContext` completely unmodified.

## 3. Why a structural interaction, not a skill-rating system

`possession_state.py` (confirmed by direct inspection) has no
continuous coordinates, screen angle, body orientation, defender
pathing, or over/under-screen navigation concept — only eight coarse
`SpatialZone`s, a `ball_zone`, four coarse `DefensivePosture` values, and
defender→offender pointer `assignments`. A "screen quality" or
"screen navigation" RATING would have to be invented with no supporting
geometry for it to act on. What the representation already supports —
posture change, an atomic assignment exchange, and a zone-scoped
`AdvantageModel` compromise — is exactly the vocabulary this module
resolves into. No new player-owned rating, tendency, or physical
formula exists anywhere in this file (verified:
`test_no_new_ability_or_physical_fields`, a namespace + dataclass-field
scan of `OffBallScreenContext`).

## 4. Matchup assignment handling

`OffBallScreenContext` carries the four real participant ids
(`screener_id`, `screener_defender_id`, `moving_receiver_id`,
`receiver_defender_id`) plus caller-supplied posture — the caller
already decided WHO is involved (no participant-selection AI here).
`apply_off_ball_screen_to_engine` validates, BEFORE any mutation or even
the outcome roll, that `screener_defender_id` is really currently
assigned to `screener_id` and `receiver_defender_id` is really currently
assigned to `moving_receiver_id` in `engine.state.assignments`
(`_validate_context_matches_state`) — a malformed/inconsistent caller
claim raises `ValueError` immediately, with zero state touched
(verified: `test_mismatched_context_fails_explicitly_no_mutation`,
`test_missing_receiver_defender_assignment_fails_explicitly_no_mutation`).
`screener_id == moving_receiver_id` and
`screener_defender_id == receiver_defender_id` are both rejected
explicitly.

## 5. Atomic switch behavior

The existing `PossessionEngine.switch()` is a single-pointer mutation
with no cross-defender invariant — two independent sequential calls to
it would, for the duration between them, leave the state with the OLD
second defender still pointed at the just-reassigned offensive player: a
real, observable duplicate-assignment / uncovered-player window. This
phase does NOT reuse `switch()` for the screen's own switch outcome for
exactly that reason (proven, not just asserted, via
`test_atomic_switch_rejects_preexisting_duplicate_assignment_state` and
the module's own docstring reasoning). Instead,
`_atomic_switch_assignments`:

1. Checks BOTH defenders currently have a real assignment, and that
   those two assignments point to two DIFFERENT offensive players
   (refusing to compound a pre-existing corrupt one-to-many state) —
   entirely BEFORE any mutation.
2. Computes the ENTIRE new `assignments` dict in a local variable
   (`a.switch(...)`, `b.switch(...)`, reusing `DefensiveAssignment.switch`
   verbatim).
3. Applies it via exactly ONE `replace(engine.state, assignments=...)`
   call — no intermediate state is ever observable to a caller reading
   `engine.state` between the two conceptual pointer changes.
4. Logs exactly two `EventType.ASSIGNMENT_SWITCH` events (one per
   defender, each carrying the REAL post-swap `secondary_player_id`) —
   verified: `test_switch_logs_exactly_two_assignment_switch_events`.

Verified: no duplicate assignment and no uncovered offensive player
after a switch (`test_no_duplicate_assignment_and_no_uncovered_player`),
canonical `player_id`s preserved (`test_canonical_player_ids_preserved`),
and a directly-constructed already-corrupt input (`test_switch_with_self_raises`,
`test_atomic_switch_rejects_preexisting_duplicate_assignment_state`)
raises before any mutation, with `engine.state` provably unchanged.

## 6. Posture handling

Reuses `DefensivePosture` unmodified. `TRAILING_SEPARATION` sets the
receiver's defender to `TRAILING` via the EXISTING
`PossessionEngine.update_posture` (reused verbatim, including its own
real "no assignment exists" guard) — the screener's own defender/
assignment is left completely untouched by this outcome (verified:
`test_receiver_defender_posture_becomes_trailing`). `SWITCH` sets BOTH
exchanged defenders to `RECOVERING` (the same default posture
`PossessionEngine.switch()` itself already uses for a fresh assignment)
via `DefensiveAssignment.switch()`'s own existing default. `NO_EFFECT`
touches no posture at all.

## 7. `AdvantageModel` interaction

Only `TRAILING_SEPARATION` creates a compromise, and only when the
caller/engine already carries a real `AdvantageModel` instance —
`engine.advantage is None` (Phase 15's own valid "no representation
chosen yet" state) is left exactly as `None`; this module never picks a
representation on the caller's behalf (verified:
`test_no_advantage_fabricated_when_none_was_chosen`). When present,
`_compromise_zone` builds a same-type "delta" instance (a real, hand-set,
SMALLEST-real-tier `DiscreteTierAdvantage` tier or a small placeholder
`SpatialMagnitudeAdvantage` magnitude) and merges it via the EXISTING
`AdvantageModel.compound()` interface — proven to work against BOTH
concrete implementations without any type-specific branching outside
this one small adapter (`test_zone_compromise_registered_when_advantage_present`,
`test_works_with_discrete_tier_advantage_too`). `SWITCH` deliberately
creates NO advantage effect in V1 — a real mismatch judgment would
require comparing player-specific attributes this phase is explicitly
barred from inventing (screen-navigation/switch-IQ ratings); a bare
assignment exchange alone is not, by itself, asserted as a structural
compromise (verified: `test_switch_creates_no_advantage_effect`). No
exact separation distance, defender displacement, screen contact force,
or player-owned numeric screen advantage is ever stored anywhere.

## 8. Phase 16 re-entry

The resolver never calls `generate_opportunities`/`perceive`/`select`
itself and never forces an action — it only updates real Phase 15 state.
Verified directly with the EXISTING, completely unmodified
`action_opportunity.generate_opportunities`:

- After a `TRAILING_SEPARATION` outcome, once the receiver becomes the
  ball handler (an ordinary, separately-caused Phase 15 transition) and
  `just_caught_pass=True` is supplied with the now-updated defender
  posture, `generate_opportunities` naturally exposes `CLOSEOUT_ATTACK`
  — the EXISTING precondition (`defender.posture in (RECOVERING,
  TRAILING)`) fires on state THIS module updated, with zero changes to
  `action_opportunity.py` (`test_trailing_separation_enables_closeout_attack_after_a_catch`).
- After a zone compromise is registered, passing the SAME
  `engine.advantage` into `generate_opportunities` naturally exposes a
  `KICKOUT` opportunity via its EXISTING `advantage.compromised_areas()`
  check — again zero changes to `action_opportunity.py`
  (`test_zone_compromise_enables_kickout_on_next_generate_opportunities_call`).
- `NO_EFFECT` never fabricates or forces any opportunity
  (`test_no_effect_never_fabricates_a_forced_action`).

## 9. `screen_active` / `roller_id` decision

**Left completely untouched — a deliberate, documented non-reuse, not
an oversight.** `StructuralContext.screen_active`/`roller_id` (Phase
16) and the `POCKET_PASS` opportunity are specifically ON-BALL PnR
scaffolding: `screen_active`'s own existing docstring says "a live
ON-BALL screen/DHO is currently engaged," and `POCKET_PASS`'s own
precondition (`TestStructuralDefensiveOpportunities.test_pocket_pass_requires_live_roller`)
requires a live roller receiving a pass FROM the ball handler. This
module resolves an interaction for a player who does NOT have the
ball (a receiver using an off-ball screen) — a semantically different
event. Forcing reuse here would conflate two real, distinct concepts
(an on-ball ball-handler/roller pick-and-roll vs. an off-ball movement
screen) purely because both involve the word "screen." Verified by a
namespace scan that this module contains no reference to
`screen_active`/`roller_id`/`POCKET_PASS` at all
(`test_module_does_not_reference_the_on_ball_pnr_scaffold`), and by a
direct re-run of the EXISTING `test_pocket_pass_requires_live_roller`
assertions proving that scaffold's own behavior is completely
unaffected by this module's existence
(`test_pocket_pass_precondition_is_unaffected_by_this_module_existing`).

## 10. Attribute firewalls

`poa_containment`, `perimeter_space_creation`, `defensive_playmaking`,
and `foul_discipline` are not referenced anywhere in this module —
verified via a namespace-name scan of the module's own defined symbols
(`test_attribute_firewalls`, the same methodology Phase 21A's own
`test_no_ability_symbols` already established — checking `vars(module)`
rather than raw source text, since the module's own docstring
legitimately NAMES these four attributes in PROSE to explain why they
are firewalled, and a bare full-text scan would false-positive on that
documentation). None is repurposed as screen-navigation, off-ball
separation, switch IQ, or illegal-screen discipline.

## 11. Role boundaries

No role signal (`role_off_initiation`/`role_off_finishing`/
`role_off_spacing`) is imported or referenced anywhere in this module
(verified: `test_no_role_or_tendency_import`) — this phase does not use
role inputs to fabricate screen-participation probabilities, per
explicit instruction. Roles remain exactly where Phase 16 already
placed them (deployment/frequency context for action SELECTION, not
screen resolution).

## 12. Physical-context policy

**No physical field is used at all in V1** — height/standing_reach/
wingspan/mass are absent from `OffBallScreenContext` entirely (verified:
`test_no_new_ability_or_physical_fields`, a dataclass-field scan). Per
the explicit instruction ("if physicals are unnecessary for a coherent
V1, prefer not using them at all"), and given no live heldout study
exists to justify any physical-driven screen-effectiveness formula, none
was added. No `mass -> strength`, `height -> screen quality`, or
`wingspan -> navigation` formula exists anywhere.

## 13. Foul boundary

**No foul detection or classification exists in this module.** The
posture-only placeholder weight table resolves only structural
positioning outcomes (attached / separated / switched) — it never
produces a foul call, and no reference to `OFFENSIVE_CHARGE`,
`DEFENSIVE_FLOOR_FOUL`, `floor_foul_administration`, or
`on_ball_pressure_resolution`'s foul vocabulary exists anywhere here
(verified by direct inspection of this module's imports — only
`possession_advantage`, `possession_engine`, `possession_events`, and
`possession_state` are imported). An illegal-screen/off-ball-contact
classification is explicitly NOT built this phase, and this module does
NOT reuse `OFFENSIVE_CHARGE` as a stand-in for it (per explicit
instruction). A future off-ball contact/classification phase can emit an
already-classified offensive floor foul into Phase 21B's existing
`administer_floor_foul` entry point exactly the way Phase 21A already
does — Phase 21B itself required zero changes to support this (it was
already built as a generic administration layer); nothing about this
phase broadens Phase 21B's own foul-class vocabulary.

## 14. RNG / calibration posture

`resolve_off_ball_screen` samples from a real, hand-set, EXPLICITLY
FLAGGED placeholder weight table (`_BASE_WEIGHTS` =
NO_EFFECT 0.45 / TRAILING_SEPARATION 0.40 / SWITCH 0.15), adjusted ONLY
by `DefensivePosture` (already-existing structural state, not a new
input) — no role, tendency, or physical value ever enters this
calculation, and no probability here is claimed as empirically
calibrated (module docstring is explicit about this). RNG is a
caller-supplied `random.Random` only (verified: `test_no_global_rng`);
`resolve_off_ball_screen` itself performs no engine mutation at all
(verified: `test_resolve_is_pure_no_engine_mutation`); a recognized
`coverage_instruction` bypasses the roll entirely and is fully
deterministic (`test_switch_via_explicit_coverage_instruction_is_deterministic`).
Deterministic replay under a fixed seed is verified
(`test_deterministic_replay`). No directional test in this suite is
claimed as calibration proof — only architecture/ownership/state
correctness is asserted.

## 15. Explicit exclusions

Per instruction, none of the following exist anywhere in this phase:
continuous coordinates, pathfinding, locomotion/acceleration/burst/
lateral-speed ratings, screen angles, screen collision physics, screen
contact force, a screen "strength" concept, named play-call sets
(HORNS/Spain/Floppy/Stagger/Hammer), a hedge/drop/blitz/trap tactical
taxonomy, a full pick-and-roll or DHO system, team playcalling,
participant-selection AI, any new screen-setting/screen-navigation/
off-ball-movement rating, any new screen-use or illegal-screen tendency,
a generic defensive-IQ rating, illegal-screen/off-ball-contact
detection or classification, and Phase 22B mechanics. `over-screen`/
`under-screen`/`top-lock`/`lock-and-trail`/`peel-switch`/`hedge depth`/
`drop depth`/`blitz angle` were not introduced — the 3-outcome
vocabulary (Sec. 16) deliberately collapses the task's own 5-item
illustrative list rather than adding finer labels.

## 16. Outcome-vocabulary collapse decision

The task's own illustrative list named 5 possible outcomes
(`DEFENDER_ATTACHED`, `DEFENDER_TRAILING`, `SWITCH`,
`ZONE_COMPROMISED`/`SEPARATION_CREATED`, `NO_EFFECT`/`RECOVERED`). This
phase implements exactly THREE (`NO_EFFECT`, `TRAILING_SEPARATION`,
`SWITCH`): `DEFENDER_ATTACHED` and `NO_EFFECT`/`RECOVERED` are
mechanically IDENTICAL under this state representation (no assignment/
posture/advantage change either way), so they are one outcome, not two;
`ZONE_COMPROMISED`/`SEPARATION_CREATED` is folded into
`TRAILING_SEPARATION`'s own consequence (a real separation IS the zone
compromise, not an independent second roll) rather than a fourth
branch. Per "use the smallest result vocabulary that maps cleanly to
existing state... do not create a huge taxonomy."

## 17. Targeted tests

`test_off_ball_screen_resolution.py` — **33 tests**, covering: attached/
no-effect (assignments/posture/advantage all unchanged), trailing
separation (correct posture change, no assignment corruption, optional
zone compromise on both `AdvantageModel` implementations, no fabricated
representation when `engine.advantage is None`), clean switch (atomic
exchange, no duplicate/uncovered player, canonical ids preserved, no
advantage side-effect, deterministic via an explicit coverage
instruction), invalid switch state (missing assignment, mismatched
context, same-defender/same-player inputs, a directly-constructed
pre-existing corrupt state, self-switch — all raising `ValueError` with
zero mutation), Phase 16 re-entry (a real downstream `CLOSEOUT_ATTACK`
and a real downstream `KICKOUT` both emerge from the EXISTING,
unmodified `generate_opportunities`, with no action forced), attribute
firewalls (namespace scan for all four barred attributes, zero role/
tendency reference, zero new ability/physical field), no-direct-scoring
(no `engine.<scoring-method>(` call site anywhere, and every outcome
leaves `ball_state`/`ball_carrier`/`offense_team_id`/`defense_team_id`
untouched), event logging (exactly two `ASSIGNMENT_SWITCH` events on a
switch, zero on the other two outcomes, no fake assist/shot/pass event
type ever logged), the `screen_active`/`roller_id` non-reuse decision
(namespace scan + a direct re-run of the existing PnR scaffold's own
test assertions), determinism (fixed-seed replay, no global RNG,
`resolve_off_ball_screen` itself mutates nothing).

## 18. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 780 tests — OK**
(747 carried over from Phase 21B + 33 new in
`test_off_ball_screen_resolution.py`). No existing test was modified or
removed. The two `TEST-RP-BAD`/`TEST-SZ-BAD` lines are pre-existing,
expected simulated-failure log lines from unrelated data-source tests
(present identically in the Phase 21B baseline run).

## 19. Unresolved issues

- The placeholder outcome-weight table (`_BASE_WEIGHTS`) is real,
  hand-set, and explicitly flagged as uncalibrated — no live per-
  off-ball-screen-outcome public dataset was identified or pulled this
  phase (consistent with every prior resolution phase's honest
  placeholder posture; directional-only, not calibration proof).
- `SWITCH`'s lack of an `AdvantageModel` effect is a deliberate V1
  simplification (Sec. 7) — a real mismatch-driven compromise is
  plausible in principle but would require a player-attribute
  comparison this phase is explicitly barred from inventing; flagged,
  not resolved.
- Illegal-screen/off-ball-contact detection remains fully unbuilt
  (Sec. 13) — a real, concrete future task if off-ball-foul modeling is
  ever prioritized, with Phase 21B already structurally ready to
  administer whatever gets detected.
- No live isolation study was performed on whether `DefensivePosture`
  alone is a sufficient/sufficiently informative structural driver for
  the outcome split — the posture-only design is a default-simplicity
  choice per instruction, not a data-driven finding.

## 20. Recommended next subphase

Phase 22B (or a differently-numbered future phase) could introduce the
off-ball illegal-screen/contact classification hook flagged in Sec. 13,
emitting into Phase 21B's existing generic administration layer without
touching it — but that is explicitly NOT begun here, per instruction.

## 21. Final phase classification

**READY WITH FLAGS.**

Ready: the structural-interaction vs. skill-rating architectural
distinction is real and enforced (no new rating/tendency/physical field
exists anywhere, verified via namespace + dataclass-field scans); the
atomic two-defender switch is proven safe (single `replace()`
transition, precondition checks before any mutation, a directly
constructed corrupt-state input correctly refused); posture and
`AdvantageModel` consequences are both real, minimal, and reuse existing
Phase 15 machinery with zero duplicated state; Phase 16 re-entry is
demonstrated with the EXISTING, unmodified `generate_opportunities`
producing two different kinds of real downstream opportunities from
this module's state changes alone; the `screen_active`/`roller_id`
on-ball PnR scaffold is deliberately, verifiably left untouched with an
explicit rationale; all four required attribute firewalls and the
role/physical exclusions are enforced and tested; no direct scoring
effect exists in any outcome path; the full suite is green at 780/780
with zero regressions.

Flags: the outcome-weight table is a real, uncalibrated placeholder
(same posture as every prior resolution phase); `SWITCH`'s
no-advantage-effect choice is a documented V1 simplification; off-ball
illegal-screen/contact detection remains a deferred, unbuilt future
task.

Not begun: Phase 22B.
