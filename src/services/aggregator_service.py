import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class _BusBuffer:
    bus_id: str
    counts: list[int]       = field(default_factory=list)
    panes:  list[list[str]] = field(default_factory=list)
    latest_timestamp: str   = ""

    def push(self, panes: list[str], count: int, timestamp: str):
        self.counts.append(count)
        self.panes.append(panes)
        self.latest_timestamp = timestamp

    def latest_count(self) -> int:
        return self.counts[-1] if self.counts else 0

    def clear(self):
        self.counts.clear()
        self.panes.clear()
        self.latest_timestamp = ""


class AggregatorService:
    """
    Buffers crowd counts per bus and flushes to Cloudflare every flush_interval seconds.

    Spike detection: if the latest count differs from the last sent count by more
    than spike_threshold, that bus is flushed immediately without waiting for the
    next scheduled flush.
    """

    def __init__(self, cloudflare_svc, flush_interval: int = 60, spike_threshold: int = 5):
        self.flush_interval  = flush_interval
        self.spike_threshold = spike_threshold
        self._cloudflare_svc = cloudflare_svc
        self._buffers:    dict[str, _BusBuffer] = {}
        self._last_sent:  dict[str, int]        = {}  # bus_id → last crowd_count sent

    async def push(self, bus_id: str, panes: list[str], count: int, timestamp: str):
        if bus_id not in self._buffers:
            self._buffers[bus_id] = _BusBuffer(bus_id=bus_id)

        self._buffers[bus_id].push(panes, count, timestamp)

        last = self._last_sent.get(bus_id)
        if last is not None and abs(count - last) >= self.spike_threshold:
            logger.warning(
                "[Aggregator] Spike detected on bus=%s: %d → %d (Δ%+d) — flushing immediately",
                bus_id, last, count, count - last,
            )
            await self._flush_bus(bus_id)

    async def _flush_bus(self, bus_id: str):
        buf = self._buffers.get(bus_id)
        if not buf or not buf.counts:
            return

        avg        = buf.latest_count()
        panes      = sorted({p for reading in buf.panes for p in reading})
        flush_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        payload = {
            "group_id":     f"{bus_id}_{flush_time}",
            "bus_id":       bus_id,
            "timestamp":    buf.latest_timestamp,
            "panes":        panes,
            "crowd_count":  avg,
            "sample_count": len(buf.counts),
        }

        await self._cloudflare_svc.send(payload)
        self._last_sent[bus_id] = avg
        buf.clear()

    async def flush(self):
        """Flush all buses — called by the 60-second loop."""
        if not self._buffers:
            return

        for bus_id, buf in list(self._buffers.items()):
            if buf.counts:
                avg = buf.latest_count()
                logger.info(
                    "[Aggregator] Scheduled flush: bus=%s crowd=%d (latest of %d samples)",
                    bus_id, avg, len(buf.counts),
                )
                await self._flush_bus(bus_id)

    async def run_loop(self):
        logger.info("[Aggregator] Flush loop started (interval=%ds, spike_threshold=%d)",
                    self.flush_interval, self.spike_threshold)
        while True:
            await asyncio.sleep(self.flush_interval)
            try:
                await self.flush()
            except Exception as exc:
                logger.error("[Aggregator] Flush error: %s", exc)
