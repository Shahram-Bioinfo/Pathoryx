"""
Failed/Suspicious Slide Watcher models.

Two tables:
  1. watched_folder_snapshots — baseline state of watched folders (upserted each cycle)
  2. technician_changes       — immutable audit log of detected technician interventions

The technician_changes table is append-only. Every detected change gets its own row.
Rows are never updated. Resolution is tracked via review_status transitions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base, ImmutableTimestampMixin, TimestampMixin

# DB schema name stays 'failed_watcher' for migration safety.
# Code/service identity is 'recovery_sentry'.


class WatchedFolderSnapshot(Base, TimestampMixin):
    """
    Current state of a single file in a watched folder.

    Updated via UPSERT on each poll cycle. Used as the baseline for change detection.
    One row per (folder_label, file_path) — the idempotency_key enforces this.
    """

    __tablename__ = "watched_folder_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "folder_label",
            "file_path",
            name="uq_watched_folder_snapshots_path",
        ),
        Index("ix_wfs_folder_label", "folder_label"),
        Index("ix_wfs_last_seen", "last_seen_at"),
        {"schema": "failed_watcher"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    folder_label: Mapped[str] = mapped_column(Text, nullable=False)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)

    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    mtime_ns: Mapped[Optional[int]] = mapped_column(BigInteger)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(Text)

    # Link to known artifact (if identifiable by slide ID pattern)
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
    )

    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Metadata sidecar contents if a .json/.yaml file exists alongside the slide
    sidecar_metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Extended fingerprint columns (added by migration 0007 for RecoverySentry)
    slide_id: Mapped[Optional[str]] = mapped_column(Text)
    case_id: Mapped[Optional[str]] = mapped_column(Text)
    extension: Mapped[Optional[str]] = mapped_column(Text)
    inode_number: Mapped[Optional[int]] = mapped_column(BigInteger)
    partial_sha256: Mapped[Optional[str]] = mapped_column(Text)


class TechnicianChange(Base, ImmutableTimestampMixin):
    """
    Immutable audit record of a technician intervention in a watched folder.

    Created when the watcher detects a change between two consecutive snapshots.
    This table is APPEND-ONLY — rows are never updated.
    review_status transitions are tracked by inserting new events into PipelineEvent
    rather than updating this row.

    CHANGE TYPES:
      rename         — same folder, different filename
      replace        — same path, different size/checksum
      move           — different folder, same or different name
      metadata_update — sidecar file changed
      size_change    — file grew or shrank (partial upload detected)
      checksum_change — file modified in place (size same, content different)
      new_file       — file appeared that was not in previous snapshot
      removed        — file was removed from the watched folder
    """

    __tablename__ = "technician_changes"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_technician_changes_idempotency"),
        Index("ix_tc_artifact", "global_artifact_id"),
        Index("ix_tc_folder", "watch_folder_label"),
        Index("ix_tc_status", "review_status"),
        Index("ix_tc_detected", "detected_at"),
        {"schema": "failed_watcher"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Idempotency: prevents duplicate records for the same observed change
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Change classification
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    watch_folder_label: Mapped[str] = mapped_column(Text, nullable=False)

    # Before state
    old_path: Mapped[Optional[str]] = mapped_column(Text)
    old_filename: Mapped[Optional[str]] = mapped_column(Text)
    old_file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    old_checksum_sha256: Mapped[Optional[str]] = mapped_column(Text)
    old_mtime_ns: Mapped[Optional[int]] = mapped_column(BigInteger)

    # After state
    new_path: Mapped[Optional[str]] = mapped_column(Text)
    new_filename: Mapped[Optional[str]] = mapped_column(Text)
    new_file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    new_checksum_sha256: Mapped[Optional[str]] = mapped_column(Text)
    new_mtime_ns: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Link to known artifact (resolved from slide ID patterns)
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    parent_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
    )

    # Inferred technician intent
    inferred_action: Mapped[Optional[str]] = mapped_column(Text)
    slide_id_inferred: Mapped[Optional[str]] = mapped_column(Text)

    # Sidecar metadata from the new file location (if present)
    sidecar_metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    technician_notes: Mapped[Optional[str]] = mapped_column(Text)

    # Review workflow
    # REVIEW_STATUSES: detected → linked | unlinked → reviewed → requeued | dismissed
    review_status: Mapped[str] = mapped_column(Text, default="detected", nullable=False)
    reviewed_by: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[Optional[str]] = mapped_column(Text)

    # Requeue tracking: did this change trigger a new processing attempt?
    requeue_trigger_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("core.service_trigger.internal_id", ondelete="SET NULL"),
    )
    requeued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Manual approval workflow (optional, enabled per config)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[Optional[str]] = mapped_column(Text)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Timing
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Context
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    runner_id: Mapped[Optional[str]] = mapped_column(Text)

    # RecoverySentry recovery outcome columns (added by migration 0007)
    case_id: Mapped[Optional[str]] = mapped_column(Text)
    timestamp_in_filename: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timestamp_extracted_from_wsi: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recovery_outcome: Mapped[Optional[str]] = mapped_column(Text)
    recovery_reason: Mapped[Optional[str]] = mapped_column(Text)
    recovery_destination_path: Mapped[Optional[str]] = mapped_column(Text)
    recovered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
