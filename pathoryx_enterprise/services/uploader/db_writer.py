"""
Uploader service DB writer.

Records upload outcomes, updates FileRecord status, appends to EventStore.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.core import FileRecord, ServiceTrigger
from pathoryx_enterprise.db.models.upload_tracking import EstimatedUploadQueue
from pathoryx_enterprise.db.models.uploader import UploadResult
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.monitoring.metrics import (
    events_appended_total,
    files_failed_total,
    files_processed_total,
)
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = logging.getLogger(__name__)

SERVICE_NAME = "upload_service"


class UploaderDBWriter:

    def __init__(self, session: Session) -> None:
        self._session = session
        self._trigger_repo = TriggerRepository(session)
        self._event_repo = EventStoreRepository(session)

    # ------------------------------------------------------------------
    # Upload queue sync helpers
    # ------------------------------------------------------------------

    def _sync_upload_queue(
        self,
        *,
        trigger: ServiceTrigger,
        file_record: FileRecord | None,
        upload_status: str,
        upload_started_at: datetime | None = None,
        upload_completed_at: datetime | None = None,
        upload_speed_mbps: float | None = None,
        failure_reason: str | None = None,
        retry_count: int = 0,
        host_id: str | None = None,
    ) -> None:
        """Upsert one row in estimated_upload_queue to reflect current upload lifecycle state."""
        filename = ""
        scanner_id = None
        file_size = None

        if file_record is not None:
            filename = file_record.current_filename or file_record.original_filename or ""
            scanner_id = file_record.scanner_id
            file_size = file_record.file_size

        if not filename:
            payload = trigger.trigger_payload_json or {}
            raw_path = payload.get("source_path") or payload.get("dicom_path") or ""
            if raw_path:
                filename = Path(raw_path).name
            if not scanner_id:
                scanner_id = payload.get("scanner_id")

        if not filename:
            return

        queued_at = trigger.triggered_at or utc_now()
        now = utc_now()
        ins = pg_insert(EstimatedUploadQueue)
        stmt = (
            ins
            .values(
                filename=filename,
                scanner_id=scanner_id,
                uploader_host=host_id,
                queued_at=queued_at,
                upload_status=upload_status,
                upload_started_at=upload_started_at,
                upload_completed_at=upload_completed_at,
                upload_speed_mbps=upload_speed_mbps,
                failure_reason=failure_reason[:500] if failure_reason else None,
                retry_count=retry_count,
                file_size_bytes=file_size,
                last_updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_euq_filename_queued_at",
                set_={
                    "upload_status":       ins.excluded.upload_status,
                    # Preserve actual start time set by record_upload_started()
                    "upload_started_at":   func.coalesce(
                        EstimatedUploadQueue.upload_started_at,
                        ins.excluded.upload_started_at,
                    ),
                    "upload_completed_at": ins.excluded.upload_completed_at,
                    "upload_speed_mbps":   ins.excluded.upload_speed_mbps,
                    "failure_reason":      ins.excluded.failure_reason,
                    "retry_count":         ins.excluded.retry_count,
                    "uploader_host":       ins.excluded.uploader_host,
                    "last_updated_at":     ins.excluded.last_updated_at,
                },
                where=ins.excluded.last_updated_at > EstimatedUploadQueue.last_updated_at,
            )
        )
        try:
            self._session.execute(stmt)
            self._session.flush()
        except Exception:
            logger.warning("failed to sync upload queue row", exc_info=True)

    def record_upload_started(
        self,
        *,
        trigger: ServiceTrigger,
        host_id: str | None = None,
    ) -> None:
        """Mark upload as in-progress in estimated_upload_queue (best-effort)."""
        file_record = self._session.execute(
            select(FileRecord).where(FileRecord.internal_id == trigger.file_record_internal_id)
        ).scalar_one_or_none()
        self._sync_upload_queue(
            trigger=trigger,
            file_record=file_record,
            upload_status="uploading",
            upload_started_at=utc_now(),
            host_id=host_id,
        )

    def record_upload_success(
        self,
        *,
        trigger: ServiceTrigger,
        upload_method: str = "filesystem",
        destination_path: str | None = None,
        file_size: int | None = None,
        duration_seconds: float | None = None,
        global_artifact_id: str | None = None,
        correlation_id: str | None = None,
        runner_id: str | None = None,
        host_id: str | None = None,
        service_version: str | None = None,
        retry_count: int = 0,
    ) -> None:
        file_record_id = trigger.file_record_internal_id
        artifact_id = global_artifact_id or trigger.global_artifact_id

        # 1. Persist uploader result
        idempotency_key = deterministic_artifact_id(
            "upload_result", str(trigger.internal_id), "success"
        )
        existing = self._session.execute(
            select(UploadResult).where(UploadResult.idempotency_key == idempotency_key)
        ).scalar_one_or_none()

        if existing is None:
            result_row = UploadResult(
                idempotency_key=idempotency_key,
                file_record_internal_id=file_record_id,
                trigger_internal_id=trigger.internal_id,
                global_artifact_id=artifact_id,
                global_run_id=correlation_id,
                correlation_id=correlation_id,
                upload_status="uploaded",
                upload_method=upload_method,
                target_endpoint=destination_path,
                file_size=file_size,
                duration_seconds=duration_seconds,
                runner_id=runner_id,
                host_id=host_id,
                service_version=service_version,
                processed_at=utc_now(),
            )
            self._session.add(result_row)
            self._session.flush()

        # 2. FileRecord → uploaded
        record = self._session.execute(
            select(FileRecord).where(FileRecord.internal_id == file_record_id)
        ).scalar_one_or_none()
        if record is not None:
            record.status = "uploaded"
            self._session.flush()

        # 3. Mark trigger complete
        self._trigger_repo.mark_completed(trigger=trigger)

        # 4. Event
        self._event_repo.append(
            event_type="file.uploaded",
            aggregate_type="file_record",
            aggregate_id=artifact_id or str(file_record_id or ""),
            service_name=SERVICE_NAME,
            event_payload={
                "upload_method": upload_method,
                "destination_path": destination_path,
                "duration_seconds": duration_seconds,
            },
            file_record_internal_id=file_record_id,
            global_artifact_id=artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        # Sync to upload queue for dashboard visibility
        speed_mbps: float | None = None
        if file_size and duration_seconds and duration_seconds > 0:
            speed_mbps = (file_size / (1024 * 1024)) / duration_seconds
        completed_at = utc_now()
        self._sync_upload_queue(
            trigger=trigger,
            file_record=record,
            upload_status="uploaded",
            upload_completed_at=completed_at,
            upload_speed_mbps=speed_mbps,
            retry_count=retry_count,
            host_id=host_id,
        )

        files_processed_total.labels(service=SERVICE_NAME, stage="upload").inc()
        events_appended_total.labels(event_type="file.uploaded", service=SERVICE_NAME).inc()

    def record_upload_failure(
        self,
        *,
        trigger: ServiceTrigger,
        error: str,
        global_artifact_id: str | None = None,
        correlation_id: str | None = None,
        runner_id: str | None = None,
        host_id: str | None = None,
        service_version: str | None = None,
        retry_count: int = 0,
    ) -> None:
        file_record_id = trigger.file_record_internal_id
        artifact_id = global_artifact_id or trigger.global_artifact_id

        record = self._session.execute(
            select(FileRecord).where(FileRecord.internal_id == file_record_id)
        ).scalar_one_or_none()
        if record is not None:
            record.status = "upload_failed"
            self._session.flush()

        self._trigger_repo.mark_failed(trigger=trigger, error_message=error)

        self._event_repo.append(
            event_type="file.upload_failed",
            aggregate_type="file_record",
            aggregate_id=artifact_id or str(file_record_id or ""),
            service_name=SERVICE_NAME,
            event_payload={"error": error, "trigger_id": trigger.internal_id},
            file_record_internal_id=file_record_id,
            global_artifact_id=artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        # Sync to upload queue for dashboard visibility
        self._sync_upload_queue(
            trigger=trigger,
            file_record=record,
            upload_status="failed",
            failure_reason=error,
            retry_count=retry_count,
            host_id=host_id,
        )

        files_failed_total.labels(
            service=SERVICE_NAME, stage="upload", error_type="upload_error"
        ).inc()
        events_appended_total.labels(
            event_type="file.upload_failed", service=SERVICE_NAME
        ).inc()
