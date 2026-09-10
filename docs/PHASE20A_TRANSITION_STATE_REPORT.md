# Phase 20A — Possession Change, Floor Balance & Transition State Generation

Initializes new-possession structural state only — does not execute the
transition attack (no outlet pass, advance dribble, or transition shot
selection/resolution; explicitly Phase 20B).

## 1. Files changed

| File | Change |
|---|---|
| `transition_state.py` | **New.** `PossessionChangeSource`, `classify_source`, `relational_tag`, `FloorPlayer`, `TransitionState`, `initialize_transition_state` |
| `test_transition_state.py` | **New.** 29 tests |

**Zero changes to any existing file** — this phase reuses Phase 15's
`PossessionState`/`PossessionEngine`/`EventType.POSSESSION_START`/
`era_rules` and Phase 15's `SpatialMagnitudeAdvantage` verbatim; no new
engine method was needed.

## 2. Existing interfaces inspected

Confirmed directly: `PossessionState.with_ball_carrier` already resets
`ball_control` to a fresh `LIVE_DRIBBLE` for a new carrier (reused,
`initialize_transition_state` calls it rather than reimplementing);
`engine.era_rules.shot_clock_seconds` already gives the real, era-aware
full-reset value (reused directly — no separate rule representation was
needed since this is an ordinary new possession, not an OREB, so
`oreb_reset_value` doesn't apply here). Phase 19's DREB/steal/turnover
outputs (Phases 17B/18B/19) all converge on the same real
`PossessionState` shape this module consumes — no parallel state system
was created.

## 3. Exact Phase 20A scope selected

Possession-change source classification, the minimal new-possession
state packet, floor-balance representation (relational tags + raw
counts, not a locked tier), old-advantage destruction + fresh
derivation, matchup/posture cleanup, and era-aware shot-clock
initialization. No outlet pass, advance dribble, transition shot
selection/resolution, fast-break rating, or sprint-speed latent.

## 4. Possession-change source audit

| Source | Repo pathway | Classification |
|---|---|---|
| Defensive rebound | Phase 19 `secure_defensive_rebound_from_loose` | LIVE |
| Live steal | Phase 17B `CLEAN_INTERCEPTION` | LIVE |
| Live bad-pass interception | Phase 17B `BAD_PASS_TO_DEFENDER` | LIVE |
| Loose-ball recovery | Phase 15/17B/19 scramble resolving to defensive control | LIVE |
| Dead-ball turnover | Phase 15 `dead_ball_turnover` / Phase 17B `BAD_PASS_OUT_OF_BOUNDS` | DEAD |
| Made FG inbound | Phase 18A/18B `resolve_shot_made` | DEAD |
| Block recovery (defense) | Phase 19 rebound of an `UNRESOLVED_BLOCK` | CONTEXT_DEPENDENT |
| Period start | game-level | DEAD |

## 5. Live-vs-dead-ball classification

LIVE sources may preserve real spatial imbalance (the defense had no
time to organize) — `player_zones` and a freshly-derived `AdvantageModel`
are retained. DEAD_BALL sources begin structurally neutral BY
CONSTRUCTION — verified directly (`test_no_live_transition_residue`):
even if a caller supplies real `player_zones` for a dead-ball source,
`initialize_transition_state` discards them and forces `advantage=None`,
implementing "do not preserve live transition overload through a dead
ball without evidence." `CONTEXT_DEPENDENT` (block recovery) is handled
identically to LIVE for state-derivation purposes but is kept as its
own named source value (not silently folded into `DEFENSIVE_REBOUND`),
honoring the real, unusual defender geometry a block can leave without
inventing a special bonus for it.

## 6. New possession-state contract

`TransitionState(source, new_offense_team_id, new_defense_team_id,
ball_carrier_id, ball_zone, player_zones, shot_clock_remaining,
advantage)` — every candidate field from the task's own list was
challenged: a numerical-advantage TIER enum was rejected (Sec. 9), a
`transition_eligible` boolean was rejected as redundant with derivable
state (`defenders_back_count`/`offense_ahead_count` already tell the
story), an outlet-receiver field was rejected as Phase 20B's job, and a
timestamp/elapsed-time field was rejected as unnecessary this phase (no
decay is implemented here).

## 7. Floor-balance representation

`FloorPlayer(player_id, zone, side)` — reuses the existing 8-zone
topology unmodified; no continuous coordinate was introduced.
`relational_tag(player_zone, ball_zone)` derives `AHEAD_OF_BALL`/
`BEHIND_BALL`/`NEAR_BALL` from a real, simple "distance from the
defended rim" ranking over the 8 zones (`RESTRICTED_RIM`=0 through
`BACKCOURT`=4) — a general, reusable ordering, not a per-pair lookup
table.

