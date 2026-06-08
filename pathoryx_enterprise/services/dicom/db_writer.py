"""
Enterprise DICOM service DB writer.

Handles:
  - Persist DICOM conversion result
  - Update FileRecord status (dicom_running → dicom_done | dicom_failed)
  - Dispatch ServiceTrigger to uploader
  - Mark originating trigger complete / failed
  - Append lifecycle events to EventStore
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.core import FileRecord, ServiceTrigger
from pathoryx_enterprise.db.models.dicomizer import ConversionResult
from pathoryx_enterprise.db.models.upload_tracking import EstimatedUploadQueue
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.repositories.file_record import FileRecordRepository
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.monitoring.metrics import (
    dicom_cstore_batches_total,
    events_appended_total,
    files_failed_total,
    files_processed_total,
)
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = logging.getLogger(__name__)

SERVICE_NAME = "dicom_service"


class DICOMDBWriter:

    def __init__(self, session: Session) -> None:
        self._session = session
        self._trigger_repo = TriggerRepository(session)
        self._event_repo = EventStoreRepository(session)
        self._file_repo = FileRecordRepository(session)

    def mark_dicom_running(self, trigger: ServiceTrigger) -> None:
        """Transition FileRecord to dicom_running when conversion starts."""
        record = self._session.execute(
            select(FileRecord).where(
                FileRecord.internal_id == trigger.file_record_internal_id
            )
        ).scalar_one_or_none()
        if record is not None:
            record.status = "dicom_running"
            self._session.flush()

    def record_conversion_success(
        self,
        *,
        trigger: ServiceTrigger,
        output_path: str,
        source_path: str | None = None,
        scanner_id: str | None = None,
        conversion_tool: str | None = None,
        conversion_tool_version: str | None = None,
        input_file_size: int | None = None,
        output_file_size: int | None = None,
        duration_seconds: float | None = None,
        metadata_summary: dict | None = None,
        global_artifact_id: str | None = None,
        correlation_id: str | None = None,
        runner_id: str | None = None,
        host_id: str | None = None,
        service_version: str | None = None,
    ) -> None:
        """
        Record successful WSI → DICOM conversion.

        Phase 11C behaviour:
          1. Write dicomizer.conversion_results (conversion_status='completed').
          2. Update FileRecord status → 'dicom_done' (not upload_pending).
             The upload_service sets upload_pending when it starts its work.
          3. Mark originating trigger completed.
          4. Enqueue upload_service trigger with full payload including
             dicom_path, source_path, global_artifact_id, scanner_id.
             Triggered unconditionally — not gated on file_record existence.
          5. Append pipeline event.

        upload_result_json is left NULL — no storescu runs in this service.
        """
        file_record_id = trigger.file_record_internal_id
        artifact_id = global_artifact_id or trigger.global_artifact_id

        # Resolve source_path: prefer explicit arg, fall back to trigger payload
        resolved_source = source_path or (
            (trigger.trigger_payload_json or {}).get("source_path")
        )

        # 1. Write dicomizer.conversion_results
        idempotency_key = deterministic_artifact_id(
            "dicom_result", str(trigger.internal_id), output_path
        )
        existing = self._session.execute(
            select(ConversionResult).where(ConversionResult.idempotency_key == idempotency_key)
        ).scalar_one_or_none()

        if existing is None:
            result_row = ConversionResult(
                idempotency_key=idempotency_key,
                file_record_internal_id=file_record_id,
                trigger_internal_id=trigger.internal_id,
                global_artifact_id=artifact_id,
                global_run_id=correlation_id,
                correlation_id=correlation_id,
                conversion_status="completed",
                source_path=resolved_source,
                output_path=output_path,
                conversion_tool=conversion_tool,
                conversion_tool_version=conversion_tool_version,
                input_file_size_bytes=input_file_size,
                output_file_size_bytes=output_file_size,
                duration_seconds=duration_seconds,
                metadata_summary=metadata_summary,
                upload_result_json=None,   # upload_service owns storescu; NULL here
                runner_id=runner_id,
                host_id=host_id,
                service_version=service_version,
                processed_at=utc_now(),
            )
            self._session.add(result_row)
            self._session.flush()

        # 2. Update FileRecord → dicom_done (upload_service will set upload_pending)
        record = self._session.execute(
            select(FileRecord).where(FileRecord.internal_id == file_record_id)
        ).scalar_one_or_none()
        if record is not None:
            record.status = "dicom_done"
            record.current_file_path = output_path
            self._session.flush()

        # 3. Mark own trigger completed
        self._trigger_repo.mark_completed(trigger=trigger)

        # 4. Enqueue upload_service trigger (unconditional — no FileRecord required)
        #    Payload gives upload_service everything it needs to run storescu.
        upload_trigger, _ = self._trigger_repo.enqueue(
            source_service=SERVICE_NAME,
            target_service="upload_service",
            stage_name="upload",
            file_record_internal_id=file_record_id,
            global_artifact_id=artifact_id,
            correlation_id=correlation_id,
            runner_id=runner_id,
            payload={
                "dicom_path": output_path,
                "source_path": resolved_source or "",
                "global_artifact_id": artifact_id,
                "scanner_id": scanner_id,
            },
        )

        # 4b. Pre-populate upload queue row so the dashboard shows this file as "queued"
        #     immediately after DICOM conversion, before the uploader picks it up.
        self._init_upload_queue_row(
            upload_trigger=upload_trigger,
            file_record=record,
            scanner_id=scanner_id,
            file_size=input_file_size,
        )

        # 5. Event
        self._event_repo.append(
            event_type="file.dicom_converted",
            aggregate_type="file_record",
            aggregate_id=artifact_id or str(file_record_id or ""),
            service_name=SERVICE_NAME,
            event_payload={
                "output_path": output_path,
                "source_path": resolved_source,
                "conversion_tool": conversion_tool,
                "duration_seconds": duration_seconds,
                "trigger_id": trigger.internal_id,
                "upload_trigger_dispatched": True,
            },
            file_record_internal_id=file_record_id,
            global_artifact_id=artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        files_processed_total.labels(service=SERVICE_NAME, stage="dicom").inc()
        events_appended_total.labels(
            event_type="file.dicom_converted", service=SERVICE_NAME
        ).inc()

    def _init_upload_queue_row(
        self,
        *,
        upload_trigger: ServiceTrigger,
        file_record: FileRecord | None,
        scanner_id: str | None,
        file_size: int | None,
    ) -> None:
        """Insert a 'queued' row into estimated_upload_queue on upload trigger dispatch."""
        if file_record is None:
            return
        filename = file_record.current_filename or file_record.original_filename or ""
        if not filename:
            return

        queued_at = upload_trigger.triggered_at or utc_now()
        now = utc_now()
        stmt = (
            pg_insert(EstimatedUploadQueue)
            .values(
                filename=filename,
                scanner_id=scanner_id or file_record.scanner_id,
                queued_at=queued_at,
                estimated_upload_at=queued_at + timedelta(minutes=10),
                upload_status="queued",
                file_size_bytes=file_size or file_record.file_size,
                last_updated_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_euq_filename_queued_at")
        )
        try:
            self._session.execute(stmt)
            self._session.flush()
        except Exception:
            logger.warning("failed to init upload queue row", exc_info=True)

    def record_conversion_failure(
        self,
        *,
        trigger: ServiceTrigger,
        error: str,
        failure_context: dict | None = None,
        source_path: str | None = None,
        global_artifact_id: str | None = None,
        correlation_id: str | None = None,
        runner_id: str | None = None,
        host_id: str | None = None,
        service_version: str | None = None,
    ) -> None:
        file_record_id = trigger.file_record_internal_id
        artifact_id = global_artifact_id or trigger.global_artifact_id

        # 1. Write a failed row to dicomizer.conversion_results
        #    (ensures the table is written even on conversion failure, not just success)
        idempotency_key = deterministic_artifact_id(
            "dicom_result_failure", str(trigger.internal_id), error[:64]
        )
        existing = self._session.execute(
            select(ConversionResult).where(
                ConversionResult.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        if existing is None:
            resolved_source = source_path or (
                (trigger.trigger_payload_json or {}).get("source_path")
            )
            result_row = ConversionResult(
                idempotency_key=idempotency_key,
                file_record_internal_id=file_record_id,
                trigger_internal_id=trigger.internal_id,
                global_artifact_id=artifact_id,
                global_run_id=correlation_id,
                correlation_id=correlation_id,
                conversion_status="failed",
                source_path=resolved_source,
                output_path=None,
                failure_context=failure_context or {"error": error},
                runner_id=runner_id,
                host_id=host_id,
                service_version=service_version,
                processed_at=utc_now(),
            )
            self._session.add(result_row)
            self._session.flush()

        # 2. Update FileRecord → dicom_failed
        record = self._session.execute(
            select(FileRecord).where(FileRecord.internal_id == file_record_id)
        ).scalar_one_or_none()
        if record is not None:
            record.status = "dicom_failed"
            self._session.flush()

        # 3. Mark trigger failed
        self._trigger_repo.mark_failed(trigger=trigger, error_message=error)

        # 4. Event
        self._event_repo.append(
            event_type="file.dicom_failed",
            aggregate_type="file_record",
            aggregate_id=artifact_id or str(file_record_id or ""),
            service_name=SERVICE_NAME,
            event_payload={
                "error": error,
                "trigger_id": trigger.internal_id,
                "failure_context": failure_context or {},
            },
            file_record_internal_id=file_record_id,
            global_artifact_id=artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        files_failed_total.labels(
            service=SERVICE_NAME, stage="dicom", error_type="conversion_error"
        ).inc()
        dicom_cstore_batches_total.labels(service=SERVICE_NAME, status="failed").inc()
        events_appended_total.labels(
            event_type="file.dicom_failed", service=SERVICE_NAME
        ).inc()
