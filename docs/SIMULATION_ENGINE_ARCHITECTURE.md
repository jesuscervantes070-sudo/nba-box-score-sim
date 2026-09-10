# Simulation Engine Architecture

## Decision

The project intentionally supports two asymmetric simulation engines. They are
different execution profiles over one basketball model, not competing product
truths and not two independently evolving player models.

### Detailed possession engine

The detailed engine is the authoritative **causal** simulation path. It advances
an event-driven basketball world through explicit possession state, action
selection, resolution, and typed terminal outcomes. It is the future path for:

- watched games and generated play-by-play;
- tactical and lineup-sensitive simulation;
- possession-level analysis; and
- the generative long-horizon basketball world.

Phases 15-23A establish the state/event kernel, action gates, resolution
primitives, and an autonomous single-possession loop. It is not yet a complete
game engine: chained possessions, persistent game state, substitutions, and a
full detailed-game loop remain later work.

### Fast aggregate engine

`game_engine.py` remains a supported approximation for season and multi-season
simulation, benchmarks, and legacy product behavior. It produces aggregate
game/box-score outcomes without requiring those outcomes to emerge from a
possession event stream. It is intentionally faster and less causally granular
than the detailed engine.

The aggregate engine is not deprecated merely because the detailed engine is
more expressive. It is also not the architectural authority for possession
mechanics, tactical causality, watched games, or generated play-by-play.

## Shared player truth

Both engines must consume the same underlying player truth:

- stable player identity;
- latent abilities;
- tendencies;
- lineup/team roles;
- physical traits; and
- estimator provenance, confidence, and temporal cutoffs.

No engine-specific fork of player truth is permitted. An execution profile may
use a coarser projection of shared truth for speed, but it must not redefine the
player, silently drop provenance/cutoff rules, or maintain an independent rating
system.

The two engines should eventually be compatible at the level of final game and
box-score outputs. That means common schemas and basketball accounting
semantics, not identical seeded trajectories or identical results for the same
seed. Accuracy and calibration claims must be measured and reported separately
for each engine profile.

## Detailed-engine authority hierarchy

Within the detailed engine, authority is deliberately split by concern:

1. **Live possession/game state owns current basketball truth.** Ball control,
   clock, score context, possession phase, matchups, advantage, and other
   mutable world facts are read from and written to live state.
2. **The event stream owns accounting truth.** Events are the auditable record
   from which possession/game accounting and future play-by-play are derived.
3. **Typed terminal possession/game results own control flow.** Callers advance,
   chain, or stop simulation from explicit terminal results rather than
   inferring termination from incidental counters or provisional deltas.

`StatDeltas` are provisional convenience data. They may help a caller apply or
inspect a resolved event, but they are non-authoritative and must never become a
second scoreboard, box score, event log, or control-flow authority.

## Integration boundary

Phase 23A does not route `main.py`, season, playoffs, database flows, or
`game_engine.py` through the detailed engine. Product routing stays unchanged
until a later phase explicitly defines and validates that integration.

The next detailed-engine milestones are intentionally narrow:

- **Phase 23B:** chain possessions through persistent game state while
  preserving the authority hierarchy above.
- **Phase 23C:** build the minimal complete detailed-game loop.

Those milestones are roadmap boundaries, not designs specified by this
document.
