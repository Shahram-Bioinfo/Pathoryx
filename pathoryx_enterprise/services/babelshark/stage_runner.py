"""
BabelShark enrichment pipeline stage runner.

Stage execution order (ROI before stain is critical — do not reorder):
  1. label_extraction        — Extract label/macro image from WSI
  2. color_marker_detection  — Optional, feature-flagged
  3. datamatrix              — Decode DataMatrix barcode from label PNG
  4. roi_fallback            — ROI-based metadata for DataMatrix failures (BEFORE stain)
  5. stain_extraction        — OCR stain detection (AFTER ROI)
  6. extra_field_extraction  — Optional, feature-flagged
  7. pasnet_validation       — LIS lookup, feature-flagged (disabled by default)
  8. slide_id_generation     — Build SlideID, rename, route the file
  9. dicom_metadata_writing  — Optional, feature-flagged

Shared output layout (under daily_dir = run_output_dir / YYYY-MM-DD):
    datamatrix_results.xlsx
    stain_results.xlsx
    slide_metadata.xlsx
    color_marker_results.xlsx
    label_crops/
    failed/
    final/
    failed_datamatrix/
    failed_fallback/
    .work/slide_{id}_{stem}/   — per-slide debug workspace (internal only)

ROI fallback note:
  temp_config_roi.yaml is written with all required keys including
  roiset_selector.layout_model.model_dir (fixes KeyError('model_dir')).
  All resource paths are made absolute before writing configs.

Enterprise integration:
  - Each stage emits lifecycle events to the immutable EventStore
  - Creates PipelineRun + StepRun records for full observability
  - Updates FileRecord.metadata_json with per-stage outputs
  - Records wall-clock timing and RSS memory snapshots
  - Correlation IDs propagate through every event and log record
  - Deferred QC trigger: fired only after enrichment completes
  - DB status: qc_pending after successful enrichment
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
import structlog
import yaml

from pathoryx_enterprise.db.models.core import FileRecord, PipelineRun, StepRun
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.monitoring.metrics import (
    events_appended_total,
    files_failed_total,
    files_processed_total,
    stage_latency_seconds,
)
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id
from pathoryx_enterprise.utils.watch_folder_priority import build_resolver_from_config
from sqlalchemy import select

logger = structlog.get_logger(__name__)

SERVICE_NAME = "babelshark"

_STAGE_ORDER: List[str] = [
    "label_extraction",
    "color_marker_detection",
    "datamatrix",
    "roi_fallback",
    "stain_extraction",
    "extra_field_extraction",
    "pasnet_validation",
    "slide_id_generation",
    "dicom_metadata_writing",
]


# =============================================================================
# Atomic IO helpers (matching runner_daily_enterprise.py)
# =============================================================================


def _resolve_watch_folder_priority(config: Dict[str, Any], file_path: str) -> dict:
    """Resolve watch folder priority for a detected file. Never raises."""
    try:
        wf_cfg = config.get("watch_folders") or []
        fallback = str(config.get("watch_dir", ""))
        resolver = build_resolver_from_config(wf_cfg, fallback_watch_dir=fallback or None)
        result = resolver.resolve(file_path)
        return {
            "priority": result.priority,
            "priority_source": result.priority_source,
            "watch_folder_path": result.watch_folder_path,
            "watch_folder_label": result.watch_folder_label,
        }
    except Exception:
        return {"priority": 5, "priority_source": "default",
                "watch_folder_path": None, "watch_folder_label": None}


def _atomic_write_yaml(dst: Path, obj: Dict[str, Any]) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst.parent, delete=False, suffix=".yaml") as tmp:
        tmp_path = Path(tmp.name)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(obj, f, allow_unicode=True, sort_keys=False)
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
                (df if df is not None else pd.DataFrame()).to_excel(
                    writer, sheet_name=safe_name, index=False
                )
        os.replace(tmp_path, path)
    finally:
        with contextlib.suppress(Exception):
            if tmp_path.exists():
                tmp_path.unlink()


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


# =============================================================================
# File lock (matching runner_daily_enterprise.py)
# =============================================================================


class LockTimeoutError(RuntimeError):
    pass


class FileLock:
    def __init__(
        self, lock_path: Path, timeout_seconds: int = 1800, poll_interval: float = 1.0
    ) -> None:
        self.lock_path = Path(lock_path)
        self.timeout_seconds = int(timeout_seconds)
        self.poll_interval = float(poll_interval)
        self.fd: Optional[int] = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout_seconds
        while True:
            try:
                self.fd = os.open(
                    str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                os.write(self.fd, str(os.getpid()).encode("utf-8"))
                return
            except FileExistsError:
                if time.time() >= deadline:
                    raise LockTimeoutError(
                        f"Timed out waiting for lock: {self.lock_path}"
                    )
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
# Excel merge helpers (matching runner_daily_enterprise.py)
# =============================================================================


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


def _merge_dataframes(
    df_old: pd.DataFrame,
    df_new: pd.DataFrame,
    *,
    preferred_keys: Sequence[str],
    inject_run_id: Optional[str] = None,
) -> pd.DataFrame:
    old_df = _normalize_text_columns(
        df_old if df_old is not None else pd.DataFrame()
    )
    new_df = _normalize_text_columns(
        df_new if df_new is not None else pd.DataFrame()
    )

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
    cols_old = set(map(str, old_df.columns))
    cols_new = set(map(str, new_df.columns))
    keys = [c for c in preferred_keys if c in cols_old and c in cols_new]
    if keys:
        merged = merged.drop_duplicates(subset=keys, keep="last")
    else:
        merged = merged.drop_duplicates(keep="last")
    return merged


def _merge_excel_into_shared(
    slide_excel: Path,
    shared_excel: Path,
    *,
    preferred_keys: Sequence[str],
    slide_run_id: Optional[str] = None,
) -> None:
    """Merge a per-slide Excel into the shared daily Excel."""
    if not slide_excel.exists():
        return

    slide_sheets = _read_excel_any(slide_excel)
    if not shared_excel.exists():
        out_sheets: Dict[str, pd.DataFrame] = {}
        for sheet_name, df_slide in slide_sheets.items():
            if slide_run_id and "RunID" not in df_slide.columns:
                df_slide = df_slide.copy()
                df_slide.insert(0, "RunID", slide_run_id)
            out_sheets[sheet_name] = df_slide
        _write_excel_sheets_atomic(shared_excel, out_sheets)
        return

    shared_sheets = _read_excel_any(shared_excel)
    out_sheets = dict(shared_sheets)
    for sheet_name, df_slide in slide_sheets.items():
        df_old = shared_sheets.get(sheet_name, pd.DataFrame())
        out_sheets[sheet_name] = _merge_dataframes(
            df_old,
            df_slide,
            preferred_keys=preferred_keys,
            inject_run_id=slide_run_id,
        )
    _write_excel_sheets_atomic(shared_excel, out_sheets)


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


# =============================================================================
# Shared path computation (matching runner_daily_enterprise.py layout)
# =============================================================================


@dataclass
class _SlidePaths:
    """All path constants for one slide's enrichment run."""

    slide_run_id: str
    day_id: str

    # Per-slide debug workspace (inside daily_dir/.work/)
    slide_dir: Path
    temp_config_path: Path      # slide_dir/temp_config.yaml
    temp_config_roi_path: Path  # slide_dir/temp_config_roi.yaml

    # Per-slide intermediate outputs (merged into daily after all stages)
    slide_label_crops_dir: Path
    slide_datamatrix_excel: Path
    slide_stain_excel: Path
    slide_color_excel: Path
    slide_slide_metadata_excel: Path
    slide_pasnet_report_excel: Path
    slide_extra_field_excel: Path
    slide_roi_csv: Path
    slide_roi_xlsx: Path
    slide_dm_failed_dir: Path
    slide_fallback_failed_dir: Path

    # Shared staging / failed dirs (referenced directly, not per-slide)
    staging_dir: Path
    failed_output_dir: Path

    # Shared daily operator-facing outputs (final destination)
    daily_dir: Path
    lock_path: Path
    daily_label_crops_dir: Path
    daily_datamatrix_excel: Path
    daily_stain_excel: Path
    daily_color_excel: Path
    daily_slide_metadata_excel: Path
    daily_pasnet_report_excel: Path
    daily_extra_field_excel: Path
    daily_dm_failed_dir: Path
    daily_fallback_failed_dir: Path


