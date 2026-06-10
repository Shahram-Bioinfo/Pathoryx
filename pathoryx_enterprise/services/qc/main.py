"""QC service entrypoint."""
from __future__ import annotations

import sys


def main() -> None:
    # Register OpenSlide DLL directory BEFORE importing any QC module.
    # thumbnail_service.py is imported transitively when _load_qc_deps() runs
    # inside runner.py; DLLs must be registered before that first import.
    # OPENSLIDE_DLL_PATH env var is the primary source; no-op on Linux.
    from pathoryx_enterprise.runtime.openslide_setup import configure_openslide_runtime
    configure_openslide_runtime()

    try:
        from pathoryx_enterprise.services.qc.config import QCSettings
        from pathoryx_enterprise.services.qc.runner import run

        settings = QCSettings()
    except Exception as exc:
        print(f"[FATAL] QC service configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    run(settings)


if __name__ == "__main__":
    main()
