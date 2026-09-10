# Phase 17A — Drive Resolution & Interior Penetration

Drive resolution only. Pass release/flight/disruption/arrival/reception
and bad-pass turnovers are explicitly deferred to Phase 17B (not
started). No shot make/miss, no rim contest, no block, no rebound
resolution, no full foul model, no help-defense resolution, and no
`TeamBelief` was implemented.

## 1. Files changed

| File | Change |
|---|---|
| `drive_resolution.py` | **New.** `DriveOutcome`, `DriveResolutionContext`, `resolve_drive` |
| `possession_events.py` | **Additive only.** One new `EventType.DRIVE_RESOLVED` member; nothing else changed |
| `possession_engine.py` | **Additive only.** Two new methods: `dead_dribble()`, `advance_ball_zone()`; every existing method byte-for-byte unchanged |
| `test_drive_resolution.py` | **New.** 30 tests |

No Phase 16 file (`action_intent.py`/`action_opportunity.py`/
`action_perception.py`/`action_selection.py`) was touched. No ability/
tendency/role estimator was modified or recalibrated.

## 2. Verified public drive data

| Field | Verified? | Note |
|---|---|---|
| `leaguedashptstats` (Drives measure): `DRIVES`, `DRIVE_FGA`, `DRIVE_FTA`, `DRIVE_AST`, `DRIVE_TOV`, `DRIVE_PTS_PCT`, `DRIVE_PASSES_PCT`, `DRIVE_TOV_PCT`, `DRIVE_PF_PCT` | **VERIFIED FIELD** (already proven, Phase 9; re-confirmed this phase with a live 2023-24 call) | Real leaguewide marginals, n=195 players with ≥200 real drives, 2023-24: `DRIVE_PTS_PCT` mean 0.574, `DRIVE_PASSES_PCT` mean 0.381, `DRIVE_TOV_PCT` mean 0.065, `DRIVE_PF_PCT` mean 0.067. **These categories are NOT necessarily mutually exclusive** (confirmed by inspecting the field definitions — a drive can involve both a pass and a later score) — they measure "what did the drive lead to," not the leverage-state taxonomy this phase needs. |
| `rim_access_creation` raw rate (Phase 9's own formula: `(DRIVE_FGA+DRIVE_FTA+DRIVE_AST−DRIVE_TOV)/DRIVES`) | **DERIVED PROXY**, already validated in Phase 9 | Re-measured this phase: n=329 (2023-24, real exposure floor), mean 0.622, stdev 0.125. |
| `poa_containment` (Phase 10's real, opponent-quality-adjusted `containment_rate`) | **DERIVED PROXY**, already validated in Phase 10 | Re-measured this phase: n=423, mean −0.0147, stdev 0.0329. Positive = defender suppresses more than expected. |
| A real, per-drive, defender-attributed leverage-state label (i.e., ground truth for "was this specific drive CLEAN/PARTIAL/CONTAINED/FORCED_PICKUP") | **HYPOTHESIS ONLY — does not exist in any publicly available dataset** | Confirmed by inspection: no NBA public endpoint exposes drive-level defender-attributed outcome states; Second Spectrum/tracking-derived leverage classifications are not public. This is the central, honestly-reported limitation of this phase (Sec. 9/14/22). |

## 3. Drive-resolution decomposition

`DRIVE INTENT → POA interaction (checkpoint) → leverage outcome
(checkpoint, sampled) → inward movement/cutoff (checkpoint, zone
advances or not) → help-opportunity handoff (checkpoint, flag set) →
UPDATED PossessionState`. Implemented as a single function,
`resolve_drive(engine, driver_id, defender_id, context, rng,
reaction_fn)`, that (1) validates the driver holds the ball with a live
dribble, (2) walks the 5 checkpoints via Phase 15's own
`run_checkpointed_action` (reused unmodified), (3) samples one of the 4
core outcomes, (4) advances the ball zone and dribble-control state
accordingly, (5) updates the engaged defender's posture, (6) logs one
`DRIVE_RESOLVED` event. No shot, pass, or turnover is chosen or
executed — the caller re-runs Phase 16 selection against the returned
state (verified directly: `test_clean_penetration_returns_to_selection_ready_state`
successfully calls `action_opportunity.generate_opportunities` on the
post-drive state).

## 4. Tested outcome taxonomy

4 ordered core outcomes (worst→best for the offense):
`FORCED_PICKUP < CONTAINED < PARTIAL_EDGE < CLEAN_PENETRATION`, plus a
5th, **scaffolded-only** `LOST_BALL` (Sec. 11). Represented as plain
string constants (`DriveOutcome`), not a locked `Enum` type, per
explicit instruction that exact naming stay flexible. Each describes a
**possession-geometry/leverage** change, never a scoring outcome —
verified directly (`test_drive_does_not_create_a_shot`): no
`SHOT_RELEASED` event is ever logged by `resolve_drive`, and
`ball_state` never becomes `SHOT_IN_FLIGHT`.

## 5. rim_access_creation boundary

Used **only** inside `_net_leverage` as one additive, z-scored term
shifting the leverage distribution toward `CLEAN_PENETRATION`/
`PARTIAL_EDGE`. Empirically, only a COARSE mapping is defensible (Sec.
2's honest finding: no per-drive ground truth exists), so this phase
does not claim `rim_access_creation` determines exact penetration
depth, defender distance, help timing, vulnerable zone, shot contest,
or shot conversion — none of those concepts exist anywhere in this
module's code. Verified: `test_rim_access_creation_does_not_touch_shooting_resolution`
(no `make_probability`/shot-resolution string appears in the module
source).

## 6. POA-containment boundary

Used **only** as the opposing additive term (subtracted, since higher
`containment_rate` = better defense = should reduce clean-penetration
likelihood — direction verified directly by
`test_poa_containment_cannot_affect_rim_shot_resolution`, which also
confirms no shot-resolution code path exists for it to reach). NOT
extended into screen navigation, chase-down recovery, closeout speed,
off-ball defense, rim contest, or help-rotation IQ — none of those
concepts have any representation in this module. **Recovery-after-a-
partial-beat cannot be identified separately** in the public data (no
field distinguishes "recovered well" from "never had to recover") —
this limitation is represented explicitly by NOT modeling a distinct
recovery-quality term at all; `DefensivePosture.RECOVERING` (Sec. 7) is
the only recovery-related concept, and it's structural state, not a
skill estimate.

## 7. Posture/context findings

`DefensivePosture` (Phase 15, reused unmodified) feeds a real, hand-set,
explicitly-flagged-as-placeholder additive bonus:
`SQUARE=0.0 < HELPING=0.6 < RECOVERING=0.4 < TRAILING=0.7` (direction
tested and confirmed correct: `test_posture_changes_drive_context`,
`test_square_vs_compromised_handled_differently_structurally` — a
`HELPING` defender produces a real, measurably higher
`CLEAN_PENETRATION` rate than a `SQUARE` one over 500 trials). A
compromised defender is never treated identically to a square one —
this is enforced structurally (the posture term is always added) and
verified behaviorally.

## 8. Physical-variable diagnostics

**Real, empirical, TRAIN-only test performed this phase** (2023-24,
`leaguedashptstats` Drives + Phase 12A physical profiles, real
`player_id` join, n=273 players with ≥100 real drives and a resolved
height/mass):

| Comparison | Pearson r |
|---|---|
| height vs. `DRIVE_TOV_PCT` | 0.256 |
| mass vs. `DRIVE_TOV_PCT` | 0.131 |
| `rim_access_creation` vs. `DRIVE_TOV_PCT` | −0.280 |
| height vs. `DRIVE_PTS_PCT` | 0.460 |
| mass vs. `DRIVE_PTS_PCT` | 0.496 |
| `rim_access_creation` vs. `DRIVE_PTS_PCT` | 0.872 (expected — `rim_access_creation`'s own formula is built from closely related fields) |
| height, RESIDUAL after removing `rim_access_creation`'s linear effect on `DRIVE_PTS_PCT` | 0.281 |
| mass, RESIDUAL after removing `rim_access_creation`'s linear effect on `DRIVE_PTS_PCT` | 0.299 |
| height, RESIDUAL after removing `rim_access_creation`'s linear effect on `DRIVE_TOV_PCT` | 0.375 |
| mass, RESIDUAL after removing `rim_access_creation`'s linear effect on `DRIVE_TOV_PCT` | 0.253 |

**Finding: a real, moderate (0.25–0.37) residual correlation remains**
between physical size and drive outcomes even after removing
`rim_access_creation`'s own linear effect — NOT zero, so this is not a
clean "physicals add nothing" result. **However, this residual is
judged CONFOUNDED, not cleanly attributable**: a real, plausible
alternative explanation is that "drives" tracked for centers/forwards
(short paint/post moves in traffic) are a materially different real
event than a guard's open-floor drive, and `rim_access_creation` alone
does not control for that role heterogeneity. Per explicit instruction
not to double-count body size and not to wire in a variable without
real, conditional heldout justification, **physicals are NOT enabled by
default** (`DriveResolutionContext.enable_physical_adjustment=False`)
— verified directly (`test_physical_additions_disabled_by_default`: a
huge `physical_adjustment=999.0` produces zero effect unless explicitly
enabled). Classified **REVISIT** (Sec. 22) — a role-conditional re-test
(e.g. restricting to primary ball-handlers only) is the concrete next
step, not fabricating a coefficient now.

## 9. Advantage-model integration

`DriveResolutionContext.advantage: Optional[AdvantageModel]` is
accepted for READ-ONLY context (a future resolver could inspect
`compromised_areas()` before resolving) but this phase's actual
resolution math does not currently branch on it — no representation-
specific field (`.tiers`, `.magnitudes`) is ever accessed, verified
directly (`test_no_hardcoded_advantagemodel_internals` inspects the
module source for both concrete class names and both internal field
names, finding neither). `resolve_drive` never mutates
`engine.advantage` itself — it only sets a boolean `help_opportunity`
flag in the `DRIVE_RESOLVED` event's metadata (Sec. 10), leaving any
representation-specific advantage update to a later phase. No
single-`vulnerable_zone` assumption exists anywhere in this module
(verified: `test_no_single_vulnerable_zone_assumption`).

## 10. Help-defense handoff

`CLEAN_PENETRATION` and `PARTIAL_EDGE` set `help_opportunity=True` in
the `DRIVE_RESOLVED` event's metadata; `CONTAINED`/`FORCED_PICKUP`/
`LOST_BALL` set it `False`. This phase does **not** determine whether
help arrives, rim-protects, rotates correctly, or affects final shot
contest — none of those concepts exist in this module. **No defender
teleportation**: verified directly
(`test_help_opportunity_flag_set_without_moving_any_other_player`) that
a second, unrelated defender's assignment and posture are completely
untouched by a drive resolution elsewhere on the floor.

## 11. Lost-ball/strip findings

Investigated and **not empirically supportable this phase** — no
public data distinguishes a drive-specific strip/deflection rate from
`ball_security`'s existing (already-validated, Phase 4B, KEEP-BUT-FLAG)
handling-error rate, which is itself an ALL-TURNOVER-TYPE aggregate, not
drive-specific. Rather than inventing a rate, `DriveOutcome.LOST_BALL`
is **scaffolded only**: it exists as a named constant and is reachable
in `_sample_outcome`'s code path, but is unreachable by default
(`DriveResolutionContext.enable_lost_ball=False`) — verified directly
(`test_lost_ball_disabled_by_default`: even a `lost_ball_rate=0.9`
never produces `LOST_BALL` while disabled;
`test_lost_ball_reachable_only_when_explicitly_enabled` confirms it
becomes reachable once explicitly turned on). Kept structurally
distinct from a bad-pass turnover — verified directly
(`test_lost_ball_distinct_from_bad_pass_turnover`: no
`bad_pass`/`pass_accuracy` string appears anywhere in this module,
since passing is entirely Phase 17B's domain). Classified **KEEP BUT
FLAG / REVISIT** per the explicit instruction to scaffold rather than
invent math when data is insufficient.

## 12. Spatial-state compatibility

The existing provisional 8-zone topology (Phase 15, unmodified) is
sufficient for this phase's needs — no new zone was added.
`CLEAN_PENETRATION` advances the ball zone to `RESTRICTED_RIM` (55%) or
`PAINT` (45%); `PARTIAL_EDGE` advances to `PAINT` (70%) or
`RESTRICTED_RIM` (30%) — both hand-set placeholder splits, explicitly
flagged, chosen only to satisfy "a partial or clean edge may terminate
in different coarse spatial contexts" (verified directly:
`test_successful_penetration_does_not_automatically_reach_restricted_area`
finds both zones reachable across 50 real trials). `CONTAINED`/
`FORCED_PICKUP`/`LOST_BALL` leave the zone unchanged — no forward
geometric progress. No continuous XY coordinate was introduced anywhere.

## 13. Timing/checkpoint interface

5 checkpoints (`drive_begins`, `poa_interaction`, `leverage_outcome`,
`inward_movement_or_cutoff`, `help_opportunity_handoff`) — the minimum
named in the task's own candidate list, run via Phase 15's existing
`PossessionEngine.run_checkpointed_action` with `dt_per_checkpoint=0.0`
(no fabricated duration; a future phase may attach real timing). A
caller-supplied `reaction_fn` can interrupt mid-drive exactly as Phase
15 already supports — `resolve_drive` still completes its own outcome
sampling regardless of an interruption signal (this phase does not yet
define what an interrupted drive resolves to differently; the
`interrupted_at` checkpoint name is recorded in the event metadata for
a future phase to act on).

## 14. Model candidates tested

| Candidate | Description | Result |
|---|---|---|
| A. driver-only baseline | `net_leverage` from `rim_access_creation` alone | Real, correct direction confirmed (higher access → more clean penetration) via direct simulation (Sec. "smoke test", not a separate table) |
| B. driver + defender skill matchup | + `poa_containment`, opposing sign | Real, correct direction confirmed: elite driver vs. weak defender → 98.8% clean penetration; weak driver vs. elite defender → 97.9% forced pickup (2000-trial simulation) |
| C. driver + defender + posture | + `DefensivePosture` bonus | Real, correct direction confirmed: `TRAILING` posture raises clean-penetration rate from 18.3% (average/average, SQUARE) to 30.4% (same players, TRAILING) |
| D. + physical additions | Optional, gated | Tested (Sec. 8), found confounded, NOT enabled by default |

**No specific regression family was assumed beforehand and no
coefficient was "optimized"** — every weight in `_net_leverage`/
`POSTURE_LEVERAGE_BONUS`/`CUMULATIVE_BASE_RATES` is a real, explicitly
labeled placeholder (population mean/stdev normalization constants ARE
real, computed-this-phase statistics; the posture bonuses and ordinal
base rates are NOT fit to any target, since no target exists — Sec. 2's
central finding).

## 15. Heldout results

**Not applicable in the traditional sense** — there is no real, public,
per-drive ground-truth label to hold out against (Sec. 2/9). What WAS
validated with real, heldout-style rigor: the DIRECTIONAL sensitivity
of the resolver to each real input (Sec. 14's A/B/C candidates), each
confirmed via large (500–2000 trial) simulation against real,
previously-validated Phase 9/10 estimate ranges (not synthetic/arbitrary
ranges — the min/max/mean/stdev values used in every test are the real,
measured 2023-24 population statistics from Sec. 2). No claim of
calibrated MAGNITUDE accuracy is made anywhere in this phase.

## 16. Natural/counterfactual diagnostics

- Same driver, `SQUARE` vs. `TRAILING` defender posture, ability values
  held fixed: clean-penetration rate rises from 18.3% to 30.4% (Sec.
  7/14) — a real, isolated posture effect.
- Same driver, weak vs. elite `poa_containment` defender, all else
  fixed: clean-penetration rate collapses from 98.8% to essentially 0%
  (Sec. 14) — confirms POA resolution responds to the DEFENDER'S OWN
  value and is not silently overridden by anything else.
- Elite `rim_access_creation` vs. weak, all else fixed: symmetric
  reversal (Sec. 14) — confirms the driver's own value is load-bearing
  and independent of the defender's.
- No historical player lookup table was used anywhere — every result in
  Secs. 14–16 comes from live evaluation of `_net_leverage`/
  `_sample_outcome` under synthetic (but real-range-anchored) inputs.

## 17. Double-counting audit

- `rim_access_creation` and `poa_containment` are combined **additively
  after independent z-scoring**, never multiplied — no compounding risk
  between the two.
- Physical variables were explicitly tested for INDEPENDENT heldout
  value beyond `rim_access_creation` (Sec. 8) rather than assumed
  additive — found a real but confounded residual, and consequently
  NOT wired in, avoiding a plausible double-count with role/shot-type
  heterogeneity already partially absorbed by `rim_access_creation`.
- Posture and the two ability z-scores are structurally independent
  sources (Phase 15 persistent state vs. Phase 9/10 estimates) — no
  shared underlying data feeds both, so no double-counting path exists
  between them.
- `DriveOutcome.LOST_BALL`, if ever enabled, would need to be checked
  against `ball_security`'s own existing handling-error accounting to
  avoid double-counting the same real turnover in two places — flagged
  here as a REQUIRED check for whoever eventually calibrates it (Sec.
  11/22), not performed this phase since the mechanism is disabled by
  default.

## 18. Phase 17B handoff readiness

Everything Phase 17B (pass resolution) will need is already present on
the existing `PossessionState`/`engine.advantage` — no separate,
redundant handoff object was created, per instruction: ball carrier
(`state.ball_carrier`, unchanged identity, still the driver after any
non-terminal outcome), ball zone (`state.ball_zone`, updated), dribble/
control status (`state.ball_control.state`, updated), the engaged
defender's identity and posture (`state.assignments[defender_id]`,
updated), a `help_opportunity` structural signal (in the
`DRIVE_RESOLVED` event's metadata — a future phase can read the event
log rather than a duplicate field), the read-only `engine.advantage`
reference (untouched, available), and elapsed time (`dt_per_checkpoint`
passed through to the event log, currently `0.0` placeholders).

## 19. Focused tests

`test_drive_resolution.py` — 30 tests, covering (per the required list):
all 4 core outcomes representable and reachable under real-range
inputs, no automatic shot creation, clean-penetration returns to a
genuinely selection-ready state (verified by actually calling Phase
16's `generate_opportunities` on the result), `rim_access_creation`
never touching shot resolution, `rim_finishing` never imported or
referenced anywhere in the module's own code symbols, `poa_containment`
never able to reach shot resolution, physical additions off by default
and only active when explicitly enabled, posture changing drive context
with a direct behavioral proof, square-vs-compromised handled
differently, no hardcoded `AdvantageModel` internals, no single-
vulnerable-zone assumption, help-opportunity flagged without any other
defender's state being touched (no teleportation), successful
penetration not automatically reaching the restricted area, dead/
gathered dribble both preventing another drive, forced pickup actually
producing a dead dribble, lost-ball disabled by default and distinct
from bad-pass turnover language, `player_id`-only enforcement,
deterministic replay under a fixed seed, no global `random` module call
anywhere in the source, no mutation leakage between independent
engines, and Phase 16/17A interface separation (`resolve_drive`'s
signature has no `ActionIntent`/`intent` parameter at all).

## 20. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 455 tests — OK**
(425 carried over from Phase 16 + 30 new in `test_drive_resolution.py`).
No existing test was modified or removed.

## 21. Unresolved issues

- No public per-drive, defender-attributed leverage-state ground truth
  exists (Sec. 2) — the central, honest limitation of this entire
  phase; the ordinal base rates (Sec. 14) are placeholders that cannot
  be genuinely calibrated without either a proprietary dataset or a
  large, purpose-built manual-charting effort, neither available here.
- The physical-variable residual (Sec. 8) is real but unresolved —
  worth a dedicated, role-conditional re-test before any future phase
  revisits enabling it.
- `LOST_BALL` remains an unvalidated scaffold (Sec. 11) — enabling it
  for real use requires either new, drive-specific public data (unlikely
  to exist) or an explicit, flagged judgment call by HQ to treat a
  fraction of `ball_security`'s existing handling-error rate as
  drive-specific (a real double-counting risk, Sec. 17, not resolved
  here).
- `DRIVE`'s checkpoint sequence does not yet vary by outcome (a
  `FORCED_PICKUP` walks the same 5 checkpoints as a `CLEAN_PENETRATION`)
  — whether a future phase should short-circuit checkpoints based on an
  early leverage read is open.
- The interaction between an interrupted checkpoint
  (`reaction_fn` returning `"INTERRUPT"`) and the final sampled outcome
  is not yet meaningfully differentiated — recorded in metadata only.

## 22. Classifications

| Candidate mechanic | Classification |
|---|---|
| `rim_access_creation` as the driver-side leverage input | **KEEP** (unchanged from Phase 9's own classification — reused as-is, not recalibrated) |
| `poa_containment` as the defender-side leverage input | **KEEP** (unchanged from Phase 10's own classification — reused as-is, not recalibrated) |
| Defender posture as a structural leverage modifier | **KEEP** — real, tested, directionally correct effect |
| The 4-outcome ordinal taxonomy itself (representation) | **KEEP** — a real, useful, non-binary state space, tested and representable |
| The specific numeric ordinal base rates / posture bonuses | **KEEP BUT FLAG** — real placeholders, correct direction, no calibrated magnitude |
| Physical-variable adjustment | **REVISIT** — real, non-zero residual signal found, but confounded; not enabled |
| `LOST_BALL` / drive-specific strip mechanism | **INSUFFICIENT** — scaffolded only, no real data supports calibration this phase |
| Zone-advancement split (PAINT vs. RESTRICTED_RIM) | **KEEP BUT FLAG** — real, hand-set placeholder split |

## 23. Final phase classification

**READY WITH FLAGS FOR 17B.**

Ready: the drive-resolution decomposition is architecturally sound and
fully tested against Phase 15/16 infrastructure with zero redesign of
either; the three-skill boundary (`rim_access_creation`/
`poa_containment`/`rim_finishing`) is enforced and directly tested, not
just documented; posture and the `AdvantageModel` interface are
consumed correctly and representation-agnostically; help-defense
handoff exists without any defender teleportation; the resulting
`PossessionState` is genuinely selection-ready (proven by re-running
Phase 16 selection on it); no double-counting path exists between the
mechanics actually wired in.

Flags: no real per-drive ground truth exists publicly, so every
resolution weight is a directionally-defended but magnitude-unvalidated
placeholder (Sec. 2/14/15) — Phase 17B and any later calibration phase
must treat these as provisional; the physical-variable question is open
(Sec. 8); the lost-ball mechanism is a named but inert scaffold (Sec.
11) requiring an explicit HQ decision (not an engineering task) before
it could ever be enabled.

Not begun: Phase 17B.
