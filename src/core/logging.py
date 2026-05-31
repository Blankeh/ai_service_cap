"""
core/logging.py
───────────────
Centralised logging configuration for the AI service.

Features
────────
• Console handler  — human-readable, colourised by level (helpful during dev / SSH sessions)
• Rotating file handler — writes to LOG_DIR/ai_service.log, caps at 5 MB × 3 backups
  (safe for the Pi 4's SD card: max ~15 MB total)
• Structured format  — timestamp · level · logger name · message
• Third-party noise suppression — ultralytics, httpx, PIL flood the root logger;
  we dial them back to WARNING so your own INFO lines stay visible
• Log level is controlled by the LOG_LEVEL env var (default INFO)

Usage
─────
    from src.core.logging import setup_logging
    setup_logging()          # call once in main.py before FastAPI starts

Every other module just does:
    import logging
    logger = logging.getLogger(__name__)
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path


# ── Colour codes (ANSI) ────────────────────────────────────────────────────────
_GREY    = "\033[38;5;246m"
_CYAN    = "\033[36m"
_YELLOW  = "\033[33m"
_RED     = "\033[31m"
_BOLD_RED= "\033[1;31m"
_RESET   = "\033[0m"

_LEVEL_COLOURS = {
    logging.DEBUG:    _GREY,
    logging.INFO:     _CYAN,
    logging.WARNING:  _YELLOW,
    logging.ERROR:    _RED,
    logging.CRITICAL: _BOLD_RED,
}


class _ColouredFormatter(logging.Formatter):
    """Adds ANSI colours to the log level name on supported terminals."""

    def format(self, record: logging.LogRecord) -> str:
        colour = _LEVEL_COLOURS.get(record.levelno, _RESET)
        record.levelname = f"{colour}{record.levelname:<8}{_RESET}"
        return super().format(record)


_PLAIN_FMT      = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_DATE_FMT       = "%Y-%m-%d %H:%M:%S"
_NOISY_LOGGERS  = [
    "ultralytics",
    "httpx",
    "httpcore",
    "PIL",
    "urllib3",
    "multipart",
    "uvicorn.access",   # we log requests ourselves in middleware
]


def setup_logging() -> None:
    """
    Configure the root logger once.  Call this at the very top of main.py
    before importing FastAPI or any service.
    """
    raw_level  = os.getenv("LOG_LEVEL", "INFO").upper()
    level      = getattr(logging, raw_level, logging.INFO)
    log_dir    = Path(os.getenv("LOG_DIR", "logs"))

    root = logging.getLogger()
    root.setLevel(level)

    # ── Remove any handlers already attached (e.g. by uvicorn's own basicConfig) ──
    root.handlers.clear()

    # ── Console handler ──────────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)

    use_colour = sys.stdout.isatty() or os.getenv("FORCE_COLOR", "").lower() in ("1", "true")
    if use_colour:
        console_handler.setFormatter(_ColouredFormatter(_PLAIN_FMT, datefmt=_DATE_FMT))
    else:
        console_handler.setFormatter(logging.Formatter(_PLAIN_FMT, datefmt=_DATE_FMT))

    root.addHandler(console_handler)

    # ── Rotating file handler ────────────────────────────────────────────────────
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "ai_service.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,   # 5 MB per file
            backupCount=3,               # keep 3 rotated files → max ~20 MB on SD card
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        # File always uses plain format (no ANSI codes)
        file_handler.setFormatter(logging.Formatter(_PLAIN_FMT, datefmt=_DATE_FMT))
        root.addHandler(file_handler)
        logging.getLogger(__name__).info(
            f"Logging to file: {log_file.resolve()}  (max 5 MB × 3 backups)"
        )
    except OSError as exc:
        logging.getLogger(__name__).warning(
            f"Could not create log file handler: {exc}  — console only"
        )

    # ── Suppress noisy third-party loggers ──────────────────────────────────────
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        f"Log level: {raw_level}  |  Pi-4 rotating log active"
    )
