"""Unit tests for streaming SHA-256 checksum utility."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pathoryx_enterprise.utils.checksum import compute_sha256 as compute_sha256_streaming


def test_checksum_matches_stdlib(tmp_path: Path) -> None:
    """Streaming SHA-256 must match hashlib reference for same file."""
    data = b"Hello Palantir " * 10_000
    test_file = tmp_path / "test.bin"
    test_file.write_bytes(data)

    expected = hashlib.sha256(data).hexdigest()
    result = compute_sha256_streaming(test_file)

    assert result == expected


def test_checksum_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    result = compute_sha256_streaming(empty)
    assert result == hashlib.sha256(b"").hexdigest()


def test_checksum_large_file(tmp_path: Path) -> None:
    """Must handle files larger than the 4 MB chunk size without loading all into memory."""
    # 12 MB file — 3 full chunks
    data = b"X" * (12 * 1024 * 1024)
    f = tmp_path / "large.bin"
    f.write_bytes(data)
    result = compute_sha256_streaming(f)
    assert result == hashlib.sha256(data).hexdigest()


def test_checksum_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        compute_sha256_streaming(tmp_path / "nonexistent.bin")
