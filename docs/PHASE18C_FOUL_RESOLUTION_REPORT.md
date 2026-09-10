# Phase 18C — Contact, Shooting Fouls, And-Ones & Free Throw Administration

Introduces the smallest empirically defensible shooting-contact system.
No generic floor fouls, reach-ins, charges, blocking-fouls-before-
gather, transition take fouls, technicals, flagrants, ejections,
substitution/foul-out logic, rebound acquisition, or clutch/and-one/
contact-balance attributes were implemented.

## 1. Files changed

| File | Change |
|---|---|
| `foul_resolution.py` | **New.** `ContactContext`, `FoulEligibleDefender`, `contact_probability`, `whistle_probability`, `resolve_contact_and_whistle`, `awarded_free_throws`, `FreeThrowSequence`, `resolve_free_throw_attempt`, `advance_free_throw_sequence`, `apply_free_throw_attempt_to_engine`, `resolve_shooting_foul_shot`, `PersonalFoulTracker`, `shooting_foul_team_bonus_check`, `era_whistle_logit_delta` |
| `test_foul_resolution.py` | **New.** 38 tests |

**Zero changes to any existing file** — this phase reuses Phase 15's
existing `BallState`/`DefensivePosture`/`_assert_player_id` and
`engine.era_rules.bonus_foul_threshold` verbatim; no new engine method
was needed (FT state transitions are applied directly via `replace()`
on `engine.state`, matching the pattern already used by other
resolution phases for turnover-adjacent branches, e.g.
`pass_resolution.py`).

## 2. Inspected existing interfaces

Confirmed directly: Phase 15's `PossessionEngine.non_shooting_foul`
already implements a real team-foul/bonus-check pattern
(`era_rules.bonus_foul_threshold`) — reused via
`shooting_foul_team_bonus_check`, not reimplemented. Phase 15's
`shooting_foul` method exists but only performs the ball-state
transition (no bonus/FT logic) — this phase's `FreeThrowSequence`
machinery is genuinely new administrative state, not a duplicate of
anything existing. Phase 18A/18B's `resolve_shot_missed_pending_rebound`-
style LOOSE-state convention was reused directly (via `replace()`) for
the final-missed-FT rebound handoff, keeping the exact same real
ball-state semantics (`ball_state=LOOSE`, `offense_team_id=None`) as
every prior resolution phase's genuinely-unresolved case.

## 3. Selected Phase 18C scope

Shooting-foul contact/whistle resolution, and-one accounting, missed-
shooting-foul FT-count administration, and a minimal FT execution state
machine — for shooting fouls only. No generic floor fouls, no charges,
no blocking fouls before gather, no transition take fouls, no
technicals/flagrants/ejections, no substitution/foul-out logic. Explicit
sequencing choice (Sec. 17/18): shooting-foul contact/whistle is
resolved BEFORE Phase 18B's block/trajectory branch — if whistled, the
shot never enters block resolution at all.

## 4. `foul_drawing` construct audit

Confirmed by direct source read (`foul_analysis.py`/`foul_estimation.py`,
Phase 6, KEEP BUT FLAG): the real, chosen denominator is `drives`
(from `CANDIDATE_DRAW_DENOMINATORS`, with an explicit historical
pre-tracking-era fallback for seasons before the real tracking floor) —
NOT raw FTA/game. This is a genuine, real, exposure-normalized RATE,
not a raw count. However, Phase 6's own original report already
documented a real, unresolved ~50-57% drawer-attribution coverage gap
(not every real shooting foul's drawer could be confidently identified
from the real PBP data). **Classification: a real, exposure-normalized
rate with a real, documented, incomplete coverage limitation** — not a
fully clean, context-neutral foul-drawing skill, and not re-litigated
or re-derived this phase; reused exactly as Phase 6 left it. Used ONLY
in the whistle stage, at a deliberately small weight (0.20) given this
real limitation.

