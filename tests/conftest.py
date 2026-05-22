import io
import pytest
import numpy as np
import cv2
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.main import app
from src.repos.database import init_engine
from src.repos.camera_repo import CameraRepo
from src.repos.queue_repo import QueueRepo
from src.services.camera_service import CameraService
from src.services.cloudflare_service import CloudflareService
from src.services.grouper_service import FrameGrouper
from src.services.image_service import ImageService
from src.services.inference_service import InferenceService


@pytest.fixture()
def image_svc() -> ImageService:
    return ImageService(target_size=640)


# ── Test DB setup ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def test_db(tmp_path):
    init_engine(f"sqlite:///{tmp_path}/test.db")


# ── Dummy image factory ────────────────────────────────────────────────────────

def make_jpeg(width: int = 320, height: int = 240, color: tuple = (120, 80, 60)) -> bytes:
    img = np.full((height, width, 3), color, dtype=np.uint8)
    noise = np.random.randint(0, 30, img.shape, dtype=np.uint8)
    img = cv2.add(img, noise)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()


def make_jpeg_bytes_io(**kwargs) -> io.BytesIO:
    return io.BytesIO(make_jpeg(**kwargs))


# ── Constants ──────────────────────────────────────────────────────────────────

CAMERA_ID = "AA:BB:CC:DD:EE:FF"
BUS_ID    = "BUS-001"
PANE      = "front"
TOKEN     = "test-token-abc123"
BUCKET    = 1000


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def dummy_jpeg() -> bytes:
    return make_jpeg()


@pytest.fixture()
def mock_queue_repo() -> QueueRepo:
    return QueueRepo()


@pytest.fixture()
def mock_camera_svc() -> MagicMock:
    svc = MagicMock(spec=CameraService)
    svc.handshake.return_value = {
        "token": TOKEN,
        "camera_id": CAMERA_ID,
        "bus_id": BUS_ID,
        "pane": PANE,
        "assigned": True,
    }
    svc.resolve.return_value = {
        "camera_id": CAMERA_ID,
        "bus_id": BUS_ID,
        "pane": PANE,
    }
    svc.list_cameras.return_value = []
    return svc


@pytest.fixture()
def mock_grouper() -> AsyncMock:
    svc = AsyncMock(spec=FrameGrouper)
    svc.add_frame.return_value = {
        "bus_id":   BUS_ID,
        "bucket":   BUCKET,
        "pane":     PANE,
        "received": [PANE],
        "expected": [PANE],
        "grouped":  True,
    }
    return svc


# ── TestClient factory ────────────────────────────────────────────────────────

def build_client(
    camera_svc=None,
    queue_repo=None,
    grouper=None,
) -> TestClient:
    app.state.camera_svc = camera_svc
    app.state.queue_repo = queue_repo
    app.state.grouper    = grouper
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def client(mock_queue_repo, mock_camera_svc, mock_grouper):
    return build_client(
        camera_svc=mock_camera_svc,
        queue_repo=mock_queue_repo,
        grouper=mock_grouper,
    )


@pytest.fixture()
def client_no_grouper(mock_queue_repo, mock_camera_svc):
    """Client where grouper.add_frame raises — upload must still return 200."""
    grouper = AsyncMock(spec=FrameGrouper)
    grouper.add_frame.side_effect = Exception("Grouper unavailable")
    return build_client(
        camera_svc=mock_camera_svc,
        queue_repo=mock_queue_repo,
        grouper=grouper,
    )
