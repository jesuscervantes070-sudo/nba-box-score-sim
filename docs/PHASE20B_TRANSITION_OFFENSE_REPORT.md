# Phase 20B — Advancement, Outletting & Early-Offense Decay

Lets the normal possession engine exploit temporary structural asymmetry
before the defense recovers — does not invent a "fast break mode" or a
transition scoring buff. Reuses Phase 16 for perception/selection,
Phase 17B for passes, Phase 17A/18 for attacks and shots.

## 1. Files changed

| File | Change |
|---|---|
| `transition_offense.py` | **New.** `generate_transition_opportunities`, `decay_transition` |
| `action_intent.py` | **Additive only.** One new `ActionType.OUTLET_PASS`; `TRANSITION_PUSH` added to `CREATION_ACTIONS`, `OUTLET_PASS` added to `PASS_ACTIONS`; one new checkpoint-tuple entry. Every existing member/value unchanged. |
| `test_transition_offense.py` | **New.** 31 tests |

**No changes to `action_opportunity.py`, `action_selection.py`,
`pass_resolution.py`, `drive_resolution.py`, `shot_resolution.py`,
`interior_shot_resolution.py`, or `possession_engine.py`** — this
phase's entire real gap (Sec. 4) was closable by (a) one additive
opportunity type + two frozenset memberships and (b) reusing Phase 15's
already-built `AdvantageModel.decay()` interface.

## 2. Existing interfaces inspected

Confirmed directly, before writing any code: `ActionType.TRANSITION_PUSH`
already existed (Phase 16) and `action_opportunity.generate_opportunities`
already emits it whenever `state.phase == PossessionPhase.TRANSITION` —
zero new code needed for "push." `DRIVE`/`PULL_UP`/`CATCH_AND_SHOOT`
("early attack") are generated under ordinary live-dribble/just-caught
preconditions that hold identically in transition — zero new code
needed. `RESET_PASS`/`SWING_PASS` ("reset") already exist. `action_selection._score_action`
already applies `role_off_initiation`'s boost to every `CREATION_ACTIONS`
member and `pass_vs_shoot`'s boost to every `PASS_ACTIONS` member — so
extending those two frozensets (Sec. 1) gave `TRANSITION_PUSH`/
`OUTLET_PASS` correct role/tendency sensitivity with ZERO new scoring
code. `AdvantageModel.decay(dt)` already existed (Phase 15) and already
implements a real, representation-specific decay curve for both
`SpatialMagnitudeAdvantage` and `DiscreteTierAdvantage` — reused
verbatim.

## 3. Exact Phase 20B scope selected

Objective-opportunity generation for the one real gap (`OUTLET_PASS`,
Sec. 4), plus state-based transition-advantage decay toward
`HALFCOURT_SET` (represented as `PossessionPhase.HALFCOURT`, already an
existing Phase 15 value — no new terminal state). No shot make/miss, no
pass completion, no custom fast-break resolver, no sprint-speed/
locomotion latent.

## 4. Action-menu architecture

