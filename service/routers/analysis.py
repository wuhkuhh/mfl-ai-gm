"""
Layer 6 — Service / Routers
Analysis endpoints. Thin — delegates to analysis layer, returns Pydantic models.
"""

from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class PositionGroupOut(BaseModel):
    position: str; raw_score: float; weighted_score: float; weight: float
    player_count: int; avg_age: Optional[float]; notes: list[str]; grade: str

class RosterConstructionOut(BaseModel):
    franchise_id: str; franchise_name: str; total_score: float; grade: str; rank: int
    qb: PositionGroupOut; rb: PositionGroupOut; wr: PositionGroupOut
    te: PositionGroupOut; capital: PositionGroupOut
    strengths: list[str]; weaknesses: list[str]; summary: str

class PositionGroupCurveOut(BaseModel):
    position: str; avg_age: Optional[float]; avg_peak_years: float
    group_curve_score: float; peak_count: int; young_count: int
    aging_count: int; notes: list[str]

class ContentionWindowOut(BaseModel):
    franchise_id: str; franchise_name: str; window: str; window_score: float
    years_in_window: int; peak_years_score: float; young_core_score: float
    capital_score: float; win_pct_score: float; roster_age_score: float
    qb_curve: PositionGroupCurveOut; rb_curve: PositionGroupCurveOut
    wr_curve: PositionGroupCurveOut; te_curve: PositionGroupCurveOut
    roster_avg_age: Optional[float]; young_core_count: int
    peak_player_count: int; total_future_picks: int
    recommendation: str; strengths: list[str]; concerns: list[str]

class FreeAgentOut(BaseModel):
    player_id: str; name: str; position: str; nfl_team: str; age: Optional[int]
    base_score: float; scarcity_multiplier: float; dynasty_score: float
    peak_years_remaining: float; position_fa_count: int; notes: list[str]

class RosterNeedOut(BaseModel):
    position: str; current_count: int; target_count: int; depth_gap: int
    starter_avg_age: Optional[float]; need_score: float; notes: list[str]

class WaiverRecOut(BaseModel):
    rank: int; player_id: str; name: str; position: str; nfl_team: str
    age: Optional[int]; dynasty_score: float; need_score: float
    combined_score: float; reason: str; peak_years_remaining: float

class FranchiseWaiverOut(BaseModel):
    franchise_id: str; franchise_name: str
    needs: dict[str, RosterNeedOut]; top_adds: list[WaiverRecOut]
    by_position: dict[str, list[WaiverRecOut]]; summary: str

class SellHighSignalOut(BaseModel):
    mfl_player_id: str; name: str; position: str; nfl_team: str; age: Optional[float]
    fc_value: int; overall_rank: int; position_rank: int; trend_30d: int
    redraft_value: int; sell_score: float; sell_signal: str; reasons: list[str]
    tier: Optional[int]

class FranchiseSellHighOut(BaseModel):
    franchise_id: str; franchise_name: str
    signals: list[SellHighSignalOut]; strong_sells: list[SellHighSignalOut]
    consider_sells: list[SellHighSignalOut]; buy_lows: list[SellHighSignalOut]
    summary: str

class FCValueOut(BaseModel):
    fc_id: int; name: str; mfl_id: Optional[str]; position: str; nfl_team: str
    age: Optional[float]; value: int; overall_rank: int; position_rank: int
    trend_30d: int; redraft_value: int; tier: Optional[int]

class ConsensusPlayerOut(BaseModel):
    mfl_id: str; name: str; position: str; nfl_team: str; age: Optional[float]
    consensus_score: float; consensus_rank: int; sources: int
    fc_value: Optional[int]; dp_value: Optional[int]
    fc_norm: Optional[float]; dp_norm: Optional[float]
    disagreement: float; is_disputed: bool; value_signal: str
    fc_rank: Optional[int]; fc_trend_30d: Optional[int]
    dp_ecr_1qb: Optional[float]; dp_scrape_date: Optional[str]

class PickOut(BaseModel):
    label: str; mfl_id: str; consensus_score: float
    fc_value: Optional[int]; fc_norm: Optional[float]

class TradeAssetIn(BaseModel):
    asset_type: str           # "player" or "pick"
    label: str
    mfl_id: Optional[str] = None   # required for players; optional for picks (resolved by label)

