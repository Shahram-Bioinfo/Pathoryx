# Palantir — Windows Operations Runbook

Day-to-day operational reference for running Palantir on Windows.
For initial setup see `WINDOWS_SETUP.md`. For a migration checklist see
`docs/WINDOWS_MIGRATION_CHECKLIST.md`.

---

## Environment assumptions

| Item | Value |
|---|---|
| Project root | `C:\Users\Public\projects\Palantir` |
| Conda env | `C:\Users\Public\conda-envs\babelfish1` |
| Python | 3.12.x |
| PostgreSQL | localhost:5432, database `pathoryx` |
| Dashboard backend | http://127.0.0.1:8090 |
| Dashboard frontend (dev) | http://localhost:5173 |

All commands assume you are in a PowerShell terminal with the conda env active:

```powershell
conda activate C:\Users\Public\conda-envs\babelfish1
cd C:\Users\Public\projects\Palantir
```

---

## Starting services

### Option A — orchestrator (all services together)

```powershell
.\scripts\windows_start_orchestrator.ps1
```

Press `Ctrl+C` to stop all services cleanly.

### Option B — individual services (separate terminals)

Open a new PowerShell window for each:

```powershell
# Terminal 1 — BabelShark (slide intake)
conda activate C:\Users\Public\conda-envs\babelfish1
cd C:\Users\Public\projects\Palantir
pathoryx-babelshark
```

```powershell
# Terminal 2 — QC inference
conda activate C:\Users\Public\conda-envs\babelfish1
cd C:\Users\Public\projects\Palantir
pathoryx-qc
```

```powershell
# Terminal 3 — DICOM conversion
conda activate C:\Users\Public\conda-envs\babelfish1
cd C:\Users\Public\projects\Palantir
pathoryx-dicom
```

```powershell
# Terminal 4 — Uploader
conda activate C:\Users\Public\conda-envs\babelfish1
cd C:\Users\Public\projects\Palantir
pathoryx-uploader
```

```powershell
# Terminal 5 — RecoverySentry
conda activate C:\Users\Public\conda-envs\babelfish1
cd C:\Users\Public\projects\Palantir
pathoryx-recovery-sentry
```

```powershell
# Terminal 6 — Dashboard backend
.\scripts\windows_start_dashboard_backend.ps1
```

```powershell
# Terminal 7 — Dashboard frontend (dev)
.\scripts\windows_start_dashboard_frontend.ps1
```

---

## Running a smoke test

```powershell
.\scripts\windows_smoke_test.ps1
```

Checks: database connection, alembic head, config files, data folders,
model weights, scanner fleet, upload_tracking table, dry-run safety.

---

## Database operations

### Connect to database

```powershell
psql "postgresql://pathoryx_user:PASSWORD@localhost:5432/pathoryx"
```

### Run migrations

```powershell
.\scripts\windows_run_migrations.ps1
```

Or directly:

```powershell
alembic upgrade head
```

### Check migration status

```powershell
alembic current
alembic history --verbose
```

### Verify schema

```sql
\dn
\dt core.*
\dt upload_tracking.*
```

### Useful diagnostic queries

```sql
-- Recent pipeline activity
SELECT global_artifact_id, status, current_file_path, updated_at
FROM core.file_records
ORDER BY updated_at DESC LIMIT 20;

-- Trigger queue depth
SELECT target_service, stage_name, trigger_status, COUNT(*)
FROM core.service_trigger
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

-- Upload queue
SELECT scanner_id, upload_status, COUNT(*), MAX(last_updated_at)
FROM upload_tracking.estimated_upload_queue
GROUP BY 1, 2
ORDER BY 1, 2;

-- Failed triggers
SELECT target_service, stage_name, error_message, updated_at
FROM core.service_trigger
WHERE trigger_status = 'failed'
ORDER BY updated_at DESC LIMIT 20;
```

---

## Uploading test slides

Drop any `.svs`, `.ndpi`, `.tiff`, or other supported WSI file into:

```
C:\Users\Public\projects\Palantir\data\watch\
```

BabelShark polls every 1 minute (`watch_interval_minutes: 1` in config).

---

## Enabling real DICOM upload

> **WARNING:** Never use real upload with patient-sensitive slides unless
> explicitly approved by your data governance officer.

1. Verify dry-run pipeline works end-to-end first.
2. Check PACS connectivity:
   ```powershell
   ping path-pacs2
   Test-NetConnection path-pacs2 -Port 32001
   ```
3. Edit `configs\dicom_config.yaml` (or the windows variant):
   ```yaml
   upload:
     dry_run: false         # was: true
   cstore:
     upload_via_c_store: true   # was: false
     peer_ip: "path-pacs2"      # confirm with PACS admin
   ```
4. Restart the DICOM service.
5. Monitor via dashboard Upload Results panel.

---

## Updating config files

After editing any config YAML, restart only the affected service
(not the full orchestrator) to pick up the change:

| Config changed | Service to restart |
|---|---|
| `babelshark_config.yaml` | `pathoryx-babelshark` |
| `qc_config.yaml` | `pathoryx-qc` |
| `dicom_config.yaml` | `pathoryx-dicom` or `pathoryx-uploader` |
| `recovery_sentry.yaml` | `pathoryx-recovery-sentry` |
| `scanner_fleet.yaml` | `pathoryx-dashboard` |

---

## Updating the codebase

```powershell
cd C:\Users\Public\projects\Palantir
git pull origin main
pip install -e .
alembic upgrade head
```

Restart all services after a pull that includes migration files.

---

## Log locations

Logs are written to stdout/stderr by default. To capture to file:

```powershell
pathoryx-dashboard 2>&1 | Tee-Object -FilePath logs\dashboard.log
```

For structured log viewing (JSON format), pipe through `jq` if installed:

```powershell
pathoryx-dashboard 2>&1 | jq .
```

---

## Checking service health

```powershell
# HTTP health endpoint (if HEALTH_HTTP_ENABLED=true in .env)
Invoke-WebRequest http://localhost:8080/health | Select-Object -ExpandProperty Content

# Dashboard API status
Invoke-WebRequest http://127.0.0.1:8090/dashboard/api/status | Select-Object -ExpandProperty Content
```

---

## Common issues

### Service starts but immediately exits

Check that `.env` is loaded and `DATABASE_URL` is correct:
```powershell
$env:DATABASE_URL = "postgresql+psycopg2://pathoryx_user:PASSWORD@localhost:5432/pathoryx"
alembic current   # should print a revision hash with (head)
```

### Port 8090 already in use

```powershell
netstat -ano | findstr :8090
taskkill /PID <PID> /F
```
Or change the port in `.env`: `PATHORYX_DASHBOARD_PORT=8091`

### OpenSlide not found

```powershell
# Add OpenSlide bin to PATH for this session
$env:PATH += ";C:\OpenSlide\bin"
# Verify
python -c "import openslide; print(openslide.__version__)"
```

Add permanently via **System Properties → Environment Variables → Path**.

### psycopg2 install fails

```powershell
pip install psycopg2-binary
```

### `alembic upgrade head` fails — no module named pathoryx_enterprise

```powershell
pip install -e .
```

### DICOM service crashes on first slide

Verify `wsidicomizer` is installed and on PATH:
```powershell
wsidicomizer --version
```
If missing, install per the wsidicomizer project documentation.
