# Palantir Enterprise — AI Session Handoff Guide

> **Purpose:** Enable a new AI session to resume development with minimal lost context.
> **Read this file first.** Then read `docs/master_context.md` and `docs/current_project_state.md`.
> **Last updated:** 2026-06-10

---

## Project in One Paragraph

Palantir Enterprise (formerly Pathoryx Enterprise) is a production-grade digital pathology pipeline that ingests Whole Slide Images (WSI), runs ML-based quality control, converts them to DICOM, and uploads to a Sectra IDS7 PACS. It is written in Python 3.12 with a React/TypeScript dashboard. All inter-service communication goes through PostgreSQL — no message broker. There are 6 active Python services (`pathoryx-babelshark`, `pathoryx-qc`, `pathoryx-dicom`, `pathoryx-uploader`, `pathoryx-recovery-sentry`, `pathoryx-dashboard`) plus an orchestrator. The primary deployment target is a Windows workstation. The codebase is at `/home/shahram/Palantir` on Linux or `C:\Users\Public\projects\Palantir` on Windows.

---

## Current Priorities (as of 2026-06-10)

### P0 — Blocking production

1. **Wire storescu into Uploader** — `uploader/runner.py._do_upload()` does not call storescu. Upload to PACS will never execute. Files to edit: `services/uploader/runner.py`, `services/uploader/config.py`. Need to move Sectra settings from `DICOMSettings` into `UploaderSettings` and call `upload_utils.build_cstore_commands()` + `run_all_cstore_batches()`.

2. **Install wsidicomizer** — DICOM conversion requires `wsidicomizer` Python package. Install: `pip install wsidicom wsidicomizer`. Run first real SVS → DICOM conversion smoke test.

3. **BabelShark enrichment smoke test** — Enable `enable_full_pipeline: true` in config and run a real `.svs` file through all 6 stages. No end-to-end test has been done yet.

4. **Windows end-to-end test** — Full pipeline on Windows host. Follow `docs/WINDOWS_MIGRATION_CHECKLIST.md` phases 6–7.

---

## Important Warnings

### NEVER DO THESE

| Action | Reason |
|--------|--------|
| Enable `upload.dry_run: false` or `cstore.upload_via_c_store: true` | Live PACS upload — requires data governance approval |
| Use `configs/dicom_config_production.yaml` for testing | Has live production settings — for reference only |
| UPDATE or DELETE from `events.pipeline_events` | Revoked at DB level; causes permission error |
| Add `UPDATE`/`DELETE` grant to `events.pipeline_events` | Breaks event sourcing immutability guarantee |
| Rename DB schema `failed_watcher` | Would break RecoverySentry without a data migration |
| Rename Python package `pathoryx_enterprise` | Would break all imports and migrations |
| Remove `SELECT FOR UPDATE SKIP LOCKED` from trigger dequeue | Causes double-processing under concurrent workers |
| Remove `uq_trigger_per_file_stage` unique constraint | Causes duplicate QC/DICOM/upload processing |
| Remove `canonical_path` unique constraint on `file_records` | Allows duplicate slide registrations |
| Set `slide_id_generator.dry_run: false` in production | File renames execute — irreversible without manual recovery |
| Per-slide `ModelRegistry` instantiation in QC | Was original bug — caused 4× overhead per slide |
| Call `is_file_stable()` in a blocking loop | Must return immediately; callers poll on interval |
| Hardcode credentials | Always via `.env` / env vars |
| Renumber Alembic migrations | Breaks down_revision chain |
| Run `alembic downgrade` past migration 0001 | Requires manual re-grant of permissions on `events.pipeline_events` |

### Key constraints

- `validate_path_under_roots()` must be called before any filesystem operation on user-supplied or config-supplied paths.
- `EventStoreRepository.append()` is the only allowed write to `events.pipeline_events`.
- `TriggerRepository.dequeue_next()` is the only allowed way to claim work from `core.service_trigger`.
- `global_artifact_id` must be deterministic UUID5 from `deterministic_artifact_id()` — never `uuid4()` for the same artifact.
- Idempotency keys must be preserved on all result table inserts.
- `is_file_stable()` must not sleep or block.

---

## Quick Architecture Reminder

```
Watch Folder → BabelShark → QC → DICOM → Uploader → Sectra PACS
                                    ↑
                             RecoverySentry (failed/ suspicious/ manual_review/)
                                    ↑
                              Dashboard (port 8090)
```

**All services ↔ PostgreSQL (single DB `pathoryx`, 8 schemas)**

Key schema names:
- `core.*` — file_records, service_trigger (queue), pipeline_runs, step_runs
- `events.*` — pipeline_events (append-only)
- `failed_watcher.*` — RecoverySentry tables (schema name intentionally kept)
- All others named after their service: `babelshark`, `qc`, `dicomizer`, `uploader`, `audit`

---

## Startup Commands

### Linux development

