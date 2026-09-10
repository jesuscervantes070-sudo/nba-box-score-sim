# Phase 19 — Rebound Opportunity + Rebound Resolution

Central requirement enforced throughout: a player must have a REAL,
structural rebound opportunity before `offensive_rebounding`/
`defensive_rebounding` is consulted at all. No automatic putbacks,
transition engine, outlet passing, `orb_crash`, `vertical_pop`, generic
strength, loose-ball foul system, or continuous XY physics were built.

## 1. Files changed

| File | Change |
|---|---|
| `rebound_resolution.py` | **New.** `ReboundOpportunity`, `ReboundCandidate`, `BoxOutState`, `ReboundSource`, `eligible_rebound_candidates`, `resolve_rebound`, `apply_rebound_to_engine` |
| `possession_engine.py` | **Additive only.** Three new methods: `secure_offensive_rebound_from_loose`, `secure_defensive_rebound_from_loose`, `credit_team_rebound` — every existing method unchanged |
| `test_rebound_resolution.py` | **New.** 33 tests |

No ability/tendency/role estimator was modified.

## 2. Inspected existing interfaces

Confirmed directly: Phase 15 already had `resolve_shot_missed_offensive_rebound`/
`resolve_shot_missed_defensive_rebound` (real SECOND_CHANCE/era-shot-
clock-reset/advantage-clearing logic), but both are guarded on
`SHOT_IN_FLIGHT` — a real state Phase 19 never actually receives, since
Phases 18A/18B/18C all transition to `LOOSE` first (via
`resolve_shot_missed_pending_rebound`/`block_secured_by_defense`/the
final-missed-FT handoff) precisely so they would NOT select a rebound
winner themselves. This is a real, concrete architecture gap — not an
inconsistency to redesign, but a genuine missing bridge. Resolved by
adding three new, purely additive engine methods that reuse the
IDENTICAL real logic, just re-guarded on `LOOSE` (Sec. "files changed").

## 3. Selected Phase 19 scope

Reboundable missed FG, reboundable unresolved block, reboundable final
missed FT; coarse carom region (Candidate A/E, Sec. 7); player
eligibility; box-out/leverage context (structural, caller-supplied);
OREB vs. DREB acquisition; secure/team-rebound outcomes (no tip state,
Sec. 22); correct possession/second-chance handoff. No full transition
engine, no putback automation, no loose-ball foul system, no lane-slot
geometry, no continuous XY, no vertical leap, no coach crash/retreat AI.

## 4. `offensive_rebounding` construct audit

