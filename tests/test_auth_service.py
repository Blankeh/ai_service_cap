from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.auth_service import AuthService


@pytest.fixture()
def auth(monkeypatch) -> AuthService:
    from src.core.config import settings
    monkeypatch.setattr(settings, "cloudflare_api_url", "https://example.com")
    monkeypatch.setattr(settings, "auth_login_path", "/api/v1/auth/login")
    monkeypatch.setattr(settings, "device_username", "device")
    monkeypatch.setattr(settings, "device_password", "secret")
    return AuthService()


def _ok_response(json_body):
    resp = MagicMock(status_code=200, raise_for_status=lambda: None)
    resp.json = lambda: json_body
    return resp


class TestLogin:
    async def test_login_url_is_built_from_base_and_path(self, auth):
        assert auth._login_url == "https://example.com/api/v1/auth/login"

    @pytest.mark.parametrize(
        "body",
        [
            {"access_token": "tok123"},
            {"accessToken": "tok123"},
            {"token": "tok123"},
            {"data": {"access_token": "tok123"}},
        ],
    )
    async def test_login_extracts_token_across_shapes(self, auth, body):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _ok_response(body)
            token = await auth.login()
        assert token == "tok123"
        assert auth.token == "tok123"
        assert auth.auth_header() == {"Authorization": "Bearer tok123"}

    async def test_login_sends_credentials(self, auth):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _ok_response({"token": "t"})
            await auth.login()
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {"username": "device", "password": "secret"}

    async def test_login_network_error_returns_none(self, auth):
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("down")):
            token = await auth.login()
        assert token is None
        assert auth.token is None

    async def test_login_missing_token_returns_none(self, auth):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _ok_response({"unexpected": "shape"})
            token = await auth.login()
        assert token is None

    async def test_ensure_token_reuses_existing(self, auth):
        auth._token = "cached"
        with patch.object(auth, "refresh", new_callable=AsyncMock) as mock_refresh:
            token = await auth.ensure_token()
        assert token == "cached"
        mock_refresh.assert_not_awaited()


class TestLoginLoop:
    async def test_loop_retries_until_token_acquired(self, auth, monkeypatch):
        monkeypatch.setattr(auth, "_retry_interval", 0)
        results = [None, None, "tok"]

        async def fake_login():
            value = results.pop(0)
            auth._token = value
            return value

        with patch.object(auth, "login", side_effect=fake_login):
            await auth.run_login_loop()

        assert auth.token == "tok"
        assert results == []
