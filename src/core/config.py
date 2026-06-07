import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Model
    model_path: str = os.getenv("MODEL_PATH", "yolov8n.pt")
    yolo_input_size: int = int(os.getenv("YOLO_INPUT_SIZE", "640"))
    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.5"))

    # Cloudflare backend
    cloudflare_api_url: str = os.getenv("CLOUDFLARE_API_URL", "")
    cloudflare_api_key: str = os.getenv("CLOUDFLARE_API_KEY", "")
    cloudflare_timeout: int = int(os.getenv("CLOUDFLARE_TIMEOUT", "10"))

    # Database — single SQLite file, all tables
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/app.db")

    # Aggregator — how often to flush buffered counts to Cloudflare
    aggregator_flush_interval:  int = int(os.getenv("AGGREGATOR_FLUSH_INTERVAL", "60"))
    # Spike threshold — immediate flush if crowd changes by this many people
    aggregator_spike_threshold: int = int(os.getenv("AGGREGATOR_SPIKE_THRESHOLD", "5"))

    # Frame grouper
    group_window_ms: int = int(os.getenv("GROUP_WINDOW_MS", "1000"))  # deadline after first frame arrives
    group_bucket_size: int = int(os.getenv("GROUP_BUCKET_SIZE", "2"))  # must match ESP32 capture interval (seconds)
    retry_interval_seconds: int = int(os.getenv("RETRY_INTERVAL_SECONDS", "30"))
    max_retry_attempts: int = int(os.getenv("MAX_RETRY_ATTEMPTS", "10"))

    # Bus identification — this Pi's bus (matched against /api/v1/buses at startup)
    bus_id: int = int(os.getenv("BUS_ID", "0"))
    bus_info_refresh_seconds: int = int(os.getenv("BUS_INFO_REFRESH_SECONDS", "300"))

    # Camera-ID template — placeholders: {bus}=busId numeric, {pos}=position suffix (001/002/003)
    camera_id_template: str = os.getenv("CAMERA_ID_TEMPLATE", "CAM-BUS{bus}-{pos}")

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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
