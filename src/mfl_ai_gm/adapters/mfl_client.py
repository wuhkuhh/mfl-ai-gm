"""
Layer 5 — Adapters
MFL API client. All external HTTP I/O lives here.
No business logic. No FastAPI. Returns raw dicts only.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from dotenv import load_dotenv
import os

load_dotenv()

logger = logging.getLogger(__name__)

MFL_BASE_URL = os.getenv("MFL_BASE_URL", "https://api.myfantasyleague.com")
MFL_API_KEY = os.getenv("MFL_API_KEY", "")
MFL_LEAGUE_ID = os.getenv("MFL_LEAGUE_ID", "25903")
MFL_SEASON = os.getenv("MFL_SEASON", "2026")


class MFLClientError(Exception):
    """Raised when the MFL API returns an unexpected response."""


class MFLClient:
    """
    Thin HTTP client for the MFL export API.

    - Always uses JSON=1
    - Always follows redirects (MFL uses 302 to per-server routing)
    - Stateless: each method builds its own params dict
    - Raises MFLClientError on non-200 or empty body
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

    def _url(self) -> str:
        return f"{self.base_url}/{self.season}/export"

    def _base_params(self) -> dict[str, str]:
        return {
            "L": self.league_id,
            "JSON": "1",
            "APIKEY": self.api_key,
        }

    def _get(self, export_type: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
        params = self._base_params()
        params["TYPE"] = export_type
        if extra:
            params.update(extra)

        logger.debug("MFL GET TYPE=%s params=%s", export_type, {k: v for k, v in params.items() if k != "APIKEY"})

        try:
            # follow_redirects=True handles MFL's 302 to www##.myfantasyleague.com
            response = httpx.get(
                self._url(),
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
            return response.json()
        except Exception as exc:
            raise MFLClientError(
                f"MFL API returned non-JSON for TYPE={export_type}: {response.text[:200]}"
            ) from exc

    # -------------------------------------------------------------------------
    # Public methods — one per MFL export TYPE
    # -------------------------------------------------------------------------

    def get_league(self) -> dict[str, Any]:
        """TYPE=league — league settings and metadata."""
        return self._get("league")

    def get_rosters(self) -> dict[str, Any]:
        """TYPE=rosters — all franchise rosters with player IDs."""
        return self._get("rosters")

    def get_players(self, details: bool = True) -> dict[str, Any]:
        """TYPE=players — full player universe with name, position, team, age."""
        extra = {"DETAILS": "1"} if details else {}
        return self._get("players", extra)

    def get_standings(self) -> dict[str, Any]:
        """TYPE=standings — current win/loss/points standings."""
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
        """TYPE=futurepicks — future draft picks owned by each franchise."""
        return self._get("futurepicks")

    def get_free_agents(self, position: str = "QB|RB|WR|TE|DEF|K") -> dict[str, Any]:
        """TYPE=freeAgents — available free agents by position."""
        return self._get("freeAgents", {"POSITION": position})

    def get_injuries(self) -> dict[str, Any]:
        """TYPE=injuries — current NFL injury report."""
        return self._get("injuries")

    def get_franchises(self) -> dict[str, Any]:
        """TYPE=franchises — franchise names, owners, IDs."""
        return self._get("franchises")
