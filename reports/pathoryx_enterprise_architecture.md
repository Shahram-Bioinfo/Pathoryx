# Pathoryx Enterprise — Database & Pipeline Architecture

**Document version:** 1.0.0  
**Generated:** 2026-05-29  
**Environment:** Development (host: CPH-DS4)  
**Database:** `pathoryx_enterprise` (PostgreSQL, alembic rev `0005`)  
**Codebase:** `/home/shahram/Pathoryx-Enterprise`

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Database Structure](#2-database-structure)
3. [Telemetry & Observability](#3-telemetry--observability)
4. [Current Pipeline Status](#4-current-pipeline-status)
5. [Real-World Deployment Readiness](#5-real-world-deployment-readiness)
6. [Risk Analysis](#6-risk-analysis)
7. [Future Recommendations](#7-future-recommendations)

---

## 1. System Overview

Pathoryx Enterprise is a medical imaging pipeline that ingests whole-slide images (WSI),
performs quality control, converts them to DICOM format, and delivers them to a Sectra
IDS7 PACS via C-STORE. PostgreSQL is the single source of truth — no file-system queues,
no message brokers, no external schedulers.

### 1.1 Services

| Service | Entry point | `target_service` | Ports |
|---|---|---|---|
| `pathoryx-qc` | `qc.main:main` | `qc_service` | Health: 8082, Metrics: 9092 |
| `pathoryx-dicom` | `dicom.main:main` | `dicom_service` | Health: 8083, Metrics: 9093 |
| `pathoryx-uploader` | `uploader.main:main` | `upload_service` | Health: 8084, Metrics: 9094 |
| `pathoryx-babelshark` | `babelshark.main:main` | `babelshark_service` | — |
| `pathoryx-failed-watcher` | `failed_watcher.main:main` | — | — |
| `pathoryx-orchestrate` | `orchestrator.main:main` | — | — |
| `pathoryx-health` | `monitoring.http_health:main` | — | — |

### 1.2 Trigger-Driven Orchestration

Every service-to-service handoff is a row in `core.service_trigger`. No service calls
another service's API directly. This decouples services completely and gives each stage a
full audit trail.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    core.service_trigger  (PostgreSQL)                         │
│                                                                              │
│  source_service ──► target_service ──► stage_name ──► payload_json          │
│                                                                              │
│  SELECT … FOR UPDATE SKIP LOCKED   ← concurrent-safe dequeue                │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Pipeline Flow (Happy Path)

```
[Scanner/NAS]
      │  WSI file detected
      ▼
[BabelShark / Watcher]
      │  intake → registers FileRecord
      │  trigger: babelshark_service → qc_service
      ▼
[QC Service]
      │  loads WSI thumbnail
      │  runs: blur / stain / penmark / bubble models (GPU)
      │  writes: qc.qc_results  (decision_status, metrics, timing)
      │  updates: file_records.status → qc_passed | qc_failed
      │  trigger: qc_service → dicom_service  (on qc_passed)
      ▼
[DICOM Service]
      │  converts WSI → DICOM folder (wsidicomizer CLI)
      │  patches IDS7/Sectra headers (dcmtk: dcmdump + dcmodify)
      │  writes: dicomizer.conversion_results
      │  updates: file_records.status → dicom_done
      │  trigger: dicom_service → upload_service
      ▼
[Upload Service]
      │  verifies DICOM folder exists
      │  runs: storescu C-STORE to Sectra PACS
      │  writes: uploader.upload_results
      │  updates: file_records.status → uploaded
      ▼
[Sectra IDS7 PACS]
```

### 1.4 FileRecord Status Machine

```
detected
    │
    ▼
intake_running
    │
    ▼
intake_registered ──────────────────────────────────────┐
    │                                                   │
    ├──► qc_pending                                     │ (qc disabled)
    │         │                                         │
    │         ▼                                         │
    │     qc_running                                    │
    │         │                                         │
    │    ┌────┴────┐                                    │
    │    ▼         ▼                                    │
    │ qc_passed qc_failed ──► failed_watcher            │
    │    │                                              │
    │    ▼                                              │
    ├──► dicom_pending ◄─────────────────────────────────┘
    │         │
    │         ▼
    │     dicom_running
    │         │
    │    ┌────┴─────┐
    │    ▼          ▼
    │  dicom_done  dicom_failed ──► failed_watcher
    │    │
    │    ▼
    │  upload_pending
    │         │
    │         ▼
    │     upload_running
    │         │
    │    ┌────┴──────┐
    │    ▼           ▼
    │  uploaded   upload_failed ──► failed_watcher
    │
    ├──► manual_review
    ├──► archived
    └──► discarded
```

### 1.5 Trigger Dequeue Mechanism

```sql
SELECT * FROM core.service_trigger
WHERE target_service = 'qc_service'
  AND trigger_status IN ('pending', 'failed')
ORDER BY triggered_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

`FOR UPDATE SKIP LOCKED` guarantees exclusive ownership under concurrent workers.
No two runners can claim the same trigger simultaneously. This replaces Redis/RabbitMQ
with zero external infrastructure.

### 1.6 Idempotency

Every service result table has a deterministic `idempotency_key` (SHA-256 of trigger_id
+ output path). If a crash occurs mid-write and the trigger is reprocessed, the second
insert is silently skipped via `SELECT … WHERE idempotency_key = ?` before inserting.

### 1.7 Retry Logic

```
trigger.trigger_status = 'failed'
trigger.retry_count < trigger.max_retries (default 3)
→ trigger is eligible for requeue on next dequeue cycle

retry_count >= max_retries
→ trigger stays 'failed', surfaced to failed_watcher for technician review
```

### 1.8 Artifact Lineage

Every row across every service table carries:
- `global_artifact_id` — UUID identifying the specific slide instance
- `parent_artifact_id` — set when a derivative is created from a source
- `correlation_id` — UUID tying all operations on one slide in one run
- `global_run_id` — run-scoped identifier for batch tracking
- `file_record_internal_id` — FK to `core.file_records` (the canonical slide record)

This creates a full lineage graph: any `qc_results`, `conversion_results`, or
`upload_results` row can be traced back to the originating `file_records` row and every
intervening trigger.

---

## 2. Database Structure

**Database:** `pathoryx_enterprise`  
**Schemas:** `core`, `qc`, `dicomizer`, `uploader`, `events`, `babelshark`,
`failed_watcher`, `ops`

### 2.1 Schema Map

```
core            ─── Infrastructure backbone
  file_records         Single row per physical slide
  service_trigger      Message queue (trigger-driven orchestration)
  pipeline_runs        Per-service run metadata
  step_runs            Per-step breakdowns
  runner_registrations Heartbeat / runner identity
  technical_metrics    CPU/memory/GPU per step
  metadata_snapshots   Immutable versioned metadata

qc              ─── QC inference results
  qc_results           Decision + AI module outputs

dicomizer       ─── DICOM conversion results
  conversion_results   Per-slide conversion record

uploader        ─── Upload results
  upload_results       Per-slide PACS delivery record

events          ─── Append-only event store
  pipeline_events      Full event log (never UPDATE/DELETE)

babelshark      ─── BabelShark extraction (7 tables)
  extraction_results, roi_results, stain_results, etc.

failed_watcher  ─── Manual review queue
  technician_changes, watched_folder_snapshots

ops             ─── Operational logs
  event_logs, error_logs
```

### 2.2 core.file_records

**Purpose:** The spine of the system. One row per physical slide file. All services
read and write against this table to track the slide lifecycle.

**Orchestration role:** Every trigger, result, and event references this table.
FileRecord.status drives the state machine.

| Column | Type | Key | Description |
|---|---|---|---|
| `internal_id` | bigint PK | — | Surrogate key |
| `uuid` | uuid UNIQUE | — | External-facing ID |
| `global_artifact_id` | text | IX | Lineage identifier |
| `parent_artifact_id` | text | — | Parent slide (re-scans, crops) |
| `canonical_path` | text UNIQUE | — | Deduplication key |
| `status` | text | IX | State machine position |
| `scanner_id` | text | IX | Originating scanner |
| `checksum_sha256` | text | — | Content integrity |
| `current_file_path` | text | — | Current location on disk |
| `source_service` | text | — | Which service registered this file |
| `source_artifact_id` | text | UQ | Dedup with source_service |

**Status CHECK constraint** enforces only valid state transitions at the DB level.

**Live counts (2026-05-29):**
- `qc_pending`: 26 rows
- `qc_failed`: 18 rows
- `intake_registered`: 5 rows
- `dicom_done`: 1 row

### 2.3 core.service_trigger

**Purpose:** PostgreSQL-backed message queue. Every service-to-service handoff is a
row here. No external message broker needed.

**Orchestration role:** Central nervous system of the pipeline. Services dequeue from
here with `FOR UPDATE SKIP LOCKED`.

| Column | Type | Description |
|---|---|---|
| `internal_id` | bigint PK | — |
| `source_service` | text NN | Who enqueued |
| `target_service` | text NN | Who should process |
| `stage_name` | text NN | `qc`, `dicom`, `upload`, etc. |
| `trigger_status` | text | `pending`, `running`, `completed`, `failed` |
| `trigger_payload_json` | jsonb | Arbitrary payload (source_path, scanner_id, …) |
| `retry_count` | int | How many times attempted |
| `max_retries` | int | Abort threshold (default 3) |
| `file_record_internal_id` | bigint FK | Links to file_records |
| `correlation_id` | text | Trace token |
| `claimed_by_runner_id` | text | Which runner is working this |
| `triggered_at` | timestamptz | When enqueued |

**Critical index:**
```sql
CREATE INDEX ix_trigger_dequeue ON core.service_trigger
  (target_service, trigger_status, triggered_at)
  WHERE trigger_status IN ('pending', 'failed');
```
Partial index — only indexes actionable rows. Avoids full scan as history grows.

**Unique constraint:**
```sql
UNIQUE(source_service, target_service, stage_name, file_record_internal_id)
```
Prevents duplicate triggers for the same file at the same stage. NULL
`file_record_internal_id` bypasses this (PostgreSQL: `NULL ≠ NULL`).

**Live status breakdown:**
```
qc_service     / completed: 4
qc_service     / failed:   34
qc_service     / running:  10  ← stale (crashed runners)
dicom_service  / completed: 1
dicom_service  / failed:    9
upload_service / pending:   1
```

### 2.4 qc.qc_results

**Purpose:** Records the AI decision for each slide: pass/fail, module metrics, timing,
resource usage.

**Orchestration role:** Read by the next-stage router to decide whether to dispatch a
`dicom_service` trigger.

| Column group | Columns | Notes |
|---|---|---|
| Identity | `idempotency_key`, `trigger_internal_id`, `global_artifact_id` | Dedup + lineage |
| Decision | `qc_result`, `decision_status`, `decision_reason` | `passed`/`failed`, reason string |
| Module metrics | `blur_metrics`, `stain_metrics`, `penmark_metrics`, `bubble_metrics` | JSONB |
| Thresholds | `decision_threshold_json` | Config snapshot at inference time |
| Timing | `started_at`, `finished_at`, `total_duration_seconds` | Wall-clock |
| Resources | `memory_rss_mb`, `cpu_percent_avg` | RSS at inference time |
| Runner | `runner_id`, `host_id`, `service_version` | Attribution |
| Routing | `next_service`, `next_stage`, `scanner_id` | Config-driven downstream |
| Error | `error_reason` | `file_missing`, `unsupported_format`, `inference_error` |

**Live counts:** 4 accepted, 8 rejected (12 total)

### 2.5 dicomizer.conversion_results

**Purpose:** Records each WSI→DICOM conversion attempt, including tool identity,
file sizes, duration, and failure context.

| Column group | Columns | Notes |
|---|---|---|
| Identity | `idempotency_key`, `trigger_internal_id` | Dedup |
| Paths | `source_path`, `output_path`, `output_format` | Before/after |
| Status | `conversion_status` | `completed`, `failed`, `skipped_already_dicom` |
| Tool | `conversion_tool`, `conversion_tool_version` | `ids7_compatible_dcm`, `placeholder_copy` |
| Sizes | `input_file_size_bytes`, `output_file_size_bytes` | Storage accounting |
| Checksums | `input_checksum_sha256`, `output_checksum_sha256` | Integrity |
| Timing | `duration_seconds`, `processed_at` | Performance tracking |
| Failure | `failure_context` (JSONB) | `error_type`, `message` |
| Upload | `upload_result_json` | NULL since Phase 11C (upload separated) |
| Runner | `runner_id`, `host_id`, `service_version` | Attribution |

**Phase 11C note:** `upload_result_json` is now always `NULL` — the DICOM service no
longer performs storescu. The upload_service owns that field going forward.

### 2.6 uploader.upload_results

**Purpose:** Records each C-STORE upload attempt to the Sectra PACS.

| Column | Description |
|---|---|
| `upload_status` | `uploaded`, `failed` |
| `upload_method` | `storescu` (current) |
| `target_endpoint` | DICOM output path passed from DICOM service |
| `duration_seconds` | Upload wall time |
| `retry_count` | Number of retry attempts |
| `response_summary` | JSONB: stdout, return_code |
| `failure_context` | JSONB: structured failure detail |

**Live counts:** 0 rows (upload_service has not completed any full uploads yet)

### 2.7 events.pipeline_events

**Purpose:** Immutable append-only event log. Every state transition produces an event.
Suitable for full pipeline replay, audit, and root-cause analysis.

**Design constraints enforced by migration:**
- `REVOKE UPDATE, DELETE ON events.pipeline_events FROM pathoryx_user`
- `UNIQUE(idempotency_key)` prevents duplicate events on retry

| Column | Description |
|---|---|
| `event_type` | `file.detected`, `qc.passed`, `dicom.conversion_completed`, etc. |
| `event_version` | Monotonic per `(aggregate_type, aggregate_id)` |
| `aggregate_type` | `file_record` |
| `aggregate_id` | `global_artifact_id` or `internal_id` |
| `event_payload` | JSONB: event-specific data |
| `caused_by_event_id` | FK to parent event (causation chain) |
| `occurred_at` | Business time (not server insert time) |

**Live count:** 226 events recorded.

### 2.8 core.technical_metrics

**Purpose:** Per-step, per-host resource telemetry. Not currently written by services
(schema is provisioned, population is a future enhancement).

Rich GPU/CPU/memory/disk schema including:
`cpu_percent_avg`, `cpu_percent_peak`, `memory_rss_mb`, `memory_peak_mb`,
`disk_read_mb`, `disk_write_mb`, `gpu_memory_allocated_mb`, `gpu_utilization_percent`,
`gpu_temperature_celsius`.

### 2.9 failed_watcher.technician_changes

**Purpose:** Captures technician filesystem actions (rename, replace, move) on failed
slides. Provides structured review workflow with `review_status`, `reviewed_by`,
`approved_by` fields.

### 2.10 ER Diagram (Core Tables)

```
core.file_records ◄─────────────────────────────────────────────┐
  internal_id (PK)                                               │
  status (CHECK)                                                 │ FK
  global_artifact_id                                             │
  canonical_path (UNIQUE)                                        │
         │                                                       │
         │ FK (SET NULL)                                         │
         ▼                                                       │
core.service_trigger                                             │
  internal_id (PK)                                               │
  source_service → target_service → stage_name (UNIQUE+FK)       │
  trigger_status                                                 │
  trigger_payload_json                                           │
         │                                                       │
         │ trigger_internal_id (soft ref)                        │
         ▼                                                       │
  ┌──────────────────┐   ┌────────────────────────┐             │
  │ qc.qc_results    │   │ dicomizer.conversion_  │             │
  │ idempotency_key  │   │ results                │             │
  │ decision_status  │   │ conversion_status      │             │
  │ blur/stain/etc.  │   │ output_path            │             │
  └──────────────────┘   └────────────────────────┘             │
         │ FK                      │ FK                          │
         └──────────┬──────────────┘                             │
                    ▼                                             │
         uploader.upload_results                                  │
         idempotency_key                                          │
         upload_status                                            │
                    │ FK ──────────────────────────────────────────┘

  events.pipeline_events (append-only)
    event_type, aggregate_id
    caused_by_event_id (self-FK for causation chain)
```

---

## 3. Telemetry & Observability

### 3.1 What Is Currently Persisted

#### QC Service (`qc.qc_results`)

| Field | Populated | Value example |
|---|---|---|
| `started_at` | ✅ Yes | `2026-05-29 14:48:57.046` |
| `finished_at` | ✅ Yes | `2026-05-29 14:48:59.716` |
| `total_duration_seconds` | ✅ Yes | `2.668` |
| `memory_rss_mb` | ✅ Yes | `1069.1` |
| `cpu_percent_avg` | ⚠️ Often NULL | Sampler too short for fast slides |
| `decision_status` | ✅ Yes | `accepted` / `rejected` |
| `decision_reason` | ✅ Yes | `no_blur`, `blur_ratio_above_threshold` |
| `decision_threshold_json` | ✅ Yes | `{blur_fail_threshold, blur_flag, blur_ratio}` |
| `blur_metrics` | ✅ Yes | `{blur_flag, blur_ratio, total_tiles, blur_tiles}` |
| `stain_metrics` | ✅ Yes | `{label, probability}` |
| `penmark_metrics` | ✅ Yes | `{flag, probability}` |
| `bubble_metrics` | ✅ Yes | when enabled |
| `error_reason` | ✅ Yes | `file_missing`, `unsupported_format`, `inference_error` |
| `scanner_id` | ✅ Yes | from trigger payload |
| `source_path` | ✅ Yes | resolved WSI path |
| `runner_id` | ✅ Yes | deterministic runner UUID |
| `host_id` | ✅ Yes | hostname |
| `service_version` | ✅ Yes | semver string |
| `correlation_id` | ✅ Yes | trace token |
| `global_artifact_id` | ✅ Yes | slide lineage UUID |
| `input_file_size_bytes` | ❌ Not written | schema exists, not populated |

#### DICOM Service (`dicomizer.conversion_results`)

| Field | Populated | Notes |
|---|---|---|
| `conversion_status` | ✅ Yes | `completed`, `failed` |
| `source_path` | ✅ Yes | input WSI path |
| `output_path` | ✅ Yes | DICOM folder path |
| `conversion_tool` | ✅ Yes | `ids7_compatible_dcm`, `placeholder_copy` |
| `duration_seconds` | ✅ Yes | wall-clock conversion time |
| `failure_context` | ✅ Yes | `{error_type, message}` — e.g. `missing_wsidicomizer` |
| `input_file_size_bytes` | ⚠️ Partial | populated on success, NULL on failure |
| `output_file_size_bytes` | ⚠️ Partial | populated on success |
| `upload_result_json` | ❌ NULL | Phase 11C: upload separated |
| `started_at`/`finished_at` | ❌ Not written | schema lacks these columns |
| `memory_rss_mb` | ❌ Not written | not instrumented |

#### Upload Service (`uploader.upload_results`)

| Field | Populated | Notes |
|---|---|---|
| `upload_status` | ✅ Yes | `uploaded`, `failed` |
| `upload_method` | ✅ Yes | `storescu` |
| `duration_seconds` | ✅ Yes | |
| `file_size` | ✅ Yes | DICOM folder size |
| `response_summary` | ❌ Not written | schema exists |
| `failure_context` | ✅ Yes | error details |

#### Trigger Layer (`core.service_trigger`)

| Field | Status |
|---|---|
| `triggered_at` | ✅ Set at enqueue |
| `started_at` | ⚠️ Not systematically set by runners |
| `finished_at` | ✅ Set by mark_completed/mark_failed |
| `claimed_by_runner_id` | ⚠️ Schema column present, not populated by all runners |
| `otel_trace_id` | ❌ Not populated (OpenTelemetry configured but not wired) |

#### Event Store (`events.pipeline_events`)

226 events recorded covering:
`file.dicom_converted`, `file.dicom_failed`, `qc.passed`, `qc.failed`,
`runner.started`, `runner.stopped`.

### 3.2 Prometheus Metrics (Runtime)

All services expose Prometheus metrics on dedicated ports:

| Metric | Labels | Description |
|---|---|---|
| `files_processed_total` | `service`, `stage` | Successful completions |
| `files_failed_total` | `service`, `stage`, `error_type` | Failure counter |
| `stage_latency_seconds` | `service`, `stage` | Histogram |
| `trigger_queue_depth` | `target_service` | Queue depth gauge |
| `runner_heartbeat_age_seconds` | `runner_id`, `service` | Heartbeat staleness |
| `dicom_cstore_batches_total` | `service`, `status` | storescu batch count |
| `events_appended_total` | `event_type`, `service` | Event store rate |

### 3.3 Telemetry Gaps

| Gap | Severity | Impact |
|---|---|---|
| `cpu_percent_avg` often NULL for short slides | Medium | Can't profile GPU vs CPU bottleneck |
| DICOM service: no `started_at`/`finished_at` columns | Medium | Can't calculate per-slide conversion latency |
| `core.technical_metrics` not populated | High | Rich resource table unused |
| `trigger.started_at` not set systematically | Low | Dequeue latency invisible |
| `trigger.claimed_by_runner_id` not always set | Low | Multi-runner attribution incomplete |
| OpenTelemetry trace IDs not propagated | Medium | Distributed tracing non-functional |
| DICOM: `input_file_size_bytes` NULL on failure | Low | Can't correlate slide size with failure |

---

## 4. Current Pipeline Status

### 4.1 Native Migration Summary

| Service | Legacy dependency | Status |
|---|---|---|
| QC service | `pipeline.*` from qc_adapter | ✅ Fully native (Phase 9) |
| DICOM service | `pipeline.*` from dicom_delivery_adapter | ✅ Fully native (Phase 11A) |
| DICOM service | `utils.wsidicom_utils` from tool_WSIDicomizer | ✅ Fully native (Phase 11A) |
| Uploader service | External dependencies | ✅ Never had any |

**Verification (runtime grep):**
```bash
grep -rn "^from pipeline|^import pipeline" pathoryx_enterprise/services/
# Result: CLEAN (zero matches)
```

### 4.2 QC Service

**Status: Production-ready for trigger mode.**

```
pathoryx_enterprise/services/qc/
  config.py        — QCSettings (env), QCServiceConfig (YAML), scanner policies
  runner.py        — trigger loop, ThreadPoolExecutor, FOR UPDATE SKIP LOCKED
  db_writer.py     — writes qc_results, updates FileRecord, enqueues dicom trigger
  engine/          — native engine modules (copied from qc_adapter in Phase 9)
    config.py      — AppConfig (inference YAML)
    domain/        — SlideQCStatus, QCModuleResult, SlideQCResult
    models/        — BlurModel, StainModel, PenmarkModel, BubbleModel (PyTorch)
    modules/       — sharpness_model (blur detection)
    services/      — ModelRegistry, SlideQcInferenceService, SlideQcDecisionService
                     ThumbnailService, VisualizationService
```

**Dependencies:**
- PyTorch (GPU inference)
- OpenSlide (WSI reading)
- `configs/qc_config.yaml` (model weights, thresholds)
- PostgreSQL (trigger dequeue, result write)

**Runtime dependencies removed:**
- ❌ `pipeline.*` — removed (Phase 9)
- ❌ `PYTHONPATH=/home/shahram/Pathoryx/services/qc_adapter` — no longer needed

**Validated (Phase 10):**
- Real SVS processed: N2024002861SA-1-2-H&E_UTC…svs (906 MB)
- `qc_results` row written with all telemetry fields
- Inference time: 2.668s on real slide
- `memory_rss_mb`: 1069.1 MB
- Decision: `accepted` / `no_blur`

### 4.3 DICOM Service

**Status: Conversion-only mode functional. Upload separated. wsidicomizer required for production.**

```
pathoryx_enterprise/services/dicom/
  config.py        — DICOMSettings (env), re-exports load_dicom_engine_config
  runner.py        — trigger loop, conversion-only (Phase 11C), dispatches upload trigger
  db_writer.py     — writes conversion_results, sets dicom_done, enqueues upload trigger
  upload_utils.py  — preserved for upload_service (storescu helpers)
  engine/          — native engine (ported from dicom_delivery_adapter in Phase 11A)
    config.py      — DicomEngineConfig (YAML), WsidicomzerConfig
    domain/        — ConversionStatus, ConversionResult, InputClassificationResult
    services/
      conversion_service.py — routes: is_wsi_file? → wsidicomizer → patch IDS7
      wsidicom_utils.py     — store_as_IDS7_compatible_dcm dispatcher (3-path)
      conversion_utils.py   — classify_input, compute_sha256, deterministic_output_folder
      metaextraction_utils.py — filename → slide_id / accession / study regex
      lis_client.py         — optional LIS patient data (pyodbc)
```

**Runtime dependencies status:**
- ✅ `pipeline.*` — removed (Phase 11A)
- ✅ `utils.wsidicom_utils` — removed (Phase 11A)
- ✅ `DICOM_PERFORM_UPLOAD=false` — storescu not called by DICOM runner (Phase 11C)
- ⚠️ `wsidicomizer` CLI — NOT installed; returns `missing_wsidicomizer` cleanly
- ✅ `dcmtk` (`dcmodify`, `dcmdump`) — installed at `/usr/bin/`
- ✅ `pydicom` 3.0.1 — installed

**Phase 11C architecture (validated):**
```
trigger 59 (placeholder_copy test):
  conversion_status = completed     ✅
  file_records.status = dicom_done  ✅
  upload_service trigger dispatched ✅
  payload = {dicom_path, source_path, scanner_id, global_artifact_id}
  upload_result_json = NULL (correct — storescu is upload_service's job)
```

### 4.4 Upload Service

**Status: Structure complete, storescu not yet wired to upload_service runner.**

```
pathoryx_enterprise/services/uploader/
  config.py         — UploaderSettings (env)
  runner.py         — dequeues upload_service triggers, calls _do_upload()
  db_writer.py      — writes upload_results, sets FileRecord.status=uploaded
  circuit_breaker.py — CLOSED/OPEN/HALF_OPEN, thread-safe
```

**Current `_do_upload()` behavior:**
- Verifies `dicom_path` exists
- Calculates folder size
- Records outcome — does NOT yet call storescu

**Gap:** storescu C-STORE must be moved from `upload_utils.py` (currently unused by
uploader runner) into the uploader's `_do_upload()` or a dedicated cstore_service.

**Runtime dependency:** CLEAN — no legacy imports.

### 4.5 DB Migration State

| Revision | Name | Status |
|---|---|---|
| 0001 | Enterprise initial schema | ✅ Applied |
| 0002 | Add operational columns | ✅ Applied |
| 0003 | Add BabelShark stage tables | ✅ Applied |
| 0004 | QC scanner dual mode | ✅ Applied |
| 0005 | QC timing resources | ✅ Applied |
| 0006 | (next) | ❌ Not written |

---

## 5. Real-World Deployment Readiness

### 5.1 Sectra IDS7 Integration Readiness

#### What Is Working

| Component | Status |
|---|---|
| WSI classification (is_wsi_file) | ✅ Functional |
| wsidicomizer dispatcher (3-path logic) | ✅ Implemented |
| IDS7 header injection (dcmtk dcmodify) | ✅ Implemented, tested on Linux |
| Filename metadata extraction (regex) | ✅ Implemented |
| IDS7 required DICOM tags | ✅ Injected: (2200,0002), (0040,0512), (0008,0050), (0020,0010), (0040,0560) |
| Upload trigger dispatch | ✅ Working (Phase 11C) |
| storescu batch utils | ✅ `upload_utils.py` ready for uploader |
| Circuit breaker (PACS protection) | ✅ Implemented in uploader |
| DICOM-only mode (no upload) | ✅ `DICOM_PERFORM_UPLOAD=false` default |

#### What Still Needs Validation

| Item | Blocker level | Notes |
|---|---|---|
| `wsidicomizer` installation | **Critical** | `pip install wsidicomizer` + OpenSlide native libs |
| First real SVS → DICOM conversion | **Critical** | Validates wsidicomizer output quality |
| IDS7 DICOM tag validation by Sectra | **Critical** | Sectra may reject non-conformant tags |
| storescu wired to uploader runner | **High** | `upload_utils.py` must be called from `_do_upload()` |
| SECTRA_REMOTE_AE / SECTRA_LOCAL_AE env vars | **High** | Configured in `.env` but as non-standard names |
| Slide filename pattern matching | **High** | `match_construct_patterns` in `dicom_config.yaml` must match real filenames |
| DICOM study/series UID uniqueness | **Medium** | wsidicomizer handles this; verify no collisions |
| PACS firewall/network access | **Medium** | Port 104 (DICOM) must be open between server and Sectra |
| LIS enrichment (patient data) | **Low** | Optional; safe to skip initially |

#### Required Environment Variables for First Real Upload

```bash
# Core (already set)
DATABASE_URL=postgresql+psycopg2://pathoryx_user:password@localhost:5432/pathoryx_enterprise
DICOM_CONFIG_PATH=./configs/dicom_config.yaml
QC_CONFIG_PATH=./configs/qc_config.yaml

# SECTRA PACS (currently mis-named in .env)
SECTRA_HOST=<sectra_server_ip>
SECTRA_PORT=104
SECTRA_REMOTE_AE=<sectra_ae_title>    # NOT SECTRA_REMOTE_AE_TITLE
SECTRA_LOCAL_AE=<local_ae_title>      # NOT SECTRA_AE_TITLE

# Upload service
DICOM_PERFORM_UPLOAD=false            # DICOM runner stays conversion-only
```

**Note:** `.env` currently has `SECTRA_AE_TITLE` and `SECTRA_REMOTE_AE_TITLE` which do
NOT match `DICOMSettings` field aliases `SECTRA_LOCAL_AE` and `SECTRA_REMOTE_AE`.
This must be corrected before the upload service can connect to Sectra.

#### Recommended First Production Test Procedure

```
Step 1: Install wsidicomizer
  pip install wsidicomizer
  Verify: wsidicomizer --help

Step 2: Run conversion test (no PACS)
  # Create one dicom_service trigger pointing to a known-good .svs
  # Run: pathoryx-dicom (DICOM_PERFORM_UPLOAD=false)
  # Verify: dicomizer.conversion_results.conversion_status='completed'
  # Verify: output DICOM folder created with valid .dcm files

Step 3: Validate DICOM output
  # Use dcmdump on generated .dcm files
  dcmdump <output.dcm> | grep -E "0008,0008|2200,0002|0040,0512|0008,0050"
  # Verify all IDS7 required tags are present

Step 4: Test storescu connectivity (dry-run to local listener)
  # On a test machine, start: storescp 10104
  # Then: storescu -aec TEST_AE -aet LOCAL_AE 127.0.0.1 10104 <file.dcm>
  # Verify: storescp received the file

Step 5: Wire storescu in uploader runner
  # Move storescu call into uploader/_do_upload()
  # Use upload_utils.build_cstore_commands() + run_all_cstore_batches()

Step 6: Full end-to-end test (staging Sectra)
  # Use a non-production Sectra instance
  # Run full pipeline: QC → DICOM → Upload
  # Verify slide appears in Sectra IDS7 viewer with correct metadata

Step 7: Production
  # After staging validation, point to production Sectra PACS
```

---

## 6. Risk Analysis

### 6.1 Architectural Strengths

| Strength | Detail |
|---|---|
| Trigger-driven, DB-backed | No external broker; PostgreSQL is the single source of truth |
| `FOR UPDATE SKIP LOCKED` | Correct concurrent dequeue without locks or races |
| Idempotent result writes | Deterministic keys prevent duplicate inserts on retry |
| Append-only event store | Full audit trail; `REVOKE UPDATE/DELETE` at DB level |
| Status machine with CHECK constraint | Invalid state transitions rejected at the DB layer |
| Partial index on trigger dequeue | Only indexes `pending`/`failed` rows; stays fast as history grows |
| Service isolation | Each service has its own schema and result table |
| Lineage preserved | `global_artifact_id` threads through every table |
| Clean runtime dependency separation | QC and DICOM services have zero legacy imports |

### 6.2 Technical Debt

| Item | Severity | Phase |
|---|---|---|
| storescu not wired to uploader runner | High | Must resolve before production upload |
| `core.technical_metrics` table empty | Medium | Rich telemetry table unused |
| `trigger.started_at` not set systematically | Low | Dequeue latency invisible |
| `trigger.claimed_by_runner_id` incomplete | Low | Multi-runner attribution |
| `cpu_percent_avg` often NULL | Medium | ResourceMonitor poll interval too short for fast slides |
| OpenTelemetry trace IDs not propagated | Medium | Distributed tracing wired but inactive |
| No migration 0006 | Low | `dicomizer.conversion_results` missing `started_at`/`finished_at` |
| `qc_results` population: `input_file_size_bytes` NULL | Low | Size vs. inference time correlation impossible |
| `.env` SECTRA var names mismatch | **High** | Will cause silent misconfiguration before upload |

### 6.3 Database Risks

| Risk | Mitigation |
|---|---|
| Stale `running` triggers (10 currently) | Runner crash leaves trigger in `running`. Need: runner heartbeat staleness detector + automatic requeue after TTL |
| NULL file_record triggers bypass unique constraint | Intentional but creates potential orphan upload triggers |
| `events.pipeline_events` grows unboundedly | No partitioning yet. Add monthly range partitions at ~50M rows/year |
| `service_trigger` table full-scans on high load | Partial index on `(target_service, trigger_status, triggered_at)` mitigates this but test under load |
| `metadata_snapshots` self-FK ON DELETE RESTRICT | Prevents accidental snapshot deletion but makes cleanup complex |

### 6.4 Scaling Concerns

| Concern | Impact | Recommendation |
|---|---|---|
| Single QC runner per deployment | GPU bottleneck for high-volume sites | `max_workers` configurable; ThreadPoolExecutor already in place |
| No horizontal scaling coordinator | Two QC nodes would fight for triggers | `FOR UPDATE SKIP LOCKED` handles this correctly; test under load |
| Large SVS files (1.6 GB seen) | Placeholder copy test succeeded; real wsidicomizer may take 30–90 min | Set `wsidicomizer.timeout_seconds: 7200` (2h default) |
| PostgreSQL as message queue | Works well up to ~10K triggers/day; above that consider pgQ or explicit partitioning | Monitor `pg_stat_user_tables` for `seq_scan` on service_trigger |

### 6.5 Retry and Orphan Trigger Risks

| Scenario | Current behavior | Risk |
|---|---|---|
| Runner crashes mid-conversion | Trigger stays `running` indefinitely | **HIGH** — 10 stale running triggers currently in DB |
| `max_retries` exceeded | Trigger left in `failed`; surfaced to failed_watcher | Correct; no orphan |
| Duplicate upload trigger for same file | Unique constraint on `(source, target, stage, file_record_id)` | Safe for non-NULL file_record_id |
| FileRecord without file_record_id in trigger | NULL bypasses unique constraint | Monitor for accumulation |

**Immediate action needed:** Create a periodic job to requeue triggers stuck in
`running` state for more than `runner_heartbeat_timeout` seconds.

### 6.6 PACS Upload Risks

| Risk | Mitigation |
|---|---|
| Sectra AE title mismatch | Sectra will reject C-STORE; fix `.env` var names |
| DICOM tag non-conformance | IDS7 may silently import but display incorrectly; validate with Sectra test instance first |
| Large DICOM folder (WSI = many GB) | storescu is already batched in `upload_utils.py`; default 500 files/batch |
| Firewall blocking port 104 | Verify network path before any upload attempt |
| Double upload | Upload trigger unique constraint prevents duplicate dispatch; circuit breaker prevents hammering |
| LIS patient data wrong/missing | Graceful degradation: conversion succeeds without patient tags |

---

## 7. Future Recommendations

### Priority 1 — Immediate (unblock production)

**7.1 Wire storescu into uploader runner**
```python
# In uploader/runner.py → _do_upload():
from pathoryx_enterprise.services.dicom.upload_utils import (
    build_cstore_commands, run_all_cstore_batches
)
commands = build_cstore_commands(
    input_path=Path(dicom_path),
    host=settings.sectra_host, port=settings.sectra_port,
    local_ae=settings.sectra_local_ae, remote_ae=settings.sectra_remote_ae,
    batch_size=settings.cstore_batch_size,
)
all_ok, batches = run_all_cstore_batches(commands, timeout_seconds=settings.upload_timeout_seconds)
```

**7.2 Fix `.env` SECTRA variable names**
```bash
# Change:
SECTRA_AE_TITLE=LOCAL_AE          → SECTRA_LOCAL_AE=LOCAL_AE
SECTRA_REMOTE_AE_TITLE=REMOTE_AE  → SECTRA_REMOTE_AE=REMOTE_AE
```

**7.3 Stale trigger requeue job**
Add a scheduled job (or startup check) that requeues triggers stuck in `running` state
for more than `N` seconds (where N > runner heartbeat interval):
```sql
UPDATE core.service_trigger
SET trigger_status = 'pending', retry_count = retry_count + 1
WHERE trigger_status = 'running'
  AND updated_at < NOW() - INTERVAL '30 minutes';
```

**7.4 Install wsidicomizer**
```bash
pip install wsidicomizer
# Test:
wsidicomizer --input slide.svs --output /tmp/test_dicom/
```

### Priority 2 — Near-term (enterprise grade)

**7.5 Populate `core.technical_metrics`**
The schema is rich (CPU, memory, disk, GPU). Wire QC and DICOM services to write here
after each inference/conversion. This enables Grafana dashboards tracking per-slide
resource costs over time.

**7.6 Fix `cpu_percent_avg` NULL issue**
The `ResourceMonitor` poll interval exceeds inference time for fast slides (<3s).
Solutions:
- Reduce poll interval to 0.1s
- Use `psutil.cpu_percent(interval=None)` at start + end delta
- Or accept that GPU-bound inference time is the primary metric

**7.7 Add `started_at`/`finished_at` to `dicomizer.conversion_results`**
Migration 0006 needed. Enables per-slide conversion latency tracking identical to QC.

**7.8 Set `trigger.started_at` and `claimed_by_runner_id`**
Update `TriggerRepository.dequeue_next()` to set these fields on claim.

### Priority 3 — Architecture (scale and reliability)

**7.9 Activate OpenTelemetry tracing**
The tracer (`get_tracer`, `setup_tracing`, `traced_stage`) is wired but `otel_trace_id`
is never stored. Set `OTEL_ENABLED=true` and configure Jaeger/Tempo endpoint.
Then populate `trigger.otel_trace_id` on dequeue.

**7.10 PostgreSQL trigger table partitioning**
At large volume, partition `core.service_trigger` by `triggered_at` (monthly ranges).
The partial index on `(pending|failed)` status already helps, but VACUUM performance
degrades on large append-only tables without partitioning.

**7.11 Partition `events.pipeline_events` by `occurred_at`**
The docstring already notes this is partition-ready. Add `PARTITION BY RANGE (occurred_at)`.

**7.12 Kubernetes readiness**
Current architecture is already well-suited for K8s:
- Services are stateless (DB is the state)
- Health/readiness HTTP probes already on ports 8082–8084
- SIGTERM handled gracefully in all runners
- Environment-variable-driven config (12-factor)
- Metrics in Prometheus format on dedicated ports
- Missing: PodDisruptionBudget config, HorizontalPodAutoscaler for QC replicas

**7.13 Dead-letter queue (DLQ) automation**
Currently, failed triggers surface to `failed_watcher.technician_changes` for manual
review. Add automated DLQ processing:
- After 3 failures + technician review → auto-archive or discard
- Notification webhook on first failure of a new error_type

**7.14 PACS upload hardening**
- Store Sectra C-STORE response codes in `uploader.upload_results.response_summary`
- Implement per-endpoint circuit breaker (already done in circuit_breaker.py — verify it gates storescu)
- Add DICOM conformance validation step before C-STORE (validate tags with pydicom)
- Log C-STORE duration per batch for storescu performance profiling

---

## Appendix A: Live DB State (2026-05-29)

```
core.file_records:           53 rows
core.service_trigger:        59 rows (10 stale 'running')
core.pipeline_runs:          37 rows
core.runner_registrations:   4 rows
qc.qc_results:               12 rows (4 accepted, 8 rejected)
dicomizer.conversion_results: 5 rows
uploader.upload_results:     0 rows
events.pipeline_events:      226 rows
```

## Appendix B: Migration History

| ID | Name | Applied |
|---|---|---|
| 0001 | Enterprise initial schema (all schemas + core tables) | ✅ |
| 0002 | Add operational columns (runner_id, host_id, service_version, processed_at) | ✅ |
| 0003 | Add BabelShark stage tables (7 result tables) | ✅ |
| 0004 | QC scanner dual mode (scanner_policies, qc_context columns) | ✅ |
| 0005 | QC timing resources (started_at, finished_at, memory_rss_mb, cpu_percent_avg) | ✅ |

## Appendix C: Required `.env` Variables

```bash
# Database (required for all services)
DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/pathoryx_enterprise

# QC service
QC_CONFIG_PATH=./configs/qc_config.yaml        # model weights + thresholds

# DICOM service
DICOM_CONFIG_PATH=./configs/dicom_config.yaml  # conversion + wsidicomizer config
DICOM_PERFORM_UPLOAD=false                      # Phase 11C: upload_service handles upload

# Upload / Sectra PACS (CORRECT names)
SECTRA_HOST=<pacs_host>
SECTRA_PORT=104
SECTRA_LOCAL_AE=<local_ae_title>               # was: SECTRA_AE_TITLE (wrong)
SECTRA_REMOTE_AE=<sectra_ae_title>             # was: SECTRA_REMOTE_AE_TITLE (wrong)

# Optional
LIS_ENABLED=false
DCMTK_BIN_DIR=                                 # empty = /usr/bin/ (PATH)
WSIDICOMIZER_EXECUTABLE=wsidicomizer           # or full path
OTEL_ENABLED=false
LOG_LEVEL=INFO
PATHORYX_ENVIRONMENT=development
PATHORYX_SERVICE_VERSION=1.0.0
```
