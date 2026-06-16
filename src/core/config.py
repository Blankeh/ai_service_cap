import json
import logging
import os
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)


def _parse_camera_rois(raw) -> dict:
    """
    Parse CAMERA_ROIS — a JSON map of pane → normalized [x1,y1,x2,y2] ROI box.

    Example: {"front": [0.0, 0.0, 1.0, 0.5], "rear": [0.0, 0.5, 1.0, 1.0]}
    Panes with no entry fall back to the full frame (count everything).

    Accepts the raw env string (or an already-parsed dict). An unset, empty, or
    invalid value yields {} — never a startup crash.
    """
    if isinstance(raw, dict):
        return raw
    if not raw or not str(raw).strip():
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Invalid CAMERA_ROIS JSON (%s) — ignoring, using full-frame ROIs", exc)
        return {}


class Settings(BaseSettings):
    # Environment — "dev" enables the live detection viewer at /dev;
    # "prod" never imports or starts the viewer.
    app_env: str = os.getenv("APP_ENV", "prod").strip().lower()

    # Model
    model_path: str = os.getenv("MODEL_PATH", "yolov8n.pt")
    yolo_input_size: int = int(os.getenv("YOLO_INPUT_SIZE", "640"))
    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.35"))

    # Cloudflare backend
    cloudflare_api_url: str = os.getenv("CLOUDFLARE_API_URL", "")
    cloudflare_api_key: str = os.getenv("CLOUDFLARE_API_KEY", "")
    cloudflare_timeout: int = int(os.getenv("CLOUDFLARE_TIMEOUT", "10"))

    # Device auth — backend issues a bearer token from the login endpoint, which
    # is then attached to every /api/v1/device/input upload. Re-login happens on
    # demand when an upload comes back 401 (token expired).
    auth_login_path: str = os.getenv("AUTH_LOGIN_PATH", "/api/v1/auth/login")
    device_username: str = os.getenv("DEVICE_USERNAME", "device")
    device_password: str = os.getenv("DEVICE_PASSWORD", "w7ePNd6j8FMSwaKH3v0McATx")
    auth_login_retry_interval: int = int(os.getenv("AUTH_LOGIN_RETRY_INTERVAL", "10"))

    # Database — single SQLite file, all tables
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/app.db")

    # Aggregator — how often to flush buffered counts to Cloudflare
    aggregator_flush_interval:  int = int(os.getenv("AGGREGATOR_FLUSH_INTERVAL", "60"))
    # Spike threshold — immediate flush if crowd changes by this many people
    aggregator_spike_threshold: int = int(os.getenv("AGGREGATOR_SPIKE_THRESHOLD", "5"))

    # Frame grouper
    group_window_ms: int = int(os.getenv("GROUP_WINDOW_MS", "1000"))  # deadline after first frame arrives
    group_bucket_size: int = int(os.getenv("GROUP_BUCKET_SIZE", "2"))  # must match ESP32 capture interval (seconds)
    # Number of cameras per bus — when >0 a group is processed as soon as this
    # many frames arrive (early-fire), instead of always waiting out the deadline.
    expected_cameras: int = int(os.getenv("EXPECTED_CAMERAS", "0"))

    # Bus identification — this Pi's bus
    bus_id: int = int(os.getenv("BUS_ID", "0"))

    # Camera-ID template — placeholders: {bus}=busId numeric, {pos}=position suffix (001/002/003)
    camera_id_template: str = os.getenv("CAMERA_ID_TEMPLATE", "CAM-BUS{bus}-{pos}")

    # Per-pane ROI boxes (normalized 0..1), selected by camera device_id/pane.
    # NoDecode: don't let pydantic JSON-decode the raw env value (an empty
    # CAMERA_ROIS= would crash startup); the validator below parses it safely.
    camera_rois: Annotated[dict, NoDecode] = {}

    @field_validator("camera_rois", mode="before")
    @classmethod
    def _decode_camera_rois(cls, v) -> dict:
        return _parse_camera_rois(v)

    # UDP broadcast camera-sync
    camera_sync_enabled: bool = os.getenv("CAMERA_SYNC_ENABLED", "true").lower() == "true"
    camera_sync_broadcast_addr: str = os.getenv("CAMERA_SYNC_BROADCAST_ADDR", "255.255.255.255")
    camera_sync_port: int = int(os.getenv("CAMERA_SYNC_PORT", "5005"))
    camera_sync_interval_seconds: int = int(os.getenv("CAMERA_SYNC_INTERVAL_SECONDS", "2"))

    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_dir: str   = os.getenv("LOG_DIR", "logs")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
