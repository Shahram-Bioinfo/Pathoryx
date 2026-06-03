"""Uploader service configuration."""
from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class UploaderSettings(BaseSettings):
    """
    Upload service configuration. All values come from environment variables.

    Required:
      DATABASE_URL

    Optional:
      UPLOADER_TRIGGER_POLL_INTERVAL  — seconds between trigger polls (default 10)
      UPLOADER_MAX_RETRIES            — per-trigger max retry attempts (default 3)
      UPLOADER_MAX_CONSECUTIVE_ERRORS — abort loop after N errors (default 10)
      UPLOADER_CIRCUIT_BREAK_THRESHOLD— consecutive failures before circuit opens (default 5)
      UPLOADER_CIRCUIT_RESET_SECONDS  — how long to wait before circuit half-opens (default 60)
      PATHORYX_HEALTH_PORT
      PATHORYX_METRICS_PORT
      PATHORYX_SERVICE_VERSION
      PATHORYX_ENVIRONMENT
    """

    model_config = {"env_file": ".env", "extra": "ignore"}

    database_url: str = Field(validation_alias="DATABASE_URL")

    trigger_poll_interval_seconds: int = Field(
        default=10, validation_alias="UPLOADER_TRIGGER_POLL_INTERVAL", ge=1
    )
    max_retries: int = Field(
        default=3, validation_alias="UPLOADER_MAX_RETRIES", ge=1, le=10
    )
    max_consecutive_errors: int = Field(
        default=10, validation_alias="UPLOADER_MAX_CONSECUTIVE_ERRORS", ge=1
    )
    circuit_break_threshold: int = Field(
        default=5, validation_alias="UPLOADER_CIRCUIT_BREAK_THRESHOLD", ge=1
    )
    circuit_reset_seconds: int = Field(
        default=60, validation_alias="UPLOADER_CIRCUIT_RESET_SECONDS", ge=10
    )

    health_port: int = Field(default=8084, validation_alias="PATHORYX_HEALTH_PORT")
    metrics_port: int = Field(default=9094, validation_alias="PATHORYX_METRICS_PORT")
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
