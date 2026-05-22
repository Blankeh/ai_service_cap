import json
from datetime import datetime, timedelta

import pytest

from src.repos.queue_repo import QueueRepo

GROUP_ID = "BUS-001_2024-01-01T00:00:00"
PAYLOAD  = {"bus_id": "BUS-001", "group_id": GROUP_ID, "crowd_count": 5}


@pytest.fixture()
def repo() -> QueueRepo:
    return QueueRepo()


class TestQueueRepo:
    def test_enqueue_increases_count(self, repo):
        assert repo.count() == 0
        repo.enqueue("BUS-001", GROUP_ID, PAYLOAD)
        assert repo.count() == 1

    def test_get_due_returns_record(self, repo):
        repo.enqueue("BUS-001", GROUP_ID, PAYLOAD)
        due = repo.get_due()
        assert len(due) == 1
        assert due[0].bus_id == "BUS-001"
        assert due[0].group_id == GROUP_ID

    def test_delete_removes_record(self, repo):
        repo.enqueue("BUS-001", GROUP_ID, PAYLOAD)
        record_id = repo.get_due()[0].id
        repo.delete(record_id)
        assert repo.count() == 0

    def test_increment_retry_defers_record(self, repo):
        repo.enqueue("BUS-001", GROUP_ID, PAYLOAD)
        record = repo.get_due()[0]
        future = (datetime.utcnow() + timedelta(minutes=1)).isoformat()
        repo.increment_retry(record.id, future)
        assert len(repo.get_due()) == 0

    def test_increment_retry_eventually_due_again(self, repo):
        repo.enqueue("BUS-001", GROUP_ID, PAYLOAD)
        record = repo.get_due()[0]
        past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
        repo.increment_retry(record.id, past)
        due = repo.get_due()
        assert len(due) == 1
        assert due[0].retry_count == 1

    def test_payload_stored_as_json(self, repo):
        repo.enqueue("BUS-001", GROUP_ID, PAYLOAD)
        record = repo.get_due()[0]
        parsed = json.loads(record.payload)
        assert parsed["bus_id"] == "BUS-001"
        assert parsed["crowd_count"] == 5

    def test_multiple_records_ordered_by_created_at(self, repo):
        for i in range(3):
            gid = f"BUS-00{i}_2024-01-01T00:00:00"
            repo.enqueue(f"BUS-00{i}", gid, {"bus_id": f"BUS-00{i}"})
        due = repo.get_due()
        assert [r.bus_id for r in due] == ["BUS-000", "BUS-001", "BUS-002"]
