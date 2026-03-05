"""
Layer 1 — Domain
Pure dataclasses. No I/O, no FastAPI, no business logic.
All fields use Python-native types. Serialization handled in snapshot layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

# MFL position codes that represent team-level units, not individual players
TEAM_UNIT_POSITIONS = frozenset({
    "TMQB", "TMRB", "TMWR", "TMTE", "TMPK",   # team offense units
    "TMDL", "TMLB", "TMDB",                     # team defense units
    "TMKR",                                      # team kicker unit
})

SKILL_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})
FLEX_POSITIONS = frozenset({"RB", "WR", "TE"})
ALL_FANTASY_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})


@dataclass
class Player:
    """A single NFL player in the MFL player universe."""
    id: str
    name: str
    position: str
    nfl_team: str
    age: Optional[int]                  # None for rookies or missing data
    is_team_unit: bool = False          # True for TMDL, TMWR, etc.

    @property
    def is_skill(self) -> bool:
        return self.position in SKILL_POSITIONS

    @property
    def is_flex_eligible(self) -> bool:
        return self.position in FLEX_POSITIONS

    @property
    def display(self) -> str:
        age_str = str(self.age) if self.age else "?"
        return f"{self.name} ({self.position}, {self.nfl_team}, age {age_str})"


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

@dataclass
class RosterSlot:
    """A single player slot on a franchise roster."""
    player_id: str
    status: str         # "ROSTER", "INJURED_RESERVE", "TAXI_SQUAD", etc.


@dataclass
class Roster:
    """All roster slots for a single franchise."""
    franchise_id: str
    week: str
    slots: list[RosterSlot] = field(default_factory=list)

    @property
    def active_ids(self) -> list[str]:
        return [s.player_id for s in self.slots if s.status == "ROSTER"]

    @property
    def ir_ids(self) -> list[str]:
        return [s.player_id for s in self.slots if s.status == "INJURED_RESERVE"]

    @property
    def all_ids(self) -> list[str]:
        return [s.player_id for s in self.slots]


# ---------------------------------------------------------------------------
# Future Draft Pick
# ---------------------------------------------------------------------------

@dataclass
class FuturePick:
    """
    A future draft pick owned by a franchise.
    Parsed from MFL pick strings like: FP_0001_2027_1
    Format: FP_{original_owner}_{year}_{round}
    """
    original_owner_id: str     # franchise that originally owned the pick
    year: int
    round: int
    current_owner_id: str      # franchise that currently holds the pick

    @property
    def label(self) -> str:
        return f"{self.year} Round {self.round}"

    @property
    def is_own_pick(self) -> bool:
        return self.original_owner_id == self.current_owner_id


# ---------------------------------------------------------------------------
# Franchise
# ---------------------------------------------------------------------------

@dataclass
class Franchise:
    """A single dynasty franchise in the league."""
    id: str
    name: str
    abbrev: str
    owner_name: str
    waiver_sort_order: int
    bbid_balance: float
    future_picks: list[FuturePick] = field(default_factory=list)

    @property
    def own_picks(self) -> list[FuturePick]:
        return [p for p in self.future_picks if p.is_own_pick]

    @property
    def acquired_picks(self) -> list[FuturePick]:
        return [p for p in self.future_picks if not p.is_own_pick]

    @property
    def pick_count_by_year(self) -> dict[int, int]:
        result: dict[int, int] = {}
        for p in self.future_picks:
            result[p.year] = result.get(p.year, 0) + 1
        return result


# ---------------------------------------------------------------------------
# Standing
# ---------------------------------------------------------------------------

@dataclass
class Standing:
    """Win/loss/points record for a single franchise."""
    franchise_id: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    projected_points: float
    h2h_pct: float
    all_play_pct: float
    streak: str             # e.g. "W3", "L1", "-"

    @property
    def record(self) -> str:
        if self.ties:
            return f"{self.wins}-{self.losses}-{self.ties}"
        return f"{self.wins}-{self.losses}"

    @property
    def games_played(self) -> int:
        return self.wins + self.losses + self.ties


# ---------------------------------------------------------------------------
# League snapshot — top-level container
# ---------------------------------------------------------------------------

@dataclass
class LeagueSnapshot:
    """
    Complete point-in-time snapshot of the league.
    Built by the snapshot layer, consumed by the analysis layer.
    """
    season: str
    week: str
    league_name: str
    league_id: str
    roster_size: int
    last_regular_season_week: int

    franchises: list[Franchise] = field(default_factory=list)
    players: dict[str, Player] = field(default_factory=dict)   # keyed by player_id
    rosters: dict[str, Roster] = field(default_factory=dict)   # keyed by franchise_id
    standings: dict[str, Standing] = field(default_factory=dict)  # keyed by franchise_id

    # Convenience lookups
    @property
    def franchise_map(self) -> dict[str, Franchise]:
        return {f.id: f for f in self.franchises}

    def get_roster_players(self, franchise_id: str) -> list[Player]:
        """Return Player objects for every slot on a franchise's roster."""
        roster = self.rosters.get(franchise_id)
        if not roster:
            return []
        return [
            self.players[pid]
            for pid in roster.all_ids
            if pid in self.players
        ]

    def get_skill_players(self, franchise_id: str) -> list[Player]:
        """Return only QB/RB/WR/TE players on a franchise's roster."""
        return [p for p in self.get_roster_players(franchise_id) if p.is_skill]

    def get_roster_ages(self, franchise_id: str) -> list[int]:
        """Return ages of all skill players with known age on a roster."""
        return [
            p.age for p in self.get_skill_players(franchise_id)
            if p.age is not None
        ]

    def average_age(self, franchise_id: str) -> Optional[float]:
        ages = self.get_roster_ages(franchise_id)
        if not ages:
            return None
        return round(sum(ages) / len(ages), 1)
