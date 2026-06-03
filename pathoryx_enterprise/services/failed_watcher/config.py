"""Failed/Suspicious Slide Watcher service configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class FailedWatcherSettings(BaseSettings):
    """
    Failed Watcher service configuration.

    Required env vars:
      DATABASE_URL
      FAILED_WATCHER_FOLDERS  — comma-separated list of folder paths to monitor

    Optional:
      FAILED_WATCHER_LABELS               — comma-separated labels for each folder
      FAILED_WATCHER_SCAN_INTERVAL_SEC    — seconds between folder scans (default 30)
      FAILED_WATCHER_MAX_ERRORS           — abort after N consecutive errors (default 20)
      FAILED_WATCHER_REQUIRES_APPROVAL    — require manual approval before requeue (default false)
      FAILED_WATCHER_ALLOWED_ROOTS        — comma-separated allowed root paths for path validation
      PATHORYX_HEALTH_PORT
      PATHORYX_METRICS_PORT
      PATHORYX_SERVICE_VERSION
      PATHORYX_ENVIRONMENT
    """

    model_config = {"env_file": ".env", "extra": "ignore"}

    database_url: str = Field(validation_alias="DATABASE_URL")

    watch_folders_raw: str = Field(
        default="", validation_alias="FAILED_WATCHER_FOLDERS"
    )
    folder_labels_raw: str = Field(
        default="", validation_alias="FAILED_WATCHER_LABELS"
    )

    scan_interval_seconds: int = Field(
        default=30, validation_alias="FAILED_WATCHER_SCAN_INTERVAL_SEC", ge=5
    )
    max_consecutive_errors: int = Field(
        default=20, validation_alias="FAILED_WATCHER_MAX_ERRORS", ge=1
    )
    requires_approval_default: bool = Field(
        default=False, validation_alias="FAILED_WATCHER_REQUIRES_APPROVAL"
    )
    allowed_roots_raw: str = Field(
        default="", validation_alias="FAILED_WATCHER_ALLOWED_ROOTS"
    )

    health_port: int = Field(default=8085, validation_alias="PATHORYX_HEALTH_PORT")
    metrics_port: int = Field(default=9095, validation_alias="PATHORYX_METRICS_PORT")
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

    @property
    def watch_folders(self) -> list[Path]:
        if not self.watch_folders_raw.strip():
            return []
        return [Path(p.strip()) for p in self.watch_folders_raw.split(",") if p.strip()]

    @property
    def folder_labels(self) -> list[str]:
        if not self.folder_labels_raw.strip():
            # Default: use folder name as label
            return [f.name for f in self.watch_folders]
        raw = [s.strip() for s in self.folder_labels_raw.split(",") if s.strip()]
        folders = self.watch_folders
        # Pad or truncate to match folder count
        while len(raw) < len(folders):
            raw.append(folders[len(raw)].name)
        return raw[: len(folders)]

    @property
    def allowed_roots(self) -> list[Path]:
        if not self.allowed_roots_raw.strip():
            return self.watch_folders
        return [Path(p.strip()) for p in self.allowed_roots_raw.split(",") if p.strip()]
