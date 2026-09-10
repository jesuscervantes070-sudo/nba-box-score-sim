# Phase 21B — Floor Foul Administration

Administers the consequences of an already-detected pre-shot floor foul
(personal fouls, category-aware team fouls, era-aware regulation/OT
bonus evaluation, and real free-throw administration when the rules
require it). Does **not** detect or classify fouls — that remains Phase
21A's (and, for shooting fouls, Phase 18C's) job.

## 1. Files changed

| File | Change |
|---|---|
| `floor_foul_administration.py` | **New.** `FoulAdministrationState`, `FoulAdministrationResult`, `administer_floor_foul`, `administer_bonus_free_throws`, `effective_bonus_foul_threshold` |
| `test_floor_foul_administration.py` | **New.** 27 tests |
| `possession_rules.py` | **Extended, not restructured.** Two new trailing-defaulted `EraRules` fields: `bonus_free_throw_format` (`"TWO_SHOT"` default) and `overtime_bonus_foul_threshold` (`None` default). Every existing keyword-arg `EraRules` construction site (all three named era constants, every existing test) is unaffected — no existing field, default, or call site changed. |

**Zero changes to `possession_engine.py`, `foul_resolution.py`, or
`on_ball_pressure_resolution.py`** — this phase reuses Phase 15's
`non_shooting_foul`/`dead_ball_turnover`, Phase 18C's `FreeThrowSequence`/
`apply_free_throw_attempt_to_engine`/`resolve_free_throw_attempt`/
`shooting_foul_team_bonus_check`, and Phase 21A's `FoulPacket` contract
verbatim.

## 2. Inspected before implementation