## 8. Relational-tag verdict

**Kept, but computed as a `TransitionState` PROPERTY, never a stored
field** — verified directly that `relational_tags`/`defenders_back_count`/
`offense_ahead_count` are all `@property` methods recomputed fresh from
`player_zones`/`ball_zone` every access, so they can never drift out of
sync with the underlying geometry or be accidentally carried over from
a prior state.

## 9. Numerical-advantage representation verdict

**Candidate B chosen: raw integer counts** (`defenders_back_count`,
`offense_ahead_count`), NOT a discrete tier enum ("defense set/offense
edge/scrambled" was explicitly named in the task as NOT to be locked).
Verified directly (`test_counts_are_plain_integers_not_enum`) that both
are plain Python `int` values, giving Phase 20B raw, interpretable
numbers to build its own push/outlet/reset policy on, rather than a
pre-digested category this phase would have had to invent thresholds
for.

## 10. Old-advantage firewall implementation

`initialize_transition_state` never reads `engine.advantage` at any
point — it only ever WRITES to it, either `None` (dead-ball sources, or
live sources with no real spatial info) or a freshly-constructed
`SpatialMagnitudeAdvantage` derived solely from the CURRENT
`player_zones`/`ball_zone` arguments. Verified directly with the
strongest form of this test
(`test_identical_geometry_different_old_advantage_yields_identical_new_state`):
three different starting `engine.advantage` values (two real
representations plus `None`) all produce byte-for-byte identical
`(defenders_back_count, offense_ahead_count, new_advantage_magnitudes)`
output given the same real geometry.

## 11. DREB integration

`PossessionChangeSource.DEFENSIVE_REBOUND` → LIVE. Verified directly
(`test_dreb_creates_new_team_possession`) that the rebounder correctly
becomes the new offense's ball carrier. `defensive_rebounding` is never
read anywhere in this module (verified by symbol-name scan) — the
rebound has ALREADY been resolved by Phase 19; this module only
consumes its OUTPUT (rebounder_id, team ids), never re-applies the
skill.

## 12. Steal/interception integration

`LIVE_STEAL` → LIVE. Verified (`test_live_steal_creates_new_possession`).
No steal-related ability is read anywhere in this module — the steal
was already resolved by Phase 17B.

## 13. Lost-ball recovery integration

`LOOSE_BALL_RECOVERY` is classified LIVE — by the time this module acts,
the ball is already `HELD` by the real recovering player (Phase 15/19's
own job to resolve WHO recovered it); this module only initializes the
resulting new-possession state, same treatment as a clean steal, since
no real evidence was gathered this phase to justify a materially
different floor-geometry treatment for a scramble recovery vs. a clean
interception (flagged, Sec. 41).

## 14. Block-recovery integration

`BLOCK_RECOVERY_DEFENSE` is its own named `CONTEXT_DEPENDENT` source
(Sec. 4/5) — handled with the same real derivation logic as LIVE
sources (real geometry in, real geometry out), but deliberately NOT
merged into `DEFENSIVE_REBOUND` naming, preserving the real distinction
for any future phase that wants to treat it differently once better
evidence exists.

## 15. Made-basket handling

**Candidate A chosen: neutral inbound state** — the smallest defensible
V1 option, per the task's own suggested default. Verified directly
(`test_does_not_copy_old_advantage`): a made-basket possession change
discards any supplied `player_zones` and forces `advantage=None`, same
treatment as every other DEAD_BALL source. No 7-seconds-or-less
behavior was built.

## 16. Dead-ball turnover handling

Same DEAD_BALL treatment as made baskets (Sec. 15) — verified directly
(`test_no_live_transition_residue`).

## 17. Period-start handling

`PERIOD_START` classified DEAD_BALL — fully neutral, no residue of any
kind, consistent with "no transition asymmetry should carry across
quarter breaks."

## 18. Player-position carryover

Only for LIVE/CONTEXT_DEPENDENT sources, and only when the caller
actually supplies real `player_zones` — this module never invents a
position, and never teleports anyone to a halfcourt position; it either
uses the real, caller-supplied geometry or has none at all (graceful
degradation, Sec. 34).

## 19. Matchup cleanup

**Candidate A chosen: clear all matchups.** Verified directly
(`test_stale_matchups_cleared`): `engine.state.assignments` is reset to
`{}` on every possession change, regardless of source — the simplest,
safest V1 option, per instruction's own suggested minimum; downstream
defense (a future phase) reconstructs real assignments from the new
geometry rather than inheriting a stale, now-inverted pointer.

## 20. Defensive-posture handling

No posture values are carried over explicitly in this V1 — clearing
`assignments` entirely (Sec. 19) also clears every associated
`DefensivePosture`, since posture lives on `DefensiveAssignment`
objects. This is a real, deliberate simplification: rather than
attempting to selectively preserve "safe" postures (per the task's own
menu of options), V1 clears everything and lets the geometry itself
(via `relational_tag`) carry the real structural information forward.

## 21. Compromised-region reconstruction

`_derive_fresh_advantage` builds compromised regions ONLY from real
defenders tagged `BEHIND_BALL` (caught out of position relative to the
new ball location) — never copied from the old offense's advantage
state (Sec. 10). A real, hand-set, explicitly flagged placeholder
magnitude (0.3) is applied per such defender's zone — direction only is
defended (more caught-behind defenders → more real compromise),
magnitude unvalidated (same posture as every prior resolution phase's
placeholder constants).

## 22. Crash/retreat residue handling

**No crash/retreat system exists in this repo** (confirmed by Phase 19's
own audit, reused here) — this module consumes whatever `FloorPlayer`
list the caller supplies (which may reflect real crash/retreat
decisions from a FUTURE team/system layer) without ever deciding who
crashed or retreated itself. `team_oreb_crash_rate` or any such concept
was NOT invented here.

## 23. Transition-eligibility representation

**Explicitly rejected as a separate stored field** — judged redundant:
`classify_source(source)` already tells a caller whether the state IS
transition-capable, and `defenders_back_count`/`offense_ahead_count`
already tell the caller HOW imbalanced it actually is. Adding a third,
separately-stored boolean would risk drifting out of sync with the two
already-authoritative signals.

## 24. Timing metadata

**Not included.** No timestamp/elapsed-time-origin field was added to
`TransitionState` — decay is explicitly Phase 20B's job, and this
module's own `dt` parameter (passed through to the event log) is
sufficient for now; no separate persistent timing field was judged
necessary.

## 25. Shot-clock integration

`engine.era_rules.shot_clock_seconds` — a real, full reset via Phase
15's existing era-rules authority, verified directly
(`test_shot_clock_from_era_rules_not_hardcoded`,
`test_no_hardcoded_24_literal`: no bare `24.0`/`= 24` literal exists
anywhere in the module) and verified to work correctly for a real
historical (pre-modern) era too
(`test_works_without_modern_tracking_fields`, 1996-97 season, same real
24-second classic-era value, via era rules, not tracking data). 20A only
INITIALIZES the shot clock; it does not own future decrement behavior.

## 26. Empirical public-data audit

| Item | Classification |
|---|---|
| `leaguedashteamstats` (Misc measure): `PTS_OFF_TOV`, `PTS_FB`, `PTS_2ND_CHANCE`, `PTS_PAINT` | **VERIFIED PUBLIC** — confirmed live this phase; real, well-known, TEAM-level aggregate categories |
| Possession-change-source-specific (steal vs. DREB vs. dead-ball) early-offense outcome breakdown | **UNCERTAIN** — the verified fields above are team-season totals, not individually attributed by possession-change source or event; no live cross-tab was found/verified this phase |
| Player-location/tracking data at the moment of a live possession change | **PROPRIETARY/UNAVAILABLE** — confirmed no such public field exists (same finding as every prior resolution phase's spatial-data audits) |
| Rebound location (already verified, Phase 19) | **VERIFIED PUBLIC** (reused finding, not re-verified this phase) |

## 27. Possession-source empirical findings

**Real, team-level confirmation only**: `PTS_FB`/`PTS_OFF_TOV` exist as
real, distinct, official NBA statistical categories, confirming that
"transition scoring" is a real, measurably distinct outcome category
from ordinary half-court scoring at the team-season level. This
supports the qualitative premise that possession-change source matters
for early-offense outcomes, but does NOT provide event-level or
player-level causal identification of WHICH sources drive it most —
that would require the proprietary tracking data this project has
consistently found unavailable. No individual-event study was
performed this phase (flagged, Sec. 41).

## 28. Floor-balance empirical findings

**Not independently studied this phase** — no live public data source
for player-location-at-possession-change was found (Sec. 26), so no
empirical floor-balance-outcome relationship was tested. The
relational-tag/count representation (Sec. 7-9) is architecturally
motivated (a general, reusable, non-arbitrary derivation from existing
zones), not empirically fit to any outcome data.

## 29. State-model comparison

Compared T0 (source only) through T5 (matchup/posture residue).
**Chosen: T0+T1+T2+T3** (source, ball/rebound zone, coarse player
zones, relational ahead/behind counts) — T4 (compromised-region
representation) is included too, since it's a direct, cheap derivation
from T2/T3 already computed. T5 (matchup/posture residue) was
explicitly REJECTED (Sec. 19/20: matchups are cleared, not carried) —
judged the safer, simpler V1 choice given no evidence that a stale,
inverted matchup pointer would be more useful than a clean slate for
downstream defense reconstruction.

## 30. AdvantageModel integration

Reuses the existing, unmodified `AdvantageModel` interface —
`_derive_fresh_advantage` constructs a real `SpatialMagnitudeAdvantage`
instance (one of Phase 15's two existing representations) rather than
inventing a new one, per "if transition requires a new AdvantageState
representation, keep it minimal and additive" — no new representation
was needed at all; the existing one sufficed.

## 31. Possession identity handling

Real, stable `player_id` only throughout — verified directly
(`test_rejects_name_keyed_carrier`: a name string raises `TypeError` via
the reused `_assert_player_id` guard). Exactly one team owns possession
at all times (verified: `test_exactly_one_team_owns_possession`) and no
stale old-offense carrier remains (verified:
`test_no_stale_old_offense_carrier`).

## 32. Event semantics

Reuses `EventType.POSSESSION_START` (already existed since Phase 15) —
no new event type was needed. The event's `metadata` carries `source`,
`classification`, `defenders_back`, and `offense_ahead`, giving a future
accounting phase (24) everything needed to reconstruct why a possession
began the way it did, without inventing a `TRANSITION_STATE_INITIALIZED`
event on top (judged event-vocabulary bloat for no added real
information).

## 33. Deterministic replay

No RNG is used anywhere in `transition_state.py` — the entire module is
a pure, deterministic function of its real inputs (verified:
`test_no_global_rng`, and `test_deterministic_no_rng_needed` confirms
two independent calls with identical inputs produce identical derived
counts). This satisfies the task's own preference ("prefer no
randomness if new transition state can be derived deterministically")
more strongly than isolation would have — there was no stochastic
decision to isolate at all.

## 34. Missing-state fallback

Verified directly (`test_missing_player_zones_does_not_crash`): calling
`initialize_transition_state` with `player_zones=None` produces a valid
`TransitionState` with `player_zones=()`, `defenders_back_count=0`,
`offense_ahead_count=0` — a real, honest "no spatial info available"
state, never a crash and never a fabricated coordinate.

## 35. Historical-era fallback

Verified directly (`test_works_without_modern_tracking_fields`): a
1996-97-season engine produces a correct, real 24-second shot-clock
reset via era rules with zero modern tracking fields required anywhere
in this module.

## 36. Counterfactual tests

All 40 required numbered counterfactuals were addressed, either by a
direct test or by a structural absence proof (no field/symbol exists to
even change): DREB/steal create new possession; dead-ball turnover
carries no live residue; made FG doesn't copy old advantage; old
advantage never survives (both the simple and the strong
multi-representation test); DREB/steal/rim-protection/shooting-ability
skills have zero effect after their respective resolution phases
already ran (none is even readable from this module); `role_off_initiation`/
`pass_vs_shoot`/`drive_aggression`/`three_point_preference` have no
field anywhere in this module (verified by dataclass-field and
namespace scans); ball carrier belongs to the new offense; exactly one
team owns possession; no stale carrier/matchup remains; DREB rebounder
initializing as carrier doesn't imply pushing (no push/advance logic
exists at all — Sec. "no action execution" test); no transition-specific
shooting attribute or sprint-speed latent exists; `player_id`-only;
deterministic replay; missing tracking doesn't crash; historical state
works without modern tracking; shot-clock reset uses era rules; DREB
doesn't auto-assign an outlet receiver or auto-create numerical
advantage (the counts are DERIVED from whatever real geometry is
supplied, never forced to a nonzero value).

## 37. Double-counting matrix

| Pair | Risk |
|---|---|
| Old `AdvantageState` × new `TransitionState` | **SAFE** — verified, never read (Sec. 10) |
| DREB/steal skill × transition opportunity | **SAFE** — neither skill is read anywhere in this module |
| Crash state × floor balance | **SAFE** — this module only consumes caller-supplied `FloorPlayer` data, never infers crash behavior itself |
| Role × ball carrier | **SAFE** — no role field exists; the carrier is exactly whoever Phase 17B/18B/19 already determined |
| Pace × transition opportunity | **SAFE** — no pace concept referenced anywhere |
| Physicals × movement speed | **SAFE** — no physical field exists anywhere in this module |
| Possession source × transition classification | **SAFE** — a deliberate, direct, one-to-one, auditable mapping (Sec. 4), not a derived/inferred relationship at risk of drifting |

## 38. Generative falsification tests

Directly verified: changing only WHICH defender got the rebound (a
different real `player_id` in `player_zones`) changes the resulting
`defenders_back_count`/derived advantage structurally, while changing
the PREVIOUS shooter's `three_point`/`rim_protection`/old
`AdvantageState` — none of which this module can even read — produces
no change at all given identical real geometry (the strong
multi-representation test, Sec. 10).

## 39. Focused test results

`test_transition_state.py` — 29 tests covering: source classification
(all 8 real sources), relational-tag derivation, DREB/steal/dead-ball/
made-basket handoffs, the old-advantage firewall (both simple and
strong multi-representation forms), skill-firewall scans (dataclass
fields + module namespace), the numerical-advantage-is-count-not-enum
verdict, ball ownership invariants, matchup cleanup and ball-control
reset, no-action-execution (a code-symbol scan, not a docstring
false-positive), era-aware shot-clock behavior (with an explicit
no-hardcoded-literal check), determinism, missing-state graceful
fallback, historical-era fallback, and `player_id`-only enforcement.

## 40. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 662 tests — OK**
(633 carried over from Phase 19 + 29 new in `test_transition_state.py`).
No existing test was modified or removed.

## 41. Unresolved issues

- Possession-change-source-specific early-offense empirical findings
  (Sec. 27) are real but team-level only — no event/player-level causal
  study was performed (proprietary tracking data unavailable).
- Loose-ball recovery and clean-interception are treated identically
  (Sec. 13) — a real, flagged simplification pending better evidence
  that they should differ.
- The compromised-region placeholder magnitude (0.3 per behind-ball
  defender, Sec. 21) is real, directionally-defended, but
  magnitude-unvalidated, same honest posture as every prior resolution
  phase.
- No floor-balance-outcome empirical validation exists (Sec. 28) — the
  representation is architecturally, not empirically, motivated.

## 42. Classifications

| Candidate mechanic | Classification |
|---|---|
| Possession-change source classification (LIVE/DEAD/CONTEXT_DEPENDENT) | **LOCK V1** — real, audited, directly mapped to existing repo pathways |
| Old-advantage firewall (fresh derivation, never copied) | **LOCK V1** — the phase's own central doctrine, verified with the strongest test form available |
| Relational tags + raw counts (not a tier enum) | **KEEP** — real, general, non-arbitrary derivation |
| Matchup clear-all (Candidate A) | **KEEP** — simplest, safest, tested |
| Fresh `AdvantageModel` derivation from behind-ball defenders | **KEEP BUT FLAG** — real direction, unvalidated magnitude |
| Made-basket / dead-ball neutral treatment | **KEEP** — matches the task's own recommended smallest defensible option |
| `transition_eligible` as a separate field | **not created** — explicitly rejected as redundant |
| Loose-ball-recovery-vs-clean-interception distinction | **INSUFFICIENT** — not empirically differentiated |
| Possession-source-specific early-offense causal effect | **INSUFFICIENT** — only team-level category existence verified, not event-level causality |

## 43. Final phase classification

**READY WITH FLAGS FOR 20B.**

Ready: the possession-change source taxonomy is real, audited, and
directly mapped to every existing repo pathway; the old-advantage
firewall (this phase's own central doctrine) is implemented and
verified with the strongest available test (three structurally
different old-advantage states producing byte-for-byte identical new
state); floor-balance representation reuses the existing 8-zone
topology with a general, non-arbitrary relational derivation rather
than an invented tier system; matchup/posture cleanup is simple, safe,
and tested; the shot clock correctly routes through era rules with an
explicit anti-hardcoding test; the module is entirely deterministic
(no RNG at all) and degrades gracefully with missing spatial
information, including full historical-era operation.

Flags: possession-source-specific early-offense findings remain
team-level only (no event/player causal study — proprietary data
unavailable); loose-ball recovery and clean interception are treated
identically pending better evidence; the fresh-advantage derivation's
magnitude is a real, flagged placeholder.

Not begun: Phase 20B.
