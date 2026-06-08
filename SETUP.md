# Setup Guide

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12+ | `python3 --version` |
| PostgreSQL | 14+ | `psql --version` |
| pip | 23+ | `pip --version` |
| dcmtk (storescu) | any | `storescu --version` — DICOM upload only |
| NVIDIA GPU | optional | QC service; CPU fallback available |
| Nextflow | 23.10+ | optional; only for Nextflow-based orchestration |

## 1. Create a virtual environment

```bash
cd /home/shahram/Palantir
python3 -m venv .venv
source .venv/bin/activate
```

Add `source /home/shahram/Palantir/.venv/bin/activate` to your shell profile to activate automatically.

## 2. Install the package

```bash
# Core services only (no QC model dependencies):
pip install -e .

# With QC dependencies (PyTorch, OpenCV, Pillow):
pip install -e ".[qc]"

# With dev/test tools:
pip install -e ".[dev]"

# Everything:
pip install -e ".[qc,dev]"
```

`-e` (editable mode) means local source changes take effect immediately — no reinstall needed.

## 3. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in every value. Required before any service starts:

```
DATABASE_URL=postgresql://pathoryx_user:REAL_PASSWORD@localhost:5432/pathoryx
BABELSHARK_CONFIG=/path/to/babelshark.yaml
QC_SERVICE_CONFIG=/path/to/qc.yaml
DICOM_CONFIG=/path/to/dicom.yaml
SECTRA_HOST=your-pacs-host
SECTRA_PORT=4242
SECTRA_REMOTE_AE=SECTRA
SECTRA_LOCAL_AE=PALANTIR
PATHORYX_ALLOWED_INPUT_ROOTS=/data/slides,/mnt/nfs/incoming
```

The startup validators reject placeholder values like `strongpassword` or `CHANGEME`.

## 4. Set up the database

See [DATABASE_SETUP.md](DATABASE_SETUP.md) for full PostgreSQL setup instructions.

## 5. Run database migrations

```bash
source .venv/bin/activate
alembic upgrade head
```

Expected output ends with: `Running upgrade -> 0001, Initial schema`

Verify:

```bash
alembic current
# Should show: 0001 (head)
```

## 6. Verify the installation

```bash
pathoryx-babelshark --help
pathoryx-qc --help
pathoryx-dicom --help
pathoryx-uploader --help
pathoryx-recovery-sentry --help
pathoryx-orchestrate --help
```

Each command should print its help text without errors.

## 7. Start services

```bash
# All services via orchestrator:
pathoryx-orchestrate

# Or individually (each in its own terminal):
pathoryx-babelshark
pathoryx-qc
pathoryx-dicom
pathoryx-uploader
pathoryx-recovery-sentry
```

Optional: health monitor

```bash
pathoryx-health        # Starts HTTP health server on port 8080
```

## 8. Verify health endpoints

Once running, each service exposes:

```
http://localhost:8081/health   # BabelShark
http://localhost:8082/health   # QC
http://localhost:8083/health   # DICOM
http://localhost:8084/health   # Uploader
http://localhost:8085/health   # Failed Watcher
```

```bash
curl http://localhost:8081/health
# {"status": "healthy", "service": "babelshark", ...}
```

## Environment variables reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | yes | — | PostgreSQL connection string |
| `BABELSHARK_CONFIG` | yes | — | Path to BabelShark YAML config |
| `QC_SERVICE_CONFIG` | yes | — | Path to QC YAML config |
| `DICOM_CONFIG` | yes | — | Path to DICOM YAML config |
| `SECTRA_HOST` | yes | — | PACS hostname |
| `SECTRA_PORT` | yes | 4242 | PACS port |
| `SECTRA_REMOTE_AE` | yes | — | Remote AE title |
| `SECTRA_LOCAL_AE` | yes | — | Local AE title |
| `PATHORYX_ALLOWED_INPUT_ROOTS` | yes | — | Comma-separated allowed input paths |
| `PATHORYX_SERVICES` | no | all | Comma-separated services for orchestrator |
| `LOG_LEVEL` | no | INFO | DEBUG/INFO/WARNING/ERROR |
| `ENVIRONMENT` | no | production | `development` → colored console logs |
| `DB_POOL_SIZE` | no | 5 | SQLAlchemy pool size per service |
| `DB_MAX_OVERFLOW` | no | 10 | Additional connections above pool_size |
| `SECTRA_CSTORE_BATCH_SIZE` | no | 500 | storescu batch size (ARG_MAX protection) |
| `UPLOADER_CIRCUIT_RESET_SECONDS` | no | 60 | Circuit breaker reset timeout |
| `FAILED_WATCHER_POLL_INTERVAL` | no | 60 | Seconds between failed-slide scans |
| `FAILED_WATCHER_CRASH_THRESHOLD_SECONDS` | no | 120 | Runner heartbeat timeout |
