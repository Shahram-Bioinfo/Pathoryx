"""
WSI metadata timestamp extractor for RecoverySentry.

Extracts the scan timestamp from WSI file metadata using OpenSlide.
Supports: Aperio (.svs), Hamamatsu (.ndpi), generic TIFF.

This imports from BabelShark's slide_id_generator._scan_ts_iso_z_from_metadata
rather than duplicating the vendor-specific logic. We call _setup_openslide()
at service startup to ensure the openslide module is initialized.

Returns ISO-Z format: "2024-08-22T08:36:39Z"
Never generates fake timestamps. Returns None if no real timestamp can be found.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# We import BabelShark's battle-tested openslide extractor.
# The underscore prefix is a module-private convention; there is no public API here.
# We own the calling context (RecoverySentry) and accept this coupling intentionally.
try:
    from pathoryx_enterprise.services.babelshark.core.slide_id_generator import (  # noqa: PLC2701
        _scan_ts_iso_z_from_metadata as _bs_extract_ts,
        _setup_openslide as _bs_setup_openslide,
    )
    _BABELSHARK_AVAILABLE = True
except ImportError:
    _BABELSHARK_AVAILABLE = False
    _bs_extract_ts = None  # type: ignore[assignment]
    _bs_setup_openslide = None  # type: ignore[assignment]


def initialize_openslide(dll_path: Optional[str] = None) -> None:
    """Call at service startup to ensure openslide is loaded."""
    if _BABELSHARK_AVAILABLE and _bs_setup_openslide is not None:
        _bs_setup_openslide(dll_path)


def extract_scan_timestamp(
    file_path: Path,
    *,
    allow_filesystem_fallback: bool = False,
) -> Optional[str]:
    """
    Extract the scan timestamp from a WSI file.

    Returns ISO-Z string "YYYY-MM-DDTHH:MM:SSZ" or None.

    Args:
        file_path: Path to the WSI file.
        allow_filesystem_fallback: If True, falls back to file mtime when
            WSI metadata lacks a timestamp. Default False (safer — avoids
            recording inaccurate timestamps for recovered slides).

    Never raises. All errors are logged and None is returned.
    """
    if not file_path.exists():
        logger.warning("file_not_found_for_timestamp", path=str(file_path))
        return None

    # Primary: OpenSlide metadata (vendor-specific properties)
    if _BABELSHARK_AVAILABLE and _bs_extract_ts is not None:
        try:
            ts = _bs_extract_ts(file_path)
            if ts:
                logger.debug(
                    "timestamp_from_wsi_metadata",
                    path=str(file_path),
                    timestamp=ts,
                )
                return ts
        except Exception as exc:
            logger.warning(
                "wsi_metadata_extraction_failed",
                path=str(file_path),
                error=str(exc),
            )
    else:
        logger.warning("openslide_unavailable_cannot_extract_timestamp")

    # Filesystem fallback (explicitly opt-in)
    if allow_filesystem_fallback:
        try:
            import datetime as _dt
            mtime = file_path.stat().st_mtime
            dt = _dt.datetime.fromtimestamp(mtime, tz=_dt.timezone.utc)
            ts_fs = dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            logger.info(
                "timestamp_from_filesystem_fallback",
                path=str(file_path),
                timestamp=ts_fs,
            )
            return ts_fs
        except OSError as exc:
            logger.warning("filesystem_timestamp_failed", path=str(file_path), error=str(exc))

    return None


def compute_partial_sha256(file_path: Path, max_bytes: int = 4 * 1024 * 1024) -> Optional[str]:
    """
    Compute SHA-256 of the first max_bytes of a file.

    Used for fast duplicate detection without reading multi-GB WSI files fully.
    Returns None on any error.
    """
    try:
        h = hashlib.sha256()
        with file_path.open("rb") as fh:
            chunk = fh.read(max_bytes)
            h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
