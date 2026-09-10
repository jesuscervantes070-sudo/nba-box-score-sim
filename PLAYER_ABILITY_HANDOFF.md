# Player Ability System — Handoff

This document exists because the conversation that built this work got too
large for one context window. Everything below was **verified against the
actual repository** (file contents, git status, test run, JSON values) at
the time this was written — not reconstructed from memory. If anything here
ever disagrees with the repo, trust the repo and treat this file as stale.

---

## 1. PROJECT GOAL

Long-term: an NBA / MyNBA-style simulator with **statistically grounded**
player abilities, eventually surfaced as 2K-like attributes and an overall
rating (OVR) — but built with a strict, deliberate separation between three
different concepts that this project's existing `models.Player` currently
conflates into one real per-game stat line:

- **Latent Player Ability** — stable, portable skill, estimated from
  multi-year evidence, independent of team/role/context. (`PlayerAbilityProfile`)
- **Season Performance / Context** — team, minutes, usage, role, workload,
  availability for one specific season. (planned `PlayerSeasonContext`, not built)
- **Observed Impact** (EPM/DARKO/RAPM/BPM-style metrics) — **training/validation
  targets**, never treated as a player skill directly.

## 2. CURRENT ARCHITECTURE DECISIONS

- The **legacy historical replay path is completely untouched** — `models.Player`,
  `game_engine.py`, `season.py`, `awards.py`, `transactions.py` all behave
  exactly as before this work started.
- The new `PlayerAbilityProfile` system exists **entirely in parallel**,
  offline, imported by nothing in the simulation path.
- **No ratings are wired into `game_engine.py`/`season.py`.**
- Intended eventual pipeline:
  `Raw Statistical Evidence → PlayerAbilityProfile → (future) PlayerSeasonContext → (future) PlayerSimulationProfile → existing game engine (unchanged)`
- Real observed-impact metrics are validation targets for calibration
  (see Part C/D of the calibration work), never plugged in as an ability input.
- A **separate, parallel Codex workstream** shares this same git checkout
  (branch `codex/phase1-counterfactual-safety`) working on counterfactual
  simulation architecture (`counterfactual.py`, `game_engine.py`,
  `injuries.py`, `simulation_rng.py`, `tests/`). **Do not touch those files.**
  See Part 10.

## 3. CORE ATTRIBUTE VOCABULARY (18 planned, from `player_ability_profile.py`)

**Scoring (7):** `rim_finishing`, `floater_short_mid`, `midrange`, `three_point`,
`free_throw`, `shot_creation`, `foul_drawing`
**Playmaking (3):** `passing`, `creation_for_others`, `ball_security`
**Defense (6):** `perimeter_defense`, `interior_defense`, `rim_protection`,
`defensive_playmaking`, `defensive_versatility`, `foul_discipline`
**Rebounding (2):** `offensive_rebounding`, `defensive_rebounding`

Only **6 of these 18** have an implemented, calibrated estimator so far (see
Part 5). `ball_security` has a *provisional, unvalidated* estimator. The
other 11 have no estimator at all yet.

## 4. IMPLEMENTED FILES

All of these are new, untracked files (verified via `git status`) — nothing
in this list existed before this work began.

| File | Purpose |
|---|---|
| `player_ability_profile.py` | Foundational typed representation: `AttributeEstimate` (value/confidence/sample_size, missing ≠ 0), `RoleProfile`, `PlayerAbilityProfile`. No calculation logic. |
| `test_player_ability_profile.py` | 24 unit tests for the above. |
| `player_ability_estimation.py` | The actual attribute estimator: multi-year recency-weighted + Bayesian-shrunk percentile ratings for `three_point`, `free_throw`, `passing`, `ball_security`, `offensive_rebounding`, `defensive_rebounding`, `defensive_playmaking`. Has `resolve_params()` (calibrated-or-provisional) and an opt-in `apply_age_adjustment` parameter (default `False`). |
| `test_player_ability_estimation.py` | Unit tests for the estimator (leakage, shrinkage, TRUE/FALLBACK rebound mode, etc.). |
| `player_ability_calibration.py` | Loads `player_ability_calibration.json` (v1) and `player_ability_calibration_v2.json` (age adjustments); safe no-op fallback if either file is missing/malformed. |
| `player_ability_calibration_search.py` | The real, reproducible walk-forward backtest + grid-search code that produced the calibration artifacts (not imported by the estimator at runtime — an offline tool). |
| `player_ability_calibration.json` | v1 artifact: calibrated λ (recency decay) and M (shrinkage prior strength) per attribute, plus baseline comparison numbers. **Not to be silently overwritten.** |
| `player_ability_calibration_v2.json` | v2 artifact: adds an optional, held-out-validated age adjustment on top of v1's unchanged λ/M. v1 file is untouched and still independently loadable. |
| `player_ability_turnover_prototype.py` | Read-only prototype: classifies real NBA turnover events (from `playbyplayv3` text) into `handling_error` / `excluded` / `other_unclassified` buckets. Not integrated into `ball_security`. |
| `test_player_ability_turnover_prototype.py` | Unit tests for the classifier (Bad Pass and Offensive Foul must never count as handling errors). |
| `test_player_ability_calibration.py` | Tests for calibration loading, resolve_params wiring, age-adjustment opt-in behavior, temporal-split determinism. |

