"""Create upload_tracking schema and estimated_upload_queue table.

Adds operational upload queue visibility for the dashboard.

New schema: upload_tracking
New table:  upload_tracking.estimated_upload_queue

Populated by the uploader service via the dashboard ingest API.
Indexed for efficient queue queries, status filtering, and time-range scans.

Revision ID: 0008
Revises:     0007
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS upload_tracking")

    op.create_table(
        "estimated_upload_queue",
        sa.Column("id",                  sa.BigInteger,              primary_key=True, autoincrement=True),
        sa.Column("slide_id",            sa.Text,                    nullable=True),
        sa.Column("filename",            sa.Text,                    nullable=False),
        sa.Column("scanner_id",          sa.Text,                    nullable=True),
        sa.Column("uploader_host",       sa.Text,                    nullable=True),
        sa.Column("queued_at",           sa.DateTime(timezone=True), nullable=False),
        sa.Column("estimated_upload_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_started_at",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_status",       sa.Text,                    nullable=False, server_default="queued"),
        sa.Column("retry_count",         sa.Integer,                 nullable=False, server_default="0"),
        sa.Column("file_size_bytes",     sa.BigInteger,              nullable=True),
        sa.Column("priority",            sa.Integer,                 nullable=False, server_default="5"),
        sa.Column("upload_speed_mbps",   sa.Float,                   nullable=True),
        sa.Column("failure_reason",      sa.Text,                    nullable=True),
        sa.Column("last_updated_at",     sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("filename", "queued_at", name="uq_euq_filename_queued_at"),
        schema="upload_tracking",
    )

    op.create_index(
        "ix_euq_status",
        "estimated_upload_queue", ["upload_status"],
        schema="upload_tracking",
    )
    op.create_index(
        "ix_euq_queued_at",
        "estimated_upload_queue", ["queued_at"],
        schema="upload_tracking",
    )
    op.create_index(
        "ix_euq_scanner",
        "estimated_upload_queue", ["scanner_id"],
        schema="upload_tracking",
    )
    op.create_index(
        "ix_euq_last_updated",
        "estimated_upload_queue", ["last_updated_at"],
        schema="upload_tracking",
    )
    op.create_index(
        "ix_euq_status_queued",
        "estimated_upload_queue", ["upload_status", "queued_at"],
        schema="upload_tracking",
    )


def downgrade() -> None:
    op.drop_index("ix_euq_status_queued", table_name="estimated_upload_queue", schema="upload_tracking")
    op.drop_index("ix_euq_last_updated",  table_name="estimated_upload_queue", schema="upload_tracking")
    op.drop_index("ix_euq_scanner",       table_name="estimated_upload_queue", schema="upload_tracking")
    op.drop_index("ix_euq_queued_at",     table_name="estimated_upload_queue", schema="upload_tracking")
    op.drop_index("ix_euq_status",        table_name="estimated_upload_queue", schema="upload_tracking")
    op.drop_table("estimated_upload_queue", schema="upload_tracking")
    op.execute("DROP SCHEMA IF EXISTS upload_tracking")
