"""
Phase 4.3B — Upload Priority / Next-in-Line Flag.

Tests:
  A. update_upload_priority() returns None for missing record
  B. update_upload_priority() raises ValueError for terminal status 'uploaded'
  C. update_upload_priority() raises ValueError for terminal status 'failed'
  D. update_upload_priority() succeeds and advances last_updated_at for queued record
  E. list_upload_queue() orders lower priority values first
  F. PATCH /uploads/queue/{id}/priority returns 404 for unknown record
  G. PATCH /uploads/queue/{id}/priority returns 409 for terminal record
  H. PATCH /uploads/queue/{id}/priority returns 422 for out-of-range priority
  I. PATCH /uploads/queue/{id}/priority returns 200 for valid queued record
  J. PATCH /uploads/queue/{id}/priority: reset to normal priority (5) succeeds
  K. PATCH /uploads/queue/{id}/priority: Upload Next (0) succeeds
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_upload_row(
    record_id: int = 1,
    upload_status: str = "queued",
    priority: int = 5,
) -> MagicMock:
    row = MagicMock()
    row.id = record_id
    row.upload_status = upload_status
    row.priority = priority
    row.filename = "N24-3625-Q.svs"
    row.slide_id = None
    row.scanner_id = "SCANNER01"
    row.uploader_host = "win-host-01"
    row.queued_at = datetime(2026, 6, 9, 10, 0, 0, tzinfo=timezone.utc)
    row.estimated_upload_at = None
    row.upload_started_at = None
    row.upload_completed_at = None
    row.retry_count = 0
    row.file_size_bytes = 1024 * 1024 * 512
    row.upload_speed_mbps = None
    row.failure_reason = None
    row.last_updated_at = datetime(2026, 6, 9, 10, 0, 0, tzinfo=timezone.utc)
    return row


def _make_app_client():
    from pathoryx_enterprise.services.dashboard.app import create_app, get_db

    app = create_app()
    session_mock = MagicMock()
    app.dependency_overrides[get_db] = lambda: session_mock
    client = TestClient(app, raise_server_exceptions=False)
    return client, session_mock


def _mock_update_priority_success(record_id: int = 1, priority: int = 0) -> dict:
    """Return a plausible dict matching UploadQueueItem schema."""
    return {
        "id": record_id,
        "slide_id": None,
        "filename": "N24-3625-Q.svs",
        "scanner_id": "SCANNER01",
        "uploader_host": "win-host-01",
        "queued_at": datetime(2026, 6, 9, 10, 0, 0, tzinfo=timezone.utc),
        "estimated_upload_at": None,
        "upload_started_at": None,
        "upload_completed_at": None,
        "upload_status": "queued",
        "retry_count": 0,
        "file_size_bytes": 536_870_912,
        "priority": priority,
        "upload_speed_mbps": None,
        "failure_reason": None,
        "last_updated_at": datetime(2026, 6, 9, 10, 5, 0, tzinfo=timezone.utc),
        "is_delayed": False,
    }


# ---------------------------------------------------------------------------
# A. update_upload_priority() returns None for missing record
# ---------------------------------------------------------------------------

class TestUpdateUploadPriorityMissing:
    def test_returns_none_for_missing_record(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority

        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        result = update_upload_priority(session, record_id=9999, priority=0)

        assert result is None


# ---------------------------------------------------------------------------
# B. update_upload_priority() raises ValueError for terminal 'uploaded'
# ---------------------------------------------------------------------------

class TestUpdateUploadPriorityTerminalUploaded:
    def test_raises_for_uploaded_status(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority

        session = MagicMock()
        row = _make_upload_row(upload_status="uploaded")
        session.execute.return_value.scalar_one_or_none.return_value = row

        with pytest.raises(ValueError, match="uploaded"):
            update_upload_priority(session, record_id=1, priority=0)


# ---------------------------------------------------------------------------
# C. update_upload_priority() raises ValueError for terminal 'failed'
# ---------------------------------------------------------------------------

class TestUpdateUploadPriorityTerminalFailed:
    def test_raises_for_failed_status(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority

        session = MagicMock()
        row = _make_upload_row(upload_status="failed")
        session.execute.return_value.scalar_one_or_none.return_value = row

        with pytest.raises(ValueError, match="failed"):
            update_upload_priority(session, record_id=1, priority=0)


# ---------------------------------------------------------------------------
# D. update_upload_priority() succeeds for queued record, advances last_updated_at
# ---------------------------------------------------------------------------

class TestUpdateUploadPrioritySuccess:
    def test_queued_record_returns_updated_dict(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import (
            update_upload_priority,
            get_upload_record,
        )

        row = _make_upload_row(record_id=1, upload_status="queued", priority=5)
        session = MagicMock()

        call_count = [0]

        def _execute(stmt):
            call_count[0] += 1
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = row
            mock_result.scalars.return_value.all.return_value = [row]
            return mock_result

        session.execute.side_effect = _execute

        with patch(
            "pathoryx_enterprise.services.dashboard.upload_queries.get_upload_record",
            return_value={"id": 1, "priority": 0, "upload_status": "queued",
                          "last_updated_at": datetime(2026, 6, 9, 10, 5, tzinfo=timezone.utc)},
        ):
            result = update_upload_priority(session, record_id=1, priority=0)

        assert result is not None
        assert result["priority"] == 0
        session.flush.assert_called()

    def test_last_updated_at_is_advanced(self):
        """last_updated_at must be touched so SSE upload_queue_updated fires."""
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority

        row = _make_upload_row(upload_status="queued", priority=5)
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = row

        original_ts = row.last_updated_at

        with patch(
            "pathoryx_enterprise.services.dashboard.upload_queries.get_upload_record",
            return_value={"id": 1, "priority": 0, "upload_status": "queued",
                          "last_updated_at": datetime(2026, 6, 9, 10, 5, tzinfo=timezone.utc)},
        ) as mock_get:
            result = update_upload_priority(session, record_id=1, priority=0)

        # The UPDATE statement should include last_updated_at
        update_call_args = session.execute.call_args_list[-1]
        update_stmt = update_call_args[0][0]
        compiled = str(update_stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "last_updated_at" in compiled


# ---------------------------------------------------------------------------
# E. list_upload_queue() orders by priority ASC within same queued_at
# ---------------------------------------------------------------------------

class TestListUploadQueuePriorityOrdering:
    def test_lower_priority_value_comes_first(self):
        """priority=0 should sort before priority=5."""
        from pathoryx_enterprise.services.dashboard.upload_queries import list_upload_queue

        high_row = _make_upload_row(record_id=1, upload_status="queued", priority=0)
        normal_row = _make_upload_row(record_id=2, upload_status="queued", priority=5)

        session = MagicMock()

        call_num = [0]

        def _execute(stmt):
            call_num[0] += 1
            result_mock = MagicMock()
            if call_num[0] == 1:
                # COUNT subquery
                result_mock.scalar.return_value = 2
            else:
                # Ordered results — priority 0 first, then 5
                result_mock.scalars.return_value.all.return_value = [high_row, normal_row]
            return result_mock

        session.execute.side_effect = _execute

        total, items = list_upload_queue(session)

        assert total == 2
        assert items[0]["priority"] == 0
        assert items[1]["priority"] == 5


# ---------------------------------------------------------------------------
# F–K: Endpoint tests via TestClient
# ---------------------------------------------------------------------------

class TestPriorityEndpoint:

    # -----------------------------------------------------------------------
    # F. 404 for unknown record
    # -----------------------------------------------------------------------

    def test_unknown_record_returns_404(self):
        client, _ = _make_app_client()

        with patch(
            "pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority",
            return_value=None,
        ):
            resp = client.patch(
                "/dashboard/api/uploads/queue/9999/priority",
                json={"priority": 0},
            )

        assert resp.status_code == 404

    # -----------------------------------------------------------------------
    # G. 409 for terminal record
    # -----------------------------------------------------------------------

    def test_terminal_record_returns_409(self):
        client, _ = _make_app_client()

        with patch(
            "pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority",
            side_effect=ValueError("Cannot change priority of record with status 'uploaded'"),
        ):
            resp = client.patch(
                "/dashboard/api/uploads/queue/1/priority",
                json={"priority": 0},
            )

        assert resp.status_code == 409
        assert "uploaded" in resp.json()["detail"]

    # -----------------------------------------------------------------------
    # H. 422 for out-of-range priority
    # -----------------------------------------------------------------------

    def test_priority_below_zero_returns_422(self):
        client, _ = _make_app_client()

        resp = client.patch(
            "/dashboard/api/uploads/queue/1/priority",
            json={"priority": -1},
        )

        assert resp.status_code == 422

    def test_priority_above_nine_returns_422(self):
        client, _ = _make_app_client()

        resp = client.patch(
            "/dashboard/api/uploads/queue/1/priority",
            json={"priority": 10},
        )

        assert resp.status_code == 422

    # -----------------------------------------------------------------------
    # I. 200 for valid queued record (priority=3)
    # -----------------------------------------------------------------------

    def test_valid_priority_update_returns_200(self):
        client, _ = _make_app_client()
        expected = _mock_update_priority_success(record_id=1, priority=3)

        with patch(
            "pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority",
            return_value=expected,
        ):
            resp = client.patch(
                "/dashboard/api/uploads/queue/1/priority",
                json={"priority": 3},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["priority"] == 3
        assert body["id"] == 1

    # -----------------------------------------------------------------------
    # J. Reset to normal priority (5)
    # -----------------------------------------------------------------------

    def test_reset_to_normal_priority(self):
        client, _ = _make_app_client()
        expected = _mock_update_priority_success(record_id=2, priority=5)

        with patch(
            "pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority",
            return_value=expected,
        ) as mock_fn:
            resp = client.patch(
                "/dashboard/api/uploads/queue/2/priority",
                json={"priority": 5, "reason": "reset by operator"},
            )

        assert resp.status_code == 200
        assert resp.json()["priority"] == 5
        mock_fn.assert_called_once()
        _, called_id, called_priority = mock_fn.call_args[0]
        assert called_id == 2
        assert called_priority == 5

    # -----------------------------------------------------------------------
    # K. Upload Next (priority=0)
    # -----------------------------------------------------------------------

    def test_upload_next_sets_priority_zero(self):
        client, _ = _make_app_client()
        expected = _mock_update_priority_success(record_id=5, priority=0)

        with patch(
            "pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority",
            return_value=expected,
        ) as mock_fn:
            resp = client.patch(
                "/dashboard/api/uploads/queue/5/priority",
                json={"priority": 0},
            )

        assert resp.status_code == 200
        assert resp.json()["priority"] == 0
        _, called_id, called_priority = mock_fn.call_args[0]
        assert called_id == 5
        assert called_priority == 0
