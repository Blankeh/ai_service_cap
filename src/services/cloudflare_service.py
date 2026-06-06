import json
import logging
from datetime import datetime, timedelta

import httpx

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
        """
        Send a camera snapshot to Cloudflare.

        Expected payload:
        {
            "cameraId":       "CAM-BUS34-001",
            "busId":          1,
            "route":          "34A-Taksim",
            "cameraStatus":   "ACTIVE",
            "busStatus":      "RUNNING",
            "timestamp":      "2024-06-03T14:30:00+03:00",
            "passengerCount": 23,
            "driverName":     "Mehmet Yilmaz"  // optional
        }

        On failure enqueues for retry. Returns True if sent.
        """
        camera_id       = payload.get("cameraId", "?")
        bus_id_str      = str(payload.get("busId", "?"))
        group_id        = f"{camera_id}_{payload.get('timestamp', '')}"
        passenger_count = payload.get("passengerCount", "?")

        try:
            async with httpx.AsyncClient(timeout=settings.cloudflare_timeout) as client:
                response = await client.post(
                    self._upload_url,
                    headers=self.headers,
                    json=payload,
                )
                response.raise_for_status()
                logger.info("Sent: %s  passengers=%s", camera_id, passenger_count)
                return True

        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning(
                "Send failed for %s (passengers=%s): %s — queuing for retry",
                camera_id, passenger_count, exc,
                exc_info=True,
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
                        json=json.loads(record.payload),
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