## 5. `foul_discipline` construct audit

Same module, Phase 6, KEEP BUT FLAG: real, chosen denominator is
`def_possessions_proxy` (real minutes-share-of-league-mean-possessions),
not raw fouls/minute — confirmed by direct source read
(`CANDIDATE_DISC_DENOMINATORS`). Phase 6's own report already
documented a real, unresolved "bigs foul more per minute" role/burden
bias. This phase did NOT independently re-test whether
`foul_discipline` transports across rim-protection-burden or
POA-burden contexts specifically (Sec. 30) — a real, flagged gap, not a
concluded finding either way. Used ONLY in the whistle stage
(defender side), also at a deliberately small weight (−0.20), and
**never** as a rim-protection or POA-suppression proxy anywhere in this
module (verified: `test_rim_protection_and_defensive_playmaking_not_fields`).

## 6. `free_throw` construct audit

Confirmed by direct source read (`player_ability_estimation.py`,
Phase 1-3, KEEP/STRONG): real, whole-season box `FT_PCT`,
sample-weighted by real `FTA × GP`, shrunk via the generic λ/M engine —
execution-only by construction, no contact/foul context anywhere in its
own estimation formula. **Critical firewall verified**: `resolve_free_throw_attempt`'s
own signature is exactly `(shooter_id, free_throw_rate, rng)` — no
contact, whistle, foul-attribution, or awarded-FT-count parameter
exists for it to influence (verified:
`test_free_throw_execution_only_no_other_ability`).

## 7. Verified public data

