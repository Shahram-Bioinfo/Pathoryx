#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
runner_daily_enterprise.py — production-grade enterprise daily runner for WSI-Babel-Shark

Core behavior
-------------
- Collects new slides from watch_dir into staging_dir
- Extracts labels
- Optionally detects color markers
- Reads DataMatrix
- Runs ROI fallback only for routine unreadable slides
- Extracts stain
- Optionally runs extra fields and Pasnet validation
- Creates final slide IDs and routes outputs
- Optionally writes outputs to database_manager.py
- Merges per-run artifacts into daily durable artifacts

Important compatibility notes
-----------------------------
- Processed-check is delegated to database_manager.py check
- Database write is delegated to database_manager.py run
- No SQLite fallback is used for processed-check
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import yaml
from tqdm import tqdm

__module_prog__ = "WSI-Babel-Shark"
__version__ = "5.0.0-enterprise-db-compatible"

import os
from pathlib import Path

def setup_openslide_from_config(config):
    dll_dir = (config.get("dll_paths", {}) or {}).get("openslide_dll")
    if not dll_dir:
        return None
    dll_path = Path(str(dll_dir))
    if not dll_path.exists():
        print(f"[WARN] OpenSlide DLL path not found: {dll_dir}")
        return None
    os.environ["PATH"] = str(dll_path) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(dll_path))
    print(f"[INFO] OpenSlide DLL loaded from: {dll_path}")
    return str(dll_path)
# =============================================================================
# Logging
# =============================================================================


def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def _attach_file_logger(log_file: Path) -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                if Path(getattr(handler, "baseFilename", "")).resolve() == log_file.resolve():
                    return
            except Exception:
                pass

    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(root.level)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(fh)
    logging.info(f"[LOG] File logging enabled: {log_file}")


# =============================================================================
# Atomic IO helpers
# =============================================================================


def _atomic_write_bytes(dst: Path, data: bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst.parent, delete=False) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, dst)


def _atomic_write_text(dst: Path, text: str, encoding: str = "utf-8") -> None:
    _atomic_write_bytes(dst, text.encode(encoding))


def _atomic_write_yaml(dst: Path, obj: Dict[str, Any]) -> None:
    _atomic_write_text(dst, yaml.safe_dump(obj, allow_unicode=True, sort_keys=False))


def _atomic_copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst.parent, delete=False, suffix=dst.suffix) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(src, tmp_path)
        os.replace(tmp_path, dst)
    finally:
        with contextlib.suppress(Exception):
            if tmp_path.exists():
                tmp_path.unlink()


def _write_excel_sheets_atomic(path: Path, sheets: Dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".xlsx") as tmp:
        tmp_path = Path(tmp.name)
    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            for sheet_name, df in sheets.items():
                safe_name = str(sheet_name)[:31] if sheet_name else "Sheet1"
                (df if df is not None else pd.DataFrame()).to_excel(writer, sheet_name=safe_name, index=False)
        os.replace(tmp_path, path)
    finally:
        with contextlib.suppress(Exception):
            if tmp_path.exists():
                tmp_path.unlink()


# =============================================================================
# File lock
# =============================================================================


class LockTimeoutError(RuntimeError):
    pass


class FileLock:
    def __init__(self, lock_path: Path, timeout_seconds: int = 1800, poll_interval: float = 1.0) -> None:
        self.lock_path = Path(lock_path)
        self.timeout_seconds = int(timeout_seconds)
        self.poll_interval = float(poll_interval)
        self.fd: Optional[int] = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout_seconds
        while True:
            try:
                self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode("utf-8"))
                return
            except FileExistsError:
                if time.time() >= deadline:
                    raise LockTimeoutError(f"Timed out waiting for lock: {self.lock_path}")
                time.sleep(self.poll_interval)

    def release(self) -> None:
        if self.fd is not None:
            with contextlib.suppress(Exception):
                os.close(self.fd)
            self.fd = None
        with contextlib.suppress(FileNotFoundError):
            self.lock_path.unlink()

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


# =============================================================================
# Runtime data model
# =============================================================================


@dataclass(frozen=True)
class ArtifactPolicy:
    stain_pdf_mode: str = "disabled"  # disabled | daily_append | keep_run_only
    materialize_daily_excels: bool = True
    keep_daily_failed_images: bool = True
    keep_temp_config: bool = False
    keep_run_workspace: bool = False
    merge_lock_timeout_seconds: int = 1800
    cleanup_stale_workspaces_hours: int = 24


@dataclass(frozen=True)
class RuntimePaths:
    run_id: str
    day_id: str
    daily_dir: Path
    run_workspace_dir: Path
    temp_config_path: Path
    temp_config_roi_path: Path
    lock_path: Path
    daily_log_file: Path
    daily_manifest_jsonl: Path

    label_crops_dir: Path
    staging_dir: Path
    failed_output_dir: Path

    run_datamatrix_excel: Path
    run_stain_excel: Path
    run_stain_pdf: Path
    run_extra_field_excel: Path
    run_slide_metadata_excel: Path
    run_pasnet_report_excel: Path
    run_color_excel: Path
    run_roi_csv: Path
    run_datamatrix_failed_dir: Path
    run_fallback_failed_dir: Path
    run_research_no_roi_dir: Path

    daily_datamatrix_excel: Path
    daily_stain_excel: Path
    daily_stain_pdf: Path
    daily_extra_field_excel: Path
    daily_slide_metadata_excel: Path
    daily_pasnet_report_excel: Path
    daily_color_excel: Path
    daily_datamatrix_failed_dir: Path
    daily_fallback_failed_dir: Path


# =============================================================================
# Generic helpers
# =============================================================================


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_wsi_exts() -> Tuple[str, ...]:
    return (".svs", ".ndpi", ".tif", ".tiff", ".scn", ".mrxs", ".bif", ".png", ".jpg", ".jpeg")


def _load_config(config_path: Path) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _read_artifact_policy(cfg: Dict[str, Any]) -> ArtifactPolicy:
    src = cfg.get("artifact_policy") if isinstance(cfg.get("artifact_policy"), dict) else {}
    return ArtifactPolicy(
        stain_pdf_mode=str(src.get("stain_pdf_mode", "disabled")).strip().lower(),
        materialize_daily_excels=bool(src.get("materialize_daily_excels", True)),
        keep_daily_failed_images=bool(src.get("keep_daily_failed_images", True)),
        keep_temp_config=bool(src.get("keep_temp_config", False)),
        keep_run_workspace=bool(src.get("keep_run_workspace", False)),
        merge_lock_timeout_seconds=int(src.get("merge_lock_timeout_seconds", 1800)),
        cleanup_stale_workspaces_hours=int(src.get("cleanup_stale_workspaces_hours", 24)),
    )


