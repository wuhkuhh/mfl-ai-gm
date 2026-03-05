"""
Layer 6 — Service / Routers
Analysis endpoints. Thin — delegates to analysis layer, returns Pydantic models.
No business logic here.
"""

from __future__ import annotations

import os

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


class FreeAgentOut(BaseModel):
    player_id: str
    name: str
    position: str
    nfl_team: str
    age: Optional[int]
    base_score: float
    scarcity_multiplier: float
    dynasty_score: float
    peak_years_remaining: float
    position_fa_count: int
    notes: list[str]


class RosterNeedOut(BaseModel):
    position: str
    current_count: int
    target_count: int
    depth_gap: int
    starter_avg_age: Optional[float]
    need_score: float
    notes: list[str]


class WaiverRecOut(BaseModel):
    rank: int
    player_id: str
    name: str
    position: str
    nfl_team: str
    age: Optional[int]
    dynasty_score: float
    need_score: float
    combined_score: float
    reason: str
    peak_years_remaining: float


class FranchiseWaiverOut(BaseModel):
    franchise_id: str
    franchise_name: str
    needs: dict[str, RosterNeedOut]
    top_adds: list[WaiverRecOut]
    by_position: dict[str, list[WaiverRecOut]]
    summary: str


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


def _fa_out(fa) -> FreeAgentOut:
    p = fa.player
    return FreeAgentOut(
        player_id=p.id,
        name=p.name,
        position=p.position,
        nfl_team=p.nfl_team,
        age=p.age,
        base_score=fa.base_score,
        scarcity_multiplier=fa.scarcity_multiplier,
        dynasty_score=fa.dynasty_score,
        peak_years_remaining=fa.peak_years_remaining,
        position_fa_count=fa.position_fa_count,
        notes=fa.notes,
    )


def _rec_out(r) -> WaiverRecOut:
    p = r.player
    return WaiverRecOut(
        rank=r.rank,
        player_id=p.id,
        name=p.name,
        position=p.position,
        nfl_team=p.nfl_team,
        age=p.age,
        dynasty_score=r.dynasty_score,
        need_score=r.need_score,
        combined_score=r.combined_score,
        reason=r.reason,
        peak_years_remaining=r.peak_years_remaining,
    )


def _need_out(n) -> RosterNeedOut:
    return RosterNeedOut(
        position=n.position,
        current_count=n.current_count,
        target_count=n.target_count,
        depth_gap=n.depth_gap,
        starter_avg_age=n.starter_avg_age,
        need_score=n.need_score,
        notes=n.notes,
    )


def _waiver_report_out(report) -> FranchiseWaiverOut:
    return FranchiseWaiverOut(
        franchise_id=report.franchise_id,
        franchise_name=report.franchise_name,
        needs={pos: _need_out(n) for pos, n in report.needs.items()},
        top_adds=[_rec_out(r) for r in report.top_adds],
        by_position={
            pos: [_rec_out(r) for r in recs]
            for pos, recs in report.by_position.items()
        },
        summary=report.summary,
    )


async def _fetch_free_agents(request: Request):
    """Fetch FA pool from MFL and return list of Player objects."""
    snapshot = _require_snapshot(request)
    from mfl_ai_gm.adapters.mfl_client import MFLClient

    client = MFLClient(
        api_key=os.environ.get("MFL_API_KEY", ""),
        league_id=os.environ.get("MFL_LEAGUE_ID", "25903"),
        season=os.environ.get("MFL_SEASON", "2026"),
    )
    try:
        fa_data = client.get_free_agents()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MFL FA fetch failed: {e}")

    fa_list_raw = fa_data.get("freeAgents", {}).get("leagueUnit", {})
    if isinstance(fa_list_raw, dict):
        players_raw = fa_list_raw.get("player", [])
        if isinstance(players_raw, dict):
            players_raw = [players_raw]
        fa_ids = {p["id"] for p in players_raw if "id" in p}
    else:
        fa_ids = set()

    return [
        p for pid, p in snapshot.players.items()
        if pid in fa_ids and not p.is_team_unit
    ]


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


@router.get("/waivers/pool", response_model=list[FreeAgentOut])
async def get_fa_pool(request: Request):
    """
    Global FA pool ranked by dynasty value.
    Scores every available player by age curve x position scarcity.
    Returns empty list during preseason when MFL wire is closed.
    """
    free_agents = await _fetch_free_agents(request)
    from mfl_ai_gm.analysis.waiver_recommender import score_free_agents
    fa_pool = score_free_agents(free_agents)
    return [_fa_out(fa) for fa in fa_pool]


@router.get("/waivers/{franchise_id}", response_model=FranchiseWaiverOut)
async def get_franchise_waivers(franchise_id: str, request: Request):
    """
    Per-franchise waiver recommendations — ranked list + position breakdown.
    Combines FA dynasty value with roster need scoring.
    Roster needs always populated; top_adds empty until MFL opens wire.
    """
    snapshot = _require_snapshot(request)
    fmap = snapshot.franchise_map
    if franchise_id not in fmap:
        raise HTTPException(
            status_code=404,
            detail=f"Franchise '{franchise_id}' not found. Valid IDs: {list(fmap.keys())}",
        )

    franchise = fmap[franchise_id]
    free_agents = await _fetch_free_agents(request)

    from mfl_ai_gm.analysis.waiver_recommender import (
        _score_fa_pool,
        build_franchise_report,
    )
    fa_pool, pos_counts = _score_fa_pool(free_agents)
    report = build_franchise_report(franchise, snapshot, fa_pool, pos_counts)
    return _waiver_report_out(report)
