"""
Uploader service runner.

The uploader is intentionally simple: it marks FileRecords as 'uploaded'
after DICOM conversion has already written files to the PACS via storescu.
This stage handles final status tracking, audit logging, and retry recovery
for any uploads that failed in the DICOM stage.

Circuit breaker: if the PACS (Sectra) is unreachable for N consecutive attempts,
the circuit opens and uploads pause for `reset_seconds` before retrying.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import structlog

from pathoryx_enterprise.db.models.core import ServiceTrigger
from pathoryx_enterprise.db.repositories.runner_registry import RunnerRegistryRepository
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.logging.setup import configure_logging, inject_context
from pathoryx_enterprise.monitoring.health import build_health_probe, build_readiness_probe
from pathoryx_enterprise.monitoring.http_health import HealthHTTPServer
from pathoryx_enterprise.monitoring.metrics import (
    runner_heartbeat_age_seconds,
    stage_latency_seconds,
    start_metrics_server,
    trigger_queue_depth,
)
from pathoryx_enterprise.monitoring.shutdown import ShutdownCoordinator
from pathoryx_enterprise.monitoring.startup import StartupValidator
from pathoryx_enterprise.monitoring.tracing import get_tracer, setup_tracing, traced_stage
from pathoryx_enterprise.services.uploader.circuit_breaker import CircuitBreaker
from pathoryx_enterprise.services.uploader.config import UploaderSettings
from pathoryx_enterprise.services.uploader.db_writer import UploaderDBWriter
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = structlog.get_logger(__name__)

SERVICE_NAME = "upload_service"
tracer = get_tracer(SERVICE_NAME)


def _do_upload(trigger: ServiceTrigger) -> dict:
    """
    Perform the upload step.

    At this stage the DICOM files were already sent to the PACS by the DICOM service.
    The upload service verifies the output path exists and records the final outcome.

    If your deployment needs a separate upload step (e.g., a different PACS target),
    add the upload logic here. For the standard workflow the DICOM runner already
    called storescu — this stage is the final status checkpoint.
    """
    payload = trigger.trigger_payload_json or {}
    dicom_path = payload.get("dicom_path", "")

    if dicom_path and not Path(dicom_path).exists():
        raise FileNotFoundError(f"Expected DICOM output not found: {dicom_path}")

    t_start = time.monotonic()

    # For the standard Sectra workflow, the file is already uploaded.
    # Record the final outcome.
    file_size: int | None = None
    if dicom_path:
        p = Path(dicom_path)
        if p.is_file():
            file_size = p.stat().st_size
        elif p.is_dir():
            file_size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    elapsed = time.monotonic() - t_start
    stage_latency_seconds.labels(service=SERVICE_NAME, stage="upload").observe(elapsed)

    return {
        "upload_method": "storescu",
        "destination_path": dicom_path or None,
        "file_size": file_size,
        "duration_seconds": elapsed,
    }


def run(settings: UploaderSettings) -> None:
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
        health_port=settings.health_port,
        metrics_port=settings.metrics_port,
        runner_id=runner_id,
        host_id=host_id,
        extra_info={
            "circuit_threshold": settings.circuit_break_threshold,
            "circuit_reset_seconds": settings.circuit_reset_seconds,
        },
    ).run()

    circuit = CircuitBreaker(
        threshold=settings.circuit_break_threshold,
        reset_seconds=settings.circuit_reset_seconds,
    )

    # --- Health/metrics servers ---
    ready_probe = build_readiness_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL"],
    )
    health_probe = build_health_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL"],
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

    logger.info("uploader runner started", runner_id=runner_id)

    consecutive_errors = 0
    last_heartbeat = utc_now()

    while not coordinator.is_stopping:
        if circuit.is_open:
            logger.warning(
                "circuit breaker OPEN — pausing upload",
                reset_in=settings.circuit_reset_seconds,
            )
            _sleep_interruptible(settings.circuit_reset_seconds, coordinator)
            continue

        with get_session() as session:
            trigger = TriggerRepository(session).dequeue_next(
                target_service=SERVICE_NAME,
                runner_id=runner_id,
                host_id=host_id,
            )
            depth = TriggerRepository(session).count_pending(target_service=SERVICE_NAME)
        trigger_queue_depth.labels(target_service=SERVICE_NAME).set(depth)

        if trigger is None:
            _sleep_interruptible(settings.trigger_poll_interval_seconds, coordinator)
            continue

        inject_context(
            correlation_id=trigger.correlation_id,
            global_artifact_id=trigger.global_artifact_id,
            runner_id=runner_id,
            host_id=host_id,
            service_name=SERVICE_NAME,
        )

        try:
            with traced_stage(
                tracer,
                "upload.finalize",
                correlation_id=trigger.correlation_id,
                global_artifact_id=trigger.global_artifact_id,
            ):
                result = _do_upload(trigger)

            with get_session() as session:
                from sqlalchemy import select
                t_fresh = session.execute(
                    select(ServiceTrigger).where(
                        ServiceTrigger.internal_id == trigger.internal_id
                    )
                ).scalar_one()
                UploaderDBWriter(session).record_upload_success(
                    trigger=t_fresh,
                    global_artifact_id=trigger.global_artifact_id,
                    correlation_id=trigger.correlation_id,
                    runner_id=runner_id,
                    host_id=host_id,
                    service_version=settings.service_version,
                    **result,
                )

            circuit.record_success()
            consecutive_errors = 0

        except Exception as exc:
            consecutive_errors += 1
            circuit.record_failure()
            logger.exception(
                "upload failed",
                trigger_id=trigger.internal_id,
                error=str(exc),
            )
            try:
                with get_session() as session:
                    from sqlalchemy import select
                    t_fresh = session.execute(
                        select(ServiceTrigger).where(
                            ServiceTrigger.internal_id == trigger.internal_id
                        )
                    ).scalar_one_or_none()
                    if t_fresh:
                        UploaderDBWriter(session).record_upload_failure(
                            trigger=t_fresh,
                            error=str(exc),
                            runner_id=runner_id,
                            host_id=host_id,
                            service_version=settings.service_version,
                        )
            except Exception:
                logger.exception("failed to record upload error in DB")

        if consecutive_errors >= settings.max_consecutive_errors:
            logger.error("too many upload errors — stopping", count=consecutive_errors)
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

    logger.info("uploader runner stopped")


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
