# Phase 23A — Autonomous Single-Possession Kernel (Detailed Possession-Engine Track)

One callable, `simulate_possession(...)`, that runs a complete
possession autonomously from a 5v5 starting configuration to a typed
terminal result, owning the full INITIALIZE → BUILD WORLD → BUILD
MATCHUPS → INBOUND → [DERIVE STRUCTURAL CONTEXT → GENERATE OPPORTUNITIES
→ PERCEIVE → SELECT → DISPATCH → RESOLVE → UPDATE WORLD/ENGINE → CONSUME
CLOCK → CONTINUE OR TERMINATE] loop. No test caller hand-chains
subsystems.

**Doctrine (post-reconciliation, see Sec. W below):** this project runs
an intentional ASYMMETRIC DUAL-ENGINE architecture — the DETAILED
POSSESSION ENGINE (Phases 15–23A, event-driven, the future source for
watched games/tactical simulation/play-by-play/generative-world
basketball) and the FAST AGGREGATE ENGINE (`game_engine.py`, the
existing, supported season/multi-season approximation, legacy-
compatible, a benchmark/reference engine, NOT event-emergent). Both are
intentional and currently COEXIST, sharing PLAYER TRUTH and expected to
eventually converge on a compatible final box-score output boundary.
`simulate_possession(...)` is Phase 23A's one-possession integration
infrastructure for the DETAILED engine ONLY — it is **not** described
here as the repository's canonical public game API, is **not** routed
into `main.py`/`season.py`/`playoffs.py`/`db.py`, and does **not**
modify or replace `game_engine.py` (confirmed: no reference to
`possession_orchestrator`/`simulate_possession` exists in any of those
five files, and this module never imports `game_engine.py` — verified
by `test_no_product_or_legacy_file_references_the_orchestrator`/
`test_orchestrator_never_imports_game_engine`). A future, deliberate,
separate game-level integration/migration checkpoint is required before
any product code routes through it.

## A. Phase 22A checkpoint

- Commit: `1659870` — "Add Phase 22A off-ball screen interaction" (exactly the three files: `off_ball_screen_resolution.py`, `test_off_ball_screen_resolution.py`, `docs/PHASE22A_OFF_BALL_SCREEN_INTERACTION_REPORT.md`).
- Push: `git push origin codex/empirical-player-modeling` → `d3cce76..1659870 codex/empirical-player-modeling -> codex/empirical-player-modeling` (succeeded).
- Working tree confirmed clean immediately before and after: `git status --short` empty, `git status` reported "nothing to commit, working tree clean" (verified via short-status emptiness) before Phase 23A work began.
- Re-ran before checkpointing: `test_off_ball_screen_resolution.py` → 33/33 OK; full suite → **780/780 OK** — identical to the previously reported Phase 22A numbers, confirmed as current truth before committing.

## B. Pre-Phase-23A baseline

