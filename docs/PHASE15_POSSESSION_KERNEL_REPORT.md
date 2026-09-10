# Phase 15 — Possession State & Event Kernel

Architectural infrastructure + correctness only. No shot/pass/rebound/
foul/drive probability, no action-choice policy, no advantage-decay
coefficient, and no event-duration distribution was implemented or
calibrated. `PlayerAbilityProfile`, tendency estimators, Phase 12A/13
physical/role code, the legacy `Player`/`loader.py` path, and all Codex
counterfactual files are untouched.

## 1. Files changed

| File | Purpose |
|---|---|
| `possession_state.py` | `BallState`, `DribbleState`/`PlayerBallControl` (+ `IllegalControlTransition`), `SpatialZone` (+ `ball_side`/`is_strong_side`), `DefensivePosture`/`DefensiveAssignment`, `PossessionPhase`, `PossessionState`, and the `_assert_player_id` identity guard |
| `possession_advantage.py` | `AdvantageModel` interface + `CompromisedArea`, and two real (empirically-unvalidated) implementations: `DiscreteTierAdvantage`, `SpatialMagnitudeAdvantage` |
| `possession_rules.py` | `EraRules`, `get_era_rules(season)`, `oreb_reset_value` |
| `possession_events.py` | `EventType`, `Event`, `EventLog` (retained + streaming modes) |
| `possession_engine.py` | `PossessionEngine` (orchestration), `resolve_legacy_name_to_engine_id` (the one sanctioned name→id bridge) |
| `test_possession_kernel.py` | 38 tests |

All new, no existing file modified.

## 2. State architecture

