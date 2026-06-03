from __future__ import annotations

from pathlib import Path

import cv2
from PIL import Image


class VisualizationService:
    @staticmethod
    def save_rgb(path: str | Path, image_np) -> str:
        path = str(path)
        cv2.imwrite(path, cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR))
        return path

    @staticmethod
    def save_pil_jpeg(path: str | Path, image_np) -> str:
        path = str(path)
        Image.fromarray(image_np).save(path, format="JPEG", quality=95)
        return path
