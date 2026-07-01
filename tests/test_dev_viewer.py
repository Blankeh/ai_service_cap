import numpy as np

from src.services.dev_viewer import DevViewer

META = {"pad_left": 0, "pad_top": 0, "new_w": 640, "new_h": 640, "scale": 1.0}


def _det(cx, cy):
    return {"bbox": [cx - 5, cy - 5, cx + 5, cy + 5], "confidence": 0.9, "class": "person"}


def _frame():
    return np.zeros((640, 640, 3), np.uint8)


def test_annotate_without_roi_returns_same_shape():
    out = DevViewer._annotate(_frame(), [_det(100, 100)], "CAM-front", 1)
    assert isinstance(out, np.ndarray) and out.shape == (640, 640, 3)


def test_annotate_with_roi_draws_without_error():
    # Top-half box ROI: one detection inside (counted/green), one outside (red).
    roi = (0.0, 0.0, 1.0, 0.5)
    out = DevViewer._annotate(
        _frame(), [_det(100, 100), _det(100, 500)], "CAM-front", 1, roi, META
    )
    assert isinstance(out, np.ndarray) and out.shape == (640, 640, 3)


def test_annotate_with_polygon_roi():
    poly = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    out = DevViewer._annotate(_frame(), [_det(50, 50)], "CAM-front", 1, poly, META)
    assert isinstance(out, np.ndarray) and out.shape == (640, 640, 3)
