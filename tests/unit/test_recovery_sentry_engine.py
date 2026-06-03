"""
Unit tests for RecoverySentry recovery engine.

Tests the decision logic without hitting the filesystem or DB.
Uses tmp_path fixtures for real filesystem operations.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import (
    build_final_filename,
    iso_z_to_filename_ts,
    parse_slide_id,
)


# --- SlideID parser unit tests (edge cases not covered in test_slide_id_parser) ---

class TestSlideIdParserEdgeCases:
    def test_parse_then_build_with_extracted_ts(self):
        parsed = parse_slide_id("N2024002863SA-1-1-H&E.svs")
        assert parsed is not None
        iso_z = "2024-08-22T08:36:39Z"
        final = build_final_filename(parsed, iso_z=iso_z)
        assert final == "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"

    def test_iso_z_roundtrip(self):
        original = "2024-08-22T08:36:39Z"
        tag = iso_z_to_filename_ts(original)
        assert tag == "UTC2024-08-22T08_36_39Z"
        # Parse back via slide_id_parser internal function
        from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import (
            _filename_ts_to_iso_z,
        )
        recovered = _filename_ts_to_iso_z(tag)
        assert recovered == original


class TestRecoveryEngineDecisions:
    """Integration-style tests using tmp_path for filesystem operations."""

    def _make_settings(self, tmp_path: Path, **overrides):
        """Build a minimal RecoverySentrySettings-like object for tests."""
        from types import SimpleNamespace
        defaults = dict(
            watch_folders=[tmp_path / "failed"],
            final_destination=tmp_path / "final",
            auto_recover_valid_slide_id=True,
            add_timestamp_if_missing=True,
            overwrite_existing=False,
            duplicate_strategy="suffix",
            checksum_mode="partial",
            allow_filesystem_timestamp_fallback=False,
            next_stage_target_service="qc_service",
            next_stage_name="qc",
            requires_approval_default=False,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_deleted_change_returns_deleted(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery
        settings = self._make_settings(tmp_path)

        result = process_recovery(
            new_path="",
            new_filename="",
            change_type="DELETED",
            technician_change_id=None,
            file_record_internal_id=None,
            global_artifact_id=None,
            correlation_id=None,
            runner_id="test",
            settings=settings,
        )
        assert result.outcome == "deleted"

    def test_invalid_slide_id_returns_manual_review(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        # Create a real file with an invalid name
        bad_file = tmp_path / "54564564.svs"
        bad_file.write_bytes(b"fake wsi")

        settings = self._make_settings(tmp_path)

        result = process_recovery(
            new_path=str(bad_file),
            new_filename=bad_file.name,
            change_type="ADDED",
            technician_change_id=None,
            file_record_internal_id=None,
            global_artifact_id=None,
            correlation_id=None,
            runner_id="test",
            settings=settings,
        )
        assert result.outcome == "manual_review_required"
        assert result.reason == "invalid_slide_id_pattern"

    def test_valid_slide_id_with_timestamp_auto_recovers(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        # Create source file with valid complete name (timestamp already present)
        src = tmp_path / "failed" / "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"fake svs data")

        settings = self._make_settings(tmp_path)

        # Patch DB operations — we're testing filesystem logic only
        with patch.object(recovery_engine, "_persist_recovery"):
            result = process_recovery(
                new_path=str(src),
                new_filename=src.name,
                change_type="ADDED",
                technician_change_id=1,
                file_record_internal_id=None,
                global_artifact_id=None,
                correlation_id=None,
                runner_id="test",
                settings=settings,
            )

        assert result.outcome == "auto_recovered"
        assert result.case_id == "N2024002863"
        assert result.timestamp_in_filename is True
        assert result.timestamp_extracted_from_wsi is False
        dest = tmp_path / "final" / "N2024002863" / "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        assert dest.exists()

    def test_valid_slide_id_no_timestamp_extracts_and_recovers(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        src = tmp_path / "failed" / "N2024002863SA-1-1-H&E.svs"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"fake svs data")

        settings = self._make_settings(tmp_path)

        with (
            patch.object(
                recovery_engine,
                "extract_scan_timestamp",
                return_value="2024-08-22T08:36:39Z",
            ),
            patch.object(recovery_engine, "_persist_recovery"),
        ):
            result = process_recovery(
                new_path=str(src),
                new_filename=src.name,
                change_type="ADDED",
                technician_change_id=1,
                file_record_internal_id=None,
                global_artifact_id=None,
                correlation_id=None,
                runner_id="test",
                settings=settings,
            )

        assert result.outcome == "auto_recovered"
        assert result.timestamp_in_filename is False
        assert result.timestamp_extracted_from_wsi is True
        assert result.final_filename == "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        dest = tmp_path / "final" / "N2024002863" / result.final_filename
        assert dest.exists()

    def test_missing_timestamp_and_no_metadata_returns_manual_review(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        src = tmp_path / "failed" / "N2024002863SA-1-1-H&E.svs"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"fake svs data")

        settings = self._make_settings(tmp_path)

        with patch.object(recovery_engine, "extract_scan_timestamp", return_value=None):
            result = process_recovery(
                new_path=str(src),
                new_filename=src.name,
                change_type="ADDED",
                technician_change_id=None,
                file_record_internal_id=None,
                global_artifact_id=None,
                correlation_id=None,
                runner_id="test",
                settings=settings,
            )

        assert result.outcome == "manual_review_required"
        assert result.reason == "missing_timestamp_metadata"
        # File must NOT be moved
        assert src.exists()

    def test_duplicate_destination_safe_suffix(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        final_name = "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        # Pre-create destination so there's a conflict
        dest_dir = tmp_path / "final" / "N2024002863"
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / final_name).write_bytes(b"existing file")

        src = tmp_path / "failed" / final_name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"new corrected file")

        settings = self._make_settings(tmp_path, duplicate_strategy="suffix")

        with patch.object(recovery_engine, "_persist_recovery"):
            result = process_recovery(
                new_path=str(src),
                new_filename=src.name,
                change_type="ADDED",
                technician_change_id=None,
                file_record_internal_id=None,
                global_artifact_id=None,
                correlation_id=None,
                runner_id="test",
                settings=settings,
            )

        assert result.outcome == "auto_recovered"
        # Should have gotten a safe suffix
        assert result.final_filename != final_name
        assert result.destination_path is not None
        assert result.destination_path.exists()
        # Original destination untouched
        assert (dest_dir / final_name).read_bytes() == b"existing file"

    def test_duplicate_destination_manual_review_strategy(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        final_name = "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        dest_dir = tmp_path / "final" / "N2024002863"
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / final_name).write_bytes(b"existing file")

        src = tmp_path / "failed" / final_name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"corrected file")

        settings = self._make_settings(tmp_path, duplicate_strategy="manual_review")

        result = process_recovery(
            new_path=str(src),
            new_filename=src.name,
            change_type="ADDED",
            technician_change_id=None,
            file_record_internal_id=None,
            global_artifact_id=None,
            correlation_id=None,
            runner_id="test",
            settings=settings,
        )

        assert result.outcome == "manual_review_required"
        assert result.reason == "duplicate_destination"
        # File must NOT be moved
        assert src.exists()


# ---------------------------------------------------------------------------
# DB handoff tests — verify _persist_recovery creates FileRecord + QC trigger
# ---------------------------------------------------------------------------

def _make_minimal_settings():
    return SimpleNamespace(
        next_stage_target_service="qc_service",
        next_stage_name="qc",
    )


def _make_mock_session():
    """Return a mock session whose execute().scalar_one_or_none() returns None by default."""
    sess = MagicMock()
    sess.execute.return_value.scalar_one_or_none.return_value = None
    sess.flush = MagicMock()
    sess.add = MagicMock()
    return sess


@contextmanager
def _patched_db(mock_file_record=None, existing_source_record=None, trigger_created=True):
    """
    Context manager that patches the three repository classes used by _persist_recovery.

    mock_file_record: the FileRecord the mock FR repo will return from get_or_create_safe.
    existing_source_record: if set, returned by get_by_canonical_path(source_path).
    trigger_created: whether TriggerRepository.enqueue reports the trigger as new.
    """
    if mock_file_record is None:
        mock_file_record = MagicMock()
        mock_file_record.internal_id = 42
        mock_file_record.global_artifact_id = None
        mock_file_record.source_service = None

    mock_trigger = MagicMock()
    mock_trigger.internal_id = 99

    mock_sess = _make_mock_session()

    # FR repo
    mock_fr_repo = MagicMock()
    if existing_source_record is not None:
        # First get_by_canonical_path(source_path) → existing record
        mock_fr_repo.get_by_canonical_path.return_value = existing_source_record
    else:
        mock_fr_repo.get_by_canonical_path.return_value = None
        mock_fr_repo.get_or_create_safe.return_value = (mock_file_record, True)

    # Trigger repo
    mock_tr_repo = MagicMock()
    mock_tr_repo.enqueue.return_value = (mock_trigger, trigger_created)

    # Event repo
    mock_es_repo = MagicMock()

    @contextmanager
    def _session_cm():
        yield mock_sess

    with (
        patch(
            "pathoryx_enterprise.services.recovery_sentry.recovery_engine.get_session",
            side_effect=_session_cm,
        ),
        patch(
            "pathoryx_enterprise.services.recovery_sentry.recovery_engine.FileRecordRepository",
            return_value=mock_fr_repo,
        ),
        patch(
            "pathoryx_enterprise.services.recovery_sentry.recovery_engine.TriggerRepository",
            return_value=mock_tr_repo,
        ),
        patch(
            "pathoryx_enterprise.services.recovery_sentry.recovery_engine.EventStoreRepository",
            return_value=mock_es_repo,
        ),
    ):
        yield mock_fr_repo, mock_tr_repo, mock_es_repo, mock_file_record, mock_trigger


class TestPersistRecoveryDbHandoff:
    """Verify _persist_recovery always creates/updates FileRecord and QC trigger."""

    def _call_persist(self, tmp_path, *, source_path=None, **kw):
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import _persist_recovery

        parsed = parse_slide_id("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        assert parsed is not None

        dest_path = tmp_path / "final" / "N2024002863" / "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"fake svs")

        defaults = dict(
            parsed=parsed,
            dest_path=dest_path,
            final_name=dest_path.name,
            slide_id_final="N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z",
            source_path=source_path or str(tmp_path / "failed" / "N2024002863SA-1-1-H&E.svs"),
            source_filename="N2024002863SA-1-1-H&E.svs",
            iso_z_ts="2024-08-22T08:36:39Z",
            timestamp_in_filename=True,
            timestamp_extracted=False,
            technician_change_id=None,
            hint_file_record_internal_id=None,
            hint_global_artifact_id=None,
            correlation_id=None,
            runner_id="test",
            settings=_make_minimal_settings(),
        )
        defaults.update(kw)
        _persist_recovery(**defaults)
        return dest_path

    def test_creates_new_file_record_when_none_exists(self, tmp_path):
        with _patched_db() as (mock_fr_repo, mock_tr_repo, mock_es_repo, mock_fr, mock_trig):
            self._call_persist(tmp_path)

        # get_or_create_safe called with the destination canonical path
        mock_fr_repo.get_or_create_safe.assert_called_once()
        canonical_path_arg = mock_fr_repo.get_or_create_safe.call_args.args[0]
        assert "final/N2024002863" in canonical_path_arg

        defaults = mock_fr_repo.get_or_create_safe.call_args.kwargs["defaults"]
        assert defaults["source_service"] == "recovery_sentry"
        assert defaults["status"] == "qc_pending"
        assert defaults["artifact_type"] == "wsi_slide"
        assert "global_artifact_id" in defaults
        # global_artifact_id must be a non-empty deterministic UUID string
        assert len(defaults["global_artifact_id"]) == 36

    def test_creates_qc_trigger_linked_to_file_record(self, tmp_path):
        with _patched_db() as (mock_fr_repo, mock_tr_repo, mock_es_repo, mock_fr, mock_trig):
            self._call_persist(tmp_path)

        mock_tr_repo.enqueue.assert_called_once()
        kwargs = mock_tr_repo.enqueue.call_args.kwargs
        assert kwargs["target_service"] == "qc_service"
        assert kwargs["stage_name"] == "qc"
        assert kwargs["file_record_internal_id"] == mock_fr.internal_id
        assert kwargs["source_service"] == "recovery_sentry"

    def test_updates_existing_file_record_found_by_source_path(self, tmp_path):
        existing = MagicMock()
        existing.internal_id = 77
        existing.global_artifact_id = "existing-uuid-1234"
        existing.source_service = "babelshark"

        with _patched_db(existing_source_record=existing) as (
            mock_fr_repo, mock_tr_repo, _, _, mock_trig
        ):
            self._call_persist(tmp_path)

        # Should NOT call get_or_create_safe — already found by source_path lookup
        mock_fr_repo.get_or_create_safe.assert_not_called()

        # QC trigger must use the EXISTING record's internal_id
        kwargs = mock_tr_repo.enqueue.call_args.kwargs
        assert kwargs["file_record_internal_id"] == 77

        # FileRecord paths/status updated
        assert existing.current_file_path is not None
        assert existing.status == "qc_pending"

    def test_idempotent_trigger_not_duplicated(self, tmp_path):
        """If a pending trigger already exists, enqueue returns created=False. Must not error."""
        with _patched_db(trigger_created=False) as (
            mock_fr_repo, mock_tr_repo, mock_es_repo, _, _
        ):
            # Should complete without raising
            self._call_persist(tmp_path)

        # enqueue still called exactly once
        mock_tr_repo.enqueue.assert_called_once()

    def test_auto_recovered_event_emitted(self, tmp_path):
        with _patched_db() as (_, _, mock_es_repo, mock_fr, _):
            self._call_persist(tmp_path)

        emitted_types = [c.kwargs["event_type"] for c in mock_es_repo.append.call_args_list]
        assert "recovery_sentry.auto_recovered" in emitted_types
        assert "recovery_sentry.qc_requeued" in emitted_types

    def test_timestamp_events_emitted_when_extracted(self, tmp_path):
        with _patched_db() as (_, _, mock_es_repo, mock_fr, _):
            self._call_persist(tmp_path, timestamp_extracted=True, timestamp_in_filename=False)

        emitted_types = [c.kwargs["event_type"] for c in mock_es_repo.append.call_args_list]
        assert "recovery_sentry.timestamp_extracted" in emitted_types
        assert "recovery_sentry.timestamp_added" in emitted_types
        assert "recovery_sentry.auto_recovered" in emitted_types

    def test_auto_recovered_event_carries_file_record_id(self, tmp_path):
        with _patched_db() as (_, _, mock_es_repo, mock_fr, _):
            self._call_persist(tmp_path)

        # Find the auto_recovered event call
        recovered_calls = [
            c for c in mock_es_repo.append.call_args_list
            if c.kwargs.get("event_type") == "recovery_sentry.auto_recovered"
        ]
        assert len(recovered_calls) == 1
        call_kwargs = recovered_calls[0].kwargs
        assert call_kwargs["file_record_internal_id"] == mock_fr.internal_id

    def test_global_artifact_id_is_durable_uuid(self, tmp_path):
        """Same dest_path always produces the same global_artifact_id."""
        from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

        dest_path = tmp_path / "final" / "N2024002863" / "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        expected_id = deterministic_artifact_id("recovery_sentry:artifact", str(dest_path))

        with _patched_db() as (mock_fr_repo, _, _, _, _):
            self._call_persist(tmp_path)

        defaults = mock_fr_repo.get_or_create_safe.call_args.kwargs["defaults"]
        assert defaults["global_artifact_id"] == expected_id
