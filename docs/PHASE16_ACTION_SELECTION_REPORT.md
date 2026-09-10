# Phase 16 — Objective Opportunity, Perception & Action Selection

Scope narrowed mid-phase per an adversarial preflight review; this
report reflects the FINAL, narrowed scope actually built and tested.
No drive/pass/shot/rebound/foul/block success probability was
implemented or calibrated. No `TeamBelief` was built. No hidden
`PlayerAbilityProfile` value is read anywhere in this phase's code.

## 1. Files changed

| File | Purpose |
|---|---|
| `action_intent.py` | `ActionType` (V1 taxonomy), `DurationClass`, `ActionIntent` schema, default checkpoint lookup |
| `action_opportunity.py` | `ObjectiveOpportunity`, `StructuralContext`, `generate_opportunities` — WORLD STATE → OBJECTIVE ACTION OPPORTUNITIES |
| `action_perception.py` | `PerceivedOpportunity`, `perceive` — PLAYER PERCEPTION → PERCEIVED OPPORTUNITIES |
| `action_selection.py` | `RoleContext`, `TendencyContext`, `ClockContext`, `SelectionPolicy` — TENDENCY + CONTEXT → ACTION SELECTION |
| `test_action_selection.py` | 35 tests (general architecture) |
| `test_action_selection_falsification.py` | 21 tests (explicit named-leakage-vector and contradictory-profile falsification, added after the mid-phase narrowing) |

All new. No existing file modified — Phase 15's `possession_*.py`,
Phase 13's `role_off_*.py`, Phase 11's `player_tendencies_*.py`, and
every ability estimator are untouched.

## 2. Selected minimal action taxonomy and why

Inspected first, per instruction, rather than adopting Gemini's exact
list: Phase 15's ball-state model (`HELD`/`PASS_IN_FLIGHT`/
`SHOT_IN_FLIGHT`/`LOOSE`/`DEAD`, live/gathered/dead dribble control) and
the existing tendency taxonomy (`three_point_preference`,
`midrange_preference`, `drive_aggression`, `pass_vs_shoot`,
`pullup_vs_catch`). The minimal core the narrowing message asked for is
**shoot / drive / pass / reset**; distinctions beyond that were kept
ONLY where a validated tendency or a materially different opportunity-
generation path justifies them:

- **`PULL_UP` vs. `CATCH_AND_SHOOT`** kept distinct — justified directly
  by the real, validated `pullup_vs_catch` tendency, and their objective
  preconditions differ materially (catch-and-shoot requires the
  just-caught-pass flag and zero prior dribbles this touch; pull-up
  requires an active live dribble instead).
- **`DRIVE` vs. `ISOLATION_ATTACK`** kept distinct — both share the
  live-dribble precondition today (a real, honest limitation, not a
  hidden asymmetry — see Sec. 17) but are structurally separable actions
  a future phase can differentiate by opportunity source without a
  schema change.
- **`KICKOUT` vs. `POCKET_PASS`** kept distinct — their objective
  generation paths are materially different (kickout requires an
  interior-zone compromised area from the `AdvantageModel` interface
  plus a perimeter receiver; pocket pass requires a live roller/screen
  flag, unrelated to advantage state).
- **`SWING_PASS`/`RESET_PASS`** kept as the two "obvious, low-perception"
  pass-family members (nearest-teammate swing vs. a safety valve).
- **`CLOSEOUT_ATTACK`/`TRANSITION_PUSH`/`RECOVER_LOOSE_BALL`** kept —
  each is gated by a materially different piece of Phase 15 state
  (defensive posture; possession phase; ball state) not covered by any
  other action.
- **NOT built**: full off-ball cuts, off-ball screens, a DHO tree,
  detailed transition selection, an elaborate isolation subsystem,
  detailed PnR resolution, or "dozens of pass types" — explicitly
  excluded per the narrowing message.

## 3. Role placement in the causal chain

