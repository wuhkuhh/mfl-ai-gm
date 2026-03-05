"""
Layer 2 — Analysis
Waiver / Free Agent Recommender

Scores every available FA by dynasty value, then filters and ranks
recommendations per franchise based on roster needs.

Factors:
  - Player age (younger = higher dynasty value, via age curve score)
  - Position scarcity on waivers (fewer FAs at a position = higher urgency)
  - Roster needs (thin positions get priority)
  - Age of current starters (young starter = less urgency to add at that spot)

Output:
  - Global FA pool ranked by dynasty value
  - Per-franchise top adds (ranked list + position breakdown)

No I/O, no FastAPI. Consumes LeagueSnapshot + free agent player list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mfl_ai_gm.domain.models import Franchise, LeagueSnapshot, Player
from mfl_ai_gm.analysis.age_curve import (
    _player_curve_score,
    _peak_years_remaining,
    POSITION_CURVES,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum dynasty roster depth targets per position
DEPTH_TARGETS = {
    "QB": 2,
    "RB": 5,
    "WR": 6,
    "TE": 2,
}

# Scarcity multiplier bounds
SCARCITY_MIN = 1.0
SCARCITY_MAX = 1.5

# If starter avg age is this or younger, urgency to add at that position drops
YOUNG_STARTER_THRESHOLD = 26

# Minimum FA dynasty score to include in recommendations
MIN_FA_SCORE = 20.0

# Top N per position per franchise
TOP_N_PER_POSITION = 3

# Top N global recommendations per franchise
TOP_N_GLOBAL = 10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FreeAgentScore:
    """Dynasty value score for a single free agent."""
    player: Player
    base_score: float
    scarcity_multiplier: float
    dynasty_score: float
    peak_years_remaining: float
    position_fa_count: int
    notes: list[str] = field(default_factory=list)


@dataclass
class RosterNeed:
    """How urgently a franchise needs players at a position."""
    position: str
    current_count: int
    target_count: int
    depth_gap: int
    starter_avg_age: Optional[float]
    need_score: float           # 0–100
    notes: list[str] = field(default_factory=list)


@dataclass
class WaiverRecommendation:
    """A single waiver add recommendation for a franchise."""
    rank: int
    player: Player
    position: str
    dynasty_score: float
    need_score: float
    combined_score: float
    reason: str
    peak_years_remaining: float


@dataclass
class FranchiseWaiverReport:
    """Full waiver report for one franchise."""
    franchise_id: str
    franchise_name: str
    needs: dict[str, RosterNeed]
    top_adds: list[WaiverRecommendation]
    by_position: dict[str, list[WaiverRecommendation]]
    summary: str = ""


# ---------------------------------------------------------------------------
# FA pool scorer
# ---------------------------------------------------------------------------

def _score_fa_pool(
    free_agents: list[Player],
) -> tuple[list[FreeAgentScore], dict[str, int]]:
    """Score all FAs by dynasty value. Returns (scored_fas, pos_counts)."""
    pos_counts: dict[str, int] = {}
    for p in free_agents:
        if p.position in DEPTH_TARGETS:
            pos_counts[p.position] = pos_counts.get(p.position, 0) + 1

    if pos_counts:
        min_count = min(pos_counts.values())
        max_count = max(pos_counts.values())
        count_range = max(max_count - min_count, 1)
    else:
        min_count = max_count = count_range = 1

    def scarcity_mult(pos: str) -> float:
        count = pos_counts.get(pos, 1)
        normalized = 1.0 - (count - min_count) / count_range
        return SCARCITY_MIN + normalized * (SCARCITY_MAX - SCARCITY_MIN)

    scored = []
    for p in free_agents:
        if p.position not in DEPTH_TARGETS:
            continue

        base = _player_curve_score(p.age, p.position)
        mult = scarcity_mult(p.position)
        dynasty = min(100.0, base * mult)
        peak_yrs = _peak_years_remaining(p.age, p.position)

        notes = []
        curve = POSITION_CURVES.get(p.position, POSITION_CURVES["WR"])
        if p.age is not None:
            if p.age <= 23:
                notes.append("Young dynasty upside")
            elif curve["peak_start"] <= p.age <= curve["peak_end"]:
                notes.append("In prime window")
            elif p.age > curve["cliff"]:
                notes.append("Past positional cliff")
        if mult > 1.3:
            notes.append(f"Scarce position ({pos_counts.get(p.position, 0)} available)")

        if dynasty >= MIN_FA_SCORE:
            scored.append(FreeAgentScore(
                player=p,
                base_score=round(base, 1),
                scarcity_multiplier=round(mult, 2),
                dynasty_score=round(dynasty, 1),
                peak_years_remaining=round(peak_yrs, 1),
                position_fa_count=pos_counts.get(p.position, 0),
                notes=notes,
            ))

    scored.sort(key=lambda x: x.dynasty_score, reverse=True)
    return scored, pos_counts


# ---------------------------------------------------------------------------
# Roster need scorer
# ---------------------------------------------------------------------------

def _score_roster_needs(
    franchise: Franchise,
    snapshot: LeagueSnapshot,
    pos_counts: dict[str, int],
) -> dict[str, RosterNeed]:
    """Score how urgently a franchise needs FAs at each position."""
    players = snapshot.get_skill_players(franchise.id)
    needs = {}

    for pos, target in DEPTH_TARGETS.items():
        pos_players = [p for p in players if p.position == pos]
        current = len(pos_players)
        gap = max(0, target - current)

        starters = pos_players[:2] if pos in ("QB", "TE") else pos_players[:3]
        starter_ages = [p.age for p in starters if p.age is not None]
        starter_avg_age = (
            round(sum(starter_ages) / len(starter_ages), 1)
            if starter_ages else None
        )

        # Base need from depth gap
        if gap == 0:
            base_need = 20.0
        elif gap == 1:
            base_need = 50.0
        elif gap == 2:
            base_need = 75.0
        else:
            base_need = 90.0

        # Starter age modifier
        age_mod = 0.0
        if starter_avg_age is not None:
            if starter_avg_age > 30:
                age_mod = +15.0
            elif starter_avg_age > 28:
                age_mod = +8.0
            elif starter_avg_age < YOUNG_STARTER_THRESHOLD:
                age_mod = -10.0

        # Scarcity modifier
        fa_count = pos_counts.get(pos, 0)
        scarcity_mod = +8.0 if fa_count < 5 else 0.0

        need_score = min(100.0, max(0.0, base_need + age_mod + scarcity_mod))

        notes = []
        if gap > 0:
            notes.append(f"{gap} below target depth ({current}/{target})")
        if starter_avg_age and starter_avg_age > 29:
            notes.append(f"Aging starters (avg {starter_avg_age})")
        if fa_count < 5:
            notes.append(f"Only {fa_count} FAs available")

        needs[pos] = RosterNeed(
            position=pos,
            current_count=current,
            target_count=target,
            depth_gap=gap,
            starter_avg_age=starter_avg_age,
            need_score=round(need_score, 1),
            notes=notes,
        )

    return needs


# ---------------------------------------------------------------------------
# Recommendation builder
# ---------------------------------------------------------------------------

def _build_recommendations(
    franchise: Franchise,
    needs: dict[str, RosterNeed],
    fa_scores: list[FreeAgentScore],
    roster_player_ids: set[str],
) -> tuple[list[WaiverRecommendation], dict[str, list[WaiverRecommendation]]]:
    """Build ranked waiver recommendations for a franchise."""
    recs: list[WaiverRecommendation] = []

    for fa in fa_scores:
        if fa.player.id in roster_player_ids:
            continue

        pos = fa.player.position
        need = needs.get(pos)
        if need is None:
            continue

        # 60% dynasty value, 40% roster need
        combined = (fa.dynasty_score * 0.60) + (need.need_score * 0.40)

        reason_parts = []
        if need.depth_gap > 0:
            reason_parts.append(f"depth need ({need.current_count}/{need.target_count} {pos}s)")
        if fa.notes:
            reason_parts.append(fa.notes[0])
        if need.starter_avg_age and need.starter_avg_age > 29:
            reason_parts.append("aging starters")
        reason = "; ".join(reason_parts) if reason_parts else "value add"

        recs.append(WaiverRecommendation(
            rank=0,
            player=fa.player,
            position=pos,
            dynasty_score=fa.dynasty_score,
            need_score=need.need_score,
            combined_score=round(combined, 1),
            reason=reason,
            peak_years_remaining=fa.peak_years_remaining,
        ))

    recs.sort(key=lambda r: r.combined_score, reverse=True)
    for i, r in enumerate(recs):
        r.rank = i + 1

    top_adds = recs[:TOP_N_GLOBAL]

    by_position: dict[str, list[WaiverRecommendation]] = {}
    for pos in DEPTH_TARGETS:
        pos_recs = [r for r in recs if r.position == pos][:TOP_N_PER_POSITION]
        if pos_recs:
            by_position[pos] = pos_recs

    return top_adds, by_position


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def score_free_agents(free_agents: list[Player]) -> list[FreeAgentScore]:
    """Score and rank the full FA pool by dynasty value."""
    scored, _ = _score_fa_pool(free_agents)
    return scored


def build_franchise_report(
    franchise: Franchise,
    snapshot: LeagueSnapshot,
    fa_scores: list[FreeAgentScore],
    pos_counts: dict[str, int],
) -> FranchiseWaiverReport:
    """Build waiver recommendations for a single franchise."""
    needs = _score_roster_needs(franchise, snapshot, pos_counts)

    roster = snapshot.rosters.get(franchise.id)
    roster_ids = set(roster.all_ids) if roster else set()

    top_adds, by_position = _build_recommendations(franchise, needs, fa_scores, roster_ids)

    urgent_needs = [pos for pos, n in needs.items() if n.need_score >= 60]
    if urgent_needs:
        summary = f"{franchise.name} has urgent needs at {', '.join(urgent_needs)}."
        if top_adds:
            summary += f" Top add: {top_adds[0].player.name} ({top_adds[0].position}, score {top_adds[0].combined_score:.0f})."
    elif top_adds:
        summary = f"{franchise.name} is well-stocked. Best value add: {top_adds[0].player.name} ({top_adds[0].position})."
    else:
        summary = f"{franchise.name} — no strong FA recommendations this week."

    return FranchiseWaiverReport(
        franchise_id=franchise.id,
        franchise_name=franchise.name,
        needs=needs,
        top_adds=top_adds,
        by_position=by_position,
        summary=summary,
    )


def build_all_waiver_reports(
    snapshot: LeagueSnapshot,
    free_agents: list[Player],
) -> tuple[list[FreeAgentScore], list[FranchiseWaiverReport]]:
    """
    Build FA pool scores + waiver reports for all franchises.
    Returns (fa_pool, reports).
    """
    fa_pool, pos_counts = _score_fa_pool(free_agents)
    reports = [
        build_franchise_report(f, snapshot, fa_pool, pos_counts)
        for f in snapshot.franchises
    ]
    return fa_pool, reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_waiver_report(
    fa_pool: list[FreeAgentScore],
    reports: list[FranchiseWaiverReport],
) -> None:
    print("\n" + "=" * 72)
    print("  GLOBAL FA POOL — Top 20 by Dynasty Value")
    print("=" * 72)
    print(f"  {'#':<4} {'Player':<28} {'Pos':<5} {'Age':<5} {'Score':<8} {'PeakYrs':<9} Notes")
    print("-" * 72)
    for i, fa in enumerate(fa_pool[:20], 1):
        p = fa.player
        print(
            f"  {i:<4} {p.name:<28} {p.position:<5} {str(p.age or '?'):<5} "
            f"{fa.dynasty_score:<8.1f} {fa.peak_years_remaining:<9.1f} "
            f"{', '.join(fa.notes[:1])}"
        )

    for report in reports[:3]:
        print(f"\n{'=' * 72}")
        print(f"  {report.franchise_name.upper()}")
        print(f"  {report.summary}")
        print("-" * 72)
        print("  NEEDS:")
        for pos, need in sorted(report.needs.items(), key=lambda x: -x[1].need_score):
            bar = "█" * int(need.need_score / 10) + "░" * (10 - int(need.need_score / 10))
            note = " · ".join(need.notes[:1]) if need.notes else "adequate"
            print(f"    {pos:<5} [{bar}] {need.need_score:>4.0f}  {note}")
        print("  TOP ADDS:")
        for r in report.top_adds[:6]:
            p = r.player
            print(
                f"    #{r.rank:<3} {p.name:<28} {p.position:<5} age {str(p.age or '?'):<4} "
                f"combined:{r.combined_score:<6.0f} — {r.reason}"
            )


if __name__ == "__main__":
    import logging
    import os
    import sys

    logging.basicConfig(level=logging.WARNING)
    sys.path.insert(0, "src")

    from dotenv import load_dotenv
    load_dotenv()

    from mfl_ai_gm.snapshot.builder import load_snapshot
    from mfl_ai_gm.adapters.mfl_client import MFLClient

    snapshot = load_snapshot()
    client = MFLClient(
        api_key=os.environ["MFL_API_KEY"],
        league_id=os.environ["MFL_LEAGUE_ID"],
        season=os.environ.get("MFL_SEASON", "2026"),
    )

    fa_data = client.get_free_agents()
    fa_list_raw = fa_data.get("freeAgents", {}).get("leagueUnit", {})
    if isinstance(fa_list_raw, dict):
        players_raw = fa_list_raw.get("player", [])
        if isinstance(players_raw, dict):
            players_raw = [players_raw]
        fa_ids = {p["id"] for p in players_raw if "id" in p}
    else:
        fa_ids = set()

    free_agents = [
        p for pid, p in snapshot.players.items()
        if pid in fa_ids and not p.is_team_unit
    ]

    print(f"FA pool: {len(free_agents)} players available")
    fa_pool, reports = build_all_waiver_reports(snapshot, free_agents)
    _print_waiver_report(fa_pool, reports)
