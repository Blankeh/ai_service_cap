"""
Per-pane ROI resolution and ROI-filtered detection counting.

Each camera's device_id (e.g. "CAM-front") selects a region of interest covering
a distinct zone of the bus. ROIs are normalized 0..1 boxes [x1, y1, x2, y2] held
Pi-side in settings.camera_rois, keyed by pane (front/mid/rear). Panes with no
configured ROI fall back to the full frame, so behaviour is unchanged until ROIs
are provided.

Detections come back in the letterboxed target_size×target_size space, so each
detection center is mapped back to original-frame-normalized coordinates using the
letterbox meta from ImageService before the ROI test.
"""
import logging

from ..core.config import settings

logger = logging.getLogger(__name__)

# Whole-frame ROI — counts every detection.
FULL_FRAME = (0.0, 0.0, 1.0, 1.0)


def _pane_key(device_id: str) -> str:
    """Normalize a device_id to its pane key (matches aggregator camera-id logic)."""
    return device_id.lower().replace("cam-", "").strip()


def resolve_roi(device_id: str) -> tuple[float, float, float, float]:
    """Return the normalized ROI box for a device_id, or the full frame if unset."""
    raw = settings.camera_rois.get(_pane_key(device_id))
    if raw is None:
        return FULL_FRAME
    try:
        x1, y1, x2, y2 = (float(v) for v in raw)
        return (x1, y1, x2, y2)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid ROI for pane %r: %r — falling back to full frame", device_id, raw
        )
        return FULL_FRAME


def count_in_roi(detections: list[dict], roi: tuple[float, float, float, float], meta: dict) -> int:
    """
    Count detections whose center falls inside `roi`.

    detections: [{"bbox": [x1,y1,x2,y2], ...}] in letterboxed inference space.
    roi:        normalized (x1,y1,x2,y2) in original-frame space.
    meta:       letterbox transform from ImageService.enhance_with_meta.
    """
    if roi == FULL_FRAME:
        return len(detections)

    pad_left = meta["pad_left"]
    pad_top  = meta["pad_top"]
    new_w    = meta["new_w"]
    new_h    = meta["new_h"]
    if new_w <= 0 or new_h <= 0:
        return len(detections)

    rx1, ry1, rx2, ry2 = roi
    count = 0
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        nx = (cx - pad_left) / new_w
        ny = (cy - pad_top) / new_h
        if rx1 <= nx <= rx2 and ry1 <= ny <= ry2:
            count += 1
    return count
