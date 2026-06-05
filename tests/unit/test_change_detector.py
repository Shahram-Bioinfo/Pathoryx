"""Unit tests for the RecoverySentry change detector."""
from __future__ import annotations

from unittest.mock import MagicMock

from pathoryx_enterprise.services.recovery_sentry.change_detector import detect_changes


def _make_snap(file_path: str, filename: str, file_size: int, mtime_ns: int):
    snap = MagicMock()
    snap.file_path = file_path
    snap.filename = filename
    snap.file_size = file_size
    snap.mtime_ns = mtime_ns
    return snap


def test_detects_added_file() -> None:
    current = {"/watch/new.svs": {"filename": "new.svs", "file_size": 100, "mtime_ns": 1000}}
    changes = detect_changes("label", current, [])
    assert len(changes) == 1
    assert changes[0].change_type == "ADDED"
    assert changes[0].new_path == "/watch/new.svs"


def test_detects_deleted_file() -> None:
    snap = _make_snap("/watch/old.svs", "old.svs", 200, 2000)
    changes = detect_changes("label", {}, [snap])
    assert len(changes) == 1
    assert changes[0].change_type == "DELETED"
    assert changes[0].old_path == "/watch/old.svs"


def test_detects_modified_by_size() -> None:
    snap = _make_snap("/watch/s.svs", "s.svs", 100, 1000)
    current = {"/watch/s.svs": {"filename": "s.svs", "file_size": 200, "mtime_ns": 1000}}
    changes = detect_changes("label", current, [snap])
    assert len(changes) == 1
    assert changes[0].change_type == "MODIFIED"


def test_detects_modified_by_mtime() -> None:
    snap = _make_snap("/watch/s.svs", "s.svs", 100, 1000)
    current = {"/watch/s.svs": {"filename": "s.svs", "file_size": 100, "mtime_ns": 9999}}
    changes = detect_changes("label", current, [snap])
    assert len(changes) == 1
    assert changes[0].change_type == "MODIFIED"


def test_no_change_on_identical() -> None:
    snap = _make_snap("/watch/s.svs", "s.svs", 100, 1000)
    current = {"/watch/s.svs": {"filename": "s.svs", "file_size": 100, "mtime_ns": 1000}}
    changes = detect_changes("label", current, [snap])
    assert changes == []


def test_multiple_changes() -> None:
    snaps = [
        _make_snap("/watch/a.svs", "a.svs", 100, 1000),
        _make_snap("/watch/b.svs", "b.svs", 200, 2000),
    ]
    current = {
        "/watch/a.svs": {"filename": "a.svs", "file_size": 999, "mtime_ns": 1000},  # modified
        "/watch/c.svs": {"filename": "c.svs", "file_size": 300, "mtime_ns": 3000},  # added
        # b.svs missing → deleted
    }
    changes = detect_changes("label", current, snaps)
    types = {c.change_type for c in changes}
    assert "ADDED" in types
    assert "DELETED" in types
    assert "MODIFIED" in types
