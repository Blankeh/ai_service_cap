"""
FrameGrouper — groups frames by NTP capture timestamp bucket, not arrival time.

Each ESP32-CAM sends X-Captured-At (Unix timestamp from NTP).
The server buckets it: bucket = floor(captured_at / bucket_size) * bucket_size

Example with bucket_size=2, capture_interval=2s:
  cam-front  captured_at=1000.050  → bucket=1000  ─┐ same group
  cam-rear   captured_at=1000.120  → bucket=1000  ─┘

  cam-rear   captured_at=1002.080  → bucket=1002  ← different group

This is reliable even with variable network latency because grouping is based
on WHEN the image was taken (NTP), not when it arrived at the server.

A short deadline (group_window_ms) after the first frame of a bucket arrives
gives late frames time to show up before processing fires.
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
    raw_bytes: bytes
    timestamp: str
    captured_at: int  # Unix seconds (from NTP)


@dataclass
class _BusGroup:
    bus_id: str
    bucket: int                                          # Unix bucket key
    frames: dict[str, _PendingFrame] = field(default_factory=dict)  # pane → frame
    timer_task: Optional[asyncio.Task] = field(default=None, compare=False)


class FrameGrouper:
    def __init__(
        self,
        image_svc,
        inference_svc,
        cloudflare_svc,
        camera_repo,
        group_window_ms: int = 500,
        bucket_size: int = 2,           # must match ESP32 capture interval (seconds)
    ):
        self.image_svc      = image_svc
        self.inference_svc  = inference_svc
        self.cloudflare_svc = cloudflare_svc
        self.camera_repo    = camera_repo
        self.group_window_ms = group_window_ms
        self.bucket_size    = bucket_size

        # keyed by (bus_id, bucket)
        self._groups: dict[tuple[str, int], _BusGroup] = {}

    def _bucket(self, captured_at: int) -> int:
        """Round down to the nearest bucket boundary."""
        return (captured_at // self.bucket_size) * self.bucket_size

    def _expected_panes(self, bus_id: str) -> set[str]:
        cameras = self.camera_repo.list_all()
        return {c.pane for c in cameras if c.bus_id == bus_id and c.pane}

    async def add_frame(
        self,
        bus_id: str,
        pane: str,
        raw_bytes: bytes,
        timestamp: str,
        captured_at: Optional[int] = None,
    ) -> dict:
        """
        Buffer a frame into its NTP bucket group.
        Returns immediately — processing happens in the background.

        captured_at: Unix timestamp from ESP32 NTP (X-Captured-At header).
                     Falls back to current server time if not provided.
        """
        unix_ts  = captured_at or int(datetime.now(timezone.utc).timestamp())
        bucket   = self._bucket(unix_ts)
        group_key = (bus_id, bucket)

        if group_key not in self._groups:
            self._groups[group_key] = _BusGroup(bus_id=bus_id, bucket=bucket)

        group = self._groups[group_key]
        group.frames[pane] = _PendingFrame(
            raw_bytes=raw_bytes,
            timestamp=timestamp,
            captured_at=unix_ts,
        )

        expected = self._expected_panes(bus_id)
        received = set(group.frames.keys())

        if expected and received >= expected:
            # All panes present — fire immediately
            if group.timer_task and not group.timer_task.done():
                group.timer_task.cancel()
            asyncio.create_task(self._process(group_key))
        elif group.timer_task is None or group.timer_task.done():
            # Start deadline timer — process whatever arrives before it fires
            group.timer_task = asyncio.create_task(
                self._wait_and_process(group_key)
            )

        return {
            "bus_id":   bus_id,
            "bucket":   bucket,
            "pane":     pane,
            "received": sorted(received),
            "expected": sorted(expected),
            "grouped":  True,
        }

    async def _wait_and_process(self, group_key: tuple[str, int]):
        await asyncio.sleep(self.group_window_ms / 1000)
        await self._process(group_key)

    async def _process(self, group_key: tuple[str, int]):
        group = self._groups.pop(group_key, None)
        if not group or not group.frames:
            return

        bus_id    = group.bus_id
        panes     = sorted(group.frames.keys())
        timestamp = max(f.timestamp for f in group.frames.values())
        group_id  = f"{bus_id}_{datetime.fromtimestamp(group.bucket, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')}"

        logger.info(f"[Grouper] Processing bus={bus_id} bucket={group.bucket} panes={panes}")

        enhanced_frames = []
        for pane in panes:
            try:
                img = self.image_svc.enhance(group.frames[pane].raw_bytes)
                enhanced_frames.append(img)
            except Exception as exc:
                logger.warning(f"[Grouper] Skipping pane={pane}: {exc}")

        if not enhanced_frames:
            logger.error(f"[Grouper] No valid frames for {group_id}")
            return

        # Stitch panes side by side → YOLO sees the full bus interior
        stitched    = cv2.hconcat(enhanced_frames) if len(enhanced_frames) > 1 else enhanced_frames[0]
        result      = self.inference_svc.count_crowd(stitched)
        crowd_count = result["crowd_count"]

        payload = {
            "group_id":    group_id,
            "bus_id":      bus_id,
            "timestamp":   timestamp,
            "panes":       panes,
            "crowd_count": crowd_count,
        }

        logger.info(f"[Grouper] {group_id} crowd={crowd_count} ({len(enhanced_frames)} pane(s))")
        await self.cloudflare_svc.send(payload)