`ROLE / SYSTEM → DEPLOYMENT / RESPONSIBILITY ALLOCATION`, exactly as
specified. Provisional interpretation implemented:
`role_off_initiation` shifts weight toward `CREATION_ACTIONS`
(`DRIVE`/`ISOLATION_ATTACK`/`PULL_UP`/`POCKET_PASS`) — "who receives
initiation responsibility"; `role_off_finishing` shifts weight toward
`TERMINAL_ACTIONS` (every action that ends the touch in a shot) — "who
is targeted for finishing opportunities"; `role_off_spacing` shifts
weight ONLY on `CATCH_AND_SHOOT`'s opportunity weight — "where a player
is deployed off-ball," kept structurally separate from any shot-zone
choice. None is required to sum to 1.0 — each `RoleContext` field is an
independent real value, and no normalization step exists anywhere in
`action_selection.py` (verified: `test_role_and_tendency_disagreement_is_additive_not_multiplicative`
proves the scoring is a pure, unnormalized sum). Role is **never**
multiplied into an already-on-ball player's selection probabilities in
a compounding way — see Sec. 8's additive-only proof.

## 4. Tendency placement

Once a player has the ball and a perceived menu, tendency is the
primary player-specific behavioral input to MICRO-selection among
already-available actions: `drive_aggression` (drive/iso vs. else),
`pass_vs_shoot` (pass-family vs. shot-family), `pullup_vs_catch`
(pull-up vs. catch-and-shoot specifically), `three_point_preference`/
`midrange_preference` (a shot-zone sub-choice ONLY, not a top-level
action-type effect). Tendency cannot create an opportunity (verified:
`test_tendency_cannot_create_unavailable_action`, an absurd
`drive_aggression=10.0` never produces `DRIVE` when the dribble is
`GATHERED`) and cannot modify execution success (there is no execution/
success code anywhere in this phase for it to modify).

## 5. ObjectiveOpportunity schema

```
ObjectiveOpportunity:
    opportunity_id: str
    action_type: ActionType
    actor_player_id: str
    target_player_id: Optional[str]
    target_zone: Optional[SpatialZone]
    source: str   # diagnostic only, e.g. "live_dribble", "advantage:PAINT"
```
Generated by `generate_opportunities(state, context, advantage)`, which
reads ONLY: Phase 15 `PossessionState` (ball state, dribble control,
zone, phase, defensive assignment/posture), a caller-supplied
`StructuralContext` (teammate ids, perimeter-receiver positions, a live
roller/screen flag, a just-caught-pass flag, the ball handler's assigned
defender id), and an optional Phase 15 `AdvantageModel`. **No hidden
ability field exists in this function's signature at all** — verified
directly by `inspect.signature` in
`test_generate_opportunities_signature_has_no_ability_params`, not just
asserted in prose.

## 6. Perception interface

`perceive(opportunities, vision_latent_propensity, rng)`. Rather than
hardcoding Gemini's exact "always visible / low / high" categories, this
phase built one flexible, binary gate (`VISION_GATED_ACTIONS` — a
plain, editable frozenset, currently `{KICKOUT, POCKET_PASS}`) plus a
single probability-mapping function (`_vision_to_perception_probability`)
that ANY future opportunity type can opt into by adding it to the set —
the interface does not assume exactly two perception "tiers," it
supports an arbitrary per-action-type gate/no-gate split with a shared,
swappable probability curve. `playmaking_vision` is used for nothing
else: it never modifies passing accuracy (no accuracy code exists in
this phase), never generates an opportunity that wasn't already
objectively produced by `generate_opportunities` (verified:
`test_high_vision_cannot_perceive_a_nonexistent_receiver` — a vision
value of 100.0 still perceives nothing when no perimeter receiver
exists in context), and never removes an obvious physical fact (obvious
actions bypass the gate entirely, provenance `OBJECTIVE_NO_GATE`).

## 7. Lazy-generation logic