**780 passing** (confirmed live, matching the task's stated expectation), zero regressions, before any Phase 23A file was added.

## C. Phase 23A architecture

One new module, `possession_orchestrator.py`, structured as:

1. `PlayerSimulationProfile` — the adapter (Sec. E).
2. Lineup/matchup: `validate_lineups`, `build_matchup_assignments`, `apply_matchup_assignments`.
3. `PossessionWorld` — structural context around the engine (Sec. F).
4. Deterministic V0 zone placement (`default_v0_zone_placement`, `_mirror_defender_zones`).
5. `StructuralContext` derivation (`build_structural_context`).
6. Clock ownership (`_charge_time`, `PossessionConfig`).
7. `PossessionTerminalReason`/`PossessionTerminalResult`.
8. Generic loose-ball recovery (`resolve_generic_loose_ball`).
9. Rebound handoff (`_dispatch_rebound`, reuses Phase 19 as-is).
10. Floor-foul handoff (`_dispatch_floor_foul`, reuses Phase 21A→21B as-is).
11–13. Drive/shot/pass dispatch (`_dispatch_drive`, `_dispatch_shot`, `_dispatch_shooting_foul`, `_dispatch_pass`).
14. `dispatch_action` — the one authoritative seam.
15. `simulate_possession` — the top-level loop.

Every actual basketball outcome is resolved by the EXISTING resolver each prior phase built; this module invents no new mechanic — it is glue, matching the task's own framing.

## D. Files changed/added

| File | Change |
|---|---|
| `possession_orchestrator.py` | **New.** Everything in Sec. C. |
| `test_possession_orchestrator.py` | **New.** 35 tests. |

**Zero changes to any existing file.** Both are currently **uncommitted**, per explicit instruction (Phase 23A is left for HQ review, not committed/pushed).

## E. `PlayerSimulationProfile` design

A frozen dataclass, `player_id`/`team_id` plus ~20 Optional numeric fields, one per attribute a SUPPORTED resolver actually reads. Not a new empirical model, not OVR, not a combination into one score. A mid-turn addendum audited the adapter against the real estimator/resolver source and required concrete corrections, all applied:

1. **Native shrunk values only, never 0–99.** Fields are named to make this explicit and unambiguous: `three_point_shrunk_rate`, `rim_finishing_shrunk_rate`, `floater_short_mid_shrunk_rate`, `free_throw_shrunk_rate`, `rim_access_creation_shrunk_rate`, `poa_containment_shrunk_rate`, `rim_protection_suppression_rate`, `offensive_rebounding_shrunk_rate`, `defensive_rebounding_shrunk_rate`. Semantic, unit-bearing names replace the earlier ambiguous ones (`passing_accuracy_ast_pct`, `ball_security_error_rate`, `defensive_playmaking_per36`) per the addendum's own naming guidance.
2. **Vision modifier disabled.** `playmaking_vision_shrunk_rate` exists as evidence only; `simulate_possession` calls `perceive(opportunities, None, rng)` UNCONDITIONALLY (verified: `test_vision_modifier_is_none`) — `estimate_playmaking_vision()`'s native scale has no validated conversion to `perceive()`'s expected `vision_latent_propensity`, so `None` (that function's own documented neutral, league-average behavior) is used instead of an invented conversion.
3. **Foul-drawing/foul-discipline modifiers disabled.** `foul_drawing_shrunk_rate`/`foul_discipline_shrunk_rate` exist as evidence only; every call site (`OnBallPressureContext.foul_drawing`/`foul_discipline`, `ContactContext.shooter_foul_drawing`, `FoulEligibleDefender.foul_discipline`) hardcodes `None` (verified: `test_foul_fields_never_reach_a_resolver`) — the estimators produce native rates, not the centered modifier value these resolvers expect, and `foul_discipline`'s own native direction (higher = worse) is opposite the sign some resolver naming implies; no conversion is invented.
4. **Ball security used in its native, un-inverted direction.** `ball_security_error_rate` (higher = worse, the specialized handling-error-per-touch estimator, excluding bad-pass/offensive-foul categories) is passed straight into `OnBallPressureContext.ball_security` — `on_ball_pressure_resolution.py` already interprets this direction correctly; the profile adapter does NOT invert it (a stray "lower = worse" comment inside that module is not followed as semantic truth — estimator + resolver behavior is).
5. **Passing stays the AST_PCT proxy**, fed only into `PassResolutionContext.passing_accuracy` (a small, already-tested delivery modifier) — never treated as an overall completion probability.
6. **Drive-adjacent fields used natively**, no adapter-side z-score — `drive_resolution.py` performs its own standardization against its own embedded population constants; z-scoring here too would double-standardize.
7. **Rebound missing-estimate gating.** A candidate missing their OWN side's estimate is EXCLUDED from `_dispatch_rebound`'s candidate list entirely, rather than trusting `rebound_resolution.py`'s own internal `None -> 0.5` fallback (verified: `test_player_missing_side_specific_estimate_excluded_from_competition` — stripping every offensive `offensive_rebounding_shrunk_rate` on a missed shot correctly yields `DEFENSIVE_REBOUND` via `resolve_rebound`'s own real "no eligible candidate" behavior, never a fabricated individual rebounder).
8. **Tendencies use `latent_propensity` semantics** (`drive_aggression`, `pass_vs_shoot`, `three_point_preference`, `midrange_preference`, `pullup_vs_catch`) — passed straight through to `TendencyContext`, selection-layer only.
9. **Role stays deployment context** (`role_off_initiation`/`role_off_finishing`/`role_off_spacing`) — passed straight through to `RoleContext`, never converted into skill.
10. **No physical field exists anywhere** — the supported action set needs none (`DriveResolutionContext.enable_physical_adjustment` stays at its default `False`; `InteriorDefenderContext` has no physical field). `mass != strength` is never touched.
11. **`orb_crash` is absent** — never added.
12. **`perimeter_space_creation` is absent** — never added; it has no resolver authority in this project.
13. A `PlayerSimulationProfile.synthetic(...)` static constructor supplies clearly-labeled, deterministic V0 test values — real ingestion is explicitly NOT built this phase (see the module's own "REAL-ADAPTER CONSTRUCTION CONTRACT" docstring, which binds a future real constructor to: one shared `as_of_season` per profile, cutoff-filtering (`season <= as_of_season`) any historical ball-security proxy path before use or disabling it, and retaining canonical estimate/provenance objects alongside — none of this applies yet since no real-ingestion path exists to violate it, but the contract is written down for whoever builds it next).

## F. `PossessionWorld` / state ownership

Explicit, documented split (module docstring, reproduced here):

- **`engine.state`** (Phase 15, untouched) owns: `ball_carrier`/`ball_state`/`ball_control`/`ball_zone`, `offense_team_id`/`defense_team_id` (which FLIPS during the possession), `assignments` (defender→offender pointer + posture), `shot_clock_remaining`/`game_clock_remaining`.
- **`PossessionWorld`** owns: the STATIC lineup↔team_id binding (`team_a_id`/`team_a_five`, `team_b_id`/`team_b_five` — these never flip; "current offense five" is DERIVED by comparing `engine.state.offense_team_id` against them, never stored as its own mutable field), the per-player coarse `SpatialZone` for all ten players (genuinely new — `PossessionState` has no non-ball positional concept at all), the one-shot `just_caught_pass_player_id` transient (Phase 16 established this as purely caller-supplied; consumed exactly once per possession-loop iteration, verified structurally by inspection and behaviorally via the CLOSEOUT-style re-entry test), the running `FoulAdministrationState` (shared across floor-AND-shooting fouls — a personal/team foul is one real running count regardless of type), `StatDeltas`, and a diagnostic `trace` list.

No field on `PossessionWorld` duplicates authority `PossessionState` already holds.

## G. Matchup initialization / rebuild

`build_matchup_assignments(offensive_five, defensive_five, matchup_pairs=None)` — deterministic lineup-order pairing by default, or a caller-supplied explicit `[(defender_id, offensive_target_id), ...]` list, validated for a REAL bijection (5 unique defenders, 5 unique offensive targets, exact coverage of both fives) before ANY assignment is built. `apply_matchup_assignments` applies the whole computed dict via ONE `replace()` on `engine.state` — no intermediate, partially-covered state is ever observable (same atomic posture as Phase 22A's own switch primitive). The SAME function is reusable to rebuild assignments after a possession change (verified: `test_rebuild_after_possession_change_still_a_bijection`, swapping which five is offense/defense and confirming the bijection still holds). No optimizer, no matchup AI.

## H. `StructuralContext` derivation

`build_structural_context(engine, world)` derives every field automatically, per the required policy table:

| Field | Source |
|---|---|
| `teammate_ids` | `world.offense_five(engine)` minus the carrier |
| `perimeter_receiver_ids` | teammates whose `world.player_zones` entry is in `PERIMETER_ZONES` |
| `ball_handler_defender_id` | `engine.state.assignments` (whoever guards the carrier) |
| `just_caught_pass` | `world.just_caught_pass_player_id == carrier`, consumed exactly once per loop iteration |
| `roller_id` / `screen_active` | always `None` / `False` — never fabricated (Sec. I) |
| `nearest_teammate_id` | deterministic coarse `ball_side()` topology match, list-order tie-break — NOT Euclidean (no continuous coordinates exist) |

Verified: `test_no_manual_structural_context_construction_required`, `test_perimeter_receiver_ids_derived_from_zones`, and `test_N_no_manual_structural_context_inside_simulate_possession` (a source scan confirming `simulate_possession` never constructs a `StructuralContext(...)` inline — only via `build_structural_context`).

## I. Supported / capability-gated actions

**Supported** (`SUPPORTED_ACTION_TYPES`): `DRIVE`, `PULL_UP`, `CATCH_AND_SHOOT`, `SWING_PASS`, `KICKOUT`, `RESET_PASS`, `POCKET_PASS` — every family with a coherent existing resolver. `POCKET_PASS` is mechanically wired (pass dispatch is generic across `PASS_ACTIONS`) but will never actually be OFFERED in V0 play since `roller_id`/`screen_active` are always `None`/`False` (Sec. above) — documented, not a bug.

**Capability-gated** (`CAPABILITY_GATED_ACTION_TYPES`): `ISOLATION_ATTACK`, `CLOSEOUT_ATTACK`, `TRANSITION_PUSH`, `OUTLET_PASS`, `RECOVER_LOOSE_BALL`. None has a resolver this module can call without inventing one (no isolation-specific resolver distinct from `DRIVE`'s own leverage model; no closeout resolver; no transition-push resolver wired; `OUTLET_PASS` needs `transition_offense.py`'s own transition-specific geometry, not built here). `RECOVER_LOOSE_BALL` is gated differently: a `BallState.LOOSE` possession is intercepted at the TOP of the loop, before `generate_opportunities` runs at all, and routed straight to `resolve_generic_loose_ball` — it never reaches ordinary Phase 16 selection (scoring "who recovers a loose ball" via role/tendency weights has no real basis).

Gating happens twice: perceived opportunities are filtered to `SUPPORTED_ACTION_TYPES` BEFORE `SelectionPolicy.select()` ever sees them, and `dispatch_action` independently raises `UnsupportedActionError` as a defense-in-depth structural guard. Off-ball screens (Phase 22A) remain fully caller-triggered and are not invoked anywhere in this loop — no participant-selection AI, no fabricated screen frequency, per explicit instruction; the resolver stays available for a future orchestration layer.

Explicit V0 simplification: perimeter-zone shots are ALWAYS dispatched as `THREE_POINT` (this project's 8-zone topology doesn't distinguish a mid-range release point from a beyond-the-arc one within the same coarse `PERIMETER_ZONES` set) — `midrange`/`midrange_preference` remain real, unused-by-V0-dispatch fields for a future finer-grained phase.

## J. Dispatcher map

`dispatch_action` routes: `DRIVE` → `_dispatch_drive`; `PULL_UP`/`CATCH_AND_SHOOT` → `_dispatch_shot` (→ `_dispatch_shooting_foul` when Phase 18C whistles); any `PASS_ACTIONS` member → `_dispatch_pass`. Anything else raises `UnsupportedActionError`.

**Drive**: Phase 21A's on-ball pressure/collision check runs FIRST (exactly once per dispatch, since a drive is exactly Phase 21A's own owned `LIVE_DRIBBLE` precondition) — `contact_established` defaults to `False` (`PossessionConfig.force_on_ball_contact_established`, a real, documented V0 ordering decision: no per-drive contact-occurrence rate has ever been derived in this project, so none is fabricated; the flag exists purely so a test can exercise the real, otherwise-dormant 21A→21B pipeline end-to-end). A collision outcome (`OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL`) routes to `_dispatch_floor_foul` and NEVER falls through into `resolve_drive` — the same contact is never seen by both (no double-resolution). `FORCED_PICKUP`/`CLEAN_STRIP_LOOSE` end the action without reaching `resolve_drive` at all (a dead/loose dribble cannot drive). Otherwise, `resolve_drive` (Phase 17A) runs unmodified.

**Shot**: Phase 18C's `resolve_contact_and_whistle` runs BEFORE the ordinary shot resolver, on the SAME `shot_family`/defender geometry used for the ordinary make-probability computation (reused, not re-derived, for the and-one case per 18C's own instruction). If whistled, `_dispatch_shooting_foul` owns the entire and-one/FT/rebound handoff using Phase 18C's OWN machinery (`resolve_shooting_foul_shot`, `FreeThrowSequence`, `apply_free_throw_attempt_to_engine`) — Phase 21A never administers a shooting foul, and Phase 21B never detects one (confirmed: `floor_foul_administration.py`'s own imports contain no shot-family/contact/whistle concept at all). If not whistled, `apply_interior_shot_to_engine`/`apply_shot_resolution_to_engine` run exactly as Phase 18A/18B built them.

