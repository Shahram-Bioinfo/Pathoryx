#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
slide_id_generator.py — Unified Slide ID builder & Excel registry writer with
research routing by color label + blacklist.

Rules:
- Default = routine
- Research if:
    1) color label detected (highest priority)
    2) or CaseID/name hits research case list Excel
- Research unreadable:
    * do NOT fail to failed/NAN
    * keep original filename
    * route to research destination
- Research readable:
    * rename
    * NO timestamp tag
- Routine unreadable:
    * normal failed/fallback flow

Precedence:
    color research > blacklist research > pasnet action > routine
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set, Any

import pandas as pd
import yaml

openslide = None
openslide_dll: Optional[str] = None


def _setup_openslide(dll_path: Optional[str]) -> None:
    global openslide, openslide_dll
    openslide_dll = dll_path
    if dll_path and hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(dll_path)
        except Exception:
            pass
    try:
        import openslide as _osl  # type: ignore[import]
        openslide = _osl
    except Exception:
        openslide = None


def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


logger = logging.getLogger(__name__)

def safe_str(value) -> str:
    """Return a string safe for .lower()/.upper() when pandas values may be NaN."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


INVALID_FS_CHARS = '<>:"/\\|?*'
FS_TRANS_TABLE = str.maketrans({ch: "_" for ch in INVALID_FS_CHARS})


def sanitize_fs(name: str) -> str:
    if name is None:
        return ""
    out = str(name).translate(FS_TRANS_TABLE)
    return out.strip(" .")


def digits_only(x: object) -> str:
    return re.sub(r"[^\d]", "", str(x) if x is not None else "")


def to_int_str_no_decimal(x: object) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    m = re.fullmatch(r"\s*([0-9]+)(?:[.,]0+)?\s*", s)
    if m:
        return str(int(m.group(1)))
    d = digits_only(s)
    return d if d else None


def to_year4_str(y: object) -> Optional[str]:
    s = to_int_str_no_decimal(y)
    if not s:
        return None
    if re.fullmatch(r"[0-9]{2}", s):
        return f"20{s}"
    if re.fullmatch(r"[0-9]{4}", s):
        return s
    return None


def to_case6_str(c: object) -> Optional[str]:
    s = to_int_str_no_decimal(c)
    if not s:
        return None
    return f"{int(s):06d}"


def normalize_stain(s: Optional[str]) -> str:
    if s is None:
        return "H&E"
    t_raw = str(s).strip()
    t = t_raw.upper().replace(" ", "")
    if t in {"HE", "H-E", "H& E", "H &E", "H & E", "H&E", "H&Ε"}:
        return "H&E"
    return t_raw.strip()


def build_datamatrix(lab, year, case, pot, blockid, section) -> Optional[str]:
    lab_s = (str(lab).strip().upper() if lab else "")
    y4 = to_year4_str(year)
    c6 = to_case6_str(case)
    pot_s = (str(pot).strip().upper() if pot else "")
    bid = to_int_str_no_decimal(blockid) or (str(blockid).strip() if blockid is not None else "")
    sec = to_int_str_no_decimal(section) or (str(section).strip() if section is not None else "")
    if not (lab_s and y4 and c6 and pot_s and bid and sec):
        return None
    return f"{lab_s}{y4}{c6}S{pot_s}-{bid}-{sec}"


def prefer_ext(candidates: List[Path]) -> Optional[Path]:
    if not candidates:
        return None
    priority = [".svs", ".ndpi", ".scn", ".mrxs", ".bif", ".tif", ".tiff", ".png", ".jpg", ".jpeg"]
    cand_map = {p.suffix.lower(): p for p in candidates}
    for ext in priority:
        if ext in cand_map:
            return cand_map[ext]
    return candidates[0]


def unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    i = 1
    stem, suf = target.stem, target.suffix
    while True:
        cand = target.with_name(f"{stem}_{i}{suf}")
        if not cand.exists():
            return cand
        i += 1


def read_excel_safe(path: Path) -> pd.DataFrame:
    try:
        return pd.read_excel(path, dtype=str)
    except Exception as exc:
        logger.warning(f"[WARN] Cannot read Excel: {path} -> {exc}")
        return pd.DataFrame()


def atomic_write_excel(dfs: Dict[str, pd.DataFrame], path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with pd.ExcelWriter(tmp, engine="openpyxl") as xw:
        for name, df in dfs.items():
            (df if df is not None else pd.DataFrame()).to_excel(xw, sheet_name=name, index=False)
    os.replace(tmp, path)


def _stem(x: object) -> str:
    try:
        return Path(str(x)).stem
    except Exception:
        return str(x)


def _utc_ts_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scan_ts_iso_z_from_metadata(path: Path) -> Optional[str]:
    if openslide is None:
        logger.warning("[WARN] OpenSlide not available; cannot read ScanTime_UTC from metadata.")
        return None
    try:
        slide = openslide.OpenSlide(str(path))
        props = dict(slide.properties)
        try:
            slide.close()
        except Exception:
            pass
    except Exception as exc:
        logger.warning(f"[WARN] Cannot open slide for metadata: {path} -> {exc}")
        return None

    def _try_parse(s: str, fmts: List[str]) -> Optional[datetime]:
        s = str(s).strip()
        for fmt in fmts:
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass
        return None

    vendor = str(props.get("openslide.vendor", "")).lower().strip()

    dt_str = props.get("tiff.DateTime")
    if dt_str:
        dt = _try_parse(dt_str, ["%Y:%m:%d %H:%M:%S"])
        if dt:
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if vendor == "aperio":
        date_str = props.get("aperio.Date")
        time_str = props.get("aperio.Time")
        tz_str = props.get("aperio.Time Zone")
        if date_str and time_str:
            dt_date = _try_parse(date_str, ["%m/%d/%Y", "%m/%d/%y"])
            dt_time = _try_parse(time_str, ["%H:%M:%S", "%H:%M"])
            if dt_date and dt_time:
                t = dt_time.time()
                offset = timezone.utc
                if tz_str:
                    m = re.match(r"GMT([+-])(\d{2})(\d{2})", str(tz_str).strip())
                    if m:
                        sign = 1 if m.group(1) == "+" else -1
                        hours = int(m.group(2))
                        minutes = int(m.group(3))
                        delta = timedelta(hours=hours, minutes=minutes)
                        offset = timezone(sign * delta)
                local_dt = datetime(
                    year=dt_date.year,
                    month=dt_date.month,
                    day=dt_date.day,
                    hour=t.hour,
                    minute=t.minute,
                    second=t.second,
                    tzinfo=offset,
                )
                utc_dt = local_dt.astimezone(timezone.utc)
                return utc_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    date_hama = props.get("hamamatsu.Created") or props.get("hamamatsu.Updated")
    if date_hama:
        dt = _try_parse(str(date_hama), ["%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"])
        if dt:
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for k, v in props.items():
        kl = str(k).lower()
        if any(tok in kl for tok in ["date", "time", "scan", "created", "creation", "acquisition"]):
            if not v:
                continue
            dt = _try_parse(str(v), [
                "%Y:%m:%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%m/%d/%Y",
                "%m/%d/%y",
                "%d/%m/%Y",
                "%d/%m/%y",
            ])
            if dt:
                dt = dt.replace(tzinfo=timezone.utc)
                return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return None


def _scan_ts_iso_z_from_dicom_folder(folder: Path) -> Optional[str]:
    try:
        import pydicom
    except Exception:
        logger.warning("[WARN] pydicom not available; cannot extract DICOM timestamp.")
        return None

    dcm_files = sorted(folder.glob("*.dcm"))
    if not dcm_files:
        logger.warning(f"[WARN] No .dcm files found for timestamp extraction in {folder}")
        return None

    first_dcm = dcm_files[0]
    try:
        ds = pydicom.dcmread(str(first_dcm), stop_before_pixels=True)
    except Exception as exc:
        logger.warning(f"[WARN] Could not read DICOM for timestamp: {first_dcm} -> {exc}")
        return None

    def _to_iso_utc(date_str: str, time_str: str) -> Optional[str]:
        date_str = (date_str or "").strip()
        time_str = (time_str or "").strip()
        if not date_str or not time_str:
            return None
        time_str = time_str.split(".")[0]
        if len(time_str) < 6:
            time_str = time_str.ljust(6, "0")
        try:
            dt = datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
        except Exception:
            return None
        dt = dt.replace(tzinfo=timezone.utc)
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    raw_dt = getattr(ds, "AcquisitionDateTime", None)
    if raw_dt:
        s = str(raw_dt).strip()
        s_core = s.split(".")[0]
        if len(s_core) >= 14:
            iso = _to_iso_utc(s_core[0:8], s_core[8:14])
            if iso:
                return iso

    date_str = getattr(ds, "AcquisitionDate", None) or getattr(ds, "StudyDate", None)
    time_str = getattr(ds, "AcquisitionTime", None) or getattr(ds, "StudyTime", None)
    iso = _to_iso_utc(str(date_str or ""), str(time_str or ""))
    if iso:
        return iso

    try:
        mtime = first_dcm.stat().st_mtime
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return None





def _parse_scan_time_to_iso_z(raw: str) -> Optional[str]:
    """Parse raw scan date/time text from DB metadata into UTC ISO-Z.

    This intentionally supports the same common formats already handled by
    the file-based readers. If parsing fails, caller falls back to direct file
    metadata extraction.
    """
    raw = str(raw or "").strip()
    if not raw:
        return None

    formats = [
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%y %H:%M:%S",
        "%Y:%m:%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except Exception:
            continue

    return None


def _scan_ts_iso_z_from_db(path: Path) -> Optional[str]:
    """Return ScanTime_UTC from Palantir PostgreSQL metadata when available.

    This function is deliberately safe:
      - DB import failure returns None.
      - query failure returns None.
      - missing/partial metadata returns None.

    The caller must always keep the original file-based reader as fallback.
    """
    try:
        from pathoryx_enterprise.db.session import get_session
        from pathoryx_enterprise.db.models.core import FileRecord
        from sqlalchemy import select, or_
    except Exception as exc:
        logger.warning(f"[WARN] DB unavailable for ScanTime lookup; falling back to file metadata: {exc}")
        return None

    try:
        canonical = str(path.resolve())
    except Exception:
        canonical = str(path)

    try:
        with get_session() as session:
            record = session.execute(
                select(FileRecord).where(
                    or_(
                        FileRecord.current_file_path == canonical,
                        FileRecord.canonical_path == canonical,
                        FileRecord.original_path == canonical,
                        FileRecord.source_artifact_id == canonical,
                        FileRecord.original_filename == path.name,
                    )
                ).order_by(FileRecord.internal_id.desc()).limit(1)
            ).scalar_one_or_none()

            if record is None:
                return None

            meta = record.metadata_json or {}
            out_meta = record.output_metadata_json or {}
            scan_time = meta.get("scan_time") if isinstance(meta.get("scan_time"), dict) else {}

            scan_date_raw = scan_time.get("scan_date_raw") or out_meta.get("scan_date_raw")
            scan_time_raw = scan_time.get("scan_time_raw") or out_meta.get("scan_time_raw")

            if not scan_date_raw:
                return None

            raw = str(scan_date_raw).strip()
            if scan_time_raw:
                raw = raw + " " + str(scan_time_raw).strip()

            return _parse_scan_time_to_iso_z(raw)

    except Exception as exc:
        logger.warning(f"[WARN] DB ScanTime lookup failed for {path}; falling back to file metadata: {exc}")
        return None


def _scan_ts_iso_z_db_first(path: Path, *, is_dicom_folder: bool = False) -> Optional[str]:
    db_value = _scan_ts_iso_z_from_db(path)
    if db_value:
        return db_value

    if is_dicom_folder:
        return _scan_ts_iso_z_from_dicom_folder(path)

    return _scan_ts_iso_z_from_metadata(path)

def _output_day_from_cfg(cfg: Dict[str, Any]) -> str:
    for key in ("output_day", "run_day", "day", "run_date"):
        value = str(cfg.get(key, "") or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value
    value = str(cfg.get("run_timestamp", "") or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", value)
    if m:
        return m.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def _dated_output_dir(root: Path, cfg: Dict[str, Any]) -> Path:
    root = Path(root)
    day = _output_day_from_cfg(cfg)
    if root.name == day:
        out = root
    else:
        out = root / day
    out.mkdir(parents=True, exist_ok=True)
    return out


def _get_research_router_cfg(cfg: Dict) -> Dict:
    sid_cfg = cfg.get("slide_id_generator") if isinstance(cfg.get("slide_id_generator"), dict) else {}
    rc = sid_cfg.get("research_case_router")
    if isinstance(rc, dict):
        return rc
    rc_alias = sid_cfg.get("research_case_generator")
    if isinstance(rc_alias, dict):
        return rc_alias
    rc2 = cfg.get("research_case_router")
    if isinstance(rc2, dict):
        return rc2
    rc3 = cfg.get("research_case_generator")
    if isinstance(rc3, dict):
        return rc3
    return {}


def _load_case_ids_excel(path: Path, sheet: object = 0) -> Set[str]:
    if not path or not path.exists():
        logger.info(f"[INFO] ResearchCaseRouter: Excel not found: {path}")
        return set()
    try:
        df = pd.read_excel(path, sheet_name=sheet, header=None)
    except Exception as exc:
        logger.warning(f"[WARN] ResearchCaseRouter: Cannot read Excel {path} -> {exc}")
        return set()
    if df is None or df.empty or df.shape[1] < 1:
        return set()
    s = df.iloc[:, 0].astype(str).str.strip()
    case_ids = {x for x in s if x and x.lower() not in {"nan", "none", "null"}}
    logger.info(f"[OK] ResearchCaseRouter: Loaded {len(case_ids)} case_id(s) from {path}")
    return case_ids


@dataclass(frozen=True)
class ConfigPaths:
    source_dir: Path
    renamed_dir: Path
    failed_dir: Path
    dm_excel: Path
    stain_excel: Path
    factor_excel: Optional[Path]
    extra_excel: Optional[Path]
    color_excel: Optional[Path]
    meta_xlsx: Path
    dry_run: bool
    ts_enabled: bool


def load_config(config_path: Path) -> Dict:
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _find_latest_extra_excel(run_dir: Path) -> Optional[Path]:
    hits = sorted(run_dir.glob("extra_field_results_*.xlsx"))
    return hits[-1] if hits else None


def materialize_paths(cfg: Dict) -> ConfigPaths:
    source_dir = Path(cfg["staging_dir"])
    renamed_dir = Path(cfg["final_output_dir"])
    renamed_dir.mkdir(parents=True, exist_ok=True)
    failed_dir = _dated_output_dir(Path(cfg["failed_output_dir"]), cfg)

    dm_excel = Path(cfg.get("datamatrix_output_excel", "")) if cfg.get("datamatrix_output_excel") else Path()
    stain_excel = Path(cfg.get("stain_output_excel", "")) if cfg.get("stain_output_excel") else Path()
    factor_excel = Path(cfg.get("factor_output_excel", "")) if cfg.get("factor_output_excel") else None

    add_xlsx = (
        cfg.get("extra_field_output_excel")
        or cfg.get("extra_field_results_excel")
        or cfg.get("extra_field_excel")
    )
    extra_excel: Optional[Path] = Path(add_xlsx) if add_xlsx else None
    if extra_excel is None:
        current_run_dir = cfg.get("current_run_dir")
        if current_run_dir:
            cand = _find_latest_extra_excel(Path(current_run_dir))
            if cand:
                extra_excel = cand

    color_xlsx = (
        cfg.get("color_marker_output_excel")
        or cfg.get("color_label_results_excel")
        or cfg.get("color_marker_excel")
    )
    color_excel: Optional[Path] = Path(color_xlsx) if color_xlsx else None

    meta_xlsx = Path(cfg.get("metadata_excel_path", "slide_metadata.xlsx"))
    meta_xlsx.parent.mkdir(parents=True, exist_ok=True)

    dry_run = bool(cfg.get("dry_run", False))
    ts_enabled = bool(cfg.get("timestamp_tag_enabled", False))

    return ConfigPaths(
        source_dir=source_dir,
        renamed_dir=renamed_dir,
        failed_dir=failed_dir,
        dm_excel=dm_excel,
        stain_excel=stain_excel,
        factor_excel=factor_excel,
        extra_excel=extra_excel,
        color_excel=color_excel,
        meta_xlsx=meta_xlsx,
        dry_run=dry_run,
        ts_enabled=ts_enabled,
    )


def merge_inputs(dm_path: Path, stain_path: Path) -> pd.DataFrame:
    df_dm = read_excel_safe(dm_path) if dm_path and dm_path.exists() else pd.DataFrame()
    df_st = read_excel_safe(stain_path) if stain_path and stain_path.exists() else pd.DataFrame()

    for c in ["FileName", "DataMatrix", "LabID", "Year", "CaseNumber", "Pot", "BlockID", "Section", "ExtractionMethod"]:
        if c not in df_dm.columns:
            df_dm[c] = None

    if "FileName" not in df_dm.columns and "FileName" in df_st.columns:
        df_dm = df_st[["FileName"]].drop_duplicates().copy()
        for c in ["DataMatrix", "LabID", "Year", "CaseNumber", "Pot", "BlockID", "Section", "ExtractionMethod"]:
            df_dm[c] = None
    elif "FileName" not in df_dm.columns:
        df_dm["FileName"] = []

    if not df_st.empty and "FileName" in df_st.columns:
        stain_col = "Stain" if "Stain" in df_st.columns else None
        if stain_col:
            df_st_min = df_st[["FileName", stain_col]].rename(columns={stain_col: "Stain"})
            df_st_min = df_st_min.dropna(subset=["Stain"]).drop_duplicates(subset=["FileName"], keep="last")
        else:
            df_st_min = pd.DataFrame(columns=["FileName", "Stain"])
    else:
        df_st_min = pd.DataFrame(columns=["FileName", "Stain"])

    return pd.merge(df_dm, df_st_min, on="FileName", how="left")


def merge_factor(df: pd.DataFrame, factor_path: Optional[Path]) -> pd.DataFrame:
    if factor_path is None or not factor_path.exists():
        df["Factor"] = ""
        return df
    df_factor = read_excel_safe(factor_path)
    if "FileName" not in df_factor.columns or "Factor" not in df_factor.columns:
        df["Factor"] = ""
        return df
    df_factor = df_factor.drop_duplicates(subset=["FileName"], keep="last")
    df = df.merge(df_factor[["FileName", "Factor"]], on="FileName", how="left")
    df["Factor"] = df["Factor"].fillna("").astype(str).str.strip()
    return df


def merge_additional_factors(df: pd.DataFrame, additional_path: Optional[Path]) -> pd.DataFrame:
    if additional_path is None or not additional_path.exists():
        return df

    add_df = read_excel_safe(additional_path)
    if add_df.empty:
        return df

    if "SlideStem" not in df.columns:
        if "FileName" in df.columns:
            df["SlideStem"] = df["FileName"].map(_stem)
        else:
            logger.warning("[WARN] main df has no FileName -> cannot build SlideStem; skipping additional merge.")
            return df

    if "SlideStem" not in add_df.columns and "FileName" in add_df.columns:
        add_df["SlideStem"] = add_df["FileName"].map(_stem)

    if "SlideStem" not in add_df.columns:
        logger.warning("[WARN] additional excel has no SlideStem/FileName; skipping additional merge.")
        return df

    add_df = add_df.drop_duplicates(subset=["SlideStem"], keep="last")
    drop_cols = ["FileName"] if "FileName" in add_df.columns else []
    add_df2 = add_df.drop(columns=drop_cols, errors="ignore")
    df2 = df.merge(add_df2, on="SlideStem", how="left")
    logger.info(f"[OK] Extra fields merged from: {additional_path}")
    return df2


def merge_color_marker_results(df: pd.DataFrame, color_excel: Optional[Path]) -> pd.DataFrame:
    if color_excel is None or not color_excel.exists():
        return df
    cdf = read_excel_safe(color_excel)
    if cdf.empty:
        return df

    if "SlideStem" not in cdf.columns and "FileName" in cdf.columns:
        cdf["SlideStem"] = cdf["FileName"].map(_stem)

    if "SlideStem" not in cdf.columns:
        logger.warning("[WARN] color marker excel missing SlideStem/FileName; skipping merge.")
        return df

    keep_cols = [c for c in ["SlideStem", "DetectedColor", "Confidence"] if c in cdf.columns]
    cdf = cdf[keep_cols].drop_duplicates(subset=["SlideStem"], keep="last")

    if "SlideStem" not in df.columns and "FileName" in df.columns:
        df["SlideStem"] = df["FileName"].map(_stem)

    out = df.merge(cdf, on="SlideStem", how="left")
    logger.info(f"[OK] Color marker fields merged from: {color_excel}")
    return out


def _get_color_route_dir(cfg: Dict, detected_color: str, confidence: object) -> Optional[Path]:
    block = cfg.get("color_label_routing", {}) or {}
    routing = block.get("routing", {}) or {}
    colors = block.get("colors", {}) or {}

    color_key = str(detected_color or "").strip().lower()
    try:
        conf = float(confidence) if confidence not in (None, "", "nan") else 0.0
    except Exception:
        conf = 0.0

    min_conf = float(routing.get("min_confidence", 0.60))
    if color_key in ("", "none", "unknown", "no_marker"):
        return None
    if conf < min_conf:
        return None

    color_cfg = colors.get(color_key, {}) if isinstance(colors, dict) else {}
    dest = str(color_cfg.get("destination_dir", "")).strip()
    if dest:
        return _dated_output_dir(Path(dest).expanduser(), cfg)
    return None


def _derive_blacklist_keys(value: str) -> Set[str]:
    out: Set[str] = set()
    s = str(value or "").strip()
    if not s:
        return out
    out.add(s.upper())
    m1 = re.match(r"^([A-Z]\d{2}-\d+)", s, flags=re.IGNORECASE)
    if m1:
        out.add(m1.group(1).upper())
    m2 = re.match(r"^([A-Z]\d{10,})", s, flags=re.IGNORECASE)
    if m2:
        out.add(m2.group(1).upper())
    token = s.split("_")[0].strip()
    if token:
        out.add(token.upper())
    return out


def _row_hits_research_case_list(row: pd.Series, case_ids: Set[str]) -> bool:
    if not case_ids:
        return False
    candidates: Set[str] = set()
    for fld in ["CaseID", "SlideID", "OriginalBase", "FileName"]:
        val = str(row.get(fld, "") or "").strip()
        if val:
            candidates |= _derive_blacklist_keys(val)
    case_ids_u = {x.upper() for x in case_ids}
    return any(c in case_ids_u for c in candidates)


def _get_pasnet_cfg(cfg: Dict) -> Dict:
    pv = cfg.get("pasnet_validator")
    return pv if isinstance(pv, dict) else {}


def _pasnet_enabled(cfg: Dict) -> bool:
    pv = _get_pasnet_cfg(cfg)
    return bool(pv.get("enabled", False))


def _pasnet_report_path(cfg: Dict) -> Optional[Path]:
    pv = _get_pasnet_cfg(cfg)
    p = pv.get("report_xlsx_path") or pv.get("report_path") or pv.get("report")
    if not p:
        return None
    try:
        return Path(str(p))
    except Exception:
        return None


def _pasnet_override_sheet(cfg: Dict) -> str:
    pv = _get_pasnet_cfg(cfg)
    return str(pv.get("report_sheet_overrides", "overrides"))


def _read_pasnet_overrides(cfg: Dict) -> pd.DataFrame:
    report = _pasnet_report_path(cfg)
    if report is None or not report.exists():
        return pd.DataFrame()

    sheet = _pasnet_override_sheet(cfg)
    try:
        df = pd.read_excel(report, sheet_name=sheet, dtype=str)
        if df is None or df.empty or "FileName" not in df.columns:
            return pd.DataFrame()
        for c in ["final_decision", "file_action", "override_stain", "override_slide_id", "dest_dir", "decision_reason"]:
            if c not in df.columns:
                df[c] = ""
        df = df.drop_duplicates(subset=["FileName"], keep="last")
        df["FileName"] = df["FileName"].astype(str).str.strip()
        return df
    except Exception as exc:
        logger.warning(f"[WARN] Cannot read pasnet overrides: {report} ({sheet}) -> {exc}")
        return pd.DataFrame()


def apply_pasnet_overrides_precompute(df_merge: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    if not _pasnet_enabled(cfg):
        return df_merge
    ov = _read_pasnet_overrides(cfg)
    if ov.empty:
        return df_merge

    df = df_merge.merge(ov, on="FileName", how="left", suffixes=("", "_pasnet"))
    df["pasnet_final_decision"] = df["final_decision"].fillna("").astype(str)
    df["pasnet_file_action"] = df["file_action"].fillna("").astype(str)
    df["pasnet_dest_dir"] = df["dest_dir"].fillna("").astype(str)
    df["pasnet_override_slide_id"] = df["override_slide_id"].fillna("").astype(str)
    df["pasnet_decision_reason"] = df["decision_reason"].fillna("").astype(str)

    if "override_stain" in df.columns:
        mask = df["override_stain"].notna() & (df["override_stain"].astype(str).str.strip() != "")
        df.loc[mask, "Stain"] = df.loc[mask, "override_stain"].astype(str)

    return df


def apply_pasnet_overrides_postcompute(df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    if not _pasnet_enabled(cfg) or "pasnet_override_slide_id" not in df.columns:
        return df
    out = df.copy()
    mask = out["pasnet_override_slide_id"].notna() & (out["pasnet_override_slide_id"].astype(str).str.strip() != "")
    out.loc[mask, "SlideID"] = out.loc[mask, "pasnet_override_slide_id"].astype(str)
    return out


def compute_identifiers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["LabID"] = df["LabID"].astype(str).str.strip().str.upper()
    df["Year4"] = df["Year"].apply(to_year4_str).astype("string")
    df["Case6"] = df["CaseNumber"].apply(to_case6_str).astype("string")
    df["Pot"] = df["Pot"].fillna("A").astype(str).str.strip().str.upper().astype("string")
    df["BlockID"] = df["BlockID"].apply(lambda x: to_int_str_no_decimal(x) or "1").astype("string")
    df["Section"] = df["Section"].apply(lambda x: to_int_str_no_decimal(x) or "1").astype("string")
    df["Stain"] = df["Stain"].apply(normalize_stain).astype("string")

    df["DataMatrix_v2"] = df.apply(
        lambda r: build_datamatrix(r["LabID"], r["Year4"], r["Case6"], r["Pot"], r["BlockID"], r["Section"]),
        axis=1,
    )
    df["DataMatrix"] = df["DataMatrix_v2"].where(
        df["DataMatrix_v2"].notna() & (df["DataMatrix_v2"] != ""), df["DataMatrix"]
    )
    df.drop(columns=["DataMatrix_v2"], inplace=True)

    df["CaseID"] = (df["LabID"].fillna("") + df["Year4"].fillna("") + df["Case6"].fillna("")).astype("string")
    df["SlideID"] = ""
    dm_series = df["DataMatrix"].astype(str)
    valid_mask = dm_series.str.strip().ne("") & dm_series.str.strip().str.lower().ne("nan")
    df.loc[valid_mask, "SlideID"] = dm_series[valid_mask].astype(str) + "-" + df["Stain"][valid_mask].astype(str)

    df["OriginalBase"] = df["FileName"].apply(lambda s: Path(str(s)).stem)
    df["SlideStem"] = df["OriginalBase"].astype(str)
    df["method of extraction"] = df.get("ExtractionMethod", "").astype(str).str.strip().apply(
        lambda em: "ROIbase" if safe_str(em).lower() == "roibase" else "Datamatrix"
    )
    return df


def append_legacy_factor_to_slideid(df: pd.DataFrame) -> pd.DataFrame:
    if "Factor" not in df.columns:
        return df
    df = df.copy()
    df["Factor"] = df["Factor"].fillna("").astype(str).str.strip()
    df["SlideID"] = df.apply(lambda r: r["SlideID"] + "-" + r["Factor"] if r["Factor"] else r["SlideID"], axis=1)
    return df


def append_additional_fields_to_slideid(df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    if "SlideID" not in df.columns:
        return df

    sid_cfg = (cfg.get("slide_id_generator", {}) or {})
    extras_cfg = (sid_cfg.get("extras", {}) or {})
    enabled = bool(extras_cfg.get("enabled", sid_cfg.get("append_additional_factors", False)))
    fields = extras_cfg.get("fields", sid_cfg.get("additional_factor_fields", [])) or []
    sep = str(extras_cfg.get("sep", sid_cfg.get("additional_factor_sep", "_")))
    include_zero = bool(extras_cfg.get("include_zero", sid_cfg.get("include_zero_additional_factors", True)))
    default_value = str(extras_cfg.get("default_value", "00"))

    if not enabled or not fields:
        return df

    df = df.copy()
    sid_series = df["SlideID"].astype(str)
    sid_ok = sid_series.str.strip().ne("") & sid_series.str.strip().str.lower().ne("nan")

    for f in fields:
        if f not in df.columns:
            df[f] = default_value
        else:
            df[f] = df[f].fillna("").astype(str).str.strip()

    def _suffix_for_row(row: pd.Series) -> str:
        parts: List[str] = []
        for f in fields:
            v = str(row.get(f, "")).strip() or default_value
            if include_zero:
                parts.append(v)
            elif v and v != default_value:
                parts.append(v)
        return sep.join(parts)

    suffix = df.apply(_suffix_for_row, axis=1).astype(str)
    mask = sid_ok & suffix.str.strip().ne("")
    df.loc[mask, "SlideID"] = df.loc[mask, "SlideID"].astype(str) + sep + suffix[mask].astype(str)
    logger.info(f"[OK] Appended extras to SlideID: {fields} | include_zero={include_zero} | sep='{sep}'")
    return df


def pick_source_and_dest(
    base: str,
    slide_id: str,
    case_id: str,
    source_dir: Path,
    renamed_dir: Path,
    use_case_folder: bool = True,
) -> Tuple[Optional[Path], Optional[Path]]:
    base = Path(str(base)).stem
    matches = list(source_dir.glob(base + ".*"))
    if not matches:
        return None, None
    src_file = prefer_ext(matches) or matches[0]

    if use_case_folder and str(case_id or "").strip():
        dst_folder = renamed_dir / sanitize_fs(str(case_id).strip())
    else:
        dst_folder = renamed_dir

    dst_folder.mkdir(parents=True, exist_ok=True)
    dst_file = unique_path(dst_folder / f"{slide_id}{src_file.suffix}")
    return src_file, dst_file



# =============================================================================
# Palantir DB final-route synchronization
# =============================================================================

def _truthy_cfg(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _status_for_final_route(row_status: str) -> str:
    """Map slide_id_generator row status to a valid core.file_records status.

    Only values in ck_file_records_status are accepted. The migration
    0006_babelshark_failed_status adds 'babelshark_failed' and 'intake_failed'.
    """
    s = str(row_status or "").strip().lower()
    if s in {"success", "research_success", "dicom_renamed"}:
        # File successfully routed to final/; QC trigger will be dispatched next.
        return "qc_pending"
    if s in {"failed", "nan", "error"}:
        # File routed to a failure directory; no downstream trigger should fire.
        return "babelshark_failed"
    # research_original, routed, suspicious, or any other partial-success variant
    # that still requires human review before the next pipeline stage.
    return "manual_review"


def _sync_final_route_records_to_db(records: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Update existing Palantir FileRecord rows after slide_id_generator routes files.

    This function intentionally does not create new FileRecord rows. The ingest
    FileRecord must already exist from collect_slides/register_collected_file.
    The function only updates the same artifact's current path/name/status and
    appends final-route metadata.

    Matching priority:
      1. current_file_path / canonical_path / original_path / source_artifact_id == InputPath
      2. original_filename / current_filename == OriginalFileName or FileName
      3. current_file_path / canonical_path == OutputPath, for reruns

    This keeps slide_metadata.xlsx as a report, not as the source of truth.
    The source of truth remains the existing DB FileRecord created during ingest.
    """

    db_cfg = cfg.get("database_manager") if isinstance(cfg.get("database_manager"), dict) else {}
    enabled = _truthy_cfg(db_cfg.get("sync_final_route_enabled"), default=True)
    if not enabled:
        return {
            "ok": True,
            "enabled": False,
            "rows_seen": len(records or []),
            "updated": 0,
            "missing_records": 0,
            "skipped": 0,
            "errors": [],
        }

    try:
        from pathoryx_enterprise.db.session import get_session
        from pathoryx_enterprise.db.models.core import FileRecord
        from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
        from sqlalchemy import select, or_
    except Exception as exc:
        return {
            "ok": False,
            "enabled": True,
            "rows_seen": len(records or []),
            "updated": 0,
            "missing_records": 0,
            "skipped": 0,
            "errors": [{"row_index": None, "error": f"DB import failed: {exc}"}],
        }

    def _resolve_text_path(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return str(Path(text).resolve())
        except Exception:
            return text

    updated = 0
    missing = 0
    skipped = 0
    errors: List[Dict[str, Any]] = []

    try:
        with get_session() as session:
            for idx, rec in enumerate(records or []):
                try:
                    with session.begin_nested():  # SAVEPOINT: per-row isolation
                        input_path = _resolve_text_path(rec.get("InputPath"))
                        output_path = _resolve_text_path(rec.get("OutputPath"))
                        original_filename = str(rec.get("OriginalFileName") or "").strip()
                        file_name = str(rec.get("FileName") or "").strip()
                        new_filename = str(rec.get("NewFileName") or "").strip()
                        row_status = str(rec.get("status") or "").strip()

                        if not input_path and not output_path and not original_filename and not file_name:
                            skipped += 1
                            continue

                        candidates = []
                        if input_path:
                            candidates.extend(
                                session.execute(
                                    select(FileRecord).where(
                                        or_(
                                            FileRecord.current_file_path == input_path,
                                            FileRecord.canonical_path == input_path,
                                            FileRecord.original_path == input_path,
                                            FileRecord.source_artifact_id == input_path,
                                        )
                                    )
                                ).scalars().all()
                            )

                        if output_path:
                            candidates.extend(
                                session.execute(
                                    select(FileRecord).where(
                                        or_(
                                            FileRecord.current_file_path == output_path,
                                            FileRecord.canonical_path == output_path,
                                        )
                                    )
                                ).scalars().all()
                            )

                        names = [x for x in [original_filename, file_name] if x]
                        if names:
                            candidates.extend(
                                session.execute(
                                    select(FileRecord).where(
                                        or_(
                                            FileRecord.original_filename.in_(names),
                                            FileRecord.current_filename.in_(names),
                                        )
                                    )
                                ).scalars().all()
                            )

                        unique_by_id = {}
                        for c in candidates:
                            unique_by_id[getattr(c, "internal_id", id(c))] = c
                        candidates = list(unique_by_id.values())

                        if not candidates:
                            missing += 1
                            continue

                        # Prefer exact path match over filename-only match.
                        def _score(candidate: FileRecord) -> int:
                            score = 0
                            if input_path and candidate.current_file_path == input_path:
                                score += 100
                            if input_path and candidate.canonical_path == input_path:
                                score += 90
                            if input_path and candidate.original_path == input_path:
                                score += 80
                            if input_path and candidate.source_artifact_id == input_path:
                                score += 70
                            if output_path and candidate.current_file_path == output_path:
                                score += 60
                            if original_filename and candidate.original_filename == original_filename:
                                score += 30
                            if file_name and candidate.original_filename == file_name:
                                score += 20
                            return score

                        record = sorted(candidates, key=_score, reverse=True)[0]
                        old_current_path = record.current_file_path

                        if output_path:
                            record.current_file_path = output_path
                            record.canonical_path = output_path

                        if new_filename:
                            record.current_filename = new_filename

                        record.status = _status_for_final_route(row_status)

                        # Make a fresh dict so SQLAlchemy reliably persists JSON changes.
                        raw_meta = record.metadata_json or {}
                        meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}

                        route_meta = {
                            "input_path": input_path,
                            "output_path": output_path,
                            "final_path": output_path,
                            "original_filename": original_filename,
                            "new_filename": new_filename,
                            "final_filename": new_filename,
                            "previous_path": old_current_path,
                            "slide_id": str(rec.get("SlideID") or "").strip(),
                            "case_id": str(rec.get("CaseID") or "").strip(),
                            "scan_time_utc": str(rec.get("ScanTime_UTC") or "").strip(),
                            "analysis_time_utc": str(rec.get("AnalysisTime_UTC") or "").strip(),
                            "route_status": row_status,
                            "detected_color": str(rec.get("DetectedColor") or "").strip(),
                            "routing_type": str(rec.get("RoutingType") or rec.get("RouteType") or "final_route").strip(),
                            "updated_by": "slide_id_generator",
                            "updated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        }
                        meta["final_route"] = route_meta
                        if output_path:
                            meta["final_path"] = output_path
                        if new_filename:
                            meta["final_filename"] = new_filename
                        meta["lifecycle_stage"] = "final_routed"
                        record.metadata_json = meta

                        raw_output_meta = record.output_metadata_json or {}
                        output_meta = dict(raw_output_meta) if isinstance(raw_output_meta, dict) else {}
                        if output_path:
                            output_meta["final_path"] = output_path
                        if new_filename:
                            output_meta["final_filename"] = new_filename
                        output_meta["lifecycle_stage"] = "final_routed"
                        record.output_metadata_json = output_meta

                        EventStoreRepository(session).append(
                            event_type="babelshark.FILE_FINAL_ROUTED",
                            aggregate_type="file_record",
                            aggregate_id=record.global_artifact_id or str(record.internal_id),
                            service_name="babelshark",
                            event_payload=route_meta,
                            file_record_internal_id=record.internal_id,
                            global_artifact_id=record.global_artifact_id,
                        )
                        updated += 1

                except Exception as row_exc:
                    errors.append({"row_index": idx, "error": str(row_exc)})

    except Exception as exc:
        errors.append({"row_index": None, "error": str(exc)})

    return {
        "ok": len(errors) == 0,
        "enabled": True,
        "rows_seen": len(records or []),
        "updated": updated,
        "missing_records": missing,
        "skipped": skipped,
        "errors": errors[:20],
        "error_count": len(errors),
    }


def run_pipeline(cfg: Dict) -> None:
    paths = materialize_paths(cfg)

    rcfg = _get_research_router_cfg(cfg)
    research_enabled = bool(rcfg.get("enabled", False))
    research_case_ids: Set[str] = set()
    research_dest_dir: Optional[Path] = None

    if research_enabled:
        excel_path = Path(str(rcfg.get("case_list_excel", ""))).expanduser()
        sheet = rcfg.get("excel_sheet", 0)
        research_case_ids = _load_case_ids_excel(excel_path, sheet=sheet)
        dest = str(rcfg.get("destination_dir", "")).strip()
        if dest:
            research_dest_dir = _dated_output_dir(Path(dest).expanduser(), cfg)
        else:
            logger.warning("[WARN] ResearchCaseRouter enabled but destination_dir is empty; routing will be skipped.")
            research_enabled = False

    df_merge = merge_inputs(paths.dm_excel, paths.stain_excel)
    if df_merge.empty:
        logger.warning("[WARN] Merged inputs are empty; nothing to process.")
        df_merge = pd.DataFrame(columns=["FileName"])

    df_merge = merge_factor(df_merge, paths.factor_excel)
    if "SlideStem" not in df_merge.columns and "FileName" in df_merge.columns:
        df_merge["SlideStem"] = df_merge["FileName"].map(_stem)

    if paths.extra_excel is not None:
        df_merge = merge_additional_factors(df_merge, paths.extra_excel)

    if paths.color_excel is not None:
        df_merge = merge_color_marker_results(df_merge, paths.color_excel)

    df_merge = apply_pasnet_overrides_precompute(df_merge, cfg)

    df = compute_identifiers(df_merge).fillna("")
    df = apply_pasnet_overrides_postcompute(df, cfg)
    df = append_legacy_factor_to_slideid(df)
    df = append_additional_fields_to_slideid(df, cfg)

    analysis_ts = _utc_ts_iso_z()
    runtime_value = str(cfg.get("run_timestamp", analysis_ts))
    moved: List[str] = []
    records: List[Dict[str, str]] = []

    for _, row in df.iterrows():
        original_base = str(row.get("OriginalBase", ""))
        slide_id_raw = str(row.get("SlideID") or "").strip()
        case_id = str(row.get("CaseID") or "").strip()

        detected_color = str(row.get("DetectedColor", "")).strip().lower()
        color_conf = row.get("Confidence", 0)

        color_route_dir = None
        if (cfg.get("color_label_routing") or {}).get("enabled", False):
            color_route_dir = _get_color_route_dir(cfg, detected_color, color_conf)

        blacklist_route = research_enabled and research_dest_dir is not None and _row_hits_research_case_list(row, research_case_ids)
        is_research = color_route_dir is not None or blacklist_route

        base_stem = Path(original_base).stem
        dicom_folder = paths.source_dir / base_stem
        matches = list(paths.source_dir.glob(base_stem + ".*"))

        # DICOM-only
        if not matches and dicom_folder.is_dir():
            scan_ts_iso_z = _scan_ts_iso_z_db_first(dicom_folder, is_dicom_folder=True)
            scan_ts_str = scan_ts_iso_z or ""

            ts_suffix = ""
            if (not is_research) and paths.ts_enabled and scan_ts_str:
                ts_suffix = f"_UTC{scan_ts_str}"

            slideid_valid = bool(slide_id_raw) and not safe_str(slide_id_raw).lower().startswith("nan-")
            if is_research and not slideid_valid:
                effective_folder_name = sanitize_fs(dicom_folder.name)
            else:
                effective_folder_name = sanitize_fs((slide_id_raw or dicom_folder.name) + ts_suffix)

            dest_root_dir = paths.renamed_dir
            if color_route_dir is not None:
                dest_root_dir = color_route_dir
            elif blacklist_route and research_dest_dir is not None:
                dest_root_dir = research_dest_dir

            if is_research:
                dst_parent = dest_root_dir
            elif case_id:
                dst_parent = dest_root_dir / sanitize_fs(case_id)
            else:
                dst_parent = paths.failed_dir

            dst_parent.mkdir(parents=True, exist_ok=True)
            dst_folder = unique_path(dst_parent / effective_folder_name)

            input_path = str(dicom_folder)
            output_path = ""
            new_name = ""
            status = "research_original" if (is_research and not slideid_valid) else ("research_success" if is_research else "dicom_renamed")

            if not paths.dry_run:
                try:
                    shutil.move(str(dicom_folder), str(dst_folder))
                    output_path = str(dst_folder)
                    new_name = dst_folder.name
                    logger.info(f"[OK-DICOM] {dicom_folder.name} -> {dst_folder}")
                except Exception as exc:
                    status = "failed"
                    logger.warning(f"[WARN] Could not move DICOM folder {dicom_folder} -> {dst_folder}: {exc}")
            else:
                output_path = str(dst_folder)
                new_name = dst_folder.name
                logger.info(f"[DRYRUN-DICOM] Would move DICOM folder {dicom_folder} -> {dst_folder}")

            records.append({
                "FileName": str(row.get("FileName", "")),
                "SlideID": effective_folder_name if is_research and not slideid_valid else (slide_id_raw if slide_id_raw else effective_folder_name),
                "CaseID": case_id,
                "OriginalFileName": "",
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "ScanTime_UTC": scan_ts_str,
                "AnalysisTime_UTC": analysis_ts,
                "method of extraction": str(row.get("method of extraction", row.get("ExtractionMethod", "metadata"))),
                "status": status,
                "DetectedColor": detected_color,
            })
            continue

        if not matches:
            logger.info(f"[MISS] No WSI found for base: {original_base}")
            continue

        src_file = prefer_ext(matches) or matches[0]
        input_path = str(src_file)
        scan_ts = _scan_ts_iso_z_db_first(src_file, is_dicom_folder=False)
        scan_ts_str = scan_ts or ""

        slideid_valid = bool(slide_id_raw) and not safe_str(slide_id_raw).lower().startswith("nan-")

        # RESEARCH HAS PRIORITY OVER PASNET AND ROUTINE
        if is_research and (not slideid_valid or not case_id):
            dest_root_dir = color_route_dir if color_route_dir is not None else research_dest_dir
            assert dest_root_dir is not None
            dest_root_dir.mkdir(parents=True, exist_ok=True)
            dst_file = unique_path(dest_root_dir / src_file.name)

            if not paths.dry_run:
                try:
                    shutil.move(str(src_file), str(dst_file))
                    moved.append(src_file.name)
                    logger.info(f"[ROUTED] {src_file.name} -> {dst_file}")
                    status = "research_original"
                    output_path = str(dst_file)
                    new_name = dst_file.name
                except Exception as exc:
                    logger.warning(f"[WARN] Could not move research original file {src_file}: {exc}")
                    status = "failed"
                    output_path = ""
                    new_name = ""
            else:
                logger.info(f"[DRYRUN-RESEARCH-ORIG] Would move {src_file.name} -> {dst_file}")
                status = "research_original"
                output_path = str(dst_file)
                new_name = dst_file.name

            records.append({
                "FileName": str(row.get("FileName", "")),
                "SlideID": slide_id_raw,
                "CaseID": case_id,
                "OriginalFileName": src_file.name,
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "ScanTime_UTC": scan_ts_str,
                "AnalysisTime_UTC": analysis_ts,
                "method of extraction": str(row.get("method of extraction", "metadata")),
                "status": status,
                "DetectedColor": detected_color,
            })
            continue

        if is_research and slideid_valid and case_id:
            ts_suffix = ""  # never add timestamp for research
            effective_slide_id = sanitize_fs(slide_id_raw + ts_suffix)

            dest_root_dir = color_route_dir if color_route_dir is not None else research_dest_dir
            assert dest_root_dir is not None

            src_file2, dst_file = pick_source_and_dest(
                base=original_base,
                slide_id=effective_slide_id,
                case_id=case_id,
                source_dir=paths.source_dir,
                renamed_dir=dest_root_dir,
                use_case_folder=False,
            )
            if src_file2 is not None:
                src_file = src_file2

            if src_file is None or dst_file is None:
                logger.info(f"[MISS] No WSI found for base (research rename step): {original_base}")
                status = "failed"
                output_path = ""
                new_name = ""
            else:
                if not paths.dry_run:
                    try:
                        shutil.move(str(src_file), str(dst_file))
                        moved.append(src_file.name)
                        logger.info(f"[ROUTED] {src_file.name} -> {dst_file}")
                        status = "research_success"
                        output_path = str(dst_file)
                        new_name = dst_file.name
                    except Exception as exc:
                        logger.warning(f"[WARN] Could not move research file {src_file} -> {dst_file}: {exc}")
                        status = "failed"
                        output_path = ""
                        new_name = ""
                else:
                    logger.info(f"[DRYRUN-RESEARCH] Would move {src_file.name} -> {dst_file}")
                    status = "research_success"
                    output_path = str(dst_file)
                    new_name = dst_file.name

            records.append({
                "FileName": str(row.get("FileName", "")),
                "SlideID": effective_slide_id,
                "CaseID": case_id,
                "OriginalFileName": src_file.name if src_file else "",
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "ScanTime_UTC": scan_ts_str,
                "AnalysisTime_UTC": analysis_ts,
                "method of extraction": str(row.get("method of extraction", "metadata")),
                "status": status,
                "DetectedColor": detected_color,
            })
            continue

        # ONLY NON-RESEARCH CASES GET PASNET ROUTING
        pas_action = str(row.get("pasnet_file_action") or "").strip().upper()
        pas_decision = str(row.get("pasnet_final_decision") or "").strip().upper()
        pas_dest = str(row.get("pasnet_dest_dir") or "").strip()
        if pas_decision == "SUSPICIOUS" and not pas_action:
            pas_action = "MOVE_TO_SUSPICIOUS"

        if pas_action in {"MOVE_TO_SUSPICIOUS", "MOVE_TO_CASELIST_FOLDER"}:
            status = "suspicious" if pas_action == "MOVE_TO_SUSPICIOUS" else "routed"
            if pas_action == "MOVE_TO_SUSPICIOUS":
                pv = _get_pasnet_cfg(cfg)
                raw_dest_root = Path(str(pv.get("suspicious_output_dir", paths.failed_dir)))
            else:
                raw_dest_root = Path(pas_dest) if pas_dest else paths.failed_dir

            dest_root = raw_dest_root if raw_dest_root.name == _output_day_from_cfg(cfg) else _dated_output_dir(raw_dest_root, cfg)
            dst_folder = dest_root
            dst_folder.mkdir(parents=True, exist_ok=True)
            dst_file = unique_path(dst_folder / src_file.name)

            if not paths.dry_run:
                try:
                    shutil.move(str(src_file), str(dst_file))
                    moved.append(src_file.name)
                    logger.info(f"[{status.upper()}] {src_file.name} -> {dst_file}")
                    output_path = str(dst_file)
                    new_name = dst_file.name
                except Exception as exc:
                    logger.warning(f"[WARN] Could not move {status} file {src_file}: {exc}")
                    output_path = ""
                    new_name = ""
            else:
                logger.info(f"[DRYRUN-{status.upper()}] Would move {src_file.name} -> {dst_file}")
                output_path = str(dst_file)
                new_name = dst_file.name

            records.append({
                "FileName": str(row.get("FileName", "")),
                "SlideID": slide_id_raw,
                "CaseID": case_id,
                "OriginalFileName": src_file.name,
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "ScanTime_UTC": scan_ts_str,
                "AnalysisTime_UTC": analysis_ts,
                "method of extraction": str(row.get("method of extraction", row.get("ExtractionMethod", "metadata"))),
                "status": status,
                "pasnet_final_decision": pas_decision,
                "pasnet_file_action": pas_action,
                "pasnet_dest_dir": str(dest_root),
                "pasnet_decision_reason": str(row.get("pasnet_decision_reason") or ""),
                "DetectedColor": detected_color,
            })
            continue

        # Routine unreadable/unresolved -> failed
        if not slideid_valid or not case_id:
            status = "failed"
            if not paths.dry_run:
                dst_failed = unique_path(paths.failed_dir / src_file.name)
                try:
                    shutil.move(str(src_file), str(dst_failed))
                    moved.append(src_file.name)
                    logger.info(f"[FAILED] {src_file.name} -> {dst_failed.name}")
                    output_path = str(dst_failed)
                    new_name = dst_failed.name
                except Exception as exc:
                    logger.warning(f"[WARN] Could not move failed file {src_file}: {exc}")
                    output_path = ""
                    new_name = ""
            else:
                logger.info(f"[DRYRUN-FAILED] Would move {src_file.name} -> failed/")
                output_path = ""
                new_name = ""

            records.append({
                "FileName": str(row.get("FileName", "")),
                "SlideID": slide_id_raw,
                "CaseID": case_id,
                "OriginalFileName": src_file.name,
                "NewFileName": new_name,
                "InputPath": input_path,
                "OutputPath": output_path,
                "ScanTime_UTC": scan_ts_str,
                "AnalysisTime_UTC": analysis_ts,
                "method of extraction": str(row.get("method of extraction", "metadata")),
                "status": status,
                "pasnet_final_decision": str(row.get("pasnet_final_decision") or ""),
                "pasnet_file_action": str(row.get("pasnet_file_action") or ""),
                "pasnet_dest_dir": str(row.get("pasnet_dest_dir") or ""),
                "pasnet_decision_reason": str(row.get("pasnet_decision_reason") or ""),
                "DetectedColor": detected_color,
            })
            continue

        # Routine readable
        ts_suffix = ""
        if paths.ts_enabled and scan_ts_str:
            ts_suffix = f"_UTC{scan_ts_str}"

        effective_slide_id = sanitize_fs(slide_id_raw + ts_suffix)
        dest_root_dir = paths.renamed_dir

        src_file2, dst_file = pick_source_and_dest(
            base=original_base,
            slide_id=effective_slide_id,
            case_id=case_id,
            source_dir=paths.source_dir,
            renamed_dir=dest_root_dir,
        )
        if src_file2 is not None:
            src_file = src_file2

        if src_file is None or dst_file is None:
            logger.info(f"[MISS] No WSI found for base (routine rename step): {original_base}")
            status = "failed"
            output_path = ""
            new_name = ""
        else:
            if not paths.dry_run:
                try:
                    shutil.move(str(src_file), str(dst_file))
                    moved.append(src_file.name)
                    logger.info(f"[OK] {src_file.name} -> {dst_file.name}")
                    status = "success"
                    output_path = str(dst_file)
                    new_name = dst_file.name
                except Exception as exc:
                    logger.warning(f"[WARN] Could not move {src_file} -> {dst_file}: {exc}")
                    status = "failed"
                    output_path = ""
                    new_name = ""
            else:
                logger.info(f"[DRYRUN] Would move {src_file.name} -> {dst_file.name}")
                status = "success"
                output_path = str(dst_file)
                new_name = dst_file.name

        records.append({
            "FileName": str(row.get("FileName", "")),
            "SlideID": effective_slide_id,
            "CaseID": case_id,
            "OriginalFileName": src_file.name if src_file else "",
            "NewFileName": new_name,
            "InputPath": input_path,
            "OutputPath": output_path,
            "ScanTime_UTC": scan_ts_str,
            "AnalysisTime_UTC": analysis_ts,
            "method of extraction": str(row.get("method of extraction", "metadata")),
            "status": status,
            "DetectedColor": detected_color,
        })

    if not paths.dry_run:
        for file in paths.source_dir.glob("*"):
            if file.is_file() and (file.name not in moved):
                try:
                    dst_unmatched = unique_path(paths.failed_dir / file.name)
                    shutil.move(str(file), str(dst_unmatched))
                    logger.info(f"[INFO] -> failed/: {file.name}")
                except Exception as exc:
                    logger.warning(f"[WARN] Could not move unmatched {file.name}: {exc}")
    else:
        logger.info("[DRYRUN] Skip moving unmatched files to failed/.")

    try:
        db_sync_summary = _sync_final_route_records_to_db(records, cfg)
        logger.info(f"[DB] Final-route sync summary: {db_sync_summary}")
    except Exception as exc:
        logger.warning(f"[WARN] Final-route DB sync failed: {exc}")

    try:
        df_records = pd.DataFrame.from_records(records)
    except Exception:
        df_records = pd.DataFrame()

    try:
        df_excel = df.copy()
        if not df_records.empty and "FileName" in df_excel.columns:
            cols_for_merge = [
                "FileName", "OriginalFileName", "NewFileName", "InputPath", "OutputPath",
                "ScanTime_UTC", "AnalysisTime_UTC", "status"
            ]
            cols_for_merge = [c for c in cols_for_merge if c in df_records.columns]
            df_excel = df_excel.merge(df_records[cols_for_merge], on="FileName", how="left")

        df_excel["RunTime"] = runtime_value
        drop_cols = ["Year4", "Case6", "ExtractionMethod"]
        for col in drop_cols:
            if col in df_excel.columns:
                df_excel = df_excel.drop(columns=[col])

        df_excel = df_excel.fillna("")
    except Exception as exc:
        logger.warning(f"[WARN] Could not build full_merged DataFrame: {exc}")
        df_excel = df.copy().fillna("")

    try:
        atomic_write_excel({"full_merged": df_excel}, paths.meta_xlsx)
        logger.info(f"[OK] Excel (full_merged) saved: {paths.meta_xlsx}")
    except Exception as exc:
        logger.warning(f"[WARN] Could not write Excel: {exc}")

    logger.info("[DONE] slide_id_generator finished.")


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Slide ID generator & Excel writer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run pipeline with config")
    p_run.add_argument("--config", type=str, required=True, help="Path to config YAML")
    p_run.add_argument("--log-level", type=str, default="INFO", help="Logging level")

    p_val = sub.add_parser("validate", help="Validate config and paths")
    p_val.add_argument("--config", type=str, required=True, help="Path to config YAML")
    p_val.add_argument("--log-level", type=str, default="INFO", help="Logging level")

    sub.add_parser("version", help="Show version")
    args = parser.parse_args(argv)

    if args.command == "version":
        setup_logging("INFO")
        logger.info("slide_id_generator 2.1.3")
        return 0

    if args.command in {"run", "validate"}:
        setup_logging(args.log_level)
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            logger.error(f"[ERROR] Config file not found: {cfg_path}")
            return 2
        try:
            cfg = load_config(cfg_path)
        except Exception as exc:
            logger.error(f"[ERROR] Failed to load config: {exc}")
            return 2

        dll_cfg = (cfg.get("dll_paths") or {})
        dll_path = dll_cfg.get("openslide_dll")
        _setup_openslide(dll_path)

        required = ["staging_dir", "final_output_dir", "failed_output_dir"]
        missing = [k for k in required if k not in cfg]
        if missing:
            logger.error(f"[ERROR] Missing config keys: {', '.join(missing)}")
            return 2

        try:
            _ = materialize_paths(cfg)
        except Exception as exc:
            logger.error(f"[ERROR] Invalid path values: {exc}")
            return 2

        if args.command == "validate":
            logger.info("[OK] Config looks valid.")
            return 0

        if args.command == "run":
            run_pipeline(cfg)
            return 0

    return 1


if __name__ == "__main__":
    sys.exit(cli())