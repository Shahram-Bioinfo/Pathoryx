# Troubleshooting Guide

Known errors, root causes, and verified fixes.

---

## Installation errors

### A. `pip install -e .` fails with BackendUnavailable

**Error:**
```
BackendUnavailable: Cannot import 'setuptools.backends.legacy'
```

**Root cause:** Old setuptools version or incorrect `build-backend` in `pyproject.toml`.

**Fix:**
```bash
pip install --upgrade pip setuptools wheel
pip install -e .
```

Verify `pyproject.toml` contains:
```toml
[build-system]
requires = ["setuptools>=70", "wheel"]
build-backend = "setuptools.build_meta"
```

---

## Database errors

### B. Password authentication failed

**Error:**
```
FATAL: password authentication failed for user "pathoryx_user"
```

**Fix:**
```bash
sudo -u postgres psql -c "ALTER USER pathoryx_user WITH PASSWORD 'YOUR_NEW_PASSWORD';"
```

Then update `DATABASE_URL` in `.env` to match.

---

### C. Database does not exist

**Error:**
```
FATAL: database "pathoryx_enterprise" does not exist
```

**Fix:**
```bash
sudo -u postgres createdb -O pathoryx_user pathoryx_enterprise
```

---

### D. Permission denied to create database

**Error:**
```
ERROR: permission denied to create database
```

**Root cause:** Running `createdb` as `pathoryx_user`, which lacks CREATEDB privilege.

**Fix:** Create the database as the `postgres` superuser:
```bash
sudo -u postgres createdb -O pathoryx_user pathoryx_enterprise
```

---

### E. Permission denied to create extension

**Error:**
```
ERROR: permission denied to create extension "pg_stat_statements"
```

**Root cause:** Extensions require superuser. The app user cannot create them.

**Fix:** Create extensions as superuser:
```bash
sudo -u postgres psql pathoryx_enterprise -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
sudo -u postgres psql pathoryx_enterprise -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
```

---

### F. psql connects as wrong user

**Error:**
```
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed:
FATAL: role "shahram" does not exist
```

**Root cause:** `psql` without arguments uses the OS username as the PostgreSQL role.

**Fix:** Always pass an explicit connection URL:
```bash
psql "postgresql://pathoryx_user:PASSWORD@localhost:5432/pathoryx_enterprise"
```

---

### G. Alembic cannot find DATABASE_URL

**Error:**
```
KeyError: 'DATABASE_URL' or
sqlalchemy.exc.ArgumentError: Could not parse rfc1738 URL from string ''
```

**Fix:**
```bash
# Option 1: source .env first
export $(grep -v '^#' .env | xargs)
alembic upgrade head

# Option 2: pass inline
DATABASE_URL="postgresql://pathoryx_user:PASSWORD@localhost:5432/pathoryx_enterprise" alembic upgrade head
```

---

## Service startup errors

### H. Orchestrator uses wrong command name

**Error:**
```
failed to start service: [Errno 2] No such file or directory: 'pathoryx-upload'
```

**Root cause:** `orchestrator/main.py` used `pathoryx-upload` but `pyproject.toml` defines `pathoryx-uploader`.

**Fix:** Already corrected in this codebase. If you see this error, reinstall:
```bash
pip install -e .
```

Verify the scripts are installed:
```bash
which pathoryx-uploader   # should print a path
```

---

### I. `cannot import name 'inject_context'`

**Error:**
```
ImportError: cannot import name 'inject_context' from 'pathoryx_enterprise.logging.setup'
```

**Root cause:** `inject_context` was missing from `logging/setup.py`. Fixed in this codebase.

**Fix:** Reinstall:
```bash
pip install -e .
```

---

### J. `configure_logging() got an unexpected keyword argument 'json_output'`

**Error:**
```
TypeError: configure_logging() got an unexpected keyword argument 'json_output'
```

**Root cause:** The original `configure_logging` had a `json_format` parameter but all service runners called it with `json_output=`. Fixed.

**Fix:** Reinstall:
```bash
pip install -e .
```

---

### K. Placeholder password rejected at startup

