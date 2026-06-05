# Pathoryx Enterprise — Project State Report

Generated: 2026-06-05 | Last updated: 2026-06-05 (Phase 2 cleanup)

---

## 1. Current Architecture Summary

Pathoryx Enterprise is a **production-grade WSI (Whole Slide Image) ingestion and processing pipeline** processing 500–1000 slides/day across four main stages:

```
[Watch Folder] → BabelShark (intake/enrichment)
              → QC Service (ML inference)
              → DICOM Service (conversion)
              → Uploader (PACS C-STORE)
              → [Sectra PACS]
```

- **Language / runtime**: Python 3.12, SQLAlchemy 2.x, FastAPI, React (TypeScript) dashboard
- **Database**: PostgreSQL (all inter-service communication goes through DB — no message broker)
- **Trigger mechanism**: `core.service_trigger` table with `SELECT FOR UPDATE SKIP LOCKED`
- **Event log**: `events.pipeline_events` — append-only, no UPDATE/DELETE granted to app user
- **Deployment targets**: Single-machine (orchestrator), Docker Compose, or individual processes
- **Current active branch**: `main`

---

## 2. Service List

| Service | CLI Entry Point | Health Port | Metrics Port | Python Module |
|---------|----------------|-------------|--------------|---------------|
| **BabelShark** | `pathoryx-babelshark` | 8081 | 9091 | `pathoryx_enterprise.services.babelshark` |
| **QC Service** | `pathoryx-qc` | 8082 | 9092 | `pathoryx_enterprise.services.qc` |
| **DICOM Service** | `pathoryx-dicom` | 8083 | 9093 | `pathoryx_enterprise.services.dicom` |
| **Uploader** | `pathoryx-uploader` | 8084 | 9094 | `pathoryx_enterprise.services.uploader` |
| **RecoverySentry** | `pathoryx-recovery-sentry` | 8087 | 9097 | `pathoryx_enterprise.services.recovery_sentry` |
| ~~Failed Watcher~~ | ~~`pathoryx-failed-watcher`~~ | ~~8085~~ | ~~9095~~ | **DEPRECATED** — stub prints error and exits; use `pathoryx-recovery-sentry` |
| **Dashboard** | `pathoryx-dashboard` | 8090 | — | `pathoryx_enterprise.services.dashboard` |
| **Orchestrator** | `pathoryx-orchestrate` | — | — | `pathoryx_enterprise.orchestrator` |

### BabelShark
- Watches input folders for new WSI files (`.svs`, `.ndpi`, `.tif`, `.tiff`, `.scn`, `.mrxs`, `.bif`, `.png`, `.jpg`, `.jpeg`)
- Copies/moves WSIs, registers `FileRecord` in DB
- Runs enrichment pipeline: label extraction → DataMatrix → ROI fallback → stain extraction → slide ID generation
- Key files: `runner.py`, `stage_runner.py`, `core/collect_slides.py`

### QC Service
- ML inference: penmark detection (MobileNetV3), bubble detection (ConvNeXtTiny), stain classification (MobileNetV3), blur detection (ResNet18)
- `ModelRegistry` singleton — weights loaded once at startup via `@cached_property`
- Decision thresholds configurable in `qc_config.yaml`; outputs `accepted` or `rejected`
- Key files: `runner.py`, `engine/services/qc_inference_service.py`, `engine/services/decision_service.py`

### DICOM Service
- WSI → IDS7-compatible DICOM via `wsidicomizer` (preferred) or `xraydcm`
- Optional LIS metadata enrichment (disabled by default)
- Upstream to Sectra PACS via `storescu` C-STORE in configurable batches (`SECTRA_CSTORE_BATCH_SIZE=500`)
- Key files: `runner.py`, `engine/services/conversion_service.py`, `engine/services/wsidicom_utils.py`

### Uploader
- Final status tracking, circuit breaker (CLOSED/OPEN/HALF_OPEN), retry logic
- Wraps DICOM service upload result; updates `FileRecord.status` to `uploaded` or `upload_failed`
- Key files: `runner.py`, `circuit_breaker.py`

