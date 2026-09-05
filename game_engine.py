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

DEFENSE needed a FOURTH fix, found only after simulating a full season
and comparing it to the real 2025-26 standings: every real strong team
(Oklahoma City: 64 real wins) came back far too weak (39 simulated),
and every real weak team (Washington: 17 real wins) came back far too
strong (37 simulated) -- a systematic squeeze toward .500, not random
noise. The cause: a team's simulated score depended ENTIRELY on their
OWN real offensive stats, with zero regard for who they were playing --
OKC's real defense (allowing the league's lowest opponent FG%) had no
mechanism to actually suppress an opponent's shooting in the sim.

Two things were fixed together, both tying stats that were previously
decorative to what they actually do in real basketball:
  - Every shot's make-probability now blends the shooter's own real %
    with the DEFENDING team's real opponent-FG%-allowed (checked
    directly: OKC allows a real 0.438 opponent FG% against a 0.471
    league average -- genuinely the toughest defense in the league).
  - Steals and blocks, which previously never affected anything, now
    have real consequences: a steal removes a shot attempt before it
    happens (crediting a TOV to the shooter's team and a STL to the
    defense), and a block overturns an already-made 2-point shot into
    a miss (crediting a BLK to the defense). Both are scaled by the
    DEFENDING team's own real STL/BLK generation relative to league
    average, computed once via LeagueAverages/compute_league_averages
    -- so a defense's real steal/block numbers now determine how often
    these events actually happen, instead of being generated fully
    independently of the opponent they're supposedly happening to.

DEFENSE needed a FIFTH fix on top of that, found the same way -- by
simulating full seasons and comparing to real 2025-26 standings, not by
guessing. Blending in the LITERAL real ratio (the fix above) was a real
improvement but still left a big gap: a full season of simulated
standings only correlated 0.55 with the real ones (vs. a real
correlation-with-itself of 1.0), and that gap did NOT shrink by
averaging more simulated seasons together -- proving it wasn't random
noise evening out, but a genuine, systematic under-count of how much a
real defense's quality should matter.

