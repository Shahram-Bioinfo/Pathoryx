"""
Dashboard operator actions — controlled mutations for the RecoverySentry workflow.

Safety invariants enforced here:
  - proposed_filename must be a plain name (no slashes, no '..')
  - The file must exist within a configured watched folder
  - The rename stays inside the same watched folder; final placement is done
    by process_recovery() following the same path as manual technician renames

Audit trail produced (identical to an automated detection):
  - TechnicianChange row (inferred_action='dashboard_correction')
  - PipelineEvent rows emitted by process_recovery / record_manual_review_required
  - WatchedFolderSnapshot updated to reflect the new path

Review state lifecycle:
  detected → investigating → corrected | dismissed
  unlinked → investigating | dismissed
  corrected → requeued
  dismissed → detected  (can reopen)
  Each transition emits a PipelineEvent so the full history is queryable.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from pathoryx_enterprise.db.models.failed_watcher import TechnicianChange, WatchedFolderSnapshot
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.repositories.failed_watcher import (
    TechnicianChangeRepository,
    WatchedFolderSnapshotRepository,
)
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery
from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import (
    SUPPORTED_EXTENSIONS,
    parse_slide_id,
)
from pathoryx_enterprise.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

_RUNNER_ID = "dashboard"
_SERVICE_NAME = "dashboard"

# Regex to detect a recognisable CaseID prefix even when the rest of the
# filename is wrong — used for "partially_valid" classification.
_CASE_ID_PREFIX_RE = re.compile(r"^(N\d{10})")

# Valid review-state transitions.  Keys are the *current* status; values are
# the set of statuses the operator may transition to from that state.
_REVIEW_TRANSITIONS: dict[str, frozenset[str]] = {
    "detected":      frozenset({"investigating", "dismissed", "reviewed"}),
    "unlinked":      frozenset({"investigating", "dismissed"}),
    "linked":        frozenset({"investigating", "reviewed"}),
    "investigating": frozenset({"corrected", "dismissed", "detected"}),
    "corrected":     frozenset({"requeued", "investigating"}),
    "requeued":      frozenset({"reviewed"}),
    "reviewed":      frozenset(),          # terminal — nothing further
    "dismissed":     frozenset({"detected"}),  # operator may re-open
}


class ActionError(ValueError):
    """Raised when an operator action is rejected due to a safety or validation failure."""


# ---------------------------------------------------------------------------
# Structured filename validation (no filesystem side-effects)
# ---------------------------------------------------------------------------


def validate_filename_structured(filename: str) -> dict:
    """
    Validate *filename* against the Pathoryx slide ID format and return a
    richly structured result.

    Designed for live UI feedback — safe to call on every keystroke.
    Does NOT touch the filesystem or DB.

    Classification values:
      valid          — parse succeeds; all components present
      partially_valid — case ID is recognisable but rest of structure is wrong,
                        OR parse succeeds but timestamp absent (recoverable via
                        WSI metadata extraction)
      invalid        — extension unsupported OR completely unrecognised
    """
    stripped = (filename or "").strip()

    base: dict = {
        "filename": stripped,
        "classification": "invalid",
        "components": None,
        "errors": [],
        "warnings": [],
        "suggested_correction": None,
    }

    if not stripped:
        base["errors"].append({"code": "empty", "message": "Filename cannot be empty"})
        return base

    if "/" in stripped or "\\" in stripped or stripped != Path(stripped).name:
        base["errors"].append({
            "code": "path_traversal",
            "message": "Filename must be a plain name with no directory components",
        })
        return base

    if ".." in stripped:
        base["errors"].append({"code": "dotdot", "message": "Filename must not contain '..'"})
        return base

    p = Path(stripped)
    ext = p.suffix.lower()
    stem = p.stem

    if not ext:
        base["errors"].append({"code": "no_extension", "message": "Filename must have a file extension"})
        return base

    if ext not in SUPPORTED_EXTENSIONS:
        base["errors"].append({
            "code": "invalid_extension",
            "message": (
                f"Extension '{ext}' is not a supported WSI format. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            ),
        })
        return base

    # Try the real parser
    parsed = parse_slide_id(stripped)
    if parsed is not None:
        base["components"] = {
            "case_id": parsed.case_id,
            "pot": parsed.pot,
            "block": parsed.block,
            "section": parsed.section,
            "stain": parsed.stain,
            "timestamp": parsed.timestamp_iso_z,
            "extension": parsed.extension,
        }
        if parsed.has_timestamp:
            base["classification"] = "valid"
        else:
            base["classification"] = "partially_valid"
            base["warnings"].append({
                "code": "no_timestamp",
                "message": (
                    "Timestamp absent — system will attempt to extract acquisition time "
                    "from WSI metadata. If unavailable, manual review will be required."
                ),
            })
        return base

    # parse_slide_id failed — diagnose the reason
    m = _CASE_ID_PREFIX_RE.match(stem)
    if m:
        case_id = m.group(1)
        base["classification"] = "partially_valid"
        base["components"] = {
            "case_id": case_id,
            "pot": None, "block": None, "section": None,
            "stain": None, "timestamp": None, "extension": ext,
        }
        base["errors"].append({
            "code": "invalid_structure",
            "message": (
                f"Case ID '{case_id}' is recognisable, but the structure after it is invalid. "
                f"Expected: {case_id}<POT>-<BLOCK>-<SECTION>-<STAIN>[_UTC<ts>]{ext}"
            ),
        })
        base["suggested_correction"] = f"{case_id}SA-1-1-H&E{ext}"
    else:
        base["errors"].append({
            "code": "unrecognized",
            "message": (
                "Filename does not match the Pathoryx slide ID format. "
                "Required: N<10-digits><POT>-<BLOCK>-<SECTION>-<STAIN>[_UTC<timestamp>].<ext>"
            ),
        })

    return base


# ---------------------------------------------------------------------------
# Review state management
# ---------------------------------------------------------------------------


def update_review_state(
    change_id: int,
    new_status: str,
    technician_note: Optional[str],
) -> dict:
    """
    Transition a TechnicianChange to a new review_status.

    Enforces the allowed transition table, updates the DB row, and emits an
    immutable PipelineEvent so the full review history is queryable.

    Returns the updated state dict, or raises ActionError on invalid transition.
    """
    from sqlalchemy import select

    if new_status not in _REVIEW_TRANSITIONS:
        raise ActionError(
            f"'{new_status}' is not a recognised review status. "
            f"Valid values: {sorted(_REVIEW_TRANSITIONS)}"
        )

    now = utc_now()

    try:
        with get_session() as session:
            change_row: Optional[TechnicianChange] = session.execute(
                select(TechnicianChange).where(TechnicianChange.internal_id == change_id)
            ).scalar_one_or_none()

            if change_row is None:
                raise ActionError(f"TechnicianChange {change_id} not found")

            current = change_row.review_status
            allowed = _REVIEW_TRANSITIONS.get(current, frozenset())
            if new_status not in allowed:
                raise ActionError(
                    f"Cannot transition from '{current}' to '{new_status}'. "
                    f"Allowed next states: {sorted(allowed) or ['(none — terminal state)']}"
                )

            change_row.review_status = new_status
            change_row.reviewed_at = now
            if technician_note:
                change_row.review_notes = technician_note
            session.flush()

            # Emit immutable audit event
            event_repo = EventStoreRepository(session)
            agg_id = (
                change_row.global_artifact_id
                or change_row.new_path
                or str(change_id)
            )
            event_repo.append(
                event_type="dashboard.review_state_updated",
                aggregate_type="wsi_file",
                aggregate_id=agg_id,
                service_name=_SERVICE_NAME,
                event_payload={
                    "change_id": change_id,
                    "previous_status": current,
                    "new_status": new_status,
                    "technician_note": technician_note,
                    "folder": change_row.watch_folder_label,
                    "filename": change_row.new_filename or change_row.old_filename,
                },
                file_record_internal_id=change_row.file_record_internal_id,
                global_artifact_id=change_row.global_artifact_id,
                runner_id=_RUNNER_ID,
            )

            return {
                "change_id": change_id,
                "previous_status": current,
                "new_status": new_status,
                "reviewed_at": now.isoformat(),
            }

    except ActionError:
        raise
    except Exception as exc:
        logger.error("update_review_state: DB error: %s", exc)
        raise ActionError(f"Failed to update review state: {exc}") from exc


# ---------------------------------------------------------------------------
# Internal validators
# ---------------------------------------------------------------------------


def _validate_proposed_filename(filename: str) -> None:
    """Raise ActionError if filename is structurally unsafe."""
    stripped = (filename or "").strip()
    if not stripped:
        raise ActionError("proposed_filename cannot be empty")
    if stripped != Path(stripped).name:
        raise ActionError("proposed_filename must be a plain filename — no directory components")
    if ".." in stripped:
        raise ActionError("proposed_filename must not contain '..'")
    if "/" in stripped or "\\" in stripped:
        raise ActionError("proposed_filename must not contain path separators")
    ext = Path(stripped).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ActionError(
            f"unsupported file extension '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )


def _resolve_watch_folder(
    file_path: Path,
    watch_folders: list[Path],
) -> Path:
    """
    Return the configured watch folder that contains file_path.
    Resolves symlinks before comparison to prevent traversal.
    Raises ActionError when file_path is not under any configured folder.
    """
    resolved_file = file_path.resolve()
    for folder in watch_folders:
        try:
            resolved_file.relative_to(folder.resolve())
            return folder
        except ValueError:
            continue
    raise ActionError(
        "File is not within any configured watched folder — "
        "dashboard rename is only permitted inside: "
        + ", ".join(str(f) for f in watch_folders)
    )


# ---------------------------------------------------------------------------
# Public action
# ---------------------------------------------------------------------------


def execute_technician_rename(
    *,
    snapshot: WatchedFolderSnapshot,
    proposed_filename: str,
    technician_note: Optional[str],
    watch_folders: list[Path],
    settings: object,  # RecoverySentrySettings — avoid hard coupling at import time
) -> dict:
    """
    Rename a watched-folder file to a technician-supplied name, then run the
    same recovery logic that handles automated technician renames.

    Steps:
      1. Structural filename safety check
      2. SlideID pattern validation (same parser as RecoverySentry)
      3. Path-traversal guard — file must be in a configured watch folder
      4. OS rename (skipped if filename unchanged)
      5. TechnicianChange + WatchedFolderSnapshot DB update
      6. process_recovery() — produces events, QC trigger, FileRecord update
      7. Return outcome dict

    Returns a plain dict (not a Pydantic model) so callers can forward it
    directly as a JSON response body.
    """
    proposed_filename = proposed_filename.strip()
    _validate_proposed_filename(proposed_filename)

    parsed = parse_slide_id(proposed_filename)
    if parsed is None:
        raise ActionError(
            f"'{proposed_filename}' does not match the Pathoryx slide ID pattern "
            "(expected e.g. N2024002863SA-1-1-H&E.svs or N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs)"
        )

    current_path = Path(snapshot.file_path)
    watch_folder = _resolve_watch_folder(current_path, watch_folders)
    folder_label = watch_folder.name

    if not current_path.exists():
        raise ActionError(
            "File no longer exists at its recorded path — "
            "it may have been moved by the automated watcher on the last scan cycle."
        )

    new_path = current_path.parent / proposed_filename
    renamed_on_disk = False

    if current_path.name != proposed_filename:
        # Security: new path must stay inside the same watch folder
        try:
            new_path.resolve().relative_to(watch_folder.resolve())
        except ValueError:
            raise ActionError("Rename target falls outside the watched folder.")

        if new_path.exists():
            raise ActionError(
                f"A file named '{proposed_filename}' already exists in this folder."
            )
        try:
            current_path.rename(new_path)
            renamed_on_disk = True
        except OSError as exc:
            raise ActionError(f"OS rename failed: {exc}") from exc

    now = utc_now()
    change_id: Optional[int] = None

    # DB update: TechnicianChange + snapshot
    try:
        with get_session() as session:
            change_repo = TechnicianChangeRepository(session)
            snap_repo = WatchedFolderSnapshotRepository(session)

            change_rec, _ = change_repo.record_change(
                change_type="rename",
                watch_folder_label=folder_label,
                detected_at=now,
                old_path=snapshot.file_path,
                old_filename=snapshot.filename,
                old_file_size=snapshot.file_size,
                new_path=str(new_path),
                new_filename=proposed_filename,
                inferred_action="dashboard_correction",
                slide_id_inferred=parsed.slide_id_base,
                technician_notes=technician_note,
                global_artifact_id=snapshot.global_artifact_id,
                file_record_internal_id=snapshot.file_record_internal_id,
                runner_id=_RUNNER_ID,
            )
            change_id = change_rec.internal_id

            snap_repo.upsert(
                folder_label=folder_label,
                folder_path=str(watch_folder),
                file_path=str(new_path),
                filename=proposed_filename,
                slide_id=parsed.slide_id_base,
                case_id=parsed.case_id,
                extension=parsed.extension,
                global_artifact_id=snapshot.global_artifact_id,
                file_record_internal_id=snapshot.file_record_internal_id,
            )
            if renamed_on_disk:
                snap_repo.delete_by_path(folder_label, snapshot.file_path)

    except Exception as exc:
        # Non-fatal: the OS rename is already committed; log and proceed so
        # process_recovery can still run and emit events.
        logger.error("Failed to write TechnicianChange / snapshot: %s", exc)

    # Run the shared recovery engine — same path as manual filesystem renames
    result = process_recovery(
        new_path=str(new_path),
        new_filename=proposed_filename,
        change_type="rename",
        technician_change_id=change_id,
        file_record_internal_id=snapshot.file_record_internal_id,
        global_artifact_id=snapshot.global_artifact_id,
        correlation_id=None,
        runner_id=_RUNNER_ID,
        settings=settings,
    )

    return {
        "outcome": result.outcome,
        "reason": result.reason,
        "destination_path": str(result.destination_path) if result.destination_path else None,
        "final_filename": result.final_filename,
        "case_id": result.case_id,
        "slide_id": result.slide_id,
        "change_id": change_id,
        "validation_error": None,
    }