### RecoverySentry (service identity) / FailedWatcher (DB schema identity)
- Monitors `failed/`, `suspicious/`, `manual_review/` folders every 30 s
- Detects technician file interventions (rename, replace, new file, remove)
- Auto-recovers valid SlideID files → moves to `final/<CaseID>/` → requeues for QC
- DB schema: `failed_watcher` (intentionally kept to avoid migration risk)
- Key files: `recovery_engine.py`, `change_processor.py`, `slide_id_parser.py`, `metadata_extractor.py`

### Dashboard
- FastAPI backend (port 8090), serves built React frontend from `dashboard-ui/dist/`
- All API endpoints are read-only except: technician-rename, review-state PATCH
- SSE stream at `/dashboard/api/stream` for live UI updates (5 s poll, 25 s heartbeat)
- Key files: `app.py`, `queries.py`, `schemas.py`, `actions.py`, `sse.py`

---

## 3. Database Schemas and Main Tables

All tables live in PostgreSQL. Connection via `DATABASE_URL` env var.

### `core.*`
| Table | Purpose |
|-------|---------|
| `file_records` | One row per physical WSI file; pipeline state machine status |
| `metadata_snapshots` | Immutable versioned metadata; linked list via `previous_snapshot_id` |
| `pipeline_runs` | One row per service processing attempt |
| `step_runs` | One row per pipeline stage within a run |
| `service_trigger` | Inter-service message queue; `SELECT FOR UPDATE SKIP LOCKED` |
| `technical_metrics` | Per-step resource usage (CPU, RAM, GPU, disk I/O) |
| `runner_registrations` | Active service runners; heartbeat-based liveness |

**`FileRecord` status machine:**
```
detected → intake_running → intake_registered
intake_registered → qc_pending | dicom_pending
qc_pending → qc_running → qc_passed | qc_failed
qc_passed → dicom_pending
dicom_pending → dicom_running → dicom_done | dicom_failed
dicom_done → upload_pending
upload_pending → upload_running → uploaded | upload_failed
*_failed → (RecoverySentry monitoring, possible requeue)
```

### `events.*`
| Table | Purpose |
|-------|---------|
| `pipeline_events` | Append-only event log; UPDATE/DELETE revoked from app user |

### `babelshark.*`
| Table | Purpose |
|-------|---------|
| `extraction_results` | Final extraction outcome per slide |
| `datamatrix_results` | Per-label DataMatrix barcode decode |
| `stain_results` | OCR/ROI stain detection |
| `roi_results` | ROI metadata (DataMatrix fallback) |
| `color_marker_results` | Color marker detection for research routing |
| `pasnet_validation_results` | LIS/PASNet validation (currently disabled) |
| `slide_routing_decisions` | Final routing + rename decision per slide |

### `qc.*`
| Table | Purpose |
|-------|---------|
| `qc_results` | ML inference outcome; blur/stain/penmark/bubble metrics; accept/reject |

### `dicomizer.*`
| Table | Purpose |
|-------|---------|
| `conversion_results` | Conversion outcome, checksums, tool version |

### `uploader.*`
| Table | Purpose |
|-------|---------|
| `upload_results` | Upload status, retry count, response summary |

### `failed_watcher.*`
| Table | Purpose |
|-------|---------|
| `watched_folder_snapshots` | Current state of all files in monitored folders (upserted each poll) |
| `technician_changes` | Immutable audit log of detected technician interventions |

### `audit.*`
| Table | Purpose |
|-------|---------|
| `audit_log` | General audit trail (model: `pathoryx_enterprise/db/models/audit.py`) |

---

## 4. Config Files and What Each Controls

| File | Controls |
|------|----------|
| `configs/babelshark_config.yaml` | Watch dirs, staging/final/failed paths, WSI types, OCR parameters, stain list path, ROI set, slide ID generator, pasnet validator (disabled), operation_mode (copy/move), dry_run flag |
| `configs/qc_config.yaml` | QC input dirs, model weight paths, enabled modules (stain/penmark/bubble/blur/sharpness), inference parameters, thresholds, output paths |
| `configs/qc_service.yaml` | QC service-level settings (separate from model config) |
| `configs/dicom_config.yaml` | DICOM conversion method (wsidicomizer/xraydcm), upload dry_run flag, C-STORE settings (peer_ip, port, sec_dcm_bin), LIS enrichment toggle, filename matching patterns |
| `configs/dicom_config_production.yaml` | **Production reference only** — live Sectra C-STORE settings; `dry_run=false`, `upload_via_c_store=true`, LIS enabled. DO NOT use for testing. |
| `configs/recovery_sentry.yaml` | Watch folders, final_destination_root, recovery options (auto_recover, duplicate_strategy, checksum_mode), poll interval |
| `.env` / `.env.example` | `DATABASE_URL`, service ports, config file paths, PASNET/LIS credentials, OTEL settings, Prometheus settings |
| `alembic.ini` | Alembic migration configuration; `script_location = pathoryx_enterprise/db/migrations` |
| `prometheus.yml` | Prometheus scrape targets (all service metrics endpoints) |
| `docker-compose.yml` | Docker service definitions; depends on `postgres` health check |
| `pyproject.toml` | Package metadata, dependencies, entry points, ruff/mypy/pytest config |

