"""
Phase 3 — RecoverySentry finalization tests.

Covers:
  A. change_detector shim backward-compat (old import path still works)
  B. Deprecated failed_watcher CLI stub exits with code 1
  C. Quarantine behavior — invalid files stay in watch folder, not moved
  D. Requeue behavior — auto_recovered outcome enqueues QC trigger
  E. Rename audit flow — dashboard rename always creates TechnicianChange
  F. Invalid review-state transition rejected with 422
  G. Review state terminal check (reviewed → nothing)
  H. Missing label preview degrades gracefully
"""
from __future__ import annotations

import sys
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
)

_QUERY_MODULE = "pathoryx_enterprise.services.dashboard.app.q"


@pytest.fixture(scope="module")
def client():
    app = create_app()
    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# A. change_detector shim backward-compat
# ---------------------------------------------------------------------------

class TestChangeDetectorShim:
    """Old import path from services.failed_watcher still works via re-export."""

    def test_old_import_path_still_importable(self):
        from pathoryx_enterprise.services.failed_watcher.change_detector import (
            ChangeEvent,
            detect_changes,
            scan_folder,
        )
        assert callable(detect_changes)
        assert callable(scan_folder)

    def test_old_and_new_import_are_same_object(self):
        from pathoryx_enterprise.services.failed_watcher.change_detector import (
            detect_changes as old_detect,
        )
        from pathoryx_enterprise.services.recovery_sentry.change_detector import (
            detect_changes as new_detect,
        )
        assert old_detect is new_detect


# ---------------------------------------------------------------------------
# B. Deprecated CLI stub exits with code 1
# ---------------------------------------------------------------------------

class TestDeprecatedCLIStub:
    def test_failed_watcher_main_exits_1(self):
        from pathoryx_enterprise.services.failed_watcher.main import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# C. Quarantine behavior — invalid files stay in the watch folder
# ---------------------------------------------------------------------------

