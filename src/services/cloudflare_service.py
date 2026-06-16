import logging
from typing import Optional

import httpx
from pydantic import ValidationError

from ..configs.schemas import OccupancyPayload
from ..core.config import settings
from .auth_service import AuthService

logger = logging.getLogger(__name__)


class CloudflareService:
    def __init__(self, auth_svc: Optional[AuthService] = None) -> None:
        self._upload_url = settings.cloudflare_api_url.rstrip("/") + "/api/v1/device/input"
        self._auth_svc = auth_svc
        self.headers = {"Content-Type": "application/json"}
        # Static-key fallback — only used when no AuthService manages a login token.
        # The token from AuthService (when present) takes precedence per request.
        if (
            auth_svc is None
            and settings.cloudflare_api_key
            and settings.cloudflare_api_key != "your_api_key_here"
        ):
            self.headers["Authorization"] = f"Bearer {settings.cloudflare_api_key}"

    def _request_headers(self) -> dict:
        headers = dict(self.headers)
        if self._auth_svc is not None:
            headers.update(self._auth_svc.auth_header())
        return headers

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

        body = validated.model_dump()

        # No auth manager: single attempt with whatever static headers we have.
        if self._auth_svc is None:
            ok, _ = await self._post_once(body, camera_id, passenger_count)
            return ok

        # Auth manager present: make sure we hold a token, then retry once on a
        # 401 (expired/invalid token) after re-logging in.
        await self._auth_svc.ensure_token()
        for attempt in (1, 2):
            ok, expired = await self._post_once(body, camera_id, passenger_count)
            if ok:
                return True
            if expired and attempt == 1:
                logger.info("[Cloudflare] Token expired — re-logging in and retrying %s", camera_id)
                await self._auth_svc.refresh()
                continue
            return False
        return False

    async def _post_once(
        self, body: dict, camera_id: str, passenger_count: int
    ) -> tuple[bool, bool]:
        """
        Single POST attempt. Returns (success, token_expired).

        token_expired is True only on a 401, signalling the caller to re-login and
        retry. All other failures return (False, False) and are dropped — the next
        scheduled flush sends the latest count, which supersedes whatever failed.
        """
        try:
            async with httpx.AsyncClient(timeout=settings.cloudflare_timeout) as client:
                response = await client.post(
                    self._upload_url,
                    headers=self._request_headers(),
                    json=body,
                )
                if response.status_code == 401:
                    logger.warning(
                        "[Cloudflare] 401 Unauthorized for %s — token expired/invalid",
                        camera_id,
                    )
                    return False, True
                response.raise_for_status()
                logger.info("[Cloudflare] Sent OK: %s  passengers=%s", camera_id, passenger_count)
                return True, False

        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning(
                "[Cloudflare] Send failed for %s (passengers=%s): %s — dropping; "
                "latest count will be sent on next flush",
                camera_id, passenger_count, exc,
            )
            return False, False
