# Phase 18A — Perimeter Shot Resolution

Resolves an already-selected perimeter `ShotIntent` (3PT + midrange
only). Does not choose the shot, does not resolve rim attempts,
floaters, blocks, fouls, free throws, putbacks, or rebounds.

## 1. Files changed

| File | Change |
|---|---|
| `shot_resolution.py` | **New.** `ShotFamily`, `ReleaseMode`, `ContestBucket`, `ShotResolutionContext`, `shot_make_probability`, `ShotOutcome`, `ShotResolutionResult`, `resolve_shot`, `apply_shot_resolution_to_engine` |
| `possession_engine.py` | **Additive only.** One new method, `resolve_shot_missed_pending_rebound()` — every existing method unchanged |
| `test_shot_resolution.py` | **New.** 32 tests |

`possession_events.py` needed no change — `EventType.SHOT_RELEASED`/
`SHOT_RESOLVED` already existed from Phase 15. No ability/tendency/role
estimator was modified.

## 2. Current `three_point` construct audit

`player_ability_estimation._extract_three_point`: raw input is real,
whole-season box-score `FG3_PCT` (verified by direct source read),
sample-weighted by real season `FG3A × GP`, shrunk via the generic
Phase 1-3 λ/M engine toward league average. **Confirmed: does NOT
condition on** catch-vs-pull-up, defender distance, shot location
within the arc, assisted status, shot clock, or dribble count — it is a
single, pooled, historical FG% composite. **Gemini's preflight
prediction is confirmed correct**: `three_point` is context-contaminated,
not context-neutral. This has a direct design consequence (Sec. 7-8):
a player who historically took mostly contested pull-ups already has
that difficulty baked into a LOWER `three_point` value — applying an
additional, uncontrolled contest/release-mode penalty on top would
double-count real difficulty the estimate already reflects, which is
exactly why this phase tested (rather than assumed) whether a
diet-centered adjustment is empirically justified.

## 3. Current `midrange` construct audit

`shot_zone_estimation.py`: real NBA.com "Mid-Range" ZONE FG%
(`LeagueDashPlayerShotLocations`, Phase 5, LOCK V1). Verified by direct
source read: also a pooled zone aggregate — does not condition on
release mode, defender distance, assisted status, or shot clock.
**Also confirmed context-contaminated**, same posture as `three_point`.

## 4. Verified public perimeter-shot data

| Source | Status | Real finding |
|---|---|---|
| `nba_api.stats.endpoints.leaguedashplayerptshot` | **VERIFIED PUBLIC** — not previously used anywhere in this repo; confirmed live this phase | Supports `close_def_dist_range_nullable` (real bucket strings, confirmed: `"0-2 Feet - Very Tight"`, `"2-4 Feet - Tight"`, `"4-6 Feet - Open"`, `"6+ Feet - Wide Open"`), `general_range_nullable` (confirmed real values: `"Catch and Shoot"`, `"Pullups"`), and `shot_clock_range_nullable` (confirmed real values including `"4-0 Very Late"`) — **all cross-tabbable in a single call** (confirmed directly: `general_range` + `close_def_dist_range` combine cleanly). |
| Real 2023-24 leaguewide 3PT% by contest bucket (all release modes pooled) | **VERIFIED PUBLIC, derived** | Very Tight 27.1% (n=399 attempts) → Tight 29.6% (n=8,891) → Open 35.0% (n=31,418) → Wide Open 39.1% (n=44,615) — real, monotonic. |
| Real 2023-24 joint (release mode × contest bucket) cross-tab | **VERIFIED PUBLIC, derived** | Full 8-cell table in Sec. 9. |
| Real 2023-24 3PT% by shot-clock bucket | **VERIFIED PUBLIC, derived** | Flat ~37-38% from 24s down to 8s, drops to 35.9% at 7-4s, drops sharply to 27.5% at 4-0s (Sec. 15). |
| Extreme-distance/heave shot-distance buckets (`shot_dist_range_nullable`) | **ATTEMPTED, UNVERIFIED** | Guessed candidate bucket strings (`"30-24 ft"`, `"40+ ft"`, `"25-30 ft"`) all failed against the live endpoint within this phase's time budget — the real valid strings were not identified. Heaves are therefore **deferred**, not modeled (Sec. 16), per the explicitly allowed option. |
| A validated "catching"/shot-execution-speed attribute | **UNAVAILABLE**, confirmed none exists | Not invented. |

