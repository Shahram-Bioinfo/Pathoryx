"""
Orchestrator entrypoint — launches all services under the process supervisor.

Usage:
    pathoryx-orchestrator
    python -m pathoryx_enterprise.orchestrator.main

Environment:
    PATHORYX_SERVICES   — comma-separated list of services to start.
                          Defaults to all: babelshark,qc,dicom,upload,recovery_sentry
                          'failed_watcher' is a backward-compat alias for recovery_sentry.
    All DATABASE_URL, BABELSHARK_CONFIG, QC_SERVICE_CONFIG, DICOM_CONFIG env vars
    must be set before launching.
"""
from __future__ import annotations

import os
import sys

from pathoryx_enterprise.orchestrator.supervisor import ProcessSupervisor, ServiceSpec


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
