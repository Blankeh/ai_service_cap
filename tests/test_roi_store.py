import json
from pathlib import Path

import pytest

from src.core.config import settings
from src.services import roi_store


@pytest.fixture()
def store_path(tmp_path) -> Path:
    return tmp_path / "camera_rois.json"


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    # Each test starts from a known in-memory ROI set; never touch the real file.
    monkeypatch.setattr(settings, "camera_rois", {})
    yield


class TestLoad:
    def test_missing_file_seeds_from_env_and_creates_file(self, store_path):
        settings.camera_rois.update({"front": [0.0, 0.0, 1.0, 0.5]})
        effective = roi_store.load(store_path)
        assert effective == {"front": [0.0, 0.0, 1.0, 0.5]}
        assert store_path.exists()
        assert json.loads(store_path.read_text()) == {"front": [0.0, 0.0, 1.0, 0.5]}

    def test_missing_file_with_no_env_writes_empty(self, store_path):
        effective = roi_store.load(store_path)
        assert effective == {}
        assert json.loads(store_path.read_text()) == {}

    def test_valid_file_overrides_env_wholesale(self, store_path):
        settings.camera_rois.update({"front": [0.0, 0.0, 1.0, 0.5]})  # .env value
        store_path.write_text(json.dumps({"rear": [0.0, 0.5, 1.0, 1.0]}))
        effective = roi_store.load(store_path)
        # front (from .env) is gone — file wins wholesale, no merge.
        assert effective == {"rear": [0.0, 0.5, 1.0, 1.0]}
        assert settings.camera_rois == {"rear": [0.0, 0.5, 1.0, 1.0]}

    def test_corrupt_file_keeps_env_and_leaves_file(self, store_path):
        settings.camera_rois.update({"front": [0.0, 0.0, 1.0, 0.5]})
        store_path.write_text("{ not json ]")
        effective = roi_store.load(store_path)
        assert effective == {"front": [0.0, 0.0, 1.0, 0.5]}     # fell back to .env
        assert store_path.read_text() == "{ not json ]"          # untouched

    def test_malformed_pane_in_file_is_dropped(self, store_path):
        store_path.write_text(json.dumps({
            "good": [0.0, 0.0, 1.0, 0.5],
            "bad":  [0.5, 0.0, 0.4, 1.0],   # x1 > x2
        }))
        effective = roi_store.load(store_path)
        assert "good" in effective and "bad" not in effective


class TestSetDeletePane:
    def test_set_pane_persists_and_mutates_settings_in_place(self, store_path):
        original = settings.camera_rois
        rois = roi_store.set_pane("front", [0.0, 0.0, 1.0, 0.5], store_path)
        assert rois == {"front": [0.0, 0.0, 1.0, 0.5]}
        assert json.loads(store_path.read_text()) == {"front": [0.0, 0.0, 1.0, 0.5]}
        # same dict object (resolve_roi holds a reference to it)
        assert settings.camera_rois is original
        assert settings.camera_rois["front"] == [0.0, 0.0, 1.0, 0.5]

    def test_set_pane_rejects_invalid_box(self, store_path):
        with pytest.raises(ValueError):
            roi_store.set_pane("front", [0.5, 0.0, 0.4, 1.0], store_path)  # inverted
        assert "front" not in settings.camera_rois
        assert not store_path.exists()

    def test_set_pane_merges_without_clobbering_others(self, store_path):
        roi_store.set_pane("front", [0.0, 0.0, 1.0, 0.5], store_path)
        roi_store.set_pane("rear", [0.0, 0.5, 1.0, 1.0], store_path)
        assert set(settings.camera_rois) == {"front", "rear"}

    def test_delete_pane_reverts_to_full_frame(self, store_path):
        roi_store.set_pane("front", [0.0, 0.0, 1.0, 0.5], store_path)
        rois = roi_store.delete_pane("front", store_path)
        assert rois == {}
        assert json.loads(store_path.read_text()) == {}

    def test_delete_absent_pane_is_idempotent(self, store_path):
        rois = roi_store.delete_pane("nope", store_path)
        assert rois == {}

    def test_concurrent_writes_do_not_lose_updates(self, store_path):
        import threading
        roi_store.load(store_path)  # create the file
        panes = [f"p{i}" for i in range(12)]

        def worker(name):
            roi_store.set_pane(name, [0.0, 0.0, 1.0, 0.5], store_path)

        threads = [threading.Thread(target=worker, args=(p,)) for p in panes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        on_disk = json.loads(store_path.read_text())
        assert set(on_disk) == set(panes)
        assert set(settings.camera_rois) == set(panes)
