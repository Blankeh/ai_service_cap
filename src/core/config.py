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

    # Frame grouper
    group_window_ms: int = int(os.getenv("GROUP_WINDOW_MS", "1000"))  # deadline after first frame arrives
    group_bucket_size: int = int(os.getenv("GROUP_BUCKET_SIZE", "2"))  # must match ESP32 capture interval (seconds)
    retry_interval_seconds: int = int(os.getenv("RETRY_INTERVAL_SECONDS", "30"))
    max_retry_attempts: int = int(os.getenv("MAX_RETRY_ATTEMPTS", "10"))

    # Model auto-update — polls Cloudflare Worker for new model versions
    model_dir: str = os.getenv("MODEL_DIR", "models")
    model_auto_update: bool = os.getenv("MODEL_AUTO_UPDATE", "false").lower() == "true"
    model_update_interval_seconds: int = int(os.getenv("MODEL_UPDATE_INTERVAL_SECONDS", "300"))

    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
