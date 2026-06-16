import asyncio
import logging
from typing import Optional

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)


class AuthService:
    """
    Holds the backend bearer access token used to authorize device uploads.

    The token is obtained by POSTing the device credentials to the login endpoint.
    At startup the login loop keeps retrying until a token is acquired (so a backend
    that is briefly unreachable doesn't permanently break auth). Expiry is handled
    on demand: when an upload comes back 401, CloudflareService calls refresh() to
    log in again and retries the request.
    """

    def __init__(self) -> None:
        base = settings.cloudflare_api_url.rstrip("/")
        self._login_url = base + "/" + settings.auth_login_path.lstrip("/")
        self._credentials = {
            "username": settings.device_username,
            "password": settings.device_password,
        }
        self._retry_interval = settings.auth_login_retry_interval
        self._token: Optional[str] = None
        # Serializes re-login so concurrent 401s don't stampede the endpoint.
        self._lock = asyncio.Lock()

    @property
    def token(self) -> Optional[str]:
        return self._token

    def auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def login(self) -> Optional[str]:
        """Single login attempt. Returns the token on success, None on failure."""
        try:
            async with httpx.AsyncClient(timeout=settings.cloudflare_timeout) as client:
                resp = await client.post(self._login_url, json=self._credentials)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
            logger.warning("[Auth] Login failed: %s", exc)
            return None

        token = self._extract_token(data)
        if not token:
            logger.error("[Auth] Login succeeded but no token found in response")
            return None

        self._token = token
        logger.info("[Auth] Obtained access token from %s", self._login_url)
        return token

    async def refresh(self) -> Optional[str]:
        """Re-login, serialized so concurrent callers don't hammer the endpoint."""
        async with self._lock:
            return await self.login()

    async def ensure_token(self) -> Optional[str]:
        """Return the current token, logging in first if we don't have one yet."""
        if self._token:
            return self._token
        return await self.refresh()

    async def run_login_loop(self) -> None:
        """
        Keep requesting the login endpoint until a token is acquired, then stop.

        Re-login on expiry is driven on demand by upload failures, so this loop only
        guarantees an initial token even if the backend is down at startup.
        """
        while self._token is None:
            await self.refresh()
            if self._token is None:
                logger.info("[Auth] Retrying login in %ds", self._retry_interval)
                await asyncio.sleep(self._retry_interval)
        logger.info("[Auth] Initial token acquired — login loop complete")

    @staticmethod
    def _extract_token(data) -> Optional[str]:
        """Pull the token out of the login response across common JSON shapes."""
        if isinstance(data, str):
            return data
        if not isinstance(data, dict):
            return None
        for key in ("access_token", "accessToken", "token"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        nested = data.get("data")
        if isinstance(nested, dict):
            return AuthService._extract_token(nested)
        return None
