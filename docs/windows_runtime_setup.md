# Palantir — Windows Runtime Setup Guide

> **Last updated:** 2026-06-10
> **Audience:** Engineers deploying Palantir on a Windows workstation or Windows Server.
> **Full checklist:** `docs/WINDOWS_MIGRATION_CHECKLIST.md`
> **Day-to-day operations:** `WINDOWS_RUNBOOK.md`

---

## Prerequisites

| Requirement | Minimum | How to verify |
|-------------|---------|--------------|
| Windows | 10/11 or Server 2019+ | `winver` |
| PowerShell | 5.1+ | `$PSVersionTable.PSVersion` |
| Git | Any recent | `git --version` |
| Conda | Any recent | `conda --version` |
| PostgreSQL | 15+ | `Get-Service postgresql*` |
| Node.js | 18+ | `node --version` |
| OpenSlide | 4.x (Windows build) | `python -c "import openslide"` after DLL setup |

---

## 1. Conda Environment

### Create the environment

```powershell
conda create --prefix C:\Users\Public\conda-envs\babelfish1 python=3.12 -y
conda activate C:\Users\Public\conda-envs\babelfish1
```

### Install Palantir

```powershell
cd D:\Slides\Palantir   # or C:\Users\Public\projects\Palantir
pip install -e ".[babelshark,dashboard,qc,ocr,datamatrix,dicom,dev]"
```

**Extras reference:**

| Extra | Includes | Key packages |
|-------|----------|-------------|
| `babelshark` | pathoryx-babelshark | openslide-python, pydicom, pandas, reportlab, tqdm |
| `qc` | pathoryx-qc | torch, openslide-python, numpy, opencv |
| `dashboard` | pathoryx-dashboard | fastapi, uvicorn |
| `dicom` | pathoryx-dicom | pydicom |
| `ocr` | BabelShark stain OCR | easyocr |
| `datamatrix` | BabelShark barcode | pylibdmtx |
| `windows` | all | pywin32 |
| `dev` | testing | pytest, ruff, mypy |

### Verify entry points

```powershell
pathoryx-dashboard --help
pathoryx-qc --help
pathoryx-babelshark --help
pathoryx-orchestrate --help
```

---

## 2. OpenSlide Setup

OpenSlide reads WSI files (`.svs`, `.ndpi`, etc.). On Windows it requires native DLLs.

### Download

Get the latest prebuilt Windows binaries from:
```
https://github.com/openslide/openslide-winbuild/releases
```
Choose `openslide-bin-X.X.X.X-windows-x64.zip`.
Recommended extract location: `D:\tools\openslide-bin-4.0.0.8-windows-x64\`

### Set OPENSLIDE_DLL_PATH

**Recommended — conda activate.d (auto-loads on `conda activate`):**

Create `C:\Users\Public\conda-envs\babelfish1\etc\conda\activate.d\palantir_env.bat`:

```bat
@echo off
set "OPENSLIDE_DLL_PATH=D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
set "DATABASE_URL=postgresql+psycopg2://pathoryx_user:PASSWORD@vm-host:5432/pathoryx"
set "BABELSHARK_CONFIG_PATH=D:\Slides\Palantir\configs\babelshark_config.windows.yaml"
set "QC_CONFIG_PATH=D:\Slides\Palantir\configs\qc_config.windows.yaml"
set "DICOM_CONFIG_PATH=D:\Slides\Palantir\configs\dicom_config.windows.yaml"
set "RECOVERY_SENTRY_CONFIG=D:\Slides\Palantir\configs\recovery_sentry.windows.yaml"
set "SCANNER_FLEET_CONFIG=D:\Slides\Palantir\configs\scanner_fleet.yaml"
```

Or PowerShell variant `palantir_env.ps1` in the same directory:

```powershell
$env:OPENSLIDE_DLL_PATH = "D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
$env:DATABASE_URL = "postgresql+psycopg2://pathoryx_user:PASSWORD@vm-host:5432/pathoryx"
```

**Alternative — Machine-level env var (permanent):**

```powershell
[System.Environment]::SetEnvironmentVariable(
    "OPENSLIDE_DLL_PATH",
    "D:\tools\openslide-bin-4.0.0.8-windows-x64\bin",
    "Machine"
)
```

**Alternative — `.env` file:**

```dotenv
OPENSLIDE_DLL_PATH=D:/tools/openslide-bin-4.0.0.8-windows-x64/bin
```

> **IMPORTANT:** Set `OPENSLIDE_DLL_PATH` to the `bin\` subdirectory, not the top-level extraction directory.
> Correct: `D:\tools\openslide-bin-4.0.0.8-windows-x64\bin`
> Wrong: `D:\tools\openslide-bin-4.0.0.8-windows-x64`

### Validate

```powershell
python -c "
import os, sys
path = os.environ.get('OPENSLIDE_DLL_PATH', '')
if path: os.add_dll_directory(path)
import openslide
print(f'OpenSlide {openslide.__version__} OK')
"
```

Expected: `OpenSlide 4.0.0 OK`

### Install Python binding

```powershell
pip install openslide-python>=4.0.0
```

---

## 3. PostgreSQL Configuration

The PostgreSQL server typically runs on a remote VM. All services connect via `DATABASE_URL`.

### DATABASE_URL format

```
postgresql+psycopg2://<user>:<password>@<host>:<port>/<database>
```

**Examples:**

```dotenv
# Remote VM by hostname
DATABASE_URL=postgresql+psycopg2://pathoryx_user:mypassword@path-db-vm:5432/pathoryx

