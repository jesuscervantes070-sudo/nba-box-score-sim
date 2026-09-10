# PROJECT STATE — Authoritative Handoff

## NEW CLAUDE CHAT BOOT SEQUENCE

1. Read `CLAUDE.md` (repo root) first.
2. Read this file (`docs/PROJECT_STATE.md`) in full.
3. Read the report for the most recently completed phase (see §M, the
   report index, for the exact filename — as of this writing that's
   `docs/PHASE12A_ANTHROPOMETRICS_REPORT.md` — see §L for what's next).
4. Run `git status` and compare against §M/§L below; if anything
   disagrees, **trust the repo, not this document**, and say so.
5. Run the test suite (`python3 -m unittest discover -p "test_*.py"`)
   or otherwise confirm the current passing count — do not quote the
   number in this file without checking.
6. Inspect current caches/modules relevant to the requested task before
   any new ingestion.
7. Continue ONLY the phase the user actually asks for.
8. Do not reopen a completed phase without new evidence/explicit
   instruction to revisit it.

---

## A. Project goal

A long-horizon, generative MyNBA-like NBA world simulator. Real
historical data trains/validates latent player state; history informs
**probabilistically**, not deterministically (a 2016-17 Warriors box
score is evidence about that season's players' latent abilities, not a
script to replay). The existing playable product (`README.md`) is a
separate, legacy box-score replay simulator — the ability/tendency/
physical work described below is offline research feeding a **future**
possession-level engine, not yet integrated with the legacy sim.

## B. Layered architecture (top to bottom, causal order)

1. **Physical traits** — geometry/body state (height, reach, wingspan,
   mass; later maybe vertical pop, workload capacity). NOT skills.
2. **Latent basketball abilities** — `PlayerAbilityProfile` /
   `AttributeEstimate` (see §C). Stabilized, multi-year, "can."
3. **Player tendencies** — separate `PlayerTendencyProfile`-style layer
   (see §E). "Prefers/attempts," relative to contemporaneous league
   environment.
4. **Lineup roles / team system** — not built yet (Phase 13 candidate).
5. **Possession mechanics** — not built yet. Physicals + abilities +
   tendencies + role/system will jointly resolve a possession; no
   component directly shortcuts into another's job (e.g. a tendency must
   never directly raise a shooting percentage).
6. **Observed stats / evidence** — real box scores, tracking data, PBP —
   the training/validation signal for layers 1-3, never the target
   itself.
7. **Beliefs / scouting** — not built yet. Will be allowed to be wrong
   about a player; distinct from the generative truth in layers 1-3.
8. **Economics / world systems** — not built yet (draft, contracts,
   trades, schedule, awards, etc. — see §J for provisional notes).

## C. Current skill taxonomy (`player_ability_profile.py`'s `SKILL_ATTRIBUTES`)

**Scoring**: `rim_finishing`, `floater_short_mid`, `midrange`,
`three_point`, `free_throw`, `shot_creation` (slot exists, intentionally
UNWIRED — see Phase 9), `foul_drawing`.
**Playmaking**: `passing` (= "passing_accuracy" in prose),
`creation_for_others` (repurposed to hold Phase 8's `playmaking_vision`
estimate — see §F Phase 8), `ball_security`.
**Defense**: `perimeter_defense` (repurposed to hold Phase 10's
`poa_containment` estimate), `interior_defense` (unused), `rim_protection`,
`defensive_playmaking`, `defensive_versatility` (unused),
`foul_discipline`.
**Rebounding**: `offensive_rebounding`, `defensive_rebounding`.

`rim_access_creation` and `perimeter_space_creation` (Phase 9) are
**internal-only** — no `SKILL_ATTRIBUTES` slot; deliberately not
aggregated into `shot_creation` yet (see Phase 9 in §F).

`interior_defense` and `defensive_versatility` exist in the schema but
have no estimator and are not an active part of the current roadmap.

## D. Current attribute board

**LOCK V1**: `rim_finishing`, `midrange`, `offensive_rebounding`,
`defensive_rebounding`, `rim_protection`.
**KEEP / STRONG**: `three_point`, `free_throw`, `passing_accuracy`,
`defensive_playmaking`.
**KEEP BUT FLAG**: `floater_short_mid`, `ball_security`, `foul_drawing`,
`foul_discipline`, `playmaking_vision`, `rim_access_creation`,
`perimeter_space_creation`, `poa_containment`.

