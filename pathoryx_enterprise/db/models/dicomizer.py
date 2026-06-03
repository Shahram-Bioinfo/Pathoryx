"""DICOM conversion result model."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base, TimestampMixin


class ConversionResult(Base, TimestampMixin):
    __tablename__ = "conversion_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_conversion_results_idempotency"),
        Index("ix_conversion_artifact", "global_artifact_id"),
        Index("ix_conversion_status", "conversion_status"),
        {"schema": "dicomizer"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )
    trigger_internal_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    global_run_id: Mapped[Optional[str]] = mapped_column(Text)
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)

    source_path: Mapped[Optional[str]] = mapped_column(Text)
    output_path: Mapped[Optional[str]] = mapped_column(Text)
    output_format: Mapped[Optional[str]] = mapped_column(Text)

    conversion_status: Mapped[Optional[str]] = mapped_column(Text)
    was_already_dicom: Mapped[Optional[bool]] = mapped_column()
    conversion_required: Mapped[Optional[bool]] = mapped_column()
    conversion_tool: Mapped[Optional[str]] = mapped_column(Text)
    conversion_tool_version: Mapped[Optional[str]] = mapped_column(Text)

    input_file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    output_file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    input_checksum_sha256: Mapped[Optional[str]] = mapped_column(Text)
    output_checksum_sha256: Mapped[Optional[str]] = mapped_column(Text)

    duration_seconds: Mapped[Optional[float]] = mapped_column()

    failure_context: Mapped[Optional[dict]] = mapped_column(JSONB)
    metadata_summary: Mapped[Optional[dict]] = mapped_column(JSONB)
    upload_result_json: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Operational metadata (added in migration 0002)
    runner_id: Mapped[Optional[str]] = mapped_column(Text)
    host_id: Mapped[Optional[str]] = mapped_column(Text)
    service_version: Mapped[Optional[str]] = mapped_column(Text)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
