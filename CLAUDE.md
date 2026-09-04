# NBA Box-Score Simulator — Project Context

## What this is
A Python project that simulates NBA games and full seasons by generating
realistic box scores per player, built up from real per-game stats pulled
live from the NBA stats API. End goal: run a full season, then compare
simulated season averages/standings against real ones to check accuracy.

This is NOT a possession-by-possession sim (no shot clock, no play-by-play
logic) — deliberately deferred, low priority per the user. It's a
"box-score generator": each simulated game produces a full,
internally-consistent stat line for every player, derived from their real
season tendencies, and a team's own real defense now genuinely affects
their opponents' shooting (see game_engine.py).

## Status: Milestones 1 and 2 are BUILT, TESTED, and on GitHub
- **Milestone 1** (single-game box-score sim): complete.
- **Milestone 2** (season sim + standings + real-vs-simulated comparison):
  complete. Playable via `main.py`.
- **Not yet built**: playoffs, injuries (deferred on purpose — see below),
  possession-by-possession realism (deliberately low priority).
- Repo is on GitHub: `https://github.com/jesuscervantes070-sudo/nba-box-score-sim`

## Core design principle (do not violate)
**Everything must add up.** Specifically:
- Player PTS is always computed as `2*(FGM-FG3M) + 3*FG3M + FTM` — a
  computed `@property`, never simulated/stored as its own number.
- FGM must never exceed FGA; FG3A must never exceed FGA; OREB must never
  exceed REB — all enforced STRUCTURALLY (e.g. FG3A is a binomial/
  Dirichlet-Multinomial split OF fga, not an independently-drawn number
  that happens to usually be smaller), not just checked after the fact.
- Team totals (points, rebounds, assists, etc.) are always DERIVED by
  summing that game's player rows — see `GameResult.home_score` in
  game_engine.py and `insert_game()` in db.py. Never entered or
  simulated independently.
- Real, externally-sourced SEASON stats (a team's real opponent-FG%-
  allowed, a player's real per-game average) are a different category
  from simulated GAME outputs — those are legitimate stored inputs
  (like `Player.fga`, `Team.opp_fg_pct`), not violations of the rule above.

## How a game actually gets simulated (game_engine.py, the core file)
1. **Active roster**: NOT the whole 15-17 man roster — a realistic
   ~9-man group, chosen via a WEIGHTED RANDOM shuffle by real minutes
   (`ROTATION_WEIGHT_EXPONENT=8`), not a fixed cutoff. (A fixed
   deterministic cutoff was tried first and caused 47% of the league to
   never play a single simulated game all season — found by testing.)
2. **Minutes**: a real, fixed 240-per-game team resource (5 players x 48
   min), split via Dirichlet-Multinomial (`MINUTES_CONCENTRATION=3000`).
3. **Fouls**: scaled to each player's simulated minutes; hard-capped at
   6; `FOUL_OUT_LEAK_PROBABILITY` makes actually fouling out rare even
   when the raw draw reaches 6 (real coaches manage foul trouble).
4. **Every counting stat** (attempts, rebounds, assists, steals, blocks,
   turnovers) uses team-total-then-split (`_team_split_stat`), never
   independent per-player draws — independent draws made team-level
   totals unrealistic (scores reaching 229+, standings not
   differentiating good/bad teams at all).
5. **Defense is modeled**: steals remove attempts before they become a
   shot (crediting STL/TOV); blocks overturn made 2-pointers into misses
   (crediting BLK); shooting % blends with the opponent's real
   opponent-FG%-allowed (`LeagueAverages`/`compute_league_averages`).
   Verified directionally correct against real data, but KNOWN
   LIMITATION: only modestly closes the real-vs-simulated standings gap
   (~10-11 mean win error) — the real defensive spread in the data is
   narrow and gets partly drowned out by the per-game variance needed
   for realistic blowouts/upsets. Good candidate to revisit later.

## Files (all in this folder, import each other by filename)
- `models.py` — `Player`, `Team`, `ScheduledGame` dataclasses. All
  percentages are computed `@property`s, never stored fields.
- `data_source.py` — fetches + caches real rosters, per-game stats,
  schedule, team defense, and conferences via `nba_api`. Run
  `python data_source.py` (`--refresh` to force re-fetch).
- `loader.py` — the ONLY file that reads the cache/*.json files
  directly; everything else works with real Player/Team objects.
- `db.py` — SQLite storage (`cache/season.db`) for simulated season
  games. DNP (0-minute) player-rows are deliberately NOT stored, so
  season averages are "per game played," matching real stat convention.
- `game_engine.py` — the actual simulation (see above). By far the
  largest/most complex file; every tunable constant has a comment
  explaining how and why it was tuned against real data.
- `season.py` — simulates the full real 1,230-game schedule (~1 second).
- `main.py` — the playable text CLI: single game, or full season with
  standings (overall/by conference), real-standings comparison, and a
  season-averages browser (any team, several, or all).

## Deferred features, in the user's stated priority order
1. **Injuries** — deferred until after season/playoffs are solid, so
   before/after accuracy can be measured. A real season-long-injury
   player (e.g. a real Achilles tear, zero games that season) is
   correctly excluded right now by the "no real stat line" rule —
   reintroducing them properly is an injury-system problem, not a
   quick patch; don't fabricate stats for them.
2. **Playoffs** — not yet built.
3. **Possession-by-possession realism** — explicitly low priority.
   Safe to defer indefinitely: `simulate_game()` is a swappable box for
   however a game gets produced, so nothing above it needs to change.
4. **Narrative/MVP-tracking features** — mentioned once as a maybe, no
   concrete plan yet.

## User context / preferences
- Building this to practice Python (non-CS background, learning fresh
  in a class rather than self-study) and to have a project for a
  summer grant program.
- **Ask before doing/building/deleting anything.** Be extremely sure
  before creating or changing files. Go one concrete step at a time.
- Every piece of code needs readable comments explaining the reasoning
  — but stay clean, not cluttered. Match density to what a beginner
  needs, not exhaustive line-by-line restating of the obvious.
- Wants things to actually be internally consistent/realistic, not
  just "look plausible" — the whole point of the "everything must add
  up" rule above. Constants get tuned by testing against real data, not
  guessed — see game_engine.py's comments for the actual numbers found.
- Not fully sure themselves on some design forks (e.g. playoffs vs.
  injuries next) — talk it through conversationally rather than assume.
