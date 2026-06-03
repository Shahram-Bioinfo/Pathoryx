#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stain_extractor.py — Babel-Shark Stain Detector

Detects and normalizes stain types (e.g., H&E, PAS, Giemsa) from label
images using OCR, with optional ROI-based fallback when stain is
ambiguous or exactly "H&E".

Main features:
    OCR-based stain detection using EasyOCR
    Supports composite stains (A+B) and replacements
    ROI fallback via roi_metadata_extractor.py integration
    Generates PDF summary and Excel/CSV outputs atomically
    Command-line interface for run, validate, and version

Runtime contract
----------------
Preferred explicit runtime keys from runner:
    - run_output_dir
    - output_run_dir
    - label_crops_dir
    - stain_output_excel
    - stain_output_pdf

If explicit output paths are missing, the script falls back to the legacy naming:
    output_run_dir / f"stain_results_<run_timestamp>.xlsx"
    output_run_dir / f"stain_report_<run_timestamp>.pdf"

Usage:
    PYTHONPATH=./src/babel_shark python -u src/babel_shark/stain_extractor.py run \
        --config ./config/config.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import easyocr
import pandas as pd
import yaml
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

__version__ = "1.1.0"


# --------------------------------------------------------------------------- #
# Logging Setup
# --------------------------------------------------------------------------- #
def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with a simple consistent format."""
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


# --------------------------------------------------------------------------- #
# Data Model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AppConfig:
    """Container for configuration values loaded from YAML."""

    run_timestamp: str
    run_output_dir: Path
    output_run_dir: Path
    label_crops_dir: Path
    stain_list_path: Path
    stain_replace_map_path: Path
    roi_pxl_offset: int
    roi_debug_dir: str
    log_level: str

    @staticmethod
    def from_yaml(path: Path) -> "AppConfig":
        """Load config from YAML path and map known keys with defaults identical to original behavior."""
        with path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        ts = str(cfg.get("run_timestamp", datetime.now().strftime("%Y-%m-%d_%H-%M")))
        output_base = Path(cfg["run_output_dir"])
        run_dir = Path(cfg.get("output_run_dir", output_base / ts))

        return AppConfig(
            run_timestamp=ts,
            run_output_dir=output_base,
            output_run_dir=run_dir,
            label_crops_dir=Path(cfg.get("label_crops_dir")),
            stain_list_path=Path(cfg.get("stain_list_path", "./stain_list.json")),
            stain_replace_map_path=Path(cfg.get("stain_replace_map_path", "./stain_error_replacements.json")),
            roi_pxl_offset=int(cfg.get("roi_pxl_offset", 2)),
            roi_debug_dir=str(cfg.get("roi_debug_dir", "")),
            log_level=str(cfg.get("log_level", "INFO")),
        )

    def ensure_output_dir(self) -> None:
        """Create output directory."""
        self.output_run_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #
def atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write bytes atomically to target using a temp file then os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("wb") as f:
        f.write(data)
    os.replace(tmp, target)


def atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy into a temp file next to dst, then finalize with os.replace."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def get_excel_uri(path: Path) -> str:
    """Build a file:// URI that Excel accepts on both Windows and Linux."""
    path = Path(path).resolve()
    parts = path.parts
    if len(parts) > 2 and parts[0] == "/" and parts[1] == "mnt":
        drive_letter = parts[2].upper()
        rest = "/".join(parts[3:])
        from urllib.parse import quote
        return f"file:///{drive_letter}:/{quote(rest)}"
    if path.drive:
        from urllib.parse import quote
        return "file:///" + quote(path.as_posix())
    from urllib.parse import quote
    return "file://" + quote(path.as_posix())


def is_exact_he(x: str) -> bool:
    """Return True if the value equals 'H&E' (case-insensitive, trimmed)."""
    return str(x).strip().upper() == "H&E"