def _prepare_slide_paths(
    cfg: Dict[str, Any], file_record_id: int, stem: str
) -> _SlidePaths:
    """Build the per-slide path structure (mirrors runner_daily_enterprise.py)."""
    now = datetime.now()
    day_id = now.strftime("%Y-%m-%d")
    slide_run_id = f"{now.strftime('%Y-%m-%d_%H-%M-%S')}_{file_record_id}"

    base_output_dir = Path(cfg["run_output_dir"])
    daily_dir = base_output_dir / day_id
    slide_dir = daily_dir / ".work" / f"slide_{file_record_id}_{stem}"

    staging_dir = Path(cfg["staging_dir"])
    failed_output_dir = Path(
        cfg.get("failed_output_dir") or str(daily_dir / "failed")
    )
    daily_label_crops_dir = daily_dir / "label_crops"

    return _SlidePaths(
        slide_run_id=slide_run_id,
        day_id=day_id,
        slide_dir=slide_dir,
        temp_config_path=slide_dir / "temp_config.yaml",
        temp_config_roi_path=slide_dir / "temp_config_roi.yaml",
        slide_label_crops_dir=slide_dir / "label_crops",
        slide_datamatrix_excel=slide_dir / "datamatrix_results.xlsx",
        slide_stain_excel=slide_dir / "stain_results.xlsx",
        slide_color_excel=slide_dir / "color_marker_results.xlsx",
        slide_slide_metadata_excel=slide_dir / "slide_metadata.xlsx",
        slide_pasnet_report_excel=slide_dir / "pasnet_validation_report.xlsx",
        slide_extra_field_excel=slide_dir / "extra_field_results.xlsx",
        slide_roi_csv=slide_dir / "roi_results.csv",
        slide_roi_xlsx=slide_dir / "roi_results.xlsx",
        slide_dm_failed_dir=slide_dir / "failed_datamatrix",
        slide_fallback_failed_dir=slide_dir / "failed_fallback",
        staging_dir=staging_dir,
        failed_output_dir=failed_output_dir,
        daily_dir=daily_dir,
        lock_path=daily_dir / ".daily_merge.lock",
        daily_label_crops_dir=daily_label_crops_dir,
        daily_datamatrix_excel=daily_dir / "datamatrix_results.xlsx",
        daily_stain_excel=daily_dir / "stain_results.xlsx",
        daily_color_excel=daily_dir / "color_marker_results.xlsx",
        daily_slide_metadata_excel=daily_dir / "slide_metadata.xlsx",
        daily_pasnet_report_excel=daily_dir / "pasnet_validation_report.xlsx",
        daily_extra_field_excel=daily_dir / "extra_field_results.xlsx",
        daily_dm_failed_dir=daily_dir / "failed_datamatrix",
        daily_fallback_failed_dir=daily_dir / "failed_fallback",
    )


# =============================================================================
# Config building (matching runner_daily_enterprise.py)
# =============================================================================


def _fix_layout_model_paths(cfg: Dict[str, Any], project_root: Path) -> None:
    """
    Make all layout-model and ROI paths absolute and propagate model_dir
    to roiset_selector.layout_model.model_dir.

    This is the fix for KeyError('model_dir') raised by layout_api/factory.py:
      RoiMetadataExtractor reads roiset_selector.layout_model then calls
      load_model(model_cfg) which does cfg["model_dir"] — a direct key access
      that fails when roiset_selector.layout_model is empty or missing model_dir.
    """

    def make_abs(raw: Any) -> str:
        if not raw:
            return str(raw)
        rp = Path(str(raw))
        if rp.is_absolute():
            return str(rp)
        return str((project_root / rp).resolve())

    # Top-level model_dir
    if cfg.get("model_dir"):
        cfg["model_dir"] = make_abs(cfg["model_dir"])

    # layout_model.model_dir
    if not isinstance(cfg.get("layout_model"), dict):
        cfg["layout_model"] = {}
    raw_lm = cfg["layout_model"].get("model_dir")
    if raw_lm:
        cfg["layout_model"]["model_dir"] = make_abs(raw_lm)

    # Propagate to top-level model_dir if still missing
    lm_model_dir = cfg["layout_model"].get("model_dir")
    if lm_model_dir and not cfg.get("model_dir"):
        cfg["model_dir"] = lm_model_dir

    # layout_api.model_dir
    if not isinstance(cfg.get("layout_api"), dict):
        cfg["layout_api"] = {}
    raw_la = cfg["layout_api"].get("model_dir")
    resolved_model_dir = cfg.get("model_dir") or lm_model_dir
    if raw_la:
        cfg["layout_api"]["model_dir"] = make_abs(raw_la)
    elif resolved_model_dir:
        cfg["layout_api"]["model_dir"] = str(resolved_model_dir)

    # roiset_root
    if cfg.get("roiset_root"):
        cfg["roiset_root"] = make_abs(cfg["roiset_root"])

    # roiset_selector — ensure all sub-keys are present and absolute
    rsel = cfg.get("roiset_selector")
    if not isinstance(rsel, dict):
        rsel = {}
    else:
        rsel = dict(rsel)  # shallow copy to avoid mutating shared config

    if cfg.get("roiset_root") and not rsel.get("roiset_root"):
        rsel["roiset_root"] = cfg["roiset_root"]
    elif rsel.get("roiset_root"):
        rsel["roiset_root"] = make_abs(rsel["roiset_root"])

    # roiset_selector.layout_model.model_dir — THE KEY FIX
    # factory.load_model(model_cfg) does cfg["model_dir"]; model_cfg comes from
    # roiset_selector.layout_model, so it must have model_dir set.
    rsel_lm = rsel.get("layout_model")
    if not isinstance(rsel_lm, dict):
        rsel_lm = {}
    else:
        rsel_lm = dict(rsel_lm)

    rsel_lm_model_dir = (
        rsel_lm.get("model_dir")
        or cfg.get("model_dir")
        or lm_model_dir
    )
    if rsel_lm_model_dir:
        rsel_lm["model_dir"] = make_abs(rsel_lm_model_dir)

    # Preserve backend and params from existing roiset_selector.layout_model
    if isinstance(cfg.get("layout_model"), dict):
        for key in ("backend", "params"):
            if key in cfg["layout_model"] and key not in rsel_lm:
                rsel_lm[key] = cfg["layout_model"][key]

    rsel["layout_model"] = rsel_lm
    cfg["roiset_selector"] = rsel


def _build_runtime_config(
    cfg: Dict[str, Any],
    paths: _SlidePaths,
    staged_path: Path,
) -> Dict[str, Any]:
    """
    Build the per-slide runtime config (mirrors _build_runtime_config in
    runner_daily_enterprise.py) with per-slide output paths and absolute resources.
    """
    project_root = Path.cwd()
    runtime_cfg = dict(cfg)

    runtime_cfg.update(
        {
            "run_timestamp": paths.slide_run_id,
            "run_day": paths.day_id,
            # Per-slide workspace (intermediate, debug)
            "output_run_dir": str(paths.slide_dir),
            "current_run_dir": str(paths.slide_dir),
            # Staging: only the one slide
            "staging_dir": str(staged_path.parent),
            # Label crops: per-slide (avoid re-processing sibling slides)
            "label_crops_dir": str(paths.slide_label_crops_dir),
            "label_root_dir": str(paths.slide_label_crops_dir),
            # DataMatrix outputs
            "datamatrix_output_excel": str(paths.slide_datamatrix_excel),
            "datamatrix_failed_folder": str(paths.slide_dm_failed_dir),
            # Stain outputs
            "stain_output_excel": str(paths.slide_stain_excel),
            "stain_output_pdf": str(paths.slide_dir / "stain_report.pdf"),
            "report_pdf_enabled": False,
            "generate_pdf": False,
            "enable_pdf": False,
            # Color marker output
            "color_marker_output_excel": str(paths.slide_color_excel),
            # Slide metadata
            "metadata_excel_path": str(paths.slide_slide_metadata_excel),
            "slide_metadata_excel": str(paths.slide_slide_metadata_excel),
            # Routing dirs: shared daily locations
            "failed_output_dir": str(paths.failed_output_dir),
            "run_output_dir": str(paths.daily_dir),
        }
    )

    # final_output_dir: from config or shared daily/final
    if not runtime_cfg.get("final_output_dir"):
        runtime_cfg["final_output_dir"] = str(paths.daily_dir / "final")

    # extra_field_extractor section
    afe = runtime_cfg.get("extra_field_extractor")
    if not isinstance(afe, dict):
        afe = {}
    afe["input_dir"] = str(paths.slide_label_crops_dir)
    afe["output_dir"] = str(paths.slide_dir)
    afe["output_excel_name"] = paths.slide_extra_field_excel.name
    runtime_cfg["extra_field_extractor"] = afe

    # pasnet_validator section
    pv_cfg = runtime_cfg.get("pasnet_validator")
    if not isinstance(pv_cfg, dict):
        pv_cfg = {}
    pv_cfg["report_xlsx_path"] = str(paths.slide_pasnet_report_excel)
    pv_cfg.setdefault("suspicious_output_dir", str(paths.daily_dir / "suspicious"))
    runtime_cfg["pasnet_validator"] = pv_cfg

    # Fix stain resource paths to absolute
    for key in ("stain_list_path", "stain_replace_map_path"):
        raw = runtime_cfg.get(key)
        if raw:
            rp = Path(str(raw))
            if not rp.is_absolute():
                runtime_cfg[key] = str((project_root / rp).resolve())

    # Fix all layout-model / roiset paths (includes roiset_selector.layout_model.model_dir)
    _fix_layout_model_paths(runtime_cfg, project_root)

    return runtime_cfg


