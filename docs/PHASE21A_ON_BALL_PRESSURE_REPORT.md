# Phase 21A — On-Ball Contact, Strips, Reach-Ins & Pre-Shot Collisions

Resolves one missing causal slice: a ball handler has control → a
defender actively interrupts → control/contact/foul consequence.
Operates strictly BEFORE gather/shooting motion. No team-foul counting,
bonus/FT administration, or late-game foul policy — all explicitly
Phase 21B.

## 1. Files changed

| File | Change |
|---|---|
| `on_ball_pressure_resolution.py` | **New.** `OnBallPressureContext`, `OnBallContactOutcome`, `resolve_on_ball_pressure`, `FoulPacket`, `apply_on_ball_pressure_to_engine` |
| `test_on_ball_pressure_resolution.py` | **New.** 27 tests |

**Zero changes to any existing file** — reuses Phase 15's `dead_dribble`,
`dead_ball_turnover`, `non_shooting_foul`, `secure_loose_ball` and
Phase 4A/4B's `ball_security` / Phase 1-3's `defensive_playmaking` /
Phase 6's `foul_drawing`/`foul_discipline`, all unmodified.

## 2. Existing interfaces inspected

Confirmed directly: Phase 15's `non_shooting_foul` already implements
the exact real bonus-check pattern Phase 21A needs for the whistle
branch (reused verbatim, its own return value — whether the fouled
team is in the bonus — is passed straight through, untouched); `dead_ball_turnover`
already implements the exact ball-state transition an offensive charge
needs (reused verbatim); `dead_dribble` (Phase 17A addition) already
implements the exact `DEAD_DRIBBLE` transition a forced pickup needs
(reused verbatim); `secure_loose_ball` (Phase 15) already lets the
offense recover its own loose ball. No new engine method was required
at all.

## 3. Exact 21A scope selected

Live-dribble pressure (disruption/forced-pickup/clean-strip) and
pre-shot collision classification (no-call/offensive-charge/defensive-
floor-foul), both gated on the ball being in `DribbleState.LIVE_DRIBBLE`
(i.e., before gather). No team-foul ledger, no bonus/FT administration,
no late-game intentional-foul policy, no take-foul/clear-path
adjudication, no defensive-3-seconds, no off-ball/screen fouls.

## 4. Phase 17A ownership boundary

`on_ball_pressure_resolution.py` does not import or call
`drive_resolution.py` at all (verified:
`test_no_drive_resolver_reference`) — ordinary POA containment/leverage
(`CLEAN_PENETRATION`/`PARTIAL_EDGE`/`CONTAINED`/Phase 17A's own clean
`FORCED_PICKUP`) remains entirely Phase 17A's domain. Phase 21A
resolves a DIFFERENT causal slice: active strip/reach/collision
interruptions triggered by a caller explicitly invoking this module,
not a byproduct of drive resolution.

## 5. Phase 18C shooting boundary

**The highest-priority empirical/architectural issue, resolved
structurally**: `apply_on_ball_pressure_to_engine` raises `ValueError`
unless `engine.state.ball_control.state == DribbleState.LIVE_DRIBBLE`
(verified: `test_gathered_dribble_rejected`, `test_dead_dribble_rejected`)
— the same contact can never be evaluated by both this module and
Phase 18C, because the precondition itself is mutually exclusive with
Phase 18C's own operating state (post-gather/shooting-motion). This is
an ENFORCED precondition, not a documented convention.

## 6. Strip-attribution audit

