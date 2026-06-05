"""
Event sourcing: immutable append-only pipeline event store.

DESIGN PRINCIPLES:
  1. Rows in pipeline_events are NEVER updated or deleted.
  2. Every state transition in the pipeline is recorded as an event.
  3. Events carry enough information to replay the full pipeline history.
  4. event_version is monotonically increasing per aggregate_id.
  5. schema_version allows future payload format changes without breaking readers.
  6. caused_by_event_id forms a causation chain (event A caused event B).

STANDARD EVENT TYPES (extend as needed):
  file.detected              — new file seen by watcher
  intake.started             — runner began classifying file
  intake.registered          — file accepted as new/rescan
  intake.duplicate_skipped   — file identified as duplicate
  qc.trigger_created         — QC trigger written to service_trigger
  qc.started                 — QC runner claimed trigger
  qc.passed                  — QC decision: accepted
  qc.failed                  — QC decision: rejected
  qc.error                   — QC inference raised an exception
  dicom.conversion_started
  dicom.conversion_completed
  dicom.conversion_failed
  upload.started
  upload.completed
  upload.failed
  upload.retry
  recovery_sentry.change_detected
  recovery_sentry.auto_recovered
  recovery_sentry.manual_review_required
  recovery_sentry.qc_requeued
  recovery_sentry.timestamp_extracted
  recovery_sentry.timestamp_added
  dashboard.review_state_updated
  runner.started
  runner.shutdown
  runner.heartbeat
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base, ImmutableTimestampMixin


class PipelineEvent(Base, ImmutableTimestampMixin):
    """
    Immutable event log. APPEND ONLY.

    Rules enforced via migration:
      - No DELETE permission granted to the application user on this table.
      - No UPDATE permission granted to the application user on this table.
      - A DB trigger (optional, belt-and-suspenders) can reject any UPDATE/DELETE.

    Partitioning note:
      This table is partition-ready by RANGE on created_at (monthly partitions).
      Add partitioning in the migration when volume warrants it (>50M rows/year).
    """

    __tablename__ = "pipeline_events"
    __table_args__ = (
        # Idempotency: same event cannot be inserted twice
        UniqueConstraint("idempotency_key", name="uq_pipeline_events_idempotency"),
        # Fast replay by aggregate
        Index("ix_pipeline_events_aggregate", "aggregate_type", "aggregate_id", "event_version"),
        # Fast lookup by artifact
        Index("ix_pipeline_events_artifact", "global_artifact_id", "event_type"),
        # Fast lookup by correlation
        Index("ix_pipeline_events_correlation", "correlation_id"),
        # Fast lookup by run
        Index("ix_pipeline_events_run", "global_run_id"),
        # Time-series scans
        Index("ix_pipeline_events_time", "occurred_at"),
        {"schema": "events"},
    )

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Event identity
    event_type: Mapped[str] = mapped_column(Text, nullable=False)

    # Schema versioning — allows evolving event payload format over time.
    # Readers should check event_schema_version before parsing event_payload.
    event_schema_version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="1.0.0",
    )

    # Per-aggregate sequence number for ordering and replay.
    # Monotonically increasing per (aggregate_type, aggregate_id).
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)

    # Idempotency key — deterministic, prevents duplicate event insertion.
    # Format: deterministic_uuid(event_type, aggregate_id, global_run_id, occurred_at_iso)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Aggregate identity (what entity this event belongs to)
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Cross-reference to relational tables (nullable — event store is independent)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    step_run_internal_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Artifact / run identifiers
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    global_run_id: Mapped[Optional[str]] = mapped_column(Text)
    parent_artifact_id: Mapped[Optional[str]] = mapped_column(Text)

    # Service context
    service_name: Mapped[str] = mapped_column(Text, nullable=False)
    runner_id: Mapped[Optional[str]] = mapped_column(Text)
    host_id: Mapped[Optional[str]] = mapped_column(Text)
    service_version: Mapped[Optional[str]] = mapped_column(Text)

    # Tracing
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    otel_trace_id: Mapped[Optional[str]] = mapped_column(Text)
    otel_span_id: Mapped[Optional[str]] = mapped_column(Text)

    # Causation chain: which event caused this one
    caused_by_event_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("events.pipeline_events.event_id", ondelete="RESTRICT"),
    )

    # Immutable event payload
    event_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Metadata snapshot version at time of event (for full reconstruction)
    metadata_snapshot_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    # When the event logically occurred (business time)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # When it was recorded in the DB (server time, immutable)
    # created_at from ImmutableTimestampMixin serves as recorded_at
