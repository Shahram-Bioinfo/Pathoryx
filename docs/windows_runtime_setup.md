# Palantir — Windows Runtime Setup Guide

This guide covers Windows-specific runtime configuration for the Palantir
digital pathology pipeline. It focuses on native library setup (OpenSlide),
environment variables, PostgreSQL connectivity, and service startup.

For the full installation checklist see `docs/WINDOWS_MIGRATION_CHECKLIST.md`.
For day-to-day operations see `WINDOWS_RUNBOOK.md`.

---

## OpenSlide Setup

OpenSlide is the WSI (Whole-Slide Image) reading library used by the QC
inference service and BabelShark. On Windows it ships as a native DLL
bundle that must be registered before Python imports it.

### 1. Download OpenSlide Windows binaries

Download the latest prebuilt Windows binaries from:

```
https://github.com/openslide/openslide-winbuild/releases
```

Choose the `openslide-bin-X.X.X.X-windows-x64.zip` asset.

Recommended extract location:

```
D:\tools\openslide-bin-4.0.0.8-windows-x64\
```

The `bin\` subdirectory will contain `libopenslide-1.dll` and supporting DLLs.

### 2. Set OPENSLIDE_DLL_PATH

Add the `bin\` directory path to your environment.

**Option A — System environment variable (permanent, all users):**

```powershell
[System.Environment]::SetEnvironmentVariable(
    "OPENSLIDE_DLL_PATH",
    "D:\tools\openslide-bin-4.0.0.8-windows-x64\bin",
    "Machine"
)
```

**Option B — .env file (recommended for development):**

```dotenv
OPENSLIDE_DLL_PATH=D:/tools/openslide-bin-4.0.0.8-windows-x64/bin
```

**Option C — PowerShell session:**

```powershell
$env:OPENSLIDE_DLL_PATH = "D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
```

**Option D — conda activate.d script (auto-loads on `conda activate`):**

Create `C:\Users\Public\conda-envs\babelfish1\etc\conda\activate.d\palantir_env.bat`:

```bat
@echo off
set "OPENSLIDE_DLL_PATH=D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
set "DATABASE_URL=postgresql+psycopg2://pathoryx_user:PASSWORD@your-vm-host:5432/pathoryx"
```

Or the PowerShell variant `palantir_env.ps1` in the same directory:

```powershell
$env:OPENSLIDE_DLL_PATH = "D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
$env:DATABASE_URL = "postgresql+psycopg2://pathoryx_user:PASSWORD@your-vm-host:5432/pathoryx"
```

### 3. Priority order

The runtime resolves the DLL path in this order:

1. `OPENSLIDE_DLL_PATH` environment variable **(always wins)**
2. `dll_paths.openslide_dll` key in YAML config (BabelShark only, fallback)

The environment variable approach is strongly preferred. The YAML key exists
only as a last-resort fallback for constrained deployment scenarios.

### 4. Validate the installation

```powershell
# Quick validation — must succeed before starting any service
$env:OPENSLIDE_DLL_PATH = "D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
python -c "
import os, sys
path = os.environ.get('OPENSLIDE_DLL_PATH', '')
if path:
    os.add_dll_directory(path)
