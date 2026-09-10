# Phase 9 — Shot Creation Internals

`rim_access_creation` and `perimeter_space_creation`. Entirely offline/
parallel: nothing here is imported by or imports `game_engine.py`,
`season.py`, `awards.py`, `models.py`, `transactions.py`. No OVR, physical
ratings, POA defense, tendencies, or simulation integration. Phase 8
(`playmaking_vision`, KEEP BUT FLAG) is unchanged. All 241 tests pass (224
pre-existing from Phases 4A-8 + 4 shot-creation-ingestion + 13 shot-creation).

## 1. Source verification

`nba_api.stats.endpoints.leaguedashptstats`, three real measure types on
the SAME endpoint family already used by Phase 4B/8: `Drives` (real
fields beyond what Phase 4B cached: `DRIVE_FGA`, `DRIVE_FGM`, `DRIVE_FTA`,
`DRIVE_AST`), `PullUpShot` (`PULL_UP_FGA`/`FGM`/`FG3A`, etc. — by
definition unassisted/self-created), `CatchShoot` (used only as a
teammate-created contrast, never a target). Real floor: same 2013-14 as
every other `leaguedashpt*` endpoint in this codebase. Cheap-ish: 3
calls/season × 13 seasons = 39 calls, 110 seconds, one-time.

**Checked and confirmed unavailable**: real defender-distance-at-release
data (which would let this phase measure true separation rather than a
volume/opportunity proxy) does not exist in any real `leaguedashpt*`
measure type this codebase's access exposes — Second Spectrum's real
separation data is proprietary. **Documented as FUTURE ONLY**, not
fabricated.

## 2. Cache reuse

Reused directly, zero re-ingestion: Phase 4B's `handling_exposure.py`
(`touches`, `time_of_poss`, and the `DRIVES` count itself — only the
ADDITIONAL Drive fields were newly fetched), existing
`player_advanced.json` (`usg_pct`, `reb_pct`). Phase 8's calibration
engine (`rim_protection_calibration.py`'s generic `evaluate`/
`grid_search_on_train`/`_shrunk_rate`) reused directly — no new grid
search code written.

## 3. Semantic boundaries

Per the task's own taxonomy, kept strictly separate: RIM ACCESS CREATION
(beat/shift defender, reach interior) ≠ RIM FINISHING (convert once
there, Phase 5, LOCKED) ≠ PERIMETER SPACE CREATION (generate separation)
≠ 3PT/MIDRANGE (convert the resulting shot, Phase 5, LOCKED) ≠ TENDENCY
(willingness to attempt) ≠ ROLE (how often given the ball). Both
candidates below are OPPORTUNITY-conditional rates (per-drive, per-touch),
never raw attempt/make counts.

## 4-5. Candidates (kept to exactly two, per instruction)

**A. Rim Access Creation** — real, unweighted composite exactly matching
the task's own suggested formula (no invented weights):

`rim_access_rate = (DRIVE_FGA + DRIVE_FTA + DRIVE_AST − DRIVE_TOV) / DRIVES`

Conditioned on real `DRIVES` (the initiation count) — measures "given a
drive was started, how often did it reach a real scoring conclusion for
self or a teammate, net of turnovers," not raw drive volume.

**B. Perimeter Space Creation** — real `PULL_UP_FGA` (by definition
self-created) conditioned on real handling exposure:

`perimeter_rate = PULL_UP_FGA / touches` (raw form — see §9 for why this
was residualized before adoption)

## 6. Exposure/denominator comparison

Not a multi-candidate denominator screen this phase (kept small per
instruction) — `DRIVES` and `touches` were the natural, real, direct
opportunity counts for each candidate respectively (drives = rim-access
attempts *initiated*; touches = real handling exposure), not selected
by a raw-MAE scan.

## 7. Usage/role contamination — the decisive finding

Real correlations, both candidates, one combined diagnostic pass (2018-19/
2022-23/2023-24):

