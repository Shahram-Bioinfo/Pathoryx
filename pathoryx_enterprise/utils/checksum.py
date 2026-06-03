"""
Streaming SHA-256 checksum utilities.

Never load the entire file into memory. WSI files are 2–10 GB.
All reads are chunked to avoid OOM on large inputs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# 4 MB chunks — large enough for throughput, small enough to avoid memory pressure.
_CHUNK_BYTES = 4 * 1024 * 1024


def compute_sha256(path: Path, chunk_size: int = _CHUNK_BYTES) -> str:
    """
    Stream-compute the SHA-256 hex digest of a file.
    Raises FileNotFoundError if the path does not exist.
    Raises IsADirectoryError if path is a directory (use compute_sha256_dir).
    """
    if not path.exists():
        raise FileNotFoundError(f"Cannot checksum non-existent path: {path}")
    if path.is_dir():
        raise IsADirectoryError(
            f"Cannot checksum a directory directly. Use compute_sha256_dir(): {path}"
        )

    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_sha256_dir(path: Path, chunk_size: int = _CHUNK_BYTES) -> str:
    """
    Compute a stable SHA-256 over the sorted contents of a directory.
    Hashes are chained: each file's (relative_path + content_hash) is fed
    into a top-level hasher in lexicographic path order.
    """
    if not path.is_dir():
        raise NotADirectoryError(f"Expected a directory: {path}")

    top = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        rel = child.relative_to(path).as_posix()
        top.update(rel.encode())
        top.update(compute_sha256(child, chunk_size).encode())
    return top.hexdigest()


def compute_sha256_or_none(path: Path) -> str | None:
    """Return SHA-256 of a file, or None if the path does not exist or is a directory."""
    try:
        if path.is_file():
            return compute_sha256(path)
        return None
    except OSError:
        return None
