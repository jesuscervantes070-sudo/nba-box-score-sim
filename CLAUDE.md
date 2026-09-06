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
  simulated one. EVERY real absence is simulated, one-game rest nights
  included (they're 48% of all real stints and ~11% of all real missed
  games — excluding them left the sim missing ~8% fewer player-games than
  really happened, in every era). `MIN_INJURY_STINT_LENGTH` now only
  decides what gets CALLED an injury for display (`InjurySpan.is_injury`),
  not who sits. A real one-game rest isn't jittered either — it's a rest
  night, not an injury with a recovery time. `MINIMUM_AVAILABLE_PLAYERS`
  = 8 keeps a team able to field a side, a real NBA rule that simulating
  every absence made reachable (4% of team-games otherwise fell below it,
  some to five players). Anchored to roughly WHEN a real absence actually started
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
- **Multi-season backtesting**: complete — every real NBA season this
  project's data source actually has, 1996-97 through 2025-26 (30
  seasons, all with real injuries + real trades on), has been fetched,
  simulated, and benchmarked against real standings. 1996-97 is a real,
  hard floor (the stats API itself has nothing before it), not a
  stopping point chosen along the way. See "Multi-season backtesting"
  section below.
- **Accuracy tuning against all 30 seasons**: complete — the sim's
  tunable constants are now fit against every backtested season at once
  (`sweep_constants.py`), with a time-based train/holdout split, instead
  of against 2025-26 alone. Found and fixed a bias no single season
  could show: the sim spread win totals ~37% wider than real basketball
  in all 30 seasons. Then three MISSING MECHANISMS, each found by
  measuring what the sim still got wrong rather than by guessing:
  ordinary turnovers were almost purely decorative; possessions weren't
  conserved between the two teams; and player averages quietly assume
  every rotation player is available every night. Final constants:
  `DEFENSE_AMPLIFICATION` 5 → 1, `OFFENSE_AMPLIFICATION` 0 → 0.5,
  `TURNOVER_POSSESSION_WEIGHT` 0 → 1.0, `PACE_COUPLING_WEIGHT` 0 → 0.75,
  `ROSTER_AVAILABILITY_WEIGHT` 0 → 1.0. Holdout standings MAE 8.25 →
  5.09 and correlation .812 → .905. See "Accuracy tuning" below.
  Since then, three more constants landed from BOX-SCORE evidence rather
  than standings — `GAME_PACE_VARIATION` 0.052, `CONSISTENCY_WEIGHT` 12,
  `CONSISTENCY_SHRINK_GAMES` 80, plus `USAGE_CONCENTRATION` 150 → 500.
  All four are standings-neutral by design; see "Game pace" and "Player
  consistency" below for what they fix and how they were measured.
- **How much accuracy is even left**: measured, not guessed. Two
  INDEPENDENT simulated runs of the same season already differ from each
  other by ~4.4 wins MAE — pure randomness, zero model error in it.
  Adding the luck in a real 82-game season puts the irreducible floor at
  ~4.7. So of the current ~5.5, well under a game is real model error
  (down from ~3.7 before this tuning) and the large majority of what
  remains is noise nothing can fix. Same for correlation: the ceiling a
  PERFECT model could reach against real standings is ~.925, because
  real standings contain unpredictable luck — the sim is at ~.90, about
  98% of achievable. Worth knowing before chasing more: standings
  accuracy is essentially done. The remaining honest gap is in STAT
  realism (see Deferred), not in wins.
- **Player consistency**: complete — every player has his own scoring
  streakiness, measured from his real game logs and cached for all 30
  seasons, replacing one global constant that made all 450 players
  equally streaky. Per-player streakiness now correlates 0.74 with real
  (was 0.26). Scoring only: defensive consistency was tested against
  real per-game defensive ratings and REJECTED (it is mostly team and
  playing time, repeating at 0.23 with workload removed). See "Player
  consistency" below.
- **Multi-season runs + offseason bridge**: complete — `main.py` option
  3 plays straight through real NBA history, following one franchise
  across as many seasons as you pick, with standings, brackets,
  champions and an offseason report between each. See "Multi-season
  runs" below.
- **Season picker + era playoff rules**: complete — `main.py` now asks
  which of the 30 cached seasons to play instead of only ever playing
  2025-26, and each one uses its REAL postseason rules (best-of-5 first
  round before the 2003 playoffs, no play-in before 2019-20, the one-off
  conditional 2019-20 bubble play-in, the 7-10 tournament from 2020-21).
  See "Playoffs" below.
- **Free throws**: mostly fixed — they now come from the opponent's
  fouls rather than being drawn unconnected to anything, which is what
  a free throw actually is. FTA night-to-night spread 2.97 → 5.61
  against a real 7.00. One piece deliberately left: real officiating
  moves both teams together and the sim's fouls are drawn independently.
  See "Free throws come from the opponent's fouls" below.
- **Game pace**: fixed — the shared per-game pace multiplier was drawn
  from a counting distribution, so 96% of its 10.8% swing was Poisson
  noise from the number happening to be ~100 rather than anything about
  basketball. Real games vary 5.2%. Simulated team points sd 17.57 →
  13.96 against a real 12.16. See "Game pace" below.
- **Not yet built**: possession-by-possession realism (deliberately low
  priority — see below); an offseason bridge connecting one backtested
  season to the next (drafts/free agency — see Deferred below).
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
   Verified directionally correct against real data. A team's OWN real
   offense is amplified the same way its opponent's defense is
   (`OFFENSE_AMPLIFICATION`, tuned jointly with `DEFENSE_AMPLIFICATION`
   — see "Accuracy tuning" below).
6. **Possessions are a shared, conserved resource**, like the 240
   minutes above: both teams get one negotiated pace and one shared
   possession count per game (`PACE_COUPLING_WEIGHT`), with turnovers
   and offensive rebounds inside that arithmetic, so giving the ball
   away really does cost a team shots.
7. **Player averages are corrected for availability**
   (`ROSTER_AVAILABILITY_WEIGHT`) — a per-game average is measured over
   games a player actually played, so nine of them summed describe an
   always-healthy team that doesn't exist. Self-calibrates per season
   against real team-level data, which is why it barely touches the
   1990s and matters a lot in the 2020s.
   Latest 30-run benchmark (post injuries + trades): standings MAE ~5.5
   games, correlation ~0.88 vs. real standings, averaged over all 29
   backtested seasons — see
   `benchmarks/*.json` for the full comparable history (pre-injury
   baseline, injuries only, trades only, both together, and
   `<season>_backtest` vs `<season>_tuned` for before/after this
   tuning).
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
- **Era-correct rules, chosen by season** (`playoff_format`): the
  postseason simulated is the one that really followed that season, not
  today's. First round is best-of-5 before the 2003 playoffs and
  best-of-7 after (`simulate_series` takes `best_of`, defaulting to 7 so
  the modern game and every existing caller are untouched); there is NO
  play-in before 2019-20 (and no empty "Play-In Tournament" heading
  either); 2019-20 alone uses its real conditional bubble format — the
  9th seed only gets a shot if it finishes within 4 games of the 8th,
  then must win twice while the 8th needs one, applied to the SIMULATED
  standings so it triggers or doesn't the way the real one did; the
  current 7-10 tournament runs from 2020-21. Verified: pre-2003 first
  round series now end in 3-5 games, and every era plays through.
