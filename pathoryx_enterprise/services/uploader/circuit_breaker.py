"""
Simple circuit breaker for the upload service.

States:
  CLOSED   — normal operation, requests flow through
  OPEN     — too many failures, reject immediately without attempting
  HALF_OPEN — after reset_seconds, allow one probe request through

Thread-safe via a threading.Lock.
"""
from __future__ import annotations

import threading
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    A simple circuit breaker for protecting the upload path.

    Usage::

        cb = CircuitBreaker(threshold=5, reset_seconds=60)

        if cb.is_open:
            logger.warning("circuit open, skipping upload")
        else:
            try:
                result = do_upload(...)
                cb.record_success()
            except Exception:
                cb.record_failure()
                raise
    """

    def __init__(self, threshold: int = 5, reset_seconds: int = 60) -> None:
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._opened_at and (time.time() - self._opened_at) >= self._reset_seconds:
                    self._state = CircuitState.HALF_OPEN
            return self._state

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        return self.state == CircuitState.HALF_OPEN

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self._threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
