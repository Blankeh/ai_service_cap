from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.services.roi_service import (
    FULL_FRAME,
    count_in_roi,
    map_detection,
    point_in_roi,
    resolve_roi,
    roi_advisories,
    roi_to_pixels,
    validate_box,
    validate_rois,
)

# Square no-padding letterbox meta → normalized = pixel / 640
META = {"pad_left": 0, "pad_top": 0, "new_w": 640, "new_h": 640, "scale": 1.0}


def _det(cx, cy):
    """A detection whose center is (cx, cy) in 640×640 inference space."""
    return {"bbox": [cx - 5, cy - 5, cx + 5, cy + 5], "confidence": 0.9, "class": "person"}


class TestResolveRoi:
    def test_default_is_full_frame(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "camera_rois", {})
        assert resolve_roi("CAM-front") == FULL_FRAME

    def test_configured_pane(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "camera_rois", {"front": [0.0, 0.0, 1.0, 0.5]})
        assert resolve_roi("CAM-front") == (0.0, 0.0, 1.0, 0.5)

    def test_unconfigured_pane_falls_back(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "camera_rois", {"front": [0.0, 0.0, 1.0, 0.5]})
        assert resolve_roi("CAM-rear") == FULL_FRAME

    def test_malformed_roi_falls_back(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "camera_rois", {"front": [0.0, 0.0]})
        assert resolve_roi("CAM-front") == FULL_FRAME

    def test_unconfigured_pane_warns_once(self, monkeypatch, caplog):
        from src.core.config import settings
        import src.services.roi_service as roi
        monkeypatch.setattr(settings, "camera_rois", {"front": [0.0, 0.0, 1.0, 0.5]})
        roi._warned_missing.clear()
        with caplog.at_level("WARNING"):
            assert resolve_roi("CAM-rear") == FULL_FRAME
            resolve_roi("CAM-rear")  # second call must NOT warn again
        warns = [r for r in caplog.records if "No ROI configured" in r.getMessage()]
        assert len(warns) == 1

    def test_no_warning_when_no_rois_configured(self, monkeypatch, caplog):
        from src.core.config import settings
        import src.services.roi_service as roi
        monkeypatch.setattr(settings, "camera_rois", {})
        roi._warned_missing.clear()
        with caplog.at_level("WARNING"):
            assert resolve_roi("CAM-front") == FULL_FRAME
        assert not [r for r in caplog.records if "No ROI configured" in r.getMessage()]


class TestValidateRois:
    def test_valid_rois_no_problems(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "camera_rois", {"front": [0.0, 0.0, 1.0, 0.5]})
        assert validate_rois() == []

    def test_flags_inverted_box(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "camera_rois", {"front": [0.5, 0.0, 0.4, 1.0]})  # x1 > x2
        assert validate_rois()

    def test_flags_out_of_range(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "camera_rois", {"front": [0.0, 0.0, 1.2, 1.0]})  # x2 > 1
        assert validate_rois()

    def test_flags_non_numeric(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "camera_rois", {"front": [0.0, 0.0]})  # only 2 values
        assert validate_rois()


class TestValidateBox:
    def test_valid_box(self):
        assert validate_box([0.0, 0.0, 1.0, 0.5]) is None

    def test_inverted_box(self):
        assert validate_box([0.5, 0.0, 0.4, 1.0]) is not None

    def test_zero_area_rejected(self):
        assert validate_box([0.3, 0.0, 0.3, 1.0]) is not None  # x1 == x2

    def test_out_of_range(self):
        assert validate_box([0.0, 0.0, 1.2, 1.0]) is not None

    def test_wrong_length(self):
        assert validate_box([0.0, 0.0, 1.0]) is not None

    def test_non_numeric(self):
        assert validate_box(["a", 0.0, 1.0, 0.5]) is not None


class TestRoiAdvisories:
    def test_tiled_panes_no_overlap(self):
        rois = {"front": [0.0, 0.0, 1.0, 0.5], "rear": [0.0, 0.5, 1.0, 1.0]}
        assert roi_advisories(rois) == []

    def test_overlapping_panes_flagged(self):
        rois = {"front": [0.0, 0.0, 1.0, 0.6], "rear": [0.0, 0.4, 1.0, 1.0]}
        adv = roi_advisories(rois)
        assert len(adv) == 1 and "overlap" in adv[0]

    def test_malformed_box_skipped(self):
        rois = {"front": [0.0, 0.0, 1.0, 0.5], "bad": [0.5, 0.0, 0.4, 1.0]}
        assert roi_advisories(rois) == []  # bad box ignored, no crash


class TestCountInRoi:
    def test_full_frame_counts_all(self):
        dets = [_det(100, 100), _det(600, 600)]
        assert count_in_roi(dets, FULL_FRAME, META) == 2

    def test_filters_outside_zone(self):
        # Top-half ROI (ny <= 0.5 → cy <= 320)
        roi = (0.0, 0.0, 1.0, 0.5)
        dets = [_det(100, 100), _det(100, 500)]  # one in, one out
        assert count_in_roi(dets, roi, META) == 1

    def test_empty_detections(self):
        assert count_in_roi([], (0.0, 0.0, 1.0, 0.5), META) == 0


