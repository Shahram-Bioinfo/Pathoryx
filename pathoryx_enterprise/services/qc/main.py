"""QC service entrypoint."""
from __future__ import annotations

import sys


def main() -> None:
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
