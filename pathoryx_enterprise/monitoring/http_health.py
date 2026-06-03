"""
Lightweight HTTP health server using only stdlib.

Exposes three endpoints:
  GET /live   — liveness probe  (always 200 if process is up)
  GET /ready  — readiness probe (200/503 based on DB + env)
  GET /health — full health report (200/503 with JSON body)

Runs in a daemon thread so it never blocks the main service loop.
Stops cleanly when the process exits (daemon=True).
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

from pathoryx_enterprise.monitoring.health import HealthStatus, liveness_probe

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    """Request handler injected with probe callables at server construction."""

    # Injected by HealthHTTPServer
    _live_probe: Callable[[], HealthStatus]
    _ready_probe: Callable[[], HealthStatus]
    _health_probe: Callable[[], HealthStatus]

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.rstrip("/").split("?")[0]
        if path == "/live":
            self._respond(self._live_probe())
        elif path == "/ready":
            self._respond(self._ready_probe())
        elif path == "/health":
            self._respond(self._health_probe())
        else:
            self.send_response(404)
            self.end_headers()

    def _respond(self, status: HealthStatus) -> None:
        code = 200 if status.healthy else 503
        body = json.dumps(status.to_dict(), indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        # Suppress per-request access logs — they spam the service logs.
        pass


class HealthHTTPServer:
    """
    Daemon-thread HTTP server for Kubernetes health probes.

    Usage::

        server = HealthHTTPServer(
            port=8081,
            ready_probe=build_readiness_probe(session_factory=get_session),
            health_probe=build_health_probe(...),
        )
        server.start()   # non-blocking
        # ... run service ...
        server.stop()    # optional — daemon thread exits on process death
    """

    def __init__(
        self,
        port: int,
        ready_probe: Callable[[], HealthStatus],
        health_probe: Callable[[], HealthStatus],
    ) -> None:
        self._port = port

        # Bind callables into the handler class via class-level attributes.
        # HTTPServer creates a new handler instance per request, so we use
        # class attributes rather than instance attributes.
        handler = type(
            "_BoundHealthHandler",
            (_HealthHandler,),
            {
                "_live_probe": staticmethod(liveness_probe),
                "_ready_probe": staticmethod(ready_probe),
                "_health_probe": staticmethod(health_probe),
            },
        )

        self._server = HTTPServer(("0.0.0.0", port), handler)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the health server in a background daemon thread."""
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="health-http",
            daemon=True,
        )
        self._thread.start()
        logger.info("health HTTP server started on port %d", self._port)

    def stop(self) -> None:
        """Gracefully shut down the HTTP server."""
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("health HTTP server stopped")
