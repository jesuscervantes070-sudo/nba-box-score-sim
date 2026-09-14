"""
Roles + Team Context Truth V1 -- lineup-aware runtime role normalization.

ROLE TRUTH (this player's real, historical, deployment-baseline value -- from `player_role_truth.py`)
vs RUNTIME ROLE (that baseline adjusted for who else is ACTUALLY on the floor right now) are kept
architecturally distinct: this module never mutates a `RoleTruthProfile`/`RoleTruthEstimate` -- it
only computes a derived, ephemeral set of weights for one specific active-player lineup, on demand.

Initiation and finishing are treated as SHARE-LIKE within an active lineup (the five players on
court cannot all be high-primary-initiators in the same sense -- their real per-36/share values are
renormalized to sum to 1 across whoever is actually active). Spacing is NOT forced into this
sum-to-one treatment (a lineup CAN have five real floor-spacers at once -- that is a real, physically
possible deployment, not a contradiction) -- spacing weights are returned as each player's own real
share value, unrenormalized, per this phase's own explicit "do not force all three into the same
normalization" instruction.

This is pure, deterministic arithmetic on already-computed real values -- no fitting, no I/O, safe
to call once per lineup change. It never reads or writes `PlayerSimulationProfile`/`RoleTruthProfile`
directly; callers pass in whatever real values they already have.
"""
from typing import Dict, Optional

# A missing player contributes 0 to the renormalization pool (their true role, if ever known, is
# simply absent from THIS lineup's context) -- never fabricated as a league-average stand-in.
_MISSING_CONTRIBUTION = 0.0


def _renormalize_shares(raw_values: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Real, positive raw values -> shares summing to 1 across the players that HAVE a real value.
    A player with no real evidence gets weight 0 (never fabricated), and does not distort the
    denominator computed from the other real players' own values."""
    real_values = {pid_: v for pid_, v in raw_values.items() if v is not None and v > 0}
    total = sum(real_values.values())
    if total <= 0:
        # no real evidence for anyone active -- an equal split is the one honest, symmetric
        # fallback (never an arbitrary single player), not a claim about any real deployment.
        n = len(raw_values)
        return {pid_: (1.0 / n if n else 0.0) for pid_ in raw_values}
    return {pid_: (real_values.get(pid_, _MISSING_CONTRIBUTION) / total) for pid_ in raw_values}


def renormalize_initiation_shares(active_initiation: Dict[str, Optional[float]]) -> Dict[str, float]:
    """`active_initiation`: {player_id: real role_off_initiation value or None} for the players
    CURRENTLY on the floor. Returns each player's SHARE of initiation responsibility within this
    specific lineup (sums to 1 across `active_initiation`'s own keys). Removing a high-initiation
    player from the input dict (e.g. they are off the floor) and recomputing raises every remaining
    player's share -- this is the intended, tested "absence redistributes opportunity" behavior,
    achieved purely by which players are IN the input set, never by mutating anyone's own truth
    value."""
    return _renormalize_shares(active_initiation)


def renormalize_finishing_shares(active_finishing: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Same contract as `renormalize_initiation_shares`, for finishing responsibility."""
    return _renormalize_shares(active_finishing)


def spacing_weights(active_spacing: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Spacing is NOT renormalized to sum to 1 -- returns each player's own real share value
    unchanged (0.0 for missing evidence, never fabricated). A lineup of five real floor-spacers is
    a legitimate real deployment, not a contradiction to be forced into a shared pool."""
    return {pid_: (v if v is not None else 0.0) for pid_, v in active_spacing.items()}