**Current safety flags in configs:**
- `dicom_config.yaml`: `upload.dry_run: true`, `cstore.upload_via_c_store: false`
- `babelshark_config.yaml`: `pasnet_validation: false`, `slide_id_generator.dry_run: true`

---

## 5. Dashboard Pages and Implemented Features

**React frontend** (`dashboard-ui/src/pages/`) + **FastAPI backend** (`pathoryx_enterprise/services/dashboard/app.py`)

| Page | Route | Key Features |
|------|-------|--------------|
| **Overview** | `/` | Slide status KPI counts, trigger queue summary, active runner count, events-last-24h |
| **Slide Explorer** | `/slides` | Paginated slide list; filter by status; links to detail |
| **Slide Detail** | `/slides/:artifactId` | Full slide detail: file record, QC result, conversion, upload, events, triggers, recovery history, BabelShark extraction result |
| **Queue Monitor** | `/queues` | Per-service trigger queue depths (pending/running/failed); bar charts; telemetry strip |
| **Failure Center** | `/failures` | Failed slides + failed triggers list; recovery badge per artifact |
| **Recovery Center** | `/recovery` | Watch folder summaries, monitored file list, technician rename drawer, review state transitions, label image preview, audit trail |
| **Operations Center** | `/operations` | Extended service health (heartbeat ages, queue depths), stuck trigger detection, operational incidents, environment/safety config, DB health metrics |

**Real-time layer (SSE):**
- Endpoint: `GET /dashboard/api/stream`
- Detects: `queue_updated`, `pipeline_event_created`, `file_record_updated`, `recovery_event_created`, `service_health_updated`
- No extra DB tables — uses `MAX(pk)` / `MAX(updated_at)` queries on existing indexes
- Polls DB every 5 s; heartbeat comment every 25 s

**Write endpoints (non-read-only):**
- `POST /dashboard/api/recovery/files/{file_id}/technician-rename` — rename file in watched folder
- `PATCH /dashboard/api/recovery/changes/{change_id}/review-state` — update review status
- `POST /dashboard/api/recovery/validate-filename` — validate proposed filename (no side effects)

---

## 6. Current Recovery Workflow

**RecoverySentry lifecycle:**
1. Every 30 s: scan `failed/`, `suspicious/`, `manual_review/` folders
2. Compare filesystem snapshot to previous `watched_folder_snapshots` snapshot
3. On change detected → classify change type (`rename`, `replace`, `new_file`, `remove`, etc.)
4. Create immutable `TechnicianChange` row (idempotency key prevents duplicates)
5. Wait `stable_after_seconds=10` for file to stop changing
6. Validate filename against SlideID pattern: `N{10digits}{POT}-{BLOCK}-{SECTION}-{STAIN}[_UTC...Z].ext`
7. If valid with timestamp → build destination `final/<CaseID>/<filename>`
8. If valid without timestamp → extract from WSI metadata via OpenSlide; append timestamp
9. If destination conflict → apply `duplicate_strategy: suffix` (add `_1`, `_2`, ...)
10. Atomic move → DB transaction:
    - `file_records.current_file_path` + `status = qc_pending`
    - Insert `service_trigger` for QC (idempotent)
    - Emit `recovery_sentry.auto_recovered` event
11. If filename invalid → record `manual_review_required`; file stays in folder

**Review state machine** (`TechnicianChange.review_status`):
```
detected → linked | unlinked → reviewed → requeued | dismissed
```

