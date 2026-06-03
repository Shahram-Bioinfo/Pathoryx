"""
BabelShark per-stage DB writer.

Each pipeline stage calls one method here at the moment it produces metadata.
All writes go to PostgreSQL via the shared session — no SQLite, no Excel as
a persistence intermediary.

USAGE (from stage_runner or directly from a refactored stage module)::

    from pathoryx_enterprise.services.babelshark.stage_db_writer import (
        BabelSharkStageDBWriter,
    )

    with get_session() as session:
        writer = BabelSharkStageDBWriter(session)
        writer.write_datamatrix_result(
            file_record_internal_id=record_id,
            global_artifact_id=artifact_id,
            pipeline_run_internal_id=run_id,
            correlation_id=correlation_id,
            label_filename="label_000.png",
            datamatrix_raw="E2025054283SA-1-2-HE",
            lab_id="E", year="2025", case_number="054283",
            pot="SA", block_id="1", section="2",
            decode_status="success",
        )

All write methods are idempotent: they upsert on the idempotency_key so that
re-running a stage after a transient failure produces the same row rather than
a duplicate.

Idempotency key construction:
  - datamatrix : "dm:<global_artifact_id>:<label_filename>"
  - stain      : "stain:<global_artifact_id>:<label_filename>"
  - roi        : "roi:<global_artifact_id>:<label_filename>"
  - color      : "color:<global_artifact_id>:<label_filename>"
  - pasnet     : "pasnet:<global_artifact_id>"
  - routing    : "routing:<global_artifact_id>"
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.babelshark import (
    ColorMarkerResult,
    DatamatrixResult,
    PasnetValidationResult,
    RoiResult,
    SlideRoutingDecision,
    StainResult,
)
from pathoryx_enterprise.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)


def _ikey(*parts: Any) -> str:
    """Build a short deterministic idempotency key from arbitrary parts."""
    raw = ":".join(str(p) for p in parts)
    if len(raw) <= 200:
        return raw
    # Truncate + hash suffix to stay within the Text column limit
    return raw[:160] + "_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _upsert(session: Session, model_class, idempotency_key: str, defaults: Dict[str, Any]):
    """
    Upsert a row by idempotency_key.

    On first call: INSERT with all columns from *defaults*.
    On subsequent calls: UPDATE the non-key columns so the row reflects
    the most recent stage output (useful for stage retries).
    """
    existing = session.execute(
        select(model_class).where(model_class.idempotency_key == idempotency_key)
    ).scalar_one_or_none()

    if existing is None:
        obj = model_class(idempotency_key=idempotency_key, **defaults)
        session.add(obj)
        session.flush()
        return obj

    for k, v in defaults.items():
        setattr(existing, k, v)
    session.flush()
    return existing


class BabelSharkStageDBWriter:
    """
    Per-session, stateless writer for BabelShark stage result tables.

    Instantiate once per session; the session must be managed by the caller
    (i.e., inside a ``with get_session() as session:`` block).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # DataMatrix stage
    # ------------------------------------------------------------------

    def write_datamatrix_result(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        label_filename: str,
        datamatrix_raw: Optional[str] = None,
        lab_id: Optional[str] = None,
        year: Optional[str] = None,
        case_number: Optional[str] = None,
        pot: Optional[str] = None,
        block_id: Optional[str] = None,
        section: Optional[str] = None,
        decode_status: str = "failed",
        decode_attempt_count: Optional[int] = None,
        error_reason: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
    ) -> DatamatrixResult:
        key = _ikey("dm", global_artifact_id, label_filename)
        return _upsert(
            self._session,
            DatamatrixResult,
            key,
            {
                "file_record_internal_id": file_record_internal_id,
                "global_artifact_id": global_artifact_id,
                "pipeline_run_internal_id": pipeline_run_internal_id,
                "correlation_id": correlation_id,
                "label_filename": label_filename,
                "datamatrix_raw": datamatrix_raw,
                "lab_id": lab_id,
                "year": year,
                "case_number": case_number,
                "pot": pot,
                "block_id": block_id,
                "section": section,
                "decode_status": decode_status,
                "decode_attempt_count": decode_attempt_count,
                "error_reason": error_reason,
                "raw_payload": raw_payload or {},
            },
        )

    def write_datamatrix_results_batch(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        rows: List[Dict[str, Any]],
    ) -> List[DatamatrixResult]:
        """Write one DatamatrixResult row per label image in a single session flush."""
        results = []
        for row in rows:
            results.append(
                self.write_datamatrix_result(
                    file_record_internal_id=file_record_internal_id,
                    global_artifact_id=global_artifact_id,
                    pipeline_run_internal_id=pipeline_run_internal_id,
                    correlation_id=correlation_id,
                    label_filename=str(row.get("FileName", "")),
                    datamatrix_raw=row.get("DataMatrix"),
                    lab_id=row.get("LabID"),
                    year=row.get("Year"),
                    case_number=row.get("CaseNumber"),
                    pot=row.get("Pot"),
                    block_id=row.get("BlockID"),
                    section=row.get("Section"),
                    decode_status=str(row.get("Status", "failed")).lower(),
                    raw_payload=row,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Stain extraction stage
    # ------------------------------------------------------------------

    def write_stain_result(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        label_filename: str,
        raw_ocr_words: Optional[str] = None,
        cleaned_words: Optional[str] = None,
        matched_word: Optional[str] = None,
        stain_initial: Optional[str] = None,
        stain_roi_double_check: Optional[str] = None,
        stain_final: Optional[str] = None,
        stain_origin: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
    ) -> StainResult:
        key = _ikey("stain", global_artifact_id, label_filename)
        return _upsert(
            self._session,
            StainResult,
            key,
            {
                "file_record_internal_id": file_record_internal_id,
                "global_artifact_id": global_artifact_id,
                "pipeline_run_internal_id": pipeline_run_internal_id,
                "correlation_id": correlation_id,
                "label_filename": label_filename,
                "raw_ocr_words": raw_ocr_words,
                "cleaned_words": cleaned_words,
                "matched_word": matched_word,
                "stain_initial": stain_initial,
                "stain_roi_double_check": stain_roi_double_check,
                "stain_final": stain_final,
                "stain_origin": stain_origin,
                "raw_payload": raw_payload or {},
            },
        )

    def write_stain_results_batch(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        rows: List[Dict[str, Any]],
    ) -> List[StainResult]:
        results = []
        for row in rows:
            results.append(
                self.write_stain_result(
                    file_record_internal_id=file_record_internal_id,
                    global_artifact_id=global_artifact_id,
                    pipeline_run_internal_id=pipeline_run_internal_id,
                    correlation_id=correlation_id,
                    label_filename=str(row.get("FileName", "")),
                    raw_ocr_words=row.get("Raw_OCR_Words"),
                    cleaned_words=row.get("Cleaned_Words"),
                    matched_word=row.get("Matched_Word"),
                    stain_initial=row.get("Stain_Initial"),
                    stain_roi_double_check=row.get("Stain_ROI_DoubleCheck"),
                    stain_final=row.get("Stain"),
                    stain_origin=row.get("Stain_Origin"),
                    raw_payload=row,
                )
            )
        return results

    # ------------------------------------------------------------------
    # ROI metadata extraction stage
    # ------------------------------------------------------------------

    def write_roi_result(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        label_filename: str,
        stain: Optional[str] = None,
        datamatrix: Optional[str] = None,
        lab_id: Optional[str] = None,
        year: Optional[str] = None,
        case_number: Optional[str] = None,
        pot: Optional[str] = None,
        block_id: Optional[str] = None,
        section: Optional[str] = None,
        extraction_status: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
    ) -> RoiResult:
        key = _ikey("roi", global_artifact_id, label_filename)
        return _upsert(
            self._session,
            RoiResult,
            key,
            {
                "file_record_internal_id": file_record_internal_id,
                "global_artifact_id": global_artifact_id,
                "pipeline_run_internal_id": pipeline_run_internal_id,
                "correlation_id": correlation_id,
                "label_filename": label_filename,
                "stain": stain,
                "datamatrix": datamatrix,
                "lab_id": lab_id,
                "year": year,
                "case_number": case_number,
                "pot": pot,
                "block_id": block_id,
                "section": section,
                "extraction_status": extraction_status,
                "raw_payload": raw_payload or {},
            },
        )

    def write_roi_results_batch(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        rows: List[Dict[str, Any]],
    ) -> List[RoiResult]:
        results = []
        for row in rows:
            results.append(
                self.write_roi_result(
                    file_record_internal_id=file_record_internal_id,
                    global_artifact_id=global_artifact_id,
                    pipeline_run_internal_id=pipeline_run_internal_id,
                    correlation_id=correlation_id,
                    label_filename=str(row.get("FileName", "")),
                    stain=row.get("Stain"),
                    datamatrix=row.get("DataMatrix"),
                    lab_id=row.get("LabID"),
                    year=row.get("Year"),
                    case_number=row.get("CaseNumber"),
                    pot=row.get("Pot"),
                    block_id=row.get("BlockID"),
                    section=row.get("Section"),
                    extraction_status=row.get("Status"),
                    raw_payload=row,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Color marker detection stage
    # ------------------------------------------------------------------

    def write_color_marker_result(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        label_filename: str,
        detected_colors: Optional[List[str]] = None,
        dominant_color: Optional[str] = None,
        is_research_case: Optional[bool] = None,
        routing_hint: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
    ) -> ColorMarkerResult:
        key = _ikey("color", global_artifact_id, label_filename)
        return _upsert(
            self._session,
            ColorMarkerResult,
            key,
            {
                "file_record_internal_id": file_record_internal_id,
                "global_artifact_id": global_artifact_id,
                "pipeline_run_internal_id": pipeline_run_internal_id,
                "correlation_id": correlation_id,
                "label_filename": label_filename,
                "detected_colors": detected_colors or [],
                "dominant_color": dominant_color,
                "is_research_case": is_research_case,
                "routing_hint": routing_hint,
                "raw_payload": raw_payload or {},
            },
        )

    def write_color_marker_results_batch(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        rows: List[Dict[str, Any]],
    ) -> List[ColorMarkerResult]:
        results = []
        for row in rows:
            results.append(
                self.write_color_marker_result(
                    file_record_internal_id=file_record_internal_id,
                    global_artifact_id=global_artifact_id,
                    pipeline_run_internal_id=pipeline_run_internal_id,
                    correlation_id=correlation_id,
                    label_filename=str(row.get("FileName", "")),
                    detected_colors=row.get("DetectedColors"),
                    dominant_color=row.get("DominantColor"),
                    is_research_case=row.get("IsResearch"),
                    routing_hint=row.get("RoutingHint"),
                    raw_payload=row,
                )
            )
        return results

    # ------------------------------------------------------------------
    # PASNet validation stage
    # ------------------------------------------------------------------

    def write_pasnet_validation_result(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        case_id: Optional[str] = None,
        slide_id: Optional[str] = None,
        stain: Optional[str] = None,
        validation_mode: Optional[str] = None,
        validation_status: Optional[str] = None,
        reason_summary: Optional[str] = None,
        pasnet_connection_status: Optional[str] = None,
        pasnet_case_exists: Optional[bool] = None,
        pasnet_slide_match_type: Optional[str] = None,
        pasnet_slide_id: Optional[str] = None,
        pasnet_stain_raw: Optional[str] = None,
        pasnet_stain_canonical: Optional[str] = None,
        extracted_slide_id: Optional[str] = None,
        extracted_stain: Optional[str] = None,
        extracted_stain_confidence: Optional[str] = None,
        final_slide_id: Optional[str] = None,
        final_stain: Optional[str] = None,
        rename_source: Optional[str] = None,
        file_action: Optional[str] = None,
        details_json: Optional[Dict[str, Any]] = None,
    ) -> PasnetValidationResult:
        key = _ikey("pasnet", global_artifact_id)
        return _upsert(
            self._session,
            PasnetValidationResult,
            key,
            {
                "file_record_internal_id": file_record_internal_id,
                "global_artifact_id": global_artifact_id,
                "pipeline_run_internal_id": pipeline_run_internal_id,
                "correlation_id": correlation_id,
                "case_id": case_id,
                "slide_id": slide_id,
                "stain": stain,
                "validation_mode": validation_mode,
                "validation_status": validation_status,
                "reason_summary": reason_summary,
                "pasnet_connection_status": pasnet_connection_status,
                "pasnet_case_exists": pasnet_case_exists,
                "pasnet_slide_match_type": pasnet_slide_match_type,
                "pasnet_slide_id": pasnet_slide_id,
                "pasnet_stain_raw": pasnet_stain_raw,
                "pasnet_stain_canonical": pasnet_stain_canonical,
                "extracted_slide_id": extracted_slide_id,
                "extracted_stain": extracted_stain,
                "extracted_stain_confidence": extracted_stain_confidence,
                "final_slide_id": final_slide_id,
                "final_stain": final_stain,
                "rename_source": rename_source,
                "file_action": file_action,
                "details_json": details_json or {},
            },
        )

    # ------------------------------------------------------------------
    # Slide routing / ID generation stage
    # ------------------------------------------------------------------

    def write_slide_routing_decision(
        self,
        *,
        file_record_internal_id: Optional[int],
        global_artifact_id: str,
        pipeline_run_internal_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        original_filename: Optional[str] = None,
        new_filename: Optional[str] = None,
        original_path: Optional[str] = None,
        final_path: Optional[str] = None,
        routing_type: str,
        routing_reason: Optional[str] = None,
        case_id: Optional[str] = None,
        slide_id: Optional[str] = None,
        stain: Optional[str] = None,
        lab_id: Optional[str] = None,
        year: Optional[str] = None,
        case_number: Optional[str] = None,
        pot: Optional[str] = None,
        block_id: Optional[str] = None,
        section: Optional[str] = None,
        scanner_id: Optional[str] = None,
        scanner_model: Optional[str] = None,
        scanner_vendor: Optional[str] = None,
        routing_metadata_json: Optional[Dict[str, Any]] = None,
    ) -> SlideRoutingDecision:
        key = _ikey("routing", global_artifact_id)
        return _upsert(
            self._session,
            SlideRoutingDecision,
            key,
            {
                "file_record_internal_id": file_record_internal_id,
                "global_artifact_id": global_artifact_id,
                "pipeline_run_internal_id": pipeline_run_internal_id,
                "correlation_id": correlation_id,
                "original_filename": original_filename,
                "new_filename": new_filename,
                "original_path": original_path,
                "final_path": final_path,
                "routing_type": routing_type,
                "routing_reason": routing_reason,
                "case_id": case_id,
                "slide_id": slide_id,
                "stain": stain,
                "lab_id": lab_id,
                "year": year,
                "case_number": case_number,
                "pot": pot,
                "block_id": block_id,
                "section": section,
                "scanner_id": scanner_id,
                "scanner_model": scanner_model,
                "scanner_vendor": scanner_vendor,
                "routing_metadata_json": routing_metadata_json or {},
            },
        )