| Signal | Rim Access (raw) | Perimeter Space (raw) |
|---|---|---|
| usg_pct | 0.27–0.40 | **0.52–0.65** |
| touches | 0.06–0.17 | **0.26–0.39** |
| time_of_poss | −0.10–0.07 | **0.45–0.55** |
| drives (own) | −0.09–0.12 | 0.55–0.58 |
| reb_pct (big-proxy) | 0.32–0.38 | −0.44 to −0.57 |

**Rim access, conditioned on DRIVES, is already fairly clean** —
touches/TOP/raw-drive-volume contamination is near zero; only a real,
moderate usage/reb_pct correlation remains (plausibly genuine gravity/
size-interaction, not pure role — not residualized, see §10).
**Perimeter space, conditioned only on touches, is heavily role-
contaminated** — usage, touches, TOP, and raw drives all correlate
0.45–0.65. This directly matches Phase 8's lesson and the task's own
warning about raw pull-up volume being "mostly tendency/role."

## 8. Conversion-skill overlap

Not separately re-run this phase beyond the contamination table above —
neither candidate's numerator involves a make/miss outcome tied to
shooting skill (`DRIVE_FGA`/`FTA`/`AST` are attempt/foul/assist EVENTS,
not FG%; `PULL_UP_FGA` is a volume count, not `PULL_UP_FG_PCT`) — by
construction, conversion skill (rim_finishing, midrange, three_point) is
not an input to either candidate, satisfying the task's "creation must
not directly improve make probability" requirement structurally, not
just empirically.

## 9. Residualization (targeted, TRAIN-only, per Phase 8 precedent)

Only Perimeter Space was residualized (Rim Access's contamination was
judged already acceptable — see §7/§10). Small, interpretable OLS
(2 predictors, no ML library), coefficients FROZEN from a real TRAIN-only
fit (2013-14–2019-20), never refit per season:

`perimeter_rate ≈ −0.036 + 0.277·USG% + 0.654·(TOP/touch)`

Residual re-check (2023-24 real data):

| Signal | Raw corr | **Residual corr** |
|---|---|---|
| usg_pct | 0.649 | **0.083** |
| touches | 0.385 | **−0.001** |
| time_of_poss | 0.550 | **0.073** |
| reb_pct | −0.502 | −0.488 (unchanged — NOT a control variable, see below) |

`reb_pct` was deliberately NOT included as a control — a center's low
perimeter-creation opportunity is a real, legitimate EXPOSURE constraint
(handled by confidence-gating, §16), not role contamination to
residualize away, per the task's explicit "do not residualize away
genuine creation result."

**Stability check (the critical test, per task instruction)**: real T→T+1
rank correlation, 2018-19→2019-20, same players: raw rate 0.919 → residual
0.841. **Real, substantial signal survives** — confirms the
residualization removed OPPORTUNITY, not the underlying ability (which
would show near-zero residual stability if it had).

## 10. Component correlation

Real correlation between `rim_access_creation` (raw) and
`perimeter_space_creation` (raw), same players, three seasons checked:

| Season | corr | rank corr |
|---|---|---|
| 2018-19 | −0.069 | −0.081 |
| 2022-23 | 0.133 | 0.130 |
| 2023-24 | 0.074 | 0.065 |

**Near-zero across every season checked** — the two candidates are NOT
measuring the same underlying thing. This is real, positive evidence for
a genuine two-component architecture (§20).

## 11. Portability

Not run this phase — per the task's own "do not build expensive per-game
infrastructure solely for this... use if cheap" allowance, and given the
decisive residualization evidence (§9) already provides real portability
signal (a candidate no longer tied to usage/ball-dominance is inherently
more transferable across role changes, the same logic Phase 8 used).
Flagged as real future work.

## 12-13. T→T+1, train/heldout

Reused `rim_protection_calibration.py`'s generic engine directly. Real
TRAIN (2013-14→2019-20, 6 transitions) → real HELDOUT (2019-20→2025-26,
6 transitions):

