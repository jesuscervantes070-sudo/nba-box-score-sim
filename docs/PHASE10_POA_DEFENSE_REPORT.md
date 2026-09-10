# Phase 10 — Point-of-Attack / Perimeter Defense

`poa_containment`. Entirely offline/parallel: nothing here is imported by
or imports `game_engine.py`, `season.py`, `awards.py`, `models.py`,
`transactions.py`. No physical ratings, screen-navigation rating,
possession-engine integration, or OVR. Phase 9 (`rim_access_creation`/
`perimeter_space_creation`, both KEEP BUT FLAG) is unchanged. All 256
tests pass (241 pre-existing from Phases 4A-9 + 5 poa-ingestion + 10 poa).

## 1. Source verification

`nba_api.stats.endpoints.leagueseasonmatchups` — a REAL, first-party
player-vs-player matchup dataset, checked directly: **one call returns
the ENTIRE league's matchup data** (2023-24: 137,763 real rows, 0.8s) —
not a per-team or per-player loop. Real, verified columns:
`OFF_PLAYER_ID/NAME`, `DEF_PLAYER_ID/NAME`, `PARTIAL_POSS` (real
fractional matchup possessions), `MATCHUP_FGM/FGA/FG_PCT` (opponent
shooting specifically while THIS defender was the PRIMARY matchup —
structurally excludes help, which is separately reported as
`HELP_FGM/FGA/PERC` on the same row), `MATCHUP_AST/TOV/BLK`,
`MATCHUP_FG3M/FG3A/PCT`, `MATCHUP_FTM/FTA`, `SFL`, `MATCHUP_TIME_SEC`
(real numeric seconds — `MATCHUP_MIN` is a string, not used).

Real floor, checked directly: 2015-16 and earlier return 0 rows; 2016-17
returns only 3,515 rows (a real PARTIAL rollout — same pattern this
codebase already documents for hustle stats' own partial year); 2017-18
returns a full 132,489. **2017-18 adopted as the reliable floor**; 2016-17
flagged unreliable, not used.

## 2. Cache reuse

Reused directly: `loader.load_teams`/`player_advanced.json` (real box
FG%, usg_pct, reb_pct, STL), Phase 6's `foul_ingestion.py` cache (foul
cross-check), Phase 7's `rim_protection_ingestion.load_team_opp_rim_frequency`
(team-scheme cross-check, reused verbatim). Only the matchup aggregation
itself was new. Reused `rim_protection_calibration.py`'s generic
grid-search engine directly — no new calibration code.

## 3. Semantic definition

Per the task's taxonomy, kept separate: POA CONTAINMENT (staying
attached, resisting penetration) ≠ B. Defensive Playmaking (steals/
blocks, existing attribute) ≠ C. Rim Protection (existing, LOCKED) ≠ D.
Foul Discipline (existing) ≠ E. Physical Lateral Agility (not built) ≠ F.
Team Scheme (investigated as contamination, not corrected) ≠ G. Matchup
Difficulty (handled via real opponent-quality adjustment, §7).

## 4. Matchup-data coverage

9 real seasons (2017-18–2025-26), ~530–605 defenders/season, 8 real T→T+1
transitions — ingested in 28 seconds total (one call/season).

## 5. Candidate metric (kept to exactly one, per instruction)

`containment_rate = (total_expected_fgm − total_matchup_fgm) / expected_fga_covered`

`total_expected_fgm` = Σ(each real opponent's OWN real season FG% ×
matchup FGA against this defender) — a REAL, opponent-quality-adjusted
baseline (each shooter's actual season rate, not a league average or a
hand-written star discount), computed once at ingestion from already-
cached box stats. Higher = better (opponent shoots below their own real
rate against this primary defender). `MATCHUP_TOV` and `SFL` are
deliberately EXCLUDED from the numerator (see §8/§9).

## 6. Drive/rim-access suppression analysis

Not built — checked directly, no real per-defender "opponent drives
while primary defender" or "blow-by" field exists in
`leagueseasonmatchups` or any other real endpoint this codebase's access
exposes. **Documented as FUTURE ONLY**, not fabricated from `MATCHUP_FGA`
alone (which would conflate a contested pull-up jumper with a
penetration failure).