- **Divisions are real per season** (`divisions_for` →
  `cache/<season>/team_divisions.json`, fetched by
  `data_source.fetch_team_divisions` from the same standings endpoint
  that already supplies conferences, so it costs no extra call). This
  replaced a hardcoded MODERN six-division map that was wrong for every
  season before the 2004-05 realignment, when the league had four
  (Atlantic/Central in the East, Midwest/Pacific in the West). Confirmed
  from the data, not memory: the endpoint returns 4 divisions through
  2003-04 and 6 from 2004-05, and Utah is Midwest before the switch and
  Northwest after. `TEAM_DIVISIONS` survives only as a fallback for an
  un-fetched season.
  Note this affects SEEDING TIEBREAKERS only, and accuracy benchmarks
  never touch playoffs.py — it is a correctness fix, not an accuracy one.
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
- Every score line also carries the followed team's RUNNING record
  through that game (season win/loss tally, or the series score for a
  playoff series) — games always reveal in real chronological order
  here (no jumping backward), so this is a plain running tally kept by
  the replay loop itself, not a re-query of the standings table per game.
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

## Multi-season backtesting (data_source.py, loader.py)
- The point: run the exact same pipeline (real data → simulate → compare
  to real) against as many past seasons as the data actually supports,
  as a validation/tuning set bigger than just 2025-26 — do the tuned
  constants (`DISPERSION`, `DEFENSE_AMPLIFICATION=5`, etc.) generalize,
  or were they quietly overfit to one season's quirks? Backtesting a
  season a season was NEVER tuned against and getting comparable (often
  BETTER) accuracy is real evidence for "generalizes," not "overfit."
- **Every cache file is now season-scoped**: `cache/<season>/rosters.json`
  etc., not one shared set of filenames — `_season_cache_dir` in
  data_source.py, mirrored by loader.py's `_season_cache_dir` (same
  layout, doesn't create the dir — a missing season should fail loudly).
  Every `load_*`/`build_and_cache_*` function takes `season` (defaulting
  to `DEFAULT_SEASON = "2025-26"`, so every existing call site keeps
  working unchanged). `season.py`/`benchmark_accuracy.py` thread it
  through to their loader calls now too — previously accepted a
  `season` argument but silently ignored it for anything except the
  db label, always loading whatever the single shared cache held.
  `main.py` still has no season-picker UI — deliberately out of scope
  for this round (backtesting was proven out via scripts first).
- **The regular-season game filter got replaced with something far more
  robust**: used to be a hardcoded `gameLabel` whitelist, tuned only
  against 2025-26 — broke the instant a second season got fetched
  (2024-25 came back with 1,233 games instead of 1,230; the extra 3
  were a real event, "Rising Stars Championship," that 2025-26 happens
  to call "Rising Stars Final" instead). Replaced with
  `REGULAR_SEASON_GAME_ID_DIGIT`: a real NBA game_id's 3rd digit IS the
  game type (`2`=regular season, `1`=preseason, `3`=All-Star, `4`=
  playoffs, `5`=play-in, `6`=the one Emirates Cup Championship game),
  true across every season checked. Verified byte-identical to the old
  filter's result on 2025-26 before trusting it anywhere else.
- **`HISTORICAL_TEAM_NAMES`**: `commonteamroster` (the roster endpoint)
  has no team-name field at all, only a numeric team_id — so rosters
  were always labeled with that team's CURRENT name, wrong for any
  season before a real relocation/rename (e.g. 2013-14 came back
  "Charlotte Hornets" from the roster file while schedule/defense/
  standings all correctly said "Charlotte Bobcats," a mismatch that
  crashes `season.py` with a KeyError on every game for that team). A
  small hardcoded table (team_id → real season-range → real name),
  same "stable fact, not worth fetch infrastructure" reasoning as
  `NBA_API_TEAM_NAME_FIXES` and playoffs.py's `TEAM_DIVISIONS` — covers
  every real rename/relocation in the backtestable range: Bobcats/
  Hornets, the original Hornets' Charlotte→New Orleans→Pelicans path
  (including the post-Katrina "New Orleans/Oklahoma City Hornets"
  seasons), Sonics→Thunder, Nets NJ→Brooklyn, Grizzlies Vancouver→
  Memphis, Bullets→Wizards.
- **`_active_roster_for_game`'s weighted draw got a numerical-safety
  floor**: `ROTATION_WEIGHT_EXPONENT=8` applied to a long tail of real
  sub-minute bench players can numerically collapse one entry to an
  exact (or rounding-swallowed) zero once normalized — `np.random.
  choice(replace=False)` then raises "Fewer non-zero entries in p than
  size." Hit once backtesting 2007-08; not specific to old data, any
  season's injury/trade-reduced roster could hit it by chance. Fixed
  with the same `+ 1e-9` floor pattern `_dirichlet_multinomial_split`
  already uses for the identical class of problem. Confirmed no
  accuracy change on 2025-26 (MAE 7.40→7.51, inside normal noise).
- **Real historical schedule anomalies, all verified as correct, not
  bugs**: 1,229 games for 2012-13 (a real Sandy Hook cancellation never
  made up), 990 for 2011-12 (the real 66-game lockout season), 725 for
  1998-99 (the real 50-game lockout season), 1,189 across the real
  29-team era (1995-96 through 2003-04, before the Bobcats expansion
  brought the league to 30), 1,059 with a real 64-75-games-per-team
  spread for 2019-20 (cut short by COVID, never made uniform).
- **1996-97 is the real floor, not a chosen stopping point**:
  `leaguedashplayerstats` (where every player's real per-game stat line
  comes from) returns real rows starting exactly there and zero rows
  for every season checked before it (1995-96, 1994-95, 1990-91) — a
  limit of the data source itself, nothing to fix in this project's code.
- **Full 30-season accuracy range** (30-run benchmark each, real
  injuries + real trades on). Before the tuning below,
  `benchmarks/<season>_backtest.json`: MAE 6.48–10.38 (avg 8.42),
  correlation .688–.919 (avg .812). Then, in order, as each fix landed:
  `<season>_tuned.json` (offense/defense retune) MAE avg 6.19,
  correlation .844; `<season>_tov.json` (turnovers cost possessions)
  MAE avg 5.94, correlation .856; `<season>_final.json` (possession
  conservation + availability correction) — the CURRENT numbers, and
  the ones to compare any future change against. Better than the
  original in every season, and the worst season now comfortably beats
  the old best one. Accuracy
  never did decline steadily with age; it tracked how extreme each
  season's real defensive spread happened to be, which is exactly the
  over-amplification the tuning below removed.
