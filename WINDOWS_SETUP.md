# Palantir — Windows Test Environment Setup

Step-by-step guide to run the full Palantir pipeline on Windows using the
`C:\Users\Public\conda-envs\babelfish1` Conda environment.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Conda | With environment at `C:\Users\Public\conda-envs\babelfish1` |
| PostgreSQL | Installed locally on Windows |
| Node.js 18+ | For the dashboard frontend |
| Git | For cloning the repository |
| OpenSlide (Windows) | Download from https://openslide.org/download/ — add `bin\` to `PATH` |

---

## 1. Open PowerShell as Administrator

Right-click **Start → Windows PowerShell (Admin)**.

---

## 2. Activate the Conda environment

```powershell
conda activate C:\Users\Public\conda-envs\babelfish1
```

Verify:

```powershell
python --version   # should be 3.12.x
```

---

## 3. Clone the repository

```powershell
cd C:\Users\Public\projects
git clone git@github.com:Shahram-Bioinfo/Palantir.git
cd Palantir
```

If the folder already exists:

```powershell
cd C:\Users\Public\projects\Palantir
git pull origin main
```

---

## 4. Install the project

```powershell
pip install -e .
pip install -e ".[dashboard]"
pip install -e ".[qc]"
pip install -e ".[dev]"
```

Verify entry points are registered:

```powershell
pathoryx-orchestrate --help
pathoryx-babelshark --help
pathoryx-dashboard --help
```

---

## 5. Create PostgreSQL database and user

Open **psql** (or pgAdmin) and run:

```sql
CREATE USER pathoryx_user WITH PASSWORD 'your_strong_password_here';
CREATE DATABASE pathoryx OWNER pathoryx_user;
GRANT ALL PRIVILEGES ON DATABASE pathoryx TO pathoryx_user;
```

Test the connection:

```powershell
psql "postgresql://pathoryx_user:your_strong_password_here@localhost:5432/pathoryx"
```

---

## 6. Configure environment variables

```powershell
copy .env.example .env
notepad .env
```

At minimum, fill in:

```dotenv
DATABASE_URL=postgresql+psycopg2://pathoryx_user:your_strong_password_here@localhost:5432/pathoryx
```

All other values are optional for initial testing (PASNET, LIS, Nexus can stay as `CHANGE_ME`).

---

## 7. Run database migrations

```powershell
alembic upgrade head
```

Verify the schema was created:

```sql
\dn   -- should show schemas: core, babelshark, qc, dicomizer, uploader, failed_watcher
\dt core.*
```

---

## 8. Create runtime data folders

The `data\` directory is pre-populated with `.gitkeep` placeholder files.
The folders are already present after cloning — no extra action needed.

To verify:

```powershell
dir C:\Users\Public\projects\Palantir\data\
```

Expected folders: `watch`, `scanner_fake`, `staging`, `final`, `failed`, `suspicious`,
`manual_review`, `dicom_output`, `run_output`, `labels`, `label_crops`, `roi_debug`,
`roi_debug_parts`, `logs`, `upload_test`, `caseid_test`, `qc_output`, `quarantine`

---

## 9. Start the full pipeline (orchestrator)

The orchestrator starts all services in the correct order:

```powershell
pathoryx-orchestrate
```

Or start services individually in separate PowerShell windows:

```powershell
# Terminal 1 — BabelShark intake
pathoryx-babelshark

# Terminal 2 — QC inference
pathoryx-qc

# Terminal 3 — DICOM conversion
pathoryx-dicom

# Terminal 4 — Upload service
pathoryx-uploader

# Terminal 5 — RecoverySentry (watches failed/suspicious/manual_review)
pathoryx-recovery-sentry

