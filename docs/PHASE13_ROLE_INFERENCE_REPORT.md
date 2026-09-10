# Phase 13 — Lineup / Player Role Inference

Scope: determine whether useful ROLE (team/lineup-assigned responsibility)
signal can be separated from ABILITY and TENDENCY. Modern-era (2013-14+
tracking floor) only. No archetypes, no softmax, no forced compositional
constraint, no gameplay/possession-engine integration.

## 1. Repository changes

| File | Purpose |
|---|---|
| `role_off_ingestion.py` | Season-level role evidence fetch/cache (Possessions/Drives/Passing/Scoring, real `player_id`-keyed), plus real-date-window helpers (`find_significant_absence_window`, `find_real_trade_date`) for the natural experiments |
| `role_off_analysis.py` | Candidate definitions, year-to-year persistence, heldout prediction, the three experiment functions, and `defensive_deployment_axis` |
| `role_off_profile.py` | `RoleObservation` + `PlayerRoleProfile` — minimal, non-compositional, non-archetypal role-state object, keyed by real `player_id` |
| `role_off_estimation.py` | `build_role_profile(player_id, as_of_season)` — source hierarchy (single tier: `MEASURED_TRACKING`) + strict temporal cutoff at the real 2013-14 tracking floor |
| `test_role_off.py` | 20 tests |

`PlayerAbilityProfile`, `RoleProfile` (the existing fixed-archetype
structure), and `player_tendencies_*.py` were not imported, read, or
modified. `RoleProfile`'s `ROLE_ARCHETYPES` was deliberately NOT reused —
it encodes discrete, named archetypes (`primary_offensive_engine`, etc.),
a different concept from this phase's continuous, independently-scored
deployment dimensions; forcing the new candidates into that shape would
have meant inventing an archetype-membership mapping with no empirical
basis. `PlayerRoleProfile` is a new, parallel, minimal object instead —
same posture Phase 12A took with `PlayerPhysicalProfile` alongside the
existing (differently-shaped) `RoleProfile`.

## 2. Real sources verified

| Source | Fields used | Real floor | Notes |
|---|---|---|---|
| `leaguedashptstats` (Possessions/Drives/Passing) | `TOUCHES`, `TIME_OF_POSS`, `DRIVES`, `DRIVE_AST_PCT`, `POTENTIAL_AST` | 2013-14 | Already-proven source (Phases 8/9); real `PLAYER_ID` column used directly this phase (previous phases in this repo used player NAME as the join key — this is a real, confirmed improvement, not an assumption) |
| `leaguedashplayerstats` (Scoring measure) | `PCT_AST_2PM/3PM/FGM`, `PCT_UAST_2PM/3PM/FGM` | Same tracking floor | **Not previously used anywhere in this repo** — verified directly this phase; real, confirmed fields, real `PLAYER_ID` present |
| `date_from_nullable`/`date_to_nullable` on both endpoints above | — | — | **Not previously exploited in this repo** — confirmed directly this phase to correctly restrict a season-total query to a real calendar window, enabling within-season natural experiments without any new endpoint |
| `leagueseasonmatchups` (already fetched, Phase 10) + `leaguedashplayershotlocations` (already cached, Phase 5) | `OFF_PLAYER_ID`, `DEF_PLAYER_ID`, `MATCHUP_MIN` (real, confirmed `"MM:SS"` STRING format — a new parsing quirk found this phase, not previously needed since Phase 10 only used `MATCHUP_TIME_SEC`, already numeric) joined against real zone-share data | 2017-18 (matchups) | **Zero new API calls** — full reuse of existing infra for the defensive candidate |
| `data_source._fetch_normalized_game_log` (already built) | `GAME_DATE`, `TEAM_NAME`, `PLAYER_NAME`, `GAME_ID` | — | Reused as-is to find real contiguous absence windows by calendar date |
| `cache/<season>/transactions.json` (already cached, `data_source.build_and_cache_transactions`) | real dated `Trade` records | 2015-16+ | Reused as-is to find a real mid-season trade date automatically (no hand-typed date) |

No API failure was silently treated as missing data — `fetch_pt_stats`/
`fetch_scoring_stats` both raise after retries exhausted, same convention
as every other ingestion module in this project.

## 3. Candidate definitions