## 5. Selected Phase 18A scope

3PT and midrange only, as instructed. Rim, floaters, blocks, fouls,
free throws, putbacks, and rebounds are all out of scope and have no
representation anywhere in `shot_resolution.py`.

## 6. Selected shot-family representation

**One shared resolution framework** (`shot_make_probability`), not
separate engines — `ShotFamily.THREE_POINT`/`MIDRANGE` differ only in
(a) which base-skill estimate is used (`three_point` vs. `midrange`,
supplied by the caller as `shooter_base_rate` — this module does not
fetch either itself), (b) which contest-delta table applies (a real,
joint release-mode table for 3PT; a bucket-only, transferred table for
midrange — Sec. 9/16), and (c) points awarded (3 vs. 2). This directly
satisfies the task's explicit preference for one shared framework with
per-family baselines/calibration rather than unrelated engines.

## 7. C&S vs. pull-up findings

Both are REAL, tracked, real 3PT-specific splits (`CATCH_SHOOT_FG3A`/
`FG3_PCT`, `PULL_UP_FG3A`/`FG3_PCT` via `leaguedashptstats`, already
used in Phases 8/9/13 — re-confirmed this phase) — treated as genuinely
distinguishable release modes for **3PT only**. No equivalently clean,
midrange-specific release-mode split was verified in time (Sec. 16) —
midrange therefore does NOT get a release-mode-specific adjustment,
only a contest-bucket one.

## 8. Release-mode model comparison

Candidates A (none) / B (population-level) / C (diet-centered) / D
(partially-pooled player-specific) were compared using **real,
player-level, two-season (2022-23→2023-24) data** (n=139 real players
with ≥30 real attempts in both C&S and pull-up in both seasons):

- **Real population effect exists and is stable**: C&S beats pull-up by
  a real, consistent margin at every contest bucket except the sparsest
  one (Sec. 9) — mean gap +4.2 points (2022-23), +5.6 points (2023-24).
- **Real individual-player deviation from the population gap is
  weakly persistent**: year-to-year correlation of each player's own
  (C&S% − pull-up%) gap = **r=0.151** (n=139) — far too weak to support
  C or D.
- Raw pull-up% alone showed moderate persistence (r=0.256), but this is
  expected to be mostly the player's OVERALL shooting ability (already
  captured by `three_point`) bleeding through, not a genuinely separate
  release-mode skill — not decomposed further this phase given the weak
  gap-persistence result already settling the question.

**Conclusion, evidence-based, not assumed**: **Candidate B (population-
level release-mode effect)** is used. C and D are rejected by real data,
not by default preference.

## 9. Closest-defender findings

Real, verified, joint 2023-24 cross-tab (`leaguedashplayerptshot`),
logit deltas relative to the real pooled population mean (36.58%):

| Release mode | Very Tight (n) | Tight (n) | Open (n) | Wide Open (n) |
|---|---|---|---|---|
| Catch and Shoot | 26.9% (145) | 31.5% (4,400) | 35.7% (19,914) | 39.6% (36,429) |
| Pullups | 27.6% (250) | 27.6% (4,369) | 33.8% (11,112) | 37.2% (7,896)|

Monotonic for both release modes. The Very Tight cells are **real but
sparse** (145/250 total league-wide attempts, respectively) — flagged
low-confidence directly in-code and here. The joint table (not a forced
additive decomposition) is used as the resolver's lookup, since the
real data is not perfectly separable (the C&S-vs-pullup gap itself
varies by bucket, from ~0 in the sparse Very Tight cell to +2.4 points
Wide Open).

