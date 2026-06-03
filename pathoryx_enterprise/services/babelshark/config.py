"""
BabelShark service configuration.

All settings are loaded from environment variables via Pydantic BaseSettings.
The YAML config path is the only positional arg accepted by the service
(passed at startup); the rest of the runtime config is env-based.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


class BabelSharkSettings(BaseSettings):
    """
    BabelShark runtime configuration.

    Required env vars:
      DATABASE_URL         — PostgreSQL DSN (no hardcoded defaults)
      BABELSHARK_CONFIG    — Path to the YAML collector config

    Optional env vars (with defaults):
      BABELSHARK_POLL_INTERVAL_SECONDS   — How often to run a collection cycle (default 60)
      BABELSHARK_NEXT_STAGE              — "qc" or "dicom" (default "qc")
      BABELSHARK_NEXT_SERVICE            — target service name for trigger dispatch
      BABELSHARK_MAX_CONSECUTIVE_ERRORS  — abort loop after N consecutive errors (default 10)
      PATHORYX_HEALTH_PORT               — health/ready/live HTTP port (default 8081)
      PATHORYX_METRICS_PORT              — Prometheus scrape port (default 9091)
      PATHORYX_SERVICE_VERSION           — service version string
      PATHORYX_ENVIRONMENT               — "development" | "staging" | "production"
    """

    model_config = {"env_file": ".env", "extra": "ignore"}

    # Required
    database_url: str = Field(validation_alias="DATABASE_URL")
    collector_config_path: str = Field(
        validation_alias=AliasChoices("BABELSHARK_CONFIG_PATH", "BABELSHARK_CONFIG")
    )

    # Runner behaviour
    poll_interval_seconds: int = Field(
        default=60, validation_alias="BABELSHARK_POLL_INTERVAL_SECONDS", ge=5
    )
    next_stage: str = Field(default="qc", validation_alias="BABELSHARK_NEXT_STAGE")
    next_service: str = Field(
        default="qc_service", validation_alias="BABELSHARK_NEXT_SERVICE"
    )
    max_consecutive_errors: int = Field(
        default=10, validation_alias="BABELSHARK_MAX_CONSECUTIVE_ERRORS", ge=1
    )

    # Observability
    health_port: int = Field(default=8081, validation_alias="PATHORYX_HEALTH_PORT")
    metrics_port: int = Field(default=9091, validation_alias="PATHORYX_METRICS_PORT")
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
        for placeholder in forbidden:
            if placeholder in v:
                raise ValueError(
                    f"DATABASE_URL contains placeholder credential '{placeholder}'. "
                    "Set a real password in your environment."
                )
        return v

    @field_validator("collector_config_path")
    @classmethod
    def config_must_exist(cls, v: str) -> str:
        if not Path(v).exists():
            raise ValueError(f"BABELSHARK_CONFIG path does not exist: {v}")
        return v

    @field_validator("next_stage")
    @classmethod
    def valid_next_stage(cls, v: str) -> str:
        if v not in {"qc", "dicom"}:
            raise ValueError(f"BABELSHARK_NEXT_STAGE must be 'qc' or 'dicom', got: {v!r}")
        return v
