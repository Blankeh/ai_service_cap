"""
Polls the Cloudflare Worker for a new model version and hot-reloads when found.

The Worker exposes:
    GET  /api/model/metadata  → { version, sha256, size_bytes, updated_at }
    GET  /api/model/download  → binary model file

The Pi4 initiates all connections outbound — no inbound port required.
"""

import hashlib
import logging
from pathlib import Path

import httpx

from ..core.config import settings
from .inference_service import InferenceService

logger = logging.getLogger(__name__)


class ModelUpdateService:
    def __init__(self, inference_svc: InferenceService) -> None:
        self._inference_svc = inference_svc
        self._current_sha256 = ""
        self._model_dir = Path(settings.model_dir)
        self._base_url = settings.cloudflare_api_url.rstrip("/").rsplit("/api/", 1)[0]
        self._headers = {"Authorization": f"Bearer {settings.cloudflare_api_key}"}

    async def check_and_update(self) -> bool:
        """Check for a new model; download and reload if one is available. Returns True if updated."""
        if not settings.cloudflare_api_url or not settings.cloudflare_api_key:
            return False

        try:
            metadata = await self._fetch_metadata()
        except Exception as exc:
            logger.debug(f"Model metadata check failed: {exc}")
            return False

        remote_sha256 = metadata.get("sha256", "")
        if not remote_sha256 or remote_sha256 == self._current_sha256:
            return False

        version = metadata.get("version", "unknown")
        logger.info(f"New model version detected: {version}")

        try:
            model_path = await self._download_model(version)
        except Exception as exc:
            logger.error(f"Model download failed: {exc}")
            return False

        if self._sha256(model_path) != remote_sha256:
            logger.error("Downloaded model SHA-256 mismatch — discarding")
            model_path.unlink(missing_ok=True)
            return False

        try:
            self._inference_svc.reload(str(model_path))
        except Exception as exc:
            logger.error(f"Model reload failed: {exc}")
            model_path.unlink(missing_ok=True)
            return False

        self._current_sha256 = remote_sha256
        logger.info(f"Model updated to version {version}")
        return True

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _fetch_metadata(self) -> dict:
        url = self._base_url + "/api/model/metadata"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def _download_model(self, version: str) -> Path:
        url = self._base_url + "/api/model/download"
        self._model_dir.mkdir(parents=True, exist_ok=True)
        dest = self._model_dir / f"model_{version}.onnx"

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("GET", url, headers=self._headers) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        size_mb = dest.stat().st_size / 1_048_576
        logger.info(f"Downloaded {dest.name}  ({size_mb:.1f} MB)")
        return dest

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()
