"""
Failed watcher repositories: WatchedFolderSnapshot + TechnicianChange.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pathoryx_enterprise.db.models.failed_watcher import TechnicianChange, WatchedFolderSnapshot
from pathoryx_enterprise.db.repositories.base import BaseRepository
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id


class WatchedFolderSnapshotRepository(BaseRepository):

    def upsert(
        self,
        *,
        folder_label: str,
        folder_path: str,
        file_path: str,
        filename: str,
        file_size: Optional[int] = None,
        mtime_ns: Optional[int] = None,
        checksum_sha256: Optional[str] = None,
        global_artifact_id: Optional[str] = None,
        file_record_internal_id: Optional[int] = None,
        sidecar_metadata_json: Optional[dict] = None,
        # RecoverySentry extended fingerprint columns (migration 0007)
        slide_id: Optional[str] = None,
        case_id: Optional[str] = None,
        extension: Optional[str] = None,
        inode_number: Optional[int] = None,
        partial_sha256: Optional[str] = None,
    ) -> WatchedFolderSnapshot:
        """
        Upsert (insert or update) a folder snapshot entry.
        Uses PostgreSQL ON CONFLICT DO UPDATE for atomicity.
        """
        now = utc_now()

        existing = self._session.execute(
            select(WatchedFolderSnapshot).where(
                and_(
                    WatchedFolderSnapshot.folder_label == folder_label,
                    WatchedFolderSnapshot.file_path == file_path,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.filename = filename
            existing.file_size = file_size
            existing.mtime_ns = mtime_ns
            if checksum_sha256 is not None:
                existing.checksum_sha256 = checksum_sha256
            existing.global_artifact_id = global_artifact_id or existing.global_artifact_id
            existing.file_record_internal_id = (
                file_record_internal_id or existing.file_record_internal_id
            )
            if sidecar_metadata_json is not None:
                existing.sidecar_metadata_json = sidecar_metadata_json
            if slide_id is not None:
                existing.slide_id = slide_id
            if case_id is not None:
                existing.case_id = case_id
            if extension is not None:
                existing.extension = extension
            if inode_number is not None:
                existing.inode_number = inode_number
            if partial_sha256 is not None:
                existing.partial_sha256 = partial_sha256
            existing.last_seen_at = now
            self._session.flush()
            return existing

        snap = WatchedFolderSnapshot(
            folder_label=folder_label,
            folder_path=folder_path,
            file_path=file_path,
            filename=filename,
            file_size=file_size,
            mtime_ns=mtime_ns,
            checksum_sha256=checksum_sha256,
            global_artifact_id=global_artifact_id,
            file_record_internal_id=file_record_internal_id,
            sidecar_metadata_json=sidecar_metadata_json,
            slide_id=slide_id,
            case_id=case_id,
            extension=extension,
            inode_number=inode_number,
            partial_sha256=partial_sha256,
            last_seen_at=now,
            first_seen_at=now,
        )
        self._session.add(snap)
        self._session.flush()
        return snap

    def get_all_for_folder(self, folder_label: str) -> list[WatchedFolderSnapshot]:
        return list(
            self._session.execute(
                select(WatchedFolderSnapshot).where(
                    WatchedFolderSnapshot.folder_label == folder_label
                )
            ).scalars().all()
        )

    def delete_by_path(self, folder_label: str, file_path: str) -> None:
        snap = self._session.execute(
            select(WatchedFolderSnapshot).where(
                and_(
                    WatchedFolderSnapshot.folder_label == folder_label,
                    WatchedFolderSnapshot.file_path == file_path,
                )
            )
        ).scalar_one_or_none()
        if snap is not None:
            self._session.delete(snap)
            self._session.flush()


class TechnicianChangeRepository(BaseRepository):

    def record_change(
        self,
        *,
        change_type: str,
        watch_folder_label: str,
        detected_at: object,
        old_path: Optional[str] = None,
        old_filename: Optional[str] = None,
        old_file_size: Optional[int] = None,
        old_checksum_sha256: Optional[str] = None,
        old_mtime_ns: Optional[int] = None,
        new_path: Optional[str] = None,
        new_filename: Optional[str] = None,
        new_file_size: Optional[int] = None,
        new_checksum_sha256: Optional[str] = None,
        new_mtime_ns: Optional[int] = None,
        global_artifact_id: Optional[str] = None,
        parent_artifact_id: Optional[str] = None,
        file_record_internal_id: Optional[int] = None,
        inferred_action: Optional[str] = None,
        slide_id_inferred: Optional[str] = None,
        sidecar_metadata_json: Optional[dict] = None,
        technician_notes: Optional[str] = None,
        requires_approval: bool = False,
        correlation_id: Optional[str] = None,
        runner_id: Optional[str] = None,
    ) -> tuple[TechnicianChange, bool]:
        """
        Insert a TechnicianChange record. Idempotent — if same change already recorded,
        returns the existing row. Returns (record, created).
        """
        idempotency_key = deterministic_artifact_id(
            change_type,
            watch_folder_label,
            old_path or "",
            new_path or "",
            str(new_mtime_ns or ""),
        )

        existing = self._session.execute(
            select(TechnicianChange).where(
                TechnicianChange.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()

        if existing is not None:
            return existing, False

        change = TechnicianChange(
            idempotency_key=idempotency_key,
            change_type=change_type,
            watch_folder_label=watch_folder_label,
            detected_at=detected_at,
            old_path=old_path,
            old_filename=old_filename,
            old_file_size=old_file_size,
            old_checksum_sha256=old_checksum_sha256,
            old_mtime_ns=old_mtime_ns,
            new_path=new_path,
            new_filename=new_filename,
            new_file_size=new_file_size,
            new_checksum_sha256=new_checksum_sha256,
            new_mtime_ns=new_mtime_ns,
            global_artifact_id=global_artifact_id,
            parent_artifact_id=parent_artifact_id,
            file_record_internal_id=file_record_internal_id,
            inferred_action=inferred_action,
            slide_id_inferred=slide_id_inferred,
            sidecar_metadata_json=sidecar_metadata_json,
            technician_notes=technician_notes,
            review_status="detected",
            requires_approval=requires_approval,
            correlation_id=correlation_id,
            runner_id=runner_id,
        )
        self._session.add(change)
        self._session.flush()
        return change, True

    def get_pending_review(
        self,
        watch_folder_label: Optional[str] = None,
        limit: int = 100,
    ) -> list[TechnicianChange]:
        stmt = (
            select(TechnicianChange)
            .where(TechnicianChange.review_status.in_(["detected", "linked", "unlinked"]))
            .order_by(TechnicianChange.detected_at.asc())
            .limit(limit)
        )
        if watch_folder_label:
            stmt = stmt.where(TechnicianChange.watch_folder_label == watch_folder_label)
        return list(self._session.execute(stmt).scalars().all())
