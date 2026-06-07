"""
FrameGrouper — groups frames by NTP capture timestamp bucket, not arrival time.

Each ESP32-CAM sends X-Captured-At (Unix timestamp from NTP) and a device_id
form field identifying its camera position (e.g. "CAM-front", "CAM-rear").

The server buckets it: bucket = floor(captured_at / bucket_size) * bucket_size

Example with bucket_size=2, capture_interval=2s:
  CAM-front  captured_at=1000.050  → bucket=1000  ─┐ same group
  CAM-rear   captured_at=1000.120  → bucket=1000  ─┘

  CAM-rear   captured_at=1002.080  → bucket=1002  ← different group

After the group_window_ms deadline each camera's frame is inferred
independently (no stitching). Per-camera counts are pushed to the aggregator
which flushes one Cloudflare payload per camera.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class _PendingFrame:
    raw_bytes:   bytes
    timestamp:   str
    captured_at: int


@dataclass
class _BusGroup:
    bus_id:     str
    bucket:     int
    frames:     dict[str, _PendingFrame] = field(default_factory=dict)  # device_id → frame
    timer_task: Optional[asyncio.Task]   = field(default=None, compare=False)


class FrameGrouper:
    def __init__(
        self,
        image_svc,
        inference_svc,
        aggregator_svc,
        group_window_ms: int = 500,
        bucket_size:     int = 2,
        dev_viewer=None,
    ) -> None:
        self.image_svc       = image_svc
        self.inference_svc   = inference_svc
        self.aggregator_svc  = aggregator_svc
        self.group_window_ms = group_window_ms
        self.bucket_size     = bucket_size
        self.dev_viewer      = dev_viewer  # DevViewer in dev, None in prod
        self._groups: dict[tuple[str, int], _BusGroup] = {}

    def _bucket(self, captured_at: int) -> int:
        return (captured_at // self.bucket_size) * self.bucket_size

    async def add_frame(
        self,
        bus_id:      str,
        device_id:   str,
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
        group.frames[device_id] = _PendingFrame(
            raw_bytes=raw_bytes,
            timestamp=timestamp,
            captured_at=unix_ts,
        )

        if group.timer_task is None or group.timer_task.done():
            logger.debug(
                "[Grouper] bus=%s bucket=%d: starting %dms deadline timer (devices so far: %s)",
                bus_id, bucket, self.group_window_ms, sorted(group.frames),
            )
            group.timer_task = asyncio.create_task(self._wait_and_process(group_key))

        return {
            "bus_id":   bus_id,
            "bucket":   bucket,
            "device_id": device_id,
            "received": sorted(group.frames),
        }

    async def _wait_and_process(self, group_key: tuple[str, int]) -> None:
        await asyncio.sleep(self.group_window_ms / 1000)
        await self._process(group_key)

    async def _process(self, group_key: tuple[str, int]) -> None:
        group = self._groups.pop(group_key, None)
        if not group or not group.frames:
            return

        bus_id = group.bus_id
        logger.info(
            "[Grouper] Processing bus=%s bucket=%d devices=%s",
            bus_id, group.bucket, sorted(group.frames),
        )

        for device_id, frame in group.frames.items():
            try:
                enhanced      = self.image_svc.enhance(frame.raw_bytes)
                result        = self.inference_svc.count_crowd(enhanced)
                count         = result["crowd_count"]
                camera_status = "ACTIVE"
                if self.dev_viewer is not None:
                    # bbox coords are in `enhanced`'s 640×640 letterboxed space
                    await self.dev_viewer.update(
                        device_id, enhanced, result["detections"], count
                    )
            except Exception as exc:
                logger.warning(
                    "[Grouper] Inference failed for device=%s bus=%s: %s",
                    device_id, bus_id, exc,
                )
                count         = 0
                camera_status = "ERROR"

            logger.info(
                "[Grouper] bus=%s device=%s count=%d status=%s",
                bus_id, device_id, count, camera_status,
            )
            await self.aggregator_svc.push(device_id, count, frame.timestamp, camera_status)