import openslide
print(f'OpenSlide {openslide.__version__} OK')
"
```

Expected output: `OpenSlide 4.0.0 OK`

### 5. Install the Python binding

```powershell
pip install openslide-python>=4.0.0
```

The Python package is separate from the native binaries. Both are required.

---

## Conda Environment

### Create the environment

```powershell
conda create --prefix C:\Users\Public\conda-envs\babelfish1 python=3.12 -y
conda activate C:\Users\Public\conda-envs\babelfish1
```

### Install Palantir with all service extras

```powershell
cd D:\Slides\Palantir
pip install -e ".[babelshark,dashboard,qc,ocr,datamatrix,dicom,dev]"
```

**Extras reference:**

| Extra | Services | Key packages |
|---|---|---|
| `babelshark` | pathoryx-babelshark | openslide-python, pydicom, pandas, reportlab, tqdm |
| `qc` | pathoryx-qc | torch, openslide-python, numpy, opencv |
| `dashboard` | pathoryx-dashboard | fastapi, uvicorn |
| `dicom` | pathoryx-dicom | pydicom |
| `ocr` | babelshark (stain OCR) | easyocr |
| `datamatrix` | babelshark (DataMatrix) | pylibdmtx, tqdm |
| `windows` | all | pywin32 |
| `dev` | testing | pytest, ruff, mypy |

**Minimal install** (database migrations and health monitoring only):

```powershell
pip install -e .
```

### Verify entry points

```powershell
pathoryx-dashboard --help
pathoryx-qc --help
pathoryx-babelshark --help
pathoryx-orchestrate --help
```

---

## Environment Variables

All required secrets and runtime paths are set via environment variables.
Never commit real values to source control.

### Required variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (see PostgreSQL VM section) |

### Runtime paths

| Variable | Description | Example |
|---|---|---|
| `OPENSLIDE_DLL_PATH` | OpenSlide native bin\\ directory | `D:\tools\openslide-bin-4.0.0.8-windows-x64\bin` |
| `BABELSHARK_CONFIG_PATH` | BabelShark YAML config path | `D:\Slides\Palantir\configs\babelshark_config.windows.yaml` |
| `QC_CONFIG_PATH` | QC inference YAML path | `D:\Slides\Palantir\configs\qc_config.windows.yaml` |
| `QC_SERVICE_CONFIG` | QC service routing YAML | `D:\Slides\Palantir\configs\qc_service.yaml` |
| `DICOM_CONFIG_PATH` | DICOM service YAML path | `D:\Slides\Palantir\configs\dicom_config.windows.yaml` |
| `RECOVERY_SENTRY_CONFIG` | RecoverySentry YAML path | `D:\Slides\Palantir\configs\recovery_sentry.windows.yaml` |
| `SCANNER_FLEET_CONFIG` | Scanner fleet YAML path | `D:\Slides\Palantir\configs\scanner_fleet.yaml` |
| `PALANTIR_ALLOWED_INPUT_ROOTS` | Allowed input root directories | `D:\Slides\Palantir\data` |

### Optional service variables

| Variable | Description |
|---|---|
| `PASNET_SERVER` | PASNET validation server hostname |
| `PASNET_USERNAME` | PASNET service account username |
| `PASNET_PASSWORD` | PASNET service account password |
| `LIS_SQL_SERVER` | LIS database server hostname |
| `LIS_SQL_USERNAME` | LIS database username |
| `LIS_SQL_PASSWORD` | LIS database password |

### Setting variables — CMD

```cmd
set DATABASE_URL=postgresql+psycopg2://pathoryx_user:PASSWORD@vm-host:5432/pathoryx
set OPENSLIDE_DLL_PATH=D:\tools\openslide-bin-4.0.0.8-windows-x64\bin
```

### Setting variables — PowerShell

```powershell
$env:DATABASE_URL = "postgresql+psycopg2://pathoryx_user:PASSWORD@vm-host:5432/pathoryx"
$env:OPENSLIDE_DLL_PATH = "D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
```

### Setting variables — .env file (recommended)

Copy the template:

```powershell
copy D:\Slides\Palantir\.env.windows.example D:\Slides\Palantir\.env
notepad D:\Slides\Palantir\.env
```

Fill in all `CHANGE_ME` values. The `.env` file is loaded automatically at
service startup by pydantic-settings.

### Setting variables — conda activate.d (auto-load on activate)

Create `C:\Users\Public\conda-envs\babelfish1\etc\conda\activate.d\` (mkdir if needed):

```bat
:: palantir_env.bat — auto-loads when conda activates babelfish1
@echo off
set "OPENSLIDE_DLL_PATH=D:\tools\openslide-bin-4.0.0.8-windows-x64\bin"
set "DATABASE_URL=postgresql+psycopg2://pathoryx_user:PASSWORD@vm-host:5432/pathoryx"
set "BABELSHARK_CONFIG_PATH=D:\Slides\Palantir\configs\babelshark_config.windows.yaml"
set "QC_CONFIG_PATH=D:\Slides\Palantir\configs\qc_config.windows.yaml"
set "DICOM_CONFIG_PATH=D:\Slides\Palantir\configs\dicom_config.windows.yaml"
set "RECOVERY_SENTRY_CONFIG=D:\Slides\Palantir\configs\recovery_sentry.windows.yaml"
set "SCANNER_FLEET_CONFIG=D:\Slides\Palantir\configs\scanner_fleet.yaml"
```

---

## PostgreSQL VM

The PostgreSQL server runs on a remote VM accessible over the network.

### DATABASE_URL format

```
postgresql+psycopg2://<user>:<password>@<host>:<port>/<database>
```

**Examples:**

```dotenv
# Remote VM with hostname
DATABASE_URL=postgresql+psycopg2://pathoryx_user:mypassword@path-db-vm:5432/pathoryx

