"""
Layer 6 — Service / Routers
Snapshot endpoints. Triggers a fresh data pull from MFL and reloads app state.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

# Simple lock to prevent concurrent refreshes
_refresh_in_progress = False


class SnapshotStatusOut(BaseModel):
    loaded: bool
    league_name: str | None
    season: str | None
    week: str | None
    franchises: int
    players: int
    last_refreshed: str | None


class RefreshResultOut(BaseModel):
    success: bool
    message: str
    league_name: str | None = None
    season: str | None = None
    week: str | None = None
    franchises: int = 0
    players: int = 0
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=SnapshotStatusOut)
async def snapshot_status(request: Request):
    """Current snapshot status — what's loaded in memory."""
    snapshot = request.app.state.snapshot
    if snapshot is None:
        return SnapshotStatusOut(
            loaded=False,
            league_name=None,
            season=None,
            week=None,
            franchises=0,
            players=0,
            last_refreshed=None,
        )

    # Try to get file mtime as last_refreshed
    from mfl_ai_gm.snapshot.builder import DEFAULT_SNAPSHOT_PATH
    last_refreshed = None
    if DEFAULT_SNAPSHOT_PATH.exists():
        mtime = DEFAULT_SNAPSHOT_PATH.stat().st_mtime
        last_refreshed = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return SnapshotStatusOut(
        loaded=True,
        league_name=snapshot.league_name,
        season=snapshot.season,
        week=snapshot.week,
        franchises=len(snapshot.franchises),
        players=len(snapshot.players),
        last_refreshed=last_refreshed,
    )


@router.post("/refresh", response_model=RefreshResultOut)
async def refresh_snapshot(request: Request):
    """
    Fetch fresh data from MFL API, rebuild snapshot, reload into app state.
    Blocks until complete — typically 2–4 seconds.
    Do not call concurrently.
    """
    global _refresh_in_progress

    if _refresh_in_progress:
        raise HTTPException(status_code=409, detail="Snapshot refresh already in progress.")

    _refresh_in_progress = True
    start = datetime.now(timezone.utc)

    try:
        from mfl_ai_gm.snapshot.builder import build_snapshot, save_snapshot
        snapshot = build_snapshot()
        save_snapshot(snapshot)
        request.app.state.snapshot = snapshot

        duration = (datetime.now(timezone.utc) - start).total_seconds()

        return RefreshResultOut(
            success=True,
            message="Snapshot refreshed successfully.",
            league_name=snapshot.league_name,
            season=snapshot.season,
            week=snapshot.week,
            franchises=len(snapshot.franchises),
            players=len(snapshot.players),
            duration_seconds=round(duration, 2),
        )

    except Exception as e:
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        raise HTTPException(
            status_code=500,
            detail=f"Snapshot refresh failed after {duration:.1f}s: {e}",
        )
    finally:
        _refresh_in_progress = False
