# Operations Guide

## Starting / Stopping Services

### Single Machine (direct)
```bash
# Start all via orchestrator
pathoryx-orchestrator

# Or individually
pathoryx-babelshark
pathoryx-qc
pathoryx-dicom
pathoryx-uploader
pathoryx-recovery-sentry

# Graceful stop: send SIGTERM
kill -TERM <pid>
```

### Docker Compose
```bash
docker-compose up -d
docker-compose logs -f qc         # tail QC logs
docker-compose restart babelshark # restart one service
docker-compose down               # stop all
```

## Health Checks

| Service | Health URL | Readiness URL |
|---------|-----------|---------------|
| BabelShark | `http://host:8081/health` | `http://host:8081/ready` |
| QC | `http://host:8082/health` | `http://host:8082/ready` |
| DICOM | `http://host:8083/health` | `http://host:8083/ready` |
| Uploader | `http://host:8084/health` | `http://host:8084/ready` |
| Failed Watcher | `http://host:8085/health` | `http://host:8085/ready` |

Response codes: `200 OK` = healthy, `503 Service Unavailable` = unhealthy.

## Database Migrations

```bash
# Apply pending migrations
alembic upgrade head

# Check current revision
alembic current

# Show migration history
alembic history

# Rollback one step
alembic downgrade -1
```

## Monitoring

Prometheus metrics are exposed on ports 9091–9095. Key metrics:

- `pathoryx_trigger_queue_depth{target_service}` — pending triggers per service
- `pathoryx_stage_latency_seconds{service,stage}` — processing time histograms
- `pathoryx_files_processed_total{service,stage}` — throughput counters
- `pathoryx_files_failed_total{service,stage,error_type}` — failure counters
- `pathoryx_runner_heartbeat_age_seconds{runner_id,service}` — runner liveness

## Runner Registry

Check active runners:
```sql
SELECT runner_id, service_name, host_id, status, last_heartbeat_at
FROM core.runner_registrations
WHERE status = 'active'
ORDER BY last_heartbeat_at DESC;
```

Mark stale crashed runners (runs automatically on startup, also callable manually):
```sql
UPDATE core.runner_registrations
SET status = 'crashed'
WHERE status = 'active'
  AND last_heartbeat_at < NOW() - INTERVAL '2 minutes';
```

## Reviewing Failed Slides

```sql
-- Pending technician review
SELECT tc.internal_id, tc.change_type, tc.new_path, tc.detected_at, tc.review_status
FROM failed_watcher.technician_changes tc
WHERE tc.review_status IN ('detected', 'linked', 'unlinked')
ORDER BY tc.detected_at ASC;

-- Requeue via dashboard: Recovery Center → select file → validate filename → Apply rename
-- Or via SQL (see RECOVERY_SENTRY.md for the manual requeue SQL):
```

## Dead-Letter Queue

```sql
-- Triggers that exhausted all retries
SELECT st.internal_id, st.target_service, st.stage_name,
       st.retry_count, st.error_message, st.finished_at
FROM core.service_trigger st
WHERE st.trigger_status = 'failed'
  AND st.retry_count >= st.max_retries
ORDER BY st.finished_at DESC;
```

Requeue via `TriggerRepository.requeue(trigger)`.

## Event Replay

```python
from pathoryx_enterprise.db.repositories.event_store import EventStoreRepository
from pathoryx_enterprise.db.session import get_session

with get_session() as session:
    repo = EventStoreRepository(session)
    events = repo.replay_artifact("global_artifact_id_here")
    for e in events:
        print(e.event_type, e.event_version, e.occurred_at)
```
