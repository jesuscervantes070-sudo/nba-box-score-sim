"""
Core data models for the sim: Player and Team.

Stats are stored as PER-GAME AVERAGES (the same numbers you'd pull from a
season stat line). Percentages (FG%, 3P%, FT%) are computed properties, not
stored fields, because makes/attempts are the source of truth -- storing a
percentage separately from its makes/attempts is exactly the kind of thing
that lets a sim's numbers stop "adding up."
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Player:
    name: str
    team: str

    min: float = 0.0
    pts: float = 0.0
    reb: float = 0.0
    oreb: float = 0.0
    dreb: float = 0.0
    ast: float = 0.0
    stl: float = 0.0
    blk: float = 0.0
    tov: float = 0.0
    pf: float = 0.0  # personal fouls per game -- drives foul-out/FT logic in the sim engine

    fgm: float = 0.0
    fga: float = 0.0
    fg3m: float = 0.0
    fg3a: float = 0.0
    ftm: float = 0.0
    fta: float = 0.0

    @property
    def fg_pct(self) -> float:
        return self.fgm / self.fga if self.fga else 0.0

    @property
    def fg3_pct(self) -> float:
        return self.fg3m / self.fg3a if self.fg3a else 0.0

    @property
    def ft_pct(self) -> float:
        return self.ftm / self.fta if self.fta else 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "Player":
        known = {f: d.get(f, 0.0) for f in cls.__dataclass_fields__ if f not in ("name", "team")}
        return cls(name=d["name"], team=d["team"], **known)

    def summary_row(self) -> str:
        """One line formatted roughly like the box score screenshot."""
        return (f"{self.name:<18} {self.min:>4.0f} {self.pts:>5.1f} "
                f"{self.reb:>4.1f} {self.ast:>4.1f} {self.stl:>4.1f} {self.blk:>4.1f} "
                f"{self.tov:>4.1f}  {self.fgm:.1f}-{self.fga:.1f} ({self.fg_pct:.3f})  "
                f"{self.fg3m:.1f}-{self.fg3a:.1f} ({self.fg3_pct:.3f})  "
                f"{self.ftm:.1f}-{self.fta:.1f} ({self.ft_pct:.3f})  OR:{self.oreb:.1f}  PF:{self.pf:.1f}")


@dataclass
class Team:
    name: str
    players: List[Player] = field(default_factory=list)

    def get_player(self, name: str) -> Optional[Player]:
        for p in self.players:
            if p.name == name:
                return p
        return None

    def print_roster(self) -> None:
        header = (f"{'NAME':<18} {'MIN':>4} {'PTS':>5} {'REB':>4} {'AST':>4} "
                  f"{'STL':>4} {'BLK':>4} {'TOV':>4}  {'FG':<14}{'3PT':<14}{'FT':<14}{'OR'}")
        print(f"-- {self.name} --")
        print(header)
        for p in sorted(self.players, key=lambda x: -x.pts):
            print(p.summary_row())
