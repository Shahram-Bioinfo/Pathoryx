"""
Uploader service configuration.

All values are sourced from environment variables (or a .env file).

SECTRA_DRY_RUN defaults to true for safety.  The upload service will NOT
transmit to the PACS until you explicitly set SECTRA_DRY_RUN=false in your
environment or .env file and supply the four required PACS variables:

    SECTRA_HOST        hostname or IP of the Sectra PACS server
    SECTRA_PORT        C-STORE port (Sectra default: 32001)
    SECTRA_REMOTE_AE   Called AE title on the PACS side  (e.g. DICOM_STORAGE)
    SECTRA_LOCAL_AE    Calling AE title for this host     (e.g. DICOM_STORAGE)

Startup will fail with a clear error message if SECTRA_DRY_RUN=false but any
of the four required PACS variables is missing.

Optional PACS tuning:
    SECTRA_CSTORE_BIN             storescu binary (default: "storescu").
                                  Use the full path on Windows if storescu.exe
                                  is not on PATH, e.g.:
                                  "C:\\Program Files\\dcmtk-3.7.0-win64-dynamic\\bin\\storescu.exe"
    SECTRA_CSTORE_BATCH_SIZE      max DCM files per storescu invocation (default 500)
    SECTRA_UPLOAD_TIMEOUT_SECONDS per-batch C-STORE timeout in seconds  (default 1800)

Log file:
    PATHORYX_LOG_DIR   directory where rotating log files are written (default "data/logs").
                       The upload service writes to PATHORYX_LOG_DIR/upload.log.
"""
from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class UploaderSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    database_url: str = Field(validation_alias="DATABASE_URL")

    # ── Sectra PACS connection ─────────────────────────────────────────────────
    # dry_run=True (default): C-STORE commands are built and logged but NOT executed.
    # Production requires: SECTRA_DRY_RUN=false  +  the four PACS variables below.
    dry_run: bool = Field(default=True, validation_alias="SECTRA_DRY_RUN")

    sectra_host: str = Field(default="", validation_alias="SECTRA_HOST")
    sectra_port: int = Field(
        default=32001, validation_alias="SECTRA_PORT", ge=1, le=65535
    )
    sectra_remote_ae: str = Field(default="", validation_alias="SECTRA_REMOTE_AE")
    sectra_local_ae: str = Field(default="", validation_alias="SECTRA_LOCAL_AE")

    # storescu binary — full absolute path or just "storescu" if it is on PATH.
    # Windows example: "C:\\Program Files\\dcmtk-3.7.0-win64-dynamic\\bin\\storescu.exe"
    cstore_bin: str = Field(default="storescu", validation_alias="SECTRA_CSTORE_BIN")

    cstore_batch_size: int = Field(
        default=500, validation_alias="SECTRA_CSTORE_BATCH_SIZE", ge=1, le=5000
    )
    upload_timeout_seconds: int = Field(
        default=1800, validation_alias="SECTRA_UPLOAD_TIMEOUT_SECONDS", ge=30
    )

    # ── Log file ──────────────────────────────────────────────────────────────
    # Rotating log file is written to {log_dir}/upload.log.
    # Set PATHORYX_LOG_DIR to override the directory (e.g. /var/log/pathoryx).
    log_dir: str = Field(default="data/logs", validation_alias="PATHORYX_LOG_DIR")

    # ── Trigger loop / retry ──────────────────────────────────────────────────
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

    # ── Service metadata ──────────────────────────────────────────────────────
    health_port: int = Field(default=8084, validation_alias="PATHORYX_HEALTH_PORT")
    metrics_port: int = Field(default=9094, validation_alias="PATHORYX_METRICS_PORT")
    service_version: str = Field(
        default="1.0.0", validation_alias="PATHORYX_SERVICE_VERSION"
    )
    environment: str = Field(
        default="development", validation_alias="PATHORYX_ENVIRONMENT"
    )

    # ── Validators ────────────────────────────────────────────────────────────

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
    def validate_pacs_config(self) -> "UploaderSettings":
        """
        When dry_run=False, the four PACS connection variables are mandatory.
        Fail fast at startup with a clear error rather than failing silently at
        upload time.
        """
        if not self.dry_run:
            missing: list[str] = []
            if not self.sectra_host:
                missing.append("SECTRA_HOST")
            if not self.sectra_remote_ae:
                missing.append("SECTRA_REMOTE_AE")
            if not self.sectra_local_ae:
                missing.append("SECTRA_LOCAL_AE")
            if missing:
                raise ValueError(
                    f"SECTRA_DRY_RUN=false requires these environment variables "
                    f"to be set: {', '.join(missing)}"
                )
        return self
