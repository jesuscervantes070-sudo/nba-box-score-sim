"""
Core data models for the sim: Player and Team.

Design rule this whole file exists to protect: PTS and every shooting
percentage are NEVER stored as their own number. They're always calculated
from the real counting stats (makes/attempts) underneath them, so it's
mathematically impossible for them to disagree with each other or drift
out of sync as the sim runs.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Player:
    """
    One NBA player's PER-GAME AVERAGES -- the same kind of numbers you'd see
    on a real season stat page. This class is just the data shape; the
    simulation logic that generates a single GAME from these averages
    doesn't exist yet -- that's a separate, later step.

    A @dataclass is Python's shorthand for "a class that's mostly just
    fields" -- it auto-generates the constructor (__init__) for us based on
    the fields listed below, so we don't have to write it by hand. Think of
    it like a Java class where every field also gets a matching constructor
    parameter for free.
    """

    # -- Identity -----------------------------------------------------
    name: str
    team: str

    # -- Stored fields: the real, independent inputs -------------------
    # These are the numbers that actually come from real per-game stats.
    # Nothing below this point is derived from anything else -- these are
    # the "source of truth" the computed properties further down are built
    # from.
    min: float = 0.0   # minutes per game

    fgm: float = 0.0    # total field goals MADE per game (this already
    fga: float = 0.0    # includes 3-pointers -- see fg3m/fg3a below)
    fg3m: float = 0.0   # 3-pointers made per game (a SUBSET of fgm, not separate from it)
    fg3a: float = 0.0   # 3-pointers attempted per game (a subset of fga)
    ftm: float = 0.0    # free throws made per game
    fta: float = 0.0    # free throws attempted per game

    reb: float = 0.0    # total rebounds per game
    oreb: float = 0.0   # offensive rebounds per game (a subset of reb)

    ast: float = 0.0    # assists per game
    stl: float = 0.0    # steals per game
    blk: float = 0.0    # blocks per game
    tov: float = 0.0    # turnovers per game
    pf: float = 0.0     # personal fouls per game -- will matter later for foul-out logic

    # -- How STREAKY this player's scoring really was ------------------
    # Measured from his real game logs (data_source.fetch_player_consistency,
    # cached per season), not typed in by hand: how far his points swung
    # from night to night, relative to the swing pure chance alone would
    # produce, once the games where he simply played more or fewer minutes
    # are accounted for. 1.0 would mean "as steady as it is physically
    # possible to be"; real players run about 1.2 to 1.9 on this.
    #
    # Optional because not every player has one -- somebody with a single
    # real game has no measurable swing at all, and older cached seasons
    # may predate the file. None means "use the league average" (see
    # game_engine's CONSISTENCY_WEIGHT), never "perfectly consistent".
    #
    # scoring_spread_games is how many real games it was measured over,
    # kept because a 6-game measurement is real but very noisy -- the sim
    # pulls small samples toward the league average using this count
    # rather than trusting them equally (CONSISTENCY_SHRINK_GAMES).
    scoring_spread: Optional[float] = None
    scoring_spread_games: int = 0

    # -- Computed properties: NEVER stored, always calculated ----------
    # A @property lets us call these like plain fields (player.pts, not
    # player.pts()), but under the hood they're just little functions that
    # do the math fresh every time they're asked for. That's what makes
    # "everything adds up" a guarantee instead of a hope.

    @property
    def dreb(self) -> float:
        """Defensive rebounds = total rebounds minus offensive rebounds."""
        return self.reb - self.oreb

    @property
    def pts(self) -> float:
        """
        Points, built from makes only -- never its own number.
        fgm includes 3-pointers, so two-point makes = fgm - fg3m.
        Each 2-point make is worth 2, each 3-point make is worth 3,
        each free throw make is worth 1.
        """
        two_point_makes = self.fgm - self.fg3m
        return (2 * two_point_makes) + (3 * self.fg3m) + self.ftm

    @property
    def fg_pct(self) -> float:
        """Overall field goal %. Guarded against dividing by zero for a
        player who hasn't attempted a shot (fga == 0)."""
        return self.fgm / self.fga if self.fga else 0.0

    @property
    def fg3_pct(self) -> float:
        """3-point %, same divide-by-zero guard as fg_pct."""
        return self.fg3m / self.fg3a if self.fg3a else 0.0

    @property
    def ft_pct(self) -> float:
        """Free throw %, same divide-by-zero guard."""
        return self.ftm / self.fta if self.fta else 0.0


@dataclass
class Team:
    """
    A team is a name, a list of Players, and its own real defensive
    stats. GAME totals (score, rebounds, etc. for one simulated game)
    still always come from summing player rows, never stored here --
    same "derive, don't duplicate" rule as Player.pts. The defensive
    fields below are a different category: real, externally-sourced
    SEASON stats (what this team's actual opponents shot against them),
    an input to the simulation rather than an output of it -- the same
    role a Player's real fga/fgm play for that player.
    """

    name: str
    # `field(default_factory=list)` gives every Team its OWN empty list to
    # start with. Writing `players: List[Player] = []` instead would be a
    # classic Python trap -- all Team objects would silently share the
    # exact same list, so adding a player to one team would add it to
    # every team. default_factory avoids that by building a fresh list
    # each time a Team is created.
    players: List[Player] = field(default_factory=list)

    # Real per-game stats about what THIS team's real opponents shot
    # against them this season -- a direct measure of this team's own
    # real defense. Stored as raw makes/attempts, not a percentage,
    # for the same reason Player does: a percentage computed on the
    # fly can never disagree with the makes/attempts underneath it.
    opp_fgm: float = 0.0
    opp_fga: float = 0.0
    opp_fg3m: float = 0.0
    opp_fg3a: float = 0.0

    # Real conference ("East"/"West") -- structural fact about the
    # team, not something that varies by game, used for grouping
    # standings the way real broadcasts/apps do.
    conference: str = ""

    def get_player(self, name: str) -> Optional[Player]:
        """Look up one player on this team by exact name match.
        Returns None if no player on the roster has that name."""
        for player in self.players:
            if player.name == name:
                return player
        return None

    @property
    def opp_fg_pct(self) -> float:
        """Real opponent field goal % allowed -- how tough this team's
        overall shot-contesting defense actually is."""
        return self.opp_fgm / self.opp_fga if self.opp_fga else 0.0

    @property
    def opp_fg3_pct(self) -> float:
        """Real opponent 3-point % allowed."""
        return self.opp_fg3m / self.opp_fg3a if self.opp_fg3a else 0.0


@dataclass
class ScheduledGame:
    """
    One entry on the real season schedule: a date and two team names.
    Just plain data -- unlike Player, nothing here is computed, so
    there's no reason for this to be more than a few fields.
    """

    game_id: str
    date: str       # ISO format, e.g. "2025-10-21"
    home_team: str
    away_team: str