**Pass**: `resolve_pass` (Phase 17B) runs unmodified; outcomes map to continuation (`COMPLETED_CLEAN`/`COMPLETED_ADJUSTED`), the generic loose-ball resolver (`DEFLECTED_LOOSE_BALL`/`DEFLECTED_RETAINED_OFFENSE`/a defender-less `BAD_PASS_TO_DEFENDER`), or a terminal `TURNOVER`/`SHOT_CLOCK_VIOLATION`.

## K. Clock ownership and durations

`pass_resolution.resolve_pass` ALREADY decrements both clocks internally by its own real `FLIGHT_DURATION_SECONDS[family]` (confirmed by direct source read) — `_dispatch_pass` NEVER calls `_charge_time` (verified: `test_pass_dispatch_never_calls_charge_time`), avoiding the exact double-charge risk the task warned about. Every other resolver used here (`drive_resolution`, `shot_resolution`, `interior_shot_resolution`, `rebound_resolution`, `foul_resolution`, `on_ball_pressure_resolution`) passes `dt=0.0` everywhere (confirmed by direct source read of each) — for those, `possession_orchestrator.py` is the SOLE clock owner via `_charge_time`, using real, EXPLICITLY UNCALIBRATED, hand-set positive durations (`PossessionConfig.drive_action_seconds=2.5`, `pull_up_action_seconds=1.5`, `catch_and_shoot_action_seconds=1.0`, `loose_ball_action_seconds=0.5`) — verified: `test_every_live_dispatch_charges_positive_time`. `_charge_time` itself raises if ever asked to charge a non-positive `dt`. No player-specific speed/pace latent exists anywhere. Shot-clock/game-clock expiry is checked ONCE, at the top of each loop iteration (not mid-dispatch) — this lets an action that started with clock remaining complete legally (a real "beat the buzzer" semantic) while guaranteeing the NEXT iteration cannot dispatch past zero.

