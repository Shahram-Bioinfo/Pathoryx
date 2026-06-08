"""
OpenTelemetry tracing setup and span context helpers.

Design decisions:
  - All OTel imports are deferred so the module is importable even when
    opentelemetry packages are not installed (e.g., lightweight test envs).
  - If OTEL_EXPORTER_OTLP_ENDPOINT is unset, tracing is installed as a
    no-op — zero overhead, zero dependency in prod if disabled.
  - correlation_id and global_artifact_id are injected as span attributes
    automatically via the context manager helpers.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    pass  # avoid circular imports at type-check time


def setup_tracing(
    service_name: str,
    service_version: str = "1.0.0",
) -> None:
    """
    Configure the global OpenTelemetry tracer provider.

    If OTEL_EXPORTER_OTLP_ENDPOINT is set, installs an OTLP/gRPC exporter.
    Otherwise installs a no-op provider so all `get_tracer()` calls still work.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
        from opentelemetry.sdk.trace import TracerProvider

        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                SERVICE_VERSION: service_version,
            }
        )
        provider = TracerProvider(resource=resource)

        if endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)

    except ImportError:
        # OTel SDK not installed — silently fall through, callers get no-ops
        pass


def get_tracer(name: str) -> "object":
    """
    Return an OTel tracer for *name*, or a no-op stub if OTel is unavailable.
    """
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


@contextmanager
def traced_stage(
    tracer: "object",
    span_name: str,
    *,
    correlation_id: str | None = None,
    global_artifact_id: str | None = None,
    global_run_id: str | None = None,
    extra_attrs: dict | None = None,
) -> Generator[None, None, None]:
    """
    Context manager that wraps a pipeline stage in an OTel span.

    Injects standard Palantir attributes so every span is searchable by
    correlation_id, artifact, and run.
    """
    try:
        from opentelemetry.trace import use_span

        with tracer.start_as_current_span(span_name) as span:  # type: ignore[union-attr]
            if correlation_id:
                span.set_attribute("pathoryx.correlation_id", correlation_id)
            if global_artifact_id:
                span.set_attribute("pathoryx.global_artifact_id", global_artifact_id)
            if global_run_id:
                span.set_attribute("pathoryx.global_run_id", global_run_id)
            for k, v in (extra_attrs or {}).items():
                span.set_attribute(k, str(v))
            yield
    except (ImportError, AttributeError):
        # No-op fallback
        yield


def current_trace_ids() -> tuple[str | None, str | None]:
    """
    Return (otel_trace_id, otel_span_id) as hex strings for the active span,
    or (None, None) if OTel is unavailable or no active span.
    """
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            trace_id = format(ctx.trace_id, "032x")
            span_id = format(ctx.span_id, "016x")
            return trace_id, span_id
    except (ImportError, AttributeError):
        pass
    return None, None


# ---------------------------------------------------------------------------
# No-op fallback tracer (used when opentelemetry not installed)
# ---------------------------------------------------------------------------

class _NoOpSpan:
    def set_attribute(self, *_: object, **__: object) -> None:
        pass

    def record_exception(self, *_: object, **__: object) -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *_: object) -> None:
        pass


class _NoOpTracer:
    def start_as_current_span(self, *_: object, **__: object) -> "_NoOpSpan":
        return _NoOpSpan()

    def start_span(self, *_: object, **__: object) -> "_NoOpSpan":
        return _NoOpSpan()
