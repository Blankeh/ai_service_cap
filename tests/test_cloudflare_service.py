from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.cloudflare_service import CloudflareService

CAMERA_ID = "CAM-BUS34-ALL"
BUS_ID    = 34
TS        = "2024-01-01T00:00:00+03:00"

PAYLOAD = {
    "cameraId":       CAMERA_ID,
    "busId":          BUS_ID,
    "cameraStatus":   "ACTIVE",
    "busStatus":      "RUNNING",
    "timestamp":      TS,
    "passengerCount": 4,
}


@pytest.fixture()
def svc(monkeypatch) -> CloudflareService:
    # Force "real send" mode so failures exercise the error path, not dummy mode.
    from src.core.config import settings
    monkeypatch.setattr(settings, "cloudflare_api_url", "https://example.com")
    monkeypatch.setattr(settings, "cloudflare_api_key", "real-key")
    return CloudflareService()


class TestSend:
    async def test_send_success_returns_true(self, svc):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            result = await svc.send(PAYLOAD)
        assert result is True

    async def test_send_network_error_returns_false(self, svc):
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("timeout")):
            result = await svc.send(PAYLOAD)
        assert result is False

    async def test_send_http_error_returns_false(self, svc):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            response = MagicMock(status_code=503)
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "503", request=MagicMock(), response=response
            )
            mock_post.return_value = response
            result = await svc.send(PAYLOAD)
        assert result is False

    async def test_invalid_enum_rejected_before_send(self, svc):
        bad = {**PAYLOAD, "busStatus": "IDLE"}  # not in the worker's accepted set
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            result = await svc.send(bad)
        assert result is False
        mock_post.assert_not_awaited()

    async def test_no_api_configured_is_dummy_success(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "cloudflare_api_url", "")
        monkeypatch.setattr(settings, "cloudflare_api_key", "")
        svc = CloudflareService()
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            result = await svc.send(PAYLOAD)
        assert result is True
        mock_post.assert_not_awaited()


def _make_auth_svc(token="tok"):
    from src.services.auth_service import AuthService
    auth = MagicMock(spec=AuthService)
    auth._token = token
    auth.ensure_token = AsyncMock(return_value=token)
    auth.refresh = AsyncMock(return_value="fresh-tok")
    auth.auth_header = lambda: (
        {"Authorization": f"Bearer {auth._token}"} if auth._token else {}
    )
    return auth


class TestAuthIntegration:
    @pytest.fixture()
    def svc_with_auth(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "cloudflare_api_url", "https://example.com")
        monkeypatch.setattr(settings, "cloudflare_api_key", "")
        self.auth = _make_auth_svc()
        return CloudflareService(auth_svc=self.auth)

    async def test_attaches_bearer_token_from_auth_service(self, svc_with_auth):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            result = await svc_with_auth.send(PAYLOAD)
        assert result is True
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer tok"

    async def test_relogs_in_and_retries_on_401(self, svc_with_auth):
        expired = MagicMock(status_code=401)
        ok      = MagicMock(status_code=200, raise_for_status=lambda: None)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [expired, ok]
            result = await svc_with_auth.send(PAYLOAD)
        assert result is True
        self.auth.refresh.assert_awaited_once()
        assert mock_post.await_count == 2

    async def test_gives_up_after_second_401(self, svc_with_auth):
        expired = MagicMock(status_code=401)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [expired, expired]
            result = await svc_with_auth.send(PAYLOAD)
        assert result is False
        assert mock_post.await_count == 2
