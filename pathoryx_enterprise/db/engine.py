"""
SQLAlchemy engine factory.

The engine is created once per process and shared across all sessions.
All configuration comes from environment variables — no hardcoded values.

Pool settings:
  pool_size     — persistent connections kept open (default 10)
  max_overflow  — extra connections allowed under peak load (default 20)
  pool_recycle  — close and re-open connections older than N seconds (default 1800)
                  prevents "server closed the connection unexpectedly" after idle periods
  pool_pre_ping — issue a lightweight SELECT 1 before handing out a connection
                  recovers transparently from connections dropped by the server or firewall
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool


def _get_required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            "Set it in your .env file or shell environment before starting Palantir."
        )
    return value


def create_pathoryx_engine(
    database_url: str | None = None,
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_recycle: int | None = None,
    pool_pre_ping: bool = True,
    echo: bool = False,
    use_null_pool: bool = False,
) -> Engine:
    """
    Create a configured SQLAlchemy engine.

    Args:
        database_url: Full PostgreSQL URL. If None, reads DATABASE_URL from env.
        pool_size: Persistent connection count. Reads DB_POOL_SIZE env if None.
        max_overflow: Extra connections under peak. Reads DB_MAX_OVERFLOW env if None.
        pool_recycle: Seconds before recycling. Reads DB_POOL_RECYCLE env if None.
        pool_pre_ping: Validate connections before use.
        echo: Log all SQL to stdout (development only).
        use_null_pool: Disable pooling entirely (useful for Alembic migrations).
    """
    url = database_url or _get_required_env("DATABASE_URL")

    kwargs: dict[str, Any] = {
        "echo": echo or os.environ.get("DB_ECHO_SQL", "false").lower() == "true",
        "future": True,
    }

    if use_null_pool:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = pool_size or int(os.environ.get("DB_POOL_SIZE", "10"))
        kwargs["max_overflow"] = max_overflow or int(os.environ.get("DB_MAX_OVERFLOW", "20"))
        kwargs["pool_recycle"] = pool_recycle or int(os.environ.get("DB_POOL_RECYCLE", "1800"))
        kwargs["pool_pre_ping"] = pool_pre_ping

    engine = create_engine(url, **kwargs)

    # Set search_path to core schema so bare table names resolve.
    # Each service still uses fully-qualified schema.table names in models.
    @event.listens_for(engine, "connect")
    def set_search_path(dbapi_conn: Any, _: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("SET search_path TO core, public")
        cursor.close()

    return engine


@lru_cache(maxsize=1)
def get_shared_engine() -> Engine:
    """
    Return the process-level singleton engine.
    Created on first call; subsequent calls return the same instance.
    Use create_pathoryx_engine() directly for test isolation or migrations.
    """
    return create_pathoryx_engine()
