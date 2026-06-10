from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from pathoryx_enterprise.services.qc.engine.config import AppConfig

if TYPE_CHECKING:
    # Import for type-checker only; actual runtime import is deferred so that
    # Windows DLL registration can happen before the openslide C extension loads.
    from openslide import OpenSlide


def _get_openslide_class() -> Any:
    """Return the OpenSlide class, importing after DLL registration."""
    try:
        from openslide import OpenSlide as _OpenSlide
        return _OpenSlide
    except ImportError as exc:
        raise ImportError(
            "openslide-python is not installed. "
            "Install with: pip install openslide-python\n"
            "On Windows, also set OPENSLIDE_DLL_PATH to the OpenSlide bin\\ directory."
        ) from exc


class ThumbnailService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def open_slide(self, wsi_path: str | Path) -> OpenSlide:
        OpenSlide = _get_openslide_class()
        return OpenSlide(str(wsi_path))

    def get_thumbnail(self, slide: OpenSlide) -> np.ndarray:
        level = min(2, slide.level_count - 1)
        thumb = slide.read_region((0, 0), level, slide.level_dimensions[level]).convert("RGB")
        return np.array(thumb)

    def get_thumbnail_2048(self, slide: OpenSlide) -> np.ndarray:
        level = min(2, slide.level_count - 1)
        w, h = slide.level_dimensions[level]
        img = slide.read_region((0, 0), level, (w, h)).convert("RGB")
        scale = 2048 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return np.array(img)
