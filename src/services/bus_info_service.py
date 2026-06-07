"""
Fetches and caches this bus's metadata from GET /api/v1/buses.

On startup (and every bus_info_refresh_seconds) the service calls the backend,
locates the Pi's own bus by matching settings.bus_id, and caches the result.
On failure it keeps the previous cache and logs a warning so the rest of the
pipeline can continue reporting with the last-known metadata.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from pydantic import ValidationError

from ..configs.schemas import BusEntry
from ..core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class BusInfo:
    bus_id: int
    route: str
    bus_status: str
    driver_name: Optional[str] = None


class BusInfoService:
    def __init__(self) -> None:
        self._cache: Optional[BusInfo] = None
        # Derive base URL from the Cloudflare worker URL (same host, strip /api/... suffix)
        self._base_url = settings.cloudflare_api_url.rstrip("/").rsplit("/api/", 1)[0]
        self._headers = {"Authorization": f"Bearer {settings.cloudflare_api_key}"}

    # ── Public ────────────────────────────────────────────────────────────────

    def current(self) -> Optional[BusInfo]:
        """Return the last successfully fetched bus info, or None if never fetched."""
        return self._cache

    async def fetch(self) -> bool:
        """
        Fetch bus list from the backend and cache this Pi's entry.

        Matches by comparing settings.bus_id (string) against the busId field
        (or 'id' as fallback) returned by each entry in the list.

        Returns True if the Pi's bus was found and cached.
        """
        if not settings.cloudflare_api_url or not settings.cloudflare_api_key:
            logger.warning("[BusInfo] No API URL/key configured — bus info unavailable")
            return False

        url = f"{self._base_url}/api/v1/buses"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, headers=self._headers)
                response.raise_for_status()
                buses = response.json()
        except Exception as exc:
            logger.warning("[BusInfo] Fetch failed (%s) — keeping cached data", exc)
            return False

        for raw in buses:
            if str(raw.get("busId", raw.get("id", ""))).strip() != str(settings.bus_id):
                continue
            try:
                entry = BusEntry(**raw)
            except ValidationError as exc:
                logger.warning("[BusInfo] Schema mismatch for bus entry: %s", exc)
                return False
            self._cache = BusInfo(
                bus_id=entry.busId,
                route=entry.route or "",
                bus_status=entry.busStatus,
                driver_name=entry.driverName,
            )
            logger.info(
                "[BusInfo] Cached: busId=%d  route=%s  busStatus=%s  driverName=%s",
                self._cache.bus_id, self._cache.route,
                self._cache.bus_status, self._cache.driver_name,
            )
            return True

        logger.warning(
            "[BusInfo] Bus id=%s not found in response (%d entries)",
            own_id, len(buses) if isinstance(buses, list) else "?",
        )
        return False

    async def run_loop(self) -> None:
        """Periodically refresh bus metadata (runs in background)."""
        logger.info(
            "[BusInfo] Refresh loop started (interval=%ds)", settings.bus_info_refresh_seconds
        )
        while True:
            await asyncio.sleep(settings.bus_info_refresh_seconds)
            try:
                await self.fetch()
            except Exception as exc:
                logger.error("[BusInfo] Refresh error: %s", exc)
