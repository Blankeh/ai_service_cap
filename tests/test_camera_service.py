import pytest
from fastapi import HTTPException

from src.repos.camera_repo import CameraRepo
from src.services.camera_service import CameraService

CAM = "AA:BB:CC:DD:EE:FF"


@pytest.fixture()
def repo() -> CameraRepo:
    return CameraRepo()


@pytest.fixture()
def svc(repo) -> CameraService:
    return CameraService(camera_repo=repo)


class TestHandshake:
    def test_new_camera_gets_token(self, svc):
        result = svc.handshake(CAM)
        assert result["token"] is not None
        assert result["camera_id"] == CAM

    def test_new_camera_is_not_assigned(self, svc):
        result = svc.handshake(CAM)
        assert result["assigned"] is False
        assert result["bus_id"] is None
        assert result["pane"] is None

    def test_repeat_handshake_rotates_token(self, svc):
        t1 = svc.handshake(CAM)["token"]
        t2 = svc.handshake(CAM)["token"]
        assert t1 != t2

    def test_assigned_camera_returns_bus_and_pane(self, svc, repo):
        repo.register_or_refresh(CAM)
        repo.assign(CAM, "BUS-001", "rear")
        result = svc.handshake(CAM)
        assert result["assigned"] is True
        assert result["bus_id"] == "BUS-001"
        assert result["pane"] == "rear"


class TestResolve:
    def test_valid_token_resolves_identity(self, svc, repo):
        token = repo.register_or_refresh(CAM).token
        repo.assign(CAM, "BUS-001", "front")
        identity = svc.resolve(token)
        assert identity["camera_id"] == CAM
        assert identity["bus_id"] == "BUS-001"
        assert identity["pane"] == "front"

    def test_unknown_token_raises_401(self, svc):
        with pytest.raises(HTTPException) as exc_info:
            svc.resolve("bad-token")
        assert exc_info.value.status_code == 401

    def test_unassigned_camera_raises_409(self, svc, repo):
        token = repo.register_or_refresh(CAM).token
        with pytest.raises(HTTPException) as exc_info:
            svc.resolve(token)
        assert exc_info.value.status_code == 409


class TestAssign:
    def test_assign_registered_camera(self, svc, repo):
        repo.register_or_refresh(CAM)
        svc.assign(CAM, "BUS-002", "left")
        row = repo.get_by_camera_id(CAM)
        assert row.bus_id == "BUS-002"
        assert row.pane == "left"

    def test_assign_unknown_camera_raises_404(self, svc):
        with pytest.raises(HTTPException) as exc_info:
            svc.assign("FF:FF:FF:FF:FF:FF", "BUS-001", "front")
        assert exc_info.value.status_code == 404
