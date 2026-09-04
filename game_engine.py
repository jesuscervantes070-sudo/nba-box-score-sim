"""
The actual game-simulation engine: takes one Player's real per-game
averages and generates a single, realistic simulated game for them.

Two random-draw tools do all the work here:

  - Negative Binomial (_negative_binomial_count): generates a random
    whole-number COUNT centered on a real average -- mostly close to that
    average, but with a deliberately fat tail so genuinely historic
    good/bad nights are rare but possible, not just background noise.
    (A plain Poisson draw was tried first and rejected -- its spread is
    mathematically locked to always equal its mean, which made truly
    wild nights nearly impossible. Negative Binomial adds an independent
    "dispersion" knob that controls tail fatness without changing the
    average at all.)

  - Binomial (_binomial_draw): the classic "flip N weighted coins, count
    how many land heads" tool. Used for two different jobs here:
      1. Turning attempts into makes, weighted by the player's real
         shooting %.
      2. Splitting a total into one of its real subsets (3PA out of FGA,
         OREB out of REB) using the player's real ratio as the split's
         odds -- this makes it STRUCTURALLY IMPOSSIBLE for the subset to
         exceed the total, rather than a rule we'd have to remember to
         enforce separately.

DISPERSION below was tuned by actually testing it, not guessed: with
DISPERSION=30, a 22.8 FGA/game player (Luka-level shot volume) hits a
40+ shot night about 0.9% of the time (roughly once every season and a
half) and a sub-10-shot night about 1.4% of the time -- rare, but real,
the way actual "legendary" or "quiet" nights are, rather than either an
everyday occurrence or a statistical impossibility.

Fouling out needed an EXTRA fix beyond just dispersion, discovered by
testing: raising dispersion approaches a plain Poisson draw, but even
pure Poisson already gives a 4.7-real-PF/game player (the most
foul-prone real player in the league right now) a ~33% chance of
hitting 6 fouls in a single game -- a third of games is clearly wrong.
That's because real coaches actively manage foul trouble (sitting a
player down before it gets that bad), which a simple per-stat random
draw has no way to know about on its own. FOUL_OUT_LEAK_PROBABILITY
below patches that in directly: even when the random draw DOES reach
the foul-out zone, it only actually results in a foul-out some small
fraction of the time. Tuned so the same 4.7-PF player fouls out about
5% of games (~4 times a season -- rare, but real) and a disciplined
low-foul player like a 2.4-PF star drops to about 0.6% (essentially
never) -- deliberately erring toward "too rare" over "too frequent,"
since a foul-out swings a simulated game a lot.
"""
import numpy as np

from models import Player

# One shared random number generator for the whole module, so every draw
# in a simulated game pulls from the same underlying random stream.
_rng = np.random.default_rng()

# Tunable "fatness" of the random tails -- smaller number = wilder, more
# frequent outlier games; larger number = tighter around the real
# average, closer to a plain Poisson draw. See the module docstring
# above for how this specific value was chosen.
DISPERSION = 30

# A real NBA player is disqualified the moment they reach 6 personal
# fouls -- a hard rule, not a tunable one.
FOUL_OUT_LIMIT = 6

# A game (ignoring overtime, which this sim doesn't model yet) is 48
# minutes long -- nobody can play more than that here.
MAX_MINUTES = 48.0

# When a player fouls out, there's no real game clock in this sim to know
# exactly which minute it happened -- so it's approximated by randomly
# cutting their simulated minutes down into this range, representing
# "pulled from the game earlier than usual" without claiming to know
# precisely how much earlier.
FOUL_OUT_MINUTES_FACTOR_RANGE = (0.5, 0.85)

# Even when the raw random foul count reaches the foul-out zone (6+),
# only let it actually count as a foul-out this fraction of the time --
# see the module docstring above for why this exists and how it was
# tuned. Lower = fouling out becomes rarer overall.
FOUL_OUT_LEAK_PROBABILITY = 0.15


def _negative_binomial_count(mean: float, dispersion: float = DISPERSION) -> int:
    """One random whole-number count, centered on `mean`, with spread
    controlled by `dispersion` (smaller = fatter tails)."""
    if mean <= 0:
        return 0
    # numpy's negative_binomial takes (successes_needed, success_probability),
    # not (mean, dispersion) directly -- this line converts between the
    # two so the rest of this file only has to think in terms of "the
    # real average" and "how much it should vary," never numpy's
    # internal parameters.
    p = dispersion / (dispersion + mean)
    return int(_rng.negative_binomial(dispersion, p))