`PossessionState` is the single source of truth for one possession:
`possession_id`, `offense_team_id`/`defense_team_id` (team ownership,
explicitly separate from individual ball possession), `phase`
(`PossessionPhase`), `ball_state`, `ball_carrier` (`Optional[str]`,
never forced non-null), `ball_control` (`Optional[PlayerBallControl]`,
only meaningful when `ball_carrier` is set and `ball_state == HELD`),
`ball_zone`, `assignments` (defender_id → `DefensiveAssignment`),
`shot_clock_remaining`/`game_clock_remaining`. Mutation is exclusively
via `with_*`/`replace()`-style methods (matching this project's existing
`AttributeEstimate`/`PhysicalObservation`/`RoleObservation` "replace,
don't mutate" convention from Phases 1–13) — nothing anywhere in this
kernel mutates a `PossessionState` field in place. `PossessionEngine`
owns exactly one `PossessionState` instance per possession; two engines
never share mutable state (verified by test).

## 3. Event architecture

Event-driven, not tick-based — confirmed directly: no file in this
phase contains a fixed-interval loop or a hardcoded `0.1`/`0.5` tick
constant; every state-changing method takes an explicit `delta_t`
parameter supplied by the caller (`dt` throughout), consumed by the
event, never invented by the kernel. `EventType` enumerates the
task-required outcome pathways (made FG, missed FG + DREB/OREB,
shooting/non-shooting foul, live-ball/dead-ball turnover, block
retained/secured, out of bounds, jump ball, shot-clock violation,
period expiration) plus the finer-grained mechanics needed to represent
a possession in between (pass released/received, dribble gathered, shot
released vs. resolved, assignment switch, advantage change, reaction
checkpoint). `Event` itself carries no resolution logic — it's a
plain, frozen record of what happened.

## 4. Reactive/interruption design

`PossessionEngine.run_checkpointed_action(checkpoints, reaction_fn,
dt_per_checkpoint)` — an ordered list of named checkpoints (the task's
own example sequence: `drive_begins → poa_interaction →
defender_beaten → help_opportunity → help_arrives_or_fails → release`)
is walked one at a time; `reaction_fn(checkpoint_name, current_state)`
is called before each subsequent checkpoint runs, and returning the
literal string `"INTERRUPT"` stops the sequence immediately — the
remaining checkpoints never execute (verified by test: exactly 3 of 5
checkpoints ran before interruption, and the interrupted checkpoint is
reported back). This satisfies "event-driven != non-interruptible"
without inventing any specific event duration — `dt_per_checkpoint`
defaults to `0.0` (pure sequencing) and is only ever a caller-supplied
test-fixture value, never a hardcoded real duration.

## 5. Ball-state model

`BallState`: `HELD`, `PASS_IN_FLIGHT`, `SHOT_IN_FLIGHT`, `LOOSE`, plus a
5th state, `DEAD` (needed to represent a made basket/whistle/out-of-
bounds awaiting inbound as distinct from a live, contested `LOOSE`
scramble — not one of the four named as a minimum, added because the
task's own required outcome pathways, e.g. dead-ball turnover and
period expiration, need a ball state that isn't "loose and
contestable"). Team possession (`offense_team_id`) and individual ball
possession (`ball_carrier`) are structurally independent fields —
verified directly: a pass in flight retains `offense_team_id` while
`ball_carrier` is `None` (test:
`test_offense_team_possession_survives_ball_flight`); a defensive block
sets `offense_team_id` to `None` (genuinely unresolved) while a block
*retained by the offense* leaves `offense_team_id` untouched (test:
`test_block_retained_by_offense_keeps_team_possession`) — the two
concepts can vary independently in either direction, not just
one-way.

## 6. Dribble/control-state model

`DribbleState`: `LIVE_DRIBBLE → GATHERED → DEAD_DRIBBLE`, one-way within
a single `PlayerBallControl` instance — `continue_dribble()` after
`GATHERED` and `gather()` after `DEAD_DRIBBLE` both raise
`IllegalControlTransition` (verified by test), making an illegal
repeated live dribble structurally unreachable rather than merely
avoided by convention. `PossessionState.with_ball_carrier` always
constructs a **fresh** `PlayerBallControl` (reset to `LIVE_DRIBBLE`) for
a new carrier and clears it entirely when nobody holds the ball —
verified directly: after a pass and catch, the new carrier's control
state is `LIVE_DRIBBLE` even though the previous carrier had reached
`GATHERED` (test: `test_new_carrier_always_starts_fresh_live_dribble`).
No footwork/physics is modeled.

## 7. Spatial topology tested/chosen (provisional)

8 zones: `BACKCOURT`, `TOP_OF_KEY`, `LEFT_WING`, `RIGHT_WING`,
`LEFT_CORNER`, `RIGHT_CORNER`, `PAINT`, `RESTRICTED_RIM` — deliberately
chosen to align with the already-empirically-validated Phase 5
shot-zone taxonomy (restricted area / paint-non-RA / midrange /
corner3 / above-break3) rather than adopting Gemini's un-examined
7-zone proposal or inventing a new, disconnected one. Strong-side/
weak-side is explicitly **derived**, not stored: `ball_side(zone)` and
`is_strong_side(player_zone, ball_zone)` are pure functions of two
zones (verified by test), so a strong/weak-side field can never drift
out of sync with the ball's actual zone. Zone count is explicitly not
locked — nothing in this kernel assumes exactly 8 (e.g. `PossessionState`
stores a `SpatialZone` value, not an integer count).

## 8. Defensive persistent vs. derived state

**Persistent**: `DefensiveAssignment` (defender_id → assigned
offensive `player_id`, a real pointer, updated in place via `switch()`
— verified by test) and `DefensivePosture` (`SQUARE`/`TRAILING`/
`RECOVERING`/`HELPING`, updated independently of the assignment pointer
via `update_posture()` — verified by test that a posture set before a
switch does not silently persist past it: a fresh `switch()` call
always takes an explicit posture argument rather than inheriting the
prior one). **Explicitly NOT persistent** (per instruction): no
"generic contest rating" or "generic defensive reaction state" field
exists anywhere in `PossessionState` or `DefensiveAssignment` — a
future action-resolution step would derive a contest level from
`(assignment, posture, zone, advantage)` at the moment of resolution,
not read it off a stored field.

## 9. Advantage abstraction

`AdvantageModel` (abstract): `compromised_areas()` (a tuple, never a
single scalar — supports zero, one, or many simultaneous
`CompromisedArea`s), `decay(dt)`, `transfer(from_zone, to_zone)`,
`compound(other)` — all return a **new** instance (immutable, matching
the rest of the kernel). Two real implementations exist to prove the
interface doesn't secretly assume one shape: `DiscreteTierAdvantage`
(candidate B — `TILTED`/`COLLAPSED`/`SCRAMBLE` per zone, `NEUTRAL`
represented by a zone's *absence* from the tier dict, not an explicit
entry — missing≠zero applied here too) and `SpatialMagnitudeAdvantage`
(candidate A — continuous per-zone magnitude). **The identical test
suite runs against both** (`test_survives_multiple_representations`),
asserting only interface-level invariants (decay never increases
compromise; transfer moves the compromised zone; compound doesn't
shrink the compromised set) — no specific numeric formula is asserted
for either, and neither is marked as the chosen final representation.
`compound()` deliberately raises `TypeError` when given the other
representation (verified by test) — combining two different
representations is out of this phase's scope, not silently coerced.
Multiple simultaneous compromised areas are directly supported and
tested (`test_multiple_simultaneous_compromised_areas`, 2 zones at
once). A `CompromisedArea` is constructible and inspectable with **no
player/perception object involved at all** — the concrete, tested proof
that WORLD OPPORTUNITY is representable independently of the (not yet
built) PLAYER PERCEPTION layer.

## 10. Possession phase model

`PossessionPhase`: `TRANSITION`, `HALFCOURT`, `SECOND_CHANCE`,
`DEAD_BALL`. `resolve_shot_missed_offensive_rebound` is the one
concrete phase-creating transition built this phase: it moves to
`SECOND_CHANCE` and **explicitly clears `self.advantage` to `None`**
(verified by test) — not because a real design decision favors "no
advantage after an OREB," but because automatically preserving the old
advantage, automatically resetting to a specific NEUTRAL instance of
whatever representation is active, or automatically forcing a putback
attempt would each be a real, unwarranted modeling choice this phase
was explicitly told not to make. A second test confirms no putback is
forced: after an OREB the new carrier can immediately pass rather than
being constrained into a shot-only state.

## 11. Era-rules interface

`EraRules` (`shot_clock_seconds`, `oreb_shot_clock_reset_seconds`,
`bonus_foul_threshold`, `period_length_seconds`, `periods_per_game`),
looked up by real season string via `get_era_rules(season)`. Two real,
verified rule-change facts anchor three era entries: no shot clock
existed before the 1954-55 season (`PRE_SHOT_CLOCK_ERA`), the
offensive-rebound shot-clock reset was a full 24s reset from 1954-55
through 2017-18 (`CLASSIC_24_RESET_ERA`), and became a real, shortened
14s reset starting 2018-19 (`MODERN_14_RESET_ERA`) — verified by test
that `oreb_reset_value` returns the correct value for all three real
eras, including `None` (no shot clock at all) for the earliest one.
`bonus_foul_threshold` is explicitly flagged in the module docstring
and in this report as a **single placeholder value shared across all
three eras, not an empirically verified historical table** — used only
to prove the hook works (`PossessionEngine.non_shooting_foul` returns
whether the fouled team is in the bonus, tested for both true and false
cases), not presented as historically accurate. No rule value is
imported as a bare constant anywhere in `possession_engine.py` — every
use goes through the `EraRules` object passed into the engine.

## 12. Terminal/continuation graph

| Event | Ball state after | Team possession after | Terminal? |
|---|---|---|---|
| Made FG | `DEAD` | unchanged (informational) | Yes — new possession object needed |
| Missed FG + DREB | `HELD` (rebounder) | unchanged field, but rebounding team is the real new offense (caller swaps ids for the next `PossessionEngine`) | Yes |
| Missed FG + OREB | `HELD` (rebounder) | unchanged (same offense) | **No** — continues as `SECOND_CHANCE` |
| Shooting foul | `DEAD` | unchanged | Depends (FT outcome, out of scope) |
| Non-shooting foul | `DEAD` | unchanged | Depends (inbound follows) |
| Live-ball turnover | `HELD` (new team) | flips immediately, `TRANSITION` phase | Yes (new possession begins immediately, no dead-ball stoppage) |
| Dead-ball turnover | `DEAD` | unchanged field (caller sets next inbound) | Yes |
| Block retained by offense | `LOOSE` | unchanged (still offense's) | No — same possession continues once secured |
| Block secured by defense | `LOOSE` | `None` (genuinely unresolved) | Pending — resolved by `secure_loose_ball` |
| Out of bounds | `DEAD` | caller-supplied new team ids | Yes |
| Jump ball | `LOOSE` | `None`, `None` (both unresolved) | Pending |
| Shot-clock violation | `DEAD` | unchanged field (caller sets next inbound) | Yes |
| Period expiration | `DEAD` | unchanged | Yes |

No pathway corrupts ownership: every row above was exercised by at
least one test, and none leaves `ball_carrier` set while `ball_state`
is anything other than `HELD`, nor leaves a stale `PlayerBallControl`
attached to a carrier who no longer holds the ball (`with_ball_carrier`
enforces this unconditionally).

## 13. Event logging/accounting

`EventLog` supports two modes behind one interface: `"retained"`
(a real Python list, `.events` returns it, used by every test this
phase) and `"streaming"` (nothing retained — each `Event` is hazed
directly to a caller-supplied `aggregator` callback and discarded,
`len(log) == 0` afterward, verified by test) — the retained-vs-streaming
choice is made once at `EventLog` construction, not per-event. No
FGA/FGM/AST/TOV/etc. derivation exists yet — `Event.event_type` +
`primary_player_id`/`secondary_player_id`/`zone`/`metadata` carry
enough real information for a future accounting layer to derive those
(e.g. `SHOT_RESOLVED` with `metadata={"made": True}` and a `zone` is
sufficient to derive FGA/FGM/3PA/3PM once shot value is known from
zone), but that derivation function is explicitly not built this phase.

## 14. Legacy-engine isolation

The old `Player`/`loader.py`/`models.py` path was not read or modified
this phase. Every Phase 15 public method that accepts a player argument
calls `_assert_player_id`, which raises `TypeError` on anything that
isn't a real numeric `player_id` string or `None` — verified directly
by test with a real name string (`"LeBron James"`) passed to
`inbound()`. The **one** sanctioned bridge from a legacy name into this
engine, `resolve_legacy_name_to_engine_id`, goes through
`player_identity.resolve_name_to_id` (Phase 14) and raises
`ValueError` on anything other than `RESOLVED` (verified by test with
both a successful resolution and a real ambiguous case, "Patrick
Ewing") — it never guesses, matching Phase 14's own ambiguity policy
exactly.

## 15. Test results

`python3 -m unittest discover -p "test_*.py"` → **Ran 369 tests — OK**
(331 carried over from Phase 14 + 38 new in `test_possession_kernel.py`).
Covered (per the required list): held→pass-in-flight→held,
held→shot-in-flight→rebound/terminal, loose-ball resolution,
offense/team possession surviving ball flight, live-dribble→gather→dead
transitions (including two distinct illegal-transition guards),
reactive-subevent interruption (and non-interruption), matchup-pointer
updates after switch, defender-posture update and no stale-posture
leakage across a switch, objective opportunity existing independent of
perception, advantage abstraction surviving both representations,
multiple simultaneous compromised areas, second-chance context creation
(clearing, not preserving/resetting, advantage; not forcing a putback),
transition-phase representability, era-specific shot-clock-reset hook
(all three real eras), bonus-rules hook, blocked-shot live state
(retained vs. secured), dead-ball vs. live-ball turnover, jump-ball
state, shot-clock violation, event-log accounting (both modes),
player_id-only interface enforcement, name-keyed-path rejection (and
its one sanctioned, Phase-14-routed bridge), deterministic replay under
a fixed RNG seed (same seed → identical sequence; different seed →
different sequence), and no state-mutation leakage between two
independent possessions/engines.

## 16. Unresolved architecture questions

- Whether `AdvantageModel`'s two candidates should eventually be
  unified into one representation, or genuinely kept as two
  interchangeable back-ends, is explicitly undecided — this phase only
  proves the interface can host either.
- The exact zone count (8) is provisional; whether `TOP_OF_KEY` or any
  other zone needs splitting will depend on future empirical shot/pass
  mechanics work, not on anything decided here.
- `bonus_foul_threshold`'s real historical values by era are not
  verified — flagged, not fixed, in `possession_rules.py`.
- No accounting/derivation layer exists yet from `EventLog` to
  box-score-style aggregates (FGA/AST/TOV/etc.) — the event shape is
  designed to support one, but building it is future work.
- `PossessionPhase` transitions beyond the ones this phase's tests
  exercise (e.g. an explicit `TRANSITION → HALFCOURT` engine method,
  rather than direct field assignment as shown in
  `test_transition_phase_can_move_to_halfcourt`) are not yet
  encapsulated in a dedicated `PossessionEngine` method — the state
  permits the transition; a convenience method was not added since no
  additional invariant needs enforcing there yet.
- Free-throw sub-sequences (after a shooting foul) are not modeled as
  their own event/state sequence — `shooting_foul` only marks the ball
  dead; a future phase should decide whether free throws are their own
  mini-possession or a special dead-ball sub-state.

## 17. Readiness classification

**READY WITH FLAGS** for empirical action-mechanics work to begin
building on top of this kernel.

Ready: every state primitive the task required exists and is tested;
team/individual possession are provably independent; illegal
control-states are structurally unreachable; the advantage interface is
proven representation-agnostic with two working examples; era rules are
a real, pluggable hook rather than hardcoded constants; the engine is
`player_id`-only with a real, tested guard and exactly one sanctioned
legacy bridge; deterministic replay works; no cross-possession state
leakage exists.

Flags: the event→box-score accounting derivation is unbuilt (Sec. 13);
`PossessionPhase` transitions have state-level support but not full
engine-method coverage for every transition (Sec. 16); free-throw
sequencing is undecided; the advantage representation choice (A vs. B)
remains genuinely open and will need a dedicated empirical phase before
any action-mechanics probability can consume it meaningfully.

Not begun: Phase 16.
