"""
Layer 6 — Service
FastAPI application entry point. Thin routing only — no business logic here.
Port: 8002 (baseball yahoo-ai-gm is on 8001)
systemd service: mfl-ai-gm
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from service.routers import analysis, franchise, snapshot
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"

@asynccontextmanager
async def lifespan(app: FastAPI):
    from mfl_ai_gm.snapshot.builder import DEFAULT_SNAPSHOT_PATH, load_snapshot
    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map as fc_mfl_map
    from mfl_ai_gm.adapters.dynastyprocess_client import fetch_dp_values, build_dp_mfl_map, fetch_dp_picks
    from mfl_ai_gm.analysis.value_aggregator import build_consensus_values, build_consensus_mfl_map
    from mfl_ai_gm.analysis.trade_calculator import build_pick_value_table

    logger.info("mfl-ai-gm service starting...")

    # Load snapshot
    if DEFAULT_SNAPSHOT_PATH.exists():
        try:
            app.state.snapshot = load_snapshot()
            logger.info("Snapshot loaded: %s season %s — %d franchises",
                app.state.snapshot.league_name, app.state.snapshot.season,
                len(app.state.snapshot.franchises))
        except Exception as e:
            logger.warning("Failed to load snapshot: %s", e)
            app.state.snapshot = None
    else:
        logger.warning("No snapshot at %s", DEFAULT_SNAPSHOT_PATH)
        app.state.snapshot = None

    # Load FantasyCalc
    try:
        fc_players = fetch_fc_values()
        app.state.fc_players = fc_players
        app.state.fc_value_map = fc_mfl_map(fc_players)
        logger.info("FantasyCalc: %d players, %d MFL IDs", len(fc_players), len(app.state.fc_value_map))
    except Exception as e:
        logger.warning("FantasyCalc load failed: %s", e)
        app.state.fc_players = []
        app.state.fc_value_map = {}

    # Load DynastyProcess player values
    try:
        dp_players = fetch_dp_values()
        app.state.dp_players = dp_players
        app.state.dp_value_map = build_dp_mfl_map(dp_players)
        logger.info("DynastyProcess: %d players, %d MFL IDs", len(dp_players), len(app.state.dp_value_map))
    except Exception as e:
        logger.warning("DynastyProcess load failed: %s", e)
        app.state.dp_players = []
        app.state.dp_value_map = {}

    # Load DP pick values
    try:
        dp_picks = fetch_dp_picks()
        app.state.dp_picks = dp_picks
        logger.info("DP picks: %d slots", len(dp_picks))
    except Exception as e:
        logger.warning("DP picks load failed: %s", e)
        app.state.dp_picks = []

    # Build consensus + pick table
    try:
        consensus = build_consensus_values(app.state.fc_value_map, app.state.dp_value_map)
        app.state.consensus_players = consensus
        app.state.consensus_map = build_consensus_mfl_map(consensus)
        app.state.pick_table = build_pick_value_table(app.state.dp_picks)
        both = sum(1 for p in consensus if p.sources == 2)
        logger.info("Consensus: %d players ranked, %d with both sources, %d pick slots",
            len(consensus), both, len(app.state.pick_table))
    except Exception as e:
        logger.warning("Consensus build failed: %s", e)
        app.state.consensus_players = []
        app.state.consensus_map = {}
        app.state.pick_table = {}

    yield
    logger.info("mfl-ai-gm service shutting down.")

app = FastAPI(
    title="MFL AI GM",
    description="AI-driven dynasty fantasy football GM for Purple Monkey Dynasty League",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(snapshot.router, prefix="/api/snapshot", tags=["Snapshot"])
app.include_router(analysis.router, prefix="/api", tags=["Analysis"])
app.include_router(franchise.router, prefix="/api/franchise", tags=["Franchise"])
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", include_in_schema=False)
async def serve_ui():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"service": "mfl-ai-gm", "docs": "/docs"})

@app.get("/health", tags=["Health"])
async def health():
    snap = app.state.snapshot
    return {
        "status": "ok", "service": "mfl-ai-gm", "port": 8002,
        "snapshot_loaded": snap is not None,
        "league": snap.league_name if snap else None,
        "season": snap.season if snap else None,
        "week": snap.week if snap else None,
        "franchises": len(snap.franchises) if snap else 0,
        "fc_players": len(getattr(app.state, "fc_value_map", {})),
        "dp_players": len(getattr(app.state, "dp_value_map", {})),
        "consensus_players": len(getattr(app.state, "consensus_players", [])),
        "pick_slots": len(getattr(app.state, "pick_table", {})),
    }
