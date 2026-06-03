"""Add recovery_sentry tracking columns to failed_watcher tables.

DB schema name stays 'failed_watcher' (rename would break existing data).
Code/service identity is 'recovery_sentry'.

New columns on technician_changes:
  - case_id                      parsed CaseID from slide filename
  - timestamp_in_filename        True when original filename already had a UTC timestamp
  - timestamp_extracted_from_wsi True when timestamp was pulled from WSI metadata
  - recovery_outcome             auto_recovered | manual_review_required | skipped | deleted
  - recovery_reason              reason code for manual_review_required
  - recovery_destination_path    final/ path after successful recovery
  - recovered_at                 UTC time of successful move

New columns on watched_folder_snapshots:
  - slide_id          parsed SlideID (base, no timestamp, no extension)
  - case_id           parsed CaseID
  - extension         lowercase file extension
  - inode_number      filesystem inode for rename tracking
  - partial_sha256    SHA-256 of first 4 MB for fast dedup

Revision ID: 0007
Revises:     0006
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── technician_changes recovery columns ─────────────────────────────────
    op.add_column(
        "technician_changes",
        sa.Column("case_id", sa.Text, nullable=True),
        schema="failed_watcher",
    )
    op.add_column(
        "technician_changes",
        sa.Column("timestamp_in_filename", sa.Boolean, nullable=False, server_default="false"),
        schema="failed_watcher",
    )
    op.add_column(
        "technician_changes",
        sa.Column("timestamp_extracted_from_wsi", sa.Boolean, nullable=False, server_default="false"),
        schema="failed_watcher",
    )
    op.add_column(
        "technician_changes",
        sa.Column("recovery_outcome", sa.Text, nullable=True),
        schema="failed_watcher",
    )
    op.add_column(
        "technician_changes",
        sa.Column("recovery_reason", sa.Text, nullable=True),
        schema="failed_watcher",
    )
    op.add_column(
        "technician_changes",
        sa.Column("recovery_destination_path", sa.Text, nullable=True),
        schema="failed_watcher",
    )
    op.add_column(
        "technician_changes",
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        schema="failed_watcher",
    )

    op.create_index(
        "ix_tc_recovery_outcome",
        "technician_changes",
        ["recovery_outcome"],
        schema="failed_watcher",
    )

    # ── watched_folder_snapshots extended fingerprint columns ────────────────
    op.add_column(
        "watched_folder_snapshots",
        sa.Column("slide_id", sa.Text, nullable=True),
        schema="failed_watcher",
    )
    op.add_column(
        "watched_folder_snapshots",
        sa.Column("case_id", sa.Text, nullable=True),
        schema="failed_watcher",
    )
    op.add_column(
        "watched_folder_snapshots",
        sa.Column("extension", sa.Text, nullable=True),
        schema="failed_watcher",
    )
    op.add_column(
        "watched_folder_snapshots",
        sa.Column("inode_number", sa.BigInteger, nullable=True),
        schema="failed_watcher",
    )
    op.add_column(
        "watched_folder_snapshots",
        sa.Column("partial_sha256", sa.Text, nullable=True),
        schema="failed_watcher",
    )


def downgrade() -> None:
    op.drop_column("technician_changes", "recovered_at", schema="failed_watcher")
    op.drop_column("technician_changes", "recovery_destination_path", schema="failed_watcher")
    op.drop_column("technician_changes", "recovery_reason", schema="failed_watcher")
    op.drop_index("ix_tc_recovery_outcome", table_name="technician_changes", schema="failed_watcher")
    op.drop_column("technician_changes", "recovery_outcome", schema="failed_watcher")
    op.drop_column("technician_changes", "timestamp_extracted_from_wsi", schema="failed_watcher")
    op.drop_column("technician_changes", "timestamp_in_filename", schema="failed_watcher")
    op.drop_column("technician_changes", "case_id", schema="failed_watcher")

    op.drop_column("watched_folder_snapshots", "partial_sha256", schema="failed_watcher")
    op.drop_column("watched_folder_snapshots", "inode_number", schema="failed_watcher")
    op.drop_column("watched_folder_snapshots", "extension", schema="failed_watcher")
    op.drop_column("watched_folder_snapshots", "case_id", schema="failed_watcher")
    op.drop_column("watched_folder_snapshots", "slide_id", schema="failed_watcher")
