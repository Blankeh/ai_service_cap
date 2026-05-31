import pytest
from src.repos.camera_repo import CameraRepo

CAM = "ESP32-BUS001-FRONT"


@pytest.fixture()
def repo() -> CameraRepo:
    return CameraRepo()


class TestCameraRepo:
    def test_register_new_camera(self, repo):
        row = repo.register_or_touch(CAM)
        assert row.camera_id == CAM
        assert row.bus_id is None
        assert row.pane is None

    def test_register_does_not_duplicate(self, repo):
        repo.register_or_touch(CAM)
        repo.register_or_touch(CAM)
        assert len(repo.list_all()) == 1

    def test_get_by_camera_id(self, repo):
        repo.register_or_touch(CAM)
        assert repo.get_by_camera_id(CAM) is not None

    def test_get_unknown_camera_returns_none(self, repo):
        assert repo.get_by_camera_id("UNKNOWN") is None

    def test_assign_sets_bus_and_pane(self, repo):
        repo.register_or_touch(CAM)
        assert repo.assign(CAM, "BUS-001", "front") is True
        row = repo.get_by_camera_id(CAM)
        assert row.bus_id == "BUS-001"
        assert row.pane   == "front"

    def test_assign_unknown_returns_false(self, repo):
        assert repo.assign("UNKNOWN", "BUS-001", "front") is False

    def test_touch_updates_last_seen(self, repo):
        repo.register_or_touch(CAM)
        before = repo.get_by_camera_id(CAM).last_seen
        import time; time.sleep(0.01)
        repo.touch(CAM)
        assert repo.get_by_camera_id(CAM).last_seen >= before

    def test_list_all(self, repo):
        for i in range(3):
            repo.register_or_touch(f"ESP32-BUS00{i}-FRONT")
        assert len(repo.list_all()) == 3