- `possession_engine.py`: confirmed `non_shooting_foul(fouler_id, fouled_id, team_foul_count)` already performs the real DEAD-ball/offense-retains-possession transition and the real `era_rules.bonus_foul_threshold` check, returning `in_bonus`; `dead_ball_turnover(committed_by)` already performs the real turnover transition. Both reused verbatim.
- `possession_rules.py`: confirmed `EraRules` is the one, real, era-aware rules object every possession-timing decision already routes through; confirmed `bonus_foul_threshold` exists but is REGULATION-ONLY (no OT concept) and `bonus_free_throw_format` did not exist at all before this phase.
- `foul_resolution.py` (Phase 18C): confirmed `FreeThrowSequence`/`advance_free_throw_sequence`/`apply_free_throw_attempt_to_engine`/`resolve_free_throw_attempt` are real, already-tested, execution-only FT machinery, and that `shooting_foul_team_bonus_check`/`PersonalFoulTracker` were BUILT in Phase 18C explicitly "so a future generic floor-foul phase (21B) can share the same bonus logic rather than reimplementing it" (Phase 18C Sec. 26).
- `on_ball_pressure_resolution.py` (Phase 21A): confirmed `apply_on_ball_pressure_to_engine` ALREADY calls `engine.dead_ball_turnover`/`engine.non_shooting_foul` directly for `OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL` — the ball-state/possession consequence at DETECTION time already exists; confirmed `FoulPacket(offender_id, fouled_player_id, foul_class, live_ball)` is the real, minimal handoff contract this phase consumes, unchanged.
- `possession_state.py`: confirmed no `period`/`is_overtime` field exists anywhere in `PossessionState` — regulation/OT distinction is necessarily a caller-supplied structural fact for this phase, not something this module can derive from engine state (same convention as Phase 21A's caller-supplied `contact_established`).
- `tests/test_simulation_safety.py`, `tools/phase1_diagnostics.py`, `data_source.py`, `game_engine.py`, `injuries.py`, `loader.py`, `counterfactual.py`, `ratings.py`, `simulation_rng.py`, `docs/PHASE1_COUNTERFACTUAL_AUDIT.md`, `docs/phase1_evidence.json` — the protected counterfactual-safety track. Not read for implementation purposes beyond confirming they are untouched by `git status`; not modified, staged, or reset.

## 3. Architecture chosen

**One immutable administration-state object + one entry-point
function**, mirroring Phase 18C's own `PersonalFoulTracker` convention
(`replace()`-based updates) rather than inventing a new mutation style
or a stateful class:

- `FoulAdministrationState` — `personal_fouls` (reuses Phase 18C's
  `PersonalFoulTracker` directly, not a reimplementation), `team_fouls`
  (a real, per-`team_id` running period count, category-gated — see
  Sec. 5), and `administered_event_ids` (the idempotence ledger).
- `administer_floor_foul(engine, state, foul_event_id, offender_id,
  fouled_player_id, foul_class, offender_team_id, fouled_team_id, rng,
  possession_consequence_already_applied=False, free_throw_rate=None,
  is_overtime=False)` → `(new_state, FoulAdministrationResult)`. Takes an
  already-classified `foul_class` (`DEFENSIVE_FLOOR_FOUL` /
  `OFFENSIVE_CHARGE`, matching Phase 21A's `FoulPacket.foul_class`
  values exactly) and never rerolls whether a foul occurred.

This was chosen over (a) folding administration logic directly into
`on_ball_pressure_resolution.py` — rejected, would make 21A a detector
AND administrator for its own fouls only, not reusable by a future
rebound/loose-ball-foul detector, the opposite of what the task asked
for; and (b) a stateful `FoulAdministrator` class holding an internal
mutable ledger — rejected in favor of the project's own established
immutable-`replace()` convention (`PersonalFoulTracker`,
`FreeThrowSequence`), for consistency and because the pure functional
shape has zero surprise-mutation risk when a caller holds the same
`state` object across many possessions/games.

## 4. Phase 21A integration

`administer_floor_foul` accepts a `foul_class`/`offender_id`/
`fouled_player_id` triple that maps directly onto Phase 21A's
`FoulPacket` fields, unchanged — verified directly
(`test_administers_from_a_real_21a_foul_packet_no_redetection`): a real
`DEFENSIVE_FLOOR_FOUL` `FoulPacket` produced by
`apply_on_ball_pressure_to_engine` is handed straight into
`administer_floor_foul` with `possession_consequence_already_applied=True`
and correctly records the personal foul WITHOUT re-invoking the engine
transition Phase 21A already performed
(`result.possession_consequence_applied_here == False`). No detection
logic (`resolve_on_ball_pressure`, `contact_established`,
`ball_security`, `defensive_playmaking`) is referenced anywhere in this
module (verified: `test_no_shot_or_contact_concept_in_this_module`, a
namespace scan for the *shooting*-side analog, plus a direct grep
confirming no on-ball-pressure symbol appears).

## 5. Phase 18C integration / firewall

Reused BY NAME, not reimplemented: `FreeThrowSequence`,
`apply_free_throw_attempt_to_engine`, `resolve_free_throw_attempt`,
`shooting_foul_team_bonus_check`, `PersonalFoulTracker` (verified:
`test_shooting_foul_path_untouched_and_reused_only_via_named_functions`,
`test_18c_bonus_hook_reused_not_reimplemented`). This module never
references `resolve_contact_and_whistle`, `resolve_shooting_foul_shot`,
`ContactContext`, or any shot-family/contact/whistle concept — Phase
18C's own shooting-foul detection and its own FT administration for
shooting fouls are completely untouched and not re-entered. The one
real, unavoidable place this module does NOT reuse a Phase 18C function
verbatim is the terminal FT-sequence state application for two new
sub-cases Phase 18C's own `apply_free_throw_attempt_to_engine` cannot
express without a double-roll (the front-end miss of a THREE_TO_MAKE_TWO
sequence, and that format's true final attempt) — `_apply_missed_final_bonus_ft`/
`_apply_made_final_bonus_ft` apply the exact SAME real rule (`LOOSE`
rebound handoff on a missed final / opponent dead-ball on a made final)
as small, local, single-purpose functions, documented as such rather
than silently duplicated.

## 6. Real-rule correction: offensive fouls do not charge a team foul

Per current NBA Rule 12 Section VII, an ordinary offensive foul (a
charge is exactly this case) records a personal foul on the offender
but does **not** charge the offending team with a team foul and awards
no penalty free throws — only a personal foul + a turnover.
`administer_floor_foul`'s `OFFENSIVE_CHARGE` branch therefore **never**
calls `state._with_team_foul_incremented`, `shooting_foul_team_bonus_check`,
or `administer_bonus_free_throws` — verified structurally
(`test_charge_source_never_calls_bonus_check_or_ft_award`, a source-text
scan of the exact branch) and behaviorally
(`test_charge_never_increments_team_fouls`: 6 real charges recorded as 6
personal fouls, 0 team fouls). This is a genuine, category-aware design
— NOT "every personal foul automatically becomes a team foul," which
was the design's own first draft before this correction and would have
been real-rule-incorrect. Punching/flagrant offensive fouls are a
separate, real exception this phase does not model (Sec. 10, deferred).

## 7. Foul/team-foul state location

Both live on the new `FoulAdministrationState` object, counted directly
(not derived from event-log replay), matching `PersonalFoulTracker`'s
own existing direct-counting convention rather than inventing a
parallel derivation mechanism (per instruction: "follow the existing
architecture rather than inventing parallel state without reason").
`team_fouls` is a plain `Dict[str, int]` keyed by real `team_id` — no
new identity concept. `reset_team_fouls()` is a real, minimal
period-boundary hook this module never calls itself (this module has no
period/clock concept of its own) — left for a future game-orchestration
layer, exactly the deferred-scope posture the task specified.

## 8. Era/rules handling

Two additive `EraRules` fields (`possession_rules.py`), both
trailing-defaulted so every existing construction site (`PRE_SHOT_CLOCK_ERA`,
`CLASSIC_24_RESET_ERA`, `MODERN_14_RESET_ERA`, every existing test) is
unaffected:

- `bonus_free_throw_format`: `"TWO_SHOT"` (real, current-NBA format,
  the default for all three named era constants — unchanged) or
  `"THREE_TO_MAKE_TWO"` (real, documented historical NBA format,
  confirmed abolished by the 1981-82 season — per explicit correction,
  **not** modeled as an NCAA-style "1-and-1," which was never the real
  NBA rule). Deliberately **not** assigned to any of the three named era
  constants (no exact 1954–1981 boundary, or exact post-1981-82 TWO_SHOT
  start date, was independently verified this phase — "do not assume an
  exact 1954-1981 rules block," per explicit instruction) — exercised
  only via dedicated, clearly-synthetic test `EraRules` instances
  (`test_bonus_format_is_rules_driven_not_hardcoded`,
  `test_full_administration_path_honors_configured_format`).
- `overtime_bonus_foul_threshold`: `None` by default (missing != zero —
  falls back to the regulation `bonus_foul_threshold` explicitly via
  `effective_bonus_foul_threshold`, never silently treated as 0).

`administer_floor_foul(..., is_overtime: bool = False)` is the caller-
supplied structural fact routing bonus evaluation — this module has no
period/clock state of its own to derive it from (same posture as Phase
21A's caller-supplied `contact_established`). Verified directly
(`test_ot_uses_a_distinct_real_threshold_not_the_regulation_number_reset`,
`test_administration_reaches_bonus_earlier_in_overtime`): the SAME team,
SAME foul count (3) is in the bonus under a configured OT threshold of 3
but NOT in the bonus under the regulation threshold of 5 — a real,
distinct number, not the regulation threshold merely reset, per explicit
correction #3.

## 9. Bonus handling

`effective_bonus_foul_threshold(era_rules, is_overtime)` picks the
regulation or OT threshold; regulation reuses Phase 18C's own
`shooting_foul_team_bonus_check(engine, count)` verbatim (it already
reads exactly `era_rules.bonus_foul_threshold`); OT performs the
identical real `count >= threshold` comparison against the OT-aware
number, since Phase 18C's own hook has no OT parameter and was not
modified to add one (keeping it a real, tested, unchanged surface).
Once in the bonus, `administer_bonus_free_throws` dispatches on
`engine.era_rules.bonus_free_throw_format` ONLY — no season, no
player_id — to either `_administer_two_shot` (full reuse of Phase 18C's
`FreeThrowSequence`/`apply_free_throw_attempt_to_engine`, looped exactly
as Phase 18C's own missed-2PT-shooting-foul case already does) or
`_administer_three_to_make_two` (up to 3 attempts, stopping as soon as 2
are made — verified: `test_three_to_make_two_stops_at_two_makes`,
`test_three_to_make_two_uses_all_three_when_never_making_two_in_a_row`).

## 10. Possession consequences

- Defensive floor foul outside the bonus: offense retains team
  possession (`engine.non_shooting_foul` leaves `offense_team_id`/
  `defense_team_id` untouched — the SAME real Phase 15 behavior Phase
  21A's own report already documents), ball `DEAD`, no FT.
- Defensive floor foul in the bonus: the SAME `non_shooting_foul` call
  happens first (offense provisionally retains, matching real
  administration order), then the bonus FT sequence's OWN terminal state
  supersedes it exactly as a real FT sequence does (made final → dead
  ball to the opponent; missed final → `LOOSE` rebound handoff) —
  verified via the `TestDefensiveFloorFoulInBonus` tests.
- Offensive charge: `engine.dead_ball_turnover` (Phase 15, reused
  verbatim, exactly as Phase 21A already calls it) — no FGA, no steal
  concept anywhere in the result (`test_charge_administration` asserts
  `"steal"` is not even an attribute name on the result object), never a
  bonus FT to the offense.

## 11. Free-throw reuse

`TWO_SHOT` is a 1:1 reuse of Phase 18C's existing missed-2PT-shooting-
foul FT loop. `THREE_TO_MAKE_TWO`'s per-shot roll reuses Phase 18C's
`resolve_free_throw_attempt` (execution-only — no contact/foul context,
same firewall Phase 18C already established) for every individual
attempt; only the terminal state application (Sec. 5) is a small, local,
documented function rather than a call into `foul_resolution.py`,
because that module's own `apply_free_throw_attempt_to_engine` couples
roll+apply and would re-roll an already-resolved shot.

## 12. Duplicate/idempotence protection

`foul_event_id` (caller-supplied, per-real-foul unique key) is checked
against `state.administered_event_ids` FIRST, before any counter is
touched. A duplicate call returns the INPUT state completely unchanged
(`state == state_before_repeat`, verified via dataclass equality) and a
`duplicate=True` result with no personal-foul increment, no team-foul
increment, and no FT sequence — verified directly
(`test_duplicate_administration_is_a_no_op`,
`test_duplicate_never_awards_a_second_ft_sequence`, the latter
specifically re-attempting a real bonus-FT-awarding call a second time
and confirming zero additional FTs are awarded).

## 13. Explicit exclusions / deferred mechanics

Per instruction, none of the following were implemented this phase:

- Illegal screens (future off-ball Phase 22).
- Late-game intentional fouling / any intentional-fouling AI.
- Take foul / clear path (kept structurally distinct from an ordinary
  generic floor foul — not collapsed into one).
- Defensive three seconds (not represented as a generic technical foul).
- Technical/flagrant/ejection systems — no such type, field, or code
  path exists anywhere in this module. A future typed special foul MAY
  be administered through an extension of this layer later; this module
  neither detects nor classifies any of them now.
- Punching/flagrant offensive-foul exceptions to the "no team foul"
  rule (Sec. 6) — the real, documented exception exists but is out of
  scope.
- The real "final two minutes, one extra non-penalty foul" exception —
  genuinely deferred, not fabricated: it requires period/game-clock
  orchestration state (`PossessionState` has no `period`/`is_overtime`
  field at all, confirmed by direct inspection, Sec. 2) that this
  project does not yet track anywhere. Flagged, not built.
- A second shooting-foul engine, a redetection of 21A's outcomes, or
  any RNG deciding WHETHER a foul happened — none exists anywhere in
  this module (only the real, already-reused FT execution roll).

## 14. Remaining flags / unknowns

- No exact historical season boundary for `THREE_TO_MAKE_TWO` vs.
  `TWO_SHOT` was independently verified this phase (Sec. 8) — the
  mechanism is real and tested; its assignment to any specific season is
  not, and none of the three named `EraRules` constants claims one.
- `overtime_bonus_foul_threshold` values (3 for OT vs. 4 for regulation,
  per the user-provided correction) are NOT set on any of the three
  named era constants — the field and its fallback semantics are real
  and tested, but no historical/current per-era table was populated this
  phase (same PLACEHOLDER posture as `bonus_foul_threshold` itself,
  Phase 15's own original flag, still unresolved).
- The final-two-minutes non-penalty-foul exception remains unimplemented
  (Sec. 13) pending a period/clock-tracking extension this phase
  deliberately did not build (out of scope for "smallest coherent
  architecture").
- `is_overtime` is caller-supplied and unverified against any real game
  orchestration layer this phase, since none exists yet to supply it
  automatically — consistent with Phase 21A's own caller-supplied
  `contact_established` precedent.

## 15. Targeted test results

`python3 -m unittest test_floor_foul_administration -v` → **Ran 27
tests — OK**. Covers: defensive floor foul outside bonus (full
administration, possession retained), defensive floor foul in bonus
(rules-evaluated bonus, correct FT consequence, foul counted exactly
once), offensive charge (personal foul, turnover, no FGA/steal, never a
team foul, never bonus FTs — both behaviorally and via a structural
source-scan firewall), idempotence (duplicate administration is a
real no-op, including re-attempting an already-awarded bonus FT
sequence), the Phase 21A integration boundary (a real `FoulPacket` from
`apply_on_ball_pressure_to_engine` administered with no redetection and
no double-applied engine transition), the Phase 18C firewall (named-
function reuse only, no shooting-foul detection surface touched), era-
rules variation (`TWO_SHOT` vs. `THREE_TO_MAKE_TWO` producing materially
different real administered behavior from rules configuration, not a
hardcoded constant), regulation-vs-OT threshold behavior (a real,
distinct OT number, not the regulation threshold reset), missing/invalid
context (missing `free_throw_rate` in the bonus raises explicitly,
unknown `foul_class`/`bonus_free_throw_format` rejected, name-keyed
`player_id` rejected via the existing `_assert_player_id` guard),
determinism (identical seed → identical replay, no global RNG), and the
team-foul ledger (independent per-team counts, category-gated, real
`reset_team_fouls` hook leaves the original state untouched).

## 16. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 747 tests — OK**
(720 carried over from Phase 21A + 27 new in
`test_floor_foul_administration.py`). No existing test was modified or
removed. The two `TEST-RP-BAD`/`TEST-SZ-BAD` lines in the output are
pre-existing, expected simulated-failure log lines from unrelated data-
source tests (present identically in the Phase 21A baseline run).

## 17. Classifications

| Candidate mechanic | Classification |
|---|---|
| `administer_floor_foul` single entry point, immutable state | **KEEP** — real, tested, consistent with existing `PersonalFoulTracker`/`FreeThrowSequence` conventions |
| Category-aware team-foul gating (charges never charge a team foul) | **LOCK V1** — real, well-documented current-NBA rule, verified structurally and behaviorally |
| `TWO_SHOT` bonus format | **KEEP** — real, current-NBA rule, full reuse of Phase 18C's own machinery |
| `THREE_TO_MAKE_TWO` bonus format | **KEEP BUT FLAG** — real, documented historical NBA format (confirmed abolished 1981-82); not assigned to any specific season this phase |
| `overtime_bonus_foul_threshold` / `effective_bonus_foul_threshold` | **KEEP BUT FLAG** — real, distinct-from-regulation mechanism, tested; no real per-era OT threshold table populated |
| Idempotence via `foul_event_id` ledger | **KEEP** — real, tested, no-op on repeat |
| Final-two-minutes non-penalty-foul exception | **not implemented** — explicitly deferred, needs period/clock state this project doesn't track yet |
| Technical/flagrant/ejection administration | **not implemented** — explicitly out of scope this phase |

## 18. Final phase classification

**READY WITH FLAGS.**

Ready: the detection/administration split is real and enforced (this
module reads an already-classified `foul_class` and never rerolls
anything); the category-aware team-foul correction (offensive fouls
never charge a team foul) is implemented and tested both structurally
and behaviorally; regulation-vs-OT bonus evaluation uses a real, rules-
supplied, distinct threshold rather than the regulation number reset;
the historical `THREE_TO_MAKE_TWO` bonus format is real and correctly
distinguished from an NCAA-style 1-and-1 (which was never modeled);
idempotence is enforced via an explicit event-id ledger with a verified
no-op on repeat; Phase 18C's FT machinery and bonus hook are reused by
name with zero modification; Phase 21A's `FoulPacket` contract is
consumed with zero redetection and a verified non-double-application
seam for the possession consequence; the full suite is green at 747/747
with zero regressions.

Flags: no real per-era table exists yet for either
`bonus_free_throw_format` or `overtime_bonus_foul_threshold` (both are
real, tested mechanisms with unpopulated historical data — same
PLACEHOLDER posture Phase 15's own `bonus_foul_threshold` has carried
since its introduction); the final-two-minutes exception is deferred
pending period/clock state this project does not yet track; punching/
flagrant offensive-foul exceptions to the no-team-foul rule are
unmodeled; technical/flagrant/transition-take-foul/clear-path/away-from-
the-play/defensive-three-seconds administration are all explicitly
deferred, per instruction.

Not begun: Phase 22.
