"""
Pathoryx Dashboard service entrypoint.

Usage:
    pathoryx-dashboard            # reads config from environment / .env
    python -m pathoryx_enterprise.services.dashboard.main

Environment variables (all optional, with defaults):
    DASHBOARD_HOST=127.0.0.1
    DASHBOARD_PORT=8090
    DASHBOARD_LOG_LEVEL=info
    DATABASE_URL=postgresql://...  # required (same as all other services)
"""
from __future__ import annotations

import sys


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            "[FATAL] uvicorn is not installed. "
            "Install the dashboard extras: pip install 'pathoryx-enterprise[dashboard]'",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from pathoryx_enterprise.services.dashboard.app import create_app
        from pathoryx_enterprise.services.dashboard.config import DashboardSettings

        settings = DashboardSettings()
    except Exception as exc:
        print(f"[FATAL] Dashboard configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    app = create_app()

    print(
        f"[pathoryx-dashboard] Starting on http://{settings.host}:{settings.port} "
        f"— docs at http://{settings.host}:{settings.port}/dashboard/docs",
        file=sys.stderr,
    )

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=settings.reload,
    )


if __name__ == "__main__":
    main()
