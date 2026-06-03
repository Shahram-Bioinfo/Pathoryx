"""
Lightweight change-detection engine for the SSE stream.

Design:
  Each call to poll_changes() runs at most 7 inexpensive SQL queries against
  existing tables — all of the form SELECT MAX(pk) or COUNT(WHERE status IN ...).
  These queries use primary-key / timestamp indexes; no full table scans.

  The SseCheckpoints dataclass holds the last-observed value for each watched
  table.  On each poll, if any value has advanced, one SSE event dict is
  appended to the return list.  The caller formats and yields those dicts.

  No database schema changes, no triggers, no sequences, no new tables.

Watched tables and detection strategies
  core.service_trigger       MAX(internal_id) — new triggers
                             COUNT(status IN pending,running) — queue depth shifts
  events.pipeline_events     MAX(event_id)   — new events (append-only)
  core.file_records          MAX(internal_id) — new records
                             MAX(updated_at) — status changes on existing records
  failed_watcher.*           MAX(internal_id) — new recovery/technician events
  core.runner_registrations  MAX(last_heartbeat_at) — runner liveness changes
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.core import FileRecord, RunnerRegistration, ServiceTrigger
from pathoryx_enterprise.db.models.events import PipelineEvent
from pathoryx_enterprise.db.models.failed_watcher import TechnicianChange

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Checkpoint state
# ---------------------------------------------------------------------------

@dataclass
class SseCheckpoints:
    """
    Mutable snapshot of the last-observed marker for every watched table.

    Two markers for service_triggers:
      trigger_max_id       — tracks new rows (monotonically increasing PK)
      trigger_active_count — tracks in-flight work (pending + running count).
                             A change here means queue depth shifted even if no
                             new row was inserted (e.g. a pending→running transition).
    """
    trigger_max_id:       int            = 0
    trigger_active_count: int            = 0
    event_max_id:         int            = 0
    file_max_id:          int            = 0
    file_max_updated:     datetime | None = None
    recovery_max_id:      int            = 0
    runner_max_heartbeat: datetime | None = None
    # False until the first successful DB read completes.
    initialized:          bool           = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scalar_int(val: Any) -> int:
    """Coerce a MAX() scalar to int, defaulting to 0 for NULL."""
    return int(val) if val is not None else 0


def _scalar_dt(val: Any) -> datetime | None:
    """Return val only when it is a datetime; discard anything else."""
    return val if isinstance(val, datetime) else None


def _query_trigger_max_id(session: Session) -> int:
    return _scalar_int(
        session.execute(select(func.max(ServiceTrigger.internal_id))).scalar()
    )


def _query_trigger_active(session: Session) -> int:
    return _scalar_int(
        session.execute(
            select(func.count())
            .select_from(ServiceTrigger)
            .where(ServiceTrigger.trigger_status.in_(["pending", "running"]))
        ).scalar()
    )


def _query_event_max_id(session: Session) -> int:
    return _scalar_int(
        session.execute(select(func.max(PipelineEvent.event_id))).scalar()
    )


def _query_file_max_id(session: Session) -> int:
    return _scalar_int(
        session.execute(select(func.max(FileRecord.internal_id))).scalar()
    )


def _query_file_max_updated(session: Session) -> datetime | None:
    return _scalar_dt(
        session.execute(select(func.max(FileRecord.updated_at))).scalar()
    )


def _query_recovery_max_id(session: Session) -> int:
    return _scalar_int(
        session.execute(select(func.max(TechnicianChange.internal_id))).scalar()
    )


def _query_runner_max_heartbeat(session: Session) -> datetime | None:
    return _scalar_dt(
        session.execute(
            select(func.max(RunnerRegistration.last_heartbeat_at))
        ).scalar()
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_checkpoints(session: Session) -> SseCheckpoints:
    """
    Read the current state of all watched tables and return a populated
    SseCheckpoints.  No events are emitted — this just establishes the
    baseline so the first real poll() call has something to compare against.

    Each query is wrapped independently; a failure on one table does not
    prevent the remaining tables from being initialised.
    """
    cp = SseCheckpoints()

    try:
        cp.trigger_max_id = _query_trigger_max_id(session)
        cp.trigger_active_count = _query_trigger_active(session)
    except Exception as exc:
        logger.debug("SSE init: trigger query failed: %s", exc)

    try:
        cp.event_max_id = _query_event_max_id(session)
    except Exception as exc:
        logger.debug("SSE init: event query failed: %s", exc)

    try:
        cp.file_max_id = _query_file_max_id(session)
        cp.file_max_updated = _query_file_max_updated(session)
    except Exception as exc:
        logger.debug("SSE init: file_record query failed: %s", exc)

    try:
        cp.recovery_max_id = _query_recovery_max_id(session)
    except Exception as exc:
        logger.debug("SSE init: recovery query failed: %s", exc)

    try:
        cp.runner_max_heartbeat = _query_runner_max_heartbeat(session)
    except Exception as exc:
        logger.debug("SSE init: runner query failed: %s", exc)

    cp.initialized = True
    return cp


def poll_changes(session: Session, cp: SseCheckpoints) -> list[dict]:
    """
    Compare the current DB state to cp.  Return a list of SSE event dicts
    for each domain that changed.  Mutates cp in-place to reflect the new
    state so the next call has an accurate baseline.

    If cp.initialized is False (startup race), this call silently initialises
    without emitting events and sets cp.initialized = True.

    Each table group is wrapped in its own try/except so a transient DB error
    on one group does not suppress events from the others.
    """
    if not cp.initialized:
        _silent_init(session, cp)
        return []

    events: list[dict] = []
    now_ts = datetime.now(tz=timezone.utc).isoformat()

    # ── Service triggers (new rows + active-count shift) ─────────────────────
    try:
        new_max = _query_trigger_max_id(session)
        new_active = _query_trigger_active(session)
        if new_max != cp.trigger_max_id or new_active != cp.trigger_active_count:
            events.append({"type": "queue_updated", "ts": now_ts})
        cp.trigger_max_id = new_max
        cp.trigger_active_count = new_active
    except Exception as exc:
        logger.debug("SSE poll: trigger query failed: %s", exc)

    # ── Pipeline events (append-only; max(event_id) only ever increases) ─────
    try:
        new_eid = _query_event_max_id(session)
        if new_eid > cp.event_max_id:
            events.append({"type": "pipeline_event_created", "ts": now_ts})
        cp.event_max_id = new_eid
    except Exception as exc:
        logger.debug("SSE poll: event query failed: %s", exc)

    # ── File records (new rows via max_id; status changes via max updated_at) ─
    try:
        new_fid = _query_file_max_id(session)
        new_fup = _query_file_max_updated(session)
        id_changed = new_fid != cp.file_max_id
        ts_advanced = (
            new_fup is not None
            and cp.file_max_updated is not None
            and new_fup > cp.file_max_updated
        )
        if id_changed or ts_advanced:
            events.append({"type": "file_record_updated", "ts": now_ts})
        cp.file_max_id = new_fid
        if new_fup is not None:
            cp.file_max_updated = new_fup
    except Exception as exc:
        logger.debug("SSE poll: file_record query failed: %s", exc)

    # ── Recovery / technician changes (append-only) ──────────────────────────
    try:
        new_rid = _query_recovery_max_id(session)
        if new_rid > cp.recovery_max_id:
            events.append({"type": "recovery_event_created", "ts": now_ts})
        cp.recovery_max_id = new_rid
    except Exception as exc:
        logger.debug("SSE poll: recovery query failed: %s", exc)

    # ── Runner heartbeats (last_heartbeat_at advances on each heartbeat) ──────
    try:
        new_hb = _query_runner_max_heartbeat(session)
        if (
            new_hb is not None
            and cp.runner_max_heartbeat is not None
            and new_hb > cp.runner_max_heartbeat
        ):
            events.append({"type": "service_health_updated", "ts": now_ts})
        if new_hb is not None:
            cp.runner_max_heartbeat = new_hb
    except Exception as exc:
        logger.debug("SSE poll: runner query failed: %s", exc)

    return events


def _silent_init(session: Session, cp: SseCheckpoints) -> None:
    """Populate cp without emitting events. Used as a fallback path."""
    try:
        cp.trigger_max_id = _query_trigger_max_id(session)
        cp.trigger_active_count = _query_trigger_active(session)
        cp.event_max_id = _query_event_max_id(session)
        cp.file_max_id = _query_file_max_id(session)
        cp.file_max_updated = _query_file_max_updated(session)
        cp.recovery_max_id = _query_recovery_max_id(session)
        cp.runner_max_heartbeat = _query_runner_max_heartbeat(session)
    except Exception as exc:
        logger.warning("SSE: silent init failed: %s", exc)
    cp.initialized = True
