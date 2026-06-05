"""
Unit tests for the WSI label extractor and updated label_image endpoint.

Covers:
  A. extract_wsi_label_to_cache — label, macro, thumbnail fallback
  B. extract_wsi_label_to_cache — RGBA→RGB conversion
  C. extract_wsi_label_to_cache — graceful None returns (no images, missing file, errors)
  D. extract_wsi_label_to_cache — openslide unavailable → None
  E. extract_wsi_label_to_cache — atomic write semantics
  F. label_image endpoint — pre-existing crop served, extractor never called
  G. label_image endpoint — WSI extraction triggered when no crop
  H. label_image endpoint — 404 when both crop and WSI fail
  I. label_image endpoint — unsafe WSI path not opened (path traversal guard)
  J. label_image endpoint — X-Label-Source header
  K. _label_cache_dir — fallback and configured path
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient  # noqa: E402

from pathoryx_enterprise.services.dashboard.app import create_app, get_db  # noqa: E402

_APP_MODULE     = "pathoryx_enterprise.services.dashboard.app"
_EXTRACTOR_MOD  = "pathoryx_enterprise.services.dashboard.wsi_label_extractor"


@pytest.fixture(scope="module")
def client():
    app = create_app()
    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _make_snap(filename: str = "N24-3625-T.svs", file_path: str | None = None) -> MagicMock:
    s = MagicMock()
    s.filename = filename
    s.file_path = file_path or f"/data/failed/{filename}"
    return s


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


# ---------------------------------------------------------------------------
# A-E. extract_wsi_label_to_cache unit tests
# ---------------------------------------------------------------------------

class TestExtractWsiLabelToCache:
    """Tests for the extraction function itself (openslide mocked via sys.modules)."""

    def _call(self, wsi_path: Path, cache_dir: Path, stem: str):
        from pathoryx_enterprise.services.dashboard.wsi_label_extractor import (
            extract_wsi_label_to_cache,
        )
        return extract_wsi_label_to_cache(wsi_path, cache_dir, stem)

    def _mock_openslide(self, associated: dict) -> tuple:
        """Return (mock_openslide_module, mock_slide) for patching."""
        from PIL import Image as PILImage

        mock_slide = MagicMock()
        mock_slide.associated_images = associated

        mock_os_module = MagicMock()
        mock_os_module.OpenSlide.return_value = mock_slide
        return mock_os_module, mock_slide

    def test_label_image_extracted_and_cached(self, tmp_path):
        from PIL import Image as PILImage

        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        label_img = PILImage.new("RGB", (100, 80), color=(200, 100, 50))
        mock_os, _ = self._mock_openslide({"label": label_img})

        with patch.dict("sys.modules", {"openslide": mock_os}):
            result = self._call(wsi, tmp_path / "cache", "slide")

        assert result is not None
        assert result.name == "slide.png"
        assert result.exists()

    def test_macro_fallback_when_no_label(self, tmp_path):
        from PIL import Image as PILImage

        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        mock_os, _ = self._mock_openslide({"macro": PILImage.new("RGB", (400, 200))})

        with patch.dict("sys.modules", {"openslide": mock_os}):
            result = self._call(wsi, tmp_path / "cache", "slide")

        assert result is not None
        assert result.exists()

    def test_thumbnail_fallback_when_only_thumbnail(self, tmp_path):
        from PIL import Image as PILImage

        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        mock_os, _ = self._mock_openslide({"thumbnail": PILImage.new("RGB", (100, 30))})

        with patch.dict("sys.modules", {"openslide": mock_os}):
            result = self._call(wsi, tmp_path / "cache", "slide")

        assert result is not None

    def test_rgba_converted_to_rgb(self, tmp_path):
        from PIL import Image as PILImage

        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        rgba = PILImage.new("RGBA", (100, 80), (200, 100, 50, 128))
        mock_os, _ = self._mock_openslide({"label": rgba})

        with patch.dict("sys.modules", {"openslide": mock_os}):
            result = self._call(wsi, tmp_path / "cache", "slide")

        assert result is not None
        saved = PILImage.open(result)
        assert saved.mode == "RGB"

    def test_returns_none_when_no_associated_images(self, tmp_path):
        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        mock_os, _ = self._mock_openslide({})

        with patch.dict("sys.modules", {"openslide": mock_os}):
            result = self._call(wsi, tmp_path / "cache", "slide")

        assert result is None

    def test_returns_none_when_openslide_unavailable(self, tmp_path):
        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        with patch.dict("sys.modules", {"openslide": None}):
            result = self._call(wsi, tmp_path / "cache", "slide")
        assert result is None

    def test_returns_none_when_file_not_found(self, tmp_path):
        result = self._call(tmp_path / "ghost.svs", tmp_path / "cache", "ghost")
        assert result is None

    def test_returns_none_on_openslide_exception(self, tmp_path):
        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        mock_os = MagicMock()
        mock_os.OpenSlide.side_effect = Exception("corrupt file")

        with patch.dict("sys.modules", {"openslide": mock_os}):
            result = self._call(wsi, tmp_path / "cache", "slide")

        assert result is None

    def test_no_tmp_file_left_after_failure(self, tmp_path):
        """Partial .tmp file must be cleaned up on failure."""
        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        mock_os = MagicMock()
        mock_os.OpenSlide.side_effect = RuntimeError("fail")

        with patch.dict("sys.modules", {"openslide": mock_os}):
            self._call(wsi, tmp_path / "cache", "slide")

        cache = tmp_path / "cache"
        if cache.exists():
            assert list(cache.glob("*.tmp")) == []

    def test_atomic_write_uses_replace(self, tmp_path):
        """Output must be written via os.replace (atomic on POSIX)."""
        from PIL import Image as PILImage

        wsi = tmp_path / "slide.svs"
        wsi.write_bytes(b"fake")
        mock_os, _ = self._mock_openslide({"label": PILImage.new("RGB", (10, 10))})

        replaced: list[tuple[str, str]] = []
        real_replace = os.replace

        def capture(src, dst):
            replaced.append((str(src), str(dst)))
            real_replace(src, dst)

        with (
            patch.dict("sys.modules", {"openslide": mock_os}),
            patch(f"{_EXTRACTOR_MOD}.os.replace", side_effect=capture),
        ):
            self._call(wsi, tmp_path / "cache", "slide")

        assert len(replaced) == 1
        assert replaced[0][1].endswith("slide.png")
        assert ".tmp" in replaced[0][0]


# ---------------------------------------------------------------------------
# F. Endpoint: pre-existing crop served — extractor NOT called
# ---------------------------------------------------------------------------

class TestLabelImageEndpointWithCrop:

    def test_existing_crop_served_wsi_not_opened(self, tmp_path, client):
        snap = _make_snap()
        img_dir = tmp_path / "crops"
        img_dir.mkdir()
        (img_dir / "N24-3625-T.png").write_bytes(_png_bytes())

        with (
            patch(f"{_APP_MODULE}.q.get_monitored_file", return_value=snap),
            patch(f"{_APP_MODULE}._build_label_search_dirs", return_value=[img_dir]),
            patch(f"{_APP_MODULE}._label_allowed_roots", return_value=[tmp_path]),
            patch(f"{_APP_MODULE}._load_babelshark_config", return_value={}),
            # Patch at the source module so the local import inside the endpoint sees the mock
            patch(f"{_EXTRACTOR_MOD}.extract_wsi_label_to_cache") as mock_extract,
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")

        assert resp.status_code == 200
        mock_extract.assert_not_called()

    def test_existing_crop_has_label_crop_header(self, tmp_path, client):
        snap = _make_snap()
        img_dir = tmp_path / "crops2"
        img_dir.mkdir()
        (img_dir / "N24-3625-T.png").write_bytes(_png_bytes())

        with (
            patch(f"{_APP_MODULE}.q.get_monitored_file", return_value=snap),
            patch(f"{_APP_MODULE}._build_label_search_dirs", return_value=[img_dir]),
            patch(f"{_APP_MODULE}._label_allowed_roots", return_value=[tmp_path]),
            patch(f"{_APP_MODULE}._load_babelshark_config", return_value={}),
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")

        assert resp.headers.get("x-label-source") == "label_crop"


# ---------------------------------------------------------------------------
# G. Endpoint: WSI extraction triggered when no pre-existing crop
# ---------------------------------------------------------------------------

class TestLabelImageEndpointWsiExtraction:

    def test_extractor_called_when_no_crop(self, tmp_path, client):
        wsi_file = tmp_path / "N24-3625-T.svs"
        wsi_file.write_bytes(b"fake wsi")
        snap = _make_snap(file_path=str(wsi_file))

        extracted_png = tmp_path / "N24-3625-T.png"
        extracted_png.write_bytes(_png_bytes())

        with (
            patch(f"{_APP_MODULE}.q.get_monitored_file", return_value=snap),
            patch(f"{_APP_MODULE}._build_label_search_dirs", return_value=[]),
            patch(f"{_APP_MODULE}._label_allowed_roots", return_value=[tmp_path]),
            patch(f"{_APP_MODULE}._load_babelshark_config", return_value={}),
            patch(f"{_APP_MODULE}._label_cache_dir", return_value=tmp_path),
            patch(f"{_EXTRACTOR_MOD}.extract_wsi_label_to_cache",
                  return_value=extracted_png) as mock_extract,
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")

        assert resp.status_code == 200
        mock_extract.assert_called_once()
        call_args = mock_extract.call_args[0]
        assert call_args[2] == "N24-3625-T"   # stem

    def test_wsi_embedded_source_header_set(self, tmp_path, client):
        wsi_file = tmp_path / "N24-3625-T.svs"
        wsi_file.write_bytes(b"fake")
        snap = _make_snap(file_path=str(wsi_file))
        extracted_png = tmp_path / "N24-3625-T.png"
        extracted_png.write_bytes(_png_bytes())

        with (
            patch(f"{_APP_MODULE}.q.get_monitored_file", return_value=snap),
            patch(f"{_APP_MODULE}._build_label_search_dirs", return_value=[]),
            patch(f"{_APP_MODULE}._label_allowed_roots", return_value=[tmp_path]),
            patch(f"{_APP_MODULE}._load_babelshark_config", return_value={}),
            patch(f"{_APP_MODULE}._label_cache_dir", return_value=tmp_path),
            patch(f"{_EXTRACTOR_MOD}.extract_wsi_label_to_cache", return_value=extracted_png),
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")

        assert resp.headers.get("x-label-source") == "wsi_embedded"


# ---------------------------------------------------------------------------
# H. Endpoint: 404 when both crop and WSI extraction fail
# ---------------------------------------------------------------------------

class TestLabelImageEndpoint404:

    def test_404_when_crop_missing_and_extraction_fails(self, tmp_path, client):
        wsi_file = tmp_path / "N24-3625-T.svs"
        wsi_file.write_bytes(b"fake")
        snap = _make_snap(file_path=str(wsi_file))

        with (
            patch(f"{_APP_MODULE}.q.get_monitored_file", return_value=snap),
            patch(f"{_APP_MODULE}._build_label_search_dirs", return_value=[]),
            patch(f"{_APP_MODULE}._label_allowed_roots", return_value=[tmp_path]),
            patch(f"{_APP_MODULE}._load_babelshark_config", return_value={}),
            patch(f"{_APP_MODULE}._label_cache_dir", return_value=tmp_path),
            patch(f"{_EXTRACTOR_MOD}.extract_wsi_label_to_cache", return_value=None),
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")

        assert resp.status_code == 404
        assert "label image" in resp.json()["detail"].lower()

    def test_404_when_wsi_file_does_not_exist(self, tmp_path, client):
        snap = _make_snap(file_path=str(tmp_path / "ghost.svs"))

        with (
            patch(f"{_APP_MODULE}.q.get_monitored_file", return_value=snap),
            patch(f"{_APP_MODULE}._build_label_search_dirs", return_value=[]),
            patch(f"{_APP_MODULE}._label_allowed_roots", return_value=[tmp_path]),
            patch(f"{_APP_MODULE}._load_babelshark_config", return_value={}),
            patch(f"{_APP_MODULE}._label_cache_dir", return_value=tmp_path),
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# I. Endpoint: unsafe WSI path blocked by path validation
# ---------------------------------------------------------------------------

class TestLabelImageUnsafeWsiPath:

    def test_wsi_outside_allowed_roots_not_opened(self, tmp_path, client):
        # WSI is at /etc/sensitive/... which is NOT under tmp_path
        snap = _make_snap(file_path="/etc/sensitive/slide.svs")

        with (
            patch(f"{_APP_MODULE}.q.get_monitored_file", return_value=snap),
            patch(f"{_APP_MODULE}._build_label_search_dirs", return_value=[]),
            patch(f"{_APP_MODULE}._label_allowed_roots", return_value=[tmp_path]),
            patch(f"{_APP_MODULE}._load_babelshark_config", return_value={}),
            patch(f"{_APP_MODULE}._label_cache_dir", return_value=tmp_path),
            patch(f"{_EXTRACTOR_MOD}.extract_wsi_label_to_cache") as mock_extract,
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")

        mock_extract.assert_not_called()
        assert resp.status_code == 404

    def test_path_traversal_stem_rejected(self, tmp_path, client):
        snap = _make_snap(filename="../../etc/passwd.svs")
        with (
            patch(f"{_APP_MODULE}.q.get_monitored_file", return_value=snap),
            patch(f"{_APP_MODULE}._load_babelshark_config", return_value={}),
        ):
            resp = client.get("/dashboard/api/recovery/files/1/label-image")
        assert resp.status_code in (400, 404)


# ---------------------------------------------------------------------------
# K. _label_cache_dir
# ---------------------------------------------------------------------------

class TestLabelCacheDir:

    def test_falls_back_to_data_label_crops(self):
        from pathoryx_enterprise.services.dashboard.app import _label_cache_dir
        result = _label_cache_dir({})
        assert "label_crops" in str(result)

    def test_uses_configured_label_crops_dir_when_parent_exists(self, tmp_path):
        from pathoryx_enterprise.services.dashboard.app import _label_cache_dir
        crops = tmp_path / "my_crops"
        result = _label_cache_dir({"label_crops_dir": str(crops)})
        assert result == crops