def _read_excel_any(path: Path) -> Dict[str, pd.DataFrame]:
    try:
        return pd.read_excel(path, sheet_name=None)
    except Exception:
        df = pd.read_excel(path)
        return {"Sheet1": df}


def _normalize_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if str(out[col].dtype) == "object":
            out[col] = out[col].astype("string")
    return out


def _choose_key_columns(df_old: pd.DataFrame, df_new: pd.DataFrame, preferred: Sequence[str]) -> List[str]:
    cols_old = set(map(str, df_old.columns))
    cols_new = set(map(str, df_new.columns))
    return [c for c in preferred if c in cols_old and c in cols_new]


def _merge_dataframes(
    df_old: pd.DataFrame,
    df_new: pd.DataFrame,
    *,
    preferred_keys: Sequence[str],
    inject_run_id: Optional[str] = None,
) -> pd.DataFrame:
    old_df = _normalize_text_columns(df_old if df_old is not None else pd.DataFrame())
    new_df = _normalize_text_columns(df_new if df_new is not None else pd.DataFrame())

    if inject_run_id and not new_df.empty and "RunID" not in new_df.columns:
        new_df.insert(0, "RunID", inject_run_id)

    if old_df.empty:
        return new_df.copy()
    if new_df.empty:
        return old_df.copy()

    all_cols = list(dict.fromkeys(list(old_df.columns) + list(new_df.columns)))
    old_df = old_df.reindex(columns=all_cols)
    new_df = new_df.reindex(columns=all_cols)

    merged = pd.concat([old_df, new_df], ignore_index=True)
    keys = _choose_key_columns(old_df, new_df, preferred_keys)
    if keys:
        merged = merged.drop_duplicates(subset=keys, keep="last")
    else:
        merged = merged.drop_duplicates(keep="last")
    return merged


def merge_excel_into_daily(
    run_excel: Path,
    daily_excel: Path,
    *,
    preferred_keys: Sequence[str],
    inject_run_id: Optional[str] = None,
) -> None:
    if not run_excel.exists():
        logging.info(f"[INFO] Skip Excel merge (run file missing): {run_excel}")
        return

    run_sheets = _read_excel_any(run_excel)
    if not daily_excel.exists():
        out_sheets: Dict[str, pd.DataFrame] = {}
        for sheet_name, df_run in run_sheets.items():
            if inject_run_id and "RunID" not in df_run.columns:
                df_run = df_run.copy()
                df_run.insert(0, "RunID", inject_run_id)
            out_sheets[sheet_name] = df_run
        _write_excel_sheets_atomic(daily_excel, out_sheets)
        logging.info(f"[OK] Created daily Excel: {daily_excel}")
        return

    daily_sheets = _read_excel_any(daily_excel)
    out_sheets = dict(daily_sheets)
    for sheet_name, df_run in run_sheets.items():
        df_old = daily_sheets.get(sheet_name, pd.DataFrame())
        out_sheets[sheet_name] = _merge_dataframes(
            df_old, df_run, preferred_keys=preferred_keys, inject_run_id=inject_run_id
        )
    _write_excel_sheets_atomic(daily_excel, out_sheets)
    logging.info(f"[OK] Updated daily Excel: {daily_excel}")


def append_pdf_into_daily(run_pdf: Path, daily_pdf: Path) -> None:
    if not run_pdf.exists():
        logging.info(f"[INFO] Skip PDF append (run PDF missing): {run_pdf}")
        return

    if not daily_pdf.exists():
        _atomic_copy_file(run_pdf, daily_pdf)
        logging.info(f"[OK] Created daily PDF: {daily_pdf}")
        return

    merger = None
    try:
        try:
            from PyPDF2 import PdfMerger  # type: ignore
        except Exception:
            from pypdf import PdfMerger  # type: ignore

        merger = PdfMerger()
        merger.append(str(daily_pdf))
        merger.append(str(run_pdf))

        with tempfile.NamedTemporaryFile(dir=daily_pdf.parent, delete=False, suffix=".pdf") as tmp:
            tmp_path = Path(tmp.name)
        try:
            with tmp_path.open("wb") as fh:
                merger.write(fh)
            os.replace(tmp_path, daily_pdf)
        finally:
            with contextlib.suppress(Exception):
                if tmp_path.exists():
                    tmp_path.unlink()
        logging.info(f"[OK] Appended run PDF into daily PDF: {daily_pdf}")
    finally:
        if merger is not None:
            with contextlib.suppress(Exception):
                merger.close()


def _copy_new_support_files(src_dir: Path, dst_dir: Path) -> int:
    if not src_dir.exists():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for p in src_dir.iterdir():
        if not p.is_file():
            continue
        target = dst_dir / p.name
        if not target.exists():
            _atomic_copy_file(p, target)
            copied += 1
    return copied


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def _cleanup_stale_workspaces(work_root: Path, older_than_hours: int) -> None:
    if not work_root.exists():
        return
    cutoff = time.time() - max(1, older_than_hours) * 3600
    for p in work_root.iterdir():
        try:
            if not p.is_dir():
                continue
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


def _prepare_runtime_paths(cfg: Dict[str, Any]) -> RuntimePaths:
    now = datetime.now()
    run_id = now.strftime("%Y-%m-%d_%H-%M-%S")
    day_id = now.strftime("%Y-%m-%d")
    date_label = day_id

    base_output_dir = Path(cfg["run_output_dir"])
    daily_dir = base_output_dir / day_id
    run_workspace_dir = daily_dir / ".work" / run_id

    label_crops_dir = Path(cfg["label_root_dir"]) / date_label
    staging_dir = Path(cfg["staging_dir"])
    failed_output_dir = Path(cfg["failed_output_dir"])

    return RuntimePaths(
        run_id=run_id,
        day_id=day_id,
        daily_dir=daily_dir,
        run_workspace_dir=run_workspace_dir,
        temp_config_path=run_workspace_dir / "temp_config.yaml",
        temp_config_roi_path=run_workspace_dir / "temp_config_roi.yaml",
        lock_path=daily_dir / ".daily_merge.lock",
        daily_log_file=daily_dir / f"pipeline_{day_id}.log",
        daily_manifest_jsonl=daily_dir / f"manifest_{day_id}.jsonl",
        label_crops_dir=label_crops_dir,
        staging_dir=staging_dir,
        failed_output_dir=failed_output_dir,
        run_datamatrix_excel=run_workspace_dir / "datamatrix_results.xlsx",
        run_stain_excel=run_workspace_dir / "stain_results.xlsx",
        run_stain_pdf=run_workspace_dir / "stain_report.pdf",
        run_extra_field_excel=run_workspace_dir / "extra_field_results.xlsx",
        run_slide_metadata_excel=run_workspace_dir / "slide_metadata.xlsx",
        run_pasnet_report_excel=run_workspace_dir / "pasnet_validation_report.xlsx",
        run_color_excel=run_workspace_dir / "color_marker_results.xlsx",
        run_roi_csv=run_workspace_dir / "roi_results.csv",
        run_datamatrix_failed_dir=run_workspace_dir / "failed_datamatrix",
        run_fallback_failed_dir=run_workspace_dir / "failed_fallback",
        run_research_no_roi_dir=run_workspace_dir / "research_no_roi",
        daily_datamatrix_excel=daily_dir / "datamatrix_results.xlsx",
        daily_stain_excel=daily_dir / "stain_results.xlsx",
        daily_stain_pdf=daily_dir / "stain_report.pdf",
        daily_extra_field_excel=daily_dir / "extra_field_results.xlsx",
        daily_slide_metadata_excel=daily_dir / "slide_metadata.xlsx",
        daily_pasnet_report_excel=daily_dir / "pasnet_validation_report.xlsx",
        daily_color_excel=daily_dir / "color_marker_results.xlsx",
        daily_datamatrix_failed_dir=daily_dir / "failed_datamatrix",
        daily_fallback_failed_dir=daily_dir / "failed_fallback",
    )


