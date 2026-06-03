"""
Graceful shutdown coordinator.

Registers a SIGTERM handler that:
  1. Sets a threading.Event so all poll loops can detect shutdown.
  2. Calls any registered cleanup callbacks in reverse registration order.
  3. Waits for the optional join_timeout before the process exits.

Usage::

    coordinator = ShutdownCoordinator()
    coordinator.install()           # registers SIGTERM handler

    coordinator.register(lambda: db_pool.dispose())
    coordinator.register(lambda: health_server.stop())

    while not coordinator.is_stopping:
        process_next_item()
"""
from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class ShutdownCoordinator:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install(self) -> None:
        """Install SIGTERM and SIGINT handlers."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def register(self, callback: Callable[[], None]) -> None:
        """Register a cleanup callback. Called in reverse order on shutdown."""
        self._callbacks.append(callback)

    @property
    def is_stopping(self) -> bool:
        return self._event.is_set()

    def wait(self) -> None:
        """Block until a shutdown signal is received."""
        self._event.wait()

    def trigger(self) -> None:
        """Manually trigger shutdown (e.g., from a health check failure)."""
        self._event.set()
        self._run_callbacks()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle_signal(self, signum: int, _frame: object) -> None:
        logger.info("received signal %d — initiating graceful shutdown", signum)
        self._event.set()
        self._run_callbacks()

    def _run_callbacks(self) -> None:
        for cb in reversed(self._callbacks):
            try:
                cb()
            except Exception:
                logger.exception("error in shutdown callback %s", cb)