class TradeRequestIn(BaseModel):
    side_a: list[TradeAssetIn]
    side_b: list[TradeAssetIn]

class TradeAssetOut(BaseModel):
    asset_type: str; label: str; mfl_id: Optional[str]
    consensus_score: float; fc_score: Optional[float]; dp_score: Optional[float]
    sources: int; position: Optional[str]; nfl_team: Optional[str]
    age: Optional[float]; notes: str; is_disputed: bool

class TradeSideOut(BaseModel):
    assets: list[TradeAssetOut]; total_score: float
    player_count: int; pick_count: int

class TradeVerdictOut(BaseModel):
    side_a: TradeSideOut; side_b: TradeSideOut
    delta: float; winner: str; fairness: str; advantage_pct: float
    summary: str; recommendation: str; disputed_assets: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_snapshot(request: Request):
    s = request.app.state.snapshot
    if s is None:
        raise HTTPException(503, "Snapshot not loaded. Call POST /api/snapshot/refresh first.")
    return s

def _require_fc_map(request: Request):
    m = getattr(request.app.state, "fc_value_map", {})
    if not m:
        raise HTTPException(503, "FantasyCalc values not loaded.")
    return m

def _require_consensus_map(request: Request):
    m = getattr(request.app.state, "consensus_map", {})
    if not m:
        raise HTTPException(503, "Consensus values not loaded.")
    return m

def _position_group_out(g) -> PositionGroupOut:
    return PositionGroupOut(position=g.position, raw_score=g.raw_score,
        weighted_score=g.weighted_score, weight=g.weight, player_count=g.player_count,
        avg_age=g.avg_age, notes=g.notes, grade=g.grade)

def _construction_out(s) -> RosterConstructionOut:
    return RosterConstructionOut(franchise_id=s.franchise_id, franchise_name=s.franchise_name,
        total_score=s.total_score, grade=s.grade, rank=s.rank,
        qb=_position_group_out(s.qb), rb=_position_group_out(s.rb),
        wr=_position_group_out(s.wr), te=_position_group_out(s.te),
        capital=_position_group_out(s.capital),
        strengths=s.strengths, weaknesses=s.weaknesses, summary=s.summary)

def _curve_group_out(g) -> PositionGroupCurveOut:
    return PositionGroupCurveOut(position=g.position, avg_age=g.avg_age,
        avg_peak_years=g.avg_peak_years, group_curve_score=g.group_curve_score,
        peak_count=g.peak_count, young_count=g.young_count,
        aging_count=g.aging_count, notes=g.notes)

def _window_out(w) -> ContentionWindowOut:
    return ContentionWindowOut(franchise_id=w.franchise_id, franchise_name=w.franchise_name,
        window=w.window, window_score=w.window_score, years_in_window=w.years_in_window,
        peak_years_score=w.peak_years_score, young_core_score=w.young_core_score,
        capital_score=w.capital_score, win_pct_score=w.win_pct_score,
        roster_age_score=w.roster_age_score,
        qb_curve=_curve_group_out(w.qb_curve), rb_curve=_curve_group_out(w.rb_curve),
        wr_curve=_curve_group_out(w.wr_curve), te_curve=_curve_group_out(w.te_curve),
        roster_avg_age=w.roster_avg_age, young_core_count=w.young_core_count,
        peak_player_count=w.peak_player_count, total_future_picks=w.total_future_picks,
        recommendation=w.recommendation, strengths=w.strengths, concerns=w.concerns)

def _fa_out(fa) -> FreeAgentOut:
    p = fa.player
    return FreeAgentOut(player_id=p.id, name=p.name, position=p.position, nfl_team=p.nfl_team,
        age=p.age, base_score=fa.base_score, scarcity_multiplier=fa.scarcity_multiplier,
        dynasty_score=fa.dynasty_score, peak_years_remaining=fa.peak_years_remaining,
        position_fa_count=fa.position_fa_count, notes=fa.notes)

def _rec_out(r) -> WaiverRecOut:
    p = r.player
    return WaiverRecOut(rank=r.rank, player_id=p.id, name=p.name, position=p.position,
        nfl_team=p.nfl_team, age=p.age, dynasty_score=r.dynasty_score,
        need_score=r.need_score, combined_score=r.combined_score,
        reason=r.reason, peak_years_remaining=r.peak_years_remaining)

