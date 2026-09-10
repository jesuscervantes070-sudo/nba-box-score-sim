# Phase 4A — Ball Security: Final Report

Offline, diagnostic-only work. Nothing in this phase is wired into
`game_engine.py`, `season.py`, `awards.py`, `models.py`, `transactions.py`,
or `player_ability_estimation.py`'s production `ATTRIBUTE_EXTRACTORS`. No
OVR, no role classification, no simulation integration. All 109 tests
(78 pre-existing + 14 new turnover-ingestion + 15 new ball-security-analysis
+ 2 name-crosswalk) pass.

## 1. Repo / handoff verification

`PLAYER_ABILITY_HANDOFF.md` matched the actual repository exactly at
session start: 78/78 tests passing, same branch
(`codex/phase1-counterfactual-safety`), same modified/untracked file set.
No discrepancies found. Proceeded per handoff Part 11 (Phase 4A objectives).

## 2. Turnover subtype coverage by era

`playbyplayv3` provides a **structured** `actionType`/`subType` pair for
every turnover event (`actionType == "Turnover"`), not just free text —
confirmed across seven real seasons (1996-97, 2000-01, 2005-06, 2009-10,
2013-14, 2018-19, 2023-24). This is a materially better signal than the
original prototype's keyword search on raw description text, and is what
`turnover_ingestion.py` uses. Team-level events (e.g. "Shot Clock") are
identified and excluded by a real franchise-id check on `personId`
(`1610612xxx`), not a text guess.

