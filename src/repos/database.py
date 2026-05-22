import os
from sqlmodel import SQLModel, create_engine, Session

_engine = None


def _init(db_url: str):
    global _engine
    # For SQLite: ensure the data/ directory exists before creating the file
    if db_url.startswith("sqlite:///"):
        db_path = db_url[len("sqlite:///"):]
        dir_name = os.path.dirname(db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
    _engine = create_engine(db_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(_engine)  # no-op if tables already exist


def _ensure_engine():
    """Lazily initialise from settings if not already done."""
    if _engine is None:
        from ..core.config import settings
        _init(settings.database_url)


def init_engine(db_url: str):
    """Explicitly set a DB URL — used in tests to point at a temp file."""
    _init(db_url)


def get_session() -> Session:
    _ensure_engine()
    return Session(_engine)