- **Not built yet, deliberately**: an "offseason bridge" between two
  consecutive backtested seasons (diff two seasons' real rosters —
  anyone new who wasn't anywhere in the league last season is a rookie/
  draftee, anyone new who WAS on a different team is a free-agent
  signing, anyone missing either retired or left the league — same
  "compare real game-log evidence" method `roster_membership.json`
  already uses for in-season trades, just across seasons instead of
  within one) and a season-picker in main.py's UI. Both need the
  season-scoped caching above to exist first, which now does.
- **Playoffs format changed shape across this range too, unhandled**:
  no play-in tournament at all before 2019-20 (a different one-off
  format that specific year, the current 7-10-seed version only from
  2020-21 on), and the first round was best-of-5, not best-of-7, before
  the 2003 playoffs. Doesn't affect anything above (backtesting only
  ever calls `benchmark_accuracy.py`, which never touches
  `playoffs.py`) — noted for whenever an old season's real postseason
  gets simulated, not just its regular season.

## Individual defensive impact — verified, no work needed
Checked directly (2023-24): removing Rudy Gobert from Minnesota raises
opponent FG% from 46.71% to 48.68%, +1.98 percentage points, with team
blocks falling 4.43 → 3.57 per game. Real DPOY-level rim protection is
worth roughly 1.5-3 points of opponent FG%, so this is calibrated, not
just directionally right.

