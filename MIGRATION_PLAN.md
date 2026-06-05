# Migration Plan — Pathoryx → Pathoryx Enterprise

## Prerequisites

1. **Postgres 14+** with the `pathoryx` database created.
2. Application DB user with CREATE SCHEMA privileges (migration only).
3. `.env` file populated from `.env.example`.
4. Original Pathoryx services **stopped** before migration begins.

## Step 1 — Install Enterprise Package

```bash
cd /home/shahram/Pathoryx-Enterprise
pip install -e .
```

## Step 2 — Run Alembic Migration

```bash
export DATABASE_URL="postgresql+psycopg2://pathoryx_user:REALPASSWORD@localhost:5432/pathoryx"
alembic upgrade head
```

This creates all 8 schemas and all tables. The `REVOKE UPDATE, DELETE ON events.pipeline_events`
is applied as part of the migration.

**Verify**:
```bash
psql "$DATABASE_URL" -c "\dn"   # should show core, events, qc, babelshark, dicomizer, uploader, failed_watcher, audit
```

## Step 3 — Backfill Existing Data (Optional)

If you have existing data in the original Pathoryx DB, write a one-off migration script:

```python
# scripts/backfill.py (not included — write per your data)
# Copy FileRecord → core.file_records
# Copy QcResult → qc.qc_results
# Copy TechnicalMetrics → core.technical_metrics
# Write synthetic events for historical pipeline runs
```

Data backfill is optional. The enterprise system works on a clean slate.

## Step 4 — Configure Environment

Copy and fill in `.env.example`:
```bash
cp .env.example .env
# Edit .env: set DATABASE_URL, BABELSHARK_CONFIG, QC_SERVICE_CONFIG, DICOM_CONFIG,
#            SECTRA_HOST, SECTRA_PORT, SECTRA_REMOTE_AE, SECTRA_LOCAL_AE, etc.
```

## Step 5 — Start Services (Single Machine)

```bash
# Option A: orchestrator (starts all services)
pathoryx-orchestrator

# Option B: individual services
pathoryx-babelshark &
pathoryx-qc &
pathoryx-dicom &
pathoryx-uploader &
pathoryx-recovery-sentry &
```

## Step 6 — Start Services (Docker)

```bash
docker-compose up postgres -d
docker-compose run --rm migrate
docker-compose up
```

## Step 7 — Verify Health

```bash
curl http://localhost:8081/health   # BabelShark
curl http://localhost:8082/health   # QC
curl http://localhost:8083/health   # DICOM
curl http://localhost:8084/health   # Uploader
curl http://localhost:8085/health   # Failed Watcher
```

All should return `{"healthy": true, ...}`.

## Rollback Plan

If anything goes wrong:
1. Stop all enterprise services.
2. The original Pathoryx project is **untouched** — restart original services.
3. Drop the enterprise schemas (`DROP SCHEMA core CASCADE; ...`) if needed.

## Data Coexistence Note

Original and Enterprise databases are completely separate. There is no shared state.
Run both in parallel during validation if needed.
