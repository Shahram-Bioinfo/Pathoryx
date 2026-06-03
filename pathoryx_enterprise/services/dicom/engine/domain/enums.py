"""DICOM engine domain enums — ported from dicom_delivery_adapter pipeline/domain/enums.py."""
from __future__ import annotations

import enum


class InputKind(str, enum.Enum):
    dicom_file = "dicom_file"
    dicom_directory = "dicom_directory"
    non_dicom_file = "non_dicom_file"
    non_dicom_directory = "non_dicom_directory"
    missing = "missing"


class ConversionStatus(str, enum.Enum):
    pending = "pending"
    started = "started"
    skipped_already_dicom = "skipped_already_dicom"
    completed = "completed"
    failed = "failed"


class UploadStatus(str, enum.Enum):
    pending = "pending"
    started = "started"
    retrying = "retrying"
    completed = "completed"
    failed = "failed"
    skipped_already_uploaded = "skipped_already_uploaded"


class StepStatus(str, enum.Enum):
    pending = "pending"
    started = "started"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
