"""
Read/write queries for the upload_tracking.estimated_upload_queue table.

Kept in a separate module from queries.py because upload tracking is a
distinct operational domain with its own ingestion and update patterns.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from .scanner_fleet import ScannerFleet

from pathoryx_enterprise.db.models.upload_tracking import EstimatedUploadQueue

# Statuses that represent "terminal" — no longer expected to upload
_TERMINAL_STATUSES = frozenset({"uploaded", "failed"})

# Statuses that represent active work (for active-uploads metric)
_ACTIVE_STATUSES = frozenset({"uploading"})

# Statuses that represent pending work (for queued metric)
_PENDING_STATUSES = frozenset({"queued", "estimating"})


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _is_delayed(row: EstimatedUploadQueue, now: datetime) -> bool:
    return (
        row.estimated_upload_at is not None
        and row.estimated_upload_at < now
        and row.upload_status not in _TERMINAL_STATUSES
    )


# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------


def list_upload_queue(
    session: Session,
    *,
    status: Optional[str] = None,
    scanner_id: Optional[str] = None,
    uploader_host: Optional[str] = None,
    search: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[int, list[dict]]:
    """
    Return (total, items) for the upload queue with optional filters.

    When status='delayed', the filter is computed from ETA vs now rather
    than the stored upload_status value, since rows are not automatically
    marked as delayed in the DB.
    """
    now = _now()
    q = select(EstimatedUploadQueue)

    if status == "delayed":
        q = q.where(
            EstimatedUploadQueue.estimated_upload_at < now,
            EstimatedUploadQueue.upload_status.notin_(_TERMINAL_STATUSES),
        )
    elif status:
        q = q.where(EstimatedUploadQueue.upload_status == status)

    if scanner_id:
        q = q.where(EstimatedUploadQueue.scanner_id == scanner_id)
    if uploader_host:
        q = q.where(EstimatedUploadQueue.uploader_host == uploader_host)
    if search:
        like = f"%{search}%"
        q = q.where(
            or_(
                EstimatedUploadQueue.filename.ilike(like),
                EstimatedUploadQueue.slide_id.ilike(like),
            )
        )
    if from_date:
        q = q.where(EstimatedUploadQueue.queued_at >= from_date)
    if to_date:
        q = q.where(EstimatedUploadQueue.queued_at <= to_date)

    total: int = session.execute(
        select(func.count()).select_from(q.subquery())
    ).scalar() or 0

    rows = session.execute(
        q.order_by(
            EstimatedUploadQueue.upload_status.in_(["uploading"]).desc(),
            EstimatedUploadQueue.priority.asc(),
            EstimatedUploadQueue.queued_at.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    items = [_row_to_dict(r, now) for r in rows]
    return total, items


def get_upload_metrics(session: Session) -> dict:
    """Compute operational summary metrics in a single pass."""
    now = _now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    all_rows = session.execute(
        select(
            EstimatedUploadQueue.upload_status,
            EstimatedUploadQueue.estimated_upload_at,
            EstimatedUploadQueue.upload_started_at,
            EstimatedUploadQueue.upload_completed_at,
            EstimatedUploadQueue.file_size_bytes,
            EstimatedUploadQueue.upload_speed_mbps,
        )
    ).all()

    queued = active = completed_today = failed = delayed = 0
    duration_secs: list[float] = []
    speeds: list[float] = []

    for row in all_rows:
        s = row.upload_status
        if s in _PENDING_STATUSES:
            queued += 1
        if s in _ACTIVE_STATUSES:
            active += 1
        if s == "uploaded":
            if row.upload_completed_at and row.upload_completed_at >= today_start:
                completed_today += 1
            if row.upload_started_at and row.upload_completed_at:
                dt = (row.upload_completed_at - row.upload_started_at).total_seconds()
                if dt > 0:
                    duration_secs.append(dt)
        if s == "failed":
            failed += 1
        if row.estimated_upload_at and row.estimated_upload_at < now and s not in _TERMINAL_STATUSES:
            delayed += 1
        if row.upload_speed_mbps and row.upload_speed_mbps > 0:
            speeds.append(row.upload_speed_mbps)

    return {
        "queued_count":          queued,
        "active_count":          active,
        "completed_today":       completed_today,
        "failed_count":          failed,
        "delayed_count":         delayed,
        "avg_duration_seconds":  (sum(duration_secs) / len(duration_secs)) if duration_secs else None,
        "avg_throughput_mbps":   (sum(speeds) / len(speeds)) if speeds else None,
    }


def get_upload_record(session: Session, record_id: int) -> Optional[dict]:
    row = session.execute(
        select(EstimatedUploadQueue).where(EstimatedUploadQueue.id == record_id)
    ).scalar_one_or_none()
    if row is None:
        return None
    return _row_to_dict(row, _now())


def list_upload_scanners(session: Session) -> list[str]:
    """Return distinct scanner IDs for the filter dropdown."""
    rows = session.execute(
        select(EstimatedUploadQueue.scanner_id)
        .where(EstimatedUploadQueue.scanner_id.isnot(None))
        .distinct()
        .order_by(EstimatedUploadQueue.scanner_id)
    ).scalars().all()
    return list(rows)


def list_upload_hosts(session: Session) -> list[str]:
    """Return distinct uploader hosts for the filter dropdown."""
    rows = session.execute(
        select(EstimatedUploadQueue.uploader_host)
        .where(EstimatedUploadQueue.uploader_host.isnot(None))
        .distinct()
        .order_by(EstimatedUploadQueue.uploader_host)
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Write queries
# ---------------------------------------------------------------------------


def upsert_upload_records(session: Session, records: list[dict]) -> tuple[int, int]:
    """
    Bulk upsert: insert or update records by (filename, queued_at).

    Only updates if the incoming last_updated_at is newer than the stored
    value, preventing stale data from overwriting fresher state.

    Returns (upserted_count, skipped_count).
    """
    now = _now()
    upserted = 0
    skipped = 0

    for rec in records:
        rec.setdefault("last_updated_at", now)
        stmt = (
            pg_insert(EstimatedUploadQueue)
            .values(**rec)
            .on_conflict_do_update(
                constraint="uq_euq_filename_queued_at",
                set_={
                    "upload_status":       pg_insert(EstimatedUploadQueue).excluded.upload_status,
                    "estimated_upload_at": pg_insert(EstimatedUploadQueue).excluded.estimated_upload_at,
                    "upload_started_at":   pg_insert(EstimatedUploadQueue).excluded.upload_started_at,
                    "upload_completed_at": pg_insert(EstimatedUploadQueue).excluded.upload_completed_at,
                    "upload_speed_mbps":   pg_insert(EstimatedUploadQueue).excluded.upload_speed_mbps,
                    "retry_count":         pg_insert(EstimatedUploadQueue).excluded.retry_count,
                    "failure_reason":      pg_insert(EstimatedUploadQueue).excluded.failure_reason,
                    "last_updated_at":     pg_insert(EstimatedUploadQueue).excluded.last_updated_at,
                },
                where=(
                    pg_insert(EstimatedUploadQueue).excluded.last_updated_at
                    > EstimatedUploadQueue.last_updated_at
                ),
            )
        )
        result = session.execute(stmt)
        if result.rowcount and result.rowcount > 0:
            upserted += 1
        else:
            skipped += 1

    session.flush()
    return upserted, skipped


def update_upload_record(session: Session, record_id: int, updates: dict) -> Optional[dict]:
    """
    Update a single record by id.

    Accepts only allowed mutable fields. Guards against stale writes via
    last_updated_at comparison when provided.
    """
    now = _now()
    allowed = {
        "upload_status", "estimated_upload_at", "upload_started_at",
        "upload_completed_at", "upload_speed_mbps", "failure_reason",
        "retry_count",
    }
    clean = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not clean:
        return get_upload_record(session, record_id)

    clean["last_updated_at"] = now
    session.execute(
        update(EstimatedUploadQueue)
        .where(EstimatedUploadQueue.id == record_id)
        .values(**clean)
    )
    session.flush()
    return get_upload_record(session, record_id)


# ---------------------------------------------------------------------------
# SSE checkpoint helpers
# ---------------------------------------------------------------------------


def query_upload_max_id(session: Session) -> int:
    val = session.execute(
        select(func.max(EstimatedUploadQueue.id))
    ).scalar()
    return int(val) if val is not None else 0


def query_upload_max_updated(session: Session) -> Optional[datetime]:
    val = session.execute(
        select(func.max(EstimatedUploadQueue.last_updated_at))
    ).scalar()
    return val if isinstance(val, datetime) else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: EstimatedUploadQueue, now: datetime) -> dict:
    return {
        "id":                  row.id,
        "slide_id":            row.slide_id,
        "filename":            row.filename,
        "scanner_id":          row.scanner_id,
        "uploader_host":       row.uploader_host,
        "queued_at":           row.queued_at,
        "estimated_upload_at": row.estimated_upload_at,
        "upload_started_at":   row.upload_started_at,
        "upload_completed_at": row.upload_completed_at,
        "upload_status":       row.upload_status,
        "retry_count":         row.retry_count,
        "file_size_bytes":     row.file_size_bytes,
        "priority":            row.priority,
        "upload_speed_mbps":   row.upload_speed_mbps,
        "failure_reason":      row.failure_reason,
        "last_updated_at":     row.last_updated_at,
        "is_delayed":          _is_delayed(row, now),
    }


# ---------------------------------------------------------------------------
# Scanner summary
# ---------------------------------------------------------------------------


def get_scanner_summary(session: Session, fleet: "ScannerFleet") -> list[dict]:
    """
    Return per-scanner upload queue metrics, enriched with fleet display names.

    Includes:
    - Scanners that appear in the DB (even if not in fleet config)
    - Enabled scanners from the fleet config with zero counts
      (so the UI can show configured scanners even before uploads arrive)

    Disabled scanners from the fleet config are only included when they have
    actual data in the queue.
    """
    now = _now()

    # ── Aggregate counts from DB ──────────────────────────────────────────────
    rows = session.execute(
        select(
            EstimatedUploadQueue.scanner_id,
            EstimatedUploadQueue.upload_status,
            EstimatedUploadQueue.estimated_upload_at,
        )
    ).all()

    counts: dict[str, dict] = {}
    for row in rows:
        sid = row.scanner_id or "_unknown"
        if sid not in counts:
            counts[sid] = {"queued": 0, "active": 0, "failed": 0, "delayed": 0, "total": 0}
        c = counts[sid]
        c["total"] += 1
        s = row.upload_status
        if s in _PENDING_STATUSES:
            c["queued"] += 1
        if s in _ACTIVE_STATUSES:
            c["active"] += 1
        if s == "failed":
            c["failed"] += 1
        if row.estimated_upload_at and row.estimated_upload_at < now and s not in _TERMINAL_STATUSES:
            c["delayed"] += 1

    # ── Seed enabled fleet scanners that have no DB data yet ─────────────────
    for entry in fleet.enabled():
        if entry.scanner_id not in counts:
            counts[entry.scanner_id] = {"queued": 0, "active": 0, "failed": 0, "delayed": 0, "total": 0}

    # ── Build result dicts ────────────────────────────────────────────────────
    result: list[dict] = []
    for sid, c in counts.items():
        entry = fleet.get(sid)
        # Include disabled fleet scanner only when it has actual data
        if entry is not None and not entry.enabled and c["total"] == 0:
            continue
        result.append({
            "scanner_id":   sid,
            "display_name": fleet.display_name(sid),
            "location":     entry.location  if entry else "",
            "vendor":       entry.vendor    if entry else "unknown",
            "enabled":      entry.enabled   if entry else True,
            **c,
        })

    return sorted(result, key=lambda x: x["display_name"].lower())
