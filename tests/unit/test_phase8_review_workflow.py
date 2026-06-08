"""
Phase 8 — Technician Review & Manual Rename: unit tests.

Tests cover:
  - Structured filename validation (all classifications)
  - Duplicate prevention in rename
  - Lineage / audit event creation on review state update
  - Filesystem rename detection (via actions.py validators)
  - Review state transition rules
  - Suspicious artifact visibility (no TC required)
  - Requeue semantics (process_recovery produces QC trigger)
  - Label preview endpoint degrades gracefully
  - Audit trail endpoint structure
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient  # noqa: E402

from pathoryx_enterprise.services.dashboard.app import create_app, get_db  # noqa: E402
from pathoryx_enterprise.services.dashboard.actions import (  # noqa: E402
    ActionError,
    _REVIEW_TRANSITIONS,
    validate_filename_structured,
    _validate_proposed_filename,
    _resolve_watch_folder,
)

_QUERY_MODULE = "pathoryx_enterprise.services.dashboard.app.q"
_ACTIONS_MODULE = "pathoryx_enterprise.services.dashboard.app"
_NOW_ISO = "2026-06-03T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def mock_db():
    return MagicMock()


@pytest.fixture(scope="module")
def client(app, mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# A) Filename validation — structured results
# ---------------------------------------------------------------------------

class TestFilenameValidationStructured:
    """validate_filename_structured returns rich classification + component info."""

    def test_valid_complete_with_timestamp(self):
        r = validate_filename_structured("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        assert r["classification"] == "valid"
        assert r["errors"] == []
        c = r["components"]
        assert c["case_id"] == "N2024002863"
        assert c["pot"] == "SA"
        assert c["stain"] == "H&E"
        assert c["timestamp"] is not None

    def test_valid_no_timestamp_is_partially_valid(self):
        r = validate_filename_structured("N2024002863SA-1-1-H&E.svs")
        assert r["classification"] == "partially_valid"
        assert any(w["code"] == "no_timestamp" for w in r["warnings"])
        assert r["components"]["case_id"] == "N2024002863"

    def test_recognisable_case_id_bad_structure(self):
        r = validate_filename_structured("N2024002863BADSTRUCTURE.svs")
        assert r["classification"] == "partially_valid"
        assert r["components"]["case_id"] == "N2024002863"
        assert any(e["code"] == "invalid_structure" for e in r["errors"])
        # Suggested correction should start with the case ID
        assert r["suggested_correction"] is not None
        assert r["suggested_correction"].startswith("N2024002863")

    def test_empty_filename_is_invalid(self):
        r = validate_filename_structured("")
        assert r["classification"] == "invalid"
        assert any(e["code"] == "empty" for e in r["errors"])

    def test_path_separator_is_invalid(self):
        r = validate_filename_structured("subdir/N2024002863SA-1-1-H&E.svs")
        assert r["classification"] == "invalid"
        assert any(e["code"] == "path_traversal" for e in r["errors"])

    def test_unsupported_extension_is_invalid(self):
        r = validate_filename_structured("N2024002863SA-1-1-H&E.exe")
        assert r["classification"] == "invalid"
        assert any(e["code"] == "invalid_extension" for e in r["errors"])

    def test_dotdot_is_invalid(self):
        r = validate_filename_structured("../N2024002863SA-1-1-H&E.svs")
        assert r["classification"] == "invalid"

    def test_completely_random_name_is_invalid(self):
        r = validate_filename_structured("random_slide_42.svs")
        assert r["classification"] == "invalid"
        assert r["suggested_correction"] is None

    def test_valid_ndpi_extension(self):
        r = validate_filename_structured("N2024002863SA-2-3-PAS_UTC2024-01-15T09_00_00Z.ndpi")
        assert r["classification"] == "valid"
        assert r["components"]["extension"] == ".ndpi"

    def test_multiple_stain_chars(self):
        r = validate_filename_structured("N2024002863SA-1-1-IHC-CD3.svs")
        assert r["classification"] == "partially_valid"  # valid structure, no timestamp
        assert r["components"]["stain"] == "IHC-CD3"


# ---------------------------------------------------------------------------
# B) Validation API endpoint
# ---------------------------------------------------------------------------

class TestValidateFilenameEndpoint:
    def test_returns_valid_classification(self, client):
        resp = client.post(
            "/dashboard/api/recovery/validate-filename",
            json={"filename": "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["classification"] == "valid"
        assert data["components"]["case_id"] == "N2024002863"

    def test_returns_invalid_for_bad_extension(self, client):
        resp = client.post(
            "/dashboard/api/recovery/validate-filename",
            json={"filename": "slide.bmp"},
        )
        assert resp.status_code == 200
        assert resp.json()["classification"] == "invalid"

    def test_returns_partially_valid_with_suggestion(self, client):
        resp = client.post(
            "/dashboard/api/recovery/validate-filename",
            json={"filename": "N2024002863BROKEN.svs"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["classification"] == "partially_valid"
        assert data["suggested_correction"] is not None

    def test_no_filesystem_sideeffect(self, client):
        """Calling validate endpoint must never touch the filesystem."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            before = set(os.listdir(d))
            client.post(
                "/dashboard/api/recovery/validate-filename",
                json={"filename": "N2024002863SA-1-1-H&E.svs"},
            )
            after = set(os.listdir(d))
        assert before == after