def _build_roi_config(
    runtime_cfg: Dict[str, Any], paths: _SlidePaths
) -> Dict[str, Any]:
    """
    Build ROI-specific config (mirrors the roi_config in runner_daily_enterprise.py).
    Written to temp_config_roi.yaml so cmd_run can find all required keys.
    """
    roi_cfg = dict(runtime_cfg)
    roi_cfg.update(
        {
            "failed_output_dir": str(paths.slide_dm_failed_dir),
            "output_csv": str(paths.slide_roi_csv),
            "output_xlsx": str(paths.slide_roi_xlsx),
            "output_pdf": str(paths.slide_dir / "roi_results.pdf"),
            "debug_parts_root": str(paths.slide_dir / "roi_debug_parts"),
            "output_run_dir": str(paths.slide_dir),
            "staging_dir": str(paths.slide_dm_failed_dir),
        }
    )
    return roi_cfg


# =============================================================================
# Output merge into shared daily structure (matching runner_daily_enterprise.py)
# =============================================================================


def _merge_slide_into_daily(paths: _SlidePaths, lock_timeout: int = 1800) -> None:
    """
    Merge all per-slide outputs into the shared daily operator directory.
    Uses FileLock so concurrent slide runs do not corrupt shared Excels.
    """
    with FileLock(paths.lock_path, timeout_seconds=lock_timeout, poll_interval=1.0):
        _merge_excel_into_shared(
            paths.slide_datamatrix_excel,
            paths.daily_datamatrix_excel,
            preferred_keys=["FileName", "DataMatrix", "SlideID", "OriginalFileName"],
            slide_run_id=paths.slide_run_id,
        )
        _merge_excel_into_shared(
            paths.slide_stain_excel,
            paths.daily_stain_excel,
            preferred_keys=["FileName", "SlideID", "OriginalFileName"],
            slide_run_id=paths.slide_run_id,
        )
        _merge_excel_into_shared(
            paths.slide_slide_metadata_excel,
            paths.daily_slide_metadata_excel,
            preferred_keys=["NewFileName", "SlideID", "OriginalFileName", "FileName"],
            slide_run_id=paths.slide_run_id,
        )
        _merge_excel_into_shared(
            paths.slide_color_excel,
            paths.daily_color_excel,
            preferred_keys=["SlideStem", "FileName", "DetectedColor"],
            slide_run_id=paths.slide_run_id,
        )
        _merge_excel_into_shared(
            paths.slide_extra_field_excel,
            paths.daily_extra_field_excel,
            preferred_keys=["SlideStem", "FileName", "SlideID"],
            slide_run_id=paths.slide_run_id,
        )
        _merge_excel_into_shared(
            paths.slide_pasnet_report_excel,
            paths.daily_pasnet_report_excel,
            preferred_keys=["FileName", "SlideID", "NewFileName"],
            slide_run_id=paths.slide_run_id,
        )

        # Copy failed images into shared daily failed dirs
        _copy_new_support_files(paths.slide_dm_failed_dir, paths.daily_dm_failed_dir)
        _copy_new_support_files(
            paths.slide_fallback_failed_dir, paths.daily_fallback_failed_dir
        )

        # Copy label crops into shared daily label_crops dir
        _copy_new_support_files(
            paths.slide_label_crops_dir, paths.daily_label_crops_dir
        )


# =============================================================================
# Subprocess runner (for script-based stages — matches run_script() in
# runner_daily_enterprise.py)
# =============================================================================


def _run_script_subprocess(
    step_title: str,
    script_path: str,
    config_path: Path,
    *,
    extra_args: Optional[Sequence[str]] = None,
    log_level: str = "INFO",
) -> bool:
    """Run a BabelShark script as a subprocess (matching runner_daily_enterprise.py)."""
    py = sys.executable
    cmd = [py, script_path, "run", "--config", str(config_path)]
    if extra_args:
        cmd.extend(list(extra_args))
    cmd += ["--log-level", log_level]

    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[4]  # Palantir root
    src_root = repo_root / "src"
    old_pp = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = f"{src_root}:{old_pp}" if old_pp else str(src_root)

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.stdout:
        logging.info(result.stdout.rstrip("\n"))
    if result.returncode == 0:
        return True

    stderr_text = (result.stderr or "").strip()
    if "unrecognized arguments: --log-level" in stderr_text:
        # Retry without --log-level for scripts that don't accept it
        cmd2 = [py, script_path, "run", "--config", str(config_path)]
        if extra_args:
            cmd2.extend(list(extra_args))
        result2 = subprocess.run(cmd2, capture_output=True, text=True, env=env)
        if result2.stdout:
            logging.info(result2.stdout.rstrip("\n"))
        if result2.returncode == 0:
            return True
        logging.error(
            f"[ERROR] {step_title} failed: {(result2.stderr or '').strip()}"
        )
        return False

    logging.error(f"[ERROR] {step_title} failed: {stderr_text}")
    return False


# =============================================================================
# RSS helper
# =============================================================================


def _rss_mb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1_048_576
    except Exception:
        return 0.0


# =============================================================================
# BabelSharkStageRunner
# =============================================================================


