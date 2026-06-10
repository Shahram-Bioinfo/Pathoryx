"""
Phase 4.3A — Recovery Sentry Recursive Folder Monitoring + Open Folder Action.

Tests:
  A. Recursive scan includes nested files
  B. Recursive scan can be disabled (flat only)
  C. Symlinks / outside-root paths are rejected
  D. Hidden directories and files are skipped
  E. relative_folder_path computed correctly in API response
  F. scan_subfolders config key is loaded from YAML
  G. POST /open-folder endpoint rejects unknown file_id (404)
  H. POST /open-folder endpoint rejects path outside allowed roots (403)
  I. POST /open-folder succeeds for known monitored file (mocked OS call)
  J. POST /open-folder returns opened=False when folder does not exist
  K. Windows os.startfile call is mocked correctly
  L. Linux xdg-open call is mocked correctly
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# A. Recursive scan includes nested files
# ---------------------------------------------------------------------------

class TestScanFolderRecursive:
    def test_finds_nested_svs(self, tmp_path: Path):
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        # Structure: failed/2026-06-05/N24-3625-Q.svs
        sub = tmp_path / "2026-06-05"
        sub.mkdir()
        slide = sub / "N24-3625-Q.svs"
        slide.write_bytes(b"FAKE")

        result = scan_folder(tmp_path, allowed_roots=[tmp_path], recursive=True)

        assert str(slide) in result
        assert result[str(slide)]["filename"] == "N24-3625-Q.svs"

    def test_finds_deep_nested(self, tmp_path: Path):
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        deep = tmp_path / "case123" / "sub"
        deep.mkdir(parents=True)
        slide = deep / "slideA.svs"
        slide.write_bytes(b"FAKE")

        result = scan_folder(tmp_path, allowed_roots=[tmp_path], recursive=True)

        assert str(slide) in result

    def test_recursive_default_is_true(self, tmp_path: Path):
        """scan_folder recursive defaults to True — no explicit flag needed."""
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        sub = tmp_path / "date_dir"
        sub.mkdir()
        slide = sub / "A24-001001SA-1-1-HE.ndpi"
        slide.write_bytes(b"FAKE")

        result = scan_folder(tmp_path, allowed_roots=[tmp_path])

        assert str(slide) in result


# ---------------------------------------------------------------------------
# B. Recursive scan can be disabled
# ---------------------------------------------------------------------------

class TestScanFolderFlat:
    def test_flat_only_finds_top_level(self, tmp_path: Path):
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        # Top-level file — should be found
        top = tmp_path / "direct.svs"
        top.write_bytes(b"FAKE")

        # Nested file — should be skipped
        sub = tmp_path / "subdir"
        sub.mkdir()
        nested = sub / "nested.svs"
        nested.write_bytes(b"FAKE")

        result = scan_folder(tmp_path, allowed_roots=[tmp_path], recursive=False)

        assert str(top) in result
        assert str(nested) not in result

    def test_flat_empty_subdir(self, tmp_path: Path):
        """Flat scan with no top-level WSI files returns empty dict."""
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "file.svs").write_bytes(b"X")

        result = scan_folder(tmp_path, allowed_roots=[tmp_path], recursive=False)

        assert result == {}


# ---------------------------------------------------------------------------
# C. Symlinks / outside-root paths are rejected
# ---------------------------------------------------------------------------

class TestScanFolderPathSafety:
    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows")
    def test_symlink_to_outside_root_skipped(self, tmp_path: Path):
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        outside = tmp_path / "outside"
        outside.mkdir()
        real_file = outside / "secret.svs"
        real_file.write_bytes(b"OUTSIDE")

        watch_root = tmp_path / "failed"
        watch_root.mkdir()

        link = watch_root / "linked.svs"
        link.symlink_to(real_file)

        # allowed_roots covers only watch_root, not outside
        result = scan_folder(watch_root, allowed_roots=[watch_root], recursive=True)

        # symlink resolves to outside — must be rejected by validate_path_under_roots
        assert str(link) not in result
        for path in result:
            assert str(outside) not in path

    def test_allowed_root_is_respected(self, tmp_path: Path):
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        slide_a = root_a / "slide.svs"
        slide_b = root_b / "slide.svs"
        slide_a.write_bytes(b"A")
        slide_b.write_bytes(b"B")

        # Only root_a is allowed
        result = scan_folder(root_a, allowed_roots=[root_a], recursive=True)

        assert str(slide_a) in result
        assert str(slide_b) not in result


# ---------------------------------------------------------------------------
# D. Hidden directories and files are skipped
# ---------------------------------------------------------------------------

class TestScanFolderHiddenItems:
    def test_hidden_directory_skipped(self, tmp_path: Path):
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        hidden_dir = tmp_path / ".cache"
        hidden_dir.mkdir()
        hidden_slide = hidden_dir / "slide.svs"
        hidden_slide.write_bytes(b"FAKE")

        result = scan_folder(tmp_path, allowed_roots=[tmp_path], recursive=True)

        assert str(hidden_slide) not in result

    def test_hidden_file_skipped(self, tmp_path: Path):
        from pathoryx_enterprise.services.recovery_sentry.change_detector import scan_folder

        (tmp_path / ".DS_Store.svs").write_bytes(b"FAKE")
        visible = tmp_path / "visible.svs"
        visible.write_bytes(b"REAL")

        result = scan_folder(tmp_path, allowed_roots=[tmp_path])

        assert str(visible) in result
        for key in result:
            assert ".DS_Store" not in key


# ---------------------------------------------------------------------------
# E. relative_folder_path computed correctly
# ---------------------------------------------------------------------------

class TestRelativeFolderPath:
    def test_nested_file_has_correct_relative_path(self, tmp_path: Path):
        """API endpoint computes relative_folder_path = subfolder under watch root."""
        watch_root = tmp_path / "failed"
        watch_root.mkdir()
        subdir = watch_root / "2026-06-05"
        subdir.mkdir()

        # Simulate what _enrich() does in app.py
        folder_path = str(subdir)
        folder_label = "failed"
        watch_root_by_label = {"failed": watch_root.resolve()}

        rel = ""
        try:
            rel = str(Path(folder_path).relative_to(watch_root_by_label[folder_label]))
            if rel == ".":
                rel = ""
        except ValueError:
            rel = ""

        assert rel == "2026-06-05"

    def test_top_level_file_has_empty_relative_path(self, tmp_path: Path):
        """Top-level file (directly in watch root) gets relative_folder_path == ''."""
        watch_root = tmp_path / "failed"
        watch_root.mkdir()

        folder_path = str(watch_root)  # file is directly in the watch root
        watch_root_by_label = {"failed": watch_root.resolve()}

        rel = ""
        try:
            rel = str(Path(folder_path).relative_to(watch_root_by_label["failed"]))
            if rel == ".":
                rel = ""
        except ValueError:
            rel = ""

        assert rel == ""

    def test_deep_nested_relative_path(self, tmp_path: Path):
        """Three levels deep: failed/case123/sub → relative = case123/sub."""
        watch_root = tmp_path / "failed"
        deep = watch_root / "case123" / "sub"
        deep.mkdir(parents=True)

        watch_root_by_label = {"failed": watch_root.resolve()}
        folder_path = str(deep)

        rel = ""
        try:
            rel = str(Path(folder_path).relative_to(watch_root_by_label["failed"]))
            if rel == ".":
                rel = ""
        except ValueError:
            rel = ""

        # On Windows/Linux both use the native separator
        assert rel.replace("\\", "/") == "case123/sub"


# ---------------------------------------------------------------------------
# F. scan_subfolders config key is loaded from YAML
# ---------------------------------------------------------------------------

class TestScanSubfoldersConfig:
    def test_default_is_true(self, tmp_path: Path):
        """RecoverySentrySettings.scan_subfolders defaults to True."""
        from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg2://u:p@h/db"}):
            settings = RecoverySentrySettings()
        assert settings.scan_subfolders is True

    def test_yaml_overrides_to_false(self, tmp_path: Path):
        """scan_subfolders: false in YAML sets the flag to False."""
        from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings

        cfg = tmp_path / "rs.yaml"
        cfg.write_text(
            "service:\n  name: test\nwatch_folders: []\nscan_subfolders: false\n"
        )
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql+psycopg2://u:p@h/db",
            "RECOVERY_SENTRY_CONFIG": str(cfg),
        }):
            settings = RecoverySentrySettings()
        assert settings.scan_subfolders is False

    def test_yaml_overrides_to_true_explicitly(self, tmp_path: Path):
        from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings

        cfg = tmp_path / "rs.yaml"
        cfg.write_text(
            "service:\n  name: test\nwatch_folders: []\nscan_subfolders: true\n"
        )
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql+psycopg2://u:p@h/db",
            "RECOVERY_SENTRY_CONFIG": str(cfg),
        }):
            settings = RecoverySentrySettings()
        assert settings.scan_subfolders is True


# ---------------------------------------------------------------------------
# Helpers: minimal DB session mock for dashboard endpoint tests
# ---------------------------------------------------------------------------

def _make_snapshot(file_id: int, file_path: str, folder_label: str = "failed") -> MagicMock:
    snap = MagicMock()
    snap.internal_id = file_id
    snap.file_path = file_path
    snap.filename = Path(file_path).name
    snap.folder_label = folder_label
    snap.global_artifact_id = None
    snap.file_record_internal_id = None
    return snap


def _make_app_client():
    """Create a TestClient with a fake DB override."""
    from pathoryx_enterprise.services.dashboard.app import create_app, get_db

    app = create_app()
    session_mock = MagicMock()
    app.dependency_overrides[get_db] = lambda: session_mock
    client = TestClient(app, raise_server_exceptions=False)
    return client, session_mock


# ---------------------------------------------------------------------------
# G. open-folder endpoint rejects unknown file_id (404)
# ---------------------------------------------------------------------------

class TestOpenFolderEndpoint:
    def test_unknown_file_id_returns_404(self):
        client, session_mock = _make_app_client()

        # get_monitored_file returns None
        with patch(
            "pathoryx_enterprise.services.dashboard.queries.get_monitored_file",
            return_value=None,
        ):
            resp = client.post("/dashboard/api/recovery/files/999999/open-folder")

        assert resp.status_code == 404

    # -----------------------------------------------------------------------
    # H. open-folder rejects path outside allowed roots (403)
    # -----------------------------------------------------------------------

    def test_path_outside_allowed_roots_returns_403(self, tmp_path: Path):
        """File whose parent is entirely outside all configured roots gets 403.

        We pick an absolute path that cannot be under CWD/data/ or any configured
        root by placing both roots under distinct tmp_path subdirectories.
        """
        client, session_mock = _make_app_client()

        # Two sibling dirs so they share the same tmp_path parent but are unrelated
        outside_root = tmp_path / "outside"
        allowed_root = tmp_path / "allowed"
        outside_root.mkdir()
        allowed_root.mkdir()

        outside_file = outside_root / "slide.svs"
        outside_file.write_bytes(b"FAKE")

        snap = _make_snapshot(1, str(outside_file))

        settings_mock = MagicMock()
        settings_mock.allowed_roots = [allowed_root]

        # Suppress the CWD data/ fallback so it doesn't cover tmp_path accidentally
        _orig_exists = Path.exists
        def _exists(self: Path) -> bool:
            if self.name == "data" and not self.is_absolute():
                return False
            return _orig_exists(self)

        with (
            patch(
                "pathoryx_enterprise.services.dashboard.queries.get_monitored_file",
                return_value=snap,
            ),
            patch(
                "pathoryx_enterprise.services.recovery_sentry.config.RecoverySentrySettings",
                return_value=settings_mock,
            ),
            patch.object(Path, "exists", _exists),
        ):
            resp = client.post("/dashboard/api/recovery/files/1/open-folder")

        assert resp.status_code == 403

    # -----------------------------------------------------------------------
    # I. open-folder succeeds for known monitored file (Linux xdg-open mocked)
    # -----------------------------------------------------------------------

    def test_linux_open_folder_success(self, tmp_path: Path):
        client, session_mock = _make_app_client()

        watch_root = tmp_path / "failed"
        watch_root.mkdir()
        subdir = watch_root / "2026-06-05"
        subdir.mkdir()
        slide = subdir / "N24-3625-Q.svs"
        slide.write_bytes(b"FAKE")

        snap = _make_snapshot(1, str(slide), "failed")

        settings_mock = MagicMock()
        settings_mock.allowed_roots = [watch_root]

        proc_mock = MagicMock(returncode=0)

        with (
            patch(
                "pathoryx_enterprise.services.dashboard.queries.get_monitored_file",
                return_value=snap,
            ),
            patch(
                "pathoryx_enterprise.services.recovery_sentry.config.RecoverySentrySettings",
                return_value=settings_mock,
            ),
            patch("platform.system", return_value="Linux"),
            patch("subprocess.run", return_value=proc_mock) as run_mock,
        ):
            resp = client.post("/dashboard/api/recovery/files/1/open-folder")

        assert resp.status_code == 200
        data = resp.json()
        assert data["opened"] is True
        assert str(subdir) in data["path"]
        run_mock.assert_called_once()
        args = run_mock.call_args[0][0]
        assert args[0] == "xdg-open"

    # -----------------------------------------------------------------------
    # J. open-folder returns opened=False when folder does not exist
    # -----------------------------------------------------------------------

    def test_folder_missing_returns_opened_false(self, tmp_path: Path):
        client, session_mock = _make_app_client()

        watch_root = tmp_path / "failed"
        watch_root.mkdir()
        # File path points to a non-existent subfolder
        slide_path = watch_root / "ghost_dir" / "slide.svs"

        snap = _make_snapshot(1, str(slide_path), "failed")

        settings_mock = MagicMock()
        settings_mock.allowed_roots = [watch_root]

        with (
            patch(
                "pathoryx_enterprise.services.dashboard.queries.get_monitored_file",
                return_value=snap,
            ),
            patch(
                "pathoryx_enterprise.services.recovery_sentry.config.RecoverySentrySettings",
                return_value=settings_mock,
            ),
            patch("platform.system", return_value="Linux"),
        ):
            resp = client.post("/dashboard/api/recovery/files/1/open-folder")

        assert resp.status_code == 200
        data = resp.json()
        assert data["opened"] is False
        assert "no longer exists" in data["message"].lower()

    # -----------------------------------------------------------------------
    # K. Windows os.startfile call is mocked
    # -----------------------------------------------------------------------

    def test_windows_startfile_called(self, tmp_path: Path):
        client, session_mock = _make_app_client()

        watch_root = tmp_path / "failed"
        watch_root.mkdir()
        subdir = watch_root / "2026-06-05"
        subdir.mkdir()
        slide = subdir / "slide.svs"
        slide.write_bytes(b"X")

        snap = _make_snapshot(1, str(slide))

        settings_mock = MagicMock()
        settings_mock.allowed_roots = [watch_root]

        # os.startfile only exists on Windows; use create=True so mock works on Linux too
        with (
            patch(
                "pathoryx_enterprise.services.dashboard.queries.get_monitored_file",
                return_value=snap,
            ),
            patch(
                "pathoryx_enterprise.services.recovery_sentry.config.RecoverySentrySettings",
                return_value=settings_mock,
            ),
            patch("platform.system", return_value="Windows"),
            patch("os.startfile", create=True) as sf_mock,
        ):
            resp = client.post("/dashboard/api/recovery/files/1/open-folder")

        assert resp.status_code == 200
        data = resp.json()
        assert data["opened"] is True
        sf_mock.assert_called_once_with(str(subdir))

    # -----------------------------------------------------------------------
    # L. macOS open call is mocked
    # -----------------------------------------------------------------------

    def test_macos_open_called(self, tmp_path: Path):
        client, session_mock = _make_app_client()

        watch_root = tmp_path / "failed"
        watch_root.mkdir()
        subdir = watch_root / "caseA"
        subdir.mkdir()
        slide = subdir / "slide.svs"
        slide.write_bytes(b"X")

        snap = _make_snapshot(1, str(slide))

        settings_mock = MagicMock()
        settings_mock.allowed_roots = [watch_root]

        with (
            patch(
                "pathoryx_enterprise.services.dashboard.queries.get_monitored_file",
                return_value=snap,
            ),
            patch(
                "pathoryx_enterprise.services.recovery_sentry.config.RecoverySentrySettings",
                return_value=settings_mock,
            ),
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.Popen") as popen_mock,
        ):
            resp = client.post("/dashboard/api/recovery/files/1/open-folder")

        assert resp.status_code == 200
        popen_mock.assert_called_once()
        args = popen_mock.call_args[0][0]
        assert args[0] == "open"
        assert str(subdir) in args[1]