# Remote VM with IP address
DATABASE_URL=postgresql+psycopg2://pathoryx_user:mypassword@192.168.1.100:5432/pathoryx

# Local PostgreSQL (development/testing)
DATABASE_URL=postgresql+psycopg2://pathoryx_user:mypassword@localhost:5432/pathoryx
```

### Test connectivity from Windows

```powershell
# Test TCP connectivity to the PostgreSQL port
Test-NetConnection -ComputerName path-db-vm -Port 5432

# Test with psql (requires PostgreSQL client tools on PATH)
psql "postgresql://pathoryx_user:PASSWORD@path-db-vm:5432/pathoryx" -c "SELECT version();"

# Test with Python
python -c "
from sqlalchemy import create_engine, text
import os
e = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
with e.connect() as c:
    print(c.execute(text('SELECT version()')).scalar())
"
```

### Troubleshooting connectivity

| Symptom | Likely cause | Fix |
|---|---|---|
| `connection refused` | PostgreSQL not accepting connections on that port/host | Check `postgresql.conf`: `listen_addresses`, `port` |
| `no pg_hba.conf entry` | Client IP not in `pg_hba.conf` | Add `host pathoryx pathoryx_user <client-IP>/32 md5` to pg_hba.conf |
| `password authentication failed` | Wrong password | Reset with `ALTER USER pathoryx_user WITH PASSWORD 'new'` |
| `SSL connection required` | SSL enforced | Add `?sslmode=require` to DATABASE_URL or `sslmode=disable` if no SSL |
| Firewall blocks port 5432 | Windows Firewall / network ACL | Open port 5432 inbound on the VM |

---

## Service Startup

Run all services from the project root with conda activated:

```powershell
conda activate C:\Users\Public\conda-envs\babelfish1
cd D:\Slides\Palantir
```

### Dashboard backend

```powershell
.\scripts\windows_start_dashboard_backend.ps1
# or directly:
pathoryx-dashboard
```

### QC inference service

```powershell
pathoryx-qc
```

The QC service calls `configure_openslide_runtime()` at startup before
importing any OpenSlide-dependent module. The DLL path is read from
`OPENSLIDE_DLL_PATH` (set it before starting the service).

### Orchestrator (all services)

```powershell
.\scripts\windows_start_orchestrator.ps1
# or directly:
pathoryx-orchestrate
```

The orchestrator validates `DATABASE_URL` before starting any service and
logs which optional env vars are configured.

### RecoverySentry

```powershell
pathoryx-recovery-sentry
```

---

## Troubleshooting

### `Couldn't locate OpenSlide DLL`

```
ImportError: OpenSlide not available: DLL load failed
```

**Cause:** OpenSlide native DLLs not found.

**Fix:**
1. Verify `OPENSLIDE_DLL_PATH` is set:
   ```powershell
   echo $env:OPENSLIDE_DLL_PATH
   ```
2. Verify the directory exists and contains `libopenslide-1.dll`:
   ```powershell
   dir "$env:OPENSLIDE_DLL_PATH\libopenslide-1.dll"
   ```
3. Re-run the validation:
   ```powershell
   python -c "import os; os.add_dll_directory(r'$env:OPENSLIDE_DLL_PATH'); import openslide; print('OK')"
   ```

---

### `No module named tqdm`

```powershell
pip install "pathoryx-enterprise[datamatrix]"
# or:
pip install tqdm>=4.66.0
```

---

### `No module named reportlab`

```powershell
pip install "pathoryx-enterprise[babelshark]"
# or:
pip install reportlab>=4.0.0
```

---

### `No module named easyocr`

```powershell
pip install "pathoryx-enterprise[ocr]"
# or:
pip install easyocr>=1.7.0
```

Note: easyocr installs PyTorch. If you already have a GPU-specific PyTorch
build, install easyocr without its torch dependency:
```powershell
pip install easyocr --no-deps
pip install opencv-python-headless Pillow numpy scipy
```

---

### `No module named pylibdmtx`

```powershell
pip install "pathoryx-enterprise[datamatrix]"
# or:
pip install pylibdmtx>=0.1.10
```

pylibdmtx requires the `libdmtx` native library.
Download from: https://github.com/dmtx/dmtx-wrappers/releases
and ensure the DLL is on PATH.

---

### `DATABASE_URL missing`