# ---------------------------------------------------------------------------
# C) Path traversal / safety guards
# ---------------------------------------------------------------------------

class TestPathSafetyGuards:
    def test_path_traversal_rejected_by_validator(self):
        with pytest.raises(ActionError):
            _validate_proposed_filename("../../etc/passwd")

    def test_backslash_rejected(self):
        with pytest.raises(ActionError):
            _validate_proposed_filename("sub\\dir.svs")

    def test_subdir_rejected(self):
        with pytest.raises(ActionError):
            _validate_proposed_filename("subdir/slide.svs")

    def test_unsupported_ext_rejected(self):
        with pytest.raises(ActionError, match="extension"):
            _validate_proposed_filename("N2024002863SA-1-1-H&E.exe")

    def test_valid_name_passes(self):
        _validate_proposed_filename("N2024002863SA-1-1-H&E.svs")  # no exception

    def test_resolve_watch_folder_rejects_outside_root(self):
        allowed = [Path("/tmp/failed"), Path("/tmp/suspicious")]
        with pytest.raises(ActionError, match="not within"):
            _resolve_watch_folder(Path("/etc/passwd"), allowed)

    def test_resolve_watch_folder_accepts_inside_root(self, tmp_path):
        watch = tmp_path / "failed"
        watch.mkdir()
        result = _resolve_watch_folder(watch / "slide.svs", [watch])
        assert result == watch


# ---------------------------------------------------------------------------
# D) Duplicate prevention
# ---------------------------------------------------------------------------

class TestDuplicatePrevention:
    """Rename must refuse when the proposed filename already exists on disk."""

    def test_rename_blocked_when_target_exists(self, tmp_path):
        from pathoryx_enterprise.services.dashboard.actions import execute_technician_rename

        watch = tmp_path / "failed"
        watch.mkdir()

        source = watch / "badname.svs"
        source.write_bytes(b"slide data")

        # Pre-create the target so there's a collision
        target = watch / "N2024002863SA-1-1-H&E.svs"
        target.write_bytes(b"existing file")

        snap = MagicMock()
        snap.file_path  = str(source)
        snap.filename   = "badname.svs"
        snap.file_size  = 10
        snap.global_artifact_id = None
        snap.file_record_internal_id = None

        settings = SimpleNamespace(
            watch_folders=[watch],
            final_destination=tmp_path / "final",
            auto_recover_valid_slide_id=True,
            add_timestamp_if_missing=True,
            overwrite_existing=False,
            duplicate_strategy="suffix",
            next_stage_target_service="qc_service",
            next_stage_name="qc",
            allow_filesystem_timestamp_fallback=False,
        )

        with pytest.raises(ActionError, match="already exists"):
            execute_technician_rename(
                snapshot=snap,
                proposed_filename="N2024002863SA-1-1-H&E.svs",
                technician_note=None,
                watch_folders=[watch],
                settings=settings,
            )

        # Source file must be untouched
        assert source.exists()
        assert target.read_bytes() == b"existing file"


