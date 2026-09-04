"""
The actual game-simulation engine: takes real per-game averages and
generates a single, realistic simulated game.

Three random-draw tools do all the work here:

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
    how many land heads" tool. Used for turning attempts into makes,
    weighted by the player's real shooting %, and for splitting a total
    into one of its real subsets (3PA out of FGA, OREB out of REB) using
    the player's real ratio as the split's odds -- this makes it
    STRUCTURALLY IMPOSSIBLE for the subset to exceed the total, rather
    than a rule we'd have to remember to enforce separately.

  - Dirichlet-Multinomial (_dirichlet_multinomial_split): splits a FIXED
    team-wide total across every player on the roster, using each
    player's real share as the split's expected proportion, but letting
    that split wobble realistically from game to game (controlled by
    USAGE_CONCENTRATION). This is the tool that fixes team-level
    realism -- see the long explanation below for why it was needed.

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
fraction of the time.

TEAM-LEVEL realism needed a THIRD, much bigger fix, found by testing a
full simulated game rather than one player at a time. Two compounding
problems showed up:

  1. Summing ~15-17 independently-random players makes their variances
     ADD UP, so even though each player was individually well-tuned
     (proven with Luka), team totals came out far more spread out than
     real teams ever are (simulated team scores were reaching 229-230 --
     the modern NBA single-game record is ~173).

  2. The deeper cause: a full NBA roster's real per-game minutes ADD UP
     to over 330 (checked directly against real 2025-26 data), but a
     real game only ever has 240 total player-minutes to hand out (5
     players x 48 minutes). The sim was generating independent stats for
     every rostered player as if they ALL played major, uncorrelated
     minutes every single night -- stacking far more independent noise
     into every team total than a real ~9-10 man rotation ever produces.

The fix: minutes became the one TRULY fixed team resource, split across
the roster from a real 240-minute pool (_simulate_team_minutes), and
every other stat (attempts, rebounds, assists, steals, blocks,
turnovers, fouls) is now generated from each player's real PER-MINUTE
rate times their ACTUAL simulated minutes for that game -- not their
flat full-game average. A player who only plays half their normal
minutes now produces roughly half as much of everything, which also
means fouling out finally reduces a player's whole stat line, not just
their minutes (previously minutes and every other stat were simulated
completely independently of each other, which was itself inconsistent).

Team-total shot attempts (which directly drive score) use the
Dirichlet-Multinomial split for the same reason minutes does: a
realistic, tunable TEAM total (TEAM_ATTEMPTS_DISPERSION), divided
across players by their minutes-scaled expected share, with a SEPARATE
tunable knob (USAGE_CONCENTRATION) for how much that division itself
wobbles game to game -- two independent knobs instead of one, which is
what a plain "give everyone their own independent random draw" approach
could never provide, no matter how it was tuned (there's a hard
mathematical floor -- a Negative Binomial's variance can never go below
its own mean -- that a single shared knob can't get under).
"""
from dataclasses import dataclass
from typing import List

import numpy as np

from models import Player, Team

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

# A real game always has exactly this many total player-minutes to hand
# out: 5 players on the floor at a time x 48 minutes. Fixed by the
# rules of the sport, not tunable, and not randomly drawn -- see the
# module docstring for why this matters so much.
TOTAL_GAME_MINUTES = 240

# Team-level dispersion for shot-attempt TOTALS (separate from the
# per-player DISPERSION above). Tuned tighter than a single player's own
# dispersion, on purpose -- team totals need to be much less spread out,
# relatively, than any one player's own attempts. Tuned by testing
# against the FINAL pipeline (active-roster + minutes-scaled attempts):
# at 2000, a 200+ point team game happens ~0.025% of the time (roughly
# once every 4000 simulated games) and 180+ about 0.33% of the time --
# rare enough to match "this has essentially never happened in real
# NBA history" without being flatly impossible. See module docstring.
TEAM_ATTEMPTS_DISPERSION = 2000

# How tightly a Dirichlet-Multinomial split sticks to each player's real
# expected share, for BOTH the minutes split and the attempts split.
# Higher = tighter (closer to real shares every game); lower = looser
# (more game-to-game variation in who gets how much of the shared
# total). Tuned by testing -- see module docstring.
USAGE_CONCENTRATION = 150


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


