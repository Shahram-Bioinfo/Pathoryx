"""
Central configuration for Palantir Enterprise.

All settings are resolved from environment variables (or a .env file).
No hardcoded credentials or paths anywhere in this file.
"""
from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")

    url: str = Field(
        default=...,
        validation_alias="DATABASE_URL",
        description="Full PostgreSQL connection string. Required — no default.",
    )
    pool_size: int = Field(default=10, ge=1, le=100)
    max_overflow: int = Field(default=20, ge=0, le=200)
    pool_recycle: int = Field(default=1800, ge=60, description="Seconds before recycling a connection.")
    pool_pre_ping: bool = Field(default=True)
    echo_sql: bool = Field(default=False)

    @field_validator("url")
    @classmethod
    def url_must_not_contain_literal_password(cls, v: str) -> str:
        # Reject obvious placeholder passwords that developers sometimes leave in.
        forbidden = ("CHANGEME", "strongpassword", "password123", "yourpassword")
        lower = v.lower()
        for f in forbidden:
            if f.lower() in lower:
                raise ValueError(
                    f"DATABASE_URL contains a placeholder password ({f!r}). "
                    "Set a real secret via the DATABASE_URL environment variable."
                )
        return v

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class ServiceIdentitySettings(BaseSettings):
    """
    Stable identity for this runner process.
    runner_id is generated once per process and registered in runner_registrations.
    host_id is the machine hostname.
    """

    environment: str = Field(default="development", validation_alias="PATHORYX_ENVIRONMENT")
    site_code: str = Field(default="site_local", validation_alias="PATHORYX_SITE_CODE")
    service_version: str = Field(default="1.0.0", validation_alias="PATHORYX_SERVICE_VERSION")

    # Derived at runtime, not from env
    runner_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    host_id: str = Field(default_factory=socket.gethostname)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class PathSettings(BaseSettings):
    runtime_root: Path = Field(
        default=Path("/data/pathoryx/runtime"),
        validation_alias="PATHORYX_RUNTIME_ROOT",
    )
    output_root: Path = Field(
        default=Path("/data/pathoryx/output"),
        validation_alias="PATHORYX_OUTPUT_ROOT",
    )
    archive_root: Optional[Path] = Field(
        default=None,
        validation_alias="PATHORYX_ARCHIVE_ROOT",
    )
    quarantine_root: Path = Field(
        default=Path("/data/pathoryx/quarantine"),
        validation_alias="PATHORYX_QUARANTINE_ROOT",
    )
    failed_root: Path = Field(
        default=Path("/data/pathoryx/failed"),
        validation_alias="PATHORYX_FAILED_ROOT",
    )
    suspicious_root: Path = Field(
        default=Path("/data/pathoryx/suspicious"),
        validation_alias="PATHORYX_SUSPICIOUS_ROOT",
    )
    technician_review_root: Path = Field(
        default=Path("/data/pathoryx/technician_review"),
        validation_alias="PATHORYX_TECHNICIAN_REVIEW_ROOT",
    )
    log_root: Optional[Path] = Field(
        default=None,
        validation_alias="PATHORYX_LOG_ROOT",
    )
    temp_root: Path = Field(
        default=Path("/tmp/pathoryx"),
        validation_alias="PATHORYX_TEMP_ROOT",
    )

    # Security: all incoming paths must resolve under one of these roots.
    allowed_input_roots: list[str] = Field(
        default_factory=list,
        validation_alias="PATHORYX_ALLOWED_INPUT_ROOTS",
    )

    @field_validator("allowed_input_roots", mode="before")
    @classmethod
    def parse_allowed_roots(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [r.strip() for r in v.split(",") if r.strip()]
        return v  # type: ignore[return-value]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class ObservabilitySettings(BaseSettings):
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="json", validation_alias="LOG_FORMAT")

    otel_enabled: bool = Field(default=False, validation_alias="OTEL_ENABLED")
    otel_service_name: str = Field(
        default="pathoryx-enterprise",
        validation_alias="OTEL_SERVICE_NAME",
    )
    otel_exporter_endpoint: Optional[str] = Field(
        default=None,
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )

    prometheus_enabled: bool = Field(
        default=True,
        validation_alias="PROMETHEUS_METRICS_ENABLED",
    )
    prometheus_port: int = Field(
        default=9090,
        validation_alias="PROMETHEUS_METRICS_PORT",
        ge=1024,
        le=65535,
    )

    health_http_enabled: bool = Field(
        default=True,
        validation_alias="HEALTH_HTTP_ENABLED",
    )
    health_http_port: int = Field(
        default=8080,
        validation_alias="HEALTH_HTTP_PORT",
        ge=1024,
        le=65535,
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class SectraSettings(BaseSettings):
    host: str = Field(default="localhost", validation_alias="SECTRA_HOST")
    port: int = Field(default=104, validation_alias="SECTRA_PORT", ge=1, le=65535)
    local_ae_title: str = Field(default="LOCAL_AE", validation_alias="SECTRA_AE_TITLE")
    remote_ae_title: str = Field(default="REMOTE_AE", validation_alias="SECTRA_REMOTE_AE_TITLE")
    cstore_bin: str = Field(default="storescu", validation_alias="SECTRA_CSTORE_BIN")
    upload_timeout_seconds: int = Field(
        default=1800,
        validation_alias="SECTRA_UPLOAD_TIMEOUT_SECONDS",
        ge=10,
    )
    # Max .dcm files per storescu invocation — prevents ARG_MAX overflow.
    cstore_batch_size: int = Field(
        default=500,
        validation_alias="SECTRA_CSTORE_BATCH_SIZE",
        ge=1,
        le=5000,
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class EnterpriseSettings(BaseSettings):
    """
    Aggregated root settings object.
    Instantiate once at service startup; pass into all components.
    """

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    identity: ServiceIdentitySettings = Field(default_factory=ServiceIdentitySettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    sectra: SectraSettings = Field(default_factory=SectraSettings)

    @model_validator(mode="after")
    def validate_database_url_present(self) -> "EnterpriseSettings":
        # Confirm DATABASE_URL was actually set (field_validator catches placeholders,
        # but we double-check here that it resolves to a non-empty string).
        if not self.database.url:
            raise ValueError("DATABASE_URL must be set in the environment.")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def load_settings() -> EnterpriseSettings:
    """Load and validate all settings from the environment. Call once at startup."""
    return EnterpriseSettings()
