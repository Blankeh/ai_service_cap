"""
UDP broadcast camera-sync service.

Sends a small trigger packet to the broadcast address every
camera_sync_interval_seconds so all ESP32-CAMs on the LAN capture a frame
simultaneously and POST it to POST /api/v1/upload.

The Pi only sends — no inbound port is opened here.
"""
import asyncio
import json
import logging
import socket
import time
from typing import Optional

from ..core.config import settings

logger = logging.getLogger(__name__)


class CameraSyncService:
    def __init__(self) -> None:
        self._addr = (settings.camera_sync_broadcast_addr, settings.camera_sync_port)
        self._sock: Optional[socket.socket] = None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        return sock

    def _send_trigger(self) -> None:
        if self._sock is None:
            self._sock = self._open_socket()

        packet = json.dumps({"cmd": "capture", "ts": int(time.time())}).encode()
        try:
            self._sock.sendto(packet, self._addr)
            logger.info("[CameraSync] Trigger sent → %s:%d", *self._addr)
        except OSError as exc:
            logger.warning(
                "[CameraSync] sendto failed: %s — socket will be re-opened next interval", exc
            )
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ── Public ────────────────────────────────────────────────────────────────

    async def run_loop(self) -> None:
        """Send broadcast trigger every interval (runs in background)."""
        if not settings.camera_sync_enabled:
            logger.info("[CameraSync] Disabled (set CAMERA_SYNC_ENABLED=true to enable)")
            return

        logger.info(
            "[CameraSync] Broadcast loop started → %s:%d every %ds",
            settings.camera_sync_broadcast_addr,
            settings.camera_sync_port,
            settings.camera_sync_interval_seconds,
        )
        while True:
            self._send_trigger()
            await asyncio.sleep(settings.camera_sync_interval_seconds)
