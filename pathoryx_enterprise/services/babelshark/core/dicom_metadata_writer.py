#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dicom_metadata_writer.py

Post-processing step for the Babel-Shark pipeline that writes
final metadata (SlideID, CaseID, AccessionNumber, StudyID, Stain, etc.)
into DICOM headers for DICOM-based WSI slides.

UPDATED to reflect IDS7 expectations from the provided table:

Required (needed=1):
  - (0008,0050) AccessionNumber
  - (0020,0010) StudyID
  - (0040,0512) ContainerIdentifier
  - (2200,0002) LabelText
  - SpecimenDescriptionSequence:
      * (0040,0551) SpecimenIdentifier        e.g. "A-1-6"  (localisation)
      * (0040,0600) SpecimenShortDescription  e.g. "Staining: H&E" (ALWAYS prefix "Staining: ")

Optional (needed=0):
  - (0008,0060) Modality
      Recommendation: use "SM" for WSI/fluo, "DX" for X-ray slide images.

IMPORTANT (PS from email):
  - Never use a placeholder like "xxxx" in any DICOM header.
  - Always store the real slide-id (e.g. "E2024000254SA-1-6-NASDCL") in LabelText/ContainerIdentifier.
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

import yaml
import pandas as pd
import pydicom
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

LOG = logging.getLogger("DicomMetadataWriter")


# ---------------------------------------------------------------------------
# Logging & config helpers
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    """Configure simple console logging."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(message)s",
        handlers=[logging.StreamHandler()],
        force=True,
    )


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load YAML configuration file for this step."""
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_run_output_dir(cfg: Dict[str, Any]) -> Path:
    """Derive the run_output_dir from config (default: ./output)."""
    run_output_dir = cfg.get("run_output_dir", "./output")
    return Path(run_output_dir).expanduser()


def _get_run_timestamp(cfg: Dict[str, Any]) -> Optional[str]:
    """Get the run_timestamp from config (if present)."""
    rt = cfg.get("run_timestamp", None)
    if rt is None:
        return None
    return str(rt)


def _find_metadata_xlsx(cfg: Dict[str, Any]) -> Optional[Path]:
    """
    Find the slide_metadata_*.xlsx for the current run.

    Strategy:
        1) If 'metadata_excel_path' or 'meta_xlsx' is set in cfg and exists, use it.
        2) If 'run_timestamp' is known:
            - Look under run_output_dir/<run_timestamp>/slide_metadata_<run_timestamp>.xlsx
        3) Fallback: newest slide_metadata_*.xlsx under run_output_dir/**.
    """
    for key in ("metadata_excel_path", "meta_xlsx"):
        p = cfg.get(key, "")
        if p:
            candidate = Path(str(p)).expanduser()
            if candidate.exists():
                return candidate

    run_output_dir = _get_run_output_dir(cfg)
    run_ts = _get_run_timestamp(cfg)

    if run_ts is not None:
        candidate = run_output_dir / run_ts / f"slide_metadata_{run_ts}.xlsx"
        if candidate.exists():
            return candidate

    candidates: List[Path] = []
    if run_output_dir.exists():
        for sub in run_output_dir.iterdir():
            if not sub.is_dir():
                continue
            for xlsx in sub.glob("slide_metadata_*.xlsx"):
                candidates.append(xlsx)

    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1]


def _get_writer_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Optional config block:
      dicom_metadata_writer:
        modality: "SM"  # or "DX"
        set_modality_if_missing_only: true
    """
    block = cfg.get("dicom_metadata_writer")
    return block if isinstance(block, dict) else {}


def _get_default_modality(cfg: Dict[str, Any]) -> str:
    """Get default Modality to write (optional)."""
    wcfg = _get_writer_cfg(cfg)
    mod = str(wcfg.get("modality", "SM")).strip().upper()
    if mod not in {"SM", "DX"}:
        mod = "SM"
    return mod


def _set_modality_if_missing_only(cfg: Dict[str, Any]) -> bool:
    """If True, only set Modality when it's missing/empty."""
    wcfg = _get_writer_cfg(cfg)
    return bool(wcfg.get("set_modality_if_missing_only", True))


# ---------------------------------------------------------------------------
# Metadata extraction from DataFrame
# ---------------------------------------------------------------------------

def _choose_first_nonempty(row: pd.Series, candidates: List[str]) -> str:
    """Pick the first non-empty value among candidate column names."""
    for col in candidates:
        if col in row:
            val = row.get(col)
            if isinstance(val, str):
                val = val.strip()
            if val not in (None, "", "nan", "NaN"):
                return str(val)
    return ""