def _dirichlet_multinomial_split(weights: List[float], total: int, concentration: float) -> List[int]:
    """
    Split an integer `total` across len(weights) players, using `weights`
    as each player's real/expected SHARE, but letting that share wobble
    game to game by an amount controlled by `concentration` (higher =
    tighter, closer to the real weights every time; lower = looser, more
    game-to-game variation in who gets how much).

    This is a two-step random process, the standard statistical tool for
    exactly this job (dividing a fixed pool among several people with
    realistic, tunable randomness):
      1. Dirichlet draw: nudge the real shares into one specific game's
         "actual" shares for the night -- still adding up to 100%, just
         not perfectly matching the real averages.
      2. Multinomial draw: given those nudged shares and the fixed
         total, roll out actual whole-number counts -- guaranteed to
         add up EXACTLY to `total`, no rounding tricks needed.

    Used instead of giving every player their own fully independent
    random number, because independent draws made team totals wildly
    unrealistic once ~15 of them got added together -- see the module
    docstring for the full story of why.
    """
    if total <= 0 or not weights:
        return [0] * len(weights)

    weight_sum = sum(weights)
    if weight_sum <= 0:
        # Nobody had any real usage to base shares on -- fall back to
        # splitting evenly rather than dividing by zero.
        shares = [1.0 / len(weights)] * len(weights)
    else:
        shares = [w / weight_sum for w in weights]

    # numpy's dirichlet() requires every concentration value to be
    # strictly positive -- this tiny floor guarantees that (for a
    # player with a real 0% share) without meaningfully changing any
    # real player's actual share.
    alpha = [concentration * s + 1e-6 for s in shares]
    nudged_shares = _rng.dirichlet(alpha)
    return _rng.multinomial(total, nudged_shares).tolist()


def _apportion_team_total(raw_values: List[float], team_total: int) -> List[int]:
    """
    Rescale a list of players' raw values so they add up EXACTLY to
    `team_total`, keeping each player's share as close as possible to
    their original proportion -- the "largest remainder" method, the
    same idea used in real life to divide up parliament seats fairly:
      1. Scale every raw value by the same factor so they'd add up to
         team_total on average.
      2. Round each one DOWN (int() truncates, it doesn't round).
      3. Rounding down always leaves a few leftover units uncounted --
         hand those out one at a time to whoever's rounded-down amount
         was cut the most, until the total matches exactly.

    Unlike _dirichlet_multinomial_split, this adds NO extra randomness
    of its own -- it's used specifically for handing minutes back to
    teammates after a foul-out or a 48-minute cap, where a plain,
    mostly-deterministic proportional hand-back is what's wanted, not
    another random draw.
    """
    raw_sum = sum(raw_values)
    if raw_sum <= 0 or team_total <= 0:
        return [0] * len(raw_values)

    scale = team_total / raw_sum
    scaled = [v * scale for v in raw_values]
    floored = [int(v) for v in scaled]
    leftover = team_total - sum(floored)

    remainders = [(scaled[i] - floored[i], i) for i in range(len(raw_values))]
    remainders.sort(reverse=True)  # biggest leftover fraction first

    result = floored[:]
    for _, player_index in remainders[:leftover]:
        result[player_index] += 1
    return result


def _redistribute_leftover_minutes(minutes: List[float], eligible: List[bool], leftover: float) -> List[float]:
    """
    Hand `leftover` minutes back to whichever players are still
    eligible (eligible[i] is True), proportional to how much they're
    already playing. Used both when a player fouls out (their remaining
    minutes go to teammates, like a coach subbing someone in) and when
    a player's minutes get capped at 48 (the extra has to go somewhere).
    """
    if leftover <= 0:
        return minutes

    eligible_idx = [i for i, ok in enumerate(eligible) if ok]
    if not eligible_idx:
        return minutes  # nobody left to give it to -- extremely unlikely

    eligible_minutes = [minutes[i] for i in eligible_idx]
    bonus = _apportion_team_total(eligible_minutes, round(leftover))

    result = minutes[:]
    for idx, extra in zip(eligible_idx, bonus):
        result[idx] += extra
    return result


