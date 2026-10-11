"""Application settings.

Kept as a small dataclass (not a global) so tests can build an app with
their own temporary database and storage folder.
"""
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # Any SQLAlchemy URL works, e.g. postgresql+psycopg://user:pw@host/db
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./certificates.db")
    )
    # Where generated PDFs are written.
    storage_dir: str = field(
        default_factory=lambda: os.getenv("STORAGE_DIR", "./storage")
    )
    # Upper bound on recipients per request (protects the server).
    max_recipients: int = field(
        default_factory=lambda: int(os.getenv("MAX_RECIPIENTS", "1000"))
    )
