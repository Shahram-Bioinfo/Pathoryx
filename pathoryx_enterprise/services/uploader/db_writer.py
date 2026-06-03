"""
Uploader service DB writer.

Records upload outcomes, updates FileRecord status, appends to EventStore.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.core import FileRecord, ServiceTrigger
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

        files_failed_total.labels(
            service=SERVICE_NAME, stage="upload", error_type="upload_error"
        ).inc()
        events_appended_total.labels(
            event_type="file.upload_failed", service=SERVICE_NAME
        ).inc()
