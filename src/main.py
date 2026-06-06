import asyncio
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response

from .core.logging import setup_logging
setup_logging()

from .api.router import api_router
from .core.config import settings
from .repos.queue_repo import QueueRepo
from .services.aggregator_service import AggregatorService
from .services.bus_info_service import BusInfoService
from .services.camera_sync_service import CameraSyncService
from .services.cloudflare_service import CloudflareService
from .services.grouper_service import FrameGrouper
from .services.image_service import ImageService
from .services.inference_service import InferenceService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    image_svc     = ImageService(target_size=settings.yolo_input_size)
    inference_svc = InferenceService(
        model_path=settings.model_path,
        confidence_threshold=settings.confidence_threshold,
    )
    queue_repo     = QueueRepo()
    cloudflare_svc = CloudflareService(queue_repo=queue_repo)

    bus_info_svc = BusInfoService()
    await bus_info_svc.fetch()  # best-effort; logs warning on failure, does not block startup

    aggregator_svc = AggregatorService(
        cloudflare_svc=cloudflare_svc,
        bus_info_svc=bus_info_svc,
        flush_interval=settings.aggregator_flush_interval,
        spike_threshold=settings.aggregator_spike_threshold,
    )

    grouper = FrameGrouper(
        image_svc=image_svc,
        inference_svc=inference_svc,
        aggregator_svc=aggregator_svc,
        group_window_ms=settings.group_window_ms,
        bucket_size=settings.group_bucket_size,
    )

    camera_sync_svc = CameraSyncService()

    app.state.image_svc      = image_svc
    app.state.inference_svc  = inference_svc
    app.state.queue_repo     = queue_repo
    app.state.cloudflare_svc = cloudflare_svc
    app.state.bus_info_svc   = bus_info_svc
    app.state.grouper        = grouper

    retry_task      = asyncio.create_task(_retry_loop(cloudflare_svc))
    aggregator_task = asyncio.create_task(aggregator_svc.run_loop())
    bus_info_task   = asyncio.create_task(bus_info_svc.run_loop())
    camera_sync_task = asyncio.create_task(camera_sync_svc.run_loop())

    logger.info("AI service started")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    retry_task.cancel()
    aggregator_task.cancel()
    bus_info_task.cancel()
    camera_sync_task.cancel()
    logger.info("AI service stopped")


app = FastAPI(title="Bus Crowd AI Service", version="1.0.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api")


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


async def _retry_loop(cloudflare_svc: CloudflareService) -> None:
    while True:
        await asyncio.sleep(settings.retry_interval_seconds)
        try:
            await cloudflare_svc.flush_queue()
        except Exception as exc:
            logger.error("Retry loop error: %s", exc)


if __name__ == "__main__":
    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=False)
