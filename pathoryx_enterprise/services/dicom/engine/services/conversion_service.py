"""Native DICOM ConversionService.

Ported from dicom_delivery_adapter/pipeline/services/conversion_service.py.

Fixes applied during port:
  G3 — Function name bug: ConversionService now calls the real
        store_as_IDS7_compatible_dcm() dispatcher from the native engine,
        which correctly routes to store_dcmdile_as_IDS7_compatible_dcm or
        store_dcmwsifolder_as_IDS7_compatible_dcm based on input type.
        Previously the old code called getattr(legacy_module, 'store_as_IDS7_compatible_dcm', None)
        which always returned None, silently falling back to placeholder_copy.

  G6 — Lineage preservation: convert() accepts global_artifact_id from the
        trigger and passes it through to ConversionResult. No new UUID is ever
        generated inside this service.

  G5 — Linux dcmtk: bin_dir resolved from config.dcmtk.bin_dir → DCMTK_BIN_DIR
        env var → system PATH. Hardcoded Windows path removed.

  LIS — Optional: if config.lis.enabled is False or LIS credentials are absent,
        conversion succeeds without patient header enrichment.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from pathoryx_enterprise.services.dicom.engine.domain.enums import ConversionStatus, InputKind
from pathoryx_enterprise.services.dicom.engine.domain.results import ConversionResult
from pathoryx_enterprise.services.dicom.engine.services.conversion_utils import (
    classify_input_as_dicom_or_not,
    compute_sha256,
    deterministic_output_folder,
)
from pathoryx_enterprise.services.dicom.engine.services.wsidicom_utils import (
    WsidicomzerNotAvailableError,
    store_as_IDS7_compatible_dcm,
)


class ConversionService:
    """
    Convert WSI files to IDS7-compatible DICOM.

    Thread-safety: each trigger should create its own ConversionService instance,
    or at minimum ensure that convert() calls do not share mutable state.
    """

    def __init__(self, config) -> None:
        self.config = config

    def convert(
        self,
        source_path: str | Path,
        *,
        global_artifact_id: str | None = None,
    ) -> ConversionResult:
        """
        Classify the input and convert if needed.

        Always returns a ConversionResult — never raises.
        Callers must inspect result.status.value to detect failures.

        global_artifact_id is threaded through from the trigger payload so that
        artifact lineage is preserved across QC → DICOM → Upload stages.
        """
        started = time.perf_counter()
        source = Path(source_path).resolve()
        classification = classify_input_as_dicom_or_not(source)

        if not classification.exists:
            return ConversionResult(
                status=ConversionStatus.failed,
                source_path=source,
                input_kind=classification.input_kind,
                was_already_dicom=False,
                conversion_required=False,
                output_path=None,
                output_format=None,
                duration_seconds=time.perf_counter() - started,
                failure_context={"reason": classification.reason},
                final_outcome="failed",
                global_artifact_id=global_artifact_id,
            )

        if classification.was_already_dicom:
            output_size = self._safe_size(source)
            return ConversionResult(
                status=ConversionStatus.skipped_already_dicom,
                source_path=source,
                input_kind=classification.input_kind,
                was_already_dicom=True,
                conversion_required=False,
                output_path=source,
                output_format=(
                    "dcm_directory" if source.is_dir()
                    else source.suffix.lower().lstrip(".")
                ),
                duration_seconds=time.perf_counter() - started,
                metadata_summary={"classification_reason": classification.reason},
                input_metadata_json={"classification_reason": classification.reason},
                output_metadata_json={"reused_existing_dicom": True},
                output_file_size=output_size,
                final_outcome="skipped_already_dicom",
                global_artifact_id=global_artifact_id,
            )

        checksum = compute_sha256(source) if source.is_file() else None
        slide_id = source.stem

        try:
            output_dir = deterministic_output_folder(
                self.config.paths.output_root, source, slide_id, checksum
            )
            output_dir.mkdir(parents=True, exist_ok=True)

            result_path, metadata_summary, tool_name, tool_version = self._convert_non_dicom(
                source, output_dir
            )
            output_size = self._safe_size(result_path)
            input_size = self._safe_size(source)

            return ConversionResult(
                status=ConversionStatus.completed,
                source_path=source,
                input_kind=classification.input_kind,
                was_already_dicom=False,
                conversion_required=True,
                output_path=result_path,
                output_format="dicom",
                duration_seconds=time.perf_counter() - started,
                metadata_summary=metadata_summary,
                input_metadata_json={"classification_reason": classification.reason},
                output_metadata_json={"output_path": str(result_path)},
                conversion_tool=tool_name,
                conversion_tool_version=tool_version,
                input_file_size=input_size,
                output_file_size=output_size,
                final_outcome="completed",
                global_artifact_id=global_artifact_id,
            )

        except WsidicomzerNotAvailableError as exc:
            # Specific failure type so runner can set failure_context.error_type correctly.
            return ConversionResult(
                status=ConversionStatus.failed,
                source_path=source,
                input_kind=classification.input_kind,
                was_already_dicom=False,
                conversion_required=True,
                output_path=None,
                output_format=None,
                duration_seconds=time.perf_counter() - started,
                failure_context={"error_type": "missing_wsidicomizer", "message": str(exc)},
                input_metadata_json={"classification_reason": classification.reason},
                final_outcome="failed",
                global_artifact_id=global_artifact_id,
            )

        except Exception as exc:
            return ConversionResult(
                status=ConversionStatus.failed,
                source_path=source,
                input_kind=classification.input_kind,
                was_already_dicom=False,
                conversion_required=True,
                output_path=None,
                output_format=None,
                duration_seconds=time.perf_counter() - started,
                failure_context={"error_type": type(exc).__name__, "message": str(exc)},
                input_metadata_json={"classification_reason": classification.reason},
                final_outcome="failed",
                global_artifact_id=global_artifact_id,
            )

    def _convert_non_dicom(
        self,
        source: Path,
        output_dir: Path,
    ) -> tuple[Path, dict[str, Any], str, str | None]:
        """
        Run the actual WSI → DICOM conversion.

        Returns (output_path, metadata_summary, tool_name, tool_version).
        Raises on failure — caller wraps in try/except.
        """
        method = self.config.conversion.image_conversion_method

        if method == "ids7_compatible_dcm":
            output_folder = output_dir / "converted"
            output_folder.mkdir(parents=True, exist_ok=True)

            match_patterns = getattr(self.config, "match_construct_patterns", {})
            dcmtk_bin_dir = (
                getattr(getattr(self.config, "dcmtk", None), "bin_dir", "")
                or os.environ.get("DCMTK_BIN_DIR", "")
            )
            lis_cursor = self._get_lis_cursor()

            # Resolve wsidicomizer config (Phase 11A SVS fix)
            wsi_cfg = getattr(self.config, "wsidicomizer", None)
            wsi_executable = getattr(wsi_cfg, "executable", "wsidicomizer") if wsi_cfg else "wsidicomizer"
            wsi_workers = getattr(wsi_cfg, "workers", None) if wsi_cfg else None
            wsi_timeout = getattr(wsi_cfg, "timeout_seconds", 7200) if wsi_cfg else 7200
            wsi_enabled = getattr(wsi_cfg, "enabled", True) if wsi_cfg else True

            if not wsi_enabled:
                from pathoryx_enterprise.services.dicom.engine.services.wsidicom_utils import (
                    is_wsi_file, WsidicomzerNotAvailableError,
                )
                from pathlib import Path as _Path
                if is_wsi_file(_Path(str(source))):
                    raise WsidicomzerNotAvailableError(
                        "wsidicomizer is disabled in config (wsidicomizer.enabled: false)"
                    )

            metadata, out_path = store_as_IDS7_compatible_dcm(
                str(source),
                match_patterns,
                str(output_folder),
                dcmtk_bin_dir=dcmtk_bin_dir,
                lis_cursor=lis_cursor,
                wsidicomizer_executable=wsi_executable,
                wsidicomizer_workers=wsi_workers,
                wsidicomizer_timeout=wsi_timeout,
            )
            return Path(out_path), metadata or {}, "ids7_compatible_dcm", "1.0"

        if method == "placeholder_copy" and self.config.conversion.allow_placeholder_copy:
            out_path = output_dir / f"{source.stem}.dcm"
            shutil.copy2(source, out_path)
            return (
                out_path,
                {"source_name": source.name, "conversion_mode": "placeholder_copy"},
                "placeholder_copy",
                "1.0",
            )

        raise RuntimeError(
            f"No DICOM conversion backend available. "
            f"image_conversion_method={method!r}, "
            f"allow_placeholder_copy={self.config.conversion.allow_placeholder_copy}. "
            "Configure ids7_compatible_dcm or enable allow_placeholder_copy for development."
        )

    def _get_lis_cursor(self):
        """
        Return an active LIS DB cursor if LIS is enabled and credentials are configured.
        Returns None when LIS is disabled — conversion proceeds without patient enrichment.
        """
        lis_cfg = getattr(self.config, "lis", None)
        if lis_cfg is None or not lis_cfg.enabled:
            return None
        if not (lis_cfg.sql_server and lis_cfg.username and lis_cfg.password):
            return None
        try:
            from pathoryx_enterprise.services.dicom.engine.services.lis_client import (
                open_lis_connection,
            )
            _conn, cursor = open_lis_connection(
                lis_cfg.sql_server, lis_cfg.username, lis_cfg.password
            )
            return cursor
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "LIS connection failed (non-fatal — converting without patient enrichment): %s", exc
            )
            return None

    @staticmethod
    def _safe_size(path: Path | None) -> int | None:
        if path is None:
            return None
        try:
            if path.is_dir():
                return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            return path.stat().st_size
        except OSError:
            return None
