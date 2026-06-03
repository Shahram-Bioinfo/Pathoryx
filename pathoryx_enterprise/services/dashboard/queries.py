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

from pathoryx_enterprise.db.models.babelshark import DatamatrixResult, ExtractionResult
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
    Return label extraction / DataMatrix metadata for a watched folder file.
    Returns available=False gracefully when no linked record exists.
    """
    snap = session.execute(
        select(WatchedFolderSnapshot).where(WatchedFolderSnapshot.internal_id == file_id)
    ).scalar_one_or_none()

    if snap is None:
        return {"file_id": file_id, "filename": None, "available": False, "unavailable_reason": "file_not_found"}

    base: dict = {
        "file_id": file_id,
        "filename": snap.filename,
        "slide_id": snap.slide_id,
        "case_id": snap.case_id,
        "available": False,
        "unavailable_reason": None,
        "scanner_id": None,
        "scanner_vendor": None,
        "stain_type": None,
        "suggested_filename": snap.filename,
        "datamatrix_raw": None,
        "extraction_metadata": None,
    }

    if not snap.global_artifact_id and not snap.file_record_internal_id:
        base["unavailable_reason"] = "no_linked_record"
        return base

    ext_q = select(ExtractionResult)
    if snap.file_record_internal_id:
        ext_q = ext_q.where(ExtractionResult.file_record_internal_id == snap.file_record_internal_id)
    else:
        ext_q = ext_q.where(ExtractionResult.global_artifact_id == snap.global_artifact_id)
    ext_row = session.execute(ext_q.order_by(ExtractionResult.internal_id.desc()).limit(1)).scalar_one_or_none()

    if ext_row:
        base["scanner_id"]   = ext_row.scanner_id
        base["scanner_vendor"] = ext_row.scanner_vendor
        base["stain_type"]   = ext_row.stain_type
        if ext_row.slide_id:
            base["slide_id"]          = ext_row.slide_id
            base["suggested_filename"] = f"{ext_row.slide_id}{snap.extension or ''}"
        base["extraction_metadata"] = {
            "intake_decision":    ext_row.intake_decision,
            "extraction_status":  ext_row.extraction_status,
            "requires_qc":        ext_row.requires_qc,
        }
        base["available"] = True

    dm_q = select(DatamatrixResult).where(DatamatrixResult.datamatrix_raw.isnot(None))
    if snap.file_record_internal_id:
        dm_q = dm_q.where(DatamatrixResult.file_record_internal_id == snap.file_record_internal_id)
    else:
        dm_q = dm_q.where(DatamatrixResult.global_artifact_id == snap.global_artifact_id)
    dm_row = session.execute(dm_q.order_by(DatamatrixResult.internal_id.desc()).limit(1)).scalar_one_or_none()

    if dm_row:
        base["datamatrix_raw"] = dm_row.datamatrix_raw
        base["available"] = True

    if not base["available"]:
        base["unavailable_reason"] = "no_extraction_result"

    return base
