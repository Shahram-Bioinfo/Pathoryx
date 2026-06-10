"""
RecoverySentry configuration.

Supports two loading modes:
  1. YAML config file (RECOVERY_SENTRY_CONFIG env var or default path)
  2. Env-var overrides on top of YAML (for Docker/K8s deployments)

Priority: env vars > YAML file > defaults

Backward-compatible: also reads FAILED_WATCHER_FOLDERS if
RECOVERY_SENTRY_CONFIG is not set.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class RecoverySentrySettings(BaseSettings):
    """
    RecoverySentry runtime configuration.

    Required env vars:
      DATABASE_URL

    Optional env vars:
      RECOVERY_SENTRY_CONFIG        — path to recovery_sentry.yaml (default: auto-discovered)
      FAILED_WATCHER_FOLDERS        — backward-compat comma-separated watch folders
      RECOVERY_SENTRY_POLL_SECONDS  — override poll interval
    """

    model_config = {"env_file": ".env", "extra": "ignore"}

    database_url: str = Field(validation_alias="DATABASE_URL")

    # Config file path — env var takes priority, then default paths
    config_file_path: Optional[str] = Field(
        default=None, validation_alias="RECOVERY_SENTRY_CONFIG"
    )

    # Backward-compat fallback for watch folders from env (FAILED_WATCHER_FOLDERS)
    fallback_folders_raw: str = Field(
        default="", validation_alias="FAILED_WATCHER_FOLDERS"
    )

    # === Fields populated from YAML + env overrides ===

    service_name: str = "recovery_sentry"
    poll_interval_seconds: int = 30
    stable_after_seconds: int = 10

    watch_folders_raw: list[str] = []

    final_destination_root: Optional[str] = None
    babelshark_config_path: Optional[str] = None

    # Recovery behaviour
    auto_recover_valid_slide_id: bool = True
    add_timestamp_if_missing: bool = True
    overwrite_existing: bool = False
    duplicate_strategy: str = "suffix"       # "suffix" | "manual_review"
    checksum_mode: str = "partial"           # "partial" | "full" | "none"
    allow_filesystem_timestamp_fallback: bool = False

    # Next stage after recovery
    next_stage_target_service: str = "qc_service"
    next_stage_name: str = "qc"

    # Optional manual-approval gate
    requires_approval_default: bool = False

    # Recursive folder scanning
    scan_subfolders: bool = True

    # Max consecutive errors before aborting
    max_consecutive_errors: int = 20

    # Observability
    health_port: int = Field(default=8087, validation_alias="PATHORYX_HEALTH_PORT")
    metrics_port: int = Field(default=9097, validation_alias="PATHORYX_METRICS_PORT")
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

    @model_validator(mode="after")
    def _load_yaml_config(self) -> "RecoverySentrySettings":
        yaml_path = self._resolve_config_path()
        if yaml_path is None:
            return self

        try:
            with open(yaml_path) as fh:
                raw = yaml.safe_load(fh) or {}
        except FileNotFoundError:
            return self

        svc = raw.get("service", {}) or {}
        if svc.get("poll_interval_seconds") is not None:
            self.poll_interval_seconds = int(svc["poll_interval_seconds"])
        if svc.get("stable_after_seconds") is not None:
            self.stable_after_seconds = int(svc["stable_after_seconds"])

        watch_raw = raw.get("watch_folders", []) or []
        if watch_raw and not self.watch_folders_raw:
            self.watch_folders_raw = [str(p) for p in watch_raw]

        if raw.get("final_destination_root"):
            self.final_destination_root = str(raw["final_destination_root"])
        if raw.get("babelshark_config_path"):
            self.babelshark_config_path = str(raw["babelshark_config_path"])

        rec = raw.get("recovery", {}) or {}
        if rec.get("auto_recover_valid_slide_id") is not None:
            self.auto_recover_valid_slide_id = bool(rec["auto_recover_valid_slide_id"])
        if rec.get("add_timestamp_if_missing") is not None:
            self.add_timestamp_if_missing = bool(rec["add_timestamp_if_missing"])
        if rec.get("overwrite_existing") is not None:
            self.overwrite_existing = bool(rec["overwrite_existing"])
        if rec.get("duplicate_strategy"):
            self.duplicate_strategy = str(rec["duplicate_strategy"])
        if rec.get("checksum_mode"):
            self.checksum_mode = str(rec["checksum_mode"])
        if rec.get("allow_filesystem_timestamp_fallback") is not None:
            self.allow_filesystem_timestamp_fallback = bool(
                rec["allow_filesystem_timestamp_fallback"]
            )
        # scan_subfolders can live either at top-level or under recovery:
        _ssf = raw.get("scan_subfolders") if "scan_subfolders" in raw else rec.get("scan_subfolders")
        if _ssf is not None:
            self.scan_subfolders = bool(_ssf)

        ns = raw.get("next_stage", {}) or {}
        if ns.get("target_service"):
            self.next_stage_target_service = str(ns["target_service"])
        if ns.get("stage_name"):
            self.next_stage_name = str(ns["stage_name"])

        return self

    def _resolve_config_path(self) -> Optional[Path]:
        # Explicit env var
        if self.config_file_path:
            return Path(self.config_file_path)
        # Environment variable (un-validated since model_validator runs post-init)
        env_path = os.environ.get("RECOVERY_SENTRY_CONFIG")
        if env_path:
            return Path(env_path)
        # Default search paths
        for candidate in (
            Path("configs/recovery_sentry.yaml"),
            Path(__file__).parent.parent.parent.parent / "configs" / "recovery_sentry.yaml",
        ):
            if candidate.exists():
                return candidate
        return None

    @property
    def watch_folders(self) -> list[Path]:
        # YAML watch_folders_raw takes priority; fallback to FAILED_WATCHER_FOLDERS env/field
        raw = self.watch_folders_raw
        if not raw and self.fallback_folders_raw:
            raw = [p.strip() for p in self.fallback_folders_raw.split(",") if p.strip()]
        return [Path(p) for p in raw if p]

    @property
    def final_destination(self) -> Optional[Path]:
        if self.final_destination_root:
            return Path(self.final_destination_root)
        # Try to resolve from babelshark config
        if self.babelshark_config_path:
            try:
                with open(self.babelshark_config_path) as fh:
                    bs_cfg = yaml.safe_load(fh) or {}
                dest = bs_cfg.get("final_output_dir")
                if dest:
                    return Path(dest)
            except Exception:
                pass
        return None

    @property
    def allowed_roots(self) -> list[Path]:
        roots = list(self.watch_folders)
        if self.final_destination is not None:
            roots.append(self.final_destination)
        return roots