**Error:**
```
ValidationError: DATABASE_URL contains placeholder password 'strongpassword'
```

**Root cause:** Pydantic BaseSettings validator rejects known placeholder values.

**Fix:** Edit `.env` and replace the placeholder with a real password.

---

### L. Path traversal error

**Error:**
```
pathoryx_enterprise.utils.path_validation.PathTraversalError:
  Path '/etc/passwd' is not under any allowed root
```

**Root cause:** The file path being processed is outside `PATHORYX_ALLOWED_INPUT_ROOTS`.

**Fix:**
1. Add the correct root to `.env`:
   ```
   PATHORYX_ALLOWED_INPUT_ROOTS=/data/slides,/mnt/nfs/incoming
   ```
2. Restart the affected service.

---

## Runtime errors

### M. QC service runs out of GPU memory

**Error:**
```
RuntimeError: CUDA out of memory.
```

**Root cause:** Multiple QC runner threads loading the model, or very large slide tiles.

**Fix:**
1. Ensure `ModelRegistry` is instantiated only once at startup (already fixed in this codebase).
2. Reduce `QC_NUM_WORKERS` in `.env` (default: 4).
3. Reduce tile size in the QC YAML config.

---

### N. storescu fails with ARG_MAX error

**Error:**
```
bash: /usr/bin/storescu: Argument list too long
```

**Root cause:** DICOM series with thousands of files sent as a single `storescu` invocation.

**Fix:** Reduce `SECTRA_CSTORE_BATCH_SIZE` (default 500, try 200):
```
SECTRA_CSTORE_BATCH_SIZE=200
```

Already batched in this codebase — if still occurring, reduce the batch size further.

---

### O. Circuit breaker open: uploads paused

**Symptom:**
```
{"event": "circuit breaker OPEN — pausing upload", "service": "uploader", ...}
```

**Root cause:** 5 consecutive PACS upload failures. The circuit opens to prevent hammering the PACS.

**Fix:**
1. Verify PACS is reachable: `nc -vz $SECTRA_HOST $SECTRA_PORT`
2. Check PACS logs for rejection reason.
3. Once PACS is healthy, the circuit auto-resets after `UPLOADER_CIRCUIT_RESET_SECONDS` (default 60s).
4. For immediate reset: restart the uploader.

---

### P. Triggers stuck in `running` after service crash

**Symptom:**
```sql
SELECT count(*) FROM core.service_trigger
WHERE trigger_status = 'running'
  AND started_at < now() - INTERVAL '30 minutes';
-- Returns > 0
```

**Root cause:** Service was killed (SIGKILL) without cleaning up its in-flight triggers.

**Fix:** The failed watcher auto-detects this after `FAILED_WATCHER_CRASH_THRESHOLD_SECONDS` (default 120s). For immediate recovery:

```sql
UPDATE core.service_trigger
SET trigger_status = 'pending',
    started_at = NULL,
    claimed_by_runner_id = NULL,
    claimed_by_host_id = NULL
WHERE trigger_status = 'running'
  AND started_at < now() - INTERVAL '30 minutes';
```

---

### Q. Connection pool exhausted

**Error:**
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached
```

**Root cause:** More concurrent slide processing threads than pool allows.

**Fix:**
```
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
```

Also check for connection leaks (sessions not closed):
```sql
SELECT count(*), state FROM pg_stat_activity
WHERE datname = 'pathoryx_enterprise'
GROUP BY state;
```

---

---

## Runtime errors (2nd session)

### R. `cannot import name 'QcResult'` / `'DicomizerResult'` / `'UploaderResult'`

**Error:**
```
ImportError: cannot import name 'QcResult' from 'pathoryx_enterprise.db.models.qc'
ImportError: cannot import name 'DicomizerResult' from 'pathoryx_enterprise.db.models.dicomizer'
ImportError: cannot import name 'UploaderResult' from 'pathoryx_enterprise.db.models.uploader'
```

**Root cause:** The service db_writer files used wrong class names. The actual class names in the models are:
- `QCResult` (capital QC, not `QcResult`)
- `ConversionResult` (not `DicomizerResult`)
- `UploadResult` (not `UploaderResult`)

**Fix:** Already corrected in all three db_writer files. If you see this error, reinstall:
```bash
pip install -e .
```

---

### S. `BABELSHARK_CONFIG` Field required

**Error:**
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for BabelSharkSettings
BABELSHARK_CONFIG
  Field required
```

