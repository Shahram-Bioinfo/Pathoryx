"""
Phase WALLBOARD-1 — backend unit tests.

Tests:
  1. Operational day starts at 07:00 Europe/Copenhagen
  2. 06:30 belongs to the previous operational day
  3. 07:05 belongs to the current operational day
  4. Exactly at 07:00 starts current day
  5. Stain percentages sum correctly
  6. Rare stains grouped into "Other"
  7. Empty stain data returns empty list
  8. get_wallboard_data returns safe zero payload on empty DB
  9. Operational day window is exactly 24 hours
 10. Wallboard endpoint returns 200 with valid structure
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

import pytest

from pathoryx_enterprise.services.dashboard.wallboard_queries import (
    OPERATIONAL_HOUR,
    OPERATIONAL_TIMEZONE,
    STAIN_TOP_N,
    get_operational_day_start,
    get_operational_day_window,
    _get_stain_distribution,
)

CPH = ZoneInfo(OPERATIONAL_TIMEZONE)


def _cph(hour: int, minute: int = 0, *, day: int = 15) -> datetime:
    """Create a Europe/Copenhagen-aware datetime on 2026-01-{day}."""
    return datetime(2026, 1, day, hour, minute, 0, tzinfo=CPH)


# ── 1. Operational day starts at 07:00 ───────────────────────────────────────

class TestOperationalDayStart:
    def test_0700_is_start_of_current_day(self):
        now = _cph(7, 0)
        start = get_operational_day_start(now=now)
        expected = now.replace(hour=OPERATIONAL_HOUR, minute=0, second=0, microsecond=0)
        assert start == expected.astimezone(timezone.utc)

    def test_0900_still_current_day(self):
        now = _cph(9, 0)
        start = get_operational_day_start(now=now)
        expected = now.replace(hour=OPERATIONAL_HOUR, minute=0, second=0, microsecond=0)
        assert start == expected.astimezone(timezone.utc)

    def test_2359_still_current_day(self):
        now = _cph(23, 59)
        start = get_operational_day_start(now=now)
        expected = now.replace(hour=OPERATIONAL_HOUR, minute=0, second=0, microsecond=0)
        assert start == expected.astimezone(timezone.utc)

    def test_start_is_utc(self):
        now = _cph(10, 0)
        start = get_operational_day_start(now=now)
        assert start.tzinfo is not None
        assert start.utcoffset() == timedelta(0)


# ── 2. 06:30 belongs to previous operational day ──────────────────────────────

class TestEarlyMorningPreviousDay:
    def test_0630_belongs_to_yesterday_op_day(self):
        now = _cph(6, 30)
        start = get_operational_day_start(now=now)
        # Start should be yesterday at 07:00
        yesterday_0700 = _cph(7, 0, day=14)
        assert start == yesterday_0700.astimezone(timezone.utc)

    def test_0000_belongs_to_yesterday_op_day(self):
        now = _cph(0, 0)
        start = get_operational_day_start(now=now)
        yesterday_0700 = _cph(7, 0, day=14)
        assert start == yesterday_0700.astimezone(timezone.utc)

    def test_0659_belongs_to_yesterday_op_day(self):
        now = _cph(6, 59)
        start = get_operational_day_start(now=now)
        yesterday_0700 = _cph(7, 0, day=14)
        assert start == yesterday_0700.astimezone(timezone.utc)


# ── 3. 07:05 belongs to the current operational day ───────────────────────────

class TestAfter0700CurrentDay:
    def test_0705_belongs_to_today(self):
        now = _cph(7, 5)
        start = get_operational_day_start(now=now)
        today_0700 = _cph(7, 0)
        assert start == today_0700.astimezone(timezone.utc)

    def test_0701_belongs_to_today(self):
        now = _cph(7, 1)
        start = get_operational_day_start(now=now)
        today_0700 = _cph(7, 0)
        assert start == today_0700.astimezone(timezone.utc)


# ── 4. Operational day window is exactly 24 hours ────────────────────────────

class TestOperationalDayWindow:
    def test_window_is_24_hours(self):
        now = _cph(10, 0)
        start, end = get_operational_day_window(now=now)
        assert (end - start) == timedelta(hours=24)

    def test_window_start_equals_day_start(self):
        now = _cph(14, 30)
        start, end = get_operational_day_window(now=now)
        assert start == get_operational_day_start(now=now)


# ── 5. Stain percentages sum correctly ───────────────────────────────────────

class TestStainPercentages:
    def _mock_session(self, rows: list[tuple[str, int]]) -> MagicMock:
        """Build a session mock that returns stain rows from execute()."""
        session = MagicMock()
        mock_rows = [MagicMock(stain_type=s, cnt=c) for s, c in rows]
        session.execute.return_value.all.return_value = mock_rows
        return session

    def test_percentages_sum_to_100(self):
        session = self._mock_session([
            ("H&E",   40),
            ("IHC",   25),
            ("PAS",   20),
            ("CD3",   10),
            ("CD20",   5),
        ])
        op_start = datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc)
        result = _get_stain_distribution(session, op_start)
        total_pct = sum(r["percentage"] for r in result)
        assert abs(total_pct - 100.0) < 1.0, f"Percentages should sum to ~100%, got {total_pct}"

    def test_each_count_matches_input(self):
        session = self._mock_session([("H&E", 30), ("IHC", 20)])
        result = _get_stain_distribution(session, datetime(2026, 1, 15, 6, tzinfo=timezone.utc))
        assert result[0]["count"] == 30
        assert result[1]["count"] == 20

    def test_percentage_calculation_correct(self):
        session = self._mock_session([("H&E", 75), ("IHC", 25)])
        result = _get_stain_distribution(session, datetime(2026, 1, 15, 6, tzinfo=timezone.utc))
        assert result[0]["percentage"] == 75.0
        assert result[1]["percentage"] == 25.0


# ── 6. Rare stains grouped into "Other" ──────────────────────────────────────

class TestStainGrouping:
    def _make_many_stain_session(self) -> MagicMock:
        session = MagicMock()
        # More than STAIN_TOP_N stains
        stains = [(f"STAIN_{i}", 10 - i) for i in range(STAIN_TOP_N + 3)]
        mock_rows = [MagicMock(stain_type=s, cnt=c) for s, c in stains]
        session.execute.return_value.all.return_value = mock_rows
        return session

    def test_rare_stains_grouped_into_other(self):
        session = self._make_many_stain_session()
        result = _get_stain_distribution(
            session, datetime(2026, 1, 15, 6, tzinfo=timezone.utc)
        )
        stain_names = [r["stain"] for r in result]
        assert "Other" in stain_names, "Rare stains should be grouped into 'Other'"

    def test_top_n_stains_kept_individually(self):
        session = self._make_many_stain_session()
        result = _get_stain_distribution(
            session, datetime(2026, 1, 15, 6, tzinfo=timezone.utc)
        )
        # At most STAIN_TOP_N + 1 (Other) items
        assert len(result) <= STAIN_TOP_N + 1

    def test_other_count_equals_sum_of_remainder(self):
        session = MagicMock()
        # 9 stains when STAIN_TOP_N=7 → 2 go into Other
        stains = [(f"S{i}", 10) for i in range(STAIN_TOP_N + 2)]
        mock_rows = [MagicMock(stain_type=s, cnt=c) for s, c in stains]
        session.execute.return_value.all.return_value = mock_rows

        result = _get_stain_distribution(
            session, datetime(2026, 1, 15, 6, tzinfo=timezone.utc)
        )
        other = next((r for r in result if r["stain"] == "Other"), None)
        assert other is not None
        assert other["count"] == 2 * 10  # 2 stains × 10 each


# ── 7. Empty stain data returns empty list ────────────────────────────────────

class TestEmptyStainData:
    def test_no_rows_returns_empty(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = []
        result = _get_stain_distribution(
            session, datetime(2026, 1, 15, 6, tzinfo=timezone.utc)
        )
        assert result == []

    def test_db_error_returns_empty(self):
        session = MagicMock()
        session.execute.side_effect = RuntimeError("DB error")
        result = _get_stain_distribution(
            session, datetime(2026, 1, 15, 6, tzinfo=timezone.utc)
        )
        assert result == []


# ── 8. Wallboard endpoint returns 200 with valid structure ───────────────────

class TestWallboardEndpoint:
    def test_endpoint_returns_200_with_required_fields(self):
        from fastapi.testclient import TestClient
        from pathoryx_enterprise.services.dashboard.app import create_app, get_db

        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = 0
        mock_db.execute.return_value.scalars.return_value.all.return_value = []
        mock_db.execute.return_value.all.return_value = []
        mock_db.execute.return_value.mappings.return_value.all.return_value = []

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/dashboard/api/wallboard")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        assert "kpis" in data
        assert "scanners" in data
        assert "stain_distribution" in data
        assert "pipeline" in data
        assert "alerts" in data
        assert "operational_day_start" in data
        assert "operational_day_end" in data
        assert "system_status" in data

    def test_kpis_have_expected_fields(self):
        from fastapi.testclient import TestClient
        from pathoryx_enterprise.services.dashboard.app import create_app, get_db

        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = 0
        mock_db.execute.return_value.scalars.return_value.all.return_value = []
        mock_db.execute.return_value.all.return_value = []

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db
        client = TestClient(app, raise_server_exceptions=False)

        data = client.get("/dashboard/api/wallboard").json()
        kpis = data["kpis"]
        for field in ["uploaded_today", "slides_scanned_today", "queue_depth",
                      "active_processing", "failed", "recovery_backlog"]:
            assert field in kpis, f"Missing KPI field: {field}"