All four are RAW, unresidualized rates/shares — no usage/touches/
opportunity control was applied to any of them. This is the concrete
mechanism enforcing role≠ability in this phase (see architectural note
in `role_off_analysis.py`'s module docstring): `playmaking_vision`
(Phase 8) took the same underlying `POTENTIAL_AST` signal and
residualized it against usage/drives/time-of-possession specifically to
isolate SKILL. `role_off_initiation` is deliberately that same raw,
un-residualized rate — ability is what's left after removing
role/opportunity; role is the opportunity itself.

- **`role_off_initiation`** = `POTENTIAL_AST` per 36 real minutes (season).
- **`role_off_finishing`** = `PCT_AST_FGM` (share of made FGs that were
  assisted — high = fed shots by others / finisher; low = self-creates
  most makes).
- **`role_off_spacing`** = `PCT_AST_3PM` (share of made 3PM that were
  assisted — a context signal, tested against `three_point_preference`
  in Sec. 8).
- **`role_def_perimeter_interior`** = matchup-time-weighted average of
  each defender's real opponents' own shot-zone profile (`three_share −
  rim_share`), continuous, signed.
- A fifth, secondary/diagnostic-only candidate, `DRIVE_AST_PCT` (share
  of a player's own drives ending in a teammate assist), was used
  alongside `role_off_initiation` in the team-switch experiment (Sec. 7)
  but is not one of the 4 primary V1 candidates.

None are forced to sum to 1 or to any fixed relation to each other —
verified by a direct test (`test_no_compositional_constraint`).

## 4. Modern coverage

`role_off_ingestion.build_and_cache_role_off` returned 572 real players
for 2023-24 and 539 for 2022-23 (season aggregate, `MIN`-floor-gated at
500 minutes for the candidate functions, not at ingestion time — every
player with any real tracking row is cached, low-exposure filtering
happens only in the candidate functions, consistent with "missing ≠
zero, low exposure ≠ zero" elsewhere in this project).
`defensive_deployment_axis` returned defenders with ≥200 real
matchup-minutes (354 players for 2023-24). No historical (pre-2013-14)
role reconstruction was attempted — `build_role_profile` returns all
four dimensions as `UNAVAILABLE` before the tracking floor, verified by
`test_pre_tracking_floor_returns_unavailable_not_zero`.

## 5. Teammate-absence results

Real natural experiment: Tyrese Maxey (Philadelphia 76ers, 2023-24) with
Joel Embiid present (42 real games, 10/26/2023–1/30/2024) vs. Embiid's
real, contiguous 29-game absence (2/1/2024–3/31/2024, a real knee-surgery
absence — the longest real missed stretch found via
`find_significant_absence_window`, confirmed directly rather than
hand-picked from memory).

| Rate (per minute unless noted) | Embiid present | Embiid absent | Change |
|---|---|---|---|
| Touches/min | 2.332 | 2.312 | ≈flat (−0.9%) |
| Time-of-possession/min | 0.194 | 0.196 | ≈flat |
| Drives/min | 0.334 | 0.399 | **+19.5%** |
| Potential-ast/min | 0.302 | 0.286 | −5.2% |
| `PCT_UAST_FGM` (self-created makes share) | 0.532 | **0.642** | **+20.7%** |
| `PCT_AST_3PM` (assisted-3 share) | 0.659 | 0.522 | −20.8% |

**Not treated as perfectly exogenous** — the 76ers' own real record
collapsed alongside this absence (29-13 → 9-14 in the two windows), a
real confound (opponent game-planning, rotation/style changes, and
possibly reduced overall roster quality around Maxey all changed at the
same time as Embiid's absence, not held fixed). **Finding**: touch/TOP
*volume* barely moved (Maxey was already a high-usage starter, likely
near a real ceiling), but *finishing-vs-creating composition* shifted
substantially — more drives, a much higher self-created-make share, and
a lower assisted-3 share. This is real evidence that responsibility
*composition*, not raw volume, is what redistributes — and that a
volume-only role signal (touches, TOP) would have missed it entirely.

## 6. Staggered-lineup-context results

**Not built as true simultaneous on-court lineup segmentation this
phase — a real, explicit scope decision, not an oversight.** The public
API has no documented endpoint giving one individual teammate's stats
conditioned on a second specific teammate's on/off-court status within
the same games (`teamplayeronoffdetails` was verified directly and
confirmed to return only TEAM-aggregate stats split by one player's
on/off status, not per-teammate individual splits — see the row-level
check in this session's exploration, not included as a separate cache).
Reconstructing true on-court lineup segments would require parsing
`playbyplayv3` substitution events to track the 10-man on-court state
continuously — feasible in principle (this repo already parses
`playbyplayv3` for turnover/foul subtypes) but a materially larger new
parsing task, not justified as a fast first pass.

**Chosen proxy, used instead**: the same real multi-game absence-window
infrastructure from Sec. 5, reframed as "lineups without the major
initiator" vs. "lineups with him" at the *game* level rather than the
*possession* level. This directly satisfies the phase's own "avoid
estimating a separate parameter for every unique 5-man lineup"
instruction (it estimates none), at the real cost of not capturing
within-game bench-unit staggering. The Sec. 5 and Sec. 7 results are
this phase's real staggered-context evidence; no separate result set
exists beyond them. **This is flagged as the single largest scope gap in
this phase** (see Sec. 14).

## 7. Team-switch results

Real natural experiment: Pascal Siakam, traded Toronto Raptors → Indiana
Pacers on 2024-01-17 (confirmed via `find_real_trade_date` against the
already-cached real transaction log, not a hand-typed date). Window:
10/25/2023–1/16/2024 (Toronto) vs. 1/19/2024–4/14/2024 (Indiana, 2-day
onboarding buffer excluded).

| Rate | Toronto (pre) | Indiana (post) | Change |
|---|---|---|---|
| Touches/min | 1.795 | 1.713 | −4.6% |
| Time-of-possession/min | 0.0785 | 0.0784 | ≈flat |
| Drives/min | 0.338 | 0.357 | +5.5% |
| `DRIVE_AST_PCT` | 0.118 | 0.065 | **−44.9% relative** |
| Potential-ast/min | 0.2444 | 0.2071 | **−15.3%** |
| `PCT_UAST_FGM` | 0.406 | 0.461 | +13.5% |
| `PCT_AST_3PM` | 0.978 | 0.974 | ≈flat (already near-ceiling both places) |

**Used strictly as a portability/system diagnostic, not as proof of
constant ability** (explicitly, per instruction) — a genuinely different
team context, offensive system, and teammate (Tyrese Haliburton, an
established primary initiator) are exactly the real confound, not
something held fixed. **Finding**: real, coherent, and directionally
consistent with the public narrative around this trade — Siakam's
creation-for-teammates responsibility (potential-ast rate, drive-assist
rate) genuinely dropped after the move, while his own shot-creation-of-
his-own-makes share rose and drive rate/efficiency held or rose. This is
a second, independent real example (distinct data-generating mechanism
from Sec. 5's injury-driven absence) of `role_off_initiation`-type and
`role_off_finishing`-type signals moving in economically sensible,
opposite directions for the same underlying "less-primary-option-now"
context shift.

## 8. Role vs. tendency diagnostics

| Comparison | n | Pearson r | Spearman ρ |
|---|---|---|---|
| `role_off_spacing` vs. `three_point_preference` | 382 | 0.420 | **0.112** |
| `role_off_initiation` vs. `drive_aggression` | 352 | 0.571 | 0.604 |
| `role_off_initiation` vs. `pass_vs_shoot` | 352 | 0.238 | 0.233 |

**Critical comparison result** (explicitly required, "be skeptical"):
`role_off_spacing` vs. `three_point_preference` shows a moderate linear
correlation (r=0.42) but a genuinely LOW rank correlation (ρ=0.112,
2023-24, real data) — meaning the two measures order players quite
differently. **They are not the same signal; role_off_spacing was kept
as a separate candidate on this real evidence, not by default.**
`role_off_initiation` correlates moderately with `drive_aggression`
(both real, distinct, but related — an initiator is more likely to also
be a frequent driver, unsurprising and not disqualifying) and only
weakly with `pass_vs_shoot` (a real, meaningfully different concept:
preference given a touch, not assigned creation load).

## 9. Role vs. ability diagnostics

| Comparison | n | Pearson r |
|---|---|---|
| `role_off_initiation` (raw potential-ast/36) vs. `playmaking_vision`'s own residualized score | 392 | 0.577 |
| `role_def_perimeter_interior` vs. `height` (Phase 12A, real `player_id` join) | 354 | **−0.746** |

`role_off_initiation` retains real, substantial (but not near-total)
overlap with `playmaking_vision` even after that estimator's own
usage/TOP/drives residualization — expected and not disqualifying:
`playmaking_vision`'s residualization targets specific confounds, not
all context, so some structural relationship between the raw
opportunity signal and the ability-after-removing-opportunity signal is
expected by construction. `role_def_perimeter_interior` correlates very
strongly (−0.746) with real player height — this is the most important,
least favorable finding in this phase: **most of the variance in this
defensive axis is explained by a player's own physical size**, which
this candidate was explicitly supposed to be kept separate from. See
Sec. 12 for the direct consequence on its classification.

## 10. Player vs. lineup vs. team/system findings

- The two natural experiments (Secs. 5, 7) show real within-PLAYER
  variation across context — the central positive finding of this
  phase: the same player's deployment composition (not raw volume)
  measurably shifts with real changes in teammate presence or team.
- No true lineup-level (5-man, on/off simultaneous) decomposition was
  attempted (Sec. 6) — this phase cannot distinguish "team/system
  redesigned this player's role" from "this specific 5-man unit's
  spacing/spacing needs changed" within a game.
- The defensive axis (Sec. 9) is dominated by a player-level, largely
  time-invariant factor (height) rather than a genuinely
  team/system-assigned signal — real evidence this specific candidate is
  closer to "a consequence of who you are" than "a consequence of what
  your team asks of you," which is the opposite of what a role signal
  should be.

## 11. Heldout results with baselines

Simple "last year's value" vs. "league mean" baseline, real player-level
heldout (2022-23 → 2023-24, matched by real `player_id`, n=293):

| Candidate | League-mean-baseline MAE | Last-year-value MAE | Improvement | Year-to-year rank corr (ρ) |
|---|---|---|---|---|
| `role_off_initiation` | 2.959 | **1.153** | 1.806 | 0.892 |
| `role_off_finishing` | 0.134 | **0.067** | 0.067 | 0.865 |
| `role_off_spacing` | 0.173 | **0.073** | 0.100 | 0.710 |

All three candidates beat the naive mean baseline by a wide, real margin
and show real, decisive year-to-year persistence (ρ 0.71–0.89) —
substantially higher than typical latent-ability persistence numbers
seen in earlier phases of this project (e.g. Phase 10's `poa_containment`
at 0.22–0.37), consistent with role being a real, somewhat sticky
team-assignment property, not day-to-day noise. `role_off_spacing`'s
persistence is real but the lowest of the three — consistent with it
being the most volatile/context-sensitive of the three offensive
candidates (matches its lower year-to-year ρ and its Sec. 8 divergence
from the more-stable `three_point_preference`).

## 12. Defensive-role feasibility

**Feasible to compute, real, decisive signal exists (Rudy Gobert
−0.236, Bam Adebayo −0.188, Draymond Green −0.117 = interior-deployed;
Herbert Jones +0.120, Alex Caruso +0.122, Jrue Holiday +0.092 =
perimeter-deployed; extremes: Mark Williams/Jericho Sims/Goga Bitadze
most interior, Sam Merrill/Jacob Gilyard/Seth Curry most perimeter — all
real, all plausible on basketball knowledge) — BUT it is dominated by
physical size** (Sec. 9: r=−0.746 vs. height). Per explicit instruction
that this candidate is allowed to fail: **this phase concludes the
public-data version of this signal, as built, does not cleanly separate
"team assigns you perimeter/interior responsibility" from "you are the
size you are and get assigned matchups accordingly."** It is real and
reproducible, but its INCREMENTAL information beyond physical size is
modest and not separately quantified this phase (would need a
height-residualized version, not built — see Sec. 16).

## 13. Tests

`python3 -m unittest discover -p "test_*.py"` → **Ran 315 tests — OK**
(295 carried over + 20 new in `test_role_off.py`). Covered: stable
`player_id` used as the profile key, candidate math (including
low-exposure → `None`, missing field → `None`, not a renamed usage
metric), no compositional/sum-to-1 constraint, `RoleObservation`
evidence-mode invariants, round-trip serialization, real temporal
cutoff (pre-2013-14 → `UNAVAILABLE`), missing player → `UNAVAILABLE`
(not zero), the defensive axis's `"MM:SS"` minutes parser, **two
explicit leakage tests** (year-to-year persistence only pairs a player
with themselves and drops any player missing from either season; the
heldout baseline's mean is computed from TRAIN values only, verified
against a case where the heldout season's values are deliberately
extreme and would visibly change the baseline if leaked in), and role↔
ability / role↔tendency module-interface separation (no import of
`player_ability_estimation`, no `ability`/`tendency`-named symbol in
`role_off_estimation`'s namespace).

## 14. Limitations / risks

- **No true simultaneous on-court lineup decomposition** (Sec. 6) — the
  single largest scope gap. The multi-game absence-window proxy cannot
  see within-game bench-unit staggering.
- Both natural experiments (Secs. 5, 7) are n=1 worked examples, not a
  systematic sweep across many teammate-absence/trade cases — real,
  concrete, and internally consistent, but not a distributional claim
  about how often/how much redistribution happens league-wide.
- Neither natural experiment is exogenous (explicitly acknowledged) —
  team record, opponent quality, and coaching adjustments move alongside
  the treatment in both cases.
- `role_def_perimeter_interior` is heavily confounded with physical
  size (Sec. 9/12) — its standalone value as a "team system" signal is
  doubtful without a height-adjusted version.
- Season-level candidates use a single, real `MIN`-based exposure floor
  (500 minutes) for all three offensive dimensions rather than a
  per-candidate-tuned floor (unlike Phase 11's tendency layer, which
  explicitly tuned floors per candidate) — a real, simplifying
  shortcut for this first pass, not re-derived per candidate this phase.
- `role_off_initiation`'s real, substantial residual overlap with
  `playmaking_vision` (r=0.577, Sec. 9) means the two are related enough
  that a downstream user must not treat them as fully independent inputs
  without further thought.
- Historical (pre-2013-14) role is entirely `UNAVAILABLE` — no proxy
  exists yet, by design this phase.

## 15. Classification recommendation

| Candidate | Classification | Basis |
|---|---|---|
| `role_off_initiation` | **KEEP** | Real, decisive persistence (ρ=0.89) and heldout improvement; real, coherent movement in both natural experiments; genuinely distinct from (not redundant with) `drive_aggression`/`pass_vs_shoot`; moderate, expected, non-disqualifying overlap with `playmaking_vision`. |
| `role_off_finishing` | **KEEP** | Real, strong persistence (ρ=0.87); real, coherent, opposite-direction movement to initiation in both natural experiments (a genuine complementary signal, not a duplicate); not simply a renamed usage metric (built from assisted-share, not FGA volume). |
| `role_off_spacing` | **KEEP BUT FLAG** | Real, decisive evidence it is NOT redundant with `three_point_preference` (Sec. 8, the phase's own required skepticism test passed); but the lowest year-to-year persistence of the three offensive candidates (ρ=0.71) and not directly exercised by either natural experiment this phase (Maxey/Siakam's `PCT_AST_3PM` was closer to a side-observation than a primary tracked outcome) — flagged for a dedicated natural-experiment check before full confidence. |
| `role_def_perimeter_interior` | **REVISIT** | Real, reproducible, basketball-plausible signal exists, but Sec. 9/12's finding that it is dominated by physical size (r=−0.746 with height) means it does not yet cleanly satisfy "keep separate from... physical size" — a height-residualized version should be tried before this is trusted as a genuine team/system deployment signal rather than a restatement of "how tall is this player." |

## 16. Immediate next recommended step

Build a **height-residualized version of `role_def_perimeter_interior`**
(regress the raw axis on real height, TRAIN/HELDOUT, keep the residual)
as a small, targeted follow-up — this directly addresses Sec. 12's one
concrete, disqualifying-shaped finding and is cheap (reuses this phase's
own cached `defensive_deployment_axis` output and Phase 12A's already-
built height estimates, real `player_id` join already proven in Sec. 9,
no new API calls). A true lineup-level (on-court simultaneous) rebuild
of the staggered-lineup experiment (Sec. 6) via `playbyplayv3`
substitution parsing is the larger, real next-tier option but is a
materially bigger task and not recommended as the immediate next step.
**Do not begin the next phase without approval.**