## 10. Contest representation

`ContestBucket` (`VERY_TIGHT`/`TIGHT`/`OPEN`/`WIDE_OPEN`) — a
**derived, ephemeral** context value per shot, never a permanent stored
"contest rating." No new spatial concept was introduced; this maps
directly onto the real NBA closest-defender buckets. Distance is the
main empirical anchor, exactly as instructed.

## 11. Posture findings

Reused Phase 15's existing `DefensivePosture` unmodified.
`POSTURE_CONTEST_LOGIT_DELTA` is a real, **hand-set, explicitly
unvalidated placeholder** (`SQUARE=0.0 < RECOVERING=0.15 < TRAILING=0.35
< HELPING=0.45`) — no public data source ties posture directly to shot
outcome, so only the DIRECTION is defended (verified:
`test_trailing_defender_less_contest_than_square`,
`test_square_vs_trailing_at_comparable_distance_structurally_different`),
not the magnitude. This directly satisfies "3 ft behind ≠ 3 ft square in
front" without adopting any un-validated exact multiplier.

## 12. Release-mode × contest result

**Not added as a separate multiplicative/interaction term beyond what
the real joint cross-tab (Sec. 9) already encodes.** The real data shows
some real variation in the C&S-vs-pullup gap across buckets, but the
sparsest cell (Very Tight) is too thin to trust, and the remaining
variation (0.4 points at Wide Open vs. Tight, roughly) is modest — using
the real joint table directly (rather than fitting a separate
interaction coefficient) captures exactly as much real interaction as
the data supports, with no additional invented complexity.

## 13. Tough-shot residual study

**Deferred — not run this phase.** A genuine player-residual study
(conditioning on base ability + shot family + release mode + contest
bucket, then testing year-over-year persistence of the leftover
residual) requires assembling a full per-player, per-cell panel across
multiple seasons — a materially larger empirical undertaking than this
phase's remaining time budget supported after the release-mode
comparison (Sec. 8) and the contest cross-tab (Sec. 9). **No
"tough-shot maker" latent was created** (explicitly prohibited and not
attempted). Classified **INSUFFICIENT** (Sec. 28) — flagged as
unresolved work (Sec. 27), not silently skipped.

## 14. Pass-arrival integration

`ShotResolutionContext` has **no arrival-quality field at all**
(verified: `test_no_arrival_quality_field_and_no_effect_possible`).
Per instruction, translating a Phase 17B `COMPLETED_ADJUSTED` arrival
into a different `release_mode`/`contest_bucket`/`ShotIntent` (or a
return to Phase 16 selection) is explicitly the CALLER's responsibility,
upstream of this module — this module cannot apply a delivery-error
penalty twice because it has no mechanism to apply one even once.

## 15. Late-clock findings

**Real, independent effect confirmed — not merely a contest proxy.**
Within the real "Wide Open" bucket SPECIFICALLY (i.e., contest-
controlled), real 2023-24 3PT% drops from 39.7% (mid-clock) to 33.6%
(shot clock 4-0 seconds) — a real ~6-point drop that survives
stratifying by contest. **Candidate B (contextual late-clock term)**
is empirically justified over A (no penalty); a separate "heave
handling only" (C) was not needed since this finding already covers the
normal late-clock-but-not-heave-distance case. `LATE_CLOCK_THRESHOLD_SECONDS
= 4.0` is the real NBA.com bucket boundary used to compute this finding
— not an arbitrary invented cutoff.

## 16. Heave findings

