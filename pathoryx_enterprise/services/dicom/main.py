"""DICOM conversion + upload service entrypoint."""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from pathoryx_enterprise.services.dicom.config import DICOMSettings
        from pathoryx_enterprise.services.dicom.runner import run

        settings = DICOMSettings()
    except Exception as exc:
        print(f"[FATAL] DICOM service configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    run(settings)


if __name__ == "__main__":
    main()
