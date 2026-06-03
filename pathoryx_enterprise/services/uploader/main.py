"""Uploader service entrypoint."""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from pathoryx_enterprise.services.uploader.config import UploaderSettings
        from pathoryx_enterprise.services.uploader.runner import run

        settings = UploaderSettings()
    except Exception as exc:
        print(f"[FATAL] Uploader service configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    run(settings)


if __name__ == "__main__":
    main()
