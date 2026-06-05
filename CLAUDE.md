# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (core only — no GPU deps)
pip install -e .

# Install with QC ML models (PyTorch, OpenCV)
pip install -e ".[qc]"

# Install with dashboard backend (FastAPI, uvicorn)
pip install -e ".[dashboard]"

# Install all dev/test tools
pip install -e ".[dev]"

# Run all unit tests
pytest tests/unit/ -v

# Run a single test file
pytest tests/unit/test_recovery_sentry_engine.py -v

# Run a single test class or function
pytest tests/unit/test_phase8_review_workflow.py::TestFilenameValidationStructured -v
pytest tests/unit/test_phase8_review_workflow.py::TestFilenameValidationStructured::test_valid_complete_with_timestamp -v

# Run integration tests (requires live PostgreSQL)
pytest tests/integration/ -v

# Lint
ruff check pathoryx_enterprise/

# Type check
mypy pathoryx_enterprise/

# Apply database migrations
alembic upgrade head

# Build dashboard frontend
cd dashboard-ui && npm run build

# Start all services via orchestrator
pathoryx-orchestrate

# Start services individually
pathoryx-babelshark
pathoryx-qc
pathoryx-dicom
pathoryx-uploader
pathoryx-recovery-sentry
pathoryx-dashboard          # FastAPI on port 8090
```

## Architecture Overview

### Pipeline flow

```
Watch folder → BabelShark → QC Service → DICOM Service → Uploader → Sectra PACS
                  ↓                                         ↑
            [failed/suspicious] ← RecoverySentry ──────────┘
                                  (auto-requeue after technician fix)
