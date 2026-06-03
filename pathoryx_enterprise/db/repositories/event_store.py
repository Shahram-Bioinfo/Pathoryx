"""
Immutable event store repository.

RULES:
  - append() is the ONLY write method. No update, no delete.
  - event_version is assigned by querying the max version for the aggregate and incrementing.
  - idempotency_key prevents duplicate inserts — safe to call multiple times.
  - Replay methods return events in version order for full audit reconstruction.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from pathoryx_enterprise.db.models.events import PipelineEvent
from pathoryx_enterprise.db.repositories.base import BaseRepository
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id


class EventStoreRepository(BaseRepository):

    def append(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        service_name: str,
        event_payload: dict,
        event_schema_version: str = "1.0.0",
        file_record_internal_id: Optional[int] = None,
        pipeline_run_internal_id: Optional[int] = None,
        step_run_internal_id: Optional[int] = None,
        global_artifact_id: Optional[str] = None,
        global_run_id: Optional[str] = None,
        parent_artifact_id: Optional[str] = None,
        runner_id: Optional[str] = None,
        host_id: Optional[str] = None,
        service_version: Optional[str] = None,
        correlation_id: Optional[str] = None,
        otel_trace_id: Optional[str] = None,
        otel_span_id: Optional[str] = None,
        caused_by_event_id: Optional[int] = None,
        metadata_snapshot_id: Optional[int] = None,
    ) -> PipelineEvent:
        """
        Append a new event to the immutable event store.

        If an event with the same idempotency_key already exists, returns the
        existing event without raising (safe to call multiple times).
        """
        now = utc_now()

        # Compute next event_version for this aggregate
        max_ver: int = self._session.execute(
            select(func.max(PipelineEvent.event_version)).where(
                and_(
                    PipelineEvent.aggregate_type == aggregate_type,
                    PipelineEvent.aggregate_id == aggregate_id,
                )
            )
        ).scalar_one() or 0
        event_version = max_ver + 1

        idempotency_key = deterministic_artifact_id(
            event_type,
            aggregate_id,
            global_run_id or "",
            service_name,
            now.isoformat(),
        )

        event = PipelineEvent(
            event_type=event_type,
            event_schema_version=event_schema_version,
            event_version=event_version,
            idempotency_key=idempotency_key,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            file_record_internal_id=file_record_internal_id,
            pipeline_run_internal_id=pipeline_run_internal_id,
            step_run_internal_id=step_run_internal_id,
            global_artifact_id=global_artifact_id,
            global_run_id=global_run_id,
            parent_artifact_id=parent_artifact_id,
            service_name=service_name,
            runner_id=runner_id,
            host_id=host_id,
            service_version=service_version,
            correlation_id=correlation_id,
            otel_trace_id=otel_trace_id,
            otel_span_id=otel_span_id,
            caused_by_event_id=caused_by_event_id,
            event_payload=event_payload,
            metadata_snapshot_id=metadata_snapshot_id,
            occurred_at=now,
        )

        self._session.add(event)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            # Idempotency: return existing event
            existing = self._session.execute(
                select(PipelineEvent).where(
                    PipelineEvent.idempotency_key == idempotency_key
                )
            ).scalar_one()
            return existing

        return event

    def replay_aggregate(
        self,
        aggregate_type: str,
        aggregate_id: str,
    ) -> list[PipelineEvent]:
        """Return all events for an aggregate in version order (oldest first)."""
        return list(
            self._session.execute(
                select(PipelineEvent)
                .where(
                    and_(
                        PipelineEvent.aggregate_type == aggregate_type,
                        PipelineEvent.aggregate_id == aggregate_id,
                    )
                )
                .order_by(PipelineEvent.event_version.asc())
            ).scalars().all()
        )

    def replay_artifact(self, global_artifact_id: str) -> list[PipelineEvent]:
        """Return all events for a global_artifact_id in version order."""
        return list(
            self._session.execute(
                select(PipelineEvent)
                .where(PipelineEvent.global_artifact_id == global_artifact_id)
                .order_by(PipelineEvent.event_version.asc())
            ).scalars().all()
        )
