"""
Per-pane ROI resolution and ROI-filtered detection counting.

Each camera's device_id (e.g. "CAM-front") selects a region of interest covering
a distinct zone of the bus. ROIs are normalized 0..1 *shapes* held Pi-side in
settings.camera_rois, keyed by pane (front/mid/rear). A shape is either:

  * a rectangle  — a flat list [x1, y1, x2, y2]      (the original format), or
  * a polygon    — a list of [x, y] vertices, >= 3   (freeform boundaries).

Both formats coexist: old rectangle ROIs keep working untouched, and the editor
can now draw an arbitrary polygon for cameras where a box is too coarse. Panes
with no configured ROI fall back to the full frame, so behaviour is unchanged
until ROIs are provided.

Detections come back in the letterboxed target_size×target_size space, so each
detection center is mapped back to original-frame-normalized coordinates using the
letterbox meta from ImageService before the ROI test.
"""
import logging

from ..core.config import settings

logger = logging.getLogger(__name__)

# Whole-frame ROI — counts every detection.
FULL_FRAME = (0.0, 0.0, 1.0, 1.0)

# device_ids already warned about (no configured ROI) — avoids per-frame log spam.
_warned_missing: set[str] = set()


def _pane_key(device_id: str) -> str:
    """Normalize a device_id to its pane key (matches aggregator camera-id logic)."""
    return device_id.lower().replace("cam-", "").strip()


def _is_polygon(shape) -> bool:
    """
    True if `shape` is a polygon (a sequence of [x, y] points), False if it's a
    flat rectangle [x1, y1, x2, y2]. Distinguished by whether the first element
    is itself a sequence. A resolved box tuple (4 floats) reads as a rectangle.
    """
    try:
        first = shape[0]
    except (IndexError, TypeError, KeyError):
        return False
    return isinstance(first, (list, tuple))


