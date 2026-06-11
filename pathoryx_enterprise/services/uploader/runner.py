"""
Uploader service runner.

This service owns ALL Sectra PACS C-STORE transmission.

Architecture (Phase 11C):
  DICOM service   → converts WSI → DICOM folder, enqueues upload_service trigger.
  Upload service  → executes storescu C-STORE to Sectra PACS, verifies DIMSE 0x0000,
                    writes confirmed result to PostgreSQL.

Guarantee:
  upload_status = 'uploaded' is ONLY written to the database after:
    1. storescu process exits with return code 0.
    2. DIMSE status 0x0000 (Success) or an acceptable warning (0xB000, 0xB006,
       0xB007) is parsed from storescu output.
  Any other outcome raises CStoreFailedError, which causes the runner to write
  upload_failed and the slide remains eligible for retry.

dry_run mode (SECTRA_DRY_RUN=true, the default):
  storescu commands are built and logged but NOT executed.
  upload_method is recorded as 'storescu_dry_run' in the database.
  This is the safe default for development / staging environments.
  Set SECTRA_DRY_RUN=false in production only after verifying:
    ping path-pacs2
    Test-NetConnection path-pacs2 -Port 32001   (Windows)
    nc -zv path-pacs2 32001                     (Linux)

Circuit breaker:
  After N consecutive storescu failures the circuit opens and all upload attempts
  pause for `reset_seconds` before retrying.  This prevents flooding a PACS that
  is temporarily unreachable.

File logging:
  A RotatingFileHandler is attached at startup via add_file_handler().
  All structlog output — including per-batch cstore_batch_executed events from
  upload_utils — flows to data/logs/upload.log (or PATHORYX_LOG_DIR/upload.log).
  Every upload_result row stored in PostgreSQL carries the log_file path and
  correlation_id in response_summary (JSONB) for cross-referencing.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import structlog
from sqlalchemy import select

from pathoryx_enterprise.db.models.core import ServiceTrigger
from pathoryx_enterprise.db.repositories.runner_registry import RunnerRegistryRepository
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.logging.setup import add_file_handler, configure_logging, inject_context
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
from pathoryx_enterprise.services.dicom.upload_utils import (
    build_cstore_commands,
    run_all_cstore_batches,
)
from pathoryx_enterprise.services.uploader.circuit_breaker import CircuitBreaker
from pathoryx_enterprise.services.uploader.config import UploaderSettings
from pathoryx_enterprise.services.uploader.db_writer import UploaderDBWriter
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id

logger = structlog.get_logger(__name__)

SERVICE_NAME = "upload_service"
tracer = get_tracer(SERVICE_NAME)


class CStoreFailedError(RuntimeError):
    """
    Raised by _do_upload when storescu exits non-zero OR when the DIMSE status
    returned by the PACS is not in the acceptable set (0x0000, 0xB000, 0xB006,
    0xB007).  The runner treats this as a retryable failure.
    """


def _do_upload(
    trigger: ServiceTrigger,
    settings: UploaderSettings,
    log_file: Path,
) -> dict:
    """
    Execute Sectra PACS C-STORE for the DICOM files referenced by this trigger.

    Reads  trigger.trigger_payload_json["dicom_path"]  — the folder (or file)
    written by the DICOM service.

    dry_run=True  (SECTRA_DRY_RUN=true):
        Builds and logs the storescu commands but does NOT execute them.
        Returns a result dict with upload_method='storescu_dry_run'.

    dry_run=False (SECTRA_DRY_RUN=false):
        Calls storescu for each batch, verifies exit code 0, parses DIMSE status,
        and raises CStoreFailedError on any failure.
        Returns a result dict including full DIMSE evidence.

    The returned dict is unpacked by the caller into UploaderDBWriter.record_upload_success().
    The dimse_evidence sub-dict includes correlation_id and log_file for every row
    so that upload_results.response_summary can be cross-referenced with the log file.
    Any exception propagates to the caller's retry loop.
    """
    payload = trigger.trigger_payload_json or {}
    dicom_path_str = (payload.get("dicom_path") or "").strip()

    if not dicom_path_str:
        raise ValueError(
            f"trigger {trigger.internal_id}: trigger_payload_json is missing 'dicom_path'"
        )

    dicom_path = Path(dicom_path_str)
    if not dicom_path.exists():
        raise FileNotFoundError(
            f"trigger {trigger.internal_id}: DICOM output path not found: {dicom_path}"
        )

    # Measure total file size for throughput metrics.
    file_size: int | None = None
    if dicom_path.is_file():
        file_size = dicom_path.stat().st_size
    elif dicom_path.is_dir():
        file_size = sum(f.stat().st_size for f in dicom_path.rglob("*") if f.is_file())

    # Count DCM files for audit logging.
    if dicom_path.is_file():
        dcm_file_count = 1
    else:
        dcm_file_count = len(list(dicom_path.rglob("*.dcm")))

    t_start = time.monotonic()

    # ── Build storescu command list ───────────────────────────────────────────
    # build_cstore_commands returns [] when no .dcm files are found.
    commands = build_cstore_commands(
        input_path=dicom_path,
        host=settings.sectra_host,
        port=settings.sectra_port,
        local_ae=settings.sectra_local_ae,
        remote_ae=settings.sectra_remote_ae,
        cstore_bin=settings.cstore_bin,
        batch_size=settings.cstore_batch_size,
    )

    if not commands:
        raise FileNotFoundError(
            f"trigger {trigger.internal_id}: no .dcm files found under {dicom_path}"
        )

    # ── Audit log — emitted regardless of dry_run ─────────────────────────────
    logger.info(
        "[UPLOADER] C-STORE plan",
        dry_run=settings.dry_run,
        pacs_host=settings.sectra_host or "(not configured)",
        pacs_port=settings.sectra_port,
        remote_ae=settings.sectra_remote_ae or "(not configured)",
        local_ae=settings.sectra_local_ae or "(not configured)",
        cstore_bin=settings.cstore_bin,
        dicom_path=str(dicom_path),
        dcm_file_count=dcm_file_count,
        batches=len(commands),
        trigger_id=trigger.internal_id,
        correlation_id=trigger.correlation_id,
        log_file=str(log_file),
        first_command=commands[0] if commands else [],
    )

    # ── Dry-run branch ────────────────────────────────────────────────────────
    if settings.dry_run:
        elapsed = time.monotonic() - t_start
        logger.warning(
            "[UPLOADER][DRY-RUN] C-STORE NOT executed. "
            "SECTRA_DRY_RUN=true — set it to false and configure "
            "SECTRA_HOST, SECTRA_REMOTE_AE, SECTRA_LOCAL_AE to enable "
            "real PACS upload.",
            pacs_host=settings.sectra_host or "(not configured)",
            pacs_port=settings.sectra_port,
            remote_ae=settings.sectra_remote_ae or "(not configured)",
            local_ae=settings.sectra_local_ae or "(not configured)",
            dicom_path=str(dicom_path),
            dcm_file_count=dcm_file_count,
        )
        stage_latency_seconds.labels(service=SERVICE_NAME, stage="upload").observe(elapsed)
        return {
            "upload_method":    "storescu_dry_run",
            "destination_path": dicom_path_str,
            "file_size":        file_size,
            "duration_seconds": elapsed,
            "dimse_evidence": {
                "dry_run":        True,
                "pacs_host":      settings.sectra_host or "",
                "pacs_port":      settings.sectra_port,
                "remote_ae":      settings.sectra_remote_ae or "",
                "local_ae":       settings.sectra_local_ae or "",
                "correlation_id": str(trigger.correlation_id or ""),
                "log_file":       str(log_file),
            },
        }

    # ── Live C-STORE branch ───────────────────────────────────────────────────
    logger.info(
        "[UPLOADER] executing C-STORE to Sectra PACS",
        pacs_host=settings.sectra_host,
        pacs_port=settings.sectra_port,
        remote_ae=settings.sectra_remote_ae,
        local_ae=settings.sectra_local_ae,
        cstore_bin=settings.cstore_bin,
        batches=len(commands),
        dcm_file_count=dcm_file_count,
        trigger_id=trigger.internal_id,
        correlation_id=trigger.correlation_id,
        log_file=str(log_file),
    )

    all_ok, batch_results = run_all_cstore_batches(
        commands=commands,
        timeout_seconds=settings.upload_timeout_seconds,
    )

    elapsed = time.monotonic() - t_start
    stage_latency_seconds.labels(service=SERVICE_NAME, stage="upload").observe(elapsed)

    # ── Per-batch summary logging (detail is in cstore_batch_executed events) ─
    for batch in batch_results:
        log_fn = logger.info if batch["batch_ok"] else logger.error
        log_fn(
            "[UPLOADER] C-STORE batch result",
            batch_index=batch["batch_index"],
            returncode=batch["returncode"],
            batch_ok=batch["batch_ok"],
            dimse_statuses=batch["dimse_statuses"],
            dimse_reason=batch["dimse_reason"],
            stdout_tail=(batch["stdout"] or "")[-500:],
            stderr_tail=(batch["stderr"] or "")[-500:],
        )

    # ── Failure handling ──────────────────────────────────────────────────────
    if not all_ok:
        failed = next(b for b in batch_results if not b["batch_ok"])
        raise CStoreFailedError(
            f"trigger {trigger.internal_id}: storescu failed — "
            f"batch={failed['batch_index']}, "
            f"exit_code={failed['returncode']}, "
            f"dimse={failed['dimse_statuses']}, "
            f"reason={failed['dimse_reason']}, "
            f"stderr={failed['stderr'][-300:]}"
        )

    # ── Success ───────────────────────────────────────────────────────────────
    all_dimse = [s for b in batch_results for s in b["dimse_statuses"]]
    logger.info(
        "[UPLOADER] C-STORE completed — PACS accepted all files",
        pacs_host=settings.sectra_host,
        pacs_port=settings.sectra_port,
        remote_ae=settings.sectra_remote_ae,
        local_ae=settings.sectra_local_ae,
        batches_sent=len(batch_results),
        dcm_file_count=dcm_file_count,
        duration_seconds=round(elapsed, 2),
        dimse_statuses=all_dimse,
        trigger_id=trigger.internal_id,
        correlation_id=trigger.correlation_id,
        log_file=str(log_file),
    )

    return {
        "upload_method":    "storescu",
        "destination_path": dicom_path_str,
        "file_size":        file_size,
        "duration_seconds": elapsed,
        "dimse_evidence": {
            "dry_run":        False,
            "pacs_host":      settings.sectra_host,
            "pacs_port":      settings.sectra_port,
            "remote_ae":      settings.sectra_remote_ae,
            "local_ae":       settings.sectra_local_ae,
            "cstore_bin":     settings.cstore_bin,
            "batches_sent":   len(batch_results),
            "dcm_file_count": dcm_file_count,
            "dimse_statuses": all_dimse,
            "all_ok":         True,
            "correlation_id": str(trigger.correlation_id or ""),
            "log_file":       str(log_file),
        },
    }


def run(settings: UploaderSettings) -> None:
    configure_logging(
        service_name=SERVICE_NAME,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        json_output=settings.environment != "development",
    )
    log_file = add_file_handler("upload", log_dir=settings.log_dir)
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
            "dry_run":                settings.dry_run,
            "sectra_host":            settings.sectra_host or "(not set)",
            "sectra_port":            settings.sectra_port,
            "remote_ae":              settings.sectra_remote_ae or "(not set)",
            "local_ae":               settings.sectra_local_ae or "(not set)",
            "cstore_bin":             settings.cstore_bin,
            "cstore_batch_size":      settings.cstore_batch_size,
            "upload_timeout_seconds": settings.upload_timeout_seconds,
            "circuit_threshold":      settings.circuit_break_threshold,
            "circuit_reset_seconds":  settings.circuit_reset_seconds,
            "log_file":               str(log_file),
        },
    ).run()

    if settings.dry_run:
        logger.warning(
            "[UPLOADER] STARTING IN DRY-RUN MODE. "
            "No C-STORE will be executed. "
            "Set SECTRA_DRY_RUN=false in .env to enable production PACS upload.",
            log_file=str(log_file),
        )
    else:
        logger.info(
            "[UPLOADER] STARTING IN LIVE MODE — C-STORE will transmit to Sectra PACS",
            pacs_host=settings.sectra_host,
            pacs_port=settings.sectra_port,
            remote_ae=settings.sectra_remote_ae,
            local_ae=settings.sectra_local_ae,
            cstore_bin=settings.cstore_bin,
            log_file=str(log_file),
        )

    circuit = CircuitBreaker(
        threshold=settings.circuit_break_threshold,
        reset_seconds=settings.circuit_reset_seconds,
    )

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

    logger.info(
        "uploader runner started",
        runner_id=runner_id,
        dry_run=settings.dry_run,
        log_file=str(log_file),
    )

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
                priority_aware=True,
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

        # Write "uploading" status to dashboard queue before starting work.
        # Wrapped in its own try/except — dashboard sync failures must not abort the pipeline.
        try:
            with get_session() as session:
                t_for_start = session.execute(
                    select(ServiceTrigger).where(
                        ServiceTrigger.internal_id == trigger.internal_id
                    )
                ).scalar_one_or_none()
                if t_for_start:
                    UploaderDBWriter(session).record_upload_started(
                        trigger=t_for_start,
                        host_id=host_id,
                    )
        except Exception:
            logger.warning(
                "failed to write upload started status",
                trigger_id=trigger.internal_id,
            )

        # ── Retry loop ────────────────────────────────────────────────────────
        last_exc: Exception | None = None
        upload_result: dict | None = None

        for attempt in range(settings.max_retries):
            try:
                with traced_stage(
                    tracer,
                    "upload.cstore",
                    correlation_id=trigger.correlation_id,
                    global_artifact_id=trigger.global_artifact_id,
                ):
                    upload_result = _do_upload(trigger, settings, log_file)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "upload attempt failed",
                    attempt=attempt + 1,
                    max_retries=settings.max_retries,
                    trigger_id=trigger.internal_id,
                    error=str(exc),
                )
                if attempt < settings.max_retries - 1 and not coordinator.is_stopping:
                    backoff = min(5 * (2 ** attempt), 60)
                    _sleep_interruptible(backoff, coordinator)

        # ── Persist result ────────────────────────────────────────────────────
        if last_exc is None and upload_result is not None:
            # Pop dimse_evidence before unpacking the remaining keys into the DB writer.
            dimse_evidence = upload_result.pop("dimse_evidence", None)
            with get_session() as session:
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
                    retry_count=0,
                    dimse_evidence=dimse_evidence,
                    **upload_result,
                )
            circuit.record_success()
            consecutive_errors = 0
        else:
            consecutive_errors += 1
            circuit.record_failure()
            logger.error(
                "upload failed after all retries",
                attempts=settings.max_retries,
                trigger_id=trigger.internal_id,
                error=str(last_exc),
            )
            try:
                with get_session() as session:
                    t_fresh = session.execute(
                        select(ServiceTrigger).where(
                            ServiceTrigger.internal_id == trigger.internal_id
                        )
                    ).scalar_one_or_none()
                    if t_fresh:
                        UploaderDBWriter(session).record_upload_failure(
                            trigger=t_fresh,
                            error=str(last_exc) if last_exc else "unknown",
                            retry_count=settings.max_retries,
                            runner_id=runner_id,
                            host_id=host_id,
                            service_version=settings.service_version,
                        )
            except Exception:
                logger.exception("failed to record upload error in DB")

        if consecutive_errors >= settings.max_consecutive_errors:
            logger.error(
                "too many consecutive upload errors — stopping",
                count=consecutive_errors,
            )
            coordinator.trigger()
            break

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
