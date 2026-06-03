"""RecoverySentry service entrypoint — CLI command: pathoryx-recovery-sentry"""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
        from pathoryx_enterprise.services.recovery_sentry.runner import run

        settings = RecoverySentrySettings()
    except Exception as exc:
        print(f"[FATAL] RecoverySentry configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    run(settings)


if __name__ == "__main__":
    main()
