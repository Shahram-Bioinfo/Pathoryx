"""Add operational metadata columns to service result tables.

Adds trigger_internal_id, runner_id, host_id, service_version, processed_at
to qc_results, conversion_results, and upload_results.

Also adds upload_result_json to conversion_results, and upload_method /
file_size to upload_results.

These columns were expected by the db_writer layer but missing from the
initial schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── qc.qc_results ────────────────────────────────────────────────────────
    for col in [
        sa.Column("trigger_internal_id", sa.BigInteger, nullable=True),
        sa.Column("runner_id", sa.Text, nullable=True),
        sa.Column("host_id", sa.Text, nullable=True),
        sa.Column("service_version", sa.Text, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        op.add_column("qc_results", col, schema="qc")

    # ── dicomizer.conversion_results ─────────────────────────────────────────
    for col in [
        sa.Column("trigger_internal_id", sa.BigInteger, nullable=True),
        sa.Column("upload_result_json", postgresql.JSONB, nullable=True),
        sa.Column("runner_id", sa.Text, nullable=True),
        sa.Column("host_id", sa.Text, nullable=True),
        sa.Column("service_version", sa.Text, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        op.add_column("conversion_results", col, schema="dicomizer")

    # ── uploader.upload_results ───────────────────────────────────────────────
    for col in [
        sa.Column("trigger_internal_id", sa.BigInteger, nullable=True),
        sa.Column("upload_method", sa.Text, nullable=True),
        sa.Column("file_size", sa.BigInteger, nullable=True),
        sa.Column("runner_id", sa.Text, nullable=True),
        sa.Column("host_id", sa.Text, nullable=True),
        sa.Column("service_version", sa.Text, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        op.add_column("upload_results", col, schema="uploader")


def downgrade() -> None:
    for col in ["trigger_internal_id", "runner_id", "host_id", "service_version", "processed_at"]:
        op.drop_column("qc_results", col, schema="qc")

    for col in ["trigger_internal_id", "upload_result_json", "runner_id", "host_id",
                "service_version", "processed_at"]:
        op.drop_column("conversion_results", col, schema="dicomizer")

    for col in ["trigger_internal_id", "upload_method", "file_size", "runner_id", "host_id",
                "service_version", "processed_at"]:
        op.drop_column("upload_results", col, schema="uploader")