def _binomial_draw(n: int, rate: float) -> int:
    """Flip `n` weighted coins (each with probability `rate` of landing
    'yes') and return how many landed 'yes'. Used both for turning
    attempts into makes, and for splitting a total into a real subset."""
    if n <= 0:
        return 0
    # Clip to [0, 1] as a safety net -- real per-game ratios should
    # already be valid probabilities, but this guards against any rare
    # rounding artifact in the source data ever crashing the sim.
    rate = min(max(rate, 0.0), 1.0)
    return int(_rng.binomial(n, rate))


def simulate_player_game(player: Player) -> Player:
    """
    Generate one simulated game for `player`, based on their real
    per-game averages. Returns a brand-new Player object representing
    just that one game -- reusing the Player class is intentional: a
    single game's stat line has the exact same shape as a season
    average (same fields), just built from a random draw instead of a
    real-world average. That also means the returned object gets PTS,
    FG%, 3P%, FT%, and DREB computed for free, with no extra code needed.
    """
    # -- Shooting: total attempts, then split real vs. simulated -------
    fga = _negative_binomial_count(player.fga)
    # What fraction of this player's REAL shot attempts are 3-pointers?
    # Reusing that real rate as the split's odds is what keeps
    # fg3a <= fga guaranteed, no matter what gets randomly drawn.
    real_3pt_rate = player.fg3a / player.fga if player.fga else 0.0
    fg3a = _binomial_draw(fga, real_3pt_rate)
    two_pt_attempts = fga - fg3a

    # Real shooting percentages for each shot type, so the makes rolled
    # below reflect how well this specific player actually shoots.
    real_2pt_makes = player.fgm - player.fg3m
    real_2pt_attempts = player.fga - player.fg3a
    two_pt_pct = real_2pt_makes / real_2pt_attempts if real_2pt_attempts else 0.0

    two_pt_makes = _binomial_draw(two_pt_attempts, two_pt_pct)
    fg3m = _binomial_draw(fg3a, player.fg3_pct)
    fgm = two_pt_makes + fg3m  # always <= fga, by construction, never checked separately

    # -- Free throws: a separate opportunity, not tied to fga ----------
    fta = _negative_binomial_count(player.fta)
    ftm = _binomial_draw(fta, player.ft_pct)

    # -- Rebounds: same total-then-split pattern as shooting -----------
    reb = _negative_binomial_count(player.reb)
    real_oreb_rate = player.oreb / player.reb if player.reb else 0.0
    oreb = _binomial_draw(reb, real_oreb_rate)

    # -- Everything else: independent counts around the real average ---
    ast = _negative_binomial_count(player.ast)
    stl = _negative_binomial_count(player.stl)
    blk = _negative_binomial_count(player.blk)
    tov = _negative_binomial_count(player.tov)

    # Fouls: draw the raw count, then apply the foul-out leak (see
    # FOUL_OUT_LEAK_PROBABILITY above) -- if the raw draw reaches the
    # foul-out zone but the leak check fails, pull it back to 5 (a
    # rough, foul-trouble night, but not a disqualification).
    raw_pf = _negative_binomial_count(player.pf)
    if raw_pf >= FOUL_OUT_LIMIT:
        pf = FOUL_OUT_LIMIT if _rng.random() < FOUL_OUT_LEAK_PROBABILITY else FOUL_OUT_LIMIT - 1
    else:
        pf = raw_pf

    # -- Minutes: normal variance, further cut short if fouled out ------
    minutes = min(_negative_binomial_count(player.min), MAX_MINUTES)
    if pf >= FOUL_OUT_LIMIT:
        reduction = _rng.uniform(*FOUL_OUT_MINUTES_FACTOR_RANGE)
        minutes = minutes * reduction

    return Player(
        name=player.name,
        team=player.team,
        min=minutes,
        fgm=fgm, fga=fga,
        fg3m=fg3m, fg3a=fg3a,
        ftm=ftm, fta=fta,
        reb=reb, oreb=oreb,
        ast=ast, stl=stl, blk=blk, tov=tov, pf=pf,
    )
