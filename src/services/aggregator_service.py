import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..core.config import settings

logger = logging.getLogger(__name__)

# Turkey is UTC+3 with no DST (since 2016)
_TURKEY_TZ = timezone(timedelta(hours=3))

# Reported for every record; this Pi has no live bus-status source.
_DEFAULT_BUS_STATUS = "RUNNING"


def _build_bus_camera_id(bus_id: Optional[int]) -> str:
    """
    Bus-level cameraId for the combined per-bus record.

    The grouper sums every synced camera's ROI count into a single value per bus,
    so the reported cameraId is bus-level (pos="ALL"), e.g. "CAM-BUS34-ALL".
    """
    bus_val = bus_id if bus_id is not None else "?"
    return settings.camera_id_template.format(bus=bus_val, pos="ALL")


@dataclass
class _BusBuffer:
    bus_key: str
    counts:           list[int] = field(default_factory=list)
    latest_timestamp: str       = ""
    latest_status:    str       = "ACTIVE"

    def push(self, count: int, timestamp: str, camera_status: str) -> None:
        self.counts.append(count)
        self.latest_timestamp = timestamp
        self.latest_status    = camera_status

    def latest_count(self) -> int:
        return self.counts[-1] if self.counts else 0

    def clear(self) -> None:
        self.counts.clear()
        self.latest_timestamp = ""
        self.latest_status    = "ACTIVE"


class AggregatorService:
    """
    Buffers the combined per-bus passenger count and flushes to Cloudflare every
    flush_interval seconds.

    The grouper already sums each synced camera's ROI count into one value per bus
    per NTP bucket, so this service buffers those per-bus samples and sends the
    latest one each flush.

    Spike detection: if the latest count differs from the last sent count by more
    than spike_threshold, that bus is flushed immediately without waiting for the
    next scheduled flush.

    On send failure the snapshot is dropped (no retry queue) — the next flush
    sends the latest count, which supersedes it anyway.
    """

    def __init__(
        self,
        cloudflare_svc,
        flush_interval:  int = 60,
        spike_threshold: int = 5,
    ) -> None:
        self.flush_interval  = flush_interval
        self.spike_threshold = spike_threshold
        self._cloudflare_svc = cloudflare_svc
        self._buffers:   dict[str, _BusBuffer] = {}
        self._last_sent: dict[str, int]        = {}  # bus_key → last passengerCount sent

    async def push(
        self,
        bus_id:        object,
        count:         int,
        timestamp:     str,
        camera_status: str = "ACTIVE",
    ) -> None:
        bus_key = str(bus_id)
        if bus_key not in self._buffers:
            self._buffers[bus_key] = _BusBuffer(bus_key=bus_key)

        self._buffers[bus_key].push(count, timestamp, camera_status)

        last = self._last_sent.get(bus_key)
        if last is not None and abs(count - last) >= self.spike_threshold:
            logger.warning(
                "[Aggregator] Spike detected on bus=%s: %d → %d (Δ%+d) — flushing immediately",
                bus_key, last, count, count - last,
            )
            await self._flush_bus(bus_key)

    async def _flush_bus(self, bus_key: str) -> None:
        buf = self._buffers.get(bus_key)
        if not buf or not buf.counts:
            return

        count = buf.latest_count()

        try:
            bus_id_num: Optional[int] = int(bus_key) or None
        except ValueError:
            bus_id_num = None

        camera_id       = _build_bus_camera_id(bus_id_num)
        flush_time      = datetime.now(_TURKEY_TZ).isoformat()
        passenger_count = max(0, min(500, count))

        payload: dict = {
            "cameraId":       camera_id,
            "busId":          bus_id_num,
            "cameraStatus":   buf.latest_status,
            "busStatus":      _DEFAULT_BUS_STATUS,
            "timestamp":      flush_time,
            "passengerCount": passenger_count,
        }

        await self._cloudflare_svc.send(payload)
        self._last_sent[bus_key] = count
        buf.clear()

    async def flush(self) -> None:
        """Flush all buses — called by the scheduled flush loop."""
        if not self._buffers:
            return

        for bus_key, buf in list(self._buffers.items()):
            if buf.counts:
                logger.info(
                    "[Aggregator] Scheduled flush: bus=%s count=%d (latest of %d samples)",
                    bus_key, buf.latest_count(), len(buf.counts),
                )
                await self._flush_bus(bus_key)

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