```
[FATAL] QC service configuration error: DATABASE_URL not set
```

Set it before starting the service:
```powershell
$env:DATABASE_URL = "postgresql+psycopg2://pathoryx_user:PASSWORD@host:5432/pathoryx"
```
Or ensure `.env` is present in the project root with `DATABASE_URL` filled in.

---

### `DLL load failed while importing _openslide`

Ensure `OPENSLIDE_DLL_PATH` is set to the **bin\\** directory (the directory
containing `libopenslide-1.dll`), not the top-level extraction directory.

Correct:
```
D:\tools\openslide-bin-4.0.0.8-windows-x64\bin
```
Incorrect:
```
D:\tools\openslide-bin-4.0.0.8-windows-x64
```

---

### Subprocess environment missing

If a child process started by the orchestrator cannot find env vars that are
set in the parent shell, check that the variables are in the machine or user
environment (not just the current PowerShell session):

```powershell
[System.Environment]::SetEnvironmentVariable("OPENSLIDE_DLL_PATH", "D:\...\bin", "Machine")
```

Alternatively, use the conda `activate.d` script approach documented above —
it sets vars whenever the conda environment is activated, including in
subprocesses spawned from that shell.

---

---

## Recovery Sentry — Subfolder Scanning

### Recursive scanning

Recovery Sentry scans all configured watch folders **recursively** by default.
Files in date-based or case-based subdirectories are detected automatically:

```
D:\Slides\Palantir\data\failed\
  2026-06-05\
    N24-3625-Q.svs        ← detected
  case123\
    sub\
      slideA.svs          ← detected
```

This behaviour is controlled by `scan_subfolders` in `recovery_sentry.yaml`:

```yaml
# Default: true — scan all subdirectories
scan_subfolders: true
```

Set to `false` to restrict scanning to the immediate children of each watch folder.

Hidden directories (names starting with `.`) are always skipped.
Symlinks that resolve outside the configured watch roots are rejected by path
validation and skipped.

### Dashboard display

When a file is inside a subfolder, the Recovery Center shows:

```
N24-3625-Q.svs
failed / 2026-06-05
```

The subfolder context (`failed / 2026-06-05`) is computed from the relative
path between the containing directory and the configured watch root.

---

## Recovery Center — Open Folder Button

The Recovery Center table and TechnicianReviewDrawer both include an
**Open Folder** button next to each monitored file.

### What it does

Clicking the button sends a `POST /dashboard/api/recovery/files/{id}/open-folder`
request to the dashboard backend. The backend then opens the containing folder
in the native file manager:

| Platform | Method |
|---|---|
| Windows | `os.startfile(folder_path)` — opens File Explorer |
| Linux   | `xdg-open folder_path` — opens Nautilus/Dolphin etc. |
| macOS   | `open folder_path` — opens Finder |

### Important: local workstation only

This feature opens a folder on the **machine running the dashboard backend**,
not the browser client. It is designed for local workstation deployments where
the backend runs on the same machine as the file system.

It is **not suitable** for remote/shared server deployments without additional
access controls. If the dashboard backend runs on a server accessed over the
network, the button will open a folder on the server, not the operator's desktop.

### Security model

- The folder path is resolved from the database snapshot (not user input).
- The path is validated against configured `watch_folders` and `final_destination_root` before opening.
- Paths outside all configured roots receive a `403 Forbidden` response.
- Raw paths are never accepted from the browser.

### Button states

| State | Meaning |
|---|---|
| Enabled | Folder exists and path is trusted |
| Greyed out | Folder no longer exists on disk |
| Error toast | OS open call failed (e.g. headless server) |

### Headless / server deployments

If the dashboard backend is running on a headless Linux server (no display),
`xdg-open` will fail with a non-zero exit code. The button returns
`opened: false` with an explanatory message. The folder path is still
returned in the response so the operator can navigate there manually.

---

### Running as a Windows Service

To run Palantir services as persistent Windows Services (auto-start, survive
logoff), use `pywin32` (`pip install "pathoryx-enterprise[windows]"`) or
the NSSM service wrapper:

```powershell
# Install NSSM from https://nssm.cc/
nssm install PalantirDashboard "C:\Users\Public\conda-envs\babelfish1\Scripts\pathoryx-dashboard.exe"
nssm set PalantirDashboard AppEnvironmentExtra DATABASE_URL=... OPENSLIDE_DLL_PATH=...
nssm start PalantirDashboard
```
