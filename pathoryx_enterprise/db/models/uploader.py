"""Upload result model."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base, TimestampMixin


class UploadResult(Base, TimestampMixin):
    __tablename__ = "upload_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_upload_results_idempotency"),
        Index("ix_upload_artifact", "global_artifact_id"),
        Index("ix_upload_status", "upload_status"),
        {"schema": "uploader"},
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
    target_system: Mapped[Optional[str]] = mapped_column(Text)
    target_endpoint: Mapped[Optional[str]] = mapped_column(Text)

    upload_status: Mapped[Optional[str]] = mapped_column(Text)
    upload_method: Mapped[Optional[str]] = mapped_column(Text)
    final_outcome: Mapped[Optional[str]] = mapped_column(Text)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)

    duration_seconds: Mapped[Optional[float]] = mapped_column()
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    response_summary: Mapped[Optional[dict]] = mapped_column(JSONB)
    failure_context: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Operational metadata (added in migration 0002)
    runner_id: Mapped[Optional[str]] = mapped_column(Text)
    host_id: Mapped[Optional[str]] = mapped_column(Text)
    service_version: Mapped[Optional[str]] = mapped_column(Text)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
