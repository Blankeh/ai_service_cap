"""
FrameGrouper — groups frames by NTP capture timestamp bucket, not arrival time.

Each ESP32-CAM sends X-Captured-At (Unix timestamp from NTP).
The server buckets it: bucket = floor(captured_at / bucket_size) * bucket_size

Example with bucket_size=2, capture_interval=2s:
  cam-front  captured_at=1000.050  → bucket=1000  ─┐ same group
  cam-rear   captured_at=1000.120  → bucket=1000  ─┘

  cam-rear   captured_at=1002.080  → bucket=1002  ← different group

Cameras are pre-configured and ship with their bus_id and pane already set.
The grouper collects all panes that arrive within group_window_ms, then fires.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import cv2

logger = logging.getLogger(__name__)


@dataclass
class _PendingFrame:
    raw_bytes:  bytes
    timestamp:  str
    captured_at: int


@dataclass
class _BusGroup:
    bus_id:     str
    bucket:     int
    frames:     dict[str, _PendingFrame] = field(default_factory=dict)  # pane → frame
    timer_task: Optional[asyncio.Task]   = field(default=None, compare=False)


class FrameGrouper:
    def __init__(
        self,
        image_svc,
        inference_svc,
        aggregator_svc,
        group_window_ms: int = 500,
        bucket_size:     int = 2,
    ):
        self.image_svc       = image_svc
        self.inference_svc   = inference_svc
        self.aggregator_svc  = aggregator_svc
        self.group_window_ms = group_window_ms
        self.bucket_size     = bucket_size
        self._groups: dict[tuple[str, int], _BusGroup] = {}

    def _bucket(self, captured_at: int) -> int:
        return (captured_at // self.bucket_size) * self.bucket_size

    async def add_frame(
        self,
        bus_id:      str,
        pane:        str,
        raw_bytes:   bytes,
        timestamp:   str,
        captured_at: Optional[int] = None,
    ) -> dict:
        """
        Buffer a frame into its NTP bucket group.
        Returns immediately — processing fires after group_window_ms deadline.
        """
        unix_ts   = captured_at or int(datetime.now(timezone.utc).timestamp())
        bucket    = self._bucket(unix_ts)
        group_key = (bus_id, bucket)

        if group_key not in self._groups:
            self._groups[group_key] = _BusGroup(bus_id=bus_id, bucket=bucket)

        group = self._groups[group_key]
        group.frames[pane] = _PendingFrame(
            raw_bytes=raw_bytes,
            timestamp=timestamp,
            captured_at=unix_ts,
        )

        if group.timer_task is None or group.timer_task.done():
            logger.debug(
                "[Grouper] bus=%s bucket=%d: starting %dms deadline timer (panes so far: %s)",
                bus_id, bucket, self.group_window_ms, sorted(group.frames),
            )
            group.timer_task = asyncio.create_task(self._wait_and_process(group_key))

        return {
            "bus_id":   bus_id,
            "bucket":   bucket,
            "pane":     pane,
            "received": sorted(group.frames),
        }

    async def _wait_and_process(self, group_key: tuple[str, int]):
        await asyncio.sleep(self.group_window_ms / 1000)
        await self._process(group_key)

    async def _process(self, group_key: tuple[str, int]):
        group = self._groups.pop(group_key, None)
        if not group or not group.frames:
            return

        bus_id   = group.bus_id
        panes    = sorted(group.frames.keys())
        timestamp = max(f.timestamp for f in group.frames.values())
        group_id  = (
            f"{bus_id}_"
            f"{datetime.fromtimestamp(group.bucket, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')}"
        )

        logger.info("[Grouper] Processing bus=%s bucket=%d panes=%s", bus_id, group.bucket, panes)

        enhanced = []
        for pane in panes:
            try:
                enhanced.append(self.image_svc.enhance(group.frames[pane].raw_bytes))
            except Exception as exc:
                logger.warning("[Grouper] Skipping pane=%s bus=%s: %s", pane, bus_id, exc)

        if not enhanced:
            logger.error("[Grouper] No valid frames for %s", group_id)
            return

        stitched    = cv2.hconcat(enhanced) if len(enhanced) > 1 else enhanced[0]
        result      = self.inference_svc.count_crowd(stitched)
        crowd_count = result["crowd_count"]

        payload = {
            "group_id":    group_id,
            "bus_id":      bus_id,
            "timestamp":   timestamp,
            "panes":       panes,
            "crowd_count": crowd_count,
        }

        logger.info("[Grouper] %s crowd=%d (%d pane(s))", group_id, crowd_count, len(enhanced))
        await self.aggregator_svc.push(bus_id, panes, crowd_count, timestamp)
