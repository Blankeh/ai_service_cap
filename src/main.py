import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .api.routes import router
from .core.config import settings
from .repos.camera_repo import CameraRepo
from .repos.queue_repo import QueueRepo
from .services.camera_service import CameraService
from .services.cloudflare_service import CloudflareService
from .services.grouper_service import FrameGrouper
from .services.image_service import ImageService
from .services.inference_service import InferenceService
from .services.model_update_service import ModelUpdateService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    image_svc     = ImageService(target_size=settings.yolo_input_size)
    inference_svc = InferenceService(
        model_path=settings.model_path,
        confidence_threshold=settings.confidence_threshold,
    )
    queue_repo    = QueueRepo()
    camera_repo   = CameraRepo()
    cloudflare_svc = CloudflareService(queue_repo=queue_repo)

    grouper = FrameGrouper(
        image_svc=image_svc,
        inference_svc=inference_svc,
        cloudflare_svc=cloudflare_svc,
        camera_repo=camera_repo,
        group_window_ms=settings.group_window_ms,
        bucket_size=settings.group_bucket_size,
    )

    app.state.image_svc      = image_svc
    app.state.inference_svc  = inference_svc
    app.state.queue_repo     = queue_repo
    app.state.camera_repo    = camera_repo
    app.state.camera_svc     = CameraService(camera_repo=camera_repo)
    app.state.cloudflare_svc = cloudflare_svc
    app.state.grouper        = grouper

    model_update_svc = ModelUpdateService(inference_svc=inference_svc)

    retry_task = asyncio.create_task(_retry_loop(cloudflare_svc))

    model_update_task = None
    if settings.model_auto_update:
        model_update_task = asyncio.create_task(_model_update_loop(model_update_svc))
        logger.info(f"Model auto-update enabled (interval={settings.model_update_interval_seconds}s)")
    else:
        logger.info("Model auto-update disabled (set MODEL_AUTO_UPDATE=true to enable)")

    logger.info("AI service started")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    retry_task.cancel()
    if model_update_task:
        model_update_task.cancel()
    logger.info("AI service stopped")


app = FastAPI(title="Bus Crowd AI Service", version="1.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")


async def _retry_loop(cloudflare_svc: CloudflareService):
    while True:
        await asyncio.sleep(settings.retry_interval_seconds)
        try:
            await cloudflare_svc.flush_queue()
        except Exception as exc:
            logger.error(f"Retry loop error: {exc}")


async def _model_update_loop(model_update_svc: ModelUpdateService):
    while True:
        await asyncio.sleep(settings.model_update_interval_seconds)
        try:
            await model_update_svc.check_and_update()
        except Exception as exc:
            logger.error(f"Model update loop error: {exc}")


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
