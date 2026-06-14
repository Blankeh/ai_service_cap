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

from .roi_service import count_in_roi, resolve_roi

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
        group_window_ms:  int = 500,
        bucket_size:      int = 2,
        dev_viewer=None,
        expected_cameras: int = 0,
    ) -> None:
        self.image_svc        = image_svc
        self.inference_svc    = inference_svc
        self.aggregator_svc   = aggregator_svc
        self.group_window_ms  = group_window_ms
        self.bucket_size      = bucket_size
        self.dev_viewer       = dev_viewer  # DevViewer in dev, None in prod
        self.expected_cameras = expected_cameras  # 0 = disabled (wait for deadline only)
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
        sync_round:  Optional[int] = None,
    ) -> dict:
        """
        Buffer a frame into its group, then return immediately.

        Frames are grouped by sync_round when the camera supplies one (the shared
        ts the Pi broadcast in the capture trigger, echoed back as X-Sync-Round) —
        every camera in one trigger shares an identical round_key, so a round can
        never split across an NTP bucket boundary. Without the header we fall back
        to the captured_at bucket (unchanged behaviour for old firmware).

        Processing fires after the group_window_ms deadline, or as soon as all
        expected_cameras frames have arrived (whichever is first).
        """
        unix_ts   = captured_at or int(datetime.now(timezone.utc).timestamp())
        round_key = sync_round if sync_round is not None else self._bucket(unix_ts)
        group_key = (bus_id, round_key)

        if group_key not in self._groups:
            self._groups[group_key] = _BusGroup(bus_id=bus_id, bucket=round_key)

        group = self._groups[group_key]
        group.frames[device_id] = _PendingFrame(
            raw_bytes=raw_bytes,
            timestamp=timestamp,
            captured_at=unix_ts,
        )

        # Early-fire: once every expected camera has reported, process now instead
        # of waiting out the deadline. The deadline timer remains the fallback for
        # rounds where a camera is missing/late.
        if self.expected_cameras > 0 and len(group.frames) >= self.expected_cameras:
            logger.debug(
                "[Grouper] bus=%s round=%d: all %d expected cameras arrived — processing early",
                bus_id, round_key, self.expected_cameras,
            )
            if group.timer_task is not None and not group.timer_task.done():
                group.timer_task.cancel()
            received = sorted(group.frames)
            await self._process(group_key)
            return {
                "bus_id":   bus_id,
                "bucket":   round_key,
                "device_id": device_id,
                "received": received,
            }

        if group.timer_task is None or group.timer_task.done():
            logger.debug(
                "[Grouper] bus=%s round=%d: starting %dms deadline timer (devices so far: %s)",
                bus_id, round_key, self.group_window_ms, sorted(group.frames),
            )
            group.timer_task = asyncio.create_task(self._wait_and_process(group_key))

        return {
            "bus_id":   bus_id,
            "bucket":   round_key,
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

        # Synced cameras each cover a distinct ROI zone of the bus; sum their
        # ROI-filtered counts into one combined value for the bus.
        bus_total      = 0
        latest_ts      = ""
        any_active     = False

        for device_id, frame in group.frames.items():
            try:
                enhanced, meta = self.image_svc.enhance_with_meta(frame.raw_bytes)
                result         = self.inference_svc.count_crowd(enhanced)
                roi            = resolve_roi(device_id)
                count          = count_in_roi(result["detections"], roi, meta)
                any_active     = True
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
                count = 0

            logger.info(
                "[Grouper] bus=%s device=%s roi_count=%d", bus_id, device_id, count,
            )
            bus_total += count
            latest_ts  = frame.timestamp

        bus_status = "ACTIVE" if any_active else "ERROR"
        logger.info(
            "[Grouper] bus=%s bucket=%d combined_count=%d status=%s",
            bus_id, group.bucket, bus_total, bus_status,
        )
        await self.aggregator_svc.push(bus_id, bus_total, latest_ts, bus_status)
