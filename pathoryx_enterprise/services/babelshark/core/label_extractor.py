#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
label_extractor.py — Babel-Shark Label Region Extractor

Extracts label or macro regions from WSI and PNG files, preparing
cropped label images for downstream OCR and barcode decoding.

Main features:
    Detects label and macro associated images in WSIs
    Crops, rotates, and saves label regions atomically as PNG
    Logs unsupported or failed cases to a text file
    Compatible with OpenSlide and Pillow
    Command-line interface for run, validate, and version

Usage:
    PYTHONPATH=./src/babel_shark/ python -u src/babel_shark/label_extractor.py run \
        --config ./config/config.yaml
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import platform
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List

import yaml
from PIL import Image

# pydicom and numpy are only needed for DICOM-folder support
# (added in STEP 1 of the DICOM integration work).
import pydicom
import numpy as np

__VERSION__ = "1.1.0"  # simple semantic version for CLI "version" command


# --------------------------------------------------------------------------- #
# Logging Helpers
# --------------------------------------------------------------------------- #

def setup_logging(level: str = "INFO") -> None:
    """Configure root logging to emulate previous console prints.

    The formatter prints only the message to keep external console output
    visually consistent with prior print(...) usage.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


LOGGER = logging.getLogger("LabelRegionExtractor")
_OPENSLIDE_DLL_HANDLE = None


# --------------------------------------------------------------------------- #
# Data Classes
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Paths:
    """Bundle of key filesystem paths derived from the config."""
    input_dir: Path
    output_dir: Path
    output_run_dir: Path
    log_file_path: Path


# --------------------------------------------------------------------------- #
# Constants (WSI & DICOM detection)
# --------------------------------------------------------------------------- #

# Extensions considered as classical WSI files (handled via OpenSlide)
WSI_EXTS: tuple[str, ...] = (
    ".svs", ".ndpi", ".mrxs", ".scn", ".bif", ".tif", ".tiff"
)

# Extensions considered as DICOM files for WSI-DICOM slides
DICOM_EXTS: tuple[str, ...] = (".dcm", ".dicom")


# --------------------------------------------------------------------------- #
# Utils
# --------------------------------------------------------------------------- #

def load_config(config_path: Path) -> Dict[str, Any]:
    """Load YAML configuration from the provided path."""
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def prepare_windows_dlls(openslide_dll_path: Optional[str]) -> None:
    """Ensure OpenSlide DLLs are available on Windows (no-op elsewhere)."""
    global _OPENSLIDE_DLL_HANDLE

    if platform.system() != "Windows" or not openslide_dll_path:
        return

    dll_dir = Path(openslide_dll_path).expanduser().resolve(strict=False)
    if not dll_dir.exists():
        LOGGER.warning("[WARNING] OpenSlide DLL directory does not exist: %s", dll_dir)
        return

    os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")

    if hasattr(os, "add_dll_directory"):
        try:
            _OPENSLIDE_DLL_HANDLE = os.add_dll_directory(str(dll_dir))
        except Exception as exc:
            LOGGER.warning("[WARNING] Could not add OpenSlide DLL directory: %s", exc)

    candidates = ["libopenslide-1.dll", "libopenslide-0.dll"]
    for name in candidates:
        dll_file = dll_dir / name
        if dll_file.exists():
            try:
                ctypes.cdll.LoadLibrary(str(dll_file))
                return
            except Exception as exc:
                LOGGER.warning("[WARNING] Could not preload %s: %s", dll_file, exc)

    LOGGER.warning("[WARNING] No libopenslide DLL found in: %s", dll_dir)

def atomic_save_pil_image(img: Image.Image, final_path: Path) -> None:
    """Save a PIL image atomically to final_path using os.replace().

    We pass the image format explicitly because the temporary file ends with
    '.tmp' and Pillow cannot infer the format from that extension.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)

    # Keep original filename + ".tmp" (e.g., "x.png.tmp") so we can os.replace later
    tmp_path = final_path.with_name(final_path.name + ".tmp")

    # Infer PIL format from the final extension; fallback to PNG (our output is PNG)
    # Example: ".png" -> "PNG"
    ext_to_fmt = Image.registered_extensions()
    pil_fmt = ext_to_fmt.get(final_path.suffix.lower(), "PNG")

    # Explicitly pass format, otherwise Pillow tries to infer from '.tmp' and fails
    img.save(tmp_path, format=pil_fmt)

    # Atomic replace to the final target path
    os.replace(tmp_path, final_path)


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #

# --- Delayed import of openslide (Windows-safe) ------------------------------
# We import openslide only AFTER DLL preparation has been attempted.
openslide = None  # will be set by _load_openslide_after_dll_prep()


def _load_openslide_after_dll_prep():
    """Import openslide only after DLL paths are prepared (Windows-safe)."""
    try:
        import openslide as _openslide  # type: ignore
        return _openslide
    except Exception as exc:  # ImportError or other
        setup_logging("INFO")
        LOGGER.error("[FATAL] Cannot import openslide. Make sure it's installed: %s", exc)
        sys.exit(1)


class LabelExtractor:
    """Extract label or macro regions from WSI, PNG files, and DICOM folders."""

    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        log_file_path: Path,
        label_crop_ratio: float = 0.3,
        rotation_degrees: int = -90,
        macro_tag: str = "macro",
        rotate_associated_label: bool = False,
        label_rotation_degrees_label: int = 0,
    ) -> None:
        """Initialize extractor with I/O paths and options."""
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.log_file_path = log_file_path
        self.label_crop_ratio = float(label_crop_ratio)
        self.rotation_degrees = int(rotation_degrees)
        self.macro_tag = (macro_tag or "macro").lower()
        self.rotate_associated_label = bool(rotate_associated_label)
        self.label_rotation_degrees_label = int(label_rotation_degrees_label)

    # ------------------------------------------------------------------ #
    # Common logging helper
    # ------------------------------------------------------------------ #
    def log_failure(self, message: str) -> None:
        """Record failures to console and append to log file."""
        LOGGER.error("[FAIL] %s", message)
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file_path.open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now().isoformat()}  {message}\n")

    # ------------------------------------------------------------------ #
    # WSI helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def is_supported_wsi(filepath: Path) -> bool:
        """Return True if file can be opened by OpenSlide."""
        try:
            slide = openslide.OpenSlide(str(filepath))  # uses delayed-imported module
            slide.close()
            return True
        except Exception:
            return False

    @staticmethod
    def generate_output_name(file_path: Path) -> str:
        """Return standardized output filename with .png extension."""
        return file_path.stem + ".png"

    # ------------------------------------------------------------------ #
    # DICOM folder helpers  (STEP 1: basic support)
    # ------------------------------------------------------------------ #
    def _is_dicom_folder(self, path: Path) -> bool:
        """
        Return True if `path` looks like a DICOM WSI folder.

        Heuristic (kept deliberately simple in STEP 1):
          - path is a directory
          - contains at least one *.dcm / *.dicom file
          - does NOT contain classical WSI files (.svs, .ndpi, ...)
        """
        if not path.is_dir():
            return False

        has_dicom = False
        has_wsi = False

        for child in path.iterdir():
            if not child.is_file():
                continue
            suffix = child.suffix.lower()
            if suffix in DICOM_EXTS:
                has_dicom = True
            if suffix in WSI_EXTS:
                has_wsi = True

        # Treat it as a DICOM folder only if at least one DICOM is present
        # and no classical WSI file is present.
        return has_dicom and not has_wsi

    def _pick_representative_dicom(self, folder: Path) -> Optional[Path]:
        """
        Select the best DICOM file for label extraction.

        This keeps the rest of the DICOM pipeline unchanged, but replaces the
        old "first file in sorted order" behavior with metadata-based selection:
          1) prefer files explicitly marked as LABEL
          2) then prefer OVERVIEW / MACRO-like files
          3) only then fall back to the first readable pixel DICOM

        The scan is recursive because many WSI-DICOM exports store instances
        inside nested series folders.
        """
        dicom_files: List[Path] = [
            child
            for child in folder.rglob("*")
            if child.is_file() and child.suffix.lower() in DICOM_EXTS
        ]

        if not dicom_files:
            return None

        dicom_files.sort()

        def _text_values(value: Any) -> str:
            """Return a normalized searchable string for DICOM scalar/list values."""
            if value is None:
                return ""
            if isinstance(value, (list, tuple)):
                return " ".join(str(v) for v in value).upper()
            return str(value).upper()

        scored_files: List[tuple[int, Path]] = []
        fallback_readable: Optional[Path] = None

        for dicom_file in dicom_files:
            try:
                # stop_before_pixels keeps this fast and avoids decoding large WSI tiles
                # while we only need metadata for choosing the label instance.
                ds = pydicom.dcmread(str(dicom_file), stop_before_pixels=True, force=True)
            except Exception:
                continue

            image_type = _text_values(getattr(ds, "ImageType", ""))
            series_description = _text_values(getattr(ds, "SeriesDescription", ""))
            acquisition_context = _text_values(getattr(ds, "AcquisitionContextSequence", ""))
            specimen_description = _text_values(getattr(ds, "SpecimenDescriptionSequence", ""))
            combined = " ".join([
                image_type,
                series_description,
                acquisition_context,
                specimen_description,
                dicom_file.name.upper(),
                dicom_file.parent.name.upper(),
            ])

            # Check whether the file probably has pixels before using it as fallback.
            # We do not require PixelData for LABEL scoring because compressed/bulk
            # transfer syntaxes can still decode later via ds.pixel_array.
            has_pixel_data = "PixelData" in ds
            if fallback_readable is None and has_pixel_data:
                fallback_readable = dicom_file

            score = 0

            # DICOM VL Whole Slide Microscopy commonly uses LABEL / OVERVIEW
            # in ImageType. Label must win over overview/macro.
            if "LABEL" in combined:
                score += 1000
            if "OVERVIEW" in combined:
                score += 500
            if "MACRO" in combined:
                score += 400

            # Prefer smaller associated images over huge tiled volume instances
            # when metadata is otherwise similar.
            try:
                rows = int(getattr(ds, "Rows", 0) or 0)
                cols = int(getattr(ds, "Columns", 0) or 0)
                total_pixels = rows * cols
                if 0 < total_pixels <= 25_000_000:
                    score += 25
            except Exception:
                pass

            # Avoid main tissue pyramid/volume tiles if possible.
            if any(token in combined for token in ("VOLUME", "THUMBNAIL", "LOCALIZER")):
                score -= 50

            if has_pixel_data:
                score += 10

            if score > 0:
                scored_files.append((score, dicom_file))

        if scored_files:
            scored_files.sort(key=lambda item: (-item[0], str(item[1])))
            return scored_files[0][1]

        # Last-resort behavior: preserve old robustness without blindly choosing
        # a no-pixel metadata file.
        return fallback_readable or dicom_files[0]

    def _extract_label_from_dicom_folder(self, folder: Path) -> bool:
        """
        Extract a label image from a DICOM folder.

        STEP 1 behavior:
          - pick one representative DICOM slice (using _pick_representative_dicom)
          - read its pixel data
          - convert to a PIL Image
          - save as <folder_name>.png into the output directory

        NOTE:
          - No cropping/rotation heuristics are applied at this stage.
          - This is intentionally conservative; later steps can refine the
            label selection (e.g. dedicated label/macro series).
        """
        try:
            rep = self._pick_representative_dicom(folder)
            if rep is None:
                self.log_failure(f"DICOM folder has no readable *.dcm files - {folder.name}")
                return False

            ds = pydicom.dcmread(str(rep))

            # pixel_array raises if no pixel data is present
            arr = ds.pixel_array  # type: ignore[attr-defined]

            # Basic handling for 2D grayscale or RGB-like arrays.
            if arr.ndim == 2:
                # Single channel image (grayscale)
                img = Image.fromarray(arr)
            elif arr.ndim == 3:
                # Likely (H, W, C). If not, we still try to interpret as image.
                if arr.shape[2] == 3:
                    img = Image.fromarray(arr)
                else:
                    # Fallback: take first channel to avoid crashing.
                    img = Image.fromarray(arr[:, :, 0])
            else:
                self.log_failure(f"Unsupported DICOM pixel array shape {arr.shape} in {rep.name}")
                return False

            # For now we do not crop/rotate; we simply store the whole image.
            out_name = f"{folder.name}.png"
            output_path = self.output_dir / out_name

            atomic_save_pil_image(img, output_path)
            LOGGER.info(f"[OK] DICOM folder -> label PNG: {folder.name} -> {output_path.name}")
            return True

        except Exception as exc:
            self.log_failure(f"DICOM folder error - {folder.name}: {exc}")
            return False

    # ------------------------------------------------------------------ #
    # Main processing
    # ------------------------------------------------------------------ #
    def extract_label(self) -> None:
        """Process input directory and extract label/macro regions.

        Notes:
            - PNG files are cropped/rotated to simulate label region.
            - WSI files: prefer 'label' associated image; otherwise crop 'macro'.
            - DICOM folders: treated as single slides; representative frame
              is converted to PNG (<folder_name>.png).
            - Unsupported or failed files are logged.
        """
        processed = 0
        if not self.input_dir.exists():
            self.log_failure(f"Input folder not found - {self.input_dir}")
            LOGGER.info("[SUMMARY] Total processed successfully: %d", processed)
            return

        for entry in self.input_dir.iterdir():
            # --------------------------------------------------------------
            # NEW STEP 1: treat DICOM slide folders as individual slides
            # --------------------------------------------------------------
            if entry.is_dir():
                if self._is_dicom_folder(entry):
                    ok = self._extract_label_from_dicom_folder(entry)
                    if ok:
                        processed += 1
                # Any other folders are ignored in STEP 1.
                continue

            # From here on we assume "entry" is a file (original behavior)
            file = entry
            if not file.is_file():
                continue

            filename = file.name
            suffix = file.suffix.lower()

            # ----------------------------- PNG path ------------------------
            if suffix == ".png":
                try:
                    img = Image.open(file)
                    width, height = img.size
                    crop_w = int(width * self.label_crop_ratio)
                    cropped = img.crop((0, 0, crop_w, height))
                    rotated = cropped.rotate(self.rotation_degrees, expand=True)
                    output_path = self.output_dir / self.generate_output_name(file)
                    atomic_save_pil_image(rotated, output_path)
                    processed += 1
                except Exception as exc:
                    self.log_failure(f"PNG error - {filename}: {exc}")
                continue

            # ----------------------------- WSI path ------------------------
            if not self.is_supported_wsi(file):
                self.log_failure(f"Unsupported WSI - {filename}")
                continue

            try:
                slide = openslide.OpenSlide(str(file))

                # 1) Try 'label' associated image
                if "label" in slide.associated_images:
                    label_img = slide.associated_images["label"]
                    if self.rotate_associated_label and self.label_rotation_degrees_label != 0:
                        label_img = label_img.rotate(self.label_rotation_degrees_label, expand=True)
                    output_path = self.output_dir / self.generate_output_name(file)
                    atomic_save_pil_image(label_img, output_path)
                    processed += 1
                    continue

                # 2) Try 'macro' (case-insensitive)
                macro_key = next((k for k in slide.associated_images if k.lower() == self.macro_tag), None)
                if macro_key:
                    macro = slide.associated_images[macro_key]
                    width, height = macro.size
                    crop_w = int(width * self.label_crop_ratio)
                    cropped = macro.crop((0, 0, crop_w, height))
                    rotated = cropped.rotate(self.rotation_degrees, expand=True)
                    output_path = self.output_dir / self.generate_output_name(file)
                    atomic_save_pil_image(rotated, output_path)
                    processed += 1
                else:
                    self.log_failure(f"No label or macro found in WSI - {filename}")
            except Exception as exc:
                self.log_failure(f"WSI processing error - {filename}: {exc}")

        LOGGER.info("[SUMMARY] Total processed successfully: %d", processed)


