# DPARS Windows Environment Setup Guide

This guide walks through setting up the DPARS (Digital Pathology Analysis & Retrieval System) pipeline on a Windows machine from scratch. No prior Linux or server experience is assumed.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Python Installation](#2-python-installation)
3. [PostgreSQL Installation](#3-postgresql-installation)
4. [OpenSlide for Windows](#4-openslide-for-windows)
5. [Git and Repository Clone](#5-git-and-repository-clone)
6. [Python Virtual Environment](#6-python-virtual-environment)
7. [Package Installation](#7-package-installation)
8. [Directory Structure](#8-directory-structure)
9. [Configuration Files](#9-configuration-files)
10. [Environment Variables (.env file)](#10-environment-variables-env-file)
11. [Database Initialisation](#11-database-initialisation)
12. [Building the Dashboard Frontend](#12-building-the-dashboard-frontend)
13. [Starting the Services](#13-starting-the-services)
14. [Verifying the Installation](#14-verifying-the-installation)
15. [Common Errors and Fixes](#15-common-errors-and-fixes)

---

## 1. Prerequisites

You need the following software installed before starting. Download links are given; install each with default options unless the guide says otherwise.

| Software | Minimum version | Where to get it |
|---|---|---|
| Python | 3.11 | python.org → Downloads → Windows installer (64-bit) |
| PostgreSQL | 15 | postgresql.org → Download → Windows |
| Node.js | 20 LTS | nodejs.org → LTS installer |
| Git | any recent | git-scm.com → Windows |
| OpenSlide Windows binaries | 4.0+ | openslide.org → Download → Windows binaries |

> **Important:** During the Python installer, tick **"Add Python to PATH"** before clicking Install Now.

---

## 2. Python Installation

1. Run the Python installer.
2. On the first screen, check **"Add python.exe to PATH"**.
3. Click **"Install Now"**.
4. After installation, open **Command Prompt** (press `Win+R`, type `cmd`, press Enter) and verify:

```
python --version
```

You should see something like `Python 3.11.9`. If you see an error, reopen Command Prompt after the installer finishes.

---

## 3. PostgreSQL Installation

1. Run the PostgreSQL installer.
2. Accept all defaults. When prompted for a password, choose something you will remember — this becomes the `postgres` superuser password.
3. Let the installer run Stack Builder at the end (you can skip it if asked about additional tools).
4. After installation, open **pgAdmin 4** (installed automatically) or use `psql` from the command line.
5. Create the application database and user. Open pgAdmin → right-click **Login/Group Roles** → **Create** → **Login/Group Role**:

   - Name: `pathoryx`
   - Password: choose a strong password (save it — you will need it in step 10)
   - Privileges tab: enable **Can login** and **Create databases**

6. Right-click **Databases** → **Create** → **Database**:

   - Name: `pathoryx`
   - Owner: `pathoryx`

Alternatively, from `psql`:

```sql
CREATE USER pathoryx WITH LOGIN PASSWORD 'YourPasswordHere';
CREATE DATABASE pathoryx OWNER pathoryx;
```

---

## 4. OpenSlide for Windows

OpenSlide is a C library that reads whole-slide image (WSI) formats. On Windows it must be installed separately.

1. Go to [openslide.org](https://openslide.org) → Download → Windows binaries.
2. Download the latest ZIP (e.g. `openslide-win64-20231011.zip`).
3. Extract it to a permanent folder, for example:

   ```
   C:\tools\openslide\
   ```

   After extraction you should have a `bin\` subfolder containing `libopenslide-1.dll` (or similar).

4. You have two ways to tell DPARS where the DLLs are:

   **Option A — environment variable (recommended):** Add `OPENSLIDE_DLL_PATH` to your `.env` file (see step 10):

   ```
   OPENSLIDE_DLL_PATH=C:\tools\openslide\bin
   ```

   **Option B — config file:** Add a `dll_paths` block to `configs/babelshark_config.yaml`:

   ```yaml
   dll_paths:
     openslide_dll: "C:/tools/openslide/bin"
   ```

   The environment variable takes priority over the config file when both are set.

---

## 5. Git and Repository Clone

1. Install Git with default options.
2. Open Command Prompt or Git Bash.
3. Navigate to your target drive. The recommended location is:

   ```
   D:\Slides\Palantir
   ```

   Create the folder if it does not exist:

   ```cmd
   mkdir D:\Slides\Palantir
   cd /d D:\Slides\Palantir
   ```

4. Clone the repository (replace the URL with your actual remote):

   ```cmd
   git clone https://your-repo-url/pathoryx-enterprise.git .
   ```

   The trailing `.` clones into the current folder instead of creating a subfolder.

---

## 6. Python Virtual Environment

A virtual environment keeps DPARS dependencies isolated from other Python projects on your machine.

```cmd
cd /d D:\Slides\Palantir
python -m venv .venv
.venv\Scripts\activate
```

After activation your prompt changes to show `(.venv)`. You must activate this environment every time you open a new Command Prompt window before running any `pathoryx-*` commands or `pip install`.

---

## 7. Package Installation

DPARS is split into optional feature groups. Install only what you need:

```cmd
:: Minimum — core pipeline + database only
pip install -e .

:: Add QC ML models (requires PyTorch — large download, ~2 GB)
pip install -e ".[qc]"

:: Add BabelShark WSI intake
pip install -e ".[babelshark]"

:: Add dashboard backend (FastAPI)
pip install -e ".[dashboard]"

:: Add DICOM conversion
pip install -e ".[dicom]"

:: Add OCR stain extraction
pip install -e ".[ocr]"

:: Add DataMatrix barcode decoding
pip install -e ".[datamatrix]"

:: Add Windows-specific extras (pywin32)
pip install -e ".[windows]"

:: Install everything including dev/test tools
pip install -e ".[qc,babelshark,dashboard,dicom,ocr,datamatrix,windows,dev]"
```

> **Typical production install** for a machine running all services:
> ```cmd
> pip install -e ".[qc,babelshark,dashboard,dicom,ocr,datamatrix,windows]"
> ```

---

## 8. Directory Structure

Create the data folders that the services expect. You can adapt these paths, but they must match what is in your config files (see step 9).

```cmd
mkdir D:\Slides\Palantir\data\watch
mkdir D:\Slides\Palantir\data\watch\urgent
mkdir D:\Slides\Palantir\data\staging
mkdir D:\Slides\Palantir\data\final
mkdir D:\Slides\Palantir\data\failed
mkdir D:\Slides\Palantir\data\suspicious
mkdir D:\Slides\Palantir\data\manual_review
mkdir D:\Slides\Palantir\data\qc_output
mkdir D:\Slides\Palantir\data\quarantine
mkdir D:\Slides\Palantir\data\labels
mkdir D:\Slides\Palantir\data\label_crops
mkdir D:\Slides\Palantir\data\run_output
mkdir D:\Slides\Palantir\models_weights
```

Place QC model weight files (`.pth`) in `models_weights\`:

```
models_weights\penmark_detection_MobileNetV3.pth
models_weights\bubble_detection_ConvNeXtTiny_model.pth
models_weights\stain_model_MobileNetV3.pth
models_weights\blur_detection_resnet18_old.pth
```

---

## 9. Configuration Files

The `configs\` folder contains the service configuration files. Copy the bundled templates and edit paths:

| File | Controls |
|---|---|
| `configs\babelshark_config.yaml` | BabelShark WSI intake, slide ID generation, watch folders |
| `configs\qc_config.yaml` | QC model paths, thresholds, output folders |
| `configs\dicom_config.yaml` | DICOM conversion and upload settings |
| `configs\recovery_sentry.yaml` | Recovery Sentry watch folders, recovery options |

Open each file in a text editor (Notepad++ or VS Code are recommended) and change any path that begins with `C:/Users/Public/projects/Palantir/` to match your actual data directory.

**Example — `configs\babelshark_config.yaml` paths to update:**

```yaml
watch_dir: "D:/Slides/Palantir/data/watch"
staging_dir: "D:/Slides/Palantir/data/staging"
failed_output_dir: "D:/Slides/Palantir/data/failed"
final_output_dir: "D:/Slides/Palantir/data/final"
label_root_dir: "D:/Slides/Palantir/data/labels"
```

**Example — `configs\qc_config.yaml` paths to update:**

```yaml
paths:
  output_root: "D:/Slides/Palantir/data/qc_output"
  quarantine_root: "D:/Slides/Palantir/data/quarantine"

models:
  penmark_weights: "D:/Slides/Palantir/models_weights/penmark_detection_MobileNetV3.pth"
  bubble_weights: "D:/Slides/Palantir/models_weights/bubble_detection_ConvNeXtTiny_model.pth"
  stain_weights: "D:/Slides/Palantir/models_weights/stain_model_MobileNetV3.pth"
  blur_weights: "D:/Slides/Palantir/models_weights/blur_detection_resnet18_old.pth"
```

**Example — `configs\recovery_sentry.yaml` paths to update:**

```yaml
watch_folders:
  - "D:/Slides/Palantir/data/failed"
  - "D:/Slides/Palantir/data/suspicious"
  - "D:/Slides/Palantir/data/manual_review"

final_destination_root: "D:/Slides/Palantir/data/final"
```

---

## 10. Environment Variables (.env file)

DPARS reads secrets and environment-specific settings from a `.env` file at the repository root. Create the file:

```
D:\Slides\Palantir\.env
```

**Minimum required content:**

```dotenv
# ── Database (required) ──────────────────────────────────────────────────────
DATABASE_URL=postgresql+psycopg2://pathoryx:YourPasswordHere@localhost:5432/pathoryx

# ── OpenSlide DLLs (Windows only) ───────────────────────────────────────────
OPENSLIDE_DLL_PATH=C:\tools\openslide\bin

# ── Service config file locations ───────────────────────────────────────────
BABELSHARK_CONFIG_PATH=configs/babelshark_config.yaml
QC_CONFIG_PATH=configs/qc_config.yaml
DICOM_CONFIG_PATH=configs/dicom_config.yaml
RECOVERY_SENTRY_CONFIG=configs/recovery_sentry.yaml
```

**Optional settings (with defaults shown):**

```dotenv
# ── Environment identity ─────────────────────────────────────────────────────
PATHORYX_ENVIRONMENT=development
PATHORYX_SITE_CODE=site_local
PATHORYX_SERVICE_VERSION=1.0.0

# ── Dashboard host/port ──────────────────────────────────────────────────────
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8090

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL=INFO
LOG_FORMAT=json

# ── PASNET/LIS integration (only needed if pasnet_validation: true in config)
PASNET_SERVER=your-lis-server.hospital.internal
PASNET_USERNAME=lis_reader
PASNET_PASSWORD=YourLISPassword

# ── Sectra PACS connection ───────────────────────────────────────────────────
SECTRA_HOST=pacs.hospital.internal
SECTRA_PORT=104
SECTRA_AE_TITLE=DPARS_SCU
SECTRA_REMOTE_AE_TITLE=SECTRA_SCP
SECTRA_CSTORE_BIN=storescu

# ── Path safety (comma-separated allowed input roots) ────────────────────────
PATHORYX_ALLOWED_INPUT_ROOTS=D:\Slides\Palantir\data
```

> **Never commit the `.env` file to Git.** It is listed in `.gitignore` by default.

---

## 11. Database Initialisation

With the virtual environment activated and `DATABASE_URL` set, run the migration tool to create all tables and schemas:

```cmd
pathoryx-migrate
```

This applies all Alembic migrations in order, creating the eight PostgreSQL schemas (`core`, `events`, `babelshark`, `qc`, `dicomizer`, `uploader`, `failed_watcher`, `audit`) and the `routing` schema added in Phase 4.8.

To verify the schemas were created, open pgAdmin, expand `pathoryx` → **Schemas**. You should see all eight schemas listed.

---

## 12. Building the Dashboard Frontend

The dashboard UI is a React application that must be compiled before first use.

1. Install Node.js (version 20 LTS).
2. Open Command Prompt and navigate to the frontend folder:

   ```cmd
   cd /d D:\Slides\Palantir\dashboard-ui
   npm install
   npm run build
   ```

3. The built files are placed in `dashboard-ui\dist\`. The dashboard backend serves them automatically when started.

---

## 13. Starting the Services

Each service runs as a separate process. Open a separate Command Prompt window for each, activate the virtual environment in each window, then run:

```cmd
.venv\Scripts\activate
```

**Start order (respect dependencies):**

| Window | Command | What it does |
|---|---|---|
| 1 | `pathoryx-babelshark` | Watches for new WSI files, runs intake pipeline |
| 2 | `pathoryx-qc` | Runs ML quality-control on staged slides |
| 3 | `pathoryx-dicom` | Converts passed slides to DICOM format |
| 4 | `pathoryx-uploader` | Uploads DICOM files to PACS |
| 5 | `pathoryx-recovery-sentry` | Monitors failed/suspicious folders for technician corrections |
| 6 | `pathoryx-dashboard` | Starts the web dashboard API on port 8090 |

**Or start everything at once using the orchestrator:**

```cmd
pathoryx-orchestrate
```

The orchestrator launches all services in a managed process group. Use individual windows during initial setup so you can see each service's log output separately.

---

## 14. Verifying the Installation

1. Open a browser and go to: `http://localhost:5173` (if running the frontend dev server) or `http://localhost:8090/dashboard/docs` (API documentation).

2. Check the API health endpoint:

   ```cmd
   curl http://localhost:8090/dashboard/health
   ```

   Expected response: `{"status": "ok", ...}`

3. To check an individual service health endpoint, each service exposes one on its configured port:

   | Service | Health port |
   |---|---|
   | BabelShark | 8081 |
   | QC | 8082 |
   | DICOM | 8083 |
   | Uploader | 8084 |
   | Recovery Sentry | 8087 |
   | Dashboard | 8090 |

   Example: `curl http://localhost:8081/health`

4. Drop a test WSI file (`.svs` or `.tif`) into `D:\Slides\Palantir\data\watch`. Watch the BabelShark log — within one minute it should detect and process the file.

5. Open the Operations Overview page in the dashboard. The **Queue Depth** and **Artifacts** metrics should update.

---

## 15. Common Errors and Fixes

### `DATABASE_URL must be set in the environment`

The `.env` file was not found or `DATABASE_URL` is missing. Check:

- The `.env` file is in the **repository root** (`D:\Slides\Palantir\.env`), not in `configs\` or `dashboard-ui\`.
- The virtual environment is activated (prompt shows `(.venv)`).
- There are no typos in the variable name.

### `openslide` import fails or DLL not found

OpenSlide DLLs were not found. Verify:

- `OPENSLIDE_DLL_PATH` in `.env` points to the `bin\` folder containing `libopenslide-1.dll`.
- Use forward slashes or escaped backslashes: `C:/tools/openslide/bin` or `C:\\tools\\openslide\\bin`.
- The path has no trailing backslash.

### `could not connect to server: Connection refused` (PostgreSQL)

- PostgreSQL service is not running. Open **Services** (`Win+R` → `services.msc`), find **postgresql-x64-15**, right-click → **Start**.
- The `DATABASE_URL` host/port do not match your PostgreSQL installation (default is `localhost:5432`).

### `alembic.util.exc.CommandError: Can't locate revision` during migration

Run `pathoryx-migrate` from the **repository root** directory, not from inside `pathoryx_enterprise\`.

### `npm: command not found`

Node.js is not installed or not on PATH. Reinstall Node.js from nodejs.org and reopen Command Prompt.

### Services start but slides are not picked up

- Confirm `watch_dir` in `babelshark_config.yaml` matches the folder where you dropped the file.
- `watch_interval_minutes: 1` — the watcher polls every 60 seconds; wait at least a minute.
- Check the BabelShark log window for error messages.

### `pathoryx-failed-watcher: command not found` or prints an error and exits

This command is deprecated. Use `pathoryx-recovery-sentry` instead.