## E. Tendency board (separate layer, `player_tendencies_*.py`)

**LOCK V1**: `three_point_preference`.
**KEEP**: `midrange_preference`, `drive_aggression`, `pass_vs_shoot`.
**KEEP BUT FLAG / FUTURE**: `pullup_vs_catch` (real usage/touches
contamination 0.47-0.67, no conditional-opportunity control built).
**EXPERIMENTAL / REVISIT**: `orb_crash` — real ~0.996 correlation with
the existing `offensive_rebounding` ability. **Do not use `orb_crash`
alongside `offensive_rebounding` in any future gameplay/possession logic
— near-certain double-count of the same empirical signal.**
**POSTPONED** (not attempted): `offball_cut`, `isolation_hunt`,
`steal_gamble`, `block_hunt`.

## F. Phase-by-phase results (concise — read the linked report for full methodology)

**Phase 1-3** (pre-dates the numbered-phase convention; see
`PLAYER_ABILITY_HANDOFF.md`, kept as historical reference, not superseded
by this file for that era's detail): built `player_ability_profile.py`
(`AttributeEstimate`, `PlayerAbilityProfile`, `SKILL_ATTRIBUTES`
foundation) and `player_ability_estimation.py` (the six originally-
calibrated attributes: `three_point`, `free_throw`, `passing`,
`offensive_rebounding`, `defensive_rebounding`, `defensive_playmaking`),
plus `player_ability_calibration.py`/`.json`/`_v2.json` (λ/M + optional
age adjustment). These six were calibrated via real walk-forward
backtests; `ball_security` was left as an unvalidated box-proxy at that
point.

**Phase 4A/4B — Ball Security** (`ball_security_ingestion.py` →
`turnover_ingestion.py`, `ball_security_analysis.py`,
`ball_security_calibration.py`/`.json`, `ball_security_estimation.py`):
real `playbyplayv3` turnover-subtype classification (Shooting/Personal/
Offensive/etc., same taxonomy convention reused in Phase 6). Handling-
error numerator explicitly EXCLUDES bad-pass turnovers (that's a passing/
decision error, not a handling failure). Best modern denominator:
`estimated_total_dribbles` (from `handling_exposure.py`'s
`TOUCHES × AVG_DRIB_PER_TOUCH`), beating raw `touches`/`FGA` once a
scale-normalized (CV_MAE) comparison was used — **a real methodological
lesson repeated in later phases**: never compare candidate denominators
on raw MAE alone if their natural scales differ. Historical (pre-
tracking) mode exists at lower confidence. Two real ingestion-scaling
bugs were found and fixed during Phase 6 that also retroactively explain
some Phase 4B softness (partial-season vs. full-season joins, and
`games_total` reflecting a `max_games` cap instead of the real schedule
length). Final: **KEEP BUT FLAG**.

**Phase 5 — Shot Zones** (`shot_zone_ingestion.py`,
`shot_zone_estimation.py`, `shot_zone_calibration.py`/`.json`/
`_search.py`): `LeagueDashPlayerShotLocations`, real zone FGM/FGA
(Restricted Area / Paint Non-RA / Mid-Range / 3PT zones), 1996-97+, full
30-season backfill (cheap, one call/season). `rim_finishing` (Restricted
Area) and `midrange` LOCK V1 (exact nested train/test parameter match).
`floater_short_mid` (Paint Non-RA) KEEP BUT FLAG (nested parameter
mismatch). 1996-97's shortened-3PT-line era was investigated and found
NOT to contaminate these three zones specifically (the discontinuity is
confined to 3PT-zone shares, out of scope).

**Phase 6 — Foul Drawing + Foul Discipline** (`foul_ingestion.py`,
`foul_analysis.py`, `foul_calibration.py`/`.json`, `foul_estimation.py`):
same `playbyplayv3` real subtype taxonomy as Phase 4A/4B, extended with
real drawer-vs-committer attribution (drawer = the following real Free
Throw event's shooter; committer = the Foul row's own real `personId`).
And-1 double-counting explicitly verified and prevented (shooter must
match the next FT's shooter AND it must be a real "1 of 1"). Best
drawing denominator: `drives` (beats `fga`/`touches`/`rim_paint_fga`
once scale-normalized — the SAME CV_MAE lesson from Phase 4B, re-learned
and this time applied proactively for Phase 7+). Both **KEEP BUT FLAG**:
`foul_drawing` (real ~50-57% drawer-attribution coverage gap, real
reb_pct/big correlation not corrected) and `foul_discipline` (real
"bigs foul more per minute" bias, well-documented, not corrected).

**Phase 7 — Rim Protection** (`rim_protection_ingestion.py`,
`rim_protection_analysis.py`, `rim_protection_calibration.py`/`.json`
[**the generic engine other phases later reused**],
`rim_protection_estimation.py`): `leaguedashptdefend` (Less Than 6Ft),
real NBA-computed `PLUSMINUS` (opponent FG% vs. real "normal shooting"
expectation for that shot type) as the suppression signal — the real,
first-party analogue of an "expected FG%" residual, not invented.
`rim_fga_defended` = real opportunity/exposure. Real, decisive: only
25-42% of variance shared with `defensive_playmaking`'s BLK rate (not a
rediscovery). Real TRAIN/HELDOUT split (not just nested walk-forward) —
TRAIN MAE ≈ HELDOUT MAE almost exactly. **LOCK V1** — the strongest
validation bar cleared by any attribute so far. Real attempt-deterrence
(do opponents avoid shooting at all) was investigated and found
NOT buildable from real per-player data — documented FUTURE ONLY.

**Phase 8 — Playmaking Vision** (`passing_tracking_ingestion.py`,
`playmaking_vision_analysis.py`, `playmaking_vision_calibration.py`/
`.json`, `playmaking_vision_estimation.py`): `leaguedashptstats`
(Passing measure), real `POTENTIAL_AST`. Raw `POTENTIAL_AST/touches`
correlated 0.85-0.91 with the EXISTING `passing_accuracy` — too high,
real dead end found fast. One targeted TRAIN-only-fit residualization
(usage, drives/touch, TOP/touch) dropped that to 0.37-0.42 while
RETAINING 0.64 of the raw 0.92 real T→T+1 stability — decisive proof it
removed opportunity, not the underlying skill. Wired into the EXISTING
`creation_for_others` slot (not a new schema entry). **KEEP BUT FLAG**
(imperfect calibration parameter transfer).

**Phase 9 — Shot Creation Internals** (`shot_creation_ingestion.py`,
`shot_creation_analysis.py`, `shot_creation_calibration.py`/`.json`,
`shot_creation_estimation.py`): `rim_access_creation` = real
`(DRIVE_FGA+DRIVE_FTA+DRIVE_AST−DRIVE_TOV)/DRIVES` (conditioned on real
drive-initiation count, not raw volume). `perimeter_space_creation` =
real `PULL_UP_FGA/touches`, residualized (usage, TOP/touch) after raw
form showed 0.45-0.65 role contamination. Real, near-zero mutual
correlation between the two components (−0.07 to +0.13, three seasons)
— genuine two-component architecture **SUPPORTED**. Both **KEEP BUT
FLAG**. `shot_creation`'s existing profile slot deliberately left
UNWIRED — no 50/50 or invented combination weight.

**Phase 10 — POA / Perimeter Defense** (`poa_containment_ingestion.py`,
`poa_containment_analysis.py`, `poa_containment_calibration.py`/`.json`,
`poa_containment_estimation.py`): `leagueseasonmatchups` — real,
ENTIRE-LEAGUE player-vs-player matchup data in ONE call/season (real
floor 2017-18; 2016-17 exists but is a too-sparse partial rollout).
Candidate = real, opponent-quality-adjusted matchup FG% suppression
(`(expected_fgm − matchup_fgm)/expected_fga_covered`, each opponent's own
real season FG% as the baseline — no hand-written star discount).
Structurally excludes help defense (`HELP_FGM/FGA` is a separate real
field on the same row). Real, decisive: ~0 correlation with STL/36 (not
steals) and ~0 with usage (not role). Real, larger-than-Phase-7 team-
scheme contamination (−0.28 to −0.39 with team defensive quality) and
the weakest raw T→T+1 stability of any phase so far (0.22-0.37) — real
21.5% held-out improvement via shrinkage regardless. Real attempt-
deterrence/drive-suppression mechanism confirmed unavailable in public
data — FUTURE ONLY. Stored in the EXISTING `perimeter_defense` slot.
**KEEP BUT FLAG**.

**Phase 11 — Player Tendencies Foundation** (`player_tendencies_analysis.py`,
`player_tendencies_estimation.py`) — **zero new API calls**, everything
reused from Phases 4B/5/8/9's caches. Central finding: real T→T+1 rank
stability 0.86-0.92 across all six raw candidates — dramatically higher
than any latent ability. Internal representation is a **logit-relative-
to-contemporaneous-league-average latent propensity** (NOT a raw share,
NOT a probability) — this also solves the real cross-era problem (league-
average `three_point_preference` nearly doubled, 0.204→0.407, 1996-97 to
2023-24, checked directly). A real bug was found and fixed mid-phase:
`orb_crash` (OREB per-36, NOT a [0,1] share) was being silently
logit-clipped to a constant — fixed with a log-ratio transform instead,
locked in with a regression test. `PlayerAbilityProfile` was **not
touched** — tendencies are a genuinely separate object
(`PlayerTendencyEstimate`), per explicit instruction. Real team-switch
portability (0.82-0.90 for switched players) used as supporting, not
definitive, evidence per an explicit addendum mid-phase. 271/271 tests
passing at completion (up from 256 pre-phase).

**Phase 12A — Anthropometrics Foundation (COMPLETE)**: `anthropometrics_ingestion.py`
+ `anthropometrics_analysis.py` + `anthropometrics_profile.py`
(`PhysicalObservation`/`PlayerPhysicalProfile`, keyed by real stable
`player_id`) + `anthropometrics_estimation.py`
(`build_physical_profile(player_id, as_of_season)`), with
`test_anthropometrics_ingestion.py` (9 tests) + `test_anthropometrics_profile.py`
(15 tests) — 295/295 total suite passing. Full methodology/results in
`docs/PHASE12A_ANTHROPOMETRICS_REPORT.md`. Classifications: `height`
LOCK V1, `standing_reach` KEEP, `mass` KEEP, `wingspan` KEEP BUT FLAG
(wider heldout inference error + unresolved listed-height-bias-import
issue for pre-combine-era players). Real sources verified and ingested:
`draftcombineplayeranthro` (real floor: draft class 2000; 2001 is a real
gap year with 0 rows; barefoot `HEIGHT_WO_SHOES` is the only field with
continuous coverage across 2000-2025 — `HEIGHT_W_SHOES` was only
measured ~2010-2020 and is NOT usable as a primary field) — all 26 years
(2000-2025) cached under `cache/anthropometrics/combine_<year>.json`.
`commonteamroster` re-fetched (same endpoint `data_source.py` already
uses for `rosters.json`, but that file only kept player names — this
phase's `player_physical_roster.json` also keeps real HEIGHT/WEIGHT/
PLAYER_ID) for 6 representative seasons (1996-97/2005-06/2013-14/
2018-19/2022-23/2023-24 — NOT a full 30-season backfill, deliberately,
per the phase's own efficiency instruction; 2022-23 was added to get one
genuine adjacent-season pair for the mass-staleness check). Real
findings, written up in `docs/PHASE12A_ANTHROPOMETRICS_REPORT.md`:
roster-listed height runs a real, measured **+1.014 inch (stdev 0.671)**
above real combine barefoot height on matched players (n=723) — a real
bias, NOT corrected (kept as a labeled, separate tier, not blended);
combine PLAYER_ID directly equals the real stable NBA `player_id` for
players who went on to play in the league (no name-based crosswalk
needed for that population — see report Sec. 4 for the full,
non-obvious match-rate-by-era finding); a `wingspan ~ height` regression
(train drafts <2018, heldout 2018+) gives a real but only moderate 1.73"
heldout MAE (real outliers up to ±6.5") — `standing_reach ~ height +
wingspan` is tighter (1.09" heldout MAE). `anthropometrics_profile.py`
(`PhysicalObservation`/`PlayerPhysicalProfile`, keyed by real
`player_id`) and `anthropometrics_estimation.py`
(`build_physical_profile`) are built, tested (15 tests in
`test_anthropometrics_profile.py`), and validated against 5 real
representative players (guard/wing/multiple big-man archetypes) plus
explicit temporal-leakage and missing-player cases. **Deferred, not
built this phase** (see report Sec. 12): a diagnostic-only
physical-vs-skill correlation pass (reach vs rim_protection, wingspan vs
defensive_playmaking, mass vs rebounding, height vs finishing) — real,
concrete blocker: existing skill estimators are keyed by player NAME
while `PlayerPhysicalProfile` is keyed by `player_id`, and no
name↔player_id crosswalk exists yet in this repo. Do NOT re-ingest
anthropometrics data — all of it is already cached.

## G. Verified data sources / coverage (by module)

| Source (real endpoint) | Used by | Real floor / notes |
|---|---|---|
| `leaguedashplayershotlocations` | `shot_zone_ingestion.py` (Phase 5) | 1996-97+, full 30-season backfill, cheap |
| `playbyplayv3` | `turnover_ingestion.py` (4A/4B), `foul_ingestion.py` (6) | 1996-97+ for subtype detail; per-GAME cost, NOT cheap — partial-season sampling used, not full historical backfill |
| `leaguedashptstats` (Possessions/Drives) | `handling_exposure.py` (4B) | 2013-14+ |
| `leaguedashptstats` (Passing) | `passing_tracking_ingestion.py` (8) | 2013-14+ |
| `leaguedashptstats` (Drives extra fields/PullUpShot/CatchShoot) | `shot_creation_ingestion.py` (9) | 2013-14+ |
| `leaguedashptdefend` (Less Than 6Ft) | `rim_protection_ingestion.py` (7) | 2013-14+, full backfill, cheap |
| `leagueseasonmatchups` | `poa_containment_ingestion.py` (10) | Real reliable floor 2017-18 (2016-17 = sparse partial rollout); ENTIRE LEAGUE in one call/season |
| `draftcombineplayeranthro` | `anthropometrics_ingestion.py` (12A, in progress) | Real floor: draft class 2000 (2001 = real gap year) |
| `commonteamroster` (height/weight columns) | `anthropometrics_ingestion.py` (12A, in progress) | Same endpoint as existing `rosters.json`; re-fetched for 5 representative seasons only so far |
| `leaguedashteamshotlocations` (Opponent) | `rim_protection_ingestion.py`'s `load_team_opp_rim_frequency` (7, reused by 10) | Team-level scheme-bias check only |

## H. Provenance standard (applies everywhere)

- Raw evidence ≠ latent estimate. A `SeasonEvidence`/raw-rate object is
  never presented as the final rating.
- Source mode ≠ player confidence ≠ model/calibration status. Keep these
  three separate fields (e.g. `mode="MODERN_TRACKING"`,
  `confidence="high"`, `param_source="calibrated"`).
- A sample-size number is meaningless without its unit (drives? touches?
  minutes? FGA?) — always label it.
- Missing ≠ zero, anywhere, ever.
- A real API/source failure should fail loudly (raise / print an
  explicit failure), never silently substitute a fallback value.
- Temporal cutoff (`as_of_season`) is mandatory on every estimator call.
- Low evidence → shrink toward a real prior, or return low confidence /
  no estimate — never a fabricated precise low (or high) rating.
- No universal arbitrary confidence/sample threshold — each estimator's
  own calibration (exposure floor, λ, M) is derived from that
  estimator's own real data, not copied from a different attribute.
- Historical reconstruction uncertainty (can we even measure this era?)
  is a DIFFERENT kind of uncertainty than in-game variance (will this
  shot go in?) — don't conflate them.
- A future generative "true" player state is not the same object as a
  future team's/scout's belief about that player (not built yet, but the
  distinction is already assumed by the architecture in §B).

## I. Physical decisions (Phase 12A)

V1 scope: `height`, `standing_reach`, `wingspan`, `mass` — real units
internally (inches, lbs), never converted to a 0-99 scale internally (a
future UI layer may derive a display rating, out of scope here).
Potential later (not started, not promised): `vertical_pop`,
`workload_capacity`. **Postponed, likely for a long time**:
`first_step_burst`, `lateral_agility` — real public data is judged
insufficient and the double-count risk against existing skill estimators
(e.g. inferring "burst" from real drive frequency, which is already
`drive_aggression`'s domain, or inferring "lateral agility" from real POA
containment outcomes, which is already `poa_containment`'s domain) is
severe. **Do not infer burst from drives. Do not infer lateral agility
from POA outcomes. Do not retroactively residualize any existing skill
estimator against a physical trait without new downstream
(possession-engine-level) evidence.**

## J. Major world-system architecture (provisional, mostly NOT built)

- **Development/aging**: not built. No fixed potential ceiling is to be
  assumed when it is built.
- **Health/injury**: legacy sim has its own real injury-calendar system
  (`injuries.py`, part of the separate Codex workstream) — not the same
  system a future generative health model would use.
- **Role/team system**: not built (Phase 13 candidate — "lineup/player
  role inference").
- **Possession engine**: not built. Documented conceptual sequences exist
  per-attribute in each phase's own report (e.g. Phase 7 §"future
  possession semantics", Phase 9's rim-access/perimeter-space sequence,
  Phase 10's POA sequence) — read the specific report when building this.
- **Scouting/beliefs**: not built.
- **Draft/contracts/trades/schedule/rules/awards**: the LEGACY sim
  already has real implementations of some of these
  (`transactions.py`, `awards.py`, real schedule/injury data) — these are
  NOT part of the new ability/tendency/physical research track and
  should not be conflated with a future generative version.
- **Cross-era**: handled per-attribute so far (e.g. Phase 11's
  relative-to-contemporaneous-league-average tendency representation;
  Phase 5's investigation of the 1996-97 shortened-3PT-line era). No
  single unified cross-era system exists yet.

## K. Known rejected / forbidden ideas

- OVR (overall rating) before core attribute validation is complete —
  explicitly off-limits in every phase so far.
- Award-based OVR, MVP/DPOY/Clutch "traits" derived from awards.
- A deterministic, fixed potential ceiling for development.
- Minutes-played treated as literal "XP"/experience currency.
- EPM/DARKO/RAPM (or any impact metric) treated as ground truth for a
  skill estimator — validation only.
- Direct team/UI access to a hypothetical `PlayerTrueState` object (once
  a beliefs/scouting layer exists, it must go through that layer).
- A "Coach OVR."
- A generic chemistry/morale system (not ruled out forever, just not
  attempted).
- Hand-set 50/50 (or any invented-weight) combination of
  `rim_access_creation`/`perimeter_space_creation` into `shot_creation` —
  Phase 9 explicitly left this unwired rather than guessing.
- Fabricated precise burst/agility ratings from indirect signals.
- Silent missing→zero, anywhere.
- Per-game resampling of latent ability from measurement uncertainty
  (an `AttributeEstimate` is a stable snapshot, not resampled noise).
- Arbitrary universal confidence/sample thresholds shared across
  unrelated estimators.

## L. Current roadmap

**COMPLETE**: Phase 12A — Anthropometrics Foundation (see §F and
`docs/PHASE12A_ANTHROPOMETRICS_REPORT.md` for full results/methodology).
**Deferred as an explicit small follow-up, not a new phase**: the
diagnostic-only physical-vs-skill correlation pass, blocked on a real
player-NAME-vs-`player_id` key mismatch between existing skill
estimators and `PlayerPhysicalProfile` (report Sec. 12) — building a
crosswalk is the concrete next piece of work if this is picked up.

**NEXT (likely)**: Phase 13 — Lineup / Player Role Inference. (The
physical-vs-skill correlation follow-up above may be worth doing first
if a name↔player_id crosswalk turns out to be cheap and generally
useful — HQ's call.)

**POSSIBLE LATER (12B)**: `vertical_pop` / `workload_capacity`, only if
real evidence supports them — not promised.

**THEN (no fixed order yet)**: role/system interfaces → possession-engine
component calibration → a minimal game loop → development/world systems.

### Important unresolved empirical work (not yet scheduled to a specific phase)

- POA containment's real scheme/help contamination (Phase 10 §10) — not
  corrected, real open problem.
- A denser Ball Security corpus (Phase 4B's ingestion was partial-season
  sampled, not a full historical backfill).
- Foul Drawing's real denominator/coverage gap (only ~50-57% of
  drawing-eligible fouls have a real attributable drawer).
- Foul Discipline's real matchup-burden question (does guarding tougher
  matchups inflate a defender's foul rate independent of real
  discipline?) — not tested.
- Playmaking Vision vs. role — the residualization (Phase 8) used a
  simple 3-variable OLS; not independently re-validated by a second
  method.
- Shot Creation vs. future physical traits — explicit, documented
  double-count risk (Phase 9 §17/§18; also see §I above) once physicals
  exist.
- Tendency portability under WITHIN-team role shocks (e.g. a real
  teammate injury/absence stretch) — Phase 11 used team-switches only
  as supporting evidence, not a clean natural experiment; a cleaner,
  cheap within-team check was suggested but not built.
- Attribute aging curves — several phases tested a one-shot age
  correction and rejected it (no held-out gain); a real, dedicated aging
  study has not been attempted.
- A persistent "development propensity" test (do certain players
  reliably outperform an age-curve prediction?) — not attempted.
- Historical (pre-tracking-era) proxy bridges — several attributes have
  a real tracking-era-only mode and an explicitly lower-confidence (or
  absent) historical fallback; none of these historical proxies have
  been independently cross-validated against each other.
- A future "coach tactical fingerprint" (systematic team-level scheme
  effects) — named as a concept in several reports' contamination
  sections, never built.
- Possession sub-mechanic calibration (the actual joint-resolution math
  connecting physicals + abilities + tendencies + role into a single
  possession outcome) — conceptual sequences only exist per-attribute so
  far; no unified mechanic.

## M. Phase report index (read the specific report for full methodology — this file only summarizes)

| File | Covers |
|---|---|
| `PLAYER_ABILITY_HANDOFF.md` (repo root) | Original Phase 1-3 handoff: `PlayerAbilityProfile`/`AttributeEstimate` foundation, first six calibrated attributes. Historical reference — superseded by this file for anything after Phase 4A, but not itself rewritten. |
| `docs/PHASE4A_BALL_SECURITY_REPORT.md` | Ball Security, first pass: turnover-subtype taxonomy, initial denominator work. |
| `docs/PHASE4B_BALL_SECURITY_REPORT.md` | Ball Security, expanded corpus + final KEEP BUT FLAG classification. |
| `docs/PHASE5_SHOT_ZONE_REPORT.md` | `rim_finishing`/`midrange`/`floater_short_mid` — shot-zone sourcing, calibration, classifications. |
| `docs/PHASE6_FOUL_ATTRIBUTES_REPORT.md` | `foul_drawing`/`foul_discipline` — PBP foul taxonomy, drawer/committer attribution, and-1 handling. |
| `docs/PHASE7_RIM_PROTECTION_REPORT.md` | `rim_protection` — LOCK V1, real TRAIN/HELDOUT methodology, blocks-overlap check. |
| `docs/PHASE8_PLAYMAKING_VISION_REPORT.md` | `playmaking_vision` — potential-assist residualization, passing_accuracy overlap. |
| `docs/PHASE9_SHOT_CREATION_REPORT.md` | `rim_access_creation`/`perimeter_space_creation` — two-component architecture evidence. |
| `docs/PHASE10_POA_DEFENSE_REPORT.md` | `poa_containment` — `leagueseasonmatchups` sourcing, steals/usage separation, scheme contamination. |
| `docs/PHASE11_PLAYER_TENDENCIES_REPORT.md` | Tendency layer — all six candidates, era-relative representation, the `orb_crash` bug/redundancy finding. |
| `docs/PHASE12A_ANTHROPOMETRICS_REPORT.md` | Physical traits (height/standing_reach/wingspan/mass) — source verification (combine/roster/commonplayerinfo), identity linkage, coverage, mass-staleness diagnostic, wingspan/standing_reach inference backtest, classifications. |

Other `docs/` files present (`PHASE1_COUNTERFACTUAL_AUDIT.md`,
`phase1_evidence.json`, `screenshot_*.png`) belong to the separate Codex
counterfactual workstream or the legacy README — not part of this
ability/tendency/physical track.
