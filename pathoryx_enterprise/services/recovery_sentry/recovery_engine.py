"""
RecoverySentry recovery engine.

Decides whether a changed/added file can be auto-recovered into the normal
Palantir pipeline, and executes the recovery if so.

Decision tree:
  1. Extension must be a supported WSI format.
  2. Filename must parse as a valid Palantir SlideID.
  3. If timestamp is in the filename → Case 1 (ready to move).
  4. If timestamp is missing → extract from WSI metadata.
     If extraction fails → manual_review_required(missing_timestamp_metadata).
  5. Check destination for duplicates.
  6. Atomic move to final/<CaseID>/<FinalFilename>.
  7. Update core.file_records.
  8. Enqueue QC trigger.
  9. Emit events.

Safety guarantees:
  - Never moves unless filename is valid.
  - Never overwrites existing files (configurable: safe suffix or manual_review).
  - Uses atomic_move so partial writes don't corrupt final/.
  - All DB writes are in a single transaction; if any fail, filesystem change
    is logged as a critical compensation event.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import structlog

from pathoryx_enterprise.db.models.failed_watcher import TechnicianChange
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.repositories.failed_watcher import TechnicianChangeRepository
from pathoryx_enterprise.db.repositories.file_record import FileRecordRepository
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
from pathoryx_enterprise.services.recovery_sentry.metadata_extractor import (
    compute_partial_sha256,
    extract_scan_timestamp,
)
from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import (
    SUPPORTED_EXTENSIONS,
    ParsedSlideID,
    build_final_filename,
    parse_slide_id,
)
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.file_utils import atomic_move, unique_dest
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = structlog.get_logger(__name__)

SERVICE_NAME = "recovery_sentry"

RecoveryOutcome = Literal[
    "auto_recovered",
    "manual_review_required",
    "deleted",
    "skipped",
]


@dataclass
class RecoveryResult:
    outcome: RecoveryOutcome
    reason: Optional[str] = None
    destination_path: Optional[Path] = None
    final_filename: Optional[str] = None
    case_id: Optional[str] = None
    slide_id: Optional[str] = None
    timestamp_in_filename: bool = False
    timestamp_extracted_from_wsi: bool = False
    events_emitted: list[str] = field(default_factory=list)


def process_recovery(
    *,
    new_path: str,
    new_filename: str,
    change_type: str,
    technician_change_id: Optional[int],
    file_record_internal_id: Optional[int],
    global_artifact_id: Optional[str],
    correlation_id: Optional[str],
    runner_id: Optional[str],
    settings: RecoverySentrySettings,
) -> RecoveryResult:
    """
    Main recovery decision and execution function.

    Called once per detected change. Not inside the DB session — opens its
    own sessions for DB writes after the filesystem move succeeds.
    """
    log = logger.bind(
        path=new_path,
        filename=new_filename,
        change_type=change_type,
    )

    # DELETED files: record but don't try to recover
    if change_type in ("DELETED", "deleted"):
        return RecoveryResult(outcome="deleted")

    # Must have a path to work with
    if not new_path or not new_filename:
        return RecoveryResult(outcome="skipped", reason="no_new_path")

    file_path = Path(new_path)
    if not file_path.exists():
        log.warning("file_disappeared_before_recovery")
        return RecoveryResult(outcome="manual_review_required", reason="file_not_found")

    # Extension check
    ext = Path(new_filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return RecoveryResult(
            outcome="manual_review_required",
            reason="unsupported_file_format",
        )

    # Auto-recovery gate
    if not settings.auto_recover_valid_slide_id:
        return RecoveryResult(outcome="skipped", reason="auto_recovery_disabled")

    # Parse slide ID
    parsed = parse_slide_id(new_filename)
    if parsed is None:
        log.info("slide_id_parse_failed", filename=new_filename)
        return RecoveryResult(
            outcome="manual_review_required",
            reason="invalid_slide_id_pattern",
            case_id=_try_extract_case_id(new_filename),
        )

    log = log.bind(case_id=parsed.case_id, slide_id_base=parsed.slide_id_base)

    # Resolve final destination root
    dest_root = settings.final_destination
    if dest_root is None:
        log.error("final_destination_root_not_configured")
        return RecoveryResult(
            outcome="manual_review_required",
            reason="final_destination_not_configured",
            case_id=parsed.case_id,
        )

    # Determine timestamp
    timestamp_in_filename = parsed.has_timestamp
    timestamp_extracted = False
    iso_z_ts: Optional[str] = parsed.timestamp_iso_z

    if not timestamp_in_filename:
        if not settings.add_timestamp_if_missing:
            return RecoveryResult(
                outcome="manual_review_required",
                reason="timestamp_missing_and_addition_disabled",
                case_id=parsed.case_id,
            )
        iso_z_ts = extract_scan_timestamp(
            file_path,
            allow_filesystem_fallback=settings.allow_filesystem_timestamp_fallback,
        )
        if iso_z_ts is None:
            log.warning("no_timestamp_available", filename=new_filename)
            return RecoveryResult(
                outcome="manual_review_required",
                reason="missing_timestamp_metadata",
                case_id=parsed.case_id,
            )
        timestamp_extracted = True

    # Build final filename and destination path
    final_name = build_final_filename(parsed, iso_z=iso_z_ts if timestamp_extracted else None)
    dest_dir = dest_root / parsed.case_id
    dest_path = dest_dir / final_name

    # Duplicate destination handling
    if dest_path.exists():
        if settings.overwrite_existing:
            log.warning("overwriting_existing_destination", dest=str(dest_path))
        elif settings.duplicate_strategy == "suffix":
            dest_path = unique_dest(dest_path)
            log.info("safe_suffix_applied", dest=str(dest_path))
        else:
            return RecoveryResult(
                outcome="manual_review_required",
                reason="duplicate_destination",
                case_id=parsed.case_id,
            )

    # === Filesystem move (point of no return) ===
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        atomic_move(file_path, dest_path)
        log.info(
            "file_moved_to_final",
            dest=str(dest_path),
            timestamp_extracted=timestamp_extracted,
        )
    except OSError as exc:
        log.error("atomic_move_failed", dest=str(dest_path), error=str(exc))
        return RecoveryResult(
            outcome="manual_review_required",
            reason="file_move_failed",
            case_id=parsed.case_id,
        )

    # === DB updates (in a single session, best-effort after filesystem move) ===
    # Use dest_path.name — may differ from final_name if a safe suffix was applied
    actual_final_name = dest_path.name
    slide_id_final = str(Path(actual_final_name).stem)
    _persist_recovery(
        parsed=parsed,
        dest_path=dest_path,
        final_name=actual_final_name,
        slide_id_final=slide_id_final,
        source_path=new_path,
        source_filename=new_filename,
        iso_z_ts=iso_z_ts,
        timestamp_in_filename=timestamp_in_filename,
        timestamp_extracted=timestamp_extracted,
        technician_change_id=technician_change_id,
        hint_file_record_internal_id=file_record_internal_id,
        hint_global_artifact_id=global_artifact_id,
        correlation_id=correlation_id,
        runner_id=runner_id,
        settings=settings,
    )

    events = ["recovery_sentry.auto_recovered"]
    if timestamp_extracted:
        events.insert(0, "recovery_sentry.timestamp_added")

    return RecoveryResult(
        outcome="auto_recovered",
        destination_path=dest_path,
        final_filename=actual_final_name,
        case_id=parsed.case_id,
        slide_id=slide_id_final,
        timestamp_in_filename=timestamp_in_filename,
        timestamp_extracted_from_wsi=timestamp_extracted,
        events_emitted=events,
    )


def _persist_recovery(
    *,
    parsed: ParsedSlideID,
    dest_path: Path,
    final_name: str,
    slide_id_final: str,
    source_path: str,
    source_filename: str,
    iso_z_ts: Optional[str],
    timestamp_in_filename: bool,
    timestamp_extracted: bool,
    technician_change_id: Optional[int],
    hint_file_record_internal_id: Optional[int],
    hint_global_artifact_id: Optional[str],
    correlation_id: Optional[str],
    runner_id: Optional[str],
    settings: RecoverySentrySettings,
) -> None:
    """
    Persist all DB changes for a successful recovery in one transaction.

    Always resolves or creates a core.file_records row, then enqueues
    an idempotent QC trigger linked to that row.

    Lookup order for existing FileRecord:
      1. By hint_file_record_internal_id if provided.
      2. By canonical_path == source_path (file was known before it failed).
      3. By canonical_path == dest_path (recovery already ran once — idempotent re-run).
      4. Create a new FileRecord (first-time recovery of an unregistered file).

    If the transaction fails after a filesystem move, logs CRITICAL with
    enough context for manual SQL recovery.
    """
    from sqlalchemy import select

    from pathoryx_enterprise.db.models.core import FileRecord
    from pathoryx_enterprise.db.models.upload_tracking import EstimatedUploadQueue

    now = utc_now()

    # Durable IDs based on the final canonical path — stable across re-runs
    dest_canonical = str(dest_path)
    artifact_id = hint_global_artifact_id or deterministic_artifact_id(
        "recovery_sentry:artifact", dest_canonical
    )
    source_artifact_id = deterministic_artifact_id("recovery_sentry:source", dest_canonical)

    try:
        with get_session() as session:
            fr_repo = FileRecordRepository(session)
            trigger_repo = TriggerRepository(session)
            event_repo = EventStoreRepository(session)

            # ── Step 1: resolve or create FileRecord ────────────────────────
            record: Optional[FileRecord] = None

            if hint_file_record_internal_id is not None:
                record = session.execute(
                    select(FileRecord).where(
                        FileRecord.internal_id == hint_file_record_internal_id
                    )
                ).scalar_one_or_none()

            if record is None and source_path:
                record = fr_repo.get_by_canonical_path(source_path)

            if record is None:
                record = fr_repo.get_by_canonical_path(dest_canonical)

            if record is None:
                # No pre-existing record — create one now.
                record, _ = fr_repo.get_or_create_safe(
                    dest_canonical,
                    defaults=dict(
                        source_service=SERVICE_NAME,
                        source_artifact_id=source_artifact_id,
                        global_artifact_id=artifact_id,
                        artifact_type="wsi_slide",
                        original_filename=source_filename,
                        original_path=source_path,
                        current_filename=final_name,
                        current_file_path=dest_canonical,
                        file_format=parsed.extension,
                        status="qc_pending",
                    ),
                )

            # Always bring the record up to date with the recovery outcome
            record.current_file_path = dest_canonical
            record.current_filename = final_name
            record.canonical_path = dest_canonical
            record.status = "qc_pending"
            if not record.global_artifact_id:
                record.global_artifact_id = artifact_id
            if not record.source_service:
                record.source_service = SERVICE_NAME
            session.flush()

            resolved_fr_id = record.internal_id
            resolved_artifact_id = record.global_artifact_id or artifact_id

            # ── Step 1b: preserve priority from existing upload queue row ────
            # If this file was previously queued, carry its priority forward
            # so operator-set or watch-folder priority survives the recovery
            # cycle. Fall back to 5 (normal) for first-time recoveries.
            existing_queue_row = session.execute(
                select(EstimatedUploadQueue).where(
                    EstimatedUploadQueue.file_record_internal_id == resolved_fr_id
                )
            ).scalar_one_or_none()
            recovered_priority: int = (
                existing_queue_row.priority
                if existing_queue_row is not None
                else 5
            )

            # ── Step 2: idempotent QC trigger ────────────────────────────────
            # Include source_path so downstream services (QC → DICOM) can
            # resolve the WSI file without an extra FileRecord DB lookup.
            trigger, trigger_created = trigger_repo.enqueue(
                source_service=SERVICE_NAME,
                target_service=settings.next_stage_target_service,
                stage_name=settings.next_stage_name,
                file_record_internal_id=resolved_fr_id,
                global_artifact_id=resolved_artifact_id,
                correlation_id=correlation_id,
                runner_id=runner_id,
                priority=recovered_priority,
                payload={
                    "source_path": dest_canonical,
                    "global_artifact_id": resolved_artifact_id,
                    "file_record_internal_id": resolved_fr_id,
                    "correlation_id": correlation_id,
                    "source_service": SERVICE_NAME,
                    "case_id": parsed.case_id,
                    "slide_id": slide_id_final,
                    "priority": recovered_priority,
                    "priority_source": (
                        existing_queue_row.priority_source
                        if existing_queue_row is not None
                        else "default"
                    ),
                },
            )

            # ── Step 3: immutable events ─────────────────────────────────────
            payload = {
                "case_id": parsed.case_id,
                "slide_id": slide_id_final,
                "source_path": source_path,
                "destination_path": dest_canonical,
                "timestamp_in_filename": timestamp_in_filename,
                "timestamp_extracted_from_wsi": timestamp_extracted,
                "iso_timestamp": iso_z_ts,
                "service_name": SERVICE_NAME,
                "file_record_internal_id": resolved_fr_id,
            }

            if timestamp_extracted:
                event_repo.append(
                    event_type="recovery_sentry.timestamp_extracted",
                    aggregate_type="wsi_file",
                    aggregate_id=resolved_artifact_id,
                    service_name=SERVICE_NAME,
                    event_payload={**payload, "source": "wsi_metadata"},
                    file_record_internal_id=resolved_fr_id,
                    global_artifact_id=resolved_artifact_id,
                    correlation_id=correlation_id,
                    runner_id=runner_id,
                )
                event_repo.append(
                    event_type="recovery_sentry.timestamp_added",
                    aggregate_type="wsi_file",
                    aggregate_id=resolved_artifact_id,
                    service_name=SERVICE_NAME,
                    event_payload=payload,
                    file_record_internal_id=resolved_fr_id,
                    global_artifact_id=resolved_artifact_id,
                    correlation_id=correlation_id,
                    runner_id=runner_id,
                )

            event_repo.append(
                event_type="recovery_sentry.auto_recovered",
                aggregate_type="wsi_file",
                aggregate_id=resolved_artifact_id,
                service_name=SERVICE_NAME,
                event_payload=payload,
                file_record_internal_id=resolved_fr_id,
                global_artifact_id=resolved_artifact_id,
                correlation_id=correlation_id,
                runner_id=runner_id,
            )

            event_repo.append(
                event_type="recovery_sentry.qc_requeued",
                aggregate_type="wsi_file",
                aggregate_id=resolved_artifact_id,
                service_name=SERVICE_NAME,
                event_payload={
                    **payload,
                    "target_service": settings.next_stage_target_service,
                    "stage_name": settings.next_stage_name,
                    "trigger_id": trigger.internal_id,
                    "trigger_created": trigger_created,
                },
                file_record_internal_id=resolved_fr_id,
                global_artifact_id=resolved_artifact_id,
                correlation_id=correlation_id,
                runner_id=runner_id,
            )

            # ── Step 4: update TechnicianChange audit row ─────────────────────
            if technician_change_id is not None:
                change_row = session.execute(
                    select(TechnicianChange).where(
                        TechnicianChange.internal_id == technician_change_id
                    )
                ).scalar_one_or_none()
                if change_row is not None:
                    change_row.recovery_outcome = "auto_recovered"
                    change_row.recovery_destination_path = dest_canonical
                    change_row.recovered_at = now
                    change_row.case_id = parsed.case_id
                    change_row.timestamp_in_filename = timestamp_in_filename
                    change_row.timestamp_extracted_from_wsi = timestamp_extracted
                    change_row.global_artifact_id = resolved_artifact_id
                    change_row.file_record_internal_id = resolved_fr_id
                    change_row.requeue_trigger_id = trigger.internal_id
                    change_row.requeued_at = now
                    change_row.review_status = "requeued"
                    change_row.reviewed_at = now
                    session.flush()

            logger.info(
                "recovery_persisted",
                file_record_id=resolved_fr_id,
                artifact_id=resolved_artifact_id,
                trigger_id=trigger.internal_id,
                trigger_created=trigger_created,
                dest=dest_canonical,
            )

    except Exception as exc:
        # CRITICAL: file is moved but DB update failed. Log for manual resolution.
        logger.critical(
            "recovery_db_update_failed_after_move",
            dest=dest_canonical,
            source=source_path,
            case_id=parsed.case_id,
            slide_id=slide_id_final,
            error=str(exc),
            action_required=(
                "File was moved to final/ but DB was not updated. "
                "Run manual SQL to update file_records and create QC trigger. "
                f"See RECOVERY_SENTRY.md for the recovery SQL."
            ),
        )


def record_manual_review_required(
    *,
    technician_change_id: Optional[int],
    reason: str,
    case_id: Optional[str],
    slide_id: Optional[str],
    new_path: Optional[str],
    global_artifact_id: Optional[str],
    file_record_internal_id: Optional[int],
    correlation_id: Optional[str],
    runner_id: Optional[str],
) -> None:
    """Persist manual_review_required outcome to DB and emit event."""
    try:
        with get_session() as session:
            event_repo = EventStoreRepository(session)

            agg_id = global_artifact_id or case_id or (slide_id or "unknown")
            event_repo.append(
                event_type="recovery_sentry.manual_review_required",
                aggregate_type="wsi_file",
                aggregate_id=agg_id,
                service_name=SERVICE_NAME,
                event_payload={
                    "reason": reason,
                    "case_id": case_id,
                    "slide_id": slide_id,
                    "path": new_path,
                    "service_name": SERVICE_NAME,
                },
                file_record_internal_id=file_record_internal_id,
                global_artifact_id=global_artifact_id,
                correlation_id=correlation_id,
                runner_id=runner_id,
            )

            if technician_change_id is not None:
                from sqlalchemy import select
                from pathoryx_enterprise.db.models.failed_watcher import TechnicianChange
                change_row = session.execute(
                    select(TechnicianChange).where(
                        TechnicianChange.internal_id == technician_change_id
                    )
                ).scalar_one_or_none()
                if change_row is not None:
                    change_row.recovery_outcome = "manual_review_required"
                    change_row.recovery_reason = reason
                    change_row.case_id = case_id
                    change_row.review_status = "unlinked"
                    session.flush()

    except Exception as exc:
        logger.error(
            "failed_to_record_manual_review_required",
            reason=reason,
            error=str(exc),
        )


def _try_extract_case_id(filename: str) -> Optional[str]:
    """Best-effort CaseID extraction from a filename that failed full parsing."""
    import re
    m = re.match(r"^(N\d{10})", Path(filename).stem)
    return m.group(1) if m else None