**Also modified** (both purely additive, no existing function/field changed):
- `data_source.py` — added `build_and_cache_player_rebound_splits(season, force=False)` and `_player_rebound_splits_cache_path()`. Reuses the *same* `leaguedashplayerstats` (Advanced) API call `player_advanced.json` already makes; extracts two columns (`OREB_PCT`, `DREB_PCT`) that were already coming back but never saved.
- `loader.py` — added `load_player_rebound_splits(season)`. Returns `{}` if the season hasn't been backfilled (never silently substitutes the fallback).
- `cache/<season>/player_rebound_splits.json` — new cache files, **all 30 real seasons backfilled** (verified: `ls cache/*/player_rebound_splits.json | wc -l` → 30).

## 5. VALIDATED ATTRIBUTES — current status (verified against the actual JSON files)

**three_point** — λ=0.70, M=50 (3PA units). Held-out weighted MAE 0.0316 vs old-provisional 0.0340 (+6.9%). Flat optimum on λ (0.70 vs 0.85 differ by 0.0001). **Age-adjust: ON (bucket correction)**, +2.5% further held-out improvement.

**free_throw** — λ=0.55, M=25 (FTA units). Held-out MAE 0.0423 vs 0.0451 (+6.2%). **Age-adjust: ON (bucket)**, +3.3% further.

**passing** — λ=0.40, M=75 (minutes units; `ast_pct` target). Held-out MAE 0.0284 vs 0.0350 (+18.8%). **Age-adjust: ON (linear)**, +1.9% further.

**offensive_rebounding** — λ=0.40, M=50 (minutes units). Held-out MAE 0.0076 vs 0.0119 (+36.4%, largest gain of the six). **True OREB_PCT source**: `cache/<season>/player_rebound_splits.json`, all 30 seasons. **Status: LOCK V1** — age adjustment tested and made things *worse* (real, tested null result, not adopted).

**defensive_rebounding** — λ=0.55, M=75 (minutes units). Held-out MAE 0.0162 vs 0.0205 (+20.6%). **True DREB_PCT source**: same file as above. **Status: LOCK V1** — age adjustment negligible (+0.1%, noise), not adopted.

