"""
Core pipeline models: FileRecord, PipelineRun, StepRun, ServiceTrigger, TechnicalMetrics.

These tables are the spine of the system. All services read and write here.

Locking strategy:
  - FileRecord: SELECT FOR UPDATE used during intake classification to prevent races.
  - ServiceTrigger: SELECT FOR UPDATE SKIP LOCKED in dequeue_next() prevents double-processing.
  - All status columns use TEXT with CHECK constraints enforced in the migration.

State machine for FileRecord.status:
  detected → intake_running → intake_registered
  intake_registered → qc_pending | dicom_pending
  qc_pending → qc_running → qc_passed | qc_failed
  qc_passed → dicom_pending
  dicom_pending → dicom_running → dicom_done | dicom_failed
  dicom_done → upload_pending
  upload_pending → upload_running → uploaded | upload_failed
  *_failed → (RecoverySentry monitoring, possible requeue)
"""
from __future__ import annotations

import uuid as py_uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base, TimestampMixin


class FileRecord(Base, TimestampMixin):
    """
    One row per physical file seen by the system.
    canonical_path is UNIQUE — enforced at the DB level to prevent duplicate inserts
    even under concurrent workers.
    """

    __tablename__ = "file_records"
    __table_args__ = (
        UniqueConstraint("canonical_path", name="uq_file_records_canonical_path"),
        UniqueConstraint(
            "source_service",
            "source_artifact_id",
            name="uq_file_records_source",
        ),
        Index("ix_file_records_global_artifact_id", "global_artifact_id"),
        Index("ix_file_records_status", "status"),
        Index("ix_file_records_original_filename", "original_filename"),
        {"schema": "core"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[py_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=py_uuid.uuid4,
        nullable=False,
        unique=True,
    )

    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    parent_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    source_service: Mapped[Optional[str]] = mapped_column(Text)
    source_artifact_id: Mapped[Optional[str]] = mapped_column(Text)

    artifact_type: Mapped[Optional[str]] = mapped_column(Text)
    original_filename: Mapped[Optional[str]] = mapped_column(Text)
    current_filename: Mapped[Optional[str]] = mapped_column(Text)
    original_path: Mapped[Optional[str]] = mapped_column(Text)
    current_file_path: Mapped[Optional[str]] = mapped_column(Text)
    canonical_path: Mapped[Optional[str]] = mapped_column(Text)

    file_format: Mapped[Optional[str]] = mapped_column(Text)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(Text)

    # Pipeline state machine status. CHECK constraint in migration.
    status: Mapped[Optional[str]] = mapped_column(Text)

    # Scanner identity — populated by QC watcher (pre-BabelShark) or BabelShark intake.
    # Logical label from scanner policy config or extracted WSI metadata.
    scanner_id: Mapped[Optional[str]] = mapped_column(Text)
    scanner_name: Mapped[Optional[str]] = mapped_column(Text)

    # Active metadata snapshot version (FK to metadata_snapshots.snapshot_id)
    current_snapshot_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    # JSONB fields kept for backward compat with BabelShark DB writer
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    input_metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    output_metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB)


class MetadataSnapshot(Base):
    """
    Immutable versioned metadata snapshots.

    Every time metadata changes, a new row is inserted. Rows are NEVER updated.
    FileRecord.current_snapshot_id points to the latest version.
    previous_snapshot_id forms a linked list back to version 1.
    """

    __tablename__ = "metadata_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "global_artifact_id",
            "snapshot_version",
            name="uq_metadata_snapshots_artifact_version",
        ),
        Index("ix_metadata_snapshots_artifact", "global_artifact_id"),
        {"schema": "core"},
    )

    snapshot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    global_artifact_id: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_service: Mapped[str] = mapped_column(Text, nullable=False)

    # Immutable payload — serialized as JSONB
    snapshot_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # SHA-256 of the serialized payload for integrity verification
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # Lineage pointer — forms a linked list of versions
    previous_snapshot_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.metadata_snapshots.snapshot_id", ondelete="RESTRICT"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    # No updated_at — this table is immutable.


class PipelineRun(Base, TimestampMixin):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_global_run_id", "global_run_id"),
        Index("ix_pipeline_runs_status", "service_name", "run_status"),
        Index("ix_pipeline_runs_artifact", "file_record_internal_id"),
        {"schema": "core"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[py_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=py_uuid.uuid4,
        nullable=False,
        unique=True,
    )

    global_run_id: Mapped[Optional[str]] = mapped_column(Text)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
    )
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)

    service_name: Mapped[Optional[str]] = mapped_column(Text)
    pipeline_name: Mapped[Optional[str]] = mapped_column(Text)
    run_status: Mapped[Optional[str]] = mapped_column(Text)
    final_outcome: Mapped[Optional[str]] = mapped_column(Text)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Multi-machine fields
    runner_id: Mapped[Optional[str]] = mapped_column(Text)
    host_id: Mapped[Optional[str]] = mapped_column(Text)

    # Tracing
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    otel_trace_id: Mapped[Optional[str]] = mapped_column(Text)
    otel_span_id: Mapped[Optional[str]] = mapped_column(Text)

    # Retry tracking
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)