**Root cause:** `.env` uses `BABELSHARK_CONFIG_PATH` (matching `.env.example`), but the Pydantic settings only accepted `BABELSHARK_CONFIG`.

**Fix:** Already corrected. Settings now accept both env var names:
- `BABELSHARK_CONFIG_PATH` (preferred, matches `.env.example`)
- `BABELSHARK_CONFIG` (legacy alias)

Same fix applied for `QC_CONFIG_PATH`/`QC_SERVICE_CONFIG` and `DICOM_CONFIG_PATH`/`DICOM_CONFIG`.

---

### T. `AttributeError: 'PrintLogger' object has no attribute 'name'`

**Error:**
```
AttributeError: 'PrintLogger' object has no attribute 'name'
```

**Where:** During `structlog.stdlib.add_logger_name` processor in `configure_logging()`.

**Root cause:** The original `configure_logging()` used `structlog.PrintLoggerFactory(sys.stdout)` which creates `PrintLogger` objects. These do not have a `.name` attribute. `add_logger_name` requires a stdlib `logging.Logger` (which has `.name`).

**Fix:** Changed logger factory to `structlog.stdlib.LoggerFactory()`. This creates stdlib loggers that have `.name` and also route through the stdlib root logger (which outputs `%(message)s` — the pre-rendered structlog string). Both JSON and console dev modes work correctly.

---

### U. Column name mismatches in db_writer instantiation

**Error (at first write attempt after fixing imports):**
```
TypeError: QCResult.__init__() got an unexpected keyword argument 'qc_status'
TypeError: ConversionResult.__init__() got an unexpected keyword argument 'metadata_summary_json'
TypeError: UploadResult.__init__() got an unexpected keyword argument 'destination_path'
```

**Root cause:** db_writer files used column kwarg names that didn't match the ORM model Python attribute names:
- `qc_status` → model has `qc_result`
- `stain_json`/`blur_json`/etc → model has `stain_metrics`/`blur_metrics`/etc
- `metadata_summary_json` → model has `metadata_summary`
- `destination_path` → model has `target_endpoint`
- `trigger_internal_id`, `runner_id`, `host_id`, `service_version`, `processed_at` → not in original model

**Fix:**
1. db_writer kwarg names corrected to match model attribute names
2. Missing operational columns (`trigger_internal_id`, `runner_id`, `host_id`, `service_version`, `processed_at`) added to models via migration 0002

Run after upgrade to apply:
```bash
alembic upgrade head
# Should report: Running upgrade 0001 -> 0002
```

---

### V. `get_or_create_safe() got an unexpected keyword argument 'canonical_path'`

**Error:**
```
TypeError: FileRecordRepository.get_or_create_safe() got an unexpected keyword argument 'canonical_path'
```

**Where:** During `register_collected_file()` in `database_manager.py`, after BabelShark processes a slide.

**Root cause:** `database_manager.py` called `get_or_create_safe()` with a Django-style `get_or_create` API
(`canonical_path=..., defaults=dict(...)`), but the repository method expected a `FastFingerprint`
object as its first positional argument plus individual keyword args — an incompatible calling convention.

**Fix:** `get_or_create_safe()` in `pathoryx_enterprise/db/repositories/file_record.py` was updated to
accept `canonical_path: str` as the lookup key and `defaults: dict | None` for creation fields.
No changes to BabelShark business logic or `database_manager.py` were needed.

Reinstall to pick up the change:
```bash
pip install -e .
```

---

## BabelShark routing errors

### W. Failed slides marked qc_pending / QC fails with "Unsupported or missing image file"

**Symptom:**
```
# DB shows:
status = 'qc_pending'
current_file_path = '/home/.../staging/54564564.svs'   ← file deleted from here
# Actual file location:
/home/.../failed/2026-06-01/54564564.svs
# QC service error:
"Unsupported or missing image file: /home/.../staging/54564564.svs"
```

