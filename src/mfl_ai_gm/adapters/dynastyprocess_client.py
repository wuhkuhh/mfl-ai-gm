"""
Layer 5 — Adapters
DynastyProcess client — fetches open dynasty trade values and player ID crosswalk.

Sources (GitHub raw, no auth required):
  values-players.csv  — dynasty trade values (value_1qb), ECR (ecr_1qb), updated weekly
  values-picks.csv    — pick slot values by round/slot, ECR-based
  db_playerids.csv    — full ID crosswalk: mfl_id ↔ fantasypros_id ↔ sleeper_id ↔ etc.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DP_VALUES_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values-players.csv"
DP_PLAYERIDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
DP_PICKS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values-picks.csv"

DEFAULT_VALUES_CACHE = Path("data/dp_values.json")
DEFAULT_IDS_CACHE = Path("data/dp_playerids.json")
DEFAULT_PICKS_CACHE = Path("data/dp_picks.json")

CACHE_TTL_SECONDS = 86_400
REQUEST_TIMEOUT = 15


class DPPlayerValue:
    __slots__ = ("name","position","nfl_team","age","draft_year","ecr_1qb","ecr_2qb",
                 "ecr_pos","value_1qb","value_2qb","scrape_date","fp_id","mfl_id")

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
        return {"name": self.name, "position": self.position, "nfl_team": self.nfl_team,
                "age": self.age, "draft_year": self.draft_year, "ecr_1qb": self.ecr_1qb,
                "ecr_2qb": self.ecr_2qb, "ecr_pos": self.ecr_pos, "value_1qb": self.value_1qb,
                "value_2qb": self.value_2qb, "scrape_date": self.scrape_date,
                "fp_id": self.fp_id, "mfl_id": self.mfl_id}


class DPPickValue:
    __slots__ = ("label","pick_round","pick_slot","pick_year","ecr_1qb","ecr_2qb","scrape_date")

    def __init__(self, row: dict):
        self.label: str = row.get("player", "").strip('"')
        self.ecr_1qb: Optional[float] = _float(row.get("ecr_1qb"))
        self.ecr_2qb: Optional[float] = _float(row.get("ecr_2qb"))
        self.scrape_date: str = row.get("scrape_date", "")
        self.pick_year, self.pick_round, self.pick_slot = _parse_pick_label(self.label)

    @classmethod
    def from_dict(cls, d: dict) -> "DPPickValue":
        obj = cls.__new__(cls)
        obj.label = d.get("label", "")
        obj.ecr_1qb = d.get("ecr_1qb")
        obj.ecr_2qb = d.get("ecr_2qb")
        obj.scrape_date = d.get("scrape_date", "")
        obj.pick_year = d.get("pick_year")
        obj.pick_round = d.get("pick_round")
        obj.pick_slot = d.get("pick_slot")
        return obj

    def to_dict(self) -> dict:
        return {"label": self.label, "ecr_1qb": self.ecr_1qb, "ecr_2qb": self.ecr_2qb,
                "scrape_date": self.scrape_date, "pick_year": self.pick_year,
                "pick_round": self.pick_round, "pick_slot": self.pick_slot}


def _float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "NA", "nan") else None
    except (ValueError, TypeError):
        return None


def _int(v) -> Optional[int]:
    f = _float(v)
    return int(f) if f is not None else None


def _parse_pick_label(label: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    m = re.search(r'(\d{4}).*?(\d+)\.(\d+)', label)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None, None, None


def _cache_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS


def _save_json(data: list[dict], path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": datetime.now(timezone.utc).isoformat(), "count": len(data), "players": data}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info("DP cache saved: %d %s → %s", len(data), label, path)


def _load_json(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f).get("players", [])


def _fetch_csv(url: str) -> list[dict]:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def fetch_dp_playerids(cache_path: Path = DEFAULT_IDS_CACHE, force_refresh: bool = False) -> dict[str, str]:
    if not force_refresh and _cache_fresh(cache_path):
        logger.info("Loading DP player IDs from cache")
        rows = _load_json(cache_path)
        return {r["fp_id"]: r["mfl_id"] for r in rows if r.get("fp_id") and r.get("mfl_id")}
    logger.info("Fetching DP player IDs from GitHub...")
    rows = _fetch_csv(DP_PLAYERIDS_URL)
    crosswalk = [{"fp_id": r["fantasypros_id"], "mfl_id": r["mfl_id"]}
                 for r in rows if r.get("fantasypros_id") and r.get("mfl_id")
                 and r["fantasypros_id"] not in ("", "NA") and r["mfl_id"] not in ("", "NA")]
    _save_json(crosswalk, cache_path, "player ID mappings")
    return {r["fp_id"]: r["mfl_id"] for r in crosswalk}


def fetch_dp_values(values_cache: Path = DEFAULT_VALUES_CACHE,
                    ids_cache: Path = DEFAULT_IDS_CACHE,
                    force_refresh: bool = False) -> list[DPPlayerValue]:
    if not force_refresh and _cache_fresh(values_cache):
        logger.info("Loading DP values from cache")
        return [DPPlayerValue.from_dict(d) for d in _load_json(values_cache)]
    fp_to_mfl = fetch_dp_playerids(cache_path=ids_cache, force_refresh=force_refresh)
    logger.info("Fetching DP values from GitHub...")
    try:
        rows = _fetch_csv(DP_VALUES_URL)
    except requests.RequestException as e:
        if values_cache.exists():
            logger.warning("DP fetch failed (%s) — stale cache", e)
            return [DPPlayerValue.from_dict(d) for d in _load_json(values_cache)]
        raise RuntimeError(f"DP fetch failed: {e}") from e
    players = []
    for row in rows:
        fp_id = row.get("fp_id", "").strip().strip('"')
        p = DPPlayerValue(row, mfl_id=fp_to_mfl.get(fp_id))
        if p.value_1qb > 0:
            players.append(p)
    players.sort(key=lambda p: p.value_1qb, reverse=True)
    _save_json([p.to_dict() for p in players], values_cache, "DP values")
    matched = sum(1 for p in players if p.mfl_id)
    logger.info("DP: %d players, %d MFL IDs (%.0f%%)", len(players), matched,
                100 * matched / len(players) if players else 0)
    return players


def build_dp_mfl_map(players: list[DPPlayerValue]) -> dict[str, DPPlayerValue]:
    return {p.mfl_id: p for p in players if p.mfl_id}


def fetch_dp_picks(cache_path: Path = DEFAULT_PICKS_CACHE,
                   force_refresh: bool = False) -> list[DPPickValue]:
    if not force_refresh and _cache_fresh(cache_path):
        logger.info("Loading DP picks from cache")
        return [DPPickValue.from_dict(d) for d in _load_json(cache_path)]
    logger.info("Fetching DP pick values from GitHub...")
    try:
        rows = _fetch_csv(DP_PICKS_URL)
    except requests.RequestException as e:
        if cache_path.exists():
            logger.warning("DP picks fetch failed (%s) — stale cache", e)
            return [DPPickValue.from_dict(d) for d in _load_json(cache_path)]
        logger.warning("DP picks unavailable: %s", e)
        return []
    picks = []
    for row in rows:
        row["player"] = row.get("player", "").strip('"')
        p = DPPickValue(row)
        if p.pick_round is not None and p.ecr_1qb is not None:
            picks.append(p)
    picks.sort(key=lambda p: (p.pick_round or 99, p.pick_slot or 99))
    _save_json([p.to_dict() for p in picks], cache_path, "DP picks")
    logger.info("DP picks: %d slots", len(picks))
    return picks


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    force = "--force" in sys.argv
    players = fetch_dp_values(force_refresh=force)
    picks = fetch_dp_picks(force_refresh=force)
    matched = sum(1 for p in players if p.mfl_id)
    print(f"\nDP Values: {len(players)} players, {matched} MFL IDs")
    for i, p in enumerate(players[:10], 1):
        print(f"  {i:<3} {p.name:<28} {p.position:<4} {p.value_1qb:<7} MFL={p.mfl_id}")
    print(f"\nDP Picks: {len(picks)} slots")
    for p in picks[:16]:
        print(f"  {p.label:<22} R{p.pick_round}.{p.pick_slot:02d}  ecr={p.ecr_1qb}")
