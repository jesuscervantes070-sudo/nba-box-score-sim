# Detailed Engine — First 100-Game League-Level Benchmark

**FIRST BENCHMARK COMPLETE.** This is a MEASUREMENT, not a tuning pass.
No probability, timing value, selection weight, player attribute, or
resolver was changed to produce or improve any number in this document.
Poor results are recorded as-is.

## 1. Benchmark purpose

This benchmark answers exactly one question: **how far is the current
detailed possession engine from real 2024-25 NBA basketball, at a
league-average, macro-statistical level?** It does NOT ask "how can we
make the current simulator look accurate," and no result below was used
to adjust anything in the engine. This is the first historical accuracy
baseline for the detailed-engine research track — future calibration
work will be measured against the numbers in this document, not the
other way around.

## 2. Commit / config tested

- Repository: `nba-sim-game`, branch `codex/empirical-player-modeling`.
- Timing calibration checkpoint tested: `4602cf3` ("Calibrate detailed
  engine possession timing") — the FIRST-PASS MACRO CALIBRATION
  checkpoint (see the "First-Pass Timing Calibration" section above in
  this same report file). Timing values used, UNCHANGED throughout this
  benchmark:
  - `ordinary_entry_seconds = 9.0`
  - `transition_entry_seconds = 1.5`
  - `second_chance_reset_seconds = 1.0`
  - `inter_action_seconds = 3.0`
- `DetailedGameConfig()` defaults otherwise (4 regulation periods, 720s
  each, up to 5 overtimes if needed).
- Players/profiles: `PlayerSimulationProfile.synthetic(...)` — the SAME
  deterministic, clearly-synthetic V0 profile every existing diagnostic
  in this repository already uses. See Section 3.
- Seeds: **25000–25099 inclusive (100 games)**, run in seed order, no
  subsetting, no re-runs, no seed selection for favorable outcomes.
- Benchmark instrumentation added this task: `detailed_engine_benchmark.py`
  (new module) + `test_detailed_engine_benchmark.py` (34 new focused
  tests) + a diagnostic-only shot-clock-bin schema update in
  `detailed_engine_diagnostics.py` (Section 9). Confirmed
  bit-for-bit non-interfering with simulation outcomes (see Section 2's
  own test: `test_benchmark_instrumentation_does_not_change_simulation_outcome_for_a_fixed_seed`).

## 3. Synthetic-profile warning

**Every player in this benchmark uses the SAME synthetic profile shape**
(`PlayerSimulationProfile.synthetic`, with only `player_id`/`team_id`
varying) — a deterministic, explicitly-labeled, real-world-plausible
PLACEHOLDER profile, not empirically derived from any real player. This
benchmark measures the ENGINE's structural/mechanical behavior under a
fixed, homogeneous, average-ish skill population — it is NOT a
prediction of what the engine would produce with real, heterogeneous,
2024-25 NBA rosters (that requires the real-player ingestion adapter
this project has explicitly deferred — see `possession_orchestrator.py`'s
own construction contract). A real-roster benchmark is future work, out
of this task's scope.

## 4. Accounting-authority warning

The detailed engine's event schema (`engine.log.events`) is not yet
fully authoritative for every box-score field. Every metric below is
tagged with one of three authority levels, re-verified against the
CURRENT repository state (`detailed_engine_benchmark.py`'s own
`ACCOUNTING_AUTHORITY` table, itself re-derived from
`possession_orchestrator.py`'s `derive_stat_deltas_from_events`
docstring — never copied blindly from a prior phase):

| Level | Meaning |
|---|---|
| **EVENT-AUTHORITATIVE** | independently reconstructable from the structured event stream alone (`OREB`, `DREB`, `TOV`, `STL`, `BLK`, `PF`, `true possessions`) |
| **PROVISIONAL STATDELTA** | only available from `StatDeltas`, a convenience projection mutated directly by dispatch code — NOT yet independently cross-checkable against the event stream (`PTS`, `FGA`, `FGM`, `3PA`, `3PM`, `FTA`, `FTM`, and every ratio built from them: `FG%`, `3P%`, `FT%`, `FTA/FGA`, `3PAr`) |
| **PARTIALLY EVENT-DERIVED** | mixes EVENT-AUTHORITATIVE and PROVISIONAL inputs (`ORtg` — possessions are event-authoritative, PTS is not; box-score-ESTIMATED possessions — FGA/FTA are provisional, OREB/TOV are event-authoritative) |

`AST` (assists) is **NOT YET MODELED** anywhere in this engine —
`StatDeltas` has no assist field at all. It is reported as
`NOT_YET_MODELED`, never a fabricated zero.

## 5. Canonical 2024-25 NBA target table (verified, supplied, not from memory)

| Metric | Value |
|---|---|
| Pace | 98.8 |
| ORtg | 114.5 |
| PTS | 113.8 |
| FGM | 41.7 |
| FGA | 89.2 |
| 3PM | 13.5 |
| 3PA | 37.6 |
| FTM | 16.9 |
| FTA | 21.7 |
| OREB | 11.1 |
| DREB | 33.0 |
| AST | 26.5 |
| STL | 8.2 |
| BLK | 4.9 |
| TOV | 14.3 |
| PF | 18.6 |
| FG% | .467 |
| 3P% | .360 |
| FT% | .780 |
| TOV% | 12.6% |
| OREB% | 25.2% |
| FT/FGA (BRef, = FTM/FGA) | .189 |

Derived for this benchmark, NOT copied from a source directly:
- **FTA/FGA** = 21.7 / 89.2 ≈ **0.243** (NOTE: distinct from BRef's own
  FT/FGA = FTM/FGA = .189 — never conflated in this report).
- **3PAr** = 37.6 / 89.2 ≈ **0.422**.
- **DERIVED MACRO TARGET**: 98.8 × 2 = 197.6 alternating team
  possessions/game; 2880s / 197.6 ≈ **14.57s/alternating possession**.
  Labeled `DERIVED MACRO TARGET` throughout — NOT a directly measured
  possession-duration statistic.

## 6. 100-game structural results

- **Games completed: 100/100. Invariant faults: 0.**
- Regulation vs. OT: **100/100 REGULATION_FINAL, 0 OT games.**
- **TRUE (internal, structural) alternating possessions/game: mean
  223.98** (SD 11.16, P10 210, P25 217, median 224, P75 231, P90 238,
  min 198, max 253, n=100 games).
  - vs. the 197.6 DERIVED MACRO TARGET: **pace error +13.35%** —
    consistent with, and expected from, the First-Pass Timing
    Calibration's own independent 50-game validation (+13.1%). No
    material drift.
- **Box-score ESTIMATED possessions** (FGA + 0.44·FTA − OREB + TOV, per
  team-game, mean **105.19** ⇒ ≈210.4/game) is reported SEPARATELY —
  see Section 4's PARTIALLY EVENT-DERIVED note. It differs from the TRUE
  possession count (111.99/team-game ⇒ 223.98/game) because the
  ESTIMATE formula's own inputs (FGA/FTA/OREB/TOV) are themselves
  already distorted relative to real NBA proportions (Section 8) — the
  gap between TRUE and ESTIMATED possessions is itself informative, not
  noise: it is smaller than real NBA's own TRUE-vs-estimate gap would
  be, largely because this engine's inflated OREB count pulls the
  estimate DOWN while inflated FGA pulls it UP, partially cancelling.
  **The headline pace comparison in this report uses TRUE simulator
  possessions, not the estimate**, per this task's own instruction —
  the detailed engine directly models possession boundaries and the
  estimate formula is not needed to infer them.
- Mean possession duration: **12.89s**; mean of per-game median
  possession durations: **11.13s**.
- Mean actions/possession: **2.87**.
- Terminal outcome mix (100 games, all possessions pooled, 22,398
  possessions total):

| Terminal reason | Count | Share |
|---|---|---|
| `MADE_FG` | 8,125 | 36.3% |
| `DEFENSIVE_REBOUND` | 7,735 | 34.5% |
| `TURNOVER` | 4,846 | 21.6% |
| `SHOT_CLOCK_VIOLATION` | 1,121 | 5.0% |
| `PERIOD_END` | 376 | 1.7% |
| `FINAL_FT_MADE` | 195 | 0.9% |

- Turnover frequency: **24.23 TOV/team-game** (mean); **0.216
  TOV/true-possession**.
- **OREB% (event-authoritative): 47.6%.**
- **FTA/FGA (provisional): 0.039.**
- **3PAr (provisional): 0.928.**
- **PF frequency: 2.07/team-game (event-authoritative).**

## 7. Simulator distribution table (SIMULATOR percentiles — NOT real NBA percentiles, which remain `NOT YET INGESTED`)

All rows are per-TEAM-GAME (n=200 team-games, 2 per simulated game),
except `possessions`, which is also shown per-team-game here (true
possessions where that team was on offense) for percentile consistency
with the other rows — the GAME-level (both teams) figure is in Section 6.

| Metric | Mean | SD | P10 | P25 | Median | P75 | P90 | Min | Max |
|---|---|---|---|---|---|---|---|---|---|
| Possessions (true, per team) | 111.99 | 5.60 | 105 | 108 | 112 | 116 | 119 | 99 | 127 |
| PTS | 121.03 | 14.23 | 103 | 111 | 120 | 133 | 140 | 86 | 158 |
| FGA | 114.24 | 8.19 | 104 | 109 | 114 | 119 | 126 | 90 | 140 |
| 3PA | 106.03 | 8.46 | 96 | 101 | 106 | 110 | 117 | 84 | 133 |
| FTA | 4.39 | 3.30 | 1 | 2 | 4 | 6 | 9 | 0 | 14 |
| TOV | 24.23 | 5.10 | 18 | 21 | 24 | 27 | 31 | 14 | 45 |
| OREB | 35.20 | 6.41 | 28 | 31 | 34.5 | 39 | 43 | 20 | 55 |
| DREB | 38.68 | 6.01 | 31 | 35 | 39 | 43 | 47 | 21 | 55 |
| PF | 2.07 | 1.41 | 1 | 1 | 2 | 3 | 4 | 0 | 8 |
| ORtg (true poss) | 108.26 | 13.12 | 91.74 | 98.35 | 108.18 | 118.10 | 125.83 | 70.87 | 139.42 |

## 8. NBA mean-vs-sim mean table (ranked by ABSOLUTE relative error)

Relative error = (sim − real) / real × 100, **sign preserved** (positive
= simulator HIGH, negative = simulator LOW).

| Metric | Real 24-25 | Sim mean | Abs. Error | Rel. Error | Authority |
|---|---|---|---|---|---|
| OREB | 11.1 | 35.20 | +24.10 | **+217.1%** | EVENT-AUTHORITATIVE |
| 3PA | 37.6 | 106.03 | +68.43 | **+182.0%** | PROVISIONAL STATDELTA |
| 3PM | 13.5 | 36.35 | +22.85 | **+169.3%** | PROVISIONAL STATDELTA |
| 3PAr | 0.422 | 0.928 | +0.506 | **+119.9%** | PROVISIONAL STATDELTA |
| BLK | 4.9 | 0.52 | −4.38 | **−89.4%** | EVENT-AUTHORITATIVE |
| PF | 18.6 | 2.07 | −16.54 | **−88.9%** | EVENT-AUTHORITATIVE |
| OREB% | 0.252 | 0.476 | +0.224 | **+88.8%** | EVENT-AUTHORITATIVE |
| FTA/FGA | 0.243 | 0.039 | −0.204 | **−84.1%** | PROVISIONAL STATDELTA |
| FTA | 21.7 | 4.39 | −17.31 | **−79.8%** | PROVISIONAL STATDELTA |
| FTM | 16.9 | 3.44 | −13.47 | **−79.7%** | PROVISIONAL STATDELTA |
| TOV | 14.3 | 24.23 | +9.93 | **+69.4%** | EVENT-AUTHORITATIVE |
| STL | 8.2 | 11.67 | +3.47 | **+42.3%** | EVENT-AUTHORITATIVE |
| FGA | 89.2 | 114.24 | +25.04 | **+28.1%** | PROVISIONAL STATDELTA |
| FG% | 0.467 | 0.357 | −0.110 | **−23.6%** | PROVISIONAL STATDELTA |
| DREB | 33.0 | 38.68 | +5.68 | **+17.2%** | EVENT-AUTHORITATIVE |
| Possessions (true, game) | 197.6 | 223.98 | +26.38 | **+13.4%** | EVENT-AUTHORITATIVE |
| PTS | 113.8 | 121.03 | +7.24 | **+6.4%** | PROVISIONAL STATDELTA |
| ORtg (true poss) | 114.5 | 108.26 | −6.24 | **−5.4%** | PARTIALLY EVENT-DERIVED |
| 3P% | 0.360 | 0.344 | −0.016 | **−4.5%** | PROVISIONAL STATDELTA |
| FGM | 41.7 | 40.63 | −1.08 | **−2.6%** | PROVISIONAL STATDELTA |
| FT% | 0.780 | 0.782 | +0.002 | **+0.3%** | PROVISIONAL STATDELTA |
| AST | 26.5 | `NOT_YET_MODELED` | — | — | not modeled |

TOV% (BRef-style, TOV/(FGA+0.44·FTA+TOV)) is deliberately **NOT**
reported against the real 12.6% figure — no BRef-formula definition is
locked in this repository yet (per this task's own instruction). Instead:
**TOV/team-game = 24.23**, **TOV/true-possession = 0.216** are reported
as the repository's own honest, un-normalized measures, pending a
future, explicitly-locked TOV% definition.

## 9. Shot-clock simulator distribution (verified NBA.com category schema)

`detailed_engine_diagnostics.py`'s `SHOT_CLOCK_BINS` was updated this
task from a 5-bin internal schema to the verified NBA.com 6-bin category
schema (`24-22`, `22-18 Very Early`, `18-15 Early`, `15-7 Average`,
`7-4 Late`, `4-0 Very Late`) — a **diagnostic-only relabeling** of an
already-computed `shot_clock_at_attempt` value; confirmed by the full
test suite that no simulation behavior changed (958/958 passing both
before and after the relabeling, and the benchmark's own non-interference
test independently confirms bit-identical outcomes). **We do not yet
know the real NBA per-bin share for any of these categories — only the
category boundaries are independently verified.** Every share below is
a SIMULATOR share only.

| Bin | Count | Simulator share |
|---|---|---|
| `24-22` | 4,809 | 21.0% |
| `22-18_VERY_EARLY` | 1,707 | 7.5% |
| `18-15_EARLY` | 4,853 | 21.2% |
| `15-7_AVERAGE` | 6,912 | 30.3% |
| `7-4_LATE` | 2,341 | 10.2% |
| `4-0_VERY_LATE` | 2,225 | 9.7% |

- Mean shot clock at FGA: **13.53s** remaining (median, averaged
  per-game: **14.13s**).
- First-action FGA share: **0.384**.
- Mean shot-clock violations/game: **11.21** (≈5.0% of possessions).
- No pathological collapse into the final seconds: `4-0_VERY_LATE`
  remains a minority (9.7%), consistent with the First-Pass Timing
  Calibration's own guardrail finding.

## 10. Primary error vector (ranked, ABSOLUTE relative error)

See Section 8's own table, already sorted by |relative error|. Top of
the ranking by raw magnitude: OREB (+217%), 3PA (+182%), 3PM (+169%),
3PAr (+120%), BLK (−89%), PF (−89%), OREB% (+89%), FTA/FGA (−84%), FTA
(−80%), FTM (−80%), TOV (+69%).

**Likely upstream system, per metric (DIAGNOSTIC ONLY — not a tuning
target list):**

| Metric | Likely upstream system |
|---|---|
| Pace / true possessions | timing/control flow (already the subject of the prior calibration pass) |
| OREB / OREB% | rebound geometry/acquisition (`rebound_resolution.py` + `_dispatch_rebound`'s candidate weighting) |
| TOV | pass/handle/strip systems (`pass_resolution.py`, `on_ball_pressure_resolution.py`, ball-security estimator wiring) |
| FTA/FGA, PF, FTM | foul generation/administration (`foul_resolution.py`'s contact-and-whistle rate, `floor_foul_administration.py`) |
| 3PA, 3PM, 3PAr | shot-family selection (the documented V0 "all perimeter zones dispatch as THREE_POINT" simplification, `_dispatch_shot`) |
| FG% | shot resolution (`shot_resolution.py`/`interior_shot_resolution.py`), but likely LARGELY a downstream consequence of the 3PAr shot-mix distortion above, not necessarily a broken make-probability model on its own (see Section 12) |
| BLK | rim-protection / block resolution (`interior_shot_resolution.py`'s block path) |
| STL | pass-disruption weighting (`pass_resolution.py`'s defender-disruption model) |

## 11. Correlation baseline (SIMULATOR BASELINE ONLY)

**`SIMULATOR BASELINE ONLY`** — no real 2,460-team-game correlation
matrix has been ingested; these are NOT claimed accurate or inaccurate,
only recorded as this engine's own current structural baseline.

| Pair | Simulator Pearson r |
|---|---|
| possessions ↔ FGA | 0.419 |
| possessions ↔ PTS | 0.123 |
| 3PA ↔ PTS | 0.253 |
| FTA ↔ PTS | 0.073 |
| OREB ↔ FGA | 0.648 |
| TOV ↔ FGA | −0.122 |
| ORtg ↔ PTS | 0.915 |

## 12. Causal interpretation

Interpreted strictly in the required order (invariants → timing/pace →
terminal mix → turnovers → rebounds → fouls/FT → shot volume/mix →
shooting efficiency → ORtg/points) — never concluding a downstream
metric is "wrong" while an upstream one remains uncorrected:

**A. How far is pace still from NBA scale after timing calibration?**
+13.35% high (223.98 vs. 197.6 true alternating possessions/game) —
consistent with, not worse than, the calibration pass's own 50-game
validation (+13.1%). This is a KNOWN, already-documented, intentionally
un-closed gap (the calibration task explicitly stopped short of full
closure to avoid pathological shot-clock-violation rates).

**B. Is remaining scoring error primarily pace-driven or
efficiency-driven?** Both, in OPPOSITE directions, that happen to
partially cancel in the raw PTS number: possession volume is high
(+13.4%) and pushes scoring up, while true per-possession efficiency is
LOW (ORtg −5.4%) and pushes it down. Net PTS error (+6.4%) is
**smaller than either underlying driver alone** — it would be a mistake
to read the modest PTS error as "scoring is basically fine"; it is two
larger, opposite errors cancelling.

**C. Simulator ORtg using TRUE possession count:** **108.26**, vs. real
114.5 (−5.4%).

**D. How abnormal is turnover frequency per possession?** Very abnormal
— 24.23 TOV/team-game (+69.4% vs. real 14.3) and 0.216 TOV/true-possession
(meaning over 1-in-5 possessions ends in a turnover). This is the
highest-priority causal upstream error identified (see Section 13):
every turnover REMOVES a shot/rebound opportunity from that possession
entirely, so an inflated turnover rate distorts nearly every downstream
volume metric (FGA, OREB opportunities, FTA opportunities) at once, in
ways this benchmark cannot cleanly separate from those systems' own,
independent, errors.

**E. How abnormal is OREB%?** Extremely abnormal — 47.6% vs. real 25.2%
(+88.8% relative), and the raw OREB count is the single largest error
in the entire table (+217.1%). Offensive rebounds are being won at
nearly double the real league rate.

**F. How abnormal is FTA/FGA?** Extremely abnormal in the OPPOSITE
direction — 0.039 vs. real 0.243 (−84.1%). Free throws are being drawn
at roughly one-sixth the real rate, and PF is similarly suppressed
(−88.9%) — the whistle is calling far too little contact relative to
real NBA foul rates.

**G. How abnormal is 3PAr?** Extremely abnormal — 0.928 vs. real 0.422
(+119.9%). This traces directly to a KNOWN, already-documented V0
architectural simplification (`_dispatch_shot`'s own comment: "ALL
perimeter-zone shots are dispatched as THREE_POINT... this project's
8-zone topology does not distinguish a mid-range release point from a
beyond-the-arc one") — not a probability-calibration issue, a shot-
family-selection/geometry gap.

**H. How abnormal are FG%, 3P%, FT%?** Highly asymmetric. FT% (+0.3%)
is essentially exact — expected, since it reads almost directly off
each synthetic profile's own `free_throw_shrunk_rate` with minimal
confounding. 3P% (−4.5%) is close. FG% (−23.6%) is the most abnormal of
the three, and Section G above gives the likely reason: FG% blends 2P%
and 3P% by volume, and with ~93% of all attempts forced into the
THREE_POINT family (real: ~42%), the blended FG% mechanically drops
toward the (real-plausible) 3P% figure — this looks like it is
substantially a DOWNSTREAM CONSEQUENCE of the 3PAr shot-mix distortion,
not necessarily an independently broken shot-make model. This benchmark
cannot fully separate the two without a corrected shot-mix baseline to
re-measure against — flagged, not concluded.

**I. Which 3–5 metrics are currently the largest MEANINGFUL errors** (by
causal priority, not raw magnitude alone — see Section 13).

**J. Which upstream system should be calibrated NEXT?** See Section 13.

## 13. Recommended next calibration target

Ranked by CAUSAL priority (per the required interpretation order), not
raw percentage error:

1. **Turnover / pass-handle-strip system** (`pass_resolution.py`,
   `on_ball_pressure_resolution.py`) — TOV +69.4%, causally upstream of
   nearly every other volume metric (a turnover removes a shot/rebound/
   FT opportunity from that possession entirely). Highest causal
   priority despite not having the single largest raw error.
2. **Rebound acquisition geometry** (`rebound_resolution.py`'s candidate
   weighting, `_dispatch_rebound`) — OREB% +88.8%, OREB count +217.1%
   (the single largest raw error in the table).
3. **Foul generation/administration** (`foul_resolution.py`'s
   contact-and-whistle rate) — FTA/FGA −84.1%, PF −88.9%, FTM −79.7% —
   a large, consistent, same-direction cluster of errors all pointing
   at under-calling contact.
4. **Shot-family selection / shot geometry** (`_dispatch_shot`'s
   perimeter-zone simplification) — 3PAr +119.9%, and likely a major
   downstream driver of the FG% error (−23.6%). A KNOWN, documented
   architectural gap, not a probability bug.
5. **Rim-protection/block resolution** (`interior_shot_resolution.py`) —
   BLK −89.4%, a large, isolated error, lower causal priority than the
   above four but worth flagging.

**Top pick for the NEXT calibration task: the turnover/pass-disruption
system.** It is causally upstream (per the required interpretation
order) of the two largest raw-magnitude errors in the table (OREB,
3PA/3PM) and of FGA volume generally — correcting it first will change
the baseline every other system is re-measured against, whereas
calibrating rebounds or fouls first risks re-tuning against an
opportunity count (FGA) that is itself still distorted by the turnover
rate.

## 14. Known limitations

- Synthetic, homogeneous player profiles only (Section 3) — no
  real-roster benchmark yet.
- PTS/FGA/FGM/3PA/3PM/FTA/FTM remain PROVISIONAL STATDELTA, not yet
  independently event-derivable (Section 4).
- AST is not modeled at all.
- No substitutions/fatigue/injuries/foul-outs/coaching/timeouts — see
  the Phase 23C scope note (still current).
- TOV% (BRef formula) intentionally not computed — no locked repository
  definition yet.
- No real 2024-25 team-game-level distribution or correlation matrix has
  been ingested — all percentile/correlation figures in Sections 7 and
  11 are SIMULATOR-ONLY, with no real-NBA comparison cells.
- This benchmark used ONE fixed synthetic-profile population across all
  100 games — no player-to-player variance was modeled, so the observed
  simulator distributions (Section 7) reflect only RNG/possession-flow
  variance, not real roster-talent variance.
- This benchmark did not attempt to disentangle the two shot-mix-driven
  errors (3PAr and FG%) quantitatively — flagged as a likely causal
  link (Section 12H), not proven by a controlled re-measurement.

## Classification

**FIRST BENCHMARK COMPLETE.**

**Simulator macro classification: MACRO-PLAUSIBLE WITH LARGE ERRORS.**

Reasoning: the engine completes 100/100 games with zero invariant
faults, zero non-termination, and pace within the correct broad order of
magnitude (+13.4% vs. target, not multiples-off); ORtg/PTS land within
single-digit-to-low-double-digit relative error despite large upstream
volume distortions. But several individual metrics (OREB +217%, 3PA
+182%, FTA/FGA −84%, PF −89%) are multiples away from real NBA values,
not fine-tuning-scale gaps — this is well past "structurally
benchmarkable but otherwise unknown" and well short of anything
approaching realistic. **Never described as `ACCURATE` or `REALISTIC`
at this stage.**

---

## Turnover Root-Cause Diagnosis

This diagnostic-only follow-up uses the identical seeds `25000–25099`,
synthetic profiles, and calibrated timing. No probability, selection weight,
player attribute, clock value, or basketball resolver changed.
`turnover_diagnostics.py` observes completed results; the only simulation-
module additions are clock snapshots that read existing state without
consuming RNG or changing flow.

### Turnover path map and taxonomy

The engine has no reachable travel, illegal-screen, generic-violation, or
standalone `DriveOutcome.LOST_BALL` path in its default game runner. The last
is scaffolded but disabled. These are all current paths:

| Terminal / event | Origin | Exact resolver/function | Live? | Steal? | Player TOV | Team TOV | Clock behavior | Next possession | Probability inputs |
|---|---|---|---|---|---|---|---|---|---|
| `TURNOVER` / `CLEAN_INTERCEPTION` | selected pass | `pass_resolution._resolve_disruption_and_delivery` → `_dispatch_pass` | yes | yes, with identified defender | passer retained in event; no player-TOV counter | exactly 1 | pass flight | live transition / `LIVE_STEAL` | eligible defenders, playmaking, posture; 0.35 given disruption |
| `TURNOVER` / `BAD_PASS_OUT_OF_BOUNDS` | selected pass | same | no | no | passer identifiable, not accumulated | exactly 1 | pass flight | dead-ball inbound | passing-accuracy bad-pass logit; fixed outcome branches |
| `TURNOVER` / identified `BAD_PASS_TO_DEFENDER` | selected pass | same | yes | no currently | passer identifiable, not accumulated | exactly 1 | pass flight | live transition / `LIVE_BAD_PASS_INTERCEPTION` | same bad-pass roll plus defender-identification draw |
| `TURNOVER` / defense-recovered pass loose ball | pass deflection, retained-offense deflection, or unidentified bad pass | `resolve_generic_loose_ball` → `_loose_ball_continuation_or_terminal` | yes | no | passer identifiable, not accumulated | exactly 1 on defense recovery | pass flight + 0.5s scramble | live transition / `LOOSE_BALL_RECOVERY` | pass disruption, then weighted recovery geometry |
| `TURNOVER` / defense-recovered handle strip | drive pressure → `CLEAN_STRIP_LOOSE` | `_resolve_pressure` → generic loose-ball resolver | yes | no | handler identifiable, not accumulated | exactly 1 on defense recovery | 2.5s drive + 0.5s scramble | live transition / `LOOSE_BALL_RECOVERY` | ball security, playmaking, posture; 0.20 strip given disruption; recovery geometry |
| `OFFENSIVE_FOUL_TURNOVER` / `DEAD_BALL_TURNOVER` + foul checkpoint | drive collision | `_resolve_collision` → `_dispatch_floor_foul` | no | no | driver gets personal foul; no player-TOV counter | exactly 1 | 2.5s drive; administration adds no live time | dead-ball inbound | supplied contact, no-call/charge/foul constants, optional foul attributes |
| `SHOT_CLOCK_VIOLATION` | clock exhaustion, no feasible action, or pass arrival | possession loop or `resolve_pass` | no | no | none | **0 in current TOV accounting** | clock already zero | dead-ball inbound | deterministic clock/control flow, not a turnover roll |

Normalized categories are `PASS_CLEAN_INTERCEPTION`, `PASS_BAD_PASS`,
`PASS_LOOSE_BALL_LOST`, `HANDLE_STRIP_LOST`, `OFFENSIVE_FOUL`, and
`SHOT_CLOCK_VIOLATION`; `OTHER_TURNOVER` is a coverage alarm whose observed
count is zero. Raw terminal/outcome, action, context, live/dead status, steal,
and accounting fields remain alongside every normalized observation.

### Fixed-seed 100-game decomposition

- True alternating possessions: **22,398**.
- Engine-accounted turnovers: **4,846** = **24.23/team-game** =
  **0.21636/true possession**.
- Target implication: 14.3 × **200 team-games** = **2,860**; observed excess
  is **1,986**. At league scale the rounded implication is correctly
  14.3 × 2,460 = **35,178**.
- Broad possession losses including shot-clock violations: **5,967**. This
  diagnostic total is not substituted for the benchmark TOV numerator.

| Category | Count | Share of engine TOV | Share of broad losses | Rate / possession | Steals | Live / dead |
|---|---:|---:|---:|---:|---:|---:|
| `PASS_CLEAN_INTERCEPTION` | 2,334 | 48.16% | 39.12% | 10.42% | 2,334 | 2,334 / 0 |
| `PASS_LOOSE_BALL_LOST` | 1,851 | 38.20% | 31.02% | 8.26% | 0 | 1,851 / 0 |
| `PASS_BAD_PASS` | 436 | 9.00% | 7.31% | 1.95% | 0 | 138 / 298 |
| `HANDLE_STRIP_LOST` | 225 | 4.64% | 3.77% | 1.00% | 0 | 225 / 0 |
| `OFFENSIVE_FOUL` | 0 | 0% | 0% | 0% | 0 | 0 / 0 |
| `SHOT_CLOCK_VIOLATION` | 1,121 | excluded | 18.79% | 5.00% | 0 | 0 / 1,121 |

Engine-accounted turnovers are **93.85% live ball** (4,548/4,846) and
**6.15% dead ball** (298/4,846). Including shot-clock losses gives 4,548
live and 1,419 dead possession losses.

### Passing exposure and failure

The run generated **29,979 pass attempts**, or **1.3385/possession**.
Exactly two defenders were eligible on every attempt. Passes caused **4,621
turnovers**: **15.414%/attempt** and **0.20631/possession**, or **95.36%** of
engine-accounted turnovers.

| Selected action family | Attempts | Completed | TOV | TOV/attempt | Clean interceptions | Other bad-pass outcomes | Deflected loose outcomes | Steals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `SWING_PASS` | 15,804 | 11,991 | 2,428 | 15.36% | 1,211 | 336 | 2,250 | 1,211 |
| `RESET_PASS` | 14,175 | 10,805 | 2,193 | 15.47% | 1,123 | 258 | 1,989 | 1,123 |
| `KICKOUT` | 0 | 0 | 0 | — | 0 | 0 | 0 | 0 |
| `POCKET_PASS` | 0 | 0 | 0 | — | 0 | 0 | 0 | 0 |
| `OUTLET_PASS` | 0 | 0 | 0 | — | 0 | 0 | 0 | 0 |

The two active families have effectively equal failure rates. `SWING_PASS`
produces 52.5% of pass turnovers because it supplies 52.7% of exposure, not
because its resolver is uniquely worse. Outcomes were 14,376 clean and 8,420
adjusted completions, 2,334 clean interceptions, 2,280 retained-offense and
1,959 unresolved deflections, 298 out-of-bounds bad passes, 296 bad passes
toward a defender, and 16 shot-clock violations on arrival.

### Handling / strip exposure and failure

- Pressure opportunities: **11,084** (**0.495/possession**).
- Meaningful disruptions: **2,335** (**21.07%/opportunity**).
- Distinct strip attempts are not represented; only resolved outcomes exist.
- `CLEAN_STRIP_LOOSE`: **463** (**4.18%/opportunity**).
- Defense/offense recoveries: **225/238**.
- Handling turnovers: **225** = **2.03%/eligible opportunity**.
- Steals: **0**. `STRIP != STEAL`; recovery does not automatically add one.
- `DriveOutcome.LOST_BALL` is disabled, so unforced handling-loss exposure is
  zero in this benchmark.

### Shot clock and offensive fouls

There were **1,121 shot-clock violations**: **11.21/game**,
**5.605/team-game**, **0.05005/possession**, and **18.79%** of broad losses.
That is 23.13% of the current engine-TOV count, but shot-clock violations are
excluded from that count and therefore do not cause the reported +69.4%
directly. Sources were 1,105 top-of-loop expirations and 16 pass-arrival
expirations; there were zero no-feasible-action cases. All followed at least
four selected actions. After the preceding action the clock was 0.0–2.8s
(mean 1.61): 17 were already zero and 1,104 were in `(0,3]`, after which
existing inter-action or flight time exhausted the clock.

Offensive-foul turnovers were **zero**. Default
`force_on_ball_contact_established=False` gives the collision branch zero
exposure. Under the existing explicit test hook, a charge produces
`OFFENSIVE_FOUL_TURNOVER`, one personal foul and turnover, no steal, no team
foul, and no bonus free throws. Phase 21B administration is unchanged.

### Steal and duplicate-accounting reconciliation

- Turnovers/steals: **4,846/2,334**. Every steal belongs to exactly one clean
  interception; none exists without a turnover.
- **2,512** accounted turnovers have no steal. Of these, 2,214 are live:
  1,851 pass loose-ball losses, 225 handle losses, and 138 identified
  `BAD_PASS_TO_DEFENDER` outcomes. The last creates defender control but no
  steal; that is an accounting-semantics review candidate, not changed here.
- Every turnover possession has exactly one turnover delta. Pass terminals do
  not also emit generic turnover events. Loose loss uses one recovery
  checkpoint. A charge's turnover event and foul checkpoint derive different
  stats. Shot-clock events remain outside TOV. No double-counting was found.

### Stage and context splits

Ordinary/transition denominators are starting exposures; second-chance
exposure counts possessions reaching an OREB. A loss after an OREB is assigned
to second chance rather than duplicated into its original entry.

| Context | Exposures | Engine TOV | Rate | Broad losses | Broad rate |
|---|---:|---:|---:|---:|---:|
| Ordinary entry before second chance | 10,126 starts | 1,521 | 15.02% | 1,988 | 19.63% |
| Transition entry before second chance | 12,272 starts | 2,025 | 16.50% | 2,213 | 18.03% |
| Second-chance continuation | 5,404 reached | 1,300 | 24.06% | 1,766 | 32.68% |

Engine turnovers occurred 1,444 times on the first selected action and 3,402
times later; all 1,121 clock violations occurred later. The second-chance
concentration is real internally, but exposure is inflated by the known OREB
error, so it is not an independent turnover calibration target.

### Player attributes and exact formulas

All synthetic profiles are homogeneous, so realized between-player dependence
cannot be estimated from this sample. Source authority is clear:

- `passing_accuracy_ast_pct=0.18` affects only independent bad-pass risk and
  arrival quality. `playmaking_vision_shrunk_rate` is disabled.
- `defensive_playmaking_per36=1.5` affects eligible-defender pass disruption
  and on-ball disruption.
- `ball_security_error_rate=0.0086` affects only on-ball disruption.
- generic loose-ball recovery reads no player attribute.

Exact unchanged decision structures:

1. Bad pass: `sigmoid(logit(.02) - .15*passing_accuracy)` = **1.9478%** at
   the synthetic value, followed by fixed/random outcome branches.
2. Each eligible defender, sequentially:
   `sigmoid(logit(.12) + .5*z(defensive_playmaking;1.5,.7) + posture)`,
   where posture is `+.3 HELPING`, `-.3 TRAILING`, otherwise zero. First
   success wins. Given disruption: .35 interception, .30 unresolved loose,
   .35 retained-offense loose. Observed disruption was **21.93%/pass** with
   two eligible defenders every time. Sequential defender exposure is a
   nonlinear amplifier; `.12` is not the per-pass rate.
3. On ball:
   `clamp(sigmoid(-1.6 + z(ball_security;.00858,.00737) +
   .35*z(defensive_playmaking;1.5,.7) + posture),.005,.995)`; posture is
   `+.3 SQUARE`, `0 RECOVERING`, `-.4 TRAILING/HELPING`. Given disruption,
   strip is .20 and forced pickup .35.
4. Loose recovery is a categorical draw over players in the ball zone,
   fallback all ten, weight 1 normally and 2 for an already-favored team.
5. When contact is enabled, no-call is
   `clamp(sigmoid(logit(.55)-.15*foul_drawing-(-.15)*foul_discipline),
   .005,.995)`; charge/defensive-foul weights are .20/.25 after a whistle.
6. Shot-clock violation has no stochastic turnover formula; clock decrements
   deterministically produce the zero-clock terminal.

The disruption base rate, sequential defender loop, 35% interception split,
pass volume, and attribute-free recovery are `CALIBRATION CANDIDATE`s, not
logic bugs. The live bad-pass/no-steal semantic is an accounting review
candidate. No value changed.

### Ranked contributors and smallest next calibration

Exact NBA category shares remain unverified, so the 1,986 excess cannot be
honestly allocated into category-specific empirical excess counts. Ranking is
by observed contribution and mechanism evidence:

| Cause | Observed contribution | Evidence | Confidence | Candidate? |
|---|---:|---|---|---|
| Shared pass-disruption resolver | 4,185 TOV (86.36%) | 2,334 interceptions + 1,851 pass loose losses; 21.93% passes disrupted; two sequential defender rolls | high | yes |
| Pass exposure | 29,979 attempts; 1.338/possession | amplifies 15.41% pass failure into .206 TOV/possession; family rates equal | high as amplifier; target unknown | yes, but not first alone |
| Independent bad pass | 436 TOV (9.00%) | explicit ~1.95%/pass branch; some loose outcomes recover | high | later |
| Handling strip/recovery | 225 TOV (4.64%) | 11,084 exposures; 4.18% loose; 48.6% defense recovery | high | not first |
| Shot clock | 1,121 broad losses, zero current TOV | material loss but excluded from +69.4% numerator | high | timing follow-up |
| Offensive foul | 0 | no default collision exposure | high | no |
| Duplicate accounting | 0 | event/terminal/delta reconciliation | high | no |

**Smallest recommended next calibration target: the pass-disruption attempt
resolver only**, specifically the per-eligible-defender attempt structure/base
rate in `_resolve_disruption_and_delivery`. Hold independent bad passes,
handling strips, loose recovery, timing, action selection, and every other
basketball probability fixed. The shared branch supplies 86.36% of observed
turnovers, while active pass families show no family-specific failure anomaly.

**Classification: TURNOVER ROOT CAUSE IDENTIFIED.**

Verification after the diagnostic additions: **10/10** new turnover-focused
tests, **222/222** combined detailed-engine diagnostic/orchestrator tests, and
**1002/1002** repository tests pass.

---

## Turnover Accounting Reconciliation

This is an accounting-semantics correction only. Seeds, profiles, resolver
probabilities, selection, clocks, and basketball state transitions are
unchanged.

### Prior ambiguity and representation

Before this reconciliation, `StatDeltas.turnovers` was the only turnover
field. It counted every represented player-chargeable loss but had neither an
individual-player map nor a way to express a team-only turnover. Callers then
aggregated it as team TOV. It therefore represented both concepts only while
their totals happened to coincide, and silently excluded shot-clock team
turnovers.

The minimum compatible extension is:

- `StatDeltas.turnovers`: preserved, unchanged, as the backward-compatible
  player-charged turnover total;
- `StatDeltas.player_turnovers`: exact `player_id -> count` attribution;
- `StatDeltas.team_turnovers`: every team turnover, including team-only
  violations.

All are provisional projections cross-checked against the event stream.
`EventDerivedStats` exposes the same three views. The game summary preserves
its existing `turnovers` field and adds explicit team/player totals. Benchmark
`TeamGameStats.turnovers` now correctly means public-comparable team TOV, with
`player_turnovers` separate.

No new `EventType` was necessary. Existing events already carry the required
semantics: `SHOT_CLOCK_VIOLATION` identifies a team-only loss without a player;
pass events identify the passer and any direct interceptor; on-ball-pressure
and loose-ball checkpoints identify the handler/passer and later recovery;
dead-ball turnover identifies the offensive-foul committer. Event-first
derivation now maps those structured facts into team and player totals.

### Category accounting

| Category | Before | Team TOV after | Player TOV after | Steal after |
|---|---|---:|---:|---:|
| `CLEAN_INTERCEPTION` | 1 undifferentiated TOV + steal | 1 | 1, passer | 1 |
| `BAD_PASS_OUT_OF_BOUNDS` | 1 undifferentiated TOV | 1 | 1, passer | 0 |
| Identified `BAD_PASS_TO_DEFENDER` | 1 undifferentiated TOV, no steal | 1 | 1, passer | **1** |
| Pass-created loose ball, defense recovery | 1 undifferentiated TOV | 1 | 1, originating passer | 0 |
| Handle strip, defense recovery | 1 undifferentiated TOV | 1 | 1, handler | 0 |
| Offensive foul | 1 undifferentiated TOV + personal foul | 1 | 1, offender | 0 |
| Shot-clock violation | possession ended, 0 TOV | **1** | **0** | 0 |

Shot-clock detection, frequency, possession transfer, and clock behavior are
unchanged. Only its already-existing event now derives one team turnover and
zero player turnovers/steals; no random player is selected.

### `BAD_PASS_TO_DEFENDER` and loose-ball steal audit

`BAD_PASS_TO_DEFENDER` has two actual control paths. With a non-null
`disrupting_defender_id`, `_apply_outcome` assigns that exact defender `HELD`
control and immediately flips team possession. This is a direct interception,
so the identified defender now receives a steal, consistently with
`CLEAN_INTERCEPTION`, while the raw category remains distinct. In the
100-game sample this corrects **138** steals.

With a null defender id, the resolver produces `LOOSE` state and generic
recovery. No steal is awarded merely because the defense later wins. The same
rule holds for pass deflections and handle strips: disruption/strip does not by
itself identify a completed steal, so `STRIP != STEAL` remains intact.

### Before/after 100-game accounting

Same seeds `25000–25099`, same 22,398 basketball possessions:

| Metric | Before | After |
|---|---:|---:|
| Player-charged turnovers | not separately representable (legacy total 4,846) | **4,846** |
| Player TOV/team-game | 24.23 implicit | **24.23** explicit |
| Team turnovers | 4,846, missing team-only violations | **5,967** |
| Team TOV/team-game | 24.23 | **29.835** |
| Team TOV vs. 14.3 target | +69.4% | **+108.64%** |
| Shot-clock team-only turnovers | 0 | **1,121** = 5.605/team-game |
| Steals | 2,334 | **2,472** |
| STL/team-game | 11.67 | **12.36** |
| STL/team-TOV ratio | 48.16% using incomplete TOV | **41.43%** |

The corrected simulator looks worse against NBA team turnovers, as expected.
No probability was tuned to conceal that result.

### Deterministic non-interference

A canonical digest over all 100 games includes final scores, possession IDs
and ownership, start/end game clocks, action selection logs, complete trace,
shots, rebounds, fouls, period/shot-clock timing, terminal reasons, event
streams, and every non-turnover stat. Before and after reconciliation it is
identically:

`e8ff98da97cad7612fe28c0dabeeb8a5a255afc9004833b1576d43a766d8df05`

Only accounting projections changed: shot-clock team TOV and the 138 logically
identified direct-interception steals.

**Classification: TURNOVER ACCOUNTING RECONCILED.** Probability calibration
has not begun.

Verification: **12/12** new accounting-focused tests, **234/234** combined
detailed-engine focused tests, and **1014/1014** repository tests pass.

---

## First Pass-Disruption Calibration

This is a macro-constrained structural calibration, not direct pass-event
empirical calibration. The verified public target is 14.3 team turnovers per
team-game. Verified 2024–25 league-wide bad-pass share, lost-ball share,
interception share, and pass-turnover/pass denominator are not available here,
so no candidate pass-turnover rate is labeled “NBA correct.” Independent bad
passes, handling, action selection, player attributes, timing, and the
`.35/.30/.35` disruption outcomes remained frozen.

### Analytic effective-probability audit

`BASE_RATE_ANY_DISRUPTION_ATTEMPT` is a probability **per eligible defender**,
not per pass. After the independent bad-pass gate, eligible defenders receive
sequential independent rolls in caller order; the first success ends the loop.
For defender probabilities `p_i`, the exact conditional probability of any
disruption is `1 - product(1 - p_i)`. Two neutral defenders therefore turn the
old `.12` base into `1-(1-.12)^2 = 22.56%` per pass conditional on reaching
the loop, rather than 12%. At synthetic passing accuracy `.18`, the unchanged
independent bad-pass probability is
`sigmoid(logit(.02)-.15*.18) = 1.9477602%`; including that preceding gate, the
old full-flow disruption probability is 22.120585%.

| Per-defender base | Two-neutral-defender conditional | Full flow after unchanged bad-pass gate |
|---:|---:|---:|
| .12 | 22.56% | 22.1206% |
| .10 | 19.00% | 18.6299% |
| .08 | 15.36% | 15.0608% |
| .06 | 11.64% | 11.4133% |
| .04 | 7.84% | 7.6873% |

Observed sensitivity rates match this control-flow calculation.

### Two-defender exposure audit

Every pass in the calibration benchmark had exactly two eligible defenders.
The default filter always admits the passer’s matchup defender and receiver’s
matchup defender. Other defenders qualify only when their coarse zone is
central or on the same side as the pass origin or destination. With the
current mirrored coarse geometry, no additional matchup defender qualified in
the benchmark.

This represents two distinct lane pressures, not the same defender counted
twice. It is therefore **structurally correct but calibration-sensitive and
high-leverage**, rather than clearly incorrect. The independent sequential
rolls nonlinearly amplify the per-defender base, but there is not enough
evidence to remove an exposure merely because aggregate turnovers are high.

### Deterministic 20-game sensitivity study

Seeds `26000–26019`; all inputs except the shared per-eligible-defender base
were frozen. “Opportunities” are eligible-defender roll slots. “Loose” includes
the disruption branch’s loose-ball outcomes, whether the offense ultimately
retained the ball or not. Rates per team-game use 40 team-games.

| Base | Pass attempts | Opportunities | Disruptions | Disruptions/pass | Clean INT | Loose | Pass TOV | Pass TOV/pass | Player TOV/tg | Team TOV/tg | STL/tg | True poss/game | FGA/tg | Shot-clock TOV/tg | Faults |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| .12 | 6,030 | 12,060 | 1,341 | 22.2388% | 472 | 869 | 915 | 15.1741% | 24.300 | 29.850 | 12.600 | 224.75 | 114.675 | 5.550 | 0 |
| .10 | 5,969 | 11,938 | 1,109 | 18.5793% | 394 | 715 | 772 | 12.9335% | 20.700 | 26.750 | 10.650 | 217.95 | 114.575 | 6.050 | 0 |
| **.08** | **5,907** | **11,814** | **886** | **14.9992%** | **321** | **565** | **642** | **10.8685%** | **17.350** | **23.750** | **8.775** | **210.10** | **114.000** | **6.400** | **0** |
| .06 | 5,830 | 11,660 | 670 | 11.4923% | 238 | 432 | 496 | 8.5077% | 13.650 | 20.475 | 6.625 | 203.40 | 114.050 | 6.825 | 0 |
| .04 | 5,754 | 11,508 | 461 | 8.0118% | 173 | 288 | 365 | 6.3434% | 10.325 | 17.675 | 5.000 | 196.70 | 113.750 | 7.350 | 0 |

The selected first-pass value is **`.08`**. It substantially removes the
shared-disruption excess without using pass errors to compensate for the
separate clock problem. `.10` leaves player turnovers high; `.06` lowers
steals to 6.625/team-game, below the supplied 8.2 target. `.08` produces
8.775 steals/team-game in calibration, preserves passing volume and all other
mechanisms, creates no faults, and avoids fake precision. The value was frozen
before independent validation.

### Independent validation

Seeds `26100–26149`, 50 games, default `.08`; no retuning afterward:

| Metric | Validation result |
|---|---:|
| Team TOV/team-game | 22.940 |
| Player TOV/team-game | 16.900 |
| Pass TOV/team-game | 15.820 |
| Pass TOV/pass | 10.8164% |
| STL/team-game | 8.300 |
| Shot-clock TOV/team-game | 6.040 |
| True possessions/game | 209.90 |
| FGA/team-game | 115.750 |
| Mechanical faults | 0 |

The same configuration and seed also reproduce identical complete detailed
game results, establishing configuration-level deterministic replay.

### Canonical 100-game before/after

Seeds `25000–25099`; before explicitly pins `.12`, after uses the selected
default `.08`.

| Metric | Before `.12` | After `.08` | Change |
|---|---:|---:|---:|
| Team TOV/team-game | 29.835 | 23.720 | -6.115 |
| Player TOV/team-game | 24.230 | 17.065 | -7.165 |
| Pass TOV/team-game | 23.105 | 15.980 | -7.125 |
| Pass TOV/pass | 15.4141% | 10.9116% | -4.5025 pp |
| Handling TOV/team-game | 1.125 | 1.085 | -0.040 observed |
| Shot-clock TOV/team-game | 5.605 | 6.655 | +1.050 observed |
| STL/team-game | 12.360 | 8.295 | -4.065 |
| True possessions/game | 223.98 | 211.63 | -12.35 |
| FGA/team-game | 114.235 | 114.015 | -0.220 |
| PTS/team-game | 121.035 | 120.555 | -0.480 |
| ORtg (true possessions) | 108.263 | 114.139 | +5.876 |
| OREB% | 47.5698% | 47.3883% | -0.1815 pp |
| FTA/FGA | 3.8677% | 3.8669% | -0.0008 pp |
| 3PAr | 92.8019% | 92.6968% | -0.1051 pp |
| FG% | 35.6641% | 35.5904% | -0.0737 pp |
| PF/team-game | 2.065 | 2.070 | +0.005 |
| Mechanical faults | 0 | 0 | 0 |

The after-calibration team TOV result remains **65.87% above** 14.3. This is
expected and is a flag, not a reason to force pass disruption lower. The
shared-base change directly reduces pass turnovers and clean interceptions;
possession count and ORtg then move because fewer early possession changes
alter downstream paths. FGA, points, handling turnovers, and the other error
vector entries above are observations only, not calibration targets.

Shot-clock mechanics are unchanged: ordinary entry remains 9.0 seconds,
transition entry 1.5, second chance 1.0, and inter-action 3.0. The realized
shot-clock count rises from 5.605 to 6.655/team-game only because more passes
survive and possessions reach later clock states. The high shot-clock rate
remains a separate timing/control-flow calibration problem.

Remaining turnover debt includes that shot-clock problem, lack of verified
empirical pass-event denominators/composition, and later evaluation of the
disruption composition and lane representation with richer geometry. Neither
the `.35/.30/.35` outcome split nor any other subsystem was changed here.

**Classification: PASS DISRUPTION CALIBRATION VALIDATED WITH FLAGS.** The
calibration materially improves both pass turnovers and steals with zero
faults, while total team turnovers remain high and the empirical limitation
prevents claiming direct NBA pass-event calibration.

Verification: **7/7** new calibration guardrails, **278/278** combined
detailed-engine focused tests, and **1021/1021** repository tests pass.

---

## Shot-Clock Violation Root-Cause Diagnosis

This section is diagnostic only. The accepted `.08` pass-disruption base,
all other basketball probabilities and weights, action selection, player
attributes, and timing values remain unchanged. The real 2024–25 NBA
shot-clock-violation rate is **NOT YET VERIFIED**, so the simulator rate is
not described as empirically correct or incorrect; its magnitude warrants the
structural diagnosis below.

### Complete path map and causal telemetry

There are three terminal accounting locations, but more causal timing stages:

| Terminal location | Trigger | Possible causal charge | Action already selected? | Accounting |
|---|---|---|---|---|
| `TOP_OF_LOOP` | shot clock is `<= 0` and ball is not loose | entry, inter-action, drive execution, loose-ball recovery, or a non-completing pass flight | no new action after expiration; a prior action may have caused it | 1 team TOV, 0 player TOV, 0 steals |
| `PASS_ARRIVAL` | a pass flight reaches zero and the pre-resolved outcome would otherwise complete | pass flight | yes, the pass | 1 team TOV, 0 player TOV, 0 steals |
| `NO_FEASIBLE_ACTION` | perception/clock filtering leaves no selectable supported action | no required zero-crossing charge | no | 1 team TOV, 0 player TOV, 0 steals |

Shot execution can reduce the clock to zero without creating a violation: the
shot was released while the clock was positive and its made/miss/rebound path
terminates or continues under shot/rebound semantics. A drive can cross zero,
but detection is deferred to a later loop. While the ball is loose, the
top-of-loop shot-clock check is deliberately skipped; if the offense recovers,
the next non-loose iteration recognizes expiration. Entry expiration is also
recognized by the first top-of-loop check. `NO_FEASIBLE_ACTION` is reachable
with positive clock but did not occur in this benchmark.

The new zero-RNG telemetry records a complete clock-charge ledger, every
perceived and clock-feasible decision menu, and exactly one terminal summary
per violation. It captures possession/team/origin, action count, previous
decision and post-action clocks, the exact zero-crossing charge, prior action
and outcome, pass/drive/action counts, entry/inter-action/pass/drive/shot time,
prior shot/OREB state, terminal location, and action/shot feasibility. No
simulation decision reads these logs.

### Canonical 100-game decomposition

Seeds `25000–25099`, accepted default `.08`:

| Measure | Result |
|---|---:|
| Total violations | **1,331** |
| Violations/game | **13.310** |
| Violations/team-game | **6.655** |
| Violations/true possession | **6.2893%** |
| Share of all team turnovers | **28.0565%** (1,331 / 4,744) |
| True possessions | 21,163 |

Accounting location and actual causal stage are sharply different:

| Accounting location | Causal stage | Count | Share |
|---|---|---:|---:|
| `TOP_OF_LOOP` | `INTER_ACTION` | **1,292** | **97.07%** |
| `TOP_OF_LOOP` | `LOOSE_BALL_RECOVERY` | 18 | 1.35% |
| `TOP_OF_LOOP` | `PASS_FLIGHT` | 2 | 0.15% |
| `PASS_ARRIVAL` | `PASS_FLIGHT` | 19 | 1.43% |
| `NO_FEASIBLE_ACTION` | none | 0 | 0% |

Thus 1,312 violations are reported at `TOP_OF_LOOP`, but only the check
location is “top of loop.” In all 1,312 cases a previous charge had already
exhausted the clock, and no new selection or dispatch occurred before
recognition. For the 1,292 inter-action cases, the prior action left positive
clock and the subsequent inter-action charge crossed zero. The two pass-flight
cases became loose on a retained-offense deflection; the loose-state exemption
delayed recognition. The 18 loose-ball cases crossed during the 0.5-second
recovery charge.

Current segment at violation, mutually exclusive:

| Segment origin | Violations | Share |
|---|---:|---:|
| Ordinary halfcourt, no prior OREB | 562 | 42.22% |
| Transition, no prior OREB | 199 | 14.95% |
| After an OREB | 570 | 42.82% |

Initial possession origin and exposure rates are:

| Initial origin/context | Exposure | Violations | Rate |
|---|---:|---:|---:|
| Ordinary dead-ball possessions | 10,293 | 831 | 8.073% |
| Live-transition possessions | 10,870 | 500 | 4.600% |
| Second-chance continuations | 7,004 | 570 | 8.138% |

The second-chance denominator overlaps the two initial-origin denominators.
Every OREB correctly resets the clock to 14.0, then the unchanged 1.0-second
setup leaves 13.0 before the next decision. Among the 570 later violations,
actions after the last OREB were: 3 actions in 147 cases, 4 in 402, 5 in 20,
and 6 in 1. A prior recorded FGA existed in 566 cases; the remaining four
reflect existing shot-log/accounting conventions, not a different reset rule.

The terminal action-count distribution (one-based number of modeled actions
completed/selected before the violation) was:

| Actions | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Violations | 543 | 326 | 189 | 131 | 62 | 30 | 27 | 12 | 5 | 3 | 3 |
| Share | 40.80% | 24.49% | 14.20% | 9.84% | 4.66% | 2.25% | 2.03% | 0.90% | 0.38% | 0.23% | 0.23% |

Passes before violation were 1:71, 2:209, 3:525, 4:263, 5:140,
6:81, 7:29, 8:10, 9:2, and 10:1 (mean **3.466**). Drives were 0:151,
1:691, 2:354, 3:117, 4:17, and 5:1 (mean **1.370**). Mean total modeled
actions were **5.381**. Every violation’s immediately preceding modeled
action was `SWING_PASS`.

### Inter-action and fixed-duration behavior

All **1,292** inter-action-caused violations began below the configured
3.0-second charge; there were no other inter-action charges starting below
three seconds that survived. Clock before the charge:

| Remaining clock | Count |
|---|---:|
| floating-point near zero | 6 |
| `(0, 0.5)` | 282 |
| `[0.5, 1.0)` | 103 |
| `[1.0, 2.0)` | 228 |
| `[2.0, 3.0)` | 673 |

Mean remaining clock was **1.641 seconds**. `_charge_time` conceptually applies
the full configured 3.0 seconds, clamps the shot clock at zero, and separately
subtracts the full 3.0 seconds from the game clock. It therefore matches
fixed-duration behavior **B** in the requested taxonomy: the action gap is
modeled as three seconds and then clamped, rather than truncated semantically
at the horn. Mean modeled overshoot was 1.359 seconds (1,755.4 seconds total
across the sample). Control flow does not allow another action after this;
the next loop terminates immediately.

Ordinary entry itself caused zero benchmark expirations because a fresh
24-second clock becomes exactly 15.0 after the unchanged 9.0-second charge.
Transition first decisions begin at 22.5 after the unchanged 1.5 seconds.
The higher initial ordinary-origin violation rate shows that the 9-second
entry materially reduces later flexibility, but it is not the immediate
zero-crossing cause and is not independently calibrated here.

### Pass-arrival behavior

There were 21 pass-flight zero crossings: 19 immediate `PASS_ARRIVAL`
violations and two later `TOP_OF_LOOP` violations after retained-offense loose
deflections. All selected intents were `SWING_PASS`; all classified to the
resolver's `DIRECT` family, whose unchanged flight duration is 0.4 seconds.
Twenty began with only floating-point residue above zero; one began at 0.2
seconds. A feasible shot had been present in all 21 prior decision menus.

For otherwise-completing passes, the resolver does not allow possession to
continue after the horn: it replaces the completion with a shot-clock
violation on arrival. It does, however, resolve the pass outcome before the
clock check, and disrupted outcomes are not uniformly overridden. The early
return on an arrival violation also leaves game clock undecremented even
though shot clock consumed the flight. Those are real mechanical consistency
flags, but only 21/1,331 violations involve pass-flight zero crossing and no
fix is made here.

### Late-clock decision audit and feasible-action classification

Remaining shot clock enters `ClockContext`, but it changes only feasibility:
extended actions disappear below 7 seconds and `RESET_PASS` disappears below
4. It does not change scores or add urgency. `SWING_PASS`, `KICKOUT`, and
`POCKET_PASS` remain equally eligible near zero, so continuation passes can be
selected until the clock expires.

| Clock threshold | Decisions | Pass selected | Shot selected | Reset selected | Pass available | Shot available |
|---:|---:|---:|---:|---:|---:|---:|
| `<= 7` | 10,585 | 5,486 (51.83%) | 5,099 (48.17%) | 1,894 | 10,585 | 9,682 |
| `<= 4` | 4,510 | 1,927 (42.73%) | 2,583 (57.27%) | 104 | 4,510 | 4,170 |
| `<= 2` | 1,924 | 753 (39.14%) | 1,171 (60.86%) | 0 | 1,924 | 1,823 |

At the previous decision, all 1,331 violations had at least one feasible
action and 1,143 had a feasible shot. Primary causal classification:

| Class | Count | Reason |
|---|---:|---|
| A. Legitimate live action/timing | 0 | no benchmark case lacked the structural flags below |
| B. No action feasible | 0 | `NO_FEASIBLE_ACTION` was never reached |
| C. Late-clock action not attempted | 21 | a pass was selected with a feasible shot and its flight crossed zero |
| D. Fixed timing charge overshot remaining clock | **1,310** | 1,292 inter-action + 18 loose-ball recovery |
| E. Other | 0 | complete coverage |

This primary classification assigns the immediate zero-crossing mechanism.
Late-clock selection remains an upstream contributor to class D: 1,104 of the
1,292 inter-action cases had a feasible shot at the prior decision, selected a
pass instead, and then applied a full continuation gap.

### Why `.08` increased realized violations

The `.12` and `.08` canonical runs use identical seeds and timing. Reducing
pass disruption lets more passes survive, creating more and longer live action
chains and therefore more late-clock exposure:

| Measure | `.12` | `.08` | Change |
|---|---:|---:|---:|
| Violations | 1,121 | 1,331 | +210 |
| Inter-action-caused | 1,083 | 1,292 | **+209** |
| Pass-flight-caused | 17 | 21 | +4 |
| Loose-recovery-caused | 21 | 18 | -3 |
| Inter-action charges | 33,496 | 34,632 | +1,136 |
| Actions before violating | 5,990 | 7,162 | +1,172 |
| Passes before violating | 3,814 | 4,613 | +799 |
| Drives before violating | 1,608 | 1,823 | +215 |
| Violations after OREB | 466 | 570 | +104 |

The net +210 is therefore almost entirely the +209 inter-action cases. There
is no hidden interaction with the `.08` probability: fewer pass turnovers
simply expose the unchanged late-clock continuation model more often.

### Ranked causes and smallest next intervention

1. **Late-clock continuation plus fixed 3.0-second inter-action charge:**
   1,292 direct crossings (97.07%); 1,104 followed a decision where a shot was
   feasible, but a pass was selected.
2. **No urgency in action scoring:** passes remain 39.14% of selections at
   two seconds or less and remain available down to floating-point residue.
3. **Ordinary-entry and second-chance exposure:** entry does not directly
   expire the clock, but ordinary starts leave 15 seconds and second chances
   leave 13; each context has an approximately 8.1% violation rate.
4. **Loose-ball recovery crossing:** 18 cases (1.35%).
5. **Pass-flight boundary semantics:** 21 crossings (1.58%), including 20
   selected from effectively zero, plus outcome-ordering and game-clock flags.
6. **No-feasible-action path:** zero observed cases.

The smallest recommended next intervention is a **late-clock terminal-action
feasibility/urgency gate**, not a global duration reduction: when the remaining
clock cannot support a pass flight plus the modeled continuation gap and a
shot is feasible, continuation passes should yield to the terminal shot. This
directly targets the traced decision failure without using shot-clock
violations as a global pace knob. After that isolated change, residual
fixed-duration/game-clock truncation and pass-arrival ordering should be
re-diagnosed separately before any timing value is calibrated.

**Classification: SHOT-CLOCK ROOT CAUSE IDENTIFIED.**

Verification: **7/7** new shot-clock diagnostic tests, **256/256**
focused diagnostic/timing/pass/benchmark tests, and **1028/1028** repository
tests pass.

## Shot-Clock Expiration Clock Semantics

### Mechanical bug and authoritative rule

The previous live-clock helper independently subtracted the full nominal
duration from both clocks and clamped each at zero. For example, a 3.0-second
inter-action segment starting at 1.2 on the shot clock and 100.0 on the game
clock produced 0.0 and 97.0. The possession actually ends at the shot-clock
horn, so the physically correct game clock is 98.8; the remaining 1.8 seconds
never occur.

All live timing now uses one competing-clock calculation:

`actual elapsed = min(nominal duration, active shot clock, period clock)`

The returned result contains nominal duration, actual elapsed duration, both
post-segment clocks, terminal cause, and separate shot-clock and period-clock
truncation. Configured durations are unchanged. Callers that resolve outcomes
at the end of a drive, pass, or loose-ball segment preflight the same rule, so
no RNG result or basketball action occurs after an earlier horn.

Exact equality is terminal. If the shot and period clocks expire
simultaneously, `SHOT_CLOCK` deterministically wins, preserving the detailed
engine's established top-of-loop shot-clock-first ordering. If the period clock
expires strictly earlier, `PERIOD` wins and no shot-clock turnover is created.
Clocks are clamped at zero. The existing `1e-9` timing-test tolerance is reused,
so meaningless positive floating-point residue cannot authorize another live
segment.

Legally released shots are the explicit exception to the shot-clock stop
condition: their existing make/miss/foul resolution is preserved after release.
The shot clock still decrements and clamps for state display, while the period
clock remains an active bound.

### Pass-arrival correction and timing coverage

Pass flight previously reduced the shot clock first and returned early on an
otherwise completed pass that reached zero, before reducing the game clock.
Passes now use the same competing-clock result as every other live category.
A pass beginning at 0.2 seconds with a 0.4-second nominal flight charges 0.2
seconds to both clocks, records 0.2 seconds of shot-clock truncation, and
terminates at the horn without resolving a later arrival outcome. A period horn
that occurs strictly first similarly ends the period without a turnover.

The shared semantics cover halfcourt entry, transition entry, second-chance
setup, inter-action time, drive execution, loose-ball recovery, and pass
flight. Shot execution uses the documented legal-release exception above.
Clock-charge telemetry now reconciles timing totals against actual elapsed
game time rather than treating nominal overshoot as elapsed time.

### Canonical 100-game before/after

Seeds `25000–25099`, accepted default pass-disruption rate `.08`; this is a
correctness comparison, not a calibration claim:

| Measure | Before | After | Change |
|---|---:|---:|---:|
| Shot-clock violations | 1,331 | 1,425 | +94 |
| Violations/team-game | 6.655 | 7.125 | +0.470 |
| Inter-action causes | 1,292 | 1,379 | +87 |
| Loose-ball recovery causes | 18 | 38 | +20 |
| Pass-flight causes | 21 | 8 | -13 |
| Mean game-clock time consumed by terminal segment | 2.914s | 1.683s | -1.231s |
| Mean total game-clock time in violating possession | 26.158s | 24.841s | -1.317s |
| Requested time truncated at shot-clock horns | not represented | 1,761.1s | physically removed |
| True possessions/game | 211.63 | 213.17 | +1.54 |
| Pace error vs. 197.6 | +7.100% | +7.880% | +0.780 pp |
| Mean shot clock at FGA | 13.055s | 13.083s | +0.028s |
| Team TOV/team-game | 23.720 | 24.120 | +0.400 |
| Player TOV/team-game | 17.065 | 16.995 | -0.070 |
| STL/team-game | 8.295 | 8.280 | -0.015 |
| Points/team-game | 120.555 | 121.040 | +0.485 |
| Combined score/game | 241.110 | 242.080 | +0.970 |
| Simulation faults | 0 | 0 | 0 |

The 1,761.1 seconds are nominal remainders that the corrected run explicitly
attributes to shot-clock truncation and no longer subtracts from game clocks.
That recovered time permits 154 additional possessions across 100 games. The
changed violation total, cause mix, overtime count (2 to 3), turnover totals,
and scoring are legitimate downstream consequences of later period state and
RNG consumption; none is interpreted as benchmark improvement.

Late-clock urgency remains deliberately deferred. After the correction, 1,229
of 1,425 violations still followed a decision with a feasible shot, and passes
remain 733 of 1,852 selections (**39.58%**) at two seconds or less. The next
intervention remains a separately reviewed decision/urgency change, not a clock
or timing-constant adjustment.

**Classification: CLOCK EXPIRATION SEMANTICS VALIDATED.**

Verification: **17/17** focused clock-expiration tests and **1045/1045**
repository tests pass. No action-selection weight, probability, pass-disruption
rate, player attribute, or configured timing constant changed.

## Late-Clock Decision Feasibility

### Old behavior and insertion point

The corrected clock primitive stopped phantom time consumption, but selection
still treated `SWING_PASS` as available with too little clock to complete its
flight and reach another decision. The narrow correction belongs in the
existing clock-feasibility layer: structural opportunities are generated and
perceived exactly as before, clock availability is filtered, and only then are
the unchanged action scores converted to probabilities and sampled. There is
no post-selection override and no forced choice of a particular shot.

Current pass control flow has no direct-terminal catch-and-shoot path. A
completed pass first consumes its real family flight time, then the generic
3.0-second inter-action stage, and only afterward generates the receiver's
next menu (including a possible `CATCH_AND_SHOOT`). The conservative pass
minimum therefore reflects the control flow that actually exists rather than
assuming an unmodeled immediate release.

### Minimum-time model and exact rule

| Selectable family | Existing timing requirement | Late-clock treatment |
|---|---:|---|
| `SWING_PASS`, `KICKOUT`, `POCKET_PASS` | family flight 0.4/0.6/0.9s + inter-action 3.0s | 3.4/3.6/3.9s to reach another decision |
| `RESET_PASS` | same actual family flight + inter-action | same continuation rule; existing 4.0s floor still applies first |
| `DRIVE` | existing 2.5s execution | unchanged; existing 7.0s extended-action floor is already stricter |
| `PULL_UP` | release begins at dispatch; 1.5s resolution | terminal shot remains available with positive clock |
| `CATCH_AND_SHOOT` | release begins at dispatch; 1.0s resolution | terminal shot remains available with positive clock |

For each perceived opportunity, the orchestrator supplies its requirement from
the existing pass-family duration table and the existing configured
inter-action duration. The feasibility layer removes a continuation only when:

1. at least one structurally valid terminal shot survives the pre-existing
   feasibility gates; and
2. `minimum continuation time > remaining shot clock + 1e-9`.

The tolerance is the authoritative clock module's existing `1e-9`; no second
epsilon was introduced. Exact equality remains feasible. If selected at exact
equality, the already-validated clock rule deterministically expires at the
horn. When no terminal shot exists, no continuation is removed and no shot is
invented; normal violation behavior remains reachable.

### Zero-RNG telemetry

Every decision now records whether filtering activated, each removed
opportunity and its minimum requirement, remaining shot clock, terminal shots
available, the action selected from the reduced menu, and whether that
possession ultimately violated. “Selected replacement” means the normal
selector's choice from the feasible menu; it is not a separately sampled or
counterfactual choice. Terminal annotation mutates diagnostic rows only and
consumes no RNG.

Canonical seeds `25000–25099` produced **3,619 activations** in **3,285
possessions**. All 3,619 removed opportunities were `SWING_PASS`; all 3,619
normal post-filter selections were terminal shots. Seventeen activated
possessions still violated later in their possession sequence.

### Canonical 100-game before/after

The “before” column is pushed clock-semantics checkpoint `6623edb`; both runs
use default pass disruption `.08` and identical seeds `25000–25099`.

| Measure | Before | After | Change |
|---|---:|---:|---:|
| Shot-clock violations | 1,425 | 274 | -1,151 |
| Violations/game | 14.250 | 2.740 | -11.510 |
| Violations/team-game | 7.125 | 1.370 | -5.755 |
| Violations/true possession | 6.6848% | 1.2855% | -5.3993 pp |
| Inter-action causes | 1,379 | 274 | -1,105 |
| Loose-ball recovery causes | 38 | 0 | -38 |
| Pass-flight causes | 8 | 0 | -8 |
| True possessions/game | 213.17 | 213.15 | -0.02 |
| Mean shot clock at FGA | 13.083s | 12.539s | -0.543s |
| Actions/possession | 2.9679 | 3.0312 | +0.0634 |
| Team TOV/team-game | 24.120 | 18.170 | -5.950 |
| Player TOV/team-game | 16.995 | 16.800 | -0.195 |
| STL/team-game | 8.280 | 8.230 | -0.050 |
| FGA/team-game | 114.405 | 122.855 | +8.450 |
| PTS/team-game | 121.040 | 128.835 | +7.795 |
| ORtg (true possessions) | 113.562 | 120.887 | +7.325 |
| OREB% | 47.3901% | 47.3271% | -0.0630 pp |
| FTA/FGA | 3.8591% | 3.8338% | -0.0253 pp |
| 3PAr | 92.7232% | 91.5225% | -1.2007 pp |
| PF/team-game | 2.075 | 2.245 | +0.170 |
| Faults | 0 | 0 | 0 |

Shot-clock-at-FGA distribution moved later without altering any shot
probability:

| Shot-clock bin | Before count (share) | After count (share) |
|---|---:|---:|
| 24–22 | 4,262 (18.63%) | 4,490 (18.27%) |
| 22–18 | 1,587 (6.94%) | 1,677 (6.83%) |
| 18–15 | 4,947 (21.62%) | 4,777 (19.44%) |
| 15–7 | 7,081 (30.95%) | 7,209 (29.34%) |
| 7–4 | 2,576 (11.26%) | 2,633 (10.72%) |
| 4–0 | 2,428 (10.61%) | 3,785 (15.40%) |

First-action counts remained nearly unchanged: drive 4,303→4,299,
`SWING_PASS` 4,247→4,235, catch-and-shoot 4,234→4,247, pull-up
4,219→4,217, and reset pass 4,147→4,143. This is consistent with the gate
being inactive outside its late-clock boundary.

### Remaining violations and empirical limits

Of the 274 remaining violations, **200** reached their previous decision with
no feasible terminal shot, so the gate correctly did not invent one. The other
**74** had a feasible shot but selected a `SWING_PASS` at exact minimum-time
equality; all 74 then expired during the exactly 3.0-second inter-action stage.
They are deterministic boundary cases under the specified strict-`>` rule,
not timing overshoot. The reduction of 1,151 violations is not treated as a
paired causal count because changed choices alter subsequent RNG trajectories.

No verified real shot-clock-violation target or empirical per-action timing
distribution exists yet. The current flight and inter-action values remain the
existing placeholders/calibrated macro hooks, and the model still cannot
express a truly immediate pass-to-shot release. The large FGA/PTS/ORtg movement
is reported for observation only and is not calibration success.

**Classification: LATE-CLOCK FEASIBILITY VALIDATED.**

Verification: **15/15** focused late-clock tests and **1060/1060** repository
tests pass. No probability, scoring weight, timing constant, pass-disruption
rate, shot/rebound/foul model, player attribute, or protected product/legacy
file changed.