## L. Terminal-result design

`PossessionTerminalReason`: `MADE_FG`, `FINAL_FT_MADE` (points via FT(s) alone — a missed-and-one's awarded FTs, or a bonus non-shooting foul's FTs — with the ball going to the opponent), `DEFENSIVE_REBOUND`, `TURNOVER`, `OFFENSIVE_FOUL_TURNOVER` (a charge, kept distinct per the task's own suggested vocabulary), `SHOT_CLOCK_VIOLATION`, `PERIOD_END`. `PossessionTerminalResult` carries `reason`, EXPLICITLY computed `resulting_offense_team_id`/`resulting_defense_team_id` (never merely read off `engine.state`, since some Phase 15 primitives like `dead_ball_turnover` do not themselves flip team ids by their own documented design — this module always resolves the real next-possession ids itself), `stats`, `steps_taken`, `engine_state`, `events`, and `world` (for Phase 23B to seed the next possession). A caller never inspects event strings or resolver metadata to know the outcome.

## M. Loose-ball handling

Two closure paths, deliberately NOT unified into one subsystem:

1. **Shot/block/FT-adjacent LOOSE states** — reuse Phase 19's `rebound_resolution.py` completely as-is (`ReboundSource.MISSED_FG`/`UNRESOLVED_BLOCK`/`FINAL_MISSED_FT`), including the addendum's missing-estimate gating (Sec. E.7).
2. **Non-shot-adjacent LOOSE states** (a deflected pass, an on-ball strip) — the smallest generic closure, `resolve_generic_loose_ball`: eligible players are whoever (either team) currently occupies the ball's coarse zone (falls back to all ten if the zone bookkeeping has nobody there — never crashes, never fabricates a phantom carrier); a UNIFORM choice among them, with a small, explicit, UNCALIBRATED 2× weight for a `favored_team_id` when the producing resolver already signaled a real structural reason (Phase 17B's own `DEFLECTED_RETAINED_OFFENSE` vs. genuinely-unresolved `DEFLECTED_LOOSE_BALL`/`CLEAN_STRIP_LOOSE` distinction — reused, not invented). Applies the winner via the EXISTING `PossessionEngine.secure_loose_ball` (Phase 15, verbatim). No new loose-ball skill latent exists anywhere.

## N. Foul / FT handoffs

**Floor fouls** (charge/defensive floor foul from Phase 21A's on-ball pressure check): `_dispatch_floor_foul` calls Phase 21B's EXISTING `administer_floor_foul` with `possession_consequence_already_applied=True` (the exact seam Phase 21B's own report documents) — no redetection, ever. A charge → `OFFENSIVE_FOUL_TURNOVER` terminal. A non-bonus defensive floor foul → offense retains the ball; the orchestrator re-inbounds the fouled player at the same zone (the smallest honest V0 choice — no inbound playcalling) and CONTINUES the possession (verified: `test_G_floor_foul_administration`'s continuation check). A bonus defensive floor foul → the FT sequence Phase 21B's own machinery already administers determines the next state (made final → `FINAL_FT_MADE` terminal; missed final → rebound handoff).

**Shooting fouls** (Phase 18C, shot dispatch only): `_dispatch_shooting_foul` uses Phase 18C's OWN `PersonalFoulTracker`/`FreeThrowSequence`/`resolve_shooting_foul_shot`/`apply_free_throw_attempt_to_engine` — NEVER routed through `administer_floor_foul` (whose `foul_class` vocabulary is `OFFENSIVE_CHARGE`/`DEFENSIVE_FLOOR_FOUL` only, and is not, and should not be, broadened just to reuse one increment — per explicit instruction not to grow Phase 21B's vocabulary and not to add Phase 22B/detailed foul classification). Personal/team foul counts are still recorded onto the SAME shared `world.foul_state` object (a real personal/team foul is one running count regardless of type), via a direct `PersonalFoulTracker.increment`/`_with_team_foul_incremented` call — not via `administer_floor_foul`.

