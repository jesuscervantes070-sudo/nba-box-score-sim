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
  0 → 0.75, `ROSTER_AVAILABILITY_WEIGHT` 0 → 1.0 — all swept TOGETHER at
  the end, because each real mechanism restored takes over work the
  defensive gain was doing by brute force (it fell 5 → 2 → 1.5 → 1 as
  each landed). Holdout MAE 8.25 → 5.09, correlation .812 → .905, spread
  ratio 1.374 → 1.038.
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
  percentages are computed `@property`s, never stored fields.
- `data_source.py` — fetches + caches real rosters, per-game stats,
  schedule, team defense, conferences, injuries, and roster membership
  via `nba_api`, one subfolder per season (`cache/<season>/`). Run
  `python data_source.py --season 2013-14` (`--refresh` to force
  re-fetch; season defaults to 2025-26). See "Multi-season backtesting"
  above for the historical-team-name table and the game_id-digit
  regular-season filter this file also owns.
- `loader.py` — the ONLY file that reads the cache/*.json files
  directly; everything else works with real Player/Team objects. Every
  `load_*` function takes a `season` (default `DEFAULT_SEASON`).
- `db.py` — SQLite storage (`cache/season.db`) for simulated season
  games AND simulated injuries. DNP (0-minute) player-rows are
  deliberately NOT stored, so season averages are "per game played,"
  matching real stat convention. Also the only file with a "rebuild one
  stored game's full box score back into a real GameResult" function
  (`get_game_box_score`) and a "walk one team's games in order"
  function (`get_team_game_log`), both built for main.py's game-by-game
  replay.
- `game_engine.py` — the actual simulation (see above), including
  overtime and the active-roster draw's numerical-safety floor (see
  "Multi-season backtesting" above). By far the largest/most complex
  file; every tunable constant has a comment explaining how and why it
  was tuned against real data.
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
   - **Player FG% still runs ~1.8pp above real** (sim minus real,
     league-wide) — now the single largest known inaccuracy left
     anywhere in the project. Untouched by any of the tuning below:
     verified flat across every constant value ever swept, so it's a
     genuinely separate problem, not a side effect. It TRENDS with era
     (~+1.1pp in the late 90s rising to ~+2.0pp by 2024-25, tracking
     the 3-point revolution), which is the strongest evidence yet for
     the "give the sim era context" idea.

     Worth knowing how to attack it, because standings metrics CANNOT
     find it: MAE and correlation are structurally blind to anything
     that inflates both teams equally. That's exactly how the
     `ROSTER_AVAILABILITY_WEIGHT` bias survived so long — it took
     comparing a simulated BOX SCORE against real per-game team
     numbers to see it at all. The same comparison is what should be
     pointed at FG% next: simulated vs. real league-average team FG%,
     2PT% and 3PT% separately, per season.
2. **An offseason bridge + season-picker UI** for the now-30-season
   backtest set (see "Multi-season backtesting" above) — diffing two
   seasons' rosters to show real draft/free-agency movement between
   them, and letting main.py actually pick which season to play.
3. **Playoffs on an old season** would need real era-specific rules
   restored (no play-in before 2019-20, best-of-5 first round before
   2003) — see "Multi-season backtesting" above. Not needed for
   anything built so far; backtesting never touches `playoffs.py`.
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
