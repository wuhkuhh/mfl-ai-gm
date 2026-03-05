"""
Layer 3 — Snapshot
Fetches raw data from the MFL adapter, normalizes it into domain models,
persists to data/snapshot.json, and provides a load function for other layers.

No FastAPI. No business logic. Pure data normalization.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mfl_ai_gm.adapters.mfl_client import MFLClient, MFLClientError
from mfl_ai_gm.domain.models import (
    Franchise,
    FuturePick,
    LeagueSnapshot,
    Player,
    Roster,
    RosterSlot,
    Standing,
    TEAM_UNIT_POSITIONS,
)

logger = logging.getLogger(__name__)

# Default snapshot path — relative to repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOT_PATH = _REPO_ROOT / "data" / "snapshot.json"


# ---------------------------------------------------------------------------
# Parsers — raw MFL dicts → domain models
# ---------------------------------------------------------------------------

def _parse_future_picks(raw_string: str, current_owner_id: str) -> list[FuturePick]:
    """
    Parse MFL future_draft_picks string into FuturePick objects.
    Format: "FP_{original_owner}_{year}_{round},..."
    Example: "FP_0001_2027_1,FP_0009_2027_2,"
    """
    picks = []
    for token in raw_string.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split("_")
        if len(parts) != 4 or parts[0] != "FP":
            logger.warning("Unexpected pick format: %s", token)
            continue
        try:
            picks.append(FuturePick(
                original_owner_id=parts[1],
                year=int(parts[2]),
                round=int(parts[3]),
                current_owner_id=current_owner_id,
            ))
        except (ValueError, IndexError) as e:
            logger.warning("Failed to parse pick %s: %s", token, e)
    return picks


def _parse_franchise(raw: dict[str, Any]) -> Franchise:
    """Parse a single franchise dict from the league response."""
    picks_raw = raw.get("future_draft_picks", "")
    fid = raw["id"]
    return Franchise(
        id=fid,
        name=raw.get("name", "Unknown"),
        abbrev=raw.get("abbrev", ""),
        owner_name=raw.get("owner_name", ""),
        waiver_sort_order=int(raw.get("waiverSortOrder", 0)),
        bbid_balance=float(raw.get("bbidAvailableBalance", 0.0)),
        future_picks=_parse_future_picks(picks_raw, fid),
    )


def _parse_player(raw: dict[str, Any]) -> Player:
    """Parse a single player dict from the players response."""
    position = raw.get("position", "")
    age_raw = raw.get("age")
    try:
        age = int(age_raw) if age_raw and str(age_raw).strip() else None
    except (ValueError, TypeError):
        age = None

    return Player(
        id=raw["id"],
        name=raw.get("name", "Unknown"),
        position=position,
        nfl_team=raw.get("team", ""),
        age=age,
        is_team_unit=position in TEAM_UNIT_POSITIONS,
    )


def _parse_roster(raw: dict[str, Any]) -> Roster:
    """Parse a single franchise roster dict from the rosters response."""
    players_raw = raw.get("player", [])
    if isinstance(players_raw, dict):
        players_raw = [players_raw]

    slots = [
        RosterSlot(player_id=p["id"], status=p.get("status", "ROSTER"))
        for p in players_raw
    ]
    return Roster(
        franchise_id=raw["id"],
        week=raw.get("week", "0"),
        slots=slots,
    )


def _parse_standing(raw: dict[str, Any]) -> Standing:
    """Parse a single franchise standing dict from the standings response."""
    def _float(val: Any) -> float:
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0

    def _int(val: Any) -> int:
        try:
            return int(val) if val else 0
        except (ValueError, TypeError):
            return 0

    return Standing(
        franchise_id=raw["id"],
        wins=_int(raw.get("h2hw")),
        losses=_int(raw.get("h2hl")),
        ties=_int(raw.get("h2ht")),
        points_for=_float(raw.get("pf")),
        points_against=_float(raw.get("pa")),
        projected_points=_float(raw.get("pp")),
        h2h_pct=_float(raw.get("h2hpct")),
        all_play_pct=_float(raw.get("all_play_pct")),
        streak=raw.get("strk", "-"),
    )


# ---------------------------------------------------------------------------
# Builder — orchestrates all API calls and normalization
# ---------------------------------------------------------------------------

def build_snapshot(client: MFLClient | None = None) -> LeagueSnapshot:
    """
    Fetch all MFL data and return a normalized LeagueSnapshot.
    Does NOT write to disk — call save_snapshot() for that.
    """
    if client is None:
        client = MFLClient()

    logger.info("Building league snapshot...")

    # ── League + franchises ──────────────────────────────────────────────────
    logger.info("Fetching league metadata...")
    league_data = client.get_league()
    league = league_data["league"]

    franchises_raw = league.get("franchises", {}).get("franchise", [])
    if isinstance(franchises_raw, dict):
        franchises_raw = [franchises_raw]
    franchises = [_parse_franchise(f) for f in franchises_raw]
    logger.info("  %d franchises parsed", len(franchises))

    # ── Players ──────────────────────────────────────────────────────────────
    logger.info("Fetching player universe...")
    players_data = client.get_players(details=True)
    players_raw = players_data.get("players", {}).get("player", [])
    players: dict[str, Player] = {}
    for p in players_raw:
        player = _parse_player(p)
        players[player.id] = player
    logger.info("  %d total players (%d individual, %d team units)",
                len(players),
                sum(1 for p in players.values() if not p.is_team_unit),
                sum(1 for p in players.values() if p.is_team_unit))

    # ── Rosters ──────────────────────────────────────────────────────────────
    logger.info("Fetching rosters...")
    rosters_data = client.get_rosters()
    rosters_raw = rosters_data.get("rosters", {}).get("franchise", [])
    rosters: dict[str, Roster] = {}
    for r in rosters_raw:
        roster = _parse_roster(r)
        rosters[roster.franchise_id] = roster
    logger.info("  %d franchise rosters parsed", len(rosters))

    # ── Standings ────────────────────────────────────────────────────────────
    logger.info("Fetching standings...")
    standings_data = client.get_standings()
    standings_raw = standings_data.get("leagueStandings", {}).get("franchise", [])
    standings: dict[str, Standing] = {}
    for s in standings_raw:
        standing = _parse_standing(s)
        standings[standing.franchise_id] = standing
    logger.info("  %d franchise standings parsed", len(standings))

    # ── Assemble snapshot ────────────────────────────────────────────────────
    # Determine current week from rosters (MFL sets week on each roster)
    current_week = "0"
    if rosters:
        current_week = next(iter(rosters.values())).week

    snapshot = LeagueSnapshot(
        season=client.season,
        week=current_week,
        league_name=league.get("name", ""),
        league_id=league.get("id", client.league_id),
        roster_size=int(league.get("rosterSize", 35)),
        last_regular_season_week=int(league.get("lastRegularSeasonWeek", 14)),
        franchises=franchises,
        players=players,
        rosters=rosters,
        standings=standings,
    )

    logger.info("Snapshot built: %s season %s week %s — %d franchises, %d players",
                snapshot.league_name, snapshot.season, snapshot.week,
                len(snapshot.franchises), len(snapshot.players))

    return snapshot


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _snapshot_to_dict(snapshot: LeagueSnapshot) -> dict[str, Any]:
    """Convert LeagueSnapshot to a JSON-serializable dict."""
    return {
        "meta": {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "season": snapshot.season,
            "week": snapshot.week,
            "league_name": snapshot.league_name,
            "league_id": snapshot.league_id,
            "roster_size": snapshot.roster_size,
            "last_regular_season_week": snapshot.last_regular_season_week,
        },
        "franchises": [asdict(f) for f in snapshot.franchises],
        "players": {pid: asdict(p) for pid, p in snapshot.players.items()},
        "rosters": {
            fid: {
                "franchise_id": r.franchise_id,
                "week": r.week,
                "slots": [asdict(s) for s in r.slots],
            }
            for fid, r in snapshot.rosters.items()
        },
        "standings": {fid: asdict(s) for fid, s in snapshot.standings.items()},
    }


def save_snapshot(
    snapshot: LeagueSnapshot,
    path: Path = DEFAULT_SNAPSHOT_PATH,
) -> Path:
    """Serialize snapshot to JSON and write to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _snapshot_to_dict(snapshot)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    size_kb = path.stat().st_size // 1024
    logger.info("Snapshot saved to %s (%d KB)", path, size_kb)
    return path


