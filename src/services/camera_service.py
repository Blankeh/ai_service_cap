import logging

from fastapi import HTTPException

from ..repos.camera_repo import CameraRepo

logger = logging.getLogger(__name__)


class CameraService:
    def __init__(self, camera_repo: CameraRepo):
        self.repo = camera_repo

    def handshake(self, camera_id: str) -> dict:
        """Register (or refresh) camera. Returns assignment status."""
        camera = self.repo.register_or_touch(camera_id)
        logger.info(
            "Handshake: camera_id=%s  assigned=%s  bus_id=%s  pane=%s",
            camera.camera_id, camera.bus_id is not None, camera.bus_id, camera.pane,
        )
        return {
            "camera_id": camera.camera_id,
            "bus_id":    camera.bus_id,
            "pane":      camera.pane,
            "assigned":  camera.bus_id is not None,
        }

    def resolve(self, camera_id: str) -> dict:
        """Look up camera by ID and return its bus/pane assignment."""
        camera = self.repo.get_by_camera_id(camera_id)
        if camera is None:
            logger.warning("resolve: unknown camera_id=%r — call /handshake first", camera_id)
            raise HTTPException(status_code=404, detail="Unknown camera — call /handshake first")

        if camera.bus_id is None or camera.pane is None:
            logger.warning(
                "resolve: camera_id=%s not yet assigned (bus_id=%s pane=%s)",
                camera.camera_id, camera.bus_id, camera.pane,
            )
            raise HTTPException(
                status_code=409,
                detail=f"Camera {camera.camera_id} is not yet assigned to a bus/pane",
            )

        self.repo.touch(camera.camera_id)
        logger.debug("resolve: camera_id=%s  bus_id=%s  pane=%s",
                     camera.camera_id, camera.bus_id, camera.pane)
        return {
            "camera_id": camera.camera_id,
            "bus_id":    camera.bus_id,
            "pane":      camera.pane,
        }

    def assign(self, camera_id: str, bus_id: str, pane: str) -> None:
        if not self.repo.assign(camera_id, bus_id, pane):
            logger.warning("assign: camera_id=%r not found", camera_id)
            raise HTTPException(status_code=404, detail=f"Camera {camera_id!r} not registered")
        logger.info("assign: camera_id=%s → bus_id=%s  pane=%s", camera_id, bus_id, pane)

    def list_cameras(self) -> list[dict]:
        return [
            {
                "camera_id":    r.camera_id,
                "bus_id":       r.bus_id,
                "pane":         r.pane,
                "assigned":     r.bus_id is not None,
                "registered_at": r.registered_at,
                "last_seen":    r.last_seen,
            }
            for r in self.repo.list_all()
        ]