def _derive_specimen_identifier_from_slide_id(slide_id: str) -> str:
    """
    Derive SpecimenIdentifier (localisation) from SlideID.
    Example: E2024000254SA-1-6-NASDCL -> A-1-6

    If parsing fails, return empty string.
    """
    s = (slide_id or "").strip()
    if not s:
        return ""

    # Defensive: strip optional timestamp suffix
    s = re.split(r"_UTC", s, maxsplit=1)[0]

    parts = s.split("-")
    if len(parts) < 3:
        return ""

    first = parts[0]  # e.g. E2024000254SA
    if "S" not in first:
        return ""

    pot = first.rsplit("S", 1)[-1].strip()
    block = parts[1].strip() if len(parts) > 1 else ""
    section = parts[2].strip() if len(parts) > 2 else ""

    if not (pot and block and section):
        return ""

    return f"{pot}-{block}-{section}"


def _extract_fields_from_row(row: pd.Series) -> Dict[str, str]:
    """
    Extract canonical metadata fields from a slide_metadata row.
    """
    slide_id = str(row.get("SlideID") or "").strip()
    case_id = str(row.get("CaseID") or "").strip()

    accession = _choose_first_nonempty(row, ["AccessionNumber", "Accession", "AccessionID", "CaseID"])
    if not accession:
        accession = case_id

    study_id = _choose_first_nonempty(row, ["StudyID", "Study_Id", "Study"])
    if not study_id:
        study_id = case_id

    stain = _choose_first_nonempty(
        row,
        ["Stain", "Staining", "stain_final", "Stain_final", "staining_final", "StainName"],
    )

    # PS requirement: NEVER use placeholders -> store real slide-id here
    label_text = slide_id

    # SpecimenIdentifier (Localization). Prefer explicit column; fallback to parsing SlideID.
    specimen_identifier = _choose_first_nonempty(row, ["SpecimenIdentifier", "SpecimenID", "Localization", "Localisation"])
    if not specimen_identifier:
        specimen_identifier = _derive_specimen_identifier_from_slide_id(slide_id)

    return {
        "slide_id": slide_id,
        "case_id": case_id,
        "accession_number": accession,
        "study_id": study_id,
        "stain": stain,
        "label_text": label_text,
        "specimen_identifier": specimen_identifier,
    }


# ---------------------------------------------------------------------------
# DICOM header update logic
# ---------------------------------------------------------------------------

def _update_single_dicom(
    dcm_path: Path,
    meta: Dict[str, str],
    *,
    modality: str,
    set_modality_if_missing_only: bool,
) -> bool:
    """
    Load a DICOM file, update selected tags, and save it in-place.
    """
    try:
        ds = pydicom.dcmread(str(dcm_path))
    except Exception as exc:
        LOG.warning(f"[WARN] Could not read DICOM: {dcm_path} -> {exc}")
        return False

    slide_id = meta["slide_id"]
    accession_number = meta["accession_number"]
    study_id = meta["study_id"]
    stain = meta["stain"]
    label_text = meta["label_text"]
    specimen_identifier = (meta.get("specimen_identifier") or "").strip()

    # (0008,0060) Modality (optional)
    try:
        if set_modality_if_missing_only:
            cur = str(getattr(ds, "Modality", "") or "").strip()
            if not cur:
                ds.Modality = modality
        else:
            ds.Modality = modality
    except Exception:
        pass

    # (2200,0002) LabelText (UT) -- ALWAYS real slide-id (no placeholders)
    try:
        ds.add_new((0x2200, 0x0002), "UT", label_text)
    except Exception:
        try:
            ds[0x22000002].value = label_text  # type: ignore[index]
        except Exception:
            pass

    # (0040,0512) ContainerIdentifier -- typically set to SlideID
    try:
        ds.ContainerIdentifier = slide_id
    except Exception:
        pass

    # (0008,0050) AccessionNumber
    if accession_number:
        try:
            ds.AccessionNumber = accession_number
        except Exception:
            pass

    # (0020,0010) StudyID
    if study_id:
        try:
            ds.StudyID = study_id
        except Exception:
            pass

    # SpecimenDescriptionSequence:
    #   (0040,0551) SpecimenIdentifier        e.g. "A-1-6"
    #   (0040,0600) SpecimenShortDescription  e.g. "Staining: H&E"  (ALWAYS prefix "Staining: ")
    try:
        specimen_item = Dataset()

        if specimen_identifier:
            specimen_item.SpecimenIdentifier = specimen_identifier  # (0040,0551)

        stain_value = stain.strip() if isinstance(stain, str) else ""
        if not stain_value:
            stain_value = "unknown"

        specimen_item.SpecimenShortDescription = f"Staining: {stain_value}"  # (0040,0600)

        ds.SpecimenDescriptionSequence = Sequence([specimen_item])
    except Exception:
        LOG.debug(f"[DEBUG] Failed to set SpecimenDescriptionSequence for {dcm_path}")

    try:
        ds.save_as(str(dcm_path))
        return True
    except Exception as exc:
        LOG.warning(f"[WARN] Could not save DICOM: {dcm_path} -> {exc}")
        return False