Confirmed by direct source read (`player_ability_estimation._extract_off_rebounding`,
Phase 1-3, KEEP): real OREB_PCT when available (`data_source.build_and_cache_player_rebound_splits`),
with an explicit, labeled historical fallback (splitting a combined
`reb_pct` by the player's own real OREB/total-REB share) when true
OREB_PCT isn't cached for that season. **Real, decisive diagnostic run
this phase** (2023-24, n=394, real `leaguedashptstats` Rebounding-
measure data): OREB_PCT correlates **r=0.535** with the real, more
granular `OREB_CHANCE_PCT` (rebounds secured out of real, individually-
tracked rebound CHANCES). Moderate, not 1.0/0.0 — OREB_PCT carries real
skill signal but also real, substantial opportunity-driven variance not
explained by chance-conditional acquisition alone. `orb_crash` (Phase
11's rejected, ~0.996-correlated-with-OREB candidate) is confirmed
**not revived** — no persistent "crash propensity" concept exists
anywhere in this module (verified by symbol-name scan).

## 5. `defensive_rebounding` construct audit

Same module (`_extract_def_rebounding`, Phase 1-3, KEEP): real
DREB_PCT with the mirror historical fallback. Real 2023-24 population
context (Sec. 6): mean `DREB_CHANCE_PCT`=60.5%, mean `DREB_CONTEST_PCT`=21.6%
(defensive rebounds are contested far less often than offensive ones,
21.6% vs. real 48.8% for OREB — a real, decisive, basketball-plausible
asymmetry). No separate DREB-vs-chance-pct correlation was computed
this phase (time-budget choice, flagged Sec. 44) — the OREB-side
diagnostic (Sec. 4) is treated as sufficiently representative of the
same real construction pattern for both attributes.

## 6. Verified public rebound data

| Item | Classification | Finding |
|---|---|---|
| `leaguedashptstats` (Rebounding measure) — `OREB_CHANCES`/`OREB_CHANCE_PCT`/`OREB_CONTEST`/`OREB_CONTEST_PCT`/`AVG_OREB_DIST` (+ DREB/REB mirrors) | **VERIFIED PUBLIC** — not previously used anywhere in this repo; confirmed live this phase | Real, decisive: n=394 real players ≥30 OREB chances, mean `OREB_CHANCE_PCT`=42.2%, mean `OREB_CONTEST_PCT`=48.8%; n=356 ≥100 DREB chances, mean `DREB_CHANCE_PCT`=60.5%, mean `DREB_CONTEST_PCT`=21.6%; leaguewide mean `AVG_OREB_DIST`=9.2ft, `AVG_DREB_DIST`=7.0ft |
| `leaguehustlestatsplayer` — `OFF_BOXOUTS`/`DEF_BOXOUTS`/`BOX_OUT_PLAYER_REBS`/`BOX_OUT_PLAYER_TEAM_REBS`/`PCT_BOX_OUTS_REB` | **VERIFIED PUBLIC** — confirmed live this phase, not previously used | Real, player-level box-out data exists (Sec. 15) |
| Rebound-chance distance conditioned on SHOT FAMILY specifically | **UNCERTAIN** — the real `AVG_OREB_DIST`/`AVG_DREB_DIST` fields are player-level aggregates, not shot-family-cross-tabbed in this endpoint | No live cross-tab was found/verified this phase (Sec. 8) |
| Box-out player identity, per-rebound-event linkage | **LIKELY DERIVABLE** (season aggregates are real and public; event-level box-out-to-specific-rebound linkage was not verified) | Not ingested this phase |
| Blocked-shot rebound-recovery-specific rate split | **UNCERTAIN** — not independently verified via a live call this phase | Sec. 9 |
| Team-rebound (no individual credited) real semantics | **VERIFIED PUBLIC** (standard, well-known real NBA box-score convention: `TEAM_REBOUNDS` exists as a distinct real stat category) | Reused conceptually, not re-verified via a fresh live call |

## 7. Carom representation study

Compared candidates A (shot-zone-only region), B (coarse carom
sectors), C (short/medium/long class), D (shot-family × miss-type
context), E (no explicit carom class, eligibility only). **Chosen: A/E
hybrid** — a single, real, evidence-anchored default carom zone
(`RESTRICTED_RIM`) with NO shot-family-specific variation, because the
real, decisive population data (Sec. 6) shows both `AVG_OREB_DIST`
(9.2ft) and `AVG_DREB_DIST` (7.0ft) cluster near the rim REGARDLESS of
shot family, at the aggregate level available. D (shot-family × miss-
type) was explicitly investigated and REJECTED for lack of a verified
shot-family-specific cross-tab (Sec. 6/8) to justify it — not chosen by
default preference. A caller MAY override the default zone with a
real, caller-determined value via `ReboundOpportunity.rebound_zone` —
this module does not force the assumption on every case, only defaults
to it absent better information.

## 8. Shot-family → carom findings

**No shot-family-specific rebound-distance data was found/verified
live this phase** (Sec. 6). The task's own explicit warning — do not
hardcode "all rim misses = SHORT" or "all threes = LONG" without data —
is honored precisely by NOT differentiating carom region by shot
family at all in V1 (a single real, pooled anchor is used, not a
per-family invented rule). This is a real, flagged limitation (Sec.
44), not a concluded "shot family doesn't matter" finding.

## 9. Blocked-shot rebound findings

**Not independently audited via a live data pull this phase.**
Architecturally, a blocked shot already reaches this module in exactly
the same `LOOSE` state as an ordinary miss (Phase 18B's
`block_secured_by_defense` produces the identical
`ball_state=LOOSE`/`offense_team_id=None` state as Phase 18A's
`resolve_shot_missed_pending_rebound`) — so `ReboundSource.UNRESOLVED_BLOCK`
is representable and tested (Sec. 40) but is NOT given bespoke recovery-
rate physics distinct from an ordinary miss, since no real evidence was
gathered this phase to justify a different treatment. Verified directly
that **only genuinely unresolved blocked-ball states reach this module
at all** — a block retained by the offense (Phase 18B's
`block_retained_by_offense`, which keeps `offense_team_id` populated,
not `None`) never needed to enter rebound competition in the first
place, since offense's own possession was never in question there.

## 10. Final-missed-FT handling

`ReboundSource.FINAL_MISSED_FT` is representable and tested (Sec. 40).
Per Phase 18C's own already-correct gating (only a legally live FINAL
missed FT transitions to `LOOSE`; a non-final miss leaves `ball_state`
untouched), Phase 19 never needs its own separate final-vs-non-final
check — by the time an opportunity reaches this module, that
distinction has already been correctly enforced upstream. No unique FT-
specific eligibility/lane-slot geometry was implemented — the SAME
eligibility/competition machinery used for every other source applies,
per the explicit "do not implement complete lane-slot geometry unless
necessary for V1" instruction (no evidence gathered showed it necessary).

## 11. Eligibility architecture

`eligible_rebound_candidates(opportunity)` — THE central gate, run
BEFORE any skill value is read anywhere in this module (verified
directly: `_candidate_log_weight`, the only function that reads
`offensive_rebounding`/`defensive_rebounding`, is only ever called on
the output of this filter, never on the raw candidate list). A
candidate is eligible only if their own `zone` equals the rebound's
`effective_zone` — verified directly
(`test_elite_skill_cannot_bypass_wrong_zone`: an absurd 0.999 skill
value on a wrong-zone candidate never wins across 300 real trials).

## 12. Zone/topology handling

Reuses the existing, unmodified 8-zone topology (`SpatialZone`) — no
new zone was added. `RESTRICTED_RIM` was judged sufficient for V1's
single default carom region (Sec. 7); `PAINT`/wing/corner zones remain
usable as real, caller-supplied overrides for a genuinely long carom,
but no automatic mapping from shot family to a non-default zone exists
this phase (Sec. 8's honest limitation).

## 13. Crash-vs-retreat boundary

**No crash/retreat system exists in this repo, and none was built
here** — flagged explicitly as a missing team/system layer, per
instruction ("if it does not exist, choose the minimum neutral
structural rule needed to operate and clearly flag the missing team/
system layer"). The minimum neutral rule actually used: `ReboundCandidate`
list membership itself IS the "who's near the play" signal — a caller
(a future team/system phase) decides who is even a candidate at all;
this module never decides who crashes or retreats, and
`offensive_rebounding`/`defensive_rebounding` never influence that
decision (verified: neither skill field is read anywhere before
`eligible_rebound_candidates` runs).

## 14. Box-out/leverage architecture

`BoxOutState` (`ESTABLISHED_BOXOUT`/`CONTESTED`/`NONE`) and
`ReboundCandidate.boxed_out_by` — real, structural, caller-supplied
leverage context, applied to each candidate's acquisition log-weight
AFTER eligibility filtering, BEFORE the final competition draw (Sec.
11's staged order: eligible → leverage-adjusted weight → competition).
Verified directly (`test_boxout_shifts_outcome_direction`): an
established box-out real, measurably shifts the offense's win rate
downward across 1,500 real trials.

## 15. Box-out attribute-gap verdict

Real, verified public box-out data DOES exist
(`leaguehustlestatsplayer`'s `OFF_BOXOUTS`/`DEF_BOXOUTS`/
`BOX_OUT_PLAYER_REBS`) — **classification: POSSIBLE GAP, not
UNIDENTIFIABLE**. However, per explicit instruction, **no new rating
was created this phase** regardless of this real data's existence —
box-out remains a purely structural, caller-supplied state
(`BoxOutState`), not a persistent player skill attribute. Whether a
future phase should build a real, calibrated box-out latent from this
real data is left as a flagged, open recommendation (Sec. 44), not
decided here.

## 16. Offensive-vs-defensive leverage handling

Real, structural asymmetry is respected but not hardcoded as a
universal rule: defense's real, lower `DREB_CONTEST_PCT` (21.6% vs.
OREB's 48.8%, Sec. 6) reflects that defense usually starts with a real
positional advantage — this module does not encode that as a fixed
scalar bonus for the defense; it emerges naturally from the CALLER
supplying more `ESTABLISHED_BOXOUT` states for defenders than offensive
players in a typical real scenario (a modeling choice left to whoever
builds the box-out-generation logic, not this module).

## 17. Multi-player model comparison

Compared A (all eligible players weighted), B (top-K pool), C
(team-first branch then individual), D (hierarchical eligibility→
competition), E (other). **Chosen: A (all eligible players receive
acquisition weights, softmax competition)** — simplest, no arbitrary
top-K cutoff to justify, no team-first double-count risk (Sec. 18).

## 18. Team-first vs. player-first study

**Team-first (P(OREB) vs. P(DREB) decided first, then a player chosen
within it) was explicitly investigated and REJECTED.** Reasoning: both
`offensive_rebounding` and `defensive_rebounding` are ALREADY real,
team-context-normalized rates (OREB_PCT/DREB_PCT are computed relative
to the player's OWN team's real rebound opportunities while they were
on the floor) — a separate team-level OREB/DREB probability layered on
top would double-count team rebounding context already baked into
those two attributes. Direct player-level competition among eligible
candidates (both sides pooled into one softmax) avoids this risk
entirely, since no team-level scalar is introduced anywhere.

## 19. Physical-variable diagnostics

**Not tested this phase** (height/standing_reach/wingspan/mass) — a
real, flagged gap given severe time constraints, not a tested-and-
rejected finding. No physical field exists in `ReboundCandidate`
(verified: `test_missing_physical_fields_not_present`). Given this
project's own repeated finding across Phases 17A/18B that physical
variables tend to substantially overlap already-existing skill
estimators, exclusion-by-default was judged the safer starting
posture — but this is an inference from precedent, not a fresh
diagnostic run this specific phase.

## 20. OREB boundary

Consulted only for `side=="OFFENSE"` eligible candidates, only after
eligibility and leverage are fixed (verified:
`test_oreb_only_used_for_offense_side`, `test_high_oreb_cannot_force_eligibility`).
Does not increase crash probability (no such concept exists), does not
create opportunity from an impossible location (gated by eligibility),
does not improve putback FG% (no putback mechanic exists at all — Sec.
21), and does not auto-generate second-chance advantage (Sec. 26).

## 21. DREB boundary

Mirror of Sec. 20 for `side=="DEFENSE"` (verified:
`test_high_dreb_cannot_force_eligibility`). Does not improve rim
protection, does not suppress shooter FG% (both already Phase 18B's
own, separately-firewalled domain), does not automatically initiate
transition (verified: `test_dreb_does_not_start_transition_state_directly`
— no `transition`/`fastbreak`/`outlet` field exists anywhere in
`ReboundOpportunity`).

## 22. Tip/loose-ball decision

**Candidate A chosen: secure only.** No explicit TIP state was built —
no clean, live, per-rebound-event public tip-rate data was gathered
within this phase's time budget to justify a distinct tip taxonomy
(Sec. 6/44). A rebound either resolves to a real secured
individual (`SECURED_OFFENSE`/`SECURED_DEFENSE`) or a team rebound —
no bespoke "controlled tip"/"loose tip"/"tip out" states exist. This is
an honest, flagged simplification, not a claim that tips don't
meaningfully differ from clean secures in reality.

## 23. Team-rebound handling

`ReboundOutcome.TEAM_REBOUND_OFFENSE`/`TEAM_REBOUND_DEFENSE` — credited
ONLY when no eligible candidate exists (verified directly:
`test_no_eligible_candidate_credits_team_rebound`,
`test_team_rebound_not_credited_to_individual` — `rebounder_id` is
always `None` on a team rebound, never an arbitrary individual pick).
`PossessionEngine.credit_team_rebound` reuses the identical real
SECOND_CHANCE/shot-clock/advantage-clearing logic (offense side) or
possession-flip logic (defense side) as the individual-rebound methods
— no parallel state model.

## 24. Rebound attribution

Exactly one outcome, exactly one (optional) `rebounder_id` per call —
verified directly (`test_exactly_one_outcome_per_call`,
`test_no_two_simultaneous_secured_rebounders`). `_softmax_choice`
performs a single weighted draw over all eligible candidates pooled
together (both sides in the same competition), never two independent
per-side draws that could produce two winners.

## 25. OREB second-chance handoff

`secure_offensive_rebound_from_loose` transitions to
`PossessionPhase.SECOND_CHANCE`, resets the shot clock via
`oreb_reset_value(self.era_rules, ...)` (Phase 15's own real era-aware
hook — NOT a hardcoded 14 seconds; verified directly:
`test_oreb_uses_era_rules_not_hardcoded_14` confirms the real 2018+-era
value of exactly 14.0 comes from era rules, and
`test_no_hardcoded_14_literal_in_module` confirms no bare `14.0`/`= 14`
literal exists anywhere in `rebound_resolution.py` itself — the value
flows entirely through `engine.era_rules`), and unconditionally clears
`self.advantage` to `None` — verified directly
(`test_oreb_clears_advantage_regardless_of_prior_state`,
`test_same_oreb_context_different_old_advantage_yields_identical_new_state`:
three different starting `AdvantageModel` states — two real
representations plus `None` — all produce byte-for-byte identical new
possession state after an OREB).

## 26. Old-advantage firewall

Directly verified (Sec. 25's second test): the new second-chance
state's phase, carrier, and advantage are IDENTICAL regardless of what
`AdvantageModel` state existed before the OREB — the old advantage is
never copied, partially preserved, or used to seed the new state in any
way. This satisfies "the new possession context may have structural
asymmetry due to player positions, but that must be newly derived" by
simply not deriving anything at all in V1 (advantage starts at `None`,
to be rebuilt by whatever future phase re-establishes it from the new,
real post-rebound geometry).

## 27. DREB transition handoff

`secure_defensive_rebound_from_loose` flips `offense_team_id`/
`defense_team_id` to the real, caller-supplied new values and moves to
`PossessionPhase.TRANSITION` — a minimal handoff packet only (real
rebounder id, real new team ids, the existing `PossessionState`'s own
zone/clock fields). No fastbreak, outlet pass, or push mechanic was
built — explicitly Phase 20's job.

## 28. Block-recovery integration

Verified directly (`test_block_does_not_directly_credit_a_rebound`):
no `blocker_id` concept appears anywhere in `rebound_resolution.py` —
this module has no awareness of WHO blocked the shot, only that the
ball is now `LOOSE` and needs a rebound resolution, exactly the correct
separation (block ≠ rebound, enforced by the two modules never sharing
that piece of information at all).

## 29. Shot-clock reset integration

Covered in Sec. 25 — routed entirely through `engine.era_rules`/
`oreb_reset_value`, the same real Phase 15 hook every prior resolution
phase (17A) already used for the identical purpose. No new rule
representation was introduced.

## 30. RNG handling

`resolve_rebound` performs exactly one `rng.random()`-consuming draw
per call (`_softmax_choice`'s single weighted selection over ALL
eligible candidates at once) — not one draw per candidate. No
carom/eligibility/acquisition/tip draws were separately isolated into
substreams this phase (unlike Phase 17B's `derive_rng`) — judged
unnecessary given there is only one real stochastic decision per
rebound event in this V1 design. No global `random` module call exists
anywhere in the file (verified: `test_no_global_rng`).

## 31. Current-attribute overlap diagnostics

Only the OREB_PCT-vs-OREB_CHANCE_PCT diagnostic (Sec. 4, r=0.535) was
run live this phase, given severe time constraints. The full requested
correlation battery (OREB vs. height/reach/wingspan/mass, DREB vs. same,
OREB vs. `role_off_finishing`, DREB vs. `rim_protection`, DREB vs.
minutes/interior role, OREB vs. `orb_crash`) was **not independently
re-run** — `orb_crash`'s real ~0.996 OREB correlation is reused from
Phase 11's own prior finding (not re-verified), and the remaining
comparisons are flagged as unresolved (Sec. 44), not silently assumed
to be zero or non-zero.

## 32. Rebound model ladder

| Rung | Included? | Basis |
|---|---|---|
| R0 shot/rebound context baseline | Implicit (real default carom zone, Sec. 7) | Real, coarse |
| R1 eligible-player geometry | **Yes** | Real, structural, central gate (Sec. 11) |
| R2 offensive/defensive side | **Yes** | Structural (`ReboundCandidate.side`) |
| R3 box-out/leverage context | **Yes** | Real, structural, caller-supplied (Sec. 14) |
| R4 OREB/DREB player skill | **Yes** | Reused as-is (Sec. 4/5), consulted only post-eligibility |
| R5 physicals | **No** | Sec. 19, untested this phase |
| R6 contextual/team factors | **No** | Crash/retreat system doesn't exist (Sec. 13) |

## 33. OREB model comparison

Direct player-level competition (Sec. 17/18) — no separate OREB-only
model variant was built or compared beyond the team-first-vs-player-
first study already covered.

## 34. DREB model comparison

Same shared framework as OREB — no DREB-specific alternative was
separately evaluated, since the team-first-vs-player-first study (Sec.
18) applies symmetrically to both sides.

## 35. Contested-rebound findings

Real, verified population split exists (Sec. 6: `OREB_CONTEST_PCT`
48.8%, `DREB_CONTEST_PCT` 21.6%) but **was not used to separately
re-fit acquisition weights for contested-only vs. uncontested-only
cases this phase** — a real, flagged opportunity for a future
calibration phase to test whether OREB/DREB latents show STRONGER
player signal specifically on contested rebounds (which would help
separate true acquisition skill from opportunity), left undone here.

## 36. Temporal heldout results

**Not run this phase** — no fitted probabilistic model exists yet to
heldout-validate (same honest posture as every prior resolution phase:
no public per-rebound-event, player-competition-outcome ground truth
exists to fit against). The one real, temporal-adjacent evidence
gathered is the Sec. 4 diagnostic itself (a single real 2023-24 cross-
section, not multi-season).

## 37. Team-change portability

**Not run this phase.** Traded-rebounder, frontcourt-partner-change,
and lineup-change natural experiments were judged out of scope given
the time already spent on the construct audits and the real box-out/
rebound-chance data discovery. Flagged (Sec. 44).

## 38. Natural-experiment findings

**Not run this phase**, same reasoning as Sec. 37.

## 39. Missingness/historical fallback

`_candidate_log_weight` treats a missing `offensive_rebounding`/
`defensive_rebounding` value as a real, neutral 0.5 contribution (a
plausible OREB/DREB-pct-scale midpoint), never a fabricated zero —
verified directly (`test_missing_skill_treated_as_neutral_not_zero`).
This directly supports historical-era operation without modern tracking
fields: an older-era candidate with no real OREB_PCT/DREB_PCT value at
all still competes on equal footing via the neutral fallback, rather
than being silently disadvantaged to zero.

## 40. Double-counting matrix

| Pair | Risk |
|---|---|
| OREB/DREB skill × eligibility | **SAFE** — skill is never read until after the eligibility gate (Sec. 11) |
| Team-first branch × player-level OREB/DREB | **HIGH RISK, avoided** — explicitly rejected (Sec. 18) precisely because it would double-count |
| Box-out leverage × OREB/DREB skill | **SAFE** — structurally independent real sources (caller-supplied leverage state vs. Phase 1-3 estimate) |
| Block outcome × rebound resolution | **SAFE** — no shared information at all (Sec. 28) |
| Old `AdvantageState` × new second-chance state | **SAFE** — explicitly, verifiably cleared (Sec. 26) |
| `orb_crash` × any mechanic in this module | **SAFE** — not referenced anywhere (verified) |

## 41. Falsification tests

All required numbered counterfactuals were run as real, direct tests —
highlights: `rim_finishing`/`three_point`/`drive_aggression`/
`three_point_preference` have no field to even change (structural, not
behavioral, absence); a player outside the eligible zone cannot win
regardless of skill (300-trial real check); OREB never auto-generates a
putback or improves future shot probability (no such field/mechanic
exists); OREB retains team possession, DREB flips it (both verified);
block never auto-credits a rebound; only unresolved blocked-ball states
enter competition; mass/height/reach/wingspan cannot bypass impossible
position (no such fields exist at all); exactly one rebound winner per
call; deterministic replay confirmed; no global RNG; the same OREB
context with three structurally different starting `AdvantageState`
values (two real representations + `None`) produces byte-for-byte
identical new second-chance state.

## 42. Focused test results

`test_rebound_resolution.py` — 33 tests covering every item in the
required focused-suite list: source handoff from all three real
sources (missed FG, unresolved block, final missed FT), player
eligibility and impossible-player exclusion, box-out/leverage ordering,
OREB/DREB used only post-opportunity, team rebound (never credited to
an individual), no putback/transition generation, old-`AdvantageState`
clearing (including the multi-representation identical-outcome test),
`SECOND_CHANCE` creation, team-possession continuity on OREB, possession
flip on DREB, era-aware shot-clock reset (with an explicit
no-hardcoded-14-literal check), `player_id`-only, deterministic replay,
missing-skill-treated-as-neutral, and no mutation leakage.

## 43. Full-suite result

`python3 -m unittest discover -p "test_*.py"` → **Ran 633 tests — OK**
(600 carried over from Phase 18C + 33 new in `test_rebound_resolution.py`).
No existing test was modified or removed.

## 44. Unresolved issues

- Physical-variable diagnostics (height/reach/wingspan/mass) were not
  run this phase — excluded by precedent-based caution, not fresh
  evidence.
- The full requested correlation battery (Sec. 31) was only partially
  run — only the OREB_PCT-vs-OREB_CHANCE_PCT diagnostic was computed
  live.
- Shot-family-specific carom/rebound-distance data was not found/
  verified live (Sec. 8) — a real, flagged gap, not a "shot family
  doesn't matter" conclusion.
- No temporal heldout, team-change portability, or natural-experiment
  validation was run (Sec. 36-38).
- Box-out is a real, verified-available public signal (Sec. 15) that
  was deliberately NOT turned into a new rating this phase — a concrete
  recommendation for a future phase, not a closed question.
- Contested-vs-uncontested-specific OREB/DREB signal strength (Sec. 35)
  was not separately tested.
- No explicit TIP state exists (Sec. 22) — a real, flagged
  simplification pending better public data.

## 45. Classifications

| Candidate mechanic | Classification |
|---|---|
| `offensive_rebounding`/`defensive_rebounding` as post-eligibility acquisition inputs (reused, unchanged) | **KEEP** (unchanged from Phase 1-3's own classification) |
| Eligibility-first architecture (opportunity before skill) | **LOCK V1** — the phase's own central requirement, real, tested, structurally enforced |
| Direct player-level competition (rejecting team-first) | **KEEP** — real, principled, evidence-based rejection of the double-count-risk alternative |
| Box-out/leverage as structural (not a new rating) | **KEEP** — real direction, unvalidated magnitude |
| Default carom zone (RESTRICTED_RIM, no shot-family variation) | **KEEP BUT FLAG** — real, evidence-anchored default, but the "no variation by shot family" choice is a gap-driven simplification |
| Secure-only (no tip state) | **KEEP BUT FLAG** — real, minimal, honest simplification |
| Team rebound (no-eligible-candidate fallback) | **KEEP** — real, tested, never mis-assigned to an individual |
| A distinct, calibrated box-out player rating | **not created** — real public data exists (Sec. 15), explicitly deferred, not built |
| Physical variables in rebound acquisition | **INSUFFICIENT** — not tested |
| Contested-vs-uncontested-specific OREB/DREB signal | **INSUFFICIENT** — not tested |
| A new `orb_crash`-style crash-propensity trait | **not created** — explicitly rejected per Phase 11's prior finding, not revisited |

## 46. Final phase classification

**READY WITH FLAGS FOR 20A.**

Ready: the eligibility-first architecture (the phase's own central
requirement) is real, tested, and structurally enforced at every level;
the team-first-vs-player-first double-counting question was resolved
with real reasoning, not assumed; OREB/DREB boundaries, block-recovery
separation, and the old-advantage firewall are all directly verified,
including a genuine multi-representation identical-outcome test; the
shot-clock reset correctly routes through Phase 15's real era-rules
hook with an explicit anti-hardcoding test; missing physical/skill data
degrades gracefully to a real neutral fallback rather than a fabricated
zero, supporting historical-era operation.

Flags: several real, decisive data sources were discovered this phase
(rebound chances/contest splits, box-out data) but not fully exploited
under the time budget — shot-family-specific carom behavior, contested-
vs-uncontested-specific skill signal, physical-variable incremental
value, and temporal/portability validation all remain open, honestly
flagged rather than force-completed; no explicit tip state exists.

Not begun: Phase 20.