def _active_roster_for_game(team: Team) -> List[Player]:
    """
    Pick which players on `team` actually get run tonight. A real box
    score never has the whole 15-17 man roster playing meaningful
    minutes at once -- it's realistically the top 8-10 or so. Found by
    testing directly against real data: summing a FULL roster's real
    minutes comes out well over 300 (checked against the real Lakers:
    331.8), but the top 9 players by real minutes alone already sum to
    252.4 -- much closer to an actual game's 240-minute pool.

    This matters a lot: splitting the 240-minute pool across the WHOLE
    bloated roster was diluting every player's share far below their
    real minutes (a star was landing at only ~72% of their real minutes
    on average, dragging every other stat down with it, since they all
    scale off simulated minutes). Restricting to a realistic-sized
    active group first fixes that dilution at the source.

    Chosen by real minutes, highest first, stopping once the group's
    combined real minutes reaches the full 240-minute pool -- so it
    naturally sizes itself per team rather than assuming a fixed
    rotation depth. Anyone left off this list didn't play tonight
    (a real, normal thing -- "DNP - Coach's Decision").
    """
    sorted_players = sorted(team.players, key=lambda p: -p.min)
    active = []
    total_minutes = 0.0
    for player in sorted_players:
        active.append(player)
        total_minutes += player.min
        if total_minutes >= TOTAL_GAME_MINUTES:
            break
    return active


def _did_not_play(player: Player) -> Player:
    """A full, explicit zero-stat line for a player who isn't part of
    tonight's active roster (see _active_roster_for_game) -- kept as a
    real Player object, just with every stat at 0, rather than leaving
    them out of the results entirely."""
    return Player(name=player.name, team=player.team)


def _cap_minutes_at_max(minutes: List[float], protected: List[bool] = None) -> List[float]:
    """
    Enforce the 48-minute-per-player cap, handing any overflow back to
    teammates still under the cap -- same as a coach subbing someone
    else in. Shared by every place minutes get set or changed (the
    initial team split, AND the foul-out minutes redistribution), found
    by testing to both need it: handing freed-up or overflow minutes to
    a player already close to 48 can push THEM over the cap too, so
    this has to REPEAT until nobody is left over 48 (or a handful of
    passes have been tried, as a safety net against a pathological case
    that never fully settles -- in that rare case everyone left over is
    just hard-clipped, which can leave the team total a hair under 240
    rather than risk looping forever).

    `protected[i] = True` means player i should never receive overflow
    bonus minutes here, even if they're under the cap -- used for
    players who just had their minutes deliberately cut for fouling
    out, so a cap-overflow bonus can't quietly undo that reduction.
    """
    minutes = minutes[:]
    if protected is None:
        protected = [False] * len(minutes)

    for _ in range(5):
        overflow = 0.0
        for i, m in enumerate(minutes):
            if m > MAX_MINUTES:
                overflow += m - MAX_MINUTES
                minutes[i] = MAX_MINUTES
        if overflow <= 0:
            break
        eligible = [m < MAX_MINUTES and not protected[i] for i, m in enumerate(minutes)]
        minutes = _redistribute_leftover_minutes(minutes, eligible, overflow)
    else:
        minutes = [min(m, MAX_MINUTES) for m in minutes]
    return minutes


def _simulate_team_minutes(active_players: List[Player]) -> List[float]:
    """
    Decide how many minutes each of tonight's ACTIVE players (see
    _active_roster_for_game) gets. Minutes are the one truly fixed team
    resource in basketball -- a game always has exactly 240 total
    player-minutes to hand out. Splitting that fixed pool by real
    playing-time share (among only the players realistically sharing
    the floor tonight) is what makes team-level totals realistic --
    see the module docstring.
    """
    real_minutes = [p.min for p in active_players]
    minutes = [float(m) for m in _dirichlet_multinomial_split(real_minutes, TOTAL_GAME_MINUTES, USAGE_CONCENTRATION)]
    return _cap_minutes_at_max(minutes)

    return minutes


def _simulate_fouls(player: Player, minutes: float) -> tuple:
    """
    Simulate personal fouls for one player, scaled to how many minutes
    they're actually playing THIS game -- more court time means more
    chances to pick up fouls. Returns (personal_fouls, fouled_out).
    """
    real_foul_rate = player.pf / player.min if player.min else 0.0
    expected_pf = real_foul_rate * minutes
    raw_pf = _negative_binomial_count(expected_pf)

    if raw_pf >= FOUL_OUT_LIMIT:
        # See FOUL_OUT_LEAK_PROBABILITY above -- even reaching the
        # foul-out zone only actually disqualifies a player some of the
        # time, correcting for real coaches managing foul trouble in
        # ways a bare random draw has no way to know about.
        if _rng.random() < FOUL_OUT_LEAK_PROBABILITY:
            return FOUL_OUT_LIMIT, True
        return FOUL_OUT_LIMIT - 1, False
    return raw_pf, False