def _update_dicom_folder(folder: Path, meta: Dict[str, str], *, modality: str, set_modality_if_missing_only: bool) -> int:
    """Apply metadata updates to all '*.dcm' files in the given folder."""
    count = 0
    if not folder.exists() or not folder.is_dir():
        LOG.warning(f"[WARN] DICOM folder does not exist or is not a directory: {folder}")
        return 0

    dcm_files = sorted(folder.glob("*.dcm"))
    if not dcm_files:
        LOG.warning(f"[WARN] No .dcm files found in folder: {folder}")
        return 0

    LOG.info(f"[INFO] Updating {len(dcm_files)} DICOM file(s) in {folder} for SlideID={meta['slide_id']}")

    for dcm_path in dcm_files:
        if _update_single_dicom(
            dcm_path,
            meta,
            modality=modality,
            set_modality_if_missing_only=set_modality_if_missing_only,
        ):
            count += 1

    return count


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def run_writer(config_path: Path, log_level: str) -> None:
    """Main entrypoint for the DICOM metadata writer."""
    setup_logging(log_level)

    try:
        cfg = load_config(config_path)
    except Exception as exc:
        LOG.error(f"[FAIL] Cannot read config: {exc}")
        return

    run_output_dir = _get_run_output_dir(cfg)
    LOG.info(f"[INFO] run_output_dir = {run_output_dir}")

    meta_xlsx = _find_metadata_xlsx(cfg)
    if meta_xlsx is None or not meta_xlsx.exists():
        LOG.error("[FAIL] Could not locate slide_metadata_*.xlsx for this run.")
        return

    LOG.info(f"[INFO] Using slide metadata file: {meta_xlsx}")

    try:
        df = pd.read_excel(meta_xlsx, sheet_name="full_merged")
    except Exception as exc:
        LOG.error(f"[FAIL] Could not read 'full_merged' sheet from {meta_xlsx}: {exc}")
        return

    if "status" not in df.columns:
        LOG.error(f"[FAIL] 'status' column not found in {meta_xlsx}. Nothing to do.")
        return

    df_dicom = df[df["status"].isin(["dicom_renamed", "dicom_only"])].copy()
    if df_dicom.empty:
        LOG.info("[INFO] No DICOM slides found. Nothing to update.")
        return

    modality = _get_default_modality(cfg)
    set_if_missing_only = _set_modality_if_missing_only(cfg)

    LOG.info(f"[INFO] Found {len(df_dicom)} DICOM slide row(s) to process.")
    LOG.info(f"[INFO] Modality policy: modality={modality} | set_if_missing_only={set_if_missing_only}")

    total_files_updated = 0
    total_folders = 0

    for _, row in df_dicom.iterrows():
        meta = _extract_fields_from_row(row)
        slide_id = meta["slide_id"]

        input_path_str = str(row.get("InputPath") or "").strip()
        output_path_str = str(row.get("OutputPath") or "").strip()

        folder_str = output_path_str or input_path_str
        if not folder_str:
            LOG.warning(f"[WARN] Missing InputPath/OutputPath for DICOM slide {slide_id}; skipping.")
            continue

        folder = Path(folder_str).expanduser()
        if not folder.exists() or not folder.is_dir():
            LOG.warning(f"[WARN] DICOM folder does not exist or is not a directory: {folder} (SlideID={slide_id})")
            continue

        updated_here = _update_dicom_folder(folder, meta, modality=modality, set_modality_if_missing_only=set_if_missing_only)
        if updated_here > 0:
            total_files_updated += updated_here
            total_folders += 1

    LOG.info(f"[SUMMARY] Updated {total_files_updated} DICOM file(s) across {total_folders} folder(s).")
    LOG.info("[DONE] dicom_metadata_writer finished.")


def validate_config(config_path: Path, log_level: str) -> int:
    """Basic config validation."""
    setup_logging(log_level)
    try:
        _ = load_config(config_path)
    except Exception as exc:
        LOG.error(f"[FAIL] Cannot read config: {exc}")
        return 1
    LOG.info("[OK] Config validation passed for dicom_metadata_writer.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write final metadata into DICOM headers for DICOM-based WSI slides."
    )
    parser.set_defaults(func=None)

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_run = subparsers.add_parser("run", help="Run DICOM metadata writer.")
    p_run.add_argument("--config", type=Path, required=True, help="Path to YAML configuration file (temp_config.yaml).")
    p_run.set_defaults(func=lambda ns: run_writer(ns.config, ns.log_level))

    p_val = subparsers.add_parser("validate", help="Validate config for DICOM metadata writer.")
    p_val.add_argument("--config", type=Path, required=True, help="Path to YAML configuration file.")
    p_val.set_defaults(func=lambda ns: exit(validate_config(ns.config, ns.log_level)))

    p_ver = subparsers.add_parser("version", help="Show module version.")
    p_ver.set_defaults(func=lambda ns: (setup_logging(ns.log_level), LOG.info("dicom_metadata_writer 0.2.0")))

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if hasattr(args, "func") and args.func is not None:
        args.func(args) if callable(args.func) else None
    else:
        parser.print_help()


if __name__ == "__main__":
    main()