def _need_out(n) -> RosterNeedOut:
    return RosterNeedOut(position=n.position, current_count=n.current_count,
        target_count=n.target_count, depth_gap=n.depth_gap,
        starter_avg_age=n.starter_avg_age, need_score=n.need_score, notes=n.notes)

def _waiver_report_out(report) -> FranchiseWaiverOut:
    return FranchiseWaiverOut(franchise_id=report.franchise_id,
        franchise_name=report.franchise_name,
        needs={pos: _need_out(n) for pos, n in report.needs.items()},
        top_adds=[_rec_out(r) for r in report.top_adds],
        by_position={pos: [_rec_out(r) for r in recs] for pos, recs in report.by_position.items()},
        summary=report.summary)

def _sell_signal_out(s) -> SellHighSignalOut:
    return SellHighSignalOut(mfl_player_id=s.mfl_player_id, name=s.name,
        position=s.position, nfl_team=s.nfl_team, age=s.age, fc_value=s.fc_value,
        overall_rank=s.overall_rank, position_rank=s.position_rank,
        trend_30d=s.trend_30d, redraft_value=s.redraft_value,
        sell_score=s.sell_score, sell_signal=s.sell_signal,
        reasons=s.reasons, tier=s.tier)

def _sell_report_out(r) -> FranchiseSellHighOut:
    return FranchiseSellHighOut(franchise_id=r.franchise_id, franchise_name=r.franchise_name,
        signals=[_sell_signal_out(s) for s in r.signals],
        strong_sells=[_sell_signal_out(s) for s in r.strong_sells],
        consider_sells=[_sell_signal_out(s) for s in r.consider_sells],
        buy_lows=[_sell_signal_out(s) for s in r.buy_lows],
        summary=r.summary)

def _fc_value_out(p) -> FCValueOut:
    return FCValueOut(fc_id=p.fc_id, name=p.name, mfl_id=p.mfl_id,
        position=p.position, nfl_team=p.nfl_team, age=p.age, value=p.value,
        overall_rank=p.overall_rank, position_rank=p.position_rank,
        trend_30d=p.trend_30d, redraft_value=p.redraft_value, tier=p.tier)

def _consensus_out(p) -> ConsensusPlayerOut:
    return ConsensusPlayerOut(mfl_id=p.mfl_id, name=p.name, position=p.position,
        nfl_team=p.nfl_team, age=p.age, consensus_score=p.consensus_score,
        consensus_rank=p.consensus_rank, sources=p.sources,
        fc_value=p.fc_value, dp_value=p.dp_value,
        fc_norm=p.fc_norm, dp_norm=p.dp_norm,
        disagreement=p.disagreement, is_disputed=p.is_disputed,
        value_signal=p.value_signal, fc_rank=p.fc_rank,
        fc_trend_30d=p.fc_trend_30d, dp_ecr_1qb=p.dp_ecr_1qb,
        dp_scrape_date=p.dp_scrape_date)

def _trade_asset_out(a) -> TradeAssetOut:
    return TradeAssetOut(asset_type=a.asset_type, label=a.label, mfl_id=a.mfl_id,
        consensus_score=a.consensus_score, fc_score=a.fc_score, dp_score=a.dp_score,
        sources=a.sources, position=a.position, nfl_team=a.nfl_team, age=a.age,
        notes=a.notes, is_disputed=a.is_disputed)

def _trade_side_out(side) -> TradeSideOut:
    return TradeSideOut(assets=[_trade_asset_out(a) for a in side.assets],
        total_score=side.total_score, player_count=side.player_count,
        pick_count=side.pick_count)

def _verdict_out(v) -> TradeVerdictOut:
    return TradeVerdictOut(side_a=_trade_side_out(v.side_a), side_b=_trade_side_out(v.side_b),
        delta=v.delta, winner=v.winner, fairness=v.fairness,
        advantage_pct=v.advantage_pct, summary=v.summary,
        recommendation=v.recommendation, disputed_assets=v.disputed_assets)

