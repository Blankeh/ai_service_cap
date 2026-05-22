import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class _PaneBuffer:
    counts: list[int] = field(default_factory=list)

    def push(self, count: int):
        self.counts.append(count)

    def average(self) -> int:
        return round(sum(self.counts) / len(self.counts)) if self.counts else 0


@dataclass
class _BusBuffer:
    bus_id: str
    panes:  dict[str, _PaneBuffer] = field(default_factory=dict)
    latest_timestamp: str = ""

    def push(self, pane: str, count: int, timestamp: str):
        if pane not in self.panes:
            self.panes[pane] = _PaneBuffer()
        self.panes[pane].push(count)
        self.latest_timestamp = timestamp

    def summarise(self, group_id: str) -> dict:
        pane_counts = {pane: buf.average() for pane, buf in self.panes.items()}
        return {
            "group_id":    group_id,
            "bus_id":      self.bus_id,
            "timestamp":   self.latest_timestamp,
            "panes":       pane_counts,
            "total_crowd": sum(pane_counts.values()),
        }

    def clear(self):
        self.panes.clear()
        self.latest_timestamp = ""


class AggregatorService:
    """
    Buffers crowd counts per bus per pane.

    On each frame upload, push(bus_id, pane, count, timestamp) is called.

    Every flush_interval seconds the background loop calls flush(), which:
      - For each bus, averages per-pane counts across all frames received
      - Builds a grouped payload and forwards it to CloudflareService
      - Clears the buffer

    group_id format: "{bus_id}_{UTC bucket timestamp}"
    Bucket size = flush_interval (one group per flush window).
    """

    def __init__(self, flush_interval: int = 60):
        self.flush_interval = flush_interval
        self._buffers: dict[str, _BusBuffer] = {}  # keyed by bus_id
        self._cloudflare_svc = None

    def set_cloudflare_svc(self, svc):
        self._cloudflare_svc = svc

    def push(self, bus_id: str, pane: str, count: int, timestamp: str):
        """Called on every frame upload."""
        if bus_id not in self._buffers:
            self._buffers[bus_id] = _BusBuffer(bus_id=bus_id)
        self._buffers[bus_id].push(pane, count, timestamp)

    async def flush(self):
        if not self._buffers:
            return

        flush_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        for bus_id, buf in list(self._buffers.items()):
            if not buf.panes:
                continue

            group_id = f"{bus_id}_{flush_time}"
            summary  = buf.summarise(group_id)

            logger.info(
                f"[Aggregator] bus={bus_id} group={group_id} "
                f"panes={summary['panes']} total={summary['total_crowd']}"
            )

            await self._cloudflare_svc.send(summary)
            buf.clear()

    async def run_loop(self):
        while True:
            await asyncio.sleep(self.flush_interval)
            try:
                await self.flush()
            except Exception as exc:
                logger.error(f"[Aggregator] Flush error: {exc}")
