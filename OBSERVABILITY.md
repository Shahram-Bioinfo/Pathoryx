# Observability Guide

## Prometheus Metrics

Each service exposes a `/metrics` endpoint (default ports 9091–9095).

### Key Metrics

**Throughput**
- `pathoryx_files_detected_total{service,folder_label}` — files seen by watchers
- `pathoryx_files_processed_total{service,stage}` — successfully processed files
- `pathoryx_files_failed_total{service,stage,error_type}` — failed files

**Queue Depth**
- `pathoryx_trigger_queue_depth{target_service}` — pending work items

**Latency**
- `pathoryx_stage_latency_seconds{service,stage}` — Histogram, p50/p95/p99
- `pathoryx_db_query_latency_seconds{service,operation}` — DB operation timing
- `pathoryx_checksum_latency_seconds{service}` — SHA-256 computation time

**Resources**
- `pathoryx_process_cpu_percent{service}`
- `pathoryx_process_memory_rss_bytes{service}`
- `pathoryx_gpu_memory_used_bytes{service,device}`
- `pathoryx_gpu_memory_total_bytes{service,device}`

**Reliability**
- `pathoryx_runner_heartbeat_age_seconds{runner_id,service}` — seconds since last heartbeat
- `pathoryx_dicom_cstore_batches_total{service,status}` — storescu batch outcomes
- `pathoryx_technician_changes_recorded_total{change_type}` — watcher audit events

### Example Prometheus Queries

Slide throughput (last 1h):
```promql
rate(pathoryx_files_processed_total{stage="intake"}[1h]) * 3600
```

QC rejection rate:
```promql
rate(pathoryx_files_failed_total{stage="qc"}[5m])
/ rate(pathoryx_files_processed_total{stage="qc"}[5m])
```

DICOM conversion p95 latency:
```promql
histogram_quantile(0.95, rate(pathoryx_stage_latency_seconds_bucket{stage="dicom"}[5m]))
```

## OpenTelemetry Tracing

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to enable distributed tracing:
```
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317
```

Each pipeline stage creates a span with attributes:
- `pathoryx.correlation_id` — end-to-end request ID
- `pathoryx.global_artifact_id` — unique slide ID
- `pathoryx.global_run_id` — pipeline run ID

If OTel is not installed or endpoint is unset, tracing is a no-op (zero overhead).

## Structured Logging

All services emit JSON logs via structlog. Key fields:

```json
{
  "timestamp": "2026-05-27T10:30:00+00:00",
  "level": "info",
  "service": "qc_service",
  "runner_id": "abc123",
  "host_id": "gpu-server-01",
  "correlation_id": "req-xyz",
  "global_artifact_id": "art-456",
  "event": "qc decision made",
  "decision_status": "accepted"
}
```

In development mode (`PATHORYX_ENVIRONMENT=development`), logs are human-readable (not JSON).

## Health Endpoints

```
GET /live   → 200 if process is running (liveness probe)
GET /ready  → 200 if DB connected + env vars present (readiness probe)
GET /health → 200 full dependency report with per-check details
```

All return JSON with `{"healthy": bool, "checks": [...], "timestamp": "..."}`.
