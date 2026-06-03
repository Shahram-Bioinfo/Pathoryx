#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extra_field_extractor.py

Extract additional ROI-based factors (defined in config),
clean them with per-field rules, and export as Excel.

Pipeline-compatible:
  python extra_field_extractor.py run --config <cfg> --log-level INFO

Key features:
- Robust ROI key lookup (ROI_<roi>_Words / ROI_<roi>_Text / case-insensitive fallback)
- Per-field default_value support (falls back to "00")
- Optional debug logging of roi_words keys
- Optional selection of only some fields via config
- Writes SlideStem to enable merging with .svs/.ndpi names (stem-based merge)
- Optional to_upper rule to normalize tokens (e.g., Ss -> SS)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import cv2
import pandas as pd
import yaml

from babel_shark.metadata_extractor_utilities.extractor import RoiMetadataExtractor

__version__ = "0.3.0"


# ---------------- Logging ---------------- #
def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


# ---------------- Config ---------------- #
@dataclass
class FieldSpec:
    name: str
    roi: Any
    rules: Dict[str, Any]


@dataclass
class AppConfig:
    cfg: Dict[str, Any]
    input_dir: Path
    output_dir: Path
    output_excel: Path
    debug_keys: bool
    only_fields: Optional[List[str]]
    fields: List[FieldSpec]

    @staticmethod
    def from_yaml(cfg_path: Path) -> "AppConfig":
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        afe = cfg.get("extra_field_extractor", {}) or {}
        input_dir = Path(afe.get("input_dir", "."))
        output_dir = Path(afe.get("output_dir", "."))
        out_name = str(afe.get("output_excel_name", "extra_field_results.xlsx"))
        output_excel = output_dir / out_name

        debug_keys = bool(afe.get("debug_keys", False))
        only_fields = afe.get("only_fields")  # e.g. ["Fresh1","Fresh2"]
        if only_fields is not None and not isinstance(only_fields, list):
            raise ValueError("extra_field_extractor.only_fields must be a list or omitted")

        raw_fields = (cfg.get("extra_field_extractor", {}) or {}).get("fields", [])
        if not raw_fields or not isinstance(raw_fields, list):
            raise ValueError("No fields defined in extra_field_extractor.fields")

        fields: List[FieldSpec] = []
        for fobj in raw_fields:
            if not isinstance(fobj, dict):
                continue
            name = str(fobj.get("name", "")).strip()
            roi = fobj.get("roi")
            rules = fobj.get("rules", {}) or {}
            if not name:
                continue
            fields.append(FieldSpec(name=name, roi=roi, rules=rules))

        if not fields:
            raise ValueError("extra_field_extractor.fields exists but no valid field items found")

        return AppConfig(
            cfg=cfg,
            input_dir=input_dir,
            output_dir=output_dir,
            output_excel=output_excel,
            debug_keys=debug_keys,
            only_fields=only_fields,
            fields=fields,
        )

    def ensure_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)


