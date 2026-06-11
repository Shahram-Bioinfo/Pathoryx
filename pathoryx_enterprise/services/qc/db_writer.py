"""
Enterprise QC DB writer.

Writes a complete, auditable record for every QC run — whether the slide
passed, failed by model decision, or crashed with a runtime error.

Decision status vocabulary (old adapter → enterprise)
────────────────────────────────────────────────────
  "passed"   → enterprise "accepted"  → file_records.status "qc_passed"
  "failed"   → enterprise "rejected"  → file_records.status "qc_failed"
  "accepted" → enterprise "accepted"  (already enterprise)
  "rejected" → enterprise "rejected"  (already enterprise)
  <unknown>  → enterprise "rejected"  (safe default)

Error reason classification (from exception string)
────────────────────────────────────────────────────
  "unsupported_format"  — OpenSlideUnsupportedFormatError
  "file_missing"        — FileNotFoundError, path does not exist
  "openslide_error"     — other OpenSlide errors
  "inference_error"     — anything else

Context auto-extraction (Phase 4 BabelShark trigger payload)
─────────────────────────────────────────────────────────────
  qc_context, input_mode, source_path, scanner_id, scanner_name are read
  directly from trigger.trigger_payload_json when not supplied as explicit
  parameters.  Explicit parameters always take precedence.

Resource fields (memory_rss_mb, cpu_percent_avg)
─────────────────────────────────────────────────
  Written as NULL until the QC runner is updated to wrap inference in
  pathoryx_enterprise.utils.process_metrics.ResourceMonitor and pass the
  resulting snapshot to record_qc_result().

record_qc_error() behaviour change (Phase 5)
─────────────────────────────────────────────
  Now also writes a qc.qc_results row (qc_result='failed',
  decision_status='rejected').  Previous behaviour only wrote FileRecord /
  trigger status and an event.  The row gives full traceability for every
  slide that QC touched, even when it crashed.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.core import FileRecord, ServiceTrigger
from pathoryx_enterprise.db.models.qc import QCResult
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

SERVICE_NAME = "qc_service"

# ── Decision-status normalisation ─────────────────────────────────────────────

_DECISION_TO_ENTERPRISE: dict[str, str] = {
    "passed":    "accepted",
    "accepted":  "accepted",
    "failed":    "rejected",
    "rejected":  "rejected",
    "failed_qc": "rejected",
}

_ENTERPRISE_TO_FILE_STATUS: dict[str, str] = {
    "accepted": "qc_passed",
    "rejected": "qc_failed",
}

_ENTERPRISE_TO_QC_RESULT: dict[str, str] = {
    "accepted": "passed",
    "rejected": "failed",
}


def _normalize_decision(raw: str) -> str:
    """Map any decision string to enterprise "accepted" | "rejected"."""
    return _DECISION_TO_ENTERPRISE.get(raw, "rejected")


# ── Error classification ───────────────────────────────────────────────────────

def _classify_error_reason(error: str) -> str:
    """
    Best-effort classification of a runtime exception message.

    Returns one of: "unsupported_format", "file_missing",
                    "openslide_error", "inference_error"
    """
    err_lower = error.lower()
    if "unsupported" in err_lower or "unsupported or missing" in err_lower:
        return "unsupported_format"
    if (
        "no such file" in err_lower
        or "file not found" in err_lower
        or "does not exist" in err_lower
        or "filenotfound" in err_lower
    ):
        return "file_missing"
    if "openslide" in err_lower:
        return "openslide_error"
    return "inference_error"


# ── DB writer ─────────────────────────────────────────────────────────────────

class QCDBWriter:

    def __init__(self, session: Session) -> None:
        self._session = session
        self._trigger_repo = TriggerRepository(session)
        self._event_repo = EventStoreRepository(session)
        self._file_repo = FileRecordRepository(session)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_file_record(self, file_record_id: Optional[int]) -> Optional[FileRecord]:
        if file_record_id is None:
            return None
        return self._session.execute(
            select(FileRecord).where(FileRecord.internal_id == file_record_id)
        ).scalar_one_or_none()

    def _extract_payload_field(
        self,
        trigger: ServiceTrigger,
        key: str,
        override: Optional[str],
    ) -> Optional[str]:
        """Return override if truthy, else read from trigger_payload_json."""
        if override:
            return override
        payload = trigger.trigger_payload_json or {}
        return payload.get(key) or None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_qc_result(
        self,
        *,
        trigger: ServiceTrigger,
        # QC decision (from old adapter — normalised internally)
        decision_status: str,
        decision_reason: str,
        # Module metrics (all optional — runner passes what it has)
        stain_json: Optional[dict] = None,
        penmark_json: Optional[dict] = None,
        bubble_json: Optional[dict] = None,
        blur_json: Optional[dict] = None,
        sharpness_json: Optional[dict] = None,
        # Decision outputs
        decision_threshold_json: Optional[dict] = None,
        final_routed_path: Optional[str] = None,
        total_duration_seconds: Optional[float] = None,
        raw_qc_payload: Optional[dict] = None,
        model_versions_json: Optional[dict] = None,
        # Identity / tracing (caller may override; trigger values are fallbacks)
        global_artifact_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        runner_id: Optional[str] = None,
        host_id: Optional[str] = None,
        service_version: Optional[str] = None,
        # Context — auto-extracted from trigger payload; explicit params win
        qc_context: Optional[str] = None,
        input_mode: Optional[str] = None,
        source_path: Optional[str] = None,
        scanner_id: Optional[str] = None,
        scanner_name: Optional[str] = None,
        # Scanner / policy fields
        trust_scanner_qc: Optional[bool] = None,
        pathoryx_qc_required: Optional[bool] = None,
        qc_skip_reason: Optional[str] = None,
        # Timing — trigger.started_at used when not supplied explicitly
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        # Resource tracking — populated by runner via ResourceMonitor
        memory_rss_mb: Optional[float] = None,
        cpu_percent_avg: Optional[float] = None,
        # Downstream — explicit values from scanner policy; fallback to dicom defaults
        next_service: Optional[str] = None,
        next_stage: Optional[str] = None,
    ) -> None:
        """
        Persist QC inference outcome, update FileRecord status, and dispatch
        the downstream trigger when QC passes.

        decision_status accepts old adapter values ("passed"/"failed") and
        enterprise values ("accepted"/"rejected"); both are normalised.
        """
        # ── 1. Normalise decision ──────────────────────────────────────────
        enterprise_decision = _normalize_decision(decision_status)
        file_status = _ENTERPRISE_TO_FILE_STATUS[enterprise_decision]
        qc_outcome = _ENTERPRISE_TO_QC_RESULT[enterprise_decision]

        file_record_id = trigger.file_record_internal_id
        artifact_id = global_artifact_id or trigger.global_artifact_id

        # ── 2. Auto-extract context from Phase 4 trigger payload ───────────
        _payload = trigger.trigger_payload_json or {}
        _qc_context = self._extract_payload_field(trigger, "qc_context", qc_context) or "post_babelshark"
        _input_mode = self._extract_payload_field(trigger, "input_mode", input_mode) or "trigger"
        _source_path = self._extract_payload_field(trigger, "source_path", source_path)
        _scanner_id = self._extract_payload_field(trigger, "scanner_id", scanner_id)
        _scanner_name = self._extract_payload_field(trigger, "scanner_name", scanner_name)

        # ── 3. Timing ──────────────────────────────────────────────────────
        _started_at = started_at or trigger.started_at   # set by dequeue_next
        _finished_at = finished_at or utc_now()

        # ── 4. Fetch FileRecord (needed for status + file size) ────────────
        record = self._fetch_file_record(file_record_id)
        _input_file_size = record.file_size if record is not None else None

        # ── 4b. Resolve source_path from FileRecord when absent from payload ─
        # This handles RecoverySentry-recovered slides whose QC trigger was
        # enqueued without a source_path in the payload.  FileRecord is always
        # the authoritative path store after recovery.
        if not _source_path and record is not None:
            _source_path = record.current_file_path or record.canonical_path or None
            if _source_path:
                logger.debug(
                    "source_path resolved from FileRecord",
                    trigger_id=trigger.internal_id,
                    source_path=_source_path,
                )

        # ── 5. Downstream routing — use caller-supplied (config-driven) values only.
        #        None = do not dispatch.  No hardcoded fallbacks here; the runner
        #        is responsible for deriving these from QCServiceConfig.
        if enterprise_decision == "accepted":
            _next_service: Optional[str] = next_service
            _next_stage: Optional[str] = next_stage
        else:
            _next_service = None
            _next_stage = None

        # ── 6. Persist QC result (idempotent) ─────────────────────────────
        idempotency_key = deterministic_artifact_id(
            "qc_result", str(trigger.internal_id), enterprise_decision
        )
        existing = self._session.execute(
            select(QCResult).where(QCResult.idempotency_key == idempotency_key)
        ).scalar_one_or_none()

        if existing is None:
            now = utc_now()
            qc_row = QCResult(
                # Idempotency
                idempotency_key=idempotency_key,
                # Lineage
                file_record_internal_id=file_record_id,
                trigger_internal_id=trigger.internal_id,
                global_artifact_id=artifact_id,
                global_run_id=correlation_id,
                correlation_id=correlation_id,
                # QC outcome
                qc_result=qc_outcome,
                decision_status=enterprise_decision,
                decision_reason=decision_reason,
                # Module metrics
                stain_metrics=stain_json,
                penmark_metrics=penmark_json,
                bubble_metrics=bubble_json,
                blur_metrics=blur_json,
                sharpness_metrics=sharpness_json,
                # Decision outputs
                decision_threshold_json=decision_threshold_json,
                final_routed_path=final_routed_path,
                total_duration_seconds=total_duration_seconds,
                model_versions_json=model_versions_json,
                raw_qc_payload_json=raw_qc_payload,
                # Operational metadata
                runner_id=runner_id,
                host_id=host_id,
                service_version=service_version,
                processed_at=now,
                # Context (Phase 4)
                qc_context=_qc_context,
                input_mode=_input_mode,
                source_path=_source_path,
                scanner_id=_scanner_id,
                scanner_name=_scanner_name,
                # Policy
                trust_scanner_qc=trust_scanner_qc,
                pathoryx_qc_required=pathoryx_qc_required,
                qc_skip_reason=qc_skip_reason,
                # Routing
                next_service=_next_service,
                next_stage=_next_stage,
                # Timing (Phase 5)
                started_at=_started_at,
                finished_at=_finished_at,
                # Resources (Phase 5 — NULL until runner uses ResourceMonitor)
                memory_rss_mb=memory_rss_mb,
                cpu_percent_avg=cpu_percent_avg,
                # File info (Phase 5)
                input_file_size_bytes=_input_file_size,
            )
            self._session.add(qc_row)
            self._session.flush()

        # ── 7. Update FileRecord status ────────────────────────────────────
        if record is not None:
            record.status = file_status   # "qc_passed" or "qc_failed"
            self._session.flush()

        # ── 8. Mark originating trigger complete ───────────────────────────
        self._trigger_repo.mark_completed(trigger=trigger)

        # ── 9. Dispatch downstream when QC passed and next_service is configured ──
        # source_path is already resolved from FileRecord at step 4b if it was
        # absent from the trigger payload (RecoverySentry recovery path).
        # Never create a trigger with a missing source_path — the downstream
        # service (DICOM) raises ValueError on an empty source_path.
        if enterprise_decision == "accepted" and record is not None and _next_service:
            if not _source_path:
                # All fallbacks exhausted — emit a clear failure event, skip trigger.
                logger.error(
                    "downstream dispatch skipped: source_path unresolvable "
                    "(trigger_id=%s, target=%s, file_record_id=%s)",
                    trigger.internal_id, _next_service, file_record_id,
                )
                self._event_repo.append(
                    event_type="qc.downstream_dispatch_failed",
                    aggregate_type="file_record",
                    aggregate_id=artifact_id or str(file_record_id or ""),
                    service_name=SERVICE_NAME,
                    event_payload={
                        "reason": "source_path_unresolvable",
                        "target_service": _next_service,
                        "target_stage": _next_stage,
                        "trigger_id": trigger.internal_id,
                        "file_record_id": file_record_id,
                    },
                    file_record_internal_id=file_record_id,
                    global_artifact_id=artifact_id,
                    correlation_id=correlation_id,
                    runner_id=runner_id,
                )
            else:
                # Propagate priority fields from incoming trigger payload
                _priority = int(_payload.get("priority", 5))
                downstream_payload = {
                    "source_path": _source_path,
                    "scanner_id": _scanner_id,
                    "scanner_name": _scanner_name,
                    "global_artifact_id": artifact_id,
                    "file_record_internal_id": file_record_id,
                    "correlation_id": correlation_id,
                    "qc_context": _qc_context,
                    "source_service": SERVICE_NAME,
                    "priority": _priority,
                    "priority_source": _payload.get("priority_source", "default"),
                    "watch_folder_path": _payload.get("watch_folder_path"),
                    "watch_folder_label": _payload.get("watch_folder_label"),
                }
                self._trigger_repo.enqueue(
                    source_service=SERVICE_NAME,
                    target_service=_next_service,
                    stage_name=_next_stage or "",
                    file_record_internal_id=file_record_id,
                    global_artifact_id=artifact_id,
                    correlation_id=correlation_id,
                    runner_id=runner_id,
                    payload=downstream_payload,
                    priority=_priority,
                )

        # ── 10. Immutable event ────────────────────────────────────────────
        event_type = (
            "file.qc_passed" if enterprise_decision == "accepted" else "file.qc_failed"
        )
        self._event_repo.append(
            event_type=event_type,
            aggregate_type="file_record",
            aggregate_id=artifact_id or str(file_record_id or ""),
            service_name=SERVICE_NAME,
            event_payload={
                "decision_status": enterprise_decision,
                "qc_result": qc_outcome,
                "decision_reason": decision_reason,
                "trigger_id": trigger.internal_id,
                "duration_seconds": total_duration_seconds,
                "qc_context": _qc_context,
                "scanner_id": _scanner_id,
                "source_path": _source_path,
            },
            file_record_internal_id=file_record_id,
            global_artifact_id=artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        # ── 11. Metrics ────────────────────────────────────────────────────
        if enterprise_decision == "accepted":
            files_processed_total.labels(service=SERVICE_NAME, stage="qc").inc()
        else:
            files_failed_total.labels(
                service=SERVICE_NAME, stage="qc", error_type="qc_rejected"
            ).inc()
        events_appended_total.labels(event_type=event_type, service=SERVICE_NAME).inc()

    # ------------------------------------------------------------------

    def record_qc_error(
        self,
        *,
        trigger: ServiceTrigger,
        error: str,
        # Identity
        global_artifact_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        runner_id: Optional[str] = None,
        host_id: Optional[str] = None,
        service_version: Optional[str] = None,
        # Error classification — auto-derived from error string when absent
        error_reason: Optional[str] = None,
        # Timing — trigger.started_at / utc_now() used when not supplied
        finished_at: Optional[datetime] = None,
        # Resource tracking — populated by runner via ResourceMonitor
        memory_rss_mb: Optional[float] = None,
        cpu_percent_avg: Optional[float] = None,
    ) -> None:
        """
        Record a processing error (OpenSlide crash, missing file, inference
        failure) without crashing the trigger loop.

        Writes a qc.qc_results row with qc_result='failed' / decision_status
        ='rejected' so the error is visible in the same table as successful
        runs.  Sets file_records.status='qc_failed' and marks the trigger
        failed (eligible for retry up to max_retries).

        file_records.status is ALWAYS set to 'qc_failed' — never the generic
        'failed' which violates ck_file_records_status.
        """
        file_record_id = trigger.file_record_internal_id
        artifact_id = global_artifact_id or trigger.global_artifact_id

        # ── 1. Classify error ──────────────────────────────────────────────
        _error_reason = error_reason or _classify_error_reason(error)

        # ── 2. Auto-extract context from trigger payload ───────────────────
        _qc_context = self._extract_payload_field(trigger, "qc_context", None) or "post_babelshark"
        _input_mode = self._extract_payload_field(trigger, "input_mode", None) or "trigger"
        _source_path = self._extract_payload_field(trigger, "source_path", None)
        _scanner_id = self._extract_payload_field(trigger, "scanner_id", None)
        _scanner_name = self._extract_payload_field(trigger, "scanner_name", None)

        # ── 3. Timing ──────────────────────────────────────────────────────
        _started_at = trigger.started_at
        _finished_at = finished_at or utc_now()

        # ── 4. Fetch FileRecord for size + status update ───────────────────
        record = self._fetch_file_record(file_record_id)
        _input_file_size = record.file_size if record is not None else None

        # ── 5. Write qc_results row for this error (Phase 5 addition) ─────
        idempotency_key = deterministic_artifact_id("qc_error", str(trigger.internal_id))
        existing = self._session.execute(
            select(QCResult).where(QCResult.idempotency_key == idempotency_key)
        ).scalar_one_or_none()

        if existing is None:
            now = utc_now()
            qc_row = QCResult(
                idempotency_key=idempotency_key,
                # Lineage
                file_record_internal_id=file_record_id,
                trigger_internal_id=trigger.internal_id,
                global_artifact_id=artifact_id,
                global_run_id=correlation_id,
                correlation_id=correlation_id,
                # Outcome — always failed/rejected for errors
                qc_result="failed",
                decision_status="rejected",
                decision_reason=error,
                error_reason=_error_reason,
                # Context
                qc_context=_qc_context,
                input_mode=_input_mode,
                source_path=_source_path,
                scanner_id=_scanner_id,
                scanner_name=_scanner_name,
                # Routing — nothing dispatched on error
                next_service=None,
                next_stage=None,
                # Operational metadata
                runner_id=runner_id,
                host_id=host_id,
                service_version=service_version,
                processed_at=now,
                # Timing (Phase 5)
                started_at=_started_at,
                finished_at=_finished_at,
                # Resources (Phase 5)
                memory_rss_mb=memory_rss_mb,
                cpu_percent_avg=cpu_percent_avg,
                # File info (Phase 5)
                input_file_size_bytes=_input_file_size,
            )
            self._session.add(qc_row)
            self._session.flush()

        # ── 6. Update FileRecord status ────────────────────────────────────
        if record is not None:
            record.status = "qc_failed"   # "failed" violates ck_file_records_status
            self._session.flush()

        # ── 7. Mark trigger failed (eligible for retry) ────────────────────
        self._trigger_repo.mark_failed(trigger=trigger, error_message=error)

        # ── 8. Immutable event ─────────────────────────────────────────────
        self._event_repo.append(
            event_type="file.qc_error",
            aggregate_type="file_record",
            aggregate_id=artifact_id or str(file_record_id or ""),
            service_name=SERVICE_NAME,
            event_payload={
                "error": error,
                "error_reason": _error_reason,
                "trigger_id": trigger.internal_id,
                "qc_context": _qc_context,
                "scanner_id": _scanner_id,
                "source_path": _source_path,
            },
            file_record_internal_id=file_record_id,
            global_artifact_id=artifact_id,
            global_run_id=correlation_id,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
        )

        # ── 9. Metrics ─────────────────────────────────────────────────────
        files_failed_total.labels(
            service=SERVICE_NAME, stage="qc", error_type="exception"
        ).inc()
        events_appended_total.labels(
            event_type="file.qc_error", service=SERVICE_NAME
        ).inc()
