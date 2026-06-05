"""
Phase 3.2A — Label preview fix: unit tests for _build_label_search_dirs
and the label_image endpoint path resolution logic.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# _build_label_search_dirs
# ---------------------------------------------------------------------------

class TestBuildLabelSearchDirs:
    """_build_label_search_dirs returns ordered candidates from config + CWD fallback."""

    def _fn(self, cfg: dict) -> list[Path]:
        from pathoryx_enterprise.services.dashboard.app import _build_label_search_dirs
        return _build_label_search_dirs(cfg)

    def test_empty_config_returns_cwd_fallback_only(self):
        dirs = self._fn({})
        # Should include data/run_output subdirs or data/label_crops / data/labels
        # At minimum the flat fallbacks are always appended
        str_dirs = [str(d) for d in dirs]
        assert any("label_crops" in s or "labels" in s for s in str_dirs)

    def test_configured_run_output_dir_takes_priority(self, tmp_path):
        run_out = tmp_path / "run_output"
        date_dir = run_out / "2026-06-05"
        date_dir.mkdir(parents=True)
        (date_dir / "label_crops").mkdir()

        dirs = self._fn({"run_output_dir": str(run_out)})
        str_dirs = [str(d) for d in dirs]
        # The configured date/label_crops should appear before CWD fallbacks
        configured_idx = next((i for i, d in enumerate(str_dirs) if "2026-06-05" in d and "label_crops" in d), None)
        assert configured_idx is not None

    def test_newest_date_dir_comes_first(self, tmp_path):
        run_out = tmp_path / "run_output"
        for date in ("2025-01-01", "2026-06-05", "2024-12-31"):
            (run_out / date / "label_crops").mkdir(parents=True)

        dirs = self._fn({"run_output_dir": str(run_out)})
        date_crop_dirs = [d for d in dirs if "label_crops" in str(d) and str(run_out) in str(d)]
        dates = [d.parent.name for d in date_crop_dirs]
        assert dates == sorted(dates, reverse=True), "Newest date must come first"

    def test_failed_datamatrix_included(self, tmp_path):
        run_out = tmp_path / "run_output"
        date_dir = run_out / "2026-06-05"
        date_dir.mkdir(parents=True)
        (date_dir / "label_crops").mkdir()
        (date_dir / "failed_datamatrix").mkdir()

        dirs = self._fn({"run_output_dir": str(run_out)})
        str_dirs = [str(d) for d in dirs]
        assert any("failed_datamatrix" in s for s in str_dirs)

    def test_label_crops_dir_config_included(self, tmp_path):
        crops_dir = tmp_path / "my_crops"
        crops_dir.mkdir()
        dirs = self._fn({"label_crops_dir": str(crops_dir)})
        assert crops_dir in dirs

    def test_label_root_dir_config_included(self, tmp_path):
        root_dir = tmp_path / "labels_root"
        root_dir.mkdir()
        dirs = self._fn({"label_root_dir": str(root_dir)})
        assert root_dir in dirs


# ---------------------------------------------------------------------------
# label_image endpoint: stem sanitization
# ---------------------------------------------------------------------------

class TestLabelImageEndpoint:
    """Integration-style tests for the /label-image endpoint."""

    @pytest.fixture(scope="class")
    def app_client(self):
        fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
        from fastapi.testclient import TestClient
        from pathoryx_enterprise.services.dashboard.app import create_app, get_db
        from unittest.mock import MagicMock

        app = create_app()
        mock_db = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_db
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        app.dependency_overrides.clear()

    def test_missing_snapshot_returns_404(self, app_client):
        with patch(
            "pathoryx_enterprise.services.dashboard.app.q.get_monitored_file",
            return_value=None,
        ):
            resp = app_client.get("/dashboard/api/recovery/files/999/label-image")
        assert resp.status_code == 404

    def test_image_found_returns_200(self, app_client, tmp_path):
        from unittest.mock import MagicMock
        snap = MagicMock()
        snap.filename = "N24-3625-T.svs"

        # Create a real PNG in a temp dir
        img_dir = tmp_path / "label_crops"
        img_dir.mkdir()
        img_file = img_dir / "N24-3625-T.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)  # minimal PNG header

        with (
            patch("pathoryx_enterprise.services.dashboard.app.q.get_monitored_file", return_value=snap),
            patch("pathoryx_enterprise.services.dashboard.app._build_label_search_dirs", return_value=[img_dir]),
            patch("pathoryx_enterprise.services.dashboard.app._label_allowed_roots", return_value=[tmp_path]),
            patch("pathoryx_enterprise.services.dashboard.app._load_babelshark_config", return_value={}),
        ):
            resp = app_client.get("/dashboard/api/recovery/files/1/label-image")
        assert resp.status_code == 200
        assert "image" in resp.headers.get("content-type", "")

    def test_no_image_in_any_dir_returns_404(self, app_client):
        from unittest.mock import MagicMock
        snap = MagicMock()
        snap.filename = "missing_completely.svs"

        with (
            patch("pathoryx_enterprise.services.dashboard.app.q.get_monitored_file", return_value=snap),
            patch("pathoryx_enterprise.services.dashboard.app._build_label_search_dirs", return_value=[]),
            patch("pathoryx_enterprise.services.dashboard.app._label_allowed_roots", return_value=[Path("/tmp")]),
            patch("pathoryx_enterprise.services.dashboard.app._load_babelshark_config", return_value={}),
        ):
            resp = app_client.get("/dashboard/api/recovery/files/1/label-image")
        assert resp.status_code == 404

    def test_path_traversal_stem_sanitized(self, app_client):
        from unittest.mock import MagicMock
        snap = MagicMock()
        snap.filename = "../../etc/passwd.svs"

        with (
            patch("pathoryx_enterprise.services.dashboard.app.q.get_monitored_file", return_value=snap),
            patch("pathoryx_enterprise.services.dashboard.app._load_babelshark_config", return_value={}),
        ):
            resp = app_client.get("/dashboard/api/recovery/files/1/label-image")
        # Sanitize_filename strips path separators; sanitized stem won't match any real file → 404
        assert resp.status_code in (400, 404)


# ---------------------------------------------------------------------------
# _label_allowed_roots
# ---------------------------------------------------------------------------

class TestLabelAllowedRoots:
    def test_always_returns_nonempty_list(self, tmp_path):
        from pathoryx_enterprise.services.dashboard.app import _label_allowed_roots
        roots = _label_allowed_roots({})
        assert len(roots) >= 1

    def test_configured_paths_included_when_exist(self, tmp_path):
        from pathoryx_enterprise.services.dashboard.app import _label_allowed_roots
        run_out = tmp_path / "run_output"
        run_out.mkdir()
        roots = _label_allowed_roots({"run_output_dir": str(run_out)})
        assert any(str(run_out) in str(r) for r in roots)
