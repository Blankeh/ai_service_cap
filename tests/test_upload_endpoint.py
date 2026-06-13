import io
import pytest
from unittest.mock import AsyncMock

from .conftest import make_jpeg, build_client, DEVICE_ID, BUS_ID, BUCKET
from src.services.grouper_service import FrameGrouper


def _post_frame(client, jpeg_bytes: bytes, content_type="image/jpeg", device_id=DEVICE_ID,
                extra_headers=None):
    headers = extra_headers or {}
    return client.post(
        "/api/v1/upload",
        headers=headers,
        files={"file": ("frame.jpg", io.BytesIO(jpeg_bytes), content_type)},
        data={"device_id": device_id},
    )


# ── Happy path ────────────────────────────────────────────────────────────────

class TestUploadSuccess:
    def test_status_200(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg).status_code == 200

    def test_response_fields(self, client, dummy_jpeg):
        body = _post_frame(client, dummy_jpeg).json()
        for key in ("device_id", "bus_id", "bucket", "received", "timestamp"):
            assert key in body, f"Missing field: {key}"

    def test_device_id_echoed(self, client, dummy_jpeg):
        body = _post_frame(client, dummy_jpeg).json()
        assert body["device_id"] == DEVICE_ID

    def test_timestamp_is_iso8601(self, client, dummy_jpeg):
        from datetime import datetime
        ts = _post_frame(client, dummy_jpeg).json()["timestamp"]
        datetime.fromisoformat(ts)

    def test_grouper_called_with_bus_and_device(self, client, dummy_jpeg, mock_grouper):
        _post_frame(client, dummy_jpeg)
        mock_grouper.add_frame.assert_called_once()
        call = mock_grouper.add_frame.call_args
        assert call.args[1] == DEVICE_ID   # second positional: device_id

    def test_captured_at_header_forwarded(self, mock_grouper, dummy_jpeg):
        c = build_client(grouper=mock_grouper)
        _post_frame(c, dummy_jpeg, extra_headers={"X-Captured-At": "1000"})
        call = mock_grouper.add_frame.call_args
        assert call.args[4] == 1000

    def test_different_device_ids_accepted(self, mock_grouper, dummy_jpeg):
        c = build_client(grouper=mock_grouper)
        for dev in ("CAM-front", "CAM-rear", "CAM-mid"):
            r = _post_frame(c, dummy_jpeg, device_id=dev)
            assert r.status_code == 200


# ── Missing required fields ───────────────────────────────────────────────────

class TestMissingFields:
    def test_missing_device_id_returns_422(self, client, dummy_jpeg):
        r = client.post(
            "/api/v1/upload",
            files={"file": ("frame.jpg", io.BytesIO(dummy_jpeg), "image/jpeg")},
            # no data= means no device_id form field
        )
        assert r.status_code == 422


# ── Image validation ──────────────────────────────────────────────────────────

class TestImageValidation:
    def test_wrong_content_type_returns_415(self, client, dummy_jpeg):
        assert _post_frame(client, dummy_jpeg, content_type="image/png").status_code == 415

    def test_corrupt_jpeg_returns_422(self, client):
        assert _post_frame(client, b"not_a_jpeg").status_code == 422

    def test_empty_body_returns_422(self, client):
        assert _post_frame(client, b"").status_code == 422


# ── Health ──────────────────────────────────────────────────────────────────

class TestInfoEndpoints:
    def test_health_ok(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── Various resolutions ───────────────────────────────────────────────────────

class TestResolutions:
    @pytest.mark.parametrize("w,h", [
        (160, 120), (320, 240), (640, 480), (800, 600), (1024, 768),
    ])
    def test_resolution(self, w, h, mock_grouper):
        c = build_client(grouper=mock_grouper)
        assert _post_frame(c, make_jpeg(width=w, height=h)).status_code == 200
