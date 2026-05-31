import io
import pytest
from unittest.mock import AsyncMock

from .conftest import make_jpeg, build_client, CAMERA_ID, BUS_ID, PANE, BUCKET
from src.services.grouper_service import FrameGrouper


UPLOAD_HEADERS = {
    "X-Camera-Id": CAMERA_ID,
    "X-Bus-Id":    BUS_ID,
    "X-Pane":      PANE,
}


def _post_frame(client, jpeg_bytes: bytes, content_type="image/jpeg", headers=None):
    return client.post(
        "/api/v1/upload",
        headers=UPLOAD_HEADERS if headers is None else headers,
        files={"file": ("frame.jpg", io.BytesIO(jpeg_bytes), content_type)},
    )


# ── Happy path ────────────────────────────────────────────────────────────────

class TestUploadSuccess:
    def test_status_200(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg).status_code == 200

    def test_response_fields(self, client, dummy_jpeg):
        body = _post_frame(client, dummy_jpeg).json()
        for key in ("camera_id", "bus_id", "bucket", "pane", "received", "timestamp"):
            assert key in body, f"Missing field: {key}"

    def test_identity_echoed(self, client, dummy_jpeg):
        body = _post_frame(client, dummy_jpeg).json()
        assert body["camera_id"] == CAMERA_ID
        assert body["bus_id"]    == BUS_ID
        assert body["pane"]      == PANE

    def test_timestamp_is_iso8601(self, client, dummy_jpeg):
        from datetime import datetime
        ts = _post_frame(client, dummy_jpeg).json()["timestamp"]
        datetime.fromisoformat(ts)

    def test_grouper_called_with_bus_and_pane(self, client, dummy_jpeg, mock_grouper):
        _post_frame(client, dummy_jpeg)
        mock_grouper.add_frame.assert_called_once()
        call = mock_grouper.add_frame.call_args
        assert call.args[0] == BUS_ID
        assert call.args[1] == PANE

    def test_captured_at_header_forwarded(self, mock_queue_repo, mock_grouper, dummy_jpeg):
        c = build_client(queue_repo=mock_queue_repo, grouper=mock_grouper)
        headers = {**UPLOAD_HEADERS, "X-Captured-At": "1000"}
        _post_frame(c, dummy_jpeg, headers=headers)
        call = mock_grouper.add_frame.call_args
        assert call.args[4] == 1000


# ── Missing required headers ──────────────────────────────────────────────────

class TestMissingHeaders:
    def test_missing_camera_id_returns_422(self, client, dummy_jpeg):
        headers = {k: v for k, v in UPLOAD_HEADERS.items() if k != "X-Camera-Id"}
        assert _post_frame(client, dummy_jpeg, headers=headers).status_code == 422

    def test_missing_bus_id_returns_422(self, client, dummy_jpeg):
        headers = {k: v for k, v in UPLOAD_HEADERS.items() if k != "X-Bus-Id"}
        assert _post_frame(client, dummy_jpeg, headers=headers).status_code == 422

    def test_missing_pane_returns_422(self, client, dummy_jpeg):
        headers = {k: v for k, v in UPLOAD_HEADERS.items() if k != "X-Pane"}
        assert _post_frame(client, dummy_jpeg, headers=headers).status_code == 422

    def test_no_headers_returns_422(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg, headers={}).status_code == 422


# ── Image validation ──────────────────────────────────────────────────────────

class TestImageValidation:
    def test_wrong_content_type_returns_415(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg, content_type="image/png").status_code == 415

    def test_corrupt_jpeg_returns_422(self, client):
        assert _post_frame(client, b"not_a_jpeg").status_code == 422

    def test_empty_body_returns_422(self, client):
        assert _post_frame(client, b"").status_code == 422


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


# ── Various resolutions ───────────────────────────────────────────────────────

class TestResolutions:
    @pytest.mark.parametrize("w,h", [
        (160, 120), (320, 240), (640, 480), (800, 600), (1024, 768),
    ])
    def test_resolution(self, w, h, mock_queue_repo, mock_grouper):
        c = build_client(queue_repo=mock_queue_repo, grouper=mock_grouper)
        assert _post_frame(c, make_jpeg(width=w, height=h)).status_code == 200
