"""
Availability + Expected Minutes + Rotations V1 -- engine-consumption adapter.

============================ SUBSTITUTION/ROTATION ENGINE AUDIT ============================
Checked directly: `detailed_game.py`, `detailed_game_orchestrator.py`, and
`possession_orchestrator.py` contain NO substitution, quarter, clock, or in-game-minutes mechanism
of any kind (grepped for "substitution"/"rotation"/"quarter"/"bench"/"starter" -- zero real hits in
any of the three). `simulate_detailed_game(home_team, away_team, home_five, away_five, profiles,
rng_seed)` takes exactly one FIXED five-man lineup per side for the ENTIRE simulated game -- there
is no mechanism to sub a player in in-game, no per-player minutes tracked or consumed by the
resolvers, no starter/bench distinction read anywhere in the frozen engine.

CLASSIFICATION: **ABSENT.** This is reported honestly rather than silently redesigned around --
per this phase's own explicit instruction, "do not build an entirely new substitution engine
silently." No hook exists to wire a `TeamRotationSnapshot`'s full minute allocation into.

THE ONE REAL, NON-INVENTED INTEGRATION POINT: since the engine only ever consumes one fixed
five-man lineup per side, the only honest way a real rotation snapshot can affect a simulated game
is by choosing WHICH five real players occupy that fixed lineup -- `primary_five` below does
exactly that (the top 5 real players by `expected_minutes`/actual minutes), and nothing more. This
does not simulate bench rotation, fatigue, or substitution -- it selects participants, which is
this phase's own stated V1 goal ("correct active players + reasonable total minutes/exposure. Not
exact substitution patterns").
"""
from typing import Tuple

from player_rotation_truth import TeamRotationSnapshot


def primary_five(snapshot: TeamRotationSnapshot) -> Tuple[str, ...]:
    """The 5 real player_ids with the highest `expected_minutes` (oracle: real minutes; pregame:
    the allocated expected minutes) in this snapshot -- the one real, non-invented way a rotation
    snapshot can feed the frozen, fixed-five-man-lineup detailed engine. Fewer than 5 available
    players (a real, if unusual, historical case) returns however many are real and available --
    never pads with a fabricated player_id."""
    ranked = snapshot.top_n(5)
    return tuple(p.player_id for p in ranked if p.expected_minutes > 0)
