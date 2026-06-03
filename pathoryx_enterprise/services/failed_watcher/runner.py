"""
Failed Watcher service runner.

Poll loop that calls scan_once() at each interval.
Handles SIGTERM gracefully — current scan completes, then exits.
"""
from __future__ import annotations

import time

import structlog

from pathoryx_enterprise.db.repositories.runner_registry import RunnerRegistryRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.logging.setup import configure_logging
from pathoryx_enterprise.monitoring.health import build_health_probe, build_readiness_probe
from pathoryx_enterprise.monitoring.http_health import HealthHTTPServer
from pathoryx_enterprise.monitoring.metrics import (
    runner_heartbeat_age_seconds,
    start_metrics_server,
)
from pathoryx_enterprise.monitoring.shutdown import ShutdownCoordinator
from pathoryx_enterprise.monitoring.startup import StartupValidator
from pathoryx_enterprise.monitoring.tracing import setup_tracing
from pathoryx_enterprise.services.failed_watcher.config import FailedWatcherSettings
from pathoryx_enterprise.services.failed_watcher.watcher import scan_once
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = structlog.get_logger(__name__)

SERVICE_NAME = "failed_watcher"


def run(settings: FailedWatcherSettings) -> None:
    import os
    configure_logging(
        service_name=SERVICE_NAME,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        json_output=settings.environment != "development",
    )
    setup_tracing(SERVICE_NAME, settings.service_version)

    _runner_id_tmp = deterministic_artifact_id(SERVICE_NAME, settings.environment, "runner")
    _host_id_tmp: str = __import__("socket").gethostname()

    StartupValidator(
        service_name=SERVICE_NAME,
        version=settings.service_version,
        environment=settings.environment,
        required_env_vars=["DATABASE_URL"],
        health_port=settings.health_port,
        metrics_port=settings.metrics_port,
        runner_id=_runner_id_tmp,
        host_id=_host_id_tmp,
        extra_info={
            "watch_folders": len(settings.watch_folders),
            "scan_interval_seconds": settings.scan_interval_seconds,
        },
    ).run()

    if not settings.watch_folders:
        logger.error(
            "no watch folders configured — set FAILED_WATCHER_FOLDERS env var"
        )
        return

    runner_id = deterministic_artifact_id(SERVICE_NAME, settings.environment, "runner")
    host_id: str = __import__("socket").gethostname()

    # --- Health/metrics servers ---
    ready_probe = build_readiness_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL", "FAILED_WATCHER_FOLDERS"],
    )
    health_probe = build_health_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL", "FAILED_WATCHER_FOLDERS"],
        input_paths=settings.watch_folders,
    )
    health_server = HealthHTTPServer(
        port=settings.health_port,
        ready_probe=ready_probe,
        health_probe=health_probe,
    )
    health_server.start()
    start_metrics_server(settings.metrics_port, SERVICE_NAME, settings.service_version)

    coordinator = ShutdownCoordinator()
    coordinator.install()
    coordinator.register(health_server.stop)

    with get_session() as session:
        RunnerRegistryRepository(session).register(
            runner_id=runner_id,
            service_name=SERVICE_NAME,
            host_id=host_id,
            environment=settings.environment,
            service_version=settings.service_version,
        )
    coordinator.register(lambda: _deregister(runner_id))

    logger.info(
        "failed watcher runner started",
        runner_id=runner_id,
        folders=len(settings.watch_folders),
        scan_interval=settings.scan_interval_seconds,
    )

    consecutive_errors = 0
    last_heartbeat = utc_now()

    while not coordinator.is_stopping:
        try:
            new_changes = scan_once(settings, runner_id)
            if new_changes > 0:
                logger.info("scan complete", new_changes=new_changes)
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            logger.exception("scan failed", error=str(exc))

        if consecutive_errors >= settings.max_consecutive_errors:
            logger.error("too many scan errors — stopping", count=consecutive_errors)
            coordinator.trigger()
            break

        # Heartbeat
        with get_session() as session:
            RunnerRegistryRepository(session).heartbeat(runner_id)
        age = (utc_now() - last_heartbeat).total_seconds()
        runner_heartbeat_age_seconds.labels(
            runner_id=runner_id, service=SERVICE_NAME
        ).set(age)
        last_heartbeat = utc_now()

        _sleep_interruptible(settings.scan_interval_seconds, coordinator)

    logger.info("failed watcher runner stopped")


def _deregister(runner_id: str) -> None:
    try:
        with get_session() as session:
            RunnerRegistryRepository(session).deregister(runner_id)
    except Exception:
        pass


def _sleep_interruptible(seconds: int, coordinator: ShutdownCoordinator) -> None:
    for _ in range(seconds):
        if coordinator.is_stopping:
            break
        time.sleep(1)
