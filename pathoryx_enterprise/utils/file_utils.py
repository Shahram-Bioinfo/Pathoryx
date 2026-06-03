"""
Safe filesystem utilities: atomic copy/move, directory sizing, stability checks.

All operations are designed for large WSI files (2–10 GB).
No operation reads file content unless explicitly required.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional


def safe_size(path: Path) -> Optional[int]:
    """
    Return the size in bytes of a file or directory.
    For directories, sums only regular files (no symlinks followed).
    Streaming iteration — does not load paths into memory all at once.
    Returns None if the path does not exist.
    """
    if not path.exists():
        return None
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return None
    # Directory: streaming sum
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    except PermissionError:
        pass
    return total


def atomic_copy(src: Path, dest: Path) -> Path:
    """
    Copy src to dest atomically using a temp file in the same directory.
    If src is a directory, copies the entire tree.
    Returns the final destination path.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
        return dest

    tmp_path = dest.parent / f".{dest.name}.{os.getpid()}.tmp"
    try:
        shutil.copy2(str(src), str(tmp_path))
        tmp_path.rename(dest)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    return dest


def atomic_move(src: Path, dest: Path) -> Path:
    """
    Move src to dest. Tries os.rename() first (same filesystem, instant).
    Falls back to copy + delete if cross-device.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dest)
    except OSError:
        # Cross-device move: copy then remove
        atomic_copy(src, dest)
        if src.is_dir():
            shutil.rmtree(str(src))
        else:
            src.unlink()
    return dest


def is_file_stable(path: Path, stable_after_seconds: float) -> bool:
    """
    Return True if the file has not been modified for stable_after_seconds.
    This check does NOT sleep — callers are responsible for their own poll loop.
    For a directory, uses the most recent mtime among all children.
    """
    if not path.exists():
        return False
    try:
        if path.is_file():
            mtime = path.stat().st_mtime
        else:
            # Directory: find latest mtime across all children
            try:
                mtime = max(
                    (p.stat().st_mtime for p in path.rglob("*") if p.is_file()),
                    default=path.stat().st_mtime,
                )
            except PermissionError:
                return False
        return (time.time() - mtime) >= stable_after_seconds
    except OSError:
        return False


def unique_dest(base: Path) -> Path:
    """
    Return base if it does not exist, else base_1, base_2, … until a free name is found.
    Thread-safety note: there is a TOCTOU window between check and creation.
    Callers that need strict uniqueness should use a DB-level constraint.
    """
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    parent = base.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
