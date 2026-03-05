"""
Layer 6 — Service
FastAPI application entry point. Thin routing only — no business logic here.

Port: 8001 (baseball is on 8000)
systemd service: mfl-ai-gm
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from service.routers import analysis, franchise, snapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan — load snapshot on startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load snapshot into app state on startup."""
    from mfl_ai_gm.snapshot.builder import DEFAULT_SNAPSHOT_PATH, load_snapshot
    logger.info("mfl-ai-gm service starting...")

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
        logger.warning("No snapshot found at %s — call /api/snapshot/refresh first", DEFAULT_SNAPSHOT_PATH)
        app.state.snapshot = None

    yield

    logger.info("mfl-ai-gm service shutting down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(snapshot.router, prefix="/api/snapshot", tags=["Snapshot"])
app.include_router(analysis.router, prefix="/api", tags=["Analysis"])
app.include_router(franchise.router, prefix="/api/franchise", tags=["Franchise"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
async def health():
    from fastapi import Request
    snapshot = app.state.snapshot
    return {
        "status": "ok",
        "service": "mfl-ai-gm",
        "port": 8001,
        "snapshot_loaded": snapshot is not None,
        "league": snapshot.league_name if snapshot else None,
        "season": snapshot.season if snapshot else None,
        "week": snapshot.week if snapshot else None,
        "franchises": len(snapshot.franchises) if snapshot else 0,
    }


@app.get("/", tags=["Health"])
async def root():
    return JSONResponse({
        "service": "mfl-ai-gm",
        "docs": "/docs",
        "health": "/health",
        "endpoints": [
            "GET  /api/roster-construction",
            "GET  /api/contention-windows",
            "GET  /api/franchise/{franchise_id}",
            "POST /api/snapshot/refresh",
        ]
    })
