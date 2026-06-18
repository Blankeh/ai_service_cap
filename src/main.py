import asyncio
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .core.logging import setup_logging
setup_logging()

from .api.router import api_router
from .core.config import settings
from .services.aggregator_service import AggregatorService
from .services.auth_service import AuthService
from .services.camera_sync_service import CameraSyncService
from .services.cloudflare_service import CloudflareService
from .services.grouper_service import FrameGrouper
from .services.image_service import ImageService
from .services.inference_service import InferenceService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup 
    image_svc     = ImageService(target_size=settings.yolo_input_size)
    inference_svc = InferenceService(
        model_path=settings.model_path,
        confidence_threshold=settings.confidence_threshold,
    )
    # Device auth — only when a backend URL is configured (empty URL = dummy mode,
    # nothing to log in to). The login loop keeps requesting a token at startup.
    auth_svc = AuthService() if settings.cloudflare_api_url else None
    cloudflare_svc = CloudflareService(auth_svc=auth_svc)

    aggregator_svc = AggregatorService(
        cloudflare_svc=cloudflare_svc,
        flush_interval=settings.aggregator_flush_interval,
        spike_threshold=settings.aggregator_spike_threshold,
    )

    # DEV-only live detection viewer — never imported/started in prod.
    dev_viewer = None
    if settings.app_env == "dev":
        from .services.dev_viewer import DevViewer
        dev_viewer = DevViewer()
        logger.info(
            "DEV mode — live detection viewer at http://%s:%d/dev",
            settings.host, settings.port,
        )
    app.state.dev_viewer = dev_viewer

    # ROI snapshot store — always on (dev AND prod) so the /roi calibration editor
    # can run against the deployed Pi. Holds the latest original frame per camera.
    from .services.snapshot_store import SnapshotStore
    snapshot_store = SnapshotStore()
    app.state.snapshot_store = snapshot_store

    # Persisted ROIs (data/camera_rois.json) override CAMERA_ROIS from .env and
    # are honored in prod too. Load before validating so we check the effective set.
    from .services import roi_store
    roi_store.load()

    # Surface ROI config in the journal so misconfig is visible, not silent.
    from .services.roi_service import validate_rois
    if settings.camera_rois:
        logger.info("Camera ROIs configured: %s", settings.camera_rois)
        roi_problems = validate_rois()
        for problem in roi_problems:
            logger.error("Malformed ROI — %s", problem)
        if (
            settings.expected_cameras
            and len(settings.camera_rois) != settings.expected_cameras
        ):
            logger.warning(
                "EXPECTED_CAMERAS=%d but %d ROI pane(s) configured (%s) — "
                "cameras without an ROI will count the full frame",
                settings.expected_cameras, len(settings.camera_rois),
                sorted(settings.camera_rois),
            )
    else:
        logger.info("No CAMERA_ROIS set — every camera counts the full frame")

    grouper = FrameGrouper(
        image_svc=image_svc,
        inference_svc=inference_svc,
        aggregator_svc=aggregator_svc,
        group_window_ms=settings.group_window_ms,
        bucket_size=settings.group_bucket_size,
        dev_viewer=dev_viewer,
        snapshot_store=snapshot_store,
        expected_cameras=settings.expected_cameras,
    )

    camera_sync_svc = CameraSyncService()

    app.state.image_svc      = image_svc
    app.state.inference_svc  = inference_svc
    app.state.cloudflare_svc = cloudflare_svc
    app.state.auth_svc       = auth_svc
    app.state.grouper        = grouper

    auth_login_task = (
        asyncio.create_task(auth_svc.run_login_loop()) if auth_svc is not None else None
    )
    aggregator_task = asyncio.create_task(aggregator_svc.run_loop())
    camera_sync_task = asyncio.create_task(camera_sync_svc.run_loop())

    logger.info("AI service started")
    yield

    # Shutdown
    if auth_login_task is not None:
        auth_login_task.cancel()
    aggregator_task.cancel()
    camera_sync_task.cancel()
    logger.info("AI service stopped")


app = FastAPI(title="Bus Crowd AI Service", version="1.0.0", lifespan=lifespan)

# Allow-everything CORS (browsers only — does not affect curl/ESP32/TCP reachability)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")

# ROI calibration editor — available in dev AND prod so ROIs can be set up on the
# deployed Pi. Writes config (unauthenticated, LAN-trust assumption — see roi_routes).
from .api.roi_routes import roi_router
app.include_router(roi_router, prefix="/roi")

# DEV-only live detection viewer (MJPEG) — mounted only when APP_ENV=dev.
if settings.app_env == "dev":
    from .api.dev_routes import dev_router
    app.include_router(dev_router, prefix="/dev")


@app.middleware("http")
async def _log_requests(request: Request, call_next) -> Response:
    start = time.perf_counter()
    logger.info("→ %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        logger.exception("✗ %s %s  [%.1f ms] — unhandled exception: %s",
                         request.method, request.url.path, elapsed, exc)
        raise
    elapsed = (time.perf_counter() - start) * 1000
    level   = logging.WARNING if response.status_code >= 400 else logging.INFO
    logger.log(level, "← %s %s  %d  [%.1f ms]",
               request.method, request.url.path, response.status_code, elapsed)
    return response


if __name__ == "__main__":
    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=False)
