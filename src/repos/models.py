from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Camera(SQLModel, table=True):
    __tablename__ = "cameras"

    camera_id:     str           = Field(primary_key=True)
    bus_id:        Optional[str] = Field(default=None)
    pane:          Optional[str] = Field(default=None)
    token:         Optional[str] = Field(default=None, unique=True)
    registered_at: str           = Field(default_factory=lambda: datetime.utcnow().isoformat())
    last_seen:     str           = Field(default_factory=lambda: datetime.utcnow().isoformat())


class FailedSend(SQLModel, table=True):
    """One record = one grouped bus snapshot that failed to reach Cloudflare."""
    __tablename__ = "failed_sends"

    id:            Optional[int] = Field(default=None, primary_key=True)
    bus_id:        str
    group_id:      str           # e.g. "BUS-001_2026-05-14T20:44:00"
    payload:       str           # full JSON string (grouped pane counts)
    retry_count:   int           = Field(default=0)
    next_retry_at: str
    created_at:    str           = Field(default_factory=lambda: datetime.utcnow().isoformat())