async def _fetch_free_agents(request: Request):
    snapshot = _require_snapshot(request)
    from mfl_ai_gm.adapters.mfl_client import MFLClient
    client = MFLClient(api_key=os.environ.get("MFL_API_KEY",""),
        league_id=os.environ.get("MFL_LEAGUE_ID","25903"),
        season=os.environ.get("MFL_SEASON","2026"))
    try:
        fa_data = client.get_free_agents()
    except Exception as e:
        raise HTTPException(502, f"MFL FA fetch failed: {e}")
    fa_list_raw = fa_data.get("freeAgents", {}).get("leagueUnit", {})
    if isinstance(fa_list_raw, dict):
        players_raw = fa_list_raw.get("player", [])
        if isinstance(players_raw, dict):
            players_raw = [players_raw]
        fa_ids = {p["id"] for p in players_raw if "id" in p}
    else:
        fa_ids = set()
    return [p for pid, p in snapshot.players.items() if pid in fa_ids and not p.is_team_unit]


# ---------------------------------------------------------------------------
# Endpoints — existing
# ---------------------------------------------------------------------------

@router.get("/roster-construction", response_model=list[RosterConstructionOut])
async def get_roster_construction(request: Request):
    snapshot = _require_snapshot(request)
    from mfl_ai_gm.analysis.roster_construction import score_all_franchises
    return [_construction_out(s) for s in score_all_franchises(snapshot)]

@router.get("/contention-windows", response_model=list[ContentionWindowOut])
async def get_contention_windows(request: Request):
    snapshot = _require_snapshot(request)
    from mfl_ai_gm.analysis.age_curve import calculate_all_windows
    return [_window_out(w) for w in calculate_all_windows(snapshot)]

@router.get("/waivers/pool", response_model=list[FreeAgentOut])
async def get_fa_pool(request: Request):
    fas = await _fetch_free_agents(request)
    from mfl_ai_gm.analysis.waiver_recommender import score_free_agents
    return [_fa_out(fa) for fa in score_free_agents(fas)]

@router.get("/waivers/{franchise_id}", response_model=FranchiseWaiverOut)
async def get_franchise_waivers(franchise_id: str, request: Request):
    snapshot = _require_snapshot(request)
    fmap = snapshot.franchise_map
    if franchise_id not in fmap:
        raise HTTPException(404, f"Franchise '{franchise_id}' not found.")
    fas = await _fetch_free_agents(request)
    from mfl_ai_gm.analysis.waiver_recommender import _score_fa_pool, build_franchise_report
    fa_pool, pos_counts = _score_fa_pool(fas)
    return _waiver_report_out(build_franchise_report(fmap[franchise_id], snapshot, fa_pool, pos_counts))

@router.get("/values", response_model=list[FCValueOut])
async def get_fc_values(request: Request, position: Optional[str] = None):
    fc_players = getattr(request.app.state, "fc_players", [])
    if not fc_players:
        raise HTTPException(503, "FantasyCalc values not loaded.")
    if position:
        fc_players = [p for p in fc_players if p.position == position.upper()]
    return [_fc_value_out(p) for p in fc_players]

@router.post("/values/refresh", response_model=dict)
async def refresh_fc_values(request: Request):
    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map
    try:
        fc_players = fetch_fc_values(force_refresh=True)
        request.app.state.fc_players = fc_players
        request.app.state.fc_value_map = build_mfl_value_map(fc_players)
        return {"status": "ok", "count": len(fc_players)}
    except Exception as e:
        raise HTTPException(502, f"FantasyCalc refresh failed: {e}")

@router.get("/sell-high", response_model=list[FranchiseSellHighOut])
async def get_all_sell_high(request: Request):
    snapshot = _require_snapshot(request)
    fc_map = _require_fc_map(request)
    from mfl_ai_gm.analysis.sell_high import build_all_sell_reports
    reports = build_all_sell_reports(snapshot, fc_map)
    reports.sort(key=lambda r: len(r.strong_sells)*100+len(r.consider_sells), reverse=True)
    return [_sell_report_out(r) for r in reports]

