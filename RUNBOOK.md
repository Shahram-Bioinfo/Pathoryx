# Operations Runbook

Day-to-day operational procedures for running and maintaining the Pathoryx pipeline.

---

## Service lifecycle

### Start all services

```bash
# Option A: Orchestrator (recommended — handles restarts)
source .venv/bin/activate
pathoryx-orchestrate

# Option B: Individual services (each in its own terminal / systemd unit)
pathoryx-babelshark
pathoryx-qc
pathoryx-dicom
pathoryx-uploader
pathoryx-recovery-sentry
pathoryx-health    # HTTP health endpoints

# Option C: Docker Compose
docker-compose up -d
```

### Stop all services (graceful)

```bash
# Orchestrator: SIGTERM causes graceful shutdown of all children
kill -TERM $(pgrep -f pathoryx-orchestrate)

# Individual process:
kill -TERM <pid>

# Docker Compose:
docker-compose down
```

Every service handles SIGTERM: it stops accepting new work, finishes the current slide, then exits. Allow up to 60s for clean shutdown.

### Restart a single service

```bash
# Find the PID:
pgrep -af pathoryx-qc

# Send SIGTERM:
kill -TERM <pid>

# Restart:
pathoryx-qc &

# Docker Compose:
docker-compose restart qc
```

### Check service status

```bash
# Process check:
pgrep -af "pathoryx-"

# Health endpoints:
curl -s http://localhost:8081/health | python3 -m json.tool   # BabelShark
curl -s http://localhost:8082/health | python3 -m json.tool   # QC
curl -s http://localhost:8083/health | python3 -m json.tool   # DICOM
curl -s http://localhost:8084/health | python3 -m json.tool   # Uploader
curl -s http://localhost:8085/health | python3 -m json.tool   # Failed Watcher

# Runner registration in DB:
psql "$DATABASE_URL" -c "
SELECT service_name, host_id, status, last_heartbeat_at
FROM core.runner_registrations
ORDER BY last_heartbeat_at DESC;"
```

---

## Database operations

### View pipeline queue

```sql
-- Active queue depth by service
SELECT target_service, trigger_status, count(*)
FROM core.service_trigger
WHERE trigger_status IN ('pending', 'running', 'failed')
GROUP BY target_service, trigger_status
ORDER BY target_service;
```

### View recently processed slides

```sql
SELECT global_artifact_id, status, updated_at
FROM core.file_records
ORDER BY updated_at DESC
LIMIT 20;
```

### Check for stalled slides (running > 30 min)

```sql
SELECT st.internal_id, st.target_service, st.stage_name,
       st.started_at, now() - st.started_at AS duration,
       st.claimed_by_runner_id, st.claimed_by_host_id
FROM core.service_trigger st
WHERE st.trigger_status = 'running'
  AND st.started_at < now() - INTERVAL '30 minutes'
ORDER BY st.started_at;
```

### Reset stalled triggers

```sql
-- Reset to pending so they can be picked up again:
UPDATE core.service_trigger
SET trigger_status = 'pending',
    started_at = NULL,
    claimed_by_runner_id = NULL,
    claimed_by_host_id = NULL
WHERE trigger_status = 'running'
  AND started_at < now() - INTERVAL '30 minutes';
```

### View dead-letter queue (exhausted retries)

```sql
SELECT internal_id, target_service, stage_name,
       retry_count, max_retries, error_message, updated_at
FROM core.service_trigger
WHERE trigger_status = 'failed'
  AND retry_count >= max_retries
ORDER BY updated_at DESC;
```

### Requeue a dead-letter trigger

```python
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.db.session import get_session
from pathoryx_enterprise.db.models.core import ServiceTrigger

trigger_id = "YOUR_TRIGGER_UUID"
with get_session() as session:
    trigger = session.get(ServiceTrigger, trigger_id)
    TriggerRepository(session).requeue(trigger)
```

Or directly in SQL:

```sql
UPDATE core.service_trigger
SET trigger_status = 'pending',
    retry_count = 0,
    error_message = NULL,
    started_at = NULL,
    claimed_by_runner_id = NULL,
    claimed_by_host_id = NULL
WHERE internal_id = 'YOUR_TRIGGER_UUID';
```

---

## QC operations

### View recent QC decisions

```sql
SELECT qr.global_artifact_id, qr.decision_status, qr.decision_reason,
       qr.processed_at
FROM qc.qc_results qr
ORDER BY qr.processed_at DESC
LIMIT 20;
```

### Reprocess a QC-rejected slide

