# Detailed Engine — Second Independent Halfcourt Interior-Creation Mechanism

## A. Verified starting checkpoint

- Branch: `codex/empirical-player-modeling`.
- Starting HEAD: `51b6226` (`Expand halfcourt interior creation`), matching the requested checkpoint.
- Starting full suite: **1298/1298 passing**, verified live.
- Starting worktree: only untracked `AGENTS.md`; it was not modified, staged, deleted, or committed.
- Canonical seed policy: TRAIN `25000-25049`, HELDOUT `25050-25099`, final `25000-25099`.
- No prior `INTERIOR_CUT` parameter search was repeated.

## B. Production-reachable halfcourt graph audit

Classification: A = reachable/useful; B = reachable but too rare; C = useful but dormant
prerequisite; D = dead/placeholder; E = unsuitable for this V1.

| Candidate/path | Class | Finding |
|---|---|---|
| `SWING_PASS` / `RESET_PASS` | A | Both are common, real passes through `resolve_pass`; they move the receiver to the already-existing destination zone. They do not themselves create a new zone or defensive displacement. |
| `CATCH_AND_SHOOT` / `PULL_UP` | A | Dominant terminal release paths. Their family selection correctly remains downstream, but neither creates interior access. |
| `DRIVE` | A | Real interior source and healthy drive-to-shot funnel, but explicitly not an independent non-drive answer. |
| `INTERIOR_CUT` | A | Real pass-created interior source. Its safe selection lever is already exhausted and was left unchanged. |
| `TRANSITION_PUSH` | A | Strong interior source, but transition is frozen and cannot solve ordinary halfcourt possessions. |
| `POCKET_PASS` | C | Resolver is production-capable, but `roller_id=None` and `screen_active=False` are hardcoded in live structural-context construction. |
| on-ball roll/slip | C/D | No production screen-initiation event supplies the prerequisite. Activating it honestly would require participant selection and screen cadence, not one small hook. |
| off-ball screen | C | `off_ball_screen_resolution.py` can mutate posture/advantage, but has no production caller. It is a caller-triggered primitive, not an autonomous action source. |
| `KICKOUT` / advantage | C | `AdvantageModel` is a sound interface, but live detailed games leave `engine.advantage=None`; fabricating an advantage instance was rejected. |
| defender posture | B | The ball-handler defender changes after drives; off-ball postures remain effectively square absent the dormant off-ball-screen caller. This mostly duplicates drive-derived cut logic. |
| successful pass sequence | B | Existing action history can identify it, but a direct experiment showed inadequate safe leverage: even aggressive weighting moved midrange only about 2-3 percentage points before THREE/FGA failed. |
| high-post/interior seal | A | One off-ball player is already deployed at the coarse `MIDRANGE`/high-post slot. Existing `role_off_finishing` can determine deployment eligibility; the existing pass resolver and PAINT/RIM shot paths complete the causal chain. |
| full post-up subsystem | E | No strength/post skill or post-resolution model exists, and adding those is out of scope. |

## C-D. Selection and rejected alternatives

Selected: **`INTERIOR_SEAL`**, a pass to an off-ball player already deployed at the
`MIDRANGE`/high-post slot who has an existing `role_off_finishing >= 0.5`.

It fits the current state machine with the least invented state:

`HALFCOURT + LIVE_DRIBBLE + high-post deployment + existing finishing role`
→ `INTERIOR_SEAL` opportunity
→ normal action selection
→ existing pass resolution
→ successful receiver arrival at PAINT or RESTRICTED_RIM
→ existing downstream shot opportunity/selection/resolution.

Rejected:

- More `INTERIOR_CUT` weight: already exhausted; untouched.
- Full screen/roll activation: dormant participant/cadence prerequisites make it a subsystem, not a minimal event.
- Fabricated `AdvantageModel`: no live producer or validated magnitude/decay in production.
- Pass-sequence-only seal: structurally valid but too rare to clear the gate safely.
- New post/strength/cutting/roll attribute: unnecessary and violates the requested identity separation.
- Jumper-family conversion, generic midrange suppression, or more forced threes: causal hacks; not implemented.

## E-J. Implemented architecture

- Added `ActionType.INTERIOR_SEAL`, included in existing pass/creation groupings and checkpoint schema.
- Added structural-context fields for the eligible seal receiver and its existing finishing role.
- Receiver selection is deterministic among real on-court teammates currently at `MIDRANGE`, highest
  `role_off_finishing` first with lineup-order tie-break. Missing role evidence excludes the player.
