"""
Live wallboard query module — operational day statistics with 07:00 reset.

The operational day for the laboratory runs 07:00 → 06:59 (Europe/Copenhagen).
All "today" statistics use this window, not calendar midnight.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from .scanner_fleet import ScannerFleet

from pathoryx_enterprise.db.models.babelshark import ExtractionResult
from pathoryx_enterprise.db.models.core import (
    FileRecord,
    RunnerRegistration,
    ServiceTrigger,
)
from pathoryx_enterprise.db.models.failed_watcher import WatchedFolderSnapshot
from pathoryx_enterprise.db.models.upload_tracking import EstimatedUploadQueue

# ── Constants ─────────────────────────────────────────────────────────────────

OPERATIONAL_TIMEZONE = "Europe/Copenhagen"
OPERATIONAL_HOUR = 7  # 07:00 local = start of working day

# Roles mapped from canonical fleet scanner IDs
SCANNER_ROLES: dict[str, dict[str, str]] = {
    "M40010":   {"role": "Clinical / Sectra",  "role_color": "teal"},
    "M40015":   {"role": "Clinical / Sectra",  "role_color": "teal"},
    "M40023":   {"role": "Research",           "role_color": "purple"},
    "M40024":   {"role": "Hybrid",             "role_color": "amber"},
    "SS12620R": {"role": "Z-Stack / Advanced", "role_color": "magenta"},
}

# Alert thresholds
ALERT_QUEUE_HIGH     = 20
ALERT_BACKLOG_HIGH   = 5
ALERT_FAILED_HIGH    = 3
RUNNER_STALE_SECONDS = 120  # no heartbeat in 2 min → service offline

# Top N stains displayed individually; remainder grouped into "Other"
STAIN_TOP_N = 7

# Scanners considered "recently active" if last_activity within this window
SCANNER_ACTIVE_WINDOW_HOURS = 1


# ── Operational day helpers ───────────────────────────────────────────────────


def get_operational_day_start(
    tz_name: str = OPERATIONAL_TIMEZONE,
    now: Optional[datetime] = None,
) -> datetime:
    """
    Return the start of the current operational day as a UTC datetime.

    Operational day starts at OPERATIONAL_HOUR (07:00) local time.
    If the current local time is before 07:00, the day started yesterday at 07:00.

    Args:
        tz_name: IANA timezone name for the laboratory.
        now: Override for the current time (used in tests). Default: datetime.now().
    """
    tz = ZoneInfo(tz_name)
    now_local: datetime
    if now is None:
        now_local = datetime.now(tz=tz)
    else:
        now_local = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)

    day_start = now_local.replace(hour=OPERATIONAL_HOUR, minute=0, second=0, microsecond=0)
    if now_local < day_start:
        # We're before 07:00 — still in the previous operational day
        day_start -= timedelta(days=1)
    return day_start.astimezone(timezone.utc)


def get_operational_day_window(
    tz_name: str = OPERATIONAL_TIMEZONE,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    """Return (op_day_start_utc, op_day_end_utc) — a 24-hour window."""
    start = get_operational_day_start(tz_name, now=now)
    return start, start + timedelta(hours=24)


# ── Main wallboard payload ────────────────────────────────────────────────────


def get_wallboard_data(
    session: Session,
    fleet: "ScannerFleet",
    babelshark_cfg: dict,
) -> dict:
    """
    Build the complete wallboard payload in a single function call.

    All counts use the 07:00 operational day window. Returns a dict that maps
    cleanly to WallboardResponse. Never raises — individual sub-queries degrade
    gracefully and return zeros.
    """
    now = datetime.now(tz=timezone.utc)
    op_start, op_end = get_operational_day_window()

    kpis          = _get_kpis(session, op_start, now=now)
    scanners      = _get_scanner_data(session, fleet, op_start, now, babelshark_cfg)
    uploads_hour  = _get_uploads_by_hour(session, op_start, now)
    by_scanner    = _get_uploaded_by_scanner(session, op_start)
    stains        = _get_stain_distribution(session, op_start)
    pipeline      = _get_pipeline_stages(session, op_start)
    active_mode   = _get_active_mode(babelshark_cfg)
    next_mode     = _get_next_mode_switch(babelshark_cfg)
    alerts        = _build_alerts(kpis, scanners, session, now)
    system_status = "degraded" if any(a["level"] == "critical" for a in alerts) else "nominal"

    peak = max(uploads_hour, key=lambda h: h["count"], default=None)
    peak_upload_hour = peak["hour_label"] if peak and peak["count"] > 0 else None

    return {
        "as_of":                  now.isoformat(),
        "operational_day_start":  op_start.isoformat(),
        "operational_day_end":    op_end.isoformat(),
        "active_mode":            active_mode,
        "system_status":          system_status,
        "kpis":                   kpis,
        "scanners":               scanners,
        "uploads_by_hour":        uploads_hour,
        "uploaded_by_scanner":    by_scanner,
        "stain_distribution":     stains,
        "pipeline":               pipeline,
        "alerts":                 alerts,
        "next_mode_switch_at":    next_mode.get("switch_at"),
        "next_mode_name":         next_mode.get("mode_name"),
        "peak_upload_hour":       peak_upload_hour,
    }


# ── Sub-queries ───────────────────────────────────────────────────────────────


def _get_kpis(session: Session, op_start: datetime, now: Optional[datetime] = None) -> dict:
    if now is None:
        now = datetime.now(tz=timezone.utc)

    try:
        uploaded_today: int = session.execute(
            select(func.count()).select_from(EstimatedUploadQueue)
            .where(EstimatedUploadQueue.upload_status == "uploaded")
            .where(EstimatedUploadQueue.upload_completed_at >= op_start)
        ).scalar() or 0
    except Exception:
        uploaded_today = 0

    try:
        slides_scanned: int = session.execute(
            select(func.count()).select_from(FileRecord)
            .where(FileRecord.created_at >= op_start)
        ).scalar() or 0
    except Exception:
        slides_scanned = 0

    try:
        queue_depth: int = session.execute(
            select(func.count()).select_from(ServiceTrigger)
            .where(ServiceTrigger.trigger_status == "pending")
        ).scalar() or 0
    except Exception:
        queue_depth = 0

    try:
        active_processing: int = session.execute(
            select(func.count()).select_from(ServiceTrigger)
            .where(ServiceTrigger.trigger_status == "running")
        ).scalar() or 0
    except Exception:
        active_processing = 0

    try:
        failed: int = session.execute(
            select(func.count()).select_from(FileRecord)
            .where(FileRecord.status.like("%failed%"))
            .where(FileRecord.updated_at >= op_start)
        ).scalar() or 0
    except Exception:
        failed = 0

    try:
        recovery_backlog: int = session.execute(
            select(func.count()).select_from(WatchedFolderSnapshot)
        ).scalar() or 0
    except Exception:
        recovery_backlog = 0

    hours_elapsed = max(0.25, (now - op_start).total_seconds() / 3600)
    avg_slides_per_hour = round(slides_scanned / hours_elapsed, 1) if slides_scanned > 0 else 0.0

    return {
        "uploaded_today":       uploaded_today,
        "slides_scanned_today": slides_scanned,
        "queue_depth":          queue_depth,
        "active_processing":    active_processing,
        "failed":               failed,
        "recovery_backlog":     recovery_backlog,
        "avg_slides_per_hour":  avg_slides_per_hour,
    }


def _get_scanner_data(
    session: Session,
    fleet: "ScannerFleet",
    op_start: datetime,
    now: datetime,
    babelshark_cfg: dict,
) -> list[dict]:
    # File records today per scanner
    slides_by: dict[str, dict] = {}
    try:
        rows = session.execute(
            select(
                FileRecord.scanner_id,
                func.count().label("slides_today"),
                func.max(FileRecord.updated_at).label("last_activity"),
            )
            .where(FileRecord.created_at >= op_start)
            .group_by(FileRecord.scanner_id)
        ).all()
        for r in rows:
            sid = r.scanner_id or "_unknown"
            la = r.last_activity
            if la and la.tzinfo is None:
                la = la.replace(tzinfo=timezone.utc)
            slides_by[sid] = {"slides_today": r.slides_today, "last_activity": la}
    except Exception:
        pass

    # Uploads today per scanner
    uploads_by: dict[str, int] = {}
    try:
        rows2 = session.execute(
            select(
                EstimatedUploadQueue.scanner_id,
                func.count().label("cnt"),
            )
            .where(EstimatedUploadQueue.upload_status == "uploaded")
            .where(EstimatedUploadQueue.upload_completed_at >= op_start)
            .group_by(EstimatedUploadQueue.scanner_id)
        ).all()
        uploads_by = {(r.scanner_id or "_unknown"): r.cnt for r in rows2}
    except Exception:
        pass

    # Destination hints from routing policy (pick first mode's scanner routes)
    dest_by: dict[str, str] = {}
    try:
        for mode_cfg in (babelshark_cfg.get("routing_policies") or {}).get("modes", {}).values():
            for sid, sd in (mode_cfg.get("scanner_destinations") or {}).items():
                if sid not in dest_by:
                    dest_by[sid] = sd.get("destination", "")
    except Exception:
        pass

    active_cutoff = now - timedelta(hours=SCANNER_ACTIVE_WINDOW_HOURS)
    result = []

    for entry in (fleet.enabled() if fleet else []):
        sid = entry.scanner_id
        info = slides_by.get(sid, {})
        la: Optional[datetime] = info.get("last_activity")

        if la and la >= active_cutoff:
            state = "active"
        elif la:
            state = "idle"
        else:
            state = "offline"

        role_info = SCANNER_ROLES.get(sid, {"role": "Unknown", "role_color": "slate"})
        result.append({
            "scanner_id":        sid,
            "display_name":      entry.display_name,
            "role":              role_info["role"],
            "role_color":        role_info["role_color"],
            "operational_state": state,
            "slides_today":      info.get("slides_today", 0),
            "uploaded_today":    uploads_by.get(sid, 0),
            "last_activity":     la.isoformat() if la else None,
            "destination":       dest_by.get(sid) or "—",
        })
    return result


def _get_uploads_by_hour(
    session: Session,
    op_start: datetime,
    now: datetime,
) -> list[dict]:
    """Returns upload counts per hour since op_start (hour 0 = 07:00)."""
    counts: dict[int, int] = {}
    try:
        rows = session.execute(
            text("""
                SELECT
                  FLOOR(EXTRACT(EPOCH FROM (
                    DATE_TRUNC('hour', upload_completed_at) - :op_start
                  )) / 3600)::int AS hour_offset,
                  COUNT(*) AS cnt
                FROM upload_tracking.estimated_upload_queue
                WHERE upload_status = 'uploaded'
                  AND upload_completed_at >= :op_start
                  AND upload_completed_at < :op_end
                GROUP BY 1
                ORDER BY 1
            """),
            {"op_start": op_start, "op_end": op_start + timedelta(hours=24)},
        ).all()
        counts = {int(r[0]): int(r[1]) for r in rows if r[0] is not None and 0 <= int(r[0]) < 24}
    except Exception:
        pass

    # Build list from hour 0 up to current elapsed hours (max 24)
    hours_elapsed = min(24, max(1, int((now - op_start).total_seconds() / 3600) + 1))
    result = []
    for i in range(hours_elapsed):
        local_hour = (OPERATIONAL_HOUR + i) % 24
        result.append({
            "hour":       i,
            "hour_label": f"{local_hour:02d}:00",
            "count":      counts.get(i, 0),
        })
    return result


def _get_uploaded_by_scanner(session: Session, op_start: datetime) -> list[dict]:
    try:
        rows = session.execute(
            select(
                EstimatedUploadQueue.scanner_id,
                func.count().label("count"),
            )
            .where(EstimatedUploadQueue.upload_status == "uploaded")
            .where(EstimatedUploadQueue.upload_completed_at >= op_start)
            .group_by(EstimatedUploadQueue.scanner_id)
            .order_by(func.count().desc())
        ).all()
        return [{"scanner_id": r.scanner_id or "Unknown", "count": r.count} for r in rows]
    except Exception:
        return []


def _get_stain_distribution(session: Session, op_start: datetime) -> list[dict]:
    """
    Stain counts since op_start. Top STAIN_TOP_N shown individually;
    remainder collected into an "Other" bucket.
    """
    try:
        rows = session.execute(
            select(
                ExtractionResult.stain_type,
                func.count().label("cnt"),
            )
            .where(ExtractionResult.created_at >= op_start)
            .group_by(ExtractionResult.stain_type)
            .order_by(func.count().desc())
        ).all()
    except Exception:
        return []

    total = sum(r.cnt for r in rows)
    if not total:
        return []

    result = []
    other_count = 0
    for i, row in enumerate(rows):
        label = (row.stain_type or "Unknown").strip().upper()
        if label in ("", "NONE", "NULL"):
            label = "Unknown"
        if i < STAIN_TOP_N:
            result.append({
                "stain":      label,
                "count":      row.cnt,
                "percentage": round(row.cnt / total * 100, 1),
            })
        else:
            other_count += row.cnt

    if other_count > 0:
        result.append({
            "stain":      "Other",
            "count":      other_count,
            "percentage": round(other_count / total * 100, 1),
        })
    return result


def _get_pipeline_stages(session: Session, op_start: datetime) -> list[dict]:
    # All-time status counts (for "active" — currently in-flight)
    all_statuses: dict[str, int] = {}
    try:
        rows = session.execute(
            select(FileRecord.status, func.count().label("cnt"))
            .group_by(FileRecord.status)
        ).all()
        all_statuses = {(r.status or "unknown"): r.cnt for r in rows}
    except Exception:
        pass

    # Today's status counts (for "today passed through stage")
    today_statuses: dict[str, int] = {}
    try:
        rows2 = session.execute(
            select(FileRecord.status, func.count().label("cnt"))
            .where(FileRecord.created_at >= op_start)
            .group_by(FileRecord.status)
        ).all()
        today_statuses = {(r.status or "unknown"): r.cnt for r in rows2}
    except Exception:
        pass

    def _all(*statuses: str) -> int:
        return sum(all_statuses.get(s, 0) for s in statuses)

    def _today(*statuses: str) -> int:
        return sum(today_statuses.get(s, 0) for s in statuses)

    return [
        {
            "name":   "scanner",
            "label":  "Scanner",
            "active": 0,
            "today":  _today("detected", "intake_running"),
            "failed": 0,
        },
        {
            "name":   "babelshark",
            "label":  "BabelShark",
            "active": _all("intake_running"),
            "today":  _today("intake_registered", "qc_pending"),
            "failed": _all("intake_failed"),
        },
        {
            "name":   "qc",
            "label":  "QC",
            "active": _all("qc_running", "qc_pending"),
            "today":  _today("qc_passed", "qc_failed"),
            "failed": _all("qc_failed"),
        },
        {
            "name":   "dicom",
            "label":  "DICOM",
            "active": _all("dicom_running", "dicom_pending"),
            "today":  _today("dicom_done", "dicom_failed"),
            "failed": _all("dicom_failed"),
        },
        {
            "name":   "upload",
            "label":  "Upload",
            "active": _all("upload_running", "upload_pending"),
            "today":  _today("uploaded"),
            "failed": _all("upload_failed"),
        },
    ]


def _get_active_mode(babelshark_cfg: dict) -> Optional[str]:
    try:
        policies = (babelshark_cfg or {}).get("routing_policies")
        if not policies:
            return None
        from pathoryx_enterprise.services.routing.engine import RoutingPolicyEngine
        engine = RoutingPolicyEngine(policies)
        mode = engine.get_active_mode()
        return mode.name if mode else policies.get("default_mode")
    except Exception:
        return None


def _get_next_mode_switch(babelshark_cfg: dict) -> dict:
    """Return {"switch_at": ISO-UTC, "mode_name": str} for the upcoming mode transition, or {}."""
    try:
        policies = (babelshark_cfg or {}).get("routing_policies")
        if not policies:
            return {}
        from pathoryx_enterprise.services.routing.engine import RoutingPolicyEngine
        engine = RoutingPolicyEngine(policies)
        mode = engine.get_active_mode()
        if not mode:
            return {}

        tz_name = policies.get("timezone", OPERATIONAL_TIMEZONE)
        tz = ZoneInfo(tz_name)
        now_local = datetime.now(tz=tz)

        # When does the active mode end?
        end_dt = now_local.replace(
            hour=mode.end.hour, minute=mode.end.minute, second=0, microsecond=0,
        )
        if end_dt <= now_local:
            end_dt += timedelta(days=1)

        # What mode comes next?
        probe = end_dt + timedelta(minutes=1)
        next_mode = engine.get_active_mode(now=probe)
        next_name = next_mode.name if next_mode else policies.get("default_mode", "unknown")

        return {
            "switch_at": end_dt.astimezone(timezone.utc).isoformat(),
            "mode_name": next_name,
        }
    except Exception:
        return {}


def _build_alerts(
    kpis: dict,
    scanners: list[dict],
    session: Session,
    now: datetime,
) -> list[dict]:
    alerts: list[dict] = []

    # Stale service runners (no heartbeat)
    try:
        stale_cutoff = now - timedelta(seconds=RUNNER_STALE_SECONDS)
        stale = session.execute(
            select(RunnerRegistration.service_name)
            .where(RunnerRegistration.status == "active")
            .where(RunnerRegistration.last_heartbeat_at < stale_cutoff)
        ).scalars().all()
        for svc in stale:
            alerts.append({"level": "critical", "message": f"{svc}: no heartbeat — service may be offline"})
    except Exception:
        pass

    if kpis.get("failed", 0) >= ALERT_FAILED_HIGH:
        alerts.append({"level": "warning", "message": f"{kpis['failed']} slides failed in this operational day"})

    if kpis.get("queue_depth", 0) >= ALERT_QUEUE_HIGH:
        alerts.append({"level": "warning", "message": f"Queue depth elevated: {kpis['queue_depth']} pending triggers"})

    if kpis.get("recovery_backlog", 0) >= ALERT_BACKLOG_HIGH:
        alerts.append({"level": "warning", "message": f"Recovery backlog: {kpis['recovery_backlog']} files awaiting review"})

    return alerts
