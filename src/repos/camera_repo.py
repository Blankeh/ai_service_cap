import secrets
from datetime import datetime
from typing import Optional

from sqlmodel import select

from .database import get_session
from .models import Camera


class CameraRepo:
    def register_or_refresh(self, camera_id: str) -> Camera:
        """
        Insert if new, otherwise rotate the token and update last_seen.
        Returns the updated Camera row.
        """
        now   = datetime.utcnow().isoformat()
        token = secrets.token_hex(32)

        with get_session() as session:
            camera = session.get(Camera, camera_id)
            if camera:
                camera.token     = token
                camera.last_seen = now
            else:
                camera = Camera(
                    camera_id=camera_id,
                    token=token,
                    registered_at=now,
                    last_seen=now,
                )
                session.add(camera)
            session.commit()
            session.refresh(camera)
            return camera

    def get_by_token(self, token: str) -> Optional[Camera]:
        with get_session() as session:
            return session.exec(
                select(Camera).where(Camera.token == token)
            ).first()

    def get_by_camera_id(self, camera_id: str) -> Optional[Camera]:
        with get_session() as session:
            return session.get(Camera, camera_id)

    def touch(self, camera_id: str):
        with get_session() as session:
            camera = session.get(Camera, camera_id)
            if camera:
                camera.last_seen = datetime.utcnow().isoformat()
                session.commit()

    def assign(self, camera_id: str, bus_id: str, pane: str) -> bool:
        with get_session() as session:
            camera = session.get(Camera, camera_id)
            if not camera:
                return False
            camera.bus_id = bus_id
            camera.pane   = pane
            session.commit()
            return True

    def list_all(self) -> list[Camera]:
        with get_session() as session:
            return session.exec(
                select(Camera).order_by(Camera.registered_at)
            ).all()