**Dashboard Recovery Center** allows technicians to:
- View all files in monitored folders
- Preview label images
- Propose renamed filenames (validated in real time)
- Approve or dismiss recovery items
- See full audit trail per file

---

## 7. Current Technician Review / Rename Status

**IMPLEMENTED:**
- `TechnicianChange` model fully deployed (migration 0007 applied all columns)
- `WatchedFolderSnapshot` with extended fingerprint columns (slide_id, case_id, extension, inode_number, partial_sha256)
- `execute_technician_rename()` in `dashboard/actions.py` — performs the rename on disk
- `update_review_state()` in `dashboard/actions.py` — transitions review status + emits events
- `validate_filename_structured()` — real-time validation before rename
- `GET /dashboard/api/recovery/files/{file_id}/label-image` — serves label image from BabelShark label dir
- `GET /dashboard/api/recovery/files/{file_id}/label-preview` — preview data (available/unavailable)
- `GET /dashboard/api/recovery/files/{file_id}/audit-trail` — full change + event history
- `TechnicianReviewDrawer` React component with rename form, validation, label preview

**Label image serving:** reads `label_root_dir` from `BABELSHARK_CONFIG_PATH` env var; searches for `.jpg/.jpeg/.png/.tif` matching the file stem.

---

## 8. Current Upload Mode and Safety Status

**All upload gates are currently SAFE (dry-run / disabled):**

| Setting | File | Current Value | Safe? |
|---------|------|---------------|-------|
| `upload.dry_run` | `configs/dicom_config.yaml` | `true` | ✅ |
| `cstore.upload_via_c_store` | `configs/dicom_config.yaml` | `false` | ✅ |
| `dicom_service.perform_upload` | `configs/dicom_config.yaml` | `false` | ✅ |
| `pasnet_validation` pipeline stage | `configs/babelshark_config.yaml` | `false` | ✅ |
| `slide_id_generator.dry_run` | `configs/babelshark_config.yaml` | `true` | ✅ |
| `recovery.auto_recover_valid_slide_id` | `configs/recovery_sentry.yaml` | `true` | ℹ️ Moves files |

**Circuit breaker:** `uploader/circuit_breaker.py` — threshold=5 failures → OPEN state, resets after `reset_seconds`

**To enable real C-STORE upload (requires explicit approval):**
1. Verify PACS connectivity: `ping path-pacs2; Test-NetConnection path-pacs2 -Port 32001`
2. Set `upload.dry_run: false` in `dicom_config.yaml`
3. Set `cstore.upload_via_c_store: true`
4. Confirm `peer_ip` and `sec_dcm_bin` are correct
5. Restart DICOM service

---

## 9. Current Windows Migration Status

**Status: Active test environment configured and ready for first end-to-end run**

