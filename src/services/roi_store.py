"""
roi_store.py — persistence for per-pane ROI boxes.

ROIs are calibrated interactively (see the dev `/dev/calibrate` editor) and must
survive restarts, so the runtime source of truth is a JSON sidecar
`data/camera_rois.json` (next to `app.db`). `.env`'s CAMERA_ROIS is only a seed:

  * file present + valid  → overrides settings.camera_rois wholesale at startup
  * file missing          → seed it once from settings.camera_rois (the .env value)
  * file present + corrupt → keep the .env values, leave the file untouched, no crash

resolve_roi() reads settings.camera_rois fresh each frame, so edits made through
set_pane/delete_pane take effect on the next processed group with no restart.
"""
import json
import logging
import os
import threading
from pathlib import Path

from ..core.config import settings
from .roi_service import _is_polygon, validate_shape


def _coerce_shape(shape):
    """Float-coerce a shape, preserving rectangle vs polygon structure."""
    if _is_polygon(shape):
        return [[float(x), float(y)] for x, y in shape]
    return [float(v) for v in shape]

logger = logging.getLogger(__name__)

# Runtime source of truth. Module-level so tests can point it at a tmp_path.
ROI_STORE_PATH = Path("data/camera_rois.json")

# Guards read-merge-write of the file + in-place mutation of settings.camera_rois.
# FastAPI runs sync route handlers in a threadpool, so two PUTs can land at once.
_lock = threading.Lock()


def _apply_to_settings(rois: dict) -> None:
    """Replace settings.camera_rois contents in place (keeps held references valid)."""
    settings.camera_rois.clear()
    settings.camera_rois.update(rois)


def _sanitize(rois: dict) -> dict:
    """Drop malformed panes so resolve_roi / the UI never choke on a bad shape."""
    clean = {}
    for pane, shape in rois.items():
        reason = validate_shape(shape)
        if reason is None:
            clean[pane] = _coerce_shape(shape)
        else:
            logger.warning("Dropping malformed ROI pane %r from store: %s", pane, reason)
    return clean


def _atomic_write(rois: dict, path: Path) -> None:
    """Write JSON via a temp file + os.replace so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rois, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic on a single filesystem (incl. Windows)


def load(path: Path | None = None) -> dict:
    """
    Load persisted ROIs into settings.camera_rois and return the effective dict.

    Called once at startup (single-threaded lifespan). See module docstring for
    the precedence rules.
    """
    path = path or ROI_STORE_PATH
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.error(
                "Corrupt ROI store at %s (%s) — keeping CAMERA_ROIS from .env, "
                "leaving the file untouched", path, exc,
            )
            return dict(settings.camera_rois)
        clean = _sanitize(raw)
        _apply_to_settings(clean)
        logger.info(
            "Loaded %d ROI pane(s) from %s (overriding CAMERA_ROIS): %s",
            len(clean), path, sorted(clean),
        )
        return clean

    # No file yet — seed it from whatever .env gave us (possibly {}).
    seed = _sanitize(dict(settings.camera_rois))
    _apply_to_settings(seed)
    _atomic_write(seed, path)
    logger.info("Seeded ROI store %s from CAMERA_ROIS: %s", path, sorted(seed))
    return seed


def set_pane(pane: str, shape, path: Path | None = None) -> dict:
    """
    Validate + persist one pane's ROI shape, returning the new effective dict.

    Accepts either a rectangle [x1,y1,x2,y2] or a polygon [[x,y],...]. Raises
    ValueError (with a human-readable reason) if the shape is malformed.
    Read-merge-write happens under the lock so concurrent edits to different
    panes don't clobber each other.
    """
    path = path or ROI_STORE_PATH
    reason = validate_shape(shape)
    if reason is not None:
        raise ValueError(reason)
    shape = _coerce_shape(shape)
    with _lock:
        rois = dict(settings.camera_rois)
        rois[pane] = shape
        _apply_to_settings(rois)
        _atomic_write(rois, path)
        return rois


def delete_pane(pane: str, path: Path | None = None) -> dict:
    """Remove one pane (idempotent) so it reverts to full-frame; persist + return."""
    path = path or ROI_STORE_PATH
    with _lock:
        rois = dict(settings.camera_rois)
        rois.pop(pane, None)
        _apply_to_settings(rois)
        _atomic_write(rois, path)
        return rois
