"""Add scanner identity to core.file_records and dual-mode QC traceability columns to qc.qc_results.

Adds:

  core.file_records
    scanner_id   TEXT   — logical scanner label (from policy config or WSI metadata)
    scanner_name TEXT   — human-readable scanner name

  qc.qc_results
    qc_context          TEXT     — "pre_babelshark" | "post_babelshark" | "standalone"
    input_mode          TEXT     — "watcher" | "trigger"
    source_path         TEXT     — resolved absolute path of the file that was QC'd
    scanner_id          TEXT     — logical scanner ID at time of QC
    scanner_name        TEXT     — human-readable scanner name at time of QC
    trust_scanner_qc    BOOLEAN  — whether scanner internal QC was trusted
    pathoryx_qc_required BOOLEAN — whether Pathoryx QC was required by policy
    qc_skip_reason      TEXT     — why QC was skipped (trust_scanner_qc or not enabled)
    next_service        TEXT     — service triggered on pass (null in watcher/folder mode)
    next_stage          TEXT     — stage triggered on pass
    error_reason        TEXT     — error classification on failure

All columns are nullable. No existing rows are affected. No change to ck_file_records_status.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── core.file_records ────────────────────────────────────────────────────
    op.add_column("file_records", sa.Column("scanner_id",   sa.Text, nullable=True), schema="core")
    op.add_column("file_records", sa.Column("scanner_name", sa.Text, nullable=True), schema="core")
    op.create_index(
        "ix_file_records_scanner_id",
        "file_records",
        ["scanner_id"],
        schema="core",
    )

    # ── qc.qc_results ────────────────────────────────────────────────────────
    # Pipeline context
    op.add_column("qc_results", sa.Column("qc_context", sa.Text, nullable=True), schema="qc")
    op.add_column("qc_results", sa.Column("input_mode", sa.Text, nullable=True), schema="qc")

    # File location at time of QC
    op.add_column("qc_results", sa.Column("source_path", sa.Text, nullable=True), schema="qc")

    # Scanner identity at time of QC
    op.add_column("qc_results", sa.Column("scanner_id",   sa.Text, nullable=True), schema="qc")
    op.add_column("qc_results", sa.Column("scanner_name", sa.Text, nullable=True), schema="qc")

    # Policy decisions recorded for this run
    op.add_column("qc_results", sa.Column("trust_scanner_qc",     sa.Boolean, nullable=True), schema="qc")
    op.add_column("qc_results", sa.Column("pathoryx_qc_required", sa.Boolean, nullable=True), schema="qc")
    op.add_column("qc_results", sa.Column("qc_skip_reason",       sa.Text,    nullable=True), schema="qc")

    # Downstream routing recorded at write time
    op.add_column("qc_results", sa.Column("next_service", sa.Text, nullable=True), schema="qc")
    op.add_column("qc_results", sa.Column("next_stage",   sa.Text, nullable=True), schema="qc")

    # Error classification (queryable without parsing raw_qc_payload_json)
    op.add_column("qc_results", sa.Column("error_reason", sa.Text, nullable=True), schema="qc")

    op.create_index(
        "ix_qc_results_scanner_id",
        "qc_results",
        ["scanner_id"],
        schema="qc",
    )
    op.create_index(
        "ix_qc_results_qc_context",
        "qc_results",
        ["qc_context"],
        schema="qc",
    )
    op.create_index(
        "ix_qc_results_input_mode",
        "qc_results",
        ["input_mode"],
        schema="qc",
    )


def downgrade() -> None:
    # ── qc.qc_results — drop indexes then columns ────────────────────────────
    op.drop_index("ix_qc_results_input_mode", table_name="qc_results", schema="qc")
    op.drop_index("ix_qc_results_qc_context",  table_name="qc_results", schema="qc")
    op.drop_index("ix_qc_results_scanner_id",  table_name="qc_results", schema="qc")

    for col in [
        "error_reason",
        "next_stage",
        "next_service",
        "qc_skip_reason",
        "pathoryx_qc_required",
        "trust_scanner_qc",
        "scanner_name",
        "scanner_id",
        "source_path",
        "input_mode",
        "qc_context",
    ]:
        op.drop_column("qc_results", col, schema="qc")

    # ── core.file_records ────────────────────────────────────────────────────
    op.drop_index("ix_file_records_scanner_id", table_name="file_records", schema="core")
    op.drop_column("file_records", "scanner_name", schema="core")
    op.drop_column("file_records", "scanner_id",   schema="core")