def load_snapshot(path: Path = DEFAULT_SNAPSHOT_PATH) -> LeagueSnapshot:
    """Load a snapshot from disk and reconstruct domain models."""
    if not path.exists():
        raise FileNotFoundError(
            f"No snapshot found at {path}. Run build_snapshot() first."
        )

    with open(path) as f:
        data = json.load(f)

    meta = data["meta"]

    franchises = []
    for f in data["franchises"]:
        picks = [FuturePick(**p) for p in f.pop("future_picks", [])]
        franchises.append(Franchise(**f, future_picks=picks))

    players = {
        pid: Player(**p) for pid, p in data["players"].items()
    }

    rosters = {}
    for fid, r in data["rosters"].items():
        slots = [RosterSlot(**s) for s in r["slots"]]
        rosters[fid] = Roster(
            franchise_id=r["franchise_id"],
            week=r["week"],
            slots=slots,
        )

    standings = {
        fid: Standing(**s) for fid, s in data["standings"].items()
    }

    return LeagueSnapshot(
        season=meta["season"],
        week=meta["week"],
        league_name=meta["league_name"],
        league_id=meta["league_id"],
        roster_size=meta["roster_size"],
        last_regular_season_week=meta["last_regular_season_week"],
        franchises=franchises,
        players=players,
        rosters=rosters,
        standings=standings,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    snapshot = build_snapshot()
    path = save_snapshot(snapshot)
    print(f"\nSnapshot saved to {path}")
    print(f"League: {snapshot.league_name}")
    print(f"Season: {snapshot.season}  Week: {snapshot.week}")
    print(f"Franchises: {len(snapshot.franchises)}")
    print(f"Players: {len(snapshot.players)}")
    print(f"Rosters: {len(snapshot.rosters)}")

    # Spot check — print one roster
    fmap = snapshot.franchise_map
    for fid, roster in list(snapshot.rosters.items())[:1]:
        fname = fmap[fid].name if fid in fmap else fid
        skill = snapshot.get_skill_players(fid)
        avg_age = snapshot.average_age(fid)
        print(f"\nSpot check — {fname}:")
        print(f"  Total roster slots: {len(roster.slots)}")
        print(f"  Skill players: {len(skill)}")
        print(f"  Avg skill age: {avg_age}")
        print(f"  Sample players:")
        for p in skill[:6]:
            print(f"    {p.display}")


if __name__ == "__main__":
    main()
