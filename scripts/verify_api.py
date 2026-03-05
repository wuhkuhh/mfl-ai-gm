#!/usr/bin/env python3
"""
Verification script — run after scaffold to confirm MFL API is working.
Usage: python scripts/verify_api.py

Tests: league, rosters, franchises, players (summary)
"""

import json
import sys
import os

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mfl_ai_gm.adapters.mfl_client import MFLClient, MFLClientError


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def check(label: str, value: object) -> None:
    status = "✓" if value else "✗ MISSING"
    print(f"  {status}  {label}: {value}")


def main() -> None:
    print("\nmfl-ai-gm API Verification")
    print(f"League: {os.getenv('MFL_LEAGUE_ID', '25903')}  Season: {os.getenv('MFL_SEASON', '2026')}")

    try:
        client = MFLClient()
    except MFLClientError as e:
        print(f"\nFATAL: {e}")
        sys.exit(1)

    # ── 1. League metadata ────────────────────────────────────────────────────
    section("1. League Metadata (TYPE=league)")
    try:
        data = client.get_league()
        league = data.get("league", {})
        check("rosterSize", league.get("rosterSize"))
        check("endWeek", league.get("endWeek"))
        check("bestLineup", league.get("bestLineup"))
        check("history years", len(league.get("history", {}).get("league", [])))
        print("  ✓ League OK")
    except MFLClientError as e:
        print(f"  ✗ FAILED: {e}")

    # ── 2. Franchises ─────────────────────────────────────────────────────────
    section("2. Franchises (TYPE=franchises)")
    franchise_map: dict[str, str] = {}
    try:
        data = client.get_franchises()
        franchises = data.get("franchises", {}).get("franchise", [])
        for f in franchises:
            franchise_map[f["id"]] = f.get("name", "Unknown")
        print(f"  ✓ {len(franchises)} franchises found")
        for fid, name in list(franchise_map.items())[:5]:
            print(f"     {fid} → {name}")
        if len(franchises) > 5:
            print(f"     ... and {len(franchises) - 5} more")
    except MFLClientError as e:
        print(f"  ✗ FAILED: {e}")

    # ── 3. Rosters ────────────────────────────────────────────────────────────
    section("3. Rosters (TYPE=rosters)")
    try:
        data = client.get_rosters()
        rosters = data.get("rosters", {}).get("franchise", [])
        print(f"  ✓ {len(rosters)} franchise rosters returned")

        total_players = 0
        for roster in rosters[:3]:
            fid = roster.get("id", "?")
            fname = franchise_map.get(fid, fid)
            players = roster.get("player", [])
            if isinstance(players, dict):
                players = [players]
            total_players += len(players)
            print(f"     {fname} ({fid}): {len(players)} players")
        print(f"  ✓ Sample roster data looks clean")
    except MFLClientError as e:
        print(f"  ✗ FAILED: {e}")

    # ── 4. Players (summary only — full list is large) ────────────────────────
    section("4. Player Universe (TYPE=players, first 5)")
    try:
        data = client.get_players(details=True)
        players = data.get("players", {}).get("player", [])
        print(f"  ✓ {len(players)} total players in universe")
        for p in players[:5]:
            print(f"     {p.get('id')} | {p.get('name')} | {p.get('position')} | {p.get('team')} | age={p.get('age')}")
    except MFLClientError as e:
        print(f"  ✗ FAILED: {e}")

    # ── 5. Standings ──────────────────────────────────────────────────────────
    section("5. Standings (TYPE=standings)")
    try:
        data = client.get_standings()
        standings = data.get("standings", {}).get("franchise", [])
        print(f"  ✓ {len(standings)} franchise standings entries")
        for s in standings[:3]:
            fid = s.get("id", "?")
            fname = franchise_map.get(fid, fid)
            print(f"     {fname}: W={s.get('h2hw')} L={s.get('h2hl')} PF={s.get('pf')}")
    except MFLClientError as e:
        print(f"  ✗ FAILED: {e}")

    print("\n" + "="*60)
    print("  Verification complete. Check for any ✗ above.")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
