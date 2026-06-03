"""
FileRecord repository with safe duplicate detection.

get_or_create_safe() uses a two-step strategy:
  1. Optimistic: try to fetch an existing record by canonical_path
  2. If not found: INSERT with the unique constraint as the safety net

Under concurrent workers, if two workers both reach step 2 simultaneously,
one will succeed and the other will get a unique-constraint violation.
The loser retries the fetch and returns the winner's row.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from pathoryx_enterprise.db.models.core import FileRecord, MetadataSnapshot
from pathoryx_enterprise.db.repositories.base import BaseRepository
from pathoryx_enterprise.utils.datetime_utils import utc_now


class FileRecordRepository(BaseRepository):

    def get_by_canonical_path(
        self,
        canonical_path: str,
        *,
        lock: bool = False,
    ) -> Optional[FileRecord]:
        """
        Fetch a FileRecord by canonical_path.
        If lock=True, acquires a FOR UPDATE row lock.
        Use lock=True during duplicate-check + insert sequences.
        """
        stmt = select(FileRecord).where(FileRecord.canonical_path == canonical_path)
        if lock:
            stmt = stmt.with_for_update()
        return self._session.execute(stmt).scalar_one_or_none()

    def get_by_global_artifact_id(self, global_artifact_id: str) -> Optional[FileRecord]:
        return self._session.execute(
            select(FileRecord).where(FileRecord.global_artifact_id == global_artifact_id)
        ).scalar_one_or_none()

    def get_or_create_safe(
        self,
        canonical_path: str,
        *,
        defaults: Optional[dict] = None,
    ) -> tuple[FileRecord, bool]:
        """
        Fetch an existing FileRecord by canonical_path or create a new one.
        Returns (record, created) where created=True if a new row was inserted.

        Thread-safe via canonical_path unique constraint.
        defaults: field values applied only when creating a new row.
        """
        # 1. Try to find existing
        existing = self.get_by_canonical_path(canonical_path, lock=True)
        if existing is not None:
            return existing, False

        # 2. Insert new
        fields = dict(defaults or {})
        fields["canonical_path"] = canonical_path
        record = FileRecord(**fields)
        self._session.add(record)
        try:
            self._session.flush()
            return record, True
        except IntegrityError:
            self._session.rollback()
            # Another worker inserted concurrently — fetch their row
            existing = self.get_by_canonical_path(canonical_path)
            assert existing is not None
            return existing, False

    def transition_status(
        self,
        record: FileRecord,
        new_status: str,
        *,
        allowed_from: Optional[list[str]] = None,
    ) -> None:
        """
        Transition FileRecord.status with optional guard on current status.
        Raises ValueError if allowed_from is specified and current status is not in it.
        """
        if allowed_from and record.status not in allowed_from:
            raise ValueError(
                f"Invalid status transition: {record.status!r} → {new_status!r}. "
                f"Expected current status in {allowed_from!r}."
            )
        record.status = new_status
        self._session.flush()

    def create_metadata_snapshot(
        self,
        record: FileRecord,
        payload: dict,
        source_service: str,
    ) -> MetadataSnapshot:
        """
        Create a new immutable metadata snapshot for this FileRecord.
        Increments snapshot_version and links previous_snapshot_id.
        """
        # Determine next version
        from sqlalchemy import func
        last_version: int = self._session.execute(
            select(func.max(MetadataSnapshot.snapshot_version)).where(
                MetadataSnapshot.global_artifact_id == record.global_artifact_id
            )
        ).scalar_one() or 0

        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()

        snapshot = MetadataSnapshot(
            global_artifact_id=record.global_artifact_id or "",
            snapshot_version=last_version + 1,
            source_service=source_service,
            snapshot_payload=payload,
            payload_hash=payload_hash,
            previous_snapshot_id=record.current_snapshot_id,
            created_at=utc_now(),
        )
        self._session.add(snapshot)
        self._session.flush()

        record.current_snapshot_id = snapshot.snapshot_id
        self._session.flush()
        return snapshot