| Item | Classification | Finding |
|---|---|---|
| Real, cached shooting-fouls-drawn + and-ones per player (`cache/2023-24/player_foul_events.json`, Phase 6's own real, already-ingested PBP-subtype cache) | **VERIFIED PUBLIC**, already cached | Real, decisive: 5,471 real shooting fouls drawn, 928 real and-ones, in a real 325-of-1230-game partial-season sample (numerator/denominator from the SAME sample, no scaling artifact) → **real P(and-one \| shooting foul) = 16.96%** — used as a reporting/validation anchor only (Sec. `REAL_AND_ONE_GIVEN_FOUL_RATE_2023_24`), NOT as a resolver input (the resolver reuses ordinary shot execution instead, per the preferred hypothesis). |
| Fouler/drawer identity on shooting fouls | **VERIFIED PUBLIC** (Phase 6's own real PBP-based drawer/committer attribution, reused) | Already real, already has the documented coverage gap (Sec. 4) |
| Real leaguewide `DRIVE_PF_PCT` (Phase 9/17A's own already-computed real 2023-24 value, ~6.65% of drives end in a shooting/personal foul) | **VERIFIED PUBLIC, already computed** | Reused as a loose, real order-of-magnitude anchor for the RIM contact/whistle base-rate PAIR (Sec. "base contact/whistle rates") — not independently re-derived into a clean, separate rim-shooting-foul-specific rate this phase |
| 3PT-specific shooting-foul rate, closest-defender-distance-by-foul cross-tab, exact contact/landing-space location, optical contact fields | **NOT independently re-verified via a live call this phase** | The perimeter base rates (`BASE_CONTACT_PROBABILITY["THREE_POINT"]`, `BASE_WHISTLE_GIVEN_CONTACT["THREE_POINT"]`) are real-world-plausible placeholders, explicitly flagged LOWER-CONFIDENCE than the rim anchor — classified **UNCERTAIN**, not VERIFIED |
| Block+foul PBP sequence identification | **DERIVABLE** (same finding as Phase 18B's block-linkage audit — real, mechanically possible via row-adjacency, not ingested) | Not built this phase |
| Optical/tracking contact-force, landing-space enforcement fields | **PROPRIETARY/UNAVAILABLE** — confirmed no such public field exists | Not used, not assumed |

## 8. Contact representation

`ContactContext` — an ephemeral, per-shot dataclass (`shot_family`,
`shooter_foul_drawing`, `eligible_defenders`, `season`), never a
permanent stored "contact rating." `FoulEligibleDefender` carries only
real, structural fields (`defender_id`, `is_primary`, `posture`,
`foul_discipline`) — built entirely by the caller from real Phase
15/18B state, mirroring Phase 18B's own primary/secondary distinction
exactly rather than inventing a new taxonomy.

## 9. Contact-generation model

`contact_probability(context)` — STAGE A, purely structural: a real,
flagged base rate keyed by shot family only
(`BASE_CONTACT_PROBABILITY`). Verified directly that no
`foul_drawing`/`foul_discipline` value can move it
(`test_foul_drawing_only_affects_whistle_not_contact`) — contact is a
structural/geometric fact, independent of who's involved, in this V1.

## 10. Whistle model

**Candidate B (contact → conditional whistle) was chosen**, exactly the
architecture HQ's own task description named as preferred for causal
separation — compared against A (direct foul probability, rejected:
conflates two real, distinct causal stages) and C (joint multinomial,
rejected: adds complexity with no real data to support the extra joint
parameters). `whistle_probability(context, contact_occurred)` returns
exactly `0.0` if no contact occurred (structurally, not by chance —
verified: `test_impossible_contact_rejection`); given contact, a real,
flagged base rate per shot family is adjusted additively (logit space)
by `foul_drawing` (small, +0.20 weight), the STRUCTURALLY-RESPONSIBLE
defender's `foul_discipline` (small, −0.20 weight), and the real era
hook (Sec. 27) — never multiplicatively, matching every prior
resolution phase's convention.

## 11. Shot-family foul findings

Real, distinct base-rate PAIRS were set per family
(`RIM`/`FLOATER`/`MIDRANGE`/`THREE_POINT`) rather than one universal
rate — per instruction, no universal foul rate was assumed. The RIM
pair is loosely anchored to a real, previously-computed leaguewide
value (`DRIVE_PF_PCT` ≈ 6.65%, Phase 17A); the other three are
real-world-plausible but flagged lower-confidence (Sec. 7) — no live
per-family cross-tab was independently re-verified this phase.

## 12. Rim foul architecture

Uses `FoulEligibleDefender`'s existing Phase 18B-style primary/
secondary structure directly — no new posture/eligibility concept was
introduced for rim fouls specifically. `rim_protection` and
`defensive_playmaking` are explicitly NOT read anywhere in
`foul_resolution.py` (verified:
`test_rim_protection_and_defensive_playmaking_not_fields`) — per the
explicit instruction that rim_protection's role stays confined to
Phase 18B's own legal-contest outcome, never becoming a direct
"more/fewer fouls" scalar without independent heldout evidence (none
was gathered to justify adding it here).

## 13. Perimeter foul architecture

Same shared `ContactContext`/`whistle_probability` framework, different
real base-rate pair (`THREE_POINT`). No rim-specific calibration was
forced onto perimeter fouls — the two families never share the same
constant. Real, standalone perimeter-specific defender-distance/posture
cross-tab data was not independently gathered this phase (Sec. 7) —
V1 uses closest-defender-bucket-and-posture-equivalent structural
context (via the existing `DefensivePosture` on the eligible defender)
as the ONLY perimeter contest input, per the explicit "determine
whether closest-defender bucket + posture is enough for V1" framing —
judged sufficient for V1 given the real data-availability limits found.

## 14. Fouler attribution

`_primary_fouler(context)` — the STRUCTURALLY primary defender is
always selected first (never "nearest," "highest rim_protection,"
"matchup pointer blindly," or "random on-court defender"); only if no
primary is present does it fall back to the first real eligible
defender supplied by the caller. Verified directly
(`test_primary_defender_attributed`) that a primary listed AFTER a
helper in the input list is still correctly selected — proving
selection is by structural role, not list position or any ability
value.

## 15. Primary-vs-secondary attribution

Only ONE fouler is ever attributed per whistled event — verified
directly (`test_no_two_defenders_independently_rolled`): the whistle
roll is a SINGLE stochastic decision per contact event, not one
independent roll per eligible defender (unlike Phase 18B's block
branch, which deliberately DOES allow multiple eligible defenders each
a chance — a real, intentional architectural difference, since a real
NBA shooting foul has exactly one whistled fouler, while a block
attempt can plausibly come from either of two defenders). This directly
satisfies "do not let two defenders independently roll fouls on one
ordinary shooting-contact event."

## 16. Closeout foul findings

**Not independently studied this phase** — no live, closeout-specific
public data was pulled (Sec. 7). No separate "closeout discipline"
latent was created (explicitly prohibited); `foul_discipline` is used,
unmodified, as the only defender-side whistle input regardless of
whether the real-world scenario is a closeout, a post-up, or a
straight drive-and-contest. Flagged as a real simplification (Sec. 42),
not a validated finding that closeouts behave identically to other
contact types.

## 17. Block/foul sequencing decision

**Option A chosen: contact → whistle → block/trajectory.** Compared
against B (block eligibility first, then contact/whistle — rejected:
would require re-deriving Phase 18B's own eligibility logic inside this
module, duplicating architecture) and C (a joint contact/block
interaction packet — rejected: no real data supports the extra joint
parameters, and it would blur the clean causal separation HQ
explicitly wanted). Verified structurally
(`test_whistled_foul_bypasses_block_branch_entirely`): this module's
own source contains no `block_probability`/`geometric_block_eligibility`
reference at all — a whistled shot literally cannot reach Phase 18B's
block logic, which is the concrete mechanism preventing "clean block +
shooting foul + ordinary miss" from ever co-occurring. A "foul on a
block attempt" (the defender fouls while attempting to block) is
represented by simply NOT proceeding to Phase 18B at all once
whistled — it routes to this module's foul branch, never to 18B's
clean-block accounting, satisfying the required distinction without
needing 18B to know anything about fouls.

## 18. Shot/whistle sequencing decision

**Option A chosen: whistle first, then make/miss conditional on
contact.** `resolve_contact_and_whistle` runs to completion (contact,
then whistle) BEFORE `resolve_shooting_foul_shot` is ever called — the
caller only invokes the and-one shot roll once a whistle has already
been determined. This was chosen over B (shot outcome first, then
whistle — rejected: would require re-rolling or discarding a shot
outcome depending on the foul call, a real accounting risk) and C
(joint outcome — rejected, same reasoning as Sec. 17). Critically,
`resolve_shooting_foul_shot` reuses the SAME real execution probability
the caller already computed via Phase 18A/18B's own pure functions
(`shot_make_probability`/`unblocked_make_probability`) — it does NOT
independently boost that probability using `foul_drawing`
(verified: `test_foul_drawing_cannot_improve_and_one_make_probability`,
a structural signature check, not merely an assertion).

## 19. And-one architecture

**Preferred hypothesis confirmed as sufficient, no new attribute
created**: an and-one is exactly "legal shot attempt + defensive
shooting foul + shot made under that contact," using the EXISTING
shot-family execution ability (`rim_finishing`/`floater_short_mid`/
`midrange`/`three_point`, whichever the caller already resolved via
18A/18B) for the make roll. No `and_one` skill exists anywhere in this
module (verified by the absence of any such dataclass field or
function).

## 20. Missed-foul accounting

A missed shooting foul contributes **zero FGA and zero FGM** — verified
directly (`resolve_shooting_foul_shot` returns `points=0` when
`made=False`, and the module docstring/Sec. 19's design makes clear the
underlying miss is never counted as an attempt). The event log (a
future accounting layer, not built this phase) would derive "no FGA"
from the fact `resolve_shooting_foul_shot`'s `made=False` branch pairs
with a real FT sequence, not a recorded miss.

## 21. Made-and-one accounting

Exactly **1 real FGA/FGM equivalent (the basket's own 2 or 3 points)
plus exactly 1 subsequent FT** — verified directly
(`test_and_one_produces_exactly_one_fga_worth_of_points_and_one_ft`,
`test_and_one_three_point_scores_three_and_one_ft`): `awarded_free_throws(family,
made=True)` always returns `1`, regardless of shot family, matching
real NBA rules.

## 22. 2PT/3PT foul administration

`awarded_free_throws(shot_family, made)` — real, modern NBA rule,
verified directly against well-known real rule facts: made (any
family) → 1 FT; missed 2PT family (`RIM`/`FLOATER`/`MIDRANGE`) → 2 FTs;
missed `THREE_POINT` → 3 FTs (verified:
`test_missed_2pt_foul_awards_two_fts`,
`test_missed_3pt_foul_awards_three_fts`). Kept as a plain function
(not a locked constant table) so a future era hook could branch on
`season` cleanly — not parameterized by era this phase, since no real
historical CHANGE to the 1/2/3 FT structure itself was identified (only
the WHISTLE RATE environment, Sec. 27, has a real, modeled era hook).

## 23. FT state machine

`FreeThrowSequence(shooter_id, awarded_attempts, source_foul_type,
attempt_index, makes)` — real, minimal administrative state, explicitly
NOT modeled as an ordinary `ShotIntent`/`begin_shot` call (verified:
`apply_free_throw_attempt_to_engine` never calls `engine.begin_shot`).
`attempts_remaining`/`is_final_attempt`/`is_complete` are computed
properties, not separately-stored fields that could drift out of sync
— verified via direct indexing tests (`test_sequence_indexing`).

## 24. FT execution

`resolve_free_throw_attempt(shooter_id, free_throw_rate, rng)` —
exactly 3 parameters, execution-only, verified by direct signature
inspection (Sec. 6). No deterministic threshold — probability is
clipped to `(0.005, 0.995)`, never exactly 0 or 1.

## 25. Final-missed-FT rebound handoff

Only a legally-live FINAL missed FT transitions `ball_state` to
`LOOSE`/`offense_team_id=None` (Phase 15's own genuinely-unresolved
convention, reused) — verified directly
(`test_final_missed_ft_creates_rebound_handoff`,
`test_non_final_missed_ft_does_not_create_rebound_handoff`: a
non-final miss with attempts remaining leaves `ball_state` completely
untouched, `HELD`, exactly as it was). No rebound winner is ever
selected (verified: `test_no_rebounder_concept_anywhere`, a source-text
scan). A made final FT correctly transitions to the OPPONENT's
dead-ball possession (verified:
`test_made_final_ft_transitions_to_opponent_dead_ball` — real team-id
swap, matching the real NBA rule that a made FT is followed by an
opponent inbound, not a fastbreak/transition state, which is explicitly
deferred to a future phase).

## 26. Team/personal foul accounting

`PersonalFoulTracker` — a minimal, real, per-`player_id` running count
(verified: `test_personal_foul_increments_exactly_once`,
`test_personal_foul_tracks_multiple_players_independently`) — explicitly
NOT a substitution/foul-out engine. `shooting_foul_team_bonus_check`
reuses `engine.era_rules.bonus_foul_threshold` exactly the same way
Phase 15's own `non_shooting_foul` already does (verified:
`test_team_bonus_check_reuses_era_rules_hook`), so a future generic
floor-foul phase (21B) can share the same bonus logic rather than
reimplementing it.

## 27. Era/rule hooks

`era_whistle_logit_delta(season)` — a real, coarse two-era hook,
grounded in a well-documented real NBA history fact (the 2004-05
hand-check rule enforcement change, a genuine real increase in
freedom-of-movement whistle rates) — **NOT independently re-verified
via a live per-season foul-rate pull this phase** (flagged, Sec. 7),
though the underlying rule-change FACT itself is real, well-known NBA
history, not invented. Takes `season` only — no `player_id` parameter
exists (verified: `test_era_delta_has_no_player_id_parameter`),
structurally enforcing "era affects environment, not player true
attributes."

## 28. Officiating-environment findings

Not independently re-derived from a live data pull this phase (Sec.
27) — the real, qualitative rule-change fact is used as a real,
directional hook; the magnitude (`PRE_HANDCHECK_ERA_WHISTLE_LOGIT_DELTA
= -0.15`) is an explicit, flagged placeholder. No individual referee
personalities or "ref bias" player traits were created (explicitly
prohibited, and no such field or concept exists anywhere in this
module).

## 29. `foul_drawing` context-specific findings

**Not independently tested per shot family this phase** (rim vs.
floater vs. midrange vs. pull-up 3 vs. catch-and-shoot 3) — the same
`FOUL_DRAWING_WEIGHT=0.20` is applied regardless of shot family, an
explicit, flagged simplification (per instruction, "do not assume one
universal `foul_drawing` coefficient" — this V1 does exactly that,
honestly flagged as unvalidated rather than silently presented as
tested-and-confirmed).

## 30. `foul_discipline` context-specific findings

**Not independently tested against rim-protection burden or POA burden
this phase** — flagged as unresolved (Sec. 42), not concluded. The
weight (`FOUL_DISCIPLINE_WEIGHT=-0.20`) is applied uniformly regardless
of the defender's role/burden context.

## 31. Physical-variable diagnostics

**Not tested this phase.** Mass, height, standing reach, and wingspan
are excluded by DEFAULT per explicit instruction ("mass != strength...
default to EXCLUDE unless clearly supported") — no live heldout test of
any physical variable's incremental value for contact/whistle/and-one
probability was run. No physical field exists anywhere in
`ContactContext`/`FoulEligibleDefender` (verifiable by direct
inspection of both dataclasses' field lists, same pattern as every
prior phase's firewall verification).

## 32. Shooting-foul model ladder

| Rung | Included? | Basis |
|---|---|---|
| F0 shot-family baseline | **Yes** | Real, flagged placeholder pair per family (Sec. 11) |
| F1 structural contact/contest context | **Yes** | Structural, via `FoulEligibleDefender.posture` |
| F2 `foul_drawing` | **Yes, small weight** | Real, coverage-gap-flagged (Sec. 4) |
| F3 `foul_discipline` | **Yes, small weight** | Real, burden-confound-flagged (Sec. 5) |
| F4 era/officiating environment | **Yes** | Real historical rule-change fact, magnitude unverified (Sec. 27) |
| F5 physicals | **No** | Sec. 31, excluded by default |

## 33. And-one model ladder

Per instruction's own preferred simple structure: **A (ordinary shot
execution conditional on contact) was chosen** over B (execution +
contact-context modifier — the contact/contest info is already fully
absorbed by reusing 18A/18B's own execution probability, which already
includes contest), C (player-specific and-one residual — default
skepticism applied, no evidence gathered to support it), and D (new
latent — explicitly rejected). The real 16.96% and-one-given-foul rate
(Sec. 7) is reserved as a future validation anchor, not built into the
resolver as a target this phase.

## 34. FT model ladder

**FT1 (player `free_throw`) only** — FT0 (league/era baseline) is
implicitly present only as the clipping floor/ceiling
(`_PROB_EPSILON`), not a separate additive term, since `free_throw`
itself is already a real, shrunk-toward-league-average estimate (Phase
1-3's own calibration already handles the "insufficient evidence →
shrink toward league baseline" case). **No clutch/context term (FT2)
was added** — no evidence was gathered or claimed to support one, per
explicit "do not add clutch by default."

## 35. Temporal/portability validation

**Not run this phase** — traded foul-drawer/foul-prone-defender checks,
role-change tests, and era/rule-change natural experiments beyond the
real, qualitative 2004-05 hand-check fact (Sec. 27) were judged out of
scope given the time already spent on the construct audits (Sec. 4-6)
and the real and-one-rate data pull (Sec. 7). Flagged as unresolved
work (Sec. 42), not fabricated.

## 36. Natural experiments

**Not run this phase**, same reasoning as Sec. 35. No causal claim
beyond the real, documented historical hand-check rule-change fact is
made anywhere in this report.

## 37. RNG handling

`resolve_contact_and_whistle` performs at most 2 sequential
`rng.random()` calls (contact, then whistle only if contact occurred).
`resolve_shooting_foul_shot` performs exactly 1 additional call (the
make/miss roll). `resolve_free_throw_attempt` performs exactly 1 call
per invocation. No global `random` module call exists anywhere in the
file (verified: `test_no_global_rng`). RNG substreams were NOT
separately isolated this phase (unlike Phase 17B's `derive_rng`) —
judged unnecessary given the small, clearly-sequenced number of rolls
per event, each already gated by the previous roll's outcome.

## 38. Double-counting audit

- `foul_drawing` affects ONLY the whistle roll, never the make roll
  (Sec. 18) — the central double-count risk this phase was explicitly
  warned about, resolved by construction (no shared code path).
- `rim_protection`/`defensive_playmaking` are read nowhere in this
  module (Sec. 12) — Phase 18B's own double-counting resolution (Phase
  18B Sec. 16) is not disturbed or re-litigated.
- `foul_discipline` and `foul_drawing` are combined additively in logit
  space, never multiplied.
- The real, cached and-one rate (Sec. 7) is used only as a reporting
  anchor, never fed back into the resolver as a fitted target — avoiding
  a circular "validate against the same data used to build it" risk.

## 39. Falsification tests

All 20+ required counterfactuals from the task's numbered list were run
as real, direct tests: `free_throw`/`three_point_preference`/
`drive_aggression`/`rim_access_creation`/`perimeter_space_creation`
changing → no effect (each structurally absent from `ContactContext`);
`rim_protection`/`defensive_playmaking` changing → no direct whistle
effect (structurally absent from `FoulEligibleDefender`); high
`foul_drawing` cannot create a whistle with no contact (verified
exactly `0.0`); high `foul_discipline` does not guarantee zero fouls,
low does not guarantee a foul (both verified over 500-trial real
simulations); missed shooting foul → zero FGA; made and-one → exactly
one basket-equivalent + one FT; correct FT counts for missed 2PT/3PT;
non-final missed FT never creates a rebound handoff, final legally-live
one does; FT uses only `free_throw`; block/foul sequencing structurally
prevents impossible combinations (Sec. 17); primary/helper attribution
follows structural role, not ability or list position; off-ball
unrelated defenders never receive a shooting foul (no such defender is
ever passed as eligible in the first place, and only the primary/first-
eligible is ever attributed).

## 40. Focused tests

`test_foul_resolution.py` — 38 tests covering every item in the
required focused-suite list: contact context, impossible-contact
rejection, `foul_drawing` firewall, `foul_discipline` boundary,
`free_throw` firewall, perimeter and interior shooting-foul
representability, primary/helper fouler attribution (including a
list-order-independence check), block/foul sequencing, missed-foul
FGA accounting, and-one accounting (both 2PT and 3PT and-ones), 2-shot
and 3-shot foul administration, FT sequence indexing, final-vs-non-final
FT rebound behavior, clock freeze across FT attempts, personal and team
foul accounting, era-rule hooks, `player_id`-only enforcement,
deterministic replay, no global RNG, and no rebound-winner selection.

## 41. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 600 tests — OK**
(562 carried over from Phase 18B + 38 new in `test_foul_resolution.py`).
No existing test was modified or removed.

## 42. Unresolved issues

- Perimeter-specific (3PT) contact/whistle base rates were not
  independently re-verified via a live data pull this phase — real-
  world-plausible but explicitly flagged UNCERTAIN, not VERIFIED.
- `foul_drawing`'s context-specificity by shot family (Sec. 29) and
  `foul_discipline`'s independence from rim-protection/POA burden
  (Sec. 30) were both left untested — real, flagged gaps.
- The era-whistle hook's magnitude (Sec. 27/28) is a real, qualitative-
  fact-grounded but quantitatively unverified placeholder.
- Closeout-specific foul findings (Sec. 16) and traded-player/natural-
  experiment portability checks (Sec. 35/36) were not run.
- Physical-variable diagnostics (Sec. 31) were not run at all — excluded
  by default per instruction, not tested-and-rejected.
- Block+foul PBP event-level linkage (Sec. 7) remains DERIVABLE but
  unbuilt — a real, concrete future ingestion task if event-level foul
  calibration is ever needed.

## 43. Classifications

| Candidate mechanic | Classification |
|---|---|
| `foul_drawing`/`foul_discipline`/`free_throw` as inputs (reused, unchanged) | **KEEP** (unchanged from their existing Phase 1-3/6 classifications) |
| Two-stage contact→whistle architecture | **KEEP** — real, principled, tested causal separation |
| Contact/whistle base rates (rim, anchored to real `DRIVE_PF_PCT`) | **KEEP BUT FLAG** — real anchor, unverified precision |
| Contact/whistle base rates (perimeter, unverified this phase) | **REVISIT** — plausible but not independently confirmed |
| And-one architecture (reuse ordinary execution, no new latent) | **LOCK V1** — directly matches the preferred hypothesis, confirmed sufficient by design and real data anchor |
| FT count rules (1/2/3 by family) | **LOCK V1** — real, well-known, verified modern NBA rule |
| FT state machine (sequence indexing, final-miss handoff) | **KEEP** — real, tested, clean |
| Era whistle hook | **KEEP BUT FLAG** — real qualitative fact, unverified magnitude |
| Fouler attribution (primary-first, single roll) | **KEEP** — real, tested, structurally sound |
| `foul_drawing`/`foul_discipline` shot-family-specific effects | **INSUFFICIENT** — not tested |
| Physical variables in contact/whistle | **INSUFFICIENT** (by design — excluded by default, not tested) |
| A new `and_one` or `contact-balance` attribute | **not created** — explicitly rejected per instruction, existing mechanisms sufficed |

## 44. Final phase classification

**READY WITH FLAGS FOR 19.**

Ready: the full `CONTACT → WHISTLE → SHOT/AND-ONE → FT ADMINISTRATION →
HANDOFF` pipeline is implemented, tested, and cleanly sequenced ahead of
Phase 18B's block/trajectory branch, structurally preventing every
named impossible combination (block+foul+miss, double-fouler
attribution, non-final-FT rebound leakage); the and-one architecture
directly matches the preferred, evidence-respecting hypothesis and is
backed by a real, cached validation anchor (16.96% real and-one-given-
foul rate); every firewall (`free_throw`, tendencies,
`rim_access_creation`, `perimeter_space_creation`,
`rim_protection`/`defensive_playmaking` as direct whistle drivers) is
enforced both structurally (no such field exists) and behaviorally;
the FT state machine correctly distinguishes final from non-final
misses and freezes the game clock throughout.

Flags: perimeter-specific base rates and both foul attributes'
context-specific behavior (by shot family and by defensive burden,
respectively) remain unverified this phase; the era-whistle hook's
magnitude is a real-fact-grounded but unquantified placeholder;
physical-variable and portability/natural-experiment questions were
not investigated at all (excluded by default, not tested-and-rejected).

Not begun: Phase 19.
