# NBA Box-Score Simulator — Project Context

## What this is
A Python project that simulates NBA games and full seasons by generating
realistic box scores per player, built up from real per-game stats pulled
live from the NBA stats API. End goal: run a full season (and now
playoffs), then compare simulated season averages/standings against real
ones to check accuracy.

This is NOT a possession-by-possession sim (no shot clock, no play-by-play
logic) — deliberately deferred, low priority per the user. It's a
"box-score generator": each simulated game produces a full,
internally-consistent stat line for every player, derived from their real
season tendencies, and a team's own real defense genuinely affects their
opponents' shooting (see game_engine.py).

## Status: season sim, playoffs, injuries, and real trades are all BUILT
- **Single-game sim, season sim + standings + real-vs-simulated
  comparison**: complete. Playable via `main.py`.
- **Playoffs**: complete — real play-in tournament, fixed 8-team bracket
  (no reseeding), Finals, seeded with the REAL NBA tiebreaker chain
  (head-to-head → division leader → division record → conference record →
  record vs. each conference's playoff picture → point differential →
  alphabetical last resort). See playoffs.py.
- **Injuries**: complete — turns each player's real absence pattern into a
  simulated one. Anchored to roughly WHEN a real absence actually started
  (a real day-one injury stays day-one in the sim too), with the LENGTH
  randomized ±`INJURY_LENGTH_JITTER_GAMES` (currently 3, "about a week")
  around the real length. See injuries.py.
- **Real in-season trades**: complete — a traded player is correctly
  simulated on BOTH real teams for the right real games, not just their
  final team. Also fixes a smaller, related case: a player whose CURRENT
  roster listing doesn't match any team they logged a real game for at all
  (e.g. a trade that happened after the season's games were already
  played) — routed to their real team and fully excluded from the
  wrongly-listed one. See transactions.py.
- **Accuracy benchmarking**: complete — `benchmark_accuracy.py` runs N full
  seasons and saves a permanent, comparable snapshot (standings MAE,
  correlation, player stat bias) to `benchmarks/*.json`, so a feature's
  actual effect on accuracy is measured, not eyeballed.