class BabelSharkStageRunner:
    """
    Orchestrate the full BabelShark enrichment pipeline for a single staged slide.

    Execution order and output layout match runner_daily_enterprise.py.
    Instantiate once per slide; not thread-safe across concurrent slides.
    """

    # Row-status values from slide_id_generator that mean the file was moved to a
    # failure directory.  These statuses must NOT trigger a downstream QC job.
    _FAILED_ROUTING_STATUSES: frozenset = frozenset({"failed", "nan", "error"})

    def __init__(
        self,
        config: Dict[str, Any],
        log: logging.Logger,
        *,
        correlation_id: Optional[str] = None,
        runner_id: Optional[str] = None,
        host_id: Optional[str] = None,
    ) -> None:
        self.config = config
        self.log = log
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.runner_id = runner_id
        self.host_id = host_id

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _stage_enabled(self, stage: str) -> bool:
        stages_cfg = self.config.get("pipeline_stages") or {}
        return bool(stages_cfg.get(stage, True))

    # ------------------------------------------------------------------
    # Enterprise DB helpers
    # ------------------------------------------------------------------

    def _emit_event(
        self,
        session,
        event_type: str,
        file_record_internal_id: int,
        global_artifact_id: str,
        payload: Dict[str, Any],
        pipeline_run_internal_id: Optional[int] = None,
        step_run_internal_id: Optional[int] = None,
    ) -> None:
        EventStoreRepository(session).append(
            event_type=f"babelshark.{event_type}",
            aggregate_type="file_record",
            aggregate_id=global_artifact_id,
            service_name=SERVICE_NAME,
            event_payload=payload,
            file_record_internal_id=file_record_internal_id,
            pipeline_run_internal_id=pipeline_run_internal_id,
            step_run_internal_id=step_run_internal_id,
            global_artifact_id=global_artifact_id,
            correlation_id=self.correlation_id,
            runner_id=self.runner_id,
            host_id=self.host_id,
        )
        events_appended_total.labels(
            event_type=f"babelshark.{event_type}", service=SERVICE_NAME
        ).inc()

    def _update_file_record_meta(
        self,
        session,
        file_record_internal_id: int,
        meta_update: Dict[str, Any],
        status: Optional[str] = None,
    ) -> None:
        record = session.execute(
            select(FileRecord).where(
                FileRecord.internal_id == file_record_internal_id
            )
        ).scalar_one_or_none()
        if record is None:
            return
        meta = dict(record.metadata_json or {})
        meta.update(meta_update)
        record.metadata_json = meta
        if status is not None:
            record.status = status
        session.flush()

    def _create_pipeline_run(
        self,
        file_record_internal_id: int,
        global_artifact_id: str,
    ) -> int:
        global_run_id = deterministic_artifact_id(
            "babelshark", "enrichment", file_record_internal_id, self.correlation_id
        )
        now = utc_now()
        with get_session() as session:
            existing = session.execute(
                select(PipelineRun).where(PipelineRun.global_run_id == global_run_id)
            ).scalar_one_or_none()
            if existing is not None:
                existing.run_status = "running"
                existing.final_outcome = None
                session.flush()
                return existing.internal_id
            run = PipelineRun(
                global_run_id=global_run_id,
                file_record_internal_id=file_record_internal_id,
                global_artifact_id=global_artifact_id,
                service_name=SERVICE_NAME,
                pipeline_name="babelshark_enrichment",
                run_status="running",
                started_at=now,
                correlation_id=self.correlation_id,
                runner_id=self.runner_id,
                host_id=self.host_id,
            )
            session.add(run)
            session.flush()
            return run.internal_id

    def _complete_pipeline_run(
        self, pipeline_run_internal_id: int, outcome: str
    ) -> None:
        now = utc_now()
        with get_session() as session:
            run = session.execute(
                select(PipelineRun).where(
                    PipelineRun.internal_id == pipeline_run_internal_id
                )
            ).scalar_one_or_none()
            if run is None:
                return
            run.run_status = "completed"
            run.final_outcome = outcome
            run.finished_at = now
            if run.started_at:
                run.duration_ms = int(
                    (now - run.started_at).total_seconds() * 1000
                )

    def _create_step_run(
        self,
        pipeline_run_internal_id: int,
        step_name: str,
        status: str,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        memory_rss_mb: Optional[float] = None,
    ) -> int:
        now = utc_now()
        with get_session() as session:
            existing = session.execute(
                select(StepRun).where(
                    StepRun.pipeline_run_internal_id == pipeline_run_internal_id,
                    StepRun.step_name == step_name,
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.step_status = status
                existing.outcome = status
                existing.finished_at = now
                existing.duration_ms = duration_ms
                existing.error_message = error_message
                if context:
                    existing.context_json = context
                if memory_rss_mb is not None:
                    existing.memory_rss_mb = memory_rss_mb
                session.flush()
                return existing.internal_id
            step = StepRun(
                pipeline_run_internal_id=pipeline_run_internal_id,
                step_name=step_name,
                step_status=status,
                outcome=status,
                started_at=now,
                finished_at=now,
                duration_ms=duration_ms,
                retry_count=0,
                error_message=error_message,
                context_json=context or {},
                memory_rss_mb=memory_rss_mb,
            )
            session.add(step)
            session.flush()
            return step.internal_id

    # ------------------------------------------------------------------
    # Stage 1: Label extraction
    # ------------------------------------------------------------------

    def run_label_extraction(
        self,
        staged_path: Path,
        slide_cfg: Dict[str, Any],
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
    ) -> Optional[Path]:
        """Extract label/macro PNG from a single WSI via a temp symlink directory."""
        t0 = time.perf_counter()
        mem0 = _rss_mb()
        label_dir = Path(slide_cfg["label_crops_dir"])
        label_dir.mkdir(parents=True, exist_ok=True)
        log_path = Path(slide_cfg["output_run_dir"]) / "label_extraction.log"

        from .core.label_extractor import (
            LabelExtractor,
            _load_openslide_after_dll_prep,
        )
        from .core import label_extractor as _le_mod
        from pathoryx_enterprise.runtime.openslide_setup import configure_openslide_runtime

        if _le_mod.openslide is None:
            # OPENSLIDE_DLL_PATH env var takes priority; config path is fallback.
            dll_path = (self.config.get("dll_paths") or {}).get("openslide_dll")
            configure_openslide_runtime(dll_path)
            _le_mod.openslide = _load_openslide_after_dll_prep()

        extractor = LabelExtractor(
            input_dir=staged_path.parent,
            output_dir=label_dir,
            log_file_path=log_path,
            label_crop_ratio=float(self.config.get("label_crop_ratio", 0.3)),
            rotation_degrees=int(self.config.get("rotation_degrees", -90)),
            macro_tag=str(self.config.get("macro_tag", "macro")),
            rotate_associated_label=bool(
                self.config.get("rotate_associated_label", False)
            ),
            label_rotation_degrees_label=int(
                self.config.get("label_rotation_degrees_label", 0)
            ),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_link = Path(tmp_dir) / staged_path.name
            try:
                os.symlink(staged_path.resolve(), tmp_link)
            except (OSError, NotImplementedError):
                shutil.copy2(staged_path, tmp_link)
            extractor.input_dir = Path(tmp_dir)
            extractor.extract_label()

        duration_ms = int((time.perf_counter() - t0) * 1000)
        mem_delta = _rss_mb() - mem0
        label_files = list(label_dir.glob("*.png"))

        with get_session() as session:
            self._emit_event(
                session,
                "label_extraction.completed",
                file_record_id,
                global_artifact_id,
                {
                    "label_dir": str(label_dir),
                    "label_files": [f.name for f in label_files],
                    "duration_ms": duration_ms,
                },
                pipeline_run_internal_id=pipeline_run_id,
            )
            self._update_file_record_meta(
                session,
                file_record_id,
                {
                    "label_extraction": {
                        "label_dir": str(label_dir),
                        "files": [f.name for f in label_files],
                    }
                },
            )

        self._create_step_run(
            pipeline_run_id,
            "label_extraction",
            "completed",
            duration_ms=duration_ms,
            memory_rss_mb=mem_delta,
            context={"label_files_count": len(label_files)},
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="label_extraction"
        ).observe(time.perf_counter() - t0)
        files_processed_total.labels(
            service=SERVICE_NAME, stage="label_extraction"
        ).inc()
        self.log.info(
            f"[STAGE] label_extraction done: {len(label_files)} label(s) in {duration_ms}ms"
        )
        return label_dir if label_files else None

    # ------------------------------------------------------------------
    # Stage 2: Color marker detection (optional, subprocess)
    # ------------------------------------------------------------------

    def run_color_marker_detection(
        self,
        cfg_path: Path,
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
        slide_color_excel: Path,
    ) -> bool:
        """Run color marker detection via the color_marker_detector module."""
        t0 = time.perf_counter()

        # Prefer in-process call; fall back to script path from config.
        ok = False
        try:
            from .core.color_marker_detector import run as color_run
            ok = color_run(cfg_path) == 0
        except Exception as exc:
            self.log.warning(
                f"[STAGE] color_marker_detection in-process failed ({exc}); "
                "trying script path from config."
            )
            script_path = (self.config.get("scripts") or {}).get(
                "color_marker_detector"
            )
            if script_path:
                ok = _run_script_subprocess(
                    "ColorMarkerDetector",
                    script_path,
                    cfg_path,
                    log_level=str(self.config.get("log_level", "INFO")).upper(),
                )

        duration_ms = int((time.perf_counter() - t0) * 1000)

        with get_session() as session:
            self._emit_event(
                session,
                "color_marker_detection.completed",
                file_record_id,
                global_artifact_id,
                {
                    "excel": str(slide_color_excel) if slide_color_excel.exists() else None,
                    "duration_ms": duration_ms,
                    "ok": ok,
                },
                pipeline_run_internal_id=pipeline_run_id,
            )
            self._update_file_record_meta(
                session,
                file_record_id,
                {
                    "color_marker_detection": {
                        "excel": str(slide_color_excel)
                        if slide_color_excel.exists()
                        else None
                    }
                },
            )

        self._create_step_run(
            pipeline_run_id,
            "color_marker_detection",
            "completed" if ok else "failed",
            duration_ms=duration_ms,
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="color_marker_detection"
        ).observe(time.perf_counter() - t0)
        if ok:
            files_processed_total.labels(
                service=SERVICE_NAME, stage="color_marker_detection"
            ).inc()
        self.log.info(
            f"[STAGE] color_marker_detection {'done' if ok else 'FAILED'} in {duration_ms}ms"
        )
        return ok

    # ------------------------------------------------------------------
    # Stage 3: DataMatrix reading
    # ------------------------------------------------------------------

    def run_datamatrix(
        self,
        label_dir: Path,
        slide_cfg: Dict[str, Any],
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
    ) -> Optional[Path]:
        """Decode DataMatrix barcodes from label PNGs."""
        t0 = time.perf_counter()
        from .core.datamatrix_reader import (
            process_all_images,
            save_results_excel_atomic,
            save_log_atomic,
        )

        results, log_lines, paths = process_all_images(slide_cfg)
        if results:
            save_results_excel_atomic(results, paths["output_excel"])
            save_log_atomic(log_lines, paths["log_file"])

        duration_ms = int((time.perf_counter() - t0) * 1000)
        success_count = sum(
            1 for r in results if str(r.get("Status", "")).lower() == "success"
        )
        excel_path = paths.get("output_excel") if results else None

        with get_session() as session:
            from .stage_db_writer import BabelSharkStageDBWriter

            BabelSharkStageDBWriter(session).write_datamatrix_results_batch(
                file_record_internal_id=file_record_id,
                global_artifact_id=global_artifact_id,
                pipeline_run_internal_id=pipeline_run_id,
                correlation_id=self.correlation_id,
                rows=results,
            )
            self._emit_event(
                session,
                "datamatrix.completed",
                file_record_id,
                global_artifact_id,
                {
                    "total": len(results),
                    "success": success_count,
                    "excel": str(excel_path) if excel_path else None,
                    "duration_ms": duration_ms,
                },
                pipeline_run_internal_id=pipeline_run_id,
            )
            self._update_file_record_meta(
                session,
                file_record_id,
                {
                    "datamatrix": {
                        "total": len(results),
                        "success": success_count,
                        "excel": str(excel_path) if excel_path else None,
                    }
                },
            )

        self._create_step_run(
            pipeline_run_id,
            "datamatrix",
            "completed",
            duration_ms=duration_ms,
            context={"total": len(results), "success": success_count},
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="datamatrix"
        ).observe(time.perf_counter() - t0)
        files_processed_total.labels(service=SERVICE_NAME, stage="datamatrix").inc()
        self.log.info(
            f"[STAGE] datamatrix done: {success_count}/{len(results)} decoded in {duration_ms}ms"
        )
        return (
            Path(str(excel_path))
            if excel_path and Path(str(excel_path)).exists()
            else None
        )

    # ------------------------------------------------------------------
    # Stage 4: ROI fallback (BEFORE stain — matches runner_daily_enterprise.py)
    # ------------------------------------------------------------------

    def run_roi_extraction(
        self,
        paths: _SlidePaths,
        roi_cfg_path: Path,
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
    ) -> Optional[Path]:
        """
        Run ROI-based metadata extraction on DataMatrix-failed label images.

        Uses temp_config_roi.yaml which has all required keys:
          - roiset_selector.roiset_root
          - roiset_selector.layout_model.model_dir  (fixes KeyError('model_dir'))
          - model_dir (top-level)
          - output_csv, output_xlsx, output_run_dir
        """
        t0 = time.perf_counter()
        dm_failed_dir = paths.slide_dm_failed_dir

        if not dm_failed_dir.exists() or not any(
            p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")
            for p in dm_failed_dir.iterdir()
        ):
            self.log.info("[STAGE] roi_fallback: no failed DM images, skipping")
            self._create_step_run(
                pipeline_run_id,
                "roi_fallback",
                "skipped",
                context={"reason": "no_dm_failed_images"},
            )
            return None

        import argparse
        from .core.metadata_extractor_utilities.cli_main import cmd_run

        args = argparse.Namespace(
            config=str(roi_cfg_path),
            input_dir=str(dm_failed_dir),
            roi_json="",
            pxl_offset=None,
            debug_folder="",
            log_level=str(self.config.get("log_level", "INFO")).upper(),
        )
        cmd_run(args)

        duration_ms = int((time.perf_counter() - t0) * 1000)
        roi_excel = paths.slide_roi_xlsx if paths.slide_roi_xlsx.exists() else None

        roi_rows: list = []
        if roi_excel and roi_excel.exists():
            try:
                _df = pd.read_excel(str(roi_excel), dtype=str)
                roi_rows = _df.fillna("").to_dict("records")
            except Exception as exc:
                self.log.warning(
                    f"[STAGE] roi: could not read Excel for DB write: {exc}"
                )

        with get_session() as session:
            from .stage_db_writer import BabelSharkStageDBWriter

            if roi_rows:
                BabelSharkStageDBWriter(session).write_roi_results_batch(
                    file_record_internal_id=file_record_id,
                    global_artifact_id=global_artifact_id,
                    pipeline_run_internal_id=pipeline_run_id,
                    correlation_id=self.correlation_id,
                    rows=roi_rows,
                )
            self._emit_event(
                session,
                "roi_fallback.completed",
                file_record_id,
                global_artifact_id,
                {
                    "dm_failed_dir": str(dm_failed_dir),
                    "excel": str(roi_excel) if roi_excel else None,
                    "duration_ms": duration_ms,
                    "rows_persisted": len(roi_rows),
                },
                pipeline_run_internal_id=pipeline_run_id,
            )
            self._update_file_record_meta(
                session,
                file_record_id,
                {
                    "roi_fallback": {
                        "excel": str(roi_excel) if roi_excel else None
                    }
                },
            )

        self._create_step_run(
            pipeline_run_id,
            "roi_fallback",
            "completed",
            duration_ms=duration_ms,
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="roi_fallback"
        ).observe(time.perf_counter() - t0)
        files_processed_total.labels(
            service=SERVICE_NAME, stage="roi_fallback"
        ).inc()
        self.log.info(f"[STAGE] roi_fallback done in {duration_ms}ms")
        return roi_excel

    # ------------------------------------------------------------------
    # Stage 5: Stain extraction (AFTER ROI — matches runner_daily_enterprise.py)
    # ------------------------------------------------------------------

    def run_stain_extraction(
        self,
        cfg_path: Path,
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
        slide_stain_excel: Path,
    ) -> Optional[Path]:
        """Run OCR-based stain detection using the per-slide config file."""
        t0 = time.perf_counter()
        from .core.stain_extractor import run_pipeline as stain_run_pipeline

        old_cwd = Path.cwd()
        run_dir = Path(cfg_path).parent
        # Legacy lookup names expected by stain_extractor's internal ROI config
        legacy_roi_cfg = run_dir / "temp_config_roi.yaml"
        legacy_main_cfg = run_dir / "temp_config.yaml"
        if not legacy_roi_cfg.exists():
            shutil.copy2(cfg_path, legacy_roi_cfg)
        if not legacy_main_cfg.exists():
            shutil.copy2(cfg_path, legacy_main_cfg)

        try:
            os.chdir(run_dir)
            stain_run_pipeline(cfg_path)
        finally:
            os.chdir(old_cwd)

        duration_ms = int((time.perf_counter() - t0) * 1000)
        stain_excel_path = slide_stain_excel if slide_stain_excel.exists() else None

        stain_rows: list = []
        if stain_excel_path:
            try:
                _df = pd.read_excel(str(stain_excel_path), dtype=str)
                stain_rows = _df.fillna("").to_dict("records")
            except Exception as exc:
                self.log.warning(
                    f"[STAGE] stain: could not read Excel for DB write: {exc}"
                )

        with get_session() as session:
            from .stage_db_writer import BabelSharkStageDBWriter

            if stain_rows:
                BabelSharkStageDBWriter(session).write_stain_results_batch(
                    file_record_internal_id=file_record_id,
                    global_artifact_id=global_artifact_id,
                    pipeline_run_internal_id=pipeline_run_id,
                    correlation_id=self.correlation_id,
                    rows=stain_rows,
                )
            self._emit_event(
                session,
                "stain_extraction.completed",
                file_record_id,
                global_artifact_id,
                {
                    "excel": str(stain_excel_path) if stain_excel_path else None,
                    "duration_ms": duration_ms,
                    "rows_persisted": len(stain_rows),
                },
                pipeline_run_internal_id=pipeline_run_id,
            )
            self._update_file_record_meta(
                session,
                file_record_id,
                {
                    "stain_extraction": {
                        "excel": str(stain_excel_path) if stain_excel_path else None
                    }
                },
            )

        self._create_step_run(
            pipeline_run_id,
            "stain_extraction",
            "completed",
            duration_ms=duration_ms,
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="stain_extraction"
        ).observe(time.perf_counter() - t0)
        files_processed_total.labels(
            service=SERVICE_NAME, stage="stain_extraction"
        ).inc()
        self.log.info(f"[STAGE] stain_extraction done in {duration_ms}ms")
        return stain_excel_path

    # ------------------------------------------------------------------
    # Stage 6: Extra field extraction (optional, subprocess)
    # ------------------------------------------------------------------

    def run_extra_field_extraction(
        self,
        cfg_path: Path,
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
        slide_extra_field_excel: Path,
    ) -> bool:
        """Run extra field extraction via script path from config."""
        t0 = time.perf_counter()
        script_path = (self.config.get("scripts") or {}).get("extra_field_extractor")
        ok = False
        if script_path:
            ok = _run_script_subprocess(
                "AdditionalFactorExtractor",
                script_path,
                cfg_path,
                log_level=str(self.config.get("log_level", "INFO")).upper(),
            )
        else:
            self.log.warning(
                "[STAGE] extra_field_extraction: scripts.extra_field_extractor missing; skipping."
            )

        duration_ms = int((time.perf_counter() - t0) * 1000)

        with get_session() as session:
            self._emit_event(
                session,
                "extra_field_extraction.completed",
                file_record_id,
                global_artifact_id,
                {
                    "excel": str(slide_extra_field_excel)
                    if slide_extra_field_excel.exists()
                    else None,
                    "duration_ms": duration_ms,
                    "ok": ok,
                },
                pipeline_run_internal_id=pipeline_run_id,
            )
            self._update_file_record_meta(
                session,
                file_record_id,
                {
                    "extra_field_extraction": {
                        "excel": str(slide_extra_field_excel)
                        if slide_extra_field_excel.exists()
                        else None
                    }
                },
            )

        self._create_step_run(
            pipeline_run_id,
            "extra_field_extraction",
            "completed" if ok else "skipped",
            duration_ms=duration_ms,
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="extra_field_extraction"
        ).observe(time.perf_counter() - t0)
        if ok:
            files_processed_total.labels(
                service=SERVICE_NAME, stage="extra_field_extraction"
            ).inc()
        self.log.info(
            f"[STAGE] extra_field_extraction {'done' if ok else 'skipped'} in {duration_ms}ms"
        )
        return ok

    # ------------------------------------------------------------------
    # Stage 7: PASNet validation (optional)
    # ------------------------------------------------------------------

    def run_pasnet_validation(
        self,
        slide_cfg: Dict[str, Any],
        cfg_path: Path,
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
    ) -> None:
        """Validate against PASNET/LIS database."""
        t0 = time.perf_counter()
        from .core.pasnet_utilities.cli import main as pasnet_cli_main

        pasnet_cli_main(["run", "--config", str(cfg_path)])

        duration_ms = int((time.perf_counter() - t0) * 1000)
        report_path = None
        pv_cfg = slide_cfg.get("pasnet_validator") or {}
        raw = pv_cfg.get("report_xlsx_path") or pv_cfg.get("report_path")
        if raw:
            report_path = Path(str(raw))

        pasnet_rows: list = []
        if report_path and report_path.exists():
            try:
                _df = pd.read_excel(str(report_path), sheet_name=0, dtype=str)
                pasnet_rows = _df.fillna("").to_dict("records")
            except Exception as exc:
                self.log.warning(
                    f"[STAGE] pasnet: could not read report for DB write: {exc}"
                )

        with get_session() as session:
            from .stage_db_writer import BabelSharkStageDBWriter

            _writer = BabelSharkStageDBWriter(session)
            first_row = pasnet_rows[0] if pasnet_rows else {}
            _writer.write_pasnet_validation_result(
                file_record_internal_id=file_record_id,
                global_artifact_id=global_artifact_id,
                pipeline_run_internal_id=pipeline_run_id,
                correlation_id=self.correlation_id,
                case_id=first_row.get("CaseID") or first_row.get("case_id"),
                slide_id=first_row.get("SlideID") or first_row.get("slide_id"),
                stain=first_row.get("Stain") or first_row.get("stain"),
                validation_mode=str(
                    slide_cfg.get("pasnet_validator", {}).get("mode", "pre_rename")
                ),
                validation_status=first_row.get("validation_status")
                or ("SKIPPED" if not pasnet_rows else "COMPLETED"),
                reason_summary=first_row.get("reason_summary"),
                pasnet_connection_status=first_row.get("pasnet_connection")
                or "UNKNOWN",
                extracted_slide_id=first_row.get("extracted_slide_id"),
                extracted_stain=first_row.get("extracted_stain"),
                final_slide_id=first_row.get("final_slide_id"),
                final_stain=first_row.get("final_stain"),
                file_action=first_row.get("file_action"),
                details_json={
                    "report_rows": pasnet_rows,
                    "report_path": str(report_path) if report_path else None,
                },
            )
            self._emit_event(
                session,
                "pasnet_validation.completed",
                file_record_id,
                global_artifact_id,
                {
                    "report": str(report_path) if report_path else None,
                    "duration_ms": duration_ms,
                },
                pipeline_run_internal_id=pipeline_run_id,
            )
            self._update_file_record_meta(
                session,
                file_record_id,
                {
                    "pasnet_validation": {
                        "report": str(report_path) if report_path else None
                    }
                },
            )

        self._create_step_run(
            pipeline_run_id,
            "pasnet_validation",
            "completed",
            duration_ms=duration_ms,
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="pasnet_validation"
        ).observe(time.perf_counter() - t0)
        files_processed_total.labels(
            service=SERVICE_NAME, stage="pasnet_validation"
        ).inc()
        self.log.info(f"[STAGE] pasnet_validation done in {duration_ms}ms")

    # ------------------------------------------------------------------
    # Stage 8: Slide ID generation, rename, routing
    # ------------------------------------------------------------------

    def run_slide_id_generation(
        self,
        slide_cfg: Dict[str, Any],
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
    ) -> tuple:
        """Build SlideID, rename the file, and route it to its final destination.

        Returns (slide_id, routing_status, routing_output_path) so the caller
        can decide whether to enqueue a QC trigger (success) or mark the record
        as babelshark_failed (failure).

        routing_status mirrors the 'status' column written to slide_metadata.xlsx
        by slide_id_generator: 'success', 'failed', 'research_success', etc.
        routing_output_path is the absolute path where the file now resides.
        """
        t0 = time.perf_counter()
        from .core.slide_id_generator import run_pipeline as sid_run_pipeline

        sid_run_pipeline(slide_cfg)

        duration_ms = int((time.perf_counter() - t0) * 1000)
        meta_xlsx = slide_cfg.get("metadata_excel_path")
        final_dir = slide_cfg.get("final_output_dir")

        slide_id = None
        routing_status = ""
        routing_output_path = ""
        sid_row: dict = {}
        if meta_xlsx and Path(str(meta_xlsx)).exists():
            try:
                _df = pd.read_excel(str(meta_xlsx), dtype=str)
                if not _df.empty:
                    sid_row = _df.fillna("").iloc[0].to_dict()
                    slide_id = str(sid_row.get("SlideID", "")).strip() or None
                    routing_status = str(sid_row.get("status", "")).strip().lower()
                    routing_output_path = str(sid_row.get("OutputPath", "")).strip()
            except Exception:
                pass

        with get_session() as session:
            from .stage_db_writer import BabelSharkStageDBWriter

            BabelSharkStageDBWriter(session).write_slide_routing_decision(
                file_record_internal_id=file_record_id,
                global_artifact_id=global_artifact_id,
                pipeline_run_internal_id=pipeline_run_id,
                correlation_id=self.correlation_id,
                original_filename=sid_row.get("OriginalFileName")
                or sid_row.get("original_filename"),
                new_filename=sid_row.get("NewFileName") or sid_row.get("new_filename"),
                final_path=routing_output_path or (str(final_dir) if final_dir else None),
                routing_type=str(
                    sid_row.get("RoutingType")
                    or sid_row.get("routing_type")
                    or routing_status
                    or "unknown"
                ),
                routing_reason=sid_row.get("RoutingReason")
                or sid_row.get("routing_reason"),
                case_id=sid_row.get("CaseID") or sid_row.get("case_id"),
                slide_id=slide_id,
                stain=sid_row.get("Stain") or sid_row.get("stain"),
                lab_id=sid_row.get("LabID") or sid_row.get("lab_id"),
                year=sid_row.get("Year") or sid_row.get("year"),
                case_number=sid_row.get("CaseNumber") or sid_row.get("case_number"),
                pot=sid_row.get("Pot") or sid_row.get("pot"),
                block_id=sid_row.get("BlockID") or sid_row.get("block_id"),
                section=sid_row.get("Section") or sid_row.get("section"),
                routing_metadata_json=sid_row,
            )
            self._emit_event(
                session,
                "slide_id_generation.completed",
                file_record_id,
                global_artifact_id,
                {
                    "slide_id": slide_id,
                    "routing_status": routing_status,
                    "routing_output_path": routing_output_path,
                    "final_dir": str(final_dir) if final_dir else None,
                    "metadata_excel": str(meta_xlsx) if meta_xlsx else None,
                    "duration_ms": duration_ms,
                },
                pipeline_run_internal_id=pipeline_run_id,
            )
            # Do NOT set status here — run_enrichment_pipeline's dispatch block
            # determines the final status from routing_status and calls either
            # mark_intake_complete (→ qc_pending) or mark_babelshark_failed
            # (→ babelshark_failed).  Setting it prematurely here caused failed-
            # routed slides to end up with status=qc_pending and a stale path.
            self._update_file_record_meta(
                session,
                file_record_id,
                {
                    "slide_id_generation": {
                        "slide_id": slide_id,
                        "routing_status": routing_status,
                        "routing_output_path": routing_output_path,
                        "final_dir": str(final_dir) if final_dir else None,
                    }
                },
            )

        self._create_step_run(
            pipeline_run_id,
            "slide_id_generation",
            "completed",
            duration_ms=duration_ms,
            context={"slide_id": slide_id, "routing_status": routing_status},
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="slide_id_generation"
        ).observe(time.perf_counter() - t0)
        files_processed_total.labels(
            service=SERVICE_NAME, stage="slide_id_generation"
        ).inc()
        self.log.info(
            f"[STAGE] slide_id_generation done: slide_id={slide_id!r} "
            f"routing_status={routing_status!r} in {duration_ms}ms"
        )
        return slide_id, routing_status, routing_output_path

    # ------------------------------------------------------------------
    # Stage 9: DICOM metadata writing (optional, subprocess)
    # ------------------------------------------------------------------

    def run_dicom_metadata_writing(
        self,
        cfg_path: Path,
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
    ) -> bool:
        """Run DICOM metadata writing via the dicom_metadata_writer module."""
        t0 = time.perf_counter()
        ok = False

        # Try in-process first; fall back to script path.
        try:
            from .core.dicom_metadata_writer import run_writer

            run_writer(
                cfg_path,
                log_level=str(self.config.get("log_level", "INFO")).upper(),
            )
            ok = True
        except Exception as exc:
            self.log.warning(
                f"[STAGE] dicom_metadata_writing in-process failed ({exc}); "
                "trying script path from config."
            )
            script_path = (self.config.get("scripts") or {}).get(
                "dicom_metadata_writer"
            )
            if script_path:
                ok = _run_script_subprocess(
                    "DicomMetadataWriter",
                    script_path,
                    cfg_path,
                    log_level=str(self.config.get("log_level", "INFO")).upper(),
                )

        duration_ms = int((time.perf_counter() - t0) * 1000)

        with get_session() as session:
            self._emit_event(
                session,
                "dicom_metadata_writing.completed",
                file_record_id,
                global_artifact_id,
                {"duration_ms": duration_ms, "ok": ok},
                pipeline_run_internal_id=pipeline_run_id,
            )

        self._create_step_run(
            pipeline_run_id,
            "dicom_metadata_writing",
            "completed" if ok else "failed",
            duration_ms=duration_ms,
        )
        stage_latency_seconds.labels(
            service=SERVICE_NAME, stage="dicom_metadata_writing"
        ).observe(time.perf_counter() - t0)
        if ok:
            files_processed_total.labels(
                service=SERVICE_NAME, stage="dicom_metadata_writing"
            ).inc()
        self.log.info(
            f"[STAGE] dicom_metadata_writing {'done' if ok else 'FAILED'} in {duration_ms}ms"
        )
        return ok

    # ------------------------------------------------------------------
    # Dead-letter / failure recording
    # ------------------------------------------------------------------

    def _record_stage_failure(
        self,
        stage: str,
        exc: Exception,
        file_record_id: int,
        global_artifact_id: str,
        pipeline_run_id: int,
        duration_ms: int,
    ) -> None:
        error_msg = f"{type(exc).__name__}: {exc}"
        try:
            self._create_step_run(
                pipeline_run_id,
                stage,
                "failed",
                duration_ms=duration_ms,
                error_message=error_msg[:2000],
            )
        except Exception:
            pass
        try:
            with get_session() as session:
                self._emit_event(
                    session,
                    f"{stage}.failed",
                    file_record_id,
                    global_artifact_id,
                    {"error": error_msg, "duration_ms": duration_ms},
                    pipeline_run_internal_id=pipeline_run_id,
                )
        except Exception:
            pass
        files_failed_total.labels(
            service=SERVICE_NAME, stage=stage, error_type=type(exc).__name__
        ).inc()
        self.log.error(f"[STAGE] {stage} FAILED: {error_msg}")

    # ------------------------------------------------------------------
    # Phase 4.8B — Routing policy decision (dry-run, Stage 1)
    # ------------------------------------------------------------------

    def _run_routing_decision(
        self,
        file_record_id: int,
        global_artifact_id: str,
        slide_id: Optional[str],
    ) -> None:
        """
        Evaluate the routing policy for a processed slide and persist the decision.

        Stage 1 contract (dry_run=True):
          - Computes and records the predicted destination.
          - Does NOT change the actual upload destination.
          - Never raises; any failure is logged and silently skipped.

        Structured log format:
          [ROUTING][DRY-RUN] slide=... scanner=... mode=... reason=... predicted=... actual_destination_unchanged=true
        """
        policies = self.config.get("routing_policies")
        if not policies:
            self.log.debug(
                "[ROUTING] routing_policies not configured — skipping decision for "
                f"file_record_id={file_record_id}"
            )
            return

        try:
            from pathoryx_enterprise.services.routing import RoutingPolicyEngine
        except Exception as exc:
            self.log.error(f"[ROUTING] Cannot import RoutingPolicyEngine: {exc}")
            return

        try:
            engine = RoutingPolicyEngine(policies)
        except Exception as exc:
            self.log.error(
                f"[ROUTING] Engine init failed: {exc} — "
                "pipeline unaffected, routing decision skipped"
            )
            return

        try:
            from sqlalchemy import text as _sql_text
            from pathoryx_enterprise.services.dashboard import routing_queries as rq

            with get_session() as session:
                # Resolve scanner_id from FileRecord
                scanner_id: Optional[str] = None
                try:
                    _row = session.execute(
                        _sql_text(
                            "SELECT scanner_id FROM core.file_records WHERE internal_id = :id"
                        ),
                        {"id": file_record_id},
                    ).fetchone()
                    scanner_id = _row[0] if _row else None
                except Exception as exc:
                    self.log.debug(f"[ROUTING] Could not fetch scanner_id: {exc}")

                # Resolve color_dot + confidence from color_marker_results
                color_dot: Optional[str] = None
                color_dot_confidence: Optional[float] = None
                try:
                    _cm = session.execute(
                        _sql_text("""
                            SELECT dominant_color, raw_payload
                              FROM babelshark.color_marker_results
                             WHERE file_record_internal_id = :id
                             ORDER BY id DESC LIMIT 1
                        """),
                        {"id": file_record_id},
                    ).fetchone()
                    if _cm:
                        raw_color = (_cm[0] or "").strip().lower()
                        color_dot = raw_color if raw_color not in ("", "none") else None
                        payload = _cm[1] or {}
                        if isinstance(payload, dict):
                            raw_conf = payload.get("Confidence")
                            if raw_conf is not None:
                                try:
                                    color_dot_confidence = float(raw_conf)
                                except (TypeError, ValueError):
                                    pass
                except Exception as exc:
                    self.log.debug(f"[ROUTING] Could not fetch color_marker_results: {exc}")

                # Load active overrides
                try:
                    overrides = rq.list_active_overrides(session)
                except Exception:
                    overrides = []

                # Evaluate routing policy
                result = engine.get_routing_decision(
                    scanner_id=scanner_id,
                    color_dot=color_dot,
                    overrides=overrides,
                    file_id=global_artifact_id,
                )

                # Persist the decision (audit trail only — never changes upload destination)
                decision_id = rq.record_decision(
                    session,
                    slide_id=slide_id,
                    scanner_id=result.scanner_id,
                    mode=result.mode,
                    profile=result.profile,
                    color_dot=result.color_dot,
                    color_dot_confidence=color_dot_confidence,
                    destination=result.destination,
                    routing_reason=result.routing_reason,
                    override_id=result.override_id,
                    dry_run=True,
                )

                self.log.info(
                    f"[ROUTING][DRY-RUN] decision_id={decision_id} "
                    f"slide={slide_id!r} scanner={result.scanner_id!r} "
                    f"mode={result.mode!r} reason={result.routing_reason!r} "
                    f"predicted_destination={result.destination!r} "
                    f"color_dot={result.color_dot!r} "
                    f"color_dot_confidence={color_dot_confidence!r} "
                    f"actual_destination_unchanged=true"
                )

        except Exception as exc:
            self.log.error(
                f"[ROUTING] Decision recording failed: {exc} — "
                "pipeline unaffected, slide continues normally"
            )

    # ------------------------------------------------------------------
    # Top-level enrichment orchestrator
    # ------------------------------------------------------------------

    def run_enrichment_pipeline(
        self,
        staged_path: Path,
        file_record_id: int,
        global_artifact_id: str,
    ) -> None:
        """
        Execute all enabled enrichment stages for a single staged slide.

        Execution order and output layout match runner_daily_enterprise.py:
          1. label_extraction
          2. color_marker_detection (optional, when color_label_routing.enabled)
          3. datamatrix
          4. roi_fallback           ← BEFORE stain (original order)
          5. stain_extraction       ← AFTER ROI (original order)
          6. extra_field_extraction (optional, when extra_field_extractor.enabled)
          7. pasnet_validation      (optional, when pasnet_validator.enabled)
          8. slide_id_generation
          9. dicom_metadata_writing (optional, when scripts.dicom_metadata_writer set)

        After all stages, per-slide outputs are merged into the shared
        daily operator directory (matching the legacy shared run/day layout).
        """
        t_total = time.perf_counter()
        self.log.info(
            f"[PIPELINE] enrichment started: file_record_id={file_record_id} "
            f"correlation_id={self.correlation_id}"
        )

        staged_path = Path(staged_path)

        # --- Prepare shared paths (mirrors runner_daily_enterprise.py) ---
        slide_paths = _prepare_slide_paths(
            self.config, file_record_id, staged_path.stem
        )
        slide_paths.slide_dir.mkdir(parents=True, exist_ok=True)
        slide_paths.slide_label_crops_dir.mkdir(parents=True, exist_ok=True)
        slide_paths.failed_output_dir.mkdir(parents=True, exist_ok=True)
        slide_paths.slide_dm_failed_dir.mkdir(parents=True, exist_ok=True)
        slide_paths.slide_fallback_failed_dir.mkdir(parents=True, exist_ok=True)
        slide_paths.daily_dir.mkdir(parents=True, exist_ok=True)

        # --- Build runtime config and write temp YAML files ---
        slide_cfg = _build_runtime_config(self.config, slide_paths, staged_path)
        _atomic_write_yaml(slide_paths.temp_config_path, slide_cfg)

        # ROI config: inherits runtime config + ROI-specific overrides + model_dir fix
        roi_config = _build_roi_config(slide_cfg, slide_paths)
        _atomic_write_yaml(slide_paths.temp_config_roi_path, roi_config)

        log_level = str(self.config.get("log_level", "INFO")).upper()
        pipeline_run_id = self._create_pipeline_run(file_record_id, global_artifact_id)
        pipeline_ok = True
        label_dir: Optional[Path] = None

        # ---- Stage 1: Label extraction ----
        if self._stage_enabled("label_extraction"):
            t0 = time.perf_counter()
            try:
                label_dir = self.run_label_extraction(
                    staged_path,
                    slide_cfg,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                )
            except Exception as exc:
                self._record_stage_failure(
                    "label_extraction",
                    exc,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    int((time.perf_counter() - t0) * 1000),
                )
                pipeline_ok = False

        # ---- Stage 2: Color marker detection (optional) ----
        color_routing = self.config.get("color_label_routing") or {}
        if (
            self._stage_enabled("color_marker_detection")
            and label_dir is not None
            and bool(color_routing.get("enabled", False))
        ):
            t0 = time.perf_counter()
            try:
                self.run_color_marker_detection(
                    slide_paths.temp_config_path,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    slide_color_excel=slide_paths.slide_color_excel,
                )
            except Exception as exc:
                self._record_stage_failure(
                    "color_marker_detection",
                    exc,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    int((time.perf_counter() - t0) * 1000),
                )

        # ---- Stage 3: DataMatrix ----
        if self._stage_enabled("datamatrix") and label_dir is not None:
            t0 = time.perf_counter()
            try:
                self.run_datamatrix(
                    label_dir,
                    slide_cfg,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                )
            except Exception as exc:
                self._record_stage_failure(
                    "datamatrix",
                    exc,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    int((time.perf_counter() - t0) * 1000),
                )
                pipeline_ok = False

        # ---- Stage 4: ROI fallback (BEFORE stain — runner_daily_enterprise.py order) ----
        if self._stage_enabled("roi_fallback"):
            t0 = time.perf_counter()
            try:
                self.run_roi_extraction(
                    slide_paths,
                    slide_paths.temp_config_roi_path,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                )
            except Exception as exc:
                self._record_stage_failure(
                    "roi_fallback",
                    exc,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    int((time.perf_counter() - t0) * 1000),
                )

        # ---- Stage 5: Stain extraction (AFTER ROI — runner_daily_enterprise.py order) ----
        if self._stage_enabled("stain_extraction") and label_dir is not None:
            t0 = time.perf_counter()
            try:
                self.run_stain_extraction(
                    slide_paths.temp_config_path,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    slide_stain_excel=slide_paths.slide_stain_excel,
                )
            except Exception as exc:
                self._record_stage_failure(
                    "stain_extraction",
                    exc,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    int((time.perf_counter() - t0) * 1000),
                )
                pipeline_ok = False

        # ---- Stage 6: Extra field extraction (optional) ----
        afe_cfg = self.config.get("extra_field_extractor") or {}
        if (
            self._stage_enabled("extra_field_extraction")
            and bool(afe_cfg.get("enabled", False))
            and (self.config.get("scripts") or {}).get("extra_field_extractor")
        ):
            t0 = time.perf_counter()
            try:
                self.run_extra_field_extraction(
                    slide_paths.temp_config_path,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    slide_extra_field_excel=slide_paths.slide_extra_field_excel,
                )
            except Exception as exc:
                self._record_stage_failure(
                    "extra_field_extraction",
                    exc,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    int((time.perf_counter() - t0) * 1000),
                )

        # ---- Stage 7: PASNet validation (optional) ----
        pv = self.config.get("pasnet_validator") or {}
        if self._stage_enabled("pasnet_validation") and bool(pv.get("enabled", False)):
            t0 = time.perf_counter()
            script_pv = (self.config.get("scripts") or {}).get("pasnet_validator")
            if not script_pv and not hasattr(
                __import__("pathoryx_enterprise.services.babelshark.core.pasnet_utilities.cli",
                           fromlist=["main"]), "main"
            ):
                self.log.warning(
                    "[STAGE] pasnet_validation enabled but no script or in-process module found; skipping."
                )
            else:
                try:
                    self.run_pasnet_validation(
                        slide_cfg,
                        slide_paths.temp_config_path,
                        file_record_id,
                        global_artifact_id,
                        pipeline_run_id,
                    )
                except Exception as exc:
                    self._record_stage_failure(
                        "pasnet_validation",
                        exc,
                        file_record_id,
                        global_artifact_id,
                        pipeline_run_id,
                        int((time.perf_counter() - t0) * 1000),
                    )
                    if not bool(pv.get("fail_open", True)):
                        pipeline_ok = False

        # ---- Stage 8: Slide ID generation ----
        _routing_status = ""
        _routing_output_path = ""
        _final_slide_id: Optional[str] = None
        if self._stage_enabled("slide_id_generation"):
            t0 = time.perf_counter()
            try:
                _final_slide_id, _routing_status, _routing_output_path = self.run_slide_id_generation(
                    slide_cfg,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                )
            except Exception as exc:
                self._record_stage_failure(
                    "slide_id_generation",
                    exc,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    int((time.perf_counter() - t0) * 1000),
                )
                pipeline_ok = False

        # ---- Stage 9: DICOM metadata writing (optional) ----
        dicom_script = (self.config.get("scripts") or {}).get("dicom_metadata_writer")
        if (
            self._stage_enabled("dicom_metadata_writing")
            and dicom_script
        ):
            t0 = time.perf_counter()
            try:
                self.run_dicom_metadata_writing(
                    slide_paths.temp_config_path,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                )
            except Exception as exc:
                self._record_stage_failure(
                    "dicom_metadata_writing",
                    exc,
                    file_record_id,
                    global_artifact_id,
                    pipeline_run_id,
                    int((time.perf_counter() - t0) * 1000),
                )

        # ---- Merge per-slide outputs into shared daily operator directory ----
        # Matches _merge_run_outputs_into_daily() in runner_daily_enterprise.py
        try:
            _merge_slide_into_daily(slide_paths)
            self.log.info(
                f"[PIPELINE] per-slide outputs merged into daily: {slide_paths.daily_dir}"
            )
        except Exception as exc:
            self.log.error(
                f"[PIPELINE] Failed to merge slide outputs into daily dir: {exc}"
            )

        # ---- Phase 4.8B: Routing decision (Stage 1 — dry-run, never changes destination) ----
        self._run_routing_decision(
            file_record_id=file_record_id,
            global_artifact_id=global_artifact_id,
            slide_id=_final_slide_id,
        )

        # ---- Dispatch: QC trigger or babelshark_failed (deferred from intake) ----
        # Only dispatch QC when the file was successfully routed to final/.
        # Failed routes (failed/, failed_datamatrix/, unreadable/, etc.) must NOT
        # receive a QC trigger — the file no longer lives at the staging path and
        # QC would fail with "Unsupported or missing image file".
        if self.config.get("defer_trigger", True):
            from pathoryx_enterprise.services.babelshark.db_writer import (
                BabelSharkDBWriter,
            )

            _routing_failed = (
                _routing_status in BabelSharkStageRunner._FAILED_ROUTING_STATUSES
                or (not _routing_status and not pipeline_ok)
            )

            if _routing_failed:
                try:
                    with get_session() as session:
                        BabelSharkDBWriter(session).mark_babelshark_failed(
                            file_record_internal_id=file_record_id,
                            global_artifact_id=global_artifact_id,
                            actual_path=_routing_output_path or None,
                            routing_status=_routing_status,
                            correlation_id=self.correlation_id,
                            runner_id=self.runner_id,
                            host_id=self.host_id,
                        )
                    self.log.info(
                        f"[PIPELINE] babelshark_failed recorded: "
                        f"routing_status={_routing_status!r} "
                        f"actual_path={_routing_output_path!r}"
                    )
                except Exception as exc:
                    self.log.error(f"[PIPELINE] mark_babelshark_failed failed: {exc}")
            else:
                next_stage = os.environ.get("BABELSHARK_NEXT_STAGE", "qc")
                next_service = os.environ.get("BABELSHARK_NEXT_SERVICE", "qc_service")
                # Resolve watch folder priority from config
                _wf_priority_info = _resolve_watch_folder_priority(
                    self.config, str(staged_path)
                )
                try:
                    with get_session() as session:
                        BabelSharkDBWriter(session).mark_intake_complete(
                            file_record_internal_id=file_record_id,
                            global_artifact_id=global_artifact_id,
                            next_stage=next_stage,
                            next_service=next_service,
                            correlation_id=self.correlation_id,
                            runner_id=self.runner_id,
                            host_id=self.host_id,
                            priority=_wf_priority_info["priority"],
                            priority_source=_wf_priority_info["priority_source"],
                            watch_folder_path=_wf_priority_info.get("watch_folder_path"),
                            watch_folder_label=_wf_priority_info.get("watch_folder_label"),
                        )
                    self.log.info(
                        f"[PIPELINE] QC trigger dispatched: next_stage={next_stage!r}"
                    )
                except Exception as exc:
                    self.log.error(f"[PIPELINE] QC trigger dispatch failed: {exc}")

        # ---- Finalize pipeline run ----
        total_ms = int((time.perf_counter() - t_total) * 1000)
        outcome = "completed" if pipeline_ok else "completed_with_errors"
        self._complete_pipeline_run(pipeline_run_id, outcome)
        self.log.info(
            f"[PIPELINE] enrichment finished: outcome={outcome!r} "
            f"total_ms={total_ms} correlation_id={self.correlation_id}"
        )
