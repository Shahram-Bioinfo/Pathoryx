"""Unit tests for FileRecordRepository.get_or_create_safe()."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from pathoryx_enterprise.db.models.core import FileRecord
from pathoryx_enterprise.db.repositories.file_record import FileRecordRepository


def _repo():
    return FileRecordRepository(MagicMock())


def test_get_or_create_safe_creates_new_record():
    repo = _repo()
    with patch.object(repo, "get_by_canonical_path", return_value=None):
        record, created = repo.get_or_create_safe(
            "/data/slides/slide1.svs",
            defaults=dict(
                global_artifact_id="gaid-001",
                source_service="babelshark",
                source_artifact_id="/source/slide1.svs",
                status="intake_registered",
            ),
        )
    assert created is True
    assert isinstance(record, FileRecord)
    assert record.canonical_path == "/data/slides/slide1.svs"
    assert record.global_artifact_id == "gaid-001"
    assert record.source_service == "babelshark"
    assert record.status == "intake_registered"
    repo._session.add.assert_called_once_with(record)
    repo._session.flush.assert_called()


def test_get_or_create_safe_returns_existing_on_duplicate():
    repo = _repo()
    existing = FileRecord(
        canonical_path="/data/slides/slide2.svs",
        global_artifact_id="gaid-002",
    )
    with patch.object(repo, "get_by_canonical_path", return_value=existing):
        record, created = repo.get_or_create_safe(
            "/data/slides/slide2.svs",
            defaults=dict(global_artifact_id="should-not-be-used"),
        )
    assert created is False
    assert record is existing
    assert record.global_artifact_id == "gaid-002"
    repo._session.add.assert_not_called()


def test_get_or_create_safe_no_defaults():
    repo = _repo()
    with patch.object(repo, "get_by_canonical_path", return_value=None):
        record, created = repo.get_or_create_safe("/data/slides/slide3.svs")
    assert created is True
    assert record.canonical_path == "/data/slides/slide3.svs"