def _strip_runtime_folder_suffix(path_value: Any, paths: RuntimePaths) -> Any:
    if not path_value:
        return path_value
    p = Path(str(path_value))
    runtime_names = {str(paths.day_id), str(paths.run_id), str(paths.day_id).replace("-", ".")}
    date_re = re.compile(r"^\d{4}[-.]\d{2}[-.]\d{2}$")
    run_re = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
    while p.name in runtime_names or date_re.match(p.name) or run_re.match(p.name):
        parent = p.parent
        if parent == p:
            break
        p = parent
    return str(p)


def _build_runtime_config(cfg: Dict[str, Any], paths: RuntimePaths, policy: ArtifactPolicy) -> Dict[str, Any]:
    runtime_config = dict(cfg)
    runtime_config.update(
        {
            "run_timestamp": paths.run_id,
            "run_day": paths.day_id,
            "output_run_dir": str(paths.run_workspace_dir),
            "current_run_dir": str(paths.run_workspace_dir),
            "datamatrix_output_excel": str(paths.run_datamatrix_excel),
            "stain_output_excel": str(paths.run_stain_excel),
            "stain_output_pdf": str(paths.run_stain_pdf),
            "extra_field_output_excel": str(paths.run_extra_field_excel),
            "metadata_excel_path": str(paths.run_slide_metadata_excel),
            "slide_metadata_excel": str(paths.run_slide_metadata_excel),
            "label_crops_dir": str(paths.label_crops_dir),
            "staging_dir": str(paths.staging_dir),
            "color_marker_output_excel": str(paths.run_color_excel),
            "datamatrix_failed_folder": str(paths.run_datamatrix_failed_dir),
            "fallback_failed_folder": str(paths.run_fallback_failed_dir),
            "stain_pdf_enabled": policy.stain_pdf_mode == "daily_append",
            "report_pdf_enabled": policy.stain_pdf_mode == "daily_append",
            "generate_pdf": policy.stain_pdf_mode == "daily_append",
            "enable_pdf": policy.stain_pdf_mode == "daily_append",
        }
    )

    afe = runtime_config.get("extra_field_extractor")
    if not isinstance(afe, dict):
        afe = {}
    afe["input_dir"] = str(paths.label_crops_dir)
    afe["output_dir"] = str(paths.run_workspace_dir)
    afe["output_excel_name"] = paths.run_extra_field_excel.name
    runtime_config["extra_field_extractor"] = afe

    pv_cfg = runtime_config.get("pasnet_validator")
    if not isinstance(pv_cfg, dict):
        pv_cfg = {}
    pv_cfg["report_xlsx_path"] = str(paths.run_pasnet_report_excel)
    pv_cfg.setdefault("suspicious_output_dir", str(paths.daily_dir / "pasnet_suspicious"))
    runtime_config["pasnet_validator"] = pv_cfg

    artifact_policy_section = runtime_config.get("artifact_policy") if isinstance(runtime_config.get("artifact_policy"), dict) else {}
    artifact_policy_section.update(asdict(policy))
    runtime_config["artifact_policy"] = artifact_policy_section

    clr_cfg = runtime_config.get("color_label_routing")
    if isinstance(clr_cfg, dict):
        colors_cfg = clr_cfg.get("colors")
        if isinstance(colors_cfg, dict):
            for ccfg in colors_cfg.values():
                if isinstance(ccfg, dict) and ccfg.get("destination_dir"):
                    ccfg["destination_dir"] = _strip_runtime_folder_suffix(ccfg.get("destination_dir"), paths)
        routing_cfg = clr_cfg.get("routing")
        if isinstance(routing_cfg, dict) and routing_cfg.get("fallback_destination_dir"):
            routing_cfg["fallback_destination_dir"] = _strip_runtime_folder_suffix(routing_cfg.get("fallback_destination_dir"), paths)
        runtime_config["color_label_routing"] = clr_cfg

    sid_cfg = runtime_config.get("slide_id_generator")
    if isinstance(sid_cfg, dict):
        if sid_cfg.get("failed_output_dir"):
            sid_cfg["failed_output_dir"] = _strip_runtime_folder_suffix(sid_cfg.get("failed_output_dir"), paths)
        research_cfg = sid_cfg.get("research_case_generator")
        if isinstance(research_cfg, dict) and research_cfg.get("destination_dir"):
            research_cfg["destination_dir"] = _strip_runtime_folder_suffix(research_cfg.get("destination_dir"), paths)
        runtime_config["slide_id_generator"] = sid_cfg

    pv_cfg = runtime_config.get("pasnet_validator")
    if isinstance(pv_cfg, dict) and pv_cfg.get("suspicious_output_dir"):
        pv_cfg["suspicious_output_dir"] = _strip_runtime_folder_suffix(pv_cfg.get("suspicious_output_dir"), paths)
        runtime_config["pasnet_validator"] = pv_cfg

    return runtime_config


# =============================================================================
# Processed-check via database_manager.py
# =============================================================================


