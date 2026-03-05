#!/usr/bin/env python3
"""
Verification script — run after scaffold to confirm MFL API is working.
Usage: python scripts/verify_api.py

Tests: league, franchises, rosters, players, standings
All key mappings match confirmed MFL 2026 response shapes.
"""

import os
import sys

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mfl_ai_gm.adapters.mfl_client import MFLClient, MFLClientError


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def ok(label: str, value: object) -> None:
    print(f"  ✓  {label}: {value}")


def missing(label: str) -> None:
    print(f"  ✗  MISSING: {label}")


def main() -> None:
    print("\nmfl-ai-gm API Verification")
    print(f"League: {os.getenv('MFL_LEAGUE_ID', '25903')}  Season: {os.getenv('MFL_SEASON', '2026')}")

    try:
        client = MFLClient()
    except MFLClientError as e:
        print(f"\nFATAL: {e}")
        sys.exit(1)

    # ── 1. Host resolution ────────────────────────────────────────────────────
    section("1. Host Resolution (302 redirect)")
    try:
        host = client._resolve_host()
        ok("Resolved host", host)
    except MFLClientError as e:
        print(f"  ✗  FAILED: {e}")

    # ── 2. League metadata ────────────────────────────────────────────────────
    section("2. League Metadata (TYPE=league)")
    franchise_map: dict[str, str] = {}
    try:
        data = client.get_league()
        league = data.get("league", {})

        for field in ["name", "id", "rosterSize", "endWeek", "lastRegularSeasonWeek",
                      "taxiSquad", "bestLineup"]:
            val = league.get(field)
            if val is not None:
                ok(field, val)
            else:
                missing(field)

        # Franchises embedded in league response
        franchises = league.get("franchises", {}).get("franchise", [])
        if isinstance(franchises, dict):
            franchises = [franchises]
        ok("franchise count", len(franchises))

        for f in franchises:
            franchise_map[f["id"]] = f.get("name", "Unknown")

        print("\n  Franchise list:")
        for fid, fname in franchise_map.items():
            print(f"     {fid} → {fname}")

    except MFLClientError as e:
        print(f"  ✗  FAILED: {e}")

    # ── 3. Rosters ────────────────────────────────────────────────────────────
    section("3. Rosters (TYPE=rosters)")
    try:
        data = client.get_rosters()
        rosters = data.get("rosters", {}).get("franchise", [])
        ok("franchise roster count", len(rosters))

        for roster in rosters[:3]:
            fid = roster.get("id", "?")
            fname = franchise_map.get(fid, fid)
            players = roster.get("player", [])
            if isinstance(players, dict):
                players = [players]
            statuses = set(p.get("status") for p in players)
            print(f"     {fname} ({fid}): {len(players)} players | statuses: {statuses}")

    except MFLClientError as e:
        print(f"  ✗  FAILED: {e}")

    # ── 4. Players ────────────────────────────────────────────────────────────
    section("4. Player Universe (TYPE=players)")
    try:
        data = client.get_players(details=True)
        players = data.get("players", {}).get("player", [])
        ok("total players in universe", len(players))

        # Filter out team defenses for display
        real_players = [p for p in players if p.get("position") not in ("TMWR", "TMPK", "TMQB", "TMRB", "TMTE")]
        ok("individual players (excl. team units)", len(real_players))

        print("\n  Sample players:")
        for p in real_players[:8]:
            print(f"     {p.get('id')} | {p.get('name')} | {p.get('position')} | {p.get('team')} | age={p.get('age', 'N/A')}")

    except MFLClientError as e:
        print(f"  ✗  FAILED: {e}")

    # ── 5. Standings ──────────────────────────────────────────────────────────
    section("5. Standings (TYPE=standings)")
    try:
        data = client.get_standings()
        # Confirmed key: leagueStandings, not standings
        standings = data.get("leagueStandings", {}).get("franchise", [])
        ok("franchise standings count", len(standings))

        print("\n  Current standings:")
        for s in standings:
            fid = s.get("id", "?")
            fname = franchise_map.get(fid, fid)
            print(
                f"     {fname:<35} W={s.get('h2hw')} L={s.get('h2hl')} "
                f"PF={s.get('pf')} PA={s.get('pa')}"
            )

    except MFLClientError as e:
        print(f"  ✗  FAILED: {e}")

    # ── 6. Future picks (dynasty critical) ───────────────────────────────────
    section("6. Future Draft Picks (from league response)")
    try:
        franchises = client.get_franchises()
        picks_count = 0
        for f in franchises:
            raw = f.get("future_draft_picks", "")
            picks = [p for p in raw.split(",") if p.strip()]
            picks_count += len(picks)
            if picks:
                fname = f.get("name", f.get("id"))
                print(f"     {fname}: {len(picks)} picks → {picks[:3]}{'...' if len(picks) > 3 else ''}")
        ok("total future picks tracked", picks_count)

    except MFLClientError as e:
        print(f"  ✗  FAILED: {e}")

    print("\n" + "="*60)
    print("  Verification complete. All ✓ = ready to build snapshot layer.")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