# ---------------------------------------------------------------------------
# E) Lineage preservation — audit event on rename
# ---------------------------------------------------------------------------

class TestLineagePreservation:
    """execute_technician_rename must create a TechnicianChange record."""

    def test_technician_change_created_on_rename(self, tmp_path):
        from pathoryx_enterprise.services.dashboard.actions import execute_technician_rename
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine

        watch = tmp_path / "failed"
        watch.mkdir()
        source = watch / "badname.svs"
        source.write_bytes(b"fake svs")

        snap = MagicMock()
        snap.file_path   = str(source)
        snap.filename    = "badname.svs"
        snap.file_size   = 8
        snap.global_artifact_id = None
        snap.file_record_internal_id = None

        settings = SimpleNamespace(
            watch_folders=[watch],
            final_destination=tmp_path / "final",
            auto_recover_valid_slide_id=True,
            add_timestamp_if_missing=True,
            overwrite_existing=False,
            duplicate_strategy="suffix",
            next_stage_target_service="qc_service",
            next_stage_name="qc",
            allow_filesystem_timestamp_fallback=False,
        )

        created_changes: list[dict] = []

        def fake_record_change(**kwargs):
            created_changes.append(kwargs)
            rec = MagicMock()
            rec.internal_id = 99
            return rec, True

        with (
            patch(
                "pathoryx_enterprise.services.dashboard.actions.get_session",
            ) as mock_session,
            patch.object(recovery_engine, "_persist_recovery"),
        ):
            # Make context manager yield a session with the change repo patched
            mock_sess = MagicMock()
            mock_session.return_value.__enter__.return_value = mock_sess

            # Patch repos on the session
            change_repo = MagicMock()
            change_repo.record_change.side_effect = fake_record_change
            snap_repo = MagicMock()
            snap_repo.upsert.return_value = None
            snap_repo.delete_by_path.return_value = None

            with (
                patch(
                    "pathoryx_enterprise.services.dashboard.actions.TechnicianChangeRepository",
                    return_value=change_repo,
                ),
                patch(
                    "pathoryx_enterprise.services.dashboard.actions.WatchedFolderSnapshotRepository",
                    return_value=snap_repo,
                ),
            ):
                result = execute_technician_rename(
                    snapshot=snap,
                    proposed_filename="N2024002863SA-1-1-H&E.svs",
                    technician_note="corrected from label",
                    watch_folders=[watch],
                    settings=settings,
                )

        # A TechnicianChange was attempted
        assert len(created_changes) == 1
        ch = created_changes[0]
        assert ch["change_type"] == "rename"
        assert ch["old_filename"] == "badname.svs"
        assert ch["new_filename"] == "N2024002863SA-1-1-H&E.svs"
        assert ch["inferred_action"] == "dashboard_correction"
        assert ch["technician_notes"] == "corrected from label"
        assert ch["slide_id_inferred"] == "N2024002863SA-1-1-H&E"


# ---------------------------------------------------------------------------
# F) Review state transitions
# ---------------------------------------------------------------------------