# Remote VM by IP
DATABASE_URL=postgresql+psycopg2://pathoryx_user:mypassword@192.168.1.100:5432/pathoryx

# Local development
DATABASE_URL=postgresql+psycopg2://pathoryx_user:mypassword@localhost:5432/pathoryx
```

### Create DB and user (first-time setup)

```sql
CREATE USER pathoryx_user WITH PASSWORD 'your_strong_password';
CREATE DATABASE pathoryx OWNER pathoryx_user;
GRANT ALL PRIVILEGES ON DATABASE pathoryx TO pathoryx_user;
```

### Test connectivity

```powershell
# TCP port test
Test-NetConnection -ComputerName path-db-vm -Port 5432

# psql test
psql "postgresql://pathoryx_user:PASSWORD@path-db-vm:5432/pathoryx" -c "SELECT version();"

# Python / SQLAlchemy test
python -c "
from sqlalchemy import create_engine, text
import os
e = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
with e.connect() as c:
    print(c.execute(text('SELECT version()')).scalar())
"
```

### Run migrations

```powershell
.\scripts\windows_run_migrations.ps1
# or directly:
alembic upgrade head
```

Verify: `alembic current` should show `(head)`.

### Connectivity troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `connection refused` | PostgreSQL not listening | Check `listen_addresses` in `postgresql.conf` |
| `no pg_hba.conf entry` | Client IP not allowed | Add client IP to `pg_hba.conf` |
| `password authentication failed` | Wrong password | `ALTER USER pathoryx_user WITH PASSWORD '...'` |
| `SSL connection required` | SSL enforced | Add `?sslmode=require` or `sslmode=disable` to URL |
| Firewall blocks 5432 | Network ACL | Open port 5432 inbound on VM |

---

## 4. Environment Variables

### `.env` file setup

```powershell
copy .env.windows.example .env
notepad .env   # fill in all CHANGE_ME values
```

### Required variables

```dotenv
DATABASE_URL=postgresql+psycopg2://pathoryx_user:PASSWORD@vm-host:5432/pathoryx
```

### Runtime paths (Windows paths — use forward slashes)

```dotenv
OPENSLIDE_DLL_PATH=D:/tools/openslide-bin-4.0.0.8-windows-x64/bin

BABELSHARK_CONFIG_PATH=D:/Slides/Palantir/configs/babelshark_config.windows.yaml
QC_CONFIG_PATH=D:/Slides/Palantir/configs/qc_config.windows.yaml
QC_SERVICE_CONFIG=D:/Slides/Palantir/configs/qc_service.yaml
DICOM_CONFIG_PATH=D:/Slides/Palantir/configs/dicom_config.windows.yaml
RECOVERY_SENTRY_CONFIG=D:/Slides/Palantir/configs/recovery_sentry.windows.yaml
SCANNER_FLEET_CONFIG=D:/Slides/Palantir/configs/scanner_fleet.yaml

PALANTIR_ALLOWED_INPUT_ROOTS=D:/Slides/Palantir/data
```

### Optional variables

```dotenv
# PASNet validation (currently disabled)
PASNET_SERVER=CHANGE_ME
PASNET_USERNAME=CHANGE_ME
PASNET_PASSWORD=CHANGE_ME

