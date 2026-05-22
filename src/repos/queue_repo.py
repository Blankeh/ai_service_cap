import json
from datetime import datetime

from sqlmodel import select

from .database import get_session
from .models import FailedSend


class QueueRepo:
    def enqueue(self, bus_id: str, group_id: str, payload: dict):
        now = datetime.utcnow().isoformat()
        with get_session() as session:
            session.add(FailedSend(
                bus_id=bus_id,
                group_id=group_id,
                payload=json.dumps(payload),
                next_retry_at=now,
                created_at=now,
            ))
            session.commit()

    def get_due(self) -> list[FailedSend]:
        now = datetime.utcnow().isoformat()
        with get_session() as session:
            return session.exec(
                select(FailedSend)
                .where(FailedSend.next_retry_at <= now)
                .order_by(FailedSend.created_at)
            ).all()

    def increment_retry(self, record_id: int, next_retry_at: str):
        with get_session() as session:
            record = session.get(FailedSend, record_id)
            if record:
                record.retry_count   += 1
                record.next_retry_at  = next_retry_at
                session.commit()

    def delete(self, record_id: int):
        with get_session() as session:
            record = session.get(FailedSend, record_id)
            if record:
                session.delete(record)
                session.commit()

    def count(self) -> int:
        with get_session() as session:
            return len(session.exec(select(FailedSend)).all())
