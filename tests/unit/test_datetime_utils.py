"""Unit tests for UTC datetime utility."""
from __future__ import annotations

from datetime import timezone

from pathoryx_enterprise.utils.datetime_utils import utc_now


def test_utc_now_is_timezone_aware() -> None:
    now = utc_now()
    assert now.tzinfo is not None


def test_utc_now_is_utc() -> None:
    now = utc_now()
    assert now.tzinfo == timezone.utc or now.utcoffset().total_seconds() == 0


def test_utc_now_advances() -> None:
    import time
    t1 = utc_now()
    time.sleep(0.01)
    t2 = utc_now()
    assert t2 > t1
