"""
Layer 5 — Adapters
DynastyProcess client — fetches open dynasty trade values and player ID crosswalk.

Sources (GitHub raw, no auth required):
  values-players.csv  — dynasty trade values (value_1qb), ECR (ecr_1qb), updated weekly
  db_playerids.csv    — full ID crosswalk: mfl_id ↔ fantasypros_id ↔ sleeper_id ↔ etc.

Strategy:
  1. Fetch db_playerids.csv → build fp_id → mfl_id lookup
  2. Fetch values-players.csv → join on fp_id → tag each row with mfl_id
  3. Cache both to disk (24h TTL)
"""

from __future__ import annotations

import csv
import io
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

DP_VALUES_URL = (
    "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values-players.csv"
)
DP_PLAYERIDS_URL = (
    "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
)

DEFAULT_VALUES_CACHE = Path("data/dp_values.json")
DEFAULT_IDS_CACHE = Path("data/dp_playerids.json")

CACHE_TTL_SECONDS = 86_400  # 24 hours
REQUEST_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

class DPPlayerValue:
    """Dynasty trade value for a single player from DynastyProcess."""

    __slots__ = (
        "name", "position", "nfl_team", "age", "draft_year",
        "ecr_1qb", "ecr_2qb", "ecr_pos",
        "value_1qb", "value_2qb",
        "scrape_date", "fp_id", "mfl_id",
    )

    def __init__(self, row: dict, mfl_id: Optional[str] = None):
        self.name: str = row.get("player", "")
        self.position: str = row.get("pos", "")
        self.nfl_team: str = row.get("team", "FA")
        self.age: Optional[float] = _float(row.get("age"))
        self.draft_year: Optional[int] = _int(row.get("draft_year"))
        self.ecr_1qb: Optional[float] = _float(row.get("ecr_1qb"))
        self.ecr_2qb: Optional[float] = _float(row.get("ecr_2qb"))
        self.ecr_pos: Optional[float] = _float(row.get("ecr_pos"))
        self.value_1qb: int = int(_float(row.get("value_1qb")) or 0)
        self.value_2qb: int = int(_float(row.get("value_2qb")) or 0)
        self.scrape_date: str = row.get("scrape_date", "")
        self.fp_id: Optional[str] = row.get("fp_id") or None
        self.mfl_id: Optional[str] = mfl_id

    @classmethod
    def from_dict(cls, d: dict) -> "DPPlayerValue":
        obj = cls.__new__(cls)
        obj.name = d.get("name", "")
        obj.position = d.get("position", "")
        obj.nfl_team = d.get("nfl_team", "FA")
        obj.age = d.get("age")
        obj.draft_year = d.get("draft_year")
        obj.ecr_1qb = d.get("ecr_1qb")
        obj.ecr_2qb = d.get("ecr_2qb")
        obj.ecr_pos = d.get("ecr_pos")
        obj.value_1qb = d.get("value_1qb", 0)
        obj.value_2qb = d.get("value_2qb", 0)
        obj.scrape_date = d.get("scrape_date", "")
        obj.fp_id = d.get("fp_id")
        obj.mfl_id = d.get("mfl_id")
        return obj

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "position": self.position,
            "nfl_team": self.nfl_team,
            "age": self.age,
            "draft_year": self.draft_year,
            "ecr_1qb": self.ecr_1qb,
            "ecr_2qb": self.ecr_2qb,
            "ecr_pos": self.ecr_pos,
            "value_1qb": self.value_1qb,
            "value_2qb": self.value_2qb,
            "scrape_date": self.scrape_date,
            "fp_id": self.fp_id,
            "mfl_id": self.mfl_id,
        }

    def __repr__(self):
        return (
            f"DPPlayerValue({self.name!r}, pos={self.position}, "
            f"value_1qb={self.value_1qb}, ecr={self.ecr_1qb}, mfl_id={self.mfl_id!r})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "NA", "nan") else None
    except (ValueError, TypeError):
        return None


def _int(v) -> Optional[int]:
    f = _float(v)
    return int(f) if f is not None else None


def _cache_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS


def _save_json(data: list[dict], path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(data),
        "players": data,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info("DP cache saved: %d %s → %s", len(data), label, path)


def _load_json(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f).get("players", [])


def _fetch_csv(url: str) -> list[dict]:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return list(reader)


# ---------------------------------------------------------------------------
# Player ID crosswalk
# ---------------------------------------------------------------------------

def fetch_dp_playerids(
    cache_path: Path = DEFAULT_IDS_CACHE,
    force_refresh: bool = False,
) -> dict[str, str]:
    """
    Fetch DynastyProcess player ID crosswalk.
    Returns dict: fantasypros_id (str) → mfl_id (str)
    """
    if not force_refresh and _cache_fresh(cache_path):
        logger.info("Loading DP player IDs from cache: %s", cache_path)
        rows = _load_json(cache_path)
        return {r["fp_id"]: r["mfl_id"] for r in rows if r.get("fp_id") and r.get("mfl_id")}

    logger.info("Fetching DP player IDs from GitHub...")
    rows = _fetch_csv(DP_PLAYERIDS_URL)

    # Save minimal crosswalk to cache
    crosswalk = [
        {"fp_id": r["fantasypros_id"], "mfl_id": r["mfl_id"]}
        for r in rows
        if r.get("fantasypros_id") and r.get("mfl_id")
        and r["fantasypros_id"] not in ("", "NA")
        and r["mfl_id"] not in ("", "NA")
    ]
    _save_json(crosswalk, cache_path, "player ID mappings")
    return {r["fp_id"]: r["mfl_id"] for r in crosswalk}


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

def fetch_dp_values(
    values_cache: Path = DEFAULT_VALUES_CACHE,
    ids_cache: Path = DEFAULT_IDS_CACHE,
    force_refresh: bool = False,
) -> list[DPPlayerValue]:
    """
    Fetch DynastyProcess dynasty values, joined with MFL IDs via crosswalk.
    Returns list of DPPlayerValue sorted by value_1qb descending.
    """
    if not force_refresh and _cache_fresh(values_cache):
        logger.info("Loading DP values from cache: %s", values_cache)
        return [DPPlayerValue.from_dict(d) for d in _load_json(values_cache)]

    # Fetch crosswalk first (or from cache)
    fp_to_mfl = fetch_dp_playerids(cache_path=ids_cache, force_refresh=force_refresh)
    logger.info("DP crosswalk: %d fp_id → mfl_id mappings", len(fp_to_mfl))

    logger.info("Fetching DP values from GitHub...")
    try:
        rows = _fetch_csv(DP_VALUES_URL)
    except requests.RequestException as e:
        if values_cache.exists():
            logger.warning("DP fetch failed (%s) — using stale cache", e)
            return [DPPlayerValue.from_dict(d) for d in _load_json(values_cache)]
        raise RuntimeError(f"DP fetch failed and no cache: {e}") from e

    players = []
    for row in rows:
        fp_id = row.get("fp_id", "").strip().strip('"')
        mfl_id = fp_to_mfl.get(fp_id)
        p = DPPlayerValue(row, mfl_id=mfl_id)
        if p.value_1qb > 0:
            players.append(p)

    players.sort(key=lambda p: p.value_1qb, reverse=True)
    _save_json([p.to_dict() for p in players], values_cache, "DP values")

    matched = sum(1 for p in players if p.mfl_id)
    logger.info(
        "DP values: %d players, %d with MFL IDs (%.0f%%), top: %s",
        len(players), matched, 100 * matched / len(players) if players else 0,
        players[0].name if players else "none",
    )
    return players


def build_dp_mfl_map(players: list[DPPlayerValue]) -> dict[str, DPPlayerValue]:
    """Build dict of mfl_id → DPPlayerValue for roster lookups."""
    return {p.mfl_id: p for p in players if p.mfl_id}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    force = "--force" in sys.argv

    players = fetch_dp_values(force_refresh=force)
    matched = sum(1 for p in players if p.mfl_id)

    print(f"\nDynastyProcess Values — {len(players)} players, {matched} with MFL IDs")
    print(f"{'#':<5} {'Player':<28} {'Pos':<5} {'Team':<5} {'Age':<6} {'Value':<8} {'ECR':<6} MFL_ID")
    print("-" * 75)
    for i, p in enumerate(players[:30], 1):
        age_str = f"{p.age:.1f}" if p.age else "?"
        ecr_str = f"{p.ecr_1qb:.1f}" if p.ecr_1qb else "?"
        print(
            f"{i:<5} {p.name:<28} {p.position:<5} {p.nfl_team:<5} "
            f"{age_str:<6} {p.value_1qb:<8} {ecr_str:<6} {p.mfl_id or '—'}"
        )

    print(f"\nMFL ID coverage: {matched}/{len(players)} ({100*matched//len(players) if players else 0}%)")
    if players:
        print(f"Scrape date: {players[0].scrape_date}")