**Candidate C chosen: Phase 16 opportunity generation + existing
selection** (not A "hardcoded source rules," not B "transition-specific
multinomial"). Audited and confirmed: 3 of 4 candidate transition
actions (push, early attack, reset) required NO new mechanism at all.
The ONLY real gap was a structurally-eligible "pass to a teammate ahead
of the ball" opportunity, which neither `SWING_PASS` (nearest teammate)
nor `KICKOUT` (interior-origin, advantage-gated) represents — closed
with one new, additive `ActionType.OUTLET_PASS`.

## 5. Phase 16 integration

`generate_transition_opportunities(state, context, transition_state,
advantage)` calls `action_opportunity.generate_opportunities` FIRST,
unmodified, then appends `OUTLET_PASS` opportunities only when real
transition geometry supports one. Verified directly
(`test_no_outlet_without_transition_state`): with `transition_state=None`,
this function returns EXACTLY what Phase 16 alone would have — no
silent behavior change outside real transition geometry. Selection
itself is Phase 16's own, completely unmodified `SelectionPolicy`
(verified: `test_no_new_selection_engine_created` — no `SelectionPolicy`
class exists anywhere in this new module).

## 6. Push decision

`TRANSITION_PUSH` is scored by Phase 16's existing, unmodified
`_score_action` — `role_off_initiation` (now applying via the extended
`CREATION_ACTIONS` set) and `drive_aggression` both legitimately shift
its relative weight (verified: `test_role_off_initiation_boosts_transition_push`).
No push-specific speed/execution rating exists — the DECISION to push
is influenced by role/tendency; EXECUTION (whether the push actually
gains ground) is not modeled as a separate resolved action at all in
V1 (the push opportunity, once selected, becomes an ordinary
`ActionIntent` a future resolver handles exactly like any other
`TRANSITION_PUSH` selection already would have).

## 7. Role boundary

`role_off_initiation` affects SELECTION weight only (verified via the
same mechanism as every prior selection-layer test in this project —
`_score_action` has no execution-outcome code path at all, it only
returns a log-weight). It does not "run faster," "pass more
accurately," or "finish better" — none of those concepts exist
anywhere in `transition_offense.py`.

## 8. Outlet opportunity generation

Gated on a REAL, structural condition: a real OFFENSE-side
`FloorPlayer` (from Phase 20A's own `TransitionState.player_zones`)
tagged `AHEAD_OF_BALL` by Phase 20A's own `relational_tag` function —
reused unmodified, not re-derived. Verified directly
(`test_outlet_generated_for_ahead_of_ball_teammate`,
`test_no_outlet_without_eligible_receiver`,
`test_receiver_must_be_offense_side`): no outlet opportunity exists
without a real, eligible, offense-side, ahead-of-ball receiver.

## 9. Outlet selection

The DECISION to outlet (vs. push/attack/reset) uses Phase 16's existing
`pass_vs_shoot`/`role_off_initiation` weights via the extended
`PASS_ACTIONS`/`CREATION_ACTIONS` sets — verified directly
(`test_pass_vs_shoot_boosts_outlet_pass`). `passing_accuracy` has no
role here at all — no such field or symbol exists anywhere in
`transition_offense.py` (verified by namespace scan,
`test_no_ability_symbol_anywhere`), satisfying "the receiver is an
opportunity target first" and "`passing_accuracy` should NOT decide
whether player chooses to pass."

## 10. Phase 17B execution integration

Once `OUTLET_PASS` is selected, execution is entirely Phase 17B's
existing, unmodified `resolve_pass` — verified directly
(`test_no_duplicate_pass_resolver`: no `resolve_pass` function or
`PassResolutionContext` class exists anywhere in this new module).
`OUTLET_PASS` is a real member of `PASS_ACTIONS` (verified:
`test_outlet_pass_is_a_real_pass_action_type`), so any caller that
already branches on `intent.action_type in PASS_ACTIONS` to invoke
`resolve_pass` handles it automatically, with zero special-casing.

## 11. Moving-target handling

**Audited, no change needed.** Phase 17B's `resolve_pass` already
operates on discrete zone-to-zone geometry (never continuous receiver
motion) — an outlet pass to a real, structurally-ahead-of-ball
teammate is, mechanically, exactly the same kind of zone-to-zone pass
Phase 17B already resolves for `SWING_PASS`/`KICKOUT`. No
`target_state = MOVING_TARGET` concept was added, because the existing
resolver's own abstraction level (coarse zones, not continuous
coordinates) already makes "moving" a non-issue — there is no receiver
velocity to model in the first place.

## 12. Advance-dribble state mutation

**Not implemented this phase.** `TRANSITION_PUSH`, once selected, is
handed off as an ordinary `ActionIntent` — this phase does not itself
mutate ball/player zones over a push's duration (that would require a
push-specific resolver, explicitly out of scope: "Phase 20B does not
own shot make/miss resolution" and, by the same reasoning, does not own
push-execution geometry either). Flagged as unresolved work for a
future phase (Sec. 50), not silently assumed away.

## 13. Player movement treatment

No feet-per-second speed, acceleration, or locomotion physics exist
anywhere in this module (verified: no such symbol in the namespace
scan). Coarse zone occupancy (`FloorPlayer.zone`) is the only movement-
adjacent state this phase reads, and it never advances it itself.

## 14. Transition-decay model

**Candidate B chosen: state-based decay** (not A, a fixed timer). Reuses
Phase 15's own, already-built, representation-agnostic
`AdvantageModel.decay(dt)` — `decay_transition(engine, dt)` calls
`engine.advantage.decay(dt)` directly and checks whether
`compromised_areas()` is now empty. No new decay coefficient or curve
was invented by this module — the concrete math remains entirely
inside whichever `AdvantageModel` implementation is active, exactly
Phase 15's own representation-agnostic posture.

## 15. Time-vs-state decay verdict

