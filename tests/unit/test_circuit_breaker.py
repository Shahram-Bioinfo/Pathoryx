"""Unit tests for the circuit breaker."""
from __future__ import annotations

import time

from pathoryx_enterprise.services.uploader.circuit_breaker import CircuitBreaker, CircuitState


def test_starts_closed() -> None:
    cb = CircuitBreaker(threshold=3)
    assert cb.state == CircuitState.CLOSED
    assert not cb.is_open


def test_opens_after_threshold_failures() -> None:
    cb = CircuitBreaker(threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open
    cb.record_failure()
    assert cb.is_open


def test_success_resets_count() -> None:
    cb = CircuitBreaker(threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert not cb.is_open  # counter was reset


def test_transitions_to_half_open() -> None:
    cb = CircuitBreaker(threshold=1, reset_seconds=1)
    cb.record_failure()
    assert cb.is_open
    time.sleep(1.1)
    assert cb.is_half_open


def test_closed_after_success_in_half_open() -> None:
    cb = CircuitBreaker(threshold=1, reset_seconds=1)
    cb.record_failure()
    time.sleep(1.1)
    assert cb.is_half_open
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
