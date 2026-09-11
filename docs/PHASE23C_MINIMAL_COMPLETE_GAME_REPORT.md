# Phase 23C — Minimal Complete Detailed Game

## Goal and classification

Build the first autonomous complete-game wrapper above the accepted Phase
23A/23B detailed engine: initialize a game, run all four regulation periods,
advance/reset period state, enter bounded overtime when tied, and return one
typed final result without caller glue.

**Classification: READY WITH FLAGS.** Complete regulation and overtime games
now terminate autonomously and deterministically. The flags are material:
fixed lineups, provisional score accounting, incomplete event box-score
schema, coarse transition starts, uncalibrated durations/mechanics, and highly
unrealistic synthetic diagnostics. This is orchestration completeness, not
production readiness or statistical validity.

## Files

| File | Purpose |
|---|---|
| `detailed_game.py` | Complete-game configuration, initialization, period/OT lifecycle, safety guards, typed result, and provisional summary |
| `test_detailed_game.py` | Phase 23C lifecycle, overtime, reset, guard, replay, and firewall tests |
| `possession_orchestrator.py` | Tiny additive `is_overtime` context passed to Phase 21B floor-foul administration |
| `detailed_game_orchestrator.py` | Phase 23B bonus query now accepts the existing Phase 21B overtime context |
| `docs/PHASE23C_MINIMAL_COMPLETE_GAME_REPORT.md` | This report |

No legacy/product/top-level-doctrine file changed.

## Architecture and state ownership

The established hierarchy remains intact:

1. `DetailedGameState`, then the active Phase 23A possession state/world, own
   current basketball state.
2. Each `PossessionRecord.events` tuple owns accounting direction;
   `DetailedGameResult.events` is only a derived flattening of those tuples.
3. Typed possession, segment, and game results own orchestration/control flow.
4. `StatDeltas` remain a provisional bridge only where events are incomplete.

Phase 23C does not reimplement possession mechanics. It invokes Phase 23B's
`simulate_possessions(...)` once per period and consumes the typed segment
result. Phase 23B continues to own possession initialization, matchups,
ownership flips, clocks within a period, and event/stat cross-checks.

## Public detailed-game API

```python
simulate_detailed_game(
    home_team_id,
    away_team_id,
    home_five,
    away_five,
    profiles,
    rng_seed,
    config=None,
) -> DetailedGameResult
```

`DetailedGameConfig` owns the four-period/clock policy, overtime clock and
guard, per-period/per-game possession guards, explicit optional opening
offense, and the existing `PossessionConfig` (including era/shot-clock rules).
Defaults are four 720-second regulation periods and 300-second overtimes.

`DetailedGameResult` is intentionally separate from the legacy `GameResult`.
It contains final state, regulation/overtime counts, ordered possession and
period records, termination reason, replay seed, and a clearly provisional
aggregate summary. Final scores/team IDs/total possessions/events are derived
properties, avoiding duplicate mutable authority.

## Opening and period policy

No jump ball is simulated. V0 defaults the Q1 opener to the home team unless
`opening_offense_team_id` explicitly selects home or away. The Q1 opener starts
odd-numbered periods and the opponent starts even-numbered periods. Overtimes
continue the same absolute-period alternation. The policy is deterministic and
does not depend on the previous period's terminal possession.

At a zero period clock, `advance_to_next_period(...)` alone:

- increments the period;
- resets the clock to 720 seconds in regulation or 300 seconds in OT (or the
  explicitly configured diagnostic lengths);
- applies the deterministic period opener;
- resets period team fouls through Phase 21B's `reset_team_fouls()`;
- preserves personal fouls and the foul-event idempotence ledger; and
- clears possession-local restart context by beginning a fresh dead-ball
  `PERIOD_START` possession.

It refuses to advance a live period. No possession begins after a period has
expired.

## Overtime and bonus policy

A tied fourth quarter advances to a five-minute overtime. A tied overtime
advances to another until the score differs. `max_overtimes` is a hard guard;
a tie at that limit raises `DetailedGameSimulationFault(MAX_OVERTIMES)` and no
fake score or sudden death is invented.

Each overtime resets team fouls. The game wrapper passes `is_overtime=True`
through Phase 23B/23A into Phase 21B administration, so an explicitly
configured `EraRules.overtime_bonus_foul_threshold` is used. Current rule
objects without a distinct OT threshold retain Phase 21B's documented fallback
to the regulation threshold.

## RNG and replay

One top-level `random.Random(rng_seed)` emits one deterministic period-segment
seed. Phase 23B then emits deterministic possession child seeds, and Phase 23A
owns intra-possession draws. The opening policy itself uses no randomness.
Equal inputs and seed reproduce periods, possession IDs, terminal sequence,
score, clocks, and final result; aggregate-engine seed equivalence is neither
implemented nor required.

## Termination and fault guards

A game is final only when four regulation periods have completed and the score
differs, or when a completed overtime produces a non-tie. Termination is typed
as `REGULATION_FINAL` or `OVERTIME_FINAL`.

