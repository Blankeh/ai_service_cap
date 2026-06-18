import json

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.roi_routes import roi_router
from src.core.config import settings
from src.services import roi_store
from src.services.snapshot_store import SnapshotStore

META = {"pad_left": 0, "pad_top": 0, "new_w": 640, "new_h": 640, "scale": 1.0}


def _det(cx, cy):
    return {"bbox": [cx - 5, cy - 5, cx + 5, cy + 5], "confidence": 0.9, "class": "person"}


def _jpeg() -> bytes:
    _, buf = cv2.imencode(".jpg", np.zeros((480, 640, 3), np.uint8))
    return buf.tobytes()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(roi_store, "ROI_STORE_PATH", tmp_path / "camera_rois.json")
    monkeypatch.setattr(settings, "camera_rois", {})
    store = SnapshotStore()
    # One live camera with two detections: one top-half, one bottom-half.
    store.update("CAM-front", _jpeg(), [_det(100, 100), _det(100, 500)], META)

    app = FastAPI()
    app.include_router(roi_router, prefix="/roi")
    app.state.snapshot_store = store
    return TestClient(app)


class TestGetRois:
    def test_lists_live_pane(self, client):
        j = client.get("/roi/rois").json()
        assert j["rois"] == {}
        panes = {p["pane"]: p for p in j["panes"]}
        assert panes["front"]["device_id"] == "CAM-front"
        assert panes["front"]["has_frame"] is True

    def test_includes_configured_but_unseen_pane(self, client):
        settings.camera_rois["rear"] = [0.0, 0.5, 1.0, 1.0]
        panes = {p["pane"]: p for p in client.get("/roi/rois").json()["panes"]}
        assert panes["rear"]["device_id"] is None
        assert panes["rear"]["has_frame"] is False


class TestPutRoi:
    def test_valid_box_persists(self, client, tmp_path):
        r = client.put("/roi/rois/front", json={"box": [0.0, 0.0, 1.0, 0.5]})
        assert r.status_code == 200
        assert r.json()["rois"]["front"] == [0.0, 0.0, 1.0, 0.5]
        on_disk = json.loads((tmp_path / "camera_rois.json").read_text())
        assert on_disk["front"] == [0.0, 0.0, 1.0, 0.5]
        assert settings.camera_rois["front"] == [0.0, 0.0, 1.0, 0.5]

    def test_invalid_box_422_and_not_persisted(self, client, tmp_path):
        r = client.put("/roi/rois/front", json={"box": [0.5, 0.0, 0.4, 1.0]})
        assert r.status_code == 422
        assert "front" not in settings.camera_rois
        assert not (tmp_path / "camera_rois.json").exists()

    def test_device_id_normalized_to_pane(self, client):
        # PUT with a full device_id should still key off the pane.
        client.put("/roi/rois/CAM-front", json={"box": [0.0, 0.0, 1.0, 0.5]})
        assert "front" in settings.camera_rois

    def test_overlap_advisory_returned(self, client):
        client.put("/roi/rois/front", json={"box": [0.0, 0.0, 1.0, 0.6]})
        r = client.put("/roi/rois/rear", json={"box": [0.0, 0.4, 1.0, 1.0]})
        assert any("overlap" in a for a in r.json()["advisories"])


class TestDeleteRoi:
    def test_delete_removes_pane(self, client):
        client.put("/roi/rois/front", json={"box": [0.0, 0.0, 1.0, 0.5]})
        r = client.delete("/roi/rois/front")
        assert r.status_code == 200
        assert "front" not in settings.camera_rois


class TestSnapshot:
    def test_returns_jpeg(self, client):
        r = client.get("/roi/snapshot/CAM-front")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"

    def test_404_when_no_frame(self, client):
        assert client.get("/roi/snapshot/CAM-rear").status_code == 404


class TestPreview:
    def test_counts_heads_in_box(self, client):
        # Top-half box catches 1 of the 2 detections.
        r = client.get("/roi/rois/front/preview",
                       params={"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 0.5,
                               "device_id": "CAM-front"})
        assert r.json() == {"count": 1, "total": 2}

    def test_null_when_no_frame(self, client):
        r = client.get("/roi/rois/rear/preview",
                       params={"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0,
                               "device_id": "CAM-rear"})
        assert r.json()["count"] is None


def test_editor_page_served(client):
    r = client.get("/roi/")
    assert r.status_code == 200 and "ROI calibration" in r.text
