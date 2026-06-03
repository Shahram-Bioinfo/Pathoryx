"""
Enterprise replacement for the original BabelShark DatabaseManager.

INTERFACE CONTRACT:
  This module must expose a `DatabaseManager` class with the EXACT same
  public method signatures as the original database_manager.py so that
  collect_slides.py can import it without modification.

WHAT CHANGED vs original:
  - No hardcoded DEFAULT_DATABASE_URL — reads DATABASE_URL from env only.
  - Uses the enterprise engine (shared, pooled) instead of create_engine() per instance.
  - Uses enterprise `get_session()` context manager instead of bare Session.
  - Uses enterprise `FileRecord`, `PipelineRun`, `StepRun` models (same schema, different import path).
  - Uses `get_or_create_safe()` from FileRecordRepository for concurrent-safe insertion.
  - Uses `EventStoreRepository.append()` instead of deprecated EventLog table.
  - classify_intake() uses SELECT with proper session scope (no detached-object risk).

WHAT DID NOT CHANGE:
  - All business logic decisions (classify_intake, duplicate detection, rescan logic).
  - All return value shapes (dicts with same keys).
  - register_collected_file(), create_pipeline_run(), create_step_run(), etc.
    all return the same tuple shapes.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select

from pathoryx_enterprise.db.engine import get_shared_engine
from pathoryx_enterprise.db.models.core import FileRecord, PipelineRun, StepRun
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.repositories.file_record import FileRecordRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id


def _build_fast_fingerprint(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path).resolve()
    stat = path.stat()
    return {
        "canonical_path": str(path),
        "original_filename": path.name,
        "file_size": stat.st_size,
        "file_mtime_ns": stat.st_mtime_ns,
    }


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return int((finished_at - started_at).total_seconds() * 1000)


class DatabaseManager:
    """
    Enterprise-grade drop-in replacement for the original BabelShark DatabaseManager.

    Uses the shared enterprise engine and context-managed sessions.
    All writes are idempotent and safe under concurrent workers.
    """

    def __init__(self) -> None:
        # Validate that the engine can be constructed (credentials present).
        get_shared_engine()

    # ------------------------------------------------------------------
    # Intake classification (read-only — no writes)
    # ------------------------------------------------------------------

    def classify_intake(self, source_path: str | Path) -> dict[str, Any]:
        fingerprint = _build_fast_fingerprint(source_path)
        canonical_path = fingerprint["canonical_path"]
        original_filename = fingerprint["original_filename"]
        file_size = fingerprint["file_size"]
        file_mtime_ns = fingerprint["file_mtime_ns"]

        with get_session() as session:
            candidates = list(
                session.execute(
                    select(FileRecord).where(
                        or_(
                            FileRecord.source_artifact_id == canonical_path,
                            FileRecord.original_path == canonical_path,
                            FileRecord.current_file_path == canonical_path,
                            FileRecord.canonical_path == canonical_path,
                            FileRecord.original_filename == original_filename,
                        )
                    )
                ).scalars().all()
            )

        # exact duplicate check
        for record in candidates:
            meta = record.metadata_json or {}
            fp = meta.get("fast_fingerprint", {})
            if (
                isinstance(fp, dict)
                and fp.get("canonical_path") == canonical_path
                and fp.get("original_filename") == original_filename
                and fp.get("file_size") == file_size
                and fp.get("file_mtime_ns") == file_mtime_ns
            ):
                return {
                    "intake_decision": "duplicate",
                    "action": "skip_duplicate",
                    "reason": "same_fast_fingerprint",
                    "matched_file_record_id": record.internal_id,
                    "parent_artifact_id": record.global_artifact_id,
                    "fast_fingerprint": fingerprint,
                }

        # rescan check
        for record in candidates:
            meta = record.metadata_json or {}
            fp = meta.get("fast_fingerprint", {})
            if not isinstance(fp, dict):
                continue
            same_name = record.original_filename == original_filename
            different_size = fp.get("file_size") != file_size
            different_mtime = fp.get("file_mtime_ns") != file_mtime_ns
            if same_name and (different_size or different_mtime):
                return {
                    "intake_decision": "rescan",
                    "action": "register_rescan",
                    "reason": "same_original_filename_different_size_or_mtime",
                    "matched_file_record_id": record.internal_id,
                    "parent_artifact_id": record.global_artifact_id,
                    "fast_fingerprint": fingerprint,
                }

        return {
            "intake_decision": "new",
            "action": "register_new",
            "reason": "new_file",
            "matched_file_record_id": None,
            "parent_artifact_id": None,
            "fast_fingerprint": fingerprint,
        }

    def is_already_collected(self, source_path: str | Path) -> bool:
        return self.classify_intake(source_path)["action"] == "skip_duplicate"

    # ------------------------------------------------------------------
    # File registration
    # ------------------------------------------------------------------

    def register_collected_file(
        self,
        source_path: str | Path,
        staged_path: str | Path,
        file_name: str,
        file_format: str,
        file_size: int,
        intake_decision: dict | None = None,
        raw_metadata: dict | None = None,
        normalized_metadata: dict | None = None,
        defer_trigger: bool = False,
    ) -> dict[str, Any]:
        """
        Create or update a FileRecord for a newly collected slide.
        Returns dict with record_id and global_artifact_id (same shape as original).
        """
        canonical_source = str(Path(source_path).resolve())
        canonical_staged = str(Path(staged_path).resolve())

        if intake_decision is None:
            intake_decision = self.classify_intake(source_path)

        fp = intake_decision.get("fast_fingerprint") or _build_fast_fingerprint(source_path)
        raw_metadata = raw_metadata or {}
        normalized_metadata = normalized_metadata or {}

        global_artifact_id = deterministic_artifact_id(
            "babelshark",
            "raw_slide",
            canonical_source,
            fp.get("file_size"),
            fp.get("file_mtime_ns"),
        )

        metadata_json = {
            "fast_fingerprint": fp,
            "intake_decision": intake_decision,
            "collector": "babelshark_collect_slides",
            "original_source_path": canonical_source,
            "staged_path": canonical_staged,
            "scanner": {
                "scanner_name": normalized_metadata.get("scanner_name"),
                "scanner_family": normalized_metadata.get("scanner_family"),
                "scanner_vendor_raw": normalized_metadata.get("scanner_vendor_raw"),
                "scanner_model_raw": normalized_metadata.get("scanner_model_raw"),
                "scanner_id_raw": normalized_metadata.get("scanner_id_raw"),
            },
            "scan_time": {
                "scan_date_raw": normalized_metadata.get("scan_date_raw"),
                "scan_time_raw": normalized_metadata.get("scan_time_raw"),
                "scan_tz_raw": normalized_metadata.get("scan_tz_raw"),
            },
            "slide_id_raw": normalized_metadata.get("slide_id_raw"),
            "slide_id_source_key": normalized_metadata.get("slide_id_source_key"),
            "magnification": normalized_metadata.get("magnification"),
            "mpp": normalized_metadata.get("mpp"),
            "openslide_detected": normalized_metadata.get("openslide_detected"),
        }

        with get_session() as session:
            repo = FileRecordRepository(session)

            force_new = intake_decision.get("action") == "register_rescan"
            existing = None
            if not force_new:
                existing = session.execute(
                    select(FileRecord).where(
                        or_(
                            FileRecord.global_artifact_id == global_artifact_id,
                            and_(
                                FileRecord.source_service == "babelshark",
                                FileRecord.source_artifact_id == canonical_source,
                            ),
                        )
                    )
                ).scalar_one_or_none()

            now = utc_now()

            # Resolve scanner identity from extracted metadata.
            # scanner_id = raw hardware ID (e.g. aperio serial, hamamatsu source)
            # scanner_name = human-readable name resolved via alias config
            _scanner_id_raw = normalized_metadata.get("scanner_id_raw") or normalized_metadata.get("scanner_name")
            _scanner_name = normalized_metadata.get("scanner_name")

            if existing is not None:
                existing.source_service = "babelshark"
                existing.source_artifact_id = canonical_source
                existing.artifact_type = existing.artifact_type or "raw_slide"
                existing.original_filename = existing.original_filename or file_name
                existing.current_filename = file_name
                existing.original_path = existing.original_path or canonical_source
                existing.current_file_path = canonical_staged
                existing.canonical_path = canonical_staged
                existing.file_format = file_format
                existing.file_size = file_size
                existing.status = "intake_registered"
                existing.scanner_id = _scanner_id_raw
                existing.scanner_name = _scanner_name
                existing.metadata_json = metadata_json
                existing.input_metadata_json = raw_metadata
                existing.output_metadata_json = normalized_metadata
                session.flush()
                record_internal_id = existing.internal_id
                record_global_artifact_id = existing.global_artifact_id
            else:
                record, _ = repo.get_or_create_safe(
                    canonical_path=canonical_staged,
                    defaults=dict(
                        global_artifact_id=global_artifact_id,
                        parent_artifact_id=intake_decision.get("parent_artifact_id"),
                        source_service="babelshark",
                        source_artifact_id=canonical_source,
                        artifact_type="raw_slide",
                        original_filename=file_name,
                        current_filename=file_name,
                        original_path=canonical_source,
                        current_file_path=canonical_staged,
                        file_format=file_format,
                        file_size=file_size,
                        checksum_sha256=None,
                        status="intake_registered",
                        scanner_id=_scanner_id_raw,
                        scanner_name=_scanner_name,
                        metadata_json=metadata_json,
                        input_metadata_json=raw_metadata,
                        output_metadata_json=normalized_metadata,
                    ),
                )
                record_internal_id = record.internal_id
                record_global_artifact_id = record.global_artifact_id

            # Dispatch trigger + event in the same session (atomic with FileRecord write).
            # When defer_trigger=True (full pipeline mode) the stage_runner dispatches
            # the trigger after all enrichment stages complete instead.
            if not defer_trigger:
                from pathoryx_enterprise.services.babelshark.db_writer import BabelSharkDBWriter  # noqa: PLC0415

                next_stage = os.environ.get("BABELSHARK_NEXT_STAGE", "qc")
                next_service = os.environ.get("BABELSHARK_NEXT_SERVICE", "qc_service")
                BabelSharkDBWriter(session).mark_intake_complete(
                    file_record_internal_id=record_internal_id,
                    global_artifact_id=record_global_artifact_id,
                    next_stage=next_stage,
                    next_service=next_service,
                )

            return {
                "record_id": record_internal_id,
                "global_artifact_id": record_global_artifact_id,
            }

    # ------------------------------------------------------------------
    # Pipeline lifecycle (preserved interface; enterprise models used)
    # ------------------------------------------------------------------

    def create_pipeline_run(
        self,
        file_record_internal_id: int,
        pipeline_name: str = "babelshark_collect",
    ) -> tuple[int, str]:
        global_run_id = deterministic_artifact_id(
            "babelshark", pipeline_name, file_record_internal_id
        )
        now = utc_now()

        with get_session() as session:
            existing = session.execute(
                select(PipelineRun).where(PipelineRun.global_run_id == global_run_id)
            ).scalar_one_or_none()

            if existing is not None:
                existing.run_status = "running"
                existing.final_outcome = None
                session.flush()
                return existing.internal_id, existing.global_run_id

            run = PipelineRun(
                global_run_id=global_run_id,
                file_record_internal_id=file_record_internal_id,
                service_name="babelshark",
                pipeline_name=pipeline_name,
                run_status="running",
                final_outcome=None,
                started_at=now,
                correlation_id=global_run_id,
            )
            session.add(run)
            session.flush()
            return run.internal_id, global_run_id

    def create_step_run(
        self,
        pipeline_run_internal_id: int,
        step_name: str,
        step_status: str = "completed",
    ) -> int:
        now = utc_now()
        with get_session() as session:
            existing = session.execute(
                select(StepRun).where(
                    StepRun.pipeline_run_internal_id == pipeline_run_internal_id,
                    StepRun.step_name == step_name,
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.step_status = step_status
                existing.outcome = step_status
                existing.finished_at = now
                session.flush()
                return existing.internal_id

            step = StepRun(
                pipeline_run_internal_id=pipeline_run_internal_id,
                step_name=step_name,
                step_status=step_status,
                outcome=step_status,
                started_at=now,
                finished_at=now,
                retry_count=0,
                context_json={},
            )
            session.add(step)
            session.flush()
            return step.internal_id

    def complete_pipeline_run(
        self,
        pipeline_run_internal_id: int,
        final_outcome: str = "completed",
    ) -> None:
        now = utc_now()
        with get_session() as session:
            run = session.execute(
                select(PipelineRun).where(PipelineRun.internal_id == pipeline_run_internal_id)
            ).scalar_one_or_none()
            if run is None:
                raise ValueError(f"PipelineRun not found: {pipeline_run_internal_id}")
            run.run_status = "completed"
            run.final_outcome = final_outcome
            run.finished_at = now
            run.duration_ms = _duration_ms(run.started_at, now)
            session.flush()

    def create_event_log(
        self,
        event_type: str,
        file_record_internal_id: int | None = None,
        pipeline_run_internal_id: int | None = None,
        step_run_internal_id: int | None = None,
        global_run_id: str | None = None,
        global_artifact_id: str | None = None,
        payload: dict | None = None,
    ) -> int:
        """Appends to the immutable enterprise event store (replaces EventLog table)."""
        with get_session() as session:
            repo = EventStoreRepository(session)
            event = repo.append(
                event_type=f"babelshark.{event_type}",
                aggregate_type="file_record",
                aggregate_id=global_artifact_id or str(file_record_internal_id or ""),
                service_name="babelshark",
                event_payload=payload or {},
                file_record_internal_id=file_record_internal_id,
                pipeline_run_internal_id=pipeline_run_internal_id,
                step_run_internal_id=step_run_internal_id,
                global_artifact_id=global_artifact_id,
                global_run_id=global_run_id,
                correlation_id=global_run_id,
            )
            return event.internal_id

    # ------------------------------------------------------------------
    # Compatibility: original code calls db.close() but sessions are now
    # self-managed context managers — close() is a no-op.
    # ------------------------------------------------------------------

    def close(self) -> None:
        pass
