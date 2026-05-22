from fastapi import HTTPException

from ..repos.camera_repo import CameraRepo


class CameraService:
    def __init__(self, camera_repo: CameraRepo):
        self.repo = camera_repo

    def handshake(self, camera_id: str) -> dict:
        camera = self.repo.register_or_refresh(camera_id)
        return {
            "token": camera.token,
            "camera_id": camera.camera_id,
            "bus_id": camera.bus_id,
            "pane": camera.pane,
            "assigned": camera.bus_id is not None,
        }

    def resolve(self, token: str) -> dict:
        camera = self.repo.get_by_token(token)
        if camera is None:
            raise HTTPException(status_code=401, detail="Unknown camera token — run handshake first")

        if camera.bus_id is None or camera.pane is None:
            raise HTTPException(
                status_code=409,
                detail=f"Camera {camera.camera_id} is not yet assigned to a bus/pane"
            )

        self.repo.touch(camera.camera_id)
        return {
            "camera_id": camera.camera_id,
            "bus_id": camera.bus_id,
            "pane": camera.pane,
        }

    def assign(self, camera_id: str, bus_id: str, pane: str):
        if not self.repo.assign(camera_id, bus_id, pane):
            raise HTTPException(status_code=404, detail=f"Camera {camera_id!r} not registered")

    def list_cameras(self) -> list[dict]:
        return [
            {
                "camera_id": r.camera_id,
                "bus_id": r.bus_id,
                "pane": r.pane,
                "assigned": r.bus_id is not None,
                "registered_at": r.registered_at,
                "last_seen": r.last_seen,
            }
            for r in self.repo.list_all()
        ]
