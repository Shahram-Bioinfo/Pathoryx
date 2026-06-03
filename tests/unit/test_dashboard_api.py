"""
Unit tests for the Pathoryx Dashboard API.

All DB interactions are mocked so no real database is required.
Tests verify: HTTP status codes, response shape, and graceful degradation
when query functions raise SQLAlchemyError.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
httpx = pytest.importorskip("httpx", reason="httpx not installed")

from fastapi.testclient import TestClient  # noqa: E402

from pathoryx_enterprise.services.dashboard.app import create_app, get_db  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def mock_db():
    """A MagicMock that stands in for a SQLAlchemy Session."""
    return MagicMock()


@pytest.fixture(scope="module")
def client(app, mock_db):
    """TestClient with the DB dependency overridden to return mock_db."""
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

_QUERY_MODULE = "pathoryx_enterprise.services.dashboard.app.q"


# ---------------------------------------------------------------------------
# /dashboard/api/overview
# ---------------------------------------------------------------------------


class TestOverview:
    def test_returns_200_with_empty_db(self, client):
        with (
            patch(f"{_QUERY_MODULE}.count_slides_by_status", return_value={}),
            patch(f"{_QUERY_MODULE}.count_triggers_by_status", return_value={}),
            patch(f"{_QUERY_MODULE}.count_runners_by_status", return_value={}),
            patch(f"{_QUERY_MODULE}.count_events_since", return_value=0),
        ):
            resp = client.get("/dashboard/api/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slides"]["total"] == 0
        assert data["triggers"]["pending"] == 0
        assert data["runners"]["active"] == 0
        assert data["events_last_24h"] == 0
        assert "as_of" in data

    def test_returns_200_with_populated_data(self, client):
        with (
            patch(
                f"{_QUERY_MODULE}.count_slides_by_status",
                return_value={"uploaded": 80, "qc_failed": 5},
            ),
            patch(
                f"{_QUERY_MODULE}.count_triggers_by_status",
                return_value={"pending": 3, "running": 1, "failed": 2, "completed": 100},
            ),
            patch(
                f"{_QUERY_MODULE}.count_runners_by_status",
                return_value={"active": 4, "crashed": 1},
            ),
            patch(f"{_QUERY_MODULE}.count_events_since", return_value=42),
        ):
            resp = client.get("/dashboard/api/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slides"]["total"] == 85
        assert data["slides"]["by_status"]["uploaded"] == 80
        assert data["triggers"]["pending"] == 3
        assert data["runners"]["active"] == 4
        assert data["runners"]["stale"] == 1  # crashed maps to stale
        assert data["events_last_24h"] == 42

    def test_degrades_gracefully_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError

        with (
            patch(
                f"{_QUERY_MODULE}.count_slides_by_status",
                side_effect=OperationalError("", {}, Exception()),
            ),
            patch(
                f"{_QUERY_MODULE}.count_triggers_by_status",
                side_effect=OperationalError("", {}, Exception()),
            ),
            patch(
                f"{_QUERY_MODULE}.count_runners_by_status",
                side_effect=OperationalError("", {}, Exception()),
            ),
            patch(
                f"{_QUERY_MODULE}.count_events_since",
                side_effect=OperationalError("", {}, Exception()),
            ),
        ):
            resp = client.get("/dashboard/api/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slides"]["total"] == 0


# ---------------------------------------------------------------------------
# /dashboard/api/slides
# ---------------------------------------------------------------------------


class TestSlides:
    def _make_slide(self) -> MagicMock:
        slide = MagicMock()
        slide.internal_id = 1
        slide.global_artifact_id = "GAI-001"
        slide.original_filename = "slide.svs"
        slide.current_filename = "slide_renamed.svs"
        slide.status = "uploaded"
        slide.file_size = 1024 * 1024 * 500
        slide.file_format = "SVS"
        slide.scanner_id = "SC-01"
        slide.scanner_name = "Aperio GT450"
        slide.artifact_type = "wsi"
        slide.created_at = _NOW
        slide.updated_at = _NOW
        slide.original_path = None
        slide.current_file_path = None
        return slide

    def test_returns_200_empty(self, client):
        with patch(f"{_QUERY_MODULE}.list_slides", return_value=(0, [])):
            resp = client.get("/dashboard/api/slides")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1

    def test_returns_slides(self, client):
        slide = self._make_slide()
        with patch(f"{_QUERY_MODULE}.list_slides", return_value=(1, [slide])):
            resp = client.get("/dashboard/api/slides")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["global_artifact_id"] == "GAI-001"
        assert data["items"][0]["status"] == "uploaded"

    def test_status_filter_passed_to_query(self, client):
        with patch(f"{_QUERY_MODULE}.list_slides", return_value=(0, [])) as mock:
            client.get("/dashboard/api/slides?status=qc_failed")
        mock.assert_called_once()
        _, kwargs = mock.call_args
        assert kwargs.get("status") == "qc_failed" or mock.call_args[0][1] == "qc_failed"

    def test_pagination_defaults(self, client):
        with patch(f"{_QUERY_MODULE}.list_slides", return_value=(0, [])):
            resp = client.get("/dashboard/api/slides")
        assert resp.json()["page"] == 1
        assert resp.json()["page_size"] == 50

    def test_degrades_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError

        with patch(
            f"{_QUERY_MODULE}.list_slides",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/slides")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# /dashboard/api/slides/{global_artifact_id}
# ---------------------------------------------------------------------------


class TestSlideDetail:
    def _make_slide(self) -> MagicMock:
        slide = MagicMock()
        slide.internal_id = 7
        slide.global_artifact_id = "GAI-007"
        slide.original_filename = "detail.svs"
        slide.current_filename = "detail.svs"
        slide.status = "uploaded"
        slide.file_size = 1024
        slide.file_format = "SVS"
        slide.scanner_id = None
        slide.scanner_name = None
        slide.artifact_type = "wsi"
        slide.created_at = _NOW
        slide.updated_at = _NOW
        slide.original_path = None
        slide.current_file_path = None
        return slide

    def test_returns_404_when_not_found(self, client):
        with patch(f"{_QUERY_MODULE}.get_slide_by_artifact_id", return_value=None):
            resp = client.get("/dashboard/api/slides/NONEXISTENT")
        assert resp.status_code == 404

    def test_returns_slide_with_no_downstream_results(self, client):
        slide = self._make_slide()
        with (
            patch(f"{_QUERY_MODULE}.get_slide_by_artifact_id", return_value=slide),
            patch(f"{_QUERY_MODULE}.get_latest_qc_result", return_value=None),
            patch(f"{_QUERY_MODULE}.get_latest_conversion_result", return_value=None),
            patch(f"{_QUERY_MODULE}.get_latest_upload_result", return_value=None),
            patch(f"{_QUERY_MODULE}.list_events_for_artifact", return_value=[]),
            patch(f"{_QUERY_MODULE}.list_triggers_for_artifact", return_value=[]),
            patch(f"{_QUERY_MODULE}.list_recovery_for_artifact", return_value=[]),
            patch(f"{_QUERY_MODULE}.get_extraction_result", return_value=None),
        ):
            resp = client.get("/dashboard/api/slides/GAI-007")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_record"]["global_artifact_id"] == "GAI-007"
        assert data["qc_result"] is None
        assert data["conversion_result"] is None
        assert data["upload_result"] is None
        assert data["recent_events"] == []
        assert data["triggers"] == []
        assert data["recovery_events"] == []
        assert data["extraction_result"] is None

    def test_returns_triggers_when_present(self, client):
        slide = self._make_slide()
        trigger = MagicMock()
        trigger.internal_id = 1
        trigger.source_service = "babelshark"
        trigger.target_service = "qc_service"
        trigger.stage_name = "qc"
        trigger.trigger_status = "completed"
        trigger.retry_count = 0
        trigger.max_retries = 3
        trigger.error_message = None
        trigger.triggered_at = _NOW
        trigger.accepted_at = _NOW
        trigger.started_at = _NOW
        trigger.finished_at = _NOW
        trigger.correlation_id = None
        with (
            patch(f"{_QUERY_MODULE}.get_slide_by_artifact_id", return_value=slide),
            patch(f"{_QUERY_MODULE}.get_latest_qc_result", return_value=None),
            patch(f"{_QUERY_MODULE}.get_latest_conversion_result", return_value=None),
            patch(f"{_QUERY_MODULE}.get_latest_upload_result", return_value=None),
            patch(f"{_QUERY_MODULE}.list_events_for_artifact", return_value=[]),
            patch(f"{_QUERY_MODULE}.list_triggers_for_artifact", return_value=[trigger]),
            patch(f"{_QUERY_MODULE}.list_recovery_for_artifact", return_value=[]),
            patch(f"{_QUERY_MODULE}.get_extraction_result", return_value=None),
        ):
            resp = client.get("/dashboard/api/slides/GAI-007")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["triggers"]) == 1
        assert data["triggers"][0]["source_service"] == "babelshark"
        assert data["triggers"][0]["stage_name"] == "qc"
        assert data["triggers"][0]["trigger_status"] == "completed"


# ---------------------------------------------------------------------------
# /dashboard/api/events/recent
# ---------------------------------------------------------------------------


class TestRecentEvents:
    def _make_event(self) -> MagicMock:
        ev = MagicMock()
        ev.event_id = 1
        ev.event_type = "qc.passed"
        ev.global_artifact_id = "GAI-001"
        ev.global_run_id = "RUN-001"
        ev.service_name = "qc"
        ev.occurred_at = _NOW
        ev.aggregate_type = "slide"
        ev.aggregate_id = "GAI-001"
        ev.event_payload = None
        return ev

    def test_returns_200_empty(self, client):
        with patch(f"{_QUERY_MODULE}.list_recent_events", return_value=[]):
            resp = client.get("/dashboard/api/events/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["count"] == 0

    def test_returns_events(self, client):
        event = self._make_event()
        with patch(f"{_QUERY_MODULE}.list_recent_events", return_value=[event]):
            resp = client.get("/dashboard/api/events/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"][0]["event_type"] == "qc.passed"

    def test_degrades_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError

        with patch(
            f"{_QUERY_MODULE}.list_recent_events",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/events/recent")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# /dashboard/api/queues
# ---------------------------------------------------------------------------


class TestQueues:
    def test_returns_200_empty(self, client):
        with patch(f"{_QUERY_MODULE}.get_queue_status", return_value=[]):
            resp = client.get("/dashboard/api/queues")
        assert resp.status_code == 200
        data = resp.json()
        assert data["queues"] == []
        assert data["total_pending"] == 0
        assert data["total_failed"] == 0

    def test_aggregates_totals(self, client):
        rows = [
            {"target_service": "qc", "pending": 5, "running": 1, "failed": 2, "completed": 100},
            {
                "target_service": "dicom",
                "pending": 3,
                "running": 0,
                "failed": 0,
                "completed": 50,
            },
        ]
        with patch(f"{_QUERY_MODULE}.get_queue_status", return_value=rows):
            resp = client.get("/dashboard/api/queues")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_pending"] == 8
        assert data["total_failed"] == 2
        assert len(data["queues"]) == 2

    def test_degrades_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError

        with patch(
            f"{_QUERY_MODULE}.get_queue_status",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/queues")
        assert resp.status_code == 200
        assert resp.json()["total_pending"] == 0


# ---------------------------------------------------------------------------
# /dashboard/api/recovery
# ---------------------------------------------------------------------------


class TestRecovery:
    def _make_change(self) -> MagicMock:
        c = MagicMock()
        c.internal_id = 1
        c.change_type = "rename"
        c.watch_folder_label = "failed"
        c.global_artifact_id = "GAI-001"
        c.review_status = "detected"
        c.detected_at = _NOW
        c.inferred_action = "rename_recovery"
        c.recovery_outcome = None
        c.recovery_reason = None
        c.recovered_at = None
        c.created_at = _NOW
        return c

    def test_returns_200_empty(self, client):
        with (
            patch(f"{_QUERY_MODULE}.list_recovery_items", return_value=(0, [])),
            patch(f"{_QUERY_MODULE}.count_recovery_by_status", return_value={}),
        ):
            resp = client.get("/dashboard/api/recovery")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["by_status"] == {}

    def test_returns_items(self, client):
        change = self._make_change()
        with (
            patch(f"{_QUERY_MODULE}.list_recovery_items", return_value=(1, [change])),
            patch(f"{_QUERY_MODULE}.count_recovery_by_status",
                  return_value={"detected": 1}),
        ):
            resp = client.get("/dashboard/api/recovery")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["change_type"] == "rename"
        assert data["by_status"]["detected"] == 1

    def test_review_status_filter(self, client):
        with (
            patch(f"{_QUERY_MODULE}.list_recovery_items", return_value=(0, [])) as mock,
            patch(f"{_QUERY_MODULE}.count_recovery_by_status", return_value={}),
        ):
            client.get("/dashboard/api/recovery?review_status=requeued")
        mock.assert_called_once()
        args = mock.call_args
        assert args[1].get("review_status") == "requeued" or args[0][1] == "requeued"

    def test_degrades_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError

        with (
            patch(f"{_QUERY_MODULE}.list_recovery_items",
                  side_effect=OperationalError("", {}, Exception())),
            patch(f"{_QUERY_MODULE}.count_recovery_by_status", return_value={}),
        ):
            resp = client.get("/dashboard/api/recovery")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["by_status"] == {}

    def test_by_status_degrades_independently(self, client):
        """count_recovery_by_status failure does not affect the items response."""
        from sqlalchemy.exc import OperationalError

        change = self._make_change()
        with (
            patch(f"{_QUERY_MODULE}.list_recovery_items", return_value=(1, [change])),
            patch(f"{_QUERY_MODULE}.count_recovery_by_status",
                  side_effect=OperationalError("", {}, Exception())),
        ):
            resp = client.get("/dashboard/api/recovery")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["change_type"] == "rename"
        assert data["by_status"] == {}   # count query failed, falls back to empty


# ---------------------------------------------------------------------------
# /dashboard/api/failures
# ---------------------------------------------------------------------------


class TestFailures:
    def _make_slide(self) -> MagicMock:
        slide = MagicMock()
        slide.internal_id = 1
        slide.global_artifact_id = "GAI-001"
        slide.original_filename = "bad.svs"
        slide.status = "qc_failed"
        slide.updated_at = _NOW
        # New fields — must be set explicitly so Pydantic doesn't receive a MagicMock
        slide.scanner_name = None
        slide.scanner_id = None
        slide.current_file_path = None
        slide.file_format = None
        return slide

    def test_returns_200_empty(self, client):
        with (
            patch(f"{_QUERY_MODULE}.list_failed_slides", return_value=[]),
            patch(f"{_QUERY_MODULE}.list_failed_triggers", return_value=[]),
            patch(f"{_QUERY_MODULE}.list_artifact_ids_with_recovery", return_value=[]),
        ):
            resp = client.get("/dashboard/api/failures")
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed_slides"] == []
        assert data["failed_triggers"] == []
        assert data["artifact_ids_with_recovery"] == []

    def test_partial_failure_returns_partial_data(self, client):
        from sqlalchemy.exc import OperationalError

        slide = self._make_slide()
        with (
            patch(f"{_QUERY_MODULE}.list_failed_slides", return_value=[slide]),
            patch(f"{_QUERY_MODULE}.list_failed_triggers",
                  side_effect=OperationalError("", {}, Exception())),
            patch(f"{_QUERY_MODULE}.list_artifact_ids_with_recovery", return_value=[]),
        ):
            resp = client.get("/dashboard/api/failures")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["failed_slides"]) == 1
        assert data["failed_triggers"] == []
        assert data["artifact_ids_with_recovery"] == []

    def test_recovery_ids_populated(self, client):
        """Artifacts with TechnicianChange records appear in artifact_ids_with_recovery."""
        slide = self._make_slide()
        with (
            patch(f"{_QUERY_MODULE}.list_failed_slides", return_value=[slide]),
            patch(f"{_QUERY_MODULE}.list_failed_triggers", return_value=[]),
            patch(f"{_QUERY_MODULE}.list_artifact_ids_with_recovery",
                  return_value=["GAI-001"]),
        ):
            resp = client.get("/dashboard/api/failures")
        assert resp.status_code == 200
        data = resp.json()
        assert "GAI-001" in data["artifact_ids_with_recovery"]

    def test_recovery_check_degrades_independently(self, client):
        """list_artifact_ids_with_recovery failure does not break the endpoint."""
        from sqlalchemy.exc import OperationalError

        slide = self._make_slide()
        with (
            patch(f"{_QUERY_MODULE}.list_failed_slides", return_value=[slide]),
            patch(f"{_QUERY_MODULE}.list_failed_triggers", return_value=[]),
            patch(f"{_QUERY_MODULE}.list_artifact_ids_with_recovery",
                  side_effect=OperationalError("", {}, Exception())),
        ):
            resp = client.get("/dashboard/api/failures")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["failed_slides"]) == 1
        assert data["artifact_ids_with_recovery"] == []


# ---------------------------------------------------------------------------
# /dashboard/api/services/health
# ---------------------------------------------------------------------------


class TestServicesHealth:
    def _make_runner(self) -> MagicMock:
        r = MagicMock()
        r.runner_id = "runner-abc"
        r.service_name = "qc"
        r.host_id = "host-01"
        r.pid = 12345
        r.status = "active"
        r.environment = "production"
        r.service_version = "1.0.0"
        r.started_at = _NOW
        r.last_heartbeat_at = _NOW
        return r

    def test_returns_200_empty(self, client):
        with patch(f"{_QUERY_MODULE}.list_runners", return_value=[]):
            resp = client.get("/dashboard/api/services/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runners"] == []
        assert data["stale_threshold_seconds"] == 120
        assert "as_of" in data

    def test_returns_runners(self, client):
        runner = self._make_runner()
        with patch(f"{_QUERY_MODULE}.list_runners", return_value=[runner]):
            resp = client.get("/dashboard/api/services/health")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["runners"]) == 1
        assert data["runners"][0]["service_name"] == "qc"
        assert data["runners"][0]["status"] == "active"

    def test_degrades_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError

        with patch(
            f"{_QUERY_MODULE}.list_runners",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/services/health")
        assert resp.status_code == 200
        assert resp.json()["runners"] == []


# ---------------------------------------------------------------------------
# /dashboard/api/stream  (SSE endpoint)
#
# Testing an infinite SSE stream with TestClient requires care:
#   - We test the response HEADERS and the first few yielded bytes only.
#   - The generator is patched to yield a finite sequence so the request
#     completes without spinning forever.
# ---------------------------------------------------------------------------

_ACTIONS_MODULE = "pathoryx_enterprise.services.dashboard.app"


# ---------------------------------------------------------------------------
# /dashboard/api/recovery/watch-folders
# ---------------------------------------------------------------------------


class TestWatchFolders:
    def test_returns_200_empty(self, client):
        with (
            patch(f"{_QUERY_MODULE}.get_watch_folder_stats", return_value=[]),
            patch(
                f"{_ACTIONS_MODULE}.RecoverySentrySettings",
                side_effect=Exception("no config"),
                create=True,
            ),
        ):
            resp = client.get("/dashboard/api/recovery/watch-folders")
        assert resp.status_code == 200
        data = resp.json()
        assert "folders" in data
        assert "as_of" in data

    def test_returns_folder_stats(self, client):
        rows = [
            {
                "folder_label": "failed",
                "folder_path": "/data/failed",
                "total_files": 5,
                "recently_changed": 1,
                "awaiting_review": 2,
                "auto_recovered": 3,
                "last_scan_time": _NOW,
            }
        ]
        with (
            patch(f"{_QUERY_MODULE}.get_watch_folder_stats", return_value=rows),
            patch(
                f"{_ACTIONS_MODULE}.RecoverySentrySettings",
                side_effect=Exception("no config"),
                create=True,
            ),
        ):
            resp = client.get("/dashboard/api/recovery/watch-folders")
        assert resp.status_code == 200
        data = resp.json()
        # Endpoint merges DB rows with configured folders; at minimum "failed" must be present
        labels = [f["label"] for f in data["folders"]]
        assert "failed" in labels
        failed = next(f for f in data["folders"] if f["label"] == "failed")
        assert failed["total_files"] == 5
        assert failed["auto_recovered"] == 3

    def test_degrades_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError

        with (
            patch(
                f"{_QUERY_MODULE}.get_watch_folder_stats",
                side_effect=OperationalError("", {}, Exception()),
            ),
        ):
            resp = client.get("/dashboard/api/recovery/watch-folders")
        # Degrades gracefully: returns 200 with zero-count folders from config
        assert resp.status_code == 200
        data = resp.json()
        assert "folders" in data
        assert "as_of" in data
        # All folders should have total_files=0 since DB failed
        for folder in data["folders"]:
            assert folder["total_files"] == 0


# ---------------------------------------------------------------------------
# /dashboard/api/recovery/files
# ---------------------------------------------------------------------------


class TestMonitoredFiles:
    def _make_row(self) -> dict:
        return {
            "file_id": 42,
            "filename": "slide.svs",
            "file_path": "/data/failed/slide.svs",
            "folder_label": "failed",
            "folder_path": "/data/failed",
            "first_seen_at": _NOW,
            "last_seen_at": _NOW,
            "file_size": 1024 * 1024 * 200,
            "slide_id": None,
            "case_id": None,
            "extension": ".svs",
            "global_artifact_id": None,
            "file_record_internal_id": None,
            "change_id": None,
            "change_type": None,
            "review_status": None,
            "recovery_outcome": None,
            "recovery_reason": None,
            "detected_at": None,
            "inferred_action": None,
        }

    def test_returns_200_empty(self, client):
        with patch(f"{_QUERY_MODULE}.list_monitored_files", return_value=(0, [])):
            resp = client.get("/dashboard/api/recovery/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_returns_files_without_recovery_events(self, client):
        """Files with no TechnicianChange (change_id=None) must be included."""
        row = self._make_row()
        with patch(f"{_QUERY_MODULE}.list_monitored_files", return_value=(1, [row])):
            resp = client.get("/dashboard/api/recovery/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["change_id"] is None
        assert data["items"][0]["review_status"] is None

    def test_files_with_recovery_enriched(self, client):
        row = {**self._make_row(), "change_id": 7, "review_status": "detected", "recovery_outcome": "manual_review_required"}
        with patch(f"{_QUERY_MODULE}.list_monitored_files", return_value=(1, [row])):
            resp = client.get("/dashboard/api/recovery/files")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["change_id"] == 7
        assert item["review_status"] == "detected"
        assert item["recovery_outcome"] == "manual_review_required"

    def test_folder_type_filter_passed(self, client):
        with patch(f"{_QUERY_MODULE}.list_monitored_files", return_value=(0, [])) as mock:
            client.get("/dashboard/api/recovery/files?folder_type=suspicious")
        mock.assert_called_once()
        call_kwargs = mock.call_args[1]
        assert call_kwargs.get("folder_type") == "suspicious"

    def test_degrades_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError

        with patch(
            f"{_QUERY_MODULE}.list_monitored_files",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/recovery/files")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# /dashboard/api/recovery/files/{file_id}/technician-rename
# ---------------------------------------------------------------------------


class TestTechnicianRename:
    def _make_snapshot(self) -> MagicMock:
        snap = MagicMock()
        snap.internal_id = 42
        snap.file_path = "/data/failed/badname.svs"
        snap.filename = "badname.svs"
        snap.folder_path = "/data/failed"
        snap.file_size = 1024
        snap.global_artifact_id = None
        snap.file_record_internal_id = None
        return snap

    def test_confirm_false_returns_422(self, client):
        resp = client.post(
            "/dashboard/api/recovery/files/42/technician-rename",
            json={"proposed_filename": "N2024002863SA-1-1-H&E.svs", "confirm": False},
        )
        assert resp.status_code == 422

    def test_file_not_found_returns_404(self, client):
        with (
            patch(f"{_QUERY_MODULE}.get_monitored_file", return_value=None),
            patch(
                "pathoryx_enterprise.services.dashboard.app.RecoverySentrySettings",
                create=True,
            ),
        ):
            resp = client.post(
                "/dashboard/api/recovery/files/99/technician-rename",
                json={"proposed_filename": "N2024002863SA-1-1-H&E.svs", "confirm": True},
            )
        assert resp.status_code == 404

    def test_path_traversal_rejected(self, client):
        snap = self._make_snapshot()
        from types import SimpleNamespace
        mock_settings = SimpleNamespace(
            watch_folders=[__import__("pathlib").Path("/data/failed")],
            final_destination=__import__("pathlib").Path("/data/final"),
            auto_recover_valid_slide_id=True,
            add_timestamp_if_missing=True,
            overwrite_existing=False,
            duplicate_strategy="suffix",
            next_stage_target_service="qc_service",
            next_stage_name="qc",
            allowed_roots=[__import__("pathlib").Path("/data/failed")],
            allow_filesystem_timestamp_fallback=False,
        )
        with (
            patch(f"{_QUERY_MODULE}.get_monitored_file", return_value=snap),
            patch(
                "pathoryx_enterprise.services.recovery_sentry.config.RecoverySentrySettings",
                return_value=mock_settings,
            ),
        ):
            resp = client.post(
                "/dashboard/api/recovery/files/42/technician-rename",
                json={"proposed_filename": "../../etc/passwd", "confirm": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["outcome"] == "validation_failed"
        assert data["validation_error"] is not None

    def test_invalid_filename_rejected(self, client):
        snap = self._make_snapshot()
        from types import SimpleNamespace
        mock_settings = SimpleNamespace(
            watch_folders=[__import__("pathlib").Path("/data/failed")],
            final_destination=__import__("pathlib").Path("/data/final"),
            auto_recover_valid_slide_id=True,
            add_timestamp_if_missing=True,
            overwrite_existing=False,
            duplicate_strategy="suffix",
            next_stage_target_service="qc_service",
            next_stage_name="qc",
            allowed_roots=[__import__("pathlib").Path("/data/failed")],
            allow_filesystem_timestamp_fallback=False,
        )
        with (
            patch(f"{_QUERY_MODULE}.get_monitored_file", return_value=snap),
            patch(
                "pathoryx_enterprise.services.recovery_sentry.config.RecoverySentrySettings",
                return_value=mock_settings,
            ),
        ):
            resp = client.post(
                "/dashboard/api/recovery/files/42/technician-rename",
                json={"proposed_filename": "totally_wrong_name.svs", "confirm": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["outcome"] == "validation_failed"

    def test_valid_rename_calls_execute_action(self, client):
        snap = self._make_snapshot()
        from pathlib import Path as _Path
        from types import SimpleNamespace
        mock_settings = SimpleNamespace(
            watch_folders=[_Path("/data/failed")],
            final_destination=_Path("/data/final"),
            auto_recover_valid_slide_id=True,
            add_timestamp_if_missing=True,
            overwrite_existing=False,
            duplicate_strategy="suffix",
            next_stage_target_service="qc_service",
            next_stage_name="qc",
            allowed_roots=[_Path("/data/failed")],
            allow_filesystem_timestamp_fallback=False,
        )
        expected_result = {
            "outcome": "auto_recovered",
            "reason": None,
            "destination_path": "/data/final/N2024002863/N2024002863SA-1-1-H&E.svs",
            "final_filename": "N2024002863SA-1-1-H&E.svs",
            "case_id": "N2024002863",
            "slide_id": "N2024002863SA-1-1-H&E",
            "change_id": 1,
            "validation_error": None,
        }
        with (
            patch(f"{_QUERY_MODULE}.get_monitored_file", return_value=snap),
            patch(
                "pathoryx_enterprise.services.recovery_sentry.config.RecoverySentrySettings",
                return_value=mock_settings,
            ),
            patch(
                "pathoryx_enterprise.services.dashboard.app.execute_technician_rename",
                return_value=expected_result,
            ),
        ):
            resp = client.post(
                "/dashboard/api/recovery/files/42/technician-rename",
                json={"proposed_filename": "N2024002863SA-1-1-H&E.svs", "confirm": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["outcome"] == "auto_recovered"
        assert data["case_id"] == "N2024002863"


# ---------------------------------------------------------------------------
# /dashboard/api/recovery/files/{file_id}/label-preview
# ---------------------------------------------------------------------------


class TestLabelPreview:
    def test_returns_200_unavailable_gracefully(self, client):
        unavailable = {
            "file_id": 42,
            "filename": "slide.svs",
            "available": False,
            "unavailable_reason": "no_linked_record",
            "slide_id": None,
            "case_id": None,
            "scanner_id": None,
            "scanner_vendor": None,
            "stain_type": None,
            "suggested_filename": "slide.svs",
            "datamatrix_raw": None,
            "extraction_metadata": None,
        }
        with patch(f"{_QUERY_MODULE}.get_label_preview_data", return_value=unavailable):
            resp = client.get("/dashboard/api/recovery/files/42/label-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert data["unavailable_reason"] == "no_linked_record"

    def test_returns_available_data(self, client):
        available = {
            "file_id": 42,
            "filename": "slide.svs",
            "available": True,
            "unavailable_reason": None,
            "slide_id": "N2024002863SA-1-1-H&E",
            "case_id": "N2024002863",
            "scanner_id": "SC-01",
            "scanner_vendor": "Aperio",
            "stain_type": "H&E",
            "suggested_filename": "N2024002863SA-1-1-H&E.svs",
            "datamatrix_raw": "N2024002863SA-1-1-H&E",
            "extraction_metadata": {"extraction_status": "success"},
        }
        with patch(f"{_QUERY_MODULE}.get_label_preview_data", return_value=available):
            resp = client.get("/dashboard/api/recovery/files/42/label-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["scanner_vendor"] == "Aperio"
        assert data["datamatrix_raw"] == "N2024002863SA-1-1-H&E"

    def test_degrades_on_query_error(self, client):
        from sqlalchemy.exc import OperationalError

        with patch(
            f"{_QUERY_MODULE}.get_label_preview_data",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/recovery/files/42/label-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert data["unavailable_reason"] == "query_failed"


# ---------------------------------------------------------------------------
# actions.py — path validation unit tests (no DB, no filesystem)
# ---------------------------------------------------------------------------


class TestActionValidation:
    def test_path_traversal_rejected(self):
        from pathoryx_enterprise.services.dashboard.actions import ActionError, _validate_proposed_filename

        for bad in ["../../etc/passwd", "../other", "sub/dir.svs", "a\\b.svs"]:
            with pytest.raises(ActionError):
                _validate_proposed_filename(bad)

    def test_unsupported_extension_rejected(self):
        from pathoryx_enterprise.services.dashboard.actions import ActionError, _validate_proposed_filename

        with pytest.raises(ActionError, match="extension"):
            _validate_proposed_filename("N2024002863SA-1-1-H&E.exe")

    def test_valid_filename_passes(self):
        from pathoryx_enterprise.services.dashboard.actions import _validate_proposed_filename

        _validate_proposed_filename("N2024002863SA-1-1-H&E.svs")  # should not raise

    def test_resolve_watch_folder_rejects_outside_path(self):
        from pathlib import Path
        from pathoryx_enterprise.services.dashboard.actions import ActionError, _resolve_watch_folder

        allowed = [Path("/tmp/failed"), Path("/tmp/suspicious")]
        with pytest.raises(ActionError):
            _resolve_watch_folder(Path("/etc/passwd"), allowed)

    def test_manual_detection_still_works(self):
        """
        Regression: slide_id_parser is shared — verify parse_slide_id still works
        for manual rename paths after actions.py is imported.
        """
        from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import parse_slide_id

        result = parse_slide_id("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        assert result is not None
        assert result.case_id == "N2024002863"
        assert result.has_timestamp is True


_SSE_MODULE = "pathoryx_enterprise.services.dashboard.app"


class TestSSEEndpoint:
    def test_stream_returns_event_stream_content_type(self, app, mock_db):
        """
        The /stream endpoint must reply with Content-Type: text/event-stream
        so browsers can use the EventSource API.
        """
        async def _finite_gen(request):
            yield b"event: queue_updated\ndata: {}\n\n"

        with patch(f"{_SSE_MODULE}._make_event_stream", side_effect=_finite_gen):
            with TestClient(app) as c:
                resp = c.get("/dashboard/api/stream")

        ct = resp.headers.get("content-type", "")
        assert "text/event-stream" in ct

    def test_stream_returns_cache_control_no_cache(self, app, mock_db):
        """Proxies must not cache SSE responses."""
        async def _finite_gen(request):
            yield b""

        with patch(f"{_SSE_MODULE}._make_event_stream", side_effect=_finite_gen):
            with TestClient(app) as c:
                resp = c.get("/dashboard/api/stream")

        cc = resp.headers.get("cache-control", "")
        assert "no-cache" in cc

    def test_stream_emits_event_type_and_data_fields(self, app, mock_db):
        """
        Each yielded chunk must follow SSE wire format:
          event: <type>\\ndata: <json>\\n\\n
        """
        import json as _json

        payload = {"type": "queue_updated", "ts": "2026-06-03T12:00:00+00:00"}

        async def _finite_gen(request):
            line = f"event: queue_updated\ndata: {_json.dumps(payload)}\n\n"
            yield line.encode()

        with patch(f"{_SSE_MODULE}._make_event_stream", side_effect=_finite_gen):
            with TestClient(app) as c:
                resp = c.get("/dashboard/api/stream")

        body = resp.text
        assert "event: queue_updated" in body
        assert '"type": "queue_updated"' in body

    def test_stream_event_route_is_registered(self, client):
        """
        Verify the route exists — even without a generator override the
        endpoint must respond (not 404).  The response may be 200 or 500
        depending on whether the DB is reachable; what matters is not 404.
        """
        with patch(
            f"{_SSE_MODULE}._make_event_stream",
            side_effect=lambda req: (x for x in [b""]),
        ):
            resp = client.get("/dashboard/api/stream")
        assert resp.status_code != 404
