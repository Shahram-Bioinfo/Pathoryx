# Palantir Enterprise — Master Context

> **Audience:** Engineers and AI sessions resuming development.
> **Maintained by:** Update this file whenever architecture, service topology, DB schema, or major conventions change.
> **Last updated:** 2026-06-10

---

## Table of Contents

1. [Project Identity](#1-project-identity)
2. [Overall Architecture](#2-overall-architecture)
3. [Services](#3-services)
4. [Orchestration Flow](#4-orchestration-flow)
5. [BabelShark Flow](#5-babelshark-flow)
6. [QC Flow](#6-qc-flow)
7. [DICOM Service](#7-dicom-service)
8. [Upload Pipeline](#8-upload-pipeline)
9. [Recovery Sentry](#9-recovery-sentry)
10. [Dashboard Architecture](#10-dashboard-architecture)
11. [Database Architecture](#11-database-architecture)
12. [Windows Deployment Notes](#12-windows-deployment-notes)
13. [OpenSlide Runtime Handling](#13-openslide-runtime-handling)
14. [Environment Variable Strategy](#14-environment-variable-strategy)
15. [Credential Strategy](#15-credential-strategy)
16. [Naming Conventions](#16-naming-conventions)
17. [Production Assumptions](#17-production-assumptions)
18. [Known Technical Debt](#18-known-technical-debt)
19. [Roadmap Ideas](#19-roadmap-ideas)

---

## 1. Project Identity

| Field | Value |
|-------|-------|
| **Current name** | Palantir Enterprise |
| **Former name** | Pathoryx Enterprise (renamed Phase 3.8A) |
| **Python package** | `pathoryx_enterprise` (package name intentionally kept for migration safety) |
| **Domain** | Digital pathology — WSI (Whole Slide Image) processing pipeline |
| **Throughput target** | 500–1000 slides/day |
| **Language** | Python 3.12, TypeScript (React dashboard) |
| **Primary DB** | PostgreSQL 15+ (single DB `pathoryx`, multiple schemas) |
| **Deployment target** | Windows workstation (primary), Linux (development), Docker Compose (optional) |
| **PACS target** | Sectra IDS7 via DICOM C-STORE (`storescu`) |
| **Repository root** | `/home/shahram/Palantir` (Linux dev) / `C:\Users\Public\projects\Palantir` (Windows) |

---

## 2. Overall Architecture

### Pipeline topology

```
[Watch Folder / Scanner Drop]
        │
        ▼
  ┌─────────────┐
  │ BabelShark  │  Intake + enrichment: label extraction, DataMatrix barcode,
  │ (port 8081) │  stain OCR, ROI fallback, SlideID generation, file routing
  └──────┬──────┘
         │ ServiceTrigger → qc_service
         ▼
  ┌─────────────┐
  │  QC Service │  ML inference: penmark / bubble / stain / blur
  │ (port 8082) │  Outputs: accepted | rejected
  └──────┬──────┘
         │ ServiceTrigger → dicom_service
         ▼
  ┌──────────────┐
  │ DICOM Service│  WSI → IDS7-compatible DICOM (wsidicomizer + dcmtk header patch)
  │ (port 8083)  │  Conversion-only; enqueues upload trigger
  └──────┬───────┘
         │ ServiceTrigger → upload_service
         ▼
  ┌──────────────┐
  │   Uploader   │  storescu C-STORE to Sectra PACS; circuit breaker
  │ (port 8084)  │
  └──────────────┘
         ↑ auto-requeue on recovery
  ┌──────────────────┐
  │ RecoverySentry   │  Watches failed/ suspicious/ manual_review/
  │ (port 8087)      │  every 30 s; auto-recovers valid slides
  └──────────────────┘
         │ all services ←→ PostgreSQL ←→ Dashboard (port 8090)
```

### Communication model

- **No message broker.** All inter-service communication via `core.service_trigger` table.
- Consumers use `SELECT … FOR UPDATE SKIP LOCKED` to prevent double-processing.
- All writes are idempotent (unique `idempotency_key` columns on all result tables).
- Events are append-only (`events.pipeline_events`) — `UPDATE`/`DELETE` revoked at DB level.

### Deployment options

| Mode | How | When |
|------|-----|------|
| Orchestrator (single machine) | `pathoryx-orchestrate` | Production Windows / dev Linux |
| Individual processes | One terminal per service | Debugging |
| Docker Compose | `docker-compose up` | Integration testing |

---

## 3. Services

### 3.1 BabelShark — `pathoryx-babelshark`

- **Ports:** Health 8081, Metrics 9091
- **Module:** `pathoryx_enterprise.services.babelshark`
- **Role:** Watch scanner drop folders; copy/move WSIs; register FileRecord; run enrichment pipeline; generate SlideID; route file; dispatch QC trigger.
- **Key files:**
  - `runner.py` — poll loop, health/metrics, runner registration
  - `stage_runner.py` — enrichment orchestrator (`BabelSharkStageRunner`)
  - `core/collect_slides.py` — watch/copy/move loop
  - `core/database_manager.py` — FileRecord registration
  - `core/label_extractor.py` — WSI label PNG extraction (OpenSlide)
  - `core/datamatrix_reader.py` — DataMatrix barcode decode (`pylibdmtx`)
  - `core/stain_extractor.py` — OCR stain detection (EasyOCR)
  - `core/roi_metadata_extractor.py` — ROI fallback wrapper
  - `core/slide_id_generator.py` — SlideID, rename, routing, DB sync
  - `core/pasnet_validator.py` — PASNet/LIS validation wrapper (currently disabled)
  - `db_writer.py` — trigger dispatch + EventStore writes
- **Config:** `BABELSHARK_CONFIG_PATH` → `configs/babelshark_config.yaml`
- **WSI extensions watched:** `.svs`, `.ndpi`, `.tif`, `.tiff`, `.scn`, `.mrxs`, `.bif`, `.png`, `.jpg`, `.jpeg`
- **Two modes:** intake-only (default, production-safe) and full enrichment pipeline (feature-flagged, code-complete but not end-to-end smoke-tested with real slides).

### 3.2 QC Service — `pathoryx-qc`

- **Ports:** Health 8082, Metrics 9092
- **Module:** `pathoryx_enterprise.services.qc`
- **Role:** ML inference on WSI slides; classify as accepted or rejected.
- **Models (all `@cached_property` in `ModelRegistry` singleton):**
  - `penmark_detection_MobileNetV3.pth` — ink/pen marks
  - `bubble_detection_ConvNeXtTiny_model.pth` — air bubbles
  - `stain_model_MobileNetV3.pth` — stain classification
  - `blur_detection_resnet18_old.pth` — blur/focus
- **Config:** `QC_CONFIG_PATH` → `configs/qc_config.yaml`
- **Key invariant:** `ModelRegistry` must remain a singleton; weights are loaded once at startup, never per-slide.
- **Output:** `qc.qc_results` row + FileRecord status → `qc_passed` or `qc_failed` + dicom_service trigger if passed.

### 3.3 DICOM Service — `pathoryx-dicom`

- **Ports:** Health 8083, Metrics 9093
- **Module:** `pathoryx_enterprise.services.dicom`
- **Role:** Convert WSI → IDS7-compatible DICOM; enqueue upload trigger.
- **Engine:** Native (`engine/` subpackage). All `pipeline.*` / `utils.*` external deps removed in Phase 11A.
- **Conversion path:** `ConversionService` → `store_as_IDS7_compatible_dcm()` dispatcher → `wsidicomizer` (preferred) or `xraydcm` → dcmtk header patching (IDS7 tags).
- **Phase 11C:** Conversion-only mode. `perform_upload=false` (default). No storescu in DICOM service. Enqueues `upload_service` trigger with `{dicom_path, source_path, global_artifact_id, scanner_id}`.
- **Config:** `DICOM_CONFIG_PATH` → `configs/dicom_config.yaml`
- **CRITICAL:** Never use `configs/dicom_config_production.yaml` for testing — has `dry_run=false` and `upload_via_c_store=true`.
- **Key bug fixed (Phase 11A):** `store_as_IDS7_compatible_dcm()` dispatcher added (old code had wrong function name, silently fell through to placeholder).
- **Known gap:** `wsidicomizer` Python package not installed. First real conversion will fail until installed.

### 3.4 Uploader — `pathoryx-uploader`

- **Ports:** Health 8084, Metrics 9094
- **Module:** `pathoryx_enterprise.services.uploader`
- **Role:** Execute storescu C-STORE to Sectra PACS; record outcome; circuit breaker.
- **Circuit breaker:** `uploader/circuit_breaker.py` — CLOSED → OPEN (after 5 failures) → HALF_OPEN (after `reset_seconds`) → CLOSED.
- **MISSING:** `_do_upload()` in `runner.py` currently only verifies the DICOM path exists and records the outcome. **`build_cstore_commands` + `run_all_cstore_batches` from `upload_utils.py` have NOT been wired into `_do_upload()`**. Upload to PACS will not happen until this is completed.
- **Config:** `UploaderSettings` (env-vars only). Sectra AE settings need to be added from `DICOMSettings` → `UploaderSettings`.
- **Safety:** `cstore.upload_via_c_store: false` in config. Do not enable without explicit data governance approval.

### 3.5 RecoverySentry — `pathoryx-recovery-sentry`

- **Ports:** Health 8087, Metrics 9097
- **Module:** `pathoryx_enterprise.services.recovery_sentry`
- **DB schema:** `failed_watcher` (name preserved intentionally — do not rename)
- **Config:** `RECOVERY_SENTRY_CONFIG` → `configs/recovery_sentry.yaml`
- **Role:** Monitors `failed/`, `suspicious/`, `manual_review/` every 30 s. Auto-recovers valid slides. See §9 for full behavior.

### 3.6 Dashboard — `pathoryx-dashboard`

- **Port:** 8090
- **Module:** `pathoryx_enterprise.services.dashboard`
- **Role:** FastAPI backend + React/TypeScript frontend. Operations UI.
- **Frontend build:** `dashboard-ui/dist/` (served statically by FastAPI).
- **SSE stream:** `GET /dashboard/api/stream` — real-time events, 5 s poll.
- See §10 for full dashboard architecture.

### 3.7 Orchestrator — `pathoryx-orchestrate`

- **Module:** `pathoryx_enterprise.orchestrator`
- **Role:** Process supervisor — starts and monitors all services on a single machine.
- **Validates** `DATABASE_URL` before starting any child process.
- **SIGTERM** causes graceful shutdown of all children. Allow up to 60 s for clean exit.

### 3.8 Deprecated

- `pathoryx-failed-watcher` — prints error and exits with code 1. Use `pathoryx-recovery-sentry`.

---

## 4. Orchestration Flow

```
1.  BabelShark polls watch folder(s) every ~60 s.

2.  New WSI found:
    a. Atomic copy/move to staging dir.
    b. OpenSlide metadata extraction.
    c. FileRecord created: status = detected → intake_running → intake_registered.
    d. Idempotency key prevents duplicate registrations.

3a. Intake-only mode (default, enable_full_pipeline=false):
    QC ServiceTrigger created immediately after registration.

3b. Full-pipeline mode (enable_full_pipeline=true):
    Stage 1: label_extraction   → label PNG extracted from WSI associated images
    Stage 2: datamatrix         → barcode decoded from label PNG
    Stage 3: stain_extraction   → EasyOCR stain type from label PNG
    Stage 4: roi_fallback       → ROI-based OCR for DataMatrix failures
    Stage 5: pasnet_validation  → LIS lookup (disabled; fail_open=true)
    Stage 6: slide_id_generation → build SlideID, rename file, route to final/
    THEN: QC ServiceTrigger created (pointing at renamed, routed file).

4.  QC Service dequeues trigger (SELECT FOR UPDATE SKIP LOCKED).
    ML inference → accepted / rejected.
    On accepted: FileRecord → qc_passed; dicom_service trigger created.
    On rejected: FileRecord → qc_failed; RecoverySentry picks up.

5.  DICOM Service dequeues trigger.
    Converts WSI → DICOM folder (wsidicomizer + IDS7 header patch).
    Writes dicomizer.conversion_results.
    FileRecord → dicom_done.
    Enqueues upload_service trigger {dicom_path, source_path, global_artifact_id, scanner_id}.

6.  Uploader dequeues trigger.
    [storescu C-STORE — NOT YET WIRED — see §18 Known Technical Debt]
    Writes uploader.upload_results.
    FileRecord → uploaded | upload_failed.

7.  RecoverySentry (parallel, 30 s poll):
    Watches failed/ suspicious/ manual_review/.
    Valid SlideID → moves to final/<CaseID>/ → requeues QC trigger.
    Invalid → manual_review_required.

8.  Dashboard SSE pushes typed change events to connected browser clients every 5 s.
```

**Key invariants:**
- `TriggerRepository.dequeue_next()` always uses `SELECT FOR UPDATE SKIP LOCKED`. Never poll the trigger table directly.
- `EventStoreRepository.append()` is the only allowed write to `events.pipeline_events`. Never UPDATE or DELETE.
- `validate_path_under_roots()` must be called before any filesystem operation on user-supplied or config-supplied paths.
- `is_file_stable()` must not block; callers poll on interval.

---

## 5. BabelShark Flow

### Intake-only (default, production-safe)

```
runner.py poll loop
  → collect_slides(conf, logger)
      → detect new WSI files in watch_dirs
      → atomic_copy / atomic_move to staging
      → metadata_intake.extract_and_normalize_metadata()   [OpenSlide properties]
      → database_manager.register_collected_file()
          → FileRecord INSERT
          → EventStore append (intake.registered)
          → BabelSharkDBWriter.mark_intake_complete()      [QC trigger dispatched here]
```

### Full enrichment pipeline (enable_full_pipeline: true)

Same as above through `register_collected_file(defer_trigger=True)`, then:

```
  → BabelSharkStageRunner.run_enrichment_pipeline()
      Stage 1  run_label_extraction()
               LabelExtractor.extract_label()          → label PNG(s) in labels/
      Stage 2  run_datamatrix()
               process_all_images(slide_cfg)            → DataMatrix Excel
      Stage 3  run_stain_extraction()
               stain_run_pipeline(cfg_path)
                 → run_roi_fallback_cli_for_single_image() [in-process, no subprocess]
                     → RoiMetadataExtractor.run_on_image()
      Stage 4  run_roi_extraction()
               cmd_run(args)
                 → RoiMetadataExtractor.run_on_image()   → ROI Excel
      Stage 5  run_pasnet_validation()  [if pasnet_validation: true]
               pasnet_cli_main(["run", ...])
      Stage 6  run_slide_id_generation()
               sid_run_pipeline(slide_cfg)
                 → merge_inputs() → compute_identifiers()
                 → atomic rename/move to final/<CaseID>/
                 → _sync_final_route_records_to_db()     [enterprise DB; get_session()]
      → BabelSharkDBWriter.mark_intake_complete()        [QC trigger dispatched here]
```

Each stage:
- Emits lifecycle events to EventStore (`babelshark.{stage}.completed` / `babelshark.{stage}.failed`).
- Creates `StepRun` row in DB with per-stage timing and memory RSS.
- Updates `FileRecord.metadata_json` with stage outputs.
- Records `stage_latency_seconds` histogram to Prometheus.
- Isolates failures — one stage failing does not abort subsequent stages.

### SlideID format

```
N{10digits}{POT}-{BLOCK}-{SECTION}-{STAIN}[_UTC{timestamp}Z].{ext}
Example: N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs
```

- Colons in timestamp → underscores in filenames; ISO-Z form in DB.
- `global_artifact_id` = deterministic UUID5 from `deterministic_artifact_id()` in `utils/fingerprint.py`. Never randomly generated for the same logical artifact.

### Feature flags (babelshark_config.yaml)

```yaml
enable_full_pipeline: true        # activates enrichment stages
defer_trigger: true               # QC trigger fires after all stages complete
pipeline_stages:
  label_extraction: true
  datamatrix: true
  stain_extraction: true
  roi_fallback: true
  slide_id_generation: true
  pasnet_validation: false        # requires PASNET credentials
slide_id_generator:
  dry_run: true                   # set false for production file renames
```

---

## 6. QC Flow

```
QC runner dequeues ServiceTrigger (target='qc_service')
  → extract source_path from trigger.trigger_payload_json
  → PipelineRun created
  → QC inference:
      penmark_detection     → MobileNetV3     → score + threshold → pass/fail
      bubble_detection      → ConvNeXtTiny    → score + threshold → pass/fail
      stain_classification  → MobileNetV3     → stain label
      blur_detection        → ResNet18        → blur score
  → DecisionService aggregates: any fail → 'rejected'; all pass → 'accepted'
  → qc.qc_results INSERT (idempotent)
  → FileRecord.status:
      accepted  → qc_passed
      rejected  → qc_failed
  → On accepted: ServiceTrigger INSERT (target='dicom_service')
  → EventStore append
  → StepRun updated with duration, memory, GPU usage
```

**ModelRegistry:** All four models are loaded once at startup via `@cached_property`. Loading is triggered on first slide, then weights stay in memory for all subsequent slides. Never revert to per-slide instantiation — original bug caused 4× startup overhead per slide.

**Config:** `QC_CONFIG_PATH` → `configs/qc_config.yaml`. Thresholds, enabled modules, model weight paths all configurable.

---

## 7. DICOM Service

### Architecture (after Phase 11A + 11C)

- **Conversion-only.** No storescu in DICOM runner. Upload is responsibility of upload_service.
- **Native engine** in `engine/` subpackage — no external `pipeline.*` or `utils.*` dependencies.
- `perform_upload=false` (default, logged at startup).

### Conversion path

```
dicom runner dequeues ServiceTrigger (target='dicom_service')
  → ConversionService.convert(source_path, global_artifact_id=artifact_id)
      → classify_input_as_dicom_or_not()           → DICOM or non-DICOM
      → skip if already DICOM
      → compute_sha256(source_path)                → input checksum
      → deterministic_output_folder()              → output_root / slide_id / stem
      → store_as_IDS7_compatible_dcm() dispatcher:
          file input  → store_dcmdile_as_IDS7_compatible_dcm()   [pydicom tag injection]
          dir input   → store_dcmwsifolder_as_IDS7_compatible_dcm() [dcmtk header patching]
      → ConversionResult (status, paths, checksums, tool, duration)
  → DICOMDBWriter.record_conversion_success():
      dicomizer.conversion_results INSERT
      FileRecord.status → dicom_done
      ServiceTrigger INSERT (target='upload_service',
                             payload={dicom_path, source_path, global_artifact_id, scanner_id})
  → own trigger marked completed
```

### IDS7 DICOM tags injected

| Tag | Attribute | Source |
|-----|-----------|--------|
| `(2200,0002)` | LabelText | SlideID from filename |
| `(0040,0512)` | ContainerIdentifier | SlideID |
| `(0040,0560)[0].(0040,0600)` | SpecimenShortDescription | "Staining: {stain}" |
| `(0008,0050)` | AccessionNumber | accession_number from filename |
| `(0020,0010)` | StudyID | study_id from filename |
| `(0010,0010)` | PatientName | from LIS (optional) |
| `(0010,0020)` | PatientID | from LIS GUID (optional) |

### dcmtk path resolution

1. `DicomEngineConfig.dcmtk.bin_dir` (from `dicom_config.yaml`)
2. `DCMTK_BIN_DIR` env var
3. System PATH (Linux: `/usr/bin/dcmodify`, `/usr/bin/dcmdump` are present)

### LIS enrichment

Optional. `lis.enabled: false` in config. When enabled: `pyodbc` → Nexus SQL Server → patient metadata → injected into DICOM tags. Credentials via `LIS_SQL_SERVER` / `LIS_SQL_USERNAME` / `LIS_SQL_PASSWORD`.

---

## 8. Upload Pipeline

### Current state (INCOMPLETE — see §18)

The upload trigger flow is:

```
upload_service trigger dequeued
  → verify dicom_path exists on disk
  → [storescu C-STORE NOT YET WIRED — must add to _do_upload()]
  → UploaderDBWriter.record_upload_result()
      uploader.upload_results INSERT
      FileRecord.status → uploaded | upload_failed
  → EventStore append
```

### What needs to happen for real uploads

1. `UploaderSettings` must add Sectra host/port/AE fields (currently only in `DICOMSettings`).
2. `uploader/runner.py._do_upload()` must call `upload_utils.build_cstore_commands()` + `run_all_cstore_batches()`.
3. `FileRecord.status → upload_pending` when uploader claims the trigger (not when DICOM service enqueues it).
4. Config gates:
   - `configs/dicom_config.yaml`: `upload.dry_run: false`, `cstore.upload_via_c_store: true`
   - Requires PACS connectivity verification first.

### Safety gates (must remain as-is until explicitly approved)

| Gate | Current value | Required for production |
|------|---------------|------------------------|
| `upload.dry_run` | `true` | Change to `false` |
| `cstore.upload_via_c_store` | `false` | Change to `true` |
| `dicom_service.perform_upload` | `false` | Keep `false` (upload belongs in uploader) |
| `slide_id_generator.dry_run` | `true` | Change to `false` |
| `pasnet_validation` | `false` | Change when credentials ready |

### Circuit breaker

`uploader/circuit_breaker.py` — 3 states:
- `CLOSED` → normal operation
- `OPEN` → 5 consecutive failures; pauses all uploads for `reset_seconds` (default 60)
- `HALF_OPEN` → single probe attempt; success → CLOSED, failure → OPEN

---

## 9. Recovery Sentry

### What it does

Monitors `failed/`, `suspicious/`, `manual_review/` folders every 30 s. Detects technician file interventions and auto-recovers valid slides back into the QC pipeline.

### Auto-recovery algorithm

```
1. Filesystem snapshot diff vs. failed_watcher.watched_folder_snapshots
2. Change classified: rename | replace | new_file | remove | no_change
3. TechnicianChange row INSERT (idempotent key)
4. Wait stable_after_seconds=10 (file no longer changing)
5. Validate filename against SlideID regex
6. Valid + timestamp → build destination path
   Valid, no timestamp → OpenSlide metadata timestamp extraction → append to filename
   Invalid / no metadata ts → manual_review_required; stop
7. Check final/<CaseID>/<filename>:
   Exists + duplicate_strategy=suffix → add _1, _2, ...
   Exists + duplicate_strategy=manual_review → stop
8. Atomic move to final/<CaseID>/
9. DB transaction (all-or-nothing):
   file_records.current_file_path = new_path
   file_records.status = qc_pending
   service_trigger INSERT (target='qc_service', source='recovery_sentry') [idempotent]
   pipeline_events INSERT (recovery_sentry.auto_recovered)
   technician_changes.recovery_outcome = 'auto_recovered'
```

**CRITICAL risk:** If file moves to `final/` but DB transaction fails → manual SQL requeue required. See `RECOVERY_SENTRY.md` for runbook.

### Review state machine

```
detected → investigating → corrected → requeued → reviewed (terminal)
detected → dismissed ↔ detected  (re-openable)
unlinked | linked → investigating | reviewed | dismissed
corrected → investigating  (can revisit)
```

Every transition emits an immutable `dashboard.review_state_updated` PipelineEvent.

### DB identity split

| Identifier | Value | Reason |
|-----------|-------|--------|
| Service name | `recovery_sentry` | Current canonical name |
| CLI command | `pathoryx-recovery-sentry` | Current CLI |
| DB schema | `failed_watcher` | Legacy name preserved to avoid migration |
| Old CLI | `pathoryx-failed-watcher` | Deprecated; exits with code 1 |

### Subfolder scanning

`scan_subfolders: true` (default) — scans recursively. Hidden dirs (`.`) always skipped. Symlinks outside watch roots rejected by path validation.

### Dashboard integration

Recovery Center page shows all monitored files, technician rename drawer, label image preview, audit trail. Backend exposes:
- `POST /recovery/files/{id}/technician-rename` — rename + trigger recovery
- `PATCH /recovery/changes/{id}/review-state` — advance state machine
- `POST /recovery/validate-filename` — real-time validation (no side effects)
- `GET /recovery/files/{id}/label-image` — serve label PNG
- `POST /recovery/files/{id}/open-folder` — open folder in OS file manager

---

## 10. Dashboard Architecture

### Backend (FastAPI, port 8090)

- Serves built React bundle from `dashboard-ui/dist/` as static files.
- All API paths prefixed `/dashboard/api/`.
- **Read-only** except 3 write endpoints (see §9).
- SSE stream: `GET /dashboard/api/stream` — 7 cheap `MAX(pk)` / `COUNT` queries every 5 s; heartbeat comment every 25 s.

**Key files:**
- `services/dashboard/app.py` — FastAPI app factory
- `services/dashboard/queries.py` — all read queries
- `services/dashboard/actions.py` — write operations
- `services/dashboard/sse.py` — SSE polling loop
- `services/dashboard/schemas.py` — Pydantic response schemas
- `services/dashboard/scanner_fleet.py` — scanner config loader

### API endpoints

| Endpoint | Data source |
|----------|------------|
| `GET /overview` | COUNT aggregates across all tables |
| `GET /slides` | `core.file_records` paginated + filtered |
| `GET /slides/{id}` | file_records + qc + dicomizer + uploader + events |
| `GET /events/recent` | `events.pipeline_events` latest N |
| `GET /queues` | `core.service_trigger` grouped by service + status |
| `GET /recovery` | `failed_watcher.technician_changes` |
| `GET /failures` | Failed file_records + failed triggers |
| `GET /services/health` | `core.runner_registrations` |
| `GET /stream` | SSE — real-time change events |

### Frontend (React 18 + TypeScript + Vite)

**Source root:** `dashboard-ui/src/`

```
src/
  types/api.ts          TypeScript types matching Pydantic schemas
  api/                  Thin fetch wrappers (one file per domain)
    client.ts           Base apiFetch() with ApiError on non-2xx
  hooks/                React Query hooks (polling, stale time)
  utils/
    formatters.ts       Date, bytes, duration, service name formatters
    colors.ts           Status → badge variant + Recharts hex palette
  components/
    layout/             Shell, Sidebar, TopBar, ThemeProvider
    ui/                 KpiCard, StatusBadge, EmptyState, ErrorBanner
    charts/             QueueBarChart, StatusDonut, ServiceQueueMiniChart
  pages/
    Overview            KPI cards, pipeline funnel, service health
    SlideExplorer       Paginated filtered slide table
    SlideDetail         Full pipeline timeline per slide
    QueueMonitor        Trigger queue depths, charts
    FailureCenter       Failed slides + triggers
    RecoveryCenter      RecoverySentry files, technician rename drawer
    OperationsCenter    Service health, stuck triggers, environment config
```

**Development proxy:** Vite dev server (`npm run dev` → http://localhost:5173) proxies all `/dashboard/api/*` to `http://127.0.0.1:8090`. No CORS needed in dev.

**Production:** Build with `npm run build` → `dist/` served by FastAPI backend. Access at http://127.0.0.1:8090.

**Dark mode:** Tailwind `dark:` classes + `ThemeProvider` with localStorage persistence.

**Future integration stubs:**
- AI Copilot: `CopilotPanel` component slot in `Shell.tsx`
- RBAC: `src/context/AuthContext.tsx` placeholder; `Sidebar.tsx` has "RBAC: not configured"
- Multi-site: static label in Sidebar; replace with dropdown from `/dashboard/api/sites`
- Notifications: bell slot in `TopBar.tsx`; connect to `useAlerts()` hook

### LCARS UI

The LCARS (Star Trek-inspired operations UI aesthetic) was reached as a visual milestone in commit `129e31d`. It is implemented via the TailwindCSS brand color system and component library in `tailwind.config.js`. No separate LCARS spec file exists — it is embodied in the component styles.

---

## 11. Database Architecture

### Schemas

| Schema | Tables | Notes |
|--------|--------|-------|
| `core` | `file_records`, `service_trigger`, `pipeline_runs`, `step_runs`, `runner_registrations`, `metadata_snapshots`, `technical_metrics` | Core pipeline backbone |
| `events` | `pipeline_events` | Append-only; UPDATE/DELETE revoked at DB level |
| `babelshark` | `datamatrix_results`, `stain_results`, `roi_results`, `color_marker_results`, `pasnet_validation_results`, `slide_routing_decisions` | Per-stage BabelShark results |
| `qc` | `qc_results` | ML scores + decision + timing |
| `dicomizer` | `conversion_results` | Conversion status, checksums, tool metadata |
| `uploader` | `upload_results` | Upload status, circuit state, retry count |
| `failed_watcher` | `watched_folder_snapshots`, `technician_changes` | RecoverySentry state (schema name preserved) |
| `audit` | `audit_log` | General audit trail |
| `upload_tracking` | (migration 0008) | Upload tracking schema added in Phase 3.8 |

### FileRecord state machine

```
detected → intake_running → intake_registered
intake_registered → qc_pending | dicom_pending
qc_pending → qc_running → qc_passed | qc_failed
qc_passed → dicom_pending → dicom_running → dicom_done | dicom_failed
dicom_done → upload_pending → upload_running → uploaded | upload_failed
*_failed → [RecoverySentry possible requeue → qc_pending]
```

CHECK constraint on `core.file_records.status` enforces this; do not add states without a migration.

### Applied migrations

| Migration | Description |
|-----------|-------------|
| `0001_enterprise_initial_schema` | All core schemas, tables, indexes, constraints |
| `0002_add_operational_columns` | runner_id, host_id, service_version, processed_at, trigger_internal_id to result tables |
| `0003_add_babelshark_stage_tables` | BabelShark per-stage result tables |
| `0004_qc_scanner_dual_mode` | QC scanner dual-mode support |
| `0005_qc_timing_resources` | QC timing and resource columns |
| `0006_babelshark_failed_status` | BabelShark failure status columns |
| `0007_recovery_sentry_columns` | `failed_watcher.technician_changes` full column set |
| `0008_upload_tracking_schema` | `upload_tracking` schema |

### Key constraints (never remove)

| Constraint | Purpose |
|-----------|---------|
| `uq_trigger_per_file_stage` on `core.service_trigger` | Prevents duplicate triggers for same file+stage |
| `UNIQUE idempotency_key` on all result tables | Prevents duplicate inserts on retry |
| `UNIQUE canonical_path` on `core.file_records` | Prevents duplicate registrations |
| `REVOKE UPDATE, DELETE ON events.pipeline_events` | Enforces event log immutability |
| `UNIQUE previous_snapshot_id` on `core.metadata_snapshots` | Enforces linked-list integrity |

### Multi-machine readiness

`core.runner_registrations`: stable `runner_id` (UUID5 from service+host), `host_id`, `last_heartbeat_at` (stale at 120 s), `claimed_by_runner_id` / `claimed_by_host_id` on triggers.

---

## 12. Windows Deployment Notes

### Target environment

- **Conda env:** `C:\Users\Public\conda-envs\babelfish1` (Python 3.12)
- **Project root:** `C:\Users\Public\projects\Palantir\` or `D:\Slides\Palantir\`
- **PostgreSQL:** Remote VM accessible over network; URL in `DATABASE_URL`.
- **OpenSlide:** Native DLL bundle; must register `bin\` directory before any Python import.

### Config variants

Each service has `*.yaml`, `*.linux.yaml`, and `*.windows.yaml` config variants. Windows `.env` should point to `*.windows.yaml` paths:

```dotenv
BABELSHARK_CONFIG_PATH=C:/Users/Public/projects/Palantir/configs/babelshark_config.windows.yaml
QC_CONFIG_PATH=C:/Users/Public/projects/Palantir/configs/qc_config.windows.yaml
DICOM_CONFIG_PATH=C:/Users/Public/projects/Palantir/configs/dicom_config.windows.yaml
RECOVERY_SENTRY_CONFIG=C:/Users/Public/projects/Palantir/configs/recovery_sentry.windows.yaml
SCANNER_FLEET_CONFIG=C:/Users/Public/projects/Palantir/configs/scanner_fleet.yaml
```

### Data directory structure (create before first run)

```
data\
  watch\           BabelShark drop folder
  scanner_fake\    Simulated scanner input (testing)
  staging\         BabelShark intermediate staging
  final\           Completed slides, routed by CaseID subfolders
  failed\          QC-failed slides (RecoverySentry watches)
  suspicious\      QC-suspicious slides (RecoverySentry watches)
  manual_review\   Manual intervention needed (RecoverySentry watches)
  dicom_output\    DICOM conversion output
  run_output\      BabelShark enrichment pipeline outputs
  labels\          Extracted label PNGs
  label_crops\     Label crop intermediates
  roi_debug\       ROI extraction debug images
  roi_debug_parts\ ROI part images
  logs\            Service log files
  upload_test\     Upload testing artifacts
  caseid_test\     CaseID validation testing
  qc_output\       QC output artifacts
  quarantine\      Quarantined / problem files
```

Bootstrap: `.\scripts\windows_bootstrap_dirs.ps1`

### Windows-specific issues

| Issue | Fix |
|-------|-----|
| OpenSlide DLL not found | Set `OPENSLIDE_DLL_PATH` to `bin\` directory containing `libopenslide-1.dll` |
| `psycopg2` build failure | Use `psycopg2-binary` wheel |
| Port conflicts | Set `PATHORYX_DASHBOARD_PORT` in `.env` |
| Subprocess env vars missing | Set env vars as Machine-level or use conda `activate.d` script |
| OpenSlide in subprocesses | Use conda `activate.d` to auto-set `OPENSLIDE_DLL_PATH` |
| `alembic upgrade head` fails | Ensure `DATABASE_URL` in environment before running |

### Windows Services (production)

Use NSSM (`nssm.cc`) to register services:

```powershell
nssm install PalantirDashboard "C:\...\pathoryx-dashboard.exe"
nssm set PalantirDashboard AppEnvironmentExtra DATABASE_URL=... OPENSLIDE_DLL_PATH=...
nssm start PalantirDashboard
```

---

## 13. OpenSlide Runtime Handling

OpenSlide is required by BabelShark (label extraction, metadata intake) and QC service (slide reading). On Windows it requires native DLL pre-loading.

### Resolution order

1. `OPENSLIDE_DLL_PATH` environment variable **(always wins)**
2. `dll_paths.openslide_dll` key in YAML config (BabelShark fallback only)

### Python runtime call pattern

```python
import os
path = os.environ.get("OPENSLIDE_DLL_PATH", "")
if path:
    os.add_dll_directory(path)   # Windows only; no-op on Linux
import openslide
```

This call must happen before any `import openslide` anywhere in the process. The QC service calls `configure_openslide_runtime()` at startup (from `pathoryx_enterprise.runtime.openslide_setup`).

### Linux

OpenSlide is typically installed system-wide (`apt-get install openslide-tools`). No DLL directory configuration needed. `OPENSLIDE_DLL_PATH` is ignored on Linux.

### Conda activate.d approach (Windows, recommended)

Create `C:\Users\Public\conda-envs\babelfish1\etc\conda\activate.d\palantir_env.bat`:

```bat
@echo off
set "OPENSLIDE_DLL_PATH=D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
set "DATABASE_URL=postgresql+psycopg2://pathoryx_user:PASSWORD@vm-host:5432/pathoryx"
```

---

## 14. Environment Variable Strategy

- All configuration via env vars or `.env` file (loaded by pydantic-settings).
- `.env` is gitignored; `.env.example` and `.env.windows.example` are committed with `CHANGE_ME` placeholders.
- Never hardcode credentials in any source file, config, or test fixture.
- Pydantic `BaseSettings` validates all settings at service startup; missing required vars cause a clean fatal error.

### Critical env vars

| Variable | Default | Required |
|----------|---------|---------|
| `DATABASE_URL` | — | Always |
| `BABELSHARK_CONFIG_PATH` | `./configs/babelshark_config.yaml` | BabelShark |
| `QC_CONFIG_PATH` | `./configs/qc_config.yaml` | QC |
| `QC_SERVICE_CONFIG` | `./configs/qc_service.yaml` | QC |
| `DICOM_CONFIG_PATH` | `./configs/dicom_config.yaml` | DICOM |
| `RECOVERY_SENTRY_CONFIG` | `./configs/recovery_sentry.yaml` | RecoverySentry |
| `SCANNER_FLEET_CONFIG` | `./configs/scanner_fleet.yaml` | Dashboard |
| `OPENSLIDE_DLL_PATH` | — | Windows only |
| `PATHORYX_ALLOWED_INPUT_ROOTS` | — | Path validation |

### Optional vars

| Variable | When needed |
|----------|------------|
| `PASNET_SERVER` / `_USERNAME` / `_PASSWORD` | PASNET validation enabled |
| `LIS_SQL_SERVER` / `_USERNAME` / `_PASSWORD` | LIS enrichment enabled |
| `DCMTK_BIN_DIR` | Non-standard dcmtk location |
| `DICOM_PERFORM_UPLOAD` | Override perform_upload flag |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry tracing |
| `PATHORYX_DASHBOARD_PORT` | Override dashboard port |
| `LOG_LEVEL` | Override log verbosity |

---

## 15. Credential Strategy

| Credential | Where configured | Notes |
|------------|-----------------|-------|
| `DATABASE_URL` password | `.env` | PostgreSQL password; never in code |
| PASNET credentials | `.env` | PASNET is currently disabled; set when enabling |
| LIS SQL credentials | `.env` | LIS is currently disabled; set when enabling |
| PACS C-STORE config | `configs/dicom_config.yaml` → `cstore.peer_ip`, `sec_dcm_bin` | Set when enabling real upload |
| Model weights | `models_weights/` directory | `.pth` files committed (binary, not secret) |

**Rules:**
- Every secret in `.env`, never in YAML configs.
- YAML configs may reference env var names but not literal values.
- Pydantic `BaseSettings` reads from env vars with `AliasChoices` where old names exist.
- The `.env` placeholder check: Pydantic startup validator rejects obvious placeholder strings like `CHANGE_ME`.

---

## 16. Naming Conventions

| Concept | Convention | Example |
|---------|-----------|---------|
| SlideID | `N{10digits}{POT}-{BLOCK}-{SECTION}-{STAIN}[_UTC{ts}Z].{ext}` | `N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs` |
| Timestamp in filename | Colons → underscores | `T08_36_39Z` |
| Timestamp in DB | ISO-Z | `2024-08-22T08:36:39Z` |
| `global_artifact_id` | Deterministic UUID5 via `deterministic_artifact_id()` | Never random for same artifact |
| DB schema for RecoverySentry | `failed_watcher` | Legacy; do not rename |
| Service name (code/logs) | `recovery_sentry` | Current canonical |
| Python package | `pathoryx_enterprise` | Legacy; do not rename |
| Brand name | Palantir | Current |
| Database name | `pathoryx` | Current |
| Migration numbering | Zero-padded 4-digit prefix | `0007_recovery_sentry_columns.py` |

---

## 17. Production Assumptions

1. **PostgreSQL is remote.** Services connect via `DATABASE_URL`. Connection pool health check via `pool_pre_ping=True`.
2. **Upload is currently dry-run.** No real PACS uploads occur. All `dry_run=true` / `upload_via_c_store=false`.
3. **BabelShark runs in intake-only mode by default.** `enable_full_pipeline: false`. Enrichment stages require explicit opt-in.
4. **QC GPU is optional.** Services fall back to CPU if CUDA is not available. Docker Compose reserves NVIDIA GPU; production Windows host may not have GPU.
5. **Single-machine deployment.** The orchestrator manages all services on one host. Multi-machine not yet tested.
6. **Alembic migrations are forward-only.** `alembic downgrade` past migration 0001 requires manual permission grants first.
7. **Dashboard is internal.** Backend binds to `127.0.0.1` by default. Do not expose on `0.0.0.0` without TLS reverse proxy.
8. **No RBAC.** Dashboard has no authentication. Assume trusted network.
9. **PASNET and LIS are disabled.** `fail_open: true` for PASNET means outage does not block slides.
10. **File renames are dry-run.** `slide_id_generator.dry_run: true`. Files are not physically renamed until explicitly changed.

---

## 18. Known Technical Debt

| Item | Severity | Notes |
|------|----------|-------|
| **storescu not wired in Uploader** | Critical | `uploader/runner.py._do_upload()` does not call storescu. Real upload to PACS requires: (1) add Sectra settings to `UploaderSettings`, (2) call `build_cstore_commands` + `run_all_cstore_batches` in `_do_upload()`, (3) set `FileRecord → upload_pending` on trigger claim. |
| **DICOM end-to-end not tested** | High | `wsidicomizer` package not installed. Native engine import-verified only. First real SVS conversion not run. |
| **BabelShark full pipeline not smoke-tested** | High | 6 enrichment stages wired and code-correct, but not run against real `.svs` file. Performance of EasyOCR and DataMatrix unknown on production hardware. |
| **`dicom_done → upload_pending` ordering** | Medium | Uploader should set `upload_pending` when it claims the trigger. Currently DICOM service sets `dicom_done` and uploader jumps to `upload_running`. |
| **`step_run_internal_id` missing from `dicomizer.conversion_results`** | Low | Present in qc model, not added for dicomizer. Low priority schema gap. |
| **`babelshark_config.yaml` sqlite_db_path** | Low | Legacy fallback reference. PostgreSQL is authoritative. No code uses SQLite path. |
| **Docker images not CI-built** | Low | Dockerfiles updated but not tested end-to-end in a container. |
| **RUNBOOK.md references port 8085** | Low | `curl localhost:8085/health` in ops runbook refers to deprecated failed_watcher. Should be 8087. |

---

## 19. Roadmap Ideas

| Feature | Priority | Notes |
|---------|----------|-------|
| Wire storescu into Uploader | P0 | Blocking all real PACS uploads |
| BabelShark full pipeline smoke test | P0 | Required before enabling enrichment in production |
| DICOM end-to-end smoke test | P0 | Install wsidicomizer; run real SVS → DICOM |
| Windows end-to-end test | P0 | First full pipeline run on Windows host |
| RBAC for Dashboard | P1 | Auth context already stubbed |
| AI Copilot panel | P1 | Slot already in `Shell.tsx` |
| Multi-site support | P1 | Sidebar label → dropdown from `/dashboard/api/sites` |
| Decouple enrichment stages into async workers | P2 | Each stage becomes a ServiceTrigger consumer (same DB pattern) |
| LIS enrichment activation | P2 | Configure credentials; test with Nexus SQL Server |
| PASNET validation activation | P2 | Configure credentials; test with PASNet |
| Prometheus / Grafana dashboard | P2 | Metrics already exported; needs Grafana config |
| End-to-end CI pipeline | P2 | Docker Compose + pytest integration tests in CI |
| Slide throughput benchmarking | P3 | Characterize per-slide latency for each stage |
| Per-stage async worker services | P3 | After Phase 5 of enrichment roadmap |
| DICOM C-FIND query back from PACS | P3 | Verify upload success by querying IDS7 |
