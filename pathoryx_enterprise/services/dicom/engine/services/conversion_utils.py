"""DICOM input classification and filesystem helpers.

Ported from dicom_delivery_adapter/pipeline/services/conversion_utils.py.
Changes: imports rewritten to native engine paths.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pydicom
from pydicom.errors import InvalidDicomError

from pathoryx_enterprise.services.dicom.engine.domain.enums import InputKind
from pathoryx_enterprise.services.dicom.engine.domain.results import InputClassificationResult


DICOM_EXTENSIONS = {".dcm", ".dicom"}


def is_dicom_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() in DICOM_EXTENSIONS:
        return True
    try:
        pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
        return True
    except (InvalidDicomError, FileNotFoundError, PermissionError, IsADirectoryError):
        return False


def find_dicom_files_in_directory(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    dicom_files: list[Path] = []
    for child in directory.rglob("*"):
        if child.name.upper() == "DICOMDIR":
            dicom_files.append(child)
            continue
        if child.is_file() and is_dicom_file(child):
            dicom_files.append(child)
    return dicom_files


def classify_input_as_dicom_or_not(source_path: str | Path) -> InputClassificationResult:
    path = Path(source_path).resolve()
    if not path.exists():
        return InputClassificationResult(
            source_path=path,
            exists=False,
            input_kind=InputKind.missing,
            was_already_dicom=False,
            reason="source path does not exist",
        )

    if path.is_file():
        if is_dicom_file(path):
            return InputClassificationResult(
                source_path=path,
                exists=True,
                input_kind=InputKind.dicom_file,
                was_already_dicom=True,
                reason="source is a DICOM file",
                detected_dicom_files=[path],
            )
        return InputClassificationResult(
            source_path=path,
            exists=True,
            input_kind=InputKind.non_dicom_file,
            was_already_dicom=False,
            reason="source file is not DICOM",
        )

    dicom_files = find_dicom_files_in_directory(path)
    if dicom_files:
        return InputClassificationResult(
            source_path=path,
            exists=True,
            input_kind=InputKind.dicom_directory,
            was_already_dicom=True,
            reason="source directory contains DICOM data",
            detected_dicom_files=dicom_files,
        )

    return InputClassificationResult(
        source_path=path,
        exists=True,
        input_kind=InputKind.non_dicom_directory,
        was_already_dicom=False,
        reason="source directory does not contain DICOM data",
    )


def compute_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_output_folder(
    output_root: Path,
    source_path: Path,
    slide_id: str | None,
    checksum: str | None,
) -> Path:
    normalized_name = source_path.stem.replace(" ", "_")
    key = slide_id or (checksum[:12] if checksum else normalized_name)
    return output_root / key / normalized_name