def _minutes_scaled_count(player: Player, real_total: float, minutes: float) -> int:
    """
    Draw one random count for a stat, centered on what this player
    would be EXPECTED to produce in `minutes` minutes, based on their
    real PER-MINUTE rate -- not their flat full-game average. A player
    who only plays half their normal minutes should produce roughly
    half as much of everything, not their usual full amount.
    """
    real_rate = real_total / player.min if player.min else 0.0
    expected = real_rate * minutes
    return _negative_binomial_count(expected)


def _finish_shooting(player: Player, fga: int, fta: int) -> tuple:
    """
    Given a player's final attempt counts for the game, roll the actual
    makes. Shared by both simulate_player_game and the real team-game
    pipeline, since the shooting-split math itself doesn't depend on
    HOW fga/fta were decided. Returns (fgm, fg3m, fg3a, ftm).
    """
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

    ftm = _binomial_draw(fta, player.ft_pct)
    return fgm, fg3m, fg3a, ftm


def simulate_player_game(player: Player) -> Player:
    """
    Generate one simulated game for `player` IN ISOLATION, using their
    flat per-game averages with no team or minutes context. Useful for
    quickly testing or inspecting a single player's own tendencies on
    their own. The real production path (simulate_game, below) uses
    _simulate_team_game instead, which additionally fixes team-total
    realism and scales every stat off ACTUALLY-simulated minutes -- see
    the module docstring for why that extra step is necessary.
    """
    fga = _negative_binomial_count(player.fga)
    fta = _negative_binomial_count(player.fta)
    fgm, fg3m, fg3a, ftm = _finish_shooting(player, fga, fta)

    reb = _negative_binomial_count(player.reb)
    real_oreb_rate = player.oreb / player.reb if player.reb else 0.0
    oreb = _binomial_draw(reb, real_oreb_rate)

    ast = _negative_binomial_count(player.ast)
    stl = _negative_binomial_count(player.stl)
    blk = _negative_binomial_count(player.blk)
    tov = _negative_binomial_count(player.tov)

    raw_pf = _negative_binomial_count(player.pf)
    if raw_pf >= FOUL_OUT_LIMIT:
        pf = FOUL_OUT_LIMIT if _rng.random() < FOUL_OUT_LEAK_PROBABILITY else FOUL_OUT_LIMIT - 1
    else:
        pf = raw_pf

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


@dataclass
class GameResult:
    """
    One full simulated game between two teams. Holds every player's
    simulated stat line for both sides -- nothing else. Scores are
    computed properties (below), never stored, for the exact same reason
    Player.pts is computed: a team's score must always be traceable back
    to its own players adding up, never an independent number that could
    disagree with them.
    """

    home_team: str
    away_team: str
    home_players: List[Player]  # each entry is one player's SIMULATED game line
    away_players: List[Player]

    @property
    def home_score(self) -> float:
        """Home team's final score = sum of its players' simulated PTS.
        Never simulated on its own -- this is what makes it impossible
        for a team's score to disagree with its own box score."""
        return sum(p.pts for p in self.home_players)

    @property
    def away_score(self) -> float:
        return sum(p.pts for p in self.away_players)


