import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.cloudflare_service import CloudflareService
from src.repos.queue_repo import QueueRepo

CAMERA_ID = "CAM-BUS34-001"
BUS_ID    = 34
TS        = "2024-01-01T00:00:00+03:00"

PAYLOAD = {
    "cameraId":       CAMERA_ID,
    "busId":          BUS_ID,
    "route":          "34A-Taksim",
    "cameraStatus":   "ACTIVE",
    "busStatus":      "RUNNING",
    "timestamp":      TS,
    "passengerCount": 4,
    "driverName":     "Mehmet Yilmaz",
}


@pytest.fixture()
def repo() -> QueueRepo:
    return QueueRepo()


@pytest.fixture()
def svc(repo) -> CloudflareService:
    return CloudflareService(queue_repo=repo)


class TestSend:
    async def test_send_success_returns_true(self, svc):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            result = await svc.send(PAYLOAD)
        assert result is True

    async def test_send_network_error_returns_false_and_queues(self, svc, repo):
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("timeout")):
            result = await svc.send(PAYLOAD)
        assert result is False
        assert repo.count() == 1

    async def test_send_http_error_queues(self, svc, repo):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            response = MagicMock(status_code=503)
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "503", request=MagicMock(), response=response
            )
            mock_post.return_value = response
            result = await svc.send(PAYLOAD)
        assert result is False
        assert repo.count() == 1

    async def test_queued_payload_preserved(self, svc, repo):
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("down")):
            await svc.send(PAYLOAD)
        record = repo.get_due()[0]
        saved = json.loads(record.payload)
        assert saved["cameraId"]       == CAMERA_ID
        assert saved["passengerCount"] == 4
        assert saved["driverName"]     == "Mehmet Yilmaz"

    async def test_group_id_stored_uses_camera_id_and_timestamp(self, svc, repo):
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("down")):
            await svc.send(PAYLOAD)
        record = repo.get_due()[0]
        assert CAMERA_ID in record.group_id
        assert TS in record.group_id


class TestFlushQueue:
    async def test_flush_sends_queued_records(self, svc, repo):
        repo.enqueue(str(BUS_ID), f"{CAMERA_ID}_{TS}", PAYLOAD)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            await svc.flush_queue()
        assert repo.count() == 0

    async def test_flush_increments_retry_on_failure(self, svc, repo):
        repo.enqueue(str(BUS_ID), f"{CAMERA_ID}_{TS}", PAYLOAD)
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("down")):
            await svc.flush_queue()
        assert repo.count() == 1

    async def test_flush_drops_after_max_retries(self, svc, repo):
        from src.core.config import settings
        repo.enqueue(str(BUS_ID), f"{CAMERA_ID}_{TS}", PAYLOAD)
        record = repo.get_due()[0]
        past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
        for _ in range(settings.max_retry_attempts):
            repo.increment_retry(record.id, past)
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("down")):
            await svc.flush_queue()
        assert repo.count() == 0

    async def test_flush_empty_queue_does_nothing(self, svc):
        await svc.flush_queue()  # should not raise
