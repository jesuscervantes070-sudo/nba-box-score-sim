# Phase 23B — Chained Possessions / Persistent Game State

## Goal and classification

Build the smallest autonomous multi-possession layer above Phase 23A:
initialize a possession from persistent game state, run
`simulate_possession(...)`, consume its typed terminal packet exactly once,
update game state, derive the next restart, and repeat under an explicit bound.

**Classification: READY WITH FLAGS.** Possessions chain autonomously and all
implemented invariants hold. V0 retains the explicitly accepted flags: scoring
uses a provisional structured bridge while the event schema is incomplete,
lineups are fixed, start geometry is coarse, action durations are uncalibrated,
and transition-specific actions remain capability-gated.

This is not a full game, is not empirically calibrated, and is not routed into
the aggregate/product engine.

## Files

| File | Purpose |
|---|---|
| `detailed_game_orchestrator.py` | Persistent state, restart policy, possession records, invariants, and bounded deterministic runner |
| `test_detailed_game_orchestrator.py` | Phase 23B boundary, integration, replay, and firewall tests |
| `possession_orchestrator.py` | Additive starting-clock/phase inputs plus two Phase 23A chaining bug fixes |
| `test_possession_orchestrator.py` | Regression coverage for unresolved loose-ball ownership |
| `docs/PHASE23B_CHAINED_POSSESSIONS_REPORT.md` | This report |

## Architecture and ownership

`DetailedGameState` owns only state that survives a possession boundary:

- `home_team_id`, `away_team_id`;
- `current_offense_team_id`, `current_defense_team_id`;
- `period`, `game_clock_seconds`;
- `score_home`, `score_away`;
- the existing Phase 21B `FoulAdministrationState` (personal fouls,
  period team fouls, and its idempotence ledger);
- `next_possession_sequence`; and
- `last_terminal_reason`.

`PossessionState` and `PossessionWorld` remain possession-local. They are
reconstructed for every new possession. Only a typed `RestartContext` crosses
the boundary, carrying restart type/source and, when structurally sound, the
live-ball carrier.

Each `PossessionRecord` retains its possession ID, teams, start/end clocks,
start/end scores, restart context, typed terminal result, authoritative event
tuple, and explicitly provisional `StatDeltas`. `MultiPossessionResult.events`
is a derived ordered flattening of those record-owned tuples, not another
mutable event log.

Therefore the hierarchy remains:

1. live game/possession state owns current basketball state;
2. record-owned event streams own accounting direction; and
3. typed terminal/segment results own control flow.

## Initialization and restart policy

`initialize_next_possession(...)` is the single conversion from
`DetailedGameState + fixed lineups + RestartContext` to Phase 23A inputs. It
selects the offense/defense fives, validates the 5v5 identity contract, creates
the monotonic ID `period{period}-possession{sequence}`, chooses the receiver,
and sets the start phase/zone. Callers never construct `StructuralContext` or
matchups; Phase 23A rebuilds those itself.

V0 restart rules:

| Prior terminal | New ownership | Restart |
|---|---|---|
| Made field goal / made final FT | Opponent | Dead-ball inbound |
| Defensive rebound with individual control | Rebounder's team | Live-transition handoff |
| Live-ball turnover with individual control | Recovering team | Live-transition handoff |
| Dead-ball turnover / offensive charge / shot-clock violation | Opponent | Dead-ball inbound |
| Period expiration | No new possession in this period | Segment stops |

An offensive rebound never reaches this table: Phase 23A keeps second chances
inside the same possession, with the same ID and its existing shot-clock reset.

Both dead-ball and live-transition starts use `TOP_OF_KEY` as a deliberate V0
coarse location. Live starts preserve the carrier and `TRANSITION` phase, but
do not carry stale old-orientation geometry or invent a backcourt-advancement
resolver. Transition push/outlet actions remain gated.

## Clock ownership

Phase 23B passes the persistent `game_clock_seconds` into Phase 23A through the
additive `PossessionConfig.initial_game_clock_seconds`. Phase 23A remains the
only action-time consumer. Phase 23B reads
`terminal.engine_state.game_clock_remaining` once and writes that exact value
back; it never subtracts duration. Invariants reject an increased, negative,
missing, or above-period starting clock. A clock at zero returns
`PERIOD_COMPLETE` and no new possession starts.

The focused test with forced 7-second and 4-second possessions proves the
state sequence is exactly `100 → 93 → 89`, not `100 → 86 → 78`.

Shot clock remains entirely possession-local. New possessions use Phase 15's
era-rule reset; offensive rebounds retain Phase 19's in-possession reset.

## Score and event accounting

The Phase 23A event stream still cannot fully derive points: `SHOT_RESOLVED`
lacks shot family/value, free throws have no complete event schema, and the
and-one path is incomplete as an event-only accounting record. Phase 23B does
not parse prose or guess values. It applies `PossessionTerminalResult.stats.points`
once to the offense as a **provisional structured orchestration bridge**.

