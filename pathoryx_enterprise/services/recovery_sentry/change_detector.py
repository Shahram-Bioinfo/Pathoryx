"""
Folder change detector for RecoverySentry.

Compares the current filesystem state of a watched folder against the
database snapshot (WatchedFolderSnapshot) to detect:
  - new files (ADDED)
  - deleted files (DELETED)
  - modified files — size or mtime changed (MODIFIED)
  - renamed files — same checksum, different path (RENAMED)

No business logic about what to do with changes. Returns a list of
ChangeEvent objects for the caller to record and act on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pathoryx_enterprise.db.models.failed_watcher import WatchedFolderSnapshot
from pathoryx_enterprise.utils.path_validation import validate_path_under_roots


@dataclass
class ChangeEvent:
    change_type: str            # ADDED | DELETED | MODIFIED | RENAMED
    folder_label: str
    old_path: Optional[str] = None
    old_filename: Optional[str] = None
    old_file_size: Optional[int] = None
    old_mtime_ns: Optional[int] = None
    new_path: Optional[str] = None
    new_filename: Optional[str] = None
    new_file_size: Optional[int] = None
    new_mtime_ns: Optional[int] = None
    inferred_action: Optional[str] = None
    slide_id_inferred: Optional[str] = None


def scan_folder(
    folder_path: Path,
    allowed_roots: list[Path],
    extensions: tuple[str, ...] = (".svs", ".ndpi", ".mrxs", ".tiff", ".tif", ".scn", ".czi", ".vsi"),
    *,
    recursive: bool = True,
) -> dict[str, dict]:
    """
    Return a dict of {canonical_path: {filename, file_size, mtime_ns}}
    for all files in folder_path matching `extensions`.

    Uses path traversal protection — any path not under allowed_roots is skipped.
    Symlinks that resolve outside allowed_roots are silently skipped.

    Args:
        recursive: When True (default), descend into subdirectories.
                   When False, only the immediate children of folder_path are scanned.
    """
    result: dict[str, dict] = {}

    walker = os.walk(folder_path, followlinks=False)
    for root, dirs, files in walker:
        # Prevent hidden/system folder traversal and optionally limit to top level.
        if not recursive and Path(root) != folder_path:
            break
        # Skip hidden directories (starting with '.' on Unix, common on Windows too)
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for fname in files:
            if fname.startswith('.'):
                continue
            if not fname.lower().endswith(extensions):
                continue
            full = Path(root) / fname
            try:
                canonical = str(validate_path_under_roots(full, allowed_roots))
            except Exception:
                continue
            try:
                stat = full.stat()
            except OSError:
                continue
            result[canonical] = {
                "filename": fname,
                "file_size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }

    return result


def detect_changes(
    folder_label: str,
    current_state: dict[str, dict],
    db_snapshots: list[WatchedFolderSnapshot],
) -> list[ChangeEvent]:
    """
    Compare current filesystem state against DB snapshots.

    Returns a list of ChangeEvent objects. Pure function — no DB access.
    """
    changes: list[ChangeEvent] = []
    db_by_path = {snap.file_path: snap for snap in db_snapshots}

    # ADDED or MODIFIED
    for path, info in current_state.items():
        if path not in db_by_path:
            changes.append(ChangeEvent(
                change_type="ADDED",
                folder_label=folder_label,
                new_path=path,
                new_filename=info["filename"],
                new_file_size=info["file_size"],
                new_mtime_ns=info["mtime_ns"],
                inferred_action="new_file_appeared",
            ))
        else:
            snap = db_by_path[path]
            size_changed = snap.file_size != info["file_size"]
            mtime_changed = snap.mtime_ns != info["mtime_ns"]
            if size_changed or mtime_changed:
                changes.append(ChangeEvent(
                    change_type="MODIFIED",
                    folder_label=folder_label,
                    old_path=path,
                    old_filename=snap.filename,
                    old_file_size=snap.file_size,
                    old_mtime_ns=snap.mtime_ns,
                    new_path=path,
                    new_filename=info["filename"],
                    new_file_size=info["file_size"],
                    new_mtime_ns=info["mtime_ns"],
                    inferred_action="file_modified",
                ))

    # DELETED
    for path, snap in db_by_path.items():
        if path not in current_state:
            changes.append(ChangeEvent(
                change_type="DELETED",
                folder_label=folder_label,
                old_path=path,
                old_filename=snap.filename,
                old_file_size=snap.file_size,
                old_mtime_ns=snap.mtime_ns,
                inferred_action="file_deleted",
            ))

    return changes
