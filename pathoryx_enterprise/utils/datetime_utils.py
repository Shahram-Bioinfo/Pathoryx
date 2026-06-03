"""
Canonical datetime utilities. Import ONLY from here — never use datetime.utcnow().

All datetimes are timezone-aware (UTC). PostgreSQL TIMESTAMPTZ stores them correctly.
datetime.utcnow() is deprecated in Python 3.12 and returns naive datetimes that
silently mismatch TIMESTAMPTZ columns.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def utc_from_timestamp(ts: float) -> datetime:
    """Convert a POSIX timestamp (e.g. os.stat().st_mtime) to UTC datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def duration_ms(started_at: datetime | None, finished_at: datetime | None = None) -> int | None:
    """Return wall-clock duration in milliseconds, or None if start is unknown."""
    if started_at is None:
        return None
    end = finished_at if finished_at is not None else utc_now()
    return int((end - started_at).total_seconds() * 1000)


def duration_seconds(started_at: datetime | None, finished_at: datetime | None = None) -> float | None:
    """Return wall-clock duration in seconds, or None if start is unknown."""
    ms = duration_ms(started_at, finished_at)
    return ms / 1000.0 if ms is not None else None


def is_older_than(path_mtime_ns: int, seconds: float) -> bool:
    """Return True if the mtime (nanoseconds) is older than `seconds` ago."""
    now_ns = int(utc_now().timestamp() * 1_000_000_000)
    return (now_ns - path_mtime_ns) > int(seconds * 1_000_000_000)
