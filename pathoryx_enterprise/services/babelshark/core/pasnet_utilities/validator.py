#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pasnet_validator.py

Pasnet validation module for the Babel-Shark pipeline.

This module performs slide-level validation against the PASNET (LIS) database
before renaming or finalizing files in the pipeline.

Main responsibilities:
- Load Datamatrix and Stain extraction results from Excel outputs.
- Reconstruct CaseID / SlideID if missing.
- Query PASNET to verify:
    * Case existence
    * Slide existence
    * Stain consistency
- Apply rule-based validation logic (via rules_search).
- Generate a structured Excel validation report with multiple sheets:
    * Datamatrix
    * Fallback
    * Suspicious_DBSlides
    * overrides
- Provide override instructions back to the pipeline.

Security note:
This module does not store credentials directly. PASNET connection handling
is delegated to io_adapters, which should implement secure credential
management (e.g., Windows Credential Manager or service accounts).

Intended usage:
- Called by the unified CLI or pipeline runner.
- Can also be executed standalone for debugging purposes.

Modes:
- pre_rename : Validate slides before rename step.
- audit      : Reserved for future audit-based validation.
"""

# =============================================================================
# Purpose
# =============================================================================
# This validator acts as a control layer between extracted metadata
# (Datamatrix/Stain) and the PASNET database.
# It ensures structural consistency, existence checks, and rule-based
# validation decisions before files proceed further in the pipeline.


from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# =============================================================================
# Imports (package-safe)
# =============================================================================

def _import_io_adapters():
    """
    io_adapters must be importable within the package.
    Prefer relative import; fallback to flat import for standalone runs.
    """
    try:
        from .io_adapters import (  # type: ignore
            load_config,
            setup_logging,
            logger,
            now_utc_iso,
            connect_pasnet,
            fetch_case_slide_infos,
        )
        return load_config, setup_logging, logger, now_utc_iso, connect_pasnet, fetch_case_slide_infos
    except Exception:
        from io_adapters import (  # type: ignore
            load_config,
            setup_logging,
            logger,
            now_utc_iso,
            connect_pasnet,
            fetch_case_slide_infos,
        )
        return load_config, setup_logging, logger, now_utc_iso, connect_pasnet, fetch_case_slide_infos


def _import_rules_engine():
    """
    You said you only have rules_search.py.
    Prefer relative import; fallback to flat import for standalone runs.
    Required symbols:
      - decide_datamatrix(row_dict, case_exists, df_case) -> decision_dict
      - decide_fallback(row_dict, case_exists, df_case) -> decision_dict
      - to_pasnet_case_id(case_id_pipeline) -> string or ""
    """
    try:
        from .rules_search import (  # type: ignore
            decide_datamatrix,
            decide_fallback,
            to_pasnet_case_id,
        )
        return decide_datamatrix, decide_fallback, to_pasnet_case_id
    except Exception:
        from rules_search import (  # type: ignore
            decide_datamatrix,
            decide_fallback,
            to_pasnet_case_id,
        )
        return decide_datamatrix, decide_fallback, to_pasnet_case_id


load_config, setup_logging, logger, now_utc_iso, connect_pasnet, fetch_case_slide_infos = _import_io_adapters()
decide_datamatrix, decide_fallback, to_pasnet_case_id = _import_rules_engine()


# =============================================================================
# Helpers: normalization (Excel-safe) + ID builders
# =============================================================================

def _to_str(v: Any) -> str:
    """Safe to-string with NaN handling."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def _strip_excel_decimal(s: str) -> str:
    """Convert '12.0' -> '12'."""
    return re.sub(r"\.0+$", "", s.strip())


def to_int_str_no_decimal(v: Any) -> str:
    s = _to_str(v)
    if not s:
        return ""
    s = _strip_excel_decimal(s)
    try:
        f = float(s)
        if float(f).is_integer():
            return str(int(f))
    except Exception:
        pass
    return s