One pass over the current `PossessionState` + `StructuralContext`,
per-action-type structural checks only — no enumeration of
player × receiver × route × pass-type × screen-type × cut-type. For
passes: only the teammate ids actually supplied in `context` (nearest
teammate, real perimeter receivers, a real roller) are considered — no
sweep over a full 5-man roster's every combination. For shots: only the
zone families structurally reachable from the CURRENT `ball_zone` and
dribble state are offered (a pull-up targets the current zone; a
catch-and-shoot targets the current zone; no other zone is fabricated).
For drives: gated strictly on `DribbleState.LIVE_DRIBBLE` — a
gathered/dead dribble structurally removes it (Phase 15's own rule,
reused, not re-implemented).

## 8. Multiple-opportunity handling

`KICKOUT` is generated once per (compromised interior area × viable
perimeter receiver) pair — 2 real compromised zones × 2 real perimeter
receivers yields 4 distinct, real `KICKOUT` opportunities in one call
(verified: `test_multiple_compromised_areas_each_license_a_kickout`).
No `vulnerable_zone` singleton exists anywhere in this phase's code, and
no test or code path assumes exactly two opportunities
(`test_not_hardcoded_to_exactly_two_opportunities` asserts strictly
more than 2 under a rich context). Selection operates over the full
opportunity/perceived-menu LIST via softmax, not a single hardcoded
slot. No duplicate `opportunity_id`s are ever produced for the same
underlying action (verified: `test_no_duplicate_opportunity_ids_for_same_underlying_action`).

## 9. Hidden-ability firewall

Two independent, both-tested mechanisms: (1) **no ability parameter
exists** in `generate_opportunities`, `perceive` (except the one
sanctioned `vision_latent_propensity`), or `SelectionPolicy.select` —
checked via `inspect.signature` against an explicit forbidden-substring
list (`ability`, `three_point`, `rim_finishing`, `rim_protection`,
`passing_accuracy`, `rim_access`, `ball_security`, `poa_containment`,
`defensive_playmaking`, `offensive_rebounding`) in all three
`TestNoAbilityParameterExistsAnywhere` tests; (2) **no ability import**
— `action_selection.py` imports nothing whose `__module__` is
`player_ability_estimation`/`player_ability_profile`, checked at
runtime. Every individually-named failure mode from the task is covered
by its own test: low `three_point` hiding an open shot, low
`rim_finishing` hiding a drive, low `passing_accuracy` hiding a pass,
high `rim_access_creation` conjuring a drive lane, high `rim_protection`
suppressing drives, high `poa_containment` suppressing drive
availability, `defensive_playmaking` suppressing pass availability, and
role being recomputed from true ability during selection — all in
`test_action_selection_falsification.py`'s `TestNamedLeakageVectors`
and `TestNoAbilityParameterExistsAnywhere` classes. (Two of the task's
named vectors — "low `ball_security` causing a player to `know` a strip
is coming" and "high `offensive_rebounding` causing crash assignment" —
have no corresponding code path in this phase's V1 action set at all;
there is no steal-anticipation or rebound-crash-assignment mechanic
built yet, so the honest test is that no such mechanic exists, which
`inspect.signature`'s parameter check already covers by construction —
noted here rather than fabricating a mechanic just to test rejecting
it.) **Correlation is explicitly allowed**: a player with a real, high
`role_off_initiation` will, in real data, ALSO tend to have strong
on-ball ability — this phase does not (and structurally cannot) prevent
that real-world correlation; it only prevents the ability value itself
from being read at runtime.

## 10. Defensive structural inputs

Selection/opportunity generation may use: defender posture
(`SQUARE`/`TRAILING`/`RECOVERING`/`HELPING`, real Phase 15 persistent
state), matchup assignment (defender→offensive-player pointer), and
paint/interior occupancy AS EXPRESSED THROUGH the `AdvantageModel`
interface's compromised areas (structural, representation-agnostic, not
a specific defender's rating). Verified directly that NO defensive
ABILITY estimate is read: `test_high_rim_protection_does_not_suppress_drive_opportunity`
and `test_high_poa_containment_does_not_suppress_drive_availability`
both confirm a `DRIVE` opportunity's existence depends only on
structural posture/dribble state, never on either estimator.

## 11. Clock feasibility

