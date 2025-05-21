"""Shared pytest fixtures.

Every test runs against a throwaway SQLite database and storage directory so that
no test can see another test's data and nothing leaks into a developer's dev DB.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DATA_DIR = ROOT / "data" / "synthetic"


@pytest.fixture(scope="session", autouse=True)
def _isolated_var_dir() -> Iterator[Path]:
    """Point the whole test session at a temporary var/ directory."""
    with tempfile.TemporaryDirectory(prefix="sop-tests-") as tmp:
        os.environ["VAR_DIR"] = tmp
        os.environ["ANALYSIS_MODE"] = "deterministic"
        os.environ["LLM_ALLOW_PAID_REQUESTS"] = "false"
        yield Path(tmp)


@pytest.fixture()
def db_session(_isolated_var_dir: Path) -> Iterator[object]:
    from app.db import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


@pytest.fixture(scope="session")
def seeded_app(_isolated_var_dir: Path):
    """A FastAPI TestClient backed by the full synthetic dataset."""
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(ROOT / "scripts"))
    from seed import seed

    from app.main import create_app

    seed(reset=True, quiet=True)
    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def admin_token(seeded_app) -> str:
    response = seeded_app.post("/api/auth/login", json={"username": "admin", "password": "Admin#12345"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="session")
def auditor_token(seeded_app) -> str:
    response = seeded_app.post("/api/auth/login", json={"username": "auditor", "password": "Auditor#12345"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="session")
def reviewer_token(seeded_app) -> str:
    response = seeded_app.post("/api/auth/login", json={"username": "reviewer", "password": "Reviewer#12345"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
