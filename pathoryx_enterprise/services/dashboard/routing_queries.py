"""
Dashboard read/write queries for the routing policy engine — Phase 4.8.

All public functions accept a SQLAlchemy Session and return plain dicts
or lists (no ORM objects) so callers don't need to import model classes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── Overrides ────────────────────────────────────────────────────────────────

def list_active_overrides(db: Session) -> list[dict]:
    """All overrides that are still active and not expired."""
    sql = text("""
        SELECT id, created_at, created_by, reason,
               target_type, target_value, destination,
               expires_at, is_active
          FROM routing.routing_overrides
         WHERE is_active = TRUE
           AND (expires_at IS NULL OR expires_at > NOW())
         ORDER BY created_at DESC
    """)
    rows = db.execute(sql).mappings().all()
    return [dict(r) for r in rows]


def list_all_overrides(db: Session, limit: int = 100) -> list[dict]:
    sql = text("""
        SELECT id, created_at, created_by, reason,
               target_type, target_value, destination,
               expires_at, is_active
          FROM routing.routing_overrides
         ORDER BY created_at DESC
         LIMIT :limit
    """)
    rows = db.execute(sql, {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]


def create_override(
    db: Session,
    *,
    target_type: str,
    target_value: str,
    destination: str,
    expires_at: Optional[datetime],
    created_by: Optional[str],
    reason: Optional[str],
) -> dict:
    sql = text("""
        INSERT INTO routing.routing_overrides
               (target_type, target_value, destination, expires_at, created_by, reason, is_active)
        VALUES (:target_type, :target_value, :destination, :expires_at, :created_by, :reason, TRUE)
        RETURNING id, created_at, target_type, target_value, destination, expires_at, created_by, reason, is_active
    """)
    row = db.execute(sql, {
        "target_type": target_type,
        "target_value": target_value,
        "destination": destination,
        "expires_at": expires_at,
        "created_by": created_by,
        "reason": reason,
    }).mappings().one()
    db.commit()
    return dict(row)


def deactivate_override(db: Session, override_id: int) -> bool:
    """Deactivate a single override. Returns True if a row was updated."""
    sql = text("""
        UPDATE routing.routing_overrides
           SET is_active = FALSE, updated_at = NOW()
         WHERE id = :id AND is_active = TRUE
        RETURNING id
    """)
    result = db.execute(sql, {"id": override_id}).fetchone()
    db.commit()
    return result is not None


def expire_stale_overrides(db: Session) -> int:
    """Mark expired overrides as inactive. Returns count of rows updated."""
    sql = text("""
        UPDATE routing.routing_overrides
           SET is_active = FALSE, updated_at = NOW()
         WHERE is_active = TRUE AND expires_at IS NOT NULL AND expires_at <= NOW()
        RETURNING id
    """)
    rows = db.execute(sql).fetchall()
    db.commit()
    return len(rows)


# ── Decisions (audit trail) ──────────────────────────────────────────────────

def record_decision(
    db: Session,
    *,
    slide_id: Optional[str],
    scanner_id: Optional[str],
    mode: Optional[str],
    profile: Optional[str],
    color_dot: Optional[str],
    color_dot_confidence: Optional[float] = None,
    destination: str,
    routing_reason: str,
    override_id: Optional[int],
    dry_run: bool = True,
) -> int:
    """Append a routing decision to the audit trail. Returns new row id."""
    sql = text("""
        INSERT INTO routing.routing_decisions
               (slide_id, scanner_id, mode, profile, color_dot, color_dot_confidence,
                destination, routing_reason, override_id, dry_run)
        VALUES (:slide_id, :scanner_id, :mode, :profile, :color_dot, :color_dot_confidence,
                :destination, :routing_reason, :override_id, :dry_run)
        RETURNING id
    """)
    row = db.execute(sql, {
        "slide_id": slide_id,
        "scanner_id": scanner_id,
        "mode": mode,
        "profile": profile,
        "color_dot": color_dot,
        "color_dot_confidence": color_dot_confidence,
        "destination": destination,
        "routing_reason": routing_reason,
        "override_id": override_id,
        "dry_run": dry_run,
    }).fetchone()
    db.commit()
    return row[0]


def list_recent_decisions(db: Session, limit: int = 100) -> list[dict]:
    sql = text("""
        SELECT id, created_at, slide_id, scanner_id, mode, profile,
               color_dot, color_dot_confidence, destination, routing_reason, override_id, dry_run
          FROM routing.routing_decisions
         ORDER BY created_at DESC
         LIMIT :limit
    """)
    rows = db.execute(sql, {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]


# ── Preview ──────────────────────────────────────────────────────────────────

def get_pending_slides_for_preview(db: Session, limit: int = 100) -> list[dict]:
    """
    Return slides in qc_pending / dicom_pending / upload_pending states
    for routing preview.  Joins scanner_id from the slide record.
    """
    sql = text("""
        SELECT fr.internal_id,
               fr.global_artifact_id,
               fr.original_filename,
               fr.current_filename,
               fr.status,
               fr.scanner_id,
               fr.scanner_name,
               brd.color_dot_detected,
               brd.routing_type     AS current_routing_type
          FROM core.file_records fr
          LEFT JOIN LATERAL (
              SELECT color_dot_detected, routing_type
                FROM babelshark.slide_routing_decisions srd
               WHERE srd.file_record_internal_id = fr.internal_id
               ORDER BY srd.id DESC
               LIMIT 1
          ) brd ON TRUE
         WHERE fr.status IN ('qc_pending','qc_running','dicom_pending','upload_pending','upload_running')
         ORDER BY fr.created_at DESC
         LIMIT :limit
    """)
    try:
        rows = db.execute(sql, {"limit": limit}).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("routing preview query failed: %s", exc)
        return []


def get_decision_by_id(db: Session, decision_id: int) -> Optional[dict]:
    """Fetch a single routing decision row by id."""
    sql = text("""
        SELECT id, created_at, slide_id, scanner_id, mode, profile,
               color_dot, color_dot_confidence, destination, routing_reason, override_id, dry_run
          FROM routing.routing_decisions
         WHERE id = :id
    """)
    row = db.execute(sql, {"id": decision_id}).mappings().first()
    return dict(row) if row else None


# ── Decision stats ───────────────────────────────────────────────────────────

def get_decision_stats(db: Session) -> dict:
    """Aggregate stats for the routing dashboard panel."""
    sql = text("""
        SELECT
            COUNT(*)                                           AS total,
            COUNT(*) FILTER (WHERE dry_run = TRUE)            AS dry_run_count,
            COUNT(*) FILTER (WHERE override_id IS NOT NULL)   AS override_count,
            COUNT(DISTINCT scanner_id)                        AS unique_scanners,
            COUNT(DISTINCT destination)                       AS unique_destinations,
            COUNT(DISTINCT mode)                              AS unique_modes,
            MAX(created_at)                                   AS last_decision_at
          FROM routing.routing_decisions
    """)
    try:
        row = db.execute(sql).mappings().one()
        return dict(row)
    except Exception as exc:
        logger.warning("routing stats query failed: %s", exc)
        return {
            "total": 0, "dry_run_count": 0, "override_count": 0,
            "unique_scanners": 0, "unique_destinations": 0,
            "unique_modes": 0, "last_decision_at": None,
        }
