"""
Prometheus metrics registry for all Pathoryx services.

All metrics are registered once at import time. Each service uses the
subset relevant to it by importing specific metric objects.

Histogram buckets are tuned for WSI pipeline timings:
  - Short ops (DB queries, file checks): 0.01s – 10s
  - Long ops (QC inference, DICOM conversion): 1s – 3600s
"""
from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    start_http_server,
)

# ---------------------------------------------------------------------------
# Single shared registry — avoids duplicate-metric errors in test suites
# ---------------------------------------------------------------------------
REGISTRY = CollectorRegistry(auto_describe=True)

_SHORT_BUCKETS = (0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))
_LONG_BUCKETS = (1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0, float("inf"))

# ---------------------------------------------------------------------------
# Service identity
# ---------------------------------------------------------------------------
service_info = Info(
    "pathoryx_service",
    "Static metadata about the running service instance",
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Pipeline file counters
# ---------------------------------------------------------------------------
files_detected_total = Counter(
    "pathoryx_files_detected_total",
    "Files detected by a watcher",
    ["service", "folder_label"],
    registry=REGISTRY,
)

files_processed_total = Counter(
    "pathoryx_files_processed_total",
    "Files successfully processed by a service stage",
    ["service", "stage"],
    registry=REGISTRY,
)

files_failed_total = Counter(
    "pathoryx_files_failed_total",
    "Files that failed processing (all retry attempts exhausted)",
    ["service", "stage", "error_type"],
    registry=REGISTRY,
)

files_retried_total = Counter(
    "pathoryx_files_retried_total",
    "Processing retry attempts",
    ["service", "stage"],
    registry=REGISTRY,
)

files_skipped_total = Counter(
    "pathoryx_files_skipped_total",
    "Files skipped (already done, filtered, duplicate)",
    ["service", "reason"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Queue / trigger depth
# ---------------------------------------------------------------------------
trigger_queue_depth = Gauge(
    "pathoryx_trigger_queue_depth",
    "Number of pending service triggers awaiting processing",
    ["target_service"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Processing latency histograms
# ---------------------------------------------------------------------------
stage_latency_seconds = Histogram(
    "pathoryx_stage_latency_seconds",
    "End-to-end processing latency for a pipeline stage",
    ["service", "stage"],
    buckets=_LONG_BUCKETS,
    registry=REGISTRY,
)

db_query_latency_seconds = Histogram(
    "pathoryx_db_query_latency_seconds",
    "Latency of individual database queries/transactions",
    ["service", "operation"],
    buckets=_SHORT_BUCKETS,
    registry=REGISTRY,
)

file_stability_wait_seconds = Histogram(
    "pathoryx_file_stability_wait_seconds",
    "Time spent waiting for a file to become stable",
    ["service"],
    buckets=_SHORT_BUCKETS,
    registry=REGISTRY,
)

checksum_latency_seconds = Histogram(
    "pathoryx_checksum_latency_seconds",
    "Time to compute SHA-256 checksum of a slide file",
    ["service"],
    buckets=_LONG_BUCKETS,
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Resource utilisation (populated by ResourceMonitor)
# ---------------------------------------------------------------------------
process_cpu_percent = Gauge(
    "pathoryx_process_cpu_percent",
    "CPU usage percent of the service process",
    ["service"],
    registry=REGISTRY,
)

process_memory_rss_bytes = Gauge(
    "pathoryx_process_memory_rss_bytes",
    "Resident set size of the service process in bytes",
    ["service"],
    registry=REGISTRY,
)

gpu_memory_used_bytes = Gauge(
    "pathoryx_gpu_memory_used_bytes",
    "GPU memory currently allocated (requires torch)",
    ["service", "device"],
    registry=REGISTRY,
)

gpu_memory_total_bytes = Gauge(
    "pathoryx_gpu_memory_total_bytes",
    "Total GPU memory available (requires torch)",
    ["service", "device"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# DICOM-specific
# ---------------------------------------------------------------------------
dicom_tiles_converted_total = Counter(
    "pathoryx_dicom_tiles_converted_total",
    "Total DICOM tiles written during WSI conversion",
    ["service"],
    registry=REGISTRY,
)

dicom_cstore_batches_total = Counter(
    "pathoryx_dicom_cstore_batches_total",
    "storescu batch invocations (each batch ≤ cstore_batch_size files)",
    ["service", "status"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Failed-watcher specific
# ---------------------------------------------------------------------------
technician_changes_recorded_total = Counter(
    "pathoryx_technician_changes_recorded_total",
    "TechnicianChange records inserted",
    ["change_type"],
    registry=REGISTRY,
)

quarantined_slides_total = Counter(
    "pathoryx_quarantined_slides_total",
    "Slides moved to quarantine",
    ["service"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Event sourcing
# ---------------------------------------------------------------------------
events_appended_total = Counter(
    "pathoryx_events_appended_total",
    "Events written to the immutable event store",
    ["event_type", "service"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Runner heartbeat
# ---------------------------------------------------------------------------
runner_heartbeat_age_seconds = Gauge(
    "pathoryx_runner_heartbeat_age_seconds",
    "Seconds since this runner last sent a heartbeat",
    ["runner_id", "service"],
    registry=REGISTRY,
)


def start_metrics_server(port: int, service: str, service_version: str) -> None:
    """Start the Prometheus HTTP scrape endpoint on *port*."""
    service_info.info({"service": service, "version": service_version})
    start_http_server(port, registry=REGISTRY)