def apply_replacements(tokens: Sequence[str], repl_map: Dict[str, str]) -> List[str]:
    """Per-token, case-insensitive replacements without substring mutation; preserve dictionary casing."""
    if not repl_map:
        return list(tokens)
    lut = {str(k).lower(): v for k, v in repl_map.items()}
    fixed: List[str] = []
    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        mapped = lut.get(t.lower())
        fixed.append(mapped if mapped is not None else t)
    return fixed


def split_tokens_on_plus(tokens: Sequence[str]) -> List[str]:
    """Split tokens with '+' into parts while keeping '+' tokens."""
    out: List[str] = []
    for t in tokens:
        if "+" in t and len(t) > 1:
            parts = t.split("+")
            for i, p in enumerate(parts):
                p = p.strip()
                if p:
                    out.append(p)
                if i < len(parts) - 1:
                    out.append("+")
        else:
            out.append(t)
    return out


def map_to_canonical(token: str, stain_ref: Dict[str, str] | List[str]) -> Optional[str]:
    """Map a token to canonical stain name (dict or list). Special-case HE/HE+ -> 'HE'."""
    if not token:
        return None
    t = token.strip()
    if not t:
        return None
    if t.upper() in ("HE", "HE+"):
        return "HE"
    if isinstance(stain_ref, dict):
        return stain_ref.get(t.lower()) or stain_ref.get(t) or stain_ref.get(t.strip().lower())
    if isinstance(stain_ref, list):
        lut = {str(x).lower(): x for x in stain_ref}
        return lut.get(t.lower())
    return None


