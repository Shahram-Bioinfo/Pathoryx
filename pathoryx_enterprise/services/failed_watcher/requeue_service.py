"""
Requeue service — allows technicians to requeue failed/quarantined slides.

All requeue operations are:
  - Idempotent (duplicate requeue attempt = no-op)
  - Immutably logged (TechnicianChange record + EventStore entry)
  - Gated by optional manual approval workflow

The requeue creates a new ServiceTrigger pointing back to the appropriate
stage (intake | qc | dicom) depending on where the slide failed.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from pathoryx_enterprise.db.models.core import FileRecord
from pathoryx_enterprise.db.models.failed_watcher import TechnicianChange
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.repositories.failed_watcher import TechnicianChangeRepository
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

SERVICE_NAME = "failed_watcher"


class RequeueService:
    """
    Handles technician-initiated requeue requests.

    Stateless — instantiate per-operation with a session.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._trigger_repo = TriggerRepository(session)
        self._event_repo = EventStoreRepository(session)
        self._change_repo = TechnicianChangeRepository(session)

    def requeue_slide(
        self,
        *,
        change_id: int,
        technician_id: str | None = None,
        technician_notes: str | None = None,
        target_stage: str = "intake",
        runner_id: str | None = None,
    ) -> bool:
        """
        Requeue a slide for reprocessing from a specific stage.

        Returns True if the requeue was created, False if already done (idempotent).
        Raises ValueError if the change record does not exist or approval is required.
        """
        change = self._session.execute(
            select(TechnicianChange).where(TechnicianChange.internal_id == change_id)
        ).scalar_one_or_none()

        if change is None:
            raise ValueError(f"TechnicianChange not found: {change_id}")

        if change.requires_approval and change.review_status not in ("approved", "requeued"):
            raise ValueError(
                f"Change {change_id} requires manual approval before requeue. "
                f"Current review_status: {change.review_status}"
            )

        if change.review_status == "requeued":
            logger.info("slide already requeued, skipping", change_id=change_id)
            return False

        # Map stage to target service
        stage_to_service = {
            "intake": "babelshark",
            "qc": "qc_service",
            "dicom": "dicom_service",
            "upload": "upload_service",
        }
        target_service = stage_to_service.get(target_stage, "babelshark")

        # Dispatch requeue trigger if we have a file record
        trigger = None
        if change.file_record_internal_id is not None:
            record = self._session.execute(
                select(FileRecord).where(
                    FileRecord.internal_id == change.file_record_internal_id
                )
            ).scalar_one_or_none()

            if record is not None:
                record.status = f"{target_stage}_pending"
                self._session.flush()

                trigger, created = self._trigger_repo.enqueue(
                    source_service=SERVICE_NAME,
                    target_service=target_service,
                    stage_name=target_stage,
                    file_record_internal_id=change.file_record_internal_id,
                    global_artifact_id=change.global_artifact_id,
                    correlation_id=change.correlation_id,
                    runner_id=runner_id,
                )

        # Update change record status
        now = utc_now()
        change.review_status = "requeued"
        change.reviewed_at = now
        change.reviewed_by = technician_id
        if technician_notes:
            change.technician_notes = technician_notes
        if trigger is not None:
            change.requeue_trigger_id = trigger.internal_id
        self._session.flush()

        # Immutable audit event
        self._event_repo.append(
            event_type="technician.slide_requeued",
            aggregate_type="technician_change",
            aggregate_id=str(change_id),
            service_name=SERVICE_NAME,
            event_payload={
                "change_id": change_id,
                "technician_id": technician_id,
                "target_stage": target_stage,
                "target_service": target_service,
                "trigger_id": trigger.internal_id if trigger else None,
            },
            file_record_internal_id=change.file_record_internal_id,
            global_artifact_id=change.global_artifact_id,
            correlation_id=change.correlation_id,
            runner_id=runner_id,
        )

        logger.info(
            "slide requeued",
            change_id=change_id,
            target_stage=target_stage,
            trigger_id=trigger.internal_id if trigger else None,
        )
        return True

    def dismiss_change(
        self,
        *,
        change_id: int,
        technician_id: str | None = None,
        technician_notes: str | None = None,
    ) -> None:
        """Mark a change as reviewed/dismissed without requeuing."""
        change = self._session.execute(
            select(TechnicianChange).where(TechnicianChange.internal_id == change_id)
        ).scalar_one_or_none()

        if change is None:
            raise ValueError(f"TechnicianChange not found: {change_id}")

        change.review_status = "dismissed"
        change.reviewed_at = utc_now()
        change.reviewed_by = technician_id
        if technician_notes:
            change.technician_notes = technician_notes
        self._session.flush()

        self._event_repo.append(
            event_type="technician.change_dismissed",
            aggregate_type="technician_change",
            aggregate_id=str(change_id),
            service_name=SERVICE_NAME,
            event_payload={
                "change_id": change_id,
                "technician_id": technician_id,
                "notes": technician_notes,
            },
            file_record_internal_id=change.file_record_internal_id,
            global_artifact_id=change.global_artifact_id,
        )
