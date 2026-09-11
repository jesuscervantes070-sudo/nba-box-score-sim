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
