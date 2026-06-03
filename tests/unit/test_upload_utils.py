"""Unit tests for DICOM upload utilities — ARG_MAX fix verification."""
from __future__ import annotations

from pathlib import Path

import pytest

from pathoryx_enterprise.services.dicom.upload_utils import build_cstore_commands


def test_single_file_returns_one_command(tmp_path: Path) -> None:
    f = tmp_path / "slide.dcm"
    f.write_bytes(b"dcm")
    commands = build_cstore_commands(
        f, host="pacs", port=104, local_ae="LOCAL", remote_ae="REMOTE"
    )
    assert len(commands) == 1
    assert str(f) in commands[0]


def test_empty_dir_returns_no_commands(tmp_path: Path) -> None:
    commands = build_cstore_commands(
        tmp_path, host="pacs", port=104, local_ae="LOCAL", remote_ae="REMOTE"
    )
    assert commands == []


def test_batching_enforced(tmp_path: Path) -> None:
    """600 DCM files with batch_size=500 must produce 2 commands."""
    for i in range(600):
        (tmp_path / f"file_{i:04d}.dcm").write_bytes(b"dcm")

    commands = build_cstore_commands(
        tmp_path,
        host="pacs",
        port=104,
        local_ae="LOCAL",
        remote_ae="REMOTE",
        batch_size=500,
    )
    assert len(commands) == 2
    # First batch: exactly 500 files
    # base command = [bin, -aec, ..., -aet, ..., host, port] = 7 args
    assert len(commands[0]) - 7 == 500
    assert len(commands[1]) - 7 == 100


def test_batch_size_one(tmp_path: Path) -> None:
    """batch_size=1 means one command per file."""
    for i in range(3):
        (tmp_path / f"file_{i}.dcm").write_bytes(b"dcm")
    commands = build_cstore_commands(
        tmp_path, host="pacs", port=104, local_ae="L", remote_ae="R", batch_size=1
    )
    assert len(commands) == 3
