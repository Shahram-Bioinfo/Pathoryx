"""
DICOM conversion runner — Phase 11C: conversion only, no storescu.

Architecture (Phase 11C)
────────────────────────
DICOM service responsibility:
  1. Dequeue trigger from core.service_trigger (target=dicom_service).
  2. Convert WSI file to DICOM folder via wsidicomizer + IDS7 header patch.
  3. Write dicomizer.conversion_results (completed or failed).
  4. Update core.file_records.status → dicom_done on success.
  5. Enqueue upload_service trigger with {dicom_path, source_path, global_artifact_id}.
  6. Mark own trigger completed.

Upload service responsibility (separate process):
  Owns all storescu / C-STORE to Sectra PACS. DICOM runner never calls storescu.

upload_utils.py is preserved for use by the upload service — not imported here.

Key design:
  • Native ConversionService from engine — no pipeline.* or utils.wsidicom_utils.
  • FOR UPDATE SKIP LOCKED dequeue — safe under concurrent workers.
  • SIGTERM handled gracefully — in-flight conversion finishes before exit.
  • global_artifact_id from trigger threaded through to ConversionResult (lineage fix).
  • SECTRA env vars are optional (only required for upload_service or legacy mode).
"""
from __future__ import annotations

import os
import time
import uuid
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
from pathoryx_enterprise.services.dicom.config import DICOMSettings, load_dicom_engine_config
from pathoryx_enterprise.services.dicom.db_writer import DICOMDBWriter
from pathoryx_enterprise.services.dicom.engine.services.conversion_service import ConversionService
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = structlog.get_logger(__name__)

SERVICE_NAME = "dicom_service"
tracer = get_tracer(SERVICE_NAME)


class _ConversionFailedError(RuntimeError):
    """
    Raised by _process_trigger when ConversionService returns a non-success status.
    Carries structured failure_context so the runner passes it to record_conversion_failure()
    and dicomizer.conversion_results is written with the correct error_type.
    """
    def __init__(
        self,
        message: str,
        *,
        failure_context: dict | None = None,
        source_path: str = "",
    ) -> None:
        super().__init__(message)
        self.failure_context: dict = failure_context or {}
        self.source_path: str = source_path


def _process_trigger(
    trigger: ServiceTrigger,
    dicom_config: object,
    runner_id: str,
    host_id: str,
) -> dict:
    """
    Convert one slide to a DICOM folder. Returns a result dict.

    Phase 11C: conversion only — no storescu, no upload.
    The returned dict is passed to DICOMDBWriter.record_conversion_success()
    which handles DB writes and upload trigger dispatch.
    """
    payload = trigger.trigger_payload_json or {}
    source_path = (payload.get("source_path") or "").strip()
    if not source_path:
        raise ValueError(f"trigger {trigger.internal_id} missing source_path in payload")

    scanner_id = payload.get("scanner_id")

    inject_context(
        correlation_id=trigger.correlation_id,
        global_artifact_id=trigger.global_artifact_id,
        runner_id=runner_id,
        host_id=host_id,
        service_name=SERVICE_NAME,
    )

    t_start = time.monotonic()

    with traced_stage(
        tracer,
        "dicom.convert",
        correlation_id=trigger.correlation_id,
        global_artifact_id=trigger.global_artifact_id,
        extra_attrs={"trigger_id": str(trigger.internal_id)},
    ):
        conv_svc = ConversionService(dicom_config)
        conversion_result = conv_svc.convert(
            source_path,
            global_artifact_id=trigger.global_artifact_id,
        )

    if conversion_result.status.value not in ("completed", "skipped_already_dicom"):
        raise _ConversionFailedError(
            f"conversion failed for {source_path}: "
            f"status={conversion_result.status.value}",
            failure_context=getattr(conversion_result, "failure_context", None),
            source_path=source_path,
        )

    elapsed = time.monotonic() - t_start
    stage_latency_seconds.labels(service=SERVICE_NAME, stage="dicom.convert").observe(elapsed)

    return {
        "output_path": str(conversion_result.output_path),
        "source_path": source_path,
        "scanner_id": scanner_id,
        "conversion_tool": getattr(conversion_result, "conversion_tool", None),
        "conversion_tool_version": getattr(conversion_result, "conversion_tool_version", None),
        "input_file_size": getattr(conversion_result, "input_file_size", None),
        "output_file_size": getattr(conversion_result, "output_file_size", None),
        "duration_seconds": elapsed,
        "metadata_summary": getattr(conversion_result, "metadata_summary", None),
    }