- **Overtime**: complete — a game tied after regulation now plays real
  5-minute overtime periods (looping until it isn't tied) instead of
  ending in an impossible tie. See game_engine.py section below.
- **Game-by-game replay**: complete — the followed team's season (and,
  in the playoffs, any series they're actually in, Finals included) can
  be paced through one game at a time instead of only ever seeing the
  end result. See main.py section below.
- **Not yet built**: possession-by-possession realism (deliberately low
  priority — see below).
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
   Verified directionally correct against real data. Latest 30-run
   benchmark (post injuries + trades): standings MAE ~7.4 games,
   correlation ~0.88 vs. real standings — see `benchmarks/*.json` for the
   full comparable history (pre-injury baseline, injuries only, trades
   only, both together).
6. **Overtime**: a game tied after regulation plays a real 5-minute,
   25-team-minute OT period (`OVERTIME_MINUTES`/`OVERTIME_MAX_MINUTES` —
   the OT-sized versions of `TOTAL_GAME_MINUTES`/`MAX_MINUTES`), and
   keeps playing more of them (`simulate_game`'s own while-loop, no cap)
   until the score isn't tied — a real game can never just end tied.
   Reuses regulation's exact active roster, not a fresh draw, minus
   anyone who's already fouled out (`_overtime_eligible_roster`); each
   period's stats get ADDED onto the existing box score
   (`_add_period_stats`), with personal fouls hard-clamped at 6 across
   the whole game, OT included, not just within one period. Reuses
   regulation's own tuned dispersion/concentration constants rather than
   inventing new ones — there's no separate real PER-PERIOD data this
   project fetches to tune fresh ones against, only real per-GAME
   averages. Sampled at ~1.8% of games needing OT (400-game sample);
   `GameResult.overtime_periods` is stored (`games.overtime_periods` in
   season.db) purely for display (main.py's "FINAL/OT" and "(OT)"
   markers), never something a score/average is computed from.

## Playoffs (playoffs.py)
- Seeds each conference 1-10 off regular-season standings, ties broken by
  the real NBA chain (see Status above) — a recursive group-split
  (`_break_ties`) so it also handles 3+-team ties, not just two-team ones.
  `TEAM_DIVISIONS` is a small hardcoded dict (divisions realign maybe once
  a decade — not worth a whole new fetch/cache file for).
- Real current-NBA play-in tournament for 7-10, then a fixed 8-team
  bracket (1v8/4v5/3v6/2v7, no reseeding between rounds).
- `main.py` runs it ROUND BY ROUND, not all at once — play-in, then each
  round in turn, each its own "press Enter" pause. The ASCII bracket
  diagram prints as a recap at the END of each conference (once every
  round's winner is actually known — it can't be drawn any earlier, its
  connector lines ARE those winners).
- The followed team is highlighted in the bracket in real color (bold
  cyan), applied AFTER the character grid is flattened to plain strings
  (never spliced into the grid itself — that would corrupt the column
  math every connector line depends on). See `_render_conference_tree`.
- Nothing here gets written to `season.db` — simulate-and-print, same
  spirit as a single exhibition game.

## Injuries (injuries.py)
- Reuses each player's real absence stint COUNT from `cache/injuries.json`
  (`data_source.fetch_player_absence_stints`), which also records where
  each stint actually STARTED (as a game index into that player's team's
  real schedule) — added specifically so a simulated injury could be
  anchored to real timing instead of scattered randomly.
- Each stint's LENGTH is randomized ±`INJURY_LENGTH_JITTER_GAMES` around
  the real one (clamped to at least `MIN_INJURY_STINT_LENGTH`), so
  recovery time isn't an identical fixed number every simulated run.
- `still_out_at_season_end` flags a span whose end lands on/past the
  season's last game — the "still hurt entering the playoffs" signal
  `main.py` uses to split the injuries display.
- `MIN_INJURY_STINT_LENGTH = 2`: real single-game absences are almost
  always a rest day, not a real injury — tested against real data,
  counting every miss flagged 81% of the league as "injured."

## Real trades (transactions.py + data_source.fetch_roster_membership)
- `rosters.json` only ever files a player under ONE team. A traded player
  gets added back to their old team's pool for real pre-trade games, and
  restricted on the new team to real post-trade games only.
- Smaller related fix: a player whose CURRENT roster listing matches NO
  team they logged a real game for at all this season (confirmed: exactly
  4 such players in the 2025-26 data — a trade that happened after the
  season's games were already played) — routed to their real team via the
  same membership mechanism, and fully excluded from the wrong team via a
  `first_game_id: None` sentinel (meaning "zero real games here," not a
  normal restricted window).

## Game-by-game replay (main.py)
- This is a REPLAY, not a live simulation: the whole season (season.py)
  or whole series (playoffs.simulate_series) is already fully simulated
  and stored/held in memory by the time any of this runs, same as
  before this feature existed — it just walks already-decided results
  back out at a controlled pace instead of dumping them all at once.
  Stopping early never leaves anything half-simulated. `simulate_season`
  takes a `verbose` flag specifically so main.py can suppress its
  "Simulated N games in X.XXs" line here — printing that right before
  asking "want to watch it game by game?" gave away that it already
  happened, undercutting the whole point.
- Scoped to the followed team, same "auto-print yours, opt-in browser
  for anyone else" pattern as moves/injuries/season averages
  (`run_team_game_log_replay` + `_run_game_log_browser`).
- Score line only by default (`_format_score_line`) — a full box score
  for all 82 games at once would be unreadable. Commands at each pause:
  Enter (next game), a number (that many games in a row), `b` (full box
  score of the last game shown — reuses `print_box_score()` unchanged,
  rebuilt from storage via `db.get_game_box_score`/`get_team_game_log`),
  `t` (fast-forward, still showing every score line along the way,
  through every game up to the real trade deadline), `e` (stop here).
  `t` explicitly checks "already past the deadline" and says so rather
  than silently behaving like Enter (found by testing — it originally
  did, and looked like a bug with no explanation).
- `TRADE_DEADLINE_DATE = "2026-02-05"` (the real 2025-26 deadline) is
  hardcoded in main.py — same reasoning as playoffs.py's `TEAM_DIVISIONS`,
  a fixed real-world fact not worth a fetch/cache file for.
- Playoff series (including the Finals) get the same treatment
  (`_replay_playoff_series`) for any series the followed team is
  actually in — every other series in the same round still resolves
  instantly. Matched to its round's raw series result (for the full
  `game_log`) by winner+loser name (`_series_for_line`), NOT by list
  position — a round's pre-formatted text lines and its raw series
  results (`tree["round1"/"round2"/"round3"]`) aren't in the same order.
- `print_box_score`/`_print_team_box_score` now take an optional
  `highlight` so the followed team's name is colored there too (bold
  cyan, same convention as everywhere else) — this was missing
  entirely before (single-game mode, option 1, never had a followed
  team to highlight in the first place).

## Files (all in this folder, import each other by filename)
- `models.py` — `Player`, `Team`, `ScheduledGame` dataclasses. All
  percentages are computed `@property`s, never stored fields.
- `data_source.py` — fetches + caches real rosters, per-game stats,
  schedule, team defense, conferences, injuries, and roster membership
  via `nba_api`. Run `python data_source.py` (`--refresh` to force
  re-fetch).
- `loader.py` — the ONLY file that reads the cache/*.json files
  directly; everything else works with real Player/Team objects.
- `db.py` — SQLite storage (`cache/season.db`) for simulated season
  games AND simulated injuries. DNP (0-minute) player-rows are
  deliberately NOT stored, so season averages are "per game played,"
  matching real stat convention. Also the only file with a "rebuild one
  stored game's full box score back into a real GameResult" function
  (`get_game_box_score`) and a "walk one team's games in order"
  function (`get_team_game_log`), both built for main.py's game-by-game
  replay.
- `game_engine.py` — the actual simulation (see above), including
  overtime. By far the largest/most complex file; every tunable
  constant has a comment explaining how and why it was tuned against
  real data.
- `injuries.py` — turns real absence data into a simulated season's
  injury calendar (see above).
- `transactions.py` — makes real in-season trades real in the sim (see
  above).
- `season.py` — simulates the full real 1,230-game schedule (~1 second).
  `simulate_season`'s `verbose` flag (see Game-by-game replay above)
  only ever gets turned off by main.py's interactive flow — every other
  caller (direct `python season.py`, benchmark_accuracy.py) still wants
  the printed summary.
- `playoffs.py` — seeding, play-in, bracket, Finals (see above).
- `benchmark_accuracy.py` — runs N full seasons, saves a permanent
  accuracy snapshot to `benchmarks/*.json`.
- `main.py` — the playable text CLI. Flow for a full season: simulate →
  optional game-by-game replay of the followed team's season (see
  above) → standings (overall/by conference) → real-vs-sim comparison →
  the followed team's real in-season moves, injuries (healed vs.
  still-out-for-playoffs split), and season averages (each with an
  opt-in browser for another team) → playoffs (optional, round by
  round, any of the followed team's own series replayed game by game)
  → Finals averages. Real-vs-sim numbers are colored by ACCURACY (how
  close, not direction — a decrease can be green if it's a small one)
  — green/yellow/red, same convention as the standings comparison.

## Deferred / open, in the user's stated priority order
1. **Trades made accuracy slightly worse** in the original benchmark
   (trades-only: MAE 8.48 vs. 7.99 baseline) — flagged, not yet
   investigated. Worth digging into before trusting that number.
2. **Possession-by-possession realism** — explicitly low priority. Safe
   to defer indefinitely: `simulate_game()` is a swappable box for
   however a game gets produced, so nothing above it needs to change.
3. **Narrative/MVP-tracking features** — mentioned once as a maybe, no
   concrete plan yet.
4. **Playoff series box stats** (browsable, not just Finals averages) —
   explicitly "not necessary right now" per the user, more UI work than
   value at the moment.
5. **Key-injury highlighting** (e.g. flag high-PPG players specifically)
   — explicitly deferred, only worth doing if it's ever quick.

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
- On the CLI's UI: prefers auto-printed views scoped to just the
  followed team (a full league-wide dump every time was "too much"),
  with an opt-in browser to look up another team when wanted. Wants
  spacing/columns actually verified against the longest real names in
  the data, not just eyeballed on one run.
- Talks through open design forks conversationally rather than having
  them assumed — e.g. the playoffs-vs-injuries ordering, and the
  anchored-start/jittered-length redesign of the injury model, both came
  from that kind of back-and-forth, not a first guess.
