import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from .bus_info_service import BusInfoService
from ..core.config import settings

logger = logging.getLogger(__name__)

# Turkey is UTC+3 with no DST (since 2016)
_TURKEY_TZ = timezone(timedelta(hours=3))

# Maps the position component of a device_id to a 3-digit suffix for cameraId
_POSITION_SUFFIX: dict[str, str] = {
    "front":  "001",
    "mid":    "002",
    "middle": "002",
    "rear":   "003",
    "back":   "003",
}


def _build_camera_id(device_id: str, bus_id: Optional[int]) -> str:
    """
    Construct a cameraId using settings.camera_id_template.

    device_id examples: "CAM-front", "front", "CAM-rear"
    Template placeholders: {bus} = numeric busId, {pos} = position suffix
    Default template "CAM-BUS{bus}-{pos}" → "CAM-BUS34-001"
    """
    pos_key = device_id.lower().replace("cam-", "").strip()
    suffix  = _POSITION_SUFFIX.get(pos_key, pos_key)
    bus_val = bus_id if bus_id is not None else "?"
    return settings.camera_id_template.format(bus=bus_val, pos=suffix)


@dataclass
class _CameraBuffer:
    camera_key: str
    counts:           list[int] = field(default_factory=list)
    statuses:         list[str] = field(default_factory=list)
    latest_timestamp: str       = ""
    latest_status:    str       = "ACTIVE"

    def push(self, count: int, timestamp: str, camera_status: str) -> None:
        self.counts.append(count)
        self.statuses.append(camera_status)
        self.latest_timestamp = timestamp
        self.latest_status    = camera_status

    def latest_count(self) -> int:
        return self.counts[-1] if self.counts else 0

    def clear(self) -> None:
        self.counts.clear()
        self.statuses.clear()
        self.latest_timestamp = ""
        self.latest_status    = "ACTIVE"


class AggregatorService:
    """
    Buffers passenger counts per camera and flushes to Cloudflare every flush_interval seconds.

    Spike detection: if the latest count differs from the last sent count by more
    than spike_threshold, that camera is flushed immediately without waiting for the
    next scheduled flush.
    """

    def __init__(
        self,
        cloudflare_svc,
        bus_info_svc: BusInfoService,
        flush_interval:  int = 60,
        spike_threshold: int = 5,
    ) -> None:
        self.flush_interval  = flush_interval
        self.spike_threshold = spike_threshold
        self._cloudflare_svc = cloudflare_svc
        self._bus_info_svc   = bus_info_svc
        self._buffers:   dict[str, _CameraBuffer] = {}
        self._last_sent: dict[str, int]           = {}  # camera_key → last passengerCount sent

    async def push(
        self,
        camera_key:    str,
        count:         int,
        timestamp:     str,
        camera_status: str = "ACTIVE",
    ) -> None:
        if camera_key not in self._buffers:
            self._buffers[camera_key] = _CameraBuffer(camera_key=camera_key)

        self._buffers[camera_key].push(count, timestamp, camera_status)

        last = self._last_sent.get(camera_key)
        if last is not None and abs(count - last) >= self.spike_threshold:
            logger.warning(
                "[Aggregator] Spike detected on camera=%s: %d → %d (Δ%+d) — flushing immediately",
                camera_key, last, count, count - last,
            )
            await self._flush_camera(camera_key)

    async def _flush_camera(self, camera_key: str) -> None:
        buf = self._buffers.get(camera_key)
        if not buf or not buf.counts:
            return

        count    = buf.latest_count()
        bus_info = self._bus_info_svc.current()

        bus_id_num      = bus_info.bus_id     if bus_info else None
        route           = bus_info.route      if bus_info else None
        bus_status      = bus_info.bus_status if bus_info else "RUNNING"

        camera_id       = _build_camera_id(camera_key, bus_id_num)
        flush_time      = datetime.now(_TURKEY_TZ).isoformat()
        passenger_count = max(0, min(500, count))

        payload: dict = {
            "cameraId":       camera_id,
            "busId":          bus_id_num,
            "route":          route,
            "cameraStatus":   buf.latest_status,
            "busStatus":      bus_status,
            "timestamp":      flush_time,
            "passengerCount": passenger_count,
        }
        if bus_info and bus_info.driver_name:
            payload["driverName"] = bus_info.driver_name

        await self._cloudflare_svc.send(payload)
        self._last_sent[camera_key] = count
        buf.clear()

    async def flush(self) -> None:
        """Flush all cameras — called by the 60-second scheduled loop."""
        if not self._buffers:
            return

        for camera_key, buf in list(self._buffers.items()):
            if buf.counts:
                logger.info(
                    "[Aggregator] Scheduled flush: camera=%s count=%d (latest of %d samples)",
                    camera_key, buf.latest_count(), len(buf.counts),
                )
                await self._flush_camera(camera_key)

    async def run_loop(self) -> None:
        logger.info(
            "[Aggregator] Flush loop started (interval=%ds, spike_threshold=%d)",
            self.flush_interval, self.spike_threshold,
        )
        while True:
            await asyncio.sleep(self.flush_interval)
            try:
                await self.flush()
            except Exception as exc:
                logger.error("[Aggregator] Flush error: %s", exc)