- When a seal is live, it replaces the same-decision `RESET_PASS` opportunity one-for-one. It does not
  enlarge the action menu and does not remove `SWING_PASS`, `DRIVE`, `PULL_UP`, or `CATCH_AND_SHOOT`.
- Added `interior_seal_enabled` for exact before/after benchmarking.
- Added `interior_seal_selection_log_weight=-1.2`, selected on TRAIN only.
- Added `interior_seal_rim_probability=0.40`; PAINT is the majority set-defense destination, but both
  PAINT and RESTRICTED_RIM are reachable. This is a labeled structural V1 split, not fit to shot totals.
- Dispatch reuses `_dispatch_pass`/`resolve_pass`. Failure, deflection, loose-ball, interception, and
  turnover paths remain live.
- Receiver/assigned-defender zones change only after a completed pass.
- The next decision uses existing interior shot opportunity and family selection. No post-hoc shot-family
  override exists.
- No player ability, tendency, physical, or OVR field was added.

## K. TRAIN calibration

Small grid, seeds `25000-25049`; values selected solely on TRAIN, then frozen.

| Seal log weight | Pace/team | FGA/team | RIM | FLOATER | MIDRANGE | THREE | Result |
|---:|---:|---:|---:|---:|---:|---:|---|
| disabled baseline | 97.26 | 87.34 | 11.23% | 9.95% | 39.92% | 38.90% | reference |
| -1.6 | 97.90 | 88.98 | 11.32% | 10.37% | 39.51% | 38.80% | safe, less movement |
| **-1.2** | **98.09** | **88.88** | **11.58%** | **10.47%** | **39.42%** | **38.52%** | selected |
| -0.8 | 97.45 | 88.19 | 11.74% | 10.98% | 39.15% | 38.13% | TRAIN-safe, held-out THREE failed |
| -0.4 | 97.60 | 87.77 | 12.32% | 11.51% | 38.47% | 37.71% | THREE failed |

Selected TRAIN full line, before → after:

| Metric | Before | After |
|---|---:|---:|
| PTS/team | 98.64 | 100.75 |
| ORtg | 101.419 | 102.712 |
| FG% / 2P% / 3P% | 41.84 / 46.42 / 34.70 | 41.81 / 46.71 / 34.10 |
| 3PA/team | 34.15 | 34.52 |
| FTA/team / PF/team | 17.68 / 13.26 | 18.88 / 13.90 |
| TOV/team / BLK/team | 15.46 / 0.94 | 14.66 / 0.96 |
| OREB% | 23.95% | 24.02% |
| drives/team / drives/100 / drive→shot | 38.42 / 39.50 / 36.23% | 39.80 / 40.58 / 36.81% |

## L. HELDOUT validation

Frozen `-1.2`, seeds `25050-25099`:

| Metric | Before | After |
|---|---:|---:|
| possessions/team | 96.47 | 96.86 |
| FGA/team | 87.25 | 89.07 |
| PTS/team / ORtg | 98.00 / 101.586 | 99.43 / 102.653 |
| FG% / 2P% / 3P% | 41.42 / 45.90 / 34.25 | 41.36 / 45.99 / 33.94 |
| 3PA/team | 33.52 | 34.21 |
| FTA/team / PF/team | 18.32 / 13.56 | 18.16 / 13.73 |
| TOV/team / BLK/team | 14.94 / 0.98 | 14.08 / 1.02 |
| OREB% | 24.89% | 25.24% |
| drives/team / drives/100 / drive→shot | 38.58 / 39.99 / 36.91% | 40.34 / 41.65 / 36.89% |
| RIM / FLOATER / MIDRANGE / THREE | 11.72 / 9.94 / 40.19 / 38.14% | 11.69 / 10.40 / 39.78 / **38.13%** |

`-0.8` was rejected after HELDOUT produced only 37.79% THREE.

## M-W. Canonical final benchmark

Seeds `25000-25099`, exact disabled-before versus selected-after:

