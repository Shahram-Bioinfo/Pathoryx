"""
RecoverySentry change processor.

Integrates change detection (from failed_watcher infrastructure) with the
RecoverySentry recovery engine. Each scan cycle:

  1. Scan each watched folder for current filesystem state.
  2. Compare against DB snapshots → detect changes.
  3. For each stable new/modified file: attempt recovery.
  4. Record all changes and outcomes in DB.
  5. Emit events.

Reuses:
  - failed_watcher.change_detector.scan_folder / detect_changes
  - failed_watcher.WatchedFolderSnapshotRepository
  - failed_watcher.TechnicianChangeRepository
  - recovery_engine.process_recovery / record_manual_review_required
"""
from __future__ import annotations

from pathlib import Path

import structlog

from pathoryx_enterprise.db.repositories.failed_watcher import (
    TechnicianChangeRepository,
    WatchedFolderSnapshotRepository,
)
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.services.failed_watcher.change_detector import (
    ChangeEvent,
    detect_changes,
    scan_folder,
)
from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
from pathoryx_enterprise.services.recovery_sentry.metadata_extractor import compute_partial_sha256
from pathoryx_enterprise.services.recovery_sentry.recovery_engine import (
    process_recovery,
    record_manual_review_required,
)
from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import parse_slide_id
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.file_utils import is_file_stable

logger = structlog.get_logger(__name__)

SERVICE_NAME = "recovery_sentry"


def scan_and_recover_once(settings: RecoverySentrySettings, runner_id: str) -> dict:
    """
    Run one full scan-and-recover cycle across all watched folders.

    Returns a summary dict with counts for observability.
    """
    summary = {
        "folders_scanned": 0,
        "changes_detected": 0,
        "auto_recovered": 0,
        "manual_review_required": 0,
        "errors": 0,
    }

    for folder_path in settings.watch_folders:
        if not folder_path.exists():
            logger.warning("watch_folder_missing", path=str(folder_path))
            continue

        folder_label = folder_path.name
        summary["folders_scanned"] += 1

        try:
            _process_folder(folder_path, folder_label, runner_id, settings, summary)
        except Exception as exc:
            summary["errors"] += 1
            logger.exception("folder_scan_failed", folder=str(folder_path), error=str(exc))

    return summary


def _process_folder(
    folder_path: Path,
    folder_label: str,
    runner_id: str,
    settings: RecoverySentrySettings,
    summary: dict,
) -> None:
    log = logger.bind(folder=str(folder_path), label=folder_label)

    # Scan filesystem
    current_state = scan_folder(
        folder_path,
        allowed_roots=settings.allowed_roots or [folder_path],
    )

    # Filter to stable files only
    stable_state = {
        path: info
        for path, info in current_state.items()
        if is_file_stable(Path(path), stable_after_seconds=settings.stable_after_seconds)
    }

    log.debug("folder_scanned", total=len(current_state), stable=len(stable_state))

    # Load DB snapshots
    with get_session() as session:
        db_snapshots = WatchedFolderSnapshotRepository(session).get_all_for_folder(folder_label)

    # Detect changes
    changes = detect_changes(folder_label, stable_state, db_snapshots)
    summary["changes_detected"] += len(changes)

    for change in changes:
        try:
            _process_change(change, runner_id, settings, summary)
        except Exception as exc:
            summary["errors"] += 1
            logger.exception(
                "change_processing_failed",
                change_type=change.change_type,
                path=change.new_path or change.old_path,
                error=str(exc),
            )


def _process_change(
    change: ChangeEvent,
    runner_id: str,
    settings: RecoverySentrySettings,
    summary: dict,
) -> None:
    log = logger.bind(
        change_type=change.change_type,
        folder=change.folder_label,
        path=change.new_path or change.old_path,
    )

    now = utc_now()

    # Enrich with slide_id if parseable
    parsed = parse_slide_id(change.new_filename or "") if change.new_filename else None
    slide_id_inferred = parsed.slide_id_base if parsed else None
    case_id_inferred = parsed.case_id if parsed else None

    # Compute partial checksum if enabled and file exists
    partial_hash = None
    if (
        settings.checksum_mode != "none"
        and change.new_path
        and Path(change.new_path).exists()
    ):
        partial_hash = compute_partial_sha256(Path(change.new_path))

    # Record TechnicianChange (idempotent)
    with get_session() as session:
        snap_repo = WatchedFolderSnapshotRepository(session)
        change_repo = TechnicianChangeRepository(session)

        change_rec, created = change_repo.record_change(
            change_type=change.change_type,
            watch_folder_label=change.folder_label,
            detected_at=now,
            old_path=change.old_path,
            old_filename=change.old_filename,
            old_file_size=change.old_file_size,
            old_mtime_ns=change.old_mtime_ns,
            new_path=change.new_path,
            new_filename=change.new_filename,
            new_file_size=change.new_file_size,
            new_mtime_ns=change.new_mtime_ns,
            inferred_action=change.inferred_action,
            slide_id_inferred=slide_id_inferred,
            requires_approval=settings.requires_approval_default,
            runner_id=runner_id,
        )

        change_id = change_rec.internal_id if created else change_rec.internal_id

        # Update snapshot to reflect current filesystem state
        if change.change_type == "DELETED":
            snap_repo.delete_by_path(change.folder_label, change.old_path or "")
        else:
            snap_repo.upsert(
                folder_label=change.folder_label,
                folder_path=str(Path(change.new_path or "").parent),
                file_path=change.new_path or "",
                filename=change.new_filename or "",
                file_size=change.new_file_size,
                mtime_ns=change.new_mtime_ns,
                slide_id=slide_id_inferred,
                case_id=case_id_inferred,
                extension=Path(change.new_filename or "").suffix.lower() or None,
                inode_number=_get_inode(change.new_path),
                partial_sha256=partial_hash,
            )

    if not created:
        # Already processed in a previous cycle
        return

    log.info("change_detected_and_recorded", change_id=change_id)

    # DELETED changes: just record, no recovery
    if change.change_type == "DELETED":
        return

    # Attempt recovery
    result = process_recovery(
        new_path=change.new_path or "",
        new_filename=change.new_filename or "",
        change_type=change.change_type,
        technician_change_id=change_id,
        file_record_internal_id=None,  # resolved in recovery_engine if global_artifact_id known
        global_artifact_id=None,
        correlation_id=None,
        runner_id=runner_id,
        settings=settings,
    )

    if result.outcome == "auto_recovered":
        summary["auto_recovered"] += 1
        log.info(
            "auto_recovered",
            dest=str(result.destination_path),
            case_id=result.case_id,
            slide_id=result.slide_id,
            timestamp_extracted=result.timestamp_extracted_from_wsi,
        )
    elif result.outcome == "manual_review_required":
        summary["manual_review_required"] += 1
        log.info(
            "manual_review_required",
            reason=result.reason,
            case_id=result.case_id,
        )
        record_manual_review_required(
            technician_change_id=change_id,
            reason=result.reason or "unknown",
            case_id=result.case_id,
            slide_id=result.slide_id,
            new_path=change.new_path,
            global_artifact_id=None,
            file_record_internal_id=None,
            correlation_id=None,
            runner_id=runner_id,
        )


def _get_inode(path_str: Optional[str]) -> Optional[int]:
    if not path_str:
        return None
    try:
        return Path(path_str).stat().st_ino
    except OSError:
        return None
