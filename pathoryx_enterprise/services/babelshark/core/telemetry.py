from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

import pandas as pd

try:
    import psutil  # type: ignore
except Exception:
    psutil = None


WSI_EXTS = {".svs", ".ndpi", ".tif", ".tiff", ".scn", ".mrxs", ".bif", ".dcm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def duration_ms(started_at: float, finished_at: Optional[float] = None) -> int:
    end = finished_at if finished_at is not None else time.perf_counter()
    return int((end - started_at) * 1000)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    try:
        import numpy as np  # type: ignore
        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    return value


def safe_count_files(path: str | Path | None, *, recursive: bool = False) -> int:
    try:
        if path is None:
            return 0
        p = Path(path)
        if not p.exists():
            return 0
        if p.is_file():
            return 1
        iterator = p.rglob("*") if recursive else p.iterdir()
        return sum(1 for x in iterator if x.is_file())
    except Exception:
        return 0


def safe_count_dirs(path: str | Path | None, *, recursive: bool = False) -> int:
    try:
        if path is None:
            return 0
        p = Path(path)
        if not p.exists() or not p.is_dir():
            return 0
        iterator = p.rglob("*") if recursive else p.iterdir()
        return sum(1 for x in iterator if x.is_dir())
    except Exception:
        return 0


def get_resource_snapshot() -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {"pid": os.getpid(), "psutil_available": psutil is not None}
    if psutil is None:
        return snapshot

    try:
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        snapshot.update(
            {
                "cpu_percent_process": proc.cpu_percent(interval=None),
                "memory_rss_mb": round(mem.rss / (1024 * 1024), 2),
                "memory_vms_mb": round(mem.vms / (1024 * 1024), 2),
            }
        )
        try:
            io = proc.io_counters()
            snapshot["io_read_mb"] = round(getattr(io, "read_bytes", 0) / (1024 * 1024), 2)
            snapshot["io_write_mb"] = round(getattr(io, "write_bytes", 0) / (1024 * 1024), 2)
        except Exception:
            pass
        try:
            snapshot["system_cpu_percent"] = psutil.cpu_percent(interval=None)
            snapshot["system_memory_percent"] = psutil.virtual_memory().percent
        except Exception:
            pass
    except Exception as exc:
        snapshot["resource_error"] = str(exc)

    return snapshot


def queue_snapshot_from_paths(
    *,
    watch_dir: str | Path | None = None,
    staging_dir: str | Path | None = None,
    label_dir: str | Path | None = None,
    datamatrix_failed_dir: str | Path | None = None,
    fallback_failed_dir: str | Path | None = None,
    final_output_dir: str | Path | None = None,
) -> Dict[str, Any]:
    return {
        "watch_dir_files": safe_count_files(watch_dir, recursive=True),
        "staging_files": safe_count_files(staging_dir, recursive=False),
        "label_files": safe_count_files(label_dir, recursive=True),
        "datamatrix_failed_files": safe_count_files(datamatrix_failed_dir, recursive=True),
        "fallback_failed_files": safe_count_files(fallback_failed_dir, recursive=True),
        "final_output_files": safe_count_files(final_output_dir, recursive=True),
        "final_output_case_dirs": safe_count_dirs(final_output_dir, recursive=False),
    }


class BabelSharkTelemetry:
    def __init__(self, *, service_name: str = "babelshark", enabled: bool = True) -> None:
        self.service_name = service_name
        self.enabled = enabled
        self._session = None
        self._EventLog = None

        if not enabled:
            return

        try:
            from db.session import SessionLocal
            from db.models import EventLog

            self._session = SessionLocal()
            self._EventLog = EventLog
        except Exception:
            self._session = None
            self._EventLog = None

    @property
    def available(self) -> bool:
        return self.enabled and self._session is not None and self._EventLog is not None

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass

    def emit(
        self,
        *,
        event_type: str,
        payload: Dict[str, Any],
        file_record_internal_id: Optional[int] = None,
        pipeline_run_internal_id: Optional[int] = None,
        step_run_internal_id: Optional[int] = None,
        global_run_id: Optional[str] = None,
        global_artifact_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        if not self.available:
            return

        try:
            now = utc_now()
            event = self._EventLog(
                service_name=self.service_name,
                event_type=event_type,
                file_record_internal_id=file_record_internal_id,
                pipeline_run_internal_id=pipeline_run_internal_id,
                step_run_internal_id=step_run_internal_id,
                global_run_id=global_run_id,
                global_artifact_id=global_artifact_id,
                correlation_id=correlation_id or global_run_id,
                event_timestamp=now,
                payload_json=_json_safe(payload),
                created_at=now,
                updated_at=now,
            )
            self._session.add(event)
            self._session.commit()
        except Exception:
            try:
                self._session.rollback()
            except Exception:
                pass

    def emit_run_event(
        self,
        *,
        event_type: str,
        run_id: str,
        day_id: str,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        data = {
            "event_scope": "run",
            "run_id": run_id,
            "day_id": day_id,
            "status": status,
            "timestamp_utc": utc_now_iso(),
            "resources": get_resource_snapshot(),
        }
        if payload:
            data.update(payload)
        self.emit(event_type=event_type, payload=data)

    def emit_step_event(
        self,
        *,
        run_id: str,
        day_id: str,
        stage: str,
        status: str,
        duration_ms_value: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        data = {
            "event_scope": "step",
            "run_id": run_id,
            "day_id": day_id,
            "stage": stage,
            "status": status,
            "duration_ms": duration_ms_value,
            "timestamp_utc": utc_now_iso(),
            "resources": get_resource_snapshot(),
        }
        if payload:
            data.update(payload)
        self.emit(event_type="BABELSHARK_STEP_METRICS", payload=data)

    def emit_slide_event(
        self,
        *,
        run_id: str,
        day_id: str,
        stage: str,
        slide_name: str,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        data = {
            "event_scope": "slide",
            "run_id": run_id,
            "day_id": day_id,
            "stage": stage,
            "slide_name": slide_name,
            "status": status,
            "timestamp_utc": utc_now_iso(),
        }
        if payload:
            data.update(payload)
        self.emit(event_type="BABELSHARK_SLIDE_EVENT", payload=data)


def _read_excel_safe(path: str | Path) -> pd.DataFrame:
    try:
        p = Path(path)
        if not p.exists():
            return pd.DataFrame()
        return pd.read_excel(p, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame()


def _expected_stems(collected_items: Iterable[str]) -> Dict[str, str]:
    return {Path(str(x)).stem: str(x) for x in collected_items}


def _row_matches_expected(row: pd.Series, expected: Dict[str, str]) -> tuple[bool, str]:
    exact_candidates = [
        row.get("OriginalFileName", ""),
        row.get("FileName", ""),
        row.get("SlideStem", ""),
        row.get("OriginalBase", ""),
    ]

    candidate_stems = set()
    candidate_names = set()

    for value in exact_candidates:
        text = str(value or "").strip()
        if not text:
            continue
        candidate_names.add(text)
        candidate_stems.add(Path(text).stem)

    for stem, original_name in expected.items():
        if original_name in candidate_names:
            return True, original_name
        if stem in candidate_stems:
            return True, original_name

    return False, ""

def emit_collected_slide_events(telemetry: BabelSharkTelemetry, *, run_id: str, day_id: str, collected_items: Iterable[str], staging_dir: str | Path) -> None:
    staging = Path(staging_dir)
    for item in collected_items:
        name = str(item)
        staged_path = staging / name
        telemetry.emit_slide_event(
            run_id=run_id,
            day_id=day_id,
            stage="collect",
            slide_name=name,
            status="collected",
            payload={
                "staged_path": str(staged_path),
                "staged_exists": staged_path.exists(),
                "file_size": staged_path.stat().st_size if staged_path.exists() and staged_path.is_file() else None,
            },
        )


def emit_label_slide_events(telemetry: BabelSharkTelemetry, *, run_id: str, day_id: str, label_root_dir: str | Path, collected_items: Iterable[str]) -> None:
    label_root = Path(label_root_dir)
    expected = _expected_stems(collected_items)
    found: Set[str] = set()

    if label_root.exists():
        for p in label_root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                continue
            stem = p.stem
            if stem not in expected:
                continue
            found.add(stem)
            telemetry.emit_slide_event(
                run_id=run_id,
                day_id=day_id,
                stage="label_extractor",
                slide_name=expected[stem],
                status="label_extracted",
                payload={
                    "label_image_path": str(p),
                    "label_image_name": p.name,
                    "file_size": p.stat().st_size if p.exists() else None,
                },
            )

    for stem, original_name in expected.items():
        if stem not in found:
            telemetry.emit_slide_event(
                run_id=run_id,
                day_id=day_id,
                stage="label_extractor",
                slide_name=original_name,
                status="label_not_found",
                payload={"label_root_dir": str(label_root)},
            )


def emit_datamatrix_slide_events(telemetry: BabelSharkTelemetry, *, run_id: str, day_id: str, datamatrix_excel: str | Path, collected_items: Iterable[str]) -> None:
    df = _read_excel_safe(datamatrix_excel)
    if df.empty:
        return
    expected = _expected_stems(collected_items)
    invalid = {"", "nan", "none", "null", "not found", "not_found", "failed", "unreadable"}

    for _, row in df.iterrows():
        matched, original_name = _row_matches_expected(row, expected)
        if not matched:
            continue
        datamatrix = str(row.get("DataMatrix", "") or "").strip()
        telemetry.emit_slide_event(
            run_id=run_id,
            day_id=day_id,
            stage="datamatrix_reader",
            slide_name=original_name,
            status="datamatrix_extracted" if datamatrix.lower() not in invalid else "datamatrix_failed",
            payload={
                "datamatrix": datamatrix,
                "lab_id": row.get("LabID", ""),
                "year": row.get("Year", ""),
                "case_number": row.get("CaseNumber", ""),
                "pot": row.get("Pot", ""),
                "block_id": row.get("BlockID", ""),
                "section": row.get("Section", ""),
                "extraction_method": row.get("ExtractionMethod", ""),
            },
        )


def emit_color_slide_events(telemetry: BabelSharkTelemetry, *, run_id: str, day_id: str, color_excel: str | Path, collected_items: Iterable[str]) -> None:
    df = _read_excel_safe(color_excel)
    if df.empty:
        return
    expected = _expected_stems(collected_items)

    for _, row in df.iterrows():
        matched, original_name = _row_matches_expected(row, expected)
        if not matched:
            continue
        telemetry.emit_slide_event(
            run_id=run_id,
            day_id=day_id,
            stage="color_marker_detector",
            slide_name=original_name,
            status="color_checked",
            payload={"detected_color": row.get("DetectedColor", ""), "confidence": row.get("Confidence", "")},
        )


def emit_stain_slide_events(telemetry: BabelSharkTelemetry, *, run_id: str, day_id: str, stain_excel: str | Path, collected_items: Iterable[str]) -> None:
    df = _read_excel_safe(stain_excel)
    if df.empty:
        return
    expected = _expected_stems(collected_items)

    for _, row in df.iterrows():
        matched, original_name = _row_matches_expected(row, expected)
        if not matched:
            continue
        stain = str(row.get("Stain", "") or "").strip()
        telemetry.emit_slide_event(
            run_id=run_id,
            day_id=day_id,
            stage="stain_extractor",
            slide_name=original_name,
            status="stain_extracted" if stain else "stain_missing",
            payload={"stain": stain, "raw_row": row.to_dict()},
        )


def emit_pasnet_slide_events(
    telemetry: BabelSharkTelemetry,
    *,
    run_id: str,
    day_id: str,
    pasnet_report_excel: str | Path,
    collected_items: Iterable[str],
) -> None:
    path = Path(pasnet_report_excel)
    if not path.exists():
        return

    expected = _expected_stems(collected_items)

    try:
        sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    except Exception:
        return

   
    emitted_slides: Set[str] = set()

    for sheet_name, df in sheets.items():
        if df is None or df.empty:
            continue

        df = df.fillna("")

        for _, row in df.iterrows():
            matched, original_name = _row_matches_expected(row, expected)
            if not matched:
                continue

            
            if original_name in emitted_slides:
                continue

            emitted_slides.add(original_name)

            telemetry.emit_slide_event(
                run_id=run_id,
                day_id=day_id,
                stage="pasnet_validator",
                slide_name=original_name,
                status="pasnet_validated",
                payload={
                    "sheet": sheet_name,
                    "final_decision": row.get("final_decision", "") or row.get("FinalDecision", ""),
                    "file_action": row.get("file_action", "") or row.get("FileAction", ""),
                    "raw_row": row.to_dict(),
                },
            )

def emit_slide_id_events(telemetry: BabelSharkTelemetry, *, run_id: str, day_id: str, slide_metadata_excel: str | Path, collected_items: Iterable[str]) -> None:
    df = _read_excel_safe(slide_metadata_excel)
    if df.empty:
        return
    expected = _expected_stems(collected_items)

    for _, row in df.iterrows():
        matched, original_name = _row_matches_expected(row, expected)
        if not matched:
            continue
        new_file = str(row.get("NewFileName", "") or "").strip()
        telemetry.emit_slide_event(
            run_id=run_id,
            day_id=day_id,
            stage="slide_id_creator",
            slide_name=original_name,
            status="slide_renamed" if new_file else "slide_rename_missing",
            payload={
                "new_filename": new_file,
                "slide_id": row.get("SlideID", ""),
                "case_id": row.get("CaseID", ""),
                "status_raw": row.get("Status", "") or row.get("FinalStatus", ""),
                "destination_path": row.get("DestinationPath", "") or row.get("OutputPath", ""),
                "raw_row": row.to_dict(),
            },
        )


def emit_final_route_events_from_slide_metadata(telemetry: BabelSharkTelemetry, *, run_id: str, day_id: str, slide_metadata_excel: str | Path, collected_items: Iterable[str]) -> None:
    df = _read_excel_safe(slide_metadata_excel)
    if df.empty:
        return
    expected = _expected_stems(collected_items)

    for _, row in df.iterrows():
        matched, original_name = _row_matches_expected(row, expected)
        if not matched:
            continue
        final_filename = str(row.get("NewFileName", "") or "").strip()
        final_path = str(row.get("DestinationPath", "") or row.get("OutputPath", "") or "").strip()
        telemetry.emit_slide_event(
            run_id=run_id,
            day_id=day_id,
            stage="final_output",
            slide_name=original_name,
            status="final_file_present" if final_filename else "final_file_missing",
            payload={
                "final_filename": final_filename,
                "final_path": final_path,
                "file_size": Path(final_path).stat().st_size if final_path and Path(final_path).exists() else None,
            },
        )


def emit_final_route_events_from_case_folder(telemetry: BabelSharkTelemetry, *, run_id: str, day_id: str, final_output_dir: str | Path, collected_items: Iterable[str]) -> None:
    final_root = Path(final_output_dir)
    expected = _expected_stems(collected_items)
    if not final_root.exists():
        return

    for p in final_root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in WSI_EXTS:
            continue
        matched_name = ""
        for stem, original_name in expected.items():
            if stem in p.name:
                matched_name = original_name
                break
        if not matched_name:
            continue
        telemetry.emit_slide_event(
            run_id=run_id,
            day_id=day_id,
            stage="final_output",
            slide_name=matched_name,
            status="final_file_present",
            payload={"final_path": str(p), "final_filename": p.name, "file_size": p.stat().st_size if p.exists() else None},
        )