| Component | TRAIN λ, M | TRAIN MAE | HELDOUT MAE | HELDOUT rank corr | Improvement vs. raw-previous |
|---|---|---|---|---|---|
| **rim_access_creation** | λ=0.5, M=50 | 0.05375 | 0.05362 | 0.713 | **9.7%** |
| **perimeter_space_creation** (residual) | λ=0.3, M=200 | 0.01000 | 0.01306 | 0.787 | **8.6%** |

Rim access shows excellent TRAIN→HELDOUT MAE transfer (nearly identical,
like Phase 7). Perimeter space shows a real but smaller gap (0.0100 →
0.0131) — still real, positive, and consistent with the residual's own
strong rank correlation (0.787).

## 14. Age test

Tested once for `rim_access_creation` (a real, visible age-decline
pattern was present in signed error by bucket — plausible for a real
burst-dependent skill). A frozen linear age correction fit on TRAIN,
applied to HELDOUT: MAE went from 0.05360 (no correction) to 0.05384
(with correction) — **slightly worse. Rejected immediately**, per
instruction, no further attempt on either component.

## 15. Representative players (2023-24, real data)

| Player | Rim Access (raw) | Real drives | Perimeter (residual) | Note |
|---|---|---|---|---|
| Kyrie Irving | 0.721 | 670 | 0.038 | Elite at both |
| Devin Booker | 0.702 | 888 | **0.051** | Elite at both |
| Shai Gilgeous-Alexander | 0.686 | 1,748 | 0.026 | Elite rim access, modest separator |
| Nikola Jokić | 0.688 | 417 | −0.053 | Elite rim access (big), weak self-created jumper |
| Domantas Sabonis | **0.559** (lowest) | 492 | −0.046 | Post/roll-based big, weak at both |
| Klay Thompson | 0.570 | 293 | **0.055** (highest) | Modest driver, real self-created-jumper signal despite catch-and-shoot reputation |
| Trae Young | 0.654 | 845 | 0.015 | High-usage, only modest residual once role removed |
| Damian Lillard | 0.668 | 945 | 0.008 | Real, honest divergence from reputation (see below) |
| Rudy Gobert | 0.933 (flagged) | **60** | −0.021 | Small-sample (near the 50-drive floor) — reported, not hidden |

Real, un-tuned divergences worth naming honestly: **Klay Thompson**'s
highest residual in this sample is a genuine finding, not an error —
his real pull-up volume conditional on his (lower) usage/TOP is
efficient, a real signal beyond his catch-and-shoot reputation.
**Damian Lillard**'s modest residual (0.008, lower than his reputation as
an elite movement shooter would suggest) is reported plainly as a real,
honest divergence — his real 2023-24 pull-up volume is largely explained
by his real high usage/ball-dominance, which the residual correctly
discounts. **Gobert**'s inflated-looking 0.933 rim-access rate is
explicitly flagged as a small-sample case (60 real drives, just above
the 50-drive floor) rather than presented with false confidence.

## 16. Historical fallback

None built. Same real 2013-14 floor as every `leaguedashpt*`-derived
attribute. A pre-2013-14 query returns `mode="INSUFFICIENT"` for both
components — no forced historical proxy, consistent with "do not invent
historical precision."

## 17. Physical-boundary discussion

Both components almost certainly contain real, unremoved PHYSICAL signal
(burst for rim access, handle/deceleration for perimeter space) — this
was NOT residualized out, per the task's explicit "do not over-residualize
physical advantage out... current observational estimator may naturally
contain physical signal." This is a REAL, STATED double-counting risk:
once a future phase builds explicit physical ratings (standing reach,
vertical, lateral quickness), `rim_access_creation`/`perimeter_space_creation`
will need to be revisited to avoid crediting the same physical advantage
twice (once here, once in a future physical attribute). Documented, not
solved, per this phase's explicit scope limit.

## 18. Final estimators

