"""
Orchestrator entrypoint — launches all services under the process supervisor.

Usage:
    pathoryx-orchestrator
    python -m pathoryx_enterprise.orchestrator.main

Environment:
    PATHORYX_SERVICES   — comma-separated list of services to start.
                          Defaults to all: babelshark,qc,dicom,upload,recovery_sentry
                          'failed_watcher' is a backward-compat alias for recovery_sentry.
    PATHORYX_LOG_DIR    — directory for rotating log files (default: data/logs).
                          orchestrator.log is written here.
    All DATABASE_URL, BABELSHARK_CONFIG, QC_SERVICE_CONFIG, DICOM_CONFIG env vars
    must be set before launching.
"""
from __future__ import annotations

import os
import sys

import structlog

from pathoryx_enterprise.logging.setup import add_file_handler, configure_logging
from pathoryx_enterprise.orchestrator.supervisor import ProcessSupervisor, ServiceSpec

logger = structlog.get_logger(__name__)

# Environment variables that child services require.  Missing required vars are
# reported at startup so the failure is obvious rather than buried in a service log.
_REQUIRED_ENV_VARS = [
    "DATABASE_URL",
]
_OPTIONAL_ENV_VARS = [
    "OPENSLIDE_DLL_PATH",
    "BABELSHARK_CONFIG_PATH",
    "QC_CONFIG_PATH",
    "QC_SERVICE_CONFIG",
    "DICOM_CONFIG_PATH",
    "RECOVERY_SENTRY_CONFIG",
    "SCANNER_FLEET_CONFIG",
    "PASNET_SERVER",
    "PASNET_USERNAME",
    # PASNET_PASSWORD intentionally omitted — never log credential presence
]
_SECRET_VARS = {"DATABASE_URL", "PASNET_PASSWORD"}


def _log_runtime_env() -> None:
    """Log which env vars are configured (values redacted for secrets)."""
    missing_required: list[str] = []
    configured: list[str] = []
    missing_optional: list[str] = []

    for var in _REQUIRED_ENV_VARS:
        val = os.environ.get(var, "")
        if val:
            configured.append(var)
        else:
            missing_required.append(var)

    for var in _OPTIONAL_ENV_VARS:
        val = os.environ.get(var, "")
        if val:
            configured.append(var)
        else:
            missing_optional.append(var)

    if configured:
        logger.info(
            "runtime env: configured",
            vars=", ".join(configured),
        )
    if missing_optional:
        logger.debug(
            "runtime env: optional vars not set",
            vars=", ".join(missing_optional),
        )
    if missing_required:
        for var in missing_required:
            logger.error("runtime env: required variable not set", var=var)
        print(
            f"[FATAL] Orchestrator cannot start — required environment variable(s) "
            f"not set: {', '.join(missing_required)}",
            file=sys.stderr,
        )
        sys.exit(1)


_ALL_SERVICES = {
    "babelshark": ["pathoryx-babelshark"],
    "qc": ["pathoryx-qc"],
    "dicom": ["pathoryx-dicom"],
    "upload": ["pathoryx-uploader"],
    "recovery_sentry": ["pathoryx-recovery-sentry"],
    # Backward-compat alias — maps old name to the new service binary
    "failed_watcher": ["pathoryx-recovery-sentry"],
}


def main() -> None:
    configure_logging(
        service_name="orchestrator",
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        json_output=True,
    )
    add_file_handler("orchestrator", log_dir=os.environ.get("PATHORYX_LOG_DIR", "data/logs"))

    _log_runtime_env()

    # Default service list excludes the 'failed_watcher' alias to avoid duplication
    _default_services = [k for k in _ALL_SERVICES if k != "failed_watcher"]
    services_raw = os.environ.get("PATHORYX_SERVICES", ",".join(_default_services))
    requested = [s.strip() for s in services_raw.split(",") if s.strip()]

    specs = []
    for name in requested:
        command = _ALL_SERVICES.get(name)
        if command is None:
            print(f"[FATAL] Unknown service: {name!r}. Valid: {list(_ALL_SERVICES)}", file=sys.stderr)
            sys.exit(1)
        specs.append(ServiceSpec(name=name, command=command))

    if not specs:
        print("[FATAL] No services configured to start.", file=sys.stderr)
        sys.exit(1)

    supervisor = ProcessSupervisor(specs)
    supervisor.start_all()
    supervisor.wait_forever()


if __name__ == "__main__":
    main()
