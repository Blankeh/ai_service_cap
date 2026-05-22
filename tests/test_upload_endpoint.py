import io
from unittest.mock import MagicMock
from fastapi import HTTPException
import pytest

from .conftest import (
    make_jpeg, build_client,
    TOKEN, CAMERA_ID, BUS_ID, PANE, BUCKET,
)
from src.services.camera_service import CameraService
from src.services.grouper_service import FrameGrouper
from unittest.mock import AsyncMock


UPLOAD_HEADERS = {"X-Camera-Token": TOKEN}


def _post_frame(client, jpeg_bytes: bytes, content_type="image/jpeg", headers=None):
    h = headers or UPLOAD_HEADERS
    return client.post(
        "/api/v1/upload",
        headers=h,
        files={"file": ("frame.jpg", io.BytesIO(jpeg_bytes), content_type)},
    )


# ── Happy path ────────────────────────────────────────────────────────────────

class TestUploadSuccess:
    def test_status_200(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg).status_code == 200

    def test_response_has_all_fields(self, client, dummy_jpeg):
        body = _post_frame(client, dummy_jpeg).json()
        for key in ("camera_id", "bus_id", "bucket", "pane", "received", "expected", "grouped", "timestamp"):
            assert key in body, f"Missing field: {key}"

    def test_camera_identity_echoed(self, client, dummy_jpeg):
        body = _post_frame(client, dummy_jpeg).json()
        assert body["camera_id"] == CAMERA_ID
        assert body["bus_id"] == BUS_ID
        assert body["pane"] == PANE

    def test_bucket_value(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg).json()["bucket"] == BUCKET

    def test_grouped_flag_true(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg).json()["grouped"] is True

    def test_received_contains_pane(self, client, dummy_jpeg):
        assert PANE in _post_frame(client, dummy_jpeg).json()["received"]

    def test_timestamp_is_iso8601(self, client, dummy_jpeg):
        from datetime import datetime
        ts = _post_frame(client, dummy_jpeg).json()["timestamp"]
        datetime.fromisoformat(ts)

    def test_grouper_add_frame_called(self, client, dummy_jpeg, mock_grouper):
        _post_frame(client, dummy_jpeg)
        mock_grouper.add_frame.assert_called_once()
        args = mock_grouper.add_frame.call_args
        assert args.args[0] == BUS_ID
        assert args.args[1] == PANE


# ── Token / auth validation ───────────────────────────────────────────────────

class TestTokenValidation:
    def test_missing_token_returns_422(self, client, dummy_jpeg):
        r = client.post(
            "/api/v1/upload",
            files={"file": ("frame.jpg", io.BytesIO(dummy_jpeg), "image/jpeg")},
        )
        assert r.status_code == 422

    def test_unknown_token_returns_401(self, mock_queue_repo, mock_grouper):
        svc = MagicMock(spec=CameraService)
        svc.resolve.side_effect = HTTPException(status_code=401, detail="Unknown token")
        c = build_client(camera_svc=svc, queue_repo=mock_queue_repo, grouper=mock_grouper)
        r = _post_frame(c, make_jpeg())
        assert r.status_code == 401

    def test_unassigned_camera_returns_409(self, mock_queue_repo, mock_grouper):
        svc = MagicMock(spec=CameraService)
        svc.resolve.side_effect = HTTPException(status_code=409, detail="Not assigned")
        c = build_client(camera_svc=svc, queue_repo=mock_queue_repo, grouper=mock_grouper)
        r = _post_frame(c, make_jpeg())
        assert r.status_code == 409


# ── Content-type / image validation ──────────────────────────────────────────

class TestImageValidation:
    def test_wrong_content_type_returns_415(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg, content_type="image/png").status_code == 415

    def test_corrupt_jpeg_returns_422(self, client):
        assert _post_frame(client, b"not_a_jpeg").status_code == 422

    def test_empty_body_returns_422(self, client):
        assert _post_frame(client, b"").status_code == 422


# ── Handshake endpoint ────────────────────────────────────────────────────────

class TestHandshake:
    def test_handshake_200(self, client):
        r = client.post("/api/v1/handshake", json={"camera_id": CAMERA_ID})
        assert r.status_code == 200

    def test_handshake_returns_token_and_identity(self, client):
        body = client.post("/api/v1/handshake", json={"camera_id": CAMERA_ID}).json()
        assert "token" in body
        assert body["camera_id"] == CAMERA_ID
        assert body["bus_id"] == BUS_ID
        assert body["pane"] == PANE
        assert body["assigned"] is True

    def test_handshake_missing_camera_id_returns_422(self, client):
        r = client.post("/api/v1/handshake", json={})
        assert r.status_code == 422


# ── Admin endpoints ───────────────────────────────────────────────────────────

class TestAdminEndpoints:
    def test_list_cameras(self, client):
        r = client.get("/api/v1/cameras")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_assign_camera(self, client, mock_camera_svc):
        r = client.post(
            f"/api/v1/cameras/{CAMERA_ID}/assign",
            json={"bus_id": "BUS-002", "pane": "rear"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["bus_id"] == "BUS-002"
        assert body["pane"] == "rear"

    def test_assign_unknown_camera_returns_404(self, mock_queue_repo, mock_grouper):
        svc = MagicMock(spec=CameraService)
        svc.list_cameras.return_value = []
        svc.assign.side_effect = HTTPException(status_code=404, detail="Not found")
        c = build_client(camera_svc=svc, queue_repo=mock_queue_repo, grouper=mock_grouper)
        r = c.post(
            "/api/v1/cameras/UNKNOWN/assign",
            json={"bus_id": "BUS-001", "pane": "front"},
        )
        assert r.status_code == 404


# ── Health & queue ────────────────────────────────────────────────────────────

class TestInfoEndpoints:
    def test_health_ok(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_queue_status(self, client):
        r = client.get("/api/v1/queue/status")
        assert r.status_code == 200
        assert "pending_records" in r.json()


# ── Various image resolutions through the pipeline ───────────────────────────

class TestPipelineResolutions:
    @pytest.mark.parametrize("w,h", [
        (160, 120),
        (320, 240),
        (640, 480),
        (800, 600),
        (1024, 768),
    ])
    def test_resolution(self, w, h, mock_queue_repo, mock_camera_svc, mock_grouper):
        c = build_client(
            camera_svc=mock_camera_svc,
            queue_repo=mock_queue_repo,
            grouper=mock_grouper,
        )
        r = _post_frame(c, make_jpeg(width=w, height=h))
        assert r.status_code == 200, f"Failed for {w}x{h}: {r.text}"