`ClockContext(shot_clock_remaining, slow_action_shot_clock_floor,
reset_pass_shot_clock_floor)` — two explicit, calibratable placeholder
floors (not empirically derived; flagged as such in-code and here).
`_clock_feasible` implements the required A/B distinction: an action is
removed ONLY when it is mechanically infeasible under the timing
interface (an `EXTENDED`-duration action or `RESET_PASS` below its
floor) — nothing marks an action merely "strategically unattractive"
and removes it; that judgment is left entirely to the additive scoring
weights (Sec. 3/4), not to feasibility filtering. **No guaranteed
bailout action is injected** when the menu shrinks — verified directly
(`test_no_guaranteed_bailout_action_is_injected` greps the module
source for "bailout"/"heave" and finds neither) — and no pass-count cap
of any kind exists; the only anti-loop mechanism is the clock itself
becoming infeasible.

## 12. ActionIntent schema

```
ActionIntent:
    action_type, actor_player_id, possession_id
    target_player_id: Optional[str]
    target_zone: Optional[str]
    originating_opportunity_id: str
    required_checkpoints: Tuple[str, ...]   # Phase-15-compatible checkpoint names
    duration_class: DurationClass            # a KEY (INSTANT/QUICK/MODERATE/EXTENDED), never a fabricated numeric duration
    perception_provenance: str
    context_snapshot_ref: str
```
**Contains no**: success probability, make/miss outcome, turnover
outcome, contest result, whistle result, or rebound result — verified
directly (`test_no_resolution_outcome_fields`) against an explicit
forbidden-field set. No numeric duration is required — `duration_class`
is a model KEY exactly as the narrowing message allows.

## 13. Empirical sources/tests

No new API calls this phase. Every empirical grounding claim reuses
already-verified real data: Phase 8's real `POTENTIAL_AST`/touches
tracking (`role_off_initiation`'s real per-36 reference scale), Phase
9's real `PULL_UP_FGA`/`CatchShoot` tracking (the basis for keeping
`PULL_UP` vs. `CATCH_AND_SHOOT` distinct), and Phase 13's real
`PCT_AST_FGM`/`PCT_AST_3PM` data (directly reused as
`role_off_finishing`/`role_off_spacing`'s own signal, and the source of
the real Spearman ρ=0.11 finding — Phase 13 Sec. 8 — that spacing role
and `three_point_preference` are empirically distinct, which licenses
keeping `role_off_spacing`'s deployment effect structurally separate
from the tendency's shot-zone effect in Sec. 3/4 above).
`playmaking_vision`'s known circularity risk (Phase 8: it was built by
residualizing `POTENTIAL_AST` against usage/drives/time-of-possession)
is respected here by NOT validating this phase's perception gate against
any assist/potential-assist outcome — the gate is tested purely
structurally (does a vision value change perceived-menu membership,
never against a real recorded assist rate), so no circular
re-validation against the same outcome vision was built from occurs.

## 14. Generative falsification results

