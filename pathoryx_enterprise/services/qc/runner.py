"""
QC service — trigger-mode runner.

Architecture
────────────
ModelRegistry is instantiated ONCE at startup.  Weights are loaded on the
first slide (cached_property) and reused for every subsequent slide.

FOR UPDATE SKIP LOCKED guarantees each trigger is claimed by exactly one
worker under concurrent deployment.

Each trigger is processed in its own DB session so a single bad slide cannot
roll back unrelated commits.

SIGTERM is handled gracefully: in-flight inference finishes, then the loop
exits cleanly.

Phase 6 additions
─────────────────
• source_path resolved at dequeue time with FileRecord fallback.
• QCServiceConfig loaded at startup for scanner-policy next_service/next_stage.
• ResourceMonitor wraps inference in _process_one to capture memory/CPU.
• _process_one returns a structured dict (never raises) so error details and
  resource metrics are always available to the result handler.
• Exception type is inspected for precise error_reason classification.
• All Phase 5 fields are explicitly passed to QCDBWriter.

On-model-fail trigger semantics
─────────────────────────────────
Model decision "fail" (blur detected, penmark detected, …) → trigger.trigger_status
set to "completed".  QC ran successfully; the slide did not pass.  No retry.

Runtime error (OpenSlide crash, file missing, …) → trigger.trigger_status set to
"failed".  QC could not process the slide.  Retry up to max_retries.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog
from sqlalchemy import select

from pathoryx_enterprise.db.models.core import FileRecord, ServiceTrigger
from pathoryx_enterprise.db.repositories.runner_registry import RunnerRegistryRepository
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.logging.setup import add_file_handler, configure_logging, inject_context
from pathoryx_enterprise.monitoring.health import (
    build_health_probe,
    build_readiness_probe,
    check_model_weights,
)
from pathoryx_enterprise.monitoring.http_health import HealthHTTPServer
from pathoryx_enterprise.monitoring.metrics import (
    files_skipped_total,
    runner_heartbeat_age_seconds,
    stage_latency_seconds,
    start_metrics_server,
    trigger_queue_depth,
)
from pathoryx_enterprise.monitoring.shutdown import ShutdownCoordinator
from pathoryx_enterprise.monitoring.startup import StartupValidator
from pathoryx_enterprise.monitoring.tracing import get_tracer, setup_tracing, traced_stage
from pathoryx_enterprise.services.qc.config import (
    QCServiceConfig,
    QCSettings,
    load_qc_service_config,
)
from pathoryx_enterprise.services.qc.db_writer import QCDBWriter
from pathoryx_enterprise.utils.datetime_utils import utc_now
from pathoryx_enterprise.utils.fingerprint import deterministic_artifact_id
from pathoryx_enterprise.utils.process_metrics import ResourceMonitor

logger = structlog.get_logger(__name__)

SERVICE_NAME = "qc_service"
tracer = get_tracer(SERVICE_NAME)


# =============================================================================
# Helper functions
# =============================================================================

def _load_qc_deps(qc_config_path: str):
    """
    Import QC inference + decision services from the bundled engine package.
    The config path is used to build AppConfig so models can be loaded.
    """
    from pathoryx_enterprise.services.qc.engine.config import load_config
    from pathoryx_enterprise.services.qc.engine.services.model_registry import ModelRegistry
    from pathoryx_enterprise.services.qc.engine.services.decision_service import SlideQcDecisionService
    from pathoryx_enterprise.services.qc.engine.services.qc_inference_service import SlideQcInferenceService

    config = load_config(qc_config_path)
    return config, ModelRegistry, SlideQcInferenceService, SlideQcDecisionService


def _classify_exc_type(exc: Exception) -> str:
    """
    Classify exception into a queryable error_reason string using the
    exception type (more reliable than string matching).

    Returns one of:
      "unsupported_format" — OpenSlideUnsupportedFormatError
      "file_missing"       — FileNotFoundError or path resolution failure
      "openslide_error"    — other openslide errors
      "inference_error"    — everything else
    """
    exc_type: str = type(exc).__name__
    exc_module: str = getattr(type(exc), "__module__", "") or ""
    exc_str: str = str(exc).lower()

    if "UnsupportedFormat" in exc_type:
        return "unsupported_format"
    if isinstance(exc, FileNotFoundError):
        return "file_missing"
    if "no such file" in exc_str or "does not exist" in exc_str:
        return "file_missing"
    if "openslide" in exc_module.lower() or "OpenSlide" in exc_type:
        return "openslide_error"
    # Fallback: check message for OpenSlide-style phrasing
    if "unsupported or missing" in exc_str:
        return "unsupported_format"
    return "inference_error"


def _resolve_source_path(
    trigger: ServiceTrigger,
    file_record: Optional[FileRecord],
) -> tuple[str, bool]:
    """
    Resolve the slide source_path for this trigger.

    Priority:
      1. trigger_payload_json["source_path"]   (Phase 4 BabelShark fix)
      2. file_record.current_file_path
      3. file_record.canonical_path

    Returns (path, fallback_used).  path may be empty string if nothing found.
    """
    payload = trigger.trigger_payload_json or {}
    path = (payload.get("source_path") or "").strip()
    if path:
        return path, False

    # Fallback: read from FileRecord
    if file_record is not None:
        for candidate in (file_record.current_file_path, file_record.canonical_path):
            if candidate:
                return str(candidate), True

    return "", True


def _get_next_service(
    qc_service_config: Optional[QCServiceConfig],
    scanner_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """
    Return (next_service, next_stage) for downstream dispatch after QC passes.

    Resolution order — fully config-driven, never hardcoded:
      1. Per-scanner policy (matched by scanner_id or __default__ catch-all)
         → uses policy.next_service / next_stage exactly as configured
         → None in policy = explicitly disabled for that scanner
      2. post_babelshark.next_service / next_stage (global YAML defaults)
         → only reached when no scanner policy matches at all
      3. (None, None) — no config loaded (legacy startup without QC_SERVICE_CONFIG)
         → skip dispatch; log a warning at startup (done in run())

    Returns (None, None) → db_writer skips trigger enqueue.
    """
    if qc_service_config is None:
        # No service config — cannot route without explicit configuration
        return None, None

    # 1. Scanner-specific or __default__ policy
    policy = qc_service_config.get_policy(scanner_id or "__default__")
    if policy is not None:
        # Use policy values exactly — None means this scanner disables dispatch
        return policy.next_service, policy.next_stage

    # 2. No matching policy at all → global post_babelshark defaults
    pb = qc_service_config.post_babelshark
    return pb.next_service, pb.next_stage


def _module_values(module_result) -> Optional[dict]:
    if module_result is None:
        return None
    return getattr(module_result, "values", None)


# =============================================================================
# Core inference worker  (runs inside ThreadPoolExecutor — never raises)
# =============================================================================

def _process_one(
    *,
    trigger_id: int,
    source_path: str,
    global_artifact_id: Optional[str],
    correlation_id: str,
    inference_service: object,
    decision_service_cls: type,
    qc_config: object,
    runner_id: str,
    host_id: str,
    service_version: str,
) -> dict:
    """
    Run QC inference + decision for one slide.  Stateless — no DB access.

    Always returns a result dict; never raises.  Callers inspect
    result["status"] == "ok" | "error" to decide the write path.

    Returned keys (always present):
      status          "ok" | "error"
      started_at      datetime — wall-clock before inference
      finished_at     datetime — wall-clock after inference / after error
      memory_rss_mb   float | None
      cpu_percent_avg float | None

    Additional keys on "ok":
      decision_status, decision_reason, decision_threshold_json,
      final_routed_path, stain_json, penmark_json, bubble_json,
      blur_json, total_duration_seconds, inference_summary

    Additional keys on "error":
      error       str(exc)
      error_reason  classified string
    """
    inject_context(
        correlation_id=correlation_id,
        global_artifact_id=global_artifact_id,
        runner_id=runner_id,
        host_id=host_id,
        service_name=SERVICE_NAME,
    )

    started_at_wall = utc_now()
    monitor = ResourceMonitor()

    # Guard: empty source_path is a file_missing condition before touching OpenSlide
    if not source_path:
        monitor.start()
        snap = monitor.stop()
        return {
            "status": "error",
            "error": "source_path is empty — trigger payload missing source_path and no FileRecord fallback",
            "error_reason": "file_missing",
            "started_at": started_at_wall,
            "finished_at": utc_now(),
            "memory_rss_mb": snap.memory_rss_mb,
            "cpu_percent_avg": snap.cpu_percent_avg,
        }

    source = Path(source_path).resolve()
    t_start = time.monotonic()

    monitor.start()
    try:
        with traced_stage(
            tracer,
            "qc.inference",
            correlation_id=correlation_id,
            global_artifact_id=global_artifact_id,
            extra_attrs={"trigger_id": str(trigger_id)},
        ):
            inference_result = inference_service.process_slide(source)  # type: ignore[union-attr]

        decision = decision_service_cls(qc_config).decide(inference_result, source)
        elapsed = time.monotonic() - t_start
        snap = monitor.stop()
        finished_at_wall = utc_now()

        stage_latency_seconds.labels(service=SERVICE_NAME, stage="qc").observe(elapsed)

        return {
            "status": "ok",
            "decision_status": decision["decision_status"],
            "decision_reason": decision["decision_reason"],
            "decision_threshold_json": decision.get("decision_threshold_json"),
            "final_routed_path": decision.get("final_routed_path"),
            "stain_json": _module_values(inference_result.stain_result),
            "penmark_json": _module_values(inference_result.penmark_result),
            "bubble_json": _module_values(inference_result.bubble_result),
            "blur_json": _module_values(inference_result.blur_result),
            "total_duration_seconds": inference_result.total_duration_seconds,
            "inference_summary": getattr(inference_result, "summary", {}),
            # Phase 6 additions
            "started_at": started_at_wall,
            "finished_at": finished_at_wall,
            "memory_rss_mb": snap.memory_rss_mb,
            "cpu_percent_avg": snap.cpu_percent_avg,
        }

    except Exception as exc:
        snap = monitor.stop()
        finished_at_wall = utc_now()
        error_reason = _classify_exc_type(exc)

        # Refine: OpenSlide raises its own exception for missing files rather than
        # Python FileNotFoundError.  If we got "unsupported_format" but the file
        # does not actually exist on disk, reclassify as "file_missing".
        if error_reason == "unsupported_format" and not source.exists():
            error_reason = "file_missing"

        # Expected conditions (bad file format, missing file) → warning, not exception
        if error_reason in ("unsupported_format", "file_missing"):
            logger.warning(
                "QC slide rejected at read stage",
                trigger_id=trigger_id,
                source_path=source_path,
                error_reason=error_reason,
                error=str(exc),
            )
        else:
            logger.exception(
                "QC inference failed",
                trigger_id=trigger_id,
                source_path=source_path,
                error_reason=error_reason,
            )

        return {
            "status": "error",
            "error": str(exc),
            "error_reason": error_reason,
            "started_at": started_at_wall,
            "finished_at": finished_at_wall,
            "memory_rss_mb": snap.memory_rss_mb,
            "cpu_percent_avg": snap.cpu_percent_avg,
        }


# =============================================================================
# Main runner loop
# =============================================================================

def run(settings: QCSettings) -> None:
    configure_logging(
        service_name=SERVICE_NAME,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        json_output=settings.environment != "development",
    )
    add_file_handler("qc", log_dir=os.environ.get("PATHORYX_LOG_DIR", "data/logs"))
    setup_tracing(SERVICE_NAME, settings.service_version)

    runner_id = deterministic_artifact_id(SERVICE_NAME, settings.environment, "runner")
    host_id: str = __import__("socket").gethostname()

    StartupValidator(
        service_name=SERVICE_NAME,
        version=settings.service_version,
        environment=settings.environment,
        required_env_vars=["DATABASE_URL"],
        config_paths={"QC_CONFIG_PATH": settings.qc_config_path},
        health_port=settings.health_port,
        metrics_port=settings.metrics_port,
        runner_id=runner_id,
        host_id=host_id,
    ).run()

    # ── Load QCServiceConfig (required for config-driven routing) ────────────
    # Without QCServiceConfig, _get_next_service returns (None, None) so no
    # downstream trigger is ever enqueued.  Set QC_SERVICE_CONFIG to enable
    # scanner-policy routing and post_babelshark.next_service defaults.
    qc_service_config: Optional[QCServiceConfig] = None
    if settings.qc_service_config_path:
        try:
            qc_service_config = load_qc_service_config(settings.qc_service_config_path)
            logger.info(
                "QC service config loaded",
                path=settings.qc_service_config_path,
                mode=qc_service_config.service.mode,
                scanner_policies=len(qc_service_config.scanner_policies),
                post_babelshark_next_service=qc_service_config.post_babelshark.next_service,
            )
        except Exception as exc:
            logger.warning(
                "QC service config could not be loaded — downstream dispatch disabled",
                path=settings.qc_service_config_path,
                error=str(exc),
            )
    else:
        logger.warning(
            "QC_SERVICE_CONFIG not set — downstream trigger dispatch disabled; "
            "set QC_SERVICE_CONFIG to enable config-driven routing"
        )

    # ── Load QC adapter deps + build shared ModelRegistry + InferenceService ─
    logger.info("loading QC model registry", config_path=settings.qc_config_path)
    qc_config, ModelRegistryCls, InferenceServiceCls, DecisionServiceCls = _load_qc_deps(
        settings.qc_config_path
    )
    shared_inference_service = InferenceServiceCls(qc_config)
    logger.info("QC model registry ready")

    # ── Health / metrics servers ─────────────────────────────────────────────
    model_weights: dict[str, str] = {}
    if hasattr(qc_config, "models"):
        m = qc_config.models
        for attr in ("penmark_weights", "bubble_weights", "stain_weights", "blur_weights"):
            path = getattr(m, attr, None)
            if path:
                model_weights[attr] = str(path)

    ready_probe = build_readiness_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL", "QC_CONFIG_PATH"],
    )
    health_probe = build_health_probe(
        session_factory=get_session,
        required_env_vars=["DATABASE_URL", "QC_CONFIG_PATH"],
        model_weights=model_weights or None,
    )
    health_server = HealthHTTPServer(
        port=settings.health_port,
        ready_probe=ready_probe,
        health_probe=health_probe,
    )
    health_server.start()
    start_metrics_server(settings.metrics_port, SERVICE_NAME, settings.service_version)

    # ── Graceful shutdown ────────────────────────────────────────────────────
    coordinator = ShutdownCoordinator()
    coordinator.install()
    coordinator.register(health_server.stop)

    # ── Runner registration ──────────────────────────────────────────────────
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
        "QC runner started",
        runner_id=runner_id,
        workers=settings.max_workers,
        poll_interval=settings.trigger_poll_interval_seconds,
        service_config_loaded=qc_service_config is not None,
    )

    consecutive_errors = 0
    last_heartbeat = utc_now()

    with ThreadPoolExecutor(
        max_workers=settings.max_workers, thread_name_prefix="qc-worker"
    ) as pool:
        while not coordinator.is_stopping:
            triggers_dispatched = 0

            # ── Dequeue + source_path resolution ────────────────────────────
            # pending: list of (trigger, resolved_source_path)
            pending: list[tuple[ServiceTrigger, str]] = []

            with get_session() as session:
                repo = TriggerRepository(session)

                for _ in range(settings.max_workers):
                    trigger = repo.dequeue_next(target_service=SERVICE_NAME, priority_aware=True)
                    if trigger is None:
                        break

                    # Advance FileRecord to qc_running within the same session
                    file_rec: Optional[FileRecord] = None
                    if trigger.file_record_internal_id is not None:
                        file_rec = session.execute(
                            select(FileRecord).where(
                                FileRecord.internal_id == trigger.file_record_internal_id
                            )
                        ).scalar_one_or_none()
                        if file_rec is not None and file_rec.status == "qc_pending":
                            file_rec.status = "qc_running"
                            session.flush()

                    # Resolve source_path with FileRecord fallback
                    source_path, fallback_used = _resolve_source_path(trigger, file_rec)
                    if fallback_used:
                        if source_path:
                            logger.warning(
                                "source_path absent from trigger payload — fell back to FileRecord",
                                trigger_id=trigger.internal_id,
                                resolved=source_path,
                            )
                        else:
                            logger.error(
                                "source_path could not be resolved — slide will be marked file_missing",
                                trigger_id=trigger.internal_id,
                            )

                    pending.append((trigger, source_path))

                trigger_queue_depth.labels(target_service=SERVICE_NAME).set(
                    repo.count_pending(target_service=SERVICE_NAME)
                )

            if not pending:
                _sleep_interruptible(settings.trigger_poll_interval_seconds, coordinator)
                continue

            # ── Dispatch to thread pool ──────────────────────────────────────
            futures: dict[Future, ServiceTrigger] = {
                pool.submit(
                    _process_one,
                    trigger_id=t.internal_id,
                    source_path=src,
                    global_artifact_id=t.global_artifact_id,
                    correlation_id=t.correlation_id or str(uuid.uuid4()),
                    inference_service=shared_inference_service,
                    decision_service_cls=DecisionServiceCls,
                    qc_config=qc_config,
                    runner_id=runner_id,
                    host_id=host_id,
                    service_version=settings.service_version,
                ): t
                for t, src in pending
            }

            # ── Handle results ───────────────────────────────────────────────
            for future in as_completed(futures):
                trigger = futures[future]

                # _process_one never raises — result is always a dict
                result: dict = future.result()

                if result["status"] == "ok":
                    # ── QC ran to completion (pass or model-fail) ────────────
                    payload = trigger.trigger_payload_json or {}
                    scanner_id = payload.get("scanner_id")
                    next_svc, next_stg = _get_next_service(qc_service_config, scanner_id)

                    try:
                        with get_session() as session:
                            t_fresh = session.execute(
                                select(ServiceTrigger).where(
                                    ServiceTrigger.internal_id == trigger.internal_id
                                )
                            ).scalar_one()

                            QCDBWriter(session).record_qc_result(
                                trigger=t_fresh,
                                decision_status=result["decision_status"],
                                decision_reason=result["decision_reason"],
                                stain_json=result.get("stain_json"),
                                penmark_json=result.get("penmark_json"),
                                bubble_json=result.get("bubble_json"),
                                blur_json=result.get("blur_json"),
                                decision_threshold_json=result.get("decision_threshold_json"),
                                final_routed_path=result.get("final_routed_path"),
                                total_duration_seconds=result.get("total_duration_seconds"),
                                raw_qc_payload={
                                    "inference_summary": result.get("inference_summary"),
                                },
                                global_artifact_id=trigger.global_artifact_id,
                                correlation_id=trigger.correlation_id,
                                runner_id=runner_id,
                                host_id=host_id,
                                service_version=settings.service_version,
                                # Phase 6: timing + resources from _process_one
                                started_at=result.get("started_at"),
                                finished_at=result.get("finished_at"),
                                memory_rss_mb=result.get("memory_rss_mb"),
                                cpu_percent_avg=result.get("cpu_percent_avg"),
                                # Phase 6: scanner-policy-driven routing
                                next_service=next_svc,
                                next_stage=next_stg,
                            )

                        consecutive_errors = 0
                        triggers_dispatched += 1

                    except Exception as db_exc:
                        consecutive_errors += 1
                        logger.exception(
                            "QC DB write failed after successful inference",
                            trigger_id=trigger.internal_id,
                            error=str(db_exc),
                        )

                else:
                    # ── Runtime error (OpenSlide crash, missing file, …) ─────
                    consecutive_errors += 1
                    logger.error(
                        "QC slide processing error",
                        trigger_id=trigger.internal_id,
                        error_reason=result.get("error_reason"),
                        error=result.get("error"),
                    )

                    try:
                        with get_session() as session:
                            t_fresh = session.execute(
                                select(ServiceTrigger).where(
                                    ServiceTrigger.internal_id == trigger.internal_id
                                )
                            ).scalar_one_or_none()

                            if t_fresh is not None:
                                QCDBWriter(session).record_qc_error(
                                    trigger=t_fresh,
                                    error=result.get("error", "unknown error"),
                                    error_reason=result.get("error_reason"),
                                    finished_at=result.get("finished_at"),
                                    memory_rss_mb=result.get("memory_rss_mb"),
                                    cpu_percent_avg=result.get("cpu_percent_avg"),
                                    global_artifact_id=trigger.global_artifact_id,
                                    correlation_id=trigger.correlation_id,
                                    runner_id=runner_id,
                                    host_id=host_id,
                                    service_version=settings.service_version,
                                )

                    except Exception as db_exc:
                        logger.exception(
                            "failed to record QC error in DB",
                            trigger_id=trigger.internal_id,
                            error=str(db_exc),
                        )

            # ── Consecutive error circuit-breaker ────────────────────────────
            if consecutive_errors >= settings.max_consecutive_errors:
                logger.error(
                    "too many consecutive QC errors — stopping",
                    count=consecutive_errors,
                )
                coordinator.trigger()
                break

            # ── Heartbeat ────────────────────────────────────────────────────
            with get_session() as session:
                RunnerRegistryRepository(session).heartbeat(runner_id)
            age = (utc_now() - last_heartbeat).total_seconds()
            runner_heartbeat_age_seconds.labels(
                runner_id=runner_id, service=SERVICE_NAME
            ).set(age)
            last_heartbeat = utc_now()

    logger.info("QC runner stopped")


# =============================================================================
# Utilities
# =============================================================================

def _deregister(runner_id: str) -> None:
    try:
        with get_session() as session:
            RunnerRegistryRepository(session).deregister(runner_id)
    except Exception:
        pass


def _sleep_interruptible(seconds: int, coordinator: ShutdownCoordinator) -> None:
    """Sleep in 1-second ticks so SIGTERM is handled within one second."""
    for _ in range(seconds):
        if coordinator.is_stopping:
            break
        time.sleep(1)
