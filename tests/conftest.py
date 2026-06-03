"""
Pytest configuration and shared fixtures for Pathoryx Enterprise tests.

Fixtures:
  - tmp_db_url: SQLite in-memory URL for unit tests (no Postgres required)
  - pg_session: PostgreSQL session for integration tests (requires DATABASE_URL)
  - tmp_dir: temporary directory scoped to test function
  - sample_slide_path: a small temporary file simulating a WSI
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from pathoryx_enterprise.db.base import Base


# ---------------------------------------------------------------------------
# Unit test DB (SQLite in-memory, no Postgres required)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sqlite_engine():
    """SQLite in-memory engine for fast unit tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # SQLite does not support schemas — strip schema prefixes for unit tests
    @event.listens_for(engine, "connect")
    def _set_pragmas(conn, _):
        conn.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine) -> Generator[Session, None, None]:
    """
    Provide a unit-test session that rolls back after each test.
    No real data is committed.
    """
    factory = sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# Integration test DB (real Postgres — skipped if DATABASE_URL not set)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pg_engine():
    """Postgres engine — requires DATABASE_URL env var. Skips if absent."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping Postgres integration tests")
    from pathoryx_enterprise.db.engine import get_shared_engine
    return get_shared_engine()


@pytest.fixture
def pg_session(pg_engine) -> Generator[Session, None, None]:
    """
    Integration test session — always rolls back, never commits to Postgres.
    """
    from pathoryx_enterprise.db.session import get_session
    with get_session() as session:
        session.begin_nested()
        yield session
        session.rollback()


# ---------------------------------------------------------------------------
# File system fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sample_slide_path(tmp_dir: Path) -> Path:
    """Create a small fake WSI file for testing file utility functions."""
    slide = tmp_dir / "test_slide.svs"
    slide.write_bytes(b"FAKE_SVS_DATA" * 100)
    return slide