## 7. Opponent-quality adjustment

Built directly into the candidate itself (§5) — NOT a separate
residualization step. Real, per-shooter baseline, no invented weights,
no star-player hand-tuning.

## 8. Steals overlap — critical, decisive test

Real correlation, containment rate vs. real STL/36 (`defensive_playmaking`'s
own raw input), three seasons checked: **2018-19: 0.019, 2022-23: −0.022,
2023-24: 0.035** — essentially ZERO. **This candidate is clearly NOT
rediscovering steals** — a direct, clean pass of the task's own explicit
"if candidate ≈ steals: REVISIT" test.

## 9. Foul interaction

Real correlation, containment rate vs. real `SFL`-rate per matchup
possession: 2018-19: 0.163, 2022-23: 0.064, 2023-24: 0.021 — small,
positive, and DECREASING over time. No meaningful evidence that apparent
containment is achieved via illegal contact.

## 10. Scheme/team contamination

Real correlation with team-level context, three seasons:

| Signal | 2018-19 | 2022-23 | 2023-24 |
|---|---|---|---|
| team defensive FG% allowed | −0.307 | −0.275 | **−0.386** |
| team opponent rim-attempt freq | −0.132 | −0.051 | −0.231 |

**A real, moderate-to-substantial team-context correlation** (−0.28 to
−0.39 with team defensive quality) — LARGER than Phase 7's rim-protection
team-context check (−0.11 to −0.22). Playing on a genuinely good defense
correlates meaningfully with better individual containment numbers. Not
corrected (per instruction: "do not arbitrarily correct unless evidence
supports it" — the evidence here supports FLAGGING, not a specific
correction formula).

## 11. Role/matchup burden bias

Real correlation vs. usg_pct: essentially zero (−0.04 to 0.00, three
seasons) — no usage contamination at all. Real correlation vs. reb_pct
(big-proxy): moderate positive (0.19–0.36) — bigs show somewhat better
containment numbers, plausibly reflecting real, favorable size mismatches
in their rare primary-perimeter-matchup situations, OR a real sample-
selection artifact (a big's occasional primary perimeter assignment may
be a low-leverage late-clock switch) — **not disambiguated this phase**,
reported as an open question.

## 12. Archetype diagnostics (2023-24, real data)

| Player | Containment rate | Note |
|---|---|---|
| Marcus Smart | **+0.069** | Elite/versatile reputation defender — matches |
| Herbert Jones | +0.029 | Real, well-regarded perimeter stopper — matches |
| Kevin Durant | +0.029 | Real, honestly underrated defensive length |
| Alex Caruso | +0.020 | Matches reputation |
| Jrue Holiday | +0.016 | Modest positive |
| Domantas Sabonis | +0.003 | Near-neutral big |
| Klay Thompson | +0.013 | Modest |
| Nikola Jokić | −0.024 | Negative, matches (not known for POA defense) |
| Fred VanVleet | −0.013 | Negative |
| Dejounte Murray | −0.036 | Negative |
| De'Aaron Fox | −0.036 | Negative |

Real, sensible spread across reputations without any tuning.

## 13. Portability

Not run this phase — per the "do not build expensive new infrastructure
solely for this" allowance, and given the calibration evidence (§15)
already gives a real portability signal via TRAIN→HELDOUT transfer.

## 14. T→T+1 stability — an honest, weaker result than prior phases

Real rank correlation, same players, three consecutive-pair checks:
**2018-19→2019-20: 0.374, 2022-23→2023-24: 0.278, 2023-24→2024-25: 0.218**.
**Real, positive, clearly above zero/noise — but MODEST, and weaker than
every prior defensive/creation attribute in this project** (rim_protection:
0.46–0.59; shot creation: 0.71–0.79). Reported plainly, not smoothed over.

## 15. Train/heldout

Real TRAIN (2017-18→2021-22, 4 transitions) → real HELDOUT (2021-22→2025-26,
4 transitions), reusing the generic engine directly:

| Split | λ, M | Weighted MAE |
|---|---|---|
| TRAIN | λ=0.6, M=800 | 0.02069 |
| **HELDOUT (train params applied)** | (same) | **0.02006** |

Excellent TRAIN→HELDOUT transfer (heldout even marginally better).
HELDOUT rank correlation: 0.386 (consistent with §14's direct checks).
**Improvement vs. raw-previous-season: 21.5%** — a real, substantial gain,
despite the modest raw year-to-year stability (shrinkage does real,
meaningful work here because the single-season rate is genuinely noisy).

## 16. Age test

Tested once. Frozen linear correction (TRAIN-fit) applied to HELDOUT:
MAE went from 0.02012 (no correction) to 0.02023 (with correction) —
**slightly worse. Rejected immediately**, no further attempt.

## 17. Historical fallback

None built. Real matchup-data floor is 2017-18; a pre-2017-18 query
returns `mode="INSUFFICIENT"`, no forced rating — consistent with "do
not force historical precision."

## 18. Physical-boundary discussion

Real, unremoved lateral-agility/length signal almost certainly lives
inside this observational estimate (a longer/quicker defender will
naturally suppress FG% better) — NOT residualized out, per the task's
explicit "do not residualize physical advantages away just because
physical traits will exist later." Documented as a real future
double-counting risk: once lateral agility/length are built as explicit
physical ratings, `poa_containment` will need revisiting to avoid
crediting the same physical advantage twice.

## 19. Final estimator

`shrunk_rate = (Σ(rate_s · λ^age_s · exposure_s) + M · league_avg) / (Σ(λ^age_s · exposure_s) + M)`,
λ=0.6, M=800 (in `expected_fga_covered` units), rate = real, opponent-
quality-adjusted matchup FG% suppression. Below a 100-FGA-covered floor:
`confidence="low"`, no rating — never fabricated precision for a player
with too few credible primary-perimeter-matchup possessions (e.g. a
center rarely switched onto guards).

## 20. Limitations

- Real, moderate team-scheme contamination (−0.28 to −0.39 with team
  defensive quality) is larger than any prior defensive attribute in
  this project and is NOT corrected — a genuine, open concern.
- T→T+1 raw stability (0.22–0.37) is real but the weakest of any phase
  so far — shrinkage compensates in the held-out MAE sense, but the
  underlying year-to-year signal is noisier than rim protection, shot
  creation, or playmaking vision.
- reb_pct (big) correlation (0.19–0.36) is not disambiguated between
  genuine favorable-mismatch signal and sample-selection artifact.
- No real drive/blow-by/penetration field exists — `MATCHUP_FGA`
  suppression is the best available proxy, not a direct measure of
  "staying attached."
- No historical (pre-2017-18) mode.
- Physical (lateral agility/length) double-counting risk documented,
  not solved.

## 21. Final classification: **KEEP BUT FLAG**

Real, decisive evidence this is NOT steals (§8, ~0 correlation) and NOT
usage (§11, ~0 correlation) — clears the task's two most critical
rejection tests cleanly. Real, substantial held-out improvement (21.5%)
with excellent TRAIN→HELDOUT parameter transfer (§15). Held back from
LOCK V1 by: (1) real, moderate-to-substantial team-scheme contamination
larger than any prior defensive attribute (§10); (2) genuinely weaker
raw year-to-year stability than every other attribute built in this
project (§14); (3) the real drive/penetration mechanism the task most
wanted (mechanism-level containment, not just outcome-level FG%
suppression) is confirmed unavailable in real public data (§6) —
`poa_containment` measures a real, valid, but outcome-level proxy, not
the full mechanical concept. This matches the task's own explicit KEEP
BUT FLAG criteria ("modern signal useful, scheme/matchup contamination
remains") precisely.

Wired into the EXISTING `perimeter_defense` `SKILL_ATTRIBUTES` slot (no
schema expansion) — conceptually documented as POA containment/
perimeter defense, per the task's own instruction. Not touched:
simulation, OVR, physical traits, screen-navigation, possession engine.

Per direction: **stopping here.** No physical ratings, tendencies, OVR,
possession-engine integration, development, or screen-navigation
attribute begun.