```

All inter-service communication happens through **PostgreSQL** — no message broker. Services enqueue work via `core.service_trigger` and dequeue with `SELECT … FOR UPDATE SKIP LOCKED` to prevent double-processing under concurrent workers.

### Service identity map

| CLI | Port (health/metrics) | Python package |
|-----|----------------------|----------------|
| `pathoryx-babelshark` | 8081 / 9091 | `services/babelshark` |
| `pathoryx-qc` | 8082 / 9092 | `services/qc` |
| `pathoryx-dicom` | 8083 / 9093 | `services/dicom` |
| `pathoryx-uploader` | 8084 / 9094 | `services/uploader` |
| `pathoryx-recovery-sentry` | 8087 / 9097 | `services/recovery_sentry` |
| `pathoryx-dashboard` | 8090 / — | `services/dashboard` |

> `pathoryx-failed-watcher` is a deprecated stub that prints an error and exits. Use `pathoryx-recovery-sentry`.

### Database schemas

Eight PostgreSQL schemas — all in one database (`pathoryx`):

- `core.*` — `file_records` (state machine), `service_trigger` (queue), `runner_registrations` (heartbeats), `pipeline_runs`, `step_runs`, `technical_metrics`, `metadata_snapshots`
- `events.*` — `pipeline_events` (append-only; UPDATE/DELETE revoked at DB level)
- `babelshark.*` — per-stage extraction results (`datamatrix_results`, `stain_results`, `roi_results`, `color_marker_results`, `pasnet_validation_results`, `slide_routing_decisions`)
- `qc.*` — `qc_results`
- `dicomizer.*` — `conversion_results`
- `uploader.*` — `upload_results`
- `failed_watcher.*` — `watched_folder_snapshots`, `technician_changes`
- `audit.*` — `audit_log`

The `failed_watcher` schema name is intentionally kept even though the service is now called `recovery_sentry` — changing the schema name requires a data migration.

### FileRecord status machine

```
detected → intake_running → intake_registered
intake_registered → qc_pending | dicom_pending
qc_pending → qc_running → qc_passed | qc_failed
qc_passed → dicom_pending → dicom_running → dicom_done | dicom_failed
dicom_done → upload_pending → upload_running → uploaded | upload_failed
*_failed → (RecoverySentry monitoring, possible requeue)
```

### Key patterns to follow

**ServiceTrigger dequeue** — always use `TriggerRepository.dequeue_next()` which wraps `SELECT … FOR UPDATE SKIP LOCKED`. Never poll the trigger table directly.

**Idempotency** — every result table has an `idempotency_key` unique constraint. All inserts are guard-checked before inserting. `TriggerRepository.enqueue()` and `TriggerRepository.create_trigger()` both check for existing rows first.

**Event sourcing** — use `EventStoreRepository.append()` to write to `events.pipeline_events`. Never UPDATE or DELETE from that table. Each write is append-only; the app user has no UPDATE/DELETE grant.

**Immutable metadata snapshots** — use `FileRecordRepository.create_metadata_snapshot()` when metadata changes. Never overwrite a snapshot row.

**Path safety** — call `validate_path_under_roots()` from `pathoryx_enterprise.utils.path_validation` before any filesystem operation involving externally-supplied paths.

**`is_file_stable()`** — this function must not block. Callers poll it on an interval; it returns True/False immediately.

**ModelRegistry** — QC model weights are loaded once at service startup via `@cached_property`. Never revert to per-slide instantiation.

**Prometheus metrics** — all metrics are in `pathoryx_enterprise/monitoring/metrics.py` on a single shared `REGISTRY`. Import the specific metric objects you need; never instantiate new ones with the same name.

### BabelShark pipeline stages (order matters)

1. `label_extraction` — extract label/macro image from WSI
2. `color_marker_detection` — optional, feature-flagged
3. `datamatrix` — decode DataMatrix barcode
4. **`roi_fallback`** — ROI metadata for DataMatrix failures (must run **before** stain)
5. `stain_extraction` — OCR stain detection (must run **after** ROI)
6. `extra_field_extraction` — optional, feature-flagged
7. `pasnet_validation` — LIS lookup, currently disabled (`pasnet_validation: false` in config)
8. `slide_id_generation` — build SlideID, rename, route file
9. `dicom_metadata_writing` — optional, feature-flagged

Stage runner: `pathoryx_enterprise/services/babelshark/stage_runner.py`

### Removed infrastructure (Phase 3)

- **`runner_daily_enterprise.py`** — legacy batch runner at repo root; removed. `stage_runner.py` is the canonical implementation.
- **`services/failed_watcher/`** — the old `FailedWatcher` service (runner, watcher, requeue_service, config) has been removed. Only `change_detector.py` (re-export shim) and `main.py` (deprecation stub) remain.
- **`change_detector.py`** now lives at `services/recovery_sentry/change_detector.py`. The old path re-exports from the new location.
- **`pathoryx-failed-watcher` CLI** — deprecated; prints an error and exits with code 1.
- **`docker/Dockerfile.failed_watcher`** — removed; replaced by `docker/Dockerfile.recovery_sentry`.

### RecoverySentry / technician review

RecoverySentry watches `failed/`, `suspicious/`, `manual_review/` folders every 30 s. When a technician renames or adds a file:

1. Change detected → `TechnicianChange` row inserted (idempotent)
2. Wait `stable_after_seconds` (default 10)
3. Parse filename against `SLIDE_ID_RE` in `slide_id_parser.py`
4. If valid → resolve/extract timestamp → `atomic_move` to `final/<CaseID>/`
5. DB transaction: update `FileRecord`, enqueue QC trigger, emit events
6. If invalid → record `manual_review_required`; file stays in watched folder

**Dashboard rename action** (`services/dashboard/actions.py`): `execute_technician_rename()` does a safety-checked OS rename inside the watch folder, then calls the same `process_recovery()` function as the automated path. Every rename creates a `TechnicianChange` row with `inferred_action='dashboard_correction'`.

Review state machine: `detected → investigating → corrected → requeued → reviewed` (plus `dismissed` as a closeable dead-end). Transitions are enforced by `_REVIEW_TRANSITIONS` in `actions.py`. Each transition emits a `PipelineEvent`.

### SSE / real-time layer

`services/dashboard/sse.py` polls 7 cheap `MAX(pk)` / `COUNT` queries every 5 s. No extra tables, no DB triggers. Emits typed events: `queue_updated`, `pipeline_event_created`, `file_record_updated`, `recovery_event_created`, `service_health_updated`.

### Config and safety flags

All configs live in `configs/`. The file loaded at startup is controlled by env vars:

| Env var | Default path |
|---------|-------------|
| `BABELSHARK_CONFIG_PATH` | `configs/babelshark_config.yaml` |
| `QC_CONFIG_PATH` | `configs/qc_config.yaml` |
| `DICOM_CONFIG_PATH` | `configs/dicom_config.yaml` |
| `RECOVERY_SENTRY_CONFIG` | `configs/recovery_sentry.yaml` |

**Do not touch** `configs/dicom_config_production.yaml` — that is the live production reference with `upload.dry_run=false` and `cstore.upload_via_c_store=true`. Tests and development always use `configs/dicom_config.yaml`.

### Running tests without a database

All unit tests (`tests/unit/`) mock the DB session and repositories. Integration tests (`tests/integration/`) require a live PostgreSQL instance. The `conftest.py` provides fixtures; check it before adding new fixtures.

The test pattern for dashboard endpoints:

```python
from pathoryx_enterprise.services.dashboard.app import create_app, get_db
app = create_app()
app.dependency_overrides[get_db] = lambda: mock_db
client = TestClient(app)
```

For recovery engine tests, patch `get_session`, `FileRecordRepository`, `TriggerRepository`, and `EventStoreRepository` directly on the `recovery_engine` module (not the repository module) since they are imported at call time via `from … import`.

### Naming conventions

- DB schema `failed_watcher` ↔ service `recovery_sentry` — intentionally divergent; do not unify
- `global_artifact_id` — deterministic UUID5 generated by `deterministic_artifact_id()` in `utils/fingerprint.py`; never randomly generated for the same logical artifact
- SlideID format: `N{10digits}{POT}-{BLOCK}-{SECTION}-{STAIN}[_UTC{ts}].{ext}` (e.g. `N2024002863SA-1-1-H&E_UTC2024-08-22T08_36_39Z.svs`)
- Timestamp in filenames uses underscores for colons: `UTC2024-08-22T08_36_39Z`; ISO-Z form in DB: `2024-08-22T08:36:39Z`