**defensive_playmaking** — λ=0.55, M=300 (minutes units; STL+BLK per-36 target). Held-out MAE 0.2972 vs 0.3198 (+7.1%). **Age-adjust: ON (bucket)**, +1.1% further. **Important finding**: STL and BLK, decomposed separately, have *different* optimal shrinkage (STL M=500, BLK M=150) and *different aging onset* (BLK's over-prediction bias starts by age 22-24; STL's doesn't show comparable bias until 26-28) — the combined attribute is masking two real, distinct aging processes. Diagnostic only; **not split** in the 18-attribute vocabulary yet.

**ball_security** — **NOT VALIDATED / PROVISIONAL.** Still uses the original box-score TOV-rate proxy (`1 - TOV/(FGA+0.44·FTA+AST+TOV)`), λ=0.6, M=1500 — never calibrated, never held-out tested. This is exactly what Phase 4A (next) addresses.

## 6. IMPORTANT EMPIRICAL FINDINGS

- The **old provisional shrinkage constants were badly over-shrinking** four of six attributes (M=1500 for passing/OREB/DREB/defensive_playmaking) — so much so that a **zero-shrinkage baseline beat the old provisional system outright** for those four. The calibrated values are far smaller (25-300).
- Calibrated models **beat old-provisional baselines on genuinely held-out data** (targets from 2016-17 through 2025-26, never used in hyperparameter selection) — nested recalibration on train-only data reproduced the exact same λ/M for 5 of 6 attributes, confirming this is real signal, not overfitting.
- **True OREB_PCT/DREB_PCT required no new data source** — both were already returned by the exact API call `player_advanced.json` already makes; `data_source.py` simply never extracted them before.
- **Age effects, real and held-out-confirmed**: three_point/free_throw/passing systematically under-predict young players (model doesn't yet know they're improving); defensive_playmaking over-predicts players 24+ (STL/BLK decline faster than the base model assumes).
- **STL vs BLK**: different optimal shrinkage, different aging onset — see Part 5. Recommendation for a future phase: represent defensive playmaking as **one combined display attribute generated from two latent components**, not a vocabulary change.

## 7. BALL SECURITY FINDINGS SO FAR (nothing implemented yet beyond the read-only prototype)

- **Current estimator's real problem**: a pure box-score TOV-rate proxy mixes passing errors, offensive fouls, and genuine handling errors into one number — conceptually wrong per the task's own definition ("preservation of the ball while handling/dribbling").
- **Real turnover-subtype text exists in `playbyplayv3`** (e.g. "Lost Ball Turnover", "Bad Pass Turnover", "Traveling Turnover", "Offensive Foul Turnover") — confirmed present across the **entire cached range, 1996-97 through present** (checked directly on both a 2023-24 and a real 1996-97 game). This is per-game data; a full ingestion needs one API call per game (~1,230 games/season × up to 30 seasons) — **not yet done**.
- **Conceptual separation established**: Bad Pass turnovers must **never** count toward ball security (belongs to Passing/Decision-Making). Offensive fouls/illegal screens must **never** count (positioning/contact, not handling). The intended numerator is Lost Ball / Traveling / Double-Dribble-type handling violations.
- `player_ability_turnover_prototype.py`'s small keyword list is **not complete** — real subtype text found beyond it during testing: `"Foul Turnover"`, `"Out Of Bounds Turnover"`, `"3 Second Violation Turnover"`, team-level `"Shot Clock"` turnovers. These currently fall into an honest `other_unclassified` bucket rather than being silently misclassified.
- **Real touch/dribble/possession tracking data** (`leaguedashptstats`, e.g. `TOUCHES`, `AVG_DRIB_PER_TOUCH`, `TIME_OF_POSS`) exists but **only from 2013-14+** (confirmed: 0 rows for 2012-13). No real handling-opportunity denominator exists before that camera-tracking floor — a real, unresolved gap for the pre-2013-14 half of this project's cached history.
- Full ingestion, a real modern (2013-14+) handling-exposure-normalized model, and a defensible pre-tracking-era proxy are **all still open** — this is exactly Phase 4A's job.

## 8. DATA AVAILABILITY (verified, not assumed)

| Data | Source | Era | Status |
|---|---|---|---|
| True OREB_PCT / DREB_PCT | `leaguedashplayerstats` (Advanced) — same call as `player_advanced.json` | 1996-97 to 2025-26, 100% coverage | Cached, all 30 seasons |
| AST% | `player_advanced.json` (`ast_pct`) | full range | Already cached |
| USG% | `player_advanced.json` (`usg_pct`) | full range | Already cached |
| Touches / dribbles / time of possession / drives | `leaguedashptstats` | **2013-14+ only** (real camera-tracking floor) | Not cached, confirmed obtainable |
| PBP turnover subtype text | `playbyplayv3` | **1996-97+** (confirmed, not gated to modern era) | Not ingested (per-game only) |
| Offensive rim-finishing shot-location | — | — | **Does not exist anywhere in this codebase's cache or fetchers.** `player_rim_defense.json`/`player_perimeter_defense.json` are both DEFENSIVE-only. `rim_finishing` is left unestimated by design. |
| Stable NBA `player_id` | `cache/<season>/transactions.json` (`fetch_player_transactions`) | **2015-16+ only**, real feed floor | Exists but **not linked** to `rosters.json`/`player_advanced.json` — no name→player_id crosswalk built. `name` remains the real, load-bearing key everywhere. Real risk: same-name-different-person collisions, inherited from the rest of the codebase, not introduced here. |

## 9. TEST STATUS

**78/78 passing**, verified just now:
```
python3 -m unittest test_player_ability_profile test_player_ability_estimation test_player_ability_turnover_prototype test_player_ability_calibration
```
`Ran 78 tests in 1.5s — OK`

## 10. FILES THAT MUST NOT BE TOUCHED WITHOUT EXPLICIT INSTRUCTION

- `game_engine.py`, `season.py`, `awards.py`, `models.py` (incl. `models.Player`), `transactions.py`
- Existing historical replay behavior (anything `season.py`/`main.py` currently do)
- Team-strength logic
- OVR calculation (does not exist yet — do not add it)
- Role classification (does not exist yet — do not add it)
- `counterfactual.py`, `simulation_rng.py`, `injuries.py`, `tests/` (Codex's parallel workstream — currently shows real diffs in `game_engine.py`/`injuries.py` that are **not from this work**)
- Do not switch git branches or worktrees in this shared checkout without explicit instruction.

## 11. NEXT PHASE — PHASE 4A: BALL SECURITY

Objectives for the next session:
1. Full, **resumable** turnover-subtype ingestion (real per-game PBP parsing — large, do incrementally, not in one giant job).
2. A real modern (2013-14+) handling-exposure denominator using the now-confirmed-available touches/dribbles data.
3. A genuine, validated Ball Security model built on Lost Ball/Traveling-type events only (never Bad Pass, never Offensive Foul).
4. A "creation burden" test — does ball security wrongly penalize high-usage shot-creators the way the old proxy did?
5. A defensible pre-2013-14 proxy for eras without tracking data (documented limitation, not invented precision).
6. Calibration (λ/M) + held-out validation, same methodology as the other six attributes.
7. **No OVR, no simulation integration** — same offline-only scope as everything so far.

## 12. GIT / WORKSPACE STATE (verified just now)

- **Repo path**: `/Users/jesuscervantes-canchola/Downloads/nba-sim-game`
- **Branch**: `codex/phase1-counterfactual-safety`
- **HEAD**: `dc6d6d0 Add MIT license`
- **Remote**: `git@github.com:jesuscervantes070-sudo/nba-box-score-sim.git`
- **Nothing committed** — every file below is either modified-uncommitted or untracked.
- Modified (pre-existing, **not from this player-ability work** — Codex's): `game_engine.py`, `injuries.py`
- Modified (**from this work**, purely additive): `data_source.py`, `loader.py`
- Untracked, **from this work**: `player_ability_profile.py`, `player_ability_estimation.py`, `player_ability_calibration.py`, `player_ability_calibration_search.py`, `player_ability_calibration.json`, `player_ability_calibration_v2.json`, `player_ability_turnover_prototype.py`, `test_player_ability_profile.py`, `test_player_ability_estimation.py`, `test_player_ability_calibration.py`, `test_player_ability_turnover_prototype.py`, `PLAYER_ABILITY_HANDOFF.md` (this file), plus `cache/*/player_rebound_splits.json` (30 files)
- Untracked, **NOT from this work** (Codex's / pre-existing): `counterfactual.py`, `simulation_rng.py`, `tests/`, `tools/`, `docs/PHASE1_COUNTERFACTUAL_AUDIT.md`, `docs/phase1_evidence.json`, `ratings.py` (a separate, earlier player-ratings effort — also not part of this PlayerAbilityProfile work)

## 13. COMMANDS

Run all player-ability tests:
```bash
cd /Users/jesuscervantes-canchola/Downloads/nba-sim-game
python3 -m unittest test_player_ability_profile test_player_ability_estimation test_player_ability_turnover_prototype test_player_ability_calibration -v
```

Inspect the calibration artifacts:
```bash
python3 -c "import json; print(json.dumps(json.load(open('player_ability_calibration.json'))['attributes'], indent=2))"
python3 -c "import json; print(json.dumps(json.load(open('player_ability_calibration_v2.json'))['attributes'], indent=2))"
```

Re-run the calibration search (slow — walks all 30 real cached seasons; do not run casually):
```bash
python3 -c "
import player_ability_calibration_search as pacs
table = pacs.build_raw_evidence_table(sorted(__import__('glob').glob('cache/????-??')))
# then pacs.build_pairs / pacs.grid_search per attribute -- see player_ability_calibration_search.py
"
```

Estimate one player's attribute directly:
```bash
python3 -c "
import glob
from player_ability_estimation import estimate_attribute
seasons = sorted(d.split('/')[-1] for d in glob.glob('cache/????-??'))
r = estimate_attribute('Stephen Curry', '2015-16', 'three_point', seasons, apply_age_adjustment=True)
print(r)
"
```

---

## START HERE IN NEW CLAUDE CHAT

> Read `PLAYER_ABILITY_HANDOFF.md` in this repository first, in full, before doing
> anything else. Then inspect the actual repository yourself (file contents,
> `git status`, run the test suite) to confirm the handoff is still accurate —
> do not assume it's current without checking.
>
> Preserve all existing validated work exactly as documented: the six
> calibrated attributes, both calibration artifact versions, and every
> existing test must keep passing. Do not modify `game_engine.py`,
> `season.py`, `awards.py`, `models.py`, `transactions.py`, or any file
> belonging to the parallel Codex counterfactual workstream
> (`counterfactual.py`, `simulation_rng.py`, `injuries.py`, `tests/`).
>
> Continue with **Phase 4A — Ball Security** only (see section 11 of the
> handoff). Do not calculate OVR, do not add role classification, do not
> integrate anything into the simulator. Stay offline and diagnostic, same
> as every prior phase.
