"""
Unit tests for QC → DICOM trigger payload construction.

Tests that QCDBWriter.record_qc_result() always puts a valid source_path
in the downstream DICOM trigger payload — regardless of whether source_path
was in the original QC trigger payload (BabelShark path) or must be resolved
from the FileRecord (RecoverySentry recovery path).

Also tests that when source_path cannot be resolved, a
qc.downstream_dispatch_failed event is emitted and no broken trigger is created.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pytest

from pathoryx_enterprise.db.models.core import FileRecord, ServiceTrigger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trigger(
    *,
    trigger_id: int = 1,
    file_record_id: int = 5,
    global_artifact_id: str = "b928f52d-4e41-5890-a0de-058b95b6d773",
    payload: dict | None = None,
) -> ServiceTrigger:
    t = MagicMock(spec=ServiceTrigger)
    t.internal_id = trigger_id
    t.file_record_internal_id = file_record_id
    t.global_artifact_id = global_artifact_id
    t.correlation_id = "corr-001"
    t.trigger_payload_json = payload or {}
    t.started_at = None
    return t


def _make_file_record(
    *,
    internal_id: int = 5,
    current_file_path: str | None = None,
    canonical_path: str | None = None,
    file_size: int = 512_000_000,
) -> FileRecord:
    fr = MagicMock(spec=FileRecord)
    fr.internal_id = internal_id
    fr.current_file_path = current_file_path
    fr.canonical_path = canonical_path
    fr.file_size = file_size
    fr.status = "qc_pending"
    return fr


@contextmanager
def _make_writer_with_mocks(file_record: FileRecord | None = None):
    """
    Build a QCDBWriter backed by a mock session + mock repositories.

    Yields (writer, mock_trigger_repo, mock_event_repo).
    """
    from pathoryx_enterprise.services.qc.db_writer import QCDBWriter

    mock_session = MagicMock()
    # QCResult idempotency select → None so a new row is always attempted
    mock_session.execute.return_value.scalar_one_or_none.return_value = None

    mock_trigger_repo = MagicMock()
    mock_event_repo = MagicMock()

    with (
        patch(
            "pathoryx_enterprise.services.qc.db_writer.TriggerRepository",
            return_value=mock_trigger_repo,
        ),
        patch(
            "pathoryx_enterprise.services.qc.db_writer.EventStoreRepository",
            return_value=mock_event_repo,
        ),
        patch(
            "pathoryx_enterprise.services.qc.db_writer.FileRecordRepository",
        ),
    ):
        writer = QCDBWriter(mock_session)
        # Override _fetch_file_record directly — simpler than mocking FR repo
        writer._fetch_file_record = MagicMock(return_value=file_record)

        yield writer, mock_trigger_repo, mock_event_repo


# ---------------------------------------------------------------------------
# Tests — source_path resolution
# ---------------------------------------------------------------------------

class TestQcDicomTriggerSourcePath:

    def test_source_path_from_trigger_payload(self):
        """Normal BabelShark path: source_path in trigger payload → passed through unchanged."""
        wsi_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-10-17T08_37_06Z.svs"
        trigger = _make_trigger(payload={"source_path": wsi_path})
        fr = _make_file_record(current_file_path="/other/path.svs")

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        mock_tr_repo.enqueue.assert_called_once()
        payload = mock_tr_repo.enqueue.call_args.kwargs["payload"]
        assert payload["source_path"] == wsi_path

    def test_source_path_resolved_from_current_file_path_when_payload_empty(self):
        """RecoverySentry path (old trigger): source_path absent → resolved from FileRecord.current_file_path."""
        wsi_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-2-H&E_UTC2024-10-17T08_37_06Z.svs"
        trigger = _make_trigger(payload={})  # no source_path
        fr = _make_file_record(current_file_path=wsi_path)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        mock_tr_repo.enqueue.assert_called_once()
        payload = mock_tr_repo.enqueue.call_args.kwargs["payload"]
        assert payload["source_path"] == wsi_path

    def test_source_path_falls_back_to_canonical_path(self):
        """If current_file_path is None, falls back to canonical_path."""
        wsi_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-10-17T08_37_06Z.svs"
        trigger = _make_trigger(payload={})
        fr = _make_file_record(current_file_path=None, canonical_path=wsi_path)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        mock_tr_repo.enqueue.assert_called_once()
        payload = mock_tr_repo.enqueue.call_args.kwargs["payload"]
        assert payload["source_path"] == wsi_path

    def test_payload_source_path_takes_priority_over_file_record(self):
        """If both trigger payload and FileRecord have a path, trigger payload wins."""
        payload_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-10-17T08_37_06Z.svs"
        fr_path = "/data/pathoryx/failed/N2024002863SA-1-1-H&E.svs"
        trigger = _make_trigger(payload={"source_path": payload_path})
        fr = _make_file_record(current_file_path=fr_path)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        payload = mock_tr_repo.enqueue.call_args.kwargs["payload"]
        assert payload["source_path"] == payload_path


# ---------------------------------------------------------------------------
# Tests — dispatch-failed event when source_path cannot be resolved
# ---------------------------------------------------------------------------

class TestQcDispatchFailed:

    def test_no_trigger_created_when_source_path_unresolvable(self):
        """If source_path is empty AND FileRecord has no path, trigger must NOT be created."""
        trigger = _make_trigger(payload={})
        fr = _make_file_record(current_file_path=None, canonical_path=None)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, mock_event_repo):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        mock_tr_repo.enqueue.assert_not_called()

    def test_dispatch_failed_event_emitted_when_source_path_unresolvable(self):
        """Emits qc.downstream_dispatch_failed instead of a broken trigger."""
        trigger = _make_trigger(payload={})
        fr = _make_file_record(current_file_path=None, canonical_path=None)

        with _make_writer_with_mocks(fr) as (writer, _, mock_event_repo):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        emitted = [c.kwargs["event_type"] for c in mock_event_repo.append.call_args_list]
        assert "qc.downstream_dispatch_failed" in emitted
        # Find the specific call
        failed_calls = [
            c for c in mock_event_repo.append.call_args_list
            if c.kwargs.get("event_type") == "qc.downstream_dispatch_failed"
        ]
        assert len(failed_calls) == 1
        ep = failed_calls[0].kwargs["event_payload"]
        assert ep["reason"] == "source_path_unresolvable"
        assert ep["target_service"] == "dicom_service"

    def test_qc_failed_slide_does_not_dispatch_downstream(self):
        """A QC-rejected slide must never create a DICOM trigger."""
        wsi_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-10-17T08_37_06Z.svs"
        trigger = _make_trigger(payload={"source_path": wsi_path})
        fr = _make_file_record(current_file_path=wsi_path)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="failed",   # QC rejected
                decision_reason="blur_detected",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        mock_tr_repo.enqueue.assert_not_called()

    def test_no_file_record_no_dispatch(self):
        """If there is no FileRecord at all, no DICOM trigger is created (existing behaviour)."""
        trigger = _make_trigger(payload={"source_path": "/some/path.svs"})

        with _make_writer_with_mocks(file_record=None) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        mock_tr_repo.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — downstream payload completeness
# ---------------------------------------------------------------------------

class TestQcDownstreamPayloadFields:

    def test_downstream_payload_includes_file_record_id(self):
        wsi_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-10-17T08_37_06Z.svs"
        trigger = _make_trigger(payload={"source_path": wsi_path}, file_record_id=42)
        fr = _make_file_record(internal_id=42, current_file_path=wsi_path)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        payload = mock_tr_repo.enqueue.call_args.kwargs["payload"]
        assert payload["file_record_internal_id"] == 42

    def test_downstream_payload_includes_global_artifact_id(self):
        wsi_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-10-17T08_37_06Z.svs"
        gaid = "b928f52d-4e41-5890-a0de-058b95b6d773"
        trigger = _make_trigger(
            payload={"source_path": wsi_path},
            global_artifact_id=gaid,
        )
        fr = _make_file_record(current_file_path=wsi_path)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                global_artifact_id=gaid,
                runner_id="test",
            )

        payload = mock_tr_repo.enqueue.call_args.kwargs["payload"]
        assert payload["global_artifact_id"] == gaid

    def test_downstream_payload_includes_correlation_id(self):
        wsi_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-10-17T08_37_06Z.svs"
        trigger = _make_trigger(payload={"source_path": wsi_path})
        trigger.correlation_id = "my-corr-id"
        fr = _make_file_record(current_file_path=wsi_path)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                correlation_id="my-corr-id",
                runner_id="test",
            )

        payload = mock_tr_repo.enqueue.call_args.kwargs["payload"]
        assert payload["correlation_id"] == "my-corr-id"

    def test_enqueue_called_with_file_record_internal_id(self):
        """Verify enqueue() receives file_record_internal_id as a kwarg (not just in payload)."""
        wsi_path = "/data/pathoryx/final/N2024002863/N2024002863SA-1-1-H&E_UTC2024-10-17T08_37_06Z.svs"
        trigger = _make_trigger(payload={"source_path": wsi_path}, file_record_id=55)
        fr = _make_file_record(internal_id=55, current_file_path=wsi_path)

        with _make_writer_with_mocks(fr) as (writer, mock_tr_repo, _):
            writer.record_qc_result(
                trigger=trigger,
                decision_status="passed",
                decision_reason="all_checks_passed",
                next_service="dicom_service",
                next_stage="dicom",
                runner_id="test",
            )

        enqueue_kwargs = mock_tr_repo.enqueue.call_args.kwargs
        assert enqueue_kwargs["file_record_internal_id"] == 55
        assert enqueue_kwargs["target_service"] == "dicom_service"
        assert enqueue_kwargs["stage_name"] == "dicom"


# ---------------------------------------------------------------------------
# Tests — RecoverySentry QC trigger payload
# ---------------------------------------------------------------------------

class TestRecoverySentryQcTriggerPayload:
    """Verify that _persist_recovery puts source_path in the QC trigger payload."""

    def test_qc_trigger_payload_includes_source_path(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import _persist_recovery
        from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import parse_slide_id
        from tests.unit.test_recovery_sentry_engine import (
            _make_minimal_settings,
            _patched_db,
        )

        parsed = parse_slide_id("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        assert parsed is not None

        dest_path = (
            tmp_path
            / "final"
            / "N2024002863"
            / "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"fake svs")

        with _patched_db() as (_, mock_tr_repo, _, _, _):
            _persist_recovery(
                parsed=parsed,
                dest_path=dest_path,
                final_name=dest_path.name,
                slide_id_final="N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z",
                source_path=str(tmp_path / "failed" / "N2024002863SA-1-1-H&E.svs"),
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

        # QC trigger must have source_path = dest_path
        mock_tr_repo.enqueue.assert_called_once()
        enqueue_kwargs = mock_tr_repo.enqueue.call_args.kwargs
        assert "payload" in enqueue_kwargs
        qc_payload = enqueue_kwargs["payload"]
        assert qc_payload["source_path"] == str(dest_path)

    def test_qc_trigger_payload_includes_global_artifact_id(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import _persist_recovery
        from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import parse_slide_id
        from tests.unit.test_recovery_sentry_engine import (
            _make_minimal_settings,
            _patched_db,
        )

        parsed = parse_slide_id("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        dest_path = (
            tmp_path
            / "final"
            / "N2024002863"
            / "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"fake svs")

        with _patched_db() as (_, mock_tr_repo, _, mock_fr, _):
            mock_fr.global_artifact_id = "test-artifact-uuid"
            _persist_recovery(
                parsed=parsed,
                dest_path=dest_path,
                final_name=dest_path.name,
                slide_id_final="N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z",
                source_path=str(tmp_path / "failed" / "N2024002863SA-1-1-H&E.svs"),
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

        qc_payload = mock_tr_repo.enqueue.call_args.kwargs["payload"]
        # global_artifact_id must be present and non-empty
        assert qc_payload.get("global_artifact_id")
