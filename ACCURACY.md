# Design & Accuracy Notes

Technical writeup of how the simulator works and how its accuracy was
measured. The README covers what the project is and how to run it; this
covers the mechanics and the evidence behind the numbers it quotes.

## Core design principle

Every stat in a simulated box score is either sampled directly or
**derived** from other sampled numbers — never both. Specifically:

- A player's points are always `2*(FGM-FG3M) + 3*FG3M + FTM`, computed on
  read, never stored as its own value.
- FGM can never exceed FGA, FG3A can never exceed FGA, OREB can never
  exceed REB. These are enforced structurally — a subset is drawn as a
  split *of* the total (a binomial or Dirichlet-multinomial draw), not
  as an independent number that happens to usually come out smaller.
- Team totals (points, rebounds, assists, etc.) are always the sum of
  that game's player rows, never entered independently.

Real, externally-sourced season stats (a team's real opponent-FG%
allowed, a player's real per-game average) are a different category —
legitimate inputs pulled from the NBA stats API, not violations of the
rule above.

## How a game is generated

This is a box-score generator, not a possession-by-possession
simulation — there's no shot clock or play-by-play. Each simulated game
produces one full, internally-consistent stat line per player, built
from that player's real season tendencies, in roughly this order:

1. **Active roster.** A ~9-man rotation is drawn via a weighted random
   shuffle keyed on real minutes played, not a fixed cutoff — a fixed
   cutoff was tried first and left 47% of the league never playing a
   single simulated game all season.
2. **Minutes.** A fixed 240 team-minutes (5 players × 48) is split
   across the roster via a Dirichlet-multinomial draw — minutes are a
   shared resource, never drawn per player independently.
3. **Team-level counts** (shot attempts, rebounds, assists, steals,
   blocks, turnovers) are drawn team-total-first, then split among
   players — never the reverse. Independent per-player draws were tried
   first and produced unrealistic team totals (229-point games,
   standings that didn't differentiate good and bad teams at all).
4. **Defense is modeled two ways**: steals remove attempts before they
   become a shot attempt; blocks overturn made 2-pointers into misses.
   A team's own shooting blends with its opponent's real
   opponent-FG%-allowed, and a team's own offense is amplified
   symmetrically with how much its opponent's defense is (see
   `OFFENSE_AMPLIFICATION`/`DEFENSE_AMPLIFICATION` below).
5. **Possessions are conserved.** Both teams share one negotiated pace
   and one shared possession count per game, with turnovers and
   offensive rebounds inside that arithmetic — a team that turns the
   ball over really does get fewer shots.
6. **Player averages correct for real availability** — a per-game
   average is measured only over games a player actually played, so
   summing nine averages doesn't describe a team where everyone is
   healthy every night.
7. **Overtime** plays real 5-minute periods, looping until the game
   isn't tied, reusing the same active roster minus anyone fouled out.

### The three random-draw tools

- **Negative binomial** — team-level counts, centered on a real average
  with a tunable dispersion controlling how fat the tail is (so a truly
  historic night is rare but possible, not either impossible or an
  everyday occurrence).
- **Dirichlet-multinomial** — splits a fixed team total across players
  by their real usage share, letting that share wobble game to game.
- **Binomial** — attempts → makes (weighted by real shooting %), and
  splitting a subset out of a total (3PA out of FGA, OREB out of REB)
  using the real ratio as the split's odds.

## Real mechanics, not shortcuts

- **Injuries**: every real absence (including one-game rest nights, ~48%
  of all real absence stints) is turned into a simulated one, anchored
  to roughly when the real absence started, with length randomized
  around the real length.
- **Trades**: a traded player is simulated on both real teams, for the
  real games they actually played on each.
- **Playoffs**: real era-specific rules by season — best-of-5 first
  round before 2003, no play-in before 2019-20, the real one-off 2019-20
  conditional bubble format, the current 7-10 tournament from 2020-21 on.
  Seeding uses the real NBA tiebreaker chain and each season's real
  conference/division alignment.
- **Season awards**: MVP, ROY, DPOY, MIP, and Coach of the Year are each
  a hand-built scoring formula (see below), not a black box.

## Accuracy methodology

Every tunable constant was fit against real data with a strict
**time-based train/holdout split**: constants are chosen using seasons
1996-97 through 2015-16 only, then scored against 2016-17 through
2025-26, which the fit never sees. Results are always reported on the
holdout, not the training set.

**Current standings accuracy** (30 simulated runs per season, all 30
real seasons, 892 team-seasons):

- 5.31 games mean absolute error, 0.88 correlation, on a single
  simulated run.
- Averaging 30 independent runs of the same real season (cancelling
  pure randomness) brings error down to 4.27 — the model's systematic
  accuracy with reporting noise removed.
- The measured ceiling: two independent simulated runs of the *same*
  real season already differ from each other by ~4.4 wins of pure
  randomness. Adding the luck already present in one real 82-game
  season puts the irreducible floor at ~4.7. Standings accuracy is
  close to done; the honest remaining gap is in box-score realism, not
  win totals.
- Baseline for comparison: guessing the league-average win total for
  every team scores 10.11 MAE.

**Key constants**, tuned jointly (each trades against the others):
`DEFENSE_AMPLIFICATION` 1.0, `OFFENSE_AMPLIFICATION` 0.5,
`TURNOVER_POSSESSION_WEIGHT` 1.0 (one turnover costs the opponent
exactly one possession — the physically correct value, not a fudge
factor), `PACE_COUPLING_WEIGHT` 0.75, `ROSTER_AVAILABILITY_WEIGHT` 1.0,
`SHORTHANDED_PENALTY` 0.15.

**Lesson that shaped the whole tuning process**: win totals are
structurally blind to any bias that shifts both teams equally, so
optimizing purely against standings will happily accept a visibly wrong
box score for a small MAE gain. This happened three times — turnover
possession weight, roster availability weight, and a real bug in
opponent-defense filtering — before checking box-score-level stats
(scoring spread, shooting %, turnover-to-scoring correlation) alongside
standings became a standard part of every tuning pass.

## Season awards

Ground truth for MVP/ROY/DPOY/MIP is verified against the real NBA
awards API (`PlayerAwards`) for all 30 seasons — not memory. Coach of
the Year has no equivalent live-API endpoint, so its ground truth is
cross-checked against a current web source instead.

Each formula is backtested with the same time-based train/holdout
split, one script per award, against real winners (hit-rate = formula's
#1 pick matches the real winner; top-3 = real winner in formula's top 3):

| Award | Holdout hit-rate | Holdout top-3 |
|---|---|---|
| MVP | 90% | 100% |
| ROY | 60% | 70% |
| DPOY | 50% | 80% |
| MIP | 40% | 40% |
| Coach of the Year | 67% | 78% |

MVP and ROY combine PIE (NBA's own impact metric), usage%, true-shooting
%, team win% (lightly weighted), and games-played availability. DPOY
weighs blocks heaviest, matching real DPOY history's lean toward rim
protectors, plus steals, rebounds, team defensive strength, and real
rim-deterrence/deflection tracking data. MIP is dominated by raw
per-game scoring increase over the player's own previous season. Coach
of the Year blends this-season win% level with win% improvement — win%
improvement alone scored only ~20%, because real winners' teams rank in
the league's top 3 in 19 of 28 backtestable seasons; adding the level
term alongside the delta is what got it to 67%.

MIP and DPOY are the two closest to a real ceiling for a stats-only
formula: MIP voting is famously narrative-driven, and DPOY voting leans
toward perimeter defenders (Draymond Green, Marcus Smart, Kawhi Leonard)
in ways raw blocks/steals/deterrence data structurally can't fully see.

## Known limitations

- **Team scoring spread is too wide.** Simulated teams differ from each
  other in season scoring average more than real teams do (7.15 vs. a
  real 4.25 standard deviation). Standings can't see this at all —
  wins come from point differential, which stays correct even when
  offense and defense are over-amplified together. Ruled out as a
  simple amplification-constant issue (sweeping the amplification
  constants down barely moves the spread while costing real standings
  accuracy); the actual cause is still open.
- **Possession swing runs ~40% too wide in every era checked**, even
  though the possession *level* is accurate to within ~1.5%. The shared
  pace draw is fed each season's real measured variation, so the excess
  comes from the draws layered on top of it (usage splits, the active
  roster draw, turnover/rebound draws).
- **No offseason simulation.** Every season replays real rosters with
  simulated results — a simulated season's standings don't affect next
  season's draft order or free agency, because that would require
  player progression and aging models this project doesn't have data
  to validate against yet. What *is* built: a report of what actually
  changed between two real seasons for a followed team (arrivals
  labeled drafted vs. signed, departures, franchise moves/renames).
- **No possession-by-possession realism** — no shot clock, no
  play-by-play. Deliberately out of scope; the box-score approach is
  the whole point of the project.

## Files

`game_engine.py` is the simulation core. `data_source.py`/`loader.py`
fetch and cache real data from `nba_api`. `season.py`/`playoffs.py`
simulate a regular season and postseason respectively.
`benchmark_accuracy.py`/`sweep_constants.py` are the tools that produced
the numbers above — every constant's tuning history is preserved in
`sweeps/*.json`, and every season's accuracy snapshot in
`benchmarks/*.json`, so any claim here can be checked against the raw
files rather than taken on faith.
