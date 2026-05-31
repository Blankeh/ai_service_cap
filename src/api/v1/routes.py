from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from ...core.config import settings
from ...repos.queue_repo import QueueRepo
from ...services.grouper_service import FrameGrouper

router = APIRouter()


@router.post("/upload")
async def upload_frame(
    request:     Request,
    file:        UploadFile = File(...),
    camera_id:   str        = Header(..., alias="X-Camera-Id"),
    bus_id:      str        = Header(..., alias="X-Bus-Id"),
    pane:        str        = Header(..., alias="X-Pane"),
    captured_at: Optional[int] = Header(None, alias="X-Captured-At"),
):
    """
    Receive a JPEG frame from an ESP32-CAM.

    Headers:
        X-Camera-Id   : unique camera identifier (pre-configured on device)
        X-Bus-Id      : bus this camera is mounted on (pre-configured on device)
        X-Pane        : position on the bus — e.g. "front" or "rear"
        X-Captured-At : Unix timestamp (seconds) from NTP — used to group
                        simultaneous frames from the same bus together.
                        Falls back to server receive time if omitted.
    Body:
        file (multipart/form-data) : JPEG image
    """
    if file.content_type not in ("image/jpeg", "image/jpg"):
        raise HTTPException(status_code=415, detail="Only JPEG images are accepted")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=422, detail="Empty file")
    if not raw_bytes.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=422, detail="Invalid JPEG data")

    timestamp = (
        datetime.fromtimestamp(captured_at, tz=timezone.utc).isoformat()
        if captured_at
        else datetime.now(timezone.utc).isoformat()
    )

    grouper: FrameGrouper = request.app.state.grouper
    result = await grouper.add_frame(bus_id, pane, raw_bytes, timestamp, captured_at)

    return JSONResponse({"camera_id": camera_id, **result, "timestamp": timestamp})


@router.get("/health")
async def health(request: Request):
    queue_repo: QueueRepo = request.app.state.queue_repo
    return {
        "status":         "ok",
        "queued_records": queue_repo.count(),
        "model":          settings.model_path,
    }


@router.get("/queue/status")
async def queue_status(request: Request):
    queue_repo: QueueRepo = request.app.state.queue_repo
    return {
        "pending_records":        queue_repo.count(),
        "max_retries":            settings.max_retry_attempts,
        "retry_interval_seconds": settings.retry_interval_seconds,
    }