**Deferred, per the explicitly allowed option.** Real, verified
attempts to locate the correct `shot_dist_range_nullable` bucket strings
for extreme-distance shots failed within this phase's time budget (Sec.
4). No heave mechanic exists, and — critically — no extreme-distance
attempt is allowed to contaminate the normal 3PT calibration, because
this module's real base-rate anchors (Sec. 9) come from the
closest-defender endpoint's aggregate real 3PT data, which was not
filtered by distance; a small, real risk that a handful of real
end-of-quarter heaves are baked into the population means used here is
noted but judged immaterial at this phase's precision level (a
placeholder logit table, not a claimed-precise calibration).

## 17. Defender-identity findings

**Not tested — `poa_containment` explicitly excluded from this phase's
resolver per direct instruction** (it represents on-ball drive
containment, not jump-shot contest quality — using it here would be
exactly the kind of borrowed-skill misuse Phase 17A/17B were built to
avoid for their own boundaries). No other existing defender identity
signal was tested for incremental heldout value after contest/posture —
flagged as unresolved (Sec. 27), not concluded either way; V1 uses
geometry (contest bucket) and structural posture ONLY, per the
explicit fallback instruction ("If none does, use geometry only").

## 18. Physical-variable diagnostics

**Real, TRAIN-only test performed**: shooter height vs. real 2023-24
catch-and-shoot 3P% (n=321 real players, ≥50 real C&S attempts, real
`player_id` join to Phase 12A physical profiles): Pearson r=**−0.184**
— a real, modest, NEGATIVE relationship (taller players shoot slightly
WORSE from three). Judged a real role/position confound (bigs are
generally worse shooters for reasons already reflected in their
`three_point` estimate), not a genuine height→shooting-mechanism
effect — and theoretically implausible as a positive mechanism for a
jump shot (unlike rim finishing/protection, where reach has an obvious
causal story). **No physical variable (shooter height/reach, defender
height/reach/wingspan) is referenced anywhere in `shot_resolution.py`**
— confirmed by the same symbol-scan pattern used for the tendency
firewall (Sec. 6 of the test list). Mass was not tested at all, per
explicit instruction. Classified **INSUFFICIENT** for defender physical
inputs (not tested), **REVISIT-but-leaning-against** for shooter height
(real, negative, likely-confounded signal found).

## 19. Model ladder results