## O. Rebound and transition handoffs

Rebounds reuse Phase 19's `apply_rebound_to_engine` completely as-is; OREB → `world.stats.oreb += 1`, no terminal result (engine's own `secure_offensive_rebound_from_loose`/`credit_team_rebound("OFFENSE")` already clear `engine.advantage` per Phase 15/19's existing rule — the orchestrator does nothing extra); DREB → `DEFENSIVE_REBOUND` terminal. **Transition initialization for the NEXT possession is explicitly NOT built** — Phase 23A's job is to finish ONE possession correctly; `PossessionTerminalResult.world`/`engine_state` carry enough (team ids, final zones, foul state) for a FUTURE Phase 23B to seed the next possession/initialize transition state, but this module does not do that itself (no `initialize_transition_state` call anywhere) — the explicit line the task drew.

## P. Stat deltas

`StatDeltas`: `points`, `fga`/`fgm`/`fg3a`/`fg3m`, `fta`/`ftm`, `oreb`/`dreb`, `turnovers`, `steals` (recorded only when a real `disrupting_defender_id` is known from a `CLEAN_INTERCEPTION`'s own event metadata), `blocks` (recorded only when `interior_shot_resolution`'s own `blocker_id` is known), `personal_fouls` (a `player_id -> count` dict, this possession only). Assist attribution is NOT fabricated — no `ast` field exists anywhere. A missed shooting foul correctly contributes zero FGA/FGM (Phase 18C's own real accounting rule, reused, not re-derived).

## Q. Autonomous possession traces

All four generated through the real, unmodified top-level `simulate_possession` API — diagnostic traces, not calibration/realism claims.

**1. Simple made-shot possession** (`reason=MADE_FG`, 2 steps):
```
ON_BALL_PRESSURE  CLEAN_CONTROL       driver=1  defender=11
DRIVE             FORCED_PICKUP       driver=1  defender=11  zone=TOP_OF_KEY
SWING_PASS        COMPLETED_ADJUSTED  passer=1 -> receiver=2  zone=TOP_OF_KEY
PULL_UP           MADE (THREE_POINT)  shooter=2  zone=TOP_OF_KEY
```
shot_clock/game_clock finished at 19.6/715.6; stats: `points=3, fga=1, fgm=1, fg3a=1, fg3m=1`.

**2. Multi-action possession (pass → shot → miss → DREB)** (`reason=DEFENSIVE_REBOUND`, 3 steps):
```
SWING_PASS        COMPLETED_CLEAN     passer=1 -> receiver=2
SWING_PASS        COMPLETED_ADJUSTED  passer=2 -> receiver=1
CATCH_AND_SHOOT   MISSED (THREE_POINT) shooter=1
PULL_UP           MISSED (THREE_POINT) shooter=2
```
resulting offense/defense flips to B/A; stats: `fga=2, fgm=0, oreb=1, dreb=1` (an offensive rebound occurred mid-possession before the eventual defensive rebound ended it — a real second-chance sequence).

**3. Turnover possession** (`reason=TURNOVER`, 1 step):
```
RESET_PASS        COMPLETED_CLEAN     passer=1 -> receiver=2
SWING_PASS        CLEAN_INTERCEPTION  passer=2 -> receiver=1
```
`steals=1`, `turnovers=1`, resulting offense/defense flips to B/A.

**4. Rebound / second-chance possession** (offensive rebounding boosted to 0.6 / defensive rebounding dropped to 0.05 to make second chances frequent; `reason=MADE_FG`, 26 steps — a long, realistic diagnostic showing repeated second-chance/loose-ball closure before the possession finally ends):
```
CATCH_AND_SHOOT   MISSED               shooter=1
ON_BALL_PRESSURE  DISRUPTED            driver=1 / defender=11
DRIVE             FORCED_PICKUP        driver=1
SWING_PASS x2     COMPLETED_CLEAN
SWING_PASS        DEFLECTED_RETAINED_OFFENSE
LOOSE_BALL_RECOVERY  OFFENSE_RECOVERED
PULL_UP           MISSED
... (several more reset passes, drives CONTAINED, a deflection, a BLOCKED_RETAINED_OFFENSE) ...
CATCH_AND_SHOOT   MADE (RIM)           shooter=1
```
The generic loose-ball resolver (Sec. M.2) fires four separate times in this one trace, each time correctly preserving offense (`OFFENSE_RECOVERED`), and a `BLOCKED_RETAINED_OFFENSE` correctly feeds Phase 19's rebound resolver rather than a terminal result, before the possession finally ends on a made rim shot.

## R. Targeted tests

`python3 -m unittest test_possession_orchestrator -v` → **Ran 43 tests — OK** (35 from initial implementation + 8 added during the reconciliation pass, Sec. W). Covers: lineup validation (4), matchup bijection including post-possession-change rebuild (6), StructuralContext derivation with no manual construction (2), capability gating structurally and over real seeds (3), the addendum's disabled-modifier firewalls (2), rebound missing-estimate gating (1), integration scenarios A–N (14, one per required letter), clock-ownership source-scans (2), event/stat consistency (5, Sec. W), and the dual-engine source-of-truth-hierarchy firewall (3, Sec. W).

## S. Full suite

`python3 -m unittest discover -p "test_*.py"` → **Ran 823 tests — OK** (780 carried over from the Phase 22A checkpoint + 43 new in `test_possession_orchestrator.py`). No existing test was modified or removed. The two `TEST-RP-BAD`/`TEST-SZ-BAD` lines are the same pre-existing, expected simulated-failure log lines present in every prior baseline run.

## T. Known limitations / placeholders

- All action durations (`PossessionConfig.*_action_seconds`) are real, hand-set, EXPLICITLY UNCALIBRATED placeholders — loop-correctness only, not timing realism.
- `default_v0_zone_placement`/`_mirror_defender_zones` are orchestration scaffolding, not calibrated player positioning.
- `resolve_generic_loose_ball`'s uniform-with-a-small-favored-team-weight model is a real, hand-set, UNCALIBRATED placeholder.
- Perimeter shots are always dispatched as `THREE_POINT` in V0 (Sec. I) — no mid-range dispatch path yet.
- `force_on_ball_contact_established` is a TEST-ONLY override; ordinary V0 drives never risk a charge/floor-foul spontaneously (no real per-drive contact-occurrence rate exists in this project to drive that decision honestly).
- No real-player-profile ingestion exists yet — `PlayerSimulationProfile.synthetic(...)` is the only constructor; a real one must follow the binding contract documented in the profile's own docstring (Sec. E).
- `nearest_teammate_id`'s coarse zone-topology proximity is a real, documented simplification, not spatial reasoning.
- Transition initialization for the NEXT possession, substitutions, fatigue, timeouts, coaching AI, playbooks, full box score, and the multi-possession game loop are all explicitly out of scope (Phase 23B+).

## U. Recommended classification

**READY WITH FLAGS.**

Ready: the full autonomous loop runs end-to-end through one real top-level API across hundreds of seeded possessions without a crash; every required integration scenario (A–N) is reproduced through the REAL loop (not mocked); the capability gate is enforced both structurally and behaviorally; clock ownership is unambiguous and the pass-resolver double-charge risk is explicitly avoided and tested; the atomic matchup bijection holds initially and after a role swap; floor-foul and shooting-foul paths correctly route through Phase 21B and Phase 18C respectively with no redetection or double-administration; the step-guard fault is real, distinguishable, and tested; the mid-turn adapter addendum's 19 verification points are all satisfied (Sec. V below); the dual-engine doctrine reconciliation (Sec. W) is complete, with `StatDeltas` now explicitly documented as provisional/non-authoritative and cross-checked against the real event stream for every field currently derivable, at zero mismatches over 2,400+ possessions across 3 configurations.

Flags: every duration/placement/loose-ball-weight constant is an uncalibrated V0 placeholder; no real-player ingestion exists yet; perimeter shots don't yet distinguish mid-range from three-point; the drive-foul pre-check ordering is a documented, not empirically forced, choice; scoring/FT stats (points/FGA/FGM/3PA/3PM/FTA/FTM) remain NOT event-derivable pending a future, deliberately-scoped event-schema addition (Sec. W).

## V. Addendum verification checklist

1. Runtime identity begins with canonical `player_id` — ✅ (`PlayerSimulationProfile.player_id`, `_assert_player_id` enforced transitively by every resolver called).
2. All source calls share one `as_of_season` — N/A this phase (no real ingestion built); binding contract documented for the future constructor.
3. Historical ball-security source seasons cutoff-filtered — N/A this phase (same reason); documented as a hard requirement before any real historical ball-security path is added.
4. 0–99 values never feed resolvers — ✅ (no `rating_0_99`/percentile field exists anywhere in `PlayerSimulationProfile`).
5. Every shot family uses native `shrunk_rate` — ✅ (`three_point_shrunk_rate`/`rim_finishing_shrunk_rate`/`floater_short_mid_shrunk_rate` passed straight through).
6. Vision modifier is `None` — ✅ (`test_vision_modifier_is_none`).
7. Foul-drawing modifier is `None` — ✅ (`test_foul_fields_never_reach_a_resolver`).
8. Foul-discipline modifier is `None` — ✅ (same test).
9. Ball security uses specialized handling-error rate, higher worse, un-inverted — ✅ (Sec. E.4).
10. Passing remains the AST_PCT proxy — ✅ (Sec. E.5).
11. Drive gets native rim-access and POA rates exactly once — ✅ (Sec. J, no z-score in the adapter).
12. Rim protection suppresses conversion; defensive playmaking owns block probability — ✅ (unchanged from `interior_shot_resolution.py`'s own Phase 18B split; this module never crosses them).
13. Missing rebound values never reach the unsafe `None -> 0.5` path — ✅ (`test_player_missing_side_specific_estimate_excluded_from_competition`).
14. Selection uses tendency `latent_propensity` — ✅ (`_tendency_context` passes profile fields straight through).
15. `orb_crash` is absent — ✅.
16. Physical effects are disabled — ✅ (no physical field anywhere; `enable_physical_adjustment` never set `True`).
17. Lost-ball drive scaffolding is disabled — ✅ (`enable_lost_ball` never set `True`).
18. Evidence/provenance/exposure/confidence/cutoff survive profile construction — documented as a REQUIREMENT of the future real constructor (Sec. E's binding contract); not applicable to the current synthetic-only path.
19. No fallback silently converts missing evidence into zero, 50, 0.5, or a probability — ✅ (`_require`/`_require_ft_rate` raise explicitly; rebound gating excludes rather than defaults; every DISABLED modifier is `None`, which is each resolver's own documented neutral behavior, not a fabricated value).

## W. Dual-engine doctrine reconciliation

### A. How `StatDeltas` worked before reconciliation

`StatDeltas` was a plain, per-possession dataclass (`points`, `fga`/`fgm`/`fg3a`/`fg3m`, `fta`/`ftm`, `oreb`/`dreb`, `turnovers`, `steals`, `blocks`, `personal_fouls`) held on `world.stats` and mutated DIRECTLY, inline, by dispatch code as each action resolved (e.g. `world.stats.fga += 1` right after a shot resolver returned, `world.stats.turnovers += 1` right after a pass/foul outcome was classified). It was never derived from, or checked against, the event stream the SAME dispatch code was simultaneously producing via the resolvers it called.

### B. Was this a genuine second-source-of-truth risk?

**Yes, structurally** — not yet a lived bug, but a real architectural risk. Phase 15 established the event stream as the accounting/source-of-truth DIRECTION for the detailed engine; `StatDeltas` was a second, independently-mutated accounting path in the SAME file, with no mechanism preventing it from drifting from what the events actually recorded. Direct investigation surfaced exactly one place this was already **latent** (not yet asserted anywhere, so not caught by the pre-reconciliation test suite): `_dispatch_pass`'s `BAD_PASS_TO_DEFENDER` branch could increment `world.stats.turnovers` under a condition that did not exactly match `pass_resolution.py`'s own real rule for when that outcome is genuinely a turnover (a `None` disrupting-defender target means the ball went LOOSE, not directly to a turnover) — see Sec. C's bugfix, found and fixed DURING this reconciliation by building the very cross-check the task asked for.

### C. Exact correction made

1. **`StatDeltas` is now explicitly documented as PROVISIONAL, non-authoritative** (its own class docstring states this plainly, and `test_stat_deltas_is_documented_as_provisional` checks it) — never described as a second accounting truth anywhere in code or docs.
2. **A new `derive_stat_deltas_from_events(events) -> EventDerivedStats` function** reconstructs, PURELY from `engine.log.events`' structured `event_type`/`primary_player_id`/`secondary_player_id`/`metadata` fields (never prose/log-string interpretation), every stat currently classified DERIVABLE NOW (Sec. E below). `EventDerivedStats` has NO `points`/`fga`/`fgm`/`fg3a`/`fg3m`/`fta`/`ftm` fields at all — their ABSENCE is the honest NOT-DERIVABLE-NOW classification (Sec. F), not an oversight (verified: `test_event_derived_stats_has_no_scoring_fields`).
3. **Two tiny, additive, orchestrator-owned event-logging fixes** (both inside `possession_orchestrator.py` only — zero changes to any existing phase file) close two real gaps that left ZERO trace in the event stream:
   - `resolve_generic_loose_ball` now logs a `REACTION_CHECKPOINT` (`checkpoint="generic_loose_ball_recovered"`, `recovering_team_id`, `recovery`) after calling `PossessionEngine.secure_loose_ball` — which itself logs nothing at all (confirmed by direct source read of `possession_engine.py`).
   - `_dispatch_floor_foul` now logs a `REACTION_CHECKPOINT` (`checkpoint="floor_foul_administered"`, `foul_class`, `foul_event_id`) after calling `administer_floor_foul` — which itself logs nothing at all. This specifically closes the gap where an `OFFENSIVE_CHARGE`'s personal foul was otherwise indistinguishable in the event stream from any other generic `DEAD_BALL_TURNOVER`.
   Both reuse the EXISTING `EventType.REACTION_CHECKPOINT` (the same event type Phase 17A/17B/21A/22A already reuse for their own resolutions) — no new `EventType` was added, no event-vocabulary bloat.
4. **A real bug found and fixed by building the cross-check**: `derive_stat_deltas_from_events`'s turnover classification for `PASS_RESOLVED` events now matches `pass_resolution._apply_outcome`'s own real rule exactly — `BAD_PASS_TO_DEFENDER` counts as a turnover ONLY when `metadata["disrupting_defender_id"]` is not `None` (a `None` target means the ball went LOOSE, which the offense may recover, per that module's own logic) — the orchestrator's OWN `StatDeltas` dispatch logic (Sec. A) was already correct on this point; the first draft of `derive_stat_deltas_from_events` was not, and cross-checking caught it before it shipped.
5. **Personal-foul double-counting avoided by construction**: the new `floor_foul_administered` checkpoint is counted toward `personal_fouls` ONLY for `foul_class == OFFENSIVE_CHARGE` — a `DEFENSIVE_FLOOR_FOUL`'s personal foul is already fully derivable from its own real `NON_SHOOTING_FOUL` event, and counting both would double-count the same real foul.
6. **Consistency tests added** (`TestEventStatConsistency`, 5 tests) assert `world.stats.{oreb,dreb,turnovers,steals,blocks,personal_fouls}` exactly match `derive_stat_deltas_from_events(result.events)` across 450 possessions spanning 3 distinct configurations (default, forced-contact, and long-duration/shot-clock-violation-heavy) — **zero mismatches**, confirmed both in this ad-hoc verification run and in the committed test.

### D. Detailed-engine source-of-truth hierarchy now enforced

Three distinct truths, explicitly documented in the module's own docstring and never conflated:

| Layer | Owner | Role |
|---|---|---|
| LIVE BASKETBALL STATE | `engine.state` (Phase 15) + `PossessionWorld` | what is true RIGHT NOW |
| EVENT-STREAM ACCOUNTING | `engine.log.events` | the ordered, replayable accounting truth (partial — Sec. E/F) |
| CONTROL-FLOW / ORCHESTRATION TRUTH | `PossessionTerminalResult` | whether/why the possession ended, and context for Phase 23B |

`StatDeltas` sits OUTSIDE this hierarchy as a provisional convenience layer, explicitly not authoritative, cross-checked where the event schema currently allows.

### E. Possession stats DERIVABLE NOW from events

| Stat | Classification | Event(s) used |
|---|---|---|
| OREB | **DERIVABLE NOW** | `EventType.OFFENSIVE_REBOUND` |
| DREB | **DERIVABLE NOW** | `EventType.DEFENSIVE_REBOUND` |
| turnovers | **DERIVABLE NOW** | `DEAD_BALL_TURNOVER` + `LIVE_BALL_TURNOVER` + `PASS_RESOLVED` (outcome-classified, Sec. C.4) + this phase's new `generic_loose_ball_recovered` checkpoint |
| steals | **DERIVABLE NOW** | `PASS_RESOLVED` with `outcome="CLEAN_INTERCEPTION"` and a real `disrupting_defender_id` |
| blocks | **DERIVABLE NOW** | `BLOCK_RETAINED_BY_OFFENSE` + `BLOCK_SECURED_BY_DEFENSE` |
| personal fouls | **DERIVABLE NOW** | `SHOOTING_FOUL` + `NON_SHOOTING_FOUL` + this phase's new `floor_foul_administered` checkpoint (charge case only) |

### F. Possession stats NOT YET DERIVABLE from events, and why

| Stat | Classification | Exact missing field |
|---|---|---|
| points | **NOT DERIVABLE** | `SHOT_RESOLVED`'s `metadata` carries only `{"made": bool}` — no shot-family/point-value field exists in the Phase 15 event schema at all. |
| FGA / FGM | **PARTIALLY DERIVABLE** | Ordinary shots DO log `SHOT_RELEASED`+`SHOT_RESOLVED` pairs, but this module's own whistled-and-one path (`_dispatch_shooting_foul`) calls `engine.begin_shot`/`engine.shooting_foul` directly and never logs a `SHOT_RESOLVED` for that shot at all — an and-one basket is currently invisible to the event stream. |
| 3PA / 3PM | **NOT DERIVABLE** | Same root cause as points — no shot-family field anywhere in `SHOT_RESOLVED`'s metadata to distinguish a 2 from a 3. |
| FTA / FTM | **NOT DERIVABLE** | No free-throw `EventType` or `_log` call exists ANYWHERE in this repository — `foul_resolution.apply_free_throw_attempt_to_engine` performs a bare `dataclasses.replace()` on `engine.state` with zero logging (confirmed by direct source read). |

### G. Event-schema fields Phase 23B needs

1. A `shot_family` and/or explicit point-value field added to `SHOT_RESOLVED`'s `metadata` (a Phase 15 `possession_engine.py` change — NOT made this phase, since it touches a foundational, cross-phase, already-extensively-tested file; not "tiny").
2. A new `EventType` for a free-throw attempt (or a documented convention for logging one via `REACTION_CHECKPOINT`), plus a decision about where it's logged — `foul_resolution.apply_free_throw_attempt_to_engine`'s FT loop is called both directly (Phase 23A's own shooting-foul path, where a caller-side fix would be possible) AND indirectly, from inside `floor_foul_administration.administer_floor_foul` (Phase 21B, outside Phase 23A's per-attempt control) — a real reason a partial, inconsistent fix was rejected this phase (Sec. F, and the module's own `derive_stat_deltas_from_events` docstring).
3. A supplementary `SHOT_RESOLVED`-equivalent log call inside `_dispatch_shooting_foul` for the and-one shot itself (achievable within Phase 23A's own file once (1) above exists, since this module already controls that call site).

### H. Files changed beyond the existing Phase 23A set

None new — the reconciliation is entirely additive edits to the SAME two Phase 23A files (`possession_orchestrator.py`, `test_possession_orchestrator.py`) plus this report. No third file was created.

### I. Confirmation: no legacy/product-routing files were touched

Verified directly: `git status --short` shows only the three pre-existing Phase 23A files (`possession_orchestrator.py`, `test_possession_orchestrator.py`, this report) — `main.py`, `season.py`, `playoffs.py`, `db.py`, `game_engine.py`, `README.md`, `ACCURACY.md` are untouched. `grep -l possession_orchestrator main.py season.py playoffs.py db.py game_engine.py` finds nothing in any of the five (exit code 1 on every one), confirmed both ad-hoc and via `test_no_product_or_legacy_file_references_the_orchestrator`. `possession_orchestrator.py` itself contains no `import game_engine`/`from game_engine` anywhere (`test_orchestrator_never_imports_game_engine`). Also reconfirmed this phase has NOT: promoted V0 action-duration constants into repository-wide truth (they live solely on `PossessionConfig`, a Phase-23A-local dataclass with no reference anywhere outside this module); treated synthetic profiles as a real-player fallback (`PlayerSimulationProfile.synthetic` is explicitly labeled test/demo-only in its own docstring, and no other code path substitutes it silently); created an engine-specific duplicate player-truth store (`PlayerSimulationProfile` is explicitly an ADAPTER, never a second identity system, per its own binding construction contract); or made the possession engine depend on a name-keyed runtime join anywhere (`_assert_player_id` is enforced transitively by every resolver this module calls).

### J. Targeted test count

**43/43 OK** (see Sec. R).

### K. Full-suite count

**823/823 OK** (see Sec. S) — 780 baseline + 43 new, zero regressions.

### L. Revised Phase 23A classification

**READY WITH FLAGS** (see Sec. U — unchanged in kind, strengthened in substance: the one architectural risk the doctrine audit flagged has been corrected and tested, not merely documented away).

### M. Safe to checkpoint as the isolated detailed-engine single-possession milestone?

**Yes.** The dual-engine doctrine boundary is now explicit in the module's own docstring and independently verified (Sec. I); `StatDeltas` is honestly reclassified as provisional and cross-checked wherever the event schema currently allows, with the exact remaining gaps itemized for Phase 23B (Sec. F/G) rather than glossed over; no legacy/product file was touched; the full suite remains green. Still NOT committed and NOT pushed, per instruction, pending explicit sign-off.

Not begun: Phase 23B. Not committed. Not pushed.
