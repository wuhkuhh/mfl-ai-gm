"""
Layer 5 — Adapters
KeepTradeCut client — scrapes dynasty values from server-rendered HTML.

KTC embeds a `playersArray = [...]` JSON blob directly in the dynasty-rankings page.
No Cloudflare block, no Playwright needed — plain requests + regex parse.

Fetches pages 0..N until a page returns < 500 players (last page).
Filters: QB|WR|RB|TE|RDP (includes picks)
Format: 1 = 1QB values (oneQBValues.value)

KTC value scale: 0-9999 (9999 = best player, 0 = unranked)
MFL ID: mflid field (int, may be 0 for players without MFL ID)

Cache: data/ktc_values.json, TTL 12h (KTC updates frequently)
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

KTC_BASE_URL = "https://keeptradecut.com/dynasty-rankings"
KTC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://keeptradecut.com/",
}

DEFAULT_CACHE_PATH = Path("data/ktc_values.json")
CACHE_TTL_SECONDS = 43_200   # 12 hours — KTC updates continuously
REQUEST_TIMEOUT = 20
PAGE_SIZE = 500               # KTC returns 500 per page
INTER_PAGE_DELAY = 1.5        # seconds between page requests — be polite


class KTCPlayerValue:
    """Dynasty value for a single player/pick from KeepTradeCut."""

    __slots__ = (
        "ktc_id", "name", "slug", "position", "nfl_team", "age",
        "rookie", "bye_week", "draft_year",
        "pick_round", "pick_num",
        "value", "rank", "positional_rank", "overall_tier",
        "trend_overall", "trend_7d",
        "kept", "traded", "cut",
        "adp", "startup_adp", "trade_count",
        "mfl_id",
    )

    def __init__(self, raw: dict):
        self.ktc_id: int = raw.get("playerID", 0)
        self.name: str = raw.get("playerName", "")
        self.slug: str = raw.get("slug", "")
        self.position: str = raw.get("position", "")
        self.nfl_team: str = raw.get("team", "FA") or "FA"
        self.age: Optional[float] = raw.get("age")
        self.rookie: bool = raw.get("rookie", False)
        self.bye_week: Optional[int] = raw.get("byeWeek")
        self.draft_year: Optional[int] = raw.get("draftYear")
        self.pick_round: Optional[int] = raw.get("pickRound") or None
        self.pick_num: Optional[int] = raw.get("pickNum") or None

        # MFL ID — KTC stores as int, 0 means no match
        raw_mfl = raw.get("mflid", 0)
        self.mfl_id: Optional[str] = str(raw_mfl) if raw_mfl else None

        # 1QB values
        qb = raw.get("oneQBValues", {})
        self.value: int = qb.get("value", 0)
        self.rank: int = qb.get("rank", 9999)
        self.positional_rank: int = qb.get("positionalRank", 999)
        self.overall_tier: int = qb.get("overallTier", 20)
        self.trend_overall: int = qb.get("overallTrend", 0)
        self.trend_7d: int = qb.get("overall7DayTrend", 0)
        self.kept: int = qb.get("kept", 0)
        self.traded: int = qb.get("traded", 0)
        self.cut: int = qb.get("cut", 0)
        self.adp: Optional[float] = qb.get("adp")
        self.startup_adp: Optional[float] = qb.get("startupAdp")
        self.trade_count: int = qb.get("tradeCount", 0)

    @classmethod
    def from_dict(cls, d: dict) -> "KTCPlayerValue":
        obj = cls.__new__(cls)
        obj.ktc_id = d.get("ktc_id", 0)
        obj.name = d.get("name", "")
        obj.slug = d.get("slug", "")
        obj.position = d.get("position", "")
        obj.nfl_team = d.get("nfl_team", "FA")
        obj.age = d.get("age")
        obj.rookie = d.get("rookie", False)
        obj.bye_week = d.get("bye_week")
        obj.draft_year = d.get("draft_year")
        obj.pick_round = d.get("pick_round")
        obj.pick_num = d.get("pick_num")
        obj.mfl_id = d.get("mfl_id")
        obj.value = d.get("value", 0)
        obj.rank = d.get("rank", 9999)
        obj.positional_rank = d.get("positional_rank", 999)
        obj.overall_tier = d.get("overall_tier", 20)
        obj.trend_overall = d.get("trend_overall", 0)
        obj.trend_7d = d.get("trend_7d", 0)
        obj.kept = d.get("kept", 0)
        obj.traded = d.get("traded", 0)
        obj.cut = d.get("cut", 0)
        obj.adp = d.get("adp")
        obj.startup_adp = d.get("startup_adp")
        obj.trade_count = d.get("trade_count", 0)
        return obj

    def to_dict(self) -> dict:
        return {
            "ktc_id": self.ktc_id, "name": self.name, "slug": self.slug,
            "position": self.position, "nfl_team": self.nfl_team, "age": self.age,
            "rookie": self.rookie, "bye_week": self.bye_week, "draft_year": self.draft_year,
            "pick_round": self.pick_round, "pick_num": self.pick_num, "mfl_id": self.mfl_id,
            "value": self.value, "rank": self.rank, "positional_rank": self.positional_rank,
            "overall_tier": self.overall_tier, "trend_overall": self.trend_overall,
            "trend_7d": self.trend_7d, "kept": self.kept, "traded": self.traded,
            "cut": self.cut, "adp": self.adp, "startup_adp": self.startup_adp,
            "trade_count": self.trade_count,
        }

    def __repr__(self):
        return f"KTCPlayerValue({self.name!r}, value={self.value}, rank={self.rank}, mfl_id={self.mfl_id!r})"


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def _fetch_page(page: int, session: requests.Session) -> list[dict]:
    """Fetch one page of KTC dynasty rankings. Returns raw player dicts."""
    url = f"{KTC_BASE_URL}?page={page}&filters=QB|WR|RB|TE|RDP&format=1"
    resp = session.get(url, headers=KTC_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    m = re.search(r'playersArray\s*=\s*(\[.*?\])\s*;', resp.text, re.DOTALL)
    if not m:
        # Try without DOTALL — some pages have it on one line
        m = re.search(r'playersArray = (\[.*\])', resp.text)
    if not m:
        logger.warning("KTC page %d: playersArray not found in HTML", page)
        return []

    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.warning("KTC page %d: JSON parse error: %s", page, e)
        return []


def _cache_fresh(path: Path, ttl: int = CACHE_TTL_SECONDS) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl


def _save_cache(players: list[KTCPlayerValue], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(players),
        "players": [p.to_dict() for p in players],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info("KTC cache saved: %d players → %s", len(players), path)


def _load_cache(path: Path) -> list[KTCPlayerValue]:
    with open(path) as f:
        data = json.load(f)
    return [KTCPlayerValue.from_dict(d) for d in data.get("players", [])]


def fetch_ktc_values(
    cache_path: Path = DEFAULT_CACHE_PATH,
    force_refresh: bool = False,
    max_pages: int = 1,
) -> list[KTCPlayerValue]:
    """
    Fetch KTC dynasty values for all players + picks.
    Uses cached data if fresh (< 12h). Pass force_refresh=True to bypass.

    Returns list sorted by rank ascending (best player first).
    """
    if not force_refresh and _cache_fresh(cache_path):
        logger.info("Loading KTC values from cache: %s", cache_path)
        players = _load_cache(cache_path)
        logger.info("KTC cache: %d players loaded", len(players))
        return players

    logger.info("Fetching KTC values from keeptradecut.com...")
    session = requests.Session()
    all_players: list[KTCPlayerValue] = []

    for page in range(max_pages):
        logger.info("KTC fetching page %d...", page)
        raw = _fetch_page(page, session)
        if not raw:
            logger.info("KTC page %d empty — stopping", page)
            break

        for r in raw:
            p = KTCPlayerValue(r)
            if p.value > 0:  # skip unranked / 0-value players
                all_players.append(p)

        logger.info("KTC page %d: %d players (running total: %d)", page, len(raw), len(all_players))

        if len(raw) < PAGE_SIZE:
            logger.info("KTC last page reached at page %d", page)
            break

        if page < max_pages - 1:
            time.sleep(INTER_PAGE_DELAY)

    # Deduplicate by ktc_id (KTC returns same players on every page)
    seen_ids: set[int] = set()
    unique_players = []
    for p in all_players:
        if p.ktc_id not in seen_ids:
            seen_ids.add(p.ktc_id)
            unique_players.append(p)
    all_players = unique_players
    # Sort by rank
    all_players.sort(key=lambda p: p.rank)
    _save_cache(all_players, cache_path)

    matched = sum(1 for p in all_players if p.mfl_id)
    picks = sum(1 for p in all_players if p.pick_round)
    logger.info(
        "KTC: %d total, %d with MFL IDs (%.0f%%), %d picks",
        len(all_players), matched,
        100 * matched / len(all_players) if all_players else 0,
        picks,
    )
    return all_players


def build_ktc_mfl_map(players: list[KTCPlayerValue]) -> dict[str, KTCPlayerValue]:
    """Build mfl_id → KTCPlayerValue map. Excludes players without MFL IDs."""
    return {p.mfl_id: p for p in players if p.mfl_id}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    force = "--force" in sys.argv

    players = fetch_ktc_values(force_refresh=force)
    ktc_map = build_ktc_mfl_map(players)
    picks = [p for p in players if p.pick_round]

    print(f"\nKTC Dynasty Values — {len(players)} players, {len(ktc_map)} MFL IDs, {len(picks)} picks")
    print(f"\nTop 15 Players:")
    print(f"  {'#':<5} {'Name':<28} {'Pos':<5} {'Team':<5} {'Value':<7} {'Trend':<8} MFL_ID")
    print("  " + "-" * 68)
    for p in players[:15]:
        trend = f"+{p.trend_overall}" if p.trend_overall > 0 else str(p.trend_overall)
        print(f"  {p.rank:<5} {p.name:<28} {p.position:<5} {p.nfl_team:<5} {p.value:<7} {trend:<8} {p.mfl_id}")

    print(f"\nTop 10 Picks:")
    for p in picks[:10]:
        print(f"  {p.rank:<5} {p.name:<28} value={p.value}  round={p.pick_round} num={p.pick_num}  mfl={p.mfl_id}")

    # Show a few key players
    print(f"\nKey player lookup by MFL ID:")
    for mfl_id, label in [("16161", "Bijan Robinson"), ("15715", "James Cook"), ("16185", "JSN")]:
        p = ktc_map.get(mfl_id)
        if p:
            print(f"  {label}: value={p.value} rank={p.rank} trend={p.trend_overall:+d}")
        else:
            print(f"  {label} (mfl_id={mfl_id}): NOT IN MAP")
