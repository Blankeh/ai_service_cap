"""
snapshot_store.py — latest raw frame + detections per camera, for the ROI editor.

Unlike the dev-only MJPEG viewer (dev_viewer.py), this is always instantiated —
in prod too — so the ROI calibration editor (/roi/calibrate) can run against the
actually-deployed Pi. It keeps only the most recent ORIGINAL (non-letterboxed)
JPEG per device plus that frame's detections + letterbox meta:

  * the raw original frame is the editor's drawing canvas (its natural W×H is a
    clean 0..1 ROI space — the letterboxed dev stream would skew the box), and
  * the detections + meta drive the live "how many heads are in this box" preview
    via roi_service.count_in_roi.

Plain synchronous dict writes from the grouper's processing loop; the editor
polls the snapshot/preview endpoints rather than streaming, so no locking or
async condition is needed. Memory is bounded by the camera count (a handful).
"""
import logging

logger = logging.getLogger(__name__)


class SnapshotStore:
    def __init__(self) -> None:
        self._raw:    dict[str, bytes] = {}   # device_id → latest ORIGINAL jpeg bytes
        self._latest: dict[str, dict]  = {}   # device_id → {"detections": [...], "meta": {...}}

    def update(self, device_id: str, raw_jpeg: bytes, detections: list[dict], meta: dict) -> None:
        """Publish the latest original frame + its detections/meta for a camera."""
        self._raw[device_id] = raw_jpeg
        self._latest[device_id] = {"detections": detections, "meta": meta}

    def snapshot(self, device_id: str) -> bytes | None:
        """Latest original JPEG for a camera, or None if none seen yet."""
        return self._raw.get(device_id)

    def latest(self, device_id: str) -> dict | None:
        """Latest {detections, meta} for a camera, or None if none seen yet."""
        return self._latest.get(device_id)

    def devices(self) -> list[str]:
        """Device_ids that have produced at least one frame."""
        return sorted(self._raw)
