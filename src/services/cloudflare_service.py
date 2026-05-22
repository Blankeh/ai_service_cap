import json
import logging
from datetime import datetime, timedelta

import httpx

from ..core.config import settings
from ..repos.queue_repo import QueueRepo

logger = logging.getLogger(__name__)


class CloudflareService:
    def __init__(self, queue_repo: QueueRepo):
        self.queue_repo = queue_repo
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.cloudflare_api_key}",
        }

    async def send(self, payload: dict) -> bool:
        """
        Send a crowd snapshot to Cloudflare.

        Expected payload:
        {
            "group_id":    "BUS-001_2026-05-14T20:44:00",
            "bus_id":      "BUS-001",
            "timestamp":   "...",
            "panes":       ["front", "rear"],
            "crowd_count": 7
        }

        On failure enqueues for retry. Returns True if sent.
        """
        bus_id   = payload["bus_id"]
        group_id = payload["group_id"]
        try:
            async with httpx.AsyncClient(timeout=settings.cloudflare_timeout) as client:
                response = await client.post(
                    settings.cloudflare_api_url,
                    headers=self.headers,
                    json=payload,
                )
                response.raise_for_status()
                logger.info(f"Sent: {group_id} crowd={payload['crowd_count']}")
                return True

        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning(f"Send failed ({exc}), queuing {group_id}")
            self.queue_repo.enqueue(bus_id, group_id, payload)
            return False

    async def flush_queue(self):
        """Retry queued records with exponential backoff."""
        due = self.queue_repo.get_due()
        if not due:
            return

        logger.info(f"Retrying {len(due)} queued record(s)")
        for record in due:
            if record.retry_count >= settings.max_retry_attempts:
                logger.error(f"Dropping {record.group_id} after {record.retry_count} attempts")
                self.queue_repo.delete(record.id)
                continue

            try:
                async with httpx.AsyncClient(timeout=settings.cloudflare_timeout) as client:
                    response = await client.post(
                        settings.cloudflare_api_url,
                        headers=self.headers,
                        json=json.loads(record.payload),
                    )
                    response.raise_for_status()
                    logger.info(f"Retry success: {record.group_id}")
                    self.queue_repo.delete(record.id)

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                delay      = min(30 * (2 ** record.retry_count), 3600)
                next_retry = (datetime.utcnow() + timedelta(seconds=delay)).isoformat()
                self.queue_repo.increment_retry(record.id, next_retry)
                logger.warning(f"Retry {record.retry_count + 1} failed for {record.group_id}, next in {delay}s")
