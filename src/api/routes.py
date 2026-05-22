from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core.config import settings
from ..repos.queue_repo import QueueRepo
from ..repos.camera_repo import CameraRepo
from ..services.camera_service import CameraService
from ..services.grouper_service import FrameGrouper

router = APIRouter()


# ── Request / response schemas ────────────────────────────────────────────────

class HandshakeRequest(BaseModel):
    camera_id: str

class AssignRequest(BaseModel):
    bus_id: str
    pane: str


# ── Handshake ─────────────────────────────────────────────────────────────────

@router.post("/handshake")
async def handshake(body: HandshakeRequest, request: Request):
    """
    Called once by ESP32-CAM on boot.
    Registers (or refreshes) the camera and returns a session token.

    The token must be sent as the X-Camera-Token header on every /upload call.
    bus_id and pane are null until an admin assigns them via /cameras/{id}/assign.
    """
    camera_svc: CameraService = request.app.state.camera_svc
    return camera_svc.handshake(body.camera_id)


# ── Image upload ──────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_frame(
    request: Request,
    file: UploadFile = File(...),
    camera_token: str = Header(..., alias="X-Camera-Token"),
    captured_at: Optional[int] = Header(None, alias="X-Captured-At"),
):
    """
    Receive a JPEG frame from ESP32-CAM.

    Headers:
        X-Camera-Token  : token returned by /handshake
        X-Captured-At   : (optional) Unix timestamp when the frame was captured (from NTP).
                          Used to group simultaneous frames from the same bus together.
                          Falls back to server time if omitted.
    """
    if file.content_type not in ("image/jpeg", "image/jpg"):
        raise HTTPException(status_code=415, detail="Only JPEG images are accepted")

    camera_svc: CameraService = request.app.state.camera_svc
    identity  = camera_svc.resolve(camera_token)
    camera_id = identity["camera_id"]
    bus_id    = identity["bus_id"]
    pane      = identity["pane"]

    if captured_at:
        timestamp = datetime.fromtimestamp(captured_at, tz=timezone.utc).isoformat()
    else:
        timestamp = datetime.now(timezone.utc).isoformat()

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=422, detail="Empty file")
    if not raw_bytes.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=422, detail="Invalid JPEG data")

    grouper: FrameGrouper = request.app.state.grouper

    # Hand raw bytes to the grouper — it stitches all panes and runs YOLO once
    result = await grouper.add_frame(bus_id, pane, raw_bytes, timestamp, captured_at)

    return JSONResponse({
        "camera_id": camera_id,
        **result,
        "timestamp": timestamp,
    })


# ── Admin: camera management ──────────────────────────────────────────────────

@router.get("/cameras")
async def list_cameras(request: Request):
    """List all registered cameras and their bus/pane assignments."""
    camera_svc: CameraService = request.app.state.camera_svc
    return camera_svc.list_cameras()


@router.post("/cameras/{camera_id}/assign")
async def assign_camera(camera_id: str, body: AssignRequest, request: Request):
    """Assign a registered camera to a bus and pane."""
    camera_svc: CameraService = request.app.state.camera_svc
    camera_svc.assign(camera_id, body.bus_id, body.pane)
    return {"camera_id": camera_id, "bus_id": body.bus_id, "pane": body.pane}


# ── Health / queue ────────────────────────────────────────────────────────────

@router.get("/health")
async def health(request: Request):
    queue_repo: QueueRepo = request.app.state.queue_repo
    return {
        "status": "ok",
        "queued_records": queue_repo.count(),
        "model": settings.model_path,
    }


@router.get("/queue/status")
async def queue_status(request: Request):
    queue_repo: QueueRepo = request.app.state.queue_repo
    return {
        "pending_records": queue_repo.count(),
        "max_retries": settings.max_retry_attempts,
        "retry_interval_seconds": settings.retry_interval_seconds,
    }

