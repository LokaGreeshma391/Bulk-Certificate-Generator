import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path):
    """A fresh app per test: its own SQLite file and storage folder."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=str(tmp_path / "storage"),
        max_recipients=50,
    )
    return create_app(settings)


@pytest.fixture
def client(app):
    # TestClient runs FastAPI background tasks before returning the response
    # to the test, so after POST /jobs the job has already been processed.
    return TestClient(app)


@pytest.fixture
def valid_payload():
    return {
        "title": "Python Bootcamp 2026",
        "issued_by": "Acme Academy",
        "issue_date": "2026-10-01",
        "recipients": [
            {"name": "Greeshma Loka", "email": "greeshma@example.com"},
            {"name": "Arjun Rao", "email": "arjun@example.com"},
            {"name": "Meera Iyer", "email": "meera@example.com"},
        ],
    }
