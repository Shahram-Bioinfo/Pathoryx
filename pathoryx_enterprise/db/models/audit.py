"""
Operational audit models: EventLog and ErrorLog.

These are the mutable operational log tables (as distinct from the immutable
event-sourced pipeline_events table). They are used for:
  - Human-readable operational events (startup, shutdown, config changes)
  - Detailed error records with stack traces
  - Sensitive operation audit trail (schema bootstraps, runner registrations)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base, ImmutableTimestampMixin


class EventLog(Base, ImmutableTimestampMixin):
    """
    Append-only operational event log.
    Used for human-readable operational events and auditing.
    """

    __tablename__ = "event_logs"
    __table_args__ = (
        Index("ix_event_logs_service_time", "service_name", "event_timestamp"),
        Index("ix_event_logs_artifact", "global_artifact_id"),
        Index("ix_event_logs_correlation", "correlation_id"),
        {"schema": "ops"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    service_name: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)

    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
    )
    step_run_internal_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    global_run_id: Mapped[Optional[str]] = mapped_column(Text)
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)

    runner_id: Mapped[Optional[str]] = mapped_column(Text)
    host_id: Mapped[Optional[str]] = mapped_column(Text)

    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_json: Mapped[Optional[dict]] = mapped_column(JSONB)


class ErrorLog(Base, ImmutableTimestampMixin):
    """
    Append-only error log with full exception context.
    """

    __tablename__ = "error_logs"
    __table_args__ = (
        Index("ix_error_logs_service_type", "service_name", "error_type"),
        Index("ix_error_logs_artifact", "global_artifact_id"),
        Index("ix_error_logs_status", "resolved"),
        {"schema": "ops"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    service_name: Mapped[str] = mapped_column(Text, nullable=False)
    error_type: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    stack_trace: Mapped[Optional[str]] = mapped_column(Text)

    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)

    runner_id: Mapped[Optional[str]] = mapped_column(Text)
    host_id: Mapped[Optional[str]] = mapped_column(Text)

    context_json: Mapped[Optional[dict]] = mapped_column(JSONB)

    resolved: Mapped[bool] = mapped_column(default=False, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)
