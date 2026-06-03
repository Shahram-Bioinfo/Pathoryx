"""
Runner registration repository.

Supports multi-machine deployments by maintaining a live registry of all
active service runner processes. Each runner registers at startup, sends
heartbeats, and deregisters on clean shutdown.

Dead runner detection: any runner with last_heartbeat_at older than
`stale_threshold_seconds` is considered crashed and can be marked accordingly.
"""
from __future__ import annotations

import os
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pathoryx_enterprise.db.models.core import RunnerRegistration
from pathoryx_enterprise.db.repositories.base import BaseRepository
from pathoryx_enterprise.utils.datetime_utils import utc_now

# Runners are considered stale/crashed if no heartbeat for this long.
DEFAULT_STALE_THRESHOLD_SECONDS = 120


class RunnerRegistryRepository(BaseRepository):

    def register(
        self,
        *,
        runner_id: str,
        service_name: str,
        host_id: str,
        environment: str = "development",
        service_version: str = "1.0.0",
        config_hash: str = "",
        capabilities: dict | None = None,
    ) -> RunnerRegistration:
        """
        Upsert runner registration. Creates a new row on first call,
        updates status + heartbeat on subsequent calls for the same runner_id.
        """
        now = utc_now()
        pid = os.getpid()

        existing = self._session.execute(
            select(RunnerRegistration).where(RunnerRegistration.runner_id == runner_id)
        ).scalar_one_or_none()

        if existing is not None:
            existing.status = "active"
            existing.last_heartbeat_at = now
            existing.pid = pid
            self._session.flush()
            return existing

        reg = RunnerRegistration(
            runner_id=runner_id,
            service_name=service_name,
            host_id=host_id,
            pid=pid,
            environment=environment,
            service_version=service_version,
            config_hash=config_hash,
            status="active",
            started_at=now,
            last_heartbeat_at=now,
            capabilities_json=capabilities or {},
        )
        self._session.add(reg)
        self._session.flush()
        return reg

    def heartbeat(self, runner_id: str) -> None:
        """Update last_heartbeat_at for a running runner."""
        reg = self._session.execute(
            select(RunnerRegistration).where(RunnerRegistration.runner_id == runner_id)
        ).scalar_one_or_none()
        if reg is not None:
            reg.last_heartbeat_at = utc_now()
            self._session.flush()

    def deregister(self, runner_id: str) -> None:
        """Mark a runner as cleanly shut down."""
        reg = self._session.execute(
            select(RunnerRegistration).where(RunnerRegistration.runner_id == runner_id)
        ).scalar_one_or_none()
        if reg is not None:
            reg.status = "shutdown"
            reg.shutdown_at = utc_now()
            self._session.flush()

    def mark_stale_crashed(
        self,
        stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
    ) -> int:
        """
        Mark any 'active' runners that have not sent a heartbeat recently as 'crashed'.
        Returns the number of runners marked.
        Safe to call from any machine — does not affect other machines' state.
        """
        cutoff = utc_now() - timedelta(seconds=stale_threshold_seconds)
        stale = self._session.execute(
            select(RunnerRegistration).where(
                RunnerRegistration.status == "active",
                RunnerRegistration.last_heartbeat_at < cutoff,
            )
        ).scalars().all()

        for reg in stale:
            reg.status = "crashed"
        self._session.flush()
        return len(stale)

    def list_active(self, service_name: str | None = None) -> list[RunnerRegistration]:
        stmt = select(RunnerRegistration).where(RunnerRegistration.status == "active")
        if service_name:
            stmt = stmt.where(RunnerRegistration.service_name == service_name)
        return list(self._session.execute(stmt).scalars().all())