class TestPolygonRoi:
    def test_resolve_returns_polygon_points(self, monkeypatch):
        from src.core.config import settings
        poly = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.5], [0.0, 0.5]]
        monkeypatch.setattr(settings, "camera_rois", {"front": poly})
        assert resolve_roi("CAM-front") == [(0.0, 0.0), (1.0, 0.0), (1.0, 0.5), (0.0, 0.5)]

    def test_count_inside_polygon(self):
        # Triangle over the top-left; (100,100)→(0.156,0.156) is inside, the
        # bottom detection (100,500)→(0.156,0.781) is below the hypotenuse.
        tri = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        # Use a clearly-inside top point and a clearly-outside bottom-right point.
        dets = [_det(50, 50), _det(600, 600)]
        assert count_in_roi(dets, tri, META) == 1

    def test_validate_flags_too_few_vertices(self):
        from src.services.roi_service import validate_shape
        assert validate_shape([[0.0, 0.0], [1.0, 1.0]]) is not None

    def test_validate_flags_degenerate_polygon(self):
        from src.services.roi_service import validate_shape
        # Three collinear points → zero area.
        assert validate_shape([[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]]) is not None

    def test_validate_accepts_good_polygon(self):
        from src.services.roi_service import validate_shape
        assert validate_shape([[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]]) is None

    def test_advisory_overlap_uses_bbox(self):
        # A top polygon and a bottom box whose bounding boxes overlap in y.
        rois = {"front": [[0.0, 0.0], [1.0, 0.0], [1.0, 0.6], [0.0, 0.6]],
                "rear": [0.0, 0.4, 1.0, 1.0]}
        assert any("overlap" in a for a in roi_advisories(rois))


class TestMapDetection:
    def test_maps_bbox_and_center(self):
        # bbox [95,95,105,105] center (100,100) → normalized 100/640 = 0.15625
        m = map_detection(_det(100, 100), META)
        assert m["box"] == pytest.approx([95 / 640, 95 / 640, 105 / 640, 105 / 640])
        assert m["cx"] == pytest.approx(0.15625)
        assert m["cy"] == pytest.approx(0.15625)
        assert m["class"] == "person"
        assert m["confidence"] == pytest.approx(0.9)

    def test_respects_letterbox_padding(self):
        # 100px of top padding, content 640×440 → center y (300) maps to (300-100)/440
        meta = {"pad_left": 0, "pad_top": 100, "new_w": 640, "new_h": 440, "scale": 1.0}
        m = map_detection(_det(320, 300), meta)
        assert m["cx"] == pytest.approx(320 / 640)
        assert m["cy"] == pytest.approx((300 - 100) / 440)

    def test_degenerate_meta_returns_none(self):
        assert map_detection(_det(100, 100), {**META, "new_w": 0}) is None


class TestPointInRoi:
    def test_box_inside_and_outside(self):
        box = (0.0, 0.0, 1.0, 0.5)
        assert point_in_roi(0.5, 0.25, box) is True
        assert point_in_roi(0.5, 0.75, box) is False

    def test_polygon_inside_and_outside(self):
        tri = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        assert point_in_roi(0.1, 0.1, tri) is True
        assert point_in_roi(0.9, 0.9, tri) is False


class TestRoiToPixels:
    def test_box_expands_to_four_corners(self):
        # Top-half box in a no-padding 640² frame → the four corner pixels.
        assert roi_to_pixels((0.0, 0.0, 1.0, 0.5), META) == [
            (0, 0), (640, 0), (640, 320), (0, 320),
        ]

    def test_polygon_maps_each_vertex(self):
        poly = [[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]]
        assert roi_to_pixels(poly, META) == [(0, 0), (640, 0), (320, 640)]


class TestGrouperRoiCombining:
    async def _run_process(self, frames, camera_rois, monkeypatch, detections_per_cam=2):
        from src.core.config import settings
        from src.services.grouper_service import FrameGrouper, _BusGroup, _PendingFrame

        monkeypatch.setattr(settings, "camera_rois", camera_rois)

        image_svc = MagicMock()
        image_svc.enhance_with_meta.return_value = (np.zeros((640, 640, 3), np.uint8), META)

        inference_svc = MagicMock()
        # Two detections: one in the top half, one in the bottom half.
        inference_svc.count_crowd.return_value = {
            "crowd_count": detections_per_cam,
            "detections": [_det(100, 100), _det(100, 500)],
        }

        aggregator = MagicMock()
        aggregator.push = AsyncMock()

        grouper = FrameGrouper(
            image_svc=image_svc,
            inference_svc=inference_svc,
            aggregator_svc=aggregator,
            group_window_ms=10_000,
            bucket_size=2,
        )
        grouper._groups[("34", 1000)] = _BusGroup(
            bus_id="34",
            bucket=1000,
            frames={
                dev: _PendingFrame(raw_bytes=b"x", timestamp="2024-01-01T00:00:00+00:00", captured_at=1000)
                for dev in frames
            },
        )
        await grouper._process(("34", 1000))
        return aggregator

    async def test_full_frame_sums_across_cameras(self, monkeypatch):
        agg = await self._run_process(["CAM-front", "CAM-rear"], {}, monkeypatch)
        agg.push.assert_awaited_once()
        bus_id, count, _ts, status = agg.push.call_args.args
        assert bus_id == "34"
        assert count == 4          # 2 detections × 2 cameras, full frame
        assert status == "ACTIVE"

    async def test_roi_filtering_reduces_sum(self, monkeypatch):
        # front = top half, rear = bottom half → each camera contributes 1 of its 2 dets
        rois = {"front": [0.0, 0.0, 1.0, 0.5], "rear": [0.0, 0.5, 1.0, 1.0]}
        agg = await self._run_process(["CAM-front", "CAM-rear"], rois, monkeypatch)
        _bus_id, count, _ts, _status = agg.push.call_args.args
        assert count == 2          # 1 per camera after ROI filtering
