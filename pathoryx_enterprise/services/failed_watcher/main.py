"""Failed/Suspicious Slide Watcher service entrypoint."""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from pathoryx_enterprise.services.failed_watcher.config import FailedWatcherSettings
        from pathoryx_enterprise.services.failed_watcher.runner import run

        settings = FailedWatcherSettings()
    except Exception as exc:
        print(f"[FATAL] Failed Watcher configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    run(settings)


if __name__ == "__main__":
    main()
