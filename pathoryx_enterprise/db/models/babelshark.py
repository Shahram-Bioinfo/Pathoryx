"""BabelShark ORM models — extraction results and per-stage result tables.

All tables live in the ``babelshark`` PostgreSQL schema.
Stage tables are written directly by each pipeline stage via
BabelSharkStageDBWriter so that structured metadata lands in PostgreSQL
at the moment it is produced, independent of Excel file generation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pathoryx_enterprise.db.base import Base, TimestampMixin


class ExtractionResult(Base, TimestampMixin):
    __tablename__ = "extraction_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_extraction_results_idempotency"),
        Index("ix_extraction_artifact", "global_artifact_id"),
        {"schema": "babelshark"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )

    global_run_id: Mapped[Optional[str]] = mapped_column(Text)
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)

    intake_decision: Mapped[Optional[str]] = mapped_column(Text)
    action_taken: Mapped[Optional[str]] = mapped_column(Text)
    next_stage: Mapped[Optional[str]] = mapped_column(Text)

    scanner_id: Mapped[Optional[str]] = mapped_column(Text)
    scanner_model: Mapped[Optional[str]] = mapped_column(Text)
    scanner_vendor: Mapped[Optional[str]] = mapped_column(Text)
    slide_id: Mapped[Optional[str]] = mapped_column(Text)
    stain_type: Mapped[Optional[str]] = mapped_column(Text)

    requires_qc: Mapped[Optional[bool]] = mapped_column()
    has_internal_qc: Mapped[Optional[bool]] = mapped_column()

    extraction_status: Mapped[Optional[str]] = mapped_column(Text)
    extraction_duration_ms: Mapped[Optional[int]] = mapped_column(BigInteger)

    metadata_snapshot_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    raw_extraction_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    normalized_metadata: Mapped[Optional[dict]] = mapped_column(JSONB)


# ---------------------------------------------------------------------------
# Per-stage result tables — written directly at stage completion time
# ---------------------------------------------------------------------------


class DatamatrixResult(Base, TimestampMixin):
    """Per-label-image DataMatrix decode result."""

    __tablename__ = "datamatrix_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_datamatrix_results_idempotency"),
        Index("ix_datamatrix_results_artifact", "global_artifact_id"),
        Index("ix_datamatrix_results_file_record", "file_record_internal_id"),
        {"schema": "babelshark"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    label_filename: Mapped[Optional[str]] = mapped_column(Text)
    datamatrix_raw: Mapped[Optional[str]] = mapped_column(Text)
    lab_id: Mapped[Optional[str]] = mapped_column(Text)
    year: Mapped[Optional[str]] = mapped_column(Text)
    case_number: Mapped[Optional[str]] = mapped_column(Text)
    pot: Mapped[Optional[str]] = mapped_column(Text)
    block_id: Mapped[Optional[str]] = mapped_column(Text)
    section: Mapped[Optional[str]] = mapped_column(Text)
    decode_status: Mapped[Optional[str]] = mapped_column(Text)
    decode_attempt_count: Mapped[Optional[int]] = mapped_column(Integer)
    error_reason: Mapped[Optional[str]] = mapped_column(Text)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB)


class StainResult(Base, TimestampMixin):
    """Per-label-image OCR/ROI stain detection result."""

    __tablename__ = "stain_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_stain_results_idempotency"),
        Index("ix_stain_results_artifact", "global_artifact_id"),
        Index("ix_stain_results_file_record", "file_record_internal_id"),
        {"schema": "babelshark"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    label_filename: Mapped[Optional[str]] = mapped_column(Text)
    raw_ocr_words: Mapped[Optional[str]] = mapped_column(Text)
    cleaned_words: Mapped[Optional[str]] = mapped_column(Text)
    matched_word: Mapped[Optional[str]] = mapped_column(Text)
    stain_initial: Mapped[Optional[str]] = mapped_column(Text)
    stain_roi_double_check: Mapped[Optional[str]] = mapped_column(Text)
    stain_final: Mapped[Optional[str]] = mapped_column(Text)
    stain_origin: Mapped[Optional[str]] = mapped_column(Text)  # Primary | ROI-Fallback
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB)


class RoiResult(Base, TimestampMixin):
    """ROI metadata extraction result (DataMatrix-failed fallback)."""

    __tablename__ = "roi_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_roi_results_idempotency"),
        Index("ix_roi_results_artifact", "global_artifact_id"),
        {"schema": "babelshark"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    label_filename: Mapped[Optional[str]] = mapped_column(Text)
    stain: Mapped[Optional[str]] = mapped_column(Text)
    datamatrix: Mapped[Optional[str]] = mapped_column(Text)
    lab_id: Mapped[Optional[str]] = mapped_column(Text)
    year: Mapped[Optional[str]] = mapped_column(Text)
    case_number: Mapped[Optional[str]] = mapped_column(Text)
    pot: Mapped[Optional[str]] = mapped_column(Text)
    block_id: Mapped[Optional[str]] = mapped_column(Text)
    section: Mapped[Optional[str]] = mapped_column(Text)
    extraction_status: Mapped[Optional[str]] = mapped_column(Text)  # Success | Failed
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB)


class ColorMarkerResult(Base, TimestampMixin):
    """Color marker detection result for research-routing decisions."""

    __tablename__ = "color_marker_results"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_color_marker_results_idempotency"),
        Index("ix_color_marker_results_artifact", "global_artifact_id"),
        {"schema": "babelshark"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    label_filename: Mapped[Optional[str]] = mapped_column(Text)
    detected_colors: Mapped[Optional[list]] = mapped_column(JSONB)
    dominant_color: Mapped[Optional[str]] = mapped_column(Text)
    is_research_case: Mapped[Optional[bool]] = mapped_column(Boolean)
    routing_hint: Mapped[Optional[str]] = mapped_column(Text)  # routine | research | unknown
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB)


class PasnetValidationResult(Base, TimestampMixin):
    """LIS/PASNet validation outcome — replaces the legacy SQLite ops + log tables."""

    __tablename__ = "pasnet_validation_results"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_pasnet_validation_results_idempotency"
        ),
        Index("ix_pasnet_validation_results_artifact", "global_artifact_id"),
        Index("ix_pasnet_validation_results_case_id", "case_id"),
        Index("ix_pasnet_validation_status", "validation_status"),
        {"schema": "babelshark"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    case_id: Mapped[Optional[str]] = mapped_column(Text)
    slide_id: Mapped[Optional[str]] = mapped_column(Text)
    stain: Mapped[Optional[str]] = mapped_column(Text)
    validation_mode: Mapped[Optional[str]] = mapped_column(Text)       # pre_rename | audit
    validation_status: Mapped[Optional[str]] = mapped_column(Text)     # VALID | INVALID | SKIPPED | ERROR
    reason_summary: Mapped[Optional[str]] = mapped_column(Text)
    pasnet_connection_status: Mapped[Optional[str]] = mapped_column(Text)  # OK | FAILED
    pasnet_case_exists: Mapped[Optional[bool]] = mapped_column(Boolean)
    pasnet_slide_match_type: Mapped[Optional[str]] = mapped_column(Text)
    pasnet_slide_id: Mapped[Optional[str]] = mapped_column(Text)
    pasnet_stain_raw: Mapped[Optional[str]] = mapped_column(Text)
    pasnet_stain_canonical: Mapped[Optional[str]] = mapped_column(Text)
    extracted_slide_id: Mapped[Optional[str]] = mapped_column(Text)
    extracted_stain: Mapped[Optional[str]] = mapped_column(Text)
    extracted_stain_confidence: Mapped[Optional[str]] = mapped_column(Text)
    final_slide_id: Mapped[Optional[str]] = mapped_column(Text)
    final_stain: Mapped[Optional[str]] = mapped_column(Text)
    rename_source: Mapped[Optional[str]] = mapped_column(Text)
    file_action: Mapped[Optional[str]] = mapped_column(Text)  # rename | keep_original | move_to_suspicious | skip
    details_json: Mapped[Optional[dict]] = mapped_column(JSONB)


class SlideRoutingDecision(Base, TimestampMixin):
    """Final routing decision per slide — rename, route type, and resolved slide identity."""

    __tablename__ = "slide_routing_decisions"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_slide_routing_decisions_idempotency"
        ),
        Index("ix_slide_routing_decisions_artifact", "global_artifact_id"),
        Index("ix_slide_routing_type", "routing_type"),
        Index("ix_slide_routing_file_record", "file_record_internal_id"),
        {"schema": "babelshark"},
    )

    internal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    file_record_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.file_records.internal_id", ondelete="SET NULL")
    )
    pipeline_run_internal_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("core.pipeline_runs.internal_id", ondelete="SET NULL")
    )
    global_artifact_id: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(Text)
    original_filename: Mapped[Optional[str]] = mapped_column(Text)
    new_filename: Mapped[Optional[str]] = mapped_column(Text)
    original_path: Mapped[Optional[str]] = mapped_column(Text)
    final_path: Mapped[Optional[str]] = mapped_column(Text)
    routing_type: Mapped[str] = mapped_column(Text, nullable=False)  # routine | research | blacklist | unreadable | duplicate | failed
    routing_reason: Mapped[Optional[str]] = mapped_column(Text)
    case_id: Mapped[Optional[str]] = mapped_column(Text)
    slide_id: Mapped[Optional[str]] = mapped_column(Text)
    stain: Mapped[Optional[str]] = mapped_column(Text)
    lab_id: Mapped[Optional[str]] = mapped_column(Text)
    year: Mapped[Optional[str]] = mapped_column(Text)
    case_number: Mapped[Optional[str]] = mapped_column(Text)
    pot: Mapped[Optional[str]] = mapped_column(Text)
    block_id: Mapped[Optional[str]] = mapped_column(Text)
    section: Mapped[Optional[str]] = mapped_column(Text)
    scanner_id: Mapped[Optional[str]] = mapped_column(Text)
    scanner_model: Mapped[Optional[str]] = mapped_column(Text)
    scanner_vendor: Mapped[Optional[str]] = mapped_column(Text)
    routing_metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB)