# --------------------------------------------------------------------------- #
# High-level runner
# --------------------------------------------------------------------------- #

def run_extraction(config_path: Path, openslide_dll: Optional[str], log_level: str) -> None:
    """Top-level 'run' implementation, preserved for pipeline compatibility."""
    setup_logging(log_level)

    try:
        config = load_config(config_path)
    except Exception as exc:
        LOGGER.error("[FAIL] Cannot read config: %s", exc)
        return

    run_output_dir = Path(config["run_output_dir"])
    staging_dir = Path(config["staging_dir"])
    label_crops_dir = Path(config.get("label_crops_dir") or config.get("label_root_dir"))

    timestamp = str(config.get("run_timestamp") or datetime.now().strftime("%Y-%m-%d_%H-%M"))
    if label_crops_dir.name == timestamp:
        label_crops_dir = label_crops_dir.parent
    label_crops_dir.mkdir(parents=True, exist_ok=True)

    output_run_dir = Path(config.get("output_run_dir", run_output_dir / timestamp))
    output_run_dir.mkdir(parents=True, exist_ok=True)

    log_file_path = output_run_dir / f"label_extraction_log_{uuid.uuid4().hex}.txt"

    paths = Paths(
        input_dir=staging_dir,
        output_dir=label_crops_dir,
        output_run_dir=output_run_dir,
        log_file_path=log_file_path,
    )

    dll_from_config = (config.get("dll_paths", {}) or {}).get("openslide_dll")
    openslide_dll = openslide_dll or dll_from_config

    if openslide_dll:
        prepare_windows_dlls(openslide_dll)

    global openslide
    if openslide is None:
        openslide = _load_openslide_after_dll_prep()

    extractor = LabelExtractor(
        input_dir=paths.input_dir,
        output_dir=paths.output_dir,
        log_file_path=paths.log_file_path,
        label_crop_ratio=float(config.get("label_crop_ratio", 0.3)),
        rotation_degrees=int(config.get("rotation_degrees", -90)),
        macro_tag=str(config.get("macro_tag", "macro")),
        rotate_associated_label=bool(config.get("rotate_associated_label", False)),
        label_rotation_degrees_label=int(config.get("label_rotation_degrees_label", 0)),
    )

    LOGGER.info(f"[STEP] Label extraction started. Input: {paths.input_dir}")
    extractor.extract_label()
    LOGGER.info(f"[STEP] Label extraction finished. Output: {paths.output_dir}")


