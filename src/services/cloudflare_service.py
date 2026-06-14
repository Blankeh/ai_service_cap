import logging

import httpx
from pydantic import ValidationError

from ..configs.schemas import OccupancyPayload
from ..core.config import settings

logger = logging.getLogger(__name__)


class CloudflareService:
    def __init__(self) -> None:
        self._upload_url = settings.cloudflare_api_url.rstrip("/") + "/api/v1/device/input"
        self.headers = {"Content-Type": "application/json"}
        # Auth is optional — only sent when a key is configured. The backend at
        # /api/v1/device/input accepts a plain JSON POST with no API key.
        if settings.cloudflare_api_key and settings.cloudflare_api_key != "your_api_key_here":
            self.headers["Authorization"] = f"Bearer {settings.cloudflare_api_key}"

    async def send(self, payload: dict) -> bool:
        try:
            validated = OccupancyPayload(**payload)
        except ValidationError as exc:
            logger.error("[Cloudflare] Invalid payload schema: %s", exc)
            return False

        camera_id       = validated.cameraId
        passenger_count = validated.passengerCount

        logger.info("[Cloudflare] Payload → %s", validated.model_dump_json())

        if not settings.cloudflare_api_url:
            logger.info("[Cloudflare] No URL configured — dummy mode, payload logged above")
            return True

        try:
            async with httpx.AsyncClient(timeout=settings.cloudflare_timeout) as client:
                response = await client.post(
                    self._upload_url,
                    headers=self.headers,
                    json=validated.model_dump(),
                )
                response.raise_for_status()
                logger.info("[Cloudflare] Sent OK: %s  passengers=%s", camera_id, passenger_count)
                return True

        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            # Don't retry this stale snapshot — the next scheduled flush sends the
            # latest count, which supersedes whatever failed here.
            logger.warning(
                "[Cloudflare] Send failed for %s (passengers=%s): %s — dropping; "
                "latest count will be sent on next flush",
                camera_id, passenger_count, exc,
            )
            return False
