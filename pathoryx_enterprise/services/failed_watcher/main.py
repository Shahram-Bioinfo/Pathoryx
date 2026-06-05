"""
DEPRECATED entrypoint — `pathoryx-failed-watcher` has been superseded by
`pathoryx-recovery-sentry`.

This stub exists only so that any scripts or process managers that still
reference the old command name get a clear error message rather than a
cryptic 'command not found'.
"""
from __future__ import annotations

import sys


def main() -> None:
    print(
        "\n[DEPRECATED] pathoryx-failed-watcher has been removed.\n"
        "Use 'pathoryx-recovery-sentry' instead.\n"
        "Update your process manager, systemd unit, or startup scripts.\n",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