def to_year4_str(v: Any) -> str:
    """Normalize year to 4 digits if possible."""
    s = to_int_str_no_decimal(v)
    if len(s) == 2 and s.isdigit():
        return "20" + s
    if len(s) >= 4 and s[:4].isdigit():
        return s[:4]
    return s


def to_case6_str(v: Any) -> str:
    """Normalize CaseNumber to 6 digits, zero-padded."""
    s = to_int_str_no_decimal(v)
    digits = re.sub(r"\D+", "", s)
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def normalize_stain_for_slideid(stain: str) -> str:
    """
    Minimal stain normalization ONLY for SlideID construction.
    (DB canonicalization is handled by the rules engine.)
    """
    raw = stain.strip()
    if not raw:
        return ""
    u = raw.upper().replace(" ", "")
    if u in {"HE", "H-E", "H&E", "H& E", "H &E", "H & E"}:
        return "H&E"
    return raw


def build_case_id_from_parts(labid: Any, year: Any, casenumber: Any) -> str:
    """
    CaseID format used in your pipeline:
      CaseID = LabID + Year4 + Case6
    Example: E + 2025 + 059542 => E2025059542
    """
    lab = _to_str(labid).upper()
    y4 = to_year4_str(year)
    c6 = to_case6_str(casenumber)
    if not (lab and y4 and c6):
        return ""
    return f"{lab}{y4}{c6}"


def build_datamatrix(labid: Any, year: Any, casenumber: Any, pot: Any, blockid: Any, section: Any) -> str:
    """
    DataMatrix format:
      {LabID}{Year4}{Case6}S{Pot}-{BlockID}-{Section}
    Defaults:
      Pot='A', BlockID='1', Section='1'
    """
    lab = _to_str(labid).upper()
    y4 = to_year4_str(year)
    c6 = to_case6_str(casenumber)
    if not (lab and y4 and c6):
        return ""
    pot_s = (_to_str(pot).upper() or "A")
    blk_s = (to_int_str_no_decimal(blockid) or "1")
    sec_s = (to_int_str_no_decimal(section) or "1")
    return f"{lab}{y4}{c6}S{pot_s}-{blk_s}-{sec_s}"


def construct_slide_id_if_missing(row: Dict[str, Any]) -> str:
    """
    Construct SlideID only if missing:
      SlideID = DataMatrix + "-" + Stain
    """
    dm = build_datamatrix(
        row.get("LabID"),
        row.get("Year"),
        row.get("CaseNumber"),
        row.get("Pot"),
        row.get("BlockID"),
        row.get("Section"),
    )
    stain = normalize_stain_for_slideid(_to_str(row.get("Stain")))
    if dm and stain:
        return f"{dm}-{stain}"
    return ""


def extract_case_id_from_datamatrix_or_slideid(text: str) -> str:
    """
    Best-effort extraction:
    - If text contains something like:  E2025059542S...
      return E2025059542
    - Or if text starts with: E2025059542...
      return first 1+4+6 pattern.
    """
    t = _to_str(text).replace(" ", "")
    if not t:
        return ""

    m = re.search(r"([A-Z])(\d{4})(\d{6})S", t, flags=re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}{m.group(3)}"

    m2 = re.match(r"^([A-Z])(\d{4})(\d{6})", t, flags=re.IGNORECASE)
    if m2:
        return f"{m2.group(1).upper()}{m2.group(2)}{m2.group(3)}"

    return ""


def is_datamatrix_method(method: str) -> bool:
    """
    Case-insensitive Datamatrix detector:
      "Datamatrix", "DataMatrix", "datamatrix", "data matrix" => True
    """
    m = _to_str(method).strip().lower().replace(" ", "")
    return m == "datamatrix"


# =============================================================================
# Report columns (legacy)
# =============================================================================

def report_columns_datamatrix() -> List[str]:
    return [
        "RunTime",
        "CaseID",
        "SlideID",
        "Stain",
        "method of extraction",
        "OriginalFileName",
        "NewFileName",
        "InputPath",
        "OutputPath",
        "status",
        "pasnet_connection",
        "pasnet_case_exists",
        "pasnet_slide_exists",
        "pasnet_stain_raw",
        "pasnet_stain_canonical",
        "extracted_stain",
        "final_stain",
        "stain_result",
        "final_decision",
        "decision_reason",
    ]


