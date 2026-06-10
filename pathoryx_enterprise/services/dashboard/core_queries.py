"""
Computer Core analytics queries — aggregated operational statistics.

All functions are read-only and return plain dicts suitable for Pydantic
serialization. They intentionally avoid joins that would require schema
migrations; instead they aggregate from existing tables already populated
by the pipeline services.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Float, case, cast, func, or_, select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from .scanner_fleet import ScannerFleet

from pathoryx_enterprise.db.models.babelshark import ExtractionResult
from pathoryx_enterprise.db.models.core import FileRecord
from pathoryx_enterprise.db.models.failed_watcher import TechnicianChange, WatchedFolderSnapshot
from pathoryx_enterprise.db.models.upload_tracking import EstimatedUploadQueue


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _today_start() -> datetime:
    n = _now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def _days_ago(n: int) -> datetime:
    return _now() - timedelta(days=n)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def get_core_overview(session: Session) -> dict:
    """
    Top-level operational summary — one fast pass per table.
    Returns counts suitable for the Computer Core status display.
    """
    now = _now()
    today = _today_start()

    # ── File records ─────────────────────────────────────────────────────────
    slide_rows = session.execute(
        select(
            FileRecord.status,
            func.count().label("cnt"),
            func.sum(FileRecord.file_size).label("total_size"),
        ).group_by(FileRecord.status)
    ).all()

    total_slides = 0
    failed_slides = 0
    total_bytes: int = 0
    status_map: dict[str, int] = {}
    for row in slide_rows:
        status_map[row.status or "unknown"] = row.cnt
        total_slides += row.cnt
        if row.status and ("failed" in row.status):
            failed_slides += row.cnt
        if row.total_size:
            total_bytes += row.total_size

    # Slides detected today (first seen)
    slides_today: int = session.execute(
        select(func.count()).select_from(FileRecord)
        .where(FileRecord.created_at >= today)
    ).scalar() or 0

    # ── Upload queue ─────────────────────────────────────────────────────────
    q_rows = session.execute(
        select(
            EstimatedUploadQueue.upload_status,
            func.count().label("cnt"),
        ).group_by(EstimatedUploadQueue.upload_status)
    ).all()

    q_status: dict[str, int] = {r.upload_status: r.cnt for r in q_rows}

    active_uploads  = q_status.get("uploading", 0)
    queued_uploads  = q_status.get("queued", 0) + q_status.get("estimating", 0)
    delayed_uploads = sum(
        1 for r in session.execute(
            select(EstimatedUploadQueue.estimated_upload_at, EstimatedUploadQueue.upload_status)
            .where(EstimatedUploadQueue.upload_status.notin_(["uploaded", "failed"]))
            .where(EstimatedUploadQueue.estimated_upload_at.isnot(None))
        ).all()
        if r.estimated_upload_at and r.estimated_upload_at < now
    )

    uploaded_today: int = session.execute(
        select(func.count()).select_from(EstimatedUploadQueue)
        .where(EstimatedUploadQueue.upload_status == "uploaded")
        .where(EstimatedUploadQueue.upload_completed_at >= today)
    ).scalar() or 0

    # ── Recovery backlog ──────────────────────────────────────────────────────
    recovery_backlog: int = session.execute(
        select(func.count()).select_from(WatchedFolderSnapshot)
    ).scalar() or 0

    unreviewed_changes: int = session.execute(
        select(func.count()).select_from(TechnicianChange)
        .where(TechnicianChange.review_status == "pending")
    ).scalar() or 0

    return {
        "total_slides":       total_slides,
        "slides_today":       slides_today,
        "uploaded_today":     uploaded_today,
        "failed_slides":      failed_slides,
        "active_uploads":     active_uploads,
        "queued_uploads":     queued_uploads,
        "delayed_uploads":    delayed_uploads,
        "recovery_backlog":   recovery_backlog,
        "unreviewed_changes": unreviewed_changes,
        "total_bytes":        total_bytes,
        "status_counts":      status_map,
        "upload_status_counts": q_status,
    }


# ---------------------------------------------------------------------------
# Scanner activity
# ---------------------------------------------------------------------------


def get_scanner_activity(session: Session, fleet: "ScannerFleet") -> list[dict]:
    """
    Per-scanner operational metrics inferred from FileRecord and upload queue.

    Operational state is INFERRED from recent activity:
      Active Recently  — file seen in last 48 hours
      Idle             — file seen 2–14 days ago
      No Recent Activity — nothing in last 14 days
    """
    now = _now()
    cutoff_active = now - timedelta(hours=48)
    cutoff_idle   = now - timedelta(days=14)

    # Aggregate FileRecord by scanner_id
    fr_rows = session.execute(
        select(
            FileRecord.scanner_id,
            func.count().label("total"),
            func.sum(FileRecord.file_size).label("total_bytes"),
            func.avg(cast(FileRecord.file_size, Float)).label("avg_bytes"),
            func.max(FileRecord.updated_at).label("last_seen"),
            func.sum(
                case((FileRecord.status.like("%failed%"), 1), else_=0)
            ).label("failed"),
            func.sum(
                case((FileRecord.status == "uploaded", 1), else_=0)
            ).label("uploaded"),
        ).group_by(FileRecord.scanner_id)
    ).all()

    scanner_data: dict[str, dict] = {}
    for row in fr_rows:
        sid = row.scanner_id or "_unknown"
        last_seen = row.last_seen
        if last_seen and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)

        if last_seen and last_seen >= cutoff_active:
            state = "active"
        elif last_seen and last_seen >= cutoff_idle:
            state = "idle"
        else:
            state = "no_recent_activity"

        scanner_data[sid] = {
            "scanner_id":        sid,
            "display_name":      fleet.display_name(sid) if sid != "_unknown" else "Unknown",
            "total_slides":      row.total,
            "failed_count":      row.failed or 0,
            "uploaded_count":    row.uploaded or 0,
            "total_bytes":       int(row.total_bytes or 0),
            "avg_file_size":     int(row.avg_bytes or 0),
            "last_activity":     last_seen.isoformat() if last_seen else None,
            "operational_state": state,
        }

    # Seed enabled-fleet scanners with no data yet
    for entry in fleet.enabled():
        if entry.scanner_id not in scanner_data:
            scanner_data[entry.scanner_id] = {
                "scanner_id":        entry.scanner_id,
                "display_name":      fleet.display_name(entry.scanner_id),
                "total_slides":      0,
                "failed_count":      0,
                "uploaded_count":    0,
                "total_bytes":       0,
                "avg_file_size":     0,
                "last_activity":     None,
                "operational_state": "no_recent_activity",
            }

    # Add upload bytes from EstimatedUploadQueue by scanner
    uq_rows = session.execute(
        select(
            EstimatedUploadQueue.scanner_id,
            func.sum(EstimatedUploadQueue.file_size_bytes).label("uq_bytes"),
            func.avg(cast(EstimatedUploadQueue.upload_speed_mbps, Float)).label("avg_speed"),
            func.count().label("uq_count"),
        ).group_by(EstimatedUploadQueue.scanner_id)
    ).all()

    for row in uq_rows:
        sid = row.scanner_id or "_unknown"
        if sid in scanner_data:
            scanner_data[sid]["avg_upload_speed_mbps"] = (
                round(float(row.avg_speed), 2) if row.avg_speed else None
            )
        else:
            scanner_data[sid] = {
                "scanner_id":        sid,
                "display_name":      fleet.display_name(sid) if sid != "_unknown" else "Unknown",
                "total_slides":      0,
                "failed_count":      0,
                "uploaded_count":    0,
                "total_bytes":       int(row.uq_bytes or 0),
                "avg_file_size":     0,
                "last_activity":     None,
                "operational_state": "no_recent_activity",
                "avg_upload_speed_mbps": (
                    round(float(row.avg_speed), 2) if row.avg_speed else None
                ),
            }

    result = sorted(scanner_data.values(), key=lambda x: x["total_slides"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Stain distribution
# ---------------------------------------------------------------------------


def get_stain_distribution(session: Session) -> list[dict]:
    """
    Aggregate stain_type counts from ExtractionResult.
    Returns sorted by count descending with percentage included.
    """
    rows = session.execute(
        select(
            ExtractionResult.stain_type,
            func.count().label("cnt"),
        ).group_by(ExtractionResult.stain_type)
        .order_by(func.count().desc())
    ).all()

    total = sum(r.cnt for r in rows)
    result = []
    for row in rows:
        label = row.stain_type or "Unknown"
        pct = round((row.cnt / total * 100), 1) if total else 0.0
        result.append({
            "stain_type":  label,
            "count":       row.cnt,
            "percentage":  pct,
        })
    return result


# ---------------------------------------------------------------------------
# Recovery statistics
# ---------------------------------------------------------------------------


def get_recovery_stats(session: Session) -> dict:
    """
    Aggregate recovery metrics from WatchedFolderSnapshot and TechnicianChange.
    """
    # Count by folder_label
    folder_rows = session.execute(
        select(
            WatchedFolderSnapshot.folder_label,
            func.count().label("cnt"),
        ).group_by(WatchedFolderSnapshot.folder_label)
    ).all()

    by_folder: dict[str, int] = {r.folder_label: r.cnt for r in folder_rows}
    total_monitored = sum(by_folder.values())

    # TechnicianChange by review_status
    tc_status_rows = session.execute(
        select(
            TechnicianChange.review_status,
            func.count().label("cnt"),
        ).group_by(TechnicianChange.review_status)
    ).all()
    by_review: dict[str, int] = {r.review_status: r.cnt for r in tc_status_rows}

    # TechnicianChange by recovery_outcome
    tc_outcome_rows = session.execute(
        select(
            TechnicianChange.recovery_outcome,
            func.count().label("cnt"),
        ).where(TechnicianChange.recovery_outcome.isnot(None))
        .group_by(TechnicianChange.recovery_outcome)
    ).all()
    by_outcome: dict[str, int] = {r.recovery_outcome: r.cnt for r in tc_outcome_rows}

    total_resolved = sum(
        v for k, v in by_review.items()
        if k in ("reviewed", "auto_recovered", "dismissed", "resolved")
    )
    total_changes = sum(by_review.values())
    recovery_rate = round(total_resolved / total_changes * 100, 1) if total_changes else 0.0

    # Recent (last 7 days)
    recent: int = session.execute(
        select(func.count()).select_from(TechnicianChange)
        .where(TechnicianChange.detected_at >= _days_ago(7))
    ).scalar() or 0

    return {
        "total_monitored":  total_monitored,
        "failed_count":     by_folder.get("failed", 0),
        "suspicious_count": by_folder.get("suspicious", 0),
        "manual_review_count": by_folder.get("manual_review", 0),
        "by_folder":        by_folder,
        "by_review_status": by_review,
        "by_outcome":       by_outcome,
        "auto_recovered":   by_outcome.get("auto_recovered", 0),
        "manual_review_required": by_outcome.get("manual_review_required", 0),
        "total_changes":    total_changes,
        "total_resolved":   total_resolved,
        "recovery_rate":    recovery_rate,
        "recent_7d":        recent,
    }


# ---------------------------------------------------------------------------
# Storage statistics
# ---------------------------------------------------------------------------


def get_storage_stats(session: Session) -> dict:
    """
    Aggregate storage metrics from FileRecord.file_size.
    Returns total, average, largest/smallest, and per-scanner breakdown.
    """
    agg = session.execute(
        select(
            func.count().label("cnt"),
            func.sum(FileRecord.file_size).label("total"),
            func.avg(cast(FileRecord.file_size, Float)).label("avg"),
            func.max(FileRecord.file_size).label("max_sz"),
            func.min(FileRecord.file_size).label("min_sz"),
        ).where(FileRecord.file_size.isnot(None))
        .where(FileRecord.file_size > 0)
    ).one()

    # Per-scanner storage breakdown
    scanner_rows = session.execute(
        select(
            FileRecord.scanner_id,
            func.count().label("cnt"),
            func.sum(FileRecord.file_size).label("total"),
            func.avg(cast(FileRecord.file_size, Float)).label("avg"),
        ).where(FileRecord.file_size.isnot(None))
        .where(FileRecord.file_size > 0)
        .group_by(FileRecord.scanner_id)
        .order_by(func.sum(FileRecord.file_size).desc())
    ).all()

    by_scanner = [
        {
            "scanner_id": r.scanner_id or "_unknown",
            "count":      r.cnt,
            "total_bytes": int(r.total or 0),
            "avg_bytes":   int(r.avg or 0),
        }
        for r in scanner_rows
    ]

    # Uploaded today
    today = _today_start()
    uploaded_today_bytes: int = session.execute(
        select(func.sum(EstimatedUploadQueue.file_size_bytes))
        .where(EstimatedUploadQueue.upload_status == "uploaded")
        .where(EstimatedUploadQueue.upload_completed_at >= today)
    ).scalar() or 0

    return {
        "total_slides_with_size": agg.cnt or 0,
        "total_bytes":            int(agg.total or 0),
        "avg_bytes":              int(agg.avg or 0),
        "max_bytes":              int(agg.max_sz or 0),
        "min_bytes":              int(agg.min_sz or 0),
        "by_scanner":             by_scanner,
        "uploaded_today_bytes":   uploaded_today_bytes,
    }


# ---------------------------------------------------------------------------
# Upload velocity
# ---------------------------------------------------------------------------


def get_upload_velocity(session: Session) -> dict:
    """
    Upload throughput and velocity metrics.
    Includes per-day counts for the last 7 days.
    """
    # Aggregates
    agg = session.execute(
        select(
            func.avg(cast(EstimatedUploadQueue.upload_speed_mbps, Float)).label("avg_speed"),
            func.count().label("total"),
            func.sum(
                case((EstimatedUploadQueue.upload_status == "uploaded", 1), else_=0)
            ).label("completed"),
            func.sum(
                case((EstimatedUploadQueue.upload_status == "failed", 1), else_=0)
            ).label("failed"),
            func.sum(EstimatedUploadQueue.retry_count).label("total_retries"),
        ).where(EstimatedUploadQueue.upload_speed_mbps.isnot(None))
    ).one()

    agg_all = session.execute(
        select(
            func.count().label("total"),
            func.sum(
                case((EstimatedUploadQueue.upload_status == "uploaded", 1), else_=0)
            ).label("completed"),
            func.sum(
                case((EstimatedUploadQueue.upload_status == "failed", 1), else_=0)
            ).label("failed"),
            func.sum(EstimatedUploadQueue.retry_count).label("total_retries"),
        )
    ).one()

    # Average upload duration (completed uploads with both start and end times)
    dur_agg = session.execute(
        select(
            func.avg(
                func.extract(
                    "epoch",
                    EstimatedUploadQueue.upload_completed_at - EstimatedUploadQueue.upload_started_at,
                )
            ).label("avg_dur")
        ).where(EstimatedUploadQueue.upload_status == "uploaded")
        .where(EstimatedUploadQueue.upload_started_at.isnot(None))
        .where(EstimatedUploadQueue.upload_completed_at.isnot(None))
    ).one()

    # Per-day completed uploads for last 7 days
    seven_days_ago = _days_ago(7)
    daily_rows = session.execute(
        select(
            func.date_trunc("day", EstimatedUploadQueue.upload_completed_at).label("day"),
            func.count().label("cnt"),
        ).where(EstimatedUploadQueue.upload_status == "uploaded")
        .where(EstimatedUploadQueue.upload_completed_at >= seven_days_ago)
        .group_by(func.date_trunc("day", EstimatedUploadQueue.upload_completed_at))
        .order_by(func.date_trunc("day", EstimatedUploadQueue.upload_completed_at))
    ).all()

    daily = [
        {
            "day":   r.day.isoformat() if r.day else None,
            "count": r.cnt,
        }
        for r in daily_rows
    ]

    # Queue depth
    queue_depth: int = session.execute(
        select(func.count()).select_from(EstimatedUploadQueue)
        .where(EstimatedUploadQueue.upload_status.in_(["queued", "estimating"]))
    ).scalar() or 0

    now = _now()
    delayed: int = sum(
        1 for r in session.execute(
            select(EstimatedUploadQueue.estimated_upload_at, EstimatedUploadQueue.upload_status)
            .where(EstimatedUploadQueue.upload_status.notin_(["uploaded", "failed"]))
            .where(EstimatedUploadQueue.estimated_upload_at.isnot(None))
        ).all()
        if r.estimated_upload_at and r.estimated_upload_at < now
    )

    return {
        "avg_speed_mbps":       round(float(agg.avg_speed), 2) if agg.avg_speed else None,
        "avg_duration_seconds": round(float(dur_agg.avg_dur), 1) if dur_agg.avg_dur else None,
        "total_in_queue":       agg_all.total or 0,
        "completed_total":      agg_all.completed or 0,
        "failed_total":         agg_all.failed or 0,
        "total_retries":        int(agg_all.total_retries or 0),
        "queue_depth":          queue_depth,
        "delayed_count":        delayed,
        "daily_uploads_7d":     daily,
    }
