# Pathoryx Enterprise — Architecture

## Overview

Pathoryx Enterprise is a production-grade WSI (Whole Slide Image) ingestion and processing pipeline.
It processes 500–1000 slides per day across four main stages: intake (BabelShark), QC inference,
DICOM conversion, and PACS upload. All inter-service communication goes through PostgreSQL.

## Services

| Service | Entry Point | Port (health/metrics) | Responsibility |
|---------|------------|----------------------|----------------|
| BabelShark | `pathoryx-babelshark` | 8081/9091 | Watch folders, copy/move WSIs, register FileRecord |
| QC Service | `pathoryx-qc` | 8082/9092 | ML inference (penmark, bubble, stain, blur), accept/reject |
| DICOM Service | `pathoryx-dicom` | 8083/9093 | WSI→DICOM conversion, storescu upload to Sectra |
| Uploader | `pathoryx-uploader` | 8084/9094 | Final status tracking, circuit breaker, retry |
| Failed Watcher | `pathoryx-failed-watcher` | 8085/9095 | Technician change detection, audit, requeue |
| Orchestrator | `pathoryx-orchestrator` | — | Process supervisor (single-machine only) |

## Database Schema

All tables live in dedicated PostgreSQL schemas:

```
core.*          — FileRecord, MetadataSnapshot, PipelineRun, StepRun, ServiceTrigger, RunnerRegistration
events.*        — PipelineEvent (append-only, REVOKE UPDATE/DELETE)
babelshark.*    — BabelSharkResult
qc.*            — QcResult
dicomizer.*     — DicomizerResult
uploader.*      — UploaderResult
failed_watcher.*— WatchedFolderSnapshot, TechnicianChange
audit.*         — AuditLog
```

## Inter-Service Communication

Services communicate via the `core.service_trigger` table — no message broker required.

```
babelshark → (ServiceTrigger: target=qc_service)    → QC Service
qc_service → (ServiceTrigger: target=dicom_service) → DICOM Service
dicom_service → (ServiceTrigger: target=upload_service) → Uploader
```

Consumers use `SELECT FOR UPDATE SKIP LOCKED` to prevent double-processing under concurrent workers.

## FileRecord State Machine

```
detected → intake_running → intake_registered
intake_registered → qc_pending | dicom_pending
qc_pending → qc_running → qc_passed | qc_failed
qc_passed → dicom_pending
dicom_pending → dicom_running → dicom_done | dicom_failed
dicom_done → upload_pending
upload_pending → upload_running → uploaded | upload_failed
*_failed → (failed_watcher monitoring, possible requeue)
```

## Event Sourcing

`events.pipeline_events` is an append-only table. No UPDATE or DELETE is possible —
the migration revokes those privileges from the application user.
Every state transition is recorded with `event_version` (per aggregate), `idempotency_key`,
and `caused_by_event_id` self-FK for causal chain reconstruction.

## Multi-Machine Readiness

`core.runner_registrations` tracks all active service instances with:
- Stable `runner_id` (deterministic UUID5 based on service + host)
- `host_id` (hostname)
- `last_heartbeat_at` — staleness detection at 120s threshold
- All triggers carry `claimed_by_runner_id` / `claimed_by_host_id`

## Key Design Decisions

1. **No hardcoded credentials** — all secrets via `DATABASE_URL` env var, validated against placeholder
   values at startup by Pydantic BaseSettings.

2. **Streaming SHA-256** — 4 MB chunks to avoid OOM on 2–10 GB WSI files.

3. **ModelRegistry singleton** — instantiated once at QC service startup. `@cached_property` weights
   load on first access and are reused for all subsequent slides (original loaded new instance per slide).

4. **storescu batching** — `SECTRA_CSTORE_BATCH_SIZE` (default 500) prevents ARG_MAX overflow on
   large DICOM series.

5. **Immutable metadata snapshots** — each metadata change creates a new `core.metadata_snapshots` row
   with `payload_hash` and linked-list `previous_snapshot_id`.

6. **Path traversal protection** — `validate_path_under_roots()` in all filesystem operations.

7. **is_file_stable() has no sleep** — callers poll on interval; no thread-blocking inside the function.