@router.get("/sell-high/{franchise_id}", response_model=FranchiseSellHighOut)
async def get_franchise_sell_high(franchise_id: str, request: Request):
    snapshot = _require_snapshot(request)
    fc_map = _require_fc_map(request)
    fmap = snapshot.franchise_map
    if franchise_id not in fmap:
        raise HTTPException(404, f"Franchise '{franchise_id}' not found.")
    roster = snapshot.rosters.get(franchise_id)
    if not roster:
        raise HTTPException(404, "No roster found.")
    player_names = {pid: p.name for pid, p in snapshot.players.items()}
    from mfl_ai_gm.analysis.sell_high import build_franchise_sell_report
    ktc_map = getattr(request.app.state, "ktc_value_map", {})
    report = build_franchise_sell_report(
        franchise_id=franchise_id, franchise_name=fmap[franchise_id].name,
        roster_player_ids=roster.all_ids, mfl_value_map=fc_map,
        snapshot_player_names=player_names, ktc_value_map=ktc_map)
    return _sell_report_out(report)


# ---------------------------------------------------------------------------
# Endpoints — consensus + trade calculator
# ---------------------------------------------------------------------------

@router.get("/consensus", response_model=list[ConsensusPlayerOut])
async def get_consensus_values(request: Request, position: Optional[str] = None,
                                limit: int = 200):
    players = getattr(request.app.state, "consensus_players", [])
    if not players:
        raise HTTPException(503, "Consensus values not loaded.")
    # Exclude picks from general consensus list
    players = [p for p in players if not (p.mfl_id.startswith("DP_") or p.mfl_id.startswith("FP_"))]
    if position:
        players = [p for p in players if p.position == position.upper()]
    return [_consensus_out(p) for p in players[:limit]]

@router.get("/consensus/search", response_model=list[ConsensusPlayerOut])
async def search_consensus(request: Request, q: str = "", limit: int = 20):
    """Search players by name for trade calculator autocomplete."""
    players = getattr(request.app.state, "consensus_players", [])
    if not players:
        raise HTTPException(503, "Consensus values not loaded.")
    q_lower = q.lower()
    # Exclude picks from search results
    results = [p for p in players
               if q_lower in p.name.lower()
               and not (p.mfl_id.startswith("DP_") or p.mfl_id.startswith("FP_"))]
    return [_consensus_out(p) for p in results[:limit]]

@router.get("/picks", response_model=list[PickOut])
async def get_pick_values(request: Request):
    """All draft pick values using FC consensus scoring."""
    from mfl_ai_gm.analysis.trade_calculator import get_all_picks
    cmap = getattr(request.app.state, "consensus_map", {})
    result = []
    for pick in get_all_picks():
        mfl_id = pick["mfl_id"]
        agg = cmap.get(mfl_id)
        result.append(PickOut(
            label=pick["label"],
            mfl_id=mfl_id,
            consensus_score=agg.consensus_score if agg else 0.0,
            fc_value=agg.fc_value if agg else None,
            fc_norm=agg.fc_norm if agg else None,
        ))
    return result

@router.post("/trade/evaluate", response_model=TradeVerdictOut)
async def evaluate_trade_endpoint(trade: TradeRequestIn, request: Request):
    """
    Evaluate a dynasty trade — unlimited players and picks on each side.
    Players: pass mfl_id.
    Picks: pass mfl_id (e.g. 'DP_0_4' for 2026 1.05) OR label (resolved automatically).
    """
    from mfl_ai_gm.analysis.trade_calculator import TradeAsset, evaluate_trade, resolve_pick_mfl_id

    consensus_map = _require_consensus_map(request)

    def _build_assets(items: list[TradeAssetIn]) -> list[TradeAsset]:
        assets = []
        for item in items:
            mfl_id = item.mfl_id
            # For picks without mfl_id, resolve from label
            if item.asset_type == "pick" and not mfl_id:
                mfl_id = resolve_pick_mfl_id(item.label)
                if not mfl_id:
                    raise HTTPException(400, f"Unknown pick label: '{item.label}'. "
                        "Use format '2026 Pick 1.05' or pass mfl_id directly.")
            assets.append(TradeAsset(
                asset_type=item.asset_type,
                label=item.label,
                mfl_id=mfl_id,
            ))
        return assets

    side_a = _build_assets(trade.side_a)
    side_b = _build_assets(trade.side_b)

    if not side_a and not side_b:
        raise HTTPException(400, "Both sides of the trade are empty.")

    verdict = evaluate_trade(side_a, side_b, consensus_map)
    return _verdict_out(verdict)

