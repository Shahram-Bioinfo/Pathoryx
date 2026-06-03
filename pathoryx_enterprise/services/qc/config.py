"""
QC service configuration — two independent concerns.

QCSettings (Pydantic BaseSettings)
    Runtime parameters loaded from environment variables.
    DATABASE_URL, ports, worker counts, service identity.
    Used at startup and by runner.py.

QCServiceConfig (dataclasses, loaded from YAML)
    Routing, scanner policies, and downstream wiring.
    Loaded explicitly via load_qc_service_config(path).
    Required for watcher mode and scanner-policy routing.
    Optional for legacy post-BabelShark trigger-only mode.

ScannerPolicy / PreBabelsharkConfig / PostBabelsharkConfig
    Typed sub-sections of QCServiceConfig.

The inference config (model weights, thresholds, decision rules) lives in a
separate YAML pointed to by QCServiceConfig.service.inference_config_path
and is loaded at runner startup by the old adapter's pipeline.config.load_config().
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


# =============================================================================
# Runtime env-based settings
# =============================================================================

class QCSettings(BaseSettings):
    """
    QC service runtime configuration — loaded from environment variables.

    Required:
      DATABASE_URL      — PostgreSQL DSN
      QC_CONFIG_PATH    — Path to QC inference YAML (models, thresholds, decisions).
                          Also accepted as QC_INFERENCE_CONFIG.

    Optional:
      QC_SERVICE_CONFIG               — Path to qc_service.yaml (routing + scanner
                                        policies).  Required for watcher mode and
                                        scanner-policy routing.  When absent, the
                                        service runs in legacy trigger-only mode.
      QC_MAX_WORKERS                  — concurrent workers (default 2)
      QC_TRIGGER_POLL_INTERVAL_SEC    — trigger poll interval seconds (default 10)
      QC_MAX_CONSECUTIVE_ERRORS       — abort after N errors (default 10)
      PATHORYX_HEALTH_PORT            — health HTTP port (default 8082)
      PATHORYX_METRICS_PORT           — Prometheus port (default 9092)
      PATHORYX_SERVICE_VERSION
      PATHORYX_ENVIRONMENT
    """

    model_config = {"env_file": ".env", "extra": "ignore"}

    database_url: str = Field(validation_alias="DATABASE_URL")

    # Inference config: models, thresholds, decision rules (old-adapter YAML).
    # Runner.py passes this to pipeline.config.load_config() at startup.
    qc_config_path: str = Field(
        validation_alias=AliasChoices("QC_CONFIG_PATH", "QC_INFERENCE_CONFIG")
    )

    # Service routing config: mode, scanner policies, next_service wiring (new YAML).
    # Optional — when absent, post-BabelShark trigger mode with hardcoded defaults.
    qc_service_config_path: Optional[str] = Field(
        default=None,
        validation_alias="QC_SERVICE_CONFIG",
    )

    max_workers: int = Field(
        default=2, validation_alias="QC_MAX_WORKERS", ge=1, le=32
    )
    trigger_poll_interval_seconds: int = Field(
        default=10, validation_alias="QC_TRIGGER_POLL_INTERVAL_SEC", ge=1
    )
    max_consecutive_errors: int = Field(
        default=10, validation_alias="QC_MAX_CONSECUTIVE_ERRORS", ge=1
    )

    health_port: int = Field(default=8082, validation_alias="PATHORYX_HEALTH_PORT")
    metrics_port: int = Field(default=9092, validation_alias="PATHORYX_METRICS_PORT")
    service_version: str = Field(
        default="1.0.0", validation_alias="PATHORYX_SERVICE_VERSION"
    )
    environment: str = Field(
        default="development", validation_alias="PATHORYX_ENVIRONMENT"
    )

    @field_validator("database_url")
    @classmethod
    def no_placeholder_creds(cls, v: str) -> str:
        forbidden = ("CHANGEME", "strongpassword", "password123", "yourpassword")
        for p in forbidden:
            if p in v:
                raise ValueError(
                    f"DATABASE_URL contains placeholder credential '{p}'. "
                    "Set a real password in your environment."
                )
        return v

    @field_validator("qc_config_path")
    @classmethod
    def inference_config_must_exist(cls, v: str) -> str:
        if not Path(v).exists():
            raise ValueError(f"QC inference config does not exist: {v}")
        return v

    @field_validator("qc_service_config_path")
    @classmethod
    def service_config_must_exist_if_set(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not Path(v).exists():
            raise ValueError(f"QC_SERVICE_CONFIG path does not exist: {v}")
        return v


# =============================================================================
# YAML-based service config dataclasses
# =============================================================================

_VALID_MODES = frozenset({"pre_babelshark", "post_babelshark", "both", "disabled"})
_VALID_QC_POSITIONS = frozenset({"pre_babelshark", "post_babelshark", "both", "none"})
_VALID_FILE_ROUTING = frozenset({"copy", "move"})


@dataclass(slots=True)
class ScannerPolicy:
    """
    Per-scanner routing and QC policy.

    In pre_babelshark mode  : scanner_id is the folder label from config —
                              WSI metadata is not yet available.
    In post_babelshark mode : scanner_id comes from the trigger payload
                              written by BabelShark after metadata extraction.
    """

    scanner_id: str
    input_dir: Optional[str] = None
    pathoryx_qc_enabled: bool = True
    qc_position: str = "pre_babelshark"   # pre_babelshark|post_babelshark|both|none
    trust_scanner_qc: bool = False
    qc_skip_reason: Optional[str] = None
    file_routing: str = "copy"             # "copy" | "move"
    passed_output_dir: Optional[str] = None
    failed_output_dir: Optional[str] = None
    quarantine_dir: Optional[str] = None
    next_service: Optional[str] = None
    next_stage: Optional[str] = None


@dataclass(slots=True)
class PreBabelsharkConfig:
    """Shared settings for the pre-BabelShark folder-watcher loop."""

    poll_interval_seconds: int = 10
    stable_file_wait_seconds: int = 20
    allowed_extensions: list = field(
        default_factory=lambda: [".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".vms"]
    )
    recursive: bool = False
    file_routing: str = "copy"
    max_workers: int = 2


@dataclass(slots=True)
class PostBabelsharkConfig:
    """Settings for the post-BabelShark DB-trigger consumer loop."""

    trigger_target_service: str = "qc_service"
    trigger_stage_name: str = "qc"
    max_workers: int = 2
    trigger_poll_interval_seconds: int = 10
    max_consecutive_errors: int = 10
    # Global downstream routing defaults.  Used when no per-scanner policy
    # specifies next_service/next_stage.  None = do not enqueue downstream.
    next_service: Optional[str] = None
    next_stage: Optional[str] = None


@dataclass(slots=True)
class ServiceSection:
    """Top-level service identity and mode settings from qc_service.yaml."""

    enabled: bool = True
    inference_config_path: str = ""
    mode: str = "post_babelshark"   # pre_babelshark|post_babelshark|both|disabled


@dataclass
class QCServiceConfig:
    """
    Full service routing configuration loaded from qc_service.yaml.

    Controls operational mode, scanner policies, and downstream wiring.
    Does NOT contain inference parameters (models / thresholds / decision rules).
    Those live in the inference config referenced by service.inference_config_path.
    """

    service: ServiceSection
    pre_babelshark: PreBabelsharkConfig
    post_babelshark: PostBabelsharkConfig
    scanner_policies: list   # list[ScannerPolicy]

    # ------------------------------------------------------------------
    # Convenience helpers — called by runner and watcher once implemented
    # ------------------------------------------------------------------

    @property
    def inference_config_path(self) -> str:
        """Path to the old-adapter inference YAML (models/thresholds)."""
        return self.service.inference_config_path

    def get_policy(self, scanner_id: str) -> Optional[ScannerPolicy]:
        """
        Return the ScannerPolicy for scanner_id.

        Lookup order:
          1. Exact match on scanner_id.
          2. '__default__' catch-all entry.
          3. None — no policy found.
        """
        default: Optional[ScannerPolicy] = None
        for policy in self.scanner_policies:
            if policy.scanner_id == scanner_id:
                return policy
            if policy.scanner_id == "__default__":
                default = policy
        return default

    def watcher_policies(self) -> list:
        """
        Return policies that define an input_dir and participate in watcher mode.
        Used by the pre-BabelShark watcher to know which folders to poll.
        """
        return [
            p for p in self.scanner_policies
            if p.input_dir is not None
            and p.qc_position in ("pre_babelshark", "both")
            and p.pathoryx_qc_enabled
        ]


# =============================================================================
# Loader
# =============================================================================

def _opt_str(mapping: dict, key: str) -> Optional[str]:
    """Return str(value) for mapping[key], or None if absent/null."""
    val = mapping.get(key)
    return str(val) if val is not None else None


def load_qc_service_config(path: str | os.PathLike[str]) -> QCServiceConfig:
    """
    Parse qc_service.yaml and return a fully validated QCServiceConfig.

    Raises:
        FileNotFoundError — if the YAML file does not exist.
        ValueError        — if required fields are missing or enum values invalid.

    Path existence for output dirs is NOT checked here — paths may be
    mounted at runtime and absent at config-load time.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"QC service config not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh) or {}

    # ── service section ────────────────────────────────────────────────────
    svc_raw = raw.get("service") or {}
    inference_path = (svc_raw.get("inference_config_path") or "").strip()
    if not inference_path:
        raise ValueError(
            "qc_service.yaml: service.inference_config_path is required"
        )

    mode = str(svc_raw.get("mode", "post_babelshark"))
    if mode not in _VALID_MODES:
        raise ValueError(
            f"qc_service.yaml: service.mode must be one of "
            f"{sorted(_VALID_MODES)}, got {mode!r}"
        )

    service = ServiceSection(
        enabled=bool(svc_raw.get("enabled", True)),
        inference_config_path=inference_path,
        mode=mode,
    )

    # ── pre_babelshark section ─────────────────────────────────────────────
    pre_raw = raw.get("pre_babelshark") or {}
    file_routing_pre = str(pre_raw.get("file_routing", "copy"))
    if file_routing_pre not in _VALID_FILE_ROUTING:
        raise ValueError(
            f"qc_service.yaml: pre_babelshark.file_routing must be 'copy' or 'move', "
            f"got {file_routing_pre!r}"
        )

    pre = PreBabelsharkConfig(
        poll_interval_seconds=int(pre_raw.get("poll_interval_seconds", 10)),
        stable_file_wait_seconds=int(pre_raw.get("stable_file_wait_seconds", 20)),
        allowed_extensions=list(
            pre_raw.get(
                "allowed_extensions",
                [".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".vms"],
            )
        ),
        recursive=bool(pre_raw.get("recursive", False)),
        file_routing=file_routing_pre,
        max_workers=int(pre_raw.get("max_workers", 2)),
    )

    # ── post_babelshark section ────────────────────────────────────────────
    post_raw = raw.get("post_babelshark") or {}
    post = PostBabelsharkConfig(
        trigger_target_service=str(
            post_raw.get("trigger_target_service", "qc_service")
        ),
        trigger_stage_name=str(post_raw.get("trigger_stage_name", "qc")),
        max_workers=int(post_raw.get("max_workers", 2)),
        trigger_poll_interval_seconds=int(
            post_raw.get("trigger_poll_interval_seconds", 10)
        ),
        max_consecutive_errors=int(post_raw.get("max_consecutive_errors", 10)),
        next_service=_opt_str(post_raw, "next_service"),
        next_stage=_opt_str(post_raw, "next_stage"),
    )

    # ── scanner_policies ──────────────────────────────────────────────────
    policies: list[ScannerPolicy] = []
    for i, entry in enumerate(raw.get("scanner_policies") or []):
        if not isinstance(entry, dict):
            raise ValueError(
                f"qc_service.yaml: scanner_policies[{i}] must be a mapping"
            )

        scanner_id = str(entry.get("scanner_id") or "").strip()
        if not scanner_id:
            raise ValueError(
                f"qc_service.yaml: scanner_policies[{i}].scanner_id is required"
            )

        qc_pos = str(entry.get("qc_position", "pre_babelshark"))
        if qc_pos not in _VALID_QC_POSITIONS:
            raise ValueError(
                f"qc_service.yaml: scanner_policies[{i}].qc_position must be one of "
                f"{sorted(_VALID_QC_POSITIONS)}, got {qc_pos!r}"
            )

        routing = str(entry.get("file_routing", pre.file_routing))
        if routing not in _VALID_FILE_ROUTING:
            raise ValueError(
                f"qc_service.yaml: scanner_policies[{i}].file_routing must be "
                f"'copy' or 'move', got {routing!r}"
            )

        policies.append(
            ScannerPolicy(
                scanner_id=scanner_id,
                input_dir=_opt_str(entry, "input_dir"),
                pathoryx_qc_enabled=bool(entry.get("pathoryx_qc_enabled", True)),
                qc_position=qc_pos,
                trust_scanner_qc=bool(entry.get("trust_scanner_qc", False)),
                qc_skip_reason=_opt_str(entry, "qc_skip_reason"),
                file_routing=routing,
                passed_output_dir=_opt_str(entry, "passed_output_dir"),
                failed_output_dir=_opt_str(entry, "failed_output_dir"),
                quarantine_dir=_opt_str(entry, "quarantine_dir"),
                next_service=_opt_str(entry, "next_service"),
                next_stage=_opt_str(entry, "next_stage"),
            )
        )

    return QCServiceConfig(
        service=service,
        pre_babelshark=pre,
        post_babelshark=post,
        scanner_policies=policies,
    )