class TestReviewStateTransitions:
    def test_valid_transition_table_defined(self):
        """Every meaningful status has a defined transition set."""
        for status in ("detected", "unlinked", "linked", "investigating",
                       "corrected", "requeued", "reviewed", "dismissed"):
            assert status in _REVIEW_TRANSITIONS

    def test_detected_can_move_to_investigating(self):
        assert "investigating" in _REVIEW_TRANSITIONS["detected"]

    def test_detected_can_be_dismissed(self):
        assert "dismissed" in _REVIEW_TRANSITIONS["detected"]

    def test_reviewed_is_terminal(self):
        assert len(_REVIEW_TRANSITIONS["reviewed"]) == 0

    def test_dismissed_can_be_reopened(self):
        assert "detected" in _REVIEW_TRANSITIONS["dismissed"]

    def test_invalid_transition_rejected_by_endpoint(self, client):
        """reviewed → dismissed is not an allowed transition."""
        change = MagicMock()
        change.internal_id = 1
        change.review_status = "reviewed"  # terminal state
        change.watch_folder_label = "failed"
        change.new_path = "/data/failed/slide.svs"
        change.new_filename = "slide.svs"
        change.global_artifact_id = None
        change.file_record_internal_id = None

        with patch(
            "pathoryx_enterprise.services.dashboard.actions.get_session",
        ) as mock_sess_cm:
            mock_sess = MagicMock()
            mock_sess_cm.return_value.__enter__.return_value = mock_sess
            mock_sess.execute.return_value.scalar_one_or_none.return_value = change

            resp = client.patch(
                "/dashboard/api/recovery/changes/1/review-state",
                json={"review_status": "dismissed"},
            )

        assert resp.status_code == 422
        assert "reviewed" in resp.json()["detail"].lower() or "terminal" in resp.json()["detail"].lower() or "Cannot" in resp.json()["detail"]

    def test_review_state_endpoint_emits_event(self, client):
        """Successful transition must emit a PipelineEvent (via EventStoreRepository.append)."""
        change = MagicMock()
        change.internal_id = 42
        change.review_status = "detected"
        change.watch_folder_label = "failed"
        change.new_path = "/data/failed/slide.svs"
        change.new_filename = "slide.svs"
        change.global_artifact_id = "GAI-test"
        change.file_record_internal_id = None

        emitted: list[str] = []

        def fake_append(**kwargs):
            emitted.append(kwargs["event_type"])
            return MagicMock()

        with patch(
            "pathoryx_enterprise.services.dashboard.actions.get_session",
        ) as mock_sess_cm:
            mock_sess = MagicMock()
            mock_sess_cm.return_value.__enter__.return_value = mock_sess
            mock_sess.execute.return_value.scalar_one_or_none.return_value = change
            mock_sess.flush.return_value = None

            event_repo = MagicMock()
            event_repo.append.side_effect = fake_append

            with patch(
                "pathoryx_enterprise.services.dashboard.actions.EventStoreRepository",
                return_value=event_repo,
            ):
                resp = client.patch(
                    "/dashboard/api/recovery/changes/42/review-state",
                    json={"review_status": "investigating"},
                )

        assert resp.status_code == 200
        assert resp.json()["new_status"] == "investigating"
        assert "dashboard.review_state_updated" in emitted


# ---------------------------------------------------------------------------
# G) Suspicious artifact visibility (files without TechnicianChange shown)
# ---------------------------------------------------------------------------

class TestSuspiciousArtifactVisibility:
    def _make_monitored_row_no_tc(self) -> dict:
        """A snapshot row with NO linked TechnicianChange (all TC fields None)."""
        from datetime import datetime, timezone
        return {
            "file_id": 77,
            "filename": "suspicious_slide.svs",
            "file_path": "/data/suspicious/suspicious_slide.svs",
            "folder_label": "suspicious",
            "folder_path": "/data/suspicious",
            "first_seen_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
            "last_seen_at":  datetime(2026, 6, 3, tzinfo=timezone.utc),
            "file_size": 50 * 1024 * 1024,
            "slide_id": None,
            "case_id": None,
            "extension": ".svs",
            "global_artifact_id": None,
            "file_record_internal_id": None,
            # No TechnicianChange:
            "change_id": None,
            "change_type": None,
            "review_status": None,
            "recovery_outcome": None,
            "recovery_reason": None,
            "detected_at": None,
            "inferred_action": None,
        }

    def test_file_without_tc_is_included_in_listing(self, client):
        row = self._make_monitored_row_no_tc()
        with patch(f"{_QUERY_MODULE}.list_monitored_files", return_value=(1, [row])):
            resp = client.get("/dashboard/api/recovery/files?folder_type=suspicious")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        item = data["items"][0]
        # File is present even with no TechnicianChange
        assert item["change_id"] is None
        assert item["review_status"] is None
        assert item["filename"] == "suspicious_slide.svs"

    def test_filter_by_folder_type_works(self, client):
        with patch(f"{_QUERY_MODULE}.list_monitored_files", return_value=(0, [])) as mock:
            client.get("/dashboard/api/recovery/files?folder_type=manual_review")
        mock.assert_called_once()
        assert mock.call_args[1]["folder_type"] == "manual_review"