def detect_plus_pair(
    tokens_after_repl: Sequence[str],
    stain_ref: Dict[str, str] | List[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Detect A + B style composite stains -> 'A&B' using canonical names."""
    seq = split_tokens_on_plus(tokens_after_repl)
    for i in range(len(seq) - 2):
        left, mid, right = seq[i], seq[i + 1], seq[i + 2]
        if mid != "+":
            continue
        if left == "+" or right == "+":
            continue
        left_can = map_to_canonical(left, stain_ref)
        right_can = map_to_canonical(right, stain_ref)
        if left_can and right_can:
            return f"{left_can}&{right_can}", f"{left}+{right}"
    return None, None


def detect_two_valid_stains(
    tokens_after_repl: Sequence[str],
    stain_ref: Dict[str, str] | List[str],
) -> Tuple[Optional[str], Optional[str]]:
    """If first two distinct tokens map to valid stains (even without '+'), return 'A&B'."""
    seen: List[str] = []
    for t in tokens_after_repl:
        can = map_to_canonical(t, stain_ref)
        if can and (can not in seen):
            seen.append(can)
        if len(seen) >= 2:
            return f"{seen[0]}&{seen[1]}", f"{seen[0]}+{seen[1]}"
    return None, None


def tokenize_raw_words(words: Iterable[str]) -> List[str]:
    """Tokenize OCR words; preserve tokens containing '*' by splitting once and keeping parts."""
    cleaned: List[str] = []
    for wtok in words:
        s = str(wtok).strip()
        if not s:
            continue
        for token in s.split():
            if "*" in token:
                i = token.index("*")
                pfx, sfx = token[:i], token[i:]
                if pfx.strip():
                    cleaned.append(pfx.strip())
                if sfx.strip():
                    cleaned.append(sfx.strip())
            else:
                cleaned.append(token.strip())
    return cleaned


def find_exact_stain(tokens: Sequence[str], stain_ref: Dict[str, str] | List[str]) -> Tuple[str, str]:
    """Match tokens to stains with original precedence rules; default 'H&E' if no match."""
    for t in tokens:
        s = t.strip()
        if not s:
            continue
        if s.upper() in ("HE", "HE+"):
            return "HE", t

    if isinstance(stain_ref, dict):
        lut = {str(k).lower(): v for k, v in stain_ref.items()}
        for t in tokens:
            hit = lut.get(t.strip().lower())
            if hit is not None:
                return hit, t

    elif isinstance(stain_ref, list):
        set_ci = {str(x).lower(): x for x in stain_ref}
        for t in tokens:
            hit = set_ci.get(t.strip().lower())
            if hit is not None:
                return hit, t

    return "H&E", ""


# --------------------------------------------------------------------------- #
# Runtime path contract
# --------------------------------------------------------------------------- #
def _resolve_output_paths(cfg_path: Path, cfg: AppConfig) -> Tuple[Path, Path, Dict[str, Any]]:
    """
    Resolve runtime output paths with enterprise-first explicit keys and legacy fallback.
    """
    with cfg_path.open("r", encoding="utf-8") as f:
        full_config = yaml.safe_load(f) or {}

    explicit_excel = str(full_config.get("stain_output_excel", "")).strip()
    explicit_pdf = str(full_config.get("stain_output_pdf", "")).strip()

    if explicit_excel:
        output_excel = Path(explicit_excel)
    else:
        output_excel = cfg.output_run_dir / f"stain_results_{cfg.run_timestamp}.xlsx"

    if explicit_pdf:
        output_pdf = Path(explicit_pdf)
    else:
        output_pdf = cfg.output_run_dir / f"stain_report_{cfg.run_timestamp}.pdf"

    output_excel.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    return output_excel, output_pdf, full_config


# --------------------------------------------------------------------------- #
# ROI Fallback (CLI)
# --------------------------------------------------------------------------- #
def run_roi_fallback_cli_for_single_image(
    cfg: AppConfig,
    image_path: Path,
    log_level: str = "INFO",
) -> Optional[Dict[str, Any]]:
    """Run ROI metadata extraction in-process for a single image (no subprocess).

    Config resolution order (same as legacy):
      1. temp_config_roi.yaml in the current working directory
      2. temp_config.yaml in the current working directory

    In the enterprise stage_runner context these files are written before
    stain extraction starts, so the resolution still works correctly.
    """
    cfg_path = Path("temp_config_roi.yaml")
    if not cfg_path.exists():
        cfg_path = Path("temp_config.yaml")
    if not cfg_path.exists():
        logging.info("[ROI][SKIP] No temp config found for ROI fallback.")
        return None

    try:
        import yaml as _yaml
        with open(cfg_path, "r", encoding="utf-8") as _f:
            roi_cfg = _yaml.safe_load(_f) or {}
    except Exception as exc:
        logging.info("[ROI][SKIP] Cannot load ROI config %s: %s", cfg_path, exc)
        return None

    try:
        from .metadata_extractor_utilities.extractor import RoiMetadataExtractor
        extractor = RoiMetadataExtractor(roi_cfg)
    except Exception as exc:
        logging.info("[ROI][SKIP] Cannot init RoiMetadataExtractor: %s", exc)
        return None

    try:
        img_bgr = cv2.imread(str(image_path))
        if img_bgr is None:
            logging.info("[ROI][SKIP] Cannot read image: %s", image_path.name)
            return None

        pxl_offset = int(roi_cfg.get("pxl_offset", 8))
        parsed, success, _roi_words = extractor.run_on_image(
            img_bgr,
            img_name=image_path.name,
            pxl_offset=pxl_offset,
        )

        return {
            "FileName": image_path.name,
            "Stain": parsed.get("Stain", ""),
            "DataMatrix": parsed.get("DataMatrix", ""),
            "LabID": parsed.get("LabID", ""),
            "Year": parsed.get("Year", ""),
            "CaseNumber": parsed.get("CaseNumber", ""),
            "Pot": parsed.get("Pot", ""),
            "BlockID": parsed.get("BlockID", ""),
            "Section": parsed.get("Section", ""),
            "Status": "Success" if success else "Failed",
        }

    except Exception as exc:
        logging.info("[ROI][ERR] In-process ROI extraction failed for %s: %s", image_path.name, exc)
        return None


# --------------------------------------------------------------------------- #
# PDF Report
# --------------------------------------------------------------------------- #
def generate_pdf_report(
    results: List[Dict[str, Any]],
    input_dir: Path,
    output_pdf: Path,
) -> None:
    """Create compact PDF summary and write atomically."""
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    tmp_pdf = output_pdf.with_suffix(output_pdf.suffix + ".tmp")

    c = canvas.Canvas(str(tmp_pdf), pagesize=A4)
    width, height = A4
    y = height - 50
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, y, "Stain Extraction Report")
    y -= 20
    c.setFont("Helvetica", 10)
    c.drawCentredString(width / 2, y, f"Generated at: {now_str}")
    y -= 20
    c.line(50, y, width - 50, y)
    y -= 30

    for item in results:
        file_name = str(item.get("FileName", ""))
        stain = str(item.get("Stain", ""))
        image_path = input_dir / file_name

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, f"File: {file_name} | Stain: {stain}")
        y -= 20

        try:
            img = ImageReader(str(image_path))
            c.drawImage(img, 50, y - 100, width=200, height=100, preserveAspectRatio=True, mask="auto")
            y -= 130
        except Exception as exc:
            c.setFont("Helvetica", 10)
            c.setFillColor(colors.red)
            c.drawString(50, y, f"[Image load failed: {exc}]")
            c.setFillColor(colors.black)
            y -= 20

        c.setStrokeColor(colors.grey)
        c.line(50, y, width - 50, y)
        c.setStrokeColor(colors.black)
        y -= 20

        if y < 150:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica-Bold", 16)
            c.drawCentredString(width / 2, y, "Stain Extraction Report (cont.)")
            y -= 20
            c.setFont("Helvetica", 10)
            c.drawCentredString(width / 2, y, f"Generated at: {now_str}")
            y -= 20
            c.line(50, y, width - 50, y)
            y -= 30

    c.save()
    os.replace(tmp_pdf, output_pdf)
    logging.info("PDF report saved at: %s", output_pdf)


# --------------------------------------------------------------------------- #
# Core Processing
# --------------------------------------------------------------------------- #
def init_ocr_reader() -> easyocr.Reader:
    """Initialize EasyOCR reader identically to original (en, gpu=False)."""
    return easyocr.Reader(["en"], gpu=False)


def ocr_crop(image: Any, crop_config: Dict[str, float]) -> Any:
    """Crop the image based on provided top, bottom, left, and right percentages."""
    h, w = image.shape[:2]

    top = crop_config.get("top", 0)
    bottom = crop_config.get("bottom", 0)
    left = crop_config.get("left", 0)
    right = crop_config.get("right", 0)

    y_start = int(h * (top / 100))
    y_end = int(h * (1 - bottom / 100))
    x_start = int(w * (left / 100))
    x_end = int(w * (1 - right / 100))

    return image[y_start:y_end, x_start:x_end]


def detect_stain_for_image(
    image_path: Path,
    reader: easyocr.Reader,
    stain_dict: Dict[str, str] | List[str],
    replacements: Dict[str, str],
    cfg: AppConfig,
    crop_config: Dict[str, float],
) -> Dict[str, Any]:
    """Run OCR + rules; if initial stain is exactly H&E, run ROI fallback via CLI."""
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError("Image could not be loaded.")

    cropped = ocr_crop(image, crop_config)
    cropped_rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
    raw_words: List[str] = reader.readtext(cropped_rgb, detail=0)

    cleaned_words = tokenize_raw_words(raw_words)
    corrected_words = apply_replacements(cleaned_words, replacements)

    composite_stain, composite_src = detect_plus_pair(corrected_words, stain_dict)
    if composite_stain:
        matched_stain = composite_stain
        matched_word = composite_src
        logging.info(
            "[INIT] %s -> Composite Stain via '+': '%s' (from '%s')",
            image_path.name,
            matched_stain,
            matched_word,
        )
    else:
        pair_stain, pair_src = detect_two_valid_stains(corrected_words, stain_dict)
        if pair_stain:
            matched_stain = pair_stain
            matched_word = pair_src
            logging.info(
                "[INIT] %s -> Multi-token Stain: '%s' (from '%s')",
                image_path.name,
                matched_stain,
                matched_word,
            )
        else:
            matched_stain, matched_word = find_exact_stain(corrected_words, stain_dict)
            logging.info(
                "[INIT] %s -> Initial Stain: '%s' (matched token: '%s')",
                image_path.name,
                matched_stain,
                matched_word,
            )

    roi_stain = ""
    final_stain = matched_stain
    origin = "Primary"

    if is_exact_he(matched_stain):
        try:
            logging.info(
                "[ROI] Initial 'H&E' -> running fallback CLI on: %s",
                image_path.name,
            )
            roi_row = run_roi_fallback_cli_for_single_image(
                cfg=cfg,
                image_path=image_path,
                log_level=cfg.log_level,
            )

            if roi_row:
                raw_val = roi_row.get("Stain", "")
                if pd.isna(raw_val):
                    raw_val = ""

                roi_stain_candidate = str(raw_val).strip()
                logging.info(
                    "[ROI] ROI Stain (raw->norm): '%s'",
                    roi_stain_candidate or "-",
                )

                if roi_stain_candidate and not is_exact_he(roi_stain_candidate):
                    final_stain = roi_stain_candidate
                    roi_stain = roi_stain_candidate
                    origin = "ROI-Fallback"
                    logging.info("[FINAL] Replaced by ROI: '%s'", final_stain)
                else:
                    roi_stain = matched_stain
                    logging.info(
                        "[FINAL] ROI empty/'H&E' -> keeping initial '%s'",
                        final_stain,
                    )
            else:
                roi_stain = matched_stain
                logging.info(
                    "[ROI] No ROI result; keeping '%s' and setting ROI stain to same.",
                    final_stain,
                )

        except Exception as exc:
            roi_stain = matched_stain
            logging.error(
                "[ROI][ERR] Fallback failed for %s: %s",
                image_path.name,
                exc,
            )

    uri = get_excel_uri(image_path)

    return {
        "FileName": image_path.name,
        "Raw_OCR_Words": ", ".join(map(str, raw_words)),
        "Cleaned_Words": ", ".join(cleaned_words),
        "Corrected_Words": ", ".join(corrected_words),
        "Matched_Word": matched_word,
        "Stain_Initial": matched_stain,
        "Stain_ROI_DoubleCheck": roi_stain,
        "Stain": final_stain,
        "Stain_Origin": origin,
        "ImageLink": f'=HYPERLINK("{uri}", "{image_path.name}")',
    }


def write_excel_atomic(df: pd.DataFrame, output_excel: Path) -> None:
    """Write the same Excel with hyperlink column via xlsxwriter, then finalize atomically."""
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    tmp_xlsx = output_excel.with_suffix(output_excel.suffix + ".tmp")

    with pd.ExcelWriter(tmp_xlsx, engine="xlsxwriter") as writer:
        df.drop(columns=["ImageLink"]).to_excel(writer, index=False, sheet_name="StainResults")
        workbook = writer.book
        worksheet = writer.sheets["StainResults"]
        link_format = workbook.add_format({"font_color": "blue", "underline": 1})

        colnames = list(df.drop(columns=["ImageLink"]).columns)
        link_col_idx = (colnames.index("FileName") + 1) if "FileName" in colnames else len(colnames)
        for i, row in df.iterrows():
            worksheet.write_formula(i + 1, link_col_idx, row["ImageLink"], link_format)

    os.replace(tmp_xlsx, output_excel)
    logging.info("Excel report saved at: %s", output_excel)


def generate_pdf(results: List[Dict[str, Any]], input_dir: Path, output_pdf: Path) -> None:
    """Backward-compatible alias to generate_pdf_report (kept for external callers)."""
    generate_pdf_report(results, input_dir, output_pdf)


def run_pipeline(cfg_path: Path) -> None:
    """Main pipeline runner; enterprise-compatible and backward-compatible."""
    cfg = AppConfig.from_yaml(cfg_path)
    setup_logging(cfg.log_level)
    cfg.ensure_output_dir()

    input_dir = cfg.label_crops_dir

    with cfg.stain_list_path.open("r", encoding="utf-8") as f:
        stain_dict = json.load(f)
    with cfg.stain_replace_map_path.open("r", encoding="utf-8") as f:
        replacements = json.load(f)

    output_excel, output_pdf, full_config = _resolve_output_paths(cfg_path, cfg)

    logging.info("[OUTPUT] Excel path: %s", output_excel)
    logging.info("[OUTPUT] PDF path: %s", output_pdf)

    crop_config = full_config.get("ocr_crop_config", {"top": 0, "bottom": 0, "left": 0, "right": 0})

    reader = init_ocr_reader()

    results: List[Dict[str, Any]] = []
    for image_path in sorted(Path(input_dir).glob("*.*")):
        try:
            rec = detect_stain_for_image(
                image_path=image_path,
                reader=reader,
                stain_dict=stain_dict,
                replacements=replacements,
                cfg=cfg,
                crop_config=crop_config,
            )
            results.append(rec)
            logging.info(
                "[DONE] %s -> Initial: %s | ROI: %s | Final: %s [%s]",
                image_path.name,
                rec["Stain_Initial"],
                rec["Stain_ROI_DoubleCheck"] or "-",
                rec["Stain"],
                rec["Stain_Origin"],
            )
        except Exception as exc:
            logging.error("[ERROR] %s: %s", image_path.name, exc)

    if not results:
        logging.info("No stains detected. No output generated.")
        return

    df = pd.DataFrame(results)
    write_excel_atomic(df, output_excel)
    generate_pdf_report(results, input_dir, output_pdf)


def validate_config(cfg_path: Path) -> int:
    """Light-weight validation that required keys/paths exist."""
    try:
        cfg = AppConfig.from_yaml(cfg_path)
        problems: List[str] = []

        if not cfg.run_output_dir:
            problems.append("run_output_dir is missing.")
        if not cfg.label_crops_dir or not cfg.label_crops_dir.exists():
            problems.append(f"label_crops_dir not found: {cfg.label_crops_dir}")
        if not cfg.stain_list_path.exists():
            problems.append(f"stain_list_path not found: {cfg.stain_list_path}")
        if not cfg.stain_replace_map_path.exists():
            problems.append(f"stain_replace_map_path not found: {cfg.stain_replace_map_path}")

        if problems:
            for p in problems:
                logging.error("CONFIG: %s", p)
            return 1

        logging.info("Configuration looks OK.")
        return 0
    except Exception as exc:
        logging.error("Failed to read/validate config: %s", exc)
        return 2


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    """Build argparse CLI with subparsers (run, validate, version)."""
    parser = argparse.ArgumentParser(
        description='Stain extractor with mandatory ROI/classifier fallback when initial result is exactly "H&E".'
    )
    parser.add_argument("--config", type=str, help="Path to YAML configuration file (compat: defaults to run)")
    subparsers = parser.add_subparsers(dest="command")

    p_run = subparsers.add_parser("run", help="Run stain extraction pipeline")
    p_run.add_argument("--config", type=str, required=True, help="Path to YAML configuration file")

    p_val = subparsers.add_parser("validate", help="Validate configuration without running")
    p_val.add_argument("--config", type=str, required=True, help="Path to YAML configuration file")

    subparsers.add_parser("version", help="Show version and exit")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point honoring backward compatibility."""
    setup_logging("INFO")
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        if getattr(args, "config", None):
            run_pipeline(Path(args.config))
            return 0
        parser.print_help()
        return 1

    if args.command == "run":
        run_pipeline(Path(args.config))
        return 0

    if args.command == "validate":
        return validate_config(Path(args.config))

    if args.command == "version":
        print(__version__)
        return 0

    parser.print_help()
    return 1


# --------------------------------------------------------------------------- #
# Script execution guard
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    raise SystemExit(main())