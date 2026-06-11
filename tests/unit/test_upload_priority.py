"""
Phase 4.6D — Simplified Upload Priority Model tests.

Priority model:
  0 = UPLOAD_NEXT  — operator-flagged "jump the queue"
  1 = HIGH         — watch folder or manual operator flag
  5 = NORMAL       — default

API uses mode strings: "upload_next" | "high" | "normal" | "clear_upload_next"
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_queue_row(
    upload_status: str = "queued",
    priority: int = 5,
    priority_source: str = "default",
    priority_reason: str | None = None,
    file_record_internal_id: int | None = None,
    watch_folder_path: str | None = None,
    watch_folder_label: str | None = None,
):
    from pathoryx_enterprise.db.models.upload_tracking import EstimatedUploadQueue
    row = MagicMock(spec=EstimatedUploadQueue)
    row.id = 42
    row.upload_status = upload_status
    row.priority = priority
    row.priority_source = priority_source
    row.priority_reason = priority_reason
    row.priority_updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    row.priority_updated_by = None
    row.file_record_internal_id = file_record_internal_id
    row.estimated_upload_at = None
    row.upload_started_at = None
    row.upload_completed_at = None
    row.last_updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    row.queued_at = None
    row.slide_id = "N2024000001SA-1-1-HE"
    row.filename = "slide.svs"
    row.scanner_id = None
    row.uploader_host = None
    row.retry_count = 0
    row.file_size_bytes = None
    row.upload_speed_mbps = None
    row.failure_reason = None
    row.watch_folder_path = watch_folder_path
    row.watch_folder_label = watch_folder_label
    return row


# ---------------------------------------------------------------------------
# 1. VALID_PRIORITIES constant
# ---------------------------------------------------------------------------

class TestValidPriorities:
    def test_only_three_levels(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import VALID_PRIORITIES
        assert VALID_PRIORITIES == frozenset({0, 1, 5})

    def test_no_low_priority(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import VALID_PRIORITIES
        assert 9 not in VALID_PRIORITIES

    def test_watch_folder_valid_priorities(self):
        from pathoryx_enterprise.utils.watch_folder_priority import VALID_PRIORITIES
        assert VALID_PRIORITIES == frozenset({0, 1, 5})


# ---------------------------------------------------------------------------
# 2. WatchFolderPriorityResolver — high_priority bool config
# ---------------------------------------------------------------------------

class TestWatchFolderHighPriorityBool:
    def test_high_priority_true_maps_to_1(self):
        from pathoryx_enterprise.utils.watch_folder_priority import build_resolver_from_config
        resolver = build_resolver_from_config([
            {"path": "/data/urgent", "label": "Urgent Biopsy", "high_priority": True},
        ])
        result = resolver.resolve("/data/urgent/slide.svs")
        assert result.priority == 1
        assert result.priority_source == "watch_folder"

    def test_high_priority_false_maps_to_5(self):
        from pathoryx_enterprise.utils.watch_folder_priority import build_resolver_from_config
        resolver = build_resolver_from_config([
            {"path": "/data/routine", "label": "Routine", "high_priority": False},
        ])
        result = resolver.resolve("/data/routine/slide.svs")
        assert result.priority == 5
        assert result.priority_source == "watch_folder"

    def test_high_priority_bool_overrides_priority_int(self):
        from pathoryx_enterprise.utils.watch_folder_priority import build_resolver_from_config
        resolver = build_resolver_from_config([
            {"path": "/data/urgent", "high_priority": True, "priority": 5},
        ])
        result = resolver.resolve("/data/urgent/slide.svs")
        assert result.priority == 1

    def test_recursive_subfolder_inherits_high(self):
        from pathoryx_enterprise.utils.watch_folder_priority import build_resolver_from_config
        resolver = build_resolver_from_config([
            {"path": "/data/urgent", "high_priority": True, "label": "Urgent"},
        ])
        result = resolver.resolve("/data/urgent/scanner-A/case-001/slide.svs")
        assert result.priority == 1
        assert result.watch_folder_label == "Urgent"

    def test_nested_override_most_specific_wins(self):
        from pathoryx_enterprise.utils.watch_folder_priority import build_resolver_from_config
        resolver = build_resolver_from_config([
            {"path": "/data/urgent", "high_priority": True, "label": "Urgent"},
            {"path": "/data/urgent/routine-subset", "high_priority": False, "label": "Routine"},
        ])
        result = resolver.resolve("/data/urgent/routine-subset/slide.svs")
        assert result.priority == 5
        result2 = resolver.resolve("/data/urgent/other/slide.svs")
        assert result2.priority == 1

    def test_unmatched_file_is_normal(self):
        from pathoryx_enterprise.utils.watch_folder_priority import build_resolver_from_config
        resolver = build_resolver_from_config([
            {"path": "/data/urgent", "high_priority": True},
        ])
        result = resolver.resolve("/data/other/slide.svs")
        assert result.priority == 5
        assert result.priority_source == "default"

    def test_watch_folder_entry_rejects_invalid_priority(self):
        from pathoryx_enterprise.utils.watch_folder_priority import WatchFolderEntry
        with pytest.raises(ValueError, match="invalid priority"):
            WatchFolderEntry(path="/data", priority=9)


# ---------------------------------------------------------------------------
# 3. _resolve_mode helper
# ---------------------------------------------------------------------------

class TestResolveMode:
    def _call(self, mode, priority=5, priority_source="default",
              priority_reason=None, watch_folder_path=None):
        from pathoryx_enterprise.services.dashboard.upload_queries import _resolve_mode
        row = _make_queue_row(
            priority=priority, priority_source=priority_source,
            priority_reason=priority_reason, watch_folder_path=watch_folder_path,
        )
        return _resolve_mode(mode, row)

    def test_high_mode(self):
        p, src, reason = self._call("high")
        assert p == 1 and src == "manual" and reason is None

    def test_normal_mode(self):
        p, src, reason = self._call("normal")
        assert p == 5 and src == "default" and reason is None

    def test_upload_next_from_normal_stores_no_was_high(self):
        p, src, reason = self._call("upload_next", priority=5)
        assert p == 0 and src == "upload_next" and reason is None

    def test_upload_next_from_high_stores_was_high(self):
        p, src, reason = self._call("upload_next", priority=1)
        assert p == 0 and src == "upload_next" and reason == "was_high"

    def test_clear_upload_next_restores_watch_folder_high(self):
        p, src, reason = self._call(
            "clear_upload_next",
            priority=0, priority_source="upload_next",
            watch_folder_path="/data/urgent",
        )
        assert p == 1 and src == "watch_folder" and reason is None

    def test_clear_upload_next_restores_manual_high(self):
        p, src, reason = self._call(
            "clear_upload_next",
            priority=0, priority_source="upload_next",
            priority_reason="was_high",
        )
        assert p == 1 and src == "manual" and reason is None

    def test_clear_upload_next_restores_normal_when_no_history(self):
        p, src, reason = self._call(
            "clear_upload_next",
            priority=0, priority_source="upload_next",
        )
        assert p == 5 and src == "default" and reason is None

    def test_invalid_mode_raises(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import _resolve_mode
        row = _make_queue_row()
        with pytest.raises(ValueError, match="Invalid mode"):
            _resolve_mode("stat", row)

    def test_low_mode_invalid(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import _resolve_mode
        row = _make_queue_row()
        with pytest.raises(ValueError, match="Invalid mode"):
            _resolve_mode("low", row)


# ---------------------------------------------------------------------------
# 4. update_upload_priority_mode — unit tests
# ---------------------------------------------------------------------------

class TestUpdateUploadPriorityMode:
    def test_returns_none_for_missing_record(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority_mode
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        with patch("pathoryx_enterprise.services.dashboard.upload_queries.get_upload_record",
                   return_value=None):
            result = update_upload_priority_mode(mock_db, 99, "high")
        assert result is None

    def test_raises_for_uploaded_status(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority_mode
        mock_db = MagicMock()
        row = _make_queue_row(upload_status="uploaded")
        mock_db.execute.return_value.scalar_one_or_none.return_value = row
        with pytest.raises(ValueError, match="Cannot change priority"):
            update_upload_priority_mode(mock_db, 42, "high")

    def test_raises_for_failed_status(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority_mode
        mock_db = MagicMock()
        row = _make_queue_row(upload_status="failed")
        mock_db.execute.return_value.scalar_one_or_none.return_value = row
        with pytest.raises(ValueError, match="Cannot change priority"):
            update_upload_priority_mode(mock_db, 42, "normal")

    def test_raises_for_invalid_mode(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority_mode
        mock_db = MagicMock()
        row = _make_queue_row()
        mock_db.execute.return_value.scalar_one_or_none.return_value = row
        with pytest.raises(ValueError, match="Invalid mode"):
            update_upload_priority_mode(mock_db, 42, "stat")

    def test_uploading_status_allowed(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import update_upload_priority_mode
        mock_db = MagicMock()
        row = _make_queue_row(upload_status="uploading", priority=5)
        mock_db.execute.return_value.scalar_one_or_none.return_value = row
        expected = {"id": 42, "priority": 1}
        with patch("pathoryx_enterprise.services.dashboard.upload_queries.get_upload_record",
                   return_value=expected):
            result = update_upload_priority_mode(mock_db, 42, "high")
        assert result is not None


# ---------------------------------------------------------------------------
# 5. get_priority_summary
# ---------------------------------------------------------------------------

class TestGetPrioritySummary:
    def test_counts_upload_next_high_normal(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_priority_summary
        mock_db = MagicMock()
        rows = [
            MagicMock(priority=0, priority_source="upload_next", upload_status="queued",
                      watch_folder_path=None, watch_folder_label=None),
            MagicMock(priority=1, priority_source="watch_folder", upload_status="queued",
                      watch_folder_path="/data/urgent", watch_folder_label="Urgent Biopsy"),
            MagicMock(priority=1, priority_source="manual", upload_status="queued",
                      watch_folder_path=None, watch_folder_label=None),
            MagicMock(priority=5, priority_source="default", upload_status="queued",
                      watch_folder_path=None, watch_folder_label=None),
            MagicMock(priority=5, priority_source="default", upload_status="uploaded",
                      watch_folder_path=None, watch_folder_label=None),
        ]
        mock_db.execute.return_value.all.return_value = rows
        result = get_priority_summary(mock_db)

        assert result["by_priority"]["upload_next"] == 1
        assert result["by_priority"]["high"] == 2
        assert result["by_priority"]["normal"] == 1
        assert "stat" not in result["by_priority"]
        assert "low" not in result["by_priority"]

    def test_only_high_watch_folders_in_output(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_priority_summary
        mock_db = MagicMock()
        rows = [
            MagicMock(priority=1, priority_source="watch_folder", upload_status="queued",
                      watch_folder_path="/data/urgent", watch_folder_label="Urgent"),
            MagicMock(priority=5, priority_source="watch_folder", upload_status="queued",
                      watch_folder_path="/data/routine", watch_folder_label="Routine"),
        ]
        mock_db.execute.return_value.all.return_value = rows
        result = get_priority_summary(mock_db)

        assert len(result["watch_folders"]) == 1
        assert result["watch_folders"][0]["watch_folder_label"] == "Urgent"

    def test_by_source_keys_match_new_model(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_priority_summary
        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = []
        result = get_priority_summary(mock_db)
        assert set(result["by_source"].keys()) == {"manual", "watch_folder", "upload_next", "default"}

    def test_terminal_rows_excluded(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_priority_summary
        mock_db = MagicMock()
        rows = [
            MagicMock(priority=1, priority_source="manual", upload_status="uploaded",
                      watch_folder_path=None, watch_folder_label=None),
            MagicMock(priority=1, priority_source="manual", upload_status="failed",
                      watch_folder_path=None, watch_folder_label=None),
        ]
        mock_db.execute.return_value.all.return_value = rows
        result = get_priority_summary(mock_db)
        assert result["by_priority"]["high"] == 0


# ---------------------------------------------------------------------------
# 6. API schema
# ---------------------------------------------------------------------------

class TestUploadPriorityRequestSchema:
    def test_mode_field_required(self):
        import pydantic
        from pathoryx_enterprise.services.dashboard.schemas import UploadPriorityRequest
        with pytest.raises(pydantic.ValidationError):
            UploadPriorityRequest()

    def test_all_valid_modes_accepted(self):
        from pathoryx_enterprise.services.dashboard.schemas import UploadPriorityRequest
        for mode in ("upload_next", "high", "normal", "clear_upload_next"):
            req = UploadPriorityRequest(mode=mode)
            assert req.mode == mode

    def test_reason_optional(self):
        from pathoryx_enterprise.services.dashboard.schemas import UploadPriorityRequest
        req = UploadPriorityRequest(mode="high")
        assert req.reason is None

    def test_valid_priority_modes_set(self):
        from pathoryx_enterprise.services.dashboard.schemas import VALID_PRIORITY_MODES
        assert VALID_PRIORITY_MODES == frozenset({"upload_next", "high", "normal", "clear_upload_next"})

    def test_upload_filter_options_excludes_low(self):
        from pathoryx_enterprise.services.dashboard.schemas import UploadFilterOptions
        opts = UploadFilterOptions(scanners=[], hosts=[])
        assert 9 not in opts.priorities


# ---------------------------------------------------------------------------
# 7. API endpoint
# ---------------------------------------------------------------------------

class TestUploadPriorityEndpoint:
    def _make_app(self):
        from pathoryx_enterprise.services.dashboard.app import create_app, get_db
        from fastapi.testclient import TestClient
        app = create_app()
        mock_db = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_db
        return TestClient(app), mock_db

    def test_stat_mode_rejected_422(self):
        client, _ = self._make_app()
        resp = client.patch("/dashboard/api/uploads/queue/1/priority", json={"mode": "stat"})
        assert resp.status_code == 422

    def test_low_mode_rejected_422(self):
        client, _ = self._make_app()
        resp = client.patch("/dashboard/api/uploads/queue/1/priority", json={"mode": "low"})
        assert resp.status_code == 422

    def test_missing_mode_field_rejected_422(self):
        client, _ = self._make_app()
        resp = client.patch("/dashboard/api/uploads/queue/1/priority", json={"priority": 0})
        assert resp.status_code == 422

    def test_high_mode_accepted(self):
        client, _ = self._make_app()
        expected = {
            "id": 1, "priority": 1, "priority_source": "manual",
            "upload_status": "queued", "queued_at": "2025-01-01T00:00:00+00:00",
            "retry_count": 0, "last_updated_at": "2025-01-01T00:00:00+00:00",
            "filename": "slide.svs", "slide_id": None, "scanner_id": None,
            "uploader_host": None, "file_size_bytes": None, "upload_speed_mbps": None,
            "failure_reason": None, "estimated_upload_at": None,
            "upload_started_at": None, "upload_completed_at": None,
            "priority_reason": None, "priority_updated_at": None,
            "priority_updated_by": None, "watch_folder_path": None,
            "watch_folder_label": None, "is_delayed": False,
        }
        with patch("pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority_mode",
                   return_value=expected):
            resp = client.patch("/dashboard/api/uploads/queue/1/priority", json={"mode": "high"})
        assert resp.status_code == 200
        assert resp.json()["priority"] == 1

    def test_upload_next_mode_accepted(self):
        client, _ = self._make_app()
        expected = {
            "id": 1, "priority": 0, "priority_source": "upload_next",
            "upload_status": "queued", "queued_at": "2025-01-01T00:00:00+00:00",
            "retry_count": 0, "last_updated_at": "2025-01-01T00:00:00+00:00",
            "filename": "slide.svs", "slide_id": None, "scanner_id": None,
            "uploader_host": None, "file_size_bytes": None, "upload_speed_mbps": None,
            "failure_reason": None, "estimated_upload_at": None,
            "upload_started_at": None, "upload_completed_at": None,
            "priority_reason": "was_high", "priority_updated_at": None,
            "priority_updated_by": None, "watch_folder_path": None,
            "watch_folder_label": None, "is_delayed": False,
        }
        with patch("pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority_mode",
                   return_value=expected):
            resp = client.patch("/dashboard/api/uploads/queue/1/priority",
                                json={"mode": "upload_next"})
        assert resp.status_code == 200
        assert resp.json()["priority"] == 0

    def test_not_found_returns_404(self):
        client, _ = self._make_app()
        with patch("pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority_mode",
                   return_value=None):
            resp = client.patch("/dashboard/api/uploads/queue/99/priority", json={"mode": "high"})
        assert resp.status_code == 404

    def test_terminal_status_returns_409(self):
        client, _ = self._make_app()
        with patch("pathoryx_enterprise.services.dashboard.upload_queries.update_upload_priority_mode",
                   side_effect=ValueError("Cannot change priority of record with status 'uploaded'")):
            resp = client.patch("/dashboard/api/uploads/queue/1/priority", json={"mode": "high"})
        assert resp.status_code == 409
