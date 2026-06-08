# Palantir — Windows Migration Checklist

Use this checklist when migrating from the Linux development environment to a
new Windows host. Work through items in order; check each box before moving on.

---

## Phase 1 — Windows host prerequisites

- [ ] Windows 10/11 or Windows Server 2019+
- [ ] PowerShell 5.1+ (`$PSVersionTable.PSVersion`)
- [ ] Git installed (`git --version`)
- [ ] Conda installed and on PATH (`conda --version`)
- [ ] Conda env `babelfish1` exists at `C:\Users\Public\conda-envs\babelfish1`
  - If not: `conda create --prefix C:\Users\Public\conda-envs\babelfish1 python=3.12`
- [ ] PostgreSQL 15+ installed and service running
  - `Get-Service postgresql*`
- [ ] Node.js 18+ installed (`node --version`, `npm --version`)
- [ ] OpenSlide for Windows installed, `bin\` directory on PATH
  - Test: `python -c "import openslide"`

---

## Phase 2 — Repository

- [ ] Clone repository:
  ```powershell
  cd C:\Users\Public\projects
  git clone git@github.com:Shahram-Bioinfo/Palantir.git
  cd Palantir
  ```
- [ ] Verify branch: `git log --oneline -5`
- [ ] Activate conda env:
  ```powershell
  conda activate C:\Users\Public\conda-envs\babelfish1
  ```
- [ ] Install Python package:
  ```powershell
  pip install -e .
  pip install -e ".[dashboard]"
  pip install -e ".[qc]"
  pip install -e ".[dev]"
  ```
- [ ] Verify entry points:
  ```powershell
  pathoryx-orchestrate --help
  pathoryx-babelshark --help
  pathoryx-dashboard --help
  ```

---

## Phase 3 — Data directories

- [ ] Run bootstrap script:
  ```powershell
  .\scripts\windows_bootstrap_dirs.ps1
  ```
- [ ] Verify expected folders exist:
  ```powershell
  dir C:\Users\Public\projects\Palantir\data\
  ```
  Expected: `watch`, `scanner_fake`, `staging`, `final`, `failed`, `suspicious`,
  `manual_review`, `dicom_output`, `run_output`, `labels`, `label_crops`,
  `qc_output`, `quarantine`, `roi_debug`, `roi_debug_parts`
- [ ] Copy model weights into `models_weights\`:
  - `penmark_detection_MobileNetV3.pth`
  - `bubble_detection_ConvNeXtTiny_model.pth`
  - `stain_model_MobileNetV3.pth`
  - `blur_detection_resnet18_old.pth`
  - `index.npz` (layout model index)

---

## Phase 4 — Environment and config

- [ ] Copy Windows env template:
  ```powershell
  copy .env.windows.example .env
  ```
- [ ] Edit `.env` in a text editor and fill in all `CHANGE_ME` values:
  - [ ] `DATABASE_URL` — PostgreSQL password
  - [ ] `PASNET_SERVER`, `PASNET_USERNAME`, `PASNET_PASSWORD` *(skip if PASNET disabled)*
  - [ ] `LIS_SQL_SERVER`, `LIS_SQL_USERNAME`, `LIS_SQL_PASSWORD` *(skip if LIS disabled)*
- [ ] Verify config paths in `.env` point to `.windows.yaml` variants:
  - `BABELSHARK_CONFIG_PATH=C:/Users/Public/projects/Palantir/configs/babelshark_config.windows.yaml`
  - `QC_CONFIG_PATH=C:/Users/Public/projects/Palantir/configs/qc_config.windows.yaml`
  - `DICOM_CONFIG_PATH=C:/Users/Public/projects/Palantir/configs/dicom_config.windows.yaml`
  - `RECOVERY_SENTRY_CONFIG=C:/Users/Public/projects/Palantir/configs/recovery_sentry.windows.yaml`
  - `SCANNER_FLEET_CONFIG=C:/Users/Public/projects/Palantir/configs/scanner_fleet.yaml`
- [ ] Confirm `DICOM_CONFIG_PATH` points to a config with `upload.dry_run: true` and
      `cstore.upload_via_c_store: false` (safe default)

---

## Phase 5 — Database

- [ ] Create PostgreSQL user and database:
  ```sql
  CREATE USER pathoryx_user WITH PASSWORD 'your_strong_password';
  CREATE DATABASE pathoryx OWNER pathoryx_user;
  GRANT ALL PRIVILEGES ON DATABASE pathoryx TO pathoryx_user;
  ```
- [ ] Test connection:
  ```powershell
  psql "postgresql://pathoryx_user:PASSWORD@localhost:5432/pathoryx"
  ```
- [ ] Run migrations:
  ```powershell
  .\scripts\windows_run_migrations.ps1
  ```
  Or: `alembic upgrade head`
- [ ] Verify schema:
  ```sql
  \dn    -- should list: core, babelshark, qc, dicomizer, uploader, failed_watcher, upload_tracking
  \dt core.*
  \dt upload_tracking.*
  ```

---

## Phase 6 — Smoke test

- [ ] Run the smoke test script:
  ```powershell
  .\scripts\windows_smoke_test.ps1
  ```
  All checks must pass before proceeding.
- [ ] Manually verify: `alembic current` shows `(head)`
- [ ] Manually verify: dashboard config loads scanner fleet
  ```powershell
  python -c "from pathoryx_enterprise.services.dashboard.scanner_fleet import ScannerFleet; f=ScannerFleet.load_default(); print(f.total_count, 'scanners')"
  ```

---

## Phase 7 — First pipeline run (dry-run mode)

- [ ] Start dashboard backend:
  ```powershell
  .\scripts\windows_start_dashboard_backend.ps1
  ```
- [ ] Open dashboard: http://127.0.0.1:8090
- [ ] Start orchestrator in a separate terminal:
  ```powershell
  .\scripts\windows_start_orchestrator.ps1
  ```
- [ ] Drop a test slide into `data\watch\`:
  ```powershell
  copy "path\to\test_slide.svs" "C:\Users\Public\projects\Palantir\data\watch\"
  ```
- [ ] Confirm BabelShark picks up the slide (within 1 minute)
- [ ] Confirm slide progresses through QC → DICOM → Upload stages
- [ ] Confirm upload shows `dry_run_ok` in the dashboard Upload Results panel
- [ ] Confirm `data\dicom_output\` contains converted DICOM files

---

## Phase 8 — Frontend (optional for dev)

- [ ] Start dev frontend:
  ```powershell
  .\scripts\windows_start_dashboard_frontend.ps1
  ```
- [ ] Open: http://localhost:5173
- [ ] OR build and serve via backend:
  ```powershell
  cd dashboard-ui && npm run build
  ```
  Then access built UI at: http://127.0.0.1:8090

---

## Phase 9 — Real upload (only when explicitly authorized)

> Complete all previous phases and confirm dry-run works end-to-end first.

- [ ] Get explicit approval from data governance officer
- [ ] Verify PACS network connectivity:
  ```powershell
  ping path-pacs2
  Test-NetConnection path-pacs2 -Port 32001
  ```
- [ ] Update `configs\dicom_config.yaml` (keep the `.windows.yaml` as reference):
  ```yaml
  upload:
    dry_run: false
  cstore:
    upload_via_c_store: true
    peer_ip: "path-pacs2"       # confirm hostname with PACS admin
    default_peer_port: "32001"
    sec_dcm_bin: "C:\\Program Files\\Sectra\\ImageTools\\bin"
  ```
- [ ] Restart DICOM service only
- [ ] Monitor first real upload via dashboard

---

## Credentials requiring manual configuration

| Credential | Location | Required when |
|---|---|---|
| `DATABASE_URL` password | `.env` | Always — PostgreSQL access |
| `PASNET_SERVER` / `_USERNAME` / `_PASSWORD` | `.env` | PASNET validation enabled |
| `LIS_SQL_SERVER` / `_USERNAME` / `_PASSWORD` | `.env` | LIS enrichment enabled |
| `NEXUS_USERNAME` / `NEXUS_PASSWORD` | `.env` | Pulling models from Nexus |
| `cstore.peer_ip` | `configs/dicom_config.yaml` | Real C-STORE upload |
| `cstore.sec_dcm_bin` | `configs/dicom_config.yaml` | Real C-STORE upload |

---

## Rollback procedure

If migration fails and the Linux host needs to be restored as primary:

1. Stop all services on Windows.
2. Point `.env` on Linux back to the correct `DATABASE_URL`.
3. Run `alembic upgrade head` on Linux (no-op if already current).
4. Restart services on Linux.
5. Document what failed on Windows for follow-up.