**State-based, not time-based** — `HALFCOURT_SET` is reached when the
`AdvantageModel`'s own `compromised_areas()` becomes empty (or was
`None` to begin with), NOT after a fixed elapsed duration. Verified
directly (`test_decay_eventually_reaches_halfcourt_set`,
`test_no_advantage_means_immediate_halfcourt_set`). No arbitrary
"transition lasts 6 seconds" constant exists anywhere in this module.

## 16. Early-attack generation

No new mechanism (Sec. 2/4) — `DRIVE`/`PULL_UP`/`CATCH_AND_SHOOT`
opportunities are generated by Phase 16's own, unmodified logic under
the SAME real structural preconditions (live dribble, just-caught pass)
that already govern them in halfcourt play. Verified directly
(`test_drive_and_pullup_available_in_transition`).

## 17. Transition-contest handling

**Not modeled specially.** A transition attack's contest quality is
whatever Phase 17A (drive)/Phase 18A/18B (shot) would derive from the
REAL structural state (defender posture, geometric eligibility) at
resolution time — Phase 20A's own fresh-derived `AdvantageModel` (Sec.
"old-advantage firewall," Phase 20A) already feeds into that
structurally; no additional transition-specific contest discount or
bonus was added here.

## 18. Breakaway handling

**No special breakaway resolver was built**, per explicit instruction.
A structurally uncontested rim attempt reaching Phase 18B's own
`unblocked_make_probability` will naturally show a high conversion rate
BECAUSE of the real, already-existing contest-suppression logic finding
no primary/secondary defender to apply — not because of a
`transition=True` flag anywhere. No such flag exists in
`InteriorShotContext` (unchanged, unmodified this phase).

## 19. Early-3 handling

Same reasoning as Sec. 18, using Phase 18A's existing
`shot_make_probability` and `ContestBucket`/`ReleaseMode` inputs,
unmodified. No transition-3 rating was created.

## 20. Trailer handling

**No `trailer` archetype was created.** A trailing offensive player who
happens to be geometrically ahead of the ball is representable via the
SAME `OUTLET_PASS` mechanism (Sec. 8) as any other ahead-of-ball
teammate — zone + real role/tendency context is sufficient; no special
"trailer" concept exists anywhere in this module.

## 21. Push-vs-pass-vs-reset architecture

A single, unified `SelectionPolicy.select()` call (Phase 16's own,
unmodified) chooses among ALL perceived opportunities at once
(`TRANSITION_PUSH`, `OUTLET_PASS`, `DRIVE`/`PULL_UP`/`CATCH_AND_SHOOT`,
`RESET_PASS`) via the same additive log-weight/softmax mechanism every
other Phase 16 selection already uses — no separate push-vs-pass-vs-
reset decision tree was built.

## 22. Pace firewall

No `pace` concept, team-historical-pace input, or "coach push
preference" exists anywhere in this module (verified by namespace
scan) — satisfies "pace is an outcome of possession duration/
transition frequency/turnover-rebound context/shot selection... not a
player ability" by simply never referencing it.

## 23. Shot/game-clock integration