- Conda environment: `C:\Users\Public\conda-envs\babelfish1` (Python 3.12)
- Project clone target: `C:\Users\Public\projects\Pathoryx\`
- All configs have Windows paths set (`C:/Users/Public/projects/Pathoryx/data/...`)
- Data folder structure created via `.gitkeep` files in `data/`
- `WINDOWS_SETUP.md` documents full step-by-step setup
- Dashboard frontend: `npm run dev` → http://localhost:5173; built version → http://127.0.0.1:8090

**Windows-specific issues known:**
- OpenSlide: must add `bin\` directory to `PATH` manually
- `psycopg2`: use `psycopg2-binary` wheel
- Port conflicts: change `PATHORYX_DASHBOARD_PORT` in `.env`
- `alembic upgrade head` needs `DATABASE_URL` set in environment or `.env`

**Expected data folders on Windows:**
```
data/watch, scanner_fake, staging, final, failed, suspicious,
manual_review, dicom_output, run_output, labels, label_crops,
roi_debug, roi_debug_parts, logs, upload_test, caseid_test,
qc_output, quarantine
```

---

## 10. Remaining Work Before Windows Migration Is Complete

1. **Run full end-to-end test** with real WSI files through all stages (BabelShark → QC → DICOM dry run → upload dry run)
2. **Verify database migrations** apply cleanly: `alembic upgrade head` on fresh Windows PostgreSQL
3. **PASNET connection** — currently disabled; configure `PASNET_SERVER/USERNAME/PASSWORD` when needed
4. **LIS enrichment** — currently disabled in `dicom_config.yaml`; configure `LIS_SQL_SERVER/USERNAME/PASSWORD` when needed
5. **Real C-STORE upload** — currently dry-run; requires PACS network connectivity verification
6. ~~**Dashboard frontend build**~~ — verified clean in Phase 2 (`tsc` + `vite build`, 0 errors, 1908 modules, 181 kB main bundle)
7. **Model weights verification** — confirm `.pth` files in `models_weights/` are accessible from the QC service on the Windows machine
8. **OpenSlide PATH** — verify OpenSlide DLLs discoverable before first run
9. **Backfill decision**: decide whether to backfill historical slide data from any existing legacy DB

---

## 11. Known Risks / TODOs

| Area | Risk / TODO |
|------|-------------|
| **Upload safety** | `cstore.upload_via_c_store` is `false` — never enable without explicit data governance approval |
| **PASNET validation** | Disabled (`pasnet_validation: false`). When enabled, it contacts a live LIS; `fail_open: true` means a PASNET outage does not block slides |
| **RecoverySentry critical path** | If file moves to `final/` but DB update fails → manual SQL requeue required (see `RECOVERY_SENTRY.md`) |
| **Event log permissions** | `events.pipeline_events` has UPDATE/DELETE revoked at DB level — never run `alembic downgrade` past migration 0001 without regranting permissions first |
| **BabelShark `.bak` files** | ~~Deleted in Phase 2~~ — `stage_runner.py.bak` and `slide_id_generator.py.bak` removed |
| **SQLite legacy path** | `babelshark_config.yaml` still references `sqlite_db_path` — this is a legacy fallback; PostgreSQL is authoritative |
| **Never use `dicom_config_production.yaml` for testing** | Renamed from `dicom_config_windows.yaml`; has `dry_run=false` and `upload_via_c_store=true`; for testing always use `dicom_config.yaml` |
| **GPU requirement (QC)** | `docker-compose.yml` reserves an NVIDIA GPU for the QC service; Windows test machine may use CPU fallback |
| **`allow_filesystem_timestamp_fallback: false`** | Means files without WSI metadata timestamps won't auto-recover; must be noted to technicians |
| **`slide_id_generator.dry_run: true`** | File renames are not executed; change to `false` when ready for production |

---

## 12. Exact Commands to Run Tests / Builds

### Install project (Linux/Mac)
```bash
cd /home/shahram/Pathoryx-Enterprise
pip install -e .
pip install -e ".[qc]"
pip install -e ".[dashboard]"
pip install -e ".[dev]"
```

### Install project (Windows PowerShell)
```powershell
conda activate C:\Users\Public\conda-envs\babelfish1
pip install -e .
pip install -e ".[dashboard]"
pip install -e ".[qc]"
pip install -e ".[dev]"
```

### Run Alembic migrations
```bash
export DATABASE_URL="postgresql+psycopg2://pathoryx_user:PASSWORD@localhost:5432/pathoryx"
alembic upgrade head
# Verify schemas:
psql "$DATABASE_URL" -c "\dn"
```

### Run unit tests
```bash
cd /home/shahram/Pathoryx-Enterprise
pytest tests/unit/ -v
```

### Run integration tests (requires live DB)
```bash
pytest tests/integration/ -v
```

### Run all tests with coverage
```bash
pytest tests/ --cov=pathoryx_enterprise --cov-report=term-missing
```

### Build dashboard frontend
```bash
cd dashboard-ui
npm install
npm run build
# Output in dashboard-ui/dist/
```

### Start dashboard frontend (dev mode)
```bash
cd dashboard-ui
npm run dev
# → http://localhost:5173
```

### Start all services (orchestrator)
```bash
pathoryx-orchestrate
```

### Start services individually
```bash
pathoryx-babelshark         # intake
pathoryx-qc                 # ML inference
pathoryx-dicom              # conversion
pathoryx-uploader           # upload
pathoryx-recovery-sentry    # watches failed/suspicious/manual_review
pathoryx-dashboard          # FastAPI backend on port 8090
```

### Start with Docker Compose
```bash
docker-compose up postgres -d
docker-compose run --rm migrate
docker-compose up
```

### Health checks
```bash
curl http://localhost:8081/health   # BabelShark
curl http://localhost:8082/health   # QC
curl http://localhost:8083/health   # DICOM
curl http://localhost:8084/health   # Uploader
curl http://localhost:8085/health   # Failed Watcher
curl http://127.0.0.1:8090/dashboard/api/overview   # Dashboard API
```

### Lint / type check
```bash
ruff check pathoryx_enterprise/
mypy pathoryx_enterprise/
```

---

## 13. What Should NOT Be Changed Without Approval

| Area | Constraint |
|------|------------|
| **`events.pipeline_events`** | Never add UPDATE/DELETE to app user permissions; never alter the append-only contract |
| **`core.service_trigger` unique constraint** | `uq_trigger_per_file_stage` prevents duplicate triggers — removing it would cause double-processing |
| **`failed_watcher` DB schema name** | Keep as `failed_watcher` (not `recovery_sentry`) — changing it requires a data migration |
| **`upload.dry_run`** | Must remain `true` until PACS connectivity is verified and data governance approves |
| **`cstore.upload_via_c_store`** | Must remain `false` until explicitly approved |
| **`pasnet_validation` stage** | Must remain `false` until PASNET credentials are configured and connection verified |
| **`slide_id_generator.dry_run`** | Must remain `true` until production rename workflow is approved |
| **`FileRecord.canonical_path` unique constraint** | Enforced at DB level; removing would allow duplicate registrations |
| **`MetadataSnapshot` rows** | Immutable by design; never UPDATE or DELETE rows from `core.metadata_snapshots` |
| **Alembic migration numbering** | Do not renumber or reorder migrations; down_revision chain must remain intact |
| **`ModelRegistry` singleton** | Weights must be loaded once at startup; do not revert to per-slide instantiation (was the original bug) |
| **`is_file_stable()` — no sleep** | The function must not block; callers poll on interval |
| **`validate_path_under_roots()`** | Always call before any filesystem operation involving user-provided or config-provided paths |
| **`SELECT FOR UPDATE SKIP LOCKED`** | Must remain on `ServiceTrigger.dequeue_next()` — removing it causes double-processing under concurrent workers |

---

## Summary for Future Sessions

**Files inspected:**
- `ARCHITECTURE.md`, `README.md`, `WINDOWS_SETUP.md`, `MIGRATION_PLAN.md`, `RECOVERY_SENTRY.md`
- All `pathoryx_enterprise/db/models/*.py` (core, babelshark, qc, dicomizer, uploader, failed_watcher, events)
- `pathoryx_enterprise/db/migrations/versions/0007_recovery_sentry_columns.py`
- `pathoryx_enterprise/services/dashboard/app.py`, `sse.py`
- `pathoryx_enterprise/services/babelshark/runner.py`, `stage_runner.py` (header)
- `pathoryx_enterprise/services/dicom/engine/services/conversion_service.py` (header)
- `pathoryx_enterprise/services/uploader/circuit_breaker.py`
- `dashboard-ui/src/App.tsx`
- `configs/babelshark_config.yaml`, `qc_config.yaml`, `dicom_config.yaml`, `recovery_sentry.yaml`
- `.env.example`, `pyproject.toml`, `docker-compose.yml`

**Report sections created:** All 13 sections as specified.

**Remaining unknowns:**
- Exact `runner_daily_enterprise.py` usage vs. `stage_runner.py` — the legacy runner still exists at root level; unclear whether it is actively used in production or is superseded entirely by `stage_runner.py`
- `qc_service.yaml` content not inspected separately (separate from `qc_config.yaml`)
- `pathoryx_enterprise/services/recovery_sentry/recovery_engine.py` internals not fully inspected — auto-recovery implementation details beyond what `RECOVERY_SENTRY.md` documents
- Docker image build status — `Dockerfile.*` files exist but images have not been built and tested end-to-end in CI

---

## Phase 2 Changelog (2026-06-05)

### Files deleted
| File | Reason |
|------|--------|
| `pathoryx_enterprise/services/babelshark/stage_runner.py.bak` | Pre-refactor backup; superseded by current `stage_runner.py` |
| `pathoryx_enterprise/services/babelshark/core/slide_id_generator.py.bak` | Pre-enterprise backup; superseded |
| `reports/.Rhistory` | R console history artifact; not project code |
| `reports/pathoryx_enterprise_architecture.md` | Generated analysis (rev 0005, 2026-05-29); stale and superseded by this document |
| `reports/diagrams.md` | Generated Mermaid diagrams from same stale analysis session |
| `reports/schema_summary.json` | Generated JSON schema dump (rev 0005); superseded |

### Files renamed
| Old name | New name | Reason |
|----------|----------|--------|
| `configs/dicom_config_windows.yaml` | `configs/dicom_config_production.yaml` | Old name implied "Windows test config"; it is actually the live production reference with `dry_run=false` and `upload_via_c_store=true` — a critical naming hazard |

### Docs fixed
| File | Change |
|------|--------|
| `DATABASE_SETUP.md` | All 7 occurrences of `pathoryx_enterprise` (DB name) replaced with `pathoryx` to match canonical name in `.env.example`, `WINDOWS_SETUP.md`, and `MIGRATION_PLAN.md` |
| `docs/PATHORYX_PROJECT_STATE.md` | Reflects all Phase 2 changes; dashboard build verified ✅ |

### Verification results
| Check | Result |
|-------|--------|
| Unit tests | **300 / 300 passed** (2.98 s) |
| Dashboard frontend build | **Clean** — `tsc` + `vite build`, 0 errors, 1908 modules |
| `upload.dry_run` in `dicom_config.yaml` | `true` ✅ |
| `cstore.upload_via_c_store` in `dicom_config.yaml` | `false` ✅ |
| `.env` not committed | Confirmed — `.gitignore` excludes `.env` and `*.env` |
| `.env.example` placeholders only | Confirmed — all secrets are `CHANGE_ME` |
| Runtime `data/` folders | All empty (`.gitkeep` only); `data/` in `.gitignore` with `!data/**/.gitkeep` exception |

---

## Phase 3 Changelog (2026-06-05)

### Architecture decision: RecoverySentry is the only recovery path

`services/failed_watcher/` (the old FailedWatcher service) has been retired. RecoverySentry is now the single, canonical recovery engine.

### Files removed
| File | Reason |
|------|--------|
| `runner_daily_enterprise.py` (root, 1510 lines) | Legacy batch runner; superseded entirely by `stage_runner.py` |
| `services/failed_watcher/runner.py` | Dead service poll loop |
| `services/failed_watcher/watcher.py` | Dead scan implementation |
| `services/failed_watcher/requeue_service.py` | Dead; never imported by RecoverySentry |
| `services/failed_watcher/config.py` | Dead config for removed runner |
| `docker/Dockerfile.failed_watcher` | Replaced by `docker/Dockerfile.recovery_sentry` |
| `tests/integration/test_failed_watcher_e2e.py` | Tested removed infrastructure |

### Files added/created
| File | Purpose |
|------|---------|
| `services/recovery_sentry/change_detector.py` | Moved here from `failed_watcher/` (canonical location) |
| `docker/Dockerfile.recovery_sentry` | Correct container for RecoverySentry (ports 8087/9097) |
| `tests/unit/test_phase3_recovery_finalization.py` | 14 new tests (see below) |

### Files converted to stubs/shims
| File | Change |
|------|--------|
| `services/failed_watcher/change_detector.py` | Now a re-export shim pointing to `recovery_sentry.change_detector` |
| `services/failed_watcher/__init__.py` | Deprecation notice |
| `services/failed_watcher/main.py` | Deprecation stub: prints error message and exits with code 1 |

### Code fixes
| File | Fix |
|------|-----|
| `services/dashboard/app.py` | Added missing `from pathlib import Path` import (bug: `Path` was used in `label_image` endpoint but never imported; would have caused 500 at runtime) |
| `services/babelshark/stage_runner.py` | Removed all `runner_daily_enterprise.py` references from module docstring |
| `db/models/core.py` | Updated service name in comments: `failed_watcher` → `RecoverySentry` |
| `db/models/events.py` | Updated standard event type list to use `recovery_sentry.*` types |
| `db/repositories/trigger.py` | Updated dead-letter strategy comment |
| `services/babelshark/db_writer.py` | Updated service reference comment |
| `tests/unit/test_change_detector.py` | Updated import to canonical `recovery_sentry.change_detector` path |
| `dashboard-ui/src/utils/formatters.ts` | `failed_watcher` now maps to `'RecoverySentry'` (was 'RecoverySentry Legacy Watcher') |

### Infrastructure updated
| File | Change |
|------|--------|
| `docker-compose.yml` | `failed_watcher` service replaced by `recovery_sentry` (ports 8087/9097, correct volumes) |
| `prometheus.yml` | Scrape target updated to `recovery_sentry:9097` |
| `pyproject.toml` | `pathoryx-failed-watcher` entry annotated as deprecated (still registered so users get helpful error) |
| `ARCHITECTURE.md` | Service table updated |
| `OPERATIONS.md` | CLI and requeue section updated |
| `RUNBOOK.md` | CLI updated |
| `SETUP.md` | CLI updated |
| `MIGRATION_PLAN.md` | CLI updated |
| `README.md` | CLI updated |
| `RECOVERY_SENTRY.md` | Backward-compat section replaced with migration notes |
| `TESTING_WITH_REAL_DATA.md` | `failed watcher` references updated |
| `TROUBLESHOOTING.md` | Service name updated |
| `CLAUDE.md` | Removed infrastructure section added |

### New tests (14 added in `test_phase3_recovery_finalization.py`)
| Class | What it verifies |
|-------|-----------------|
| `TestChangeDetectorShim` | Old import path still works; old and new imports are the same object |
| `TestDeprecatedCLIStub` | `pathoryx-failed-watcher` main() exits with code 1 |
| `TestQuarantineBehavior` | Invalid/unsupported filenames stay in watch folder, never moved |
| `TestRequeueBehavior` | Valid auto-recovered file always enqueues a QC trigger |
| `TestRenameAuditFlow` | Dashboard rename creates `TechnicianChange` with `inferred_action='dashboard_correction'`; works without a technician note |
| `TestInvalidTransitionRejected` | Invalid review-state transitions return 422 |
| `TestTerminalState` | `reviewed` state has no outgoing transitions; all expected states present |
| `TestLabelPreviewDegrades` | Missing label dir returns 404 (not 500); DB error on preview returns 200 with `available=False` |

### Finalized RecoverySentry review state machine
```
detected → investigating → corrected → requeued → reviewed  (terminal)
detected → dismissed ↔ detected  (re-openable)
unlinked → investigating | dismissed
linked   → investigating | reviewed
corrected → investigating  (can revisit)
```
Every transition emits an immutable `dashboard.review_state_updated` PipelineEvent.

### Technician workflow (how to use)
1. **Dashboard → Recovery Center**: see all files in `failed/`, `suspicious/`, `manual_review/`
2. **Click a file** → opens TechnicianReviewDrawer showing filename, parsed metadata, label image (if available)
3. **Type corrected filename** → real-time validation shows `valid` / `partially_valid` / `invalid` classification
4. **Click Apply Rename** → file renamed on disk; RecoverySentry runs auto-recovery logic:
   - If valid SlideID + timestamp → moves to `final/<CaseID>/` → QC trigger created
   - If valid SlideID, no timestamp → timestamp extracted from WSI metadata → same path
   - If timestamp missing from metadata → stays in watch folder, flagged `manual_review_required`
5. **Alternatively**: rename file directly in the watched folder → RecoverySentry detects it on the next 30 s poll cycle and runs the same recovery logic automatically
6. **Review state** can be manually advanced via the PATCH endpoint for operational tracking

### Verification results
| Check | Result |
|-------|--------|
| Unit tests | **314 / 314 passed** (300 pre-existing + 14 new) |
| Dashboard frontend build | **Clean** — 0 TypeScript errors, 1908 modules |
| `app.py` `Path` import bug | **Fixed** |
| `change_detector.py` canonical location | `services/recovery_sentry/change_detector.py` |
| Old import path backward compat | `services/failed_watcher/change_detector.py` re-exports correctly |

### Known remaining risks before Windows migration
| Risk | Status |
|------|--------|
| Docker images not built or tested | Dockerfiles updated but no CI build verification |
| `configs/dicom_config_production.yaml` has live upload settings | Known, labeled clearly; never use for testing |
| `pasnet_validation` disabled | Intentional; credentials not configured |
| `slide_id_generator.dry_run: true` | Intentional; file renames not executing |
| Real C-STORE upload requires PACS connectivity | Must be verified before enabling |
| End-to-end test with real WSI files | Not yet done |
