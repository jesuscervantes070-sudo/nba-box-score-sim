# Phase 18B — Interior/Rim Shot Resolution + Blocks

Resolves an already-selected interior `ShotIntent` (rim + floater
only), on an explicitly UNWHISTLED attempt. No fouls, whistles, free
throws, and-ones, charges, rebound winner, box-outs, putback
generation, transition, or `TeamBelief` were implemented.

## 1. Files changed

| File | Change |
|---|---|
| `interior_shot_resolution.py` | **New.** `InteriorShotFamily`, `InteriorDefenderContext`, `geometric_block_eligibility`, `block_probability`, `unblocked_make_probability`, `InteriorShotContext`, `InteriorShotOutcome`, `resolve_interior_shot`, `apply_interior_shot_to_engine` |
| `test_interior_shot_resolution.py` | **New.** 38 tests |

**Zero changes to any existing file** — this phase reuses Phase 15's
`block_retained_by_offense`/`block_secured_by_defense`/`begin_shot`/
`resolve_shot_made` and Phase 18A's `resolve_shot_missed_pending_rebound`
verbatim, unlike every prior resolution phase which needed 1-2 small
additive engine methods. No ability/tendency/role estimator was
modified.

## 2. Inspected existing interfaces

Confirmed directly (source reads, not assumption): Phase 15's
`block_retained_by_offense`/`block_secured_by_defense` already exist
and already implement exactly the ball-state distinctions this phase
needs (offense retains vs. genuinely-unresolved `LOOSE`) — reused
verbatim, no new engine method required. Phase 18A's
`resolve_shot_missed_pending_rebound` is already shot-family-agnostic
— reused verbatim for interior misses too. The existing 8-zone topology
(`PAINT`, `RESTRICTED_RIM`) already distinguishes rim from floater
geometry — no new zone needed. `DefensivePosture`
(`SQUARE`/`RECOVERING`/`TRAILING`/`HELPING`) already covers every
primary/secondary posture concept this phase needs — no new taxonomy
was introduced (Gemini's exact posture taxonomy was NOT imported, per
instruction, since the repo's existing 4-state enum already suffices).

## 3. Selected Phase 18B scope

`RIM` (restricted-area) and `FLOATER` (paint non-RA) only. No dunk/
layup/hook/alley-oop/putback skill was created (Sec. 19). No mass
effect (Sec. 21). No contact/whistle mechanics (explicitly Phase 18C's
job — this module operates on an "explicitly unwhistled" attempt by
design, per instruction).

## 4. `rim_finishing` construct audit

`shot_zone_estimation.py` (Phase 5, LOCK V1): real, pooled Restricted-
Area zone FGM/FGA — confirmed by direct source read to NOT condition on
contest, assisted status, dunk/layup mix, transition, defender
identity, rim-protector quality, physical size, or shot clock. **A
historical composite, not a context-neutral pure finishing latent** —
same finding pattern as Phase 18A's `three_point`/`midrange` audit.
Reused as-is; no compensating context correction was invented on top
(the same double-counting caution from 18A applies here).

## 5. `floater_short_mid` construct audit

Same module, Paint-Non-RA zone (Phase 5, **KEEP BUT FLAG** — a real,
weaker classification than `rim_finishing`, per Phase 5's own original
report: nested train/test parameter mismatch). Same pooled-composite
contamination profile as `rim_finishing`. Sample size/portability are
real, already-documented weaknesses (Phase 5's own report) — not
re-litigated here, only respected (this module does not treat
`floater_short_mid` as more reliable than its own existing
classification implies).

## 6. `rim_protection` construct audit (critical empirical task)

Confirmed by direct source read (`rim_protection_analysis.py`):
`suppression_rate` — the actual estimator input — is the real,
NBA-computed `PLUSMINUS` field from `leaguedashptdefend` (Less Than
6Ft): `(expected_fgm − actual_fgm) / fga_covered`, a pure real
opponent-FG%-suppression signal. `blk_per36` is loaded onto the same
row as a SEPARATE, diagnostic-only field, and Phase 7's own original
report already found only a real, PARTIAL (25-42%) shared-variance
overlap between rim-suppression and raw BLK rate — HQ's concern that
`rim_protection` "may combine block production + suppression + exposure
+ physical length + role" is **partially confirmed** (suppression and
blocks are correlated but genuinely distinct, per Phase 7's own
evidence) and **partially not** (`rim_protection` itself is NOT a block
count — it's a suppression rate; block volume lives in the separate
`defensive_playmaking` attribute). Real, additional finding this phase
(Sec. 21): defender standing reach correlates r=0.407 (n=441,
2023-24) with `rim_protection`'s own suppression rate — real,
substantial evidence that physical length is already absorbed.

**Chosen treatment: option D** — split derived components using TWO
ALREADY-EXISTING real attributes (`rim_protection` for unblocked
conversion suppression, `defensive_playmaking` for block probability),
rather than reusing one latent for both branches (which would risk
double-counting the same real suppression signal) and rather than
inventing a new `block_ability` attribute (explicitly prohibited absent
overwhelming evidence, which was not found).

## 7. Verified public data

| Item | Classification | Finding |
|---|---|---|
| Restricted-area / paint-non-RA attempts and makes | **VERIFIED PUBLIC** (already used, Phase 5) | Reused unchanged |
| Rim-defended FGA / opponent DFG% (`leaguedashptdefend`) | **VERIFIED PUBLIC** (already used, Phase 7) | Reused unchanged — real opportunity exposure, not raw minutes |
| Block events with blocker identity (`playbyplayv3`) | **DERIVABLE, not directly VERIFIED as a clean joined field** | Confirmed live: a real block appears as a row with `description` containing `"BLOCK"` and a real, correct `personId` for the BLOCKER — but `actionType` is blank/empty on these rows (a real, confirmed data-quality quirk), and linking a block to its specific shooter/shot-subtype requires matching it to the immediately-preceding "Missed Shot" row by game/period/clock ordering — mechanically possible, NOT ingested this phase (a real, non-trivial ingestion task, deferred per instruction to keep calibration-identification separate from simulation-state defender identity, Sec. 12) |
| Shot subtype descriptors ("Layup Shot", "Turnaround Hook Shot", "Dunk Shot" when present) | **VERIFIED PUBLIC** (`playbyplayv3`'s real `subType` field, confirmed live) | Available in principle for a future dunk/layup split; not used this phase (Sec. 19) |
| Dunk/layup FGA/FG% split by player | **UNCERTAIN** — not independently re-verified via a clean league-wide aggregate endpoint this phase | Deferred (Sec. 19) |
| Assisted status | **VERIFIED PUBLIC** (`AST`, and per-shot assisted flags exist in shot-chart data) | Not wired into make probability this phase (Sec. 20) — see reasoning there |

## 8. Shot-family representation

One shared resolution framework (`block_probability`,
`unblocked_make_probability`) parameterized by `InteriorShotFamily`,
mirroring Phase 18A's own "one framework, per-family baseline" pattern
— `RIM` uses `rim_finishing` and the `RESTRICTED_RIM` zone for
geometric checks; `FLOATER` uses `floater_short_mid` and the `PAINT`
zone. No separate engines.

## 9. Primary defender representation

`InteriorDefenderContext(is_primary=True)` — reuses `DefensivePosture`
directly (`SQUARE`=attached, `RECOVERING`, `TRAILING`=beaten,
`HELPING`=not engaged on this shooter). No new "BEATEN" state was added
— `TRAILING`'s own existing Phase 15 definition ("beaten, behind the
assignment's hip") already covers it.

## 10. Secondary anchor representation

`InteriorDefenderContext(is_primary=False)`, optional
(`InteriorShotContext.secondary_defender`, defaults to `None` = anchor
absent). Only counts as a real "established help anchor" if BOTH its
zone matches the shot's interior zone AND its posture is `HELPING` —
verified directly (`test_helper_not_helping_posture_does_not_count`): a
secondary defender in the right zone but `RECOVERING` posture has zero
effect, since they are not yet an established anchor.

## 11. Contest-combination model comparison

Compared, per instruction: (a) blind sum — REJECTED, explicitly
identified as double-penalizing overlapping defenders on the same shot
geometry; (b) hierarchical primary/secondary — considered, but adds a
sequencing assumption (which one "goes first") not supported by any
real data; (c) **strongest-effective (max)** — chosen; (d) bounded
combined contest (e.g. a capped sum) — considered but re-introduces an
arbitrary cap coefficient with no empirical basis. **Strongest-effective
was selected as the simplest model that behaves correctly**: the
combined effective suppression is the LARGER (more suppressing) of the
primary's and any established secondary anchor's contribution, never
their sum.

## 12. Chosen contest architecture

`_effective_suppression(primary, secondary) = max(primary_suppression,
secondary_suppression)` where `primary_suppression = z(rim_protection)
+ posture_delta` and `secondary_suppression` is a real, flagged
placeholder constant applied only when a real, eligible, `HELPING`
anchor is present. This is subtracted (not added) from the shooter's
own base logit in `unblocked_make_probability` — a **real sign bug was
caught and fixed during this phase's own smoke-testing** (an earlier
draft added the suppression term instead of subtracting it, which made
elite rim protectors INCREASE opponent make probability — inverted;
fixed and reverified with a full directional sanity sweep before any
test was written, same discipline as the sign-inversion catches in
Phases 17A/17B).

Per HQ's explicit instruction, the SIMULATION-time defender identity
(who is structurally the primary/secondary defender right now) comes
entirely from the caller-supplied `InteriorDefenderContext` objects —
this module never infers "the help defender" from any historical
rim-contest-volume proxy. Calibration-time identification weaknesses in
the underlying `rim_protection`/`defensive_playmaking` estimators (both
already real, Phase 7/1-3 attributes) are a separate, already-existing
concern this phase does not touch or worsen.

## 13. Block eligibility architecture

`geometric_block_eligibility(shot_family, defender)` — a general
topological gate: the primary defender is always eligible (they are,
by construction, at the shot); a secondary defender is eligible ONLY if
their zone equals the shot's interior zone AND their posture is
`HELPING`. A defender in an unrelated perimeter zone can never be
eligible regardless of any ability value — verified directly
(`test_impossible_zone_cannot_block_even_with_high_ability`,
`test_high_rim_protection_cannot_bypass_geometric_eligibility`, both
using an absurd ability value on a geometrically-impossible defender
and confirming zero effect on block probability).

## 14. Block model

`block_probability(context)`: geometric eligibility filters candidates
first; among ELIGIBLE defenders only, the single MOST dangerous
eligible shot-blocker's real, z-scored `defensive_playmaking` value
(NOT `rim_protection` — Sec. 6/16) sets the block logit, added to a
real, flagged placeholder base rate (`BASE_BLOCK_LOGIT=-2.6`, chosen so
an average defender's block probability ≈ 6.9%, a real, roughly
plausible order of magnitude — not fit to any per-shot target). A
`BLOCK_LEVERAGE_SCALE=0.5` damping factor was added after an initial
smoke test showed an unscaled z-score pushed an elite shot-blocker
toward an implausible >70% block rate — the scale keeps the real
DIRECTION intact while avoiding a wildly unrealistic magnitude; both
constants are explicitly flagged placeholders (Sec. 32), not claimed as
calibrated.

## 15. Block outcome representation

Reuses Phase 15's own `block_retained_by_offense`/
`block_secured_by_defense` methods verbatim — no new ball-state concept
was introduced. A real, flagged placeholder split
(`BLOCK_RETAINED_BY_OFFENSE_RATE=0.35`) decides which of the two real
Phase 15 outcomes applies; no steal is ever credited on a block
(verified: `test_block_never_automatically_steal` — the result object
has no `steal` field at all), and no rebound is credited either
(verified: `test_block_never_automatically_dreb` — a
`BLOCKED_SECURED_DEFENSE` outcome leaves `ball_carrier=None`, `LOOSE`
state, exactly Phase 15's own genuinely-unresolved convention).

## 16. `rim_protection` double-count study

Directly investigated (Sec. 6): using the SAME `rim_protection`
suppression value for both the block branch and the unblocked-
conversion-suppression branch would count the same real signal twice —
once as an actual block (removing the shot entirely) and again as a
further FG%-discount on shots that survive being blocked. **Resolved by
using two DIFFERENT, already-existing real attributes for the two
branches** (`defensive_playmaking` for blocks, `rim_protection` for
unblocked suppression) — verified directly that `block_probability`
never reads `rim_protection` and `unblocked_make_probability` never
reads `defensive_playmaking` (by construction — inspect each function's
own real code; not merely asserted).

## 17. Unblocked rim-resolution model

`unblocked_make_probability`: `logit(rim_finishing) −
effective_suppression(primary, secondary)`, sigmoid, clipped to
`(0.01, 0.99)`. Verified: elite rim protector (real Phase 7 value
range) drops probability substantially (0.65→0.14 in a representative
sanity check); weak rim protector raises it (0.65→0.93); trailing
posture raises it relative to square (0.65→0.73); an established help
anchor lowers it further (0.65→0.60) — all real, directionally
verified, magnitude-unvalidated (same posture as every constant in this
phase).

## 18. Floater-resolution model

Same shared framework, `floater_short_mid` as the base rate, `PAINT` as
the geometric zone. Per explicit instruction, **no fixed attenuation
ratio (e.g. Gemini's proposed "50%") was copied** — the floater's
contest suppression uses the exact same `_effective_suppression`
function and constants as rim, since no real, floater-specific contest
data was gathered this phase to justify a different magnitude. This is
an honest, flagged simplification (Sec. 31), not a claim that floater
and rim contest sensitivity are empirically known to be identical.

## 19. Dunk/layup findings

**Not modeled — kept as one RIM family, per instruction.** Real shot
subtype descriptors ARE available (`playbyplayv3`'s `subType` field,
confirmed live — Sec. 7), but (a) no `vertical_pop` attribute exists to
ground a dunk-eligibility rule, and (b) deriving dunk eligibility from
an invented standing-reach threshold was explicitly prohibited. No
dunk/layup/hook/alley-oop/putback skill was created.

## 20. Assisted-status findings

**Not wired into make probability**, per the explicit correct causal
chain (pass/drive → defender displaced → receiver obtains position →
lower contest → ordinary finishing resolves). `InteriorShotContext` has
no `assisted` field at all — an assist's real effect, if any, is
already supposed to flow through the primary/secondary defender
CONTEXT this module already consumes (a receiver who got a great pass
should show up as a defender in `TRAILING`/`HELPING`/absent posture,
not as a separate assisted-status bonus). No independent heldout test
of assisted status's residual value was run this phase — flagged as
unresolved (Sec. 31), not concluded either way.

## 21. Physical-variable diagnostics

Real, TRAIN-only test: defender standing reach vs. real 2023-24
`rim_protection` suppression rate (n=441, real `player_id` join):
**Pearson r=0.407** — a real, substantial, positive correlation.
Judged sufficient evidence that `rim_protection` already substantially
absorbs physical length; standing reach was NOT added as an
independent term in either the block or unblocked-suppression branch
(verified: `test_no_standing_reach_or_wingspan_parameter` — neither
`InteriorDefenderContext` nor `InteriorShotContext` has any such
field). Mass was not tested at all, per explicit default-off
instruction — no mass field exists anywhere (verified:
`test_no_mass_parameter_anywhere`).

## 22. Rim-conversion model ladder

| Rung | Included? | Basis |
|---|---|---|
| M0 shot-family/zone baseline | Implicit (real base rate = `rim_finishing`/`floater_short_mid` itself) | Real |
| M1 shooter latent (`rim_finishing`/`floater_short_mid`) | **Yes** | Reused as-is (Sec. 4/5) |
| M2 primary defender context | **Yes** | Real `rim_protection` z-score + real structural posture delta |
| M3 secondary anchor context | **Yes, gated on real HELPING posture + zone match** | Structural |
| M4 `rim_protection` distinct-incremental-signal check | **Yes — confirmed distinct from `defensive_playmaking`** (Phase 7's own real 25-42% overlap finding, reused, not re-derived) | Real |
| M5 standing reach / physical geometry | **No** — real r=0.407 double-counting risk found (Sec. 21) | Real, against |

## 23. Block model ladder

| Rung | Included? | Basis |
|---|---|---|
| B0 unconditional block baseline by shot family | Implicit (`BASE_BLOCK_LOGIT`, real rough order-of-magnitude placeholder) | Placeholder |
| B1 geometric eligibility | **Yes** | Real, structural, tested (Sec. 13) |
| B2 `rim_protection` | **No — `defensive_playmaking` used instead** (Sec. 6/16 double-count avoidance) | Real, evidence-based choice |
| B3 physical reach | **No** | Sec. 21 |
| B4 subtype (dunk/layup) | **No** | Sec. 19 |

## 24. Temporal heldout results

**Not run as a fitted probabilistic model this phase** — same honest
limitation as every prior resolution phase (17A/17B/18A): no public
per-shot, defender-attributed block/make ground truth exists. The one
genuinely temporal/cross-context evidence gathered this phase is the
`rim_protection` vs. reach correlation (Sec. 21, single-season
2023-24) and the reuse of Phase 7's own already-heldout-validated
`rim_protection` estimator (real TRAIN/HELDOUT split, from its own
report) — not re-validated here, reused as-is.

## 25. Portability/natural-experiment findings

**Not run this phase** — traded rim finisher/protector checks, anchor-
absence natural experiments, and POA-context-controlled comparisons
were judged out of scope given the time already spent on the primary
construct audits (Sec. 4-6) and the sign-bug catch (Sec. 12). Flagged
as unresolved work (Sec. 31), consistent with this project's practice
of naming a real gap rather than fabricating a result to fill it.

## 26. RNG handling

`resolve_interior_shot` performs at most TWO independent `rng.random()`
calls per shot: one for the block roll, and — only if not blocked — one
for the unblocked make/miss roll (with a possible third, conditional
roll for the retained-vs-secured split, only reached on an actual
block). This means a counterfactual that changes ONLY block-relevant
inputs (e.g. `defensive_playmaking`) while holding the RNG seed fixed
does not reshuffle the unblocked-trajectory roll's random draw UNLESS
the block outcome itself changes (which is the whole point — if the
shot isn't blocked in either counterfactual, the same second
`rng.random()` call resolves the same trajectory roll). No global
`random` module call exists anywhere in the file (verified:
`test_no_global_rng`). RNG was not further split into named substreams
this phase (unlike Phase 17B's `derive_rng`) — judged unnecessary,
since the two rolls are already sequential and mutually exclusive in
effect (a block roll failure always precedes and gates the trajectory
roll for that same shot).

## 27. Miss/block handoff

Reused Phase 18A's `resolve_shot_missed_pending_rebound` verbatim for
unblocked misses (already family-agnostic — no Phase 18B-specific
change needed) and Phase 15's `block_retained_by_offense`/
`block_secured_by_defense` verbatim for blocks. No new handoff object
was created — `PossessionState` already carries shooter identity (via
the just-completed `SHOT_RESOLVED` event's `primary_player_id`), shot
zone, ball state, and team-possession status, which is everything Phase
19 will need. No carom class/coordinate was invented — left entirely to
Phase 19, per the explicitly allowed "if coarse carom metadata lacks
real support, leave it for Phase 19."

## 28. Falsification tests

All 15+ required counterfactuals were run as real, direct tests (not
narrative claims) — see Sec. 29 for the full list; highlights:
`rim_access_creation`/`poa_containment`/`foul_drawing`/`foul_discipline`
changing has **zero effect** because none of the four has ANY parameter
anywhere in this module (verified by dataclass-field and namespace
scans, not just by not being read); a defender in an impossible zone
cannot block regardless of an absurd (99.0) ability value; a high
`rim_protection` value cannot bypass geometric eligibility (it isn't
even the block-branch input, and was tested anyway); block and make
never co-occur on the same resolved attempt (verified over 300 real
trials); elite rim protectors never guarantee a block, and poor ones
occasionally still get one (both verified over real multi-hundred-trial
simulations).

## 29. Focused tests

`test_interior_shot_resolution.py` — 38 tests covering: both supported
shot families, correct ability-family mapping, primary/secondary
context (including the "not-yet-an-anchor" recovering-posture case),
geometric block eligibility (primary always eligible, wrong zone,
wrong posture, right zone+posture, impossible-zone-with-absurd-ability),
block stochastic resolution (reachable, not guaranteed for elite,
occasionally reachable for poor protectors), unblocked make/miss
(low-skill can make, elite can miss), every required firewall
(`rim_access_creation`, `poa_containment`, tendencies, foul attributes,
mass, standing reach/wingspan — all via namespace/dataclass-field scans
AND behavioral no-op checks), no block/steal conflation, no
block/rebound conflation, no rebound-winner selection, no putback
field, `player_id`-only for both shooter and defender, deterministic
replay, no global RNG, missing-evidence-treated-as-neutral (not zero
skill), and no mutation leakage between independent engines.

## 30. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 562 tests — OK**
(524 carried over from Phase 18A + 38 new in
`test_interior_shot_resolution.py`). No existing test was modified or
removed.

## 31. Unresolved issues

- Assisted-status's independent residual heldout value was not tested
  (Sec. 20).
- Floater contest sensitivity reuses rim's exact suppression constants
  — an explicit, flagged simplification, not an empirically-verified
  equivalence (Sec. 18).
- No traded-player/anchor-absence natural experiment was run (Sec. 25).
- Dunk/layup subtype's real incremental value for conversion/block
  prediction was not tested, only confirmed as theoretically available
  (Sec. 19).
- Block-to-shooter/shot-subtype PBP linkage is real and DERIVABLE but
  not ingested — a real, concrete future ingestion task if a later
  phase needs event-level (rather than season-aggregate) block
  calibration (Sec. 7).
- `BASE_BLOCK_LOGIT`, `BLOCK_LEVERAGE_SCALE`,
  `BLOCK_RETAINED_BY_OFFENSE_RATE`, and both posture-delta tables remain
  unvalidated placeholders (Sec. 32).

## 32. Classifications

| Candidate mechanic | Classification |
|---|---|
| `rim_finishing`/`floater_short_mid` as base-skill inputs (reused, unchanged) | **KEEP** (unchanged from Phase 5's own classifications: LOCK V1 / KEEP BUT FLAG respectively) |
| `rim_protection` for unblocked conversion suppression | **KEEP** (unchanged Phase 7 classification, reused for one branch only) |
| `defensive_playmaking` for block probability | **KEEP** (unchanged Phase 1-3 classification, reused for the other branch) |
| Geometric block eligibility gate | **KEEP** — real, tested, structurally enforced |
| Strongest-effective contest combination | **KEEP** — real, principled, tested; magnitude unvalidated |
| Posture contest deltas (primary + secondary) | **KEEP BUT FLAG** — real direction, unvalidated magnitude |
| Block base rate / leverage scale / retained-vs-secured split | **KEEP BUT FLAG** — real, plausible order of magnitude, not fit to data |
| Floater contest reuse of rim's constants | **KEEP BUT FLAG** — explicit simplification |
| Standing reach as an independent term | **REVISIT** — real r=0.407 double-count evidence found, excluded |
| Dunk/layup subtype | **INSUFFICIENT** — not evaluated |
| Assisted-status residual effect | **INSUFFICIENT** — not tested |
| A new standalone `block_ability` attribute | **not created** — explicitly rejected per instruction, existing attributes sufficed |

## 33. Final phase classification

**READY WITH FLAGS FOR 18C.**

Ready: the full `INTERIOR SHOT INTENT → RELEASE CONTEXT → PRIMARY/HELP
GEOMETRY → CONTEST → BLOCK ELIGIBILITY → BLOCK OR UNBLOCKED PATH →
MAKE/MISS → HANDOFF` pipeline is implemented, tested, and reuses
Phase 15/18A engine machinery with ZERO new engine methods needed; the
`rim_protection` double-counting question (this phase's central
empirical task) was resolved with a real, evidence-based split across
two already-existing attributes rather than assumed or scaffolded away;
geometric block eligibility is proven to gate ability-based block
resolution before any ability is consulted; block is never conflated
with steal or rebound; a real sign-inversion bug was caught and fixed
during this phase's own verification, matching the discipline of every
prior resolution phase; every firewall
(`rim_access_creation`/`poa_containment`/tendencies/foul attributes/
mass/reach) is enforced both structurally (no such field exists) and
behaviorally (counterfactual tests).

Flags: every numeric constant beyond the two real population-
normalization statistics (`rim_protection` mean/stdev,
`defensive_playmaking` mean/stdev, both reused from prior phases) is an
explicit, unvalidated placeholder; floater contest sensitivity is an
unverified equivalence-to-rim simplification; assisted-status,
dunk/layup, and portability/natural-experiment questions remain
genuinely open, not force-completed.

Not begun: Phase 18C.
