"""DICOM engine domain result types — ported from dicom_delivery_adapter pipeline/domain/results.py.

Changes from original:
  - imports rewritten from pipeline.domain.enums to native engine path
  - ConversionResult gains input_file_size field (runner accessed it via getattr)
  - global_artifact_id type is str | None (not uuid.UUID) — enterprise uses string IDs
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pathoryx_enterprise.services.dicom.engine.domain.enums import (
    ConversionStatus,
    InputKind,
    UploadStatus,
)


@dataclass(slots=True)
class InputClassificationResult:
    source_path: Path
    exists: bool
    input_kind: InputKind
    was_already_dicom: bool
    reason: str
    detected_dicom_files: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class ConversionResult:
    status: ConversionStatus
    source_path: Path
    input_kind: InputKind
    was_already_dicom: bool
    conversion_required: bool
    output_path: Path | None
    output_format: str | None
    duration_seconds: float
    metadata_summary: dict[str, Any] = field(default_factory=dict)
    input_metadata_json: dict[str, Any] = field(default_factory=dict)
    output_metadata_json: dict[str, Any] = field(default_factory=dict)
    failure_context: dict[str, Any] | None = None
    conversion_tool: str | None = None
    conversion_tool_version: str | None = None
    # Both size fields available; runner accesses both via getattr
    input_file_size: int | None = None
    output_file_size: int | None = None
    # Preserve lineage: accept string ID from trigger — never generate a new UUID here
    global_artifact_id: str | None = None
    final_outcome: str | None = None


@dataclass(slots=True)
class UploadResult:
    status: UploadStatus
    source_path: Path
    upload_input_path: Path
    target_system: str
    target_endpoint: str
    duration_seconds: float
    retry_count: int = 0
    remote_identifier: str | None = None
    response_summary: dict[str, Any] = field(default_factory=dict)
    failure_context: dict[str, Any] | None = None
    input_metadata_json: dict[str, Any] = field(default_factory=dict)
    output_metadata_json: dict[str, Any] = field(default_factory=dict)
    final_outcome: str | None = None
