"""
Enterprise BabelShark DB writer.

Responsibilities:
  - Dispatch ServiceTrigger to the next pipeline stage after a slide is collected.
  - Append lifecycle events to the immutable EventStore.
  - Update FileRecord status transitions (intake_registered → qc_pending | dicom_pending).
  - Create MetadataSnapshot after successful intake.

All methods accept a SQLAlchemy session that must be provided by the caller
(usually runner.py inside a get_session() context). This keeps transactions
correctly scoped — one session per collected file.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.core import FileRecord, ServiceTrigger
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.repositories.file_record import FileRecordRepository
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.monitoring.metrics import (
    events_appended_total,
    files_failed_total,
    files_processed_total,
)
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = logging.getLogger(__name__)

SERVICE_NAME = "babelshark"


def _build_trigger_payload(record: "FileRecord", global_artifact_id: str | None) -> dict:  # noqa: F821
    """
    Build trigger_payload_json for every downstream service trigger dispatched
    by BabelShark.  Provides the QC service (and any other consumer) with
    everything it needs to process the slide without additional DB lookups.

    source_path resolution priority
    ────────────────────────────────
    1. file_record.current_file_path  — exists on disk
    2. file_record.canonical_path     — exists on disk
    3. file_record.current_file_path  — DB value even if not found on disk
    4. file_record.canonical_path     — DB value even if not found on disk
    5. None                           — no path recorded; source_path_resolution_error=true

    Scanner identity resolution priority
    ─────────────────────────────────────
    1. file_record.scanner_id / scanner_name  (Phase 1+2 columns)
    2. file_record.metadata_json["scanner"]["scanner_id_raw"] / "scanner_name"
    3. file_record.output_metadata_json["scanner_id_raw"] / "scanner_name"
    4. None
    """
    # ── source_path ───────────────────────────────────────────────────────────
    source_path: str | None = None
    resolution_error = False

    path_candidates = [record.current_file_path, record.canonical_path]

    # Pass 1: prefer a path that actually exists on disk right now
    for candidate in path_candidates:
        if candidate and Path(candidate).exists():
            source_path = str(candidate)
            break

    # Pass 2: use whatever DB has, even if file not on disk (may have moved)
    if source_path is None:
        for candidate in path_candidates:
            if candidate:
                source_path = str(candidate)
                resolution_error = True
                break

    if source_path is None:
        resolution_error = True

    # ── scanner identity ──────────────────────────────────────────────────────
    scanner_id: str | None = getattr(record, "scanner_id", None) or None
    scanner_name: str | None = getattr(record, "scanner_name", None) or None

    if not scanner_id or not scanner_name:
        meta_scanner = (record.metadata_json or {}).get("scanner") or {}
        if not scanner_id:
            scanner_id = meta_scanner.get("scanner_id_raw") or meta_scanner.get("scanner_id") or None
        if not scanner_name:
            scanner_name = meta_scanner.get("scanner_name") or None

    if not scanner_id or not scanner_name:
        out_meta = record.output_metadata_json or {}
        if not scanner_id:
            scanner_id = out_meta.get("scanner_id_raw") or out_meta.get("scanner_id") or None
        if not scanner_name:
            scanner_name = out_meta.get("scanner_name") or None

    # ── assemble ──────────────────────────────────────────────────────────────
    payload: dict = {
        "source_path": source_path,
        "scanner_id": scanner_id,
        "scanner_name": scanner_name,
        "original_filename": record.original_filename,
        "current_filename": record.current_filename,
        "global_artifact_id": global_artifact_id or record.global_artifact_id,
        "source_service": SERVICE_NAME,
        "qc_context": "post_babelshark",
        "input_mode": "trigger",
    }

    if resolution_error:
        payload["source_path_resolution_error"] = True

    return payload


class BabelSharkDBWriter:
    """
    Stateless DB writer — instantiate once per operation, pass in session.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._trigger_repo = TriggerRepository(session)
        self._event_repo = EventStoreRepository(session)
        self._file_repo = FileRecordRepository(session)

    def mark_intake_complete(
        self,
        *,
        file_record_internal_id: int,
        global_artifact_id: str,
        next_stage: str,
        next_service: str,
        correlation_id: str | None = None,
        runner_id: str | None = None,
        host_id: str | None = None,
        service_version: str | None = None,
        metadata_payload: dict | None = None,
        priority: int = 5,
        priority_source: str = "default",
        watch_folder_path: str | None = None,
        watch_folder_label: str | None = None,
    ) -> ServiceTrigger:
        """
        Transition FileRecord to the appropriate post-intake status and dispatch
        a ServiceTrigger so the next service picks it up.

        next_stage / next_service:
          - ("qc", "qc_service")         — slide goes to QC
          - ("dicom", "dicom_service")   — slide skips QC, goes directly to DICOM
        """
        record = self._session.execute(
            select(FileRecord).where(FileRecord.internal_id == file_record_internal_id)
        ).scalar_one()

        if next_stage == "qc":
            record.status = "qc_pending"
        elif next_stage == "dicom":
            record.status = "dicom_pending"
        else:
            record.status = "intake_registered"

        self._session.flush()

        # Immutable snapshot of the metadata at intake time
        if metadata_payload:
            self._file_repo.create_metadata_snapshot(
                record=record,
                payload=metadata_payload,
                source_service=SERVICE_NAME,
            )

        # Build payload — include priority info so downstream services can propagate it
        payload = _build_trigger_payload(record, global_artifact_id)
        payload["priority"] = priority
        payload["priority_source"] = priority_source
        if watch_folder_path:
            payload["watch_folder_path"] = watch_folder_path
        if watch_folder_label:
            payload["watch_folder_label"] = watch_folder_label

        # Dispatch to next stage — always include a rich payload so downstream
        # services (QC, DICOM) can resolve the file without extra DB lookups.
        trigger, created = self._trigger_repo.enqueue(
            source_service=SERVICE_NAME,
            target_service=next_service,
            stage_name=next_stage,
            file_record_internal_id=file_record_internal_id,
            global_artifact_id=global_artifact_id,
            correlation_id=correlation_id,
            runner_id=runner_id,
            payload=payload,
            priority=priority,
        )

        # Immutable event
        self._event_repo.append(
            event_type="file.intake_completed",
            aggregate_type="file_record",
            aggregate_id=global_artifact_id,
            service_name=SERVICE_NAME,
            event_payload={
                "file_record_internal_id": file_record_internal_id,
                "next_stage": next_stage,
                "next_service": next_service,
                "trigger_id": trigger.internal_id,
                "trigger_created": created,
            },
            file_record_internal_id=file_record_internal_id,
            global_artifact_id=global_artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        files_processed_total.labels(service=SERVICE_NAME, stage="intake").inc()
        events_appended_total.labels(event_type="file.intake_completed", service=SERVICE_NAME).inc()

        logger.info(
            "intake complete",
            extra={
                "global_artifact_id": global_artifact_id,
                "next_stage": next_stage,
                "trigger_id": trigger.internal_id,
            },
        )
        return trigger

    def mark_intake_failed(
        self,
        *,
        file_record_internal_id: int | None,
        global_artifact_id: str | None,
        error: str,
        correlation_id: str | None = None,
        runner_id: str | None = None,
        host_id: str | None = None,
        service_version: str | None = None,
    ) -> None:
        """Record a failed intake in the event store. Does NOT transition FileRecord status.

        FileRecord status is intentionally left unchanged: the slide stays at
        whatever lifecycle state it had before the failed intake attempt
        (typically 'detected' or 'intake_running').  RecoverySentry monitors
        stale records for operator review.

        'failed' is NOT written here — it is not in ck_file_records_status and
        would cause a constraint violation.
        """
        # No FileRecord status update — see docstring.

        agg_id = global_artifact_id or deterministic_artifact_id(
            "babelshark", "unknown", error[:64]
        )

        self._event_repo.append(
            event_type="file.intake_failed",
            aggregate_type="file_record",
            aggregate_id=agg_id,
            service_name=SERVICE_NAME,
            event_payload={
                "error": error,
                "file_record_internal_id": file_record_internal_id,
            },
            file_record_internal_id=file_record_internal_id,
            global_artifact_id=global_artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        events_appended_total.labels(event_type="file.intake_failed", service=SERVICE_NAME).inc()

    def mark_babelshark_failed(
        self,
        *,
        file_record_internal_id: int,
        global_artifact_id: str,
        actual_path: str | None = None,
        routing_status: str | None = None,
        correlation_id: str | None = None,
        runner_id: str | None = None,
        host_id: str | None = None,
        service_version: str | None = None,
    ) -> None:
        """Mark a file record as babelshark_failed after routing to a failure directory.

        Sets status=babelshark_failed and, when actual_path is provided, updates
        current_file_path/canonical_path to the actual location on disk (e.g.
        failed/YYYY-MM-DD/filename.svs).  Does NOT create a ServiceTrigger — no
        downstream service should receive this file.
        """
        record = self._session.execute(
            select(FileRecord).where(FileRecord.internal_id == file_record_internal_id)
        ).scalar_one()

        record.status = "babelshark_failed"

        if actual_path:
            record.current_file_path = actual_path
            record.canonical_path = actual_path

        self._session.flush()

        self._event_repo.append(
            event_type="babelshark.failed_routed",
            aggregate_type="file_record",
            aggregate_id=global_artifact_id,
            service_name=SERVICE_NAME,
            event_payload={
                "file_record_internal_id": file_record_internal_id,
                "actual_path": actual_path,
                "routing_status": routing_status,
            },
            file_record_internal_id=file_record_internal_id,
            global_artifact_id=global_artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        files_failed_total.labels(
            service=SERVICE_NAME, stage="intake", error_type="babelshark_failed_routed"
        ).inc()
        events_appended_total.labels(
            event_type="babelshark.failed_routed", service=SERVICE_NAME
        ).inc()

        logger.info(
            "babelshark failed routed",
            extra={
                "global_artifact_id": global_artifact_id,
                "actual_path": actual_path,
                "routing_status": routing_status,
            },
        )