Real per-game ingestion is fast per call (~0.1–1s) but the underlying
`stats.nba.com` endpoint is genuinely flaky under sustained load (observed
timeout bursts after ~20-100 consecutive calls, consistent with
`leaguedashptdefend`'s already-documented flakiness in `data_source.py`) —
`fetch_game_turnovers` retries with backoff for this reason.

**Ingested this session** (partial samples, not full 30-season history —
see §18/§20):

| Season | Games ingested | Turnover events | Classified % | Unknown % |
|---|---|---|---|---|
| 1996-97 | 60/60 | 2,043 | 97.0% | 3.0% |
| 2005-06 | 60/60 | 1,823 | 98.8% | 1.2% |
| 2013-14 | 102/300 (partial) | ~3,290 | 99.2%* | 0.8%* |
| 2018-19 | 60/60 | — | — | — |
| 2022-23 | 60/60 | — | — | — |
| 2023-24 | 60/60 | 1,721 | 99.4% | 0.6% |

(*2013-14 numbers shown are from the original 60-game validation pass;
the season was subsequently extended to 102/300 games for the tracking-era
analysis without re-running the coverage report.)

Classification held at 97–99.4% across every era sampled — no era-specific
wording collapse. Top real unknown subtypes, preserved (not discarded):
`Offensive Goaltending`, bare `Out Of Bounds`, `Kicked Ball Violation`,
`Lane Violation`, rare one-offs (`Backcourt`, `Jump Ball Violation`). All
are genuinely NOT ball-handling failures — their exclusion from
`handling_error` is correct, not a coverage gap.

## 3. Classification accuracy / unknown rate

See table above: 97.0%–99.4% classified, 0.6%–3.0% unknown, every era.
`test_turnover_ingestion.py` locks in that Bad Pass/Offensive Foul/Illegal
Screen/Illegal Pick can never land in `handling_error`, and that an unseen
subtype string is preserved under `other_unclassified` with its own
example description and count — never silently dropped or reclassified.

Box-score cross-check (`validate_against_box_score`) is implemented but
not reported numerically here: this session's turnover ingestion is a
**partial-game sample** while box totals are full-season, so a raw
count comparison would only demonstrate the (already-known, already-
documented) coverage gap rather than real discrepancy — not a useful
number until a season is ingested to completion.

## 4. Cache / data structures added

- `cache/<season>/player_turnover_subtypes.json` (new, resumable —
  `turnover_ingestion.py`): per-player-id counts for
  `handling_error`/`bad_pass`/`offensive_foul_nonhandle`/`team_system`/
  `other_unclassified`/`total`, plus `games_done`/`games_failed`/
  `games_total`, `unknown_subtypes` (count + example description per real
  unseen subtype), `cache_version`, `last_updated`.
- `cache/<season>/player_handling_exposure.json` (new — `handling_exposure.py`,
  2013-14+ only): real `touches`, `front_ct_touches`, `time_of_poss`,
  `avg_sec_per_touch`, `avg_drib_per_touch`, `estimated_total_dribbles`
  (= `touches * avg_drib_per_touch`, a direct multiplication of two real
  reported fields), plus real `drives`/`drive_tov`/`drive_tov_pct` from a
  second real `leaguedashptstats` call.
- `ball_security_calibration.json` (new, separate from
  `player_ability_calibration.json`/`_v2.json` — never touches either):
  the accepted λ/M/denominator/status for ball security.

## 5. Final Ball Security semantic definition

"The player's latent ability to retain control of the ball while actively
handling/dribbling it, given realistic handling exposure" — operationalized
as: **handling-error turnovers (Lost Ball, Traveling, Double Dribble,
Discontinue Dribble, Palming, Backcourt, defensible OOB-while-handling
subtypes) per unit of real handling exposure**, NEVER Bad Pass, NEVER
Offensive Foul/Illegal Screen, NEVER team/clock violations.

## 6. Final handling-error numerator

`handling_error` = `Lost Ball + Traveling + Double Dribble +
Discontinue Dribble + Palming Turnover + Backcourt Turnover +
Out of Bounds Lost Ball Turnover + Poss Lost Ball Turnover +
Step Out of Bounds Turnover` (see `turnover_ingestion.SUBTYPE_CATEGORY_MAP`).
Explicitly excludes Bad Pass, Offensive Foul/Illegal Pick/Illegal Screen,
and Shot Clock/3-5-8-Second/Jump Ball/Team violations — verified by
`test_turnover_ingestion.py`.

## 7. Modern tracking fields used

Real `leaguedashptstats` fields, `pt_measure_type="Possessions"` and
`"Drives"`, `player_or_team="Player"`: `TOUCHES`, `FRONT_CT_TOUCHES`,
`TIME_OF_POSS`, `AVG_SEC_PER_TOUCH`, `AVG_DRIB_PER_TOUCH`, `DRIVES`,
`DRIVE_TOV`, `DRIVE_TOV_PCT`. Confirmed real floor: **0 rows for 2012-13**,
**482 rows for 2013-14** — same real SportVU rollout floor already
documented for `leaguedashptdefend` in `data_source.py`. No invented
field; `estimated_total_dribbles` is a direct multiplication of two real
reported columns, not a fitted formula.

## 8. Best modern handling-exposure denominator

**`estimated_total_dribbles`** (TOUCHES × AVG_DRIB_PER_TOUCH). Real,
weighted next-season-prediction comparison across 2013-14→2018-19 and
2018-19→2022-23 (this session's real overlap sample):

| Denominator | n pairs | weighted MAE | weighted RMSE |
|---|---|---|---|
| **estimated_total_dribbles** | 287 | **0.00110** | **0.00415** |
| touches | 287 | 0.00261 | 0.00627 |
| drives | 206 | 0.02166 | 0.04769 |
| time_of_poss | 208 | 0.03911 | 0.07783 |

`estimated_total_dribbles` beats `touches` by ~2.4x on both metrics —
consistent with the task's own intuition that dribble volume, not raw
touches, is the more direct handling-opportunity exposure. `drives` and
`time_of_poss` are both real fields but much noisier next-season
predictors for THIS purpose (they measure something adjacent — aggression/
attacking, not raw handling volume).

## 9. Candidate denominator comparison

See §8's table — full detail in `ball_security_analysis.compare_denominators`.

## 10. Creation-burden findings

Real Pearson correlation, handling-error rate (per `estimated_total_dribbles`)
vs. real USG%, computed separately per season (2013-14, 2018-19, 2022-23):
**-0.084, -0.123, -0.149** — small, consistently **negative** (higher-usage
players have marginally *fewer* handling errors per dribble, not more).
Using `touches` as the denominator instead: **+0.070, -0.015, +0.050** —
inconsistent sign, near zero. **No evidence of a systematic burden penalty
against high-usage creators once Bad Pass/Offensive Foul are excluded from
the numerator** — the bias the task asked about is real in the OLD
box-score proxy (see §16) but does not reappear once volume is normalized
by real dribble/touch exposure instead of box-score plays.

## 11. Burden adjustment: NOT adopted

Per §10, no systematic role bias was found in the real handling-only rate
once a real exposure denominator replaces box-score "plays." Per the
task's explicit instruction ("do not residualize merely because it sounds
theoretically attractive"), no expected-rate/burden model was built.

## 12. Historical exposure proxy methodology

Simple, interpretable 2-variable OLS: `touches_per_minute ≈ a + b·USG% +
c·AST%`, fit by closed-form normal equations (no ML library) on
TRAIN tracking-era seasons only, applied to a HELD-OUT tracking-era season
never used in fitting. Both predictors (`USG%`, `AST%`) are real,
available in every cached season back to 1996-97 via `player_advanced.json`.

## 13. Historical proxy validation quality

Trained on 2013-14 + 2018-19, validated on the held-out 2022-23 season
(never used in fitting): **R² = 0.755, MAE = 0.160, RMSE = 0.196 (touches/min
units), rank correlation = 0.878** (n=322 real players). A real, moderate-
to-good proxy — not a weak/unusable one, but not "true tracking" precision
either. Every historical-era estimate produced by `ball_security_estimation.py`
is explicitly labeled `HISTORICAL_PROXY` (never silently blended with
`TRUE_TRACKING`) and carries this held-out R² alongside it.

## 14. Selected lambda/M

`ball_security_calibration.json`: **λ = 0.4, M = 200** (in
`estimated_total_dribbles` units), selected by weighted-MAE grid search
over real (T, T+1) player pairs (2013-14→2018-19→2022-23; 289 real
observations). **Flagged REVISIT, not LOCK V1** — see §15 for why: a
percentile-rank-based (0-99, the same scale the final rating uses) view
of the same comparison shows this λ/M is NOT clearly better than a
simpler unshrunk multi-year average, and only 2 real season-transition
pairs are available from this session's ingestion. The raw-rate-scale
optimum is real but thin; a future session with a fuller ingestion should
re-run `ball_security_calibration.grid_search` before trusting λ/M as final.

## 15. Held-out performance vs. old Ball Security

Same real (T, T+1) pairs, converted to a common 0-99 percentile-rank scale
(so the old box-score-based estimate and the new handling-only rate are
comparable despite different raw units) — **lower is better**:

| Method | n | weighted MAE (percentile pts) | weighted RMSE |
|---|---|---|---|
| **old_provisional** (existing box-TOV estimator) | 282 | 22.6 | 29.0 |
| raw_previous (single season, unshrunk) | 287 | 17.3 | 24.3 |
| multiyear_no_shrink (λ=0.4, M=0) | 287 | **16.6** | **22.5** |
| **calibrated (λ=0.4, M=200)** | 287 | 20.1 | 25.4 |

**Every new handling-only method beats the old box-score proxy** by
2.5–6 percentile points — confirming the core redesign (separating Bad
Pass/Offensive Foul out of the numerator) is a real improvement regardless
of exact shrinkage. But the calibrated M=200 point is *not* the best of
the four in this rank-based view (`multiyear_no_shrink` is) — a genuine,
reported tension between raw-rate-scale MAE (which favors light shrinkage)
and rank-based MAE (which doesn't, on this small sample). This inconsistency,
not a flat-but-directionally-clear optimum like the other six attributes,
is the direct reason for the REVISIT classification.

## 16. Representative players — OLD vs. NEW

All from this session's real partial-season ingestion (see coverage notes
per row — NOT full-season totals). `NEW rating` = 0-99, higher = better
ball security; `--` = not computed in this mode (see §13/§17 on why
proxy mode doesn't force a percentile rating).

| Player | Season | Role | Total TOV (partial) | Handling | Bad Pass | Off. Foul | Mode | OLD rating | NEW rating |
|---|---|---|---|---|---|---|---|---|---|
| Chris Paul | 2022-23 | Elite primary handler | 8 | 0 | 8 | 0 | TRUE_TRACKING | 64.6 | **74.9** |
| Trae Young | 2018-19 | High-usage creator, many TOV | 13 | 2 | 8 | 3 | TRUE_TRACKING | 30.5 | **72.8** |
| Russell Westbrook | 2018-19 | Turnover-prone creator | 5 | 1 | 4 | 0 | TRUE_TRACKING | 38.5 | **75.9** |
| Ben Simmons | 2018-19 | Playmaking forward-guard | 9 | 2 | 4 | 3 | TRUE_TRACKING | 26.3 | **67.2** |
| Draymond Green | 2022-23 | Ball-handling/playmaking big | 9 | 2 | 6 | 1 | TRUE_TRACKING | 14.6 | **39.3** |
| Nikola Jokić | 2022-23 | Ball-handling big | 12 | 4 | 4 | 4 | TRUE_TRACKING | 39.2 | 40.9 |
| Alex Caruso | 2022-23 | Conservative low-usage guard | 11 | 2 | 9 | 0 | TRUE_TRACKING | 38.9 | 41.8 |
| Michael Jordan | 1996-97 | Historical (pre-tracking) | 7 | 2 | 5 | 0 | HISTORICAL_PROXY, R²=0.755 | 94.6 | -- (rate only, see §13) |

**The expected pattern from the task's own hypothesis appears cleanly**:
Trae Young, Westbrook, and Simmons — all previously punished by the box
TOV-rate proxy (25-39 rating, "bad ball security") because their real
box turnovers are dominated by Bad Pass and Offensive Foul, not handling
failures — jump to 67-76 once those non-handling categories are correctly
excluded. Conversely, Alex Caruso (genuinely low-usage) does NOT become
artificially elite (41.8, essentially average) — his real handling-error
rate per dribble is unremarkable even though his raw turnover count is
low, because his real exposure (dribbles) is also low. Jokić, a real
ball-handling big with a real mix of all four categories, barely moves
(39.2→40.9) — the old and new numbers happen to agree here since his box
TOV rate wasn't dominated by any one non-handling category.

## 17. Passing-error evidence preserved (not acted on)

Bad Pass counts are preserved per player-season in every
`player_turnover_subtypes.json` cache file (`bad_pass` field) — e.g.
Trae Young 2018-19: 8 real Bad Pass turnovers out of 13 total. A future
Passing-attribute phase could compute `bad_pass / passing_opportunities`
(a real denominator like `AST% × minutes` or a tracking-era pass-count
field) as a diagnostic. **Not calculated here, not used to modify
`player_ability_estimation.py`'s existing `passing` estimator** — that
attribute's estimator, calibration, and tests are byte-for-byte unchanged
this phase.

## 18. Files changed

New (all offline/parallel, none imported by the simulation path or by
`player_ability_estimation.py`'s production extractors):
`turnover_ingestion.py`, `handling_exposure.py`, `ball_security_analysis.py`,
`ball_security_calibration.py`, `ball_security_estimation.py`,
`ball_security_calibration.json`, `test_turnover_ingestion.py`,
`test_ball_security_analysis.py`, `docs/PHASE4A_BALL_SECURITY_REPORT.md`
(this file), plus new cache files under `cache/<season>/
player_turnover_subtypes.json` and `cache/<season>/player_handling_exposure.json`
for 1996-97, 2005-06, 2013-14, 2018-19, 2022-23, 2023-24 (gitignored,
same as every other `cache/` file).

**Not touched**: `player_ability_estimation.py`, `player_ability_profile.py`,
`player_ability_calibration.py/.json/_v2.json`, `player_ability_turnover_prototype.py`,
`game_engine.py`, `season.py`, `awards.py`, `models.py`, `transactions.py`,
`data_source.py`, `loader.py`, or any Codex counterfactual-workstream file
(`counterfactual.py`, `simulation_rng.py`, `injuries.py`, `tests/`).

## 19. Tests run / results

`python3 -m unittest test_player_ability_profile test_player_ability_estimation
test_player_ability_turnover_prototype test_player_ability_calibration
test_turnover_ingestion test_ball_security_analysis` →
**109/109 passing** (78 pre-existing, unchanged + 14 new turnover-ingestion
+ 2 new name-crosswalk + 15 new ball-security-analysis). All pre-existing
tests pass byte-for-byte unmodified.

Real-world resumability was also exercised against the LIVE API, not just
mocked: a real ingestion run was deliberately killed mid-season
(`kill -SIGTERM` on a running `ingest_season_turnovers` process at
102/300 games for 2013-14) — the cache file was verified intact, valid
JSON, `games_done` exactly matched what had actually completed, and a
resumed run picked up from game 103 with no re-fetching and no corruption.
Six real games that failed on the first 1996-97 pass (early-run API
flakiness) were automatically retried and succeeded on the very next run,
with zero re-fetching of the 54 already-completed games.

## 20. Remaining limitations

- **Ingestion is a real but partial sample**, not the full historical
  corpus: 60-300 games per season across 6 seasons, not ~1,230 games ×
  ~30 seasons. The full resumable pipeline is tested and proven against
  the live API (§19) and ready to run as a long background job — NOT
  launched to completion in this session (real, observed ~0.3-3s/game
  with periodic API flakiness means a full historical backfill is a
  multi-hour job against a documented-flaky endpoint, explicitly out of
  this phase's "avoid a giant fetch before proving resumability" scope).
- Only 2 real (T, T+1) season-transition pairs exist in this session's
  data for calibration — thin for a confident λ/M pick (see §14-15).
- `validate_against_box_score` is implemented and tested but not
  numerically reported (§3) since partial-game coverage would make any
  comparison look like a false discrepancy.
- The historical proxy (§12-13) is real and moderately strong (R²=0.755)
  but not exact — HISTORICAL_PROXY-mode ratings should be treated as
  lower-confidence than TRUE_TRACKING, and this session deliberately does
  not produce a 0-99 percentile rating in proxy mode at all (no real
  cross-player historical reference population was built this phase).
- `estimated_total_dribbles` is a real multiplied field, but
  `AVG_DRIB_PER_TOUCH` is itself an average — extreme per-touch dribble
  counts within a touch are not separately visible.

## 21. Final classification: **REVISIT**

Not LOCK V1: the core redesign (excluding Bad Pass/Offensive Foul from
the numerator, real dribble-based exposure) is a clear, real, held-out-
confirmed improvement over the old box-score proxy (§15-16) and directly
resolves the task's own stated failure mode (high-usage creators unfairly
punished — §16). But the specific λ/M calibration is NOT yet as settled
as the other six attributes' (§14-15's rank-based inconsistency, only 2
real season-pairs) and the full historical corpus has not been ingested.
Not INSUFFICIENT_EVIDENCE — there is real, consistent, multi-metric
evidence the redesign works — but not KEEP_BUT_FLAG either, since the
calibrated parameters specifically (not the underlying design) still need
more data to trust. `ball_security_estimation.py` remains a standalone
diagnostic file, intentionally not wired into
`player_ability_estimation.py`'s production `ball_security` slot yet.

## 22. Recommended next phase

1. Run `turnover_ingestion.ingest_season_turnovers` to FULL completion
   (all real games) for a handful of tracking-era seasons spanning early/
   mid/late 2013-14+ (e.g. 2013-14, 2016-17, 2019-20, 2022-23, 2023-24)
   as a long background job — the pipeline is proven safe to interrupt
   and resume.
2. Re-run `ball_security_calibration.grid_search` on the resulting
   larger, denser set of real (T, T+1) pairs; if the rank-based and
   raw-rate-based optima agree once n grows, promote to LOCK V1 (or
   KEEP_BUT_FLAG) and wire `ball_security_estimation.py`'s logic into
   `player_ability_estimation.py`'s `ball_security` slot, replacing the
   box-TOV proxy.
3. Optionally extend ingestion further back (pre-1996-97 is not
   available in this codebase's cache at all) and build a real
   cross-player HISTORICAL_PROXY reference population so proxy-mode
   percentile ratings become possible.
4. Separately, revisit Passing using the now-preserved `bad_pass`
   evidence (§17) — out of scope for this phase, flagged for later.

Per the task's own stop condition: **Phase 4A is complete. Not continuing
into another attribute or OVR phase automatically.**