def run(settings: DICOMSettings) -> None:
    configure_logging(
        service_name=SERVICE_NAME,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        json_output=settings.environment != "development",
    )
    setup_tracing(SERVICE_NAME, settings.service_version)

    runner_id = deterministic_artifact_id(SERVICE_NAME, settings.environment, "runner")
    host_id: str = __import__("socket").gethostname()

    # Phase 11C: only DATABASE_URL and DICOM_CONFIG_PATH are strictly required.
    # SECTRA settings are logged but not enforced — upload is handled by upload_service.
    StartupValidator(
        service_name=SERVICE_NAME,
        version=settings.service_version,
        environment=settings.environment,
        required_env_vars=["DATABASE_URL"],
        config_paths={"DICOM_CONFIG_PATH": settings.dicom_config_path},
        health_port=settings.health_port,
        metrics_port=settings.metrics_port,
        runner_id=runner_id,
        host_id=host_id,
        extra_info={
            "perform_upload": settings.perform_upload,
            "sectra_host": settings.sectra_host or "(not set)",
        },
    ).run()

    logger.info("loading DICOM engine config", config_path=settings.dicom_config_path)
    dicom_config = load_dicom_engine_config(settings.dicom_config_path)
    logger.info(
        "DICOM engine config loaded",
        conversion_method=dicom_config.conversion.image_conversion_method,
        output_root=str(dicom_config.paths.output_root),
        lis_enabled=dicom_config.lis.enabled,
        wsidicomizer=dicom_config.wsidicomizer.executable,
    )

    if settings.perform_upload:
        logger.warning(
            "DICOM_PERFORM_UPLOAD=true — storescu will run in this process. "
            "Recommended: keep DICOM_PERFORM_UPLOAD=false and use upload_service."
        )
    else:
        logger.info(
            "DICOM runner in conversion-only mode (perform_upload=false). "
            "Upload triggers will be dispatched to upload_service."
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
        "DICOM runner started",
        runner_id=runner_id,
        perform_upload=settings.perform_upload,
    )

    consecutive_errors = 0
    last_heartbeat = utc_now()

    while not coordinator.is_stopping:
        with get_session() as session:
            trigger = TriggerRepository(session).dequeue_next(
                target_service=SERVICE_NAME,
                runner_id=runner_id,
                host_id=host_id,
                priority_aware=True,
            )
            depth = TriggerRepository(session).count_pending(target_service=SERVICE_NAME)
        trigger_queue_depth.labels(target_service=SERVICE_NAME).set(depth)

        if trigger is None:
            _sleep_interruptible(settings.trigger_poll_interval_seconds, coordinator)
            continue

        try:
            result = _process_trigger(
                trigger=trigger,
                dicom_config=dicom_config,
                runner_id=runner_id,
                host_id=host_id,
            )

            with get_session() as session:
                from sqlalchemy import select
                t_fresh = session.execute(
                    select(ServiceTrigger).where(
                        ServiceTrigger.internal_id == trigger.internal_id
                    )
                ).scalar_one()
                DICOMDBWriter(session).record_conversion_success(
                    trigger=t_fresh,
                    global_artifact_id=trigger.global_artifact_id,
                    correlation_id=trigger.correlation_id,
                    runner_id=runner_id,
                    host_id=host_id,
                    service_version=settings.service_version,
                    **result,
                )

            consecutive_errors = 0

        except _ConversionFailedError as exc:
            consecutive_errors += 1
            logger.error(
                "DICOM conversion failed",
                trigger_id=trigger.internal_id,
                error_type=exc.failure_context.get("error_type"),
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
                        DICOMDBWriter(session).record_conversion_failure(
                            trigger=t_fresh,
                            error=str(exc),
                            failure_context=exc.failure_context,
                            source_path=exc.source_path,
                            runner_id=runner_id,
                            host_id=host_id,
                            service_version=settings.service_version,
                        )
            except Exception:
                logger.exception("failed to record DICOM conversion failure in DB")

        except Exception as exc:
            consecutive_errors += 1
            logger.exception(
                "DICOM processing failed",
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
                        DICOMDBWriter(session).record_conversion_failure(
                            trigger=t_fresh,
                            error=str(exc),
                            runner_id=runner_id,
                            host_id=host_id,
                            service_version=settings.service_version,
                        )
            except Exception:
                logger.exception("failed to record DICOM error in DB")

        if consecutive_errors >= settings.max_consecutive_errors:
            logger.error(
                "too many consecutive DICOM errors — stopping",
                count=consecutive_errors,
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

    logger.info("DICOM runner stopped")


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
