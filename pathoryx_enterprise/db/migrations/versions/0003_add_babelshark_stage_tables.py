"""Add per-stage result tables to babelshark schema.

Each BabelShark pipeline stage now writes its own structured result row
directly to PostgreSQL at the time that metadata is produced.

Tables added (all in babelshark schema):
  - datamatrix_results       — per-label DataMatrix decode output
  - stain_results            — per-label OCR/ROI stain detection output
  - roi_results              — ROI metadata extraction output (DM-failed fallback)
  - color_marker_results     — color-dot research-routing detection output
  - pasnet_validation_results— LIS/PASNet validation outcome (replaces SQLite ops table)
  - slide_routing_decisions  — final rename/route decision per slide

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── babelshark.datamatrix_results ────────────────────────────────────────
    op.create_table(
        "datamatrix_results",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column(
            "file_record_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        # Per-image columns (one row per label image per slide)
        sa.Column("label_filename", sa.Text),
        sa.Column("datamatrix_raw", sa.Text),
        sa.Column("lab_id", sa.Text),
        sa.Column("year", sa.Text),
        sa.Column("case_number", sa.Text),
        sa.Column("pot", sa.Text),
        sa.Column("block_id", sa.Text),
        sa.Column("section", sa.Text),
        sa.Column("decode_status", sa.Text),          # success | failed
        sa.Column("decode_attempt_count", sa.Integer),
        sa.Column("error_reason", sa.Text),
        sa.Column("raw_payload", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_datamatrix_results_idempotency"),
        schema="babelshark",
    )
    op.create_index(
        "ix_datamatrix_results_artifact",
        "datamatrix_results",
        ["global_artifact_id"],
        schema="babelshark",
    )
    op.create_index(
        "ix_datamatrix_results_file_record",
        "datamatrix_results",
        ["file_record_internal_id"],
        schema="babelshark",
    )

    # ── babelshark.stain_results ─────────────────────────────────────────────
    op.create_table(
        "stain_results",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column(
            "file_record_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("label_filename", sa.Text),
        sa.Column("raw_ocr_words", sa.Text),
        sa.Column("cleaned_words", sa.Text),
        sa.Column("matched_word", sa.Text),
        sa.Column("stain_initial", sa.Text),
        sa.Column("stain_roi_double_check", sa.Text),
        sa.Column("stain_final", sa.Text),
        # "Primary" | "ROI-Fallback"
        sa.Column("stain_origin", sa.Text),
        sa.Column("raw_payload", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_stain_results_idempotency"),
        schema="babelshark",
    )
    op.create_index(
        "ix_stain_results_artifact",
        "stain_results",
        ["global_artifact_id"],
        schema="babelshark",
    )
    op.create_index(
        "ix_stain_results_file_record",
        "stain_results",
        ["file_record_internal_id"],
        schema="babelshark",
    )

    # ── babelshark.roi_results ───────────────────────────────────────────────
    op.create_table(
        "roi_results",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column(
            "file_record_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("label_filename", sa.Text),
        sa.Column("stain", sa.Text),
        sa.Column("datamatrix", sa.Text),
        sa.Column("lab_id", sa.Text),
        sa.Column("year", sa.Text),
        sa.Column("case_number", sa.Text),
        sa.Column("pot", sa.Text),
        sa.Column("block_id", sa.Text),
        sa.Column("section", sa.Text),
        # "Success" | "Failed"
        sa.Column("extraction_status", sa.Text),
        sa.Column("raw_payload", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_roi_results_idempotency"),
        schema="babelshark",
    )
    op.create_index(
        "ix_roi_results_artifact",
        "roi_results",
        ["global_artifact_id"],
        schema="babelshark",
    )

    # ── babelshark.color_marker_results ──────────────────────────────────────
    op.create_table(
        "color_marker_results",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column(
            "file_record_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("label_filename", sa.Text),
        # JSON array of detected color names
        sa.Column("detected_colors", postgresql.JSONB),
        sa.Column("dominant_color", sa.Text),
        sa.Column("is_research_case", sa.Boolean),
        # "routine" | "research" | "unknown"
        sa.Column("routing_hint", sa.Text),
        sa.Column("raw_payload", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_color_marker_results_idempotency"),
        schema="babelshark",
    )
    op.create_index(
        "ix_color_marker_results_artifact",
        "color_marker_results",
        ["global_artifact_id"],
        schema="babelshark",
    )

    # ── babelshark.pasnet_validation_results ─────────────────────────────────
    # Replaces the SQLite WSI_Babel_Shark_ops + WSI_Babel_Shark_post_validation_log
    # tables that previously lived in a per-run SQLite file.
    op.create_table(
        "pasnet_validation_results",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column(
            "file_record_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        # Input metadata
        sa.Column("case_id", sa.Text),
        sa.Column("slide_id", sa.Text),
        sa.Column("stain", sa.Text),
        # Validation context
        sa.Column("validation_mode", sa.Text),          # pre_rename | audit
        sa.Column("validation_status", sa.Text),        # VALID | INVALID | SKIPPED | ERROR
        sa.Column("reason_summary", sa.Text),
        # PASNet query results
        sa.Column("pasnet_connection_status", sa.Text), # OK | FAILED
        sa.Column("pasnet_case_exists", sa.Boolean),
        sa.Column("pasnet_slide_match_type", sa.Text),
        sa.Column("pasnet_slide_id", sa.Text),
        sa.Column("pasnet_stain_raw", sa.Text),
        sa.Column("pasnet_stain_canonical", sa.Text),
        # Extraction inputs
        sa.Column("extracted_slide_id", sa.Text),
        sa.Column("extracted_stain", sa.Text),
        sa.Column("extracted_stain_confidence", sa.Text),
        # Resolved output
        sa.Column("final_slide_id", sa.Text),
        sa.Column("final_stain", sa.Text),
        sa.Column("rename_source", sa.Text),
        # file_action: rename | keep_original | move_to_suspicious | skip
        sa.Column("file_action", sa.Text),
        sa.Column("details_json", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_pasnet_validation_results_idempotency"
        ),
        schema="babelshark",
    )
    op.create_index(
        "ix_pasnet_validation_results_artifact",
        "pasnet_validation_results",
        ["global_artifact_id"],
        schema="babelshark",
    )
    op.create_index(
        "ix_pasnet_validation_results_case_id",
        "pasnet_validation_results",
        ["case_id"],
        schema="babelshark",
    )
    op.create_index(
        "ix_pasnet_validation_status",
        "pasnet_validation_results",
        ["validation_status"],
        schema="babelshark",
    )

    # ── babelshark.slide_routing_decisions ───────────────────────────────────
    op.create_table(
        "slide_routing_decisions",
        sa.Column("internal_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column(
            "file_record_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.file_records.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_internal_id",
            sa.BigInteger,
            sa.ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("global_artifact_id", sa.Text),
        sa.Column("correlation_id", sa.Text),
        sa.Column("original_filename", sa.Text),
        sa.Column("new_filename", sa.Text),
        sa.Column("original_path", sa.Text),
        sa.Column("final_path", sa.Text),
        # routine | research | blacklist | unreadable | duplicate | failed
        sa.Column("routing_type", sa.Text, nullable=False),
        sa.Column("routing_reason", sa.Text),
        sa.Column("case_id", sa.Text),
        sa.Column("slide_id", sa.Text),
        sa.Column("stain", sa.Text),
        sa.Column("lab_id", sa.Text),
        sa.Column("year", sa.Text),
        sa.Column("case_number", sa.Text),
        sa.Column("pot", sa.Text),
        sa.Column("block_id", sa.Text),
        sa.Column("section", sa.Text),
        sa.Column("scanner_id", sa.Text),
        sa.Column("scanner_model", sa.Text),
        sa.Column("scanner_vendor", sa.Text),
        sa.Column("routing_metadata_json", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_slide_routing_decisions_idempotency"
        ),
        schema="babelshark",
    )
    op.create_index(
        "ix_slide_routing_decisions_artifact",
        "slide_routing_decisions",
        ["global_artifact_id"],
        schema="babelshark",
    )
    op.create_index(
        "ix_slide_routing_type",
        "slide_routing_decisions",
        ["routing_type"],
        schema="babelshark",
    )
    op.create_index(
        "ix_slide_routing_file_record",
        "slide_routing_decisions",
        ["file_record_internal_id"],
        schema="babelshark",
    )


def downgrade() -> None:
    for table in [
        "slide_routing_decisions",
        "pasnet_validation_results",
        "color_marker_results",
        "roi_results",
        "stain_results",
        "datamatrix_results",
    ]:
        op.drop_table(table, schema="babelshark")
