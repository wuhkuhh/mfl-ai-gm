"""
Layer 6 — Service / Routers
Franchise endpoints. Returns combined summary for a single franchise.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from service.routers.analysis import (
    ContentionWindowOut,
    RosterConstructionOut,
    _construction_out,
    _window_out,
    _require_snapshot,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class PlayerOut(BaseModel):
    id: str
    name: str
    position: str
    nfl_team: str
    age: Optional[int]
    is_team_unit: bool


class RosterOut(BaseModel):
    franchise_id: str
    week: str
    total_slots: int
    active_count: int
    ir_count: int
    skill_players: list[PlayerOut]


class StandingOut(BaseModel):
    franchise_id: str
    wins: int
    losses: int
    ties: int
    record: str
    points_for: float
    points_against: float
    projected_points: float
    streak: str


class FranchiseSummaryOut(BaseModel):
    franchise_id: str
    franchise_name: str
    abbrev: str
    owner_name: str
    roster: RosterOut
    standing: Optional[StandingOut]
    construction: RosterConstructionOut
    contention_window: ContentionWindowOut
    avg_age: Optional[float]
    young_core_count: int
    total_future_picks: int


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/{franchise_id}", response_model=FranchiseSummaryOut)
async def get_franchise(franchise_id: str, request: Request):
    """
    Full summary for a single franchise: roster, standing, construction score,
    and contention window in one response.
    """
    snapshot = _require_snapshot(request)

    # Validate franchise exists
    fmap = snapshot.franchise_map
    if franchise_id not in fmap:
        valid = list(fmap.keys())
        raise HTTPException(
            status_code=404,
            detail=f"Franchise '{franchise_id}' not found. Valid IDs: {valid}",
        )

    franchise = fmap[franchise_id]
    roster = snapshot.rosters.get(franchise_id)
    standing = snapshot.standings.get(franchise_id)
    skill_players = snapshot.get_skill_players(franchise_id)

    # Construction score
    from mfl_ai_gm.analysis.roster_construction import (
        compute_league_context,
        score_all_franchises,
        score_roster,
    )
    context = compute_league_context(snapshot)
    construction = score_roster(franchise, snapshot, context)
    # Rank requires league-wide sort — compute quickly
    all_scores = score_all_franchises(snapshot)
    construction = next(s for s in all_scores if s.franchise_id == franchise_id)

    # Contention window
    from mfl_ai_gm.analysis.age_curve import (
        compute_age_curve_context,
        calculate_contention_window,
    )
    age_context = compute_age_curve_context(snapshot)
    window = calculate_contention_window(franchise, snapshot, age_context)

    # Build roster output
    roster_out = RosterOut(
        franchise_id=franchise_id,
        week=roster.week if roster else "0",
        total_slots=len(roster.slots) if roster else 0,
        active_count=len(roster.active_ids) if roster else 0,
        ir_count=len(roster.ir_ids) if roster else 0,
        skill_players=[
            PlayerOut(
                id=p.id,
                name=p.name,
                position=p.position,
                nfl_team=p.nfl_team,
                age=p.age,
                is_team_unit=p.is_team_unit,
            )
            for p in skill_players
        ],
    )

    # Standing output
    standing_out = None
    if standing:
        standing_out = StandingOut(
            franchise_id=franchise_id,
            wins=standing.wins,
            losses=standing.losses,
            ties=standing.ties,
            record=standing.record,
            points_for=standing.points_for,
            points_against=standing.points_against,
            projected_points=standing.projected_points,
            streak=standing.streak,
        )

    return FranchiseSummaryOut(
        franchise_id=franchise_id,
        franchise_name=franchise.name,
        abbrev=franchise.abbrev,
        owner_name=franchise.owner_name,
        roster=roster_out,
        standing=standing_out,
        construction=_construction_out(construction),
        contention_window=_window_out(window),
        avg_age=snapshot.average_age(franchise_id),
        young_core_count=window.young_core_count,
        total_future_picks=len(franchise.future_picks),
    )