Before advancing state it independently derives every currently event-backed
field (OREB, DREB, turnovers, steals, blocks, personal fouls) and requires exact
parity with provisional deltas. Scores must be non-negative integers and never
decrease. Closing the shot-value/free-throw/and-one event debt is required
before detailed box-score accounting can be called authoritative.

## Fouls and bonus

The exact Phase 21B `FoulAdministrationState` returned by one possession is
passed into the next. Phase 23B neither detects nor classifies fouls. Shooting
fouls and qualifying defensive floor fouls persist their team-foul increments;
ordinary offensive charges persist the personal foul and turnover but do not
increment any team foul or trigger bonus free throws. Bonus status is derived
with Phase 21B's `effective_bonus_foul_threshold`, not a duplicate rule.

V0 does not advance periods, so it does not invoke the existing
`reset_team_fouls()` period-boundary hook yet.

## Possession IDs, RNG, and replay

Every new team possession consumes one monotonically increasing sequence ID.
Second chances do not. Every event in a record must carry that record's ID.

The bounded runner creates one `random.Random(rng_seed)` stream and draws one
deterministic 64-bit child seed per new possession. Phase 23A owns all draws
within that possession. No system randomness is consulted. Equal initial
state/config/seed replay identically; different seeds are free to diverge.

## Supported and deferred behavior

All Phase 23A terminal reasons are supported: `MADE_FG`, `FINAL_FT_MADE`,
`DEFENSIVE_REBOUND`, `TURNOVER`, `OFFENSIVE_FOUL_TURNOVER`,
`SHOT_CLOCK_VIOLATION`, and `PERIOD_END`. Phase 23A faults and invariant
violations propagate; they are never converted to basketball outcomes.

Still deferred/gated:

- full 48-minute/four-period orchestration and period advancement;
- overtime, substitutions, rotations, minutes, fatigue, injuries, foul-outs,
  coaching, timeouts, intentional fouling, and playbooks;
- real-player ingestion (the Phase 23A profile contract is unchanged);
- true transition advancement/outlet dispatch and orientation-preserving
  cross-possession geometry;
- full event-derived score/box score and unsupported assists;
- detailed-engine accuracy claims; and
- UI, database, season/playoff, aggregate-engine, or public routing.

## Tests

Focused result after implementation:

- Phase 23A: **44/44** (the accepted 43 plus one chaining regression).
- Phase 23B: **28/28**.
- Combined focused: **72/72**.
- Full repository suite: **852/852**.

Coverage includes fixed-lineup/profile invariants; ordinary consecutive
possessions; made-shot, rebound, live/dead turnover, charge, shooting-foul/FT,
and shot-clock/period restart semantics; multiple OREBs under one ID; exact
clock transfer; score/foul persistence; charge team-foul exclusion; bonus
derivation; event ordering/parity; deterministic replay; matchup rebuilding;
step-guard propagation; and the legacy firewall.

## Autonomous diagnostic segment

Seed `23023`, 50-possession maximum, synthetic fixed 5v5 profiles, current V0
configuration:

- completed possessions: **50** (`MAX_POSSESSIONS` stop);
- game clock: **720.0 → 488.9** seconds;
- provisional score: **HOME 25, AWAY 27**;
- terminals: **20 made field goals, 17 defensive rebounds, 13 turnovers**;
- turnovers: **13**;
- offensive rebounds/second chances: **24**;
- personal/team fouls: **0 / 0**;
- free throws: **0/0**;
- maximum Phase 23A steps in one possession: **16**;
- step-guard faults: **0**;
- invariant failures: **0**.

This trace checks orchestration only. No constant was tuned and no realism or
accuracy claim follows from these values.

## Phase 23A bugs found while chaining

1. Made-shot/final-FT/shot-clock/dead-turnover terminal packets could expose
   the prior team IDs—or fail after an FT cleared live ownership—instead of the
   next-possession teams. Terminal ownership now derives from the immutable
   possession team binding, not transient end-state ownership.
2. Generic loose-ball recovery compared the winner against
   `engine.state.offense_team_id` even though pass deflection can set that field
   to `None` while unresolved. It therefore classified an original-offense
   recovery as a defensive recovery/turnover. It now compares against the
   immutable possession offense, with a regression test.

## Exact blockers before Phase 23C

Before a minimal complete detailed game can be honest, Phase 23C must define
period completion/advancement and final game termination, reset period-local
team fouls at the boundary, and define the opening possession for each new
period. Before a detailed full box score or accuracy claim, the event schema
must also close shot-value, free-throw, and and-one accounting debt. Transition
advancement/geometry remains a flagged quality limitation unless explicitly
resolved or kept gated; it must not be silently simulated as a calibrated
mechanic.
