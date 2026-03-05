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
    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map
    logger.info("mfl-ai-gm service starting...")

    # Load snapshot
    if DEFAULT_SNAPSHOT_PATH.exists():
        try:
            app.state.snapshot = load_snapshot()
            logger.info(
                "Snapshot loaded: %s season %s — %d franchises",
                app.state.snapshot.league_name,
                app.state.snapshot.season,
                len(app.state.snapshot.franchises),
            )
        except Exception as e:
            logger.warning("Failed to load snapshot on startup: %s", e)
            app.state.snapshot = None
    else:
        logger.warning(
            "No snapshot found at %s — call POST /api/snapshot/refresh first",
            DEFAULT_SNAPSHOT_PATH,
        )
        app.state.snapshot = None

    # Load FantasyCalc values (cached 24h, safe to call every startup)
    try:
        fc_players = fetch_fc_values()
        app.state.fc_value_map = build_mfl_value_map(fc_players)
        app.state.fc_players = fc_players
        logger.info("FantasyCalc values loaded: %d players with MFL IDs", len(app.state.fc_value_map))
    except Exception as e:
        logger.warning("Failed to load FantasyCalc values on startup: %s", e)
        app.state.fc_value_map = {}
        app.state.fc_players = []

    yield
    logger.info("mfl-ai-gm service shutting down.")

app = FastAPI(
    title="MFL AI GM",
    description="AI-driven dynasty fantasy football GM for Purple Monkey Dynasty League",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
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
    return JSONResponse({"service": "mfl-ai-gm", "docs": "/docs", "ui": "index.html not found"})

@app.get("/health", tags=["Health"])
async def health():
    snapshot = app.state.snapshot
    fc_count = len(getattr(app.state, "fc_value_map", {}))
    return {
        "status": "ok",
        "service": "mfl-ai-gm",
        "port": 8002,
        "snapshot_loaded": snapshot is not None,
        "league": snapshot.league_name if snapshot else None,
        "season": snapshot.season if snapshot else None,
        "week": snapshot.week if snapshot else None,
        "franchises": len(snapshot.franchises) if snapshot else 0,
        "fc_values_loaded": fc_count > 0,
        "fc_player_count": fc_count,
    }
