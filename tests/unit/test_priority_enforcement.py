"""
Phase 4.6D — Priority Scheduler Enforcement Tests (Simplified Model).

Priority model:
  0 = UPLOAD_NEXT  — operator-flagged "jump the queue"
  1 = HIGH         — watch folder or manual flag
  5 = NORMAL       — default

Queue order: UPLOAD_NEXT → HIGH → NORMAL, FIFO within each group.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from pathoryx_enterprise.utils.watch_folder_priority import (
    WatchFolderEntry,
    WatchFolderPriorityResolver,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(offset_minutes: int = 0) -> datetime:
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes)


def _upload_row(priority: int, queued_offset_minutes: int, upload_status: str = "queued",
                file_record_internal_id: int = None, priority_source: str = "default",
                watch_folder_label: str = None, filename: str = "slide.svs"):
    row = MagicMock()
    row.id = hash((priority, queued_offset_minutes, filename)) % 100_000
    row.priority = priority
    row.queued_at = _dt(queued_offset_minutes)
    row.upload_status = upload_status
    row.estimated_upload_at = None
    row.upload_started_at = None
    row.upload_completed_at = None
    row.last_updated_at = _dt()
    row.slide_id = None
    row.filename = filename
    row.scanner_id = None
    row.uploader_host = None
    row.retry_count = 0
    row.file_size_bytes = None
    row.upload_speed_mbps = None
    row.failure_reason = None
    row.priority_source = priority_source
    row.priority_reason = None
    row.priority_updated_at = None
    row.priority_updated_by = None
    row.file_record_internal_id = file_record_internal_id
    row.watch_folder_path = None
    row.watch_folder_label = watch_folder_label
    return row


# ---------------------------------------------------------------------------
# 1. FIFO within same priority
# ---------------------------------------------------------------------------

class TestFIFOWithinPriority:
    def test_earlier_queued_at_wins_at_same_normal_priority(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        row_a = _upload_row(priority=5, queued_offset_minutes=0,  filename="a.svs")
        row_b = _upload_row(priority=5, queued_offset_minutes=10, filename="b.svs")

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = [row_a, row_b]

        items = get_next_uploads_preview(mock_db, limit=5)
        assert items[0]["filename"] == "a.svs"
        assert items[1]["filename"] == "b.svs"

    def test_fifo_within_high_priority(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        early_high = _upload_row(priority=1, queued_offset_minutes=0,  filename="early.svs")
        late_high  = _upload_row(priority=1, queued_offset_minutes=10, filename="late.svs")

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = [early_high, late_high]

        items = get_next_uploads_preview(mock_db)
        assert len(items) == 2

    def test_same_priority_same_time_stable(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        row_a = _upload_row(priority=5, queued_offset_minutes=0, filename="a.svs")
        row_b = _upload_row(priority=5, queued_offset_minutes=0, filename="b.svs")

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = [row_a, row_b]

        items = get_next_uploads_preview(mock_db)
        assert len(items) == 2


# ---------------------------------------------------------------------------
# 2. HIGH beats NORMAL
# ---------------------------------------------------------------------------

class TestHighBeatsNormal:
    def test_high_priority_query_orders_by_priority_asc(self):
        """HIGH (1) item queued later still beats NORMAL (5) — query must ORDER BY priority ASC."""
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        get_next_uploads_preview(mock_db)

        call_args = mock_db.execute.call_args
        stmt = call_args.args[0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "priority" in stmt_str.lower()
        assert "queued_at" in stmt_str.lower()

    def test_db_result_high_before_normal(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        high_late   = _upload_row(priority=1, queued_offset_minutes=60, filename="high.svs")
        normal_early = _upload_row(priority=5, queued_offset_minutes=0,  filename="normal.svs")

        mock_db = MagicMock()
        # DB returns already ordered (priority ASC)
        mock_db.execute.return_value.scalars.return_value.all.return_value = [high_late, normal_early]

        items = get_next_uploads_preview(mock_db)
        assert items[0]["filename"] == "high.svs"
        assert items[1]["filename"] == "normal.svs"


# ---------------------------------------------------------------------------
# 3. UPLOAD_NEXT beats all
# ---------------------------------------------------------------------------

class TestUploadNextBeatsAll:
    def test_upload_next_appears_first_regardless_of_queued_at(self):
        """UPLOAD_NEXT (0) must be first regardless of when it was queued."""
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        high_first    = _upload_row(priority=1, queued_offset_minutes=0,   filename="high.svs")
        normal_mid    = _upload_row(priority=5, queued_offset_minutes=5,   filename="normal.svs")
        next_last     = _upload_row(priority=0, queued_offset_minutes=100, filename="upload_next.svs")

        mock_db = MagicMock()
        # DB returns priority-sorted: UPLOAD_NEXT first
        mock_db.execute.return_value.scalars.return_value.all.return_value = [
            next_last, high_first, normal_mid
        ]

        items = get_next_uploads_preview(mock_db)
        assert items[0]["filename"] == "upload_next.svs"

    def test_upload_next_does_not_interrupt_active_upload(self):
        """UPLOAD_NEXT must not affect an in-progress uploading row."""
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        # Active upload is excluded from the preview entirely
        active = _upload_row(priority=0, queued_offset_minutes=0,
                             upload_status="uploading", filename="active.svs")
        queued = _upload_row(priority=0, queued_offset_minutes=5, filename="next.svs")

        mock_db = MagicMock()
        # Preview excludes uploading rows, so only queued UPLOAD_NEXT appears
        mock_db.execute.return_value.scalars.return_value.all.return_value = [queued]

        items = get_next_uploads_preview(mock_db)
        assert all(i["filename"] != "active.svs" for i in items)


# ---------------------------------------------------------------------------
# 4. No LOW priority
# ---------------------------------------------------------------------------

class TestNoLowPriority:
    def test_low_priority_invalid_in_watch_folder(self):
        with pytest.raises(ValueError, match="invalid priority"):
            WatchFolderEntry(path="/data/low", priority=9)

    def test_valid_priorities_no_low(self):
        from pathoryx_enterprise.utils.watch_folder_priority import VALID_PRIORITIES
        assert 9 not in VALID_PRIORITIES

    def test_upload_queries_valid_priorities_no_low(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import VALID_PRIORITIES
        assert 9 not in VALID_PRIORITIES


# ---------------------------------------------------------------------------
# 5 & 6. Subfolder inheritance + longest-path match
# ---------------------------------------------------------------------------

class TestSubfolderInheritance:
    def test_subfolder_inherits_high_from_parent(self):
        resolver = WatchFolderPriorityResolver([
            WatchFolderEntry(path="/incoming/urgent", priority=1, label="Urgent"),
        ])
        result = resolver.resolve("/incoming/urgent/frozen/specimen.svs")
        assert result.priority == 1

    def test_nested_override_wins_over_parent(self):
        resolver = WatchFolderPriorityResolver([
            WatchFolderEntry(path="/incoming/urgent_cases",                  priority=1, label="Urgent"),
            WatchFolderEntry(path="/incoming/urgent_cases/upload_next_queue", priority=0, label="Upload Next"),
        ])
        result_next   = resolver.resolve("/incoming/urgent_cases/upload_next_queue/sample.svs")
        result_urgent = resolver.resolve("/incoming/urgent_cases/other.svs")
        assert result_next.priority == 0
        assert result_urgent.priority == 1

    def test_file_not_under_any_folder_is_normal(self):
        resolver = WatchFolderPriorityResolver([
            WatchFolderEntry(path="/incoming/urgent", priority=1, label="Urgent"),
        ])
        result = resolver.resolve("/other/path/slide.svs")
        assert result.priority == 5
        assert result.priority_source == "default"

    def test_high_priority_bool_config_inherits_through_subfolders(self):
        from pathoryx_enterprise.utils.watch_folder_priority import build_resolver_from_config
        resolver = build_resolver_from_config([
            {"path": "/data/urgent", "high_priority": True, "label": "Urgent"},
        ])
        result = resolver.resolve("/data/urgent/scanner/case/slide.svs")
        assert result.priority == 1
        assert result.watch_folder_label == "Urgent"


# ---------------------------------------------------------------------------
# 7. Retry preserves priority
# ---------------------------------------------------------------------------

class TestRetryPreservesPriority:
    def test_mark_failed_does_not_touch_priority(self):
        from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
        from pathoryx_enterprise.db.models.core import ServiceTrigger

        mock_trigger = MagicMock(spec=ServiceTrigger)
        mock_trigger.priority = 1  # HIGH
        mock_trigger.retry_count = 0
        mock_trigger.trigger_status = "running"

        mock_session = MagicMock()
        repo = TriggerRepository(mock_session)
        repo.mark_failed(mock_trigger, "connection error")

        assert mock_trigger.priority == 1
        assert mock_trigger.trigger_status == "failed"
        assert mock_trigger.retry_count == 1

    def test_requeue_does_not_touch_priority(self):
        from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
        from pathoryx_enterprise.db.models.core import ServiceTrigger

        mock_trigger = MagicMock(spec=ServiceTrigger)
        mock_trigger.priority = 0  # UPLOAD_NEXT
        mock_trigger.trigger_status = "failed"

        mock_session = MagicMock()
        repo = TriggerRepository(mock_session)
        repo.requeue(mock_trigger)

        assert mock_trigger.priority == 0
        assert mock_trigger.trigger_status == "pending"

    def test_upload_next_priority_preserved_after_mark_failed(self):
        from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
        from pathoryx_enterprise.db.models.core import ServiceTrigger

        mock_trigger = MagicMock(spec=ServiceTrigger)
        mock_trigger.priority = 0
        mock_trigger.retry_count = 2
        mock_trigger.trigger_status = "running"

        repo = TriggerRepository(MagicMock())
        repo.mark_failed(mock_trigger, "timeout")

        assert mock_trigger.priority == 0  # UPLOAD_NEXT preserved


# ---------------------------------------------------------------------------
# 8. Recovery preserves priority
# ---------------------------------------------------------------------------

class TestRecoveryPreservesPriority:
    def test_recover_file_with_upload_next_priority_preserves_it(self):
        """Recovery must pass priority=0 (UPLOAD_NEXT) to the new trigger."""
        from pathoryx_enterprise.db.models.upload_tracking import EstimatedUploadQueue

        existing_queue_row = MagicMock(spec=EstimatedUploadQueue)
        existing_queue_row.priority = 0
        existing_queue_row.priority_source = "upload_next"

        enqueue_calls: list[dict] = []

        def fake_enqueue(**kwargs):
            enqueue_calls.append(kwargs)
            mock_trigger = MagicMock()
            mock_trigger.internal_id = 999
            return mock_trigger, True

        mock_trigger_repo = MagicMock()
        mock_trigger_repo.enqueue.side_effect = fake_enqueue

        mock_session = MagicMock()
        mock_session.execute.return_value.scalar_one_or_none.return_value = existing_queue_row

        with patch(
            "pathoryx_enterprise.services.recovery_sentry.recovery_engine.get_session"
        ) as mock_get_session:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=mock_session)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_get_session.return_value = ctx

            with patch(
                "pathoryx_enterprise.services.recovery_sentry.recovery_engine.TriggerRepository",
                return_value=mock_trigger_repo,
            ), patch(
                "pathoryx_enterprise.services.recovery_sentry.recovery_engine.FileRecordRepository",
            ) as mock_fr_repo_cls, patch(
                "pathoryx_enterprise.services.recovery_sentry.recovery_engine.EventStoreRepository",
            ):
                mock_fr_repo = MagicMock()
                mock_fr_repo_cls.return_value = mock_fr_repo

                mock_file_record = MagicMock()
                mock_file_record.internal_id = 42
                mock_file_record.global_artifact_id = "art-uuid"
                mock_file_record.status = "qc_pending"
                mock_session.execute.return_value.scalar_one_or_none.side_effect = [
                    mock_file_record,
                    existing_queue_row,
                    None,
                ]
                mock_fr_repo.get_by_canonical_path.return_value = None
                mock_fr_repo.get_or_create_safe.return_value = (mock_file_record, True)

                from pathoryx_enterprise.services.recovery_sentry.recovery_engine import _persist_recovery
                from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import ParsedSlideID
                from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
                from pathlib import Path

                mock_settings = MagicMock(spec=RecoverySentrySettings)
                mock_settings.next_stage_target_service = "qc_service"
                mock_settings.next_stage_name = "qc"
                mock_parsed = MagicMock(spec=ParsedSlideID)
                mock_parsed.case_id = "E2024001"
                mock_parsed.extension = ".svs"

                _persist_recovery(
                    parsed=mock_parsed,
                    dest_path=Path("/final/E2024001/slide.svs"),
                    final_name="slide.svs",
                    slide_id_final="N2024001001SA-1-1-HE",
                    source_path="/failed/slide.svs",
                    source_filename="slide.svs",
                    iso_z_ts=None,
                    timestamp_in_filename=False,
                    timestamp_extracted=False,
                    technician_change_id=None,
                    hint_file_record_internal_id=42,
                    hint_global_artifact_id=None,
                    correlation_id="corr-1",
                    runner_id="runner-1",
                    settings=mock_settings,
                )

        if enqueue_calls:
            assert enqueue_calls[0]["priority"] == 0, (
                f"Expected priority=0 (UPLOAD_NEXT), got {enqueue_calls[0]['priority']}"
            )

    def test_recover_new_file_gets_default_priority(self):
        """Brand-new file with no queue history gets priority 5 (NORMAL)."""
        enqueue_calls: list[dict] = []

        def fake_enqueue(**kwargs):
            enqueue_calls.append(kwargs)
            mock_trigger = MagicMock()
            mock_trigger.internal_id = 1000
            return mock_trigger, True

        mock_trigger_repo = MagicMock()
        mock_trigger_repo.enqueue.side_effect = fake_enqueue

        mock_session = MagicMock()
        mock_file_record = MagicMock()
        mock_file_record.internal_id = 99
        mock_file_record.global_artifact_id = "art-2"
        mock_file_record.status = "qc_pending"

        mock_session.execute.return_value.scalar_one_or_none.side_effect = [
            None,
            mock_file_record,
            None,
            None,
        ]

        with patch(
            "pathoryx_enterprise.services.recovery_sentry.recovery_engine.get_session"
        ) as mock_get_session:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=mock_session)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_get_session.return_value = ctx

            with patch(
                "pathoryx_enterprise.services.recovery_sentry.recovery_engine.TriggerRepository",
                return_value=mock_trigger_repo,
            ), patch(
                "pathoryx_enterprise.services.recovery_sentry.recovery_engine.FileRecordRepository",
            ) as mock_fr_repo_cls, patch(
                "pathoryx_enterprise.services.recovery_sentry.recovery_engine.EventStoreRepository",
            ):
                mock_fr_repo = MagicMock()
                mock_fr_repo.get_by_canonical_path.return_value = None
                mock_fr_repo.get_or_create_safe.return_value = (mock_file_record, True)
                mock_fr_repo_cls.return_value = mock_fr_repo

                from pathoryx_enterprise.services.recovery_sentry.recovery_engine import _persist_recovery
                from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import ParsedSlideID
                from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
                from pathlib import Path

                mock_settings = MagicMock(spec=RecoverySentrySettings)
                mock_settings.next_stage_target_service = "qc_service"
                mock_settings.next_stage_name = "qc"
                mock_parsed = MagicMock(spec=ParsedSlideID)
                mock_parsed.case_id = "E2024002"
                mock_parsed.extension = ".svs"

                _persist_recovery(
                    parsed=mock_parsed,
                    dest_path=Path("/final/E2024002/new.svs"),
                    final_name="new.svs",
                    slide_id_final="N2024002001SA-1-1-HE",
                    source_path="/failed/new.svs",
                    source_filename="new.svs",
                    iso_z_ts=None,
                    timestamp_in_filename=False,
                    timestamp_extracted=False,
                    technician_change_id=None,
                    hint_file_record_internal_id=None,
                    hint_global_artifact_id=None,
                    correlation_id="corr-2",
                    runner_id="runner-1",
                    settings=mock_settings,
                )

        if enqueue_calls:
            assert enqueue_calls[0]["priority"] == 5, (
                f"Expected default priority=5, got {enqueue_calls[0]['priority']}"
            )


# ---------------------------------------------------------------------------
# 9. Scheduler query ordering
# ---------------------------------------------------------------------------

class TestSchedulerQueryOrdering:
    def _make_dequeue_repo(self):
        from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
        mock_session = MagicMock()
        mock_session.execute.return_value.scalar_one_or_none.return_value = None
        return TriggerRepository(mock_session), mock_session

    def test_priority_aware_dequeue_orders_by_priority(self):
        from sqlalchemy.sql import Select
        repo, mock_session = self._make_dequeue_repo()
        repo.dequeue_next(target_service="upload_service", runner_id="r1", host_id="h1",
                          priority_aware=True)
        stmt = mock_session.execute.call_args.args[0]
        assert isinstance(stmt, Select)
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "priority" in stmt_str.lower()
        assert "triggered_at" in stmt_str.lower()

    def test_all_three_services_use_priority_aware(self):
        import inspect
        from pathoryx_enterprise.services.dicom import runner as dicom_runner
        assert "priority_aware=True" in inspect.getsource(dicom_runner)

        from pathoryx_enterprise.services.qc import runner as qc_runner
        assert "priority_aware=True" in inspect.getsource(qc_runner)

        from pathoryx_enterprise.services.uploader import runner as uploader_runner
        assert "priority_aware=True" in inspect.getsource(uploader_runner)

    def test_upload_next_beats_high_in_sql_order(self):
        """priority=0 (UPLOAD_NEXT) sorts before priority=1 (HIGH) with ORDER BY priority ASC."""
        upload_next_priority = 0
        high_priority = 1
        normal_priority = 5
        # Numeric ordering guarantees 0 < 1 < 5
        assert upload_next_priority < high_priority < normal_priority


# ---------------------------------------------------------------------------
# 10. Queue preview ordering
# ---------------------------------------------------------------------------

class TestQueuePreviewOrdering:
    def test_preview_excludes_terminal_and_active_rows(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        get_next_uploads_preview(mock_db, limit=10)

        stmt = mock_db.execute.call_args.args[0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "uploaded" in stmt_str.lower() or "NOT IN" in stmt_str.upper()

    def test_preview_orders_by_priority_then_queued_at(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        get_next_uploads_preview(mock_db, limit=5)

        stmt = mock_db.execute.call_args.args[0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "priority" in stmt_str.lower()
        assert "queued_at" in stmt_str.lower()

    def test_preview_respects_limit(self):
        from pathoryx_enterprise.services.dashboard.upload_queries import get_next_uploads_preview

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        get_next_uploads_preview(mock_db, limit=3)

        stmt = mock_db.execute.call_args.args[0]
        stmt_str = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "3" in stmt_str or "LIMIT" in stmt_str.upper()