def _resolve_db_manager_script(cfg: Dict[str, Any]) -> Optional[str]:
    scripts = cfg.get("scripts", {}) if isinstance(cfg.get("scripts"), dict) else {}
    candidate = scripts.get("database_manager")
    return str(candidate) if candidate else None


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("{") and ln.endswith("}"):
            try:
                return json.loads(ln)
            except Exception:
                pass

    m = re.search(r"(\{.*\})\s*$", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def _is_processed_via_database_manager(cfg: Dict[str, Any], cfg_path: Path, source_path: Path) -> Optional[bool]:
    script = _resolve_db_manager_script(cfg)
    if not script:
        return None

    cmd = [
        sys.executable,
        script,
        "check",
        "--config",
        str(cfg_path),
        "--source",
        str(source_path),
        "--log-level",
        "ERROR",
    ]

    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    old_pp = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = f"{src_root}:{old_pp}" if old_pp else str(src_root)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    except Exception as exc:
        logging.info(f"[WARN] Processed-check subprocess failed for {source_path}: {exc}")
        return None

    if result.returncode != 0:
        stderr_text = (result.stderr or "").strip()
        logging.info(f"[WARN] Processed-check returned non-zero for {source_path}: {stderr_text}")
        return None

    payload = _extract_json_object(result.stdout)
    if isinstance(payload, dict) and isinstance(payload.get("is_processed"), bool):
        return bool(payload["is_processed"])

    logging.info(f"[WARN] Processed-check output was not parseable JSON for {source_path}")
    return None


def _already_processed(cfg: Dict[str, Any], cfg_path: Path, source_path: Path) -> bool:
    db_answer = _is_processed_via_database_manager(cfg, cfg_path, source_path)
    if db_answer is None:
        logging.info(f"[WARN] No processed-check answer for {source_path}; treating as not processed.")
        return False
    return bool(db_answer)


# =============================================================================
# Color / blacklist / research helpers
# =============================================================================


def _stem_name(x: object) -> str:
    try:
        return Path(str(x)).stem
    except Exception:
        return str(x)


def _read_case_list_excel(path: Optional[Path], sheet: object = 0) -> set[str]:
    if path is None or not path.exists():
        return set()
    try:
        df = pd.read_excel(path, sheet_name=sheet, header=None)
    except Exception:
        return set()
    if df.empty or df.shape[1] < 1:
        return set()
    s = df.iloc[:, 0].astype(str).str.strip()
    return {x.upper() for x in s if x and x.lower() not in {"nan", "none", "null"}}


def _derive_case_keys_from_name(name: str) -> set[str]:
    stem = _stem_name(name)
    keys: set[str] = set()

    m1 = re.match(r"^([A-Z]\d{2}-\d+)", stem, flags=re.IGNORECASE)
    if m1:
        keys.add(m1.group(1).upper())

    m2 = re.match(r"^([A-Z]\d{10,})", stem, flags=re.IGNORECASE)
    if m2:
        keys.add(m2.group(1).upper())

    token = stem.split("_")[0].strip()
    if token:
        keys.add(token.upper())

    return keys


def _load_color_excel(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame()
    if "SlideStem" not in df.columns and "FileName" in df.columns:
        df["SlideStem"] = df["FileName"].map(_stem_name)
    return df


def _research_classification_for_failed_pngs(
    cfg: Dict[str, Any],
    color_excel: Path,
    failed_pngs: List[Path],
) -> Tuple[List[Path], List[Path]]:
    routine_for_roi: List[Path] = []
    research_skip_roi: List[Path] = []

    color_df = _load_color_excel(color_excel)
    color_map: Dict[str, Tuple[str, float]] = {}
    if not color_df.empty and "SlideStem" in color_df.columns:
        for _, row in color_df.iterrows():
            stem = str(row.get("SlideStem", "")).strip()
            color = str(row.get("DetectedColor", "")).strip().lower()
            conf_raw = row.get("Confidence", "")
            try:
                conf = float(conf_raw) if str(conf_raw).strip() else 0.0
            except Exception:
                conf = 0.0
            if stem:
                color_map[stem] = (color, conf)

    sid = cfg.get("slide_id_generator") if isinstance(cfg.get("slide_id_generator"), dict) else {}
    rcfg = sid.get("research_case_generator") or sid.get("research_case_router") or {}
    if not isinstance(rcfg, dict):
        rcfg = {}
    case_list_excel = Path(str(rcfg.get("case_list_excel", "")).strip()) if rcfg.get("case_list_excel") else None
    case_ids = _read_case_list_excel(case_list_excel, rcfg.get("excel_sheet", 0))

    color_block = cfg.get("color_label_routing") if isinstance(cfg.get("color_label_routing"), dict) else {}
    routing_block = color_block.get("routing", {}) if isinstance(color_block, dict) else {}
    min_conf = float(routing_block.get("min_confidence", 0.60))

    for p in failed_pngs:
        stem = p.stem
        color, conf = color_map.get(stem, ("", 0.0))
        has_research_color = color not in {"", "none", "unknown", "no_marker"} and conf >= min_conf
        is_blacklisted = any(key in case_ids for key in _derive_case_keys_from_name(p.name))
        if has_research_color or is_blacklisted:
            research_skip_roi.append(p)
        else:
            routine_for_roi.append(p)

    return routine_for_roi, research_skip_roi


# =============================================================================
# ROI merge helpers
# =============================================================================


def _merge_roi_into_datamatrix(dm_excel: Path, roi_csv: Path) -> None:
    if not dm_excel.exists() or not roi_csv.exists():
        return

    try:
        df_dm = pd.read_excel(dm_excel)
        df_roi = pd.read_csv(roi_csv)
    except Exception as exc:
        logging.info(f"[WARN] ROI merge skipped: {exc}")
        return

    if "FileName" not in df_dm.columns or "FileName" not in df_roi.columns:
        logging.info("[WARN] Missing FileName column; cannot merge ROI.")
        return

    roi_meta_cols = ["DataMatrix", "LabID", "Year", "CaseNumber", "Pot", "BlockID", "Section"]
    for col in roi_meta_cols:
        if col not in df_roi.columns:
            df_roi[col] = None

    if "ExtractionMethod" not in df_dm.columns:
        df_dm["ExtractionMethod"] = None
    if "ExtractionMethod" not in df_roi.columns:
        df_roi["ExtractionMethod"] = None

    merge_subset = df_roi[["FileName"] + roi_meta_cols].copy()
    df_dm = df_dm.merge(merge_subset, on="FileName", how="left", suffixes=("", "_roi"))

    update_cols = ["DataMatrix", "LabID", "Year", "CaseNumber", "Pot", "BlockID", "Section"]
    update_any = pd.Series([False] * len(df_dm), index=df_dm.index)
    for col in update_cols:
        roi_col = f"{col}_roi"
        if roi_col not in df_dm.columns:
            continue
        mask = (
            df_dm[col].isna() | (df_dm[col].astype(str).str.strip() == "")
        ) & df_dm[roi_col].notna() & (df_dm[roi_col].astype(str).str.strip() != "")
        df_dm.loc[mask, col] = df_dm.loc[mask, roi_col]
        update_any |= mask

    df_dm.loc[update_any, "ExtractionMethod"] = "ROIbase"
    df_dm = df_dm.drop(columns=[c for c in df_dm.columns if c.endswith("_roi")], errors="ignore")
    _write_excel_sheets_atomic(dm_excel, {"Sheet1": df_dm})
    logging.info(f"[OK] ROI metadata merged into run DataMatrix Excel: {dm_excel}")


def _fill_datamatrix_extraction_method(dm_excel: Path) -> None:
    if not dm_excel.exists():
        return
    try:
        sheets = _read_excel_any(dm_excel)
    except Exception as exc:
        logging.info(f"[WARN] Cannot read DataMatrix Excel for ExtractionMethod sync: {exc}")
        return

    changed = False
    filled_total = 0
    invalid_values = {"", "nan", "none", "null", "not found", "not_found", "failed", "unreadable"}

    for sheet_name, df in list(sheets.items()):
        if df is None or "DataMatrix" not in df.columns:
            continue
        if "ExtractionMethod" not in df.columns:
            df["ExtractionMethod"] = None
            changed = True

        dm_text = df["DataMatrix"].astype("string").fillna("").str.strip()
        method_text = df["ExtractionMethod"].astype("string").fillna("").str.strip()
        has_dm = dm_text.ne("") & ~dm_text.str.lower().isin(invalid_values)
        missing_method = method_text.eq("") | method_text.str.lower().isin({"nan", "none", "null"})
        mask = has_dm & missing_method
        count = int(mask.sum())
        if count:
            df.loc[mask, "ExtractionMethod"] = "DataMatrix"
            filled_total += count
            changed = True
        sheets[sheet_name] = df

    if changed:
        _write_excel_sheets_atomic(dm_excel, sheets)
        logging.info(f"[OK] ExtractionMethod synced for {filled_total} DataMatrix row(s): {dm_excel}")


def _move_unresolved_to_fallback(datamatrix_failed_dir: Path, roi_csv: Path, fallback_failed_dir: Path) -> None:
    fallback_failed_dir.mkdir(parents=True, exist_ok=True)
    try:
        df_roi = pd.read_csv(roi_csv)
    except Exception as exc:
        logging.info(f"[WARN] Cannot read ROI CSV for fallback move: {exc}")
        return

    if "FileName" not in df_roi.columns or "DataMatrix" not in df_roi.columns:
        logging.info("[WARN] ROI CSV missing FileName or DataMatrix; cannot determine unresolved files.")
        return

    unresolved = set(
        df_roi.loc[
            df_roi["DataMatrix"].isna() | (df_roi["DataMatrix"].astype(str).str.strip() == ""),
            "FileName",
        ].astype(str)
    )

    if not datamatrix_failed_dir.exists():
        return

    for file in datamatrix_failed_dir.iterdir():
        if file.is_file() and file.name in unresolved:
            dest = fallback_failed_dir / file.name
            try:
                shutil.move(str(file), str(dest))
                logging.info(f"[INFO] Moved unresolved ROI file to fallback_failed: {dest}")
            except Exception as exc:
                logging.info(f"[WARN] Could not move unresolved file {file}: {exc}")


# =============================================================================
# Slide collection
# =============================================================================


def copy_new_slides(
    cfg: Dict[str, Any],
    cfg_path: Path,
    watch_dir: Path,
    staging_dir: Path,
    failed_dir: Path,
) -> List[str]:
    staging_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)

    operation_mode = str(cfg.get("operation_mode", "copy")).lower().strip()
    if operation_mode not in ("copy", "move"):
        logging.info(f"[WARN] Unknown operation_mode='{operation_mode}', defaulting to 'copy'.")
        operation_mode = "copy"

    cfg_exts = cfg.get("wsi_types", [])
    valid_exts = tuple(cfg_exts) if cfg_exts else _default_wsi_exts()

    if not watch_dir.exists():
        logging.info(f"[ERROR] Watched folder does not exist: {watch_dir}")
        return []

    candidates_files: List[Path] = []
    dicom_folders: List[Path] = []

    for root, _, files in os.walk(watch_dir):
        root_path = Path(root)
        has_dicomdir = any(f == "DICOMDIR" for f in files)
        has_dcm = any(f.lower().endswith(".dcm") for f in files)
        if has_dicomdir and has_dcm:
            dicom_folders.append(root_path)
        for f in files:
            candidates_files.append(root_path / f)

    new_wsi_files: List[Tuple[Path, str]] = []
    for src in candidates_files:
        name = src.name
        if not name.lower().endswith(valid_exts):
            continue
        if (staging_dir / name).exists():
            continue
        if _already_processed(cfg, cfg_path, src):
            logging.info(f"[SKIP] Already processed source: {src}")
            continue
        new_wsi_files.append((src, name))

    new_dicom_folders: List[Tuple[Path, str]] = []
    for src_folder in dicom_folders:
        folder_name = src_folder.name
        if (staging_dir / folder_name).exists():
            continue
        if _already_processed(cfg, cfg_path, src_folder):
            logging.info(f"[SKIP] Already processed DICOM folder: {src_folder}")
            continue
        new_dicom_folders.append((src_folder, folder_name))

    if not new_wsi_files and not new_dicom_folders:
        return []

    label = "Moving new slides" if operation_mode == "move" else "Copying new slides"
    for src, file_name in tqdm(new_wsi_files, desc=label):
        dest = staging_dir / file_name
        try:
            if operation_mode == "move":
                shutil.move(str(src), str(dest))
            else:
                shutil.copy2(str(src), str(dest))
            logging.info(f"[{operation_mode.upper()}] {src} -> {dest}")
        except Exception as exc:
            logging.info(f"[ERROR] Failed to {operation_mode} {file_name}: {exc}")
            try:
                failed_dest = failed_dir / file_name
                if src.exists():
                    if operation_mode == "move":
                        shutil.move(str(src), str(failed_dest))
                    else:
                        shutil.copy2(str(src), str(failed_dest))
            except Exception as exc2:
                logging.info(f"[ERROR] Also failed to place in failed folder: {file_name} -> {exc2}")

    dicom_label = "Moving DICOM folders" if operation_mode == "move" else "Copying DICOM folders"
    for src_folder, folder_name in tqdm(new_dicom_folders, desc=dicom_label):
        dest_folder = staging_dir / folder_name
        try:
            if operation_mode == "move":
                shutil.move(str(src_folder), str(dest_folder))
            else:
                shutil.copytree(str(src_folder), str(dest_folder))
            logging.info(f"[OK] DICOM folder staged: {src_folder} -> {dest_folder}")
        except Exception as exc:
            logging.info(f"[ERROR] Failed ({operation_mode}) DICOM folder: {src_folder} -> {exc}")

    return [name for _, name in new_wsi_files] + [name for _, name in new_dicom_folders]


# =============================================================================
# Child process runner
# =============================================================================


def run_script(
    step_title: str,
    script_path: str,
    temp_config_path: Path,
    *,
    extra_args: Optional[Sequence[str]] = None,
    log_level: str = "INFO",
    supports_log_level: bool = True,
) -> bool:
    logging.info(f"\n[STEP] Running {step_title} ...")

    py = sys.executable
    base_cmd = [py, script_path, "run", "--config", str(temp_config_path)]
    if extra_args:
        base_cmd.extend(list(extra_args))

    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    old_pp = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = f"{src_root}:{old_pp}" if old_pp else str(src_root)

    cmd = list(base_cmd)
    if supports_log_level and log_level:
        cmd += ["--log-level", str(log_level)]

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.stdout:
        logging.info(result.stdout.rstrip("\n"))
    if result.returncode == 0:
        return True

    stderr_text = (result.stderr or "").strip()
    if "--log-level" in " ".join(cmd) and "unrecognized arguments: --log-level" in stderr_text:
        logging.info(f"[WARN] {step_title}: child script does not accept --log-level; retrying without it.")
        result2 = subprocess.run(base_cmd, capture_output=True, text=True, env=env)
        if result2.stdout:
            logging.info(result2.stdout.rstrip("\n"))
        if result2.returncode == 0:
            return True
        logging.info(f"[ERROR] {step_title} failed (no --log-level):\n{(result2.stderr or '').strip()}")
        return False

    logging.info(f"[ERROR] {step_title} failed:\n{stderr_text}")
    return False


# =============================================================================
# Daily merge / output materialization
# =============================================================================


def _handle_stain_pdf_policy(paths: RuntimePaths, policy: ArtifactPolicy) -> None:
    mode = policy.stain_pdf_mode
    if mode == "disabled":
        with contextlib.suppress(Exception):
            paths.daily_stain_pdf.unlink(missing_ok=True)
        with contextlib.suppress(Exception):
            paths.run_stain_pdf.unlink(missing_ok=True)
        logging.info("[INFO] Stain PDF policy=disabled. No durable PDF kept.")
        return

    if mode == "daily_append":
        if paths.run_stain_pdf.exists():
            append_pdf_into_daily(paths.run_stain_pdf, paths.daily_stain_pdf)
            with contextlib.suppress(Exception):
                paths.run_stain_pdf.unlink(missing_ok=True)
        else:
            logging.info("[INFO] No run stain PDF found to append.")
        return

    if mode == "keep_run_only":
        logging.info("[INFO] Stain PDF policy=keep_run_only. Run PDF stays only in workspace.")
        return

    raise ValueError(f"Unsupported artifact_policy.stain_pdf_mode: {mode}")


def _merge_run_outputs_into_daily(paths: RuntimePaths, policy: ArtifactPolicy) -> None:
    with FileLock(paths.lock_path, timeout_seconds=policy.merge_lock_timeout_seconds, poll_interval=1.0):
        logging.info(f"[LOCK] Acquired daily merge lock: {paths.lock_path}")

        if policy.materialize_daily_excels:
            merge_excel_into_daily(
                paths.run_datamatrix_excel,
                paths.daily_datamatrix_excel,
                preferred_keys=["FileName", "DataMatrix", "SlideID", "OriginalFileName"],
                inject_run_id=paths.run_id,
            )
            merge_excel_into_daily(
                paths.run_stain_excel,
                paths.daily_stain_excel,
                preferred_keys=["FileName", "SlideID", "OriginalFileName"],
                inject_run_id=paths.run_id,
            )
            merge_excel_into_daily(
                paths.run_extra_field_excel,
                paths.daily_extra_field_excel,
                preferred_keys=["SlideStem", "FileName", "SlideID"],
                inject_run_id=paths.run_id,
            )
            merge_excel_into_daily(
                paths.run_slide_metadata_excel,
                paths.daily_slide_metadata_excel,
                preferred_keys=["NewFileName", "SlideID", "OriginalFileName", "FileName"],
                inject_run_id=paths.run_id,
            )
            merge_excel_into_daily(
                paths.run_pasnet_report_excel,
                paths.daily_pasnet_report_excel,
                preferred_keys=["FileName", "SlideID", "NewFileName"],
                inject_run_id=paths.run_id,
            )
            merge_excel_into_daily(
                paths.run_color_excel,
                paths.daily_color_excel,
                preferred_keys=["SlideStem", "FileName", "DetectedColor"],
                inject_run_id=paths.run_id,
            )

        _handle_stain_pdf_policy(paths, policy)

        if policy.keep_daily_failed_images:
            copied_failed_dm = _copy_new_support_files(paths.run_datamatrix_failed_dir, paths.daily_datamatrix_failed_dir)
            copied_failed_fb = _copy_new_support_files(paths.run_fallback_failed_dir, paths.daily_fallback_failed_dir)
            if copied_failed_dm:
                logging.info(f"[OK] Copied {copied_failed_dm} failed DataMatrix images into daily folder.")
            if copied_failed_fb:
                logging.info(f"[OK] Copied {copied_failed_fb} fallback-failed images into daily folder.")


# =============================================================================
# Main orchestration
# =============================================================================


def run_once(cfg: Dict[str, Any], cfg_path: Path) -> None:
    policy = _read_artifact_policy(cfg)
    paths = _prepare_runtime_paths(cfg)

    paths.daily_dir.mkdir(parents=True, exist_ok=True)
    paths.run_workspace_dir.mkdir(parents=True, exist_ok=True)
    paths.label_crops_dir.mkdir(parents=True, exist_ok=True)
    paths.failed_output_dir.mkdir(parents=True, exist_ok=True)
    paths.run_datamatrix_failed_dir.mkdir(parents=True, exist_ok=True)
    paths.run_fallback_failed_dir.mkdir(parents=True, exist_ok=True)
    paths.run_research_no_roi_dir.mkdir(parents=True, exist_ok=True)

    _cleanup_stale_workspaces(paths.daily_dir / ".work", policy.cleanup_stale_workspaces_hours)
    _attach_file_logger(paths.daily_log_file)

    logging.info(f"[RUN] Starting run_id={paths.run_id} day={paths.day_id}")

    originals = copy_new_slides(
        cfg=cfg,
        cfg_path=cfg_path,
        watch_dir=Path(cfg["watch_dir"]),
        staging_dir=paths.staging_dir,
        failed_dir=paths.failed_output_dir,
    )

    _append_jsonl(
        paths.daily_manifest_jsonl,
        {
            "ts_utc": _utc_now_iso(),
            "event": "staging_complete",
            "run_id": paths.run_id,
            "day_id": paths.day_id,
            "staged_items": len(originals),
            "items": originals,
        },
    )

    if not originals:
        logging.info("[INFO] No new slides to process.")
        return

    runtime_config = _build_runtime_config(cfg, paths, policy)
    _atomic_write_yaml(paths.temp_config_path, runtime_config)
    log_level = str(cfg.get("log_level", "INFO")).upper()

    def fail_run(message: str) -> None:
        _append_jsonl(
            paths.daily_manifest_jsonl,
            {"ts_utc": _utc_now_iso(), "event": "run_failed", "run_id": paths.run_id, "message": message},
        )

    try:
        openslide_dll = (cfg.get("dll_paths", {}) or {}).get("openslide_dll")
        label_extra_args = ["--openslide_dll", str(openslide_dll)] if openslide_dll else None
        if not run_script("LabelRegionExtractor", cfg["scripts"]["label_extractor"], paths.temp_config_path, extra_args=label_extra_args, log_level=log_level):
            fail_run("LabelRegionExtractor failed")
            return

        if bool((cfg.get("color_label_routing") or {}).get("enabled", False)):
            script_color = (cfg.get("scripts", {}) or {}).get("color_marker_detector")
            if not script_color:
                fail_run("color_label_routing.enabled=true but scripts.color_marker_detector is missing")
                return
            if not run_script("ColorMarkerDetector", script_color, paths.temp_config_path, log_level=log_level):
                fail_run("ColorMarkerDetector failed")
                return

        if not run_script("DataMatrixReader", cfg["scripts"]["datamatrix_reader"], paths.temp_config_path, log_level=log_level):
            fail_run("DataMatrixReader failed")
            return

        _fill_datamatrix_extraction_method(paths.run_datamatrix_excel)

        if not paths.run_datamatrix_excel.exists():
            fail_run("No DataMatrix result file found")
            logging.info("[ERROR] No DataMatrix result file found.")
            return

        files_waiting: List[Path] = []
        if paths.run_datamatrix_failed_dir.exists():
            files_waiting = [
                p for p in paths.run_datamatrix_failed_dir.iterdir()
                if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")
            ]

        if files_waiting:
            routine_for_roi, research_skip_roi = _research_classification_for_failed_pngs(
                cfg=cfg,
                color_excel=paths.run_color_excel,
                failed_pngs=files_waiting,
            )

            for p in research_skip_roi:
                dst = paths.run_research_no_roi_dir / p.name
                with contextlib.suppress(Exception):
                    shutil.move(str(p), str(dst))

            if research_skip_roi:
                logging.info(
                    f"[INFO] Skipped ROI fallback for {len(research_skip_roi)} research slide(s); "
                    f"kept for original-name routing in {paths.run_research_no_roi_dir}"
                )

            if routine_for_roi:
                logging.info(f"[STEP] ROI fallback: {len(routine_for_roi)} routine file(s) in {paths.run_datamatrix_failed_dir}")

                roi_xlsx = paths.run_workspace_dir / "roi_results.xlsx"
                roi_pdf = paths.run_workspace_dir / "roi_results.pdf"
                roi_debug_parts = paths.run_workspace_dir / "roi_debug_parts"

                roi_config = dict(runtime_config)
                roi_config.update(
                    {
                        "failed_output_dir": str(paths.run_datamatrix_failed_dir),
                        "output_csv": str(paths.run_roi_csv),
                        "output_xlsx": str(roi_xlsx),
                        "output_pdf": str(roi_pdf),
                        "debug_parts_root": str(roi_debug_parts),
                        "output_run_dir": str(paths.run_workspace_dir),
                    }
                )
                _atomic_write_yaml(paths.temp_config_roi_path, roi_config)

                extra_args = ["--input-dir", str(paths.run_datamatrix_failed_dir)]
                ok_roi = run_script(
                    "ROI Fallback",
                    cfg["scripts"]["roi_metadata_extractor"],
                    paths.temp_config_roi_path,
                    extra_args=extra_args,
                    log_level=log_level,
                )
                if not ok_roi:
                    logging.info("[WARN] ROI fallback step failed; continuing without it.")
                else:
                    _merge_roi_into_datamatrix(paths.run_datamatrix_excel, paths.run_roi_csv)
                    _move_unresolved_to_fallback(paths.run_datamatrix_failed_dir, paths.run_roi_csv, paths.run_fallback_failed_dir)
            else:
                logging.info("[INFO] ROI fallback skipped (all unreadable slides were classified as research).")
        else:
            logging.info(f"[INFO] ROI fallback skipped (no files in {paths.run_datamatrix_failed_dir}).")

        if not run_script("StainDataExtractor", cfg["scripts"]["stain_extractor"], paths.temp_config_path, log_level=log_level):
            fail_run("StainDataExtractor failed")
            return

        if not paths.run_stain_excel.exists():
            fail_run("No Stain result file found")
            logging.info("[ERROR] No Stain result file found.")
            return

        afe_cfg = cfg.get("extra_field_extractor") if isinstance(cfg.get("extra_field_extractor"), dict) else {}
        if bool(afe_cfg.get("enabled", False)) and "extra_field_extractor" in (cfg.get("scripts") or {}):
            if not run_script("AdditionalFactorExtractor", cfg["scripts"]["extra_field_extractor"], paths.temp_config_path, log_level=log_level):
                logging.info("[WARN] AdditionalFactorExtractor failed; continuing pipeline.")

        pv = cfg.get("pasnet_validator") if isinstance(cfg.get("pasnet_validator"), dict) else {}
        if bool(pv.get("enabled", False)):
            script_pv = (cfg.get("scripts", {}) or {}).get("pasnet_validator")
            if not script_pv:
                logging.info("[WARN] pasnet_validator.enabled=true but scripts.pasnet_validator is missing; skipping.")
            else:
                ok = run_script("PasnetValidator", script_pv, paths.temp_config_path, log_level=log_level)
                if not ok:
                    if bool(pv.get("fail_open", True)):
                        logging.info("[WARN] PasnetValidator failed but fail_open=true; continuing pipeline.")
                    else:
                        fail_run("PasnetValidator failed and fail_open=false")
                        return

        if not run_script("SlideIDcreator", cfg["scripts"]["slide_id_creator"], paths.temp_config_path, log_level=log_level):
            fail_run("SlideIDcreator failed")
            return

        dicom_script = (cfg.get("scripts", {}) or {}).get("dicom_metadata_writer")
        if dicom_script:
            if not run_script("DicomMetadataWriter", dicom_script, paths.temp_config_path, log_level=log_level):
                logging.info("[WARN] DicomMetadataWriter failed; continuing pipeline.")

        db_script = (cfg.get("scripts", {}) or {}).get("database_manager")
        pipeline_run_id = runtime_config.get("pipeline_run_internal_id")
        step_run_id = runtime_config.get("step_run_internal_id")

        if db_script and pipeline_run_id is not None and step_run_id is not None:
            py = sys.executable
            cmd = [
                py,
                db_script,
                "run",
                "--config",
                str(paths.temp_config_path),
                "--metadata-excel",
                str(paths.run_slide_metadata_excel),
                "--pipeline-run-id",
                str(pipeline_run_id),
                "--step-run-id",
                str(step_run_id),
                "--log-level",
                log_level,
            ]

            env = os.environ.copy()
            repo_root = Path(__file__).resolve().parents[2]
            src_root = repo_root / "src"
            old_pp = env.get("PYTHONPATH", "").strip()
            env["PYTHONPATH"] = f"{src_root}:{old_pp}" if old_pp else str(src_root)

            logging.info("\n[STEP] Running DatabaseManager ...")
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            if result.stdout:
                logging.info(result.stdout.rstrip("\n"))
            if result.returncode != 0:
                logging.info(f"[WARN] DatabaseManager failed:\n{(result.stderr or '').strip()}")
            else:
                logging.info("[OK] DatabaseManager completed.")
        else:
            logging.info("[INFO] DatabaseManager skipped (pipeline_run_internal_id/step_run_internal_id not provided).")

        _merge_run_outputs_into_daily(paths, policy)

        _append_jsonl(
            paths.daily_manifest_jsonl,
            {
                "ts_utc": _utc_now_iso(),
                "event": "run_completed",
                "run_id": paths.run_id,
                "day_id": paths.day_id,
                "staged_items": len(originals),
                "policy": asdict(policy),
            },
        )

        logging.info("\n[DONE] Pipeline run completed and daily artifacts updated.")

    finally:
        if not policy.keep_temp_config:
            with contextlib.suppress(Exception):
                paths.temp_config_path.unlink(missing_ok=True)
            with contextlib.suppress(Exception):
                paths.temp_config_roi_path.unlink(missing_ok=True)

        if not policy.keep_run_workspace:
            with contextlib.suppress(Exception):
                shutil.rmtree(paths.run_workspace_dir, ignore_errors=True)


# =============================================================================
# Validation / CLI
# =============================================================================


_REQUIRED_TOP_KEYS = [
    "run_output_dir",
    "label_root_dir",
    "staging_dir",
    "failed_output_dir",
    "scripts",
    "slide_id_generator",
    "watch_dir",
]

_REQUIRED_SCRIPTS = [
    "label_extractor",
    "datamatrix_reader",
    "stain_extractor",
    "slide_id_creator",
]


def validate(cfg_path: Path) -> int:
    setup_logging("INFO")
    try:
        cfg = _load_config(cfg_path)
    except Exception as exc:
        logging.info(f"[ERROR] Cannot load config: {exc}")
        return 1

    ok = True

    for key in _REQUIRED_TOP_KEYS:
        if key not in cfg:
            logging.info(f"[ERROR] Missing required config key: {key}")
            ok = False

    scripts = cfg.get("scripts")
    if not isinstance(scripts, dict):
        logging.info("[ERROR] Missing or invalid 'scripts' section in config.")
        ok = False
    else:
        for s in _REQUIRED_SCRIPTS:
            if s not in scripts:
                logging.info(f"[ERROR] Missing script path for: {s}")
                ok = False
            else:
                sp = Path(str(scripts[s]))
                if not sp.exists():
                    logging.info(f"[ERROR] Script not found: {sp}")
                    ok = False

        db_script = scripts.get("database_manager")
        if db_script:
            db_path = Path(str(db_script))
            if not db_path.exists():
                logging.info(f"[ERROR] database_manager script not found: {db_path}")
                ok = False

    try:
        policy = _read_artifact_policy(cfg)
        if policy.stain_pdf_mode not in {"disabled", "daily_append", "keep_run_only"}:
            logging.info(
                "[ERROR] artifact_policy.stain_pdf_mode must be one of: "
                "disabled, daily_append, keep_run_only"
            )
            ok = False
    except Exception as exc:
        logging.info(f"[ERROR] Invalid artifact_policy: {exc}")
        ok = False

    dbm_cfg = cfg.get("database_manager")
    if dbm_cfg is not None and not isinstance(dbm_cfg, dict):
        logging.info("[ERROR] 'database_manager' section must be a mapping.")
        ok = False

    if ok:
        logging.info("[OK] Config validation successful.")
        return 0

    return 2


def run(cfg_path: Path) -> None:
    cfg = _load_config(cfg_path)
    setup_logging(cfg.get("log_level", "INFO"))
    setup_openslide_from_config(cfg)

    watch_mode = bool(cfg.get("watch_mode", False))
    if watch_mode:
        interval = int(cfg.get("watch_interval_minutes", 60))
        logging.info(f"[INFO] Watch mode active. Checking every {interval} minutes. Press Ctrl+C to stop.")
        try:
            while True:
                run_once(cfg, cfg_path)
                logging.info(f"[INFO] Waiting {interval} minutes...")
                time.sleep(interval * 60)
        except KeyboardInterrupt:
            logging.info("[STOP] Watch mode interrupted by user.")
    else:
        run_once(cfg, cfg_path)


def build_cli(argv: Optional[List[str]] = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=__module_prog__,
        description="WSI-Babel-Shark enterprise daily aggregation runner.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the pipeline using a YAML config.")
    p_run.add_argument("--config", required=True, help="Path to YAML config file.")
    p_run.set_defaults(func=lambda args: run(Path(args.config)))

    p_val = sub.add_parser("validate", help="Validate the YAML config file.")
    p_val.add_argument("--config", required=True, help="Path to YAML config file.")
    p_val.set_defaults(func=lambda args: sys.exit(validate(Path(args.config))))

    p_ver = sub.add_parser("version", help="Show version information.")
    p_ver.set_defaults(func=lambda args: print(f"{__module_prog__} runner {__version__}", flush=True))

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_cli(argv)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()