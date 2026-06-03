"""
Read-only database queries for the dashboard service.

All functions accept a SQLAlchemy Session and return ORM objects or aggregated dicts.
No writes are performed here. Callers are responsible for error handling.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.babelshark import (
    DatamatrixResult,
    ExtractionResult,
    RoiResult,
    SlideRoutingDecision,
    StainResult,
)
from pathoryx_enterprise.db.models.core import (
    FileRecord,
    RunnerRegistration,
    ServiceTrigger,
)
from pathoryx_enterprise.db.models.dicomizer import ConversionResult
from pathoryx_enterprise.db.models.events import PipelineEvent
from pathoryx_enterprise.db.models.failed_watcher import TechnicianChange, WatchedFolderSnapshot
from pathoryx_enterprise.db.models.qc import QCResult
from pathoryx_enterprise.db.models.uploader import UploadResult

# How many seconds of silence before a runner is considered stale.
RUNNER_STALE_THRESHOLD_SECONDS = 120


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def count_slides_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(FileRecord.status, func.count().label("cnt")).group_by(FileRecord.status)
    ).all()
    return {(r.status or "unknown"): r.cnt for r in rows}


def count_triggers_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(ServiceTrigger.trigger_status, func.count().label("cnt"))
        .group_by(ServiceTrigger.trigger_status)
    ).all()
    return {(r.trigger_status or "unknown"): r.cnt for r in rows}


def count_runners_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(RunnerRegistration.status, func.count().label("cnt"))
        .group_by(RunnerRegistration.status)
    ).all()
    return {r.status: r.cnt for r in rows}


def count_events_since(session: Session, since: datetime) -> int:
    result = session.execute(
        select(func.count()).select_from(PipelineEvent).where(PipelineEvent.occurred_at >= since)
    ).scalar()
    return result or 0


# ---------------------------------------------------------------------------
# Slides list
# ---------------------------------------------------------------------------


def list_slides(
    session: Session,
    status: Optional[str],
    page: int,
    page_size: int,
) -> tuple[int, list[FileRecord]]:
    base_q = select(FileRecord)
    if status:
        base_q = base_q.where(FileRecord.status == status)

    total: int = session.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar() or 0

    items = (
        session.execute(
            base_q.order_by(FileRecord.internal_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return total, list(items)


# ---------------------------------------------------------------------------
# Slide detail
# ---------------------------------------------------------------------------


def get_slide_by_artifact_id(session: Session, global_artifact_id: str) -> Optional[FileRecord]:
    return session.execute(
        select(FileRecord).where(FileRecord.global_artifact_id == global_artifact_id)
    ).scalar_one_or_none()


def get_latest_qc_result(session: Session, file_record_internal_id: int) -> Optional[QCResult]:
    return session.execute(
        select(QCResult)
        .where(QCResult.file_record_internal_id == file_record_internal_id)
        .order_by(QCResult.internal_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_latest_conversion_result(
    session: Session, file_record_internal_id: int
) -> Optional[ConversionResult]:
    return session.execute(
        select(ConversionResult)
        .where(ConversionResult.file_record_internal_id == file_record_internal_id)
        .order_by(ConversionResult.internal_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_latest_upload_result(
    session: Session, file_record_internal_id: int
) -> Optional[UploadResult]:
    return session.execute(
        select(UploadResult)
        .where(UploadResult.file_record_internal_id == file_record_internal_id)
        .order_by(UploadResult.internal_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_events_for_artifact(
    session: Session, global_artifact_id: str, limit: int = 50
) -> list[PipelineEvent]:
    rows = (
        session.execute(
            select(PipelineEvent)
            .where(PipelineEvent.global_artifact_id == global_artifact_id)
            .order_by(PipelineEvent.occurred_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows)


def list_triggers_for_artifact(session: Session, global_artifact_id: str) -> list[ServiceTrigger]:
    rows = (
        session.execute(
            select(ServiceTrigger)
            .where(ServiceTrigger.global_artifact_id == global_artifact_id)
            .order_by(ServiceTrigger.triggered_at.asc())
        )
        .scalars()
        .all()
    )
    return list(rows)


def list_recovery_for_artifact(session: Session, global_artifact_id: str) -> list[TechnicianChange]:
    rows = (
        session.execute(
            select(TechnicianChange)
            .where(TechnicianChange.global_artifact_id == global_artifact_id)
            .order_by(TechnicianChange.detected_at.desc())
        )
        .scalars()
        .all()
    )
    return list(rows)


def get_extraction_result(session: Session, file_record_internal_id: int) -> Optional[ExtractionResult]:
    return session.execute(
        select(ExtractionResult)
        .where(ExtractionResult.file_record_internal_id == file_record_internal_id)
        .order_by(ExtractionResult.internal_id.desc())
        .limit(1)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def list_recent_events(session: Session, limit: int = 100) -> list[PipelineEvent]:
    rows = (
        session.execute(
            select(PipelineEvent)
            .order_by(PipelineEvent.occurred_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows)


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


def get_queue_status(session: Session) -> list[dict]:
    """Return per-(target_service, status) counts as a list of dicts."""
    rows = session.execute(
        select(
            ServiceTrigger.target_service,
            ServiceTrigger.trigger_status,
            func.count().label("cnt"),
        ).group_by(ServiceTrigger.target_service, ServiceTrigger.trigger_status)
    ).all()

    # Pivot into {service: {status: count}}
    pivot: dict[str, dict[str, int]] = {}
    for r in rows:
        svc = r.target_service or "unknown"
        st = r.trigger_status or "unknown"
        pivot.setdefault(svc, {})[st] = r.cnt

    result = []
    for service, counts in sorted(pivot.items()):
        result.append(
            {
                "target_service": service,
                "pending": counts.get("pending", 0),
                "running": counts.get("running", 0),
                "failed": counts.get("failed", 0),
                "completed": counts.get("completed", 0),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def count_recovery_by_status(session: Session) -> dict[str, int]:
    """Return total TechnicianChange count grouped by review_status.

    Called independently of the paginated list so the dashboard can display
    accurate per-status totals regardless of the active tab or page limit.
    """
    rows = session.execute(
        select(TechnicianChange.review_status, func.count().label("cnt"))
        .group_by(TechnicianChange.review_status)
    ).all()
    return {r.review_status: r.cnt for r in rows}


def list_recovery_items(
    session: Session,
    review_status: Optional[str],
    limit: int = 50,
) -> tuple[int, list[TechnicianChange]]:
    base_q = select(TechnicianChange)
    if review_status:
        base_q = base_q.where(TechnicianChange.review_status == review_status)

    total: int = session.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar() or 0

    items = (
        session.execute(
            base_q.order_by(TechnicianChange.detected_at.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
    return total, list(items)


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def list_artifact_ids_with_recovery(
    session: Session,
    artifact_ids: list[str],
) -> list[str]:
    """Return the subset of artifact_ids that have at least one TechnicianChange.

    Uses a single IN query rather than per-artifact lookups. Returns empty list
    immediately when artifact_ids is empty so no DB round-trip is wasted.
    """
    if not artifact_ids:
        return []
    rows = (
        session.execute(
            select(TechnicianChange.global_artifact_id)
            .where(TechnicianChange.global_artifact_id.in_(artifact_ids))
            .where(TechnicianChange.global_artifact_id.isnot(None))
            .distinct()
        )
        .scalars()
        .all()
    )
    return [r for r in rows if r is not None]


def list_failed_slides(session: Session, limit: int = 50) -> list[FileRecord]:
    rows = (
        session.execute(
            select(FileRecord)
            .where(FileRecord.status.like("%_failed"))
            .order_by(FileRecord.updated_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows)


def list_failed_triggers(session: Session, limit: int = 50) -> list[ServiceTrigger]:
    rows = (
        session.execute(
            select(ServiceTrigger)
            .where(ServiceTrigger.trigger_status == "failed")
            .order_by(ServiceTrigger.triggered_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows)


# ---------------------------------------------------------------------------
# Services health
# ---------------------------------------------------------------------------


def list_runners(session: Session) -> list[RunnerRegistration]:
    rows = (
        session.execute(
            select(RunnerRegistration).order_by(
                RunnerRegistration.service_name, RunnerRegistration.started_at.desc()
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# ---------------------------------------------------------------------------
# Recovery — watched folder stats (WatchedFolderSnapshot)
# ---------------------------------------------------------------------------


def get_watch_folder_stats(session: Session) -> list[dict]:
    """Per-folder file counts and review-state aggregates for the three watched folders."""
    stmt = text("""
        WITH folder_counts AS (
            SELECT
                wfs.folder_label,
                MIN(wfs.folder_path)                                                  AS folder_path,
                COUNT(*)                                                               AS total_files,
                COUNT(*) FILTER (WHERE wfs.last_seen_at > NOW() - INTERVAL '24 hours') AS recently_changed,
                MAX(wfs.last_seen_at)                                                  AS last_scan_time
            FROM failed_watcher.watched_folder_snapshots wfs
            GROUP BY wfs.folder_label
        ),
        change_counts AS (
            SELECT
                tc.watch_folder_label,
                COUNT(*) FILTER (WHERE tc.review_status IN ('detected', 'unlinked'))  AS awaiting_review,
                COUNT(*) FILTER (WHERE tc.recovery_outcome = 'auto_recovered')        AS auto_recovered
            FROM failed_watcher.technician_changes tc
            GROUP BY tc.watch_folder_label
        )
        SELECT
            fc.folder_label,
            fc.folder_path,
            CAST(fc.total_files      AS INTEGER) AS total_files,
            CAST(fc.recently_changed AS INTEGER) AS recently_changed,
            fc.last_scan_time,
            CAST(COALESCE(cc.awaiting_review, 0) AS INTEGER) AS awaiting_review,
            CAST(COALESCE(cc.auto_recovered,  0) AS INTEGER) AS auto_recovered
        FROM folder_counts fc
        LEFT JOIN change_counts cc ON cc.watch_folder_label = fc.folder_label
        ORDER BY fc.folder_label
    """)
    rows = session.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Recovery — monitored file listing
# ---------------------------------------------------------------------------


def list_monitored_files(
    session: Session,
    folder_type: Optional[str] = None,
    review_status: Optional[str] = None,
    recovery_status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
) -> tuple[int, list[dict]]:
    """
    List files from watched folder snapshots with their latest TechnicianChange.

    Files without any TechnicianChange are included — they are displayed as
    "unreviewed" in the dashboard.  The review_status / recovery_status filters
    only apply when a TechnicianChange exists for that file.
    """
    conditions: list[str] = []
    params: dict = {"limit": limit}

    if folder_type:
        conditions.append("wfs.folder_label = :folder_type")
        params["folder_type"] = folder_type
    if search:
        conditions.append("wfs.filename ILIKE :search_pattern")
        params["search_pattern"] = f"%{search}%"
    if review_status:
        conditions.append("tc.review_status = :review_status")
        params["review_status"] = review_status
    if recovery_status:
        conditions.append("tc.recovery_outcome = :recovery_status")
        params["recovery_status"] = recovery_status

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    lateral = """
        LEFT JOIN LATERAL (
            SELECT tc2.*
            FROM failed_watcher.technician_changes tc2
            WHERE tc2.watch_folder_label = wfs.folder_label
              AND tc2.new_path = wfs.file_path
            ORDER BY tc2.detected_at DESC
            LIMIT 1
        ) tc ON true
    """

    count_sql = text(f"""
        SELECT COUNT(*) FROM (
            SELECT wfs.internal_id
            FROM failed_watcher.watched_folder_snapshots wfs
            {lateral}
            {where}
        ) sub
    """)

    data_sql = text(f"""
        SELECT
            wfs.internal_id             AS file_id,
            wfs.filename,
            wfs.file_path,
            wfs.folder_label,
            wfs.folder_path,
            wfs.first_seen_at,
            wfs.last_seen_at,
            wfs.file_size,
            wfs.slide_id,
            wfs.case_id,
            wfs.extension,
            wfs.global_artifact_id,
            wfs.file_record_internal_id,
            tc.internal_id              AS change_id,
            tc.change_type,
            tc.review_status,
            tc.recovery_outcome,
            tc.recovery_reason,
            tc.detected_at,
            tc.inferred_action
        FROM failed_watcher.watched_folder_snapshots wfs
        {lateral}
        {where}
        ORDER BY wfs.last_seen_at DESC NULLS LAST
        LIMIT :limit
    """)

    total: int = session.execute(count_sql, params).scalar() or 0
    rows = session.execute(data_sql, params).mappings().all()
    return total, [dict(r) for r in rows]


def get_monitored_file(session: Session, file_id: int) -> Optional[WatchedFolderSnapshot]:
    return session.execute(
        select(WatchedFolderSnapshot).where(WatchedFolderSnapshot.internal_id == file_id)
    ).scalar_one_or_none()


def get_label_preview_data(session: Session, file_id: int) -> dict:
    """
    Return all label/extraction metadata for a watched folder file.

    Aggregates: ExtractionResult, DatamatrixResult, StainResult, RoiResult,
    SlideRoutingDecision.  The technician can immediately see what the system
    parsed, what it could not parse, and why the artifact was routed to
    failed/suspicious.

    Returns available=False gracefully when no linked record exists.
    """
    snap = session.execute(
        select(WatchedFolderSnapshot).where(WatchedFolderSnapshot.internal_id == file_id)
    ).scalar_one_or_none()

    if snap is None:
        return {
            "file_id": file_id, "filename": None,
            "available": False, "unavailable_reason": "file_not_found",
        }

    base: dict = {
        "file_id": file_id,
        "filename": snap.filename,
        "slide_id": snap.slide_id,
        "case_id": snap.case_id,
        "available": False,
        "unavailable_reason": None,
        "scanner_id": None,
        "scanner_vendor": None,
        "scanner_model": None,
        "stain_type": None,
        "suggested_filename": snap.filename,
        "datamatrix_raw": None,
        "datamatrix_decode_status": None,
        "datamatrix_error": None,
        "stain_ocr_raw": None,
        "stain_matched": None,
        "stain_origin": None,
        "roi_case_number": None,
        "roi_lab_id": None,
        "roi_stain": None,
        "routing_type": None,
        "routing_reason": None,
        "original_filename": snap.filename,
        "extraction_metadata": None,
    }

    if not snap.global_artifact_id and not snap.file_record_internal_id:
        base["unavailable_reason"] = "no_linked_record"
        return base

    def _q(model):
        q = select(model)
        if snap.file_record_internal_id:
            q = q.where(model.file_record_internal_id == snap.file_record_internal_id)
        else:
            q = q.where(model.global_artifact_id == snap.global_artifact_id)
        return q

    # ExtractionResult — scanner identity + slide ID + intake decision
    ext_row = session.execute(
        _q(ExtractionResult).order_by(ExtractionResult.internal_id.desc()).limit(1)
    ).scalar_one_or_none()
    if ext_row:
        base["scanner_id"]    = ext_row.scanner_id
        base["scanner_vendor"] = ext_row.scanner_vendor
        base["scanner_model"] = ext_row.scanner_model
        base["stain_type"]    = ext_row.stain_type
        if ext_row.slide_id:
            base["slide_id"]           = ext_row.slide_id
            base["suggested_filename"] = f"{ext_row.slide_id}{snap.extension or ''}"
        base["extraction_metadata"] = {
            "intake_decision":    ext_row.intake_decision,
            "action_taken":       ext_row.action_taken,
            "extraction_status":  ext_row.extraction_status,
            "requires_qc":        ext_row.requires_qc,
            "next_stage":         ext_row.next_stage,
        }
        base["available"] = True

    # DatamatrixResult — raw barcode value and decode status
    dm_row = session.execute(
        _q(DatamatrixResult).order_by(DatamatrixResult.internal_id.desc()).limit(1)
    ).scalar_one_or_none()
    if dm_row:
        base["datamatrix_raw"]           = dm_row.datamatrix_raw
        base["datamatrix_decode_status"] = dm_row.decode_status
        base["datamatrix_error"]         = dm_row.error_reason
        if dm_row.datamatrix_raw:
            base["available"] = True

    # StainResult — OCR text and matched stain
    stain_row = session.execute(
        _q(StainResult).order_by(StainResult.internal_id.desc()).limit(1)
    ).scalar_one_or_none()
    if stain_row:
        base["stain_ocr_raw"]  = stain_row.raw_ocr_words
        base["stain_matched"]  = stain_row.stain_final or stain_row.matched_word
        base["stain_origin"]   = stain_row.stain_origin
        base["available"]      = True

    # RoiResult — fallback metadata extraction
    roi_row = session.execute(
        _q(RoiResult).order_by(RoiResult.internal_id.desc()).limit(1)
    ).scalar_one_or_none()
    if roi_row:
        base["roi_case_number"] = roi_row.case_number
        base["roi_lab_id"]      = roi_row.lab_id
        base["roi_stain"]       = roi_row.stain
        base["available"]       = True

    # SlideRoutingDecision — why it ended up in failed/suspicious
    routing_row = session.execute(
        _q(SlideRoutingDecision).order_by(SlideRoutingDecision.internal_id.desc()).limit(1)
    ).scalar_one_or_none()
    if routing_row:
        base["routing_type"]       = routing_row.routing_type
        base["routing_reason"]     = routing_row.routing_reason
        base["original_filename"]  = routing_row.original_filename or snap.filename
        base["available"]          = True
        # If the routing decision has a slide_id, prefer it for suggested filename
        if routing_row.slide_id and not base.get("suggested_filename", snap.filename) != snap.filename:
            base["suggested_filename"] = f"{routing_row.slide_id}{snap.extension or ''}"

    if not base["available"]:
        base["unavailable_reason"] = "no_extraction_result"

    return base


def get_technician_change(
    session: Session, change_id: int
) -> Optional[TechnicianChange]:
    return session.execute(
        select(TechnicianChange).where(TechnicianChange.internal_id == change_id)
    ).scalar_one_or_none()


def get_artifact_audit_trail(session: Session, file_id: int) -> dict:
    """
    Return the complete audit history for a watched folder file.

    Combines:
      - All TechnicianChange records touching this file (by path or artifact ID)
      - All PipelineEvents for the linked global_artifact_id

    Items are ordered oldest-first so the UI can render a timeline.
    """
    snap = session.execute(
        select(WatchedFolderSnapshot).where(WatchedFolderSnapshot.internal_id == file_id)
    ).scalar_one_or_none()

    if snap is None:
        return {"file_id": file_id, "changes": [], "events": []}

    # All TechnicianChange records for this file (by file_path match on new/old path)
    changes_stmt = (
        select(TechnicianChange)
        .where(
            (TechnicianChange.new_path == snap.file_path) |
            (TechnicianChange.old_path == snap.file_path) |
            (
                (snap.global_artifact_id is not None) &
                (TechnicianChange.global_artifact_id == snap.global_artifact_id)
            )
        )
        .order_by(TechnicianChange.detected_at.asc())
    )
    change_rows = session.execute(changes_stmt).scalars().all()

    changes = []
    for c in change_rows:
        changes.append({
            "change_id":       c.internal_id,
            "change_type":     c.change_type,
            "inferred_action": c.inferred_action,
            "old_filename":    c.old_filename,
            "new_filename":    c.new_filename,
            "old_path":        c.old_path,
            "new_path":        c.new_path,
            "review_status":   c.review_status,
            "recovery_outcome": c.recovery_outcome,
            "recovery_reason": c.recovery_reason,
            "technician_notes": c.technician_notes,
            "review_notes":    c.review_notes,
            "detected_at":     c.detected_at.isoformat() if c.detected_at else None,
            "recovered_at":    c.recovered_at.isoformat() if c.recovered_at else None,
            "requeued_at":     c.requeued_at.isoformat() if c.requeued_at else None,
            "reviewed_at":     c.reviewed_at.isoformat() if c.reviewed_at else None,
        })

    # PipelineEvents for the linked artifact
    events = []
    if snap.global_artifact_id:
        event_rows = session.execute(
            select(PipelineEvent)
            .where(PipelineEvent.global_artifact_id == snap.global_artifact_id)
            .order_by(PipelineEvent.occurred_at.asc())
            .limit(100)
        ).scalars().all()
        for e in event_rows:
            events.append({
                "event_id":    e.event_id,
                "event_type":  e.event_type,
                "service_name": e.service_name,
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                "event_payload": e.event_payload,
            })

    return {
        "file_id":           file_id,
        "filename":          snap.filename,
        "global_artifact_id": snap.global_artifact_id,
        "changes":           changes,
        "events":            events,
    }