# ---------------- Excel write (atomic) ---------------- #
def atomic_write_excel(df: pd.DataFrame, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(dst.parent), suffix=".xlsx", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as xw:
            df.to_excel(xw, index=False)
        os.replace(tmp_path, dst)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ---------------- Cleaning ---------------- #
def clean_factor(raw: Optional[str], rules: Dict[str, Any]) -> str:
    """
    Apply per-field rules:
      - replacement_patterns: list of [pattern, replacement]
      - allowed_chars: keep only characters present in this string
      - to_upper: bool (optional) -> uppercase output
      - default_value: returned if final cleaned text is empty
    """
    default_value = str(rules.get("default_value", "00"))

    t = str(raw or "").strip()

    repls = rules.get("replacement_patterns", []) or []
    for item in repls:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        pat, sub = item[0], item[1]
        try:
            t = re.sub(str(pat), str(sub), t)
        except re.error:
            # ignore invalid regex instead of crashing
            continue

    allow = rules.get("allowed_chars")
    if allow:
        allow_set = set(str(allow))
        t = "".join(ch for ch in t if ch in allow_set)

    if bool(rules.get("to_upper", False)):
        t = t.upper()

    t = t.strip()
    return t if t else default_value


# ---------------- Robust ROI key lookup ---------------- #
def pick_roi_value(roi_words: Any, roi: Any) -> str:
    """
    Try multiple key variants to get ROI text from roi_words mapping.
    Supports:
      ROI_<roi>_Words, ROI_<roi>_Text, <roi>_Words, <roi>_Text
    And a case-insensitive fallback search.
    """
    if not isinstance(roi_words, dict):
        return ""

    roi_s = str(roi).strip()
    if not roi_s:
        return ""

    # Direct candidates
    candidates = [
        f"ROI_{roi_s}_Words",
        f"ROI_{roi_s}_Text",
        f"{roi_s}_Words",
        f"{roi_s}_Text",
    ]
    for k in candidates:
        v = roi_words.get(k)
        if v is not None and str(v).strip() != "":
            return str(v)

    # Case-insensitive fallback
    roi_u = roi_s.upper()
    for k, v in roi_words.items():
        if v is None:
            continue
        vv = str(v).strip()
        if not vv:
            continue
        ku = str(k).upper()
        if roi_u in ku and ("WORDS" in ku or "TEXT" in ku):
            return vv

    return ""


# ---------------- IO helpers ---------------- #
def list_images(folder: Path) -> List[Path]:
    exts = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
    if not folder.exists():
        return []
    out: List[Path] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in exts:
            out.append(p)
    return out


def select_fields(all_fields: List[FieldSpec], only: Optional[List[str]]) -> List[FieldSpec]:
    if not only:
        return all_fields
    only_set = {str(x).strip() for x in only}
    return [f for f in all_fields if f.name in only_set]



# ---------------- Core extraction ---------------- #
def extract(cfg: AppConfig) -> List[Dict[str, str]]:
    logging.info("[INFO] Initializing RoiMetadataExtractor")
    extractor = RoiMetadataExtractor(cfg.cfg)

    rows: List[Dict[str, str]] = []

    images = list_images(cfg.input_dir)
    if not images:
        logging.info(f"[WARN] No images found in: {cfg.input_dir}")
        return rows

    chosen_fields = select_fields(cfg.fields, cfg.only_fields)
    if not chosen_fields:
        logging.info("[WARN] No fields selected to extract (check only_fields)")
        return rows

    # Optional: debug raw values too (set in config: extra_field_extractor.debug_values: true)
    afe_cfg = (cfg.cfg.get("extra_field_extractor", {}) or {})
    debug_values = bool(afe_cfg.get("debug_values", False))

    for img_path in images:
        logging.info(f"[INFO] Processing {img_path.name}")

        img = cv2.imread(str(img_path))
        if img is None or getattr(img, "size", 0) == 0:
            logging.info(f"[WARN] Cannot read image: {img_path.name}")
            continue

        try:
            _, _, roi_words = extractor.run_on_image(img, str(img_path))
        except Exception as exc:
            logging.info(f"[WARN] ROI extraction failed on {img_path.name}: {exc}")
            continue

        if cfg.debug_keys:
            try:
                keys = list(roi_words.keys()) if isinstance(roi_words, dict) else []
                logging.info(f"[DEBUG] roi_words keys for {img_path.name}: {keys}")
            except Exception:
                pass

        # IMPORTANT: write SlideStem for robust merging later with SVS/NDPI
        row: Dict[str, str] = {
            "SlideStem": img_path.stem,   # e.g. N24-2172-Q
            "FileName": img_path.name,    # e.g. N24-2172-Q.png
        }

        for field in chosen_fields:
            fname = field.name
            roi = field.roi

            raw = pick_roi_value(roi_words, roi)
            cleaned = clean_factor(raw, field.rules)

            row[fname] = cleaned

            if debug_values:
                # show raw/cleaned for selected fields
                logging.info(
                    f"[DEBUG] {img_path.name} | field={fname} roi={roi!r} raw={raw!r} cleaned={cleaned!r}"
                )

        rows.append(row)

    return rows


def run(cfg_path: Path, log_level: str) -> int:
    setup_logging(log_level)

    try:
        cfg = AppConfig.from_yaml(cfg_path)
    except Exception as exc:
        logging.info(f"[ERROR] Config error: {exc}")
        return 2

    cfg.ensure_dirs()

    rows = extract(cfg)
    if not rows:
        logging.info("[WARN] No factors extracted")
        return 0

    df = pd.DataFrame(rows)

    # ensure stable column order: key cols first
    key_cols = [c for c in ["SlideStem", "FileName"] if c in df.columns]
    other_cols = [c for c in df.columns if c not in key_cols]
    df = df[key_cols + other_cols]

    atomic_write_excel(df, cfg.output_excel)
    logging.info(f"[OK] Extra fields saved: {cfg.output_excel}")
    return 0


# ---------------- CLI ---------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="extra_field_extractor")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="Run extraction")
    pr.add_argument("--config", required=True, help="Path to YAML config")
    pr.add_argument("--log-level", default="INFO", help="Logging level")

    pv = sub.add_parser("version", help="Print version")

    args = p.parse_args(argv)

    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "run":
        return run(Path(args.config), str(args.log_level))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