```bash
cd /home/shahram/Palantir
source .venv/bin/activate   # or conda activate
export DATABASE_URL="postgresql+psycopg2://pathoryx_user:PASSWORD@localhost:5432/pathoryx"

# Run all services
pathoryx-orchestrate

# Or individual services
pathoryx-babelshark &
pathoryx-qc &
pathoryx-dicom &
pathoryx-uploader &
pathoryx-recovery-sentry &
pathoryx-dashboard &
```

### Windows

```powershell
conda activate C:\Users\Public\conda-envs\babelfish1
cd D:\Slides\Palantir

# Start all services
pathoryx-orchestrate

# Start dashboard only
.\scripts\windows_start_dashboard_backend.ps1
```

---

## One-Command Stack Launch (Recommended)

### Prerequisites

Ensure `DATABASE_URL` and other required vars are set in `.env` (copy from `.env.example` if needed).

### Start everything

```bash
# Make executable (first time only)
chmod +x run_palantir.sh run_palantir_headless.sh

# Full launch with browser (desktop/GUI sessions)
./run_palantir.sh

# Headless launch for tmux/SSH (no browser, no interactive prompts)
./run_palantir_headless.sh
```

### Stop all services

```bash
./run_palantir.sh --stop
# or
./run_palantir_headless.sh --stop
```

### Logs

All service output goes to timestamped files under `/tmp/palantir_logs/`.

```bash
tail -f /tmp/palantir_logs/backend_*.log
tail -f /tmp/palantir_logs/frontend_*.log
tail -f /tmp/palantir_logs/orchestrator_*.log
```

### Recommended tmux layout

```bash
tmux new-session -d -s palantir './run_palantir_headless.sh'
tmux split-window -h -t palantir 'tail -f /tmp/palantir_logs/backend_*.log'
tmux split-window -v -t palantir 'tail -f /tmp/palantir_logs/frontend_*.log'
tmux attach -t palantir
```

---

## Backend Launch (manual)

```bash
# Requires DATABASE_URL in environment
pathoryx-dashboard

# API available at: http://127.0.0.1:8090
# Swagger UI: http://127.0.0.1:8090/dashboard/docs
# Health: http://127.0.0.1:8090/dashboard/api/services/health
```

---

## Frontend Launch

### Development

```bash
cd dashboard-ui
npm install   # first time only
npm run dev
# → http://localhost:5173
# Proxies /dashboard/api/* to http://127.0.0.1:8090
```

### Production build

```bash
cd dashboard-ui
npm run build
# Output: dashboard-ui/dist/
# Served automatically by FastAPI at http://127.0.0.1:8090
```

### TypeScript check only

```bash
cd dashboard-ui && npx tsc --noEmit
```

---

## Required Environment Variables

| Variable | Where needed | Notes |
|----------|-------------|-------|
| `DATABASE_URL` | All services | `postgresql+psycopg2://user:pw@host:5432/pathoryx` |
| `BABELSHARK_CONFIG_PATH` | BabelShark | Default: `./configs/babelshark_config.yaml` |
| `QC_CONFIG_PATH` | QC | Default: `./configs/qc_config.yaml` |
| `QC_SERVICE_CONFIG` | QC | Default: `./configs/qc_service.yaml` |
| `DICOM_CONFIG_PATH` | DICOM | Default: `./configs/dicom_config.yaml` |
| `RECOVERY_SENTRY_CONFIG` | RecoverySentry | Default: `./configs/recovery_sentry.yaml` |
| `SCANNER_FLEET_CONFIG` | Dashboard | Default: `./configs/scanner_fleet.yaml` |
| `OPENSLIDE_DLL_PATH` | Windows only | Path to OpenSlide `bin\` directory |
| `PATHORYX_ALLOWED_INPUT_ROOTS` | Path safety | Root directories for path validation |

---

## Run Tests

```bash
# All unit tests (no DB required — all DB calls mocked)
pytest tests/unit/ -v

# Single test file
pytest tests/unit/test_recovery_sentry_engine.py -v

# Single test class
pytest tests/unit/test_phase8_review_workflow.py::TestFilenameValidationStructured -v

# With coverage
pytest tests/ --cov=pathoryx_enterprise --cov-report=term-missing

# Integration tests (requires live PostgreSQL)
pytest tests/integration/ -v
```

**Current test count:** 314 unit tests, all passing.

---

## Run Migrations

```bash
export DATABASE_URL="postgresql+psycopg2://pathoryx_user:PASSWORD@localhost:5432/pathoryx"
alembic upgrade head