class TestQuarantineBehavior:
    """Files with invalid SlideID must NOT be moved; they stay for technician review."""

    def test_invalid_filename_not_moved(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        bad_file = tmp_path / "garbage_name.svs"
        bad_file.write_bytes(b"fake wsi")

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
        # File must still exist in its original location
        assert bad_file.exists(), "Invalid file must not be moved out of watch folder"
        # final/ must be empty
        final_dir = tmp_path / "final"
        if final_dir.exists():
            assert list(final_dir.rglob("*.svs")) == [], "No SVS files should appear in final/"

    def test_unsupported_extension_not_moved(self, tmp_path):
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        bad_ext = tmp_path / "N2024002863SA-1-1-H&E.exe"
        bad_ext.write_bytes(b"not a slide")

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

        result = process_recovery(
            new_path=str(bad_ext),
            new_filename=bad_ext.name,
            change_type="ADDED",
            technician_change_id=None,
            file_record_internal_id=None,
            global_artifact_id=None,
            correlation_id=None,
            runner_id="test",
            settings=settings,
        )

        assert result.outcome == "manual_review_required"
        assert bad_ext.exists()


# ---------------------------------------------------------------------------
# D. Requeue behavior — auto_recovered outcome enqueues QC trigger
# ---------------------------------------------------------------------------

class TestRequeueBehavior:
    def test_valid_file_with_timestamp_enqueues_qc_trigger(self, tmp_path):
        """Confirmed: valid slide → auto_recovered → QC trigger created."""
        from contextlib import contextmanager
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine
        from pathoryx_enterprise.services.recovery_sentry.recovery_engine import process_recovery

        slide = tmp_path / "N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs"
        slide.write_bytes(b"fake wsi data")

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

        enqueue_calls: list[dict] = []

        mock_fr = MagicMock()
        mock_fr.internal_id = 1
        mock_fr.global_artifact_id = None
        mock_fr.source_service = None

        mock_trig = MagicMock()
        mock_trig.internal_id = 42

        mock_fr_repo = MagicMock()
        mock_fr_repo.get_by_canonical_path.return_value = None
        mock_fr_repo.get_or_create_safe.return_value = (mock_fr, True)

        mock_tr_repo = MagicMock()
        def capture(**kw):
            enqueue_calls.append(kw)
            return mock_trig, True
        mock_tr_repo.enqueue.side_effect = capture

        @contextmanager
        def fake_session():
            yield MagicMock()

        with (
            patch.object(recovery_engine, "get_session", side_effect=fake_session),
            patch.object(recovery_engine, "FileRecordRepository", return_value=mock_fr_repo),
            patch.object(recovery_engine, "TriggerRepository", return_value=mock_tr_repo),
            patch.object(recovery_engine, "EventStoreRepository", return_value=MagicMock()),
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
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0]["target_service"] == "qc_service"
        assert enqueue_calls[0]["stage_name"] == "qc"
        assert enqueue_calls[0]["source_service"] == "recovery_sentry"


# ---------------------------------------------------------------------------
# E. Rename audit flow — execute_technician_rename always creates TechnicianChange
# ---------------------------------------------------------------------------

class TestRenameAuditFlow:
    def test_rename_produces_technician_change_with_inferred_action(self, tmp_path):
        from pathoryx_enterprise.services.dashboard.actions import execute_technician_rename
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine

        watch = tmp_path / "failed"
        watch.mkdir()
        src = watch / "corrupt_name.svs"
        src.write_bytes(b"fake wsi")

        snap = MagicMock()
        snap.file_path = str(src)
        snap.filename = "corrupt_name.svs"
        snap.file_size = 8
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

        recorded_changes: list[dict] = []

        def capture_change(**kw):
            recorded_changes.append(kw)
            r = MagicMock()
            r.internal_id = 77
            return r, True

        with (
            patch("pathoryx_enterprise.services.dashboard.actions.get_session") as mock_sess_cm,
            patch.object(recovery_engine, "_persist_recovery"),
        ):
            mock_sess = MagicMock()
            mock_sess_cm.return_value.__enter__.return_value = mock_sess

            change_repo = MagicMock()
            change_repo.record_change.side_effect = capture_change
            snap_repo = MagicMock()

            with (
                patch("pathoryx_enterprise.services.dashboard.actions.TechnicianChangeRepository",
                      return_value=change_repo),
                patch("pathoryx_enterprise.services.dashboard.actions.WatchedFolderSnapshotRepository",
                      return_value=snap_repo),
            ):
                result = execute_technician_rename(
                    snapshot=snap,
                    proposed_filename="N2024002863SA-1-1-H&E.svs",
                    technician_note="corrected in dashboard",
                    watch_folders=[watch],
                    settings=settings,
                )

        assert len(recorded_changes) == 1
        ch = recorded_changes[0]
        assert ch["inferred_action"] == "dashboard_correction"
        assert ch["change_type"] == "rename"
        assert ch["old_filename"] == "corrupt_name.svs"
        assert ch["new_filename"] == "N2024002863SA-1-1-H&E.svs"
        assert ch["technician_notes"] == "corrected in dashboard"

    def test_rename_without_note_still_creates_record(self, tmp_path):
        """Audit record created even when technician_note is None."""
        from pathoryx_enterprise.services.dashboard.actions import execute_technician_rename
        from pathoryx_enterprise.services.recovery_sentry import recovery_engine

        watch = tmp_path / "suspicious"
        watch.mkdir()
        src = watch / "badname.svs"
        src.write_bytes(b"slide")

        snap = MagicMock()
        snap.file_path = str(src)
        snap.filename = "badname.svs"
        snap.file_size = 5
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

        recorded: list[dict] = []

        def capture(**kw):
            recorded.append(kw)
            r = MagicMock()
            r.internal_id = 55
            return r, True

        with (
            patch("pathoryx_enterprise.services.dashboard.actions.get_session") as mock_sess_cm,
            patch.object(recovery_engine, "_persist_recovery"),
        ):
            mock_sess = MagicMock()
            mock_sess_cm.return_value.__enter__.return_value = mock_sess
            change_repo = MagicMock()
            change_repo.record_change.side_effect = capture
            snap_repo = MagicMock()
            with (
                patch("pathoryx_enterprise.services.dashboard.actions.TechnicianChangeRepository",
                      return_value=change_repo),
                patch("pathoryx_enterprise.services.dashboard.actions.WatchedFolderSnapshotRepository",
                      return_value=snap_repo),
            ):
                execute_technician_rename(
                    snapshot=snap,
                    proposed_filename="N2024002863SA-1-1-PAS.svs",
                    technician_note=None,
                    watch_folders=[watch],
                    settings=settings,
                )

        assert len(recorded) == 1
        assert recorded[0]["technician_notes"] is None


# ---------------------------------------------------------------------------
# F. Invalid review-state transition rejected (via endpoint)
# ---------------------------------------------------------------------------

class TestInvalidTransitionRejected:
    def test_requeued_to_dismissed_blocked(self, client):
        """requeued → dismissed is not allowed; must return 422."""
        change = MagicMock()
        change.internal_id = 5
        change.review_status = "requeued"
        change.watch_folder_label = "failed"
        change.new_path = "/data/failed/slide.svs"
        change.new_filename = "slide.svs"
        change.global_artifact_id = None
        change.file_record_internal_id = None

        with patch(
            "pathoryx_enterprise.services.dashboard.actions.get_session",
        ) as mock_cm:
            mock_sess = MagicMock()
            mock_cm.return_value.__enter__.return_value = mock_sess
            mock_sess.execute.return_value.scalar_one_or_none.return_value = change

            resp = client.patch(
                "/dashboard/api/recovery/changes/5/review-state",
                json={"review_status": "dismissed"},
            )

        assert resp.status_code == 422

    def test_completely_unknown_status_blocked(self, client):
        change = MagicMock()
        change.review_status = "detected"
        with patch(
            "pathoryx_enterprise.services.dashboard.actions.get_session",
        ) as mock_cm:
            mock_sess = MagicMock()
            mock_cm.return_value.__enter__.return_value = mock_sess
            mock_sess.execute.return_value.scalar_one_or_none.return_value = change
            resp = client.patch(
                "/dashboard/api/recovery/changes/1/review-state",
                json={"review_status": "HACKED"},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# G. Terminal state — reviewed has no outgoing transitions
# ---------------------------------------------------------------------------

class TestTerminalState:
    def test_reviewed_is_terminal(self):
        assert _REVIEW_TRANSITIONS["reviewed"] == frozenset()

    def test_all_expected_states_present(self):
        expected = {
            "detected", "unlinked", "linked", "investigating",
            "corrected", "requeued", "reviewed", "dismissed",
        }
        assert expected.issubset(set(_REVIEW_TRANSITIONS))


# ---------------------------------------------------------------------------
# H. Missing label preview degrades gracefully
# ---------------------------------------------------------------------------

class TestLabelPreviewDegrades:
    def test_missing_label_dir_returns_404_not_500(self, client):
        """If label image cannot be found the endpoint returns 404, not 500."""
        snap = MagicMock()
        snap.filename = "N2024002863SA-1-1-H&E.svs"

        with (
            patch(_QUERY_MODULE + ".get_monitored_file", return_value=snap),
            patch(
                "pathoryx_enterprise.services.dashboard.app._resolve_label_root_dir",
                return_value=None,
            ),
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")

        assert resp.status_code == 404
        assert "not configured" in resp.json()["detail"].lower()

    def test_label_preview_endpoint_returns_200_on_db_error(self, client):
        """label-preview endpoint degrades to unavailable=False on DB error."""
        from sqlalchemy.exc import OperationalError

        with patch(
            _QUERY_MODULE + ".get_label_preview_data",
            side_effect=OperationalError("", {}, Exception()),
        ):
            resp = client.get("/dashboard/api/recovery/files/99/label-preview")

        assert resp.status_code == 200
        assert resp.json()["available"] is False
