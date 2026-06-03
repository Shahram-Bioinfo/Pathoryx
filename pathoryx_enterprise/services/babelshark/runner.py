"""
BabelShark collection runner.

Poll loop that drives one collect_slides() cycle at each interval.
Uses the enterprise database_manager drop-in replacement so no original
BabelShark source files are modified.

Lifecycle:
  startup   → validate config → start health/metrics servers → register runner
  loop      → collect_slides() → dispatch triggers → heartbeat → wait interval
  shutdown  → SIGTERM → clean deregister → exit 0
"""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid

import structlog

from pathoryx_enterprise.db.repositories.runner_registry import RunnerRegistryRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.logging.setup import configure_logging, inject_context
from pathoryx_enterprise.monitoring.health import build_health_probe, build_readiness_probe
from pathoryx_enterprise.monitoring.http_health import HealthHTTPServer
from pathoryx_enterprise.monitoring.metrics import (
    files_detected_total,
    files_failed_total,
    files_skipped_total,
    runner_heartbeat_age_seconds,
    start_metrics_server,
    trigger_queue_depth,
)
from pathoryx_enterprise.monitoring.shutdown import ShutdownCoordinator
from pathoryx_enterprise.monitoring.startup import StartupValidator
from pathoryx_enterprise.monitoring.tracing import setup_tracing
from pathoryx_enterprise.services.babelshark.config import BabelSharkSettings
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = structlog.get_logger(__name__)

SERVICE_NAME = "babelshark"


def _collect_once(
    settings: BabelSharkSettings,
    runner_id: str,
    host_id: str,
    correlation_id: str,
) -> tuple[int, int, int]:
    """
    Run one collection cycle. Returns (collected, skipped, errors).

    We call collect_slides.collect_slides() from our enterprise core copy.
    That copy's database_manager import resolves to our enterprise version
    (same package, same relative import path).
    """
    import yaml

    # Import from our enterprise core (verbatim copy with enterprise DB layer)
    from pathoryx_enterprise.services.babelshark.core.collect_slides import (
        collect_slides,
        setup_logging,
        validate_config,
    )

    bs_logger = setup_logging("INFO")

    with open(settings.collector_config_path) as fh:
        conf = yaml.safe_load(fh) or {}

    issues = validate_config(conf)
    if issues:
        for issue in issues:
            logger.error("babelshark config invalid", issue=issue)
        return 0, 0, len(issues)

    inject_context(
        correlation_id=correlation_id,
        runner_id=runner_id,
        host_id=host_id,
        service_name=SERVICE_NAME,
    )

    try:
        collect_slides(conf, bs_logger)
        return 1, 0, 0
    except Exception as exc:
        logger.exception("collect_slides cycle failed", error=str(exc))
        return 0, 0, 1


def run(settings: BabelSharkSettings) -> None:
    configure_logging(
        service_name=SERVICE_NAME,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        json_output=settings.environment != "development",
    )
    setup_tracing(SERVICE_NAME, settings.service_version)

    runner_id = deterministic_artifact_id(SERVICE_NAME, settings.environment, "runner")
    host_id: str = __import__("socket").gethostname()

    StartupValidator(
        service_name=SERVICE_NAME,
        version=settings.service_version,
        environment=settings.environment,
        required_env_vars=["DATABASE_URL"],
        config_paths={"BABELSHARK_CONFIG_PATH": settings.collector_config_path},
        health_port=settings.health_port,
        metrics_port=settings.metrics_port,
        runner_id=runner_id,
        host_id=host_id,
    ).run()

    # --- Health + Metrics servers (daemon threads) ---
    ready_probe = build_readiness_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL", "BABELSHARK_CONFIG"],
    )
    health_probe = build_health_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL", "BABELSHARK_CONFIG"],
    )
    health_server = HealthHTTPServer(
        port=settings.health_port,
        ready_probe=ready_probe,
        health_probe=health_probe,
    )
    health_server.start()
    start_metrics_server(settings.metrics_port, SERVICE_NAME, settings.service_version)

    # --- Graceful shutdown ---
    coordinator = ShutdownCoordinator()
    coordinator.install()
    coordinator.register(health_server.stop)

    # --- Runner registration ---
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
        "babelshark runner started",
        runner_id=runner_id,
        poll_interval=settings.poll_interval_seconds,
        next_stage=settings.next_stage,
    )

    consecutive_errors = 0
    last_heartbeat = utc_now()

    while not coordinator.is_stopping:
        cycle_id = str(uuid.uuid4())

        try:
            _collected, _skipped, _errors = _collect_once(
                settings, runner_id, host_id, cycle_id
            )
            if _errors:
                consecutive_errors += _errors
                files_failed_total.labels(
                    service=SERVICE_NAME, stage="intake", error_type="collect_cycle"
                ).inc(_errors)
            else:
                consecutive_errors = 0

        except Exception as exc:
            consecutive_errors += 1
            logger.exception("unexpected error in runner loop", error=str(exc))

        if consecutive_errors >= settings.max_consecutive_errors:
            logger.error(
                "too many consecutive errors — stopping",
                consecutive_errors=consecutive_errors,
            )
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

        _sleep_interruptible(settings.poll_interval_seconds, coordinator)

    logger.info("babelshark runner stopped")


def _deregister(runner_id: str) -> None:
    try:
        with get_session() as session:
            RunnerRegistryRepository(session).deregister(runner_id)
    except Exception:
        pass


def _sleep_interruptible(seconds: int, coordinator: ShutdownCoordinator) -> None:
    """Sleep in 1-second ticks so SIGTERM is handled promptly."""
    for _ in range(seconds):
        if coordinator.is_stopping:
            break
        time.sleep(1)
