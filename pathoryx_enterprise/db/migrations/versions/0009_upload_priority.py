"""Upload priority queue — Phase 4.6A.

Adds two priority layers:
  1. File-level priority (operator-set via dashboard)
  2. Watch-folder-level priority (config-driven, assigned at intake)

Changes:
  core.service_trigger:
    + priority INTEGER NOT NULL DEFAULT 5
    + index ix_trigger_upload_priority  (partial, upload_service only)

  upload_tracking.estimated_upload_queue:
    + file_record_internal_id BIGINT NULL  (enables trigger sync on priority update)
    + priority_source TEXT DEFAULT 'default'
    + priority_reason TEXT NULL
    + priority_updated_at TIMESTAMPTZ NULL
    + priority_updated_by TEXT NULL
    + watch_folder_path TEXT NULL
    + watch_folder_label TEXT NULL
    + index ix_euq_priority  (upload_status, priority, queued_at)

Revision ID: 0009
Revises:     0008
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── core.service_trigger ────────────────────────────────────────────────
    op.add_column(
        "service_trigger",
        sa.Column("priority", sa.Integer, nullable=False, server_default="5"),
        schema="core",
    )
    # Partial index for priority-aware upload dequeue (upload_service only).
    # Keeps all other services unaffected.
    op.create_index(
        "ix_trigger_upload_priority",
        "service_trigger",
        ["priority", "triggered_at", "internal_id"],
        schema="core",
        postgresql_where="target_service = 'upload_service' AND trigger_status = 'pending'",
    )

    # ── upload_tracking.estimated_upload_queue ──────────────────────────────
    op.add_column(
        "estimated_upload_queue",
        sa.Column("file_record_internal_id", sa.BigInteger, nullable=True),
        schema="upload_tracking",
    )
    op.add_column(
        "estimated_upload_queue",
        sa.Column("priority_source", sa.Text, nullable=False, server_default="default"),
        schema="upload_tracking",
    )
    op.add_column(
        "estimated_upload_queue",
        sa.Column("priority_reason", sa.Text, nullable=True),
        schema="upload_tracking",
    )
    op.add_column(
        "estimated_upload_queue",
        sa.Column("priority_updated_at", sa.DateTime(timezone=True), nullable=True),
        schema="upload_tracking",
    )
    op.add_column(
        "estimated_upload_queue",
        sa.Column("priority_updated_by", sa.Text, nullable=True),
        schema="upload_tracking",
    )
    op.add_column(
        "estimated_upload_queue",
        sa.Column("watch_folder_path", sa.Text, nullable=True),
        schema="upload_tracking",
    )
    op.add_column(
        "estimated_upload_queue",
        sa.Column("watch_folder_label", sa.Text, nullable=True),
        schema="upload_tracking",
    )

    op.create_index(
        "ix_euq_priority",
        "estimated_upload_queue",
        ["upload_status", "priority", "queued_at"],
        schema="upload_tracking",
    )


def downgrade() -> None:
    op.drop_index("ix_euq_priority", table_name="estimated_upload_queue", schema="upload_tracking")

    for col in ["watch_folder_label", "watch_folder_path", "priority_updated_by",
                "priority_updated_at", "priority_reason", "priority_source",
                "file_record_internal_id"]:
        op.drop_column("estimated_upload_queue", col, schema="upload_tracking")

    op.drop_index("ix_trigger_upload_priority", table_name="service_trigger", schema="core")
    op.drop_column("service_trigger", "priority", schema="core")
