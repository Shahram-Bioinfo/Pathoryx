"""Add timing and resource-tracking columns to qc.qc_results.

Adds:
  qc.qc_results
    started_at            TIMESTAMPTZ — when QC inference started (from trigger.started_at)
    finished_at           TIMESTAMPTZ — when QC inference finished (wall-clock at write time)
    memory_rss_mb         FLOAT       — RSS memory MB during inference
                                        NULL until QC runner is updated to capture it
                                        via pathoryx_enterprise.utils.process_metrics.ResourceMonitor
    cpu_percent_avg       FLOAT       — avg CPU% during inference
                                        NULL until QC runner captures it
    input_file_size_bytes BIGINT      — from file_records.file_size at QC time

All columns nullable.  No backfill. No constraint changes.

Resource fields (memory_rss_mb, cpu_percent_avg) are intentionally left NULL in
current production until QC runner.py is updated to call ResourceMonitor around
the inference call and pass the snapshot to QCDBWriter.record_qc_result().

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── qc.qc_results — timing ───────────────────────────────────────────────
    op.add_column(
        "qc_results",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        schema="qc",
    )
    op.add_column(
        "qc_results",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema="qc",
    )

    # ── qc.qc_results — resource tracking ────────────────────────────────────
    # These are NULL until the QC runner is updated to use ResourceMonitor.
    op.add_column(
        "qc_results",
        sa.Column("memory_rss_mb", sa.Float, nullable=True),
        schema="qc",
    )
    op.add_column(
        "qc_results",
        sa.Column("cpu_percent_avg", sa.Float, nullable=True),
        schema="qc",
    )

    # ── qc.qc_results — file info ─────────────────────────────────────────────
    op.add_column(
        "qc_results",
        sa.Column("input_file_size_bytes", sa.BigInteger, nullable=True),
        schema="qc",
    )

    # Index on started_at so time-range queries on QC runs are fast
    op.create_index(
        "ix_qc_results_started_at",
        "qc_results",
        ["started_at"],
        schema="qc",
    )


def downgrade() -> None:
    op.drop_index("ix_qc_results_started_at", table_name="qc_results", schema="qc")
    for col in [
        "input_file_size_bytes",
        "cpu_percent_avg",
        "memory_rss_mb",
        "finished_at",
        "started_at",
    ]:
        op.drop_column("qc_results", col, schema="qc")