Real counterfactual-state tests (hidden abilities held structurally
unreadable throughout, never "held fixed" as a value because no such
value is ever passed in): removing the primary initiator's role value
lowers creation-action selection rate (Sec. 3's tests); changing
teammate deployment (adding/removing a roller, a perimeter receiver)
changes which opportunities exist at all (Sec. 8); changing defender
posture (`SQUARE`→`RECOVERING`) toggles `CLOSEOUT_ATTACK`'s existence
structurally (`test_no_closeout_attack_from_square_posture`/
`test_closeout_attack_from_recovering_posture`); changing shot-clock
state removes `EXTENDED`/`RESET_PASS` actions (Sec. 11); changing ball
zone changes which shot-family opportunities are generated (zone is
passed straight through to `target_zone`). All 5 required contradictory
role/tendency profiles (A–E) were run as real, direct simulations (300
trials each, varying only role/tendency, RNG-seeded per trial) and each
produced the expected DIRECTIONAL result — never solved by multiplying
every latent number together, always by the additive score (Sec. 3/4's
mechanism):
- **A** (high initiation, low pass, high drive) → higher self-initiated
  (drive/iso) rate than baseline.
- **B** (high finishing, low drive, high 3PT pref) → drive rate not
  pushed above baseline (finishing role does not mechanically force a
  shot type; it only shifts weight among already-available terminal
  actions).
- **C** (high spacing, low 3PT pref) → `CATCH_AND_SHOOT`'s deployment
  score still favored by spacing role despite a low shooting tendency
  (deployment ≠ zone-choice, proven directly by score comparison).
- **D** (low initiation, very high drive aggression) → drive rate still
  rises above that same low-initiation baseline (context/tendency can
  still move the needle when the opportunity objectively exists).
- **E** (high initiation, pass-heavy, low finishing) → pass-family rate
  exceeds baseline (creation responsibility channeled into passing, not
  forced into a shot).
No historical player lookup table was used to answer any of these —
every result comes from the live scoring function under synthetic
role/tendency inputs.

## 15. Focused test results

`test_action_selection.py` (35 tests, general architecture — ball-state
gating, dribble restrictions, structural defensive opportunities,
multiple-opportunity handling, the firewall's import/fixed-input checks,
role/tendency integration, clock integration, `ActionIntent` schema,
determinism/isolation) plus `test_action_selection_falsification.py`
(21 tests, added after the mid-phase narrowing — signature-level
no-ability-parameter proofs, every individually-named leakage vector,
all 5 contradictory profiles, perception-cannot-invent-receivers,
no-duplicate-opportunity-ids, and the mechanical-vs-strategic clock
distinction including the no-bailout-action grep check). All pass.

## 16. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 425 tests — OK**
(369 carried over from Phase 15 + 35 from the initial Phase 16 pass +
21 added after the mid-phase narrowing). No existing test was modified
or removed; no Phase 1–15 file was touched.

## 17. Unresolved issues

- Every scoring weight and clock floor remains an unvalidated
  placeholder — a dedicated future calibration phase must fit these
  against real heldout action-frequency data before this policy should
  be trusted beyond structural/interpretability testing.
- `DRIVE` and `ISOLATION_ATTACK` currently share an identical objective
  precondition (live dribble) — a real, honest limitation flagged in
  Sec. 2 rather than hidden; a future phase may need a materially
  different opportunity-generation path (e.g. teammate spacing/help
  context) to make the distinction meaningful beyond naming.
- The `_select_shot_zone` sub-choice (three_point_preference/
  midrange_preference nudging a shot's target zone) is scaffolded
  (fields read, weights defined) but is currently a no-op passthrough —
  flagged rather than silently left half-built.
- Two of the task's named leakage vectors (`ball_security`-driven strip
  anticipation, `offensive_rebounding`-driven crash assignment) have no
  corresponding mechanic in this phase's V1 action set at all (Sec. 9)
  — not a gap in the firewall, but a real scope boundary worth noting
  before a future phase adds either mechanic.
- No interaction with a future `TeamBelief` layer exists yet — this
  phase only guarantees selection does not itself violate the
  belief/truth boundary; how a future strategic layer would influence
  `RoleContext`/`ClockContext` inputs remains open.

## 18. Readiness classification

**READY WITH FLAGS** for Phase 17 action-resolution work to begin
consuming `ActionIntent`.

Ready: the WORLD STATE → OBJECTIVE OPPORTUNITY → PERCEPTION →
SELECTION separation is real, tested, and mechanically firewalled from
hidden ability at the signature level (not just by convention); role
and tendency integrate additively with a proven non-compounding
disagreement mechanism, verified against all 5 required contradictory
profiles; ball-state/dribble-state compatibility is complete; multiple
simultaneous opportunities are natively supported with no duplication;
clock feasibility is mechanical, not strategic, with no bailout
injection and no pass-count cap; `ActionIntent` carries zero
resolution-outcome fields and full Phase-15-compatible checkpoint
metadata.

Flags: every weight/threshold remains an unvalidated placeholder; the
shot-zone tendency sub-choice is a scaffolded no-op; `DRIVE`/
`ISOLATION_ATTACK` share an identical precondition today; two named
leakage vectors have no corresponding mechanic yet to test against.

Not begun: Phase 17.