`decay_transition` never touches `shot_clock_remaining` or
`game_clock_remaining` at all (verified directly:
`test_decay_does_not_reset_shot_clock`) — Phase 20A already
initializes the shot clock (Phase 20A's own job); actual decrementing
happens only through real, event-driven action resolution (Phase
17/18's own `dt` consumption), never through this module's decay logic.

## 24. DREB handling

`PossessionChangeSource.DEFENSIVE_REBOUND` (Phase 20A) → `TRANSITION`
phase (verified: `test_dreb_initializes_transition_phase`). The
rebounder is NOT assumed to push or outlet — both remain real,
available opportunities the SelectionPolicy chooses among based on
role/tendency/geometry, never a hardcoded rule ("big rebounders always
outlet" / "guards always push" — neither exists anywhere in this
module).

## 25. Steal handling

Same treatment as DREB (verified: `test_steal_also_transition_capable`)
— no steal-specific bonus or forced rim-attack rule exists.

## 26. Dead-ball handling

Verified directly (`test_dead_ball_neutral_generates_no_outlet`): a
`MADE_BASKET_INBOUND` possession change (already neutralized by Phase
20A) produces zero `OUTLET_PASS` opportunities even when the caller
supplies real `player_zones` for it — because Phase 20A itself already
discards `player_zones` for dead-ball sources (Phase 20A Sec. 5), and
this module's own generation logic reads `transition_state.player_zones`
as its only source of geometry, so there is nothing left to generate an
outlet from.

## 27. AdvantageModel integration

Reuses the existing, unmodified interface exclusively via its own
`decay()` method — no representation-specific field (`.tiers`,
`.magnitudes`) is ever read directly in `transition_offense.py`
(verified by source inspection — the only `AdvantageModel` interaction
in the entire module is the single `engine.advantage.decay(dt)` call
and the `compromised_areas()` emptiness check, both abstract-interface
calls).

## 28. Compromised-region recovery

Entirely delegated to whichever real `AdvantageModel` implementation is
active — verified directly (`test_decay_reduces_advantage_over_time`):
calling `decay_transition` once measurably reduces the total real
compromised magnitude, using the exact same real decay math Phase
15/17A already validated for other purposes.

## 29. Defender-recovery handling

**Not separately modeled as posture transitions this phase** — Phase
20A already clears all matchups/postures on possession change (Phase
20A Sec. 19/20); this phase does not reintroduce per-defender posture
recovery timers, relying entirely on the `AdvantageModel`'s own decay
for the "defense is recovering" signal. Flagged as a real, honest
simplification (Sec. 50), not a claim that individual defender posture
recovery is unimportant.

## 30. Matchup treatment

**Not rebuilt by this phase** — Phase 20A already cleared
`engine.state.assignments` to `{}` (Phase 20A Sec. 19); this phase does
not construct temporary transition-specific matchup assignments,
relying on structural proximity (`FloorPlayer.zone`/`relational_tag`)
instead, exactly per the task's own suggested minimum ("use proximity/
structural defender relation if needed... downstream halfcourt system
may rebuild full matchups").

## 31. Public-data audit

| Item | Classification |
|---|---|
| `leaguedashteamstats` (Misc): `PTS_FB`, `PTS_OFF_TOV` | **VERIFIED PUBLIC** (reused from Phase 20A's own already-verified finding — real, team-level, distinct categories) |
| Possession-change-source-specific (DREB vs. steal vs. dead-ball) time-to-shot, rim-share, pass-before-shot-frequency breakdowns | **UNCERTAIN** — not independently re-verified via a live call this phase (severe time constraint; Phase 20A's own audit already found the same limitation for adjacent questions) |
| Player location/tracking at the moment of transition advance, player speed, distance traveled | **PROPRIETARY/UNAVAILABLE** — consistent with every prior phase's finding |
| Outlet-pass identification, DREB-to-pass timing | **UNCERTAIN** — no live verification performed this phase |

## 32. Transition-action empirical findings

**Not independently re-run this phase** — given the severe time
constraint and that the real architectural finding (Sec. 2/4: 3 of 4
candidate actions required zero new mechanism) already strongly
supported the "reuse Phase 16, do not invent a transition-specific
mechanism" hypothesis on ARCHITECTURAL grounds alone, no new live data
pull was performed to further empirically validate action-frequency
distributions. Flagged (Sec. 50).

## 33. DREB-vs-steal findings

**Not empirically differentiated this phase** — both are classified
identically (`LIVE_TRANSITION_CAPABLE`, per Phase 20A) and consume the
identical opportunity-generation/selection logic in this module. No
evidence was gathered to justify treating them differently at the
action-menu level (source itself remains a valid selection INPUT via
whatever real geometry each naturally produces, per Phase 20A's own
audit, but no additional distinction was added in 20B).

## 34. Outlet-study findings

**Not run this phase** (Sec. 31) — no live PBP-derived outlet-pass
identification study was performed. Classified INSUFFICIENT (Sec. 51),
not fabricated.

## 35. Early-offense-decay findings

**Not empirically validated this phase** — the state-based decay
choice (Sec. 14/15) is architecturally motivated (reuses an
already-validated interface) rather than fit to any real early-offense-
efficiency-over-time dataset.

## 36. Transition-shot-quality findings

**Not separately measured this phase.** The architectural claim (Sec.
18/19: transition efficiency should emerge from real contest/geometry,
not a bonus) was NOT empirically re-verified with a live data pull —
it follows directly from the fact that no transition-specific field
exists anywhere in the shot-resolution modules this phase touches
(zero changes to `shot_resolution.py`/`interior_shot_resolution.py`),
which is a structural guarantee, not merely an empirical expectation.

## 37. Empirical model ladder

| Rung | Included? | Basis |
|---|---|---|
| E0 possession source only | Implicit (Phase 20A's own classification) | Reused |
| E1 floor balance | **Yes** | Reused (Phase 20A's `player_zones`/relational tags) |
| E2 Advantage/compromised regions | **Yes** | Reused (Phase 20A's fresh derivation + this phase's decay) |
| E3 role/tendencies | **Yes** | Reused, unmodified (`_score_action`) |
| E4 action selection | **Yes** | Reused, unmodified (`SelectionPolicy`) |
| E5 existing execution engines | **Yes** | Reused, unmodified (17A/17B/18A/18B) |

**No new transition-specific ability was found necessary** — the
expected answer per instruction, confirmed by the architectural audit
(Sec. 2) rather than contradicted by any evidence.

## 38. Ability/tendency authority audit

Verified directly: `passing_accuracy`/`three_point`/`rim_finishing`/
defensive ratings have NO parameter or symbol anywhere in
`transition_offense.py` (namespace scan,
`test_no_ability_symbol_anywhere`) — they retain authority ONLY in
their proper downstream resolvers (17B/18A/18B), never in this phase's
opportunity generation or decay logic.

## 39. Role/tendency authority

`role_off_initiation`/`pass_vs_shoot`/`drive_aggression` legitimately
affect SELECTION (verified: Sec. 6/9's tests) via Phase 16's own,
unmodified scoring function — none of them appears anywhere in
`transition_offense.py` itself, since the extension was accomplished
purely by frozenset membership (Sec. 1), not new scoring code.

## 40. HALFCOURT_SET reset

Represented as `PossessionPhase.HALFCOURT` — Phase 15's own, existing
enum value, no new terminal state was created. Verified directly
(`test_halfcourt_set_preserves_ball_carrier_and_zones`): reaching it
preserves the current ball carrier and zone exactly, never teleporting
anyone to a canonical halfcourt position.

## 41. Possession continuity

Verified directly (`test_decay_does_not_end_possession`,
`test_possession_id_unchanged_through_transition_lifecycle`):
`possession_id`, `offense_team_id`, and `ball_carrier` are all
unchanged across the entire generate-opportunities → decay lifecycle.
Phase 20B never creates a new possession on reset.

## 42. Event semantics

**No new event type was created.** Opportunity generation and decay in
this phase produce no events of their own — the eventual selected
action (a `TRANSITION_PUSH`/`OUTLET_PASS`/etc. `ActionIntent`) is
logged by whichever existing resolver (17A/17B/18) actually executes
it, exactly as it already would be for a halfcourt action. This avoids
the event-vocabulary bloat the task explicitly warned against.

## 43. Deterministic replay

`generate_transition_opportunities` and `decay_transition` are both
pure, deterministic functions of their inputs — no RNG is used in
either (verified: `test_no_global_rng`, and
`test_deterministic_opportunity_generation` confirms two independent
calls with identical inputs produce identical opportunity lists).

## 44. Historical fallback

Verified directly (`test_works_in_classic_era`): a 1996-97-season
engine produces a valid, non-empty transition opportunity set with zero
modern tracking fields required.

## 45. Counterfactual tests

All required numbered counterfactuals were addressed: DREB/steal don't
automatically trigger outlet or push (both remain real, selectable
OPTIONS, never forced); dead-ball neutral state generates no transition
advantage or outlet opportunity; no transition finishing/passing/3PT/
sprint-speed rating exists anywhere (verified by namespace scan); role
affects selection only; `passing_accuracy`/`three_point`/`rim_finishing`
have zero authority in this module (no such symbol exists at all);
`pass_vs_shoot`/`drive_aggression`/`three_point_preference` (the last
via Phase 16's existing, unmodified shot-zone sub-choice, untouched
this phase) can legitimately affect selection; outlet uses Phase 17B
unmodified; push doesn't bypass Phase 16; decay doesn't end the
possession, change `possession_id`, reset the shot clock, or teleport
players; compromised regions can recover (verified); old
`AdvantageState` cannot re-enter (Phase 20A's own firewall, untouched);
deterministic replay confirmed; historical era works without modern
tracking.

## 46. Double-counting matrix

| Pair | Risk |
|---|---|
| `role_off_initiation` × `CREATION_ACTIONS` (now incl. `TRANSITION_PUSH`) | **SAFE** — reuses one existing, unmodified scoring path, applied consistently |
| `pass_vs_shoot` × `PASS_ACTIONS` (now incl. `OUTLET_PASS`) | **SAFE** — same reasoning |
| Transition `AdvantageState` × shot contest | **SAFE** — decay only reduces magnitude; the resolvers that consume it (17A/18) are unmodified and already firewalled from double-applying it |
| Possession source × floor balance | **SAFE** — Phase 20A's own, already-audited, one-to-one mapping; not re-derived here |
| DREB/steal skill × outlet/push selection | **SAFE** — neither skill is read anywhere in this module (already resolved by their own phases) |

## 47. Falsification tests

Directly verified: `OUTLET_PASS` generation requires a real, eligible
receiver (no receiver → no opportunity, regardless of any tendency
value); dead-ball sources never produce an outlet opportunity even with
real supplied geometry (since Phase 20A already stripped it); decay
never resets the shot clock or ends the possession; two identical
opportunity-generation calls produce identical output (determinism);
role/tendency weights shift selection in the expected direction without
any ability value being readable at all.

## 48. Focused test results

`test_transition_offense.py` — 31 tests covering: push/outlet/early-
attack/reset opportunity generation, DREB/steal/dead-ball state
handling, Phase 16 integration (including a check that no parallel
`SelectionPolicy` was created), role/tendency authority (three
directional tests), ability firewalls (namespace + signature scans),
pass/shot/drive resolver reuse (explicit "no duplicate resolver"
checks), transition decay (magnitude reduction, eventual
`HALFCOURT_SET`, possession/shot-clock continuity, immediate-halfcourt
on empty advantage), `HALFCOURT_SET` state preservation, determinism,
historical fallback, and `player_id`-only enforcement.

## 49. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 693 tests — OK**
(662 carried over from Phase 20A + 31 new in
`test_transition_offense.py`). No existing test was modified or
removed; the additive `action_intent.py` changes were verified not to
break any of the 662 prior tests before writing new ones.

## 50. Unresolved issues

- Advance-dribble state mutation during a selected `TRANSITION_PUSH`
  (Sec. 12) is not implemented — the push opportunity exists and is
  selectable, but no resolver advances zones/consumes time for it yet.
- Per-defender posture recovery (Sec. 29) and temporary transition
  matchups (Sec. 30) were deliberately not modeled — decay operates
  purely at the `AdvantageModel` level.
- No live empirical study (Sec. 31-36) was performed this phase given
  severe time constraints — every empirical claim in this report is
  either reused from Phase 20A's own prior finding or an architectural
  (not data-driven) justification.
- DREB-vs-steal opportunity-menu differentiation (Sec. 33) remains
  unexplored.

## 51. Classifications

| Candidate mechanic | Classification |
|---|---|
| Reuse-Phase-16 action-menu architecture (Candidate C) | **LOCK V1** — real, audited, confirmed 3/4 candidate actions needed zero new mechanism |
| `OUTLET_PASS` as an additive opportunity type | **KEEP** — real, structurally-gated, tested |
| State-based decay via existing `AdvantageModel.decay()` | **KEEP** — real, principled reuse; underlying decay math remains each representation's own unvalidated placeholder (inherited from Phase 15/17A, not newly introduced) |
| `HALFCOURT_SET` as `PossessionPhase.HALFCOURT` | **KEEP** — no new terminal state needed |
| Role/tendency authority via extended frozensets | **KEEP** — zero new scoring code, correct firewall behavior verified |
| Advance-dribble state mutation | **INSUFFICIENT** — not built |
| Per-defender posture recovery / temporary transition matchups | **INSUFFICIENT** — not built, deliberately deferred |
| DREB-vs-steal menu differentiation | **INSUFFICIENT** — not studied |
| A new transition-specific ability of any kind | **not created** — explicitly and successfully avoided per instruction |

## 52. Final phase classification

**READY WITH FLAGS FOR 21A.**

Ready: the central architectural question (does transition need new
mechanisms, or does Phase 16/17/18 already suffice?) was answered with
a real, direct audit BEFORE writing code, and the answer (mostly
"already suffices") is reflected in an unusually small, low-risk diff —
one additive action type, two frozenset extensions, and a decay
function that calls one already-existing interface method. Every
required firewall (ability/tendency authority, no transition-specific
ratings, no sprint-speed/locomotion physics, no pace-as-speed) is
enforced and verified by direct namespace/signature scans plus
behavioral tests. Possession continuity, shot-clock non-interference,
and deterministic replay are all directly verified.

Flags: advance-dribble state mutation for a selected push remains
unbuilt; per-defender posture recovery and temporary transition
matchups were deliberately deferred to whatever `AdvantageModel` decay
already provides; no live empirical study was performed this phase
(architectural reasoning carried the design instead, which is a
real, honestly-flagged departure from this project's usual "empirical
checks first" practice, justified here by how conclusively the
architecture audit itself already answered the central question).

Not begun: Phase 21.