def _simulate_team_game(team: Team) -> List[Player]:
    """
    The real production path for simulating one team's game. Unlike
    simulate_player_game (which treats every player as fully
    independent), this ties the whole roster together through a shared,
    realistic 240-minute budget, and scales every other stat off each
    player's ACTUAL simulated minutes rather than their flat per-game
    average. See the module docstring for why this was necessary (team
    score realism) and what it also fixes for free (a fouled-out player
    now genuinely produces less of everything, not just fewer minutes).
    """
    # Step 0: who actually plays tonight? Restricting to a realistic-
    # sized active group (see _active_roster_for_game) BEFORE splitting
    # the 240-minute pool matters a lot -- splitting it across the
    # entire bloated roster was diluting every player's share far below
    # their real minutes.
    active_players = _active_roster_for_game(team)

    # Step 1: how much does each ACTIVE player play tonight? A fixed
    # 240-minute team pool, split by real playing-time share.
    minutes = _simulate_team_minutes(active_players)

    # Step 2: simulate fouls (scaled to each player's minutes this
    # game), and cut a fouled-out player's minutes short -- handing the
    # freed-up minutes back to their teammates, so the team's total
    # stays at 240, same as a real coach subbing someone else in.
    # Minutes are rounded to a whole number here (matching the whole-
    # minute granularity every other step already uses) specifically
    # so the freed-up amount is an exact integer too -- redistributing
    # a rounded-off fraction of a minute was previously letting a
    # team's total drift a hair below 240.
    pf_values = []
    fouled_out = []
    for player, mins in zip(active_players, minutes):
        pf, is_fouled_out = _simulate_fouls(player, mins)
        pf_values.append(pf)
        fouled_out.append(is_fouled_out)

    freed_minutes = 0.0
    for i, is_out in enumerate(fouled_out):
        if is_out:
            reduction = _rng.uniform(*FOUL_OUT_MINUTES_FACTOR_RANGE)
            reduced = round(minutes[i] * reduction)
            freed_minutes += minutes[i] - reduced
            minutes[i] = reduced
    if freed_minutes > 0:
        eligible = [not is_out for is_out in fouled_out]
        minutes = _redistribute_leftover_minutes(minutes, eligible, freed_minutes)
        # Handing freed-up minutes to teammates can push one of THEM
        # over 48 (found by testing) -- re-enforce the cap, protecting
        # fouled-out players so this can't hand their reduced minutes
        # back to them.
        minutes = _cap_minutes_at_max(minutes, protected=fouled_out)

    # Step 3: team-level shot attempts, based on each player's
    # MINUTES-SCALED expected attempts (their real per-minute rate x
    # tonight's actual minutes) rather than their flat real average --
    # so a player who played extra (or fewer) minutes tonight naturally
    # takes proportionally more (or fewer) shots. The team TOTAL is its
    # own separately-tuned random draw (TEAM_ATTEMPTS_DISPERSION), then
    # divided across players by their expected share (USAGE_CONCENTRATION).
    expected_fga = [(p.fga / p.min if p.min else 0.0) * m for p, m in zip(active_players, minutes)]
    expected_fta = [(p.fta / p.min if p.min else 0.0) * m for p, m in zip(active_players, minutes)]

    team_target_fga = _negative_binomial_count(sum(expected_fga), dispersion=TEAM_ATTEMPTS_DISPERSION)
    team_target_fta = _negative_binomial_count(sum(expected_fta), dispersion=TEAM_ATTEMPTS_DISPERSION)

    final_fga = _dirichlet_multinomial_split(expected_fga, team_target_fga, USAGE_CONCENTRATION)
    final_fta = _dirichlet_multinomial_split(expected_fta, team_target_fta, USAGE_CONCENTRATION)

    # Step 4: finish every active player's line -- shooting makes, and
    # every other counting stat scaled off their actual simulated
    # minutes.
    results = []
    for player, mins, pf, fga, fta in zip(active_players, minutes, pf_values, final_fga, final_fta):
        fgm, fg3m, fg3a, ftm = _finish_shooting(player, fga, fta)

        reb = _minutes_scaled_count(player, player.reb, mins)
        real_oreb_rate = player.oreb / player.reb if player.reb else 0.0
        oreb = _binomial_draw(reb, real_oreb_rate)

        ast = _minutes_scaled_count(player, player.ast, mins)
        stl = _minutes_scaled_count(player, player.stl, mins)
        blk = _minutes_scaled_count(player, player.blk, mins)
        tov = _minutes_scaled_count(player, player.tov, mins)

        results.append(Player(
            name=player.name,
            team=player.team,
            min=mins,
            fgm=fgm, fga=fga,
            fg3m=fg3m, fg3a=fg3a,
            ftm=ftm, fta=fta,
            reb=reb, oreb=oreb,
            ast=ast, stl=stl, blk=blk, tov=tov, pf=pf,
        ))

    # Step 5: anyone NOT in tonight's active group gets an explicit
    # zero-stat line (a real, normal thing -- "DNP - Coach's Decision")
    # rather than being silently dropped from the results.
    active_names = {p.name for p in active_players}
    for player in team.players:
        if player.name not in active_names:
            results.append(_did_not_play(player))

    return results


def simulate_game(home_team: Team, away_team: Team) -> GameResult:
    """
    Simulate one full game between two real Team objects: generate one
    simulated game for every player on both rosters (via
    _simulate_team_game, which ties each roster together through a
    realistic shared minutes budget -- see module docstring), and
    package the results into a GameResult. Every team-level number (the
    score, and later the team totals shown in the box score) is
    guaranteed to be a sum of real, individually-simulated player rows
    -- nothing about a team is ever generated independently of its own
    players.
    """
    home_players = _simulate_team_game(home_team)
    away_players = _simulate_team_game(away_team)

    return GameResult(
        home_team=home_team.name,
        away_team=away_team.name,
        home_players=home_players,
        away_players=away_players,
    )