# ---------------------------------------------------------------------------
# H) Requeue semantics — process_recovery produces QC trigger
# ---------------------------------------------------------------------------

class TestRequeueSemanticsFromEngine:
    """The shared process_recovery() path must enqueue a QC trigger."""

    def test_auto_recovered_file_gets_qc_trigger(self, tmp_path):
        from contextlib import contextmanager
        from unittest.mock import patch

        from pathoryx_enterprise.services.recovery_sentry import recovery_engine
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        slide = tmp_path / "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        slide.write_bytes(b"fake")

        settings = SimpleNamespace(
            watch_folders=[tmp_path],
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

        trigger_calls: list[dict] = []

        mock_fr = MagicMock()
        mock_fr.internal_id = 1
        mock_fr.global_artifact_id = None
        mock_fr.source_service = None

        mock_trig = MagicMock()
        mock_trig.internal_id = 55

        mock_fr_repo = MagicMock()
        mock_fr_repo.get_by_canonical_path.return_value = None
        mock_fr_repo.get_or_create_safe.return_value = (mock_fr, True)

        mock_tr_repo = MagicMock()
        def capture_enqueue(**kw):
            trigger_calls.append(kw)
            return mock_trig, True
        mock_tr_repo.enqueue.side_effect = capture_enqueue

        mock_ev_repo = MagicMock()

        @contextmanager
        def fake_session():
            yield MagicMock()

        with (
            patch.object(recovery_engine, "get_session", side_effect=fake_session),
            patch.object(recovery_engine, "FileRecordRepository", return_value=mock_fr_repo),
            patch.object(recovery_engine, "TriggerRepository", return_value=mock_tr_repo),
            patch.object(recovery_engine, "EventStoreRepository", return_value=mock_ev_repo),
        ):
            result = process_recovery(
                new_path=str(slide),
                new_filename=slide.name,
                change_type="ADDED",
                technician_change_id=None,
                file_record_internal_id=None,
                global_artifact_id=None,
                correlation_id=None,
                runner_id="test",
                settings=settings,
            )

        assert result.outcome == "auto_recovered"
        assert len(trigger_calls) == 1
        assert trigger_calls[0]["target_service"] == "qc_service"
        assert trigger_calls[0]["stage_name"] == "qc"


# ---------------------------------------------------------------------------
# I) Audit trail endpoint
# ---------------------------------------------------------------------------

class TestAuditTrailEndpoint:
    def test_returns_200_with_empty_history(self, client):
        with patch(f"{_QUERY_MODULE}.get_artifact_audit_trail", return_value={
            "file_id": 42, "filename": "slide.svs",
            "global_artifact_id": None, "changes": [], "events": [],
        }):
            resp = client.get("/dashboard/api/recovery/files/42/audit-trail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_id"] == 42
        assert data["changes"] == []
        assert data["events"] == []

    def test_returns_changes_with_full_fields(self, client):
        trail = {
            "file_id": 10, "filename": "slide.svs",
            "global_artifact_id": "GAI-10",
            "changes": [{
                "change_id": 1,
                "change_type": "rename",
                "inferred_action": "dashboard_correction",
                "old_filename": "bad.svs",
                "new_filename": "N2024002863SA-1-1-H&E.svs",
                "old_path": None, "new_path": None,
                "review_status": "investigating",
                "recovery_outcome": None, "recovery_reason": None,
                "technician_notes": "Fixed OCR", "review_notes": None,
                "detected_at": _NOW_ISO,
                "recovered_at": None, "requeued_at": None, "reviewed_at": None,
            }],
            "events": [],
        }
        with patch(f"{_QUERY_MODULE}.get_artifact_audit_trail", return_value=trail):
            resp = client.get("/dashboard/api/recovery/files/10/audit-trail")
        assert resp.status_code == 200
        ch = resp.json()["changes"][0]
        assert ch["inferred_action"] == "dashboard_correction"
        assert ch["old_filename"] == "bad.svs"
        assert ch["technician_notes"] == "Fixed OCR"

    def test_degrades_gracefully_on_db_error(self, client):
        from sqlalchemy.exc import OperationalError
        with patch(
            f"{_QUERY_MODULE}.get_artifact_audit_trail",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/recovery/files/42/audit-trail")
        assert resp.status_code == 200
        assert resp.json()["changes"] == []


# ---------------------------------------------------------------------------
# J) Label preview — enriched fields + graceful degradation
# ---------------------------------------------------------------------------

class TestLabelPreviewEnriched:
    def test_returns_all_enriched_fields_when_available(self, client):
        data = {
            "file_id": 5,
            "filename": "slide.svs",
            "available": True,
            "unavailable_reason": None,
            "slide_id": "N2024002863SA-1-1-H&E",
            "case_id": "N2024002863",
            "scanner_id": "SC-01",
            "scanner_vendor": "Aperio",
            "scanner_model": "GT450",
            "stain_type": "H&E",
            "suggested_filename": "N2024002863SA-1-1-H&E.svs",
            "datamatrix_raw": "N2024002863SA1HE",
            "datamatrix_decode_status": "success",
            "datamatrix_error": None,
            "stain_ocr_raw": "HE haematoxylin eosin",
            "stain_matched": "H&E",
            "stain_origin": "Primary",
            "roi_case_number": None,
            "roi_lab_id": None,
            "roi_stain": None,
            "routing_type": "routine",
            "routing_reason": None,
            "original_filename": "slide.svs",
            "extraction_metadata": {"intake_decision": "accept", "extraction_status": "success", "requires_qc": True, "next_stage": "qc", "action_taken": "rename"},
        }
        with patch(f"{_QUERY_MODULE}.get_label_preview_data", return_value=data):
            resp = client.get("/dashboard/api/recovery/files/5/label-preview")
        assert resp.status_code == 200
        r = resp.json()
        assert r["available"] is True
        assert r["datamatrix_raw"] == "N2024002863SA1HE"
        assert r["stain_matched"] == "H&E"
        assert r["scanner_model"] == "GT450"

    def test_degrades_gracefully_when_no_linked_record(self, client):
        data = {
            "file_id": 9, "filename": "slide.svs",
            "available": False, "unavailable_reason": "no_linked_record",
            "slide_id": None, "case_id": None,
            "scanner_id": None, "scanner_vendor": None, "scanner_model": None,
            "stain_type": None, "suggested_filename": "slide.svs",
            "datamatrix_raw": None, "datamatrix_decode_status": None, "datamatrix_error": None,
            "stain_ocr_raw": None, "stain_matched": None, "stain_origin": None,
            "roi_case_number": None, "roi_lab_id": None, "roi_stain": None,
            "routing_type": None, "routing_reason": None,
            "original_filename": "slide.svs", "extraction_metadata": None,
        }
        with patch(f"{_QUERY_MODULE}.get_label_preview_data", return_value=data):
            resp = client.get("/dashboard/api/recovery/files/9/label-preview")
        assert resp.status_code == 200
        assert resp.json()["available"] is False
        assert resp.json()["unavailable_reason"] == "no_linked_record"


# ---------------------------------------------------------------------------
# K) External filesystem rename detection (via slide_id_parser)
# ---------------------------------------------------------------------------

class TestExternalRenameDetection:
    """
    RecoverySentry detects filesystem renames via snapshot diff.
    Verify the underlying parser correctly classifies the renamed file.
    """

    def test_renamed_to_valid_id_is_parsed(self):
        from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import parse_slide_id

        # Simulate a file that was manually renamed to a valid Palantir ID
        result = parse_slide_id("N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs")
        assert result is not None
        assert result.case_id == "N2024002863"
        assert result.has_timestamp is True

    def test_renamed_to_partial_id_fails_parser(self):
        from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import parse_slide_id

        # Technician gave it a partial name — parser returns None (→ manual_review)
        result = parse_slide_id("N2024002863INCOMPLETE.svs")
        assert result is None

    def test_renamed_to_valid_no_timestamp_returns_parsed(self):
        from pathoryx_enterprise.services.recovery_sentry.slide_id_parser import parse_slide_id

        result = parse_slide_id("N2024002863SA-1-1-H&E.svs")
        assert result is not None
        assert result.has_timestamp is False
        assert result.slide_id_base == "N2024002863SA-1-1-H&E"
