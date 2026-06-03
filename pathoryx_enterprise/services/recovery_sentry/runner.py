"""
RecoverySentry service runner.

Poll loop: scan watched folders, detect technician changes, attempt recovery.
Handles SIGTERM gracefully — current scan completes, then exits cleanly.
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
from pathoryx_enterprise.services.recovery_sentry.change_processor import scan_and_recover_once
from pathoryx_enterprise.services.recovery_sentry.config import RecoverySentrySettings
from pathoryx_enterprise.services.recovery_sentry.metadata_extractor import initialize_openslide
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = structlog.get_logger(__name__)

SERVICE_NAME = "recovery_sentry"


def run(settings: RecoverySentrySettings) -> None:
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
            "poll_interval_seconds": settings.poll_interval_seconds,
            "auto_recover": settings.auto_recover_valid_slide_id,
            "add_timestamp_if_missing": settings.add_timestamp_if_missing,
        },
    ).run()

    if not settings.watch_folders:
        logger.error(
            "no_watch_folders_configured",
            hint="Set RECOVERY_SENTRY_CONFIG or FAILED_WATCHER_FOLDERS",
        )
        return

    if settings.final_destination is None:
        logger.error(
            "final_destination_not_configured",
            hint="Set final_destination_root in recovery_sentry.yaml or babelshark_config_path",
        )
        return

    # Initialize OpenSlide for WSI metadata extraction
    initialize_openslide()

    runner_id = deterministic_artifact_id(SERVICE_NAME, settings.environment, "runner")
    host_id: str = __import__("socket").gethostname()

    ready_probe = build_readiness_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL"],
    )
    health_probe = build_health_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL"],
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
        "recovery_sentry_started",
        runner_id=runner_id,
        folders=[str(f) for f in settings.watch_folders],
        final_destination=str(settings.final_destination),
        poll_interval=settings.poll_interval_seconds,
    )

    consecutive_errors = 0
    last_heartbeat = utc_now()

    while not coordinator.is_stopping:
        try:
            summary = scan_and_recover_once(settings, runner_id)
            if summary["changes_detected"] > 0 or summary["auto_recovered"] > 0:
                logger.info("scan_complete", **summary)
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            logger.exception("scan_failed", error=str(exc), consecutive=consecutive_errors)

        if consecutive_errors >= settings.max_consecutive_errors:
            logger.error("too_many_errors_stopping", count=consecutive_errors)
            coordinator.trigger()
            break

        with get_session() as session:
            RunnerRegistryRepository(session).heartbeat(runner_id)
        age = (utc_now() - last_heartbeat).total_seconds()
        runner_heartbeat_age_seconds.labels(
            runner_id=runner_id, service=SERVICE_NAME
        ).set(age)
        last_heartbeat = utc_now()

        _sleep_interruptible(settings.poll_interval_seconds, coordinator)

    logger.info("recovery_sentry_stopped")


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