```python
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.db.session import get_session

file_record_id = "..."  # core.file_records.internal_id
global_artifact_id = "..."

with get_session() as session:
    trigger, created = TriggerRepository(session).enqueue(
        source_service="manual_recovery",
        target_service="qc_service",
        stage_name="qc",
        file_record_internal_id=file_record_id,
        global_artifact_id=global_artifact_id,
    )
    print(f"Created: {created}, trigger_id: {trigger.internal_id}")
```

---

## Uploader operations

### Check circuit breaker state

The circuit breaker state is in memory. If the uploader log shows:

```
"circuit breaker OPEN — pausing upload"
```

It means 5 consecutive PACS upload failures occurred. The circuit automatically
transitions to HALF_OPEN after `UPLOADER_CIRCUIT_RESET_SECONDS` (default 60s).

To reset immediately, restart the uploader:

```bash
kill -TERM $(pgrep -f pathoryx-uploader)
pathoryx-uploader &
```

### View upload results

```sql
SELECT global_artifact_id, upload_status, error_message, uploaded_at
FROM uploader.upload_results
ORDER BY uploaded_at DESC
LIMIT 20;
```

---

## Log management

### Log format

All services emit structured JSON logs (or human-readable in `ENVIRONMENT=development`):

```json
{"timestamp": "2026-05-27T10:00:00Z", "level": "info", "event": "qc.started",
 "service": "qc_runner", "runner_id": "...", "host_id": "...",
 "correlation_id": "...", "global_artifact_id": "..."}
```

### Log levels

Change at runtime by restarting with `LOG_LEVEL=DEBUG`.

### Log rotation (if writing to file)

```bash
# Add to .env to write logs to file (pipe stdout):
pathoryx-qc >> /var/log/pathoryx/qc.log 2>&1 &

# Logrotate config (/etc/logrotate.d/pathoryx):
/var/log/pathoryx/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    postrotate
        kill -HUP $(pgrep -f pathoryx-)
    endscript
}
```

---

## Database maintenance

### Vacuum and analyze (weekly)

```bash
psql "$DATABASE_URL" -c "VACUUM ANALYZE;"
```

### Check table sizes

```sql
SELECT schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname IN ('core','events','qc','babelshark','dicomizer','uploader','failed_watcher')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

### Archive old events (after 6 months)

```sql
-- Create archive table (run once):
CREATE TABLE events.pipeline_events_archive (LIKE events.pipeline_events INCLUDING ALL);

-- Move events older than 6 months:
WITH moved AS (
    DELETE FROM events.pipeline_events
    WHERE occurred_at < now() - INTERVAL '6 months'
    RETURNING *
)
INSERT INTO events.pipeline_events_archive SELECT * FROM moved;
```

---

## Monitoring

### Prometheus metrics

Each service exposes metrics on its Prometheus port (`:9091`–`:9095`):

```bash
curl http://localhost:9092/metrics | grep -E "^pathoryx_"
```

Key metrics:

| Metric | Description |
|--------|-------------|
| `pathoryx_queue_depth` | Pending triggers per service |
| `pathoryx_slide_processing_seconds` | Per-stage processing time histogram |
| `pathoryx_slides_processed_total` | Counter by service and status |
| `pathoryx_gpu_utilization_percent` | GPU usage (QC service) |
| `pathoryx_circuit_breaker_state` | Uploader circuit state (0=closed,1=open) |

### Check connection pool health

```sql
SELECT count(*), state, wait_event_type, wait_event
FROM pg_stat_activity
WHERE datname = 'pathoryx_enterprise'
GROUP BY state, wait_event_type, wait_event
ORDER BY count(*) DESC;
```

If `idle` connections are near `max_connections`, increase `DB_MAX_OVERFLOW` or check for connection leaks.

---

## Deployment / upgrade

### Rolling upgrade of a single service

```bash
# 1. Deploy new code
cd /home/shahram/Pathoryx-Enterprise
git pull  # or copy new files

# 2. Reinstall
source .venv/bin/activate
pip install -e .

# 3. Run new migrations (if any)
alembic upgrade head

# 4. Restart affected service
kill -TERM $(pgrep -f pathoryx-qc)
pathoryx-qc &
```

In-flight slides are not lost: triggers remain `running` and will be visible in the Operations Center stuck-trigger view. RecoverySentry can requeue them if the service doesn't reconnect within the heartbeat timeout.

### Rollback

```bash
# 1. Check available migration targets
alembic history

# 2. Downgrade one revision
alembic downgrade -1

# 3. Reinstall previous version
pip install -e /path/to/previous/version

# 4. Restart services
```