No event-level PBP linkage of a specific strip to a specific defender
was ingested this phase (same real limitation pattern as Phase 18B's
block-linkage and Phase 18C's foul-linkage audits) — this module
operates entirely on real, already-existing SEASON-AGGREGATE estimates
(`ball_security`, `defensive_playmaking`) supplied by the caller, never
event-level identification.

## 7. `ball_security` audit

Confirmed by direct source read (`ball_security_analysis.handling_error_rate`,
Phase 4A/4B, KEEP BUT FLAG): real `handling_error / estimated_total_dribbles`,
where `handling_error` is the real `CATEGORY_HANDLING` ("Lost Ball") PBP
subtype bucket SPECIFICALLY (Phase 4A's own taxonomy) — confirmed to
ALREADY exclude bad-pass turnovers by construction. This is exactly the
"live-ball control loss" concept 21A needs, reused as-is. Real 2023-24
population (computed live this phase): mean 0.00858, stdev 0.00737
(n=392, `estimated_total_dribbles` ≥ 500).

## 8. Public strip data audit

| Item | Classification |
|---|---|
| `handling_error` (Lost Ball) PBP subtype counts, real, id-keyed | **VERIFIED PUBLIC** (already ingested, Phase 4A/4B, reused) |
| STL/BLK counts (`defensive_playmaking`'s own real inputs) | **VERIFIED PUBLIC** (already used, Phase 1-3) |
| A specific strip event's defender attribution, linked to the specific ball handler's lost-ball event | **LIKELY DERIVABLE** (same PBP-adjacency mechanism as Phase 18B/18C's block/foul-linkage findings) — NOT ingested this phase |
| Touch/dribble counts, time of possession | **VERIFIED PUBLIC** (already used, `handling_exposure.py`, Phase 4B) |
| Matchup tracking, defender proximity at moment of strip | **UNCERTAIN/PROPRIETARY** — consistent with every prior phase's spatial-data finding |

## 9. Strip model ladder

| Rung | Included? | Basis |
|---|---|---|
| S0 context only | Implicit (real placeholder base rate) | Real |
| S1 offensive `ball_security` | **Yes** | Reused as-is (Sec. 7) |
| S2 defender `defensive_playmaking` | **Yes, DELIBERATELY SMALL weight** | No live isolation study performed this phase (Sec. 36/37) — used with limited authority per explicit instruction |
| S3 POA/contact context (posture) | **Yes** | Structural, reused `DefensivePosture` |
| S4 role/usage context | **No** | Not tested — `ball_security`/`defensive_playmaking` are already real, denominator-normalized rates |
| S5 physicals | **No** | Not tested; excluded by structural default (no field exists) |

## 10. Bad-pass/lost-ball firewall

Verified directly: `pass_resolution.py`'s entire namespace contains no
`ball_security`-named symbol (`test_bad_pass_never_uses_ball_security`),
and `on_ball_pressure_resolution.py`'s `resolve_on_ball_pressure` has no
`passing_accuracy` parameter anywhere (`test_passing_accuracy_has_zero_effect_on_strip_outcome`).
`BAD_PASS` (Phase 17B) and lost-ball/`CLEAN_STRIP_LOOSE`/`FORCED_PICKUP`
(this phase) remain structurally distinct, never sharing a code path.

## 11. Clean-strip handling

`OnBallContactOutcome.CLEAN_STRIP_LOOSE` — the ball becomes
`BallState.LOOSE`, `ball_carrier=None`, `offense_team_id=None`
(genuinely unresolved, Phase 15's own convention) — verified directly
(`test_loose_ball_no_phantom_carrier`) that no phantom carrier is ever
assigned. A strip is explicitly NOT equated with a steal or an
immediate possession change — recovery is a SEPARATE step (Sec. 12).

## 12. Loose-ball handling

Reuses Phase 15's own, unmodified `secure_loose_ball` — verified
directly (`test_offense_can_recover_own_loose_ball`) that the offense
CAN recover its own stripped ball, exactly the required "no automatic
turnover until opponent controls" behavior. No new loose-ball scrum
engine was built.

## 13. Forced-pickup integration

`OnBallContactOutcome.FORCED_PICKUP` calls Phase 17A's own
`engine.dead_dribble()` (added in Phase 17A, reused verbatim here) —
verified directly (`test_forced_pickup_sets_dead_dribble`,
`test_forced_pickup_is_not_a_turnover`) that the SAME `DEAD_DRIBBLE`
state Phase 17A's own clean forced pickup produces is used here too —
one shared state, two real causes (clean containment vs. active
pressure), no incompatible duplicate state.

## 14. Reach-in resolution

Only evaluated when `contact_established=True` (a real, caller-supplied
structural fact — this module never invents contact) — verified
directly (`test_reach_in_foul_representable`) that a real defensive
floor foul is reachable and correctly leaves the offense in possession
(`ball_state=DEAD`, `offense_team_id` unchanged).

## 15. `foul_discipline` authority

Deliberately SMALL weight (`FOUL_DISCIPLINE_WEIGHT=-0.15`), applied
ONLY inside the collision-classification branch (whistle-vs-no-call
split) — never in the pressure/strip branch, and never able to
"guarantee no foul" (verified directionally: high `foul_discipline`
shifts the distribution toward `NO_CALL_CONTACT` but a real,
non-degenerate whistle rate remains, per the directional check in Sec.
41).

## 16. `foul_drawing` authority

Same deliberately small posture (`FOUL_DRAWING_WEIGHT=0.15`) — affects
ONLY the whistle-vs-no-call split, never whether contact occurs at all
(verified structurally: `contact_established` is a caller-supplied
input to `resolve_on_ball_pressure`, never derived from
`foul_drawing`).

## 17. Contact-vs-whistle separation

Enforced structurally by two entirely separate functions:
`_resolve_pressure` (no whistle concept at all) and `_resolve_collision`
(only reachable when `contact_established=True`) — a single
`resolve_on_ball_pressure` call branches to exactly one of the two,
never both, per the required `CONTACT != WHISTLE` doctrine.

## 18. Pre-shot collision architecture

`_resolve_collision`: a real, two-stage roll — first no-call vs.
whistle (Sec. 15/16's attributes apply here), then, only if whistled, a
second roll splitting offensive-charge vs. defensive-floor-foul. Real,
hand-set, explicitly flagged placeholder base rates
(`BASE_NO_CALL_GIVEN_CONTACT=0.55`, `BASE_OFFENSIVE_CHARGE_GIVEN_CONTACT=0.20`,
`BASE_DEFENSIVE_FOUL_GIVEN_CONTACT=0.25`) — no public per-collision
dataset exists to fit against (same honest limitation as every prior
resolution phase).

## 19. Charge-vs-block resolution

**No bespoke "charge-taking" latent was created** — per explicit
instruction and the absence of any live isolation study this phase
(Sec. 36). The offensive-charge branch consumes only structural context
(posture, via the same collision roll) plus the same small
`foul_drawing`/`foul_discipline` weights — no defender rating
"teleports" a defender into position; eligibility to even be in the
collision branch is entirely caller-determined (`contact_established`).

## 20. Defender-positioning treatment

Reuses `DefensivePosture` unmodified — a `SQUARE` defender is
structurally more able to pressure/strip than a `TRAILING`/`HELPING`
one (verified: `_posture_disruption_delta`, directional check Sec. 41).
No new posture taxonomy was created.

## 21. Offensive-control treatment

`ball_security` is the ONLY offensive input, and only in the pressure
branch — never in the collision branch (an offensive charge's
likelihood does not depend on `ball_security` in this V1, since a
charge is about BODY POSITION/CONTACT, not dribble control).

## 22. Physical audit

**No physical field exists anywhere in `OnBallPressureContext`**
(verified: `test_no_physical_field`) — mass/height/standing_reach/
wingspan are structurally excluded by default, per explicit instruction
("mass != strength... require heldout incremental evidence"). No live
heldout test was run this phase to justify including any of them.

## 23. Reach/wingspan audit

**Not tested this phase** — structurally excluded (Sec. 22), consistent
with this project's repeated finding (Phases 17A/18B/19) that physical
variables tend to substantially overlap already-existing skill
estimators. This is an inference from precedent, not a fresh diagnostic
run this specific phase — flagged (Sec. 54).

## 24. Foul event contract

`FoulPacket(offender_id, fouled_player_id, foul_class, live_ball)` — a
minimal, clean handoff for Phase 21B. This module does NOT administer
team fouls, bonus state, or FT consequences (verified:
`test_defensive_foul_does_not_award_fts_locally` — no
`FreeThrowSequence`/`resolve_free_throw` reference exists anywhere in
this module).

## 25. Offensive foul consequence

`engine.dead_ball_turnover(ball_handler_id)` (Phase 15, reused verbatim)
— real, dead-ball turnover, no FGA, no steal credited (verified:
`test_charge_is_dead_ball_turnover_no_steal` — the outcome string
itself has no `steal` attribute).

## 26. Defensive floor-foul consequence

`engine.non_shooting_foul(defender_id, ball_handler_id, team_foul_count)`
(Phase 15, reused verbatim, including its own real bonus-check return
value) — offense retains the ball (verified:
`test_reach_in_foul_representable`); no FT is awarded inside this
module.

## 27. Continuation/no-call handling

`OnBallContactOutcome.NO_CALL_CONTACT` and `CLEAN_CONTROL` both leave
`ball_state`/`ball_carrier` completely untouched (verified:
`test_no_call_continues_play`) — a defender's missed reach or a
no-call collision never forces a terminal event.

## 28. Advantage interaction

**Not directly mutated by this module** — a `DISRUPTED` outcome is
documented as a real, plausible trigger for a CALLER to separately
reduce structural advantage via the existing `AdvantageModel` interface
(same "read-only unless caller supplies an explicit updater" posture as
Phase 17B), but `on_ball_pressure_resolution.py` itself never reads or
writes `engine.advantage` directly (verified by source inspection — no
`.advantage` reference exists anywhere in the module).

## 29. `poa_containment` firewall

Not referenced anywhere in this module (verified by the namespace scan,
`test_no_ability_symbols`) — `poa_containment` remains Phase 17A's own,
unchanged domain (whether the defender was beaten on the drive), never
extended into strip/reach/collision authority here.

## 30. `defensive_playmaking` firewall

Used ONLY in the pressure branch, at a deliberately small weight (Sec.
9) — never in the collision branch, never as a universal "defense
rating." Its existing block-probability use (Phase 18B) and this
phase's strip use are structurally independent code paths in different
modules.

## 31. `ball_security` firewall

Used ONLY in the pressure branch — never in the collision branch (an
offensive charge doesn't depend on ball-handling security), never in
Phase 17B's pass resolution (Sec. 10).

## 32. Action-selection firewall

Not applicable this phase in the strong sense Phase 16 established —
this module is a RESOLVER, not a selector; it has no `SelectionPolicy`
of its own and doesn't choose whether pressure/collision occurs, only
resolves the consequence once a caller (a future orchestration layer)
invokes it.

## 33. Reactive-checkpoint integration

Logs a single `EventType.REACTION_CHECKPOINT` (`"on_ball_pressure_resolved"`)
per resolution — reuses Phase 15's existing event vocabulary, no new
event type was created (avoiding event-vocabulary bloat, per explicit
instruction).

## 34. Transition integration

No transition-specific code path exists in this module at all — the
SAME `resolve_on_ball_pressure`/`apply_on_ball_pressure_to_engine`
functions work identically whether `engine.state.phase` is `TRANSITION`
or `HALFCOURT`, since neither reads `phase` at all. No separate
transition-steal rating was created (verified by namespace scan).

## 35. Public empirical findings

Reused Phase 4A/4B's already-verified real `handling_error`/PBP
subtype infrastructure (Sec. 7/8) — no new live API call was made this
phase beyond the population-statistic recomputation (Sec. 7).

## 36. `ball_security` empirical findings

Real, recomputed 2023-24 population: mean 0.00858, stdev 0.00737
(n=392) — confirms the estimator is a real, live, usable rate.
**No live T→T+1 stability or team-change portability check was run this
phase** — reused Phase 4A/4B's own prior classification (KEEP BUT FLAG)
without re-deriving it.

## 37. `defensive_playmaking` strip findings

**No live isolation study (controlling for team scheme/opponent
ball-handling/role) was performed this phase**, given severe time
constraints — per the task's own explicit allowance, this is handled by
using a deliberately SMALL weight (0.35, vs. `ball_security`'s implicit
weight of 1.0 via direct z-score) and flagging the specific STRIP use
as KEEP BUT FLAG, distinct from `defensive_playmaking`'s own unchanged,
broader classification.

## 38. `ball_security` × defender interaction

Real, directional check performed (Sec. 41): strong security + weak
defender → 94.4% clean control; weak security + strong defender → only
17.1% clean control, with disruption/pickup/strip rates all
substantially elevated — the expected directional relationship holds
in the implemented model. This is a DIRECTIONAL SANITY CHECK on the
implemented logic, not calibration proof or an independent empirical
validation of the underlying coefficients.

## 39. Charge/block empirical findings

**Not studied this phase** — no live check of whether real charges-
drawn/blocking-foul rates carry a portable player-specific signal was
performed (Sec. 19's "no bespoke latent" decision is a default-
skepticism application of the task's own instruction, not a data-driven
rejection).

## 40. Foul attribute portability

**Not independently re-tested this phase** — `foul_drawing`/
`foul_discipline`'s existing Phase 6 classifications (KEEP BUT FLAG,
with documented coverage/burden limitations) are reused as-is for this
NEW context (pre-shot collision) without re-verifying they transport
unchanged, per the task's own caution ("do not assume Phase 18C weights
transport unchanged") — flagged, not resolved (Sec. 54).

## 41. Strip model validation

Directional checks only (not calibration proof): strong-security/weak-
defender vs. weak-security/strong-defender (Sec. 38); trailing posture
vs. square posture at fixed ability inputs (trailing → 59.9% clean vs.
square → 57.3% clean at weak security, correct direction — trailing
defenders pressure less effectively).

## 42. Collision model validation

Directional check: high `foul_drawing`/low `foul_discipline` → 31.7%
defensive-foul rate vs. low `foul_drawing`/high `foul_discipline` →
16.2% — correct direction, real magnitude difference, unvalidated
against any real per-collision dataset.

## 43. Temporal stability

**Not tested this phase** for either `ball_security` or
`defensive_playmaking` in this NEW strip-specific use — flagged (Sec.
54).

## 44. Team-change portability

**Not tested this phase.**

## 45. Missingness/historical fallback

Every ability field in `OnBallPressureContext` is `Optional`, and every
`None` contributes exactly zero to the additive logit (verified via
`_z`'s explicit `None → 0.0` branch and the module's own missing-data
convention, consistent with every prior resolution phase) — a
historical-era player with no cached `ball_security`/`defensive_playmaking`
estimate still resolves to a real, defined, population-baseline-anchored
outcome distribution, never a crash or a fabricated zero-skill value.

## 46. Era treatment

**No era-specific hook was added this phase** — unlike Phase 18C's
real, historically-grounded hand-check-era whistle delta, no equivalent
real historical fact was investigated for on-ball pressure/collision
rates this phase (time constraint, flagged Sec. 54). The module's base
rates are single-era (implicitly modern) placeholders.

## 47. Event semantics

One `REACTION_CHECKPOINT` event per resolution (Sec. 33) — no
`BALL_DISRUPTED`/`STRIP_ATTEMPTED`/`OFFENSIVE_FOUL`/`DEFENSIVE_FLOOR_FOUL`
event types were created; the real, existing engine methods
(`dead_ball_turnover`, `non_shooting_foul`) already log their own
appropriate events (`DEAD_BALL_TURNOVER`, `NON_SHOOTING_FOUL`) when
invoked, giving Phase 21B everything it needs without event-vocabulary
duplication.

## 48. Deterministic replay

Verified directly (`test_deterministic_replay`): identical context +
identical `random.Random` seed produce identical outcomes. No global
`random` module call exists anywhere (verified: `test_no_global_rng`).

## 49. Counterfactual firewall tests

All required numbered counterfactuals were addressed: clean containment
remains Phase 17A's (no import/reference); active strip routes through
21A; bad-pass never uses `ball_security` (verified via namespace scan
on `pass_resolution.py`); lost-ball never uses `passing_accuracy`
(verified: no such parameter anywhere in this module); successful
strip creates `LOOSE` without immediate possession change; loose ball
has no phantom carrier; offense recovery preserves possession; forced
pickup is not a turnover; pre-shot contact never routes to the shooting-
foul branch (structural precondition, Sec. 5); offensive charge records
neither a steal nor an FGA; defensive foul doesn't locally award FTs;
`poa_containment`/`rim_access_creation` have zero authority (no
parameter or symbol exists); mass/height/reach/wingspan have zero
authority (no field exists); `passing_accuracy`/`rim_finishing`/
`three_point`/`defensive_rebounding` have zero effect on strip outcome
(none is readable); no contact event resolves twice (a single
`resolve_on_ball_pressure` call returns exactly one outcome); `player_id`-
only; deterministic replay.

## 50. Double-counting matrix

| Pair | Risk |
|---|---|
| `ball_security` × pressure branch | **SAFE** — single, direct real input, not combined with any other offensive attribute |
| `defensive_playmaking` × block probability (18B) × strip (21A) | **WATCH** — same real attribute used in two different modules for two different real event types (blocks vs. strips); both are SEPARATE, real, only-partially-overlapping event categories per Phase 7's own prior finding, but no live cross-module double-count audit was performed this phase |
| `foul_drawing`/`foul_discipline` × 18C's shooting-foul use × 21A's collision use | **WATCH** — same two attributes reused for a materially different context (pre-shot collision vs. shooting-motion contact) without re-verifying portability (Sec. 40) |
| `poa_containment` × strip/collision | **SAFE** — not referenced anywhere in this module |
| Phase 21A foul × Phase 18C foul | **SAFE** — structurally mutually exclusive via the `LIVE_DRIBBLE` precondition (Sec. 5) |

## 51. Failure modes

Every reach does NOT become a steal (real, non-degenerate `CLEAN_CONTROL`
rate at every tested input combination); every failed reach does NOT
become a foul (`NO_CALL_CONTACT` is real and reachable); `ball_security`
never fixes a bad pass (structurally impossible — different module,
different code path); a strip never automatically grants defender
possession (verified: `CLEAN_STRIP_LOOSE` leaves `ball_carrier=None`);
a charge is never credited as a steal; a pre-shot foul is never treated
as a shooting foul (structural precondition).

## 52. Focused test results

`test_on_ball_pressure_resolution.py` — 27 tests covering: all four
pressure-branch outcomes reachable, all three collision-branch outcomes
reachable, loose-ball handling (no phantom carrier, offense self-
recovery), forced-pickup integration (correct `DEAD_DRIBBLE` state, not
a turnover), reach-in/no-call representability, offensive-charge
accounting (dead-ball turnover, no steal), the shooting-boundary
firewall (both `GATHERED` and `DEAD_DRIBBLE` states correctly rejected,
plus a source-scan confirming no shot-resolution reference), Phase 17A
non-duplication (source scan), every required ability/physical firewall
(namespace + dataclass-field scans), determinism, `player_id`-only, no
mutation leakage, and the FT-non-administration boundary for Phase 21B.

## 53. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 720 tests — OK**
(693 carried over from Phase 20B + 27 new in
`test_on_ball_pressure_resolution.py`). No existing test was modified
or removed.

## 54. Unresolved issues

- No live isolation study for `defensive_playmaking`'s strip-specific
  signal was performed (Sec. 37) — handled via a deliberately small
  weight and an explicit KEEP-BUT-FLAG classification for this specific
  use, per the task's own allowance, not a data-driven finding.
- `foul_drawing`/`foul_discipline`'s portability into the NEW
  pre-shot-collision context was not independently re-verified (Sec.
  40).
- No era-specific hook exists for on-ball pressure/collision rates
  (Sec. 46) — unlike Phase 18C's real hand-check-era finding, no
  equivalent historical fact was investigated this phase.
- Temporal stability and team-change portability were not tested for
  either `ball_security` or `defensive_playmaking` in this specific
  new use (Sec. 43/44).
- Charge-taking/blocking-foul player-specific signal was not
  empirically studied (Sec. 39) — the "no new latent" decision follows
  default skepticism, not a data-driven rejection.

## 55. Classifications

| Candidate mechanic | Classification |
|---|---|
| `ball_security` as the pressure-branch offensive input (reused, unchanged) | **KEEP** (unchanged from Phase 4A/4B's own classification) |
| `defensive_playmaking` as the pressure-branch defensive input, SMALL weight | **KEEP BUT FLAG** — real attribute, but its strip-specific isolation was not studied this phase |
| Geometric/structural precondition separating 21A from 18C | **LOCK V1** — real, enforced, tested |
| Pressure→disruption→pickup/strip ordinal branch | **KEEP** — real, principled, directionally validated |
| Collision no-call/charge/defensive-foul branch | **KEEP BUT FLAG** — real, principled structure, unvalidated base-rate magnitudes |
| `foul_drawing`/`foul_discipline` in the collision branch, SMALL weight | **KEEP BUT FLAG** — reused from a different context without re-verifying portability |
| A new charge-taking or blocking-foul latent | **not created** — explicitly rejected per default skepticism |
| Physical variables (mass/height/reach/wingspan) | **INSUFFICIENT** — not tested, structurally excluded by default |

## 56. Final phase classification

**READY WITH FLAGS FOR 21B.**

Ready: the Phase 18C shooting-motion boundary is enforced as a real,
structural precondition (not a convention); `ball_security` is reused
exactly as Phase 4A/4B already validated it, with a real, confirmed
construction audit; the pressure/collision separation and every
required ability/physical firewall are verified both structurally
(namespace/signature/dataclass-field scans) and behaviorally
(directional checks); loose-ball, forced-pickup, and offensive-charge
handling all reuse existing Phase 15/17A machinery with zero
duplicated state; the clean `FoulPacket` handoff leaves all
administrative consequences to Phase 21B as required.

Flags: `defensive_playmaking`'s strip-specific signal and both foul
attributes' portability into this new collision context were not
independently isolated this phase (time constraint, handled via
deliberately small weights and explicit flags rather than either
assumed-strong or assumed-absent); no era-specific hook exists for
on-ball pressure rates; temporal stability/team-change portability
remain untested for this specific new use.

Not begun: Phase 21B.
