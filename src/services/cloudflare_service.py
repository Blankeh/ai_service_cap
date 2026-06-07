import logging
from datetime import datetime, timedelta

import httpx
from pydantic import ValidationError

from ..configs.schemas import OccupancyPayload
from ..core.config import settings
from ..repos.queue_repo import QueueRepo

logger = logging.getLogger(__name__)


class CloudflareService:
    def __init__(self, queue_repo: QueueRepo) -> None:
        self.queue_repo  = queue_repo
        self._upload_url = settings.cloudflare_api_url.rstrip("/") + "/occupancy"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.cloudflare_api_key}",
        }

    async def send(self, payload: dict) -> bool:
        try:
            validated = OccupancyPayload(**payload)
        except ValidationError as exc:
            logger.error("[Cloudflare] Invalid payload schema: %s", exc)
            return False

        camera_id       = validated.cameraId
        bus_id_str      = str(validated.busId)
        group_id        = f"{camera_id}_{validated.timestamp}"
        passenger_count = validated.passengerCount

        logger.info("[Cloudflare] Payload → %s", validated.model_dump_json())

        if not settings.cloudflare_api_url or not settings.cloudflare_api_key or settings.cloudflare_api_key == "your_api_key_here":
            logger.info("[Cloudflare] No API configured — dummy mode, payload logged above")
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
            logger.warning(
                "[Cloudflare] Send failed for %s (passengers=%s): %s — queuing for retry",
                camera_id, passenger_count, exc,
            )
            self.queue_repo.enqueue(bus_id_str, group_id, payload)
            return False

    async def flush_queue(self) -> None:
        """Retry queued records with exponential backoff."""
        due = self.queue_repo.get_due()
        if not due:
            return

        logger.info("Retrying %d queued record(s)", len(due))
        for record in due:
            if record.retry_count >= settings.max_retry_attempts:
                logger.error("Dropping %s after %d attempts", record.group_id, record.retry_count)
                self.queue_repo.delete(record.id)
                continue

            try:
                async with httpx.AsyncClient(timeout=settings.cloudflare_timeout) as client:
                    response = await client.post(
                        self._upload_url,
                        headers=self.headers,
                        content=record.payload.encode(),
                    )
                    response.raise_for_status()
                    logger.info("Retry success: %s", record.group_id)
                    self.queue_repo.delete(record.id)

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                delay      = min(30 * (2 ** record.retry_count), 3600)
                next_retry = (datetime.utcnow() + timedelta(seconds=delay)).isoformat()
                self.queue_repo.increment_retry(record.id, next_retry)
                logger.warning(
                    "Retry %d failed for %s, next in %ds: %s",
                    record.retry_count + 1, record.group_id, delay, exc,
                    exc_info=True,
                )
