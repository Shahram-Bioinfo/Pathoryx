"""
Structured logging for Palantir.

Uses structlog backed by the stdlib logging machinery so that:
  - structlog.stdlib processors (add_logger_name, add_log_level) work correctly
  - Both JSON and human-readable development output are supported
  - Per-task context (correlation_id, artifact_id, run_id) is automatically
    injected into every log line via ContextVar

Usage::

    from pathoryx_enterprise.logging.setup import configure_logging, get_logger, inject_context

    # At service startup:
    configure_logging(
        service_name="qc_runner",
        log_level="INFO",
        runner_id=runner_id,
        host_id=hostname,
        json_output=True,
    )

    # Attach rotating file handler (call once, right after configure_logging):
    log_file = add_file_handler("qc", log_dir="data/logs")

    # Per-task context (call at start of each slide/trigger):
    inject_context(
        correlation_id="abc-123",
        global_artifact_id="file-xyz",
        service_name="qc_runner",
        runner_id=runner_id,
        host_id=hostname,
    )

    log = get_logger(__name__)
    log.info("qc.started", source_path="/data/slide.svs")
"""
from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import structlog

# Per-task context variables — automatically included in every log line when set.
_ctx_correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
_ctx_artifact_id: ContextVar[Optional[str]] = ContextVar("global_artifact_id", default=None)
_ctx_run_id: ContextVar[Optional[str]] = ContextVar("global_run_id", default=None)

# Global service identity — set once at startup.
_SERVICE_NAME: str = "pathoryx"
_RUNNER_ID: str = ""
_HOST_ID: str = ""


def configure_logging(
    service_name: str = "",
    log_level: str = "INFO",
    runner_id: str = "",
    host_id: str = "",
    json_output: bool = True,
) -> None:
    """
    Initialize structlog backed by stdlib logging. Call ONCE at service startup.

    Uses structlog.stdlib.LoggerFactory() so that add_logger_name and other
    stdlib processors work correctly — PrintLogger does NOT have a .name
    attribute and will crash with those processors.

    Args:
        service_name: Included in every log line (e.g. "qc_runner"). Optional.
        log_level:    Standard level string ("DEBUG", "INFO", "WARNING", "ERROR").
        runner_id:    Stable runner UUID registered in runner_registrations table.
        host_id:      Hostname of the machine running this service.
        json_output:  True (default) → JSON lines; False → human-readable dev output.
    """
    global _SERVICE_NAME, _RUNNER_ID, _HOST_ID
    if service_name:
        _SERVICE_NAME = service_name
    _RUNNER_ID = runner_id
    _HOST_ID = host_id

    level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure stdlib root logger — the final rendered string from structlog
    # passes through stdlib as %(message)s, so set format to just the message.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    for noisy in ("sqlalchemy.engine", "alembic", "urllib3", "botocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,   # requires stdlib logger (.name attribute)
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_service_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        # stdlib.LoggerFactory creates logging.Logger objects that have .name —
        # required for add_logger_name; PrintLoggerFactory does NOT work here.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def add_file_handler(
    service_name: str,
    log_dir: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 7,
) -> Path:
    """
    Attach a RotatingFileHandler to the stdlib root logger.

    Because structlog is configured with LoggerFactory() (stdlib), every
    structlog log line already flows through stdlib logging.  Adding a handler
    here captures all output — both structlog and plain logging.getLogger() —
    without touching the structlog pipeline.

    Call once, immediately after configure_logging().  Returns the absolute
    Path of the log file so callers can store it in audit records
    (e.g. dimse_evidence["log_file"] in upload_results.response_summary).

    Args:
        service_name:  Used to derive the log filename.
                       e.g. "upload" → data/logs/upload.log
        log_dir:       Directory for log files.
                       Defaults to PATHORYX_LOG_DIR env var or "data/logs".
        max_bytes:     Rotate when the file reaches this size.  Default 10 MB.
        backup_count:  Number of rotated files to retain.  Default 7.

    Returns:
        The resolved Path of the log file.
    """
    import os
    resolved_dir = Path(log_dir or os.environ.get("PATHORYX_LOG_DIR", "data/logs"))
    resolved_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolved_dir / f"{service_name}.log"

    handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    # Same format as stdout — structlog already rendered the message as JSON.
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.root.level)
    logging.root.addHandler(handler)
    return log_path.resolve()


def _inject_service_context(
    logger: Any,  # noqa: ARG001
    method: Any,  # noqa: ARG001
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor: inject global service identity into every event."""
    event_dict.setdefault("service", _SERVICE_NAME)
    if _RUNNER_ID:
        event_dict.setdefault("runner_id", _RUNNER_ID)
    if _HOST_ID:
        event_dict.setdefault("host_id", _HOST_ID)

    if (cid := _ctx_correlation_id.get()):
        event_dict.setdefault("correlation_id", cid)
    if (aid := _ctx_artifact_id.get()):
        event_dict.setdefault("global_artifact_id", aid)
    if (rid := _ctx_run_id.get()):
        event_dict.setdefault("global_run_id", rid)

    return event_dict


def inject_context(
    *,
    correlation_id: Optional[str] = None,
    global_artifact_id: Optional[str] = None,
    global_run_id: Optional[str] = None,
    runner_id: Optional[str] = None,
    host_id: Optional[str] = None,
    service_name: Optional[str] = None,
) -> None:
    """
    Bind per-task context for the current execution scope.

    All parameters are optional keyword-only. Values that are None are left
    unchanged. Call clear_context() when the task finishes.
    """
    global _SERVICE_NAME, _RUNNER_ID, _HOST_ID
    if correlation_id is not None:
        _ctx_correlation_id.set(correlation_id)
    if global_artifact_id is not None:
        _ctx_artifact_id.set(global_artifact_id)
    if global_run_id is not None:
        _ctx_run_id.set(global_run_id)
    if runner_id is not None:
        _RUNNER_ID = runner_id
    if host_id is not None:
        _HOST_ID = host_id
    if service_name is not None:
        _SERVICE_NAME = service_name


def bind_context(
    correlation_id: Optional[str] = None,
    global_artifact_id: Optional[str] = None,
    global_run_id: Optional[str] = None,
) -> None:
    """Kept for backwards compatibility. Prefer inject_context() for new code."""
    if correlation_id is not None:
        _ctx_correlation_id.set(correlation_id)
    if global_artifact_id is not None:
        _ctx_artifact_id.set(global_artifact_id)
    if global_run_id is not None:
        _ctx_run_id.set(global_run_id)


def clear_context() -> None:
    """Clear all per-task context variables. Call at the end of each task."""
    _ctx_correlation_id.set(None)
    _ctx_artifact_id.set(None)
    _ctx_run_id.set(None)


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a structlog logger bound to the given name."""
    return structlog.get_logger(name)
