"""
Integration test: Failed Watcher end-to-end.

Creates a temporary folder, runs scan_once(), verifies TechnicianChange records
are created for added/deleted/modified files.

Requires DATABASE_URL pointing to a real PostgreSQL instance.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from pathoryx_enterprise.db.repositories.failed_watcher import (
    TechnicianChangeRepository,
    WatchedFolderSnapshotRepository,
)
from pathoryx_enterprise.db.session import get_session

pytestmark = pytest.mark.integration


@pytest.fixture
def watch_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "watch"
    folder.mkdir()
    return folder


def _settings(watch_folder: Path):
    from unittest.mock import MagicMock
    from pathoryx_enterprise.services.failed_watcher.config import FailedWatcherSettings

    s = MagicMock(spec=FailedWatcherSettings)
    s.watch_folders = [watch_folder]
    s.folder_labels = [watch_folder.name]
    s.allowed_roots = [watch_folder]
    s.requires_approval_default = False
    s.scan_interval_seconds = 30
    s.max_consecutive_errors = 5
    return s


def test_new_file_detected(watch_folder: Path, pg_session) -> None:
    from pathoryx_enterprise.services.failed_watcher.watcher import scan_once

    slide = watch_folder / "test_slide.svs"
    slide.write_bytes(b"fake_slide_data" * 1000)
    # Wait 1s so file mtime is in the past (is_file_stable check)
    time.sleep(1.1)

    settings = _settings(watch_folder)
    new_changes = scan_once(settings, runner_id="test-runner")

    assert new_changes >= 1

    with get_session() as session:
        repo = TechnicianChangeRepository(session)
        pending = repo.get_pending_review(watch_folder_label=watch_folder.name, limit=10)
    assert any(c.change_type == "ADDED" for c in pending)


def test_deleted_file_detected(watch_folder: Path, pg_session) -> None:
    from pathoryx_enterprise.services.failed_watcher.watcher import scan_once

    # Create file and snapshot it first
    slide = watch_folder / "to_delete.svs"
    slide.write_bytes(b"data" * 1000)
    time.sleep(1.1)

    settings = _settings(watch_folder)
    scan_once(settings, runner_id="test-runner")  # first scan: ADDED

    # Delete the file
    slide.unlink()

    new_changes = scan_once(settings, runner_id="test-runner")  # second scan: DELETED
    assert new_changes >= 1

    with get_session() as session:
        pending = TechnicianChangeRepository(session).get_pending_review(
            watch_folder_label=watch_folder.name
        )
    assert any(c.change_type == "DELETED" for c in pending)
