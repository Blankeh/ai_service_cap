from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class Camera(SQLModel, table=True):
    __tablename__ = "cameras"

    camera_id:     str           = Field(primary_key=True)
    bus_id:        Optional[str] = Field(default=None)
    pane:          Optional[str] = Field(default=None)
    registered_at: str           = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen:     str           = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
