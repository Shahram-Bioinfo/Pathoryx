"""
Failed Watcher main scan loop.

Each scan cycle:
  1. Scan each watched folder for current filesystem state.
  2. Load WatchedFolderSnapshot rows from DB for that folder.
  3. Detect changes (ADDED | DELETED | MODIFIED).
  4. For each change: record TechnicianChange + update WatchedFolderSnapshot.
  5. Sleep until next cycle.

Idempotent: re-running the same scan on an unchanged folder produces no new records.
"""
from __future__ import annotations

import time
from pathlib import Path

import structlog

from pathoryx_enterprise.db.repositories.failed_watcher import (
    TechnicianChangeRepository,
    WatchedFolderSnapshotRepository,
)
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.monitoring.metrics import (
    files_detected_total,
    technician_changes_recorded_total,
)
from pathoryx_enterprise.services.failed_watcher.change_detector import (
    ChangeEvent,
    detect_changes,
    scan_folder,
)
from pathoryx_enterprise.services.failed_watcher.config import FailedWatcherSettings
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.file_utils import is_file_stable

logger = structlog.get_logger(__name__)


def _record_change(
    change: ChangeEvent,
    settings: FailedWatcherSettings,
    runner_id: str,
) -> bool:
    """Record a single ChangeEvent in the DB. Returns True if a new record was created."""
    with get_session() as session:
        snap_repo = WatchedFolderSnapshotRepository(session)
        change_repo = TechnicianChangeRepository(session)

        _record, created = change_repo.record_change(
            change_type=change.change_type,
            watch_folder_label=change.folder_label,
            detected_at=utc_now(),
            old_path=change.old_path,
            old_filename=change.old_filename,
            old_file_size=change.old_file_size,
            old_mtime_ns=change.old_mtime_ns,
            new_path=change.new_path,
            new_filename=change.new_filename,
            new_file_size=change.new_file_size,
            new_mtime_ns=change.new_mtime_ns,
            inferred_action=change.inferred_action,
            slide_id_inferred=change.slide_id_inferred,
            requires_approval=settings.requires_approval_default,
            runner_id=runner_id,
        )

        if not created:
            return False

        # Update snapshot to reflect current state
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
            )

        return True


def scan_once(settings: FailedWatcherSettings, runner_id: str) -> int:
    """
    Run one full scan of all watched folders.
    Returns the total number of new change records created.
    """
    total_new = 0

    for folder_path, folder_label in zip(settings.watch_folders, settings.folder_labels):
        if not folder_path.exists():
            logger.warning("watch folder missing", path=str(folder_path), label=folder_label)
            continue

        logger.debug("scanning folder", path=str(folder_path), label=folder_label)

        current_state = scan_folder(
            folder_path,
            allowed_roots=settings.allowed_roots,
        )

        # Filter: only stable files (no sleep — is_file_stable uses time comparison)
        stable_state = {
            path: info
            for path, info in current_state.items()
            if is_file_stable(Path(path), stable_after_seconds=5)
        }

        files_detected_total.labels(
            service="failed_watcher", folder_label=folder_label
        ).inc(len(stable_state))

        with get_session() as session:
            db_snapshots = WatchedFolderSnapshotRepository(session).get_all_for_folder(
                folder_label
            )

        changes = detect_changes(folder_label, stable_state, db_snapshots)

        for change in changes:
            try:
                created = _record_change(change, settings, runner_id)
                if created:
                    total_new += 1
                    technician_changes_recorded_total.labels(
                        change_type=change.change_type
                    ).inc()
                    logger.info(
                        "change detected",
                        change_type=change.change_type,
                        folder=folder_label,
                        path=change.new_path or change.old_path,
                    )
            except Exception:
                logger.exception(
                    "failed to record change",
                    change_type=change.change_type,
                    path=change.new_path or change.old_path,
                )

    return total_new
