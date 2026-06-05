"""Upload tracking model — estimated upload queue for operational visibility."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base


class EstimatedUploadQueue(Base):
    """
    Operational queue entry for each slide pending or in-progress upload.

    Written by the uploader service via the dashboard ingest API.
    Updated in-place as the upload progresses through states.

    upload_status values:
      queued      — registered, waiting for uploader to start
      estimating  — ETA computation in progress
      uploading   — transfer active
      uploaded    — completed successfully
      delayed     — ETA exceeded, still pending
      failed      — terminal failure
    """

    __tablename__ = "estimated_upload_queue"
    __table_args__ = (
        UniqueConstraint(
            "filename", "queued_at",
            name="uq_euq_filename_queued_at",
        ),
        Index("ix_euq_status",        "upload_status"),
        Index("ix_euq_queued_at",     "queued_at"),
        Index("ix_euq_scanner",       "scanner_id"),
        Index("ix_euq_last_updated",  "last_updated_at"),
        Index("ix_euq_status_queued", "upload_status", "queued_at"),
        {"schema": "upload_tracking"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    slide_id: Mapped[Optional[str]] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    scanner_id: Mapped[Optional[str]] = mapped_column(Text)
    uploader_host: Mapped[Optional[str]] = mapped_column(Text)

    # Lifecycle timestamps
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estimated_upload_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    upload_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    upload_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    upload_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    upload_speed_mbps: Mapped[Optional[float]] = mapped_column(Float)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)

    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
