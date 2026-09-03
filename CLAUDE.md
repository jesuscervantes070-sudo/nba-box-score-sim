# NBA Box-Score Simulator — Project Context

## What this is
A Python project that simulates NBA games and full seasons by generating
realistic box scores per player, built up from real per-game stats pulled
live from the NBA stats API. End goal: run a full season, then compare
simulated season averages against real ones to check accuracy.

This is NOT a possession-by-possession sim (no shot clock, no play-by-play
logic). It's a "box-score generator": each simulated game produces a full,
internally-consistent stat line for every player, derived from their real
season tendencies.

## Core design principle (do not violate)
**Everything must add up.** Specifically:
- Player PTS is always computed as `2*FGM2 + 3*FGM3 + FTM` — never simulated
  as its own independent number.
- FGM must never exceed FGA.
- Team totals (points, rebounds, assists, etc.) are always DERIVED by
  summing that game's player rows. They are never entered or simulated
  independently. This is enforced in `db.py`'s `insert_game()`.

## Planned generation order for a single game (not yet built)
1. Simulate team pace -> total possessions for the game (with variance)
2. Distribute possessions across players by usage rate -> each player's
   FGA/3PA/FTA for the game (Poisson/negative-binomial around their real
   per-game average, scaled by pace)
3. Roll makes from attempts using real FG%/3P%/FT% (binomial draw)
4. Compute PTS from makes (see principle above)
5. Simulate REB/AST/STL/BLK/TOV/PF per player independently (Poisson around
   their real average)
6. **Foul-out logic**: if a player's simulated PF reaches 6 before their
   expected minutes are used up, they foul out — remaining minutes for that
   game get redistributed across their team's other available players, and
   their box score is finalized early. This directly affects downstream
   stats (a player who fouls out early scores/rebounds less than usual).
7. Sum player rows -> team row automatically (guaranteed correct by design)

## Stats being tracked
MIN, PTS, REB (OREB + DREB split), AST, STL, BLK, TOV, PF, FGM/FGA,
FG3M/FG3A, FTM/FTA. Percentages (FG%, 3P%, FT%) are always computed
properties from makes/attempts, never stored as standalone fields.

## Files (all in this folder, import each other by filename)
- `models.py` — `Player` and `Team` dataclasses. Percentages are computed
  properties. `Player.from_dict()` builds a Player from a stats dict.
- `data_source.py` — fetches live rosters + current-season per-game stats
  via the `nba_api` package (wraps stats.nba.com), caches to
  `cache/rosters.json`. Run with `python data_source.py`, use `--refresh`
  to force re-fetch. Requires `pip install nba_api`.
- `loader.py` — reads `cache/rosters.json` and builds `Team`/`Player`
  objects for the rest of the sim to use.
- `db.py` — SQLite storage (`cache/sim_history.db`) for simulated game
  results. Key functions:
  - `init_db()` — creates tables if missing
  - `insert_game(conn, season, home_team, away_team, home_score,
    away_score, player_rows, game_number)` — writes a game; derives team
    totals from player_rows automatically
  - `get_season_player_averages(conn, player_name, season)` — averages a
    player's simulated games; this is what gets diffed against their real
    season average to measure sim accuracy
  - `get_standings(conn, season)` — derives W/L from stored game results

## Not yet built
- The actual game-simulation engine (the 7-step process above) — this
  produces the `player_rows` list that gets passed into `insert_game()`.
- Season scheduling (generating or importing a real matchup schedule to
  loop the game engine over).
- The accuracy-validation report (diffing simulated vs. real season
  averages per player, across the whole league).

## User context / preferences
- Building this to practice Python (non-CS background, learning
  fresh in a class rather than self-study) and to have a project for a
  summer grant program.
- Prefers working through design/architecture conversationally first,
  then implementing file-by-file.
- Wants things to actually be internally consistent/realistic, not just
  "look plausible" — flagged this explicitly as a priority multiple times.
