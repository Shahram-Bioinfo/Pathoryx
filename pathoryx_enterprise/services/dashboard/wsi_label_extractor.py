"""
Dashboard WSI label image extractor.

Extracts the embedded label/associated image from a WSI file using OpenSlide
and saves it as a PNG cache file.

Key design decisions:
  - Only reads associated_images (tiny embedded JPEG/PNG, not the full scan).
    A 10 GB SVS file has a ~50 KB label image; opening it with OpenSlide to
    read associated_images loads only the TIFF directory, not the tile data.
  - Image preference order: label > macro > thumbnail.
    'label' is the physical barcode/stain label photo.
    'macro' is a low-res overview of the slide region.
  - Atomic write: {stem}.png.tmp → os.replace → {stem}.png
  - All failures are logged and return None — caller always degrades gracefully.
  - Path safety is the caller's responsibility (validate before calling).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Preferred associated image keys in priority order (case-insensitive match)
_ASSOCIATED_PRIORITY = ("label", "macro", "thumbnail")


def extract_wsi_label_to_cache(
    wsi_path: Path,
    cache_dir: Path,
    stem: str,
) -> Optional[Path]:
    """
    Extract the embedded label image from *wsi_path* and save to *cache_dir/{stem}.png*.

    Uses OpenSlide's associated_images — reads only the small label/macro JPEG
    embedded in the TIFF/SVS/NDPI container.  Never reads slide tile data.

    Returns the Path of the cached PNG on success, None on any failure.

    Args:
        wsi_path:  Absolute path to the WSI file.  Caller must have validated
                   this is within an allowed root before calling.
        cache_dir: Directory where the PNG will be written.
        stem:      Filename stem (no extension) used for the output file.
    """
    try:
        import openslide  # system library — optional at dashboard runtime
    except ImportError:
        logger.debug("openslide_not_available_skipping_wsi_extraction")
        return None

    try:
        from PIL import Image  # Pillow — optional at dashboard runtime
    except ImportError:
        logger.debug("pillow_not_available_skipping_wsi_extraction")
        return None

    if not wsi_path.exists():
        logger.debug("wsi not found: %s", wsi_path)
        return None

    dest = cache_dir / f"{stem}.png"
    tmp  = cache_dir / f"{stem}.png.tmp"

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)

        slide  = openslide.OpenSlide(str(wsi_path))
        assoc  = slide.associated_images

        # Pick the best available associated image
        img: Optional[Image.Image] = None
        source: Optional[str] = None
        for key in _ASSOCIATED_PRIORITY:
            match = next((k for k in assoc if k.lower() == key), None)
            if match:
                img    = assoc[match].copy()   # copy before closing
                source = match
                break

        slide.close()

        if img is None:
            logger.debug("no associated images in WSI: %s", wsi_path)
            return None

        # Convert RGBA → RGB (paste over white background to preserve clarity)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Atomic write
        img.save(tmp, format="PNG")
        os.replace(tmp, dest)

        logger.info(
            "wsi label extracted: source=%s size=%s -> %s", source, img.size, dest
        )
        return dest

    except Exception as exc:
        logger.warning("wsi label extraction failed for %s: %s", wsi_path, exc)
        # Clean up any partial temp file
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return None