# Terminal 6 — Dashboard backend
pathoryx-dashboard
```

---

## 10. Start the dashboard frontend

```powershell
cd C:\Users\Public\projects\Palantir\dashboard-ui
npm install
npm run dev
```

Open in browser: http://localhost:5173

Or use the built version (served by the FastAPI backend): http://127.0.0.1:8090

---

## 11. Drop test slides into the watch folder

Copy any `.svs`, `.ndpi`, `.tiff`, or other supported WSI file into:

```
C:\Users\Public\projects\Palantir\data\watch\
```

BabelShark will detect and process it within 1 minute (configured in `watch_interval_minutes`).

---

## 12. Monitor the pipeline

### Dashboard

Open http://127.0.0.1:8090 — the Operations Dashboard shows:
- Overview: slide status counts, active services
- Slide Explorer: per-slide pipeline status
- Queue Monitor: trigger queue depths
- Failure Center: failed slides and triggers
- Recovery Center: technician review workflow

### Database checks

```powershell
psql "postgresql://pathoryx_user:CHANGE_ME@localhost:5432/pathoryx"
```

**Check processed slides:**
```sql
SELECT global_artifact_id, status, current_file_path, updated_at
FROM core.file_records
ORDER BY updated_at DESC
LIMIT 20;
```

**Check trigger queue:**
```sql
SELECT target_service, stage_name, trigger_status, error_message, updated_at
FROM core.service_trigger
ORDER BY updated_at DESC
LIMIT 30;
```

**Check upload results:**
```sql
SELECT *
FROM uploader.upload_results
ORDER BY created_at DESC
LIMIT 10;
```

---

## Activating real Sectra C-STORE upload

> **WARNING:** Never test real C-STORE upload with patient-sensitive slides
> unless explicitly approved by your data governance officer.

### Step 1 — Verify dry-run first

Run through the full pipeline with `upload.dry_run: true` (the default).
Confirm slides appear in `data\dicom_output\` and the database shows `upload_status: dry_run_ok`.

### Step 2 — Verify network connectivity to PACS

```powershell
ping path-pacs2
Test-NetConnection path-pacs2 -Port 32001
```

Both must succeed before enabling real upload.

### Step 3 — Enable real upload

Edit `configs\dicom_config.yaml`:

```yaml
upload:
  dry_run: false           # was: true

cstore:
  upload_via_c_store: true  # was: false
  peer_ip: "path-pacs2"     # site-specific — confirm with your PACS admin
  default_peer_port: "32001"
  sec_dcm_bin: "C:\\Program Files\\Sectra\\ImageTools\\bin"
```

Restart the DICOM service:

```powershell
pathoryx-dicom
```

Monitor the first real upload via the dashboard Upload Results panel.

---

## Credentials that still need manual configuration

| Credential | Where | When needed |
|---|---|---|
| `DATABASE_URL` password | `.env` | Required — PostgreSQL user password |
| `PASNET_SERVER`, `PASNET_USERNAME`, `PASNET_PASSWORD` | `.env` | Only if PASNET validation is enabled |
| `LIS_SQL_SERVER`, `LIS_SQL_USERNAME`, `LIS_SQL_PASSWORD` | `.env` | Only if LIS enrichment is enabled in `dicom_config.yaml` |
| `NEXUS_USERNAME`, `NEXUS_PASSWORD` | `.env` | Only if pulling models from Nexus registry |
| `cstore.peer_ip` | `configs/dicom_config.yaml` | Only when enabling real C-STORE upload |
| `cstore.sec_dcm_bin` | `configs/dicom_config.yaml` | Only when enabling real C-STORE upload |

---

## Common issues on Windows

### `openslide` not found

Add the OpenSlide `bin\` directory to your `PATH`:
```powershell
$env:PATH += ";C:\OpenSlide\bin"
```
Or add it permanently via **System Properties → Environment Variables**.

### `psycopg2` install fails

Use the binary wheel:
```powershell
pip install psycopg2-binary
```

### Port 8090 already in use

Change the dashboard port in `.env`:
```dotenv
PATHORYX_DASHBOARD_PORT=8091
```

### `alembic upgrade head` fails — `DATABASE_URL not set`

Ensure `.env` is in the project root and has been filled in.
Alternatively, set the variable directly:
```powershell
$env:DATABASE_URL = "postgresql+psycopg2://pathoryx_user:password@localhost:5432/pathoryx"
alembic upgrade head
```