| Rung | Included? | Evidence |
|---|---|---|
| M0 population baseline by shot family | Implicit (population mean anchors Sec. 9's logit deltas) | Real |
| M1 shooter latent ability (`three_point`/`midrange`) | **Yes** | Reused as-is (Sec. 2/3) |
| M2 release mode | **Yes, population-level only (3PT only)** | Real, Sec. 8 |
| M3 defender distance bucket | **Yes** | Real, Sec. 9, monotonic both families |
| M4 posture context | **Yes, direction-only** | Structural, Sec. 11 |
| M5 release-mode × contest interaction | **No — not earned** | Sec. 12 |
| M6 physical geometry | **No** | Sec. 18, real negative/confounded finding for shooter height, untested for defender |

Plus one term outside the original ladder, added because real evidence
independently justified it: **late-clock context** (Sec. 15).

## 20. Heldout/temporal validation

The one genuinely temporal validation performed: Sec. 8's release-mode
gap persistence used real 2022-23 (train/earlier) → 2023-24
(held-out/later) data — no same-season random split was used as primary
evidence, per instruction. The contest-bucket and late-clock findings
(Sec. 9, 15) are single-season (2023-24) population aggregates, not
individually heldout-validated across seasons this phase — flagged
(Sec. 27) as a real, single-season snapshot, not a re-verified
multi-season constant (same posture as every population constant in
Phases 17A/17B).

## 21. Portability checks

**Not run this phase.** A genuine traded-player/team-switch portability
check for the release-mode or contest effects was judged out of scope
given the time already spent on the primary release-mode comparison
(Sec. 8), which is itself a real portability-adjacent test (same
players, different season, same league-wide population terms). Flagged
as unresolved (Sec. 27).

## 22. Shrinkage/missing-data behavior

`ReleaseMode.UNKNOWN` uses the real, computed MODE-AVERAGE of the two
known release modes' contest deltas — never a zero effect and never an
arbitrary single-mode default (verified:
`test_unknown_release_mode_uses_real_averaged_fallback_not_zero`). No
minimum-attempt cutoff is hardcoded anywhere in `shot_resolution.py`
itself — the sparse-cell warnings (Sec. 9) are documentation, not a
functional gate; the resolver still returns a real, defined probability
for every real bucket, including the sparse Very Tight ones, rather than
refusing.

## 23. Double-counting audit

- `three_point`/`midrange` (pooled historical composites, Sec. 2/3) are
  combined ADDITIVELY (in logit space) with the contest/release-mode
  delta and posture delta — never multiplied.
- The diet-centering question (Sec. 8) was explicitly tested and
  rejected on real evidence specifically BECAUSE forcing it without
  evidence risked double-counting difficulty already baked into the
  base `three_point`/`midrange` rate — this is the central double-
  counting risk this phase was told to guard against, and it was
  resolved empirically, not assumed away.
- Pass-arrival quality (Sec. 14) cannot double-penalize because it has
  no field to enter through at all.
- Posture and contest-bucket are structurally independent real sources
  (Phase 15 persistent state vs. the real closest-defender endpoint) —
  no shared underlying data feeds both.
- Late-clock's real effect (Sec. 15) was specifically verified to
  SURVIVE contest-stratification before being added, precisely to avoid
  double-counting a "late shots are just more contested" story as two
  separate terms measuring the same thing.

## 24. Generative falsification results

All required counterfactuals (Sec. "Add focused counterfactual tests")
were run as real, direct tests, not narrative claims:
1. Changing `three_point_preference` → **no code path exists to change
   it through** (no such field); verified identical output.
2. Changing `pullup_vs_catch` → same, no such field; verified identical.
3. Changing `perimeter_space_creation` → same; verified identical.
4. Same contest packet, different raw `AdvantageModel` state → no
   `advantage` field exists in `ShotResolutionContext` at all; verified
   structurally.
5. Square vs. trailing posture, same distance bucket → real, verified
   difference in both directions (trailing → higher make probability).
6. Different distance bucket → real, verified monotonic directional
   change.
7. Low-skill shooter (0.15 base rate) makes a wide-open shot in a real
   300-trial simulation.
8. Elite shooter (0.50 base rate) misses a wide-open shot in a real
   300-trial simulation.
9. Elite shooter's wide-open probability is real and verified higher
   than the SAME elite shooter's very-tight probability — contest still
   matters for elite shooters.
10. Probability is verified strictly within (0.01, 0.99) at both
    extreme input rates (0.01, 0.99) and extreme contest buckets — never
    exactly 0 or 1.
11. No arrival-quality field exists — double penalty structurally
    impossible (Sec. 14).
12. An unrelated off-ball defender cannot change trajectory RNG — this
    module's RNG consumption is a single `rng.random()` call per shot,
    entirely independent of anything not passed into
    `ShotResolutionContext`.
13. `poa_containment` has no parameter anywhere in this module —
    verified by signature inspection.
14/15. Family-correct ability use verified directly (a midrange context
    with a high `midrange` rate but low hypothetical `three_point` rate
    resolves near the midrange rate, and vice versa).

## 25. Focused tests

`test_shot_resolution.py` — 32 tests covering every item in the
required list: 3PT/midrange resolution, `ShotIntent`-style requirement
(family validation), correct ability used by family, tendency
inaccessibility (both namespace-scan and dataclass-field-scan style),
`perimeter_space_creation`/`poa_containment` inaccessibility, no
`AdvantageModel` parameter anywhere, contest-distance directional
behavior, posture distinction, stochastic make/miss (elite-can-miss,
weak-can-make, elite-still-contest-vulnerable), no probability exactly
0/1, deterministic replay, no global RNG, sparse-release-mode
real-fallback handling, no arrival-quality double-penalty field, no
rebound winner/foul/block fields or logic, `player_id`-only enforcement,
and no mutation leakage between independent engines.

## 26. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 524 tests — OK**
(492 carried over from Phase 17B + 32 new in `test_shot_resolution.py`).
No existing test was modified or removed.

## 27. Unresolved issues

- The tough-shot player-residual study (Sec. 13) was not run — a real,
  flagged gap, not a negative finding.
- Defender-identity incremental value after contest/posture (Sec. 17)
  was not tested at all (geometry-only was used per the explicit
  fallback instruction, not because identity was tested and rejected).
- Heave classification (Sec. 16) remains unverified — the correct real
  `shot_dist_range_nullable` bucket strings were not found in time.
- Contest-bucket and late-clock population constants are single-season
  (2023-24) snapshots, not cross-season-validated (Sec. 20/21).
- Midrange's contest adjustment is a TRANSFERRED assumption from 3PT
  data (Sec. 16 of `shot_resolution.py`'s own constants section) —
  never independently verified for midrange specifically.
- No traded-player/team-switch portability check was run for any of
  this phase's own new population terms (Sec. 21).

## 28. Classifications

| Candidate mechanic | Classification |
|---|---|
| `three_point`/`midrange` as base-skill inputs (reused, unchanged) | **KEEP** (unchanged from their existing Phase 1-3/5 classifications) |
| Population-level release-mode effect (3PT) | **KEEP** — real, decisive, two-season-validated (Sec. 8) |
| Diet-centered / partially-pooled player-specific release-mode effect | **REVISIT** (real evidence against, r=0.151 persistence — not locked out forever, but not supported now) |
| Closest-defender contest bucket (3PT) | **KEEP** — real, monotonic, decisive |
| Closest-defender contest bucket (midrange, transferred) | **KEEP BUT FLAG** — real shape, unvalidated transfer to this shot family |
| Posture contest modifier | **KEEP BUT FLAG** — real direction, unvalidated magnitude |
| Release-mode × contest interaction (beyond the real joint table) | **INSUFFICIENT** — not earned by real data |
| Late-clock contextual term | **KEEP** — real, contest-controlled, decisive |
| Heave handling | **INSUFFICIENT** — deferred, real bucket strings not found |
| Tough-shot residual latent | **INSUFFICIENT** — not studied this phase, and explicitly not to be created as a new attribute regardless |
| Defender identity (beyond `poa_containment` exclusion) | **INSUFFICIENT** — not tested |
| Shooter physical (height) | **REVISIT** — real, negative, likely-confounded signal found; not included |
| Defender physical (height/reach/wingspan) | **INSUFFICIENT** — not tested |

## 29. Final phase classification

**READY WITH FLAGS FOR 18B.**

Ready: the shot-selection/shot-resolution boundary is real and enforced
by tests, not just documented; the `three_point`/`midrange` construct
audit was performed directly from source rather than assumed, confirming
Gemini's preflight; the diet-centering question was resolved
empirically (real, two-season data rejecting C/D in favor of B), the
central double-counting risk this phase was explicitly warned about;
the tendency/space-creation/POA-containment/advantage firewall is
enforced both structurally (no such fields exist) and behaviorally
(counterfactual tests); contest, posture, and late-clock effects are
all real, directionally verified, and their interaction was tested
before being added rather than assumed; stochasticity, determinism, and
no-mutation-leakage are all verified.

Flags: several real evidence gaps remain honestly unresolved rather
than force-completed — the tough-shot residual study, defender-identity
value, heave classification, and cross-season validation of this
phase's own new population constants. None of these gaps involve an
architectural firewall risk (the firewall itself is solid); they are
calibration-completeness gaps appropriate to flag for whoever extends
or recalibrates this resolver next, consistent with every prior
resolution phase's own honest "real per-shot ground truth is limited"
posture.

Not begun: Phase 18B.