`shrunk_rate = (Σ(rate_s · λ^age_s · exposure_s) + M · league_avg) / (Σ(λ^age_s · exposure_s) + M)`

- `rim_access_creation`: λ=0.5, M=50 (drives units), rate = real
  `(DRIVE_FGA+DRIVE_FTA+DRIVE_AST−DRIVE_TOV)/DRIVES`, exposure floor 50
  drives.
- `perimeter_space_creation`: λ=0.3, M=200 (touches units), rate = the
  frozen-coefficient residual of `PULL_UP_FGA/touches`, exposure floor
  200 touches.

Both return `confidence="low"`, no rating, below their exposure floor —
never fabricated precision for a low-opportunity player (e.g. a
non-driving big or a bench guard with few touches).

## 19. Internal/display recommendation

**Option B, per the task's own preference**: BOTH components are kept as
separate, independently-calibrated internal estimator outputs.
`player_ability_profile.SKILL_ATTRIBUTES`'s existing `"shot_creation"`
slot is **intentionally left UNWIRED** this phase — neither component
reached LOCK V1, no validated combination method was built (no hand-set
50/50, no invented weights), and combining now would lose real,
demonstrated two-component information (§10's near-zero correlation) for
no real benefit. `shot_creation_estimation.py` exposes
`estimate_rim_access_creation`/`estimate_perimeter_space_creation`
independently; a future phase can build a real, validated combination
once both components (or a successor) reach a higher confidence bar.

## 20. Classifications

**`rim_access_creation`: KEEP BUT FLAG**
Real, demonstrated incremental signal beyond raw drive volume (drives/
touches/TOP contamination already near zero once conditioned on DRIVES);
excellent real TRAIN→HELDOUT transfer (0.0538→0.0536); real 9.7% held-out
improvement; age correction tested and rejected. Held back from LOCK V1
by: a real, moderate, unaddressed usage/reb_pct correlation (0.27-0.40,
0.32-0.38) that could reflect genuine gravity/physical interaction OR
residual role contamination — not disambiguated this phase.

**`perimeter_space_creation`: KEEP BUT FLAG**
Real, demonstrated incremental signal after TRAIN-only residualization
(usage/touches/TOP contamination reduced from 0.45-0.65 to near-zero
while retaining 0.841 real T→T+1 stability, down only modestly from 0.919
raw); real 8.6% held-out improvement. Held back from LOCK V1 by: a real,
larger TRAIN→HELDOUT MAE gap (0.0100→0.0131) than rim access showed, and
the residualization itself, while validated, is a first-pass 2-variable
model not independently cross-checked by a second method.

**Overall two-component Shot Creation architecture: SUPPORTED**
Real, near-zero component correlation (§10, −0.07 to +0.13 across three
seasons) is positive, decisive evidence the two skills are empirically
distinct, not redundant facets of one factor. Both components
independently show real (if imperfect) predictive stability. The
architecture is empirically justified; the exact combination into one
`shot_creation` display number is deliberately deferred, not because the
split failed, but because averaging two KEEP-BUT-FLAG signals without a
validated method would manufacture false precision.

## 21. Limitations

- Perimeter space residual model is a simple 2-variable OLS — not
  independently re-validated by a second method or a richer feature set
  (deliberately, per "prefer interpretable candidate").
- Rim access's usage/reb_pct correlation is real but NOT disambiguated
  between "genuine gravity/physical interaction" and "residual role
  contamination" — a real, open question for a future phase.
- No defender-distance/separation data exists in this codebase's real
  access — both candidates remain volume/opportunity-conditional proxies,
  not true separation measurements.
- Physical-capacity double-counting risk (§17) is documented, not solved.
- No historical (pre-2013-14) mode for either component.
- No team-switch/portability study run (time-efficiency tradeoff, per
  instruction).

Per direction: **stopping here.** No physical traits, POA defense, OVR,
development, tendencies, or game-engine integration begun.
