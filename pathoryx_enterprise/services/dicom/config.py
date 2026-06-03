"""
DICOM service configuration.

DICOMSettings  — env-based runtime settings (Pydantic BaseSettings).
                 Controls ports, workers, Sectra connection, timing.

load_dicom_engine_config() — loads dicom_config.yaml into DicomEngineConfig.
                              Controls conversion backend, dcmtk path, LIS,
                              slide ID patterns. Re-exported here for convenience.

Phase 11C architecture
────────────────────────
DICOM service:   convert only. No storescu. Enqueues upload_service trigger.
Upload service:  owns storescu C-STORE to Sectra.

SECTRA_HOST / SECTRA_PORT / SECTRA_REMOTE_AE / SECTRA_LOCAL_AE are still parsed
so the runner can log connection info and so the uploader service can inherit
them if needed. They are optional (default empty) when DICOM_PERFORM_UPLOAD=false.
"""
from __future__ import annotations

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings

from pathoryx_enterprise.services.dicom.engine.config import (  # noqa: F401  re-export
    DicomEngineConfig,
    load_dicom_engine_config,
)


class DICOMSettings(BaseSettings):
    """
    DICOM conversion service runtime configuration.

    Required env vars:
      DATABASE_URL        — PostgreSQL DSN
      DICOM_CONFIG_PATH   — Path to the DICOM service YAML config (alias: DICOM_CONFIG)

    Phase 11C: perform_upload=False (default).
    SECTRA connection settings are optional unless DICOM_PERFORM_UPLOAD=true.

      DICOM_PERFORM_UPLOAD           — true = DICOM service runs storescu after conversion
                                       false (default) = upload delegated to upload_service

    Sectra PACS settings (required only when DICOM_PERFORM_UPLOAD=true):
      SECTRA_HOST         — PACS host
      SECTRA_PORT         — PACS port
      SECTRA_REMOTE_AE    — Remote AE title
      SECTRA_LOCAL_AE     — Local AE title

    Optional:
      DICOM_MAX_WORKERS              — concurrent DICOM workers (default 1)
      DICOM_TRIGGER_POLL_INTERVAL    — seconds between trigger polls (default 10)
      DICOM_MAX_CONSECUTIVE_ERRORS   — abort after N errors (default 10)
      SECTRA_CSTORE_BIN              — storescu binary (default "storescu")
      SECTRA_CSTORE_BATCH_SIZE       — max DCM files per storescu call (default 500)
      SECTRA_UPLOAD_TIMEOUT_SECONDS  — per-batch upload timeout (default 1800)
      PATHORYX_HEALTH_PORT
      PATHORYX_METRICS_PORT
      PATHORYX_SERVICE_VERSION
      PATHORYX_ENVIRONMENT
    """

    model_config = {"env_file": ".env", "extra": "ignore"}

    database_url: str = Field(validation_alias="DATABASE_URL")
    dicom_config_path: str = Field(
        validation_alias=AliasChoices("DICOM_CONFIG_PATH", "DICOM_CONFIG")
    )

    # Phase 11C: upload separation.
    # Default False — DICOM service converts only; upload_service handles storescu.
    # Set DICOM_PERFORM_UPLOAD=true only to re-enable the old combined mode.
    perform_upload: bool = Field(default=False, validation_alias="DICOM_PERFORM_UPLOAD")

    # Sectra PACS connection — optional when perform_upload=False.
    sectra_host: str = Field(default="", validation_alias="SECTRA_HOST")
    sectra_port: int = Field(default=104, validation_alias="SECTRA_PORT")
    sectra_remote_ae: str = Field(default="", validation_alias="SECTRA_REMOTE_AE")
    sectra_local_ae: str = Field(default="", validation_alias="SECTRA_LOCAL_AE")

    cstore_batch_size: int = Field(
        default=500, validation_alias="SECTRA_CSTORE_BATCH_SIZE", ge=1, le=5000
    )
    cstore_bin: str = Field(default="storescu", validation_alias="SECTRA_CSTORE_BIN")
    upload_timeout_seconds: int = Field(
        default=1800, validation_alias="SECTRA_UPLOAD_TIMEOUT_SECONDS", ge=30
    )

    max_workers: int = Field(
        default=1, validation_alias="DICOM_MAX_WORKERS", ge=1, le=8
    )
    trigger_poll_interval_seconds: int = Field(
        default=10, validation_alias="DICOM_TRIGGER_POLL_INTERVAL", ge=1
    )
    max_consecutive_errors: int = Field(
        default=10, validation_alias="DICOM_MAX_CONSECUTIVE_ERRORS", ge=1
    )

    health_port: int = Field(default=8083, validation_alias="PATHORYX_HEALTH_PORT")
    metrics_port: int = Field(default=9093, validation_alias="PATHORYX_METRICS_PORT")
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