# LIS enrichment (currently disabled)
LIS_SQL_SERVER=CHANGE_ME
LIS_SQL_USERNAME=CHANGE_ME
LIS_SQL_PASSWORD=CHANGE_ME

# OpenTelemetry tracing (optional)
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317

# Override ports if conflicts exist
PATHORYX_DASHBOARD_PORT=8090
```

---

## 5. Data Directory Setup

```powershell
.\scripts\windows_bootstrap_dirs.ps1
```

This creates the full directory structure under `data\`:

```
data\
  watch\           BabelShark drop folder (place WSI files here)
  scanner_fake\    Simulated scanner input
  staging\         BabelShark intermediate staging area
  final\           Completed routed slides (final/<CaseID>/filename)
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

---

## 6. Model Weights

Copy the following `.pth` files into `models_weights\` before starting the QC service:

| File | Model | Used by |
|------|-------|---------|
| `penmark_detection_MobileNetV3.pth` | Pen mark detection | QC |
| `bubble_detection_ConvNeXtTiny_model.pth` | Bubble detection | QC |
| `stain_model_MobileNetV3.pth` | Stain classification | QC |
| `blur_detection_resnet18_old.pth` | Blur detection | QC |
| `index.npz` | Layout model index | BabelShark |

Verify: `dir models_weights\*.pth` — all 4 files must exist.

---

## 7. Service Startup

### Start all services (recommended)

```powershell
conda activate C:\Users\Public\conda-envs\babelfish1
cd D:\Slides\Palantir
.\scripts\windows_start_orchestrator.ps1
# or:
pathoryx-orchestrate
```

### Start services individually

```powershell
# Dashboard backend
.\scripts\windows_start_dashboard_backend.ps1
# or: pathoryx-dashboard

# QC service (loads models — needs OPENSLIDE_DLL_PATH set)
pathoryx-qc

# BabelShark (intake)
pathoryx-babelshark

# DICOM conversion
pathoryx-dicom

# Uploader
pathoryx-uploader

# RecoverySentry
pathoryx-recovery-sentry
```

### Dashboard frontend (dev mode)

```powershell
.\scripts\windows_start_dashboard_frontend.ps1
# or:
cd dashboard-ui && npm run dev
# → http://localhost:5173
```

### Dashboard frontend (production)

```powershell
cd dashboard-ui && npm run build
# Access built UI at: http://127.0.0.1:8090
```

---

## 8. First Pipeline Run (Dry-Run Mode)

1. Start dashboard backend.
2. Open `http://127.0.0.1:8090` — verify Overview page loads.
3. Start orchestrator in a separate terminal.
4. Drop a test slide:
   ```powershell
   copy "path\to\test_slide.svs" "D:\Slides\Palantir\data\watch\"
   ```
5. Watch dashboard — slide should appear within 60 s in the Slide Explorer.
6. Confirm slide progresses through QC → DICOM → Upload stages.
7. Upload result should show `dry_run_ok` (no real PACS upload).
8. Check `data\dicom_output\` for converted DICOM files.

---

## 9. RecoverySentry — Subfolder Scanning

RecoverySentry scans watch folders **recursively** by default:

```
data\failed\
  2026-06-05\
    N24-3625-Q.svs        ← detected automatically
  case123\sub\
    slideA.svs            ← detected automatically
```

Control via `scan_subfolders` in `recovery_sentry.windows.yaml`:

```yaml
scan_subfolders: true   # default; false restricts to immediate children
```

Hidden directories (`.` prefix) are always skipped. Symlinks outside configured watch roots are rejected by path validation.

---

## 10. Open Folder Button (Recovery Center)

The Recovery Center "Open Folder" button sends `POST /dashboard/api/recovery/files/{id}/open-folder`.

On Windows: `os.startfile(folder_path)` opens File Explorer at the file's containing directory.

> **Important:** This opens a folder on the **machine running the dashboard backend**, not the browser. For local workstation deployments this is the same machine. On remote/server deployments it opens a folder on the server.

If the backend is headless (no display), returns `opened: false` with an explanatory message.

---

## 11. Running as Windows Services (Production)

Use NSSM from `https://nssm.cc/`:

```powershell
nssm install PalantirDashboard "C:\Users\Public\conda-envs\babelfish1\Scripts\pathoryx-dashboard.exe"
nssm set PalantirDashboard AppDirectory "D:\Slides\Palantir"
nssm set PalantirDashboard AppEnvironmentExtra "DATABASE_URL=postgresql+...`nOPENSLIDE_DLL_PATH=D:\tools\..."
nssm start PalantirDashboard
```

Or use `pywin32` (`pip install "pathoryx-enterprise[windows]"`) for programmatic service registration.

---

## 12. Troubleshooting

### `Couldn't locate OpenSlide DLL` / `DLL load failed`

```powershell
# Check if variable is set
echo $env:OPENSLIDE_DLL_PATH

# Check DLL exists
dir "$env:OPENSLIDE_DLL_PATH\libopenslide-1.dll"

# Manual test
python -c "import os; os.add_dll_directory(r'$env:OPENSLIDE_DLL_PATH'); import openslide; print('OK')"
```

Ensure you're pointing at the `bin\` subdirectory, not the root extraction folder.

### `DATABASE_URL missing` / `FATAL: configuration error`

```powershell
# Set for current session
$env:DATABASE_URL = "postgresql+psycopg2://pathoryx_user:PASSWORD@host:5432/pathoryx"

# Or ensure .env file exists in project root with correct DATABASE_URL
```

### `No module named tqdm` / `No module named reportlab` / `No module named easyocr`

```powershell
pip install -e ".[babelshark]"    # tqdm, reportlab
pip install -e ".[ocr]"           # easyocr
pip install -e ".[datamatrix]"    # pylibdmtx
```

### `No module named pylibdmtx`

pylibdmtx requires the `libdmtx` native library. Download from:
`https://github.com/dmtx/dmtx-wrappers/releases` and ensure the DLL is on PATH.

### Subprocess child processes missing env vars

If a child process started by the orchestrator cannot find env vars set in the parent shell:

```powershell
# Set at Machine level (visible to all subprocesses)
[System.Environment]::SetEnvironmentVariable("OPENSLIDE_DLL_PATH", "D:\...\bin", "Machine")
```

Or use the conda `activate.d` script — it sets vars whenever the env is activated, including in subprocesses.

### `alembic upgrade head` fails

```powershell
# Ensure DATABASE_URL is in environment
$env:DATABASE_URL = "postgresql+psycopg2://..."
alembic upgrade head
```

### Dashboard loads but API calls fail (CORS / proxy)

In development mode (Vite dev server): all `/dashboard/api/*` are proxied to `http://127.0.0.1:8090`. Ensure the backend is running on port 8090.

In production (built bundle served by FastAPI): no proxy needed — same origin.

---

## 13. Credentials Requiring Manual Configuration

| Credential | Location | Required when |
|-----------|----------|--------------|
| `DATABASE_URL` password | `.env` | Always |
| `PASNET_SERVER/USERNAME/PASSWORD` | `.env` | PASNET validation enabled |
| `LIS_SQL_SERVER/USERNAME/PASSWORD` | `.env` | LIS DICOM enrichment enabled |
| `cstore.peer_ip` | `configs/dicom_config.yaml` | Real C-STORE upload |
| `cstore.sec_dcm_bin` | `configs/dicom_config.yaml` | Real C-STORE upload |

---

## 14. Enabling Real C-STORE Upload (Production Only)

> Complete phases 1–8 and verify dry-run works end-to-end before proceeding.
> Requires explicit data governance approval.

1. Verify PACS connectivity:
   ```powershell
   ping path-pacs2
   Test-NetConnection path-pacs2 -Port 32001
   ```
2. Edit `configs\dicom_config.yaml` (NOT `dicom_config_production.yaml`):
   ```yaml
   upload:
     dry_run: false
   cstore:
     upload_via_c_store: true
     peer_ip: "path-pacs2"
     default_peer_port: "32001"
     sec_dcm_bin: "C:\\Program Files\\Sectra\\ImageTools\\bin"
   ```
3. Restart DICOM service only.
4. Monitor first upload via dashboard.

> **WARNING:** `configs/dicom_config_production.yaml` already has `dry_run=false` and `upload_via_c_store=true`. Never use it directly — it is a reference template only. Always edit `dicom_config.yaml` or `dicom_config.windows.yaml`.

---

## 15. Rollback Procedure

If migration to Windows fails and Linux host must be restored as primary:

1. Stop all services on Windows.
2. Point `.env` on Linux back to the correct `DATABASE_URL`.
3. Run `alembic upgrade head` on Linux (no-op if already current).
4. Restart services on Linux.
5. Document what failed on Windows for follow-up.
