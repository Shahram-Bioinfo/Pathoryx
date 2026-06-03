"""
Fast file fingerprinting and deterministic artifact ID generation.

FastFingerprint uses only filesystem metadata (no file read) and is suitable
for quick duplicate detection. It must be backed by a DB-level unique constraint
to be safe under concurrent access — see db/repositories/file_record.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FastFingerprint:
    canonical_path: str
    original_filename: str
    file_size: int
    file_mtime_ns: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "original_filename": self.original_filename,
            "file_size": self.file_size,
            "file_mtime_ns": self.file_mtime_ns,
        }

    def idempotency_key(self) -> str:
        """Stable key for deduplication. Combines all four fields."""
        return deterministic_artifact_id(
            self.canonical_path,
            self.original_filename,
            self.file_size,
            self.file_mtime_ns,
        )


def build_fast_fingerprint(file_path: Path | str) -> FastFingerprint:
    """
    Build a FastFingerprint from filesystem metadata only.
    No file content is read. Raises FileNotFoundError if path absent.
    """
    path = Path(file_path).resolve()
    stat = path.stat()
    return FastFingerprint(
        canonical_path=str(path),
        original_filename=path.name,
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
    )


def deterministic_artifact_id(*parts: Any) -> str:
    """
    Generate a stable UUID5 from an ordered set of parts.
    Identical parts always produce the same ID. Used for idempotency keys,
    global_artifact_id, and global_run_id generation.
    """
    seed = "|".join(str(p) for p in parts if p is not None)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def new_artifact_id() -> str:
    """Generate a fresh random UUID4 for new artifacts."""
    return str(uuid.uuid4())
