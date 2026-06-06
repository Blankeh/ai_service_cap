import io
import pytest
import numpy as np
import cv2
from unittest.mock import AsyncMock

from src.main import app
from src.repos.database import init_engine
from src.repos.queue_repo import QueueRepo
from src.services.grouper_service import FrameGrouper
from src.services.image_service import ImageService


# ── Constants ──────────────────────────────────────────────────────────────────

DEVICE_ID = "CAM-front"
BUS_ID    = "1"
BUCKET    = 1000


# ── Image helpers ──────────────────────────────────────────────────────────────

def make_jpeg(width: int = 320, height: int = 240, color: tuple = (120, 80, 60)) -> bytes:
    img   = np.full((height, width, 3), color, dtype=np.uint8)
    noise = np.random.randint(0, 30, img.shape, dtype=np.uint8)
    img   = cv2.add(img, noise)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()


def make_jpeg_bytes_io(**kwargs) -> io.BytesIO:
    return io.BytesIO(make_jpeg(**kwargs))


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def image_svc() -> ImageService:
    return ImageService(target_size=640)


@pytest.fixture(autouse=True)
def test_db(tmp_path):
    init_engine(f"sqlite:///{tmp_path}/test.db")


@pytest.fixture()
def dummy_jpeg() -> bytes:
    return make_jpeg()


@pytest.fixture()
def mock_queue_repo() -> QueueRepo:
    return QueueRepo()


@pytest.fixture()
def mock_grouper() -> AsyncMock:
    svc = AsyncMock(spec=FrameGrouper)
    svc.add_frame.return_value = {
        "bus_id":    BUS_ID,
        "bucket":    BUCKET,
        "device_id": DEVICE_ID,
        "received":  [DEVICE_ID],
    }
    return svc


# ── TestClient factory ─────────────────────────────────────────────────────────

def build_client(queue_repo=None, grouper=None):
    from fastapi.testclient import TestClient
    app.state.queue_repo = queue_repo
    app.state.grouper    = grouper
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def client(mock_queue_repo, mock_grouper):
    return build_client(queue_repo=mock_queue_repo, grouper=mock_grouper)