@router.get("/roster/{franchise_id}", response_model=list)
async def get_franchise_roster(franchise_id: str, request: Request):
    """Full roster for a franchise with consensus values attached."""
    snapshot = _require_snapshot(request)
    fmap = snapshot.franchise_map
    if franchise_id not in fmap:
        raise HTTPException(404, f"Franchise {franchise_id!r} not found.")
    roster = snapshot.rosters.get(franchise_id)
    if not roster:
        raise HTTPException(404, "No roster found.")
    consensus_map = getattr(request.app.state, "consensus_map", {})
    players_dict = snapshot.players
    result = []
    for slot in roster.slots:
        pid = slot.player_id
        p = players_dict.get(pid)
        if not p or p.is_team_unit:
            continue
        agg = consensus_map.get(pid)
        result.append({
            "player_id": pid,
            "name": p.name,
            "position": p.position,
            "nfl_team": p.nfl_team,
            "age": p.age,
            "status": slot.status,
            "consensus_score": agg.consensus_score if agg else None,
            "consensus_rank": agg.consensus_rank if agg else None,
            "ktc_value": agg.ktc_value if agg else None,
            "ktc_norm": agg.ktc_norm if agg else None,
            "ktc_rank": agg.ktc_rank if agg else None,
            "fc_norm": agg.fc_norm if agg else None,
            "dp_norm": agg.dp_norm if agg else None,
            "sources": agg.sources if agg else 0,
            "value_signal": agg.value_signal if agg else None,
            "is_disputed": agg.is_disputed if agg else False,
        })
    # Sort: skill positions first, then by consensus score desc
    pos_order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
    result.sort(key=lambda x: (
        pos_order.get(x["position"], 9),
        -(x["consensus_score"] or 0)
    ))
    return result


@router.get("/standings", response_model=list)
async def get_standings(request: Request):
    """League standings sorted by wins then points for."""
    snapshot = _require_snapshot(request)
    fmap = snapshot.franchise_map
    result = []
    for fid, s in snapshot.standings.items():
        franchise = fmap.get(fid)
        result.append({
            "franchise_id": fid,
            "franchise_name": franchise.name if franchise else fid,
            "wins": s.wins,
            "losses": s.losses,
            "ties": s.ties,
            "record": s.record,
            "points_for": s.points_for,
            "points_against": s.points_against,
            "streak": s.streak,
        })
    result.sort(key=lambda x: (-x["wins"], -x["points_for"]))
    return result


@router.get("/franchises", response_model=list)
async def get_franchises(request: Request):
    """All franchises - id, name, owner. Used for UI selects."""
    snapshot = _require_snapshot(request)
    return [
        {"franchise_id": fid, "franchise_name": f.name, "owner_name": f.owner_name}
        for fid, f in snapshot.franchise_map.items()
    ]


@router.post("/values/refresh-all", response_model=dict)
async def refresh_all_values(request: Request):
    """Force refresh FC + DP values and rebuild consensus. Bypasses all caches."""
    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map as fc_mfl_map
    from mfl_ai_gm.adapters.dynastyprocess_client import fetch_dp_values, build_dp_mfl_map, fetch_dp_picks
    from mfl_ai_gm.analysis.value_aggregator import build_consensus_values, build_consensus_mfl_map
    try:
        fc_players = fetch_fc_values(force_refresh=True)
        dp_players = fetch_dp_values(force_refresh=True)
        dp_picks = fetch_dp_picks(force_refresh=True)
        fc_map = fc_mfl_map(fc_players)
        dp_map = build_dp_mfl_map(dp_players)
        consensus = build_consensus_values(fc_map, dp_map)
        request.app.state.fc_players = fc_players
        request.app.state.fc_value_map = fc_map
        request.app.state.dp_players = dp_players
        request.app.state.dp_value_map = dp_map
        request.app.state.dp_picks = dp_picks
        request.app.state.consensus_players = consensus
        request.app.state.consensus_map = build_consensus_mfl_map(consensus)
        return {"status": "ok", "fc": len(fc_players), "dp": len(dp_players),
                "consensus": len(consensus)}
    except Exception as e:
        raise HTTPException(502, f"Value refresh failed: {e}")
