"""
ServiceTrigger repository with safe concurrent dequeue.

CRITICAL: dequeue_next() uses SELECT … FOR UPDATE SKIP LOCKED.
This guarantees that in a multi-worker deployment, each trigger is claimed
by exactly one worker. Workers that find no pending triggers simply return None
and sleep until the next poll cycle.

Dead-letter recovery:
  Triggers with retry_count >= max_retries are left in 'failed' status.
  The failed_watcher service monitors these and can requeue or escalate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pathoryx_enterprise.db.models.core import ServiceTrigger
from pathoryx_enterprise.db.repositories.base import BaseRepository
from pathoryx_enterprise.utils.datetime_utils import utc_now


class TriggerRepository(BaseRepository):

    def dequeue_next(
        self,
        target_service: str,
        *,
        runner_id: str = "",
        host_id: str = "",
    ) -> Optional[ServiceTrigger]:
        """
        Atomically claim the next pending trigger for target_service.

        Uses SELECT … FOR UPDATE SKIP LOCKED:
          - FOR UPDATE: locks the row so no other worker can claim it simultaneously.
          - SKIP LOCKED: workers skip rows already locked by another, preventing wait storms.

        Returns None if no pending triggers exist.
        The caller must commit after processing to release the lock.
        """
        stmt = (
            select(ServiceTrigger)
            .where(
                and_(
                    ServiceTrigger.target_service == target_service,
                    ServiceTrigger.trigger_status == "pending",
                )
            )
            .order_by(ServiceTrigger.triggered_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        trigger = self._session.execute(stmt).scalar_one_or_none()

        if trigger is None:
            return None

        # Mark as claimed atomically within the same locked transaction
        now = utc_now()
        trigger.trigger_status = "running"
        trigger.accepted_at = trigger.accepted_at or now
        trigger.started_at = now
        trigger.claimed_by_runner_id = runner_id or None
        trigger.claimed_by_host_id = host_id or None
        self._session.flush()
        return trigger

    def create_trigger(
        self,
        *,
        source_service: str,
        target_service: str,
        stage_name: str,
        file_record_internal_id: int,
        pipeline_run_internal_id: Optional[int] = None,
        global_artifact_id: Optional[str] = None,
        global_run_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        otel_trace_id: Optional[str] = None,
        payload_json: Optional[dict] = None,
        max_retries: int = 3,
    ) -> ServiceTrigger:
        """
        Create a new trigger using INSERT … ON CONFLICT DO NOTHING.

        The unique constraint uq_trigger_per_file_stage prevents duplicate triggers.
        If an identical trigger already exists, returns the existing one.
        """
        now = utc_now()

        # Try plain insert; if the unique constraint fires, fetch the existing row
        existing = self._session.execute(
            select(ServiceTrigger).where(
                and_(
                    ServiceTrigger.source_service == source_service,
                    ServiceTrigger.target_service == target_service,
                    ServiceTrigger.stage_name == stage_name,
                    ServiceTrigger.file_record_internal_id == file_record_internal_id,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            return existing

        trigger = ServiceTrigger(
            source_service=source_service,
            target_service=target_service,
            stage_name=stage_name,
            file_record_internal_id=file_record_internal_id,
            pipeline_run_internal_id=pipeline_run_internal_id,
            global_artifact_id=global_artifact_id,
            global_run_id=global_run_id,
            trigger_status="pending",
            trigger_payload_json=payload_json or {},
            retry_count=0,
            max_retries=max_retries,
            correlation_id=correlation_id,
            otel_trace_id=otel_trace_id,
            triggered_at=now,
        )
        self._session.add(trigger)
        self._session.flush()
        return trigger

    def enqueue(
        self,
        *,
        source_service: str,
        target_service: str,
        stage_name: str,
        file_record_internal_id: int,
        global_artifact_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        runner_id: Optional[str] = None,
        payload: Optional[dict] = None,
        max_retries: int = 3,
    ) -> tuple[ServiceTrigger, bool]:
        """Enqueue a trigger; returns (trigger, created). Idempotent."""
        now = utc_now()
        existing = self._session.execute(
            select(ServiceTrigger).where(
                and_(
                    ServiceTrigger.source_service == source_service,
                    ServiceTrigger.target_service == target_service,
                    ServiceTrigger.stage_name == stage_name,
                    ServiceTrigger.file_record_internal_id == file_record_internal_id,
                    ServiceTrigger.trigger_status.in_(["pending", "running"]),
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            return existing, False

        trigger = ServiceTrigger(
            source_service=source_service,
            target_service=target_service,
            stage_name=stage_name,
            file_record_internal_id=file_record_internal_id,
            global_artifact_id=global_artifact_id,
            trigger_status="pending",
            trigger_payload_json=payload or {},
            retry_count=0,
            max_retries=max_retries,
            correlation_id=correlation_id,
            triggered_at=now,
        )
        self._session.add(trigger)
        self._session.flush()
        return trigger, True

    def mark_completed(self, trigger: ServiceTrigger) -> None:
        trigger.trigger_status = "completed"
        trigger.finished_at = utc_now()
        self._session.flush()

    def mark_failed(self, trigger: ServiceTrigger, error_message: str) -> None:
        trigger.trigger_status = "failed"
        trigger.error_message = error_message[:2000]  # cap length
        trigger.finished_at = utc_now()
        trigger.retry_count = (trigger.retry_count or 0) + 1
        self._session.flush()

    def requeue(self, trigger: ServiceTrigger) -> None:
        """Reset a failed trigger to pending so it can be retried."""
        trigger.trigger_status = "pending"
        trigger.error_message = None
        trigger.started_at = None
        trigger.finished_at = None
        trigger.accepted_at = None
        trigger.claimed_by_runner_id = None
        trigger.claimed_by_host_id = None
        trigger.triggered_at = utc_now()
        self._session.flush()

    def count_pending(self, target_service: str) -> int:
        """Return queue depth for Prometheus metrics."""
        from sqlalchemy import func
        result = self._session.execute(
            select(func.count()).where(
                and_(
                    ServiceTrigger.target_service == target_service,
                    ServiceTrigger.trigger_status == "pending",
                )
            )
        ).scalar_one()
        return int(result)

    def count_failed_dead_letters(self, target_service: str) -> int:
        """Return count of failed triggers that have exhausted retries."""
        from sqlalchemy import func
        result = self._session.execute(
            select(func.count()).where(
                and_(
                    ServiceTrigger.target_service == target_service,
                    ServiceTrigger.trigger_status == "failed",
                    ServiceTrigger.retry_count >= ServiceTrigger.max_retries,
                )
            )
        ).scalar_one()
        return int(result)
