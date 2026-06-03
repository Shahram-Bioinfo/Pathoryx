from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from openslide import OpenSlide

from pathoryx_enterprise.services.qc.engine.config import AppConfig


class ThumbnailService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def open_slide(self, wsi_path: str | Path) -> OpenSlide:
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
