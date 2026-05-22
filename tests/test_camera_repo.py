import pytest
from src.repos.camera_repo import CameraRepo

CAM = "AA:BB:CC:DD:EE:FF"


@pytest.fixture()
def repo() -> CameraRepo:
    return CameraRepo()


class TestCameraRepo:
    def test_register_new_camera(self, repo):
        row = repo.register_or_refresh(CAM)
        assert row.camera_id == CAM
        assert row.token is not None
        assert row.bus_id is None
        assert row.pane is None

    def test_refresh_rotates_token(self, repo):
        first  = repo.register_or_refresh(CAM).token
        second = repo.register_or_refresh(CAM).token
        assert first != second

    def test_refresh_does_not_duplicate(self, repo):
        repo.register_or_refresh(CAM)
        repo.register_or_refresh(CAM)
        assert len(repo.list_all()) == 1

    def test_get_by_token(self, repo):
        token = repo.register_or_refresh(CAM).token
        row = repo.get_by_token(token)
        assert row is not None
        assert row.camera_id == CAM

    def test_get_by_token_unknown_returns_none(self, repo):
        assert repo.get_by_token("no-such-token") is None

    def test_get_by_camera_id(self, repo):
        repo.register_or_refresh(CAM)
        row = repo.get_by_camera_id(CAM)
        assert row is not None

    def test_assign_sets_bus_and_pane(self, repo):
        repo.register_or_refresh(CAM)
        result = repo.assign(CAM, "BUS-001", "front")
        assert result is True
        row = repo.get_by_camera_id(CAM)
        assert row.bus_id == "BUS-001"
        assert row.pane == "front"

    def test_assign_unknown_camera_returns_false(self, repo):
        assert repo.assign("FF:FF:FF:FF:FF:FF", "BUS-001", "front") is False

    def test_touch_updates_last_seen(self, repo):
        repo.register_or_refresh(CAM)
        before = repo.get_by_camera_id(CAM).last_seen
        import time; time.sleep(0.01)
        repo.touch(CAM)
        after = repo.get_by_camera_id(CAM).last_seen
        assert after >= before

    def test_list_all_returns_all_cameras(self, repo):
        for i in range(3):
            repo.register_or_refresh(f"AA:BB:CC:DD:EE:0{i}")
        assert len(repo.list_all()) == 3
