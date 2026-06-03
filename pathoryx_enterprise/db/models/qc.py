"""QC result model."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base, TimestampMixin


class QCResult(Base, TimestampMixin):
    __tablename__ = "qc_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_qc_results_idempotency"),
        Index("ix_qc_results_artifact", "global_artifact_id"),
        Index("ix_qc_results_decision", "decision_status"),
        {"schema": "qc"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )
    step_run_internal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    trigger_internal_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    global_run_id: Mapped[Optional[str]] = mapped_column(Text)
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)

    # Overall QC outcome
    qc_result: Mapped[Optional[str]] = mapped_column(Text)       # "passed" | "failed"
    decision_status: Mapped[Optional[str]] = mapped_column(Text) # "accepted" | "rejected"
    decision_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Per-module metric blobs
    blur_metrics: Mapped[Optional[dict]] = mapped_column(JSONB)
    stain_metrics: Mapped[Optional[dict]] = mapped_column(JSONB)
    penmark_metrics: Mapped[Optional[dict]] = mapped_column(JSONB)
    bubble_metrics: Mapped[Optional[dict]] = mapped_column(JSONB)
    sharpness_metrics: Mapped[Optional[dict]] = mapped_column(JSONB)

    decision_threshold_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    final_routed_path: Mapped[Optional[str]] = mapped_column(Text)

    total_duration_seconds: Mapped[Optional[float]] = mapped_column()
    model_versions_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    raw_qc_payload_json: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Operational metadata (added in migration 0002)
    runner_id: Mapped[Optional[str]] = mapped_column(Text)
    host_id: Mapped[Optional[str]] = mapped_column(Text)
    service_version: Mapped[Optional[str]] = mapped_column(Text)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Dual-mode context (added in migration 0004)
    # Pipeline position
    qc_context: Mapped[Optional[str]] = mapped_column(Text)  # "pre_babelshark" | "post_babelshark" | "standalone"
    input_mode: Mapped[Optional[str]] = mapped_column(Text)  # "watcher" | "trigger"

    # File location at time of QC
    source_path: Mapped[Optional[str]] = mapped_column(Text)

    # Scanner identity at time of QC
    scanner_id: Mapped[Optional[str]] = mapped_column(Text)
    scanner_name: Mapped[Optional[str]] = mapped_column(Text)

    # Policy decisions recorded for this run
    trust_scanner_qc: Mapped[Optional[bool]] = mapped_column()      # scanner internal QC trusted?
    pathoryx_qc_required: Mapped[Optional[bool]] = mapped_column()  # Pathoryx QC required by policy?
    qc_skip_reason: Mapped[Optional[str]] = mapped_column(Text)     # why QC was skipped

    # Downstream routing at write time
    next_service: Mapped[Optional[str]] = mapped_column(Text)
    next_stage: Mapped[Optional[str]] = mapped_column(Text)

    # Error classification — queryable without parsing raw_qc_payload_json
    error_reason: Mapped[Optional[str]] = mapped_column(Text)  # "unsupported_format" | "openslide_error" | "inference_error" | "file_missing"

    # Timing (added in migration 0005)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))   # inference start (from trigger.started_at)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))  # inference end (wall-clock at write time)

    # Resource tracking (added in migration 0005)
    # Populated once QC runner is updated to use ResourceMonitor; NULL until then.
    memory_rss_mb: Mapped[Optional[float]] = mapped_column()
    cpu_percent_avg: Mapped[Optional[float]] = mapped_column()

    # File info (added in migration 0005)
    input_file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