def report_columns_fallback() -> List[str]:
    return [
        "RunTime",
        "CaseID",
        "SlideID",
        "Pot",
        "BlockID",
        "Section",
        "Stain",
        "method of extraction",
        "OriginalFileName",
        "NewFileName",
        "InputPath",
        "OutputPath",
        "status",
        "pasnet_connection",
        "pasnet_case_exists",
        "pasnet_total_slides_in_case",
        "candidate_key",
        "candidate_count",
        "candidate_match_type",
        "best_candidate_slide_id",
        "best_candidate_section",
        "pasnet_best_stain_raw",
        "pasnet_best_stain_canonical",
        "final_slide_id",
        "final_stain",
        "final_decision",
        "decision_reason",
        "candidates_compact",
    ]


# =============================================================================
# Config
# =============================================================================

@dataclass(frozen=True)
class ValidatorConfig:
    enabled: bool
    mode: str              # "pre_rename" or "audit"
    fail_open: bool

    datamatrix_output_excel: Optional[Path]
    stain_output_excel: Optional[Path]
    slide_metadata_excel: Optional[Path]  # for audit mode (optional)

    report_xlsx_path: Path
    suspicious_output_dir: Path

    sheet_datamatrix: str
    sheet_fallback: str
    sheet_overrides: str

    @staticmethod
    def from_yaml(cfg: Dict[str, Any]) -> "ValidatorConfig":
        pv = cfg.get("pasnet_validator") if isinstance(cfg.get("pasnet_validator"), dict) else {}

        dm = cfg.get("datamatrix_output_excel") or pv.get("datamatrix_output_excel")
        st = cfg.get("stain_output_excel") or pv.get("stain_output_excel")
        sm = cfg.get("slide_metadata_excel") or pv.get("slide_metadata_excel")

        return ValidatorConfig(
            enabled=bool(pv.get("enabled", True)),
            mode=str(pv.get("mode", "pre_rename")).strip(),
            fail_open=bool(pv.get("fail_open", True)),
            datamatrix_output_excel=Path(dm) if dm else None,
            stain_output_excel=Path(st) if st else None,
            slide_metadata_excel=Path(sm) if sm else None,
            report_xlsx_path=Path(pv.get("report_xlsx_path", "./output/pasnet_validation_report.xlsx")),
            suspicious_output_dir=Path(pv.get("suspicious_output_dir", "./output/suspicious")),
            sheet_datamatrix=str(pv.get("report_sheet_datamatrix", "Datamatrix")),
            sheet_fallback=str(pv.get("report_sheet_fallback", "Fallback")),
            sheet_overrides=str(pv.get("report_sheet_overrides", "overrides")),
        )


# =============================================================================
# Excel IO
# =============================================================================

def _read_excel_required(path: Optional[Path], label: str) -> pd.DataFrame:
    if path is None:
        raise FileNotFoundError(f"{label} path is missing in config.")
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return pd.read_excel(path)


def _ensure_filename_col(df: pd.DataFrame, label: str) -> pd.DataFrame:
    if "FileName" in df.columns:
        return df
    for c in df.columns:
        if str(c).strip().lower() in ("filename", "file_name"):
            return df.rename(columns={c: "FileName"})
    raise KeyError(f"{label} must contain a FileName column.")


def _ensure_stain_col(df: pd.DataFrame) -> pd.DataFrame:
    if "Stain" in df.columns:
        return df
    for c in df.columns:
        if str(c).strip().lower() == "stain":
            return df.rename(columns={c: "Stain"})
    return df


