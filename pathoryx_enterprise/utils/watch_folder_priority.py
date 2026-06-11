"""
Watch folder priority resolver.

Resolves upload priority for a file path based on configured watch folders.
Lower priority number = higher urgency.

Priority model:
  0 = UPLOAD_NEXT — operator-flagged "jump the queue"
  1 = HIGH        — urgent / high-priority watch folder or manual operator flag
  5 = NORMAL      — default for all files

Rules:
  - If multiple watch folders match, the most specific (longest) path wins.
  - Recursive: files in sub-directories inherit their parent folder's priority.
  - Unknown / unmatched folders default to priority 5 (NORMAL).
  - File-level (operator-set) priority always overrides folder-level priority.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


VALID_PRIORITIES = frozenset({0, 1, 5})
DEFAULT_PRIORITY = 5
HIGH_PRIORITY = 1


@dataclass(frozen=True)
class WatchFolderEntry:
    path: str
    priority: int = DEFAULT_PRIORITY
    label: str = ""

    def __post_init__(self) -> None:
        if self.priority not in VALID_PRIORITIES:
            raise ValueError(
                f"Watch folder '{self.path}' has invalid priority {self.priority}. "
                f"Allowed: {sorted(VALID_PRIORITIES)}"
            )


@dataclass(frozen=True)
class PriorityResolution:
    priority: int
    priority_source: str          # 'watch_folder' | 'default'
    watch_folder_path: Optional[str] = None
    watch_folder_label: Optional[str] = None


class WatchFolderPriorityResolver:
    """
    Resolves effective priority for a file from a list of configured watch folders.

    Thread-safe (immutable after construction).
    """

    def __init__(self, entries: list[WatchFolderEntry]) -> None:
        # Sort by path length descending so longest (most specific) match wins
        self._entries = sorted(entries, key=lambda e: len(e.path), reverse=True)

    def resolve(self, file_path: str) -> PriorityResolution:
        """
        Return the priority resolution for *file_path*.

        Checks each watch folder in specificity order (longest prefix first).
        Returns default resolution if nothing matches.
        """
        if not file_path:
            return PriorityResolution(
                priority=DEFAULT_PRIORITY,
                priority_source="default",
            )

        try:
            norm = Path(file_path).resolve()
        except Exception:
            norm = Path(file_path)

        for entry in self._entries:
            try:
                folder = Path(entry.path).resolve()
            except Exception:
                folder = Path(entry.path)
            try:
                norm.relative_to(folder)
                # File is under this watch folder
                return PriorityResolution(
                    priority=entry.priority,
                    priority_source="watch_folder",
                    watch_folder_path=entry.path,
                    watch_folder_label=entry.label or entry.path,
                )
            except ValueError:
                continue  # Not under this folder

        return PriorityResolution(
            priority=DEFAULT_PRIORITY,
            priority_source="default",
        )


def build_resolver_from_config(
    watch_folders_cfg: list[dict],
    *,
    fallback_watch_dir: Optional[str] = None,
) -> WatchFolderPriorityResolver:
    """
    Build a resolver from the babelshark_config watch_folders list.

    Each entry may have:
      path: str                (required)
      high_priority: bool      (preferred; true → priority 1/HIGH)
      priority: int            (fallback; must be in {0, 1, 5})
      label: str               (optional)

    If watch_folders_cfg is empty and fallback_watch_dir is given, creates a
    single default-priority entry for the legacy watch_dir path.
    """
    entries: list[WatchFolderEntry] = []

    for cfg in watch_folders_cfg:
        # Prefer high_priority bool flag; fall back to explicit priority int.
        if "high_priority" in cfg:
            priority = HIGH_PRIORITY if cfg["high_priority"] else DEFAULT_PRIORITY
        else:
            raw_priority = cfg.get("priority", DEFAULT_PRIORITY)
            try:
                priority = int(raw_priority)
            except (TypeError, ValueError):
                priority = DEFAULT_PRIORITY
            if priority not in VALID_PRIORITIES:
                priority = DEFAULT_PRIORITY

        entries.append(WatchFolderEntry(
            path=str(cfg["path"]),
            priority=priority,
            label=str(cfg.get("label", "") or ""),
        ))

    if not entries and fallback_watch_dir:
        entries.append(WatchFolderEntry(
            path=fallback_watch_dir,
            priority=DEFAULT_PRIORITY,
            label="",
        ))

    return WatchFolderPriorityResolver(entries)