The cause: real NBA defensive quality is narrow in absolute terms (OKC's
best-in-the-league real defense is only ~10% below league-average
opponent shooting) but shows up almost every single night for that
team. This sim ALSO adds real, deliberate per-game randomness on top of
every shot (the same DISPERSION-driven variance that makes a "wild
outlier" night possible for any player or team) -- and that necessary
noise was swamping the real, correct-but-modest defensive signal before
it could accumulate into a full season's standings the way it does in
real basketball.

DEFENSE_AMPLIFICATION fixes this WITHOUT touching any of that per-game
randomness (a good team can still have a bad night, same as before) --
it just scales up how far a real defense's factor is allowed to push
away from 1.0 (league average), strong enough for that real signal to
actually survive 82 games of noise. Tested by sweeping the multiplier
from 1x-8x against real 2025-26 standings: 4x was the sweep's sweet
spot -- correlation rose from 0.55 to 0.87 and mean win-total error
dropped from ~9.9 to ~6.3 games; going higher (6x, 8x) started pushing
simulated records WIDER than real ones actually spread (e.g. an 8x
factor produced a simulated 4-74 win range against a real ~17-64 one),
which made the error creep back up even as raw correlation kept
climbing. Worth being upfront about the tradeoff: the applied factor is
no longer the literal real ratio checked against real data above --
it's that real ratio, deliberately amplified to compensate for what
this sim's own necessary per-game randomness otherwise dilutes over a
season.

DEFENSE needed a SIXTH fix, found by checking a DIFFERENT number this
time -- not win/loss standings, but real-vs-simulated PLAYER shooting
%. That check found simulated FG% running ~3 percentage points below
real, across 425 real players averaged over 10 simulated seasons (real
per-game attempts also ran ~1.1 low). Isolating each mechanism (turning
steals/blocks off one at a time) traced it almost entirely to
blocks -- and to a lesser but real degree, steals -- not to the
DEFENSE_AMPLIFICATION fix above (which, tested the same way, added
only ~0.2 of those ~3 points; the rest was already there beforehand).

The actual cause: a real player's real FG% and real FGA are already
NET of however many of their real shots got blocked, or never became
attempts because they got stolen, on AVERAGE. block_rate_for and
steal_rate_for return the FULL real rate for a given defense (needed
so even a below-average defense still generates a realistic, non-zero
STL/BLK box score) -- but _finish_shooting was applying that FULL rate
directly on top of a % and volume that already had the LEAGUE-AVERAGE
version of that same effect baked in, silently blocking/stealing the
same shots twice over.

The fix: gross real_2pt_pct (in _finish_shooting) and expected_fga (in
_resolve_team_offense) back UP by the league-average per-make block
rate / per-attempt steal rate BEFORE the defender's full rate gets
applied -- so an exactly-average defense nets back out to a player's
real numbers instead of double-subtracting, while an above/below-
average defense still correctly pushes below/above them. The STL/BLK
counting stats credited to the defense still use the FULL rate,
unchanged, so every team's own box score still looks realistic.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

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

# A regulation game is 48 minutes long -- nobody can play more than
# that WITHIN regulation. Overtime (see OVERTIME_MINUTES below) is its
# own separate period with its own separate 5-minute cap per player,
# stacked on top of this one, not folded into it.
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

# How tightly the shot-ATTEMPTS Dirichlet-Multinomial split sticks to
# each player's real expected share. Higher = tighter (closer to real
# shares every game); lower = looser (more game-to-game variation in
# who gets how many shots). Tuned by testing -- see module docstring.
USAGE_CONCENTRATION = 150

# A SEPARATE, much tighter concentration used only for the MINUTES
# split -- found by testing that sharing USAGE_CONCENTRATION between
# minutes and attempts was a mistake: minutes and shot attempts aren't
# the same kind of randomness. A coach's rotation plan is fairly
# stable game to game (tight variance), while how many shots a player
# takes WITHIN their minutes can swing more freely. Sharing one knob
# was producing a player logging all 48 minutes of a game 2.2% of the
# time -- in real basketball that's essentially never. Tightening this
# specific value brought it down to a small fraction of a percent
# (a floor that a Dirichlet-Multinomial split can't fully eliminate,
# same idea as the Negative Binomial floor discussed earlier -- but
# far closer to realistic than sharing the looser attempts value did).
MINUTES_CONCENTRATION = 3000

# How strongly the active-roster weighted shuffle (see
# _active_roster_for_game) favors higher real-minute players. Raising
# real minutes to this power before weighting exaggerates the gap
# between them -- needed because plain real minutes (exponent 1) treats
# a team with a very flat, deep rotation (checked against Utah: top 5
# players all within 34.3-27.6 real minutes of each other) almost like
# a coin flip, which let real 30+ minute players sit out 1 in 3
# simulated games -- far too often for a real rotation regular. Tuned
# by testing against both a flat rotation (Utah) and a normal one
# (Lakers): at 8, Utah's real 30+ minute players land at 94-99.7%
# inclusion (still not a guarantee, matching real rest nights) while
# its borderline 25-28 minute players get a real, non-zero, 43-76%
# shot instead of the 0% a hard cutoff gave them -- and the Lakers'
# normal rotation is barely affected at all (stars still 99.6-100%).
ROTATION_WEIGHT_EXPONENT = 8

# How much stronger a real defense's effect on opponent shooting % gets
# made than its literal real ratio -- see the module docstring's FIFTH
# DEFENSE fix for the full experiment. 1 = the literal real ratio, no
# amplification. First tuned (sweeping 1x-8x) to 4, which got mean
# win-total error from ~9.9 down to ~6.3 games and real-vs-sim
# correlation from 0.55 to 0.87 -- but that sweep was run BEFORE the
# SIXTH DEFENSE fix (the block/steal double-counting bug below), which
# had been quietly doing some of this constant's job. Re-swept after
# that fix: 5 was the best fit (error ~6.3 -> ~7.8 games on the
# corrected baseline, correlation 0.85) -- worse than the old number
# LOOKED, but that old number was partly measuring a bug, not real
# accuracy.
#
# Then lowered 5 -> 2, by sweep_constants.py across all 30 backtested
# seasons instead of just 2025-26 (which is all either sweep above ever
# saw). Every single-season sweep had missed a bias that only shows up
# league-history-wide: at 5 the sim spread win totals ~37% WIDER than
# real basketball does, in all 30 seasons, with no exception in either
# direction. Correlation is blind to that -- multiplying every team's
# distance from .500 by a constant leaves it mathematically unchanged --
# so a sweep watching correlation and per-season error could rank all 30
# teams nearly right and still miss every win total badly, which is
# exactly what was happening.
#
# At 2 (30 seasons, 20 train / 10 holdout, fit never shown the holdout):
# holdout win-total error 8.25 -> 6.17 games, spread ratio 1.374 -> 0.940
# (1.0 = real), and correlation went UP, .812 -> .823. Train and holdout
# curves both bottom at 2 independently, which is what rules out this
# being fit to the past 30 seasons' quirks. Player FG% bias is flat
# across every value tried (+1.31 to +1.36), so this constant is not
# what drives that separate, still-open problem.
#
# Landing slightly NARROWER than real spread (0.940, not 1.000) is
# correct, not a miss: when the ranking is imperfect, shrinking
# predictions toward the mean beats matching the real spread exactly.
# The value that does hit ratio 1.0 (~2.3) measurably scores worse.
DEFENSE_AMPLIFICATION = 2

# The OFFENSIVE mirror of DEFENSE_AMPLIFICATION: how much a team's own
# real shooting quality gets exaggerated away from league average, the
# same way the opponent's real defense already is.
#
# 0 means "no extra amplification" -- a player simply shoots their own
# real percentage, which is this file's behavior for its entire history
# before this constant existed. It is deliberately the default so the
# machinery below is provably inert until a swept value turns it on.
#
# Why it needs to exist at all: fixing DEFENSE_AMPLIFICATION above
# uncovered a second, opposite bias that the over-spread had been hiding.
# Measured across 29 seasons, correlating each team's real offensive
# quality with the sim's win error:
#
#   DEFENSE_AMPLIFICATION   corr(defense, error)   corr(offense, error)
#            5                    +0.657                 -0.006
#            3                    +0.334                 -0.117
#            2                    -0.005                 -0.221
#
# At 5 the offense number looks like a clean zero -- but that is the
# over-spread masking it, not offense being modeled right. Lowering the
# defensive gain drives the defensive bias to essentially perfect
# (-0.005) and leaves a real offensive one behind (-0.221): teams with
# genuinely good real offenses keep getting UNDER-predicted.
#
# The cause is a plain asymmetry in how a simulated matchup is built. A
# team's own offense enters at its literal real strength, while the
# opponent's defense enters amplified -- so defense decides more of who
# wins than it should, no matter what the defensive gain is set to. The
# FIFTH DEFENSE fix's reasoning (a real but modest signal gets swamped by
# this sim's own necessary per-game randomness before it can accumulate
# over 82 games) applies just as much to offense; it had simply only ever
# been applied to one side of the ball.
#
# Tuned to 0.5 by sweeping it JOINTLY with DEFENSE_AMPLIFICATION (29
# combinations, 30 seasons, same train/holdout split). Jointly matters:
# the two knobs trade against each other, because adding offensive gain
# widens the simulated win spread and so pulls the best defensive gain
# down with it. Tuning one and then the other finds whatever the first
# pass happened to leave behind, not the best pair.
#
# What 0.5 buys, all on the 10 holdout seasons the fit never saw:
#   - win-total error 6.17 -> 6.07 games
#   - correlation with real standings .825 -> .842
#   - the offensive bias above, -0.221, essentially to zero (-0.003),
#     while the defensive one stays near zero too (+0.058); total
#     structural bias 0.666 -> 0.061 against the pre-tuning engine
#   - player FG% bias unmoved (+1.80 -> +1.78), so this buys standings
#     accuracy without paying for it in stat lines
#
# Worth being clear about what this constant is NOT. Its win-total gain
# alone is small -- the bulk of the accuracy improvement came from
# DEFENSE_AMPLIFICATION. What it genuinely fixes is RANKING: correlation
# rose with offensive gain at every defensive gain tried (1.5, 2, 2.5, 3)
# on the holdout seasons, which is why it's believed to be a real effect
# rather than the fit chasing the training seasons' quirks. Going higher
# overshoots and inverts the bias it exists to remove: at 1.0 the
# offensive correlation flips from -0.221 clean past zero to +0.140.
OFFENSE_AMPLIFICATION = 0.5

# A real NBA game that's still tied after regulation doesn't end -- it
# plays a 5-minute overtime period (5 players x 5 minutes = 25 team-
# minutes, the OT version of TOTAL_GAME_MINUTES/MAX_MINUTES above), and
# keeps playing MORE of them until somebody wins. Reuses the exact same
# tuned dispersion/concentration constants as regulation (there's no
# separate real PER-PERIOD data this project fetches to tune fresh ones
# against -- every real stat this sim has is a real per-GAME average),
# just fed a much smaller fixed pool -- see _simulate_period_defaults.
OVERTIME_MINUTES = 25
OVERTIME_MAX_MINUTES = 5.0


@dataclass
class LeagueAverages:
    """
    Real, LEAGUE-WIDE baselines computed once from every team's real
    stats (see compute_league_averages below) -- needed because "is
    this a good or bad defense" only means something relative to
    league average, not looked at on its own. Passed into simulate_game
    once per season/session rather than recomputed every game.
    """

    avg_opp_2pt_pct: float
    avg_opp_3pt_pct: float
    avg_team_stl: float
    avg_team_blk: float
    # The probability an average NBA defense steals/blocks a given shot
    # attempt -- derived directly from real league totals (steals per
    # shot attempt faced, blocks per 2-point attempt faced), not guessed.
    baseline_steal_prob: float
    baseline_block_prob: float

    # Each team's own real 2PT/3PT shooting percentage, and the league's,
    # for OFFENSE_AMPLIFICATION (see the offense factors below). Stored
    # per team NAME, computed once here, rather than re-summed from a
    # Team's players on every simulated game -- for speed, but mainly for
    # correctness: by the time a Team reaches simulate_game its `players`
    # list has usually been filtered down to who's actually available
    # that night (injuries in season.py, trade windows in
    # transactions.py). Recomputing from that filtered list would let a
    # team's season-long offensive IDENTITY dip every time someone sits,
    # double-counting an absence that the active-roster draw has already
    # accounted for by removing that player's production outright. This
    # matches how the defensive factors behave: they read Team.opp_*,
    # real stored season fields that roster filtering never touches.
    team_2pt_pct: Dict[str, float] = field(default_factory=dict)
    team_3pt_pct: Dict[str, float] = field(default_factory=dict)
    avg_team_2pt_pct: float = 0.0
    avg_team_3pt_pct: float = 0.0

    @property
    def avg_block_rate_per_make(self) -> float:
        """baseline_block_prob is blocks per 2-point ATTEMPT faced, but
        block_rate_for (and _finish_shooting, which applies it) work in
        blocks per 2-point MAKE (only a make can be overturned into a
        miss) -- this converts between the two (blocks-per-attempt /
        makes-per-attempt = blocks-per-make). block_rate_for reuses
        this SAME conversion, not its own -- found by testing that
        using the two independently, even though each was individually
        "correct", left them scaled differently from each other
        (block_rate_for still in attempt-units), so applying
        block_rate_for's result to a MAKE count removed less than
        _finish_shooting's gross-up (in true make-units) had added
        back, overshooting real FG% by +2.4 points in the OTHER
        direction instead of landing near zero."""
        return self.baseline_block_prob / self.avg_opp_2pt_pct if self.avg_opp_2pt_pct else 0.0

    def two_pt_defense_factor(self, defender: Team) -> float:
        """>1 means `defender` allows EASIER 2-point shooting than a
        league-average defense (weaker D); <1 means tougher. Amplified
        by DEFENSE_AMPLIFICATION -- see that constant and the module
        docstring's FIFTH DEFENSE fix for why the literal real ratio
        alone wasn't enough."""
        opp_2pt_makes = defender.opp_fgm - defender.opp_fg3m
        opp_2pt_attempts = defender.opp_fga - defender.opp_fg3a
        opp_2pt_pct = opp_2pt_makes / opp_2pt_attempts if opp_2pt_attempts else self.avg_opp_2pt_pct
        real_ratio = opp_2pt_pct / self.avg_opp_2pt_pct if self.avg_opp_2pt_pct else 1.0
        return 1 + DEFENSE_AMPLIFICATION * (real_ratio - 1)

    def three_pt_defense_factor(self, defender: Team) -> float:
        """Same real-ratio-amplified-by-DEFENSE_AMPLIFICATION idea as
        two_pt_defense_factor above, just for 3-point defense."""
        real_ratio = defender.opp_fg3_pct / self.avg_opp_3pt_pct if self.avg_opp_3pt_pct else 1.0
        return 1 + DEFENSE_AMPLIFICATION * (real_ratio - 1)

    def two_pt_offense_factor(self, attacker: Team) -> float:
        """>1 means `attacker` is a BETTER-than-league-average 2-point
        shooting team, so its players shoot above their own real
        percentages; <1 means worse. The exact mirror of
        two_pt_defense_factor, amplified by OFFENSE_AMPLIFICATION
        instead -- see that constant for the measured bias this exists
        to correct.

        Returns exactly 1.0 (a no-op) when OFFENSE_AMPLIFICATION is 0,
        which is its default, and also whenever this team's real
        shooting is unknown -- a team absent from team_2pt_pct can only
        mean it wasn't in the roster set compute_league_averages was
        built from, and inventing an offensive rating for it would be
        worse than leaving it at league-average."""
        if not OFFENSE_AMPLIFICATION or not self.avg_team_2pt_pct:
            return 1.0
        team_pct = self.team_2pt_pct.get(attacker.name)
        if not team_pct:
            return 1.0
        real_ratio = team_pct / self.avg_team_2pt_pct
        return 1 + OFFENSE_AMPLIFICATION * (real_ratio - 1)

    def three_pt_offense_factor(self, attacker: Team) -> float:
        """Same idea as two_pt_offense_factor, for 3-point shooting."""
        if not OFFENSE_AMPLIFICATION or not self.avg_team_3pt_pct:
            return 1.0
        team_pct = self.team_3pt_pct.get(attacker.name)
        if not team_pct:
            return 1.0
        real_ratio = team_pct / self.avg_team_3pt_pct
        return 1 + OFFENSE_AMPLIFICATION * (real_ratio - 1)

    def steal_rate_for(self, defender: Team) -> float:
        """Probability one shot attempt against `defender` gets stolen
        before it happens, scaled by how much better/worse `defender`'s
        real steal generation is than league average."""
        team_stl = sum(p.stl for p in defender.players)
        relative = team_stl / self.avg_team_stl if self.avg_team_stl else 1.0
        return self.baseline_steal_prob * relative

    def block_rate_for(self, defender: Team) -> float:
        """Probability one already-made 2-point shot against `defender`
        gets overturned into a blocked miss, scaled the same way.
        Built on avg_block_rate_per_make (blocks-per-MAKE units, not
        baseline_block_prob's raw blocks-per-ATTEMPT) -- see that
        property's docstring for why the two must share one
        conversion, not compute it separately."""
        team_blk = sum(p.blk for p in defender.players)
        relative = team_blk / self.avg_team_blk if self.avg_team_blk else 1.0
        return self.avg_block_rate_per_make * relative


def compute_league_averages(teams: Dict[str, Team]) -> LeagueAverages:
    """
    Computes real, league-wide baselines from every team's real data --
    call this ONCE (e.g. right after loader.load_teams()) and reuse the
    result for every simulated game, rather than recomputing it per game.

    Percentages are computed from SUMMED makes/attempts across the
    whole league, never by averaging each team's own percentage --
    same "derive from real totals" rule used everywhere else in this
    project, and it avoids letting a low-volume team's percentage
    count as much as a high-volume one.
    """
    total_opp_2pt_m = total_opp_2pt_a = 0.0
    total_opp_3pt_m = total_opp_3pt_a = 0.0
    total_stl = total_blk = total_fga = total_2pt_fga = 0.0
    # Each team's OWN real shooting, for the offensive factors -- see
    # LeagueAverages.team_2pt_pct for why these are precomputed here
    # rather than read off a Team at simulate_game time.
    team_2pt_pct: Dict[str, float] = {}
    team_3pt_pct: Dict[str, float] = {}
    total_2pt_m = total_2pt_a = total_3pt_m = total_3pt_a = 0.0

    for team in teams.values():
        total_opp_2pt_m += team.opp_fgm - team.opp_fg3m
        total_opp_2pt_a += team.opp_fga - team.opp_fg3a
        total_opp_3pt_m += team.opp_fg3m
        total_opp_3pt_a += team.opp_fg3a

        team_stl = sum(p.stl for p in team.players)
        team_blk = sum(p.blk for p in team.players)
        team_fga = sum(p.fga for p in team.players)
        team_fg3a = sum(p.fg3a for p in team.players)
        total_stl += team_stl
        total_blk += team_blk
        total_fga += team_fga
        total_2pt_fga += team_fga - team_fg3a

        # A team's own real shooting, summed from its real players --
        # no new fetch needed, because a team's offense simply IS its
        # players' real numbers (unlike its defense, which real per-
        # player stats don't describe at all, hence Team.opp_*).
        # Summing per-game averages across a whole roster overstates
        # any one game's raw volume, but these are only ever read as a
        # PERCENTAGE, where it's exactly the attempt-weighted average
        # this project uses everywhere else.
        team_fgm = sum(p.fgm for p in team.players)
        team_fg3m = sum(p.fg3m for p in team.players)
        team_2pt_m, team_2pt_a = team_fgm - team_fg3m, team_fga - team_fg3a
        if team_2pt_a:
            team_2pt_pct[team.name] = team_2pt_m / team_2pt_a
        if team_fg3a:
            team_3pt_pct[team.name] = team_fg3m / team_fg3a
        total_2pt_m += team_2pt_m
        total_2pt_a += team_2pt_a
        total_3pt_m += team_fg3m
        total_3pt_a += team_fg3a

    n_teams = len(teams)
    avg_opp_2pt_pct = total_opp_2pt_m / total_opp_2pt_a if total_opp_2pt_a else 0.5
    avg_opp_3pt_pct = total_opp_3pt_m / total_opp_3pt_a if total_opp_3pt_a else 0.36
    avg_team_stl = total_stl / n_teams if n_teams else 0.0
    avg_team_blk = total_blk / n_teams if n_teams else 0.0
    avg_team_fga = total_fga / n_teams if n_teams else 0.0
    avg_team_2pt_fga = total_2pt_fga / n_teams if n_teams else 0.0

    return LeagueAverages(
        avg_opp_2pt_pct=avg_opp_2pt_pct,
        avg_opp_3pt_pct=avg_opp_3pt_pct,
        avg_team_stl=avg_team_stl,
        avg_team_blk=avg_team_blk,
        baseline_steal_prob=(total_stl / n_teams) / avg_team_fga if avg_team_fga else 0.0,
        baseline_block_prob=(total_blk / n_teams) / avg_team_2pt_fga if avg_team_2pt_fga else 0.0,
        team_2pt_pct=team_2pt_pct,
        team_3pt_pct=team_3pt_pct,
        # Same "sum the real totals, then divide" rule as every other
        # percentage here -- never the mean of 30 team percentages.
        avg_team_2pt_pct=total_2pt_m / total_2pt_a if total_2pt_a else 0.0,
        avg_team_3pt_pct=total_3pt_m / total_3pt_a if total_3pt_a else 0.0,
    )


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

    WHO makes that group is a WEIGHTED RANDOM shuffle by real minutes,
    not a fixed cutoff -- found by testing a full simulated season that
    a fixed cutoff was a real bug: 244 of 522 real players across the
    league (47%) never appeared in a single simulated game, ALL SEASON,
    including genuine rotation players (a real 22-minutes-a-game player
    on the Kings, several 20+ minute players on the Jazz). A deep,
    balanced bench (several players clustered around 20-25 real
    minutes) meant a hard cutoff permanently locked out whoever fell
    just below the line, every single game -- real rotations vary
    night to night (matchups, rest, foul trouble), ours didn't at all.

    The weighting still strongly favors real stars (so they play almost
    every night, matching reality), but a borderline rotation player
    now has a real, non-zero chance of making the cut on any given
    night instead of a guaranteed zero across an entire season.
    Stopping once the group's combined real minutes reaches the full
    240-minute pool, same as before -- it still sizes itself
    naturally per team rather than assuming a fixed rotation depth.
    """
    weights = np.array([p.min for p in team.players], dtype=float)
    if weights.sum() <= 0:
        # Every player has 0 real minutes -- shouldn't happen for a
        # real roster, but falls back to an even chance for everyone
        # rather than dividing by zero.
        weights = np.ones(len(team.players))
    weights = weights ** ROTATION_WEIGHT_EXPONENT
    weights = weights / weights.sum()
    # A tiny positive floor on every entry, same reasoning (and same
    # 1e-6 size) as _dirichlet_multinomial_split's alpha floor below --
    # found by testing while backtesting old seasons: a long tail of
    # real sub-minute bench players, raised to ROTATION_WEIGHT_EXPONENT
    # (8), can numerically collapse one or more entries to an exact
    # (or effectively-zero, swallowed-by-rounding) 0.0 once normalized.
    # np.random.choice(replace=False) then raises "Fewer non-zero
    # entries in p than size" -- a real, if rare, crash (hit once in
    # 2007-08's data; nothing about it is specific to old data, any
    # season's injury/trade-reduced roster for one game could hit it).
    # This floor is negligible next to any real player's actual share
    # (their weight is already the dominant term), so it doesn't
    # change who's likely to make the active roster, only guarantees
    # every player has SOME nonzero chance, however small.
    weights = weights + 1e-9
    weights = weights / weights.sum()

    # A weighted shuffle: draws every player once, in an order where
    # higher real minutes make an EARLIER draw more likely -- not a
    # guarantee, just a strong tilt. Walking that order and stopping at
    # 240 minutes is what turns "more likely to be drawn early" into
    # "more likely to make the active group," while still leaving room
    # for a borderline player to occasionally get in ahead of someone
    # slightly ahead of them in real minutes.
    order = _rng.choice(len(team.players), size=len(team.players), replace=False, p=weights)

    active = []
    total_minutes = 0.0
    for index in order:
        player = team.players[index]
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


def _cap_minutes_at_max(minutes: List[float], protected: List[bool] = None,
                         max_minutes: float = MAX_MINUTES) -> List[float]:
    """
    Enforce a per-player minutes cap (48 for a regulation game,
    OVERTIME_MAX_MINUTES for one OT period -- see `max_minutes`),
    handing any overflow back to teammates still under the cap -- same
    as a coach subbing someone else in. Shared by every place minutes
    get set or changed (the initial team split, AND the foul-out
    minutes redistribution), found by testing to both need it: handing
    freed-up or overflow minutes to a player already close to the cap
    can push THEM over it too, so this has to REPEAT until nobody is
    left over (or a handful of passes have been tried, as a safety net
    against a pathological case that never fully settles -- in that
    rare case everyone left over is just hard-clipped, which can leave
    the team total a hair under its pool rather than risk looping
    forever).

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
            if m > max_minutes:
                overflow += m - max_minutes
                minutes[i] = max_minutes
        if overflow <= 0:
            break
        eligible = [m < max_minutes and not protected[i] for i, m in enumerate(minutes)]
        minutes = _redistribute_leftover_minutes(minutes, eligible, overflow)
    else:
        minutes = [min(m, max_minutes) for m in minutes]
    return minutes


def _simulate_team_minutes(active_players: List[Player], period_minutes: int = TOTAL_GAME_MINUTES,
                            max_minutes: float = MAX_MINUTES) -> List[float]:
    """
    Decide how many minutes each of tonight's ACTIVE players (see
    _active_roster_for_game) gets. Minutes are the one truly fixed team
    resource in basketball -- a game always has exactly 240 total
    player-minutes to hand out (or, for one overtime period, exactly
    OVERTIME_MINUTES -- see `period_minutes`). Splitting that fixed pool
    by real playing-time share (among only the players realistically
    sharing the floor tonight) is what makes team-level totals
    realistic -- see the module docstring.
    """
    real_minutes = [p.min for p in active_players]
    minutes = [float(m) for m in _dirichlet_multinomial_split(real_minutes, period_minutes, MINUTES_CONCENTRATION)]
    return _cap_minutes_at_max(minutes, max_minutes=max_minutes)


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


def _team_split_stat(
    active_players: List[Player], minutes: List[float], stat_name: str,
    dispersion: float, concentration: float,
) -> List[int]:
    """
    The general version of the fix originally built just for shot
    attempts, now reused for every counting stat that a real team total
    should be bounded by (rebounds, assists, steals, blocks, turnovers,
    as well as fga/fta): rather than letting each player's count vary
    fully independently (which stacks way more randomness into a team
    total than real basketball ever shows -- see the module docstring),
    decide a realistic TEAM total first, then split it across players.

    Each player's SHARE of that total is based on their real per-minute
    rate for `stat_name`, scaled by their ACTUAL simulated minutes
    tonight -- so a player who played extra (or fewer) minutes naturally
    gets a bigger (or smaller) expected share, not their flat full-game
    average regardless of tonight's minutes.
    """
    expected = [
        (getattr(player, stat_name) / player.min if player.min else 0.0) * mins
        for player, mins in zip(active_players, minutes)
    ]
    team_target = _negative_binomial_count(sum(expected), dispersion=dispersion)
    return _dirichlet_multinomial_split(expected, team_target, concentration)


def _finish_shooting(
    player: Player, fga: int, fta: int,
    steal_rate: float = 0.0, block_rate: float = 0.0,
    two_pt_factor: float = 1.0, three_pt_factor: float = 1.0,
    avg_block_rate_per_make: float = 0.0,
) -> Tuple[int, int, int, int, int, int, int]:
    """
    Given a player's final attempt counts for the game, resolves what
    actually happens to them AGAINST A SPECIFIC DEFENSE. Shared by both
    simulate_player_game (which leaves the defense knobs at their
    do-nothing defaults: no steals, no blocks, no % adjustment -- an
    "average, anonymous opponent") and the real team-game pipeline
    (which passes real opponent-derived values -- see
    LeagueAverages/compute_league_averages).

    Three defensive effects, applied in the order they'd actually
    happen in a real possession:
      1. STEALS: some attempts never become a shot at all -- removed
         before the 3PA/2PA split, so they can't also show up as a
         miss. Each one becomes a turnover for this player's team.
      2. SHOOTING %: the remaining attempts get resolved using this
         player's real % BLENDED with the defense's real opponent-%-
         allowed (two_pt_factor/three_pt_factor -- see LeagueAverages).
      3. BLOCKS: some of the makes from step 2 get overturned into
         blocked misses (2-point makes only -- 3-point blocks are rare
         enough in real basketball to not model here).

    Returns (fgm, fga, fg3m, fg3a, ftm, stolen, blocked). `fga` here is
    the REAL, final attempt count (after steals remove some) -- a shot
    that got stolen before it happened was never really an attempt at
    all, matching how a real box score counts it. `stolen`/`blocked`
    are events the DEFENSE gets credited for, which the caller
    aggregates across every shooter it defended into that defense's
    own STL/BLK.

    `avg_block_rate_per_make` fixes a real, measured bug (found by
    testing: real vs. simulated FG% was off by ~3 percentage points
    across 425 players, averaged over 10 simulated seasons): a
    player's real 2PT% is ALREADY net of however many of their real
    makes got blocked on average -- so applying `block_rate` in step 3
    on top of that real %, unchanged, was blocking the SAME shots
    twice: once implicitly (baked into the real % itself) and once
    explicitly (step 3 below). Step 2 grosses the real % back up by
    this league-average per-make block rate BEFORE applying step 3's
    FULL, defender-specific rate -- so an exactly-average-blocking
    defense nets back out to the real %, while an above/below-average
    one correctly pushes below/above it. `block_rate` itself stays the
    FULL rate (not just the above-average excess) specifically so a
    below-average-blocking defense still generates a realistic,
    non-zero BLK total in its own box score, not zero.
    """
    # -- Step 1: steals remove attempts before they become a shot ------
    stolen = _binomial_draw(fga, steal_rate)
    fga -= stolen

    # What fraction of this player's REAL shot attempts are 3-pointers?
    # Reusing that real rate as the split's odds is what keeps
    # fg3a <= fga guaranteed, no matter what gets randomly drawn.
    real_3pt_rate = player.fg3a / player.fga if player.fga else 0.0
    fg3a = _binomial_draw(fga, real_3pt_rate)
    two_pt_attempts = fga - fg3a

    # -- Step 2: shooting %, blended with the real defense faced --------
    real_2pt_makes = player.fgm - player.fg3m
    real_2pt_attempts = player.fga - player.fg3a
    real_2pt_pct = real_2pt_makes / real_2pt_attempts if real_2pt_attempts else 0.0
    # Gross real_2pt_pct back up to an "unblocked" baseline -- see this
    # function's docstring on `avg_block_rate_per_make` for why.
    unblocked_2pt_pct = (
        real_2pt_pct / (1 - avg_block_rate_per_make) if avg_block_rate_per_make < 1 else real_2pt_pct
    )
    blended_2pt_pct = unblocked_2pt_pct * two_pt_factor
    blended_3pt_pct = player.fg3_pct * three_pt_factor

    two_pt_makes = _binomial_draw(two_pt_attempts, blended_2pt_pct)
    fg3m = _binomial_draw(fg3a, blended_3pt_pct)

    # -- Step 3: blocks overturn some 2-point makes into misses ---------
    two_pt_makes_blocked = _binomial_draw(two_pt_makes, block_rate)
    two_pt_makes -= two_pt_makes_blocked
    blocked = two_pt_makes_blocked

    fgm = two_pt_makes + fg3m  # always <= fga, by construction, never checked separately

    ftm = _binomial_draw(fta, player.ft_pct)  # free throws are uncontested -- no defense to blend/block
    return fgm, fga, fg3m, fg3a, ftm, stolen, blocked


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
    # No opponent in isolated mode -- _finish_shooting's defaults (no
    # steals, no blocks, no % adjustment) mean an "average, anonymous
    # opponent" that doesn't change anything about this player's output.
    fgm, fga, fg3m, fg3a, ftm, _stolen, _blocked = _finish_shooting(player, fga, fta)

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


def _simulate_period_defaults(active_players: List[Player], period_minutes: int, max_minutes: float) -> tuple:
    """
    The period-agnostic core of _simulate_team_defaults: given an
    already-decided group of active players and how big THIS period's
    fixed minutes pool is (240/48 for a regulation game, OVERTIME_
    MINUTES/OVERTIME_MAX_MINUTES for one OT period -- see
    simulate_game), simulates their minutes, fouls, and the rebound/
    assist/turnover team-split for just that period. Extracted so
    regulation and overtime run through the exact same tuned math
    instead of overtime needing its own copy of it.

    Returns (active_players, minutes, pf_values, final_reb, final_ast,
    final_tov) -- final_tov here is only the "ordinary" (non-steal)
    turnovers; _resolve_team_offense adds steal-caused ones on top.
    """
    # Step 1: how much does each ACTIVE player play THIS period? A
    # fixed team-minutes pool, split by real playing-time share.
    minutes = _simulate_team_minutes(active_players, period_minutes, max_minutes)

    # Step 2: simulate fouls (scaled to each player's minutes this
    # period), and cut a fouled-out player's minutes short -- handing
    # the freed-up minutes back to their teammates, so the period's
    # total stays at its fixed pool, same as a real coach subbing
    # someone else in. Minutes are rounded to a whole number here
    # (matching the whole-minute granularity every other step already
    # uses) specifically so the freed-up amount is an exact integer too
    # -- redistributing a rounded-off fraction of a minute was
    # previously letting a team's total drift a hair below its pool.
    #
    # Note: this only catches a player reaching FOUL_OUT_LIMIT WITHIN
    # this one period's own random draw -- a player who enters an
    # overtime period already close to the limit (from regulation, or
    # an earlier OT period) is excluded from playing it AT ALL by
    # _overtime_eligible_roster before this function is ever called;
    # see _add_period_stats for the rare remaining case (crossing the
    # limit mid-period on top of fouls already carried in).
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
        # over the cap (found by testing) -- re-enforce it, protecting
        # fouled-out players so this can't hand their reduced minutes
        # back to them.
        minutes = _cap_minutes_at_max(minutes, protected=fouled_out, max_minutes=max_minutes)

    # Step 3: rebounds/assists/(non-steal) turnovers use the same
    # team-total-then-split pattern as minutes -- see the module
    # docstring for why letting each player vary fully independently
    # made team totals unrealistic.
    final_reb = _team_split_stat(active_players, minutes, "reb", TEAM_ATTEMPTS_DISPERSION, USAGE_CONCENTRATION)
    final_ast = _team_split_stat(active_players, minutes, "ast", TEAM_ATTEMPTS_DISPERSION, USAGE_CONCENTRATION)
    final_tov = _team_split_stat(active_players, minutes, "tov", TEAM_ATTEMPTS_DISPERSION, USAGE_CONCENTRATION)

    return active_players, minutes, pf_values, final_reb, final_ast, final_tov


def _simulate_team_defaults(team: Team) -> tuple:
    """
    Everything about a team's REGULATION game that does NOT depend on
    who they're playing: who's active tonight, their minutes, fouls,
    rebounds, and assists. (Shooting, steals, and blocks DO depend on
    the opponent's real defense -- that's _resolve_team_offense,
    below.) Overtime periods (see _play_overtime_period) reuse this
    same active roster rather than picking a fresh one -- only the
    minutes/fouls/reb/ast/tov step (_simulate_period_defaults) repeats
    per period.
    """
    # Who actually plays tonight? Restricting to a realistic-sized
    # active group (see _active_roster_for_game) BEFORE splitting the
    # 240-minute pool matters a lot -- splitting it across the entire
    # bloated roster was diluting every player's share far below their
    # real minutes.
    active_players = _active_roster_for_game(team)
    return _simulate_period_defaults(active_players, TOTAL_GAME_MINUTES, MAX_MINUTES)


def _resolve_team_offense(
    active_players: List[Player], minutes: List[float], attacker: Team, defender: Team,
    league_avg: LeagueAverages,
) -> tuple:
    """
    Resolves one team's shooting for the game AGAINST a specific
    opponent's real defense (`defender`). Team-total shot attempts
    still use the same total-then-split pattern as always; what's new
    is that turning those attempts into makes now runs through
    _finish_shooting's steal/%-blend/block mechanics for `defender`.

    Returns (per_player_shot_stats, defender_credit).
    per_player_shot_stats is a list of (fgm, fga, fg3m, fg3a, ftm, fta,
    stolen) tuples matching active_players' order -- the REAL final
    shooting numbers, ready to go straight into a box score (fga
    already has any stolen attempts removed, matching how a real box
    score counts it -- a steal isn't a missed shot, it's a turnover).
    `stolen` is returned per player too, so the caller can add it to
    THIS player's own turnovers. defender_credit = {"stl": total,
    "blk": total} -- the events `defender` earned shutting this
    offense down, which the CALLER credits to defender's own box score.

    expected_fga is grossed up by the league-average steal rate before
    the team-total draw -- the same real-vs-double-counted issue as
    _finish_shooting's block gross-up (see its docstring): a player's
    real FGA is already NET of however many of their real attempts
    got stolen on average (a stolen ball never became a real
    "attempt"), so subtracting an ADDITIONAL average-level steal rate
    below (via `_finish_shooting`'s FULL, defender-specific steal_rate)
    was removing attempts that real per-game data already accounts
    for. Measured impact: -1.11 attempts/game bias with this ungrossed;
    grossing up here is what lets an exactly-average-stealing defense
    reproduce real attempt volume instead of double-subtracting steals.
    """
    steal_gross_up = 1 - league_avg.baseline_steal_prob
    expected_fga = [
        ((p.fga / p.min if p.min else 0.0) * m) / steal_gross_up if steal_gross_up > 0 else
        (p.fga / p.min if p.min else 0.0) * m
        for p, m in zip(active_players, minutes)
    ]
    expected_fta = [(p.fta / p.min if p.min else 0.0) * m for p, m in zip(active_players, minutes)]

    team_target_fga = _negative_binomial_count(sum(expected_fga), dispersion=TEAM_ATTEMPTS_DISPERSION)
    team_target_fta = _negative_binomial_count(sum(expected_fta), dispersion=TEAM_ATTEMPTS_DISPERSION)

    assigned_fga = _dirichlet_multinomial_split(expected_fga, team_target_fga, USAGE_CONCENTRATION)
    assigned_fta = _dirichlet_multinomial_split(expected_fta, team_target_fta, USAGE_CONCENTRATION)

    steal_rate = league_avg.steal_rate_for(defender)
    block_rate = league_avg.block_rate_for(defender)
    # Both sides of the matchup scale the same shooting percentage: how
    # good `attacker` really is at scoring, and how good `defender` really
    # is at stopping it. Multiplied together rather than picking one,
    # because a real game is genuinely both at once -- and because at
    # OFFENSE_AMPLIFICATION = 0 the offensive half is exactly 1.0, which
    # leaves this line doing precisely what it did before it existed.
    two_pt_factor = (league_avg.two_pt_defense_factor(defender)
                     * league_avg.two_pt_offense_factor(attacker))
    three_pt_factor = (league_avg.three_pt_defense_factor(defender)
                       * league_avg.three_pt_offense_factor(attacker))
    avg_block_rate_per_make = league_avg.avg_block_rate_per_make

    per_player_shot_stats = []
    total_stolen = 0
    total_blocked = 0
    for player, fga, fta in zip(active_players, assigned_fga, assigned_fta):
        fgm, final_fga, fg3m, fg3a, ftm, stolen, blocked = _finish_shooting(
            player, fga, fta, steal_rate, block_rate, two_pt_factor, three_pt_factor,
            avg_block_rate_per_make,
        )
        # fta is untouched by steals (free throws aren't stolen, so the
        # assigned count is already the real final count).
        per_player_shot_stats.append((fgm, final_fga, fg3m, fg3a, ftm, fta, stolen))
        total_stolen += stolen
        total_blocked += blocked

    return per_player_shot_stats, {"stl": total_stolen, "blk": total_blocked}


def _split_credited_defense(
    active_players: List[Player], minutes: List[float], credited_total: int, stat_name: str,
) -> List[int]:
    """
    Splits a defensive total a team's defense ACTUALLY earned (steals
    or blocks -- computed by _resolve_team_offense while resolving the
    OPPONENT's shooting) across this team's own active players, weighted
    by their real per-minute rate for that stat. Same weighting idea as
    _team_split_stat, but the total itself is already known (a real
    event count), so there's no separate "draw a random team total"
    step -- only the split among players is randomized.
    """
    expected = [
        (getattr(player, stat_name) / player.min if player.min else 0.0) * mins
        for player, mins in zip(active_players, minutes)
    ]
    return _dirichlet_multinomial_split(expected, credited_total, USAGE_CONCENTRATION)


def _build_active_player_rows(
    active_players: List[Player], minutes: List[float], pf_values: List[int],
    final_reb: List[int], final_ast: List[int], final_base_tov: List[int],
    shot_stats: List[tuple], credited_defense: dict,
) -> List[Player]:
    """
    Builds one period's worth of real stat rows for a team's ACTIVE
    players only (no DNP rows -- see _assemble_team_players, which adds
    those, and _play_overtime_period, which has no DNP list of its own
    since it's ADDING onto an already-complete box score instead),
    combining everything decided elsewhere: minutes/fouls/reb/ast/
    base-tov (from _simulate_period_defaults), shooting + steal/block
    byproducts (from _resolve_team_offense against this game's specific
    opponent), and this team's own STL/BLK (this team's SHARE of the
    defensive credit it earned -- see _split_credited_defense).
    """
    final_stl = _split_credited_defense(active_players, minutes, credited_defense["stl"], "stl")
    final_blk = _split_credited_defense(active_players, minutes, credited_defense["blk"], "blk")

    results = []
    for player, mins, pf, reb, ast, base_tov, (fgm, fga, fg3m, fg3a, ftm, fta, stolen), stl, blk in zip(
        active_players, minutes, pf_values, final_reb, final_ast, final_base_tov, shot_stats, final_stl, final_blk,
    ):
        real_oreb_rate = player.oreb / player.reb if player.reb else 0.0
        oreb = _binomial_draw(reb, real_oreb_rate)

        results.append(Player(
            name=player.name,
            team=player.team,
            min=mins,
            fgm=fgm, fga=fga,
            fg3m=fg3m, fg3a=fg3a,
            ftm=ftm, fta=fta,
            reb=reb, oreb=oreb,
            ast=ast, stl=stl, blk=blk,
            tov=base_tov + stolen,  # this player's own turnovers, including ones stolen off them
            pf=pf,
        ))
    return results


def _assemble_team_players(
    team: Team, active_players: List[Player], minutes: List[float], pf_values: List[int],
    final_reb: List[int], final_ast: List[int], final_base_tov: List[int],
    shot_stats: List[tuple], credited_defense: dict,
) -> List[Player]:
    """
    Builds the final REGULATION Player rows for one team -- every
    active player's real stat row (_build_active_player_rows) plus an
    explicit zero-stat "DNP" row for anyone on the roster not in
    tonight's active group.
    """
    results = _build_active_player_rows(
        active_players, minutes, pf_values, final_reb, final_ast, final_base_tov, shot_stats, credited_defense,
    )
    active_names = {p.name for p in active_players}
    for player in team.players:
        if player.name not in active_names:
            results.append(_did_not_play(player))
    return results


def _overtime_eligible_roster(active_players: List[Player], current_players: List[Player]) -> List[Player]:
    """
    Regulation's active-roster Player TEMPLATES (real season-average
    rates -- needed by every downstream draw, so this can't just reuse
    tonight's box-score rows), filtered down to whoever hasn't fouled
    out yet. `current_players` is this team's box score AS OF RIGHT NOW
    (regulation, plus any earlier OT periods already merged in), used
    only to check accumulated fouls -- a real NBA rule: nobody re-enters
    after 6 personal fouls, for the rest of the game, overtime included.
    """
    fouls_by_name = {p.name: p.pf for p in current_players}
    eligible = [p for p in active_players if fouls_by_name.get(p.name, 0) < FOUL_OUT_LIMIT]
    if not eligible:
        # Every single active player has fouled out -- essentially never
        # happens even across several overtimes, but falls back to the
        # full active roster rather than crashing on an empty split.
        eligible = active_players
    return eligible


def _add_period_stats(base: Player, extra: Player) -> Player:
    """
    Adds one overtime period's stat line onto a player's running total
    for this game -- literal addition for every counting stat (the same
    "everything must add up" rule as the rest of this file), with one
    hard ceiling: personal fouls can never exceed FOUL_OUT_LIMIT (a real
    rule, not a tunable one). A player already at 5 fouls entering a
    period who then draws 2 more in it is clamped at 6, not 7 -- they
    fouled out PARTWAY through that period. This doesn't also try to
    shave down their minutes/stats for the fraction of the period they
    didn't actually play -- by the time that scenario happens, a game is
    already deep into extra overtimes, genuinely rare and not worth the
    extra complexity (see _overtime_eligible_roster for the very next
    period, which correctly excludes them from here on).
    """
    return Player(
        name=base.name, team=base.team,
        min=base.min + extra.min,
        fgm=base.fgm + extra.fgm, fga=base.fga + extra.fga,
        fg3m=base.fg3m + extra.fg3m, fg3a=base.fg3a + extra.fg3a,
        ftm=base.ftm + extra.ftm, fta=base.fta + extra.fta,
        reb=base.reb + extra.reb, oreb=base.oreb + extra.oreb,
        ast=base.ast + extra.ast, stl=base.stl + extra.stl,
        blk=base.blk + extra.blk, tov=base.tov + extra.tov,
        pf=min(base.pf + extra.pf, FOUL_OUT_LIMIT),
    )


def _merge_overtime_period(existing_players: List[Player], period_rows: List[Player]) -> List[Player]:
    """
    Folds one OT period's stat rows onto a team's existing box score
    (regulation, plus any earlier OT periods already merged in) -- see
    _add_period_stats. Anyone who didn't play THIS specific period (a
    DNP all game, or excluded this period for fouling out -- see
    _overtime_eligible_roster) is carried through completely unchanged.
    """
    period_by_name = {p.name: p for p in period_rows}
    return [
        _add_period_stats(existing, period_by_name[existing.name]) if existing.name in period_by_name else existing
        for existing in existing_players
    ]


def _play_overtime_period(
    home_team: Team, away_team: Team, home_active: List[Player], away_active: List[Player],
    home_players: List[Player], away_players: List[Player], league_avg: LeagueAverages,
) -> Tuple[List[Player], List[Player]]:
    """
    One more real NBA overtime period (5 minutes, 25 team-minutes)
    between the same two teams, restricted to whoever from regulation's
    active roster hasn't fouled out yet (_overtime_eligible_roster), run
    through the exact same offense/defense pipeline as regulation --
    just a smaller fixed minutes pool (see _simulate_period_defaults).
    Returns NEW box scores with this period's stats ADDED onto what was
    passed in (_merge_overtime_period), never replacing it -- called in
    a loop by simulate_game for as many periods as it takes to break a
    tie, same real "keep playing overtime until somebody wins" rule.
    """
    home_eligible = _overtime_eligible_roster(home_active, home_players)
    away_eligible = _overtime_eligible_roster(away_active, away_players)

    home_defaults = _simulate_period_defaults(home_eligible, OVERTIME_MINUTES, OVERTIME_MAX_MINUTES)
    away_defaults = _simulate_period_defaults(away_eligible, OVERTIME_MINUTES, OVERTIME_MAX_MINUTES)

    # Same cross-team dependency as regulation (see simulate_game): each
    # side's shooting this period is resolved against the OTHER side's
    # real defense.
    home_shot_stats, away_defense_credit = _resolve_team_offense(
        home_defaults[0], home_defaults[1], home_team, away_team, league_avg)
    away_shot_stats, home_defense_credit = _resolve_team_offense(
        away_defaults[0], away_defaults[1], away_team, home_team, league_avg)

    home_period_rows = _build_active_player_rows(*home_defaults, shot_stats=home_shot_stats,
                                                  credited_defense=home_defense_credit)
    away_period_rows = _build_active_player_rows(*away_defaults, shot_stats=away_shot_stats,
                                                  credited_defense=away_defense_credit)

    return (_merge_overtime_period(home_players, home_period_rows),
            _merge_overtime_period(away_players, away_period_rows))


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
    # How many extra 5-minute periods this game needed (0 for a normal
    # game decided in regulation) -- see simulate_game. Every player row
    # above already has any OT minutes/stats folded in (_add_period_
    # stats), so this is purely informational for display (e.g. main.py
    # marking a final score "F/OT"), never something a score/average is
    # computed from.
    overtime_periods: int = 0

    @property
    def home_score(self) -> float:
        """Home team's final score = sum of its players' simulated PTS.
        Never simulated on its own -- this is what makes it impossible
        for a team's score to disagree with its own box score."""
        return sum(p.pts for p in self.home_players)

    @property
    def away_score(self) -> float:
        return sum(p.pts for p in self.away_players)


def simulate_game(home_team: Team, away_team: Team, league_avg: LeagueAverages) -> GameResult:
    """
    Simulate one full game between two real Team objects, now with
    each side's defense actually affecting the other's shooting (see
    the module docstring's DEFENSE section for why this was needed).

    `league_avg` must come from compute_league_averages(all_teams) --
    computed once (e.g. right after loader.load_teams()) and passed in,
    not recomputed here every game.
    """
    home_defaults = _simulate_team_defaults(home_team)
    away_defaults = _simulate_team_defaults(away_team)
    home_active, home_minutes = home_defaults[0], home_defaults[1]
    away_active, away_minutes = away_defaults[0], away_defaults[1]

    # Each side's shooting is resolved against the OTHER side's real
    # defense -- this is the cross-team dependency that didn't exist
    # before. Resolving HOME's offense (defended by AWAY) earns AWAY
    # its steal/block credit, and vice versa -- naming these by WHO
    # EARNED the credit, not who's currently being resolved, so the
    # assembly step below reads unambiguously.
    home_shot_stats, away_defense_credit = _resolve_team_offense(
        home_active, home_minutes, home_team, away_team, league_avg)
    away_shot_stats, home_defense_credit = _resolve_team_offense(
        away_active, away_minutes, away_team, home_team, league_avg)

    home_players = _assemble_team_players(
        home_team, *home_defaults, shot_stats=home_shot_stats, credited_defense=home_defense_credit,
    )
    away_players = _assemble_team_players(
        away_team, *away_defaults, shot_stats=away_shot_stats, credited_defense=away_defense_credit,
    )

    # Real NBA rule: a game tied after regulation doesn't just end --
    # it plays a real overtime period (_play_overtime_period), and keeps
    # playing more of them until it isn't tied anymore. In practice this
    # is essentially always 0 or 1 extra period; the loop itself doesn't
    # cap how many it's willing to play, same as a real game never does.
    overtime_periods = 0
    while sum(p.pts for p in home_players) == sum(p.pts for p in away_players):
        overtime_periods += 1
        home_players, away_players = _play_overtime_period(
            home_team, away_team, home_active, away_active, home_players, away_players, league_avg,
        )

    return GameResult(
        home_team=home_team.name,
        away_team=away_team.name,
        home_players=home_players,
        away_players=away_players,
        overtime_periods=overtime_periods,
    )