| Metric | Before | After | Change |
|---|---:|---:|---:|
| possessions/team | 96.865 | 97.475 | +0.610 |
| FGA/team | 87.295 | 88.975 | +1.680 |
| PTS/team | 98.320 | 100.090 | +1.770 |
| ORtg | 101.502 | 102.683 | +1.181 |
| FG% | 41.629% | 41.585% | -0.044 pp |
| 2P% | 46.156% | 46.347% | +0.191 pp |
| 3P% | 34.476% | 34.017% | -0.459 pp |
| 3PA/team | 33.835 | 34.365 | +0.530 |
| FTA/team | 18.000 | 18.520 | +0.520 |
| PF/team | 13.410 | 13.815 | +0.405 |
| TOV/team | 15.200 | 14.370 | -0.830 |
| BLK/team | 0.960 | 0.990 | +0.030 |
| OREB% | 24.421% | 24.630% | +0.209 pp |
| drives/team | 38.500 | 40.070 | +1.570 |
| drives/100 | 39.746 | 41.108 | +1.362 |
| drive→shot | 36.571% | 36.848% | +0.277 pp |

Shot family:

| Family | Before | After | Change |
|---|---:|---:|---:|
| RIM | 11.477% | 11.639% | +0.162 pp |
| FLOATER | 9.946% | 10.435% | +0.489 pp |
| **combined interior** | **21.422%** | **22.073%** | **+0.651 pp** |
| MIDRANGE | 40.060% | 39.600% | -0.460 pp |
| THREE | 38.518% | **38.327%** | -0.191 pp |

Shot origin:

| Origin | Before share | After share | After family detail |
|---|---:|---:|---|
| ORDINARY | 72.877% | 71.954% | 253 RIM, 209 FLOATER, 6,471 MIDRANGE, 6,631 THREE |
| DRIVE | 15.230% | 15.665% | 572 RIM, 871 FLOATER, 952 MIDRANGE, 558 THREE |
| TRANSITION_PUSH | 7.458% | 7.326% | 863 RIM, 468 FLOATER, 29 MIDRANGE, 21 THREE |
| INTERIOR_CUT | 4.435% | 4.164% | 437 RIM, 325 FLOATER, 13 MIDRANGE, 10 THREE |
| **INTERIOR_SEAL** | 0 | **0.891%** | 69 RIM, 94 FLOATER, 0 MIDRANGE, 5 THREE |

Permanent `INTERIOR_SEAL` funnel: **11,073 offered → 668 selected/pass attempts → 552
successful passes; 65 direct pass turnovers; 69 RIM + 94 FLOATER + 0 MIDRANGE + 5 THREE
immediate downstream shots; 56 immediate downstream shooting fouls.** The diagnostic is read-only,
reconciles to the existing opportunity/action totals, and consumes no RNG.

Natural downstream movement was directionally supportive: FTA +0.52/team, PF +0.405/team,
BLK +0.03/team, and ORtg +1.18 without conversion tuning. Pace, FGA, THREE, drive→shot, TOV,
and OREB% stayed within their guardrails.

## X. Tests

- New focused seal tests cover requirements A-R, including reachability, impossible contexts,
  both destinations, non-guaranteed selection/completion, real failure/turnover, zone mutation only
  after success, downstream shot selection, ability/tendency separation, preserved THREE path,
  and preserved cut/drive/transition/reset behavior.
- Turnover diagnostics now recognize `INTERIOR_SEAL` as the same real pass path and retain exact
  team/player/steal reconciliation.
- Focused integration sweep: **536/536 passing**.
- Full repository suite: **1310/1310 passing** (1298 baseline + 12 focused seal tests).
- `git diff --check`: clean.

## Y-Z. Classification and exact next bottleneck

**NBA-AVERAGE OPPORTUNITY ARCHITECTURE V1 READY is NOT justified.**

The new mechanism is causal and production-reachable, but it does not clear the shot-mix gate.
Canonical MIDRANGE remains **39.60%**, combined interior only **22.07%**, and `INTERIOR_SEAL`
originates just **0.89%** of shots. Ordinary offense still originates **71.95%** of all shots and
splits into 6,471 MIDRANGE versus 6,631 THREE, with only 462 combined interior attempts.

The exact wall is action substitution: a real interior pass must compete with live shots and other
passes. Increasing its weight enough to create the missing ~13-28 percentage points of interior
volume takes too much THREE and eventually pace/FGA, just as the exhausted cut lever did. The
remaining missing basketball concept is therefore not another isolated cut/seal weight. It is a
high-frequency **screen/roll and broader off-ball movement/deployment layer** that creates interior
windows on ordinary possessions before terminal action selection. The smallest next architectural
addition is autonomous lightweight on-ball screen initiation that supplies real `screen_active` and
`roller_id` state to the already-wired `POCKET_PASS` path, with participant/cadence logic and the same
TRAIN/HELDOUT discipline. Do not add a third arbitrary interior action.