class StepRun(Base, TimestampMixin):
    __tablename__ = "step_runs"
    __table_args__ = (
        Index("ix_step_runs_run_step", "pipeline_run_internal_id", "step_name"),
        Index("ix_step_runs_status", "step_status"),
        {"schema": "core"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.pipeline_runs.internal_id", ondelete="CASCADE"),
    )

    step_name: Mapped[Optional[str]] = mapped_column(Text)
    step_status: Mapped[Optional[str]] = mapped_column(Text)
    outcome: Mapped[Optional[str]] = mapped_column(Text)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(BigInteger)

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    error_type: Mapped[Optional[str]] = mapped_column(Text)
    context_json: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Resource metrics snapshot for this step
    cpu_percent_avg: Mapped[Optional[float]] = mapped_column()
    memory_rss_mb: Mapped[Optional[float]] = mapped_column()
    disk_read_mb: Mapped[Optional[float]] = mapped_column()
    disk_write_mb: Mapped[Optional[float]] = mapped_column()


class ServiceTrigger(Base, TimestampMixin):
    """
    Inter-service message queue backed by PostgreSQL.

    Consumers use SELECT … FOR UPDATE SKIP LOCKED for safe concurrent dequeue.
    Unique constraint prevents duplicate triggers for the same (source→target, stage, file).

    Dead-letter strategy: triggers with retry_count >= max_retries and
    trigger_status = 'failed' are surfaced via RecoverySentry for review.
    """

    __tablename__ = "service_trigger"
    __table_args__ = (
        UniqueConstraint(
            "source_service",
            "target_service",
            "stage_name",
            "file_record_internal_id",
            name="uq_trigger_per_file_stage",
        ),
        # Primary dequeue index: target + status + time
        Index(
            "ix_trigger_dequeue",
            "target_service",
            "trigger_status",
            "triggered_at",
            postgresql_where="trigger_status IN ('pending', 'failed')",
        ),
        Index("ix_trigger_correlation", "correlation_id"),
        {"schema": "core"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    source_service: Mapped[str] = mapped_column(Text, nullable=False)
    target_service: Mapped[str] = mapped_column(Text, nullable=False)
    stage_name: Mapped[str] = mapped_column(Text, nullable=False)

    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
    )
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    global_run_id: Mapped[Optional[str]] = mapped_column(Text)

    trigger_status: Mapped[Optional[str]] = mapped_column(Text)
    trigger_payload_json: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Retry
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Timing
    triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Tracing
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    otel_trace_id: Mapped[Optional[str]] = mapped_column(Text)

    # Multi-machine: which runner claimed this trigger
    claimed_by_runner_id: Mapped[Optional[str]] = mapped_column(Text)
    claimed_by_host_id: Mapped[Optional[str]] = mapped_column(Text)


class TechnicalMetrics(Base, TimestampMixin):
    """Resource usage metrics per pipeline step per machine."""

    __tablename__ = "technical_metrics"
    __table_args__ = (
        Index("ix_technical_metrics_stage_time", "service_name", "stage_name", "started_at"),
        {"schema": "core"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
    )
    step_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.step_runs.internal_id", ondelete="SET NULL"),
    )

    service_name: Mapped[Optional[str]] = mapped_column(Text)
    stage_name: Mapped[Optional[str]] = mapped_column(Text)
    host_id: Mapped[Optional[str]] = mapped_column(Text)
    runner_id: Mapped[Optional[str]] = mapped_column(Text)
    pid: Mapped[Optional[int]] = mapped_column(Integer)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(BigInteger)

    service_version: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)

    # CPU
    cpu_percent_avg: Mapped[Optional[float]] = mapped_column()
    cpu_percent_peak: Mapped[Optional[float]] = mapped_column()
    cpu_time_user: Mapped[Optional[float]] = mapped_column()
    cpu_time_system: Mapped[Optional[float]] = mapped_column()

    # Memory
    memory_rss_mb: Mapped[Optional[float]] = mapped_column()
    memory_peak_mb: Mapped[Optional[float]] = mapped_column()
    memory_percent: Mapped[Optional[float]] = mapped_column()

    # Disk I/O
    disk_read_mb: Mapped[Optional[float]] = mapped_column()
    disk_write_mb: Mapped[Optional[float]] = mapped_column()
    read_count: Mapped[Optional[int]] = mapped_column(BigInteger)
    write_count: Mapped[Optional[int]] = mapped_column(BigInteger)

    # File sizes
    input_file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    output_file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)

    # GPU
    gpu_name: Mapped[Optional[str]] = mapped_column(Text)
    gpu_index: Mapped[Optional[int]] = mapped_column(Integer)
    gpu_memory_allocated_mb: Mapped[Optional[float]] = mapped_column()
    gpu_memory_reserved_mb: Mapped[Optional[float]] = mapped_column()
    gpu_memory_peak_mb: Mapped[Optional[float]] = mapped_column()
    gpu_utilization_percent: Mapped[Optional[float]] = mapped_column()
    gpu_temperature_celsius: Mapped[Optional[float]] = mapped_column()

    extra_metrics_json: Mapped[Optional[dict]] = mapped_column(JSONB)


class RunnerRegistration(Base):
    """
    Stable identity record for each service runner process.

    Each runner inserts/upserts its registration at startup and updates
    last_heartbeat_at every N seconds. This enables:
      - multi-machine visibility (which hosts are running)
      - dead runner detection (heartbeat stale > threshold → 'crashed')
      - trigger claim attribution (ServiceTrigger.claimed_by_runner_id)
    """

    __tablename__ = "runner_registrations"
    __table_args__ = (
        UniqueConstraint("runner_id", name="uq_runner_registrations_runner_id"),
        Index("ix_runner_reg_service_status", "service_name", "status"),
        {"schema": "core"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    runner_id: Mapped[str] = mapped_column(Text, nullable=False)
    service_name: Mapped[str] = mapped_column(Text, nullable=False)
    host_id: Mapped[str] = mapped_column(Text, nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    environment: Mapped[Optional[str]] = mapped_column(Text)
    service_version: Mapped[Optional[str]] = mapped_column(Text)
    config_hash: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(Text, default="active", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shutdown_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    capabilities_json: Mapped[Optional[dict]] = mapped_column(JSONB)