def _polygon_area(pts) -> float:
    """Signed shoelace area of a polygon (normalized units)."""
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _point_in_polygon(x: float, y: float, pts) -> bool:
    """Ray-casting point-in-polygon test (points are (x, y) in 0..1 space)."""
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def _shape_bbox(shape) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box (x1, y1, x2, y2) of any shape (box or polygon)."""
    if _is_polygon(shape):
        xs = [float(p[0]) for p in shape]
        ys = [float(p[1]) for p in shape]
        return (min(xs), min(ys), max(xs), max(ys))
    x1, y1, x2, y2 = (float(v) for v in shape)
    return (x1, y1, x2, y2)


def resolve_roi(device_id: str):
    """
    Return the configured ROI shape for a device_id, or the full frame if unset.

    The result is a 4-tuple (x1, y1, x2, y2) for a rectangle ROI, or a list of
    (x, y) tuples for a polygon ROI. count_in_roi accepts either.
    """
    pane = _pane_key(device_id)
    raw = settings.camera_rois.get(pane)
    if raw is None:
        # When ROIs are configured at all, a camera with no entry counting the
        # whole frame is almost always a misconfig (wrong key) that silently
        # double-counts overlapping people. Make it loud — once per device_id.
        if settings.camera_rois and device_id not in _warned_missing:
            logger.warning(
                "No ROI configured for device_id=%r (pane=%r); known panes=%s — "
                "counting full frame (risks double-counting overlap)",
                device_id, pane, sorted(settings.camera_rois),
            )
            _warned_missing.add(device_id)
        return FULL_FRAME
    try:
        if _is_polygon(raw):
            return [(float(x), float(y)) for x, y in raw]
        x1, y1, x2, y2 = (float(v) for v in raw)
        return (x1, y1, x2, y2)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid ROI for pane %r: %r — falling back to full frame", device_id, raw
        )
        return FULL_FRAME


def validate_box(box) -> str | None:
    """
    Return None if `box` is a well-formed normalized rectangle, else a reason.

    A valid box is 4 numbers with 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1.
    The strict `<` rejects zero-area (degenerate) boxes.
    """
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        return f"not 4 numbers ({box!r})"
    if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
        return (
            "out of range / inverted "
            f"(need 0<=x1<x2<=1, 0<=y1<y2<=1; got {[x1, y1, x2, y2]})"
        )
    return None


def validate_polygon(poly) -> str | None:
    """
    Return None if `poly` is a well-formed normalized polygon, else a reason.

    A valid polygon is >= 3 [x, y] vertices, each in 0..1, enclosing a non-zero
    area (rejects collinear / degenerate point sets).
    """
    try:
        pts = [(float(x), float(y)) for x, y in poly]
    except (TypeError, ValueError):
        return f"polygon vertices must be [x, y] pairs ({poly!r})"
    if len(pts) < 3:
        return f"polygon needs at least 3 vertices (got {len(pts)})"
    for x, y in pts:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return f"polygon vertex out of range (need 0<=x,y<=1; got {(x, y)})"
    if abs(_polygon_area(pts)) < 1e-9:
        return "polygon is degenerate (zero area / collinear vertices)"
    return None


def validate_shape(shape) -> str | None:
    """Validate a ROI shape of either format (polygon or rectangle)."""
    if _is_polygon(shape):
        return validate_polygon(shape)
    return validate_box(shape)


def validate_rois() -> list[str]:
    """
    Check every configured ROI shape is well-formed and return a list of problems.

    Called at startup so malformed shapes surface in the journal instead of
    silently mis-counting. Returns [] when all ROIs are valid (or none set).
    """
    problems: list[str] = []
    for pane, raw in settings.camera_rois.items():
        reason = validate_shape(raw)
        if reason is not None:
            problems.append(f"{pane!r}: {reason}")
    return problems


def roi_advisories(rois: dict) -> list[str]:
    """
    Non-fatal calibration warnings for a set of ROI shapes — never blocks a save.

    Flags pairs of panes that overlap (people in the overlap are double-counted).
    Overlap is tested on each shape's bounding box, so for polygons it is a
    conservative approximation (it may warn when the polygons themselves don't
    quite touch). Malformed shapes are skipped (validate_shape handles those).
    """
    advisories: list[str] = []
    bboxes = {p: _shape_bbox(b) for p, b in rois.items()
              if validate_shape(b) is None}

    panes = sorted(bboxes)
    for i, a in enumerate(panes):
        ax1, ay1, ax2, ay2 = bboxes[a]
        for b in panes[i + 1:]:
            bx1, by1, bx2, by2 = bboxes[b]
            ox = min(ax2, bx2) - max(ax1, bx1)
            oy = min(ay2, by2) - max(ay1, by1)
            if ox > 0 and oy > 0:
                advisories.append(
                    f"{a!r} and {b!r} overlap — people in the shared zone are "
                    f"double-counted"
                )
    return advisories


def point_in_roi(nx: float, ny: float, roi) -> bool:
    """
    True if the normalized point (nx, ny) lies inside `roi`.

    Unifies the two shape formats — a rectangle (x1,y1,x2,y2) uses a simple range
    test, a polygon (list of (x,y) vertices) uses ray casting. Shared by
    count_in_roi (the count), the editor dot colors, and the dev-viewer box
    colors so all three agree by construction.
    """
    if _is_polygon(roi):
        return _point_in_polygon(nx, ny, roi)
    rx1, ry1, rx2, ry2 = roi
    return rx1 <= nx <= rx2 and ry1 <= ny <= ry2


def map_detection(det: dict, meta: dict) -> dict | None:
    """
    Map a detection's bbox from letterboxed inference space to original-frame
    normalized 0..1 coords, for overlaying on the ROI editor canvas.

    Returns {"box": [nx1,ny1,nx2,ny2], "cx", "cy", "class", "confidence"} or
    None when meta is degenerate (new_w/new_h <= 0). Coords are intentionally not
    clamped: a detection straddling the frame edge maps partly outside 0..1 and
    the SVG clips it; the center still drives the in/out (counted) decision.
    """
    new_w = meta["new_w"]
    new_h = meta["new_h"]
    if new_w <= 0 or new_h <= 0:
        return None
    pad_left = meta["pad_left"]
    pad_top  = meta["pad_top"]
    x1, y1, x2, y2 = det["bbox"]
    nx1 = (x1 - pad_left) / new_w
    ny1 = (y1 - pad_top) / new_h
    nx2 = (x2 - pad_left) / new_w
    ny2 = (y2 - pad_top) / new_h
    return {
        "box": [nx1, ny1, nx2, ny2],
        "cx": (nx1 + nx2) / 2,
        "cy": (ny1 + ny2) / 2,
        "class": det.get("class"),
        "confidence": det.get("confidence"),
    }


def roi_to_pixels(roi, meta: dict) -> list[tuple[int, int]]:
    """
    Turn an ROI (box or polygon) into pixel vertices in the letterboxed frame.

    Inverse of map_detection's per-coordinate map: px = nx*new_w + pad_left,
    py = ny*new_h + pad_top. A rectangle expands to its 4 corners. Used to draw
    the ROI outline on the dev viewer, whose canvas is the letterboxed frame.
    """
    pad_left = meta["pad_left"]
    pad_top  = meta["pad_top"]
    new_w    = meta["new_w"]
    new_h    = meta["new_h"]
    if _is_polygon(roi):
        verts = [(float(x), float(y)) for x, y in roi]
    else:
        rx1, ry1, rx2, ry2 = roi
        verts = [(rx1, ry1), (rx2, ry1), (rx2, ry2), (rx1, ry2)]
    return [(int(nx * new_w + pad_left), int(ny * new_h + pad_top)) for nx, ny in verts]


def count_in_roi(detections: list[dict], roi, meta: dict) -> int:
    """
    Count detections whose center falls inside `roi`.

    detections: [{"bbox": [x1,y1,x2,y2], ...}] in letterboxed inference space.
    roi:        FULL_FRAME, a normalized rectangle (x1,y1,x2,y2), or a polygon
                (list of (x,y) vertices) in original-frame space.
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

    count = 0
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        nx = (cx - pad_left) / new_w
        ny = (cy - pad_top) / new_h
        if point_in_roi(nx, ny, roi):
            count += 1
    return count