The wrapper raises structured faults for:

- `MAX_POSSESSIONS_PER_PERIOD` with a live period clock;
- `MAX_POSSESSIONS_PER_GAME` before a valid final;
- `MAX_OVERTIMES` while tied; and
- an invalid Phase 23B period result.

Phase 23A possession step-guard faults and Phase 23B invariant failures
propagate unchanged. No guard is translated into a basketball outcome or a
truncated “final” game.

## Score and accounting limitation

Phase 23C preserves Phase 23B's exact accounting posture. Persistent score and
the provisional game summary use typed `StatDeltas` because the event schema
still cannot fully derive points, shot family/value, every FGA/FGM branch,
3PA/3PM, FTA/FTM, or and-one accounting. Nothing parses human-readable event
descriptions.

OREB, DREB, turnovers, steals, blocks, and personal fouls remain event-derived
and cross-checked at every possession by Phase 23B. A detailed box score and
statistical accuracy claim remain explicitly non-authoritative until the event
schema debt closes.

## Fixed-lineup and deferred behavior

The same five canonical profiles per team play every possession. There are no
substitutions, rotations, minutes, fatigue, injuries, foul-outs, timeouts,
coaching, playbooks, intentional fouling, or late-game tactics. Real-player
ingestion is not added. Transition advancement/outlets remain gated and
cross-possession geometry is coarse. There is no UI/PBP presentation,
database, season/playoff routing, legacy replacement, calibration, historical
rules expansion, or Phase 24 work.

## Tests

Phase 23C adds **24/24** tests. The combined focused detailed-engine suite is
**96/96** (44 Phase 23A + 28 Phase 23B + 24 Phase 23C).
The full repository suite is **876/876**.

Coverage includes:

- authoritative period-one initialization and explicit opener override;
- automatic Q1→Q2, halftime Q2→Q3, and Q3→Q4 boundaries;
- exact regulation/OT clocks and refusal to advance a live period;
- deterministic alternating period openers;
- complete regulation, single-OT, and double-OT games;
- structured max-OT/per-period/per-game possession faults;
- global possession-sequence monotonicity and score/team association;
- team-foul reset with persistent personal fouls and charge exclusion;
- Phase 21B overtime bonus-context propagation;
- typed final-result consistency and derived event view;
- Phase 23A fault propagation;
- real Phase 23A/23B complete-game replay and matchup validity; and
- legacy/manual-glue firewalls.

## Primary autonomous diagnostic

Untuned synthetic fixed lineups, seed `23024`, default four 720-second periods:

- final: **HOME 463, AWAY 400** (`REGULATION_FINAL`);
- regulation periods / overtimes: **4 / 0**;
- total possessions: **681** (HOME 340, AWAY 341);
- terminals: **300 MADE_FG, 199 DEFENSIVE_REBOUND, 174 TURNOVER,
  5 FINAL_FT_MADE, 3 PERIOD_END**;
- turnovers: **174**;
- OREB / DREB: **353 / 199**;
- personal fouls: **18**;
- FT: **24/32**;
- provisional period scores: **Q1 97-106, Q2 109-79, Q3 114-108,
  Q4 143-107**;
- maximum steps in one possession: **31**;
- faults / invariant failures: **0 / 0**.

## Ten-game diagnostic sample

Seeds `23024`-`23033`, identical untuned synthetic inputs:

- completed: **10/10**, faults: **0**;
- mean provisional score: **HOME 444.4, AWAY 429.3**;
- mean possessions: **672.3**, range **641-710**;
- overtime games: **1/10**;
- mean OREB: **327.3**, or **0.487 per possession**;
- mean personal fouls: **16.3**, with fouls in **10/10** games;
- mean FTA: **34.4**, with FTs in **10/10** games.

The score mean above is computed from the recorded sample rows in the
diagnostic output; it is descriptive only and carries the same provisional
score caveat.

## Structural observations and blockers

Foul and FT branches are naturally reachable over complete games; the earlier
50-possession zero-foul sample was not evidence that those paths were dead.
The abnormal offensive-rebound volume emphatically persists: nearly one OREB
per two possessions in the ten-game sample. Pace and scores are also wildly
high. No constants were changed in response.

Before statistical calibration or validation:

1. add structured shot-family/value and complete FT/and-one events so score and
   shooting totals are event-derived;
2. build the cutoff/provenance-preserving real-player adapter required by the
   Phase 23A contract;
3. establish empirical datasets/targets separately for action choice,
   duration/pace, shooting, turnover, rebound, foul, and transition mechanics;
4. replace or validate coarse transition/start geometry and the current
   all-perimeter-shots-as-three V0 simplification; and
5. define evaluation protocols separately from the fast aggregate engine.

Richer gameplay additionally requires substitutions/rotations/minutes,
fatigue/health/foul-outs, coaching/tactics, timeouts, and late-game behavior,
but those are not disguised as calibration work in this phase.
