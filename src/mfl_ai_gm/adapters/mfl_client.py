"""
Layer 5 — Adapters
MFL API client. All external HTTP I/O lives here.
No business logic. No FastAPI. Returns raw dicts only.

MFL API notes:
- api.myfantasyleague.com issues a 302 to a per-server host (e.g. www48.myfantasyleague.com)
- We resolve the target host once on init and use it directly for all subsequent calls
- This avoids the "Invalid request. This API request must go to api.myfantasyleague.com" error
  that occurs when some TYPE calls are made directly to the www## host
- Always use JSON=1 — never XML
- Franchise data lives inside TYPE=league, not a separate TYPE=franchises endpoint
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MFL_BASE_URL = os.getenv("MFL_BASE_URL", "https://api.myfantasyleague.com")
MFL_API_KEY = os.getenv("MFL_API_KEY", "")
MFL_LEAGUE_ID = os.getenv("MFL_LEAGUE_ID", "25903")
MFL_SEASON = os.getenv("MFL_SEASON", "2026")


class MFLClientError(Exception):
    """Raised when the MFL API returns an unexpected or error response."""


class MFLClient:
    """
    Thin HTTP client for the MFL export API.

    Resolves the per-server redirect host on first call, then routes all
    subsequent requests directly to that host to avoid redirect errors.
    """

    def __init__(
        self,
        api_key: str = MFL_API_KEY,
        league_id: str = MFL_LEAGUE_ID,
        season: str = MFL_SEASON,
        base_url: str = MFL_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise MFLClientError("MFL_API_KEY is not set. Check your .env file.")
        self.api_key = api_key
        self.league_id = league_id
        self.season = season
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._resolved_host: str | None = None  # set on first _resolve_host() call

    # -------------------------------------------------------------------------
    # Host resolution — called once, result cached
    # -------------------------------------------------------------------------

    def _resolve_host(self) -> str:
        """
        Follow the MFL 302 redirect once to discover the per-server host
        (e.g. https://www48.myfantasyleague.com). Cache and reuse for all calls.
        """
        if self._resolved_host:
            return self._resolved_host

        probe_url = f"{self.base_url}/{self.season}/export"
        params = {"TYPE": "league", "L": self.league_id, "JSON": "1"}

        try:
            # Do NOT follow redirects — we want the Location header
            response = httpx.get(
                probe_url, params=params, follow_redirects=False, timeout=self.timeout
            )
        except httpx.RequestError as exc:
            raise MFLClientError(f"Network error resolving MFL host: {exc}") from exc

        if response.status_code == 302:
            location = response.headers.get("location", "")
            if location:
                parsed = httpx.URL(location)
                self._resolved_host = f"{parsed.scheme}://{parsed.host}"
                logger.info("MFL resolved host: %s", self._resolved_host)
                return self._resolved_host

        # No redirect — use base URL directly
        self._resolved_host = self.base_url
        logger.info("MFL no redirect — using base URL: %s", self._resolved_host)
        return self._resolved_host

    def _export_url(self) -> str:
        host = self._resolve_host()
        return f"{host}/{self.season}/export"

    def _base_params(self) -> dict[str, str]:
        return {
            "L": self.league_id,
            "JSON": "1",
            "APIKEY": self.api_key,
        }

    def _get_public(self, export_type: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
        """Same as _get() but without APIKEY — for endpoints that reject auth."""
        params = {"L": self.league_id, "JSON": "1", "TYPE": export_type}
        if extra:
            params.update(extra)
        try:
            response = httpx.get(
                self._export_url(), params=params, follow_redirects=True, timeout=self.timeout
            )
        except httpx.RequestError as exc:
            raise MFLClientError(f"Network error calling MFL API: {exc}") from exc
        if response.status_code != 200:
            raise MFLClientError(f"MFL API returned HTTP {response.status_code} for TYPE={export_type}")
        if not response.text.strip():
            raise MFLClientError(f"MFL API returned empty body for TYPE={export_type}")
        try:
            data = response.json()
        except Exception as exc:
            raise MFLClientError(f"MFL API returned non-JSON for TYPE={export_type}: {response.text[:200]}") from exc
        if "error" in data:
            msg = data["error"].get("$t", "Unknown MFL error")
            raise MFLClientError(f"MFL API error for TYPE={export_type}: {msg}")
        return data

    def _get(self, export_type: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
        params = self._base_params()
        params["TYPE"] = export_type
        if extra:
            params.update(extra)

        logger.debug("MFL GET TYPE=%s host=%s", export_type, self._resolved_host)

        try:
            response = httpx.get(
                self._export_url(),
                params=params,
                follow_redirects=True,
                timeout=self.timeout,
            )
        except httpx.RequestError as exc:
            raise MFLClientError(f"Network error calling MFL API: {exc}") from exc

        if response.status_code != 200:
            raise MFLClientError(
                f"MFL API returned HTTP {response.status_code} for TYPE={export_type}"
            )

        if not response.text.strip():
            raise MFLClientError(
                f"MFL API returned empty body for TYPE={export_type}. "
                "Check APIKEY, league ID, and season."
            )

        try:
            data = response.json()
        except Exception as exc:
            raise MFLClientError(
                f"MFL API returned non-JSON for TYPE={export_type}: {response.text[:200]}"
            ) from exc

        # Check for MFL-level error responses
        if "error" in data:
            msg = data["error"].get("$t", "Unknown MFL error")
            raise MFLClientError(f"MFL API error for TYPE={export_type}: {msg}")

        return data

    # -------------------------------------------------------------------------
    # Public methods — one per MFL export TYPE
    # -------------------------------------------------------------------------

    def get_league(self) -> dict[str, Any]:
        """
        TYPE=league — league settings, metadata, and all franchise info.

        Note: MFL rejects APIKEY on this endpoint — must be called unauthenticated.
        The league endpoint is public and does not require auth.

        Response shape:
            data["league"]                                    → league settings dict
            data["league"]["franchises"]["franchise"]         → list of franchise dicts
            franchise keys: id, name, abbrev, owner_name, future_draft_picks, ...
        """
        return self._get_public("league")

    def get_franchises(self) -> list[dict[str, Any]]:
        """
        Returns the franchise list extracted from TYPE=league.
        Each franchise dict includes: id, name, abbrev, owner_name, future_draft_picks.

        Note: MFL does not have a reliable standalone TYPE=franchises endpoint.
        Franchise data is embedded in the league response.
        """
        data = self.get_league()
        franchises = data.get("league", {}).get("franchises", {}).get("franchise", [])
        if isinstance(franchises, dict):
            franchises = [franchises]
        return franchises

    def get_rosters(self) -> dict[str, Any]:
        """
        TYPE=rosters — all franchise rosters with player IDs and status.

        Response shape:
            data["rosters"]["franchise"]          → list of franchise roster dicts
            franchise keys: id, week, player      → list of {id, status}
        """
        return self._get("rosters")

    def get_players(self, details: bool = True) -> dict[str, Any]:
        """
        TYPE=players — full player universe.

        Response shape:
            data["players"]["player"]  → list of player dicts
            player keys: id, name, position, team, age (absent for team defenses)

        Note: Includes team defense entries (e.g. position TMWR). Filter in snapshot layer.
        """
        extra = {"DETAILS": "1"} if details else {}
        return self._get("players", extra)

    def get_standings(self) -> dict[str, Any]:
        """
        TYPE=standings — current win/loss/points standings.

        Response shape:
            data["leagueStandings"]["franchise"]  → list of franchise standing dicts
            keys: id, h2hw, h2hl, h2ht, h2hwlt, pf, pa, pp, h2hpct, all_play_pct, strk
        """
        return self._get("standings")

    def get_schedule(self, week: str | None = None) -> dict[str, Any]:
        """TYPE=schedule — full season schedule or a specific week."""
        extra = {"W": week} if week else {}
        return self._get("schedule", extra)

    def get_player_scores(self, week: str, year: str | None = None) -> dict[str, Any]:
        """TYPE=playerScores — scoring for a given week."""
        extra: dict[str, str] = {"W": week, "YEAR": year or self.season, "PLAYERS": "ALL"}
        return self._get("playerScores", extra)

    def get_transactions(self, transaction_type: str = "ALL") -> dict[str, Any]:
        """TYPE=transactions — waiver, trade, add/drop history."""
        return self._get("transactions", {"TRANS_TYPE": transaction_type})

    def get_draft_results(self) -> dict[str, Any]:
        """TYPE=draftResults — completed draft picks."""
        return self._get("draftResults")

    def get_future_picks(self) -> dict[str, Any]:
        """
        TYPE=futurepicks — future draft picks owned by each franchise.
        Note: future_draft_picks strings also available on each franchise in get_league().
        """
        return self._get("futurepicks")

    def get_free_agents(self, position: str = "QB|RB|WR|TE|DEF|K") -> dict[str, Any]:
        """TYPE=freeAgents — available free agents by position."""
        return self._get("freeAgents", {"POSITION": position})

    def get_injuries(self) -> dict[str, Any]:
        """TYPE=injuries — current NFL injury report."""
        return self._get("injuries")
