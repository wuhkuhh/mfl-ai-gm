"""
Layer 5 — Adapters
FantasyCalc client — fetches dynasty player trade values.

Endpoint: https://api.fantasycalc.com/values/current
  isDynasty=true
  numQbs=1        (1QB league)
  numTeams=14     (14-team league)
  ppr=1           (PPR)

Returns list of players with:
  - value (0–10000+ dynasty trade value)
  - overallRank / positionRank
  - trend30Day (value change over 30 days, positive = rising)
  - mflId (direct match to MFL player IDs)
  - maybeAge, position, nfl team

Cache: data/fantasycalc_values.json — refreshed daily.
No auth required. Open API.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FC_BASE_URL = "https://api.fantasycalc.com/values/current"

FC_PARAMS = {
    "isDynasty": "true",
    "numQbs": "1",
    "numTeams": "14",
    "ppr": "1",
}

DEFAULT_CACHE_PATH = Path("data/fantasycalc_values.json")

# Refresh cache if older than this many seconds (24 hours)
CACHE_TTL_SECONDS = 86_400

REQUEST_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

class FCPlayerValue:
    """Dynasty trade value for a single player from FantasyCalc."""

    __slots__ = (
        "fc_id", "name", "mfl_id", "sleeper_id", "position",
        "nfl_team", "age", "value", "overall_rank", "position_rank",
        "trend_30d", "redraft_value", "tier",
    )

    def __init__(self, raw: dict):
        """Construct from raw FantasyCalc API response dict."""
        p = raw.get("player", {})
        self.fc_id: int = p.get("id", 0)
        self.name: str = p.get("name", "")
        self.mfl_id: Optional[str] = p.get("mflId")
        self.sleeper_id: Optional[str] = p.get("sleeperId")
        self.position: str = p.get("position", "")
        self.nfl_team: str = p.get("maybeTeam") or "FA"
        self.age: Optional[float] = p.get("maybeAge")
        self.value: int = raw.get("value", 0)
        self.overall_rank: int = raw.get("overallRank", 999)
        self.position_rank: int = raw.get("positionRank", 999)
        self.trend_30d: int = raw.get("trend30Day", 0)
        self.redraft_value: int = raw.get("redraftValue", 0)
        self.tier: Optional[int] = raw.get("maybeTier")

    @classmethod
    def from_dict(cls, d: dict) -> "FCPlayerValue":
        """Reconstruct from cached flat dict (to_dict format)."""
        obj = cls.__new__(cls)
        obj.fc_id = d.get("fc_id", 0)
        obj.name = d.get("name", "")
        obj.mfl_id = d.get("mfl_id")
        obj.sleeper_id = d.get("sleeper_id")
        obj.position = d.get("position", "")
        obj.nfl_team = d.get("nfl_team", "FA")
        obj.age = d.get("age")
        obj.value = d.get("value", 0)
        obj.overall_rank = d.get("overall_rank", 999)
        obj.position_rank = d.get("position_rank", 999)
        obj.trend_30d = d.get("trend_30d", 0)
        obj.redraft_value = d.get("redraft_value", 0)
        obj.tier = d.get("tier")
        return obj

    def to_dict(self) -> dict:
        return {
            "fc_id": self.fc_id,
            "name": self.name,
            "mfl_id": self.mfl_id,
            "sleeper_id": self.sleeper_id,
            "position": self.position,
            "nfl_team": self.nfl_team,
            "age": self.age,
            "value": self.value,
            "overall_rank": self.overall_rank,
            "position_rank": self.position_rank,
            "trend_30d": self.trend_30d,
            "redraft_value": self.redraft_value,
            "tier": self.tier,
        }

    def __repr__(self):
        return (
            f"FCPlayerValue({self.name!r}, pos={self.position}, "
            f"value={self.value}, rank={self.overall_rank}, "
            f"trend={self.trend_30d:+d})"
        )


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_is_fresh(cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    mtime = cache_path.stat().st_mtime
    age = time.time() - mtime
    return age < CACHE_TTL_SECONDS


def _save_cache(data: list[dict], cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(data),
        "players": data,
    }
    with open(cache_path, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info("FantasyCalc cache saved: %d players → %s", len(data), cache_path)


def _load_cache(cache_path: Path) -> list[FCPlayerValue]:
    """Load from flat-dict cache format using from_dict."""
    with open(cache_path) as f:
        payload = json.load(f)
    return [FCPlayerValue.from_dict(d) for d in payload.get("players", [])]


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

def fetch_fc_values(
    cache_path: Path = DEFAULT_CACHE_PATH,
    force_refresh: bool = False,
) -> list[FCPlayerValue]:
    """
    Fetch dynasty player values from FantasyCalc.
    Uses disk cache (TTL 24h) to avoid hammering the API.

    Returns:
        List of FCPlayerValue objects sorted by overall_rank ascending.
    """
    if not force_refresh and _cache_is_fresh(cache_path):
        logger.info("Loading FantasyCalc values from cache: %s", cache_path)
        return _load_cache(cache_path)

    logger.info("Fetching FantasyCalc values from API...")
    try:
        resp = requests.get(FC_BASE_URL, params=FC_PARAMS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        raw_list = resp.json()
    except requests.RequestException as e:
        if cache_path.exists():
            logger.warning("FantasyCalc fetch failed (%s) — using stale cache", e)
            return _load_cache(cache_path)
        raise RuntimeError(f"FantasyCalc fetch failed and no cache available: {e}") from e

    players = [FCPlayerValue(r) for r in raw_list]
    players.sort(key=lambda p: p.overall_rank)

    _save_cache([p.to_dict() for p in players], cache_path)

    logger.info(
        "FantasyCalc: %d players fetched, top player: %s",
        len(players), players[0].name if players else "none",
    )
    return players


def build_mfl_value_map(
    players: list[FCPlayerValue],
) -> dict[str, FCPlayerValue]:
    """Build dict of mfl_id → FCPlayerValue for roster lookups."""
    return {p.mfl_id: p for p in players if p.mfl_id}


def get_cache_metadata(cache_path: Path = DEFAULT_CACHE_PATH) -> dict:
    """Return metadata about the current cache state."""
    if not cache_path.exists():
        return {"cached": False, "age_hours": None, "count": None, "fetched_at": None}
    with open(cache_path) as f:
        payload = json.load(f)
    age_seconds = time.time() - cache_path.stat().st_mtime
    return {
        "cached": True,
        "age_hours": round(age_seconds / 3600, 1),
        "count": payload.get("count"),
        "fetched_at": payload.get("fetched_at"),
        "fresh": _cache_is_fresh(cache_path),
    }


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    force = "--force" in sys.argv
    players = fetch_fc_values(force_refresh=force)

    print(f"\nFantasyCalc Dynasty Values — {len(players)} players")
    print(f"{'#':<5} {'Player':<28} {'Pos':<5} {'Team':<5} {'Age':<6} {'Value':<8} {'Trend':<8} Tier")
    print("-" * 75)
    for p in players[:30]:
        age_str = f"{p.age:.1f}" if p.age else "?"
        print(
            f"{p.overall_rank:<5} {p.name:<28} {p.position:<5} {p.nfl_team:<5} "
            f"{age_str:<6} {p.value:<8} {p.trend_30d:+d:<8} {p.tier or '—'}"
        )

    with_mfl = sum(1 for p in players if p.mfl_id)
    print(f"\nMFL ID coverage: {with_mfl}/{len(players)} ({100*with_mfl//len(players)}%)")