def validate_config(config_path: Path, openslide_dll: Optional[str], log_level: str) -> int:
    """Lightweight validation of config and environment (no behavior change for `run`)."""
    setup_logging(log_level)
    try:
        config = load_config(config_path)
    except Exception as exc:
        LOGGER.error("[FAIL] Cannot read config: %s", exc)
        return 1

    required_keys = ["run_output_dir", "staging_dir"]
    missing = [k for k in required_keys if k not in config]
    if "label_crops_dir" not in config and "label_root_dir" not in config:
        missing.append("label_crops_dir or label_root_dir")
    if missing:
        LOGGER.error("[FAIL] Missing required config keys: %s", ", ".join(missing))
        return 2

    # Check folders existence where applicable
    src = Path(config["staging_dir"])
    if not src.exists():
        LOGGER.warning("[WARNING] Source folder does not exist yet: %s", src)

    # Attempt DLL prep if specified
    try:
        prepare_windows_dlls(openslide_dll or (config.get("dll_paths", {}) or {}).get("openslide_dll", None))
    except Exception as exc:
        LOGGER.warning("[WARNING] DLL preparation issue: %s", exc)

    LOGGER.info("Config validation passed.")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    """Create top-level argparse parser with subcommands."""
    parser = argparse.ArgumentParser(
        description="Extract label regions from WSI/PNG slides (structured CLI)."
    )
    parser.set_defaults(func=None)

    # Common options
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = subparsers.add_parser("run", help="Run extraction (original behavior).")
    p_run.add_argument("--config", type=Path, required=True, help="Path to YAML configuration file.")
    p_run.add_argument("--openslide_dll", type=str, required=False,
                       help="Path to OpenSlide DLL directory (Windows only).")
    p_run.set_defaults(func=lambda ns: run_extraction(ns.config, ns.openslide_dll, ns.log_level))

    # validate
    p_val = subparsers.add_parser("validate", help="Validate config and environment.")
    p_val.add_argument("--config", type=Path, required=True, help="Path to YAML configuration file.")
    p_val.add_argument("--openslide_dll", type=str, required=False,
                       help="Path to OpenSlide DLL directory (Windows only).")
    p_val.set_defaults(func=lambda ns: sys.exit(validate_config(ns.config, ns.openslide_dll, ns.log_level)))

    # version
    p_ver = subparsers.add_parser("version", help="Show module version.")
    p_ver.set_defaults(func=lambda ns: (setup_logging(ns.log_level), LOGGER.info(__VERSION__)))

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    """CLI entry-point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Dispatch
    if hasattr(args, "func") and args.func is not None:
        args.func(args) if callable(args.func) else None
    else:
        parser.print_help()


# --------------------------------------------------------------------------- #
# Script execution guard
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    main()