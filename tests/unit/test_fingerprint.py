"""Unit tests for deterministic fingerprint utility."""
from __future__ import annotations

import pytest

from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id


def test_deterministic_same_inputs() -> None:
    a = deterministic_artifact_id("babelshark", "raw_slide", "/data/slide1.svs", 1024, 99999)
    b = deterministic_artifact_id("babelshark", "raw_slide", "/data/slide1.svs", 1024, 99999)
    assert a == b


def test_deterministic_different_inputs() -> None:
    a = deterministic_artifact_id("babelshark", "raw_slide", "/data/slide1.svs", 1024, 99999)
    b = deterministic_artifact_id("babelshark", "raw_slide", "/data/slide2.svs", 1024, 99999)
    assert a != b


def test_returns_string() -> None:
    result = deterministic_artifact_id("service", "type", "path")
    assert isinstance(result, str)
    assert len(result) > 0


def test_empty_parts() -> None:
    """Empty string parts still produce a stable UUID string."""
    result = deterministic_artifact_id("", "", "")
    assert isinstance(result, str)


def test_none_parts_are_safe() -> None:
    """None parts should not raise — they get coerced to string."""
    result = deterministic_artifact_id("service", None, "path")
    assert isinstance(result, str)
