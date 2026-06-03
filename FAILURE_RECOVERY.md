# Failure Recovery Guide

## Trigger Dead-Letter Queue

Triggers that fail `max_retries` times (default 3) are left with `trigger_status = 'failed'`.
They appear in the dead-letter queue:

```sql
SELECT internal_id, target_service, stage_name, retry_count, error_message
FROM core.service_trigger
WHERE trigger_status = 'failed' AND retry_count >= max_retries;
```

### Requeue a Dead-Letter Trigger

```python
from pathoryx_enterprise.db.repositories.trigger import TriggerRepository
from pathoryx_enterprise.db.session import get_session

with get_session() as session:
    trigger = session.get(ServiceTrigger, trigger_id)
    TriggerRepository(session).requeue(trigger)
```

## Crashed Runner Recovery

Runners that crash (no heartbeat for 120s) are automatically marked `crashed`:

```sql
SELECT runner_id, service_name, host_id, last_heartbeat_at
FROM core.runner_registrations WHERE status = 'crashed';
```

Any pending trigger claimed by a crashed runner is left in `running` status.
Recovery:
```sql
-- Reset stale running triggers to pending so they can be requeued
UPDATE core.service_trigger
SET trigger_status = 'pending',
    started_at = NULL,
    claimed_by_runner_id = NULL,
    claimed_by_host_id = NULL
WHERE trigger_status = 'running'
  AND started_at < NOW() - INTERVAL '30 minutes';
```

## Failed Slide Investigation

```sql
-- Full audit trail for a slide
SELECT event_type, event_version, occurred_at, event_payload
FROM events.pipeline_events
WHERE global_artifact_id = 'YOUR_ARTIFACT_ID'
ORDER BY event_version ASC;

-- Current FileRecord status
SELECT status, current_file_path, updated_at
FROM core.file_records
WHERE global_artifact_id = 'YOUR_ARTIFACT_ID';
```

## QC Rejected Slides

```sql
SELECT qr.global_artifact_id, qr.decision_reason,
       qr.stain_json, qr.blur_json, qr.penmark_json
FROM qc.qc_results qr
WHERE qr.decision_status != 'accepted'
ORDER BY qr.processed_at DESC
LIMIT 50;
```

To reprocess QC-failed slides through QC again:
```python
# Set trigger from qc_failed back to qc_pending
with get_session() as session:
    trigger, created = TriggerRepository(session).enqueue(
        source_service="manual_recovery",
        target_service="qc_service",
        stage_name="qc",
        file_record_internal_id=file_record_id,
        global_artifact_id=global_artifact_id,
    )
```

## Circuit Breaker — Upload Service

If the upload service circuit opens (5 consecutive failures), it pauses for `UPLOADER_CIRCUIT_RESET_SECONDS` (default 60s).
Logs will show: `"circuit breaker OPEN — pausing upload"`.

To check circuit state, restart the uploader with the PACS available. The circuit resets
automatically to HALF_OPEN after the timeout.

## Database Connection Issues

If services fail to connect:
1. Check `DATABASE_URL` is set and correct.
2. Verify Postgres is running: `pg_isready -d "$DATABASE_URL"`
3. Check connection pool exhaustion: 
   ```sql
   SELECT count(*), state FROM pg_stat_activity GROUP BY state;
   ```
4. Verify pool settings in `.env`: `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`.

## Event Store Integrity Check

```sql
-- Verify no gaps in event_version per aggregate
SELECT aggregate_id, aggregate_type,
       min(event_version), max(event_version), count(*)
FROM events.pipeline_events
GROUP BY aggregate_id, aggregate_type
HAVING max(event_version) != count(*);
-- Empty result = no gaps (healthy)
```