The chain that produces it: `_available_rate_relative` rates each team's
steal/block generation PER MINUTE among available players, so losing a
great shot-blocker genuinely lowers the rate (losing an average one
doesn't); fewer blocks means fewer made 2-pointers overturned into
misses; opponents shoot better. `SHORTHANDED_PENALTY` adds to it.

Worth knowing this only works because of the steal/block roster-filtering
fix — before it, a short-handed team's whole defence was miscounted, so
this test would have measured the bug rather than Gobert.

## Player consistency (data_source.py, game_engine.py)
- **What it is**: every player now has his OWN scoring streakiness,
  measured from his real game logs, instead of one global constant
  making all 450 players equally streaky relative to their own average.
  A steady star stays steady and a streaky one actually erupts.
- **The raw measurement** lives in `cache/<season>/player_consistency.json`,
  built by `fetch_player_consistency` from the SAME league-wide game log
  that injuries and roster membership already fetch — zero extra API
  calls. Cached for all 30 backtested seasons. Two numbers per player,
  both relative to the swing pure chance alone would produce
  (sqrt of the average), so a 30-point scorer and a 10-point one are
  comparable:
    - `spread_raw` — how much his points bounced, full stop.
    - `spread_rate` — the same with MINUTES accounted for.
  Real players run about 1.2 to 2.5 on `spread_raw`.
- **The sim consumes `spread_rate`, and this matters.** The sim already
  draws minutes per game, slightly WIDER than real (sd 6.19 vs 5.39), so
  feeding it `spread_raw` would count minute-wobble twice — the same
  double-counting family as the three box-score bugs. It changes who is
  streaky: GG Jackson had the highest `spread_raw` in 2023-24 (2.54)
  purely because a rookie's minutes swung 10 to 35, and is ordinary at
  1.56 once that's removed. `spread_raw` is kept for the eventual 1-99
  DISPLAY rating, where "unpredictable to watch" is the honest meaning.
  Per the user: store the raw number, leave the 1-99 scaling for later.
- **It works on shot VOLUME, not shooting percentage** — measured, not
  assumed. A player's night-to-night FG% varies only 0.98x as much as
  pure coin flips already explain (i.e. not at all beyond luck), while
  his shot COUNT varies 1.15x. "He was ice cold tonight" is mostly "he
  took eight shots tonight." Scoring and shot-volume streakiness are the
  same underlying trait (+1.00 corrected for measurement noise). So the
  lever is `_scoring_concentrations`: a per-player usage-split
  concentration replacing the single global `USAGE_CONCENTRATION`.
- **Correction to a long-standing note here**: this doc used to say
  every player was equally streaky because of `DISPERSION` = 30. That
  was wrong about the actual code — `DISPERSION` is NOT used for player
  scoring in the team pipeline at all (only `_simulate_fouls` and the
  standalone `simulate_player_game` still use it). A player's shot
  volume comes from `_dirichlet_multinomial_split(..., USAGE_CONCENTRATION)`,
  which is why that is the constant that had to move. Worth checking
  where a constant is actually READ before planning work around it.
- **It is a real trait, tested out of sample**: across all 30 cached
  seasons a player's spread in one season predicts his spread in the
  NEXT one at r = 0.43 over 2,585 player-seasons — different teammates,
  different opponents, a summer in between. Points-per-game carries at
  0.82 for scale; streakiness carries BETTER for stars (0.49). This is
  the number to trust: single-season split-half estimates moved between
  0.21 and 0.61 depending on how the population and controls were
  sliced, which is what over-slicing one season looks like.
- **`CONSISTENCY_SHRINK_GAMES` = 80** pulls a small sample toward the
  league average (a 6-game measurement is real but mostly noise).
  Derived from that same carryover, not guessed.
- **Why `CONSISTENCY_WEIGHT` is 12 and not 1**: it is an exponent, and
  the lever is weak. The usage split is only responsible for about a
  FIFTH of a player's game-to-game scoring swing — the rest is the
  shared team shot total, his minutes, and plain coin-flip shooting
  luck, none of which can be made player-specific. So it takes roughly
  five times more push than the naive variance relationship suggests.
- **`USAGE_CONCENTRATION` had to be retuned 150 → 500 at the same
  time.** Differentiating players around the old baseline made the tails
  WORSE, because at 150 the league was already 8% too streaky. Both were
  calibrated jointly against real 2023-24 game logs. Every target
  improved:

    per-player correlation   0.26 → 0.74
    streakiness level        1.08 → 1.05   (1.00 = real)
    spread across players    0.60 → 0.95   (1.00 = real)
    40-point games/season     219 →  210   (real 162)
    50-point games/season      32 →   25   (real 20)

  And the ordering on the players that motivated this is finally right —
  Booker and Brunson streakier than SGA, where the sim had it backwards:

    player     real avg/sd    sim avg/sd (was)   real high / sim (was)
    SGA         30.1/ 7.0     28.6/ 8.7 (11.3)      43 / 54 (70)
    Brunson     28.7/10.1     28.1/ 9.2 ( 8.7)      61 / 58 (49)
    Booker      27.1/10.2     26.8/11.1 ( 8.7)      62 / 68 (50)

- **Scoped to SCORING only, and DEFENSIVE consistency was properly
  tested and rejected** — not waved away. The first pass used steals and
  blocks, which is a bad proxy for defence; the real test used per-game
  DEFENSIVE RATING (`playergamelogs`, Advanced). Findings: 56% of a
  player's nightly defensive rating is just his TEAM's night; what
  remains is 0.69-correlated with minutes played even after correcting
  for it, so the "most consistent defenders" are simply the starters
  (Durant, Davis, Luka) and the "least consistent" are bench players
  (McConnell, Pritchard) — a rating measured over fewer possessions
  bounces more, the way 10 coin flips look wilder than 100. With
  workload removed it repeats at 0.23. OFFENSIVE rating is no better
  (0.19) and tells us nothing points doesn't (+0.06). Blocks repeat at
  0.03 — pure noise. Assists ARE real but a separate trait (+0.05 with
  scoring) — see Deferred.
- **Standings unaffected**, checked across all 30 seasons:
  `benchmarks/<season>_consistency.json` averages MAE 5.68 /
  correlation 0.874 against `_v6`'s 5.67 / 0.873.
- **The 1-99 rating** (`Player.consistency_rating`, shown as the CONS
  column in main.py's season averages) is the display layer the user
  asked to defer until the raw numbers existed. It is a computed
  property, never stored, and means something exact: 90 = steadier than
  90% of real NBA rotation players, read off a reference distribution
  of 5,566 player-seasons (40+ games, 8+ ppg, all 30 seasons) held in
  `CONSISTENCY_REFERENCE_*` in models.py. Finer at both ends than in the
  middle, because the streakiest 5% span 2.23 to 3.87 and an even grid
  crushed all of them into 5-9. One pooled table, not one per season:
  league-average spread drifts 1.72 → 1.79 over the 30 years, but that
  is only half the player-to-player spread within a season, so pooling
  stays fair and buys cross-era comparability.
  It shows the RAW spread while the sim runs on the minutes-stripped
  one — deliberate, and the reverse of a bug: the sim needs minutes
  removed because it already draws minutes, but a viewer experiences
  the opposite, since a player you can't predict because you don't know
  if he'll play 12 minutes or 32 really is unpredictable to watch.
  Two floors, both because the alternative is confidently wrong: under
  20 games, and under 8 ppg (without the second, a 1.2 ppg bench player
  rated 98, the steadiest scorer in the league — true arithmetic,
  nonsense basketball). Both print "--", never a number. On a real
  roster: Gilgeous-Alexander 97, Capela and Adebayo 98, Isaiah Joe 20.

## Audit: do this project's constants hold across ERAS? (do this again)
- **Why it matters**: the single most repeated mistake in this project's
  history is tuning against ONE season and shipping a bias visible only
  league-history-wide. It happened with `DEFENSE_AMPLIFICATION` (tuned
  on 2025-26, spread win totals 37% too wide in all 30 seasons) and
  nearly again with the consistency and free-throw work, which was
  calibrated on 2023-24 because that was the season whose real game logs
  were loaded.
- **The check**: fetch real game logs for a handful of seasons spanning
  the range (1996-97, 2003-04, 2010-11, 2018-19, 2024-25) and compare
  the real quantity each constant targets. Results:

    free throws vs opponent's fouls   0.79-0.81 every era   GENERALIZES
    free-throw swing                  30.6-32.4% every era  GENERALIZES
    two teams' free throws correlate  0.20-0.26 every era   GENERALIZES
    between-team scoring spread       4.04-4.59 every era   GENERALIZES
    game pace variation               6.8% -> 5.3%          ERA-SPECIFIC

  So the foul/free-throw mechanism is real basketball in every era, and
  the one constant that did NOT hold is now measured per season (above).
- **It also found a real BUG that had nothing to do with constants** --
  the phantom-team problem below. Worth knowing that an era audit pays
  for itself twice: it checks what you meant to check, and it exercises
  code paths (old seasons, small rosters) that a modern-season workflow
  never touches.

## Teams that did not exist in a season (loader.py)
- `commonteamroster` is keyed by team_id and happily returns an EMPTY
  roster for a franchise not yet founded or already relocated, so
  2002-03 and 2003-04 both loaded a "Charlotte Hornets" with no players
  (they had moved to New Orleans in 2002-03; the Bobcats arrive in
  2004-05). `load_teams` now skips any team with no players.
- **This was not cosmetic.** Every league-wide average in
  `compute_league_averages` divides a real total by `len(teams)`, so one
  phantom team made all of them **3.3% too low across the entire real
  29-team era** -- league-average steals, blocks, shot attempts,
  turnovers and pace, in the eight backtested seasons from 1996-97 to
  2003-04. Those averages are what every defensive and pace adjustment
  is measured against, so the error propagated into the games.
- It also crashes `_active_roster_for_game` outright (numpy
  "probabilities do not sum to 1") if anything asks such a team to field
  a lineup -- which is how it was found.
- The season sim never noticed either problem because the dropped team
  plays exactly zero real games. Verified against the schedule: 29 teams
  and 1,189 games in those seasons, matching the real 29-team count
  already documented above.

## Multi-season runs and the offseason bridge (offseason.py, main.py)
- **What it is**: pick a start and end season, follow one franchise, and
  each season is simulated in turn — your record and playoff finish, the
  champion, optional standings and bracket, then what changed over the
  summer. Ends with a one-line-per-season summary of the whole run.
- **Real rosters every season, simulated results.** Each season uses its
  OWN real rosters, so the real offseason already happened and
  `offseason.py` REPORTS it rather than simulating one. Champions
  diverge from real history immediately (that's the point); what a
  simulated 1997 title cannot do is change who is on which roster in
  1998. Simulating the offseason itself — progression, contracts, AI
  decisions — is the much bigger "option B" the user explicitly deferred
  to later. Note it would need player progression, which does not exist.
- **Franchise continuity is DERIVED, not a hardcoded rename table.** A
  renamed franchise keeps most of its players, so a disappeared team
  name matches whichever NEW name shares the most players with it.
  Validated against all nine real renames in range (Bullets→Wizards,
  Vancouver→Memphis, Charlotte→New Orleans, both Katrina-era Hornets
  moves, Seattle→Oklahoma City, New Jersey→Brooklyn, Hornets→Pelicans,
  Bobcats→Hornets) with ZERO false positives across all 29 transitions:
  each shares 3-8 players with its true successor and only 1-2 with the
  nearest unrelated team. So following the 1996-97 Sonics leaves you
  holding the Thunder in 2008-09 rather than losing the team mid-run.
  An expansion team (the 2004-05 Bobcats) has no disappeared team to
  match and is correctly left unlinked.
- **"Arrived" is not "drafted", deliberately.** The data records who
  played, never why they didn't, so a player who missed a whole season
  injured reads exactly like a rookie — Shaun Livingston shows as new to
  the league in 2008-09, Alonzo Mourning in 2003-04. Labelled "new to
  the league" rather than guessing.
- Reports are scoped to your team and filtered to players at 10+ mpg —
  a summer has ~90 league-wide arrivals, almost all fringe.

## Free throws come from the opponent's fouls (game_engine.py)
- `FOUL_FREE_THROW_WEIGHT` ties each team's free-throw volume to how
  many fouls the OPPONENT actually committed tonight (`_foul_pressure`),
  because they are two halves of one event: real free-throw attempts
  correlate +0.80 with the other team's fouls. The sim was already
  drawing fouls per player and using them for nothing but fouling out.
- Extra trips DISPLACE shots at `FREE_THROW_POSSESSION_COST` = 0.44, the
  same figure the possession estimate already uses — a possession spent
  at the line isn't spent shooting from the floor.
- `CAPPED_FOUL_SHARE` = 0.972 corrects a bug measurement caught: foul
  pressure divides by the UNCAPPED expectation, but the 6-foul limit and
  FOUL_OUT_LEAK_PROBABILITY hold the draw below it by design, so every
  team read as fouling 2.8% under normal every night. Re-measure if
  DISPERSION, FOUL_OUT_LIMIT or FOUL_OUT_LEAK_PROBABILITY change.
- Results, and what's still open, are in the Deferred list's free-throw
  entry. Standings unaffected (MAE 5.56 → 5.60, correlation 0.899 →
  0.900 over six seasons at 30 runs).

## Game pace (game_engine.py)
- **Pace variation is measured PER SEASON**, not hardcoded:
  `data_source.fetch_league_pace_variation` derives it from the same
  league-wide game log already fetched, caches it as
  `cache/<season>/league_pace.json`, and it reaches the sim as
  `LeagueAverages.pace_variation` (loaded by `load_league_pace_variation`
  and passed in by season.py / benchmark_accuracy.py / main.py).
  `GAME_PACE_VARIATION` is now only the FALLBACK for a season with no
  cached file.
  It has to be per season because it genuinely drifts: 6.8% in 1996-97,
  6.2% in 2003-04, 5.7% in 2018-19, 5.3% in 2024-25. Hardcoding the
  modern value gave 1990s games about a quarter too little pace
  variation. Found by auditing whether this session's constants held
  across ERAS rather than only on the 2023-24 season they were
  calibrated against -- see the audit note below.
- **`GAME_PACE_VARIATION` = 0.052 replaced a real bug.** The
  game's shared pace multiplier used to be
  `_negative_binomial_count(avg_team_poss, TEAM_ATTEMPTS_DISPERSION)`
  divided by `avg_team_poss` — drawing a COUNT with a mean near 100 and
  treating it as a ratio. A count draw carries an unavoidable
  Poisson-style spread of about 1/sqrt(mean), so 96% of the resulting
  10.8% pace swing came from the number happening to be ~100, and only
  4% from the dispersion constant it looked like it was reusing.
  **Tuning `TEAM_ATTEMPTS_DISPERSION` could never have fixed it** — the
  floor is set by the size of the number, not by the constant.
- Real games vary 5.2%: possessions per team-game (FGA + 0.44*FTA + TOV
  - OREB) average 100.8 with sd 5.3, and a team's own night-to-night
  swing is the same 5.1%. The constant IS that measurement.
- Effect: team shot attempts sd 9.97 → 5.90 and points sd 17.57 → 13.96
  against real 6.72 and 12.16. Before this, 4.3% of simulated team-games
  were under 80 points where real basketball has 0.2%, and the sim
  produced 46- and 188-point team games (real range that season: 73 to
  157).
- **Standings-neutral** (MAE 5.58 → 5.62 across six seasons). A single
  season looked like a clear win (2023-24 MAE 5.45 → 5.32) and did NOT
  survive more seasons — recorded as neutral. The gain is entirely
  box-score realism, which no standings metric can see: both teams share
  the pace draw, so its excess spread cancels out of wins completely.
  Same blind spot as the double-counts.
- Found while calibrating player consistency — the shared pace swing was
  drowning out each player's own game-to-game signal, so it had to be
  right first.

## Accuracy tuning (sweep_constants.py)
- **The problem it found**: every tuned constant in game_engine.py was
  chosen honestly, by testing against real data — but almost all against
  ONE season, 2025-26. That hid a bias visible only league-history-wide:
  at `DEFENSE_AMPLIFICATION = 5` the sim spread win totals ~37% WIDER
  than real basketball (simulated st-dev 16.6 wins vs. a real 12.1), in
  all 30 seasons, no exception in either direction.
- **Why no earlier sweep could have caught it**: correlation is blind to
  it. Multiplying every team's distance from .500 by a constant leaves
  correlation mathematically UNCHANGED while making win-total error much
  worse — so the sim could rank all 30 teams nearly right and still miss
  every win total badly. `spread_ratio` (simulated win st-dev / real) is
  tracked as its own metric for exactly this reason.
- **A second, opposite bias underneath it**: with the spread error
  factored out, good defenses were over-predicted and good offenses
  under-predicted by nearly equal and opposite amounts (+0.33 / -0.31
  correlation with win error, same sign in all 29 seasons checked). Cause
  was a plain asymmetry: a team's own offense entered a simulated game at
  its literal real strength while the opponent's defense entered
  amplified 5x. Fixed by `OFFENSE_AMPLIFICATION`, the offensive mirror.
  Total structural bias 0.666 → 0.061.
- **Fit honestly, not curve-fit**: seasons are split by TIME, never
  shuffled — constants fit on 1996-97→2015-16 and scored on
  2016-17→2025-26, which the fit never sees. Results sort by HOLDOUT
  error. The two constants were swept JOINTLY (they trade against each
  other: offensive gain widens spread, pulling the best defensive gain
  down), because tuning one then the other only finds whatever the first
  pass left behind.
- **A third fix, found the same way**: with the two above landed, the
  biggest remaining signal in what the sim still got wrong was a team's
  real TURNOVERS (+0.355 across 862 team-seasons). Cause: ordinary
  turnovers were almost purely decorative. Only steal-caused ones did
  anything (~15%); the other ~85% were drawn into the box score with no
  other number depending on them. Note what was NOT wrong — a team's own
  turnovers should NOT reduce its own shot attempts, because a player's
  real FGA already reflects the turnovers they really committed
  (subtracting again is the SIXTH DEFENSE fix's double-counting trap).
  What was genuinely missing is the other half of the exchange: the
  possession the OPPONENT gains. So `TURNOVER_POSSESSION_WEIGHT` is
  applied strictly as an opponent effect, relative to league average.
  It tuned to exactly 1.0 — one turnover, one possession, the physically
  correct value with no fudge factor — beating 1.5 in all 8 head-to-head
  pairings against the other constants. A mechanism landing on its true
  real-world magnitude the moment it's allowed to exist is good evidence
  it was MISSING rather than a knob that flatters the fit.
- **A fourth fix: possessions weren't conserved.** Real basketball's two
  teams ALTERNATE possessions, so both finish a game within one or two of
  each other. This sim drew each team's shot volume independently: over
  600 games the possession differential had a standard deviation of 19.2,
  and 76% of games handed one team 6+ more possessions than its opponent.
  That's the same category of rule `TOTAL_GAME_MINUTES` already respects
  (240 minutes are a shared resource, split, never drawn per player) —
  possessions were the one such quantity still drawn independently.
  `PACE_COUPLING_WEIGHT` negotiates one shared pace, draws the game's
  pace randomness ONCE for both teams, and scales both to a single
  possession count with turnovers and offensive rebounds INSIDE the
  arithmetic (POSS ~ FGA + 0.44*FTA + TOV - OREB), so a team that gives
  it away takes correspondingly fewer shots. Differential 19.2 → 4.05,
  and holdout correlation .866 → .906 — the single largest correlation
  gain of any fix here.
- **A fifth fix, found by looking at a BOX SCORE rather than standings.**
  Simulated teams scored ~7 points per game too many in 2024-25 but only
  ~2 in the 1990s. Cause: a player's real per-game average is measured
  over games they ACTUALLY PLAYED, so adding up nine of them describes a
  team with all nine rotation players available every night, which no
  real team is. It grows with era because modern players miss far more
  games. Crucially, no standings metric could ever have found it — both
  teams inflate equally, so wins are unaffected and MAE and correlation
  are structurally BLIND to it. `ROSTER_AVAILABILITY_WEIGHT` calibrates
  against real data and re-measures per season (league-average team FGA
  IS league-average opponent FGA allowed, so `Team.opp_fga` is the exact
  target), correcting a 1990s season barely at all and a modern one a
  lot, with no era logic hardcoded anywhere. 2024-25 scoring +7.3 → +1.5
  points, FGA +4.7 → +0.6; 1996-97 moves only +0.3 → +0.1.
- **Result**: `DEFENSE_AMPLIFICATION` 5 → 1, `OFFENSE_AMPLIFICATION`
  0 → 0.5, `TURNOVER_POSSESSION_WEIGHT` 0 → 1.0, `PACE_COUPLING_WEIGHT`
  0 → 0.75, `ROSTER_AVAILABILITY_WEIGHT` 0 → 0.5, `SHORTHANDED_PENALTY`
  0 → 0.15 — all swept TOGETHER at the end, because each real mechanism
  restored takes over work the defensive gain was doing by brute force
  (it fell 5 → 2 → 1.5 → 1 as each landed). Final constants:
  `DEFENSE_AMPLIFICATION` 1, `OFFENSE_AMPLIFICATION` 0.5,
  `TURNOVER_POSSESSION_WEIGHT` 0, `PACE_COUPLING_WEIGHT` 0.75,
  `ROSTER_AVAILABILITY_WEIGHT` 1.0, `SHORTHANDED_PENALTY` 0.15. Across
  all 30 seasons: MAE 8.45 → 5.67, correlation .810 → .873, player FG%
  bias +1.49pp → -0.00pp, and every counting stat (reb/ast/stl/blk/tov/
  pf) within 0.15 of real. Standings sat as low as 5.31 at one point --
  the ~0.35 difference was given up DELIBERATELY, twice, to fix box
  scores (see the standings-vs-realism entry below).
- **Be honest that the last round was a TRADE, not a clean win.** Fixing
  the steal/block bug and simulating every real absence moved standings
  slightly the WRONG way (MAE 5.31 → 5.51, correlation .898 → .883)
  while eliminating the FG% bias entirely. Two reasons that's still the
  right call: the old 5.31 was partly MEASURING A BUG, the same trap
  `DEFENSE_AMPLIFICATION`'s original sweep fell into (a number that
  looks better because something is broken is not accuracy); and the sim
  now simulates ~11% more real absences, which is a harder and more
  honest problem. Also worth knowing: only `SHORTHANDED_PENALTY`,
  `DEFENSE_AMPLIFICATION` and `ROSTER_AVAILABILITY_WEIGHT` were re-swept
  after these changes — `PACE_COUPLING_WEIGHT`,
  `TURNOVER_POSSESSION_WEIGHT` and `OFFENSE_AMPLIFICATION` still hold
  values tuned BEFORE the bug fix, so a full six-constant sweep is the
  obvious next thing and would likely win some of that 0.2 back.
- **A sixth fix, and a cautionary one: removing a real BUG made accuracy
  WORSE, which was worth understanding rather than reverting.**
  `steal_rate_for`/`block_rate_for` read the injury-filtered roster, so a
  short-handed team looked like a weaker defence (see the FG% entry in
  Deferred). Fixing that took league-wide FG% bias from +2.03pp to
  ~0.00pp — but cost standings accuracy in injury-heavy seasons
  (2024-25 MAE 4.16 → 5.35), because the bug had been crudely capturing
  something REAL: a short-handed team plays worse than the sum of its
  available parts (forced lineups, worse spacing, minutes for players who
  shouldn't have them). So it became its own explicit mechanism,
  `SHORTHANDED_PENALTY` = 0.15, applied as the DIFFERENCE in availability
  between the two teams — self-normalising, so unlike the bug it replaces
  it can never shift the league-wide level. The lesson generalises: when
  removing a bug costs accuracy, the bug was probably standing in for a
  real effect, and the fix is to model that effect honestly rather than
  keep the bug.
- **THE most important lesson of this whole effort: sweeping on
  standings quietly trades away box-score realism.** Win totals are
  structurally blind to any bias that moves BOTH teams equally, so a
  sweep will happily accept a visibly wrong box score for a few
  hundredths of a game. It happened three separate times:
  `ROSTER_AVAILABILITY_WEIGHT` (a standings sweep picked 0.25, which
  scores teams +4.6 points per game too high, over 1.0 at +1.4 -- for
  0.06 games of MAE); `TURNOVER_POSSESSION_WEIGHT` (kept a setting where
  turnover-prone teams SCORED MORE, the opposite of real basketball);
  and the FG% bug, which survived because it inflated both teams. The
  fix is not a cleverer sweep -- it is checking BOTH every time, and
  preferring the basketball when they disagree cheaply.
- **Check the sim against real basketball's RELATIONSHIPS, not just
  real averages.** Comparing average-vs-average says whether the level
  is right; it cannot see a mechanism pointing the wrong way. Correlating
  team stats against each other and comparing that to the real
  correlation is what caught the turnover double-count: real teams that
  turn it over score notably less (-0.44) and the sim had essentially no
  relationship (-0.04).
- **Three double-counting bugs of one shape, all found this way.** A
  player's real per-game average ALREADY nets out the defence that really
  happened, so a sim that re-applies that defence has to remove it from
  the baseline first -- and the code was using `/(1-x)` where the algebra
  gives `x(1+x)`, which keeps RATIOS right while inflating COUNTS.
  Measured: turnovers ran +57% (2.21 vs 1.41 real, because simulated
  steals were added on top of a turnover average that already contained
  them), blocks +31%, steals +13%. All three now land within 0.05 of
  real. This is the same family as the SIXTH DEFENSE fix; when adding any
  new defensive effect, check whether the real stat already contains it.
- **The residual diagnostic is how all three mechanisms were found**, and
  it's the tool to reuse before chasing anything else: correlate each
  team's real stats against what the sim still gets wrong, and go after
  the largest signal. Every one has now collapsed — defense quality +0.67
  → ~0, turnovers +0.355 → +0.107, opponent pace +0.296 → +0.147, own
  pace +0.226 → +0.014. Nothing above +0.15 remains, which is the
  evidence that the easy mechanisms are gone, not a guess that they are.
- **Know the ceiling before chasing more** (see "How much accuracy is
  even left" in Status): remaining model error is ~1.2 games and ~.07
  correlation. Further gains have to come from finding another MISSING
  MECHANISM, the way turnovers were — not from more knob-turning, which
  is now firmly into diminishing returns.
- **Landing slightly narrower than real spread is correct**: the MAE
  optimum sits at ratio ~0.94-1.02, not exactly 1.0, because when the
  ranking is imperfect, shrinking predictions toward the mean beats
  matching real spread exactly. Values that hit 1.0 exactly score worse.
- **`OFFENSE_AMPLIFICATION` needed no new data**: a team's offense simply
  IS its players' real numbers, already loaded, so it's summed in
  `compute_league_averages` — no fetch, no cache file, no new `Team`
  field. (Unlike defense, which real per-player stats don't describe at
  all — hence `Team.opp_*`.) Precomputed per team NAME rather than read
  off a Team at game time, because by then its `players` list is usually
  filtered to who's available that night; recomputing from that would let
  a team's season-long offensive identity dip every time someone sits,
  double-counting an absence the roster draw already handled.
- **Its honest size**: most of the accuracy gain is `DEFENSE_AMPLIFICATION`.
  `OFFENSE_AMPLIFICATION`'s own win-total gain is small (6.17 → 6.07);
  what it genuinely fixes is RANKING — correlation rose with offensive
  gain at every defensive gain tried (1.5, 2, 2.5, 3) on the holdout
  seasons, which is why it's believed real rather than fit to quirks.
  Going higher overshoots: at 1.0 the offensive bias flips past zero from
  -0.221 to +0.140.
- **Snapshots are permanent**: `benchmarks/<season>_backtest.json` is the
  pre-tuning record and `<season>_tuned.json` the post — never overwrite
  a snapshot to reuse its name, that destroys the comparison the file
  exists for.

## Files (all in this folder, import each other by filename)
- `models.py` — `Player`, `Team`, `ScheduledGame` dataclasses. All
  percentages are computed `@property`s, never stored fields. `Player`
  also carries `scoring_spread`/`scoring_spread_games`/
  `scoring_spread_raw` (see "Player consistency"), all optional — `None`
  means "use the league average," never "perfectly consistent" — plus
  `consistency_rating`, the 1-99 display rating computed from them and
  the `CONSISTENCY_REFERENCE_*` table, returning `None` rather than a
  number when there is no honest answer.
- `data_source.py` — fetches + caches real rosters, per-game stats,
  schedule, team defense, conferences, injuries, roster membership and
  per-player scoring consistency via `nba_api`, one subfolder per season
  (`cache/<season>/`). The last three all come from ONE shared
  league-wide game-log fetch (`build_and_cache_player_history`), which
  writes only the files actually missing — so adding a new one backfills
  just that file across already-cached seasons. Run
  `python data_source.py --season 2013-14` (`--refresh` to force
  re-fetch; season defaults to 2025-26). See "Multi-season backtesting"
  above for the historical-team-name table and the game_id-digit
  regular-season filter this file also owns.
- `loader.py` — the ONLY file that reads the cache/*.json files
  directly; everything else works with real Player/Team objects. Every
  `load_*` function takes a `season` (default `DEFAULT_SEASON`).
  `player_consistency.json` is the one OPTIONAL cache file: it was added
  after every season was already cached, so a missing one just leaves
  every player on the league-average default rather than refusing to
  load the season.
- `db.py` — SQLite storage (`cache/season.db`) for simulated season
  games AND simulated injuries. DNP (0-minute) player-rows are
  deliberately NOT stored, so season averages are "per game played,"
  matching real stat convention. Also the only file with a "rebuild one
  stored game's full box score back into a real GameResult" function
  (`get_game_box_score`) and a "walk one team's games in order"
  function (`get_team_game_log`), both built for main.py's game-by-game
  replay.
- `game_engine.py` — the actual simulation (see above), including
  overtime, per-player scoring consistency (`_scoring_concentrations`,
  and `_dirichlet_multinomial_split`'s per-player concentration path)
  and the active-roster draw's numerical-safety floor (see
  "Multi-season backtesting" above). By far the largest/most complex
  file; every tunable constant has a comment explaining how and why it
  was tuned against real data.
- `injuries.py` — turns real absence data into a simulated season's
  injury calendar (see above). Also owns `enforce_minimum_roster` /
  `MINIMUM_AVAILABLE_PLAYERS`, the real "a team must dress eight" rule,
  applied by both season.py and benchmark_accuracy.py wherever a night's
  available roster is built.
- `transactions.py` — makes real in-season trades real in the sim (see
  above).
- `season.py` — simulates the full real 1,230-game schedule (~1 second).
  `simulate_season`'s `verbose` flag (see Game-by-game replay above)
  only ever gets turned off by main.py's interactive flow — every other
  caller (direct `python season.py`, benchmark_accuracy.py) still wants
  the printed summary.
- `playoffs.py` — seeding, play-in, bracket, Finals (see above).
- `benchmark_accuracy.py` — runs N full seasons of any single season
  (`--season`, defaults to 2025-26), saves a permanent accuracy
  snapshot to `benchmarks/*.json`. This is what every multi-season
  backtest run above actually is: 30 separate calls, one per season.
  Takes optional pre-fetched `real_standings` so sweep_constants.py
  doesn't re-fetch the same finished season's standings over the
  network once per candidate setting.
- `sweep_constants.py` — fits game_engine's constants against ALL 30
  backtested seasons at once, with a train/holdout split, rather than
  one season at a time. Sweeps one constant or several jointly:
  `python3 sweep_constants.py --constant DEFENSE_AMPLIFICATION,OFFENSE_AMPLIFICATION
  --values "1.5,2,2.5;0,0.5,1"`. Saves to `sweeps/*.json`. See "Accuracy
  tuning" above for what it found and why the holdout split matters.
- `offseason.py` — what changed between two consecutive real seasons
  (arrived / left / moved / renamed), plus `franchise_map` for following
  a team through a relocation. Pure data, no printing. See "Multi-season
  runs" above.
- `main.py` — the playable text CLI. Opens by asking which of the 30
  cached seasons to play (`loader.available_seasons` finds them by
  checking for the files each needs, so a newly fetched season is
  playable with no code change). Known real trade deadlines live in
  `TRADE_DEADLINE_BY_SEASON` — only seasons whose deadline is actually
  known are listed, and the replay's jump command says so plainly for
  the rest, rather than jumping to an invented date (deriving it from
  cached data was tried and fails: the roster data records WHEN a player
  first appeared for a team, not WHY, so the last such move is mid-April
  every season — buyout signings, not trades).
  Flow for a full season: simulate →
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
1. **Accuracy issues still open.** Two things that used to head this
   list are now FIXED — the `DEFENSE_AMPLIFICATION` tails problem (see
   "Accuracy tuning" below), and the "trades made accuracy worse"
   finding, which REVERSED once the constants were fixed. Retested
   across all 30 seasons at the tuned constants: trades-only MAE 7.66
   vs. 8.04 baseline, and 6.24 vs. 6.58 on top of injuries — i.e. real
   roster movement now HELPS by ~0.35 games either way, where the
   original single-season, pre-tuning measurement had it hurting by
   +0.49 (8.48 vs. 7.99). The over-amplified defense had been
   distorting it. The underlying modeling gap that was blamed at the
   time is still real and still unmodeled (traded-in/remaining players
   keep their own real per-minute rate rather than the real "usage
   bump" a team gives its pieces after losing a star) — it just wasn't
   costing accuracy the way the old number suggested.
   - **Player FG% is FIXED** (was ~+1.8pp above real, now ~0.0pp). It
     turned out not to be a calibration gap at all but a real bug, found
     by splitting the bias by code path: it was -0.08pp with injuries
     off and +1.98pp with them on. `steal_rate_for`/`block_rate_for`
     summed over the injury-FILTERED roster, so any short-handed team
     read as a weaker defence — and fewer blocks means fewer made
     2-pointers overturned into misses, inflating everyone's shooting.
     See `_available_rate_relative`, which now rates per MINUTE so that
     WHO is missing matters and HOW MANY doesn't.
   - **PLAYER CONSISTENCY is BUILT**, and now VISIBLE (was the top item
     here). Every player has his own scoring streakiness, taken from his
     real game logs, instead of one global constant making all 450
     players equally streaky — and `main.py`'s season-averages table
     shows it as a 1-99 CONS rating (`Player.consistency_rating`, a
     computed property, never stored). See "Player consistency" below
     for the whole thing — what it measures, why it works on shot volume
     rather than shooting %, why the RATING uses the raw spread while
     the SIM uses the minutes-stripped one, and why defensive
     consistency was tested and rejected.
   - **FREE THROWS are MOSTLY FIXED, with one piece deliberately left.**
     They were drawn purely from each player's own rate and connected to
     nothing, so they swung 13.8% against a real 32.2%. The missing
     piece was a LINK, not a random number: a team's free throws
     correlate +0.80 with the fouls the OTHER team committed, and the
     sim was already simulating fouls per player and using them for
     nothing but fouling out. See `FOUL_FREE_THROW_WEIGHT` and
     `_foul_pressure`. FTA sd 2.97 → 5.61 (real 7.00), FTA vs opponent
     fouls -0.02 → 0.78 (real 0.80), and extra trips now DISPLACE shots
     at the standard 0.44 possessions each, so free throws and field
     goals no longer rise together (+0.55 → +0.05, real -0.24).
     **What remains, and it is its own mechanism**: the two teams' free
     throws correlate +0.07 against a real +0.20 (it was +0.35 before —
     equally wrong the other way). Real officiating moves BOTH teams
     together; their fouls correlate +0.28 where the sim's, drawn
     independently, correlate -0.02. Note the simulated foul SPREAD is
     already right (sd 4.30 vs a real 4.15), so this is NOT about adding
     foul variance — it is about splitting the variance that already
     exists into a shared game-level part and an independent part, which
     means changing how `_simulate_fouls` draws. Left undone on purpose
     rather than half-done.
     Also worth knowing: `CAPPED_FOUL_SHARE` = 0.972 exists because the
     6-foul cap and `FOUL_OUT_LEAK_PROBABILITY` hold drawn fouls below
     their uncapped expectation BY DESIGN, so dividing by that
     expectation made every team read as fouling 2.8% less than normal
     every night. Re-measure it if any of those three change.
   - **THE SIM'S POSSESSION SWING IS ~40% TOO WIDE, IN EVERY ERA — the
     sharpest statement of the gap below, and the best target to aim at.**
     Real game-to-game possession swing runs 6.8% (1996-97) down to 5.3%
     (2024-25); the sim produces 9.5% down to 7.9% against those. The
     pace LEVEL is right everywhere (92.8 real vs 93.1 sim in 1996-97,
     101.2 vs 102.7 in 2024-25, within 1.5% in every era checked), and
     the shared pace draw is now fed each season's REAL variation — so
     the excess comes from the other draws stacked on top of it (usage
     splits, the active-roster draw, turnover/offensive-rebound draws),
     not from pace itself.
     Worth knowing: feeding the true per-season pace made 1996-97's team
     points spread slightly WORSE (14.33 → 14.95 against a real 12.22),
     because the too-small hardcoded constant had been partly cancelling
     this excess. Kept the truthful number anyway — this file's own
     lesson is that a value which looks better because something else is
     broken is not accuracy. Standings are neutral either way (MAE 5.67
     → 5.66 over six seasons at 30 runs).
   - **TEAM SCORING IDENTITIES ARE TOO SPREAD OUT — measured, cause NOT
     found, and explicitly not worth chasing further right now.**
     Simulated teams differ from each other in scoring far more than
     real teams do: between-team points-per-game standard deviation is
     7.15 against a real 4.25 (1.68x). The night-to-night half is fine
     (14.93 vs a real 12.16, 1.23x, mostly fixed by GAME_PACE_VARIATION).
     Standings cannot see this at all — wins come from point
     DIFFERENTIAL, so offense and defense being over-amplified together
     leaves the differential right while the scoring levels spread out.
     Same blind spot that hid the pace bug and the double-counts.
     **Already ruled out: it is not an amplification retune.** Sweeping
     `DEFENSE_AMPLIFICATION`/`OFFENSE_AMPLIFICATION` down together
     (1.0/0.5 → 0.75/0.375 → 0.5/0.25) barely moves the spread at all
     (1.61x → 1.62x → 1.62x) while standings degrade sharply (MAE 5.42 →
     5.81, correlation .890 → .871). Going to 0.25/0.125 finally reaches
     1.38x but costs MAE 6.24 and correlation .844 — a bad trade.
     So the cause is somewhere else and finding it is a real
     investigation, not a knob. Written down deliberately INSTEAD of
     being chased: this was found at the end of a long accuracy session,
     and the honest call was to stop rather than open another
     multi-step hunt. Pick it up fresh, or leave it — it is a realism
     nicety, not something that blocks anything.
   - **Assist consistency is real but SEPARATE**, and unbuilt. Assist
     streakiness is measured about as reliably as scoring, but it
     correlates only +0.05 with scoring streakiness — a streaky scorer
     is not a streaky passer, so it cannot ride along on one
     "offensive consistency" number and would need its own rating.
     Deliberately not folded into the scoring work.
2. **Simulating the offseason itself** (the user's "option B", deferred
   on purpose): letting a simulated season CHANGE the next one — a draft
   ordered by simulated standings, free-agency decisions, aging and
   progression. Needs player progression, which does not exist, and has
   no real data to validate against. The reporting half (option A) is
   DONE — see "Multi-season runs" above — and is the pipeline B would
   build on. The user also floated season AWARDS as a later addition.
3. **Playoffs on an old season: DONE** — era-specific rules are in
   (see "Playoffs" above). The one piece still wrong is pre-2004-05
   DIVISIONS in the seeding tiebreakers, documented there.
4. **Possession-by-possession realism** — explicitly low priority. Safe
   to defer indefinitely: `simulate_game()` is a swappable box for
   however a game gets produced, so nothing above it needs to change.
5. **Narrative/MVP-tracking features** — mentioned once as a maybe, no
   concrete plan yet.
6. **Playoff series box stats** (browsable, not just Finals averages) —
   wanted; not yet built. (Earlier note here said the user called this
   unnecessary — that was wrong, corrected 2026-09-04.)
7. **Key-injury highlighting** (e.g. flag high-PPG players specifically)
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