**Root cause (two-part):**

1. `_status_for_final_route()` in `slide_id_generator.py` mapped failed routing rows
   to invalid status strings (`"FINAL_ROUTE_FAILED"`, `"ROUTED_RESEARCH_ORIGINAL"`, etc.)
   that violated the `ck_file_records_status` check constraint.  Each write rolled
   back silently via SAVEPOINT, leaving `current_file_path` pointing to the staging
   path (which the file had just been moved away from).

2. `run_enrichment_pipeline()` in `stage_runner.py` unconditionally dispatched a QC
   trigger via `mark_intake_complete(next_stage="qc")` after all stages — including
   when `slide_id_generation` routed the file to `failed/`, `failed_datamatrix/`, or
   `unreadable/`.  This set `status=qc_pending` and enqueued a QC ServiceTrigger
   pointing to the now-deleted staging path.

**Fix (applied in migration 0006 + code patch):**

- Migration `0006_babelshark_failed_status` adds `babelshark_failed` and
  `intake_failed` to the status check constraint.
- `_status_for_final_route` now maps:
  - `success`, `research_success`, `dicom_renamed` → `qc_pending`
  - `failed`, `nan`, `error` → `babelshark_failed`
  - anything else → `manual_review`
- `run_slide_id_generation()` now returns `(slide_id, routing_status, routing_output_path)`.
- `run_enrichment_pipeline()` checks `routing_status` before dispatch:
  - failure route → `mark_babelshark_failed()` (sets `babelshark_failed`, updates path, emits `babelshark.failed_routed`, **no** ServiceTrigger)
  - success route → `mark_intake_complete()` as before (sets `qc_pending`, dispatches QC trigger)
- `BabelSharkDBWriter.mark_babelshark_failed()` is the new method that handles the failure path.

**Apply the migration:**
```bash
alembic upgrade head
# Expected: Running upgrade 0005 -> 0006
```

**Verify fix for an affected slide:**
```sql
-- Before fix: status='qc_pending', path points to staging (file gone)
-- After fix: status='babelshark_failed', current_file_path = actual failed path
SELECT status, current_file_path, canonical_path
FROM core.file_records
WHERE original_filename = '54564564.svs';

-- Also check the event log:
SELECT event_type, event_payload
FROM events.pipeline_events
WHERE event_type = 'babelshark.failed_routed'
ORDER BY created_at DESC LIMIT 10;
```

---

## Diagnostic commands

```bash
# Test DB connectivity (SQLAlchemy 2.0 style)
python3 -c "
from sqlalchemy import text
from pathoryx_enterprise.db.engine import get_shared_engine
with get_shared_engine().connect() as c:
    print(c.execute(text('SELECT version()')).scalar())
"

# Test logging (JSON and dev mode, no errors expected)
python3 -c "
from pathoryx_enterprise.logging.setup import configure_logging, inject_context, get_logger
configure_logging(service_name='test', json_output=True)
get_logger('test').info('json test', ok=True)
configure_logging(service_name='test', json_output=False)
get_logger('test').info('dev test', ok=True)
print('OK')
"

# Test all critical model imports
python3 -c "
from pathoryx_enterprise.db.models.qc import QCResult
from pathoryx_enterprise.db.models.dicomizer import ConversionResult
from pathoryx_enterprise.db.models.uploader import UploadResult
from pathoryx_enterprise.logging.setup import configure_logging, inject_context, get_logger
from pathoryx_enterprise.monitoring.startup import StartupValidator
print('All imports OK')
"

# Run startup validator
python3 -c "
import os
for line in open('.env'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())
from pathoryx_enterprise.monitoring.startup import StartupValidator
StartupValidator(service_name='test', required_env_vars=['DATABASE_URL']).run()
print('Startup validation passed')
"

# Check installed entry points
pip show pathoryx-enterprise | grep -i location
ls \$(pip show pathoryx-enterprise | grep Location | awk '{print \$2}')/../../../bin/pathoryx-*

# Verify alembic migration
alembic current
alembic history --verbose
```
