"""Native DICOM engine configuration.

Loads dicom_config.yaml into typed dataclasses. Only includes fields that the
enterprise ConversionService actually uses — upload settings are managed separately
by DICOMSettings (env-based) and the existing upload_utils.py.

Ported and simplified from dicom_delivery_adapter/pipeline/config.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass(slots=True)
class DicomPathsConfig:
    output_root: Path

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root).resolve()


@dataclass(slots=True)
class DicomConversionConfig:
    image_conversion_method: str = "ids7_compatible_dcm"
    allow_placeholder_copy: bool = False
    supported_extensions: list = field(
        default_factory=lambda: [".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".vms"]
    )
    redicomize_when_hash_changed: bool = False
    preserve_original_input: bool = True


@dataclass(slots=True)
class DicomDcmtkConfig:
    """dcmtk binary location. Empty bin_dir means use system PATH."""
    bin_dir: str = ""
    dcmodify_path: str = ""
    dcmdump_path: str = ""


@dataclass(slots=True)
class DicomLisConfig:
    enabled: bool = False
    enrich_case_metadata: bool = False
    sql_server: str | None = None
    username: str | None = None
    password: str | None = None
    query_timeout_seconds: int = 15


@dataclass(slots=True)
class DicomUploadPlaceholderConfig:
    """Placeholder upload settings — not used by ConversionService.
    Actual upload behaviour is controlled by DICOMSettings + upload_utils.py."""
    dry_run: bool = True
    timeout_seconds: int = 1800
    max_retries: int = 2
    retry_backoff_seconds: int = 10


@dataclass(slots=True)
class WsidicomzerConfig:
    """
    Configuration for the wsidicomizer WSI → DICOM conversion step.

    enabled:          If False, WSI files will immediately fail with missing_wsidicomizer.
    executable:       wsidicomizer CLI binary name or full path.
    prefer_cli:       True = use CLI (default); False = attempt Python API (WsiDicomizer.convert).
    workers:          Number of wsidicomizer worker threads. None = wsidicomizer default.
    timeout_seconds:  Maximum wall time allowed per conversion (default: 2 hours).
    """
    enabled: bool = True
    executable: str = "wsidicomizer"
    prefer_cli: bool = True
    workers: int | None = None
    timeout_seconds: int = 7200


@dataclass
class DicomEngineConfig:
    """Full DICOM engine configuration loaded from dicom_config.yaml."""
    paths: DicomPathsConfig
    conversion: DicomConversionConfig
    dcmtk: DicomDcmtkConfig
    lis: DicomLisConfig
    upload: DicomUploadPlaceholderConfig
    wsidicomizer: WsidicomzerConfig = field(default_factory=WsidicomzerConfig)
    match_construct_patterns: dict = field(default_factory=dict)


def load_dicom_engine_config(config_path: str | os.PathLike[str]) -> DicomEngineConfig:
    """
    Parse dicom_config.yaml and return a fully populated DicomEngineConfig.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError for missing required fields.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"DICOM engine config not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh) or {}

    raw = _expand_env(raw)

    # ── paths ────────────────────────────────────────────────────────────────
    paths_raw = raw.get("paths", {})
    output_root = (paths_raw.get("output_root") or "").strip()
    if not output_root:
        raise ValueError("dicom_config.yaml: paths.output_root is required")
    paths = DicomPathsConfig(output_root=Path(output_root))

    # ── conversion ───────────────────────────────────────────────────────────
    conv_raw = raw.get("conversion", {})
    conversion = DicomConversionConfig(
        image_conversion_method=str(
            conv_raw.get("image_conversion_method", "ids7_compatible_dcm")
        ),
        allow_placeholder_copy=bool(conv_raw.get("allow_placeholder_copy", False)),
        supported_extensions=list(
            conv_raw.get(
                "supported_extensions",
                [".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".vms"],
            )
        ),
        redicomize_when_hash_changed=bool(
            conv_raw.get("redicomize_when_hash_changed", False)
        ),
        preserve_original_input=bool(conv_raw.get("preserve_original_input", True)),
    )

    # ── dcmtk ────────────────────────────────────────────────────────────────
    dcmtk_raw = raw.get("dcmtk", {})
    dcmtk = DicomDcmtkConfig(
        bin_dir=str(dcmtk_raw.get("bin_dir", "") or os.environ.get("DCMTK_BIN_DIR", "")),
        dcmodify_path=str(dcmtk_raw.get("dcmodify_path", "")),
        dcmdump_path=str(dcmtk_raw.get("dcmdump_path", "")),
    )

    # ── LIS ──────────────────────────────────────────────────────────────────
    lis_raw = raw.get("lis", {})
    lis = DicomLisConfig(
        enabled=bool(lis_raw.get("enabled", False)),
        enrich_case_metadata=bool(lis_raw.get("enrich_case_metadata", False)),
        sql_server=_opt_str(lis_raw, "sql_server") or os.environ.get("LIS_SQL_SERVER"),
        username=_opt_str(lis_raw, "username") or os.environ.get("LIS_SQL_USERNAME"),
        password=_opt_str(lis_raw, "password") or os.environ.get("LIS_SQL_PASSWORD"),
        query_timeout_seconds=int(lis_raw.get("query_timeout_seconds", 15)),
    )

    # ── upload (placeholder — not active in Phase 11A) ───────────────────────
    up_raw = raw.get("upload", {})
    upload = DicomUploadPlaceholderConfig(
        dry_run=bool(up_raw.get("dry_run", True)),
        timeout_seconds=int(up_raw.get("timeout_seconds", 1800)),
        max_retries=int(up_raw.get("max_retries", 2)),
        retry_backoff_seconds=int(up_raw.get("retry_backoff_seconds", 10)),
    )

    # ── wsidicomizer ─────────────────────────────────────────────────────────
    wsi_raw = raw.get("wsidicomizer", {})
    workers_val = wsi_raw.get("workers")
    wsidicomizer = WsidicomzerConfig(
        enabled=bool(wsi_raw.get("enabled", True)),
        executable=str(wsi_raw.get("executable", "wsidicomizer")),
        prefer_cli=bool(wsi_raw.get("prefer_cli", True)),
        workers=int(workers_val) if workers_val is not None else None,
        timeout_seconds=int(wsi_raw.get("timeout_seconds", 7200)),
    )

    # ── match_construct_patterns ──────────────────────────────────────────────
    patterns = raw.get("match_construct_patterns") or {}

    return DicomEngineConfig(
        paths=paths,
        conversion=conversion,
        dcmtk=dcmtk,
        lis=lis,
        upload=upload,
        wsidicomizer=wsidicomizer,
        match_construct_patterns=patterns,
    )


def _opt_str(mapping: dict, key: str) -> str | None:
    val = mapping.get(key)
    return str(val).strip() if val is not None else None