def write_excel_sheets(path: Path, sheets: List[Tuple[str, pd.DataFrame]]) -> None:
    """Reliable multi-sheet writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as writer:
        for sheet_name, df in sheets:
            df.to_excel(writer, index=False, sheet_name=sheet_name)


# =============================================================================
# NEW: Suspicious DB Slides pivot sheet
# =============================================================================

def build_suspicious_dbslides_pivot(
    suspicious_case_ids_pasnet: List[str],
    case_cache: Dict[str, Tuple[bool, pd.DataFrame]],
) -> pd.DataFrame:
    """
    Sheet where:
      - each column is a CaseID (Pasnet format: e.g., E/2025/059542)
      - rows are SlideIDs (from DB) for that case, optionally with staining.
    Only one column per CaseID even if multiple suspicious files are in that case.
    """
    # unique in order
    seen = set()
    unique_cases: List[str] = []
    for cid in suspicious_case_ids_pasnet:
        if cid and cid not in seen:
            seen.add(cid)
            unique_cases.append(cid)

    col_values: Dict[str, List[str]] = {}
    max_len = 0

    for cid in unique_cases:
        case_exists, df_case = case_cache.get(cid, (False, pd.DataFrame()))
        items: List[str] = []

        if case_exists and isinstance(df_case, pd.DataFrame) and (not df_case.empty) and ("slide_id" in df_case.columns):
            if "staining" in df_case.columns:
                for _, r in df_case.iterrows():
                    sid = _to_str(r.get("slide_id"))
                    stn = _to_str(r.get("staining"))
                    if sid and stn:
                        items.append(f"{sid} ({stn})")
                    elif sid:
                        items.append(sid)
            else:
                for sid in df_case["slide_id"].astype(str).tolist():
                    sid2 = _to_str(sid)
                    if sid2:
                        items.append(sid2)

        # de-dup per case, preserve order
        seen_item = set()
        uniq_items: List[str] = []
        for x in items:
            if x not in seen_item:
                seen_item.add(x)
                uniq_items.append(x)

        col_values[cid] = uniq_items
        max_len = max(max_len, len(uniq_items))

    if not col_values:
        return pd.DataFrame({"NO_SUSPICIOUS_CASES": []})

    data: Dict[str, List[str]] = {}
    for cid, vals in col_values.items():
        data[cid] = vals + [""] * (max_len - len(vals))

    return pd.DataFrame(data)


# =============================================================================
# Core runner: pre_rename
# =============================================================================

def run_pre_rename(cfg: Dict[str, Any], vcfg: ValidatorConfig) -> None:
    logger.info(f"[STEP] pasnet_validator mode={vcfg.mode} (pre_rename) fail_open={vcfg.fail_open}")

    df_dm = _ensure_filename_col(
        _read_excel_required(vcfg.datamatrix_output_excel, "datamatrix_output_excel"),
        "datamatrix_output_excel",
    )
    df_st = _ensure_stain_col(
        _ensure_filename_col(
            _read_excel_required(vcfg.stain_output_excel, "stain_output_excel"),
            "stain_output_excel",
        )
    )

    # Track whether extraction method info existed in input at all
    orig_has_extraction_col = ("method of extraction" in df_dm.columns) or ("ExtractionMethod" in df_dm.columns)

    # Merge stain into DM
    df_st_min = df_st[["FileName", "Stain"]].copy() if "Stain" in df_st.columns else df_st[["FileName"]].copy()
    df = df_dm.merge(df_st_min, on="FileName", how="left", suffixes=("", "_stain"))

    # Unify stain column after merge
    if "Stain_stain" in df.columns and "Stain" in df.columns:
        df["Stain"] = df["Stain"].fillna(df["Stain_stain"])
        df = df.drop(columns=["Stain_stain"])
    elif "Stain" not in df.columns and "Stain_stain" in df.columns:
        df = df.rename(columns={"Stain_stain": "Stain"})

    # Normalize extraction method column to legacy name:
    # If there is no extraction column at all -> default everything to Datamatrix
    if "method of extraction" not in df.columns:
        if "ExtractionMethod" in df.columns:
            df["method of extraction"] = df["ExtractionMethod"]
        else:
            df["method of extraction"] = "Datamatrix"

    # failed folder (pipeline-level)
    failed_dir = str(cfg.get("failed_output_dir") or "")

    # Connect Pasnet
    pasnet_con, pasnet_status = connect_pasnet(cfg)
    if pasnet_status != "OK":
        logger.warning("[WARN] Pasnet connection FAILED.")
    else:
        logger.info("[OK] Pasnet connection established.")

    case_cache: Dict[str, Tuple[bool, pd.DataFrame]] = {}

    dm_rows: List[Dict[str, Any]] = []
    fb_rows: List[Dict[str, Any]] = []
    overrides_rows: List[Dict[str, Any]] = []

    # NEW: collect suspicious case ids (Pasnet format) for DB-slide pivot sheet
    suspicious_case_ids_pasnet: List[str] = []

    for _, rec in df.iterrows():
        r = rec.to_dict()
        runtime = _to_str(r.get("RunTime")) or now_utc_iso()

        # ---- CaseID: read, else build from parts, else extract from SlideID/DataMatrix text
        case_id = _to_str(r.get("CaseID"))
        if not case_id:
            case_id = build_case_id_from_parts(r.get("LabID"), r.get("Year"), r.get("CaseNumber"))
        if not case_id:
            candidate_text = _to_str(r.get("SlideID")) or _to_str(r.get("DataMatrix")) or ""
            case_id = extract_case_id_from_datamatrix_or_slideid(candidate_text)

        stain = _to_str(r.get("Stain"))

        # ---- method of extraction
        method_raw = _to_str(r.get("method of extraction"))
        # FAIL only if the input had an extraction column but this row is empty
        extraction_missing_for_row = (orig_has_extraction_col and not method_raw)
        method = method_raw or "Datamatrix"

        # Optional file identity columns
        original_name = _to_str(r.get("OriginalFileName")) or _to_str(r.get("FileName"))
        new_name = _to_str(r.get("NewFileName"))
        input_path = _to_str(r.get("InputPath"))
        output_path = _to_str(r.get("OutputPath"))
        status = _to_str(r.get("status") or r.get("Status") or "success")

        pot = _to_str(r.get("Pot"))
        blockid = to_int_str_no_decimal(r.get("BlockID"))
        section = to_int_str_no_decimal(r.get("Section"))

        # ---- SlideID: read, else construct
        slide_id = _to_str(r.get("SlideID"))
        if not slide_id:
            slide_id = construct_slide_id_if_missing(r)

        # ---- FAIL conditions (requested)
        if extraction_missing_for_row or not case_id:
            final_decision = "FAIL"
            decision_reason = "missing_extraction_method" if extraction_missing_for_row else "case_id_missing"

            fb_rows.append({
                "RunTime": runtime,
                "CaseID": case_id,
                "SlideID": slide_id,
                "Pot": pot,
                "BlockID": blockid,
                "Section": section,
                "Stain": stain,
                "method of extraction": method,
                "OriginalFileName": original_name,
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "status": status,
                "pasnet_connection": "NOT_RUN",
                "pasnet_case_exists": "ERROR",
                "pasnet_total_slides_in_case": None,
                "candidate_key": None,
                "candidate_count": None,
                "candidate_match_type": "ERROR",
                "best_candidate_slide_id": None,
                "best_candidate_section": None,
                "pasnet_best_stain_raw": None,
                "pasnet_best_stain_canonical": None,
                "final_slide_id": None,
                "final_stain": stain,
                "final_decision": final_decision,
                "decision_reason": decision_reason,
                "candidates_compact": None,
            })

            overrides_rows.append({
                "FileName": _to_str(r.get("FileName")),
                "final_decision": final_decision,
                "file_action": "MOVE_TO_CASELIST_FOLDER",
                "dest_dir": failed_dir,
                "override_stain": stain or "",
                "override_slide_id": slide_id or "",
                "decision_reason": decision_reason,
            })
            continue

        # Prepare legacy-like row dict for rules engine
        legacy_row = {
            "RunTime": runtime,
            "CaseID": case_id,
            "SlideID": slide_id,
            "Stain": stain,
            "method of extraction": method,
            "Pot": pot,
            "BlockID": blockid,
            "Section": section,
            "OriginalFileName": original_name,
            "NewFileName": new_name,
            "InputPath": input_path,
            "OutputPath": output_path,
            "status": status,
        }

        # If Pasnet is down
        if pasnet_status != "OK" or pasnet_con is None:
            final_decision = "PASNET_UNAVAILABLE" if vcfg.fail_open else "SUSPICIOUS"
            decision_reason = "pasnet_connection_failed"

            if is_datamatrix_method(method):
                dm_rows.append({
                    "RunTime": runtime,
                    "CaseID": case_id,
                    "SlideID": slide_id,
                    "Stain": stain,
                    "method of extraction": method,
                    "OriginalFileName": original_name,
                    "NewFileName": new_name,
                    "InputPath": input_path,
                    "OutputPath": output_path,
                    "status": status,
                    "pasnet_connection": "FAILED",
                    "pasnet_case_exists": "ERROR",
                    "pasnet_slide_exists": "ERROR",
                    "pasnet_stain_raw": None,
                    "pasnet_stain_canonical": None,
                    "extracted_stain": stain,
                    "final_stain": stain,
                    "stain_result": "NOT_CHECKED",
                    "final_decision": final_decision,
                    "decision_reason": decision_reason,
                })
            else:
                fb_rows.append({
                    "RunTime": runtime,
                    "CaseID": case_id,
                    "SlideID": slide_id,
                    "Pot": pot,
                    "BlockID": blockid,
                    "Section": section,
                    "Stain": stain,
                    "method of extraction": method,
                    "OriginalFileName": original_name,
                    "NewFileName": new_name,
                    "InputPath": input_path,
                    "OutputPath": output_path,
                    "status": status,
                    "pasnet_connection": "FAILED",
                    "pasnet_case_exists": "ERROR",
                    "pasnet_total_slides_in_case": None,
                    "candidate_key": None,
                    "candidate_count": None,
                    "candidate_match_type": "ERROR",
                    "best_candidate_slide_id": None,
                    "best_candidate_section": None,
                    "pasnet_best_stain_raw": None,
                    "pasnet_best_stain_canonical": None,
                    "final_slide_id": None,
                    "final_stain": stain,
                    "final_decision": final_decision,
                    "decision_reason": decision_reason,
                    "candidates_compact": None,
                })

            overrides_rows.append({
                "FileName": _to_str(r.get("FileName")),
                "final_decision": final_decision,
                "file_action": "MOVE_TO_SUSPICIOUS" if final_decision == "SUSPICIOUS" else "NONE",
                "dest_dir": str(vcfg.suspicious_output_dir) if final_decision == "SUSPICIOUS" else "",
                "override_stain": stain or "",
                "override_slide_id": slide_id or "",
                "decision_reason": decision_reason,
            })
            continue

        # CaseID must be convertible for Pasnet (invalid => FAIL)
        case_id_pasnet = to_pasnet_case_id(case_id)
        if not case_id_pasnet:
            final_decision = "FAIL"
            decision_reason = "invalid_case_id_format_for_pasnet"

            fb_rows.append({
                "RunTime": runtime,
                "CaseID": case_id,
                "SlideID": slide_id,
                "Pot": pot,
                "BlockID": blockid,
                "Section": section,
                "Stain": stain,
                "method of extraction": method,
                "OriginalFileName": original_name,
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "status": status,
                "pasnet_connection": "OK",
                "pasnet_case_exists": "ERROR",
                "pasnet_total_slides_in_case": None,
                "candidate_key": None,
                "candidate_count": None,
                "candidate_match_type": "ERROR",
                "best_candidate_slide_id": None,
                "best_candidate_section": None,
                "pasnet_best_stain_raw": None,
                "pasnet_best_stain_canonical": None,
                "final_slide_id": None,
                "final_stain": stain,
                "final_decision": final_decision,
                "decision_reason": decision_reason,
                "candidates_compact": None,
            })

            overrides_rows.append({
                "FileName": _to_str(r.get("FileName")),
                "final_decision": final_decision,
                "file_action": "MOVE_TO_CASELIST_FOLDER",
                "dest_dir": failed_dir,
                "override_stain": stain or "",
                "override_slide_id": slide_id or "",
                "decision_reason": decision_reason,
            })
            continue

        # Fetch case slides (cached)
        if case_id_pasnet not in case_cache:
            case_exists, df_case = fetch_case_slide_infos(pasnet_con, case_id_pasnet)
            case_cache[case_id_pasnet] = (case_exists, df_case)
        else:
            case_exists, df_case = case_cache[case_id_pasnet]

        # Apply rules
        if is_datamatrix_method(method):
            decision = decide_datamatrix(legacy_row, case_exists, df_case)
            dm_rows.append({
                "RunTime": runtime,
                "CaseID": case_id,
                "SlideID": slide_id,
                "Stain": stain,
                "method of extraction": method,
                "OriginalFileName": original_name,
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "status": status,
                "pasnet_connection": "OK",
                "pasnet_case_exists": decision.get("pasnet_case_exists"),
                "pasnet_slide_exists": decision.get("pasnet_slide_exists"),
                "pasnet_stain_raw": decision.get("pasnet_stain_raw"),
                "pasnet_stain_canonical": decision.get("pasnet_stain_canonical"),
                "extracted_stain": stain,
                "final_stain": decision.get("final_stain"),
                "stain_result": decision.get("stain_result"),
                "final_decision": decision.get("final_decision"),
                "decision_reason": decision.get("decision_reason"),
            })
            final_decision = decision.get("final_decision") or "SUSPICIOUS"
            final_stain = decision.get("final_stain") or stain
            final_slide_id = decision.get("final_slide_id") or slide_id
            decision_reason = decision.get("decision_reason") or ""
        else:
            decision = decide_fallback(legacy_row, case_exists, df_case)
            fb_rows.append({
                "RunTime": runtime,
                "CaseID": case_id,
                "SlideID": slide_id,
                "Pot": pot,
                "BlockID": blockid,
                "Section": section,
                "Stain": stain,
                "method of extraction": method,
                "OriginalFileName": original_name,
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "status": status,
                "pasnet_connection": "OK",
                "pasnet_case_exists": decision.get("pasnet_case_exists"),
                "pasnet_total_slides_in_case": decision.get("pasnet_total_slides_in_case"),
                "candidate_key": decision.get("candidate_key"),
                "candidate_count": decision.get("candidate_count"),
                "candidate_match_type": decision.get("candidate_match_type"),
                "best_candidate_slide_id": decision.get("best_candidate_slide_id"),
                "best_candidate_section": decision.get("best_candidate_section"),
                "pasnet_best_stain_raw": decision.get("pasnet_best_stain_raw"),
                "pasnet_best_stain_canonical": decision.get("pasnet_best_stain_canonical"),
                "final_slide_id": decision.get("final_slide_id"),
                "final_stain": decision.get("final_stain"),
                "final_decision": decision.get("final_decision"),
                "decision_reason": decision.get("decision_reason"),
                "candidates_compact": decision.get("candidates_compact"),
            })
            final_decision = decision.get("final_decision") or "SUSPICIOUS"
            final_stain = decision.get("final_stain") or stain
            final_slide_id = decision.get("final_slide_id") or ""
            decision_reason = decision.get("decision_reason") or ""

        # NEW: collect suspicious cases (one column per CaseID later)
        if final_decision == "SUSPICIOUS":
            suspicious_case_ids_pasnet.append(case_id_pasnet)

        # Overrides (pipeline contract)
        file_action = "MOVE_TO_SUSPICIOUS" if final_decision == "SUSPICIOUS" else "NONE"
        dest_dir = str(vcfg.suspicious_output_dir) if file_action == "MOVE_TO_SUSPICIOUS" else ""
        overrides_rows.append({
            "FileName": _to_str(r.get("FileName")),
            "final_decision": final_decision,
            "file_action": file_action,
            "dest_dir": dest_dir,
            "override_stain": final_stain or "",
            "override_slide_id": final_slide_id or "",
            "decision_reason": decision_reason,
        })

    # Build DataFrames and enforce column order
    df_dm_out = pd.DataFrame(dm_rows)
    df_fb_out = pd.DataFrame(fb_rows)

    for c in report_columns_datamatrix():
        if c not in df_dm_out.columns:
            df_dm_out[c] = None
    df_dm_out = df_dm_out[report_columns_datamatrix()]

    for c in report_columns_fallback():
        if c not in df_fb_out.columns:
            df_fb_out[c] = None
    df_fb_out = df_fb_out[report_columns_fallback()]

    df_ov = pd.DataFrame(
        overrides_rows,
        columns=[
            "FileName", "final_decision", "file_action", "dest_dir",
            "override_stain", "override_slide_id", "decision_reason"
        ],
    )

    # NEW SHEET: Suspicious DB Slides (pivot columns=CaseID, rows=db slide ids)
    df_susp_db = build_suspicious_dbslides_pivot(suspicious_case_ids_pasnet, case_cache)

    write_excel_sheets(
        vcfg.report_xlsx_path,
        [
            (vcfg.sheet_datamatrix, df_dm_out),
            (vcfg.sheet_fallback, df_fb_out),
            ("Suspicious_DBSlides", df_susp_db),
            (vcfg.sheet_overrides, df_ov),
        ],
    )

    logger.info(f"[OK] Pasnet report written: {vcfg.report_xlsx_path}")
    logger.info(
        f"[OK] Rows: Datamatrix={len(df_dm_out)} | Fallback={len(df_fb_out)} | Suspicious_DBSlides_cols={df_susp_db.shape[1]} | overrides={len(df_ov)}"
    )


def run_audit(cfg: Dict[str, Any], vcfg: ValidatorConfig) -> None:
    """
    Kept for CLI compatibility.
    Implement later if you need audit from slide_metadata_excel.
    """
    raise NotImplementedError("audit mode is not implemented in this validator build.")


# =============================================================================
# CLI-compatible validation + main
# =============================================================================

def cmd_validate(cfg_path: Path) -> int:
    """
    CLI expects this symbol.
    Return codes:
      0 = valid config
      2 = invalid config
    """
    try:
        cfg = load_config(cfg_path)
        _ = ValidatorConfig.from_yaml(cfg)
        logger.info("[OK] Config validated.")
        return 0
    except Exception as exc:
        logger.error(f"[INVALID] {exc}")
        return 2


def main(argv: Optional[List[str]] = None) -> None:
    """
    Optional standalone runner for local testing.
    The pipeline typically calls pasnet_utilities/cli.py, but this is useful for debugging.
    """
    parser = argparse.ArgumentParser(prog="validator", description="Pasnet Validator (pre_rename/audit).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run validator.")
    p_run.add_argument("--config", required=True)
    p_run.add_argument("--log-level", default=None)

    p_val = sub.add_parser("validate", help="Validate config.")
    p_val.add_argument("--config", required=True)
    p_val.add_argument("--log-level", default=None)

    args = parser.parse_args(argv)

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    log_level = args.log_level or cfg.get("log_level", "INFO")
    setup_logging(str(log_level))

    if args.cmd == "validate":
        raise SystemExit(cmd_validate(cfg_path))

    vcfg = ValidatorConfig.from_yaml(cfg)
    if not vcfg.enabled:
        logger.info("[SKIP] pasnet_validator.enabled=false")
        return

    if vcfg.mode == "pre_rename":
        run_pre_rename(cfg, vcfg)
        return

    if vcfg.mode == "audit":
        run_audit(cfg, vcfg)
        return

    raise SystemExit(f"Unknown mode: {vcfg.mode!r}")


if __name__ == "__main__":
    main()
