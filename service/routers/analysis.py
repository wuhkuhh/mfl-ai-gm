"""
Layer 6 — Service / Routers
Analysis endpoints. Thin — delegates to analysis layer, returns Pydantic models.
No business logic here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class PositionGroupOut(BaseModel):
    position: str
    raw_score: float
    weighted_score: float
    weight: float
    player_count: int
    avg_age: Optional[float]
    notes: list[str]
    grade: str


class RosterConstructionOut(BaseModel):
    franchise_id: str
    franchise_name: str
    total_score: float
    grade: str
    rank: int
    qb: PositionGroupOut
    rb: PositionGroupOut
    wr: PositionGroupOut
    te: PositionGroupOut
    capital: PositionGroupOut
    strengths: list[str]
    weaknesses: list[str]
    summary: str


class PositionGroupCurveOut(BaseModel):
    position: str
    avg_age: Optional[float]
    avg_peak_years: float
    group_curve_score: float
    peak_count: int
    young_count: int
    aging_count: int
    notes: list[str]


class ContentionWindowOut(BaseModel):
    franchise_id: str
    franchise_name: str
    window: str
    window_score: float
    years_in_window: int
    peak_years_score: float
    young_core_score: float
    capital_score: float
    win_pct_score: float
    roster_age_score: float
    qb_curve: PositionGroupCurveOut
    rb_curve: PositionGroupCurveOut
    wr_curve: PositionGroupCurveOut
    te_curve: PositionGroupCurveOut
    roster_avg_age: Optional[float]
    young_core_count: int
    peak_player_count: int
    total_future_picks: int
    recommendation: str
    strengths: list[str]
    concerns: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_snapshot(request: Request):
    snapshot = request.app.state.snapshot
    if snapshot is None:
        raise HTTPException(
            status_code=503,
            detail="Snapshot not loaded. Call POST /api/snapshot/refresh first.",
        )
    return snapshot


def _position_group_out(group) -> PositionGroupOut:
    return PositionGroupOut(
        position=group.position,
        raw_score=group.raw_score,
        weighted_score=group.weighted_score,
        weight=group.weight,
        player_count=group.player_count,
        avg_age=group.avg_age,
        notes=group.notes,
        grade=group.grade,
    )


def _construction_out(score) -> RosterConstructionOut:
    return RosterConstructionOut(
        franchise_id=score.franchise_id,
        franchise_name=score.franchise_name,
        total_score=score.total_score,
        grade=score.grade,
        rank=score.rank,
        qb=_position_group_out(score.qb),
        rb=_position_group_out(score.rb),
        wr=_position_group_out(score.wr),
        te=_position_group_out(score.te),
        capital=_position_group_out(score.capital),
        strengths=score.strengths,
        weaknesses=score.weaknesses,
        summary=score.summary,
    )


def _curve_group_out(group) -> PositionGroupCurveOut:
    return PositionGroupCurveOut(
        position=group.position,
        avg_age=group.avg_age,
        avg_peak_years=group.avg_peak_years,
        group_curve_score=group.group_curve_score,
        peak_count=group.peak_count,
        young_count=group.young_count,
        aging_count=group.aging_count,
        notes=group.notes,
    )


def _window_out(window) -> ContentionWindowOut:
    return ContentionWindowOut(
        franchise_id=window.franchise_id,
        franchise_name=window.franchise_name,
        window=window.window,
        window_score=window.window_score,
        years_in_window=window.years_in_window,
        peak_years_score=window.peak_years_score,
        young_core_score=window.young_core_score,
        capital_score=window.capital_score,
        win_pct_score=window.win_pct_score,
        roster_age_score=window.roster_age_score,
        qb_curve=_curve_group_out(window.qb_curve),
        rb_curve=_curve_group_out(window.rb_curve),
        wr_curve=_curve_group_out(window.wr_curve),
        te_curve=_curve_group_out(window.te_curve),
        roster_avg_age=window.roster_avg_age,
        young_core_count=window.young_core_count,
        peak_player_count=window.peak_player_count,
        total_future_picks=window.total_future_picks,
        recommendation=window.recommendation,
        strengths=window.strengths,
        concerns=window.concerns,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/roster-construction", response_model=list[RosterConstructionOut])
async def get_roster_construction(request: Request):
    """
    Score every franchise's roster construction across WR/RB/QB/TE/Capital.
    Returns list sorted by total score descending (rank 1 = best).
    """
    snapshot = _require_snapshot(request)
    from mfl_ai_gm.analysis.roster_construction import score_all_franchises
    scores = score_all_franchises(snapshot)
    return [_construction_out(s) for s in scores]


@router.get("/contention-windows", response_model=list[ContentionWindowOut])
async def get_contention_windows(request: Request):
    """
    Calculate dynasty contention window for every franchise.
    Returns list sorted by window score descending.
    Tiers: Contend Now / Transition / Rebuild.
    """
    snapshot = _require_snapshot(request)
    from mfl_ai_gm.analysis.age_curve import calculate_all_windows
    windows = calculate_all_windows(snapshot)
    return [_window_out(w) for w in windows]
