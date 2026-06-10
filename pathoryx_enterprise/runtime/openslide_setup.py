"""
Centralized OpenSlide runtime initialization.

Must be called BEFORE any ``import openslide`` statement — including
transitive imports triggered by loading service modules.

Priority order for the DLL directory:
  1. ``OPENSLIDE_DLL_PATH`` environment variable  (always wins)
  2. ``dll_path`` argument supplied by the caller  (from YAML config or CLI)

On Linux/macOS the call is a lightweight no-op.

Usage (service main.py)::

    from pathoryx_enterprise.runtime.openslide_setup import configure_openslide_runtime
    configure_openslide_runtime()          # env-var driven
    # — or —
    configure_openslide_runtime(dll_path)  # with config-based fallback

Usage (stage_runner / per-operation setup)::

    from pathoryx_enterprise.runtime.openslide_setup import configure_openslide_runtime
    dll_path = config.get("dll_paths", {}).get("openslide_dll")
    configure_openslide_runtime(dll_path)  # env var still takes priority
"""
from __future__ import annotations

import ctypes
import logging
import os
import platform
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ENV_VAR = "OPENSLIDE_DLL_PATH"

# Guard against calling add_dll_directory twice for the same directory.
_registered_dirs: set[str] = set()

# Known DLL names to attempt a ctypes preload (newer builds first).
_OPENSLIDE_DLL_CANDIDATES = [
    "libopenslide-1.dll",
    "libopenslide-0.dll",
]


def configure_openslide_runtime(dll_path: Optional[str] = None) -> bool:
    """
    Register the OpenSlide binary directory with the Windows DLL loader.

    On non-Windows platforms this function is a no-op that always returns True.
    On Windows it calls ``os.add_dll_directory()`` so that ``import openslide``
    finds its native libraries regardless of the ``PATH`` search order.

    Args:
        dll_path: Optional fallback path from a YAML config or CLI argument.
                  ``OPENSLIDE_DLL_PATH`` environment variable takes precedence
                  if it is set.

    Returns:
        True  — DLL registered successfully (or not needed on this platform).
        False — Windows, but the resolved directory does not exist or
                ``add_dll_directory`` raised an error.
    """
    if platform.system() != "Windows":
        logger.debug("openslide_setup: non-Windows platform — skipping DLL registration")
        return True

    env_path = os.environ.get(_ENV_VAR, "").strip()
    resolved_str = env_path or (dll_path.strip() if dll_path else "")

    if not resolved_str:
        logger.warning(
            "openslide_setup: no DLL path configured. "
            "Set OPENSLIDE_DLL_PATH to the OpenSlide bin\\ directory "
            "(e.g. D:\\openslide-bin-4.0.0.8-windows-x64\\bin) or add "
            "dll_paths.openslide_dll to your service YAML config."
        )
        return False

    dll_dir = Path(resolved_str).expanduser().resolve()

    if not dll_dir.is_dir():
        logger.error(
            "openslide_setup: directory not found: %s — "
            "check OPENSLIDE_DLL_PATH or dll_paths.openslide_dll in config",
            dll_dir,
        )
        return False

    source = f"env:{_ENV_VAR}" if env_path else "config"
    dll_dir_str = str(dll_dir)

    # Add to PATH so that ctypes fallback and child processes also see it.
    current_path = os.environ.get("PATH", "")
    if dll_dir_str not in current_path.split(os.pathsep):
        os.environ["PATH"] = dll_dir_str + os.pathsep + current_path

    # Register with the Windows DLL loader (Python 3.8+ / Windows only).
    if dll_dir_str not in _registered_dirs:
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(dll_dir_str)
                _registered_dirs.add(dll_dir_str)
                logger.info(
                    "openslide_setup: registered DLL directory [%s]: %s",
                    source,
                    dll_dir,
                )
            except OSError as exc:
                logger.error(
                    "openslide_setup: add_dll_directory(%s) failed: %s",
                    dll_dir,
                    exc,
                )
                return False
        else:
            # Python < 3.8 on Windows — PATH update above is the only option.
            logger.warning(
                "openslide_setup: os.add_dll_directory not available; "
                "relying on PATH update only (Python >= 3.8 recommended)"
            )

    # Attempt an explicit ctypes preload to ensure the shared library is
    # mapped into the process before Python's openslide extension loads it.
    for candidate in _OPENSLIDE_DLL_CANDIDATES:
        dll_file = dll_dir / candidate
        if dll_file.exists():
            try:
                ctypes.cdll.LoadLibrary(str(dll_file))
                logger.debug("openslide_setup: preloaded %s", dll_file.name)
                break
            except OSError as exc:
                logger.warning(
                    "openslide_setup: could not preload %s: %s",
                    dll_file.name,
                    exc,
                )

    return True