# Verify
alembic current   # should show (head)
psql "$DATABASE_URL" -c "\dn"   # should list 8+ schemas
```

---

## Deployment Checklist (Before Any Production Change)

- [ ] Read `docs/current_project_state.md` for latest blockers and warnings.
- [ ] Confirm `upload.dry_run: true` in `configs/dicom_config.yaml`.
- [ ] Confirm `cstore.upload_via_c_store: false` in `configs/dicom_config.yaml`.
- [ ] Confirm `slide_id_generator.dry_run: true` in `configs/babelshark_config.yaml`.
- [ ] Confirm `pasnet_validation: false` in `configs/babelshark_config.yaml`.
- [ ] Run unit tests: `pytest tests/unit/ -v` (all 314 must pass).
- [ ] Run type check: `cd dashboard-ui && npx tsc --noEmit`.
- [ ] Check for stale migrations: `alembic current` shows `(head)`.
- [ ] Verify `.env` does not contain literal credentials (placeholder check).

---

## Current Unstable Areas

| Area | Risk | Status |
|------|------|--------|
| Uploader `_do_upload()` | Does not call storescu — upload to PACS is silently missing | ❌ Not implemented |
| BabelShark full pipeline | 6 stages wired but never run against real WSI | 🟡 Untested |
| DICOM conversion | wsidicomizer not installed; import-verified only | 🟡 Untested |
| Windows deployment | No end-to-end test on Windows host | 🟡 Untested |
| RecoverySentry atomic move + DB | If move succeeds but DB fails → manual requeue | Known risk (documented) |
| DICOM/Upload state transition | `upload_pending` set at wrong point in pipeline | 🔧 Needs fix |

---

## Key File Locations

| What | Where |
|------|-------|
| Architecture overview | `docs/master_context.md` |
| Current project state | `docs/current_project_state.md` |
| Windows setup | `docs/windows_runtime_setup.md` |
| Windows migration checklist | `docs/WINDOWS_MIGRATION_CHECKLIST.md` |
| This handoff file | `docs/ai_handoff.md` |
| Service health URL | `http://localhost:8090/dashboard/api/services/health` |
| Dashboard Swagger | `http://localhost:8090/dashboard/docs` |
| Alembic migrations | `pathoryx_enterprise/db/migrations/versions/` |
| DB models | `pathoryx_enterprise/db/models/` |
| DB repositories | `pathoryx_enterprise/db/repositories/` |
| Service configs | `configs/*.yaml` |
| Unit tests | `tests/unit/` |
| Integration tests | `tests/integration/` |
| Dashboard frontend | `dashboard-ui/src/` |
| BabelShark stages | `services/babelshark/core/` |
| DICOM engine | `services/dicom/engine/` |
| Uploader (needs storescu) | `services/uploader/runner.py` |
| RecoverySentry engine | `services/recovery_sentry/recovery_engine.py` |
| Prometheus metrics | `pathoryx_enterprise/monitoring/metrics.py` |
| Path validation | `pathoryx_enterprise/utils/path_validation.py` |
| Fingerprint / artifact ID | `pathoryx_enterprise/utils/fingerprint.py` |

---

## Recommended Next Prompts for AI Sessions

Use one of these prompts to start the next session effectively:

### To wire storescu into the Uploader:
```
Read docs/master_context.md and docs/current_project_state.md.
Then look at services/uploader/runner.py and services/uploader/config.py
and services/dicom/upload_utils.py.
Wire build_cstore_commands + run_all_cstore_batches into _do_upload().
Add Sectra host/port/AE settings to UploaderSettings.
Fix FileRecord state: uploader should set upload_pending on trigger claim.
All upload gates (dry_run=true, upload_via_c_store=false) must stay locked.
```

### To run BabelShark full pipeline smoke test:
```
Read docs/master_context.md and docs/current_project_state.md.
I want to run the BabelShark enrichment pipeline for the first time
with a real SVS file. Help me configure enable_full_pipeline: true
in a test config and verify all 6 stages complete successfully.
Check core.step_runs in the DB after the run.
```

### To set up Windows deployment:
```
Read docs/master_context.md, docs/current_project_state.md,
and docs/windows_runtime_setup.md.
I am setting up Palantir on a Windows workstation.
Walk me through the Windows Migration Checklist phases 1-7.
The PostgreSQL server is at [host]. The project is at [path].
```

### For a general architecture question:
```
Read docs/master_context.md first.
Then answer: [your question about a specific service / flow / table]
```

### To add a new dashboard page:
```
Read docs/master_context.md section 10 (Dashboard Architecture).
Read dashboard-ui/src/App.tsx and dashboard-ui/src/pages/ for existing page patterns.
I want to add a new page: [description].
```

---

## Documentation Maintenance Rules

Update the following files whenever the corresponding change occurs:

| Change | Files to update |
|--------|----------------|
| New service or service removed | `docs/master_context.md` §3, `docs/current_project_state.md` |
| New DB table or migration | `docs/master_context.md` §11, `docs/current_project_state.md` latest DB changes |
| New API endpoint | `docs/master_context.md` §10, `docs/current_project_state.md` latest API changes |
| Architecture change | `docs/master_context.md` §2, §4 |
| Phase completed | `docs/current_project_state.md` completed phases + blockers |
| New Windows config | `docs/windows_runtime_setup.md` |
| New blocker discovered | `docs/current_project_state.md` current blockers, `docs/ai_handoff.md` warnings |
| New feature flag added | `docs/master_context.md` relevant service section |
