"""
BabelShark service entrypoint.

Usage:
    pathoryx-babelshark          # reads all config from environment
    python -m pathoryx_enterprise.services.babelshark.main
"""
from __future__ import annotations

import sys


def main() -> None:
    # Register OpenSlide DLL directory BEFORE importing any service module.
    # OPENSLIDE_DLL_PATH env var is the primary source; per-stage config-driven
    # fallback (dll_paths.openslide_dll) is also supported via stage_runner.
    # No-op on Linux.
    from pathoryx_enterprise.runtime.openslide_setup import configure_openslide_runtime
    configure_openslide_runtime()

    try:
        from pathoryx_enterprise.services.babelshark.config import BabelSharkSettings
        from pathoryx_enterprise.services.babelshark.runner import run

        settings = BabelSharkSettings()
    except Exception as exc:
        print(f"[FATAL] BabelShark configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    run(settings)


if __name__ == "__main__":
    main()
