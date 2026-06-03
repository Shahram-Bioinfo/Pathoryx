"""
Secure path validation — prevents path traversal attacks.

All incoming file paths from watchers, configs, or external triggers
must be validated before use. Symlinks are resolved to their real path.
"""
from __future__ import annotations

from pathlib import Path


class PathTraversalError(ValueError):
    """Raised when a path resolves outside the permitted root(s)."""


def validate_path_under_roots(
    path: Path | str,
    allowed_roots: list[str | Path],
    *,
    must_exist: bool = False,
) -> Path:
    """
    Resolve `path` and confirm it falls under at least one of `allowed_roots`.

    Args:
        path: The path to validate. Symlinks are followed (resolve()).
        allowed_roots: One or more root directories the path must live under.
        must_exist: If True, raise FileNotFoundError if the path does not exist.

    Returns:
        The resolved absolute Path.

    Raises:
        PathTraversalError: If the path escapes all allowed roots.
        FileNotFoundError: If must_exist=True and path does not exist.
        ValueError: If allowed_roots is empty.
    """
    if not allowed_roots:
        raise ValueError("allowed_roots must not be empty.")

    resolved = Path(path).resolve()

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Path does not exist: {resolved}")

    for root in allowed_roots:
        root_resolved = Path(root).resolve()
        try:
            resolved.relative_to(root_resolved)
            return resolved  # within this root — safe
        except ValueError:
            continue

    raise PathTraversalError(
        f"Path {resolved!r} is outside all allowed roots: "
        + ", ".join(str(Path(r).resolve()) for r in allowed_roots)
    )


def is_path_safe(
    path: Path | str,
    allowed_roots: list[str | Path],
) -> bool:
    """Non-raising variant of validate_path_under_roots. Returns True/False."""
    try:
        validate_path_under_roots(path, allowed_roots)
        return True
    except (PathTraversalError, ValueError, FileNotFoundError):
        return False


def sanitize_filename(name: str) -> str:
    """
    Strip path separators and null bytes from a bare filename.
    Does NOT accept paths — only filenames (no slashes).
    """
    cleaned = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    return cleaned.strip(". ")